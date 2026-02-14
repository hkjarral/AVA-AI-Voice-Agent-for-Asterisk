"""
# Milestone7: Local pipeline adapters for the local AI server.

These adapters implement the STT/LLM/TTS interfaces defined in `base.py` by
talking directly to the local AI server WebSocket (default ws://127.0.0.1:8765).
Each adapter maintains its own connection lifecycle so pipelines can mix and
match local components without sharing provider state.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import audioop
from ..audio.resampler import resample_audio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Optional, Tuple

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from ..config import AppConfig, LocalProviderConfig

# Reconnection constants
_MAX_RECONNECT_ATTEMPTS = 3
_RECONNECT_DELAY_BASE_SEC = 0.5
from ..logging_config import get_logger
from .base import LLMComponent, LLMResponse, STTComponent, TTSComponent

logger = get_logger(__name__)

_DEFAULT_WS_URL = "ws://127.0.0.1:8765"


def _merge_dicts(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(base or {})
    if override:
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = _merge_dicts(merged[key], value)
            elif value is not None:
                merged[key] = value
    return merged


@dataclass
class _LocalSessionState:
    websocket: ClientConnection
    options: Dict[str, Any]
    mode: str
    call_id: str
    handshake_complete: bool = False
    result_queue: Optional[asyncio.Queue] = None
    receiver_task: Optional[asyncio.Task] = None
    send_lock: Optional[asyncio.Lock] = None


class _LocalAdapterBase:
    """Shared helpers for local pipeline adapters."""

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: LocalProviderConfig,
        pipeline_defaults: Optional[Dict[str, Any]],
        *,
        default_mode: str,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_config = provider_config
        self._provider_defaults = provider_config.model_dump()
        self._pipeline_defaults = pipeline_defaults or {}
        self._default_mode = default_mode
        self._sessions: Dict[str, _LocalSessionState] = {}
        self._closed = False
        self._reconnect_locks: Dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        # Log backend info from provider config
        stt_backend = getattr(self._provider_config, 'stt_backend', 'vosk')
        tts_backend = getattr(self._provider_config, 'tts_backend', 'piper')
        
        logger.debug(
            "Local adapter initialized",
            component=self.component_key,
            default_mode=self._default_mode,
            stt_backend=stt_backend,
            tts_backend=tts_backend,
        )

    async def stop(self) -> None:
        self._closed = True
        for call_id in list(self._sessions.keys()):
            await self.close_call(call_id)
        logger.debug("Local adapter stopped", component=self.component_key)

    # validate_connectivity removed - uses smart generic base class implementation

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError(f"Adapter {self.component_key} has been stopped")

        merged = self._compose_options(options)
        existing = self._sessions.get(call_id)
        if existing:
            if existing.websocket.state.name != "OPEN":
                self._sessions.pop(call_id, None)
            else:
                # Reuse any open websocket session regardless of handshake status
                logger.debug(
                    "Reusing existing local adapter session",
                    component=self.component_key,
                    call_id=call_id,
                )
                return

        ws_url = merged.get("ws_url") or _DEFAULT_WS_URL
        connect_timeout = float(merged.get("connect_timeout_sec", 5.0))
        mode = merged.get("mode", self._default_mode)

        logger.info(
            "Opening local adapter session",
            component=self.component_key,
            call_id=call_id,
            url=ws_url,
            mode=mode,
        )

        try:
            websocket = await asyncio.wait_for(
                websockets.connect(
                    ws_url,
                    ping_interval=None,
                    ping_timeout=None,
                    max_size=None,
                ),
                timeout=connect_timeout,
            )
        except Exception as exc:
            logger.error(
                "Failed to connect to local AI server",
                component=self.component_key,
                call_id=call_id,
                error=str(exc),
            )
            raise

        # Optional auth handshake for local-ai-server.
        auth_token = (merged.get("auth_token") or "").strip()
        if auth_token:
            try:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "auth",
                            "auth_token": auth_token,
                            "call_id": call_id,
                        }
                    )
                )
                raw = await asyncio.wait_for(websocket.recv(), timeout=connect_timeout)
                if isinstance(raw, (bytes, bytearray)):
                    raise RuntimeError("Unexpected binary auth response")
                resp = json.loads(raw)
                if resp.get("type") != "auth_response" or resp.get("status") != "ok":
                    raise RuntimeError(f"Auth rejected: {resp}")
                logger.info(
                    "Local adapter authenticated",
                    component=self.component_key,
                    call_id=call_id,
                )
            except Exception as exc:
                try:
                    await websocket.close()
                except Exception:
                    pass
                logger.error(
                    "Local adapter auth failed",
                    component=self.component_key,
                    call_id=call_id,
                    error=str(exc),
                )
                raise

        session = _LocalSessionState(
            websocket=websocket,
            options=merged,
            mode=mode,
            call_id=call_id,
            handshake_complete=False,
        )
        self._sessions[call_id] = session

        await self._send_json(
            session,
            {
                "type": "set_mode",
                "mode": mode,
                "call_id": call_id,
            },
        )
        try:
            logger.info(
                "Local adapter set_mode sent",
                component=self.component_key,
                call_id=call_id,
                mode=mode,
            )
        except Exception:
            pass
        try:
            # Best-effort handshake; proceed without failing if ack not received
            await self._await_mode_ready(session, merged)
        except Exception:
            logger.warning(
                "Local adapter handshake not confirmed; proceeding without mode_ready",
                component=self.component_key,
                call_id=call_id,
                exc_info=True,
            )
        try:
            # Diagnostic: confirm session index and mode after set_mode send
            logger.info(
                "Local adapter session opened",
                component=self.component_key,
                call_id=call_id,
                mode=mode,
                session_keys=list(self._sessions.keys()),
                url=ws_url,
            )
        except Exception:
            logger.debug("Local adapter session open logging failed", exc_info=True)

    async def close_call(self, call_id: str) -> None:
        session = self._sessions.pop(call_id, None)
        if not session:
            return

        try:
            await session.websocket.close()
        except Exception as exc:
            logger.warning(
                "Error closing local adapter session",
                component=self.component_key,
                call_id=call_id,
                error=str(exc),
            )
        finally:
            logger.info(
                "Local adapter session closed",
                component=self.component_key,
                call_id=call_id,
            )

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged = _merge_dicts(dict(self._provider_defaults or {}), self._pipeline_defaults)
        merged = _merge_dicts(merged, runtime_options)
        merged.setdefault("ws_url", merged.get("ws_url") or _DEFAULT_WS_URL)
        merged.setdefault("connect_timeout_sec", merged.get("connect_timeout_sec", 5.0))
        merged.setdefault("response_timeout_sec", merged.get("response_timeout_sec", 5.0))
        merged.setdefault("mode", merged.get("mode", self._default_mode))
        merged.setdefault("locale", merged.get("locale") or merged.get("language") or "en-US")
        merged.setdefault("chunk_ms", merged.get("chunk_ms", 200))
        return merged

    async def _send_json(self, session: _LocalSessionState, payload: Dict[str, Any]) -> None:
        try:
            await session.websocket.send(json.dumps(payload))
        except Exception as exc:
            logger.error(
                "Failed to send JSON payload to local AI server",
                component=self.component_key,
                call_id=session.call_id,
                payload_type=payload.get("type"),
                error=str(exc),
            )
            raise

    async def _send_json_with_retry(
        self,
        call_id: str,
        payload: Dict[str, Any],
        options: Dict[str, Any],
        *,
        max_attempts: int = _MAX_RECONNECT_ATTEMPTS,
    ) -> None:
        """Send JSON payload with automatic reconnection on failure."""
        last_error: Optional[Exception] = None
        
        for attempt in range(1, max_attempts + 1):
            session = self._sessions.get(call_id)
            
            # Check if session exists and is open
            if not session or session.websocket.state.name != "OPEN":
                logger.debug(
                    "Session not available, attempting reconnection",
                    component=self.component_key,
                    call_id=call_id,
                    attempt=attempt,
                    max_attempts=max_attempts,
                )
                try:
                    session = await self._reconnect_session(call_id, options)
                except Exception as exc:
                    last_error = exc
                    logger.debug(
                        "Reconnection failed",
                        component=self.component_key,
                        call_id=call_id,
                        attempt=attempt,
                        error=str(exc),
                    )
                    if attempt < max_attempts:
                        delay = _RECONNECT_DELAY_BASE_SEC * (2 ** (attempt - 1))
                        await asyncio.sleep(delay)
                    continue
            
            try:
                await self._send_json(session, payload)
                return  # Success
            except (ConnectionClosed, ConnectionClosedError) as exc:
                last_error = exc
                logger.debug(
                    "Connection closed during send, will retry",
                    component=self.component_key,
                    call_id=call_id,
                    attempt=attempt,
                    error=str(exc),
                )
                # Remove stale session so next attempt reconnects
                self._sessions.pop(call_id, None)
                if attempt < max_attempts:
                    delay = _RECONNECT_DELAY_BASE_SEC * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Send failed with unexpected error",
                    component=self.component_key,
                    call_id=call_id,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < max_attempts:
                    delay = _RECONNECT_DELAY_BASE_SEC * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
        
        # All attempts exhausted
        logger.error(
            "All send attempts failed after reconnection retries",
            component=self.component_key,
            call_id=call_id,
            max_attempts=max_attempts,
            last_error=str(last_error) if last_error else "unknown",
        )
        if last_error:
            raise last_error
        raise RuntimeError(f"Failed to send payload after {max_attempts} attempts")

    async def _reconnect_session(
        self,
        call_id: str,
        options: Dict[str, Any],
    ) -> _LocalSessionState:
        """Reconnect a session with proper locking to avoid races."""
        # Get or create lock for this call_id
        if call_id not in self._reconnect_locks:
            self._reconnect_locks[call_id] = asyncio.Lock()
        
        async with self._reconnect_locks[call_id]:
            # Check again under lock - another task may have reconnected
            existing = self._sessions.get(call_id)
            if existing and existing.websocket.state.name == "OPEN":
                logger.debug(
                    "Session already reconnected by another task",
                    component=self.component_key,
                    call_id=call_id,
                )
                return existing
            
            # Clean up stale session
            if existing:
                self._sessions.pop(call_id, None)
                try:
                    await existing.websocket.close()
                except Exception:
                    pass
            
            logger.info(
                "Reconnecting local adapter session",
                component=self.component_key,
                call_id=call_id,
            )
            
            # Reopen the session
            await self.open_call(call_id, options)
            
            session = self._sessions.get(call_id)
            if not session or session.websocket.state.name != "OPEN":
                raise RuntimeError(f"Failed to reconnect session for call {call_id}")
            
            logger.info(
                "Local adapter session reconnected successfully",
                component=self.component_key,
                call_id=call_id,
            )
            return session

    async def _await_mode_ready(self, session: _LocalSessionState, options: Dict[str, Any]) -> None:
        handshake_timeout = float(
            options.get(
                "handshake_timeout_sec",
                max(float(options.get("connect_timeout_sec", 5.0)), 5.0),
            )
        )
        deadline = time.perf_counter() + handshake_timeout
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise asyncio.TimeoutError("Local adapter handshake timed out")
            kind, message = await self._recv_any(session, remaining)
            if kind != "json":
                logger.debug(
                    "Ignoring non-JSON message during handshake",
                    component=self.component_key,
                    call_id=session.call_id,
                    message_type=kind,
                )
                continue
            msg_type = message.get("type")
            if msg_type != "mode_ready":
                logger.debug(
                    "Unexpected JSON payload during handshake",
                    component=self.component_key,
                    call_id=session.call_id,
                    payload_type=msg_type,
                )
                continue
            ack_mode = message.get("mode")
            if ack_mode:
                session.mode = ack_mode
            ack_call_id = message.get("call_id")
            if ack_call_id:
                session.call_id = ack_call_id
            session.handshake_complete = True
            logger.info(
                "Local adapter handshake complete",
                component=self.component_key,
                call_id=session.call_id,
                mode=session.mode,
            )
            return

    async def _recv_any(
        self,
        session: _LocalSessionState,
        timeout: Optional[float],
    ) -> Tuple[str, Any]:
        try:
            if timeout is None:
                message = await session.websocket.recv()
            else:
                message = await asyncio.wait_for(session.websocket.recv(), timeout=timeout)
        except ConnectionClosed as exc:
            logger.warning(
                "Local AI server connection closed",
                component=self.component_key,
                call_id=session.call_id,
                code=getattr(exc, "code", None),
                reason=getattr(exc, "reason", None),
            )
            raise
        except asyncio.TimeoutError as exc:
            raise exc

        if isinstance(message, bytes):
            return "binary", message

        if isinstance(message, str):
            try:
                payload = json.loads(message)
                return "json", payload
            except json.JSONDecodeError:
                logger.debug(
                    "Discarding non-JSON text message from local server",
                    component=self.component_key,
                    call_id=session.call_id,
                    preview=message[:64],
                )
                return "text", message

        logger.debug(
            "Received unsupported message type from local server",
            component=self.component_key,
            call_id=session.call_id,
            message_type=type(message).__name__,
        )
        return "unknown", message

    async def _ensure_session(self, call_id: str, options: Dict[str, Any]) -> _LocalSessionState:
        session = self._sessions.get(call_id)
        if session and session.websocket.state.name == "OPEN":
            return session

        if session and session.websocket.state.name != "OPEN":
            self._sessions.pop(call_id, None)

        await self.open_call(call_id, options)
        session = self._sessions.get(call_id)
        if session and session.websocket.state.name == "OPEN":
            return session

        raise RuntimeError(f"Local adapter session not available for call {call_id}")


class LocalSTTAdapter(_LocalAdapterBase, STTComponent):
    """# Milestone7: STT adapter backed by the local AI server."""

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: LocalProviderConfig,
        options: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            component_key,
            app_config,
            provider_config,
            options,
            default_mode="stt",
        )
        self._resample_state: Optional[tuple] = None

    async def start_stream(
        self,
        call_id: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        runtime_options = options or {}
        session = await self._ensure_session(call_id, runtime_options)
        if session.send_lock is None:
            session.send_lock = asyncio.Lock()
        if session.result_queue is None:
            maxsize = int(runtime_options.get("stream_queue_maxsize", 16))
            session.result_queue = asyncio.Queue(max(maxsize, 1))
        if session.receiver_task and not session.receiver_task.done():
            return
        session.receiver_task = asyncio.create_task(
            self._stream_receive_loop(session, runtime_options)
        )
        logger.debug(
            "Local STT streaming started",
            component=self.component_key,
            call_id=call_id,
        )

    async def send_audio(
        self,
        call_id: str,
        audio: bytes,
        *,
        fmt: str = "pcm16_16k",
    ) -> None:
        if not audio:
            logger.debug(
                "send_audio called with empty audio",
                component=self.component_key,
                call_id=call_id,
            )
            return
        
        logger.debug(
            "🎤 STT send_audio called",
            component=self.component_key,
            call_id=call_id,
            input_bytes=len(audio),
            format=fmt,
        )
        
        session = self._sessions.get(call_id)
        if not session or session.send_lock is None:
            # Try to recover by checking if we can get a valid session
            logger.debug(
                "STT session not found or no send_lock, attempting recovery",
                component=self.component_key,
                call_id=call_id,
            )
            return  # Skip this audio frame rather than crash the call
        
        pcm16 = self._to_pcm16_16k(audio, fmt)
        if not pcm16:
            logger.warning(
                "send_audio: conversion to PCM16 16kHz produced empty result",
                component=self.component_key,
                call_id=call_id,
                input_bytes=len(audio),
            )
            return
        
        logger.debug(
            "🎤 STT audio converted and sending to server",
            component=self.component_key,
            call_id=call_id,
            pcm16_bytes=len(pcm16),
            base64_size=len(base64.b64encode(pcm16).decode("ascii")),
        )
        
        payload = {
            "type": "audio",
            "mode": "stt",
            "call_id": call_id,
            "rate": 16000,
            "format": "pcm16le",
            "data": base64.b64encode(pcm16).decode("ascii"),
        }
        
        try:
            async with session.send_lock:
                # Check connection state before sending
                if session.websocket.state.name != "OPEN":
                    logger.debug(
                        "WebSocket not open, attempting reconnection for STT audio",
                        component=self.component_key,
                        call_id=call_id,
                        ws_state=session.websocket.state.name,
                    )
                    # Use retry logic
                    await self._send_json_with_retry(call_id, payload, session.options)
                else:
                    await self._send_json(session, payload)
        except (ConnectionClosed, ConnectionClosedError) as exc:
            logger.warning(
                "STT send_audio connection closed, will retry on next audio",
                component=self.component_key,
                call_id=call_id,
                error=str(exc),
            )
            # Remove stale session so next call can reconnect
            self._sessions.pop(call_id, None)
            return  # Don't crash - skip this frame
        except Exception as exc:
            logger.warning(
                "STT send_audio failed unexpectedly",
                component=self.component_key,
                call_id=call_id,
                error=str(exc),
            )
            return  # Don't crash the call
        
        logger.debug(
            "🎤 STT audio sent to local-ai-server",
            component=self.component_key,
            call_id=call_id,
            pcm16_bytes=len(pcm16),
        )

    async def iter_results(self, call_id: str) -> AsyncIterator[str]:
        session = self._sessions.get(call_id)
        if not session or session.result_queue is None:
            raise RuntimeError(
                f"Streaming session not started for call {call_id}; call start_stream first"
            )
        while True:
            result = await session.result_queue.get()
            if result is None:
                break
            yield result

    async def stop_stream(self, call_id: str) -> None:
        session = self._sessions.get(call_id)
        if not session:
            return
        if session.receiver_task:
            session.receiver_task.cancel()
            try:
                await session.receiver_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug(
                    "Local STT receive loop error during shutdown",
                    component=self.component_key,
                    call_id=call_id,
                    exc_info=True,
                )
        if session.result_queue:
            try:
                session.result_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        session.receiver_task = None
        session.result_queue = None
        session.send_lock = None

    async def close_call(self, call_id: str) -> None:
        await self.stop_stream(call_id)
        await super().close_call(call_id)

    async def _stream_receive_loop(
        self,
        session: _LocalSessionState,
        options: Dict[str, Any],
    ) -> None:
        queue = session.result_queue
        if queue is None:
            return
        timeout = options.get("streaming_result_timeout_sec")
        timeout_val = float(timeout) if timeout is not None else None
        try:
            while True:
                try:
                    kind, message = await self._recv_any(session, timeout_val)
                except asyncio.TimeoutError:
                    continue
                if kind != "json":
                    continue
                if message.get("type") != "stt_result":
                    continue
                if message.get("is_partial", False):
                    logger.debug(
                        "Local STT partial received",
                        component=self.component_key,
                        call_id=session.call_id,
                        transcript_preview=(message.get("text") or "")[:80],
                    )
                    continue
                text = (message.get("text") or "")
                try:
                    queue.put_nowait(text)
                except asyncio.QueueFull:
                    await queue.put(text)
        except asyncio.CancelledError:
            pass
        except ConnectionClosed:
            pass
        except Exception:
            logger.debug(
                "Local STT streaming receive loop error",
                component=self.component_key,
                call_id=session.call_id,
                exc_info=True,
            )
        finally:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def _to_pcm16_16k(self, audio: bytes, fmt: str) -> bytes:
        if not audio:
            return audio
        fmt = fmt.lower()
        if fmt in {"pcm16", "pcm16_16k", "pcm16-16k"}:
            return audio
        if fmt in {"pcm16_8k", "pcm16-8k"}:
            converted, self._resample_state = resample_audio(audio, 8000, 16000, state=self._resample_state)
            return converted
        if fmt in {"mulaw8k", "ulaw8k"}:
            linear = audioop.ulaw2lin(audio, 2)
            converted, self._resample_state = resample_audio(linear, 8000, 16000, state=self._resample_state)
            return converted
        raise ValueError(f"Unsupported audio format '{fmt}' for local STT streaming")

    async def transcribe(
        self,
        call_id: str,
        audio_pcm16: bytes,
        sample_rate_hz: int,
        options: Dict[str, Any],
    ) -> str:
        runtime_options = options or {}
        session = await self._ensure_session(call_id, runtime_options)

        merged = self._compose_options(runtime_options)
        logger.debug(
            "Sending STT audio chunk",
            component=self.component_key,
            call_id=call_id,
            bytes=len(audio_pcm16),
            rate=sample_rate_hz,
        )
        payload = {
            "type": "audio",
            "mode": "stt",
            "call_id": call_id,
            "rate": sample_rate_hz,
            "data": base64.b64encode(audio_pcm16).decode("ascii"),
        }

        await self._send_json(session, payload)
        # STT should use its own response timeout
        timeout = float(merged.get("response_timeout_sec", 5.0))
        started_at = time.perf_counter()
        deadline = started_at + timeout
        partial_text = ""

        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise asyncio.TimeoutError("Local STT adapter timed out waiting for transcript")

            kind, message = await self._recv_any(session, remaining)
            if kind != "json":
                continue
            if message.get("type") != "stt_result":
                continue

            is_final = bool(message.get("is_final", not message.get("is_partial", False)))
            is_partial = bool(message.get("is_partial", not is_final))
            text = (message.get("text") or "").strip()

            if is_partial:
                if text:
                    partial_text = text
                deadline = time.perf_counter() + timeout
                logger.debug(
                    "Local STT partial received",
                    component=self.component_key,
                    call_id=call_id,
                    transcript_preview=text[:80],
                )
                continue

            transcript = text or partial_text
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            logger.info(
                "Local STT transcript received",
                component=self.component_key,
                call_id=call_id,
                latency_ms=round(latency_ms, 2),
                transcript_preview=transcript[:80],
            )
            return transcript


class LocalLLMAdapter(_LocalAdapterBase, LLMComponent):
    """# Milestone7: LLM adapter backed by the local AI server."""

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: LocalProviderConfig,
        options: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            component_key,
            app_config,
            provider_config,
            options,
            default_mode="llm",
        )

    async def generate(
        self,
        call_id: str,
        transcript: str,
        context: Dict[str, Any],
        options: Dict[str, Any],
    ) -> LLMResponse:
        runtime_options = options or {}
        
        try:
            session = await self._ensure_session(call_id, runtime_options)
        except Exception as exc:
            logger.error(
                "Failed to establish LLM session",
                component=self.component_key,
                call_id=call_id,
                error=str(exc),
            )
            return LLMResponse(text="")  # Return empty response rather than crash

        merged = self._compose_options(runtime_options)
        logger.debug(
            "Sending LLM request",
            component=self.component_key,
            call_id=call_id,
            transcript_preview=(transcript or "")[:80],
        )
        payload = {
            "type": "llm_request",
            "call_id": call_id,
            "mode": "llm",
            "text": transcript,
            "context": context.get("messages") or context,
        }

        # Use retry logic for LLM send
        try:
            await self._send_json_with_retry(call_id, payload, runtime_options)
        except Exception as exc:
            logger.error(
                "Failed to send LLM request after retries",
                component=self.component_key,
                call_id=call_id,
                error=str(exc),
            )
            return LLMResponse(text="")  # Return empty response rather than crash
        
        # Re-fetch session after potential reconnection
        session = self._sessions.get(call_id)
        if not session:
            logger.error(
                "LLM session lost after send",
                component=self.component_key,
                call_id=call_id,
            )
            return LLMResponse(text="")

        # Prefer a dedicated LLM timeout when provided (pipeline or provider level)
        timeout = float(merged.get("llm_response_timeout_sec", merged.get("response_timeout_sec", 5.0)))
        started_at = time.perf_counter()

        try:
            while True:
                try:
                    kind, message = await self._recv_any(session, timeout)
                except (ConnectionClosed, ConnectionClosedError) as exc:
                    logger.warning(
                        "LLM connection closed while waiting for response",
                        component=self.component_key,
                        call_id=call_id,
                        error=str(exc),
                    )
                    self._sessions.pop(call_id, None)
                    return LLMResponse(text="")
                except asyncio.TimeoutError:
                    logger.warning(
                        "LLM response timed out",
                        component=self.component_key,
                        call_id=call_id,
                        timeout_sec=timeout,
                    )
                    return LLMResponse(text="")
                    
                if kind != "json":
                    continue
                if message.get("type") != "llm_response":
                    continue

                response = message.get("text", "").strip()
                latency_ms = (time.perf_counter() - started_at) * 1000.0
                logger.info(
                    "Local LLM response received",
                    component=self.component_key,
                    call_id=call_id,
                    latency_ms=round(latency_ms, 2),
                    response_preview=response[:80],
                )
                return LLMResponse(text=response)
        except Exception as exc:
            logger.error(
                "LLM receive loop failed unexpectedly",
                component=self.component_key,
                call_id=call_id,
                error=str(exc),
                exc_info=True,
            )
            return LLMResponse(text="")


