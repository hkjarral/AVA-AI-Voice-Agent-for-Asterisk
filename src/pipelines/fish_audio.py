"""
Fish Audio TTS Pipeline Adapter.

Implements the TTSComponent interface for Fish Audio's speech models (S1, S2 and
the drama preview), over either transport:

- ``http`` (default): ``POST /v1/tts`` returns raw PCM over a chunked response.
  One request per text fragment, converted and forwarded chunk by chunk.
- ``websocket``: the realtime endpoint ``wss://api.fish.audio/v1/tts/live`` keeps
  one session open for a whole turn. Text is sent as the LLM produces it and
  audio comes back while the sentence is still being written, so the engine never
  waits for a fragment to be synthesised before consuming the next tokens.

Both transports ask the provider for the call's own sample rate, so a telephone
call needs no intermediate resampling.

API Reference:
  https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech
  https://docs.fish.audio/api-reference/endpoint/websocket/tts-live
"""
from __future__ import annotations

import asyncio
import io
import time
import uuid
import wave
from typing import Any, AsyncIterator, Callable, Dict, Optional, Tuple

import aiohttp

from ..audio import (
    convert_pcm16le_to_target_format,
    resample_audio,
    resolve_output_resampler_policy,
)
from ..config import AppConfig, FishAudioProviderConfig
from ..logging_config import get_logger
from .base import TTSComponent

logger = get_logger(__name__)

# Sample rates Fish Audio accepts for raw PCM and WAV output.
FISH_AUDIO_SAMPLE_RATES = (8000, 16000, 24000, 32000, 44100, 48000)
# Used when the negotiated transport rate is not one the provider can emit.
FISH_AUDIO_FALLBACK_SAMPLE_RATE = 16000
# Size of the HTTP reads while the response is still streaming.
FISH_AUDIO_READ_BYTES = 4096
# Realtime path of the websocket endpoint, appended to the configured base URL.
FISH_AUDIO_WS_PATH = "/tts/live"


def _load_msgpack() -> Tuple[Callable[[Any], bytes], Callable[[bytes], Any]]:
    """Return (pack, unpack) for the realtime transport.

    The websocket endpoint speaks MessagePack. Import lazily so the HTTP
    transport keeps working when the optional dependency is absent.
    """
    try:
        import ormsgpack

        return ormsgpack.packb, ormsgpack.unpackb
    except ImportError:
        pass
    try:
        import msgpack

        return (
            lambda obj: msgpack.packb(obj, use_bin_type=True),
            lambda data: msgpack.unpackb(data, raw=False),
        )
    except ImportError as exc:
        raise RuntimeError(
            "Fish Audio realtime transport requires msgpack (pip install msgpack) "
            "or ormsgpack; use transport: http otherwise"
        ) from exc


