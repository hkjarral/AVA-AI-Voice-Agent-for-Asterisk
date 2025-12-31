"""
# Groq cloud component adapters for configurable pipelines.

Groq exposes OpenAI-compatible REST endpoints for:
- STT:  POST https://api.groq.com/openai/v1/audio/transcriptions
- TTS:  POST https://api.groq.com/openai/v1/audio/speech

These adapters implement the modular pipeline interfaces in `base.py` so the
PipelineOrchestrator can compose Groq STT/TTS with any LLM component.
"""

from __future__ import annotations

import base64
import io
import json
import time
import uuid
import wave
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Iterable, Optional, Tuple

import aiohttp

from ..audio import convert_pcm16le_to_target_format, resample_audio
from ..config import AppConfig, GroqSTTProviderConfig, GroqTTSProviderConfig
from ..logging_config import get_logger
from .base import STTComponent, TTSComponent

logger = get_logger(__name__)


def _merge_dicts(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(base or {})
    if override:
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = _merge_dicts(merged[key], value)
            elif value is not None:
                merged[key] = value
    return merged


def _bytes_per_sample(encoding: str) -> int:
    fmt = (encoding or "").lower()
    if fmt in ("ulaw", "mulaw", "mu-law", "g711_ulaw"):
        return 1
    return 2


def _chunk_audio(audio_bytes: bytes, encoding: str, sample_rate: int, chunk_ms: int) -> Iterable[bytes]:
    if not audio_bytes:
        return
    bytes_per_sample = _bytes_per_sample(encoding)
    frame_size = max(bytes_per_sample, int(sample_rate * (chunk_ms / 1000.0) * bytes_per_sample))
    for idx in range(0, len(audio_bytes), frame_size):
        yield audio_bytes[idx : idx + frame_size]


def _pcm16le_to_wav_bytes(pcm16: bytes, sample_rate_hz: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate_hz))
        wf.writeframes(pcm16 or b"")
    return buf.getvalue()


def _wav_bytes_to_pcm16le(wav_bytes: bytes) -> Tuple[bytes, int]:
    if not wav_bytes:
        return b"", 0

    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        channels = int(wf.getnchannels())
        sample_width = int(wf.getsampwidth())
        sample_rate = int(wf.getframerate())
        frames = wf.readframes(wf.getnframes())

    if channels != 1:
        # Avoid extra dependencies; simplest safe approach is to keep left channel only.
        if sample_width == 2:
            import audioop

            frames = audioop.tomono(frames, 2, 1.0, 0.0)
        else:
            # Unknown layout; fall back to returning raw bytes.
            logger.warning("Groq TTS WAV returned non-mono audio; returning raw frames", channels=channels)

    if sample_width != 2:
        logger.warning("Groq TTS WAV returned non-PCM16 sample width; returning raw frames", sample_width=sample_width)
        return frames, sample_rate

    return frames, sample_rate


def _decode_audio_payload(raw_bytes: bytes) -> bytes:
    """Handle both raw audio responses and JSON wrapper responses."""
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw_bytes

    audio_b64 = payload.get("data") or payload.get("audio")
    if not audio_b64:
        return raw_bytes
    try:
        return base64.b64decode(audio_b64)
    except (base64.binascii.Error, TypeError):
        logger.warning("Failed to base64 decode Groq audio payload")
        return raw_bytes