class LocalTTSAdapter(_LocalAdapterBase, TTSComponent):
    """# Milestone7: TTS adapter backed by the local AI server."""

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: LocalProviderConfig,
        options: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            component_key,
            app_config,
            provider_config,
            options,
            default_mode="tts",
        )

    async def synthesize(
        self,
        call_id: str,
        text: str,
        options: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        if not text:
            return  # Exit early - yields nothing (async generator)
            yield  # Unreachable but makes this an async generator
        runtime_options = options or {}
        
        # Use retry-aware session acquisition
        try:
            session = await self._ensure_session(call_id, runtime_options)
        except Exception as exc:
            logger.error(
                "Failed to establish TTS session",
                component=self.component_key,
                call_id=call_id,
                error=str(exc),
            )
            return
            yield  # Unreachable but makes this an async generator

        merged = self._compose_options(runtime_options)
        logger.debug(
            "Sending TTS request",
            component=self.component_key,
            call_id=call_id,
            text_preview=(text or "")[:80],
        )
        payload = {
            "type": "tts_request",
            "call_id": call_id,
            "mode": "tts",
            "text": text,
        }

        # Use retry logic for TTS send
        try:
            await self._send_json_with_retry(call_id, payload, runtime_options)
        except Exception as exc:
            logger.error(
                "Failed to send TTS request after retries",
                component=self.component_key,
                call_id=call_id,
                error=str(exc),
            )
            return
            yield  # Unreachable but makes this an async generator
        
        # Re-fetch session after potential reconnection
        session = self._sessions.get(call_id)
        if not session:
            logger.error(
                "TTS session lost after send",
                component=self.component_key,
                call_id=call_id,
            )
            return
            yield  # Unreachable

        timeout = float(merged.get("response_timeout_sec", 8.0))
        started_at = time.perf_counter()
        yielded_audio = False

        try:
            while True:
                try:
                    kind, message = await self._recv_any(session, timeout)
                except (ConnectionClosed, ConnectionClosedError) as exc:
                    logger.warning(
                        "TTS connection closed while waiting for response",
                        component=self.component_key,
                        call_id=call_id,
                        error=str(exc),
                    )
                    # Remove stale session
                    self._sessions.pop(call_id, None)
                    break
                except asyncio.TimeoutError:
                    logger.warning(
                        "TTS response timed out",
                        component=self.component_key,
                        call_id=call_id,
                        timeout_sec=timeout,
                    )
                    break
                    
                if kind == "json":
                    msg_type = message.get("type")
                    if msg_type == "tts_response" and message.get("audio_data"):
                        decoded = base64.b64decode(message["audio_data"])
                        latency_ms = (time.perf_counter() - started_at) * 1000.0
                        logger.info(
                            "Local TTS response (base64) received",
                            component=self.component_key,
                            call_id=call_id,
                            latency_ms=round(latency_ms, 2),
                            bytes=len(decoded),
                        )
                        yielded_audio = True
                        yield decoded
                        break
                    if msg_type == "tts_audio":
                        logger.debug(
                            "Local TTS metadata received",
                            component=self.component_key,
                            call_id=call_id,
                            meta=message,
                        )
                        continue
                    continue

                if kind == "binary":
                    latency_ms = (time.perf_counter() - started_at) * 1000.0
                    logger.info(
                        "Local TTS audio chunk received",
                        component=self.component_key,
                        call_id=call_id,
                        latency_ms=round(latency_ms, 2),
                        chunk_bytes=len(message),
                    )
                    yielded_audio = True
                    yield message
                    # Assume the local server sends a single binary payload per request.
                    break
        except Exception as exc:
            logger.error(
                "TTS receive loop failed unexpectedly",
                component=self.component_key,
                call_id=call_id,
                error=str(exc),
                exc_info=True,
            )

        if not yielded_audio:
            logger.warning(
                "Local TTS returned no audio data",
                component=self.component_key,
                call_id=call_id,
            )


__all__ = [
    "LocalSTTAdapter",
    "LocalLLMAdapter",
    "LocalTTSAdapter",
]
