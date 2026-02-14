"""
OpenAI Realtime provider implementation.

This module integrates OpenAI's server-side Realtime WebSocket transport into the
Asterisk AI Voice Agent without requiring WebRTC. Audio from AudioSocket is
upsampled to PCM16 @ 24 kHz, streamed to OpenAI, and PCM16 24 kHz output is
resampled to the configured downstream AudioSocket format (µ-law or PCM16 8 kHz).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
import uuid
import audioop
from typing import Any, Dict, Optional, List

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from structlog import get_logger
from prometheus_client import Gauge, Info

from .base import AIProviderInterface, ProviderCapabilities
from ..audio import (
    convert_pcm16le_to_target_format,
    mulaw_to_pcm16le,
    resample_audio,
)
from ..config import OpenAIRealtimeProviderConfig

# Tool calling support
from src.tools.registry import tool_registry
from src.tools.adapters.openai import OpenAIToolAdapter

logger = get_logger(__name__)


def _log_provider_task_exception(task: asyncio.Task) -> None:
    """Done-callback: log exceptions from fire-and-forget provider tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("Provider background task failed", task_name=task.get_name(), error=str(exc), exc_info=exc)


_COMMIT_INTERVAL_SEC = 0.2
_KEEPALIVE_INTERVAL_SEC = 15.0

_OPENAI_ASSUMED_OUTPUT_RATE = Gauge(
    "ai_agent_openai_assumed_output_sample_rate_hz",
    "Configured OpenAI Realtime output sample rate per call",
)
_OPENAI_PROVIDER_OUTPUT_RATE = Gauge(
    "ai_agent_openai_provider_output_sample_rate_hz",
    "Provider-advertised OpenAI Realtime output sample rate per call",
)
_OPENAI_MEASURED_OUTPUT_RATE = Gauge(
    "ai_agent_openai_measured_output_sample_rate_hz",
    "Measured OpenAI Realtime output sample rate per call",
)
_OPENAI_SESSION_AUDIO_INFO = Info(
    "ai_agent_openai_session_audio",
    "OpenAI Realtime session audio format assumptions and provider acknowledgements",
)