def _split_text_for_tts(text: str, max_chars: int) -> Iterable[str]:
    """Split text into chunks safe for Orpheus (<= max_chars)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    if max_chars <= 0:
        return [cleaned]
    if len(cleaned) <= max_chars:
        return [cleaned]

    chunks: list[str] = []
    remaining = cleaned
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break

        window = remaining[: max_chars + 1]
        # Prefer sentence boundary within window.
        split_at = max(window.rfind("."), window.rfind("!"), window.rfind("?"))
        if split_at <= 0:
            # Fallback to whitespace.
            split_at = window.rfind(" ")
        if split_at <= 0:
            split_at = max_chars

        part = remaining[:split_at].strip()
        if part:
            chunks.append(part)
        remaining = remaining[split_at:].strip()

    return chunks


# Groq STT Adapter ----------------------------------------------------------------


@dataclass
class _GroqSTTSessionState:
    options: Dict[str, Any]
    http_session: aiohttp.ClientSession


class GroqSTTAdapter(STTComponent):
    """Groq Whisper-based transcription adapter (REST, file upload)."""

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: GroqSTTProviderConfig,
        options: Optional[Dict[str, Any]] = None,
        *,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_defaults = provider_config
        self._pipeline_defaults = options or {}
        self._session_factory = session_factory
        self._sessions: Dict[str, _GroqSTTSessionState] = {}

    async def start(self) -> None:
        logger.debug("Groq STT adapter initialized", component=self.component_key, default_model=self._provider_defaults.stt_model)

    async def stop(self) -> None:
        for call_id in list(self._sessions.keys()):
            await self.close_call(call_id)

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        merged = self._compose_options(options)
        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("Groq STT requires GROQ_API_KEY")

        factory = self._session_factory or aiohttp.ClientSession
        http_session = factory()
        self._sessions[call_id] = _GroqSTTSessionState(options=merged, http_session=http_session)

        logger.info(
            "Groq STT session opened",
            call_id=call_id,
            model=merged.get("model"),
            response_format=merged.get("response_format"),
        )

    async def close_call(self, call_id: str) -> None:
        session = self._sessions.pop(call_id, None)
        if not session:
            return
        try:
            await session.http_session.close()
        finally:
            logger.info("Groq STT session closed", call_id=call_id)

    async def transcribe(
        self,
        call_id: str,
        audio_pcm16: bytes,
        sample_rate_hz: int,
        options: Dict[str, Any],
    ) -> str:
        session = self._sessions.get(call_id)
        if not session:
            raise RuntimeError(f"Groq STT session not found for call {call_id}")

        merged = _merge_dicts(session.options, options or {})
        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("Groq STT requires GROQ_API_KEY")

        request_id = f"groq-stt-{uuid.uuid4().hex[:12]}"
        timeout_sec = float(merged.get("request_timeout_sec", 15.0))
        model = merged.get("model")
        response_format = merged.get("response_format") or "json"

        # Send WAV @ 16 kHz mono PCM16 for consistent latency/size.
        target_rate = 16000
        pcm_bytes = audio_pcm16 or b""
        if sample_rate_hz != target_rate and pcm_bytes:
            pcm_bytes, _ = resample_audio(pcm_bytes, int(sample_rate_hz), target_rate)

        wav_bytes = _pcm16le_to_wav_bytes(pcm_bytes, target_rate)
        form = aiohttp.FormData()
        form.add_field("file", wav_bytes, filename="audio.wav", content_type="audio/wav")
        form.add_field("model", str(model))
        if merged.get("language"):
            form.add_field("language", str(merged["language"]))
        if merged.get("prompt"):
            form.add_field("prompt", str(merged["prompt"]))
        if merged.get("temperature") is not None:
            form.add_field("temperature", str(merged["temperature"]))
        if response_format:
            form.add_field("response_format", str(response_format))
        timestamp_granularities = merged.get("timestamp_granularities")
        if timestamp_granularities:
            for val in list(timestamp_granularities):
                form.add_field("timestamp_granularities[]", str(val))

        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Asterisk-AI-Voice-Agent/1.0",
        }

        url = merged.get("stt_base_url") or merged.get("base_url") or self._provider_defaults.stt_base_url
        started_at = time.perf_counter()
        async with session.http_session.post(
            url,
            data=form,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout_sec),
        ) as resp:
            body = await resp.read()
            if resp.status >= 400:
                body_preview = body.decode("utf-8", errors="ignore")[:200]
                logger.error(
                    "Groq STT request failed",
                    call_id=call_id,
                    request_id=request_id,
                    status=resp.status,
                    body_preview=body_preview,
                )
                resp.raise_for_status()

        latency_ms = (time.perf_counter() - started_at) * 1000.0
        transcript = self._parse_transcript(body, response_format=response_format)
        logger.info(
            "Groq STT transcript received",
            call_id=call_id,
            request_id=request_id,
            latency_ms=round(latency_ms, 2),
            transcript_preview=(transcript or "")[:80],
        )
        return transcript or ""

    async def validate_connectivity(self, options: Dict[str, Any]) -> Dict[str, Any]:
        # Ensure base connectivity checks see the provider defaults (stt_base_url).
        merged = self._compose_options(options or {})
        return await super().validate_connectivity(merged)

    @staticmethod
    def _parse_transcript(payload: bytes, *, response_format: str) -> str:
        fmt = (response_format or "json").lower()
        if fmt == "text":
            return payload.decode("utf-8", errors="ignore").strip()

        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            return payload.decode("utf-8", errors="ignore").strip()

        text = data.get("text")
        if isinstance(text, str):
            return text.strip()
        return ""

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        runtime_options = runtime_options or {}
        model = runtime_options.get(
            "model",
            runtime_options.get(
                "stt_model",
                self._pipeline_defaults.get("model", self._pipeline_defaults.get("stt_model", self._provider_defaults.stt_model)),
            ),
        )
        merged = {
            "api_key": runtime_options.get("api_key", self._pipeline_defaults.get("api_key", self._provider_defaults.api_key)),
            "stt_base_url": runtime_options.get(
                "stt_base_url",
                runtime_options.get("base_url", self._pipeline_defaults.get("stt_base_url", self._provider_defaults.stt_base_url)),
            ),
            "model": model,
            "language": runtime_options.get("language", self._pipeline_defaults.get("language", self._provider_defaults.language)),
            "prompt": runtime_options.get("prompt", self._pipeline_defaults.get("prompt", self._provider_defaults.prompt)),
            "response_format": runtime_options.get(
                "response_format",
                self._pipeline_defaults.get("response_format", self._provider_defaults.response_format),
            ),
            "temperature": runtime_options.get(
                "temperature",
                self._pipeline_defaults.get("temperature", self._provider_defaults.temperature),
            ),
            "timestamp_granularities": runtime_options.get(
                "timestamp_granularities",
                self._pipeline_defaults.get("timestamp_granularities", self._provider_defaults.timestamp_granularities),
            ),
            "request_timeout_sec": float(
                runtime_options.get(
                    "request_timeout_sec",
                    self._pipeline_defaults.get("request_timeout_sec", self._provider_defaults.request_timeout_sec),
                )
            ),
        }
        return merged


# Groq TTS Adapter ----------------------------------------------------------------


class GroqTTSAdapter(TTSComponent):
    """Groq Orpheus TTS adapter (REST, WAV response)."""

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: GroqTTSProviderConfig,
        options: Optional[Dict[str, Any]] = None,
        *,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_defaults = provider_config
        self._pipeline_defaults = options or {}
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        logger.debug("Groq TTS adapter initialized", component=self.component_key, default_model=self._provider_defaults.tts_model)

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        await self._ensure_session()

    async def close_call(self, call_id: str) -> None:
        return

    async def synthesize(self, call_id: str, text: str, options: Dict[str, Any]) -> AsyncIterator[bytes]:
        if not text:
            return
            yield

        await self._ensure_session()
        assert self._session is not None

        merged = self._compose_options(options)
        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("Groq TTS requires GROQ_API_KEY")

        target_encoding = merged["format"]["encoding"]
        target_sample_rate = int(merged["format"]["sample_rate"])
        chunk_ms = int(merged.get("chunk_size_ms", self._provider_defaults.chunk_size_ms))
        max_chars = int(merged.get("max_input_chars", self._provider_defaults.max_input_chars))

        for part in _split_text_for_tts(text, max_chars):
            if not part:
                continue
            audio_pcm = await self._synthesize_one(call_id, part, merged)
            if not audio_pcm:
                continue
            pcm_bytes, source_rate = audio_pcm
            if source_rate and source_rate != target_sample_rate:
                pcm_bytes, _ = resample_audio(pcm_bytes, source_rate, target_sample_rate)
            converted = convert_pcm16le_to_target_format(pcm_bytes, target_encoding)

            for chunk in _chunk_audio(converted, target_encoding, target_sample_rate, chunk_ms):
                if chunk:
                    yield chunk

    async def validate_connectivity(self, options: Dict[str, Any]) -> Dict[str, Any]:
        # Ensure base connectivity checks see the provider defaults (tts_base_url).
        merged = self._compose_options(options or {})
        return await super().validate_connectivity(merged)

    async def _synthesize_one(self, call_id: str, text: str, merged: Dict[str, Any]) -> Tuple[bytes, int]:
        assert self._session is not None
        request_id = f"groq-tts-{uuid.uuid4().hex[:12]}"
        url = merged.get("tts_base_url") or merged.get("base_url") or self._provider_defaults.tts_base_url
        timeout_sec = float(merged.get("request_timeout_sec", self._provider_defaults.request_timeout_sec))

        payload = {
            "model": merged["model"],
            "input": text,
            "voice": merged["voice"],
            "response_format": merged.get("response_format", "wav"),
        }

        headers = {
            "Authorization": f"Bearer {merged['api_key']}",
            "Content-Type": "application/json",
            "User-Agent": "Asterisk-AI-Voice-Agent/1.0",
        }

        logger.info(
            "Groq TTS synthesis started",
            call_id=call_id,
            request_id=request_id,
            model=payload["model"],
            voice=payload["voice"],
            text_preview=text[:64],
        )

        started_at = time.perf_counter()
        async with self._session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout_sec),
        ) as resp:
            raw = await resp.read()
            if resp.status >= 400:
                body_preview = raw.decode("utf-8", errors="ignore")[:200]
                logger.error(
                    "Groq TTS request failed",
                    call_id=call_id,
                    request_id=request_id,
                    status=resp.status,
                    body_preview=body_preview,
                )
                resp.raise_for_status()

        audio_bytes = _decode_audio_payload(raw)
        # Orpheus docs: wav only; decode WAV into PCM16 + sample rate.
        pcm_bytes, sample_rate = _wav_bytes_to_pcm16le(audio_bytes)

        latency_ms = (time.perf_counter() - started_at) * 1000.0
        logger.info(
            "Groq TTS synthesis completed",
            call_id=call_id,
            request_id=request_id,
            latency_ms=round(latency_ms, 2),
            pcm_bytes=len(pcm_bytes),
            sample_rate=sample_rate,
        )
        return pcm_bytes, sample_rate

    async def _ensure_session(self) -> None:
        if self._session and not self._session.closed:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory()

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        runtime_options = runtime_options or {}

        format_defaults = self._pipeline_defaults.get("format", {})
        merged_format = {
            "encoding": runtime_options.get("format", {}).get(
                "encoding",
                format_defaults.get("encoding", self._provider_defaults.target_encoding),
            ),
            "sample_rate": int(
                runtime_options.get("format", {}).get(
                    "sample_rate",
                    format_defaults.get("sample_rate", self._provider_defaults.target_sample_rate_hz),
                )
            ),
        }

        return {
            "api_key": runtime_options.get("api_key", self._pipeline_defaults.get("api_key", self._provider_defaults.api_key)),
            "tts_base_url": runtime_options.get(
                "tts_base_url",
                runtime_options.get("base_url", self._pipeline_defaults.get("tts_base_url", self._provider_defaults.tts_base_url)),
            ),
            "model": runtime_options.get(
                "model",
                runtime_options.get(
                    "tts_model",
                    self._pipeline_defaults.get("model", self._pipeline_defaults.get("tts_model", self._provider_defaults.tts_model)),
                ),
            ),
            "voice": runtime_options.get("voice", self._pipeline_defaults.get("voice", self._provider_defaults.voice)),
            "response_format": runtime_options.get(
                "response_format",
                self._pipeline_defaults.get("response_format", self._provider_defaults.response_format),
            ),
            "max_input_chars": int(
                runtime_options.get("max_input_chars", self._pipeline_defaults.get("max_input_chars", self._provider_defaults.max_input_chars))
            ),
            "chunk_size_ms": int(
                runtime_options.get("chunk_size_ms", self._pipeline_defaults.get("chunk_size_ms", self._provider_defaults.chunk_size_ms))
            ),
            "request_timeout_sec": float(
                runtime_options.get("request_timeout_sec", self._pipeline_defaults.get("request_timeout_sec", self._provider_defaults.request_timeout_sec))
            ),
            "format": merged_format,
        }


__all__ = ["GroqSTTAdapter", "GroqTTSAdapter"]