class FishAudioTTSAdapter(TTSComponent):
    """
    Fish Audio TTS adapter for pipeline orchestrator.

    Converts text to speech with Fish Audio and adapts its native PCM output to
    the negotiated per-call transport.
    """

    wideband_output_format = {
        "encoding": "linear16",
        "sample_rate": 16000,
        "options": {"audio_format": "pcm", "sample_rate": 16000},
    }

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: FishAudioProviderConfig,
        options: Optional[Dict[str, Any]] = None,
        *,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_config = provider_config
        self._pipeline_defaults = options or {}
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        # The engine feeds text progressively only when the realtime transport is on.
        self.supports_text_stream = self._compose_options({})["transport"] == "websocket"

    async def start(self) -> None:
        logger.debug(
            "Fish Audio TTS adapter initialized",
            component=self.component_key,
            model=self._provider_config.model,
            reference_id=self._provider_config.reference_id,
            latency=self._provider_config.latency,
            transport=self._provider_config.transport,
        )

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        await self._ensure_session()

    async def close_call(self, call_id: str) -> None:
        pass

    async def validate_connectivity(self, options: Dict[str, Any]) -> Dict[str, Any]:
        merged = self._compose_options(options or {})
        return await super().validate_connectivity(merged)

    async def synthesize(
        self,
        call_id: str,
        text: str,
        options: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """
        Synthesize one text fragment over the HTTP endpoint.

        Yields audio chunks in the negotiated per-call transport format, as soon
        as the provider sends them.
        """
        if not text:
            return
            yield  # Makes this an async generator

        await self._ensure_session()
        merged = self._compose_options(options)
        api_key = self._require_api_key(merged)

        target_encoding = merged["format"]["encoding"]
        target_sample_rate = int(merged["format"]["sample_rate"])
        audio_format = str(merged["audio_format"]).lower()
        if audio_format not in {"pcm", "wav"}:
            raise RuntimeError(
                "Unsupported Fish Audio TTS output format: "
                + audio_format
                + " (use pcm or wav)"
            )
        source_sample_rate = self._resolve_source_sample_rate(
            merged.get("sample_rate"), target_sample_rate
        )

        payload = self._build_request(merged, source_sample_rate, audio_format, text=text)
        headers = self._build_headers(api_key, merged)
        url = str(merged["base_url"]).rstrip("/") + "/tts"
        request_id = "fish-tts-" + uuid.uuid4().hex[:12]
        chunk_ms = int(merged.get("chunk_size_ms", 20))

        logger.info(
            "Fish Audio TTS synthesis started",
            call_id=call_id,
            request_id=request_id,
            text_preview=text[:64],
            model=merged["model"],
            reference_id=merged.get("reference_id"),
            latency=merged["latency"],
            source_sample_rate=source_sample_rate,
            target_encoding=target_encoding,
            target_sample_rate=target_sample_rate,
        )

        started_at = time.perf_counter()
        first_audio_ms: Optional[float] = None
        output_bytes = 0

        # Bound the whole exchange: a hung provider must not hold the turn open.
        timeout = aiohttp.ClientTimeout(total=float(merged["request_timeout_sec"]))
        try:
            async with self._session.post(
                url, json=payload, headers=headers, timeout=timeout
            ) as response:
                if response.status >= 400:
                    body = await response.text()
                    logger.error(
                        "Fish Audio TTS synthesis failed",
                        call_id=call_id,
                        request_id=request_id,
                        status=response.status,
                        body=body[:200],
                    )
                    response.raise_for_status()

                if audio_format == "pcm" and source_sample_rate == target_sample_rate:
                    # The provider rate already matches the call: convert and forward
                    # each HTTP chunk instead of waiting for the whole sentence.
                    emit_size = self._chunk_size_bytes(
                        target_encoding, target_sample_rate, chunk_ms
                    )
                    carry = b""
                    pending = bytearray()
                    async for raw in response.content.iter_chunked(FISH_AUDIO_READ_BYTES):
                        if not raw:
                            continue
                        data = carry + raw
                        aligned = len(data) - (len(data) % 2)
                        carry = data[aligned:]
                        if not aligned:
                            continue
                        pending.extend(
                            convert_pcm16le_to_target_format(
                                data[:aligned], target_encoding
                            )
                        )
                        if first_audio_ms is None and pending:
                            first_audio_ms = (time.perf_counter() - started_at) * 1000.0
                        while len(pending) >= emit_size:
                            chunk = bytes(pending[:emit_size])
                            del pending[:emit_size]
                            output_bytes += len(chunk)
                            yield chunk
                    if pending:
                        output_bytes += len(pending)
                        yield bytes(pending)
                else:
                    raw_audio = await response.read()
                    pcm_data, decoded_rate = self._decode_audio(
                        raw_audio, audio_format, source_sample_rate
                    )
                    if decoded_rate != target_sample_rate:
                        pcm_data, _ = resample_audio(
                            pcm_data,
                            decoded_rate,
                            target_sample_rate,
                            mode=merged["output_resampler"],
                        )
                    converted = convert_pcm16le_to_target_format(
                        pcm_data, target_encoding
                    )
                    first_audio_ms = (time.perf_counter() - started_at) * 1000.0
                    for chunk in self._chunk_audio(
                        converted, target_encoding, target_sample_rate, chunk_ms
                    ):
                        if chunk:
                            output_bytes += len(chunk)
                            yield chunk

            logger.info(
                "Fish Audio TTS synthesis completed",
                call_id=call_id,
                request_id=request_id,
                first_audio_ms=round(first_audio_ms, 2) if first_audio_ms else None,
                total_ms=round((time.perf_counter() - started_at) * 1000.0, 2),
                output_bytes=output_bytes,
                target_encoding=target_encoding,
                target_sample_rate=target_sample_rate,
            )

        except aiohttp.ClientError as exc:
            logger.error(
                "Fish Audio TTS HTTP error",
                call_id=call_id,
                request_id=request_id,
                error=str(exc),
            )
            raise

    async def synthesize_stream(
        self,
        call_id: str,
        text_chunks: AsyncIterator[str],
        options: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """
        Synthesize a whole turn over the realtime websocket session.

        Text fragments are sent as the engine produces them and audio is yielded
        as the provider returns it. Falls back to one HTTP request per fragment
        when the realtime transport is not selected.
        """
        merged = self._compose_options(options)
        if str(merged["transport"]).lower() != "websocket":
            async for text in text_chunks:
                async for chunk in self.synthesize(call_id, text, options):
                    yield chunk
            return

        api_key = self._require_api_key(merged)
        pack, unpack = _load_msgpack()
        await self._ensure_session()

        target_encoding = merged["format"]["encoding"]
        target_sample_rate = int(merged["format"]["sample_rate"])
        source_sample_rate = self._resolve_source_sample_rate(
            merged.get("sample_rate"), target_sample_rate
        )
        chunk_ms = int(merged.get("chunk_size_ms", 20))
        emit_size = self._chunk_size_bytes(target_encoding, target_sample_rate, chunk_ms)
        request_id = "fish-tts-" + uuid.uuid4().hex[:12]
        url = self._websocket_url(merged)

        logger.info(
            "Fish Audio realtime session opening",
            call_id=call_id,
            request_id=request_id,
            model=merged["model"],
            reference_id=merged.get("reference_id"),
            latency=merged["latency"],
            source_sample_rate=source_sample_rate,
            target_encoding=target_encoding,
            target_sample_rate=target_sample_rate,
        )

        started_at = time.perf_counter()
        first_audio_ms: Optional[float] = None
        output_bytes = 0
        fragments = 0
        carry = b""
        pending = bytearray()
        resample_state = None
        sender: Optional[asyncio.Task] = None

        async def feed(websocket) -> None:
            """Send each fragment as it is produced, then close the text stream."""
            nonlocal fragments
            try:
                async for fragment in text_chunks:
                    if not fragment:
                        continue
                    fragments += 1
                    await websocket.send_bytes(pack({"event": "text", "text": fragment}))
                    # Flush so the provider starts on this fragment instead of
                    # waiting for its own buffer to fill.
                    await websocket.send_bytes(pack({"event": "flush"}))
            finally:
                with_stop = {"event": "stop"}
                try:
                    await websocket.send_bytes(pack(with_stop))
                except Exception:  # noqa: BLE001 - the socket may already be gone
                    pass

        timeout = aiohttp.ClientTimeout(total=float(merged["request_timeout_sec"]))
        try:
            async with self._session.ws_connect(
                url,
                headers=self._build_headers(api_key, merged),
                timeout=timeout,
                heartbeat=20,
                max_msg_size=0,
            ) as websocket:
                await websocket.send_bytes(
                    pack({
                        "event": "start",
                        "request": self._build_request(
                            merged, source_sample_rate, "pcm", text=""
                        ),
                    })
                )
                sender = asyncio.ensure_future(feed(websocket))

                async for message in websocket:
                    if message.type is aiohttp.WSMsgType.BINARY:
                        event = unpack(message.data)
                    elif message.type is aiohttp.WSMsgType.TEXT:
                        continue
                    elif message.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.CLOSE,
                    ):
                        break
                    elif message.type is aiohttp.WSMsgType.ERROR:
                        raise RuntimeError("Fish Audio realtime socket error")
                    else:
                        continue

                    if not isinstance(event, dict):
                        continue
                    name = event.get("event")
                    if name == "audio":
                        data = carry + (event.get("audio") or b"")
                        aligned = len(data) - (len(data) % 2)
                        carry = data[aligned:]
                        if not aligned:
                            continue
                        pcm = data[:aligned]
                        if source_sample_rate != target_sample_rate:
                            pcm, resample_state = resample_audio(
                                pcm,
                                source_sample_rate,
                                target_sample_rate,
                                mode=merged["output_resampler"],
                                state=resample_state,
                            )
                        pending.extend(
                            convert_pcm16le_to_target_format(pcm, target_encoding)
                        )
                        if first_audio_ms is None and pending:
                            first_audio_ms = (time.perf_counter() - started_at) * 1000.0
                        while len(pending) >= emit_size:
                            chunk = bytes(pending[:emit_size])
                            del pending[:emit_size]
                            output_bytes += len(chunk)
                            yield chunk
                    elif name == "finish":
                        reason = event.get("reason")
                        if reason == "error":
                            raise RuntimeError(
                                "Fish Audio realtime session finished with an error"
                            )
                        break
                    elif name == "log":
                        logger.debug(
                            "Fish Audio realtime log",
                            call_id=call_id,
                            request_id=request_id,
                            message=str(event.get("message"))[:200],
                        )

                if pending:
                    output_bytes += len(pending)
                    yield bytes(pending)

            logger.info(
                "Fish Audio realtime session completed",
                call_id=call_id,
                request_id=request_id,
                fragments=fragments,
                first_audio_ms=round(first_audio_ms, 2) if first_audio_ms else None,
                total_ms=round((time.perf_counter() - started_at) * 1000.0, 2),
                output_bytes=output_bytes,
            )

        except aiohttp.ClientError as exc:
            logger.error(
                "Fish Audio realtime connection error",
                call_id=call_id,
                request_id=request_id,
                error=str(exc),
            )
            raise
        finally:
            if sender is not None and not sender.done():
                sender.cancel()
                try:
                    await sender
                except (asyncio.CancelledError, Exception):  # noqa: B014 - cleanup only
                    pass

    def _require_api_key(self, merged: Dict[str, Any]) -> str:
        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("Fish Audio TTS requires an API key (FISH_AUDIO_API_KEY)")
        return str(api_key)

    def _build_headers(self, api_key: str, merged: Dict[str, Any]) -> Dict[str, str]:
        return {
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            # Fish Audio selects the speech model with a header, not a body field.
            "model": str(merged["model"]),
        }

    def _build_request(
        self,
        merged: Dict[str, Any],
        source_sample_rate: int,
        audio_format: str,
        *,
        text: str,
    ) -> Dict[str, Any]:
        """Body shared by the HTTP request and the realtime start event."""
        request: Dict[str, Any] = {
            "text": text,
            "format": audio_format,
            "sample_rate": source_sample_rate,
            "latency": merged["latency"],
            "chunk_length": int(merged["chunk_length"]),
            "normalize": bool(merged["normalize"]),
            "temperature": float(merged["temperature"]),
            "top_p": float(merged["top_p"]),
        }
        reference_id = merged.get("reference_id")
        if reference_id:
            request["reference_id"] = reference_id
        prosody: Dict[str, Any] = {}
        if merged.get("speed") is not None:
            prosody["speed"] = float(merged["speed"])
        if merged.get("volume") is not None:
            prosody["volume"] = float(merged["volume"])
        if prosody:
            request["prosody"] = prosody
        return request

    def _websocket_url(self, merged: Dict[str, Any]) -> str:
        configured = merged.get("ws_base_url")
        base = str(configured or merged["base_url"]).rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        if base.endswith(FISH_AUDIO_WS_PATH):
            return base
        return base + FISH_AUDIO_WS_PATH

    def _resolve_source_sample_rate(
        self, configured: Optional[int], target_sample_rate: int
    ) -> int:
        """Pick the sample rate to request from Fish Audio.

        Following the call's own rate keeps the telephone path resample-free. An
        explicitly configured rate wins; a rate the provider cannot emit falls
        back to 16 kHz and is resampled locally.
        """
        candidate = int(configured) if configured else int(target_sample_rate)
        if candidate in FISH_AUDIO_SAMPLE_RATES:
            return candidate
        logger.debug(
            "Fish Audio TTS sample rate unsupported by the provider; falling back",
            component=self.component_key,
            requested=candidate,
            fallback=FISH_AUDIO_FALLBACK_SAMPLE_RATE,
        )
        return FISH_AUDIO_FALLBACK_SAMPLE_RATE

    def _decode_audio(
        self, raw_audio: bytes, audio_format: str, source_sample_rate: int
    ) -> Tuple[bytes, int]:
        """Return (PCM16 frames, sample rate) for a buffered response."""
        if audio_format == "wav":
            with wave.open(io.BytesIO(raw_audio), "rb") as wav_file:
                return wav_file.readframes(wav_file.getnframes()), wav_file.getframerate()
        return raw_audio, source_sample_rate

    async def _ensure_session(self) -> None:
        if self._session and not self._session.closed:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory()

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge runtime options with pipeline and provider defaults."""
        runtime_options = runtime_options or {}
        runtime_format = (
            runtime_options.get("format") or runtime_options.get("target_format") or {}
        )
        default_format = (
            self._pipeline_defaults.get("format")
            or self._pipeline_defaults.get("target_format")
            or {}
        )

        def pick(key: str, provider_value: Any) -> Any:
            return runtime_options.get(
                key, self._pipeline_defaults.get(key, provider_value)
            )

        merged = {
            "api_key": pick("api_key", self._provider_config.api_key),
            "base_url": pick("base_url", self._provider_config.base_url),
            "ws_base_url": pick("ws_base_url", self._provider_config.ws_base_url),
            "transport": pick("transport", self._provider_config.transport),
            "model": pick("model", self._provider_config.model),
            "reference_id": pick("reference_id", self._provider_config.reference_id),
            "audio_format": pick("audio_format", self._provider_config.audio_format),
            "sample_rate": pick("sample_rate", self._provider_config.sample_rate),
            "latency": pick("latency", self._provider_config.latency),
            "chunk_length": pick("chunk_length", self._provider_config.chunk_length),
            "normalize": pick("normalize", self._provider_config.normalize),
            "temperature": pick("temperature", self._provider_config.temperature),
            "top_p": pick("top_p", self._provider_config.top_p),
            "speed": pick("speed", self._provider_config.speed),
            "volume": pick("volume", self._provider_config.volume),
            "format": {
                "encoding": runtime_format.get(
                    "encoding", default_format.get("encoding", "mulaw")
                ),
                "sample_rate": int(
                    runtime_format.get(
                        "sample_rate", default_format.get("sample_rate", 8000)
                    )
                ),
            },
            "chunk_size_ms": pick("chunk_size_ms", 20),
            "request_timeout_sec": pick(
                "request_timeout_sec", self._provider_config.request_timeout_sec
            ),
            "output_resampler": pick(
                "output_resampler", self._provider_config.output_resampler
            ),
        }

        merged["output_resampler"] = resolve_output_resampler_policy(
            provider_mode=merged.get("output_resampler")
        )[0]
        return merged

    @staticmethod
    def _chunk_size_bytes(encoding: str, sample_rate: int, chunk_ms: int) -> int:
        bytes_per_sample = 1 if encoding.lower() in {"ulaw", "mulaw", "mu-law"} else 2
        return max(
            bytes_per_sample,
            int(sample_rate * (chunk_ms / 1000.0) * bytes_per_sample),
        )

    def _chunk_audio(
        self,
        audio: bytes,
        encoding: str,
        sample_rate: int,
        chunk_ms: int = 20,
    ) -> list:
        """Split encoded audio into transport-sized playback chunks."""
        chunk_size = self._chunk_size_bytes(encoding, sample_rate, chunk_ms)
        chunks = []
        for index in range(0, len(audio), chunk_size):
            chunk = audio[index:index + chunk_size]
            if chunk:
                chunks.append(chunk)
        return chunks