class OpenAIRealtimeProvider(AIProviderInterface):
    """
    OpenAI Realtime provider using server-side WebSocket transport.

    Lifecycle:
    1. start_session(call_id) -> establishes WebSocket session.
    2. send_audio(bytes) -> converts inbound AudioSocket frames to PCM16 24 kHz,
       base64-encodes, and streams via input_audio_buffer.
    3. Provider output deltas are decoded, resampled to AudioSocket format, and
       emitted as AgentAudio / AgentAudioDone events.
    4. stop_session() -> closes the WebSocket and cancels background tasks.
    """

    def __init__(
        self,
        config: OpenAIRealtimeProviderConfig,
        on_event,
        gating_manager=None,
    ):
        super().__init__(on_event)
        self.config = config
        self.websocket: Optional[ClientConnection] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()

        self._call_id: Optional[str] = None
        self._pending_response: bool = False
        self._current_response_id: Optional[str] = None  # Track active response for cancellation
        self._greeting_response_id: Optional[str] = None  # Track greeting to protect from barge-in
        self._greeting_completed: bool = False  # Track if greeting has finished
        # Debounce engine-level barge-in signals (prevents flush storms).
        self._last_barge_in_emit_ts: float = 0.0
        self._farewell_response_id: Optional[str] = None  # Track farewell response for hangup
        self._hangup_after_response: bool = False  # Flag to trigger hangup after next response
        self._farewell_timeout_task: Optional[asyncio.Task] = None  # Timeout fallback for hangup
        self._greeting_vad_task: Optional[asyncio.Task] = None
        self._background_tasks: set[asyncio.Task] = set()
        self._in_audio_burst: bool = False
        # Track whether ANY audio was emitted during a given response (response_id -> bool).
        # _in_audio_burst is only "currently emitting", and is often false by response.done.
        self._audio_seen_response_ids: set[str] = set()
        # For farewells, wait for output_audio.done before emitting HangupReady to avoid cutting off speech.
        self._farewell_waiting_for_audio_done: bool = False
        self._response_audio_start_time: Optional[float] = None  # Track when audio started for interruption cooldown
        self._min_response_time_before_interrupt: float = 2.5  # Minimum seconds of audio before allowing interruption (increased for farewells)
        self._first_output_chunk_logged: bool = False
        self._closing: bool = False
        self._closed: bool = False

        self._input_resample_state: Optional[tuple] = None
        self._output_resample_state: Optional[tuple] = None
        self._transcript_buffer: str = ""
        self._input_info_logged: bool = False
        self._allowed_tools: Optional[List[str]] = None
        
        # Turn latency tracking (Milestone 21 - Call History)
        self._turn_start_time: Optional[float] = None
        self._turn_first_audio_received: bool = False
        self._session_store = None  # Set via engine for latency tracking
        # Aggregate provider-rate PCM16 bytes (24 kHz default) and commit in >=100ms chunks
        self._pending_audio_provider_rate: bytearray = bytearray()
        
        # Audio gating for echo prevention
        self._gating_manager = gating_manager
        if self._gating_manager:
            logger.info("🎛️ Audio gating enabled for OpenAI Realtime (echo prevention)")
        else:
            logger.debug("Audio gating not available for OpenAI Realtime")
        self._last_commit_ts: float = 0.0
        # Serialize append/commit to avoid empty commits from races
        self._audio_lock: asyncio.Lock = asyncio.Lock()
        self._provider_output_format: str = "pcm16"
        self._provider_reported_output_rate: Optional[int] = None
        self._output_meter_start_ts: float = 0.0
        self._output_meter_last_log_ts: float = 0.0
        self._output_meter_bytes: int = 0
        self._output_rate_warned: bool = False
        self._active_output_sample_rate_hz: Optional[float] = (
            float(self.config.output_sample_rate_hz) if getattr(self.config, "output_sample_rate_hz", None) else None
        )
        self._session_output_bytes_per_sample: int = 2
        self._session_output_encoding: str = "pcm16"
        # Output format acknowledgment flag: only enable μ-law pass-through after server ACK
        self._outfmt_acknowledged: bool = False
        # Heuristic inference state when provider does not ACK output format
        self._inferred_provider_encoding: Optional[str] = None
        self._inference_logged: bool = False
        # Egress pacing and buffering (telephony cadence)
        self._egress_pacer_enabled: bool = bool(getattr(config, "egress_pacer_enabled", True))
        try:
            self._egress_pacer_warmup_ms: int = int(getattr(config, "egress_pacer_warmup_ms", 320))
        except Exception:
            self._egress_pacer_warmup_ms = 320
        self._outbuf: bytearray = bytearray()
        self._pacer_task: Optional[asyncio.Task] = None
        self._pacer_running: bool = False
        self._pacer_start_ts: float = 0.0
        self._pacer_underruns: int = 0
        self._pacer_lock: asyncio.Lock = asyncio.Lock()
        self._fallback_pcm24k_done: bool = False
        self._reconnect_task: Optional[asyncio.Task] = None

        # Tool calling support
        self.tool_adapter = OpenAIToolAdapter(tool_registry)
        logger.info("🛠️  OpenAI Realtime provider initialized with tool support")

        try:
            if self.config.input_encoding:
                self.config.input_encoding = self.config.input_encoding.strip()
        except Exception:
            pass

    def describe_alignment(
        self,
        *,
        audiosocket_format: str,
        streaming_encoding: str,
        streaming_sample_rate: int,
    ) -> List[str]:
        issues: List[str] = []
        inbound_enc = (self.config.input_encoding or "slin16").lower()
        inbound_rate = int(self.config.input_sample_rate_hz or 0)
        target_enc = (self.config.target_encoding or "ulaw").lower()
        target_rate = int(self.config.target_sample_rate_hz or 0)

        def _class(enc: str) -> str:
            e = (enc or "").lower()
            if e in ("ulaw", "mulaw", "g711_ulaw", "mu-law"):
                return "ulaw"
            if e in ("slin", "slin16", "linear16", "pcm16", "pcm"):
                return "pcm16"
            return e

        # Check inbound encoding vs AudioSocket
        # NOTE: Intentional transcoding (slin ↔ ulaw) is supported - system handles conversion
        if inbound_enc in ("slin16", "linear16", "pcm16") and _class(audiosocket_format) == "ulaw":
            issues.append(
                "OpenAI inbound encoding is PCM16 but AudioSocket format is μ-law; set audiosocket.format=slin16 "
                "or change openai_realtime.input_encoding to ulaw."
            )
        elif inbound_enc in ("ulaw", "mulaw", "g711_ulaw", "mu-law") and _class(audiosocket_format) == "ulaw":
            # Perfect alignment: both ulaw
            pass
        elif inbound_enc in ("ulaw", "mulaw", "g711_ulaw", "mu-law") and _class(audiosocket_format) in ("pcm16",):
            # Intentional transcoding: AudioSocket PCM → Provider μ-law (system handles this)
            pass
        elif inbound_enc in ("ulaw", "mulaw", "g711_ulaw", "mu-law") and _class(audiosocket_format) != "ulaw":
            # Only warn if it's not a supported transcoding path
            if audiosocket_format not in ("slin", "slin16", "linear16", "pcm16"):
                issues.append(
                    f"OpenAI inbound encoding {inbound_enc} does not match audiosocket.format={audiosocket_format}."
                )
        if inbound_enc in ("ulaw", "mulaw", "g711_ulaw", "mu-law") and inbound_rate and inbound_rate != 8000:
            issues.append(
                f"OpenAI inbound μ-law sample rate is {inbound_rate} Hz; μ-law transport should be 8000 Hz."
            )

        # Check target encoding vs streaming manager output
        # NOTE: Intentional transcoding is supported - streaming manager transcodes provider output to target
        if _class(target_enc) == _class(streaming_encoding):
            # Perfect alignment
            pass
        elif _class(target_enc) == "ulaw" and _class(streaming_encoding) == "pcm16":
            # Intentional transcoding: Provider outputs PCM → Streaming manager transcodes to μ-law
            pass
        elif _class(target_enc) == "pcm16" and _class(streaming_encoding) == "ulaw":
            # Intentional transcoding: Provider outputs μ-law → Streaming manager transcodes to PCM
            pass
        else:
            # Warn only for unexpected mismatches
            issues.append(
                f"OpenAI target_encoding={target_enc} but streaming manager emits {streaming_encoding}."
            )
        if target_rate and target_rate != streaming_sample_rate:
            issues.append(
                f"OpenAI target_sample_rate_hz={target_rate} but streaming sample rate is {streaming_sample_rate}."
            )

        provider_rate = int(self.config.provider_input_sample_rate_hz or 0)
        provider_enc = (getattr(self.config, "provider_input_encoding", None) or "linear16").lower()
        if provider_rate:
            if provider_enc in ("ulaw", "mulaw", "g711_ulaw", "mu-law", "alaw", "g711_alaw") and provider_rate != 8000:
                issues.append(
                    f"OpenAI provider_input_sample_rate_hz={provider_rate}; G.711 (μ-law/a-law) should be 8000 Hz."
                )
            elif provider_enc in ("slin16", "linear16", "pcm16"):
                # Telephony deployments commonly run an internal 16 kHz PCM pipeline; 24 kHz PCM is also supported.
                # Only warn when configured to an unusual PCM rate.
                if provider_rate not in (16000, 24000):
                    issues.append(
                        f"OpenAI provider_input_sample_rate_hz={provider_rate}; for PCM16 use 16000 Hz (telephony) or 24000 Hz (wideband)."
                    )

        return issues

    @property
    def supported_codecs(self):
        fmt = (self.config.target_encoding or "ulaw").lower()
        return [fmt]

    # P1: Static capability hints for Transport Orchestrator
    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            # Audio format capabilities
            input_encodings=["ulaw", "linear16"],
            input_sample_rates_hz=[8000, 16000],
            # Output depends on session.update and downstream target; we advertise both
            output_encodings=["mulaw", "pcm16"],
            output_sample_rates_hz=[8000, 24000],
            preferred_chunk_ms=20,
            can_negotiate=False,  # Uses static session.update config, not runtime ACK
            # Provider type and audio processing capabilities
            is_full_agent=True,  # Full bidirectional agent (not pipeline component)
            has_native_vad=True,  # OpenAI Realtime has server-side VAD (turn detection)
            has_native_barge_in=True,  # Handles interruptions via cancel_response
            requires_continuous_audio=True,  # Needs continuous audio for server-side VAD
        )
    
    def parse_ack(self, event_data: Dict[str, Any]) -> Optional[ProviderCapabilities]:
        """
        Parse session.updated event from OpenAI Realtime API to extract negotiated formats.
        
        Returns capabilities based on provider ACK, or None if not a session.updated event.
        """
        event_type = event_data.get('type')
        if event_type != 'session.updated':
            return None
        
        try:
            session = event_data.get('session', {})
            
            # OpenAI session.updated includes input_audio_format and output_audio_format
            input_format = session.get('input_audio_format', 'pcm16')
            output_format = session.get('output_audio_format', 'pcm16')
            
            # OpenAI Realtime API only supports 24kHz
            sample_rate = 24000
            
            # Map OpenAI format names to our encoding names
            format_map = {
                'pcm16': 'linear16',
                'g711_ulaw': 'mulaw',
                'g711_alaw': 'alaw',
            }
            
            input_enc = format_map.get(input_format, input_format)
            output_enc = format_map.get(output_format, output_format)
            
            logger.info(
                "Parsed OpenAI session.updated ACK",
                call_id=self._call_id,
                input_format=input_format,
                output_format=output_format,
                sample_rate=sample_rate,
            )
            
            return ProviderCapabilities(
                input_encodings=[input_enc],
                input_sample_rates_hz=[sample_rate],
                output_encodings=[output_enc],
                output_sample_rates_hz=[sample_rate],
                preferred_chunk_ms=20,
                can_negotiate=False,  # ACK confirmed static session configuration
            )
        except Exception as exc:
            logger.warning(
                "Failed to parse OpenAI session.updated event",
                call_id=self._call_id,
                error=str(exc),
            )
            return None

    async def start_session(self, call_id: str, context: Optional[Dict[str, Any]] = None):
        if not self.config.api_key:
            raise ValueError("OpenAI Realtime provider requires OPENAI_API_KEY")

        await self.stop_session()
        self._call_id = call_id
        self._pending_response = False
        self._in_audio_burst = False
        self._first_output_chunk_logged = False
        self._input_resample_state = None
        self._output_resample_state = None
        self._transcript_buffer = ""
        self._closing = False
        self._closed = False
        
        # Initialize session ACK mechanism (similar to Deepgram pattern)
        self._session_ack_event = asyncio.Event()
        self._outfmt_acknowledged = False
        # Per-call tool allowlist (contexts are the source of truth):
        # - [] => no tools
        # - ["hangup_call", ...] => allowlisted tools
        # Missing/None is treated as [] for safety.
        if context and "tools" in context:
            self._allowed_tools = list(context.get("tools") or [])
        else:
            self._allowed_tools = []

        self._reset_output_meter()

        url = self._build_ws_url()
        use_beta = getattr(self.config, 'api_version', 'ga').lower() == 'beta'
        headers = [
            ("Authorization", f"Bearer {self.config.api_key}"),
        ]
        if use_beta:
            headers.append(("OpenAI-Beta", "realtime=v1"))
        if self.config.organization:
            headers.append(("OpenAI-Organization", self.config.organization))
        if self.config.project_id:
            headers.append(("OpenAI-Project", self.config.project_id))

        logger.info("Connecting to OpenAI Realtime", url=url, call_id=call_id, api_version="beta" if use_beta else "ga")
        try:
            self.websocket = await websockets.connect(url, additional_headers=headers)
        except Exception:
            logger.error("Failed to connect to OpenAI Realtime", call_id=call_id, exc_info=True)
            raise

        # CRITICAL FIX: Wait for session.created before configuring (per OpenAI docs)
        # "The server sends session.created as the first inbound message.
        # session.update sent before session.created is ignored."
        logger.debug("Waiting for session.created from OpenAI...", call_id=call_id)
        try:
            first_message = await asyncio.wait_for(
                self.websocket.recv(),
                timeout=5.0
            )
            first_event = json.loads(first_message)
            
            if first_event.get("type") == "session.created":
                session_data = first_event.get("session", {})
                logger.info(
                    "✅ Received session.created - session ready",
                    call_id=call_id,
                    session_id=session_data.get("id"),
                    model=session_data.get("model"),
                )
            else:
                logger.warning(
                    "Unexpected first event (expected session.created)",
                    call_id=call_id,
                    event_type=first_event.get("type")
                )
        except asyncio.TimeoutError:
            logger.error(
                "Timeout waiting for session.created",
                call_id=call_id
            )
            raise RuntimeError("OpenAI did not send session.created within 5s")
        except Exception as exc:
            logger.error(
                "Error receiving session.created",
                call_id=call_id,
                error=str(exc),
                exc_info=True
            )
            raise

        # NOW send session configuration (server is ready)
        await self._send_session_update()
        self._log_session_assumptions()
        
        # Start receive loop FIRST - this is required to receive ACK events!
        # Previous bug: We waited for ACK but the receive loop wasn't running yet,
        # so we always timed out. Now we start the loop first, then wait briefly.
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        
        # Brief wait for session.updated ACK - now receive loop can process it
        # Per OpenAI Dec 2024 docs, session config must be applied before response.create
        try:
            logger.debug("Waiting for OpenAI session.updated ACK before greeting...", call_id=call_id)
            await asyncio.wait_for(self._session_ack_event.wait(), timeout=2.0)  # Short timeout - ACK arrives fast now
            logger.info(
                "✅ OpenAI session.updated ACK received - session configured",
                call_id=call_id,
                acknowledged=self._outfmt_acknowledged,
                output_format=self._provider_output_format,
                sample_rate=self._active_output_sample_rate_hz,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "⚠️ OpenAI session.updated ACK timeout - proceeding anyway",
                call_id=call_id,
                note="Session may not be fully configured"
            )
        
        # NOW send greeting after session is configured
        try:
            if (self.config.greeting or "").strip():
                logger.info("Sending explicit greeting (after session ACK)", call_id=call_id)
                await self._send_explicit_greeting()
            else:
                await self._ensure_response_request()
        except Exception:
            logger.debug("Initial response.create request failed", call_id=call_id, exc_info=True)

        # Reset egress pacer state at session start
        try:
            async with self._pacer_lock:
                self._outbuf.clear()
            self._pacer_running = False
            self._pacer_start_ts = 0.0
            self._pacer_underruns = 0
            self._fallback_pcm24k_done = False
            if self._pacer_task and not self._pacer_task.done():
                self._pacer_task.cancel()
        except Exception:
            logger.debug("Failed to reset pacer state on session start", exc_info=True)

        logger.info("OpenAI Realtime session established", call_id=call_id)

    async def send_audio(self, audio_chunk: bytes, sample_rate: int = None, encoding: str = None):
        """Send audio to OpenAI Realtime API.
        
        Args:
            audio_chunk: Audio data bytes
            sample_rate: Source sample rate (if provided by engine)
            encoding: Source encoding format (if provided by engine)
        
        Engine provides explicit encoding/sample_rate when available.
        Falls back to config-based conversion for backward compatibility.
        """
        if not audio_chunk:
            return
        if not self.websocket or self.websocket.state.name != "OPEN":
            logger.debug("Dropping inbound audio: websocket not ready", call_id=self._call_id)
            return

        try:
            # Log input codec/config once for diagnosis
            if not self._input_info_logged:
                try:
                    logger.info(
                        "OpenAI input config",
                        call_id=self._call_id,
                        input_encoding=self.config.input_encoding,
                        input_sample_rate_hz=self.config.input_sample_rate_hz,
                        provider_input_sample_rate_hz=self.config.provider_input_sample_rate_hz,
                        engine_provided_encoding=encoding,
                        engine_provided_sample_rate=sample_rate,
                    )
                    self._input_info_logged = True
                except Exception:
                    pass

            # CRITICAL: Use engine-provided encoding/sample_rate if available
            # This avoids double conversion and respects the engine's format negotiation
            if encoding and sample_rate:
                # Engine already converted to correct format via _encode_for_provider
                # Trust the engine's conversion
                if encoding.lower().strip() in ("linear16", "pcm16", "slin16"):
                    pcm16 = audio_chunk
                    provider_rate = sample_rate
                else:
                    # Unexpected format - fall back to conversion
                    logger.warning(
                        "OpenAI Realtime: unexpected encoding from engine, converting",
                        call_id=self._call_id,
                        encoding=encoding,
                        sample_rate=sample_rate
                    )
                    pcm16 = self._convert_inbound_audio(audio_chunk)
                    provider_rate = int(getattr(self.config, "provider_input_sample_rate_hz", 0) or 24000)
            else:
                # Fallback: No parameters from engine - do own conversion (backward compat)
                pcm16 = self._convert_inbound_audio(audio_chunk)
                provider_rate = int(getattr(self.config, "provider_input_sample_rate_hz", 0) or 24000)
            
            if not pcm16:
                return
            
            # ECHO GATING for speakerphone support:
            # Gate input ONLY while we're outputting real audio from OpenAI.
            # The pacer keeps running and emitting silence, so we can't just check _outbuf.
            # Instead, check if pacer has real audio (underruns == 0 means real audio).
            # 
            # When _pacer_underruns > 0, we're just emitting silence - allow input.
            try:
                if self._in_audio_burst and self._pacer_underruns == 0:
                    # Agent is outputting REAL audio - gate input to prevent echo
                    return
            except Exception:
                pass  # If we can't check, allow input
            
            # Send audio to OpenAI for processing
            await self._send_audio_to_openai(pcm16)
            
        except ConnectionClosedError:
            logger.warning("OpenAI Realtime socket closed while sending audio", call_id=self._call_id)
            await self._reconnect_with_backoff()
        except Exception:
            logger.error("Failed to send audio to OpenAI Realtime", call_id=self._call_id, exc_info=True)

    async def cancel_response(self):
        """Cancel any in-progress response generation (for barge-in)."""
        if not self.websocket or self.websocket.state.name != "OPEN":
            return
        if not self._pending_response:
            logger.debug("No pending response to cancel", call_id=self._call_id)
            return
        
        try:
            cancel_payload = {
                "type": "response.cancel",
                "event_id": f"cancel-{uuid.uuid4()}",
            }
            await self._send_json(cancel_payload)
            logger.info("Sent response.cancel to OpenAI (barge-in)", call_id=self._call_id)
            self._pending_response = False
        except Exception:
            logger.error("Failed to send response.cancel", call_id=self._call_id, exc_info=True)

    async def _handle_function_call(self, event_data: Dict[str, Any]):
        """
        Handle function call request from OpenAI Realtime API.
        
        Routes the function call to the appropriate tool via the tool adapter.
        """
        try:
            # Build context for tool execution
            # These will be injected by the engine when it sets up the provider
            context = {
                'call_id': self._call_id,
                'caller_channel_id': getattr(self, '_caller_channel_id', None),
                'bridge_id': getattr(self, '_bridge_id', None),
                'called_number': getattr(self, '_called_number', None),
                'session_store': getattr(self, '_session_store', None),
                'ari_client': getattr(self, '_ari_client', None),
                'config': getattr(self, '_full_config', None),
                'allowed_tools': self._allowed_tools,
                'websocket': self.websocket,
                'is_ga': self._is_ga,  # Pass API version to adapter for correct response.create format
            }
            
            # Execute tool via adapter
            result = await self.tool_adapter.handle_tool_call_event(event_data, context)
            
            # Check if this is a hangup_call tool that will trigger hangup
            item = event_data.get("item", {})
            function_name = item.get("name")
            if function_name == "hangup_call" and result:
                # Check if tool result indicates hangup will occur
                # Tool adapter returns result directly in top-level dict
                if result.get("will_hangup"):
                    self._hangup_after_response = True
                    logger.info(
                        "🔚 Hangup tool executed - next response will trigger hangup",
                        call_id=self._call_id,
                        function_name=function_name,
                        farewell=result.get("message")
                    )
            
            # Send result back to OpenAI
            await self.tool_adapter.send_tool_result(result, context)

            # For hangup_call, create a dedicated farewell response with tools disabled so OpenAI
            # doesn't recurse into another tool call (which can lead to `farewell_no_audio` hangups).
            if function_name == "hangup_call" and result and result.get("will_hangup"):
                farewell_text = str(result.get("message") or "").strip()
                if farewell_text and self.websocket and self.websocket.state.name == "OPEN":
                    try:
                        # Disable tool calling for the farewell response to force spoken audio.
                        await self._send_json(
                            {
                                "type": "session.update",
                                "event_id": f"sess-tools-none-{uuid.uuid4()}",
                                "session": self._ga_session_type({"tool_choice": "none"}),
                            }
                        )
                    except Exception:
                        logger.debug(
                            "Failed to disable tool_choice for farewell response",
                            call_id=self._call_id,
                            exc_info=True,
                        )

                    try:
                        farewell_response: Dict[str, Any] = {
                            "instructions": (
                                "Say the following sentence to the user exactly, then stop. "
                                f"Do not call any tools: {farewell_text}"
                            ),
                        }
                        if not self._is_ga:
                            farewell_response["modalities"] = self._response_modalities
                            farewell_response["input"] = []
                        await self._send_json(
                            {
                                "type": "response.create",
                                "event_id": f"resp-farewell-{uuid.uuid4()}",
                                "response": farewell_response,
                            }
                        )
                        self._pending_response = True
                        logger.info(
                            "🎤 Farewell response.create sent (tools disabled)",
                            call_id=self._call_id,
                            farewell_preview=farewell_text[:80],
                            modalities=farewell_response.get("modalities"),
                        )
                    except Exception:
                        logger.debug("Failed to send farewell response.create", call_id=self._call_id, exc_info=True)
            
            # Log tool call to session for call history (Milestone 21)
            try:
                session_store = getattr(self, '_session_store', None)
                if session_store and self._call_id and function_name:
                    from datetime import datetime
                    session = await session_store.get_by_call_id(self._call_id)
                    if session:
                        tool_record = {
                            "name": function_name,
                            "params": item.get("arguments", {}),
                            "result": result.get("status", "unknown") if isinstance(result, dict) else "success",
                            "message": result.get("message", "") if isinstance(result, dict) else str(result),
                            "timestamp": datetime.now().isoformat(),
                            "duration_ms": 0,
                        }
                        if not hasattr(session, 'tool_calls') or session.tool_calls is None:
                            session.tool_calls = []
                        session.tool_calls.append(tool_record)
                        await session_store.upsert_call(session)
                        logger.debug("Tool call logged to session", call_id=self._call_id, tool=function_name)
            except Exception as log_err:
                logger.debug(f"Failed to log tool call to session: {log_err}", call_id=self._call_id)
            
        except Exception as e:
            logger.error(
                "Function call handling failed",
                call_id=self._call_id,
                error=str(e),
                exc_info=True
            )
            # Send error response to OpenAI in correct format
            try:
                item = event_data.get("item", {})
                call_id_field = item.get("call_id")
                if call_id_field:
                    error_response = {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id_field,
                            "output": json.dumps({
                                "status": "error",
                                "message": f"Tool execution failed: {str(e)}",
                                "error": str(e)
                            })
                        }
                    }
                    if self.websocket and self.websocket.state.name == "OPEN":
                        await self._send_json(error_response)
                        logger.info("Sent error response to OpenAI", call_id=call_id_field)
            except Exception as send_error:
                logger.error(f"Failed to send error response: {send_error}")

    async def stop_session(self):
        if self._closing or self._closed:
            return

        self._closing = True
        try:
            if self._receive_task:
                self._receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._receive_task
            if self._keepalive_task:
                self._keepalive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._keepalive_task
            
            # Cancel farewell timeout if active
            self._cancel_farewell_timeout()

            if self._greeting_vad_task and not self._greeting_vad_task.done():
                self._greeting_vad_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._greeting_vad_task

            bg_tasks = list(self._background_tasks)
            for task in bg_tasks:
                if not task.done():
                    task.cancel()
            if bg_tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.gather(*bg_tasks, return_exceptions=True)

            if self.websocket and self.websocket.state.name == "OPEN":
                await self.websocket.close()

            await self._emit_audio_done()
        finally:
            # Cleanup pacer
            try:
                self._pacer_running = False
                if self._pacer_task:
                    self._pacer_task.cancel()
            except Exception:
                pass
            previous_call_id = self._call_id
            self._receive_task = None
            self._keepalive_task = None
            self._greeting_vad_task = None
            self._background_tasks.clear()
            self.websocket = None
            self._call_id = None
            self._closing = False
            self._closed = True
            self._pending_response = False
            self._in_audio_burst = False
            self._input_resample_state = None
            self._output_resample_state = None
            self._transcript_buffer = ""
            logger.info("OpenAI Realtime session stopped")
            self._clear_metrics(previous_call_id)

    def get_provider_info(self) -> Dict[str, Any]:
        return {
            "name": "OpenAIRealtimeProvider",
            "type": "cloud",
            "model": self.config.model,
            "voice": self.config.voice,
            "supported_codecs": self.supported_codecs,
        }

    def is_ready(self) -> bool:
        return bool(self.config.api_key)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @property
    def _is_ga(self) -> bool:
        """True when using the GA Realtime API (no beta header)."""
        return getattr(self.config, 'api_version', 'ga').lower() != 'beta'

    def _ga_session_type(self, session: Dict[str, Any]) -> Dict[str, Any]:
        """Inject session type for GA API if not already present."""
        if self._is_ga and "type" not in session:
            session["type"] = "realtime"
        return session

    @property
    def _modalities_key(self) -> str:
        """GA uses 'output_modalities'; Beta uses 'modalities'."""
        return "output_modalities" if self._is_ga else "modalities"

    @property
    def _response_modalities(self) -> list:
        """GA only accepts ['audio'] or ['text']; Beta accepts ['audio','text']."""
        if self._is_ga:
            return ["audio"]
        return [m for m in (self.config.response_modalities or []) if m in ("audio", "text")] or ["audio"]

    def _build_ws_url(self) -> str:
        base = (self.config.base_url or "").strip()
        # Fallback if unresolved placeholders exist or scheme isn't ws/wss
        if base.startswith("${") or not base.startswith(("ws://", "wss://")):
            logger.warning("Invalid OpenAI base_url in config; falling back to default", base_url=base)
            base = "wss://api.openai.com/v1/realtime"
        base = base.rstrip("/")
        return f"{base}?model={self.config.model}"

    async def _send_session_update(self):
        # Map config modalities to output_modalities per latest guide
        output_modalities = [m for m in (self.config.response_modalities or []) if m in ("audio", "text")]
        if not output_modalities:
            output_modalities = ["audio"]

        # CRITICAL FIX: Use output_encoding (what OpenAI sends), NOT target_encoding (downstream format)
        output_enc = (self.config.output_encoding or "linear16").lower()
        input_enc = (getattr(self.config, "provider_input_encoding", None) or "linear16").lower()

        if self._is_ga:
            # GA API: exact schema from official OpenAI API reference (session.created example)
            # - format objects under audio.input.format / audio.output.format with type + rate
            # - turn_detection under audio.input.turn_detection
            # - transcription under audio.input.transcription
            # - voice under audio.output.voice
            # - MIME format types: audio/pcm (24kHz), audio/pcmu (8kHz), audio/pcma (8kHz)
            def _ga_audio_fmt(enc: str) -> str:
                enc = enc.lower()
                if enc in ("ulaw", "mulaw", "g711_ulaw", "mu-law"):
                    return "audio/pcmu"
                elif enc in ("alaw", "g711_alaw"):
                    return "audio/pcma"
                return "audio/pcm"

            in_fmt = _ga_audio_fmt(input_enc)
            out_fmt = _ga_audio_fmt(output_enc)
            # Rate is tied to format: audio/pcm=24000, audio/pcmu=8000, audio/pcma=8000
            in_rate = 8000 if in_fmt in ("audio/pcmu", "audio/pcma") else 24000
            out_rate = 8000 if out_fmt in ("audio/pcmu", "audio/pcma") else 24000

            # Build audio.input with turn_detection nested inside
            audio_input: Dict[str, Any] = {
                "format": {"type": in_fmt, "rate": in_rate},
                "transcription": {"model": "whisper-1"},
            }
            # turn_detection lives under audio.input per official GA schema
            td_config: Dict[str, Any] = {
                "type": "server_vad",
                "create_response": True,
                "interrupt_response": True,
            }
            if getattr(self.config, "turn_detection", None):
                try:
                    td = self.config.turn_detection
                    td_config.update({
                        "type": td.type,
                        "silence_duration_ms": td.silence_duration_ms,
                        "threshold": td.threshold,
                        "prefix_padding_ms": td.prefix_padding_ms,
                    })
                except Exception:
                    logger.debug("Failed to build turn_detection for GA", call_id=self._call_id, exc_info=True)
            audio_input["turn_detection"] = td_config

            # GA: always request audio/pcm @ 24kHz output — engine transcodes downstream
            # (audio/pcmu output may be silently ignored by some models)
            session: Dict[str, Any] = {
                "type": "realtime",
                "output_modalities": ["audio"],
                "audio": {
                    "input": audio_input,
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "voice": self.config.voice,
                    },
                },
            }
        else:
            # Beta API: flat format strings (pcm16, g711_ulaw, g711_alaw)
            def _beta_audio_fmt(enc: str) -> str:
                enc = enc.lower()
                if enc in ("ulaw", "mulaw", "g711_ulaw", "mu-law"):
                    return "g711_ulaw"
                elif enc in ("alaw", "g711_alaw"):
                    return "g711_alaw"
                return "pcm16"

            in_fmt = _beta_audio_fmt(input_enc)
            out_fmt = _beta_audio_fmt(output_enc)

            session: Dict[str, Any] = {
                "modalities": output_modalities,
                "input_audio_format": in_fmt,
                "output_audio_format": out_fmt,
                "voice": self.config.voice,
                "input_audio_transcription": {
                    "model": "whisper-1"
                },
            }
            # Beta: turn_detection at session level
            if getattr(self.config, "turn_detection", None):
                try:
                    td = self.config.turn_detection
                    session["turn_detection"] = {
                        "type": td.type,
                        "silence_duration_ms": td.silence_duration_ms,
                        "threshold": td.threshold,
                        "prefix_padding_ms": td.prefix_padding_ms,
                    }
                    logger.info(
                        "Using custom turn_detection config from YAML",
                        call_id=self._call_id,
                        threshold=td.threshold,
                        silence_ms=td.silence_duration_ms,
                    )
                except Exception:
                    logger.debug("Failed to include turn_detection in session.update", call_id=self._call_id, exc_info=True)
        
        # GA API requires session type
        self._ga_session_type(session)
        
        # Optional: Add temperature control (affects response creativity and consistency)
        if hasattr(self.config, 'temperature') and self.config.temperature is not None:
            session["temperature"] = self.config.temperature
        
        # Optional: Add max response tokens (encourages complete audio responses)
        if hasattr(self.config, 'max_response_output_tokens') and self.config.max_response_output_tokens:
            session["max_response_output_tokens"] = self.config.max_response_output_tokens

        # Build instructions with audio-forcing prefix
        # CRITICAL: Per OpenAI community reports (Dec 2024), the Realtime API has a bug
        # where modalities can get "stuck" on text-only after any text exchange.
        # Adding explicit audio instructions helps force audio output.
        audio_forcing_prefix = (
            "IMPORTANT: You are a voice-based AI assistant. "
            "ALWAYS respond with AUDIO speech, never text-only. "
            "Every response MUST include spoken audio output. "
        )
        
        if self.config.instructions:
            session["instructions"] = audio_forcing_prefix + self.config.instructions
        else:
            session["instructions"] = audio_forcing_prefix

        # Add tool calling configuration (context allowlist only)
        try:
            tools = self.tool_adapter.get_tools_config(list(self._allowed_tools or []))
            if tools:
                session["tools"] = tools
                session["tool_choice"] = "auto"  # Let OpenAI decide when to call tools
                logger.info(
                    f"🛠️  OpenAI session configured with {len(tools)} tools",
                    call_id=self._call_id,
                )
        except Exception as e:
            logger.warning(
                f"Failed to add tools to OpenAI session: {e}",
                call_id=self._call_id,
                exc_info=True,
            )

        payload: Dict[str, Any] = {
            "type": "session.update",
            "event_id": f"sess-{uuid.uuid4()}",
            "session": session,
        }

        # DEBUG: Log what we're actually sending to OpenAI
        logger.info(
            "OpenAI session.update payload",
            call_id=self._call_id,
            output_audio_format=session.get("output_audio_format"),
            input_audio_format=session.get("input_audio_format"),
            modalities=session.get("modalities"),
        )

        await self._send_json(payload)

    async def _send_explicit_greeting(self):
        greeting = (self.config.greeting or "").strip()
        if not greeting or not self.websocket or self.websocket.state.name != "OPEN":
            return

        # Per OpenAI Dec 2024 docs: Disable turn_detection during greeting
        # to prevent user speech from interrupting the greeting
        logger.info(
            "🔇 Disabling turn_detection for greeting playback",
            call_id=self._call_id
        )
        
        # Disable VAD before greeting (Beta only - GA doesn't accept turn_detection)
        if not self._is_ga:
            disable_vad_payload: Dict[str, Any] = {
                "type": "session.update",
                "event_id": f"sess-disable-vad-{uuid.uuid4()}",
                "session": self._ga_session_type({
                    "turn_detection": None  # Disable automatic VAD
                })
            }
            await self._send_json(disable_vad_payload)
        
        # Small delay to ensure VAD disable is processed
        await asyncio.sleep(0.1)

        # Build response.create payload
        if self._is_ga:
            # GA: minimal response object — modalities set via session, not response
            response_payload: Dict[str, Any] = {
                "type": "response.create",
                "event_id": f"resp-{uuid.uuid4()}",
                "response": {
                    "instructions": f"Please greet the user with the following: {greeting}",
                },
            }
        else:
            # Beta: include modalities and input
            response_payload: Dict[str, Any] = {
                "type": "response.create",
                "event_id": f"resp-{uuid.uuid4()}",
                "response": {
                    "modalities": self._response_modalities,
                    "instructions": f"Please greet the user with the following: {greeting}",
                    "input": [],
                },
            }
        
        logger.info(
            "🎤 Sending greeting response.create",
            call_id=self._call_id,
            greeting_preview=greeting[:50] + "..." if len(greeting) > 50 else greeting,
        )

        await self._send_json(response_payload)
        self._pending_response = True
        
        logger.info(
            "🛡️  Greeting sent - will re-enable VAD after completion",
            call_id=self._call_id
        )
        
        # FALLBACK: Re-enable VAD after timeout in case response.done doesn't fire correctly
        # This ensures two-way conversation can proceed even if greeting tracking fails
        if self._greeting_vad_task and not self._greeting_vad_task.done():
            self._greeting_vad_task.cancel()
        self._greeting_vad_task = asyncio.create_task(self._greeting_vad_fallback())
        self._greeting_vad_task.add_done_callback(_log_provider_task_exception)
        self._background_tasks.add(self._greeting_vad_task)
        self._greeting_vad_task.add_done_callback(self._background_tasks.discard)

    async def _greeting_vad_fallback(self):
        """Fallback to re-enable VAD if greeting completion detection fails."""
        try:
            # Wait for greeting to complete (typical greeting is 3-5 seconds)
            await asyncio.sleep(5.0)
            
            # If VAD wasn't re-enabled yet, do it now
            if not self._greeting_completed:
                logger.warning(
                    "⚠️ VAD fallback - greeting completion not detected, re-enabling VAD",
                    call_id=self._call_id
                )
                self._greeting_completed = True
                await self._re_enable_vad()
        except asyncio.CancelledError:
            pass  # Task cancelled on session stop
        except Exception:
            logger.debug("VAD fallback failed", call_id=self._call_id, exc_info=True)

    async def _re_enable_vad(self):
        """Re-enable turn_detection after greeting completes."""
        if not self.websocket or self.websocket.state.name != "OPEN":
            return
        
        # Build turn_detection config from YAML or use OpenAI defaults
        turn_detection_config = None
        if getattr(self.config, "turn_detection", None):
            try:
                td = self.config.turn_detection
                turn_detection_config = {
                    "type": td.type,
                    "silence_duration_ms": td.silence_duration_ms,
                    "threshold": td.threshold,
                    "prefix_padding_ms": td.prefix_padding_ms,
                }
            except Exception:
                logger.debug("Failed to build turn_detection config, using OpenAI defaults", 
                           call_id=self._call_id, exc_info=True)
        
        # GA API does not accept turn_detection in session.update; skip entirely
        if self._is_ga:
            logger.info(
                "🔊 GA mode: skipping turn_detection re-enable (server manages VAD)",
                call_id=self._call_id,
            )
        else:
            # If no config in YAML, let OpenAI use its defaults by not setting the field
            # This is better than hardcoding default values
            session_update = {}
            if turn_detection_config:
                session_update["turn_detection"] = turn_detection_config
            else:
                # Use OpenAI's default server_vad configuration
                session_update["turn_detection"] = {"type": "server_vad"}
            
            enable_vad_payload: Dict[str, Any] = {
                "type": "session.update",
                "event_id": f"sess-enable-vad-{uuid.uuid4()}",
                "session": self._ga_session_type(session_update)
            }
            
            await self._send_json(enable_vad_payload)
            logger.info(
                "🔊 Turn_detection re-enabled after greeting",
                call_id=self._call_id,
                config=turn_detection_config if turn_detection_config else "OpenAI defaults"
            )

    async def _ensure_response_request(self):
        if self._pending_response or not self.websocket or self.websocket.state.name != "OPEN":
            return

        resp_obj: Dict[str, Any] = {}
        if not self._is_ga:
            resp_obj[self._modalities_key] = self._response_modalities
        resp_obj["metadata"] = {"call_id": self._call_id}
        if self.config.instructions:
            resp_obj["instructions"] = self.config.instructions

        response_payload: Dict[str, Any] = {
            "type": "response.create",
            "event_id": f"resp-{uuid.uuid4()}",
            "response": resp_obj,
        }

        await self._send_json(response_payload)
        self._pending_response = True

    def _start_farewell_timeout(self):
        """Start a 5-second timeout to ensure hangup happens even if OpenAI doesn't generate audio."""
        # Cancel any existing timeout first
        self._cancel_farewell_timeout()
        
        # Create new timeout task
        self._farewell_timeout_task = asyncio.create_task(self._farewell_timeout_handler())
        logger.debug(
            "⏱️  Farewell timeout started (5s fallback)",
            call_id=self._call_id
        )
    
    def _cancel_farewell_timeout(self):
        """Cancel the farewell timeout if it's still running."""
        if self._farewell_timeout_task and not self._farewell_timeout_task.done():
            self._farewell_timeout_task.cancel()
            logger.debug(
                "⏱️  Farewell timeout cancelled",
                call_id=self._call_id
            )
            self._farewell_timeout_task = None
    
    async def _farewell_timeout_handler(self):
        """Wait 5 seconds, then trigger hangup if farewell audio wasn't generated."""
        try:
            await asyncio.sleep(5.0)
            
            # If we reach here, timeout expired without being cancelled
            logger.warning(
                "⏱️  Farewell timeout expired - OpenAI did not generate audio within 5s, triggering hangup anyway",
                call_id=self._call_id
            )
            
            # Emit HangupReady event to trigger hangup
            try:
                if self.on_event:
                    await self.on_event({
                        "type": "HangupReady",
                        "call_id": self._call_id,
                        "reason": "farewell_timeout",
                        "had_audio": False
                    })
            except Exception as e:
                logger.error(
                    "Failed to emit HangupReady event from timeout",
                    call_id=self._call_id,
                    error=str(e),
                    exc_info=True
                )
        except asyncio.CancelledError:
            # Normal cancellation when audio completes
            pass
        except Exception as e:
            logger.error(
                "Farewell timeout handler error",
                call_id=self._call_id,
                error=str(e),
                exc_info=True
            )

    async def _send_json(self, payload: Dict[str, Any]):
        if not self.websocket or self.websocket.state.name != "OPEN":
            return
        # Avoid logging base64 audio payloads; but log control message types
        try:
            ptype = payload.get("type")
            if ptype and not ptype.startswith("input_audio_buffer."):
                logger.debug("OpenAI send", call_id=self._call_id, type=ptype)
        except Exception:
            pass
        message = json.dumps(payload)
        async with self._send_lock:
            await self.websocket.send(message)
    
    async def _cancel_response(self, response_id: str):
        """
        Cancel an in-progress response when user interrupts (barge-in).
        
        This implements the OpenAI Realtime API's response.cancel event,
        which stops audio generation and discards remaining chunks when
        the user starts speaking during an AI response.
        
        See: https://platform.openai.com/docs/api-reference/realtime-client-events/response/cancel
        """
        if not self.websocket or self.websocket.state.name != "OPEN":
            return

        try:
            cancel_payload = {
                "type": "response.cancel",
                "event_id": f"cancel-{uuid.uuid4()}",
                "response_id": response_id
            }
            await self._send_json(cancel_payload)
            logger.debug(
                "Sent response.cancel to OpenAI",
                call_id=self._call_id,
                response_id=response_id
            )
            # Local egress can have buffered audio (pacer/outbuf). Flush it immediately so the interrupted
            # sentence does not resume locally even if OpenAI continues sending a few in-flight frames.
            try:
                await self._emit_audio_done()
            except Exception:
                logger.debug("Failed emitting AgentAudioDone during barge-in cancel", call_id=self._call_id, exc_info=True)
            try:
                async with self._pacer_lock:
                    self._outbuf.clear()
            except Exception:
                logger.debug("Failed clearing pacer buffer during barge-in cancel", call_id=self._call_id, exc_info=True)
            try:
                self._pacer_running = False
                if self._pacer_task and not self._pacer_task.done():
                    self._pacer_task.cancel()
            except Exception:
                logger.debug("Failed stopping pacer during barge-in cancel", call_id=self._call_id, exc_info=True)
        except Exception:
            logger.error(
                "Failed to cancel OpenAI response",
                call_id=self._call_id,
                response_id=response_id,
                exc_info=True
            )

    async def _emit_provider_barge_in(self, *, event_type: str) -> None:
        """Notify the engine that provider-side VAD detected user interruption.

        Engine uses this to flush local playback immediately (Option 2),
        while OpenAI remains responsible for response cancellation/turn-taking.
        """
        try:
            now = time.time()
            if now - float(self._last_barge_in_emit_ts or 0.0) < 0.25:
                return
            self._last_barge_in_emit_ts = now
            await self.on_event(
                {
                    "type": "ProviderBargeIn",
                    "call_id": self._call_id,
                    "provider": "openai_realtime",
                    "event": event_type,
                }
            )
        except Exception:
            logger.debug("Failed to emit ProviderBargeIn", call_id=self._call_id, exc_info=True)
    
    async def _send_audio_to_openai(self, pcm16: bytes):
        """Helper method to send PCM16 audio to OpenAI (extracted for gating logic).
        
        This contains the actual audio sending logic that was previously inline in send_audio.
        It handles both VAD-enabled and manual commit modes.
        """
        # Turn start tracking moved to response.done event to count conversational turns correctly
        
        # If server VAD is enabled, just append frames; do not commit.
        vad_enabled = getattr(self.config, "turn_detection", None) is not None
        if vad_enabled:
            try:
                audio_b64 = base64.b64encode(pcm16).decode("ascii")
                await self._send_json({"type": "input_audio_buffer.append", "audio": audio_b64})
            except Exception:
                logger.error("Failed to append input audio buffer (VAD)", call_id=self._call_id, exc_info=True)
        else:
            # Serialize accumulation and commit to avoid empty commits due to races
            async with self._audio_lock:
                # Accumulate until we have >= 160ms to comfortably satisfy >=100ms minimum
                self._pending_audio_provider_rate.extend(pcm16)
                bytes_per_ms = int(self.config.provider_input_sample_rate_hz * 2 / 1000)
                commit_threshold_ms = 160
                commit_threshold_bytes = bytes_per_ms * commit_threshold_ms

                if len(self._pending_audio_provider_rate) >= commit_threshold_bytes:
                    chunk = bytes(self._pending_audio_provider_rate)
                    self._pending_audio_provider_rate.clear()
                    audio_b64 = base64.b64encode(chunk).decode("ascii")
                    try:
                        await self._send_json({"type": "input_audio_buffer.append", "audio": audio_b64})
                        # CRITICAL FIX #2: Do NOT manually commit input audio buffer
                        # Manual commits caused 310 "buffer too small" errors (40% failure rate)
                        # OpenAI automatically commits when speech_stopped is detected (per API design)
                        # Removes empty buffer errors and lets OpenAI handle turn-taking naturally
                        # await self._send_json({"type": "input_audio_buffer.commit"})
                        self._last_commit_ts = time.monotonic()
                        logger.info(
                            "OpenAI appended input audio (auto-commit on speech_stopped)",
                            call_id=self._call_id,
                            ms=len(chunk) // bytes_per_ms,
                            bytes=len(chunk),
                        )
                    except Exception:
                        logger.error("Failed to append input audio buffer", call_id=self._call_id, exc_info=True)
                    # CRITICAL FIX: Do NOT manually trigger response.create after every audio commit
                    # OpenAI's server_vad automatically generates responses when user stops speaking
                    # Calling _ensure_response_request() here caused 148 requests in 70s (spam!)
                    # Let OpenAI handle turn-taking naturally

    def _convert_inbound_audio(self, audio_chunk: bytes) -> Optional[bytes]:
        fmt_raw = getattr(self.config, "input_encoding", None) or "slin16"
        fmt = fmt_raw.strip().lower()
        # Persist sanitized value so future checks stay consistent
        try:
            self.config.input_encoding = fmt
        except Exception:
            pass

        valid_encodings = {
            "ulaw",
            "mulaw",
            "g711_ulaw",
            "mu-law",
            "slin16",
            "linear16",
            "pcm16",
        }
        if fmt not in valid_encodings:
            logger.warning("Unsupported input encoding for OpenAI Realtime", encoding=fmt_raw)
            fmt = "slin16"
            try:
                self.config.input_encoding = fmt
            except Exception:
                pass

        chunk_len = len(audio_chunk)
        # Infer actual transport format from canonical 20 ms frame sizes when possible
        #  - 160 B ≈ μ-law @ 8 kHz (20 ms)
        #  - 320 B ≈ PCM16 @ 8 kHz (20 ms)
        #  - 640 B ≈ PCM16 @ 16 kHz (20 ms)
        if chunk_len == 160:
            actual_format = "ulaw"
            inferred_rate = 8000
        elif chunk_len == 320:
            actual_format = "pcm16"
            inferred_rate = 8000
        elif chunk_len == 640:
            actual_format = "pcm16"
            inferred_rate = 16000
        else:
            actual_format = "pcm16" if fmt in ("slin16", "linear16", "pcm16") else "ulaw"
            inferred_rate = int(getattr(self.config, "input_sample_rate_hz", 0) or 0) or 8000

        # Select source_rate based on declared encoding and inference
        if actual_format == "ulaw":
            source_rate = 8000
        else:
            # PCM path: prefer declared input_sample_rate_hz if set, else inference
            declared_rate = int(getattr(self.config, "input_sample_rate_hz", 0) or 0)
            source_rate = declared_rate or inferred_rate or 8000
        if actual_format == "ulaw":
            pcm_src = mulaw_to_pcm16le(audio_chunk)
        else:
            pcm_src = audio_chunk

        # Diagnostics-only: probe PCM16 RMS native vs swapped once; do not mutate audio
        try:
            if actual_format == "pcm16" and not getattr(self, "_endianness_probe_done", False):
                import audioop  # local import to avoid top-level dependency for non-PCM paths
                rms_native = audioop.rms(pcm_src, 2) if pcm_src else 0
                try:
                    swapped = audioop.byteswap(pcm_src, 2) if pcm_src else b""
                    rms_swapped = audioop.rms(swapped, 2) if swapped else 0
                except Exception:
                    rms_swapped = 0
                try:
                    logger.info(
                        "OpenAI inbound PCM16 probe",
                        call_id=self._call_id,
                        rms_native=rms_native,
                        rms_swapped=rms_swapped,
                    )
                except Exception:
                    pass
                try:
                    self._endianness_probe_done = True
                except Exception:
                    pass
        except Exception:
            # Non-fatal; proceed without probe
            pass

        provider_rate = int(getattr(self.config, "provider_input_sample_rate_hz", 0) or 0)

        if provider_rate and provider_rate != source_rate:
            pcm_provider_rate, self._input_resample_state = resample_audio(
                pcm_src,
                source_rate,
                provider_rate,
                state=self._input_resample_state,
            )
            return pcm_provider_rate

        self._input_resample_state = None
        return pcm_src

    async def _receive_loop(self):
        assert self.websocket is not None
        try:
            async for message in self.websocket:
                if isinstance(message, bytes):
                    continue
                try:
                    event = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning("Failed to decode OpenAI Realtime payload", payload_preview=message[:64])
                    continue
                await self._handle_event(event)
        except asyncio.CancelledError:
            pass
        except (ConnectionClosedError, ConnectionClosedOK):
            logger.info("OpenAI Realtime connection closed", call_id=self._call_id)
        except Exception:
            logger.error("OpenAI Realtime receive loop error", call_id=self._call_id, exc_info=True)
        finally:
            await self._emit_audio_done()
            self._pending_response = False
            try:
                if not self._closing and not self._closed and self._call_id:
                    if not self._reconnect_task or self._reconnect_task.done():
                        self._reconnect_task = asyncio.create_task(self._reconnect_with_backoff())
            except Exception:
                logger.debug("Failed to schedule OpenAI reconnect", call_id=self._call_id, exc_info=True)

    async def _handle_event(self, event: Dict[str, Any]):
        event_type = event.get("type")

        # Log top-level error events with full payload to diagnose API contract issues
        if event_type == "error":
            error_code = event.get("error", {}).get("code")
            
            # Handle expected errors gracefully
            if error_code == "response_cancel_not_active":
                # Not an error - response already completed before cancellation
                logger.debug(
                    "Response already completed (cannot cancel)",
                    call_id=self._call_id,
                    response_id=self._current_response_id
                )
                return
            
            # Log other errors
            logger.error("OpenAI Realtime error event", call_id=self._call_id, error_event=event)
            return

        if event_type == "response.created":
            # Track response ID for potential cancellation on barge-in
            response = event.get("response", {})
            response_id = response.get("id")
            if response_id:
                self._current_response_id = response_id
                # Reset per-response audio tracking.
                try:
                    self._audio_seen_response_ids.discard(response_id)
                except Exception:
                    pass
                
                # Mark first response as greeting response (protected from barge-in)
                if not self._greeting_completed and self._greeting_response_id is None:
                    self._greeting_response_id = response_id
                    logger.info(
                        "🛡️  Greeting response created - protected from barge-in",
                        call_id=self._call_id,
                        response_id=response_id
                    )
                # Mark response as farewell if hangup was requested
                elif self._hangup_after_response:
                    self._farewell_response_id = response_id
                    logger.info(
                        "🔚 Farewell response created - will trigger hangup on completion",
                        call_id=self._call_id,
                        response_id=response_id
                    )
                    # Start fallback timeout in case OpenAI doesn't generate audio
                    self._start_farewell_timeout()
                    # Wait for output_audio.done before emitting HangupReady so we don't cut off speech.
                    self._farewell_waiting_for_audio_done = True
                else:
                    logger.debug("OpenAI response created", call_id=self._call_id, response_id=response_id)
            return

        if event_type == "response.delta":
            delta = event.get("delta") or {}
            delta_type = delta.get("type")

            if delta_type == "output_audio.delta":
                audio_b64 = delta.get("audio")
                if audio_b64:
                    await self._handle_output_audio(audio_b64)
            elif delta_type == "output_audio.done":
                await self._emit_audio_done()
            elif delta_type == "output_text.delta":
                text = delta.get("text")
                if text:
                    await self._emit_transcript(text, is_final=False)
            elif delta_type == "output_text.done":
                if self._transcript_buffer:
                    await self._emit_transcript("", is_final=True)
            return

        # Modern event naming variants (top-level types)
        if event_type == "response.output_audio.delta":
            # GA: audio is in event["delta"] as a base64 string, or event["audio"]
            delta = event.get("delta")
            audio_b64 = (
                event.get("audio")
                or (delta if isinstance(delta, str) else (delta or {}).get("audio"))
            )
            if audio_b64:
                await self._handle_output_audio(audio_b64)
            else:
                logger.debug("Missing audio in response.output_audio.delta", call_id=self._call_id)
            return

        if event_type == "response.output_audio.done":
            await self._emit_audio_done()
            return

        # Additional modern variant used by some previews
        if event_type == "response.audio.delta":
            audio_b64 = event.get("delta")
            if audio_b64:
                # Track audio burst for metrics, but don't use gating for server-side VAD
                if not self._in_audio_burst:
                    self._in_audio_burst = True
                    # SMOOTHNESS FIX: Record when audio started for interruption cooldown
                    self._response_audio_start_time = time.time()
                
                await self._handle_output_audio(audio_b64)
            else:
                logger.debug("Missing audio in response.audio.delta", call_id=self._call_id)
            return

        if event_type == "response.audio.done":
            # Track end of audio burst for metrics
            if self._in_audio_burst:
                self._in_audio_burst = False
            # NOTE: Don't reset _response_audio_start_time here - response.audio.done fires per-segment
            # We keep the timer until the full response is done to prevent mid-sentence interruption
            
            # NOTE: response.audio.done fires after EACH audio segment, not at end of response
            # Do NOT re-enable VAD here - it will trigger too early!
            # VAD re-enable handled in response.done event
            
            await self._emit_audio_done()
            return

        if event_type == "response.audio_transcript.delta":
            delta = event.get("delta")
            text = event.get("text")
            if text is None:
                if isinstance(delta, dict):
                    text = delta.get("text")
                elif isinstance(delta, str):
                    text = delta
            if text:
                await self._emit_transcript(text, is_final=False)
            return

        if event_type == "response.audio_transcript.done":
            if self._transcript_buffer:
                # Track assistant conversation for email tools
                await self._track_conversation("assistant", self._transcript_buffer)
                await self._emit_transcript("", is_final=True)
            return

        if event_type in ("response.completed", "response.error", "response.cancelled", "response.done"):
            # Track whether ANY audio was emitted during this response (not just "currently emitting").
            current_response_id = self._current_response_id
            had_audio_for_response = bool(
                current_response_id and current_response_id in self._audio_seen_response_ids
            )
            
            # Reset audio start time when response fully completes - allows interruption for next response
            self._response_audio_start_time = None
            
            # Note: Turn latency timer now starts on input_audio_buffer.speech_stopped
            # (moved from here for standardized measurement across providers)
            
            await self._emit_audio_done()
            
            # Only emit additional audio_done if this response actually had audio output
            # This prevents premature hangup when tool responses complete (no audio yet)
            # The farewell response will emit audio_done when IT completes with audio
            if event_type in ("response.completed", "response.done") and not had_audio_for_response:
                # DEBUG: Log response details to understand why no audio
                response_data = event.get("response", {})
                output_items = response_data.get("output", [])
                status = response_data.get("status")
                status_details = response_data.get("status_details")
                logger.warning(
                    "⚠️ Response completed without audio output - investigating",
                    call_id=self._call_id,
                    event_type=event_type,
                    response_status=status,
                    status_details=status_details,
                    output_items_count=len(output_items),
                    output_types=[item.get("type") for item in output_items] if output_items else [],
                )
            
            if event_type == "response.error":
                logger.error("OpenAI Realtime response error", call_id=self._call_id, error=event.get("error"))
            elif event_type == "response.cancelled":
                logger.info("OpenAI response cancelled (barge-in)", call_id=self._call_id, response_id=self._current_response_id)
            
            # Re-enable VAD when greeting response completes
            # response.done fires when entire response is generated (not per-segment)
            # This is the correct event to wait for, not response.audio.done (which fires per-segment)
            if (self._current_response_id == self._greeting_response_id and 
                not self._greeting_completed and 
                event_type in ("response.completed", "response.done")):
                self._greeting_completed = True
                logger.info(
                    "✅ Greeting response completed - re-enabling turn_detection",
                    call_id=self._call_id,
                    had_audio=had_audio_for_response
                )
                # Re-enable turn_detection now that greeting is fully generated
                await self._re_enable_vad()

                # Request early TTS gating clear so caller audio can flow after greeting
                try:
                    if self.on_event and self._call_id:
                        await self.on_event(
                            {
                                "type": "ClearTtsGating",
                                "call_id": self._call_id,
                                "reason": "greeting_completed",
                            }
                        )
                except Exception:
                    logger.debug(
                        "Failed to emit ClearTtsGating event",
                        call_id=self._call_id,
                        exc_info=True,
                    )
            
            # Check if this was the farewell response
            # CRITICAL: Check farewell_response_id is not None to prevent None == None false positive
            if (self._farewell_response_id is not None and 
                self._current_response_id == self._farewell_response_id and 
                event_type in ("response.completed", "response.done")):
                
                # Cancel timeout if it's still running
                self._cancel_farewell_timeout()
                
                # If farewell has audio, we hang up on output_audio.done (not response.done) so we don't
                # cut off the end of the spoken goodbye (provider can deliver audio faster than real-time).
                if had_audio_for_response:
                    logger.info(
                        "🔚 Farewell response completed with audio - waiting for output_audio.done",
                        call_id=self._call_id,
                        response_id=self._current_response_id
                    )
                else:
                    # No audio generated - trigger hangup immediately
                    logger.warning(
                        "⚠️  Farewell response completed WITHOUT audio - triggering immediate hangup",
                        call_id=self._call_id,
                        response_id=self._current_response_id
                    )
                    
                    # Emit HangupReady event immediately since there's no audio to wait for
                    try:
                        if self.on_event:
                            await self.on_event({
                                "type": "HangupReady",
                                "call_id": self._call_id,
                                "reason": "farewell_no_audio",
                                "had_audio": False
                            })
                    except Exception as e:
                        logger.error(
                            "Failed to emit HangupReady event for no-audio farewell",
                            call_id=self._call_id,
                            error=str(e),
                            exc_info=True,
                        )
                
                # Reset hangup marker; HangupReady will be emitted on output_audio.done if we had audio.
                if not had_audio_for_response:
                    self._farewell_waiting_for_audio_done = False
                    self._farewell_response_id = None
                self._hangup_after_response = False
            
            # Drop per-response audio tracking to avoid unbounded growth.
            try:
                if current_response_id:
                    self._audio_seen_response_ids.discard(current_response_id)
            except Exception:
                pass

            self._pending_response = False
            self._current_response_id = None  # Clear response ID after completion
            if self._transcript_buffer:
                await self._emit_transcript("", is_final=True)
            return

        if event_type == "conversation.item.input_audio_transcription.completed":
            # User speech transcription completed - works with server_vad
            transcript = event.get("transcript", "")
            if transcript:
                logger.info(
                    "📝 User transcript received",
                    call_id=self._call_id,
                    transcript_preview=transcript[:100] if len(transcript) > 100 else transcript
                )
                await self._emit_transcript(transcript, is_final=True)
                # Track user conversation for email tools and call history
                await self._track_conversation("user", transcript)
            return
        
        if event_type == "conversation.item.input_audio_transcription.failed":
            # Transcription failed - log but don't crash
            error = event.get("error", {})
            logger.warning(
                "⚠️ User transcription failed",
                call_id=self._call_id,
                error_type=error.get("type"),
                error_message=error.get("message"),
            )
            return

        if event_type == "response.output_text.delta":
            delta = event.get("delta") or {}
            text = delta.get("text")
            if text:
                await self._emit_transcript(text, is_final=False)
            return

        # Optional acks/telemetry for audio buffer operations
        if event_type and event_type.startswith("input_audio_buffer"):
            # Track turn start time when user STOPS speaking (Milestone 21)
            # This measures: speech end → first AI audio response
            if event_type == "input_audio_buffer.speech_stopped":
                self._turn_start_time = time.time()
                self._turn_first_audio_received = False
                logger.debug("Turn latency timer started (speech_stopped)", call_id=self._call_id)
            # Handle barge-in: cancel ongoing response when user starts speaking
            elif event_type == "input_audio_buffer.speech_started" and self._current_response_id:
                # Protect greeting response from barge-in cancellation
                if self._current_response_id == self._greeting_response_id and not self._greeting_completed:
                    logger.info(
                        "🛡️  Barge-in blocked - protecting greeting response",
                        call_id=self._call_id,
                        response_id=self._current_response_id
                    )
                # SMOOTHNESS FIX: Don't cancel if response just started - prevents premature cutoff
                elif self._response_audio_start_time:
                    elapsed = time.time() - self._response_audio_start_time
                    if elapsed < self._min_response_time_before_interrupt:
                        logger.info(
                            "🛡️  Barge-in blocked - response too young",
                            call_id=self._call_id,
                            response_id=self._current_response_id,
                            elapsed_seconds=round(elapsed, 2),
                            min_required=self._min_response_time_before_interrupt
                        )
                    else:
                        logger.info(
                            "🎤 User interruption detected, cancelling response",
                            call_id=self._call_id,
                            response_id=self._current_response_id,
                            elapsed_seconds=round(elapsed, 2)
                        )
                        await self._cancel_response(self._current_response_id)
                        await self._emit_provider_barge_in(event_type=event_type)
                else:
                    # No audio started yet, still cancel text-only responses
                    logger.info(
                        "🎤 User interruption detected (no audio), cancelling response",
                        call_id=self._call_id,
                        response_id=self._current_response_id
                    )
                    await self._cancel_response(self._current_response_id)
                    await self._emit_provider_barge_in(event_type=event_type)
            else:
                # IMPORTANT: even when there's no cancellable response (e.g., output buffered locally),
                # we still want the platform to flush local playback immediately on speech_started.
                if event_type == "input_audio_buffer.speech_started":
                    # Never interrupt the greeting turn via platform flush.
                    if self._greeting_response_id and not self._greeting_completed:
                        logger.info(
                            "🛡️  Barge-in blocked - protecting greeting response",
                            call_id=self._call_id,
                            response_id=self._greeting_response_id,
                        )
                    else:
                        # AudioSocket+streaming: a response can be "done" at the provider while
                        # we're still draining buffered audio locally (pacer/outbuf).
                        # If the caller starts speaking, we must stop emitting any remaining buffered
                        # audio immediately so the next turn can proceed normally.
                        try:
                            async with self._pacer_lock:
                                self._outbuf.clear()
                        except Exception:
                            logger.debug("Failed to clear OpenAI egress buffer on barge-in", call_id=self._call_id, exc_info=True)
                        try:
                            await self._emit_audio_done()
                        except Exception:
                            logger.debug("Failed to stop OpenAI egress pacer on barge-in", call_id=self._call_id, exc_info=True)
                        logger.info(
                            "🎤 User speech started (no active response); requesting platform flush",
                            call_id=self._call_id,
                            event_type=event_type,
                        )
                        await self._emit_provider_barge_in(event_type=event_type)
                else:
                    logger.info("OpenAI input_audio_buffer ack", call_id=self._call_id, event_type=event_type)
            return

        # Additional transcript variants per guide
        if event_type == "response.output_audio_transcript.delta":
            delta = event.get("delta")
            text = None
            if isinstance(delta, dict):
                text = delta.get("text")
            elif isinstance(delta, str):
                text = delta
            if text:
                await self._emit_transcript(text, is_final=False)
            return

        if event_type == "response.output_audio_transcript.done":
            if self._transcript_buffer:
                # Track assistant conversation for email tools
                await self._track_conversation("assistant", self._transcript_buffer)
                await self._emit_transcript("", is_final=True)
            return

        # CRITICAL FIX #1: Handle session.updated ACK (following Deepgram pattern)
        if event_type == "session.updated":
            try:
                session = event.get("session", {})
                input_format = session.get("input_audio_format", "pcm16")
                output_format = session.get("output_audio_format", "pcm16")
                
                # Map OpenAI format names to internal format names and sample rates
                format_map = {
                    'pcm16': ('pcm16', 24000),
                    'g711_ulaw': ('g711_ulaw', 8000),
                    'g711_alaw': ('g711_alaw', 8000),
                }
                
                if output_format in format_map:
                    fmt, rate = format_map[output_format]
                    self._provider_output_format = fmt
                    self._active_output_sample_rate_hz = rate
                    self._outfmt_acknowledged = True
                
                logger.info(
                    "✅ OpenAI session.updated ACK received",
                    call_id=self._call_id,
                    input_format=input_format,
                    output_format=output_format,
                    sample_rate=self._active_output_sample_rate_hz,
                    acknowledged=self._outfmt_acknowledged,
                )
                
                # Unblock audio streaming (similar to Deepgram's _ack_event.set())
                if hasattr(self, '_session_ack_event') and self._session_ack_event:
                    self._session_ack_event.set()
                
            except Exception as exc:
                logger.error(
                    "Failed to process session.updated event",
                    call_id=self._call_id,
                    error=str(exc),
                    exc_info=True
                )
            return

        # Handle function calls from response.output_item.done events
        # This is the correct event per OpenAI Realtime API spec
        if event_type == "response.output_item.done":
            item = event.get("item", {})
            if item.get("type") == "function_call":
                call_id_field = item.get("call_id")
                function_name = item.get("name")
                logger.info(
                    "📞 OpenAI function call detected",
                    call_id=self._call_id,
                    function_call_id=call_id_field,
                    function_name=function_name,
                )
                # Handle function call via tool adapter
                task = asyncio.create_task(self._handle_function_call(event))
                task.add_done_callback(_log_provider_task_exception)
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            return

        logger.debug("Unhandled OpenAI Realtime event", event_type=event_type)

    async def _handle_output_audio(self, audio_b64: str):
        try:
            raw_bytes = base64.b64decode(audio_b64)
        except Exception:
            logger.warning("Invalid base64 audio payload from OpenAI", call_id=self._call_id)
            return

        if not raw_bytes:
            return
        
        # Mark audio observed for this response id (used for reliable hangup behavior).
        try:
            if self._current_response_id:
                self._audio_seen_response_ids.add(self._current_response_id)
        except Exception:
            pass

        # Track turn latency on first audio output (Milestone 21 - Call History)
        if self._turn_start_time is not None and not self._turn_first_audio_received:
            self._turn_first_audio_received = True
            turn_latency_ms = (time.time() - self._turn_start_time) * 1000
            # Save to session for call history
            if self._session_store and self._call_id:
                try:
                    call_id_copy = self._call_id
                    latency_copy = turn_latency_ms
                    async def save_latency():
                        try:
                            session = await self._session_store.get_by_call_id(call_id_copy)
                            if session:
                                session.turn_latencies_ms.append(latency_copy)
                                await self._session_store.upsert_call(session)
                                logger.debug("Turn latency saved to session", call_id=call_id_copy, latency_ms=round(latency_copy, 1))
                            else:
                                logger.debug("Session not found for latency tracking", call_id=call_id_copy)
                        except Exception as e:
                            logger.debug("Failed to save turn latency", call_id=call_id_copy, error=str(e))
                    task = asyncio.create_task(save_latency())
                    task.add_done_callback(_log_provider_task_exception)
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
                except Exception as e:
                    logger.debug("Failed to create latency save task", error=str(e))
            logger.info("Turn latency recorded", call_id=self._call_id, latency_ms=round(turn_latency_ms, 1))

        # Always update the output meter with provider-native bytes
        self._update_output_meter(len(raw_bytes))

        # Fast-path: only after server ACK, if provider emits μ-law and downstream target is μ-law@8k, pass through bytes
        target_enc = (self.config.target_encoding or "").lower()
        if (
            self._outfmt_acknowledged
            and self._provider_output_format in ("g711_ulaw", "ulaw", "mulaw", "g711", "mu-law")
            and target_enc in ("ulaw", "mulaw", "g711_ulaw", "mu-law")
            and int(self.config.target_sample_rate_hz or 0) == 8000
            and int(round(self._active_output_sample_rate_hz or 8000)) == 8000
        ):
            outbound = raw_bytes
        else:
            # Otherwise, normalize to PCM16 using either ACK'ed format or inferred format, then convert
            effective_fmt = self._provider_output_format
            if not self._outfmt_acknowledged:
                # Heuristic inference when ACK missing: prefer μ-law if odd length or RMS-ulaw >> RMS-pcm
                inferred = None
                try:
                    l = len(raw_bytes)
                    if l % 2 == 1:
                        inferred = "ulaw"
                    else:
                        # Compare RMS when treated as PCM16 vs μ-law→PCM16 on a small window
                        win_pcm = raw_bytes[: min(640, l - (l % 2))]
                        rms_pcm = audioop.rms(win_pcm, 2) if win_pcm else 0
                        try:
                            win_mulaw_pcm16 = mulaw_to_pcm16le(raw_bytes[: min(320, l)])
                        except Exception:
                            win_mulaw_pcm16 = b""
                        rms_ulaw = audioop.rms(win_mulaw_pcm16, 2) if win_mulaw_pcm16 else 0
                        if rms_ulaw > max(50, int(1.5 * (rms_pcm or 1))):
                            inferred = "ulaw"
                        else:
                            inferred = "pcm16"
                except Exception:
                    inferred = None
                self._inferred_provider_encoding = inferred or self._inferred_provider_encoding or "pcm16"
                effective_fmt = self._inferred_provider_encoding
                if not self._inference_logged:
                    try:
                        logger.info(
                            "OpenAI output format not ACKed; using inferred decode path",
                            call_id=self._call_id,
                            inferred=effective_fmt,
                            bytes=len(raw_bytes),
                        )
                    except Exception:
                        pass
                    self._inference_logged = True

            # CRITICAL FIX #3: Warn loudly if format not ACKed (following Deepgram strict pattern)
            if not self._outfmt_acknowledged and not self._inference_logged:
                logger.warning(
                    "⚠️ Processing audio without format ACK - using inference fallback",
                    call_id=self._call_id,
                    inferred_format=effective_fmt,
                    note="Audio quality may be degraded. OpenAI should send session.updated ACK."
                )
            
            # Decode to PCM16 according to effective format
            if effective_fmt in ("g711_ulaw", "ulaw", "mulaw", "g711", "mu-law"):
                try:
                    pcm_provider_output = mulaw_to_pcm16le(raw_bytes)
                except Exception:
                    logger.warning("Failed to convert μ-law provider output to PCM16", call_id=self._call_id, exc_info=True)
                    return
            else:
                pcm_provider_output = raw_bytes

            target_rate = self.config.target_sample_rate_hz
            # Determine source_rate more safely when provider hasn't ACKed.
            # If we inferred μ-law, the true source is 8000 Hz regardless of config defaults.
            if not self._outfmt_acknowledged and effective_fmt in ("g711_ulaw", "ulaw", "mulaw", "g711", "mu-law"):
                source_rate = 8000
            else:
                source_rate = int(round(self._active_output_sample_rate_hz or self.config.output_sample_rate_hz or 0))
                if not source_rate:
                    source_rate = self.config.output_sample_rate_hz
            pcm_target, self._output_resample_state = resample_audio(
                pcm_provider_output,
                source_rate,
                target_rate,
                state=self._output_resample_state,
            )

            outbound = convert_pcm16le_to_target_format(pcm_target, self.config.target_encoding)
            if not outbound:
                return

        # Append to egress buffer and start pacer, or emit immediately if disabled
        try:
            async with self._pacer_lock:
                self._outbuf.extend(outbound)
        except Exception:
            logger.debug("Failed appending to pacer buffer", call_id=self._call_id, exc_info=True)

        if self._egress_pacer_enabled:
            await self._ensure_pacer_started()
        else:
            # Fallback to immediate emit (legacy behavior)
            if self.on_event:
                if not self._first_output_chunk_logged:
                    logger.info(
                        "OpenAI Realtime first audio chunk",
                        call_id=self._call_id,
                        bytes=len(outbound),
                        target_encoding=self.config.target_encoding,
                    )
                    self._first_output_chunk_logged = True
                self._in_audio_burst = True
                try:
                    await self.on_event(
                        {
                            "type": "AgentAudio",
                            "data": outbound,
                            "streaming_chunk": True,
                            "call_id": self._call_id,
                            "encoding": (self.config.target_encoding or "slin16"),
                            "sample_rate": self.config.target_sample_rate_hz,
                        }
                    )
                except Exception:
                    logger.error("Failed to emit AgentAudio event", call_id=self._call_id, exc_info=True)

    async def _emit_audio_done(self):
        if not self.on_event or not self._call_id:
            return
        try:
            if self._in_audio_burst:
                await self.on_event(
                    {
                        "type": "AgentAudioDone",
                        "streaming_done": True,
                        "call_id": self._call_id,
                    }
                )
        except Exception:
            logger.error("Failed to emit AgentAudioDone event", call_id=self._call_id, exc_info=True)
        finally:
            self._in_audio_burst = False
            # Pause pacer between bursts so we don't emit prolonged silence
            try:
                self._pacer_running = False
                if self._pacer_task and not self._pacer_task.done():
                    self._pacer_task.cancel()
            except Exception:
                logger.debug("Failed to pause pacer on AgentAudioDone", call_id=self._call_id, exc_info=True)
            self._output_resample_state = None
            self._first_output_chunk_logged = False

        # If a hangup was requested and we just finished emitting the farewell audio, trigger hangup now.
        if self._farewell_waiting_for_audio_done and self._farewell_response_id is not None:
            self._farewell_waiting_for_audio_done = False
            self._farewell_response_id = None
            try:
                await self.on_event(
                    {
                        "type": "HangupReady",
                        "call_id": self._call_id,
                        "reason": "farewell_completed",
                        "had_audio": True,
                    }
                )
            except Exception:
                logger.error("Failed to emit HangupReady after output_audio.done", call_id=self._call_id, exc_info=True)

    async def _emit_transcript(self, text: str, *, is_final: bool):
        if not self.on_event or not self._call_id:
            return

        if text:
            self._transcript_buffer += text

        payload = {
            "type": "Transcript",
            "call_id": self._call_id,
            "text": text or self._transcript_buffer,
            "is_final": is_final,
        }
        try:
            await self.on_event(payload)
        except Exception:
            logger.error("Failed to emit transcript event", call_id=self._call_id, exc_info=True)

        if is_final:
            self._transcript_buffer = ""

    async def _track_conversation(self, role: str, text: str):
        """Track conversation turns for email tools (similar to Deepgram implementation)."""
        import time
        
        if not self._call_id or not text:
            return
        
        if not hasattr(self, '_session_store') or not self._session_store:
            logger.debug(
                "⚠️ Session store not available for conversation tracking",
                call_id=self._call_id,
                role=role
            )
            return
        
        try:
            session = await self._session_store.get_by_call_id(self._call_id)
            if session:
                # Add to conversation history
                session.conversation_history.append({
                    "role": role,  # "user" or "assistant"
                    "content": text,
                    "timestamp": time.time()
                })
                # Update session
                await self._session_store.upsert_call(session)
                logger.debug(
                    "✅ Tracked conversation message",
                    call_id=self._call_id,
                    role=role,
                    text_preview=text[:50] + "..." if len(text) > 50 else text
                )
                
                # Fallback detection: Warn if AI naturally ends conversation without using hangup_call
                if role == "assistant" and not self._hangup_after_response:
                    text_lower = text.lower()
                    ending_phrases = [
                        "goodbye", "bye", "see you", "talk to you later", "take care",
                        "have a great day", "have a good day", "have a nice day"
                    ]
                    if any(phrase in text_lower for phrase in ending_phrases):
                        logger.warning(
                            "⚠️  AI used farewell phrase without invoking hangup_call tool",
                            call_id=self._call_id,
                            text_preview=text[:100]
                        )
            else:
                logger.warning(
                    "⚠️ Session not found for conversation tracking",
                    call_id=self._call_id
                )
        except Exception as e:
            logger.error(
                "❌ Failed to track conversation",
                call_id=self._call_id,
                error=str(e),
                exc_info=True
            )

    async def _keepalive_loop(self):
        try:
            while self.websocket and self.websocket.state.name == "OPEN":
                await asyncio.sleep(_KEEPALIVE_INTERVAL_SEC)
                if not self.websocket or self.websocket.state.name != "OPEN":
                    break
                try:
                    # Use native WebSocket ping control frames instead of
                    # sending an application-level {"type":"ping"} event,
                    # which Realtime rejects with invalid_request_error.
                    async with self._send_lock:
                        if self.websocket and self.websocket.state.name == "OPEN":
                            await self.websocket.ping()
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.debug("OpenAI Realtime keepalive failed", call_id=self._call_id, exc_info=True)
                    break
        except asyncio.CancelledError:
            pass

    async def _reconnect_with_backoff(self):
        call_id = self._call_id
        if not call_id:
            return
        backoff = 0.5
        for attempt in range(1, 6):
            if self._closing or self._closed:
                return
            try:
                url = self._build_ws_url()
                use_beta = getattr(self.config, 'api_version', 'ga').lower() == 'beta'
                headers = [
                    ("Authorization", f"Bearer {self.config.api_key}"),
                ]
                if use_beta:
                    headers.append(("OpenAI-Beta", "realtime=v1"))
                if self.config.organization:
                    headers.append(("OpenAI-Organization", self.config.organization))
                if self.config.project_id:
                    headers.append(("OpenAI-Project", self.config.project_id))
                logger.info("Reconnecting to OpenAI Realtime", call_id=call_id, attempt=attempt)
                self.websocket = await websockets.connect(url, additional_headers=headers)
                # Reset minor state
                self._pending_response = False
                self._in_audio_burst = False
                self._first_output_chunk_logged = False
                # Send session update again and restart loops
                await self._send_session_update()
                self._log_session_assumptions()
                self._receive_task = asyncio.create_task(self._receive_loop())
                self._keepalive_task = asyncio.create_task(self._keepalive_loop())
                logger.info("OpenAI Realtime reconnected", call_id=call_id)
                return
            except Exception:
                logger.warning("OpenAI Realtime reconnect failed", call_id=call_id, attempt=attempt, exc_info=True)
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    return
                backoff = min(6.0, backoff * 2)
        logger.error("OpenAI Realtime reconnection exhausted attempts", call_id=call_id)

    # ------------------------------------------------------------------ #
    # Metrics and session metadata helpers ------------------------------ #
    # ------------------------------------------------------------------ #

    def _reset_output_meter(self) -> None:
        self._output_meter_start_ts = 0.0
        self._output_meter_last_log_ts = 0.0
        self._output_meter_bytes = 0
        self._output_rate_warned = False
        self._provider_reported_output_rate = None
        try:
            self._active_output_sample_rate_hz = float(self.config.output_sample_rate_hz)
        except Exception:
            self._active_output_sample_rate_hz = None

    def _log_session_assumptions(self) -> None:
        call_id = self._call_id
        if not call_id:
            return

        assumed_output = int(getattr(self.config, "output_sample_rate_hz", 0) or 0)
        try:
            _OPENAI_ASSUMED_OUTPUT_RATE.set(assumed_output)
        except Exception:
            pass

        info_payload = {
            "input_encoding": str(getattr(self.config, "input_encoding", "") or ""),
            "input_sample_rate_hz": str(getattr(self.config, "input_sample_rate_hz", "") or ""),
            "provider_input_encoding": str(getattr(self.config, "provider_input_encoding", "") or ""),
            "provider_input_sample_rate_hz": str(getattr(self.config, "provider_input_sample_rate_hz", "") or ""),
            "output_encoding": self._session_output_encoding,
            "output_sample_rate_hz": str(int(self._active_output_sample_rate_hz or getattr(self.config, "output_sample_rate_hz", "") or 0)),
            "target_encoding": str(getattr(self.config, "target_encoding", "") or ""),
            "target_sample_rate_hz": str(getattr(self.config, "target_sample_rate_hz", "") or ""),
        }

        try:
            _OPENAI_SESSION_AUDIO_INFO.info(info_payload)
        except Exception:
            pass

        try:
            logger.info(
                "OpenAI Realtime session assumptions",
                call_id=call_id,
                input_encoding=info_payload["input_encoding"],
                input_sample_rate_hz=info_payload["input_sample_rate_hz"],
                provider_input_sample_rate_hz=info_payload["provider_input_sample_rate_hz"],
                output_sample_rate_hz=info_payload["output_sample_rate_hz"],
                target_encoding=info_payload["target_encoding"],
                target_sample_rate_hz=info_payload["target_sample_rate_hz"],
            )
        except Exception:
            logger.debug("Failed to log OpenAI session assumptions", exc_info=True)

    def _handle_session_info_event(self, event: Dict[str, Any]) -> None:
        call_id = self._call_id
        if not call_id:
            return

        session_data = event.get("session") or {}
        output_meta = session_data.get("output_audio_format") or {}
        provider_rate = self._extract_sample_rate(output_meta)
        provider_encoding = self._extract_encoding(output_meta)

        if provider_rate:
            self._provider_reported_output_rate = provider_rate
            try:
                _OPENAI_PROVIDER_OUTPUT_RATE.set(provider_rate)
            except Exception:
                pass
            try:
                self._active_output_sample_rate_hz = float(provider_rate)
            except Exception:
                self._active_output_sample_rate_hz = provider_rate

        # Acknowledge μ-law only when provider confirms it
        enc_norm = (provider_encoding or "").lower()
        if enc_norm in ("g711_ulaw", "ulaw", "mulaw", "mu-law") and int(provider_rate or 0) == 8000:
            self._outfmt_acknowledged = True
            self._provider_output_format = "g711_ulaw"
            self._session_output_bytes_per_sample = 1
            self._session_output_encoding = "g711_ulaw"
        else:
            # Default to PCM16 assumptions until μ-law is confirmed
            self._outfmt_acknowledged = False
            self._provider_output_format = "pcm16"
            self._session_output_bytes_per_sample = 2
            self._session_output_encoding = "pcm16"

        info_payload = {
            "input_encoding": str(getattr(self.config, "input_encoding", "") or ""),
            "input_sample_rate_hz": str(getattr(self.config, "input_sample_rate_hz", "") or ""),
            "provider_input_encoding": str(getattr(self.config, "provider_input_encoding", "") or ""),
            "provider_input_sample_rate_hz": str(getattr(self.config, "provider_input_sample_rate_hz", "") or ""),
            "output_encoding": provider_encoding or self._session_output_encoding,
            "output_sample_rate_hz": str(provider_rate or self._active_output_sample_rate_hz or getattr(self.config, "output_sample_rate_hz", "") or ""),
            "target_encoding": str(getattr(self.config, "target_encoding", "") or ""),
            "target_sample_rate_hz": str(getattr(self.config, "target_sample_rate_hz", "") or ""),
        }

        try:
            _OPENAI_SESSION_AUDIO_INFO.info(info_payload)
        except Exception:
            pass

        try:
            logger.info(
                "OpenAI Realtime session acknowledged audio format",
                call_id=call_id,
                provider_output_encoding=provider_encoding,
                provider_output_sample_rate_hz=provider_rate,
                event_type=event.get("type"),
            )
        except Exception:
            logger.debug("Failed to log OpenAI session metadata", exc_info=True)

    def _update_output_meter(self, chunk_bytes: int) -> None:
        if not chunk_bytes or not self._call_id:
            return

        now = time.monotonic()
        if not self._output_meter_start_ts:
            self._output_meter_start_ts = now
            self._output_meter_last_log_ts = now

        self._output_meter_bytes += chunk_bytes
        elapsed = max(1e-6, now - self._output_meter_start_ts)
        bytes_per_sample = max(1, self._session_output_bytes_per_sample)
        measured_rate = (self._output_meter_bytes / bytes_per_sample) / elapsed

        # Guardrails: when target is μ-law, avoid "learning" sub-8kHz rates unless PCM is confirmed
        try:
            target_is_ulaw = str(getattr(self.config, "target_encoding", "") or "").lower() in (
                "ulaw",
                "mulaw",
                "g711_ulaw",
                "mu-law",
            )
        except Exception:
            target_is_ulaw = False
        confirmed_pcm = bool(self._outfmt_acknowledged and self._provider_output_format == "pcm16")

        try:
            _OPENAI_MEASURED_OUTPUT_RATE.set(measured_rate)
        except Exception:
            pass

        # Early drift correction (within ~250ms) so we don't wait a full second
        # before aligning the active source rate. This minimizes initial warble.
        try:
            assumed_now = float(self._active_output_sample_rate_hz or getattr(self.config, "output_sample_rate_hz", 0) or 0)
        except Exception:
            assumed_now = float(getattr(self.config, "output_sample_rate_hz", 0) or 0)
        # CRITICAL FIX: Never adjust sample rate based on measured_rate for streaming audio
        # OpenAI sends PCM16@24kHz at playback speed (real-time), not processing speed.
        # Measuring bytes/time gives playback rate (~1-3 kHz), NOT sample rate (24kHz).
        # Always keep the configured sample rate (24000 Hz) for accurate resampling.
        if elapsed >= 0.25 and assumed_now > 0:
            try:
                drift_now = abs(measured_rate - assumed_now) / assumed_now
            except Exception:
                drift_now = 0.0
            if drift_now > 0.10 and not self._output_rate_warned:
                self._output_rate_warned = True
                # Log the drift for diagnostics but DO NOT change _active_output_sample_rate_hz
                logger.debug(
                    "OpenAI output rate drift detected (expected for real-time streaming)",
                    call_id=self._call_id,
                    measured_rate_hz=round(measured_rate, 2),
                    configured_rate_hz=assumed_now,
                    note="Measured rate reflects playback speed, not sample rate. Ignoring.",
                )

        if now - self._output_meter_last_log_ts >= 1.0:
            self._output_meter_last_log_ts = now
            assumed = float(self._active_output_sample_rate_hz or getattr(self.config, "output_sample_rate_hz", 0) or 0)
            reported = self._provider_reported_output_rate
            log_payload = {
                "call_id": self._call_id,
                "assumed_output_sample_rate_hz": assumed or None,
                "provider_reported_sample_rate_hz": reported,
                "measured_output_sample_rate_hz": round(measured_rate, 2),
                "window_seconds": round(elapsed, 2),
                "bytes_window": self._output_meter_bytes,
            }
            try:
                logger.info(
                    "OpenAI Realtime output rate check",
                    **{k: v for k, v in log_payload.items() if v is not None},
                )
            except Exception:
                logger.debug("Failed to log OpenAI output rate check", exc_info=True)

            # CRITICAL FIX: Same as above - do not adjust rate based on measured_rate
            # Keep this section for logging only, never modify _active_output_sample_rate_hz
            if assumed > 0:
                drift = abs(measured_rate - assumed) / assumed
                # Log drift for diagnostics only - rate adjustment removed
                if drift > 0.10:
                    try:
                        logger.debug(
                            "OpenAI output rate drift info (streaming timing, not sample rate error)",
                            call_id=self._call_id,
                            measured_streaming_rate_hz=round(measured_rate, 2),
                            configured_sample_rate_hz=assumed,
                            provider_reported_rate_hz=reported,
                            drift_ratio=round(drift, 4),
                            note="This is expected for real-time streaming. Sample rate remains fixed.",
                        )
                    except Exception:
                        logger.debug("Failed to log OpenAI output rate info", exc_info=True)

            # Fallback trigger: if stream has been running >10s and measured rate remains <7.6–8 kHz, switch to PCM16@24k.
            # Guardrail: when the server has ACKed G.711 (μ-law/a-law) output, this heuristic is unreliable because
            # the "measured_rate" is an average over wall-clock time and naturally drops during user-speech/silence.
            # Switching formats mid-call can introduce audible artifacts, so only allow the fallback when we are not
            # currently in an ACKed G.711 mode.
            try:
                if not self._fallback_pcm24k_done:
                    if self._outfmt_acknowledged and self._provider_output_format in ("g711_ulaw", "g711_alaw"):
                        return
                    # Use pacer start when available, otherwise meter start as a conservative window
                    window_anchor = self._pacer_start_ts if self._pacer_start_ts > 0.0 else self._output_meter_start_ts
                    window = now - window_anchor if window_anchor > 0.0 else elapsed
                    if window >= 10.0 and measured_rate and measured_rate < 7600.0:
                        asyncio.create_task(self._switch_to_pcm24k_output())
                        self._fallback_pcm24k_done = True
            except Exception:
                logger.debug("PCM24k fallback evaluation error", exc_info=True)

    async def _ensure_pacer_started(self) -> None:
        if self._pacer_running:
            return
        if not self.on_event or not self._call_id:
            return
        self._pacer_running = True
        self._pacer_start_ts = time.monotonic()
        
        # CRITICAL: Clear OpenAI's input audio buffer when we start outputting
        # This prevents echo from being processed - any audio buffered before
        # our local gating kicked in will be discarded by OpenAI
        try:
            clear_buffer_payload = {
                "type": "input_audio_buffer.clear",
                "event_id": f"clear-echo-{uuid.uuid4()}",
            }
            await self._send_json(clear_buffer_payload)
            logger.debug("🔇 Cleared OpenAI input buffer for echo prevention", call_id=self._call_id)
        except Exception:
            logger.debug("Failed to clear input buffer", call_id=self._call_id, exc_info=True)
        
        try:
            if self._pacer_task and not self._pacer_task.done():
                self._pacer_task.cancel()
        except Exception:
            pass
        self._pacer_task = asyncio.create_task(self._pacer_loop())

    async def _pacer_loop(self) -> None:
        call_id = self._call_id
        if not call_id or not self.on_event:
            self._pacer_running = False
            return
        # Determine 20ms chunk sizing based on target encoding/sample-rate
        chunk_bytes, silence_factory = self._pacer_params()
        warmup_bytes = int(max(0, self._egress_pacer_warmup_ms) / 20) * chunk_bytes
        # Warm-up buffer
        try:
            while self.websocket and self.websocket.state.name == "OPEN" and self._pacer_running:
                async with self._pacer_lock:
                    buf_len = len(self._outbuf)
                if buf_len >= warmup_bytes or not self._egress_pacer_enabled:
                    break
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.debug("Pacer warm-up error", call_id=call_id, exc_info=True)

        # Emit loop at 20 ms cadence
        try:
            while self.websocket and self.websocket.state.name == "OPEN" and self._pacer_running:
                chunk = b""
                async with self._pacer_lock:
                    if len(self._outbuf) >= chunk_bytes:
                        chunk = bytes(self._outbuf[:chunk_bytes])
                        del self._outbuf[:chunk_bytes]
                if not chunk:
                    # Underrun: emit silence to maintain cadence
                    self._pacer_underruns += 1
                    chunk = silence_factory(chunk_bytes)

                # Track whether we're emitting real audio (not silence) for echo gating
                # NOTE: We do NOT set _in_audio_burst=False during silence here!
                # The burst flag must remain True until response.audio.done fires,
                # otherwise AgentAudioDone won't be emitted and the stream queue
                # won't be cleared for the next response.
                has_buffered_audio = len(self._outbuf) > 0
                is_first_real_chunk = chunk and self._pacer_underruns == 0
                
                if has_buffered_audio or is_first_real_chunk:
                    self._in_audio_burst = True
                    if not self._first_output_chunk_logged:
                        try:
                            logger.info(
                                "OpenAI Realtime first paced audio chunk",
                                call_id=call_id,
                                bytes=len(chunk),
                                target_encoding=self.config.target_encoding,
                            )
                        except Exception:
                            pass
                        self._first_output_chunk_logged = True
                # Don't clear _in_audio_burst during silence - let response.audio.done handle it
                
                try:
                    await self.on_event(
                        {
                            "type": "AgentAudio",
                            "data": chunk,
                            "streaming_chunk": True,
                            "call_id": call_id,
                            "encoding": (self.config.target_encoding or "slin16"),
                            "sample_rate": self.config.target_sample_rate_hz,
                        }
                    )
                except Exception:
                    logger.error("Failed to emit paced AgentAudio", call_id=call_id, exc_info=True)
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.debug("Pacer loop error", call_id=call_id, exc_info=True)
        finally:
            self._pacer_running = False
            self._in_audio_burst = False  # Always clear when pacer stops

    def _pacer_params(self) -> (int, Any):
        # Compute chunk size for 20 ms frames and a silence factory matching target encoding
        enc = (self.config.target_encoding or "ulaw").lower()
        rate = int(self.config.target_sample_rate_hz or 8000)
        if enc in ("ulaw", "mulaw", "g711_ulaw", "mu-law"):
            bytes_per_sample = 1
            chunk_bytes = int(rate / 50) * bytes_per_sample
            def silence(n: int) -> bytes:
                return bytes([0xFF]) * max(0, n)
            return chunk_bytes, silence
        # PCM16 path (e.g., slin16)
        bytes_per_sample = 2
        chunk_bytes = int(rate / 50) * bytes_per_sample
        def silence(n: int) -> bytes:
            return b"\x00" * max(0, n)
        return chunk_bytes, silence

    async def _switch_to_pcm24k_output(self) -> None:
        if not self.websocket or self.websocket.state.name != "OPEN":
            return
        call_id = self._call_id
        try:
            logger.warning(
                "Switching OpenAI output to PCM16@24k due to sustained low measured rate",
                call_id=call_id,
            )
        except Exception:
            pass
        if self._is_ga:
            pcm_session = {"audio": {"output": {"format": {"type": "audio/pcm", "rate": 24000}}}}
        else:
            pcm_session = {"output_audio_format": "pcm16"}
        payload: Dict[str, Any] = {
            "type": "session.update",
            "event_id": f"sess-{uuid.uuid4()}",
            "session": self._ga_session_type(pcm_session),
        }
        try:
            await self._send_json(payload)
            self._provider_output_format = "pcm16"
            self._session_output_bytes_per_sample = 2
            try:
                self._active_output_sample_rate_hz = float(24000)
            except Exception:
                self._active_output_sample_rate_hz = 24000.0
            self._reset_output_meter()
        except Exception:
            logger.debug("Failed to switch OpenAI session to PCM16@24k", call_id=call_id, exc_info=True)

    @staticmethod
    def _extract_sample_rate(fmt: Any) -> Optional[int]:
        if isinstance(fmt, str):
            # Some previews may send "pcm16@24000"
            if "@" in fmt:
                try:
                    return int(float(fmt.split("@", 1)[1]))
                except (IndexError, ValueError):
                    return None
            return None
        if not isinstance(fmt, dict):
            return None
        for key in ("sample_rate", "sample_rate_hz", "rate"):
            value = fmt.get(key)
            if value is None:
                continue
            try:
                return int(float(value))
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _extract_encoding(fmt: Any) -> Optional[str]:
        if isinstance(fmt, str):
            if "@" in fmt:
                return fmt.split("@", 1)[0].strip().lower()
            return fmt.lower()
        if not isinstance(fmt, dict):
            return None
        for key in ("encoding", "format", "type"):
            value = fmt.get(key)
            if isinstance(value, str) and value.strip():
                return value.lower()
        return None

    def _clear_metrics(self, call_id: Optional[str]) -> None:
        self._reset_output_meter()
