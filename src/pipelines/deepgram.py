"""
# Milestone7: Deepgram cloud component adapters for configurable pipelines.

This module introduces concrete implementations for Deepgram STT and TTS adapters
used by the pipeline orchestrator. Both adapters honour pipeline/provider options,
support latency-aware logging, and integrate with Deepgram's REST APIs for
pre-recorded STT (batch processing) and TTS (synthesis).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Iterable, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import aiohttp
import websockets

from ..audio import convert_pcm16le_to_target_format, mulaw_to_pcm16le, resample_audio
from ..config import AppConfig, DeepgramProviderConfig
from ..logging_config import get_logger
from .base import STTComponent, TTSComponent

logger = get_logger(__name__)


# Shared helpers -----------------------------------------------------------------


def _normalize_stt_url(base_url: Optional[str]) -> str:
    """Normalize base URL to Deepgram pre-recorded STT endpoint."""
    default = "https://api.deepgram.com/v1/listen"
    if not base_url:
        return default
    parsed = urlparse(base_url)
    if parsed.path.endswith("/v1/listen"):
        return urlunparse(parsed)
    path = parsed.path.rstrip("/") + "/v1/listen"
    return urlunparse(parsed._replace(path=path))


def _normalize_rest_url(base_url: Optional[str]) -> str:
    default = "https://api.deepgram.com/v1/speak"
    if not base_url:
        return default
    parsed = urlparse(base_url)
    if parsed.path.endswith("/v1/speak"):
        return urlunparse(parsed)
    path = parsed.path.rstrip("/") + "/v1/speak"
    return urlunparse(parsed._replace(path=path))


def _merge_dicts(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(base)
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


# Deepgram STT Adapter ------------------------------------------------------------


@dataclass
class _STTSessionState:
    """Tracks per-call STT session state (API key, model, encoding, etc)."""
    options: Dict[str, Any]
    http_session: aiohttp.ClientSession
    # Streaming fields (optional, only used when streaming: true)
    websocket: Optional[Any] = None
    transcript_queue: Optional[asyncio.Queue] = None
    receiver_task: Optional[asyncio.Task] = None
    active: bool = True


class DeepgramSTTAdapter(STTComponent):
    """
    # Milestone7: Deepgram STT adapter with dual-mode support.

    Supports both REST (batch) and WebSocket (streaming) modes:
    - REST mode (streaming: false): HTTP POST for discrete chunks
    - Streaming mode (streaming: true): WebSocket for continuous audio

    Configure via pipeline options:
      options:
        stt:
          streaming: false  # Use REST API (transcribe method)
          streaming: true   # Use WebSocket streaming (start_stream, send_audio, iter_results)

    Note: This is separate from src/providers/deepgram.py (monolithic full agent).
    """

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: DeepgramProviderConfig,
        options: Optional[Dict[str, Any]] = None,
        *,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_defaults = provider_config
        self._pipeline_defaults = options or {}
        self._session_factory = session_factory
        self._sessions: Dict[str, _STTSessionState] = {}
        self._default_timeout = float(self._pipeline_defaults.get("response_timeout_sec", 5.0))

    async def start(self) -> None:
        # No global warm-up required yet.
        logger.debug(
            "Deepgram STT adapter initialized",
            component=self.component_key,
            default_model=self._provider_defaults.model,
        )

    async def stop(self) -> None:
        # Close any lingering sessions.
        for call_id in list(self._sessions.keys()):
            await self.close_call(call_id)

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        merged = self._compose_options(options)
        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("Deepgram STT requires an API key")

        # Create HTTP session for reuse across multiple transcribe() calls
        factory = self._session_factory or aiohttp.ClientSession
        http_session = factory()
        self._sessions[call_id] = _STTSessionState(options=merged, http_session=http_session)

        logger.info(
            "Deepgram STT session opened (REST API)",
            call_id=call_id,
            model=merged.get("model"),
            encoding=merged.get("encoding"),
            sample_rate=merged.get("sample_rate"),
            component=self.component_key,
        )

    async def close_call(self, call_id: str) -> None:
        session = self._sessions.pop(call_id, None)
        if not session:
            return
        
        # Close streaming resources if active
        if session.websocket:
            session.active = False
            if session.receiver_task and not session.receiver_task.done():
                session.receiver_task.cancel()
                try:
                    await session.receiver_task
                except asyncio.CancelledError:
                    pass
            try:
                await session.websocket.close()
            except Exception:
                pass
        
        # Close HTTP session
        try:
            await session.http_session.close()
        finally:
            logger.info("Deepgram STT session closed", call_id=call_id)

    async def transcribe(
        self,
        call_id: str,
        audio_pcm16: bytes,
        sample_rate_hz: int,
        options: Dict[str, Any],
    ) -> str:
        session = self._sessions.get(call_id)
        if not session:
            raise RuntimeError(f"Deepgram STT session not found for call {call_id}")

        merged = _merge_dicts(session.options, options or {})
        timeout = float(merged.get("response_timeout_sec", self._default_timeout))
        request_id = f"dg-stt-{uuid.uuid4().hex[:12]}"

        # Get API requirements from session options (set during open_call)
        api_encoding = merged.get("encoding", "linear16")
        api_sample_rate = int(merged.get("sample_rate", 16000))
        
        # Transcode audio to match API expectations
        api_audio = audio_pcm16
        
        # Resample if needed
        if sample_rate_hz != api_sample_rate:
            import audioop
            api_audio, _ = audioop.ratecv(api_audio, 2, 1, sample_rate_hz, api_sample_rate, None)
            logger.debug(
                "STT resampled audio",
                call_id=call_id,
                from_rate=sample_rate_hz,
                to_rate=api_sample_rate,
                bytes=len(api_audio),
            )
        
        # Encode if needed
        if api_encoding in ("mulaw", "g711_ulaw", "mu-law"):
            import audioop
            api_audio = audioop.lin2ulaw(api_audio, 2)
            logger.debug("STT encoded PCM16 → mulaw", call_id=call_id, bytes=len(api_audio))
        elif api_encoding in ("alaw", "g711_alaw"):
            import audioop
            api_audio = audioop.lin2alaw(api_audio, 2)
            logger.debug("STT encoded PCM16 → alaw", call_id=call_id, bytes=len(api_audio))
        # "linear16", "pcm16" = no encoding needed

        # Build query parameters
        query_params = {
            "model": merged.get("model"),
            "language": merged.get("language"),
            "smart_format": str(merged.get("smart_format", True)).lower(),
        }
        query_params = {k: v for k, v in query_params.items() if v}

        # Build REST URL
        base_url = _normalize_stt_url(merged.get("base_url"))
        parsed = urlparse(base_url)
        existing = dict(parse_qsl(parsed.query))
        existing.update({k: str(v) for k, v in query_params.items()})
        rest_url = urlunparse(parsed._replace(query=urlencode(existing)))

        # Determine Content-Type based on encoding
        if api_encoding in ("mulaw", "g711_ulaw", "mu-law"):
            content_type = f"audio/mulaw; rate={api_sample_rate}"
        elif api_encoding in ("alaw", "g711_alaw"):
            content_type = f"audio/alaw; rate={api_sample_rate}"
        elif api_encoding in ("linear16", "pcm16"):
            content_type = f"audio/pcm; rate={api_sample_rate}"
        else:
            content_type = "audio/wav"

        headers = {
            "Authorization": f"Token {merged.get('api_key')}",
            "Content-Type": content_type,
            "User-Agent": "Asterisk-AI-Voice-Agent/1.0",
        }

        logger.debug(
            "Deepgram STT sending audio chunk (REST)",
            call_id=call_id,
            request_id=request_id,
            chunk_bytes=len(api_audio),
            api_encoding=api_encoding,
            api_sample_rate=api_sample_rate,
            url=rest_url,
        )

        started_at = time.perf_counter()
        try:
            async with session.http_session.post(
                rest_url,
                headers=headers,
                data=api_audio,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise RuntimeError(
                        f"Deepgram API error {response.status}: {error_text}"
                    )
                
                result = await response.json()
                transcript = self._extract_transcript_from_rest(result)
                
                if not transcript:
                    raise RuntimeError("No transcript in Deepgram response")
                
                latency_ms = (time.perf_counter() - started_at) * 1000.0
                logger.info(
                    "Deepgram STT transcript received (REST)",
                    call_id=call_id,
                    request_id=request_id,
                    latency_ms=round(latency_ms, 2),
                    transcript_preview=transcript[:50],
                )
                return transcript
                
        except asyncio.TimeoutError:
            logger.warning(
                "Deepgram STT request timeout",
                call_id=call_id,
                request_id=request_id,
                timeout_sec=timeout,
            )
            raise
        except Exception as exc:
            logger.warning(
                "Deepgram STT request failed",
                call_id=call_id,
                request_id=request_id,
                error=str(exc),
            )
            raise

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        runtime_options = runtime_options or {}
        merged = {
            "base_url": runtime_options.get("base_url", self._pipeline_defaults.get("base_url", self._provider_defaults.base_url)),
            "model": runtime_options.get("model", self._pipeline_defaults.get("model", self._provider_defaults.model)),
            "language": runtime_options.get("language", self._pipeline_defaults.get("language", self._provider_defaults.stt_language)),
            "encoding": runtime_options.get("encoding", self._pipeline_defaults.get("encoding", self._provider_defaults.input_encoding)),
            "sample_rate": runtime_options.get("sample_rate", self._pipeline_defaults.get("sample_rate", self._provider_defaults.input_sample_rate_hz)),
            "smart_format": runtime_options.get("smart_format", self._pipeline_defaults.get("smart_format", True)),
            "api_key": runtime_options.get("api_key", self._pipeline_defaults.get("api_key", self._provider_defaults.api_key)),
        }
        if runtime_options.get("response_timeout_sec") is not None:
            merged["response_timeout_sec"] = runtime_options["response_timeout_sec"]
        return merged

    @staticmethod
    def _extract_transcript_from_rest(result: Dict[str, Any]) -> Optional[str]:
        """
        Extract transcript from Deepgram pre-recorded API response.
        Response format:
        {
          "results": {
            "channels": [
              {
                "alternatives": [
                  {"transcript": "...", "confidence": 0.95}
                ]
              }
            ]
          }
        }
        """
        try:
            results = result.get("results", {})
            channels = results.get("channels", [])
            if not channels:
                return None
            
            alternatives = channels[0].get("alternatives", [])
            if not alternatives:
                return None
            
            transcript = alternatives[0].get("transcript", "")
            return transcript.strip() if transcript else None
        except (KeyError, IndexError, AttributeError) as exc:
            logger.debug("Failed to extract transcript from Deepgram response", error=str(exc))
            return None

    # Streaming Methods (for streaming: true mode) --------------------------------

    async def start_stream(self, call_id: str, options: Dict[str, Any]) -> None:
        """Open WebSocket streaming connection to Deepgram."""
        session = self._sessions.get(call_id)
        if not session:
            raise RuntimeError(f"Deepgram STT session not found for call {call_id}")

        merged = _merge_dicts(session.options, options or {})
        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("Deepgram STT streaming requires an API key")

        # Build WebSocket URL
        base_url = merged.get("base_url", "https://api.deepgram.com")
        # Convert https:// to wss:// for WebSocket
        if base_url.startswith("https://"):
            ws_base = base_url.replace("https://", "wss://", 1)
        elif base_url.startswith("http://"):
            ws_base = base_url.replace("http://", "ws://", 1)
        else:
            ws_base = base_url

        # Build v1/listen endpoint
        parsed = urlparse(ws_base)
        if not parsed.path or parsed.path == "/":
            path = "/v1/listen"
        elif "/v1/listen" in parsed.path:
            path = parsed.path
        else:
            path = parsed.path.rstrip("/") + "/v1/listen"

        # Build query parameters
        query_params = {
            "model": merged.get("model", "nova-2"),
            "language": merged.get("language", "en-US"),
            "encoding": merged.get("encoding", "linear16"),
            "sample_rate": str(merged.get("sample_rate", 16000)),
            "channels": "1",
        }
        existing = dict(parse_qsl(parsed.query))
        existing.update(query_params)
        ws_url = urlunparse(parsed._replace(path=path, query=urlencode(existing)))

        logger.info(
            "Deepgram STT opening streaming session",
            call_id=call_id,
            url=ws_url,
            component=self.component_key,
        )

        headers = [
            ("Authorization", f"Token {api_key}"),
            ("User-Agent", "Asterisk-AI-Voice-Agent/1.0"),
        ]

        try:
            websocket = await websockets.connect(
                ws_url,
                additional_headers=headers,
                max_size=16 * 1024 * 1024,
                ping_interval=20,
                ping_timeout=10,
            )
        except Exception as exc:
            logger.error(
                "Failed to connect to Deepgram streaming",
                call_id=call_id,
                error=str(exc),
                exc_info=True,
            )
            raise RuntimeError(f"Deepgram streaming connection failed: {exc}") from exc

        # Update session with streaming resources
        session.websocket = websocket
        session.transcript_queue = asyncio.Queue(maxsize=8)
        session.active = True

        # Start async receiver task
        session.receiver_task = asyncio.create_task(
            self._receive_loop(call_id, session)
        )

        logger.info(
            "Deepgram STT streaming session opened",
            call_id=call_id,
            model=merged.get("model"),
        )

    async def send_audio(
        self,
        call_id: str,
        audio_pcm16: bytes,
        fmt: str = "pcm16_16k",
    ) -> None:
        """Send audio chunk to Deepgram streaming WebSocket."""
        session = self._sessions.get(call_id)
        if not session or not session.websocket or not session.active:
            return

        try:
            await session.websocket.send(audio_pcm16)
        except (websockets.ConnectionClosed, websockets.WebSocketException) as exc:
            logger.warning(
                "Deepgram streaming websocket closed while sending audio",
                call_id=call_id,
                error=str(exc),
            )
            session.active = False
        except Exception as exc:
            logger.error(
                "Error sending audio to Deepgram streaming",
                call_id=call_id,
                error=str(exc),
                exc_info=True,
            )

    async def iter_results(self, call_id: str) -> AsyncIterator[str]:
        """Yield transcripts as they arrive from Deepgram streaming."""
        session = self._sessions.get(call_id)
        if not session or not session.transcript_queue:
            return

        while True:
            try:
                transcript = await session.transcript_queue.get()
                if transcript is None:
                    break
                yield transcript
            except asyncio.CancelledError:
                break

    async def stop_stream(self, call_id: str) -> None:
        """Stop streaming session (cleanup handled in close_call)."""
        session = self._sessions.get(call_id)
        if session and session.websocket:
            logger.debug(
                "Deepgram STT stop_stream (cleanup in close_call)",
                call_id=call_id,
            )

    async def _receive_loop(self, call_id: str, session: _STTSessionState) -> None:
        """Async receiver loop for Deepgram streaming results."""
        try:
            async for message in session.websocket:
                if not session.active:
                    break

                try:
                    data = json.loads(message) if isinstance(message, str) else message
                except (json.JSONDecodeError, TypeError):
                    continue

                msg_type = data.get("type")

                if msg_type == "Results":
                    transcript = self._extract_transcript_from_streaming(data)
                    if transcript:
                        is_final = data.get("is_final", False)
                        speech_final = data.get("speech_final", False)

                        logger.debug(
                            "Deepgram streaming transcript received",
                            call_id=call_id,
                            transcript_preview=transcript[:50],
                            is_final=is_final,
                            speech_final=speech_final,
                        )

                        # Only queue final transcripts
                        if is_final and transcript.strip():
                            try:
                                session.transcript_queue.put_nowait(transcript)
                            except asyncio.QueueFull:
                                # Drop oldest transcript if queue full
                                try:
                                    session.transcript_queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    pass
                                await session.transcript_queue.put(transcript)

        except websockets.ConnectionClosed:
            logger.info(
                "Deepgram streaming websocket closed",
                call_id=call_id,
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(
                "Deepgram streaming receive loop error",
                call_id=call_id,
                error=str(exc),
                exc_info=True,
            )
        finally:
            session.active = False
            # Signal end to transcript queue
            if session.transcript_queue:
                try:
                    session.transcript_queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass

    @staticmethod
    def _extract_transcript_from_streaming(message: Dict[str, Any]) -> Optional[str]:
        """Extract transcript from Deepgram streaming Results message."""
        try:
            channel = message.get("channel", {})
            alternatives = channel.get("alternatives", [])
            if alternatives:
                return alternatives[0].get("transcript", "").strip()
        except (KeyError, IndexError, AttributeError):
            pass
        return None


# Deepgram TTS Adapter ------------------------------------------------------------


class DeepgramTTSAdapter(TTSComponent):
    """
    # Milestone7: Deepgram REST TTS adapter with μ-law conversion and chunking.
    """

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: DeepgramProviderConfig,
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
        logger.debug(
            "Deepgram TTS adapter initialized",
            component=self.component_key,
            default_voice=self._provider_defaults.tts_model,
        )

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        # No call-scoped preparation required beyond ensuring the session exists.
        await self._ensure_session()

    async def close_call(self, call_id: str) -> None:
        # Nothing to tear down per call.
        return

    async def synthesize(
        self,
        call_id: str,
        text: str,
        options: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        if not text:
            return  # Exit early - yields nothing (async generator)
            yield  # Unreachable but makes this an async generator
        await self._ensure_session()

        merged = self._compose_options(options)
        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("Deepgram TTS requires an API key")

        target_format = merged["format"]
        target_encoding = target_format.get("encoding", "mulaw")
        target_sample_rate = int(target_format.get("sample_rate", 8000))

        request_id = f"dg-tts-{uuid.uuid4().hex[:12]}"
        url, params = self._build_tts_request(merged, target_encoding, target_sample_rate)

        logger.info(
            "Deepgram TTS synthesis started",
            call_id=call_id,
            request_id=request_id,
            text_preview=text[:64],
            url=url,
            params=params,
        )

        payload = {"text": text}
        headers = {
            "Authorization": f"Token {api_key}",
            "Accept": "audio/*",
            "Content-Type": "application/json",
        }

        started_at = time.perf_counter()
        async with self._session.post(url, json=payload, params=params, headers=headers) as response:
            if response.status >= 400:
                body = await response.text()
                logger.error(
                    "Deepgram TTS synthesis failed",
                    call_id=call_id,
                    request_id=request_id,
                    status=response.status,
                    body=body,
                )
                response.raise_for_status()

            raw_audio = await response.read()
            source_encoding = params.get("encoding", "linear16")
            source_sample_rate = int(params.get("sample_rate", target_sample_rate))
            converted = self._convert_audio(raw_audio, source_encoding, source_sample_rate, target_encoding, target_sample_rate)
            latency_ms = (time.perf_counter() - started_at) * 1000.0

        logger.info(
            "Deepgram TTS synthesis completed",
            call_id=call_id,
            request_id=request_id,
            latency_ms=round(latency_ms, 2),
            output_bytes=len(converted),
            target_encoding=target_encoding,
            target_sample_rate=target_sample_rate,
        )

        chunk_ms = int(merged.get("chunk_size_ms", 20))
        for chunk in self._chunk_audio(converted, target_encoding, target_sample_rate, chunk_ms):
            if chunk:
                yield chunk

    async def _ensure_session(self) -> None:
        if self._session and not self._session.closed:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory()

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        runtime_options = runtime_options or {}
        format_defaults = self._pipeline_defaults.get("format", {})
        merged_format = {
            "encoding": runtime_options.get("format", {}).get("encoding", format_defaults.get("encoding", "mulaw")),
            "sample_rate": runtime_options.get("format", {}).get("sample_rate", format_defaults.get("sample_rate", 8000)),
        }

        merged = {
            "base_url": runtime_options.get("base_url", self._pipeline_defaults.get("base_url", self._provider_defaults.base_url)),
            "model": runtime_options.get("model", self._pipeline_defaults.get("model", self._provider_defaults.tts_model or self._provider_defaults.model)),
            "voice": runtime_options.get("voice", self._pipeline_defaults.get("voice", self._provider_defaults.tts_model)),
            "language": runtime_options.get("language", self._pipeline_defaults.get("language", self._provider_defaults.stt_language)),
            "chunk_size_ms": runtime_options.get("chunk_size_ms", self._pipeline_defaults.get("chunk_size_ms", 20)),
            "api_key": runtime_options.get("api_key", self._pipeline_defaults.get("api_key", self._provider_defaults.api_key)),
            "format": merged_format,
        }
        # Default the provider output (source_format) sample rate to the target sample rate
        # so we request 8 kHz from Deepgram when our downstream is μ-law 8 kHz.
        source_cfg = runtime_options.get("source_format", self._pipeline_defaults.get("source_format", {}))
        default_source_rate = int(merged_format.get("sample_rate", 8000))
        merged["source_format"] = {
            "encoding": source_cfg.get("encoding", "linear16"),
            "sample_rate": int(source_cfg.get("sample_rate", default_source_rate)),
        }
        return merged

    def _build_tts_request(
        self,
        options: Dict[str, Any],
        target_encoding: str,
        target_sample_rate: int,
    ) -> Tuple[str, Dict[str, Any]]:
        url = _normalize_rest_url(options.get("base_url"))
        params: Dict[str, Any] = {
            "model": options.get("model") or options.get("voice"),
            "voice": options.get("voice"),
            "language": options.get("language"),
        }
        source_format = options.get("source_format", {})
        params["encoding"] = source_format.get("encoding", "linear16")
        # Request provider to emit audio at the downstream target sample rate by default
        params["sample_rate"] = int(source_format.get("sample_rate", target_sample_rate))
        params["target_encoding"] = target_encoding
        params["target_sample_rate"] = target_sample_rate
        # Remove None values
        params = {k: v for k, v in params.items() if v is not None}
        return url, params

    @staticmethod
    def _convert_audio(
        audio_bytes: bytes,
        source_encoding: str,
        source_rate: int,
        target_encoding: str,
        target_rate: int,
    ) -> bytes:
        if not audio_bytes:
            return b""

        fmt = (source_encoding or "").lower()
        if fmt in ("ulaw", "mulaw", "mu-law", "g711_ulaw"):
            pcm_bytes = mulaw_to_pcm16le(audio_bytes)
        else:
            pcm_bytes = audio_bytes

        if source_rate != target_rate:
            pcm_bytes, _ = resample_audio(pcm_bytes, source_rate, target_rate)

        return convert_pcm16le_to_target_format(pcm_bytes, target_encoding)

    @staticmethod
    def _chunk_audio(
        audio_bytes: bytes,
        encoding: str,
        sample_rate: int,
        chunk_ms: int,
    ) -> Iterable[bytes]:
        if not audio_bytes:
            return
        bytes_per = _bytes_per_sample(encoding)
        frame_size = max(bytes_per, int(sample_rate * (chunk_ms / 1000.0) * bytes_per))
        for idx in range(0, len(audio_bytes), frame_size):
            yield audio_bytes[idx : idx + frame_size]
