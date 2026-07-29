"""
Deepgram Flux STT Adapter for Pipeline Orchestrator.

Flux is Deepgram's conversational speech recognition model built specifically
for voice agents with built-in turn detection and ultra-low latency.

Key Features:
- Built-in end-of-turn detection (~260ms latency)
- EagerEndOfTurn events for early LLM triggering
- Continuous bidirectional streaming (required for Flux)
- Natural interruption/barge-in handling
- Nova-3 level accuracy

Requirements:
- Endpoint: wss://api.deepgram.com/v2/listen (NOT /v1/listen)
- Model: flux-general-en
- Pattern: Continuous audio sending + async result receiving

Documentation: https://developers.deepgram.com/docs/flux/quickstart
"""

import asyncio
import json
import time
import uuid
from typing import Any, AsyncGenerator, Dict, Optional
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import websockets

from ..config import AppConfig, DeepgramProviderConfig
from ..logging_config import get_logger
from .base import STREAMING_STT_FORMAT_ALIASES, STTComponent

logger = get_logger(__name__)


def _normalize_ws_url(base_url: Optional[str]) -> str:
    """Ensure we use the v2 endpoint for Flux with correct websocket scheme."""
    default = "wss://api.deepgram.com/v2/listen"
    if not base_url:
        return default
    parsed = urlparse(base_url)
    
    # Convert HTTP(S) to WS(S) scheme for websocket
    scheme = parsed.scheme
    if scheme == "https":
        scheme = "wss"
    elif scheme == "http":
        scheme = "ws"
    elif scheme not in ("ws", "wss"):
        # If scheme is something else, default to wss
        scheme = "wss"
    
    # Force v2 endpoint for Flux
    path = parsed.path
    if "/v1/" in path:
        path = path.replace("/v1/", "/v2/")
    elif not path or path == "/":
        path = "/v2/listen"
    
    return urlunparse(parsed._replace(scheme=scheme, path=path))


class _FluxSessionState:
    """Per-call session state for Deepgram Flux STT."""
    
    def __init__(self, websocket: Any, options: Dict[str, Any], session_id: str):
        self.websocket = websocket
        self.options = options
        self.session_id = session_id
        self.transcript_queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=8)
        self.turn_complete_event = asyncio.Event()
        self.receiver_task: Optional[asyncio.Task] = None
        self.active = True


class DeepgramFluxSTTAdapter(STTComponent):
    """
    Deepgram Flux STT adapter with continuous streaming and turn detection.
    
    This adapter implements the correct pattern for Flux:
    - Continuous audio sending (no request/response blocking)
    - Async result receiver running in parallel
    - Turn detection via EndOfTurn events
    - Optional EagerEndOfTurn for early LLM triggering
    """

    supports_streaming = True
    
    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: DeepgramProviderConfig,
        options: Optional[Dict[str, Any]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_defaults = provider_config
        self._pipeline_defaults = options or {}
        self._sessions: Dict[str, _FluxSessionState] = {}
        
    async def start(self) -> None:
        logger.debug(
            "Deepgram Flux STT adapter initialized",
            component=self.component_key,
        )
    
    async def stop(self) -> None:
        # Close any lingering sessions
        for call_id in list(self._sessions.keys()):
            await self.close_call(call_id)
    
    async def validate_connectivity(self, options: Dict[str, Any]) -> Dict[str, Any]:
        """Validate Deepgram Flux - uses base class but adds Flux-specific query params."""
        merged = self._compose_options(options)
        
        # Build Flux-specific URL with required query parameters
        # Always use WSS for Flux, ignore HTTP(S) from provider config
        base_url = merged.get("base_url", "")
        if not base_url or not base_url.startswith("wss://"):
            base_url = "wss://api.deepgram.com/v2/listen"
        query_params = {
            "model": "flux-general-en",
            "language": merged.get("language", "en-US"),
            "encoding": merged.get("encoding", "linear16"),
            "sample_rate": merged.get("sample_rate", "16000"),
            "channels": "1",
        }
        
        parsed = urlparse(base_url)
        existing = dict(parse_qsl(parsed.query))
        existing.update(query_params)
        flux_url = urlunparse(parsed._replace(query=urlencode(existing)))
        
        # Create options with Flux URL for base class validation
        flux_options = {**merged, "base_url": flux_url}
        
        # Use smart generic validation from base class
        return await super().validate_connectivity(flux_options)
    
    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        """Open a Flux streaming session with continuous audio support."""
        merged = self._compose_options(options)
        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("Deepgram Flux STT requires an API key")
        
        # Build Flux-specific query parameters
        query_params = {
            "model": "flux-general-en",  # Required for Flux
            "language": merged.get("language", "en-US"),
            "encoding": merged.get("encoding", "linear16"),
            "sample_rate": merged.get("sample_rate", "16000"),
            "channels": "1",  # Mono audio (required for Flux)
            # Turn detection parameters
            "eot_threshold": merged.get("eot_threshold", "0.7"),
            "eot_timeout_ms": merged.get("eot_timeout_ms", "5000"),
        }
        
        # Optional: Enable EagerEndOfTurn for faster LLM triggering
        if "eager_eot_threshold" in merged:
            query_params["eager_eot_threshold"] = merged["eager_eot_threshold"]
        
        # Remove None values
        query_params = {k: str(v) for k, v in query_params.items() if v is not None}
        
        # Always use WSS for Flux, ignore HTTP(S) from provider config
        base_url = merged.get("base_url", "")
        if not base_url or not base_url.startswith("wss://"):
            ws_url = "wss://api.deepgram.com/v2/listen"
        else:
            ws_url = base_url
            
        parsed = urlparse(ws_url)
        existing = dict(parse_qsl(parsed.query))
        existing.update(query_params)
        ws_url = urlunparse(parsed._replace(query=urlencode(existing)))
        
        logger.info(
            "Deepgram Flux STT opening session",
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
                "Failed to connect to Deepgram Flux",
                call_id=call_id,
                error=str(exc),
                exc_info=True,
            )
            raise RuntimeError(f"Deepgram Flux connection failed: {exc}") from exc
        
        session_id = str(uuid.uuid4())
        session = _FluxSessionState(
            websocket=websocket,
            options=merged,
            session_id=session_id,
        )
        self._sessions[call_id] = session
        
        # Start async receiver task
        session.receiver_task = asyncio.create_task(
            self._receive_loop(call_id, session)
        )
        
        logger.info(
            "Deepgram Flux STT session opened",
            call_id=call_id,
            session_id=session_id,
        )
    
    async def close_call(self, call_id: str) -> None:
        """Close the Flux session and stop receiver task."""
        session = self._sessions.pop(call_id, None)
        if not session:
            return
        
        session.active = False
        
        # Cancel receiver task
        if session.receiver_task and not session.receiver_task.done():
            session.receiver_task.cancel()
            try:
                await session.receiver_task
            except asyncio.CancelledError:
                pass
        
        # Signal end to transcript queue
        try:
            session.transcript_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        
        # Close websocket
        try:
            await session.websocket.close()
        except Exception:
            pass
        
        logger.info(
            "Deepgram Flux STT session closed",
            call_id=call_id,
            session_id=session.session_id,
        )
    
    async def start_stream(
        self,
        call_id: str,
        options: Dict[str, Any],
        *,
        sample_rate_hz: int,
        fmt: str,
    ) -> None:
        """
        Start streaming session (already opened in open_call).
        
        This method is called by the pipeline runner when using streaming mode.
        For Flux, the stream is already active after open_call, so this is a no-op.
        """
        normalized_fmt = str(fmt or "").strip().lower()
        if normalized_fmt not in STREAMING_STT_FORMAT_ALIASES:
            raise ValueError(f"Unsupported Deepgram Flux streaming STT format: {fmt!r}")
        session = self._sessions.get(call_id)
        if not session:
            raise RuntimeError(f"Deepgram Flux STT session not found for call {call_id}")
        declared_rate = int(session.options.get("sample_rate", 0) or 0)
        declared_encoding = str(session.options.get("encoding", "")).strip().lower()
        if declared_rate != int(sample_rate_hz) or declared_encoding != "linear16":
            raise RuntimeError(
                "Deepgram Flux session audio format does not match the engine pipeline bus "
                f"(declared={declared_encoding}@{declared_rate}, "
                f"engine=linear16@{sample_rate_hz})"
            )
        logger.debug(
            "Deepgram Flux stream already active",
            call_id=call_id,
            session_id=session.session_id,
            encoding=declared_encoding,
            sample_rate_hz=declared_rate,
        )
    
    async def send_audio(
        self,
        call_id: str,
        audio_pcm16: bytes,
        fmt: str = "pcm16_16k",
    ) -> None:
        """
        Send audio to Flux continuously (non-blocking).
        
        This is the correct pattern for Flux - send audio as it arrives,
        do NOT wait for responses.
        """
        session = self._sessions.get(call_id)
        if not session or not session.active:
            return
        
        try:
            await session.websocket.send(audio_pcm16)
        except (websockets.ConnectionClosed, websockets.WebSocketException) as exc:
            logger.warning(
                "Deepgram Flux websocket closed while sending audio",
                call_id=call_id,
                error=str(exc),
            )
            session.active = False
        except Exception as exc:
            logger.error(
                "Error sending audio to Deepgram Flux",
                call_id=call_id,
                error=str(exc),
                exc_info=True,
            )
    
    async def stop_stream(self, call_id: str) -> None:
        """
        Stop streaming session (cleanup handled in close_call).
        
        This method is called by the pipeline runner when using streaming mode.
        For Flux, we handle cleanup in close_call, so this is a no-op.
        """
        session = self._sessions.get(call_id)
        if session:
            logger.debug(
                "Deepgram Flux stop_stream (cleanup in close_call)",
                call_id=call_id,
                session_id=session.session_id,
            )
    
    async def transcribe(
        self,
        call_id: str,
        audio_pcm16: bytes,
        sample_rate_hz: int,
        options: Dict[str, Any],
    ) -> str:
        """
        Flux requires streaming mode - transcribe() not supported.
        
        Flux is designed for continuous bidirectional streaming and cannot
        operate in request/response mode. Configure pipeline with:
        
        options:
          stt:
            streaming: true
        
        This method is only here to satisfy the STTComponent interface.
        """
        raise NotImplementedError(
            "Deepgram Flux requires streaming mode. "
            "Set options.stt.streaming=true in pipeline configuration. "
            "Flux cannot operate in request/response mode."
        )
    
    async def iter_results(self, call_id: str) -> AsyncGenerator[str, None]:
        """
        Yield transcripts as they arrive from Flux.
        
        This is an async generator that yields final transcripts.
        EndOfTurn events signal turn completion.
        """
        session = self._sessions.get(call_id)
        if not session:
            return
        
        while True:
            try:
                transcript = await session.transcript_queue.get()
                if transcript is None:
                    break
                yield transcript
            except asyncio.CancelledError:
                break
    
    async def _receive_loop(self, call_id: str, session: _FluxSessionState) -> None:
        """
        Async receiver loop for Flux results.
        
        Handles:
        - Results: Interim and final transcripts
        - EndOfTurn: Speaker finished, trigger LLM
        - EagerEndOfTurn: Early signal for faster response
        - TurnResumed: Cancel speculative LLM processing
        """
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
                    # Extract transcript from Results message
                    transcript = self._extract_transcript(data)
                    if transcript:
                        is_final = data.get("is_final", False)
                        
                        logger.debug(
                            "Deepgram Flux transcript received",
                            call_id=call_id,
                            transcript_preview=transcript[:50],
                            is_final=is_final,
                        )
                        
                        # Only queue final transcripts
                        if is_final:
                            try:
                                session.transcript_queue.put_nowait(transcript)
                            except asyncio.QueueFull:
                                # Drop oldest transcript if queue full
                                try:
                                    session.transcript_queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    pass
                                await session.transcript_queue.put(transcript)
                
                elif msg_type == "EndOfTurn":
                    # Speaker finished - signal turn complete
                    logger.info(
                        "Deepgram Flux end of turn detected",
                        call_id=call_id,
                    )
                    session.turn_complete_event.set()
                
                elif msg_type == "EagerEndOfTurn":
                    # Early turn detection - can start LLM processing
                    logger.debug(
                        "Deepgram Flux eager end of turn",
                        call_id=call_id,
                    )
                    # Pipeline can use this for early LLM triggering
                
                elif msg_type == "TurnResumed":
                    # User continued speaking - cancel speculative LLM
                    logger.debug(
                        "Deepgram Flux turn resumed",
                        call_id=call_id,
                    )
                    session.turn_complete_event.clear()
                
                elif msg_type in ("Metadata", "SpeechStarted"):
                    # Informational events
                    pass
                
                else:
                    logger.debug(
                        "Deepgram Flux unknown message type",
                        call_id=call_id,
                        type=msg_type,
                    )
        
        except websockets.ConnectionClosed:
            logger.info(
                "Deepgram Flux websocket closed",
                call_id=call_id,
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(
                "Deepgram Flux receive loop error",
                call_id=call_id,
                error=str(exc),
                exc_info=True,
            )
        finally:
            session.active = False
            # Signal end to transcript queue
            try:
                session.transcript_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
    
    def _extract_transcript(self, message: Dict[str, Any]) -> Optional[str]:
        """Extract transcript text from Flux Results message."""
        try:
            channel = message.get("channel", {})
            alternatives = channel.get("alternatives", [])
            if alternatives:
                return alternatives[0].get("transcript", "").strip()
        except (KeyError, IndexError, AttributeError):
            pass
        return None
    
    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge runtime options with pipeline and provider defaults."""
        runtime_options = runtime_options or {}
        merged = {
            "base_url": runtime_options.get(
                "base_url",
                self._pipeline_defaults.get("base_url", self._provider_defaults.base_url),
            ),
            "language": runtime_options.get(
                "language",
                self._pipeline_defaults.get("language", self._provider_defaults.stt_language),
            ),
            "encoding": runtime_options.get(
                "encoding",
                self._pipeline_defaults.get("encoding", "linear16"),
            ),
            "sample_rate": runtime_options.get(
                "sample_rate",
                self._pipeline_defaults.get("sample_rate", "16000"),
            ),
            "api_key": runtime_options.get(
                "api_key",
                self._pipeline_defaults.get("api_key", self._provider_defaults.api_key),
            ),
            # Flux-specific turn detection parameters
            "eot_threshold": runtime_options.get(
                "eot_threshold",
                self._pipeline_defaults.get("eot_threshold", "0.7"),
            ),
            "eot_timeout_ms": runtime_options.get(
                "eot_timeout_ms",
                self._pipeline_defaults.get(
                    "eot_timeout_ms",
                    getattr(self._provider_defaults, "eot_timeout_ms", "5000"),
                ),
            ),
        }
        
        # Optional eager end-of-turn
        if "eager_eot_threshold" in runtime_options:
            merged["eager_eot_threshold"] = runtime_options["eager_eot_threshold"]
        elif "eager_eot_threshold" in self._pipeline_defaults:
            merged["eager_eot_threshold"] = self._pipeline_defaults["eager_eot_threshold"]
        
        return merged
