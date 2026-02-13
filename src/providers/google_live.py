"""
Google Gemini Live API provider implementation.

This module integrates Google's Gemini Live API (bidirectional streaming) into the
Asterisk AI Voice Agent. Audio from AudioSocket is resampled to PCM16 @ 16 kHz,
streamed to Gemini Live API, and PCM16 output is resampled to the configured
downstream AudioSocket format (µ-law or PCM16 8 kHz).

Key features:
- Real-time bidirectional voice streaming
- Native audio processing (no separate STT/TTS)
- Built-in Voice Activity Detection (VAD)
- Barge-in support
- Function calling / tool use
- Session management for long conversations
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import math
import time
import struct
import audioop
import re
from typing import Any, Dict, Optional, List, Tuple
from collections import deque

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from structlog import get_logger
from prometheus_client import Gauge, Counter

from .base import AIProviderInterface, ProviderCapabilities
from ..audio import (
    convert_pcm16le_to_target_format,
    mulaw_to_pcm16le,
    resample_audio,
)
from ..config import GoogleProviderConfig
from src.tools.telephony.hangup_policy import normalize_hangup_policy

# Tool calling support
from src.tools.registry import tool_registry
from src.tools.adapters.google import GoogleToolAdapter

logger = get_logger(__name__)


def _merge_transcription_fragment(buffer: str, fragment: str, last_fragment: str) -> Tuple[str, str]:
    """
    Merge a transcription fragment into an existing buffer.

    Some providers repeat the same fragment or send cumulative text. This helper keeps the
    resulting transcript stable (prevents duplicated phrases like "Goodbye!Goodbye!").
    """
    fragment = str(fragment or "")
    if not fragment:
        return buffer, last_fragment

    # Drop exact duplicate fragment repeats.
    if fragment == last_fragment:
        return buffer, last_fragment

    # If the provider sends cumulative text (new fragment contains the full buffer), replace.
    if buffer and fragment.startswith(buffer):
        return fragment, fragment

    # If we somehow receive a fragment that is already fully present as the suffix, ignore.
    if buffer and buffer.endswith(fragment):
        return buffer, fragment

    return f"{buffer}{fragment}", fragment

# Constants
_GEMINI_INPUT_RATE = 16000  # Gemini requires 16kHz input
_GEMINI_OUTPUT_RATE = 24000  # Gemini outputs 24kHz audio
_COMMIT_INTERVAL_SEC = 0.02  # 20ms chunks (320 bytes at 16kHz)
_KEEPALIVE_INTERVAL_SEC = 15.0

# Metrics
_GOOGLE_LIVE_SESSIONS = Gauge(
    "ai_agent_google_live_active_sessions",
    "Number of active Google Live API sessions",
)
_GOOGLE_LIVE_AUDIO_SENT = Counter(
    "ai_agent_google_live_audio_bytes_sent",
    "Total audio bytes sent to Google Live API",
)
_GOOGLE_LIVE_AUDIO_RECEIVED = Counter(
    "ai_agent_google_live_audio_bytes_received",
    "Total audio bytes received from Google Live API",
)


class GoogleLiveProvider(AIProviderInterface):
    """
    Google Gemini Live API provider using bidirectional WebSocket streaming.

    Lifecycle:
    1. start_session(call_id) -> establishes WebSocket, sends setup message
    2. send_audio(bytes) -> converts AudioSocket frames to PCM16 16kHz, streams to Gemini
    3. Receive server responses: audio, text transcription, tool calls
    4. stop_session() -> closes WebSocket and cancels background tasks

    Audio flow:
    - Input: 8kHz µ-law → 16kHz PCM16 → Gemini Live API
    - Output: 24kHz PCM16 from Gemini → 8kHz µ-law/PCM16 → AudioSocket
    """
    DEFAULT_LIVE_MODEL = "gemini-2.5-flash-native-audio-latest"
    LEGACY_LIVE_MODEL_MAP = {
        # Older preview aliases that are no longer preferred.
        "gemini-live-2.5-flash-preview": DEFAULT_LIVE_MODEL,
    }

    def __init__(
        self,
        config: GoogleProviderConfig,
        on_event,
        gating_manager=None,
        hangup_policy: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(on_event)
        self.config = config
        self._hangup_policy = normalize_hangup_policy(hangup_policy or {})
        # Google Live only: allow disabling marker-based hangup heuristics to isolate provider disconnects.
        self._hangup_markers_enabled: bool = bool(getattr(config, "hangup_markers_enabled", True))
        self.websocket: Optional[ClientConnection] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()
        self._gating_manager = gating_manager

        self._call_id: Optional[str] = None
        self._session_id: Optional[str] = None
        self._setup_complete: bool = False
        self._greeting_completed: bool = False
        self._in_audio_burst: bool = False
        self._setup_ack_event: Optional[asyncio.Event] = None  # ACK gate like Deepgram
        self._hangup_after_response: bool = False  # Flag to trigger hangup after next response
        self._farewell_in_progress: bool = False  # Track if farewell is being spoken
        self._hangup_fallback_armed: bool = False
        self._hangup_fallback_emitted: bool = False
        self._hangup_fallback_task: Optional[asyncio.Task] = None
        self._hangup_fallback_armed_at: Optional[float] = None
        self._hangup_fallback_audio_started: bool = False
        self._hangup_fallback_turn_complete_seen: bool = False
        self._hangup_fallback_wait_logged: bool = False
        self._last_audio_out_monotonic: Optional[float] = None
        self._user_end_intent: Optional[str] = None
        self._assistant_farewell_intent: Optional[str] = None
        self._turn_has_assistant_output: bool = False
        self._hangup_ready_emitted: bool = False
        # If the model calls hangup_call but does not produce any audio shortly after, prompt it
        # to speak the farewell message (keeps the goodbye in the model's voice, avoids engine-side canned audio).
        self._force_farewell_task: Optional[asyncio.Task] = None
        self._force_farewell_text: str = ""
        self._force_farewell_sent: bool = False
        self._post_hangup_output_detected: bool = False
        # Conversation state
        self._conversation_history: List[Dict[str, Any]] = []
        
        # Initialize tool adapter early (before start_session) so engine can inject context
        # This ensures _session_store, _ari_client, etc. are available for tool execution
        from src.tools.registry import tool_registry
        self._tool_adapter = GoogleToolAdapter(tool_registry)
        self._allowed_tools: Optional[List[str]] = None
        
        # Transcription buffering - hold latest partial until turnComplete
        self._input_transcription_buffer: str = ""
        self._output_transcription_buffer: str = ""
        self._model_text_buffer: str = ""
        self._last_final_user_text: str = ""
        self._last_final_assistant_text: str = ""
        self._last_input_transcription_fragment: str = ""
        self._last_output_transcription_fragment: str = ""
        
        # Turn latency tracking (Milestone 21 - Call History)
        self._turn_start_time: Optional[float] = None
        self._turn_first_audio_received: bool = False
        
        # Golden Baseline: Simple input buffer for 20ms chunking
        self._input_buffer = bytearray()
        
        # Metrics tracking
        self._session_start_time: Optional[float] = None
        # Tool response sizing: keep Google toolResponse payloads small to avoid provider errors.
        self._tool_response_max_bytes: int = 8000
        self._session_gauge_incremented: bool = False
        self._ws_unavailable_logged: bool = False
        self._ws_send_close_logged: bool = False
        self._closing: bool = False
        self._closed: bool = False
        # Auto-reconnect on 1008 (Google server-side policy violation bug with native audio + tools).
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reconnect_context: Optional[Dict[str, Any]] = None
        self._reconnect_count: int = 0
        self._max_reconnect_attempts: int = 3
        # Diagnostics: keep a small ring buffer of outbound message summaries
        # to help explain server-initiated closes (e.g., 1008 policy violations).
        self._outbound_summaries: deque[Dict[str, Any]] = deque(maxlen=12)
        # WebSocket keepalive telemetry (ping/pong frames are not part of the JSON protocol payload).
        # We track them explicitly so 1008/1011 closes can be correlated to keepalive timing.
        self._ws_ping_seq: int = 0
        self._ws_pong_seq: int = 0
        self._last_ws_ping_monotonic: Optional[float] = None
        self._last_ws_pong_monotonic: Optional[float] = None
        self._last_ws_ping_rtt_ms: Optional[float] = None
        self._last_ws_ping_error: Optional[str] = None
        # When `realtimeInput` is continuously streaming, WebSocket pings are redundant (and may trigger 1008
        # policy closes on some accounts). Track last `realtimeInput` send time so keepalive only fires on idle.
        self._last_realtime_input_sent_monotonic: Optional[float] = None

    def _summarize_outbound(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a compact, PII-safe summary of an outbound message.

        Never include raw text content or tool response payloads; only include
        message type, key presence, and basic sizing/shape.
        """
        try:
            top_keys = list(message.keys()) if isinstance(message, dict) else []
            msg_type = top_keys[0] if top_keys else "unknown"
            summary: Dict[str, Any] = {"type": msg_type, "keys": top_keys}

            if msg_type == "setup":
                setup = message.get("setup") or {}
                summary["setup_keys"] = list((setup or {}).keys())[:24]
                summary["model"] = (setup or {}).get("model")
                summary["has_tools"] = bool((setup or {}).get("tools"))
                summary["has_input_transcription"] = "inputAudioTranscription" in (setup or {})
                summary["has_output_transcription"] = "outputAudioTranscription" in (setup or {})
            elif msg_type == "realtimeInput":
                rt = message.get("realtimeInput") or {}
                audio = (rt or {}).get("audio") or {}
                # data is base64; include only length.
                summary["mimeType"] = audio.get("mimeType")
                data = audio.get("data")
                if isinstance(data, str):
                    summary["data_len"] = len(data)
            elif msg_type == "clientContent":
                cc = message.get("clientContent") or {}
                summary["turnComplete"] = bool(cc.get("turnComplete"))
                turns = cc.get("turns") or []
                if isinstance(turns, list) and turns:
                    first = turns[0] if isinstance(turns[0], dict) else {}
                    summary["role"] = first.get("role")
                    # Only include the number of parts; do not include text.
                    parts = first.get("parts") or []
                    if isinstance(parts, list):
                        summary["parts"] = len(parts)
            elif msg_type == "toolResponse":
                tr = message.get("toolResponse") or {}
                fr = tr.get("functionResponses") or []
                summary["functionResponses"] = len(fr) if isinstance(fr, list) else 0
                if isinstance(fr, list) and fr:
                    names = []
                    for item in fr[:4]:
                        if isinstance(item, dict) and item.get("name"):
                            names.append(str(item.get("name")))
                    if names:
                        summary["functions"] = names
            return summary
        except Exception:
            return {"type": "unknown"}

    def _mark_ws_disconnected(self) -> None:
        self._setup_complete = False
        self.websocket = None
        if self._session_gauge_incremented:
            try:
                _GOOGLE_LIVE_SESSIONS.dec()
            except Exception:
                pass
            self._session_gauge_incremented = False
        # Keepalive telemetry should not leak across sessions.
        self._ws_ping_seq = 0
        self._ws_pong_seq = 0
        self._last_ws_ping_monotonic = None
        self._last_ws_pong_monotonic = None
        self._last_ws_ping_rtt_ms = None
        self._last_ws_ping_error = None

    async def _emit_provider_disconnected(self, *, code: Optional[int], reason: str) -> None:
        # IMPORTANT: Tell the engine the provider is gone so we don't leave dead air.
        # The engine can hang up or route to a live agent fallback.
        if not self.on_event or not self._call_id:
            return
        await self.on_event(
            {
                "type": "ProviderDisconnected",
                "call_id": self._call_id,
                "provider": "google_live",
                "code": code,
                "reason": reason,
            }
        )

    async def _flush_pending_transcriptions_on_disconnect(self, *, code: Optional[int], reason: str) -> None:
        """
        Best-effort: persist any pending transcription buffers when the websocket closes mid-turn.

        Google Live may close with 1008/1011 before emitting `turnComplete`, which means incremental
        fragments were logged but never committed to call history. This keeps the tail visible for RCA.
        """
        if not self._call_id:
            return
        # Prefer outputTranscription; fall back to modelTurn.text buffer if present.
        pending_user = (self._input_transcription_buffer or "").strip()
        pending_assistant = (self._output_transcription_buffer or self._model_text_buffer or "").strip()

        try:
            if pending_user and pending_user != (self._last_final_user_text or "").strip():
                await self._track_conversation_message("user", f"(partial) {pending_user}")
                self._last_final_user_text = pending_user
                logger.info(
                    "Google Live flushed pending user transcription on disconnect",
                    call_id=self._call_id,
                    code=code,
                    reason=(reason or "")[:120],
                    text=pending_user[:150],
                )
        except Exception:
            logger.debug("Failed flushing pending user transcription on disconnect", call_id=self._call_id, exc_info=True)

        try:
            if pending_assistant and pending_assistant != (self._last_final_assistant_text or "").strip():
                await self._track_conversation_message("assistant", f"(partial) {pending_assistant}")
                self._last_final_assistant_text = pending_assistant
                logger.info(
                    "Google Live flushed pending assistant transcription on disconnect",
                    call_id=self._call_id,
                    code=code,
                    reason=(reason or "")[:120],
                    text=pending_assistant[:150],
                )
        except Exception:
            logger.debug(
                "Failed flushing pending assistant transcription on disconnect", call_id=self._call_id, exc_info=True
            )

        # Clear buffers so any subsequent teardown doesn't double-log.
        self._input_transcription_buffer = ""
        self._last_input_transcription_fragment = ""
        self._output_transcription_buffer = ""
        self._last_output_transcription_fragment = ""
        self._model_text_buffer = ""

    @staticmethod
    def _norm_text(value: str) -> str:
        return re.sub(r"\s+", " ", (value or "").strip().lower())

    def _detect_user_end_intent(self, text: str) -> Optional[str]:
        if not self._hangup_markers_enabled:
            return None
        t = self._norm_text(text)
        if not t:
            return None
        markers = (self._hangup_policy.get("markers") or {}).get("end_call", [])
        for m in markers:
            if m in t:
                return m
        return None

    def _detect_assistant_farewell(self, text: str) -> Optional[str]:
        if not self._hangup_markers_enabled:
            return None
        t = self._norm_text(text)
        if not t:
            return None
        markers = (self._hangup_policy.get("markers") or {}).get("assistant_farewell", [])
        for m in markers:
            if m in t:
                return m
        return None

    def _should_wait_for_turn_complete_before_fallback(self, now: float, armed_at: float) -> bool:
        if self._hangup_fallback_turn_complete_seen:
            return False
        try:
            timeout_sec = float(getattr(self.config, "hangup_fallback_turn_complete_timeout_sec", 2.5) or 0.0)
        except Exception:
            timeout_sec = 0.0
        if timeout_sec <= 0.0:
            return False
        return (now - armed_at) < timeout_sec

    async def _flush_pending_user_transcription(self, *, reason: str) -> bool:
        pending = (self._input_transcription_buffer or "").strip()
        if not pending:
            return False
        if pending == (self._last_final_user_text or "").strip():
            self._input_transcription_buffer = ""
            self._last_input_transcription_fragment = ""
            return False
        await self._track_conversation_message("user", pending)
        self._last_final_user_text = pending
        self._input_transcription_buffer = ""
        self._last_input_transcription_fragment = ""
        logger.info(
            "Google Live flushed pending user transcription before fallback hangup",
            call_id=self._call_id,
            reason=reason,
            text=pending[:150],
        )
        return True

    async def _ensure_hangup_fallback_watchdog(self) -> None:
        if self._hangup_fallback_task and not self._hangup_fallback_task.done():
            return
        if not self._call_id:
            return
        self._hangup_fallback_task = asyncio.create_task(
            self._hangup_fallback_watchdog_loop(),
            name=f"google-live-hangup-fallback-{self._call_id}",
        )

    async def _hangup_fallback_watchdog_loop(self) -> None:
        """
        If Gemini speaks a farewell but never emits a toolCall, or if turnComplete never arrives,
        the engine may not see AgentAudioDone(streaming_done=True) and won't check cleanup_after_tts.

        This watchdog observes output audio idle time and, when appropriate, emits AgentAudioDone
        and HangupReady to reliably tear down the call.
        """
        call_id = self._call_id
        # Keep conservative defaults; engine still applies farewell_hangup_delay_sec before ARI hangup.
        idle_sec = float(getattr(self.config, "hangup_fallback_audio_idle_sec", 1.25) or 1.25)
        min_armed_sec = float(getattr(self.config, "hangup_fallback_min_armed_sec", 0.8) or 0.8)
        turn_complete_timeout_sec = float(
            getattr(self.config, "hangup_fallback_turn_complete_timeout_sec", 2.5) or 2.5
        )
        # If the model called hangup_call but never produced any farewell audio, we still must end the call.
        # This commonly happens when the model emits toolCalls but does not follow up with an assistant turn.
        no_audio_timeout_sec = float(getattr(self.config, "hangup_fallback_no_audio_timeout_sec", 4.0) or 4.0)

        try:
            while self._call_id == call_id and not self._hangup_fallback_emitted:
                if not self._hangup_fallback_armed:
                    await asyncio.sleep(0.2)
                    continue
                if self._hangup_ready_emitted:
                    self._hangup_fallback_emitted = True
                    return

                now = time.monotonic()
                last_audio = self._last_audio_out_monotonic
                armed_at = self._hangup_fallback_armed_at or now
                waiting_for_turn_complete = self._should_wait_for_turn_complete_before_fallback(now, armed_at)
                if (now - armed_at) < min_armed_sec:
                    await asyncio.sleep(0.2)
                    continue
                if not self._hangup_fallback_audio_started and (now - armed_at) >= no_audio_timeout_sec:
                    if waiting_for_turn_complete:
                        if not self._hangup_fallback_wait_logged:
                            logger.info(
                                "Hangup fallback waiting for turnComplete before no-audio fallback",
                                call_id=call_id,
                                wait_timeout_sec=turn_complete_timeout_sec,
                                elapsed_sec=round((now - armed_at), 3),
                            )
                            self._hangup_fallback_wait_logged = True
                        await asyncio.sleep(0.2)
                        continue
                    await self._flush_pending_user_transcription(reason="fallback_no_audio")
                    if self.on_event:
                        await self.on_event(
                            {
                                "type": "HangupReady",
                                "call_id": call_id,
                                "reason": "fallback_no_audio",
                                "had_audio": False,
                                "turn_complete_seen": bool(self._hangup_fallback_turn_complete_seen),
                            }
                        )
                    self._hangup_ready_emitted = True
                    self._hangup_fallback_emitted = True
                    logger.info(
                        "🔚 Hangup fallback watchdog emitted HangupReady (no assistant audio)",
                        call_id=call_id,
                        no_audio_timeout_sec=no_audio_timeout_sec,
                        user_end_intent=self._user_end_intent,
                        assistant_farewell_intent=self._assistant_farewell_intent,
                    )
                    return
                if not self._hangup_fallback_audio_started or last_audio is None:
                    await asyncio.sleep(0.2)
                    continue
                if (now - last_audio) < idle_sec:
                    await asyncio.sleep(0.2)
                    continue

                if waiting_for_turn_complete:
                    if not self._hangup_fallback_wait_logged:
                        logger.info(
                            "Hangup fallback waiting for turnComplete before audio-idle fallback",
                            call_id=call_id,
                            wait_timeout_sec=turn_complete_timeout_sec,
                            elapsed_sec=round((now - armed_at), 3),
                        )
                        self._hangup_fallback_wait_logged = True
                    await asyncio.sleep(0.2)
                    continue

                # Audio has been idle long enough; treat as end-of-response.
                had_audio = bool(self._hangup_fallback_audio_started)
                if self._in_audio_burst:
                    self._in_audio_burst = False
                    if self.on_event:
                        await self.on_event(
                            {
                                "type": "AgentAudioDone",
                                "call_id": call_id,
                                "streaming_done": True,
                            }
                        )

                await self._flush_pending_user_transcription(reason="fallback_audio_idle")
                if self.on_event:
                    await self.on_event(
                        {
                            "type": "HangupReady",
                            "call_id": call_id,
                            "reason": "fallback_audio_idle",
                            "had_audio": had_audio,
                            "turn_complete_seen": bool(self._hangup_fallback_turn_complete_seen),
                        }
                    )

                self._hangup_ready_emitted = True
                self._hangup_fallback_emitted = True
                logger.info(
                    "🔚 Hangup fallback watchdog emitted HangupReady",
                    call_id=call_id,
                    idle_sec=idle_sec,
                    user_end_intent=self._user_end_intent,
                    assistant_farewell_intent=self._assistant_farewell_intent,
                )
                return
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.debug(
                "Hangup fallback watchdog failed",
                call_id=call_id,
                error=str(e),
                exc_info=True,
            )

    @staticmethod
    def _normalize_response_modalities(value: Any) -> List[str]:
        """
        Live API expects `generationConfig.responseModalities` as a list of modality strings.

        Our config historically stores this as a string ("audio", "text", "audio_text").
        Normalize to the documented list form, using the canonical "AUDIO"/"TEXT" tokens.
        """
        if value is None:
            return ["AUDIO"]

        def normalize_token(token: str) -> Optional[str]:
            token_norm = (token or "").strip().upper()
            if not token_norm:
                return None
            if token_norm in ("AUDIO", "AUDIO_ONLY"):
                return "AUDIO"
            if token_norm in ("TEXT", "TEXT_ONLY"):
                return "TEXT"
            if token_norm in ("AUDIO_TEXT", "TEXT_AUDIO", "AUDIO+TEXT", "TEXT+AUDIO", "AUDIO,TEXT", "TEXT,AUDIO"):
                # Caller will expand this at the top-level.
                return token_norm
            return token_norm

        tokens: List[str] = []
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    t = normalize_token(item)
                    if t:
                        tokens.append(t)
        elif isinstance(value, str):
            # Support compact forms: "audio_text", "audio,text", "audio+text".
            raw = value.strip()
            if any(sep in raw for sep in (",", "+", " ")):
                parts = [p for p in re.split(r"[,+\\s]+", raw) if p]
                for p in parts:
                    t = normalize_token(p)
                    if t:
                        tokens.append(t)
            else:
                t = normalize_token(raw)
                if t:
                    tokens.append(t)
        else:
            tokens = [str(value).strip().upper()]

        modalities: List[str] = []
        for t in tokens:
            if t in ("AUDIO_TEXT", "TEXT_AUDIO", "AUDIO+TEXT", "TEXT+AUDIO", "AUDIO,TEXT", "TEXT,AUDIO"):
                for expanded in ("AUDIO", "TEXT"):
                    if expanded not in modalities:
                        modalities.append(expanded)
                continue
            if t in ("AUDIO", "TEXT") and t not in modalities:
                modalities.append(t)

        return modalities or ["AUDIO"]

    def _ws_is_open(self) -> bool:
        ws = self.websocket
        if not ws:
            return False
        try:
            state = getattr(ws, "state", None)
            if state is not None and getattr(state, "name", None) is not None:
                return state.name == "OPEN"
        except Exception:
            pass
        return bool(getattr(ws, "open", False))

    @staticmethod
    def get_capabilities() -> Optional[ProviderCapabilities]:
        """Return capabilities of Google Live provider for transport orchestration."""
        return ProviderCapabilities(
            # Audio format capabilities
            input_encodings=["ulaw", "pcm16"],  # μ-law or PCM16
            input_sample_rates_hz=[8000, 16000],  # Telephony or wideband
            output_encodings=["ulaw", "pcm16"],  # Output resampled to telephony
            output_sample_rates_hz=[8000, 16000, 24000],  # Gemini native is 24kHz
            preferred_chunk_ms=20,  # 20ms chunks for smooth streaming
            can_negotiate=True,  # Can adapt to different formats
            # Provider type and audio processing capabilities
            is_full_agent=True,  # Full bidirectional agent (not pipeline component)
            has_native_vad=True,  # Gemini Live has built-in Voice Activity Detection
            has_native_barge_in=True,  # Handles interruptions automatically
            requires_continuous_audio=True,  # Needs continuous audio stream for VAD
        )
    
    @property
    def supported_codecs(self) -> List[str]:
        """Return list of supported audio codecs (μ-law for telephony)."""
        return ["ulaw"]

    def is_ready(self) -> bool:
        """Check if provider is properly configured with required API key."""
        api_key = getattr(self.config, 'api_key', None) or ""
        return bool(api_key and str(api_key).strip())

    async def start_session(
        self,
        call_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Start a Google Gemini Live API session.

        Args:
            call_id: Unique identifier for the call
            context: Optional context including system prompt, tools, etc.
        """
        self._call_id = call_id
        self._closing = False
        self._closed = False
        self._session_start_time = time.time()
        self._reconnect_context = context
        self._reconnect_count = 0
        self._conversation_history = []
        self._setup_complete = False
        self._greeting_completed = False
        self._ws_unavailable_logged = False
        self._ws_send_close_logged = False
        self._hangup_ready_emitted = False
        self._input_transcription_buffer = ""
        self._output_transcription_buffer = ""
        self._model_text_buffer = ""
        self._last_final_user_text = ""
        self._last_final_assistant_text = ""
        self._last_input_transcription_fragment = ""
        self._last_output_transcription_fragment = ""
        # Per-call tool allowlist (contexts are the source of truth).
        # Missing/None is treated as [] for safety.
        if context and "tools" in context:
            self._allowed_tools = list(context.get("tools") or [])
        else:
            self._allowed_tools = []

        effective_model = self._normalize_model_name(self.config.llm_model)
        logger.info(
            "Starting Google Live session",
            call_id=call_id,
            configured_model=self.config.llm_model,
            effective_model=effective_model,
        )
        if not self._hangup_markers_enabled:
            logger.warning(
                "Google Live marker-based hangup heuristics are disabled",
                call_id=call_id,
            )

        # Build WebSocket URL with API key
        api_key = self.config.api_key or ""
        
        # Debug: Check API key
        if not api_key:
            logger.error(
                "GOOGLE_API_KEY not found! Cannot connect to Google Live API.",
                call_id=call_id,
            )
            raise ValueError("GOOGLE_API_KEY is required for Google Live provider")
        
        api_key_preview = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "<too_short>"
        endpoint = (self.config.websocket_endpoint or "").strip()
        if not endpoint:
            # Fallback to historical constant if config not populated
            endpoint = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"

        logger.debug(
            "Connecting to Google Live API",
            call_id=call_id,
            endpoint=endpoint,
            api_key_preview=api_key_preview,
        )
        
        ws_url = f"{endpoint}?key={api_key}"

        try:
            # Establish WebSocket connection
            self.websocket = await websockets.connect(
                ws_url,
                subprotocols=["gemini-live"],
                max_size=10 * 1024 * 1024,  # 10MB max message size
                # Disable library-level ping frames. We implement our own keepalive behavior
                # in `_keepalive_loop()` and have seen 1008 closes correlated with ping activity.
                ping_interval=None,
                ping_timeout=None,
            )

            _GOOGLE_LIVE_SESSIONS.inc()
            self._session_gauge_incremented = True

            logger.info(
                "Google Live WebSocket connected",
                call_id=call_id,
            )

            # Create ACK event BEFORE sending setup (like Deepgram pattern)
            self._setup_ack_event = asyncio.Event()

            # Start receive loop FIRST (so it can catch setupComplete)
            self._receive_task = asyncio.create_task(
                self._receive_loop(),
                name=f"google-live-receive-{call_id}",
            )

            # Send setup message to configure session
            await self._send_setup(context)

            # Wait for setup acknowledgment
            logger.debug("Waiting for Google Live setupComplete...", call_id=self._call_id)
            await asyncio.wait_for(self._setup_ack_event.wait(), timeout=5.0)
            logger.info("Google Live setup complete (ACK received)", call_id=self._call_id)

            # Note: Greeting is sent by _handle_setup_complete() to avoid race condition
            # Do NOT send greeting here as it would duplicate the greeting

            self._keepalive_task = asyncio.create_task(
                self._keepalive_loop(),
                name=f"google-live-keepalive-{call_id}",
            )

            logger.info(
                "Google Live session started",
                call_id=call_id,
            )

        except Exception as e:
            logger.error(
                "Failed to start Google Live session",
                call_id=call_id,
                error=str(e),
                exc_info=True,
            )
            await self.stop_session()
            raise

    @staticmethod
    def _normalize_model_name(model: Optional[str]) -> str:
        """
        Strip the ``models/`` prefix and apply legacy alias remapping.

        Google Live requires a Live-capable native-audio model. If a non-live
        model name is provided, fall back to the provider default so calls
        don't fail due to a UI/wizard mismatch.
        """
        m = (model or "").strip()
        if m.startswith("models/"):
            m = m[7:]

        if not m:
            logger.warning(
                "No Google Live model configured; using default",
                fallback_model=GoogleLiveProvider.DEFAULT_LIVE_MODEL,
            )
            return GoogleLiveProvider.DEFAULT_LIVE_MODEL

        if m in GoogleLiveProvider.LEGACY_LIVE_MODEL_MAP:
            replacement = GoogleLiveProvider.LEGACY_LIVE_MODEL_MAP[m]
            logger.warning(
                "Google Live model alias/deprecated value configured; remapping",
                configured_model=m,
                replacement_model=replacement,
            )
            return replacement

        m_l = m.lower()
        if ("native-audio" not in m_l) and ("live" not in m_l):
            logger.warning(
                "Google Live model name does not look like a Live native-audio model; using default",
                configured_model=m,
                fallback_model=GoogleLiveProvider.DEFAULT_LIVE_MODEL,
            )
            return GoogleLiveProvider.DEFAULT_LIVE_MODEL
        return m

    async def _send_setup(self, context: Optional[Dict[str, Any]], system_prompt_override: Optional[str] = None) -> None:
        """Send session setup message to Gemini Live API."""
        # Use instructions from config (like OpenAI Realtime pattern)
        system_prompt = system_prompt_override or self.config.instructions
        
        response_modalities = self._normalize_response_modalities(self.config.response_modalities)

        # Build generation config from configurable parameters
        # https://gist.github.com/quartzjer/9636066e96b4f904162df706210770e4
        generation_config = {
            "responseModalities": response_modalities,
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": self.config.tts_voice_name or "Aoede"
                    }
                }
            },
            # LLM generation parameters (all configurable via YAML)
            "temperature": self.config.llm_temperature,
            "maxOutputTokens": self.config.llm_max_output_tokens,
            "topP": self.config.llm_top_p,
            "topK": self.config.llm_top_k,
        }

        # Detailed debug logging for speech configuration
        speech_cfg = generation_config.get("speechConfig", {})
        voice_cfg = speech_cfg.get("voiceConfig", {}).get("prebuiltVoiceConfig", {})
        logger.debug(
            "Google Live speech configuration",
            call_id=self._call_id,
            voice_name=voice_cfg.get("voiceName"),
            response_modalities=response_modalities,
        )

        # Build tools config using context tool list (filtered by engine)
        # CRITICAL: Use context['tools'] to respect per-context tool configuration
        # Don't call get_tools_config() which returns ALL tools - use context filtering
        tools = []
        tool_names = context.get('tools', []) if context else []
        if tool_names and self._tool_adapter:
            try:
                # Use format_tools() with filtered tool list from context
                tools = self._tool_adapter.format_tools(tool_names)
                if tools:
                    tool_count = len(tools[0].get("functionDeclarations", [])) if tools else 0
                    logger.debug(
                        "Google Live tools configured from context",
                        call_id=self._call_id,
                        tool_count=tool_count,
                        tool_names=tool_names
                    )
            except Exception as e:
                logger.warning(f"Failed to configure tools: {e}", call_id=self._call_id, exc_info=True)

        # Setup message
        # Strip any accidental "models/" prefix from config to avoid models/models/...
        model_name = self._normalize_model_name(self.config.llm_model)
        if model_name.startswith("models/"):
            model_name = model_name[7:]  # Remove "models/" prefix
        
        setup_msg = {
            "setup": {
                "model": f"models/{model_name}",
                # Live API expects camelCase field names.
                "generationConfig": generation_config,
            }
        }

        if system_prompt:
            # Live API expects `systemInstruction` (Content).
            setup_msg["setup"]["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        if tools:
            setup_msg["setup"]["tools"] = tools
            # NOTE: toolConfig/functionCallingConfig is NOT supported by Live API
            # (causes 1007 "Unknown name toolConfig" error)
            # Tool calling mode must be controlled via system prompt instead
        
        # Enable transcriptions for conversation history tracking (configurable)
        # This allows us to populate email summaries and transcripts
        # Note: Using camelCase per Google Live API format
        # Use empty object {} to enable with default settings (no "model" field - API doesn't support it)
        # CRITICAL: languageCode is NOT supported by transcription config (API rejects it with code 1007)
        # Language must be controlled via system prompt instead
        if self.config.enable_input_transcription:
            setup_msg["setup"]["inputAudioTranscription"] = {}
        
        if self.config.enable_output_transcription:
            setup_msg["setup"]["outputAudioTranscription"] = {}

        # Configure server-side VAD for turn detection (realtimeInputConfig)
        # Higher startOfSpeechSensitivity = catches shorter utterances
        # Lower silenceDurationMs = faster response after user stops talking
        # Configurable via YAML: providers.google_live.vad_*
        vad_eos = getattr(self.config, "vad_end_of_speech_sensitivity", "END_SENSITIVITY_HIGH")
        vad_sos = getattr(self.config, "vad_start_of_speech_sensitivity", "START_SENSITIVITY_HIGH")
        vad_prefix_ms = int(getattr(self.config, "vad_prefix_padding_ms", 20))
        vad_silence_ms = int(getattr(self.config, "vad_silence_duration_ms", 500))
        setup_msg["setup"]["realtimeInputConfig"] = {
            "automaticActivityDetection": {
                "disabled": False,
                "startOfSpeechSensitivity": vad_sos,
                "endOfSpeechSensitivity": vad_eos,
                "prefixPaddingMs": vad_prefix_ms,
                "silenceDurationMs": vad_silence_ms,
            }
        }

        # Debug: Log setup message structure
        logger.debug(
            "Sending Google Live setup message",
            call_id=self._call_id,
            setup_keys=list(setup_msg.get("setup", {}).keys()),
            model=setup_msg.get("setup", {}).get("model"),
            has_system_instruction=bool(system_prompt),
            tools_count=len(tools),
            generation_config=generation_config,
        )

        await self._send_message(setup_msg)
        
        logger.info(
            "Sent Google Live setup",
            call_id=self._call_id,
            has_system_prompt=bool(system_prompt),
            tools_count=len(tools),
        )

    async def _send_message(self, message: Dict[str, Any]) -> None:
        """Send a message to Google Live API."""
        summary = self._summarize_outbound(message)
        try:
            summary_with_ts = dict(summary)
            summary_with_ts["ts_monotonic"] = round(time.monotonic(), 3)
            self._outbound_summaries.append(summary_with_ts)
            # Track continuous audio traffic to avoid unnecessary WS pings.
            if summary_with_ts.get("type") == "realtimeInput":
                self._last_realtime_input_sent_monotonic = float(summary_with_ts["ts_monotonic"])
        except Exception:
            pass

        if not self._ws_is_open():
            if not self._ws_unavailable_logged:
                logger.warning(
                    "Google Live websocket not open; dropping outbound message",
                    call_id=self._call_id,
                    message_type=summary.get("type"),
                    message_keys=summary.get("keys"),
                )
                self._ws_unavailable_logged = True
            return

        async with self._send_lock:
            try:
                await self.websocket.send(json.dumps(message))
                self._ws_unavailable_logged = False
            except Exception as e:
                if isinstance(e, (ConnectionClosedError, ConnectionClosedOK)):
                    close_reason = getattr(e, "reason", None)
                    close_code = getattr(e, "code", None)
                    if not self._ws_send_close_logged:
                        logger.warning(
                            "Google Live WebSocket closed during send",
                            call_id=self._call_id,
                            code=close_code,
                            reason=close_reason,
                            last_outbound=summary,
                        )
                        self._ws_send_close_logged = True
                    self._mark_ws_disconnected()
                    return
                logger.error(
                    "Failed to send message to Google Live",
                    call_id=self._call_id,
                    error=str(e),
                    last_outbound=summary,
                )
                # Prevent log storms when the socket is already closed.
                if not self._ws_is_open():
                    self._mark_ws_disconnected()

    def _safe_jsonable(self, obj: Any, *, depth: int = 0, max_depth: int = 4, max_items: int = 30) -> Any:
        if depth >= max_depth:
            return str(obj)
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, dict):
            out: Dict[str, Any] = {}
            for idx, (k, v) in enumerate(obj.items()):
                if idx >= max_items:
                    break
                out[str(k)] = self._safe_jsonable(v, depth=depth + 1, max_depth=max_depth, max_items=max_items)
            return out
        if isinstance(obj, (list, tuple)):
            return [self._safe_jsonable(v, depth=depth + 1, max_depth=max_depth, max_items=max_items) for v in list(obj)[:max_items]]
        return str(obj)

    def _build_tool_response_payload(self, tool_name: str, result: Any) -> Dict[str, Any]:
        """
        Google Live can return 1011 internal errors if toolResponse payloads are too large or contain
        unexpected shapes. Keep responses minimal, JSON-serializable, and capped in size.
        """
        if not isinstance(result, dict):
            payload: Dict[str, Any] = {"status": "success", "message": str(result)}
        else:
            payload = {}
            # Keep fields that affect conversation control.
            for k in ("status", "message", "will_hangup", "transferred", "transfer_mode", "extension", "destination"):
                if k in result:
                    payload[k] = self._safe_jsonable(result.get(k))
            # Always provide a message string (best-effort).
            if "message" not in payload:
                payload["message"] = str(result.get("message") or "")
            # Do NOT include raw MCP result blobs - they are commonly large/nested and cause
            # Google Live to stutter when generating audio. The `message` field already contains
            # the speech text extracted via speech_field/speech_template.

        # Cap size aggressively.
        try:
            encoded = json.dumps(payload, ensure_ascii=False)
            if len(encoded.encode("utf-8")) <= self._tool_response_max_bytes:
                return payload
        except Exception:
            pass

        # If too large, fall back to status + truncated message only.
        msg = str(payload.get("message") or "")
        msg = msg[:800]
        return {"status": payload.get("status", "success"), "message": msg}

    async def _send_greeting(self) -> None:
        """Send greeting by asking Gemini to speak it (validated pattern from Golden Baseline)."""
        greeting = (self.config.greeting or "").strip()
        if not greeting:
            return
        
        logger.info("Sending greeting request to Google Live", call_id=self._call_id, greeting_preview=greeting[:50])
        
        # Per Golden Baseline (docs/case-studies/Google-Live-Golden-Baseline.md):
        # Validated approach for ExternalMedia RTP - send user turn requesting greeting
        # This worked successfully in production testing (Nov 14, 2025 - Call 1763092342.5132)
        # 
        # NOTE: This is the VALIDATED pattern, but current AudioSocket implementation
        # is experiencing greeting repetition issue that ExternalMedia RTP did not have.
        # Need to investigate AudioSocket-specific difference.
        greeting_msg = {
            "clientContent": {
                "turns": [
                    {
                        "role": "user",
                        "parts": [{"text": f"Please greet the caller with the following message: {greeting}"}]
                    }
                ],
                "turnComplete": True
            }
        }
        await self._send_message(greeting_msg)
        
        logger.info(
            "✅ Greeting request sent to Gemini (Golden Baseline pattern)",
            call_id=self._call_id,
        )

    async def send_audio(self, audio_chunk: bytes, sample_rate: int = 8000, encoding: str = "ulaw") -> None:
        """
        Send audio chunk to Gemini Live API.

        Args:
            audio_chunk: Raw audio bytes (µ-law or PCM16)
            sample_rate: Sample rate of input audio (default from config)
            encoding: Audio encoding (ulaw/linear16/pcm16)
        """
        if not self.websocket or not self._setup_complete or self._closing:
            return

        try:
            # Infer format from chunk size if not specified
            if encoding == "ulaw" or (sample_rate == 8000 and len(audio_chunk) == 160):
                # μ-law to PCM16
                pcm16_src = mulaw_to_pcm16le(audio_chunk)
                src_rate = sample_rate
            else:
                # Already PCM16
                pcm16_src = audio_chunk
                src_rate = sample_rate

            # Resample to provider's input rate (16kHz for Gemini Live)
            provider_rate = self.config.provider_input_sample_rate_hz
            if src_rate != provider_rate:
                pcm16_provider, _ = resample_audio(
                    pcm16_src,
                    source_rate=src_rate,
                    target_rate=provider_rate,
                )
            else:
                pcm16_provider = pcm16_src

            # GOLDEN BASELINE APPROACH: Buffer and send in 20ms chunks
            # This matches the validated implementation from Nov 14, 2025
            # Add to buffer
            self._input_buffer.extend(pcm16_provider)
            
            # Send in chunks (20ms at provider rate)
            chunk_size = int(provider_rate * 2 * _COMMIT_INTERVAL_SEC)  # 2 bytes per sample
            
            while len(self._input_buffer) >= chunk_size:
                chunk_to_send = bytes(self._input_buffer[:chunk_size])
                self._input_buffer = self._input_buffer[chunk_size:]
                
                # Encode as base64
                audio_b64 = base64.b64encode(chunk_to_send).decode("utf-8")
                
                # Send realtime input (using camelCase keys per actual API)
                message = {
                    "realtimeInput": {  # camelCase not snake_case
                        # `mediaChunks` is deprecated in the Live API schema; prefer `audio`.
                        "audio": {
                            "mimeType": f"audio/pcm;rate={provider_rate}",
                            "data": audio_b64,
                        },
                    }
                }
                
                await self._send_message(message)
                _GOOGLE_LIVE_AUDIO_SENT.inc(len(chunk_to_send))

        except Exception as e:
            logger.error(
                "Error sending audio to Google Live",
                call_id=self._call_id,
                error=str(e),
                exc_info=True,
            )

    async def _receive_loop(self) -> None:
        """Continuously receive and process messages from Gemini Live API."""
        if not self.websocket:
            return

        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    await self._handle_server_message(data)
                except json.JSONDecodeError as e:
                    logger.error(
                        "Failed to decode Google Live message",
                        call_id=self._call_id,
                        error=str(e),
                    )
                except Exception as e:
                    logger.error(
                        "Error handling Google Live message",
                        call_id=self._call_id,
                        error=str(e),
                        exc_info=True,
                    )
        except (ConnectionClosedError, ConnectionClosedOK) as e:
            # Enhanced logging for WebSocket close
            close_reason = e.reason if hasattr(e, 'reason') else "No reason provided"
            close_code = e.code if hasattr(e, 'code') else None
            
            # Decode close code meaning
            close_code_meanings = {
                1000: "Normal closure",
                1001: "Going away",
                1002: "Protocol error",
                1003: "Unsupported data",
                1006: "Abnormal closure (no close frame)",
                1007: "Invalid frame payload data",
                1008: "Policy violation (unsupported operation, auth, or feature gating)",
                1009: "Message too big",
                1010: "Mandatory extension missing",
                1011: "Internal server error",
            }
            close_meaning = close_code_meanings.get(close_code, "Unknown")
            
            logger.warning(
                "Google Live WebSocket closed",
                call_id=self._call_id,
                code=close_code,
                meaning=close_meaning,
                reason=close_reason,
                outbound_tail=list(self._outbound_summaries),
                ws_keepalive={
                    "ping_seq": self._ws_ping_seq,
                    "pong_seq": self._ws_pong_seq,
                    "last_ping_age_sec": (
                        round(time.monotonic() - self._last_ws_ping_monotonic, 3)
                        if self._last_ws_ping_monotonic is not None
                        else None
                    ),
                    "last_pong_age_sec": (
                        round(time.monotonic() - self._last_ws_pong_monotonic, 3)
                        if self._last_ws_pong_monotonic is not None
                        else None
                    ),
                    "last_ping_rtt_ms": (round(self._last_ws_ping_rtt_ms, 2) if self._last_ws_ping_rtt_ms else None),
                    "last_ping_error": self._last_ws_ping_error,
                },
            )
            
            # Specific guidance for common errors
            if close_code == 1008:
                hint = "Verify: 1) model supports Live (bidiGenerateContent) 2) API key + Live API access 3) request schema matches docs"
                if isinstance(close_reason, str) and "not supported" in close_reason.lower():
                    hint = "Model/endpoint feature gating: verify model supports bidiGenerateContent (Live) for your API version/region"
                if isinstance(close_reason, str) and "not implemented" in close_reason.lower():
                    hint = "Server rejected an unsupported operation; verify message schema + model supports the requested features"
                logger.error(
                    "Policy violation (1008)",
                    call_id=self._call_id,
                    hint=hint,
                    outbound_tail=list(self._outbound_summaries),
                    ws_keepalive={
                        "ping_seq": self._ws_ping_seq,
                        "pong_seq": self._ws_pong_seq,
                        "last_ping_age_sec": (
                            round(time.monotonic() - self._last_ws_ping_monotonic, 3)
                            if self._last_ws_ping_monotonic is not None
                            else None
                        ),
                        "last_pong_age_sec": (
                            round(time.monotonic() - self._last_ws_pong_monotonic, 3)
                            if self._last_ws_pong_monotonic is not None
                            else None
                        ),
                        "last_ping_rtt_ms": (round(self._last_ws_ping_rtt_ms, 2) if self._last_ws_ping_rtt_ms else None),
                        "last_ping_error": self._last_ws_ping_error,
                    },
                )
            # Persist any pending transcription buffers before we signal the engine to tear down.
            try:
                await self._flush_pending_transcriptions_on_disconnect(code=close_code, reason=close_reason)
            except Exception:
                logger.debug(
                    "Failed flushing pending transcriptions on disconnect",
                    call_id=self._call_id,
                    exc_info=True,
                )
            self._mark_ws_disconnected()
            # On 1008 (Google server-side bug with native audio + tools), attempt
            # auto-reconnect instead of killing the call.
            if close_code == 1008 and self._reconnect_count < self._max_reconnect_attempts:
                if not self._closing and not self._closed and self._call_id:
                    try:
                        if not self._reconnect_task or self._reconnect_task.done():
                            self._reconnect_task = asyncio.create_task(
                                self._reconnect_with_backoff(),
                                name=f"google-live-reconnect-{self._call_id}",
                            )
                    except Exception:
                        logger.debug("Failed to schedule Google Live reconnect", call_id=self._call_id, exc_info=True)
                    return  # Don't emit ProviderDisconnected yet
            try:
                # Only treat abnormal closes as a "disconnect" signal for the engine.
                # Normal closure (1000) can occur during expected teardown and should not
                # force an immediate hangup (would cut off farewell audio / cleanup flows).
                if close_code != 1000:
                    await self._emit_provider_disconnected(code=close_code, reason=close_reason)
            except Exception:
                logger.debug(
                    "Failed to emit ProviderDisconnected event",
                    call_id=self._call_id,
                    exc_info=True,
                )
        except Exception as e:
            logger.error(
                "Google Live receive loop error",
                call_id=self._call_id,
                error=str(e),
                exc_info=True,
            )

    async def _handle_server_message(self, data: Dict[str, Any]) -> None:
        """Handle incoming message from Gemini Live API."""
        message_type = None
        
        # Determine message type
        if "setupComplete" in data:
            message_type = "setupComplete"
        elif "serverContent" in data:
            message_type = "serverContent"
        elif "toolCall" in data:
            message_type = "toolCall"
        elif "toolCallCancellation" in data:
            message_type = "toolCallCancellation"
        elif "inputTranscription" in data:
            message_type = "inputTranscription"
        elif "outputTranscription" in data:
            message_type = "outputTranscription"
        elif "goAway" in data:
            message_type = "goAway"

        logger.debug(
            "Received Google Live message",
            call_id=self._call_id,
            message_type=message_type,
        )

        # Handle by type
        if message_type == "setupComplete":
            await self._handle_setup_complete(data)
        elif message_type == "serverContent":
            await self._handle_server_content(data)
        elif message_type == "toolCall":
            await self._handle_tool_call(data)
        elif message_type == "toolCallCancellation":
            await self._handle_tool_call_cancellation(data)
        elif message_type == "goAway":
            await self._handle_go_away(data)
        # Note: inputTranscription and outputTranscription are NOT separate message types
        # They are fields within serverContent and are handled in _handle_server_content()

    async def _handle_setup_complete(self, data: Dict[str, Any]) -> None:
        """Handle setupComplete message."""
        self._setup_complete = True
        
        # Unblock audio streaming (ACK pattern like Deepgram)
        if self._setup_ack_event:
            self._setup_ack_event.set()
        
        logger.info(
            "Google Live setup complete",
            call_id=self._call_id,
        )

        # Play greeting if configured (skip on reconnect — caller already heard it)
        if self.config.greeting and not self._greeting_completed:
            self._greeting_completed = True
            await self._send_greeting()

    async def _handle_server_content(self, data: Dict[str, Any]) -> None:
        """Handle serverContent message (audio, text, etc.)."""
        content = data.get("serverContent", {})

        logger.debug(
            "Google Live serverContent envelope",
            call_id=self._call_id,
            keys=list(content.keys()),
            has_input=bool(content.get("inputTranscription")),
            has_output=bool(content.get("outputTranscription")),
            has_turn_complete=bool(content.get("turnComplete")),
            has_model_turn=bool(content.get("modelTurn")),
        )
        
        # Handle input transcription (user speech) - per official API docs
        # CONFIRMED BY TESTING: API sends INCREMENTAL fragments, not cumulative updates
        # Despite documentation suggesting cumulative behavior, actual API sends:
        # " What" -> " is" -> " the" -> " la" -> "ten" -> "cy" -> " for" -> " this project."
        # We must CONCATENATE all fragments until turnComplete
        input_transcription = content.get("inputTranscription")
        if input_transcription:
            text = input_transcription.get("text", "")
            if text:
                # If we armed a heuristic cleanup_after_tts fallback (no toolCall), cancel it when the
                # user continues speaking. This prevents premature hangups during transcript/email
                # capture where the model may say "thank you for calling" before the user is done.
                await self._maybe_disarm_cleanup_after_tts_fallback_on_user_speech()
                # Track turn start time on EVERY user input fragment (Milestone 21)
                # This captures the LAST speech fragment time before AI responds
                # Measures: last user speech → first AI audio response
                self._turn_start_time = time.time()
                self._turn_first_audio_received = False
                self._input_transcription_buffer, self._last_input_transcription_fragment = _merge_transcription_fragment(
                    self._input_transcription_buffer, text, self._last_input_transcription_fragment
                )
                logger.debug(
                    "Google Live input transcription fragment",
                    call_id=self._call_id,
                    fragment=text,
                    buffer_length=len(self._input_transcription_buffer),
                )
                intent = self._detect_user_end_intent(self._input_transcription_buffer)
                if intent and not self._user_end_intent:
                    self._user_end_intent = intent
                    logger.info(
                        "Google Live detected user end-of-call intent",
                        call_id=self._call_id,
                        intent=intent,
                        buffer_preview=self._input_transcription_buffer[:120],
                    )

        # Handle output transcription (AI speech) - per official API docs
        # Like inputTranscription, API sends incremental fragments that must be concatenated
        output_transcription = content.get("outputTranscription")
        if output_transcription:
            text = output_transcription.get("text", "")
            if text:
                self._turn_has_assistant_output = True
                if self._hangup_after_response and not self._force_farewell_sent:
                    self._post_hangup_output_detected = True
                self._output_transcription_buffer, self._last_output_transcription_fragment = _merge_transcription_fragment(
                    self._output_transcription_buffer, text, self._last_output_transcription_fragment
                )
                logger.debug(
                    "Google Live output transcription fragment",
                    call_id=self._call_id,
                    fragment=text,
                    buffer_length=len(self._output_transcription_buffer),
                )
                farewell = self._detect_assistant_farewell(self._output_transcription_buffer)
                if farewell and not self._assistant_farewell_intent:
                    self._assistant_farewell_intent = farewell
                    logger.info(
                        "Google Live detected assistant farewell intent",
                        call_id=self._call_id,
                        intent=farewell,
                        buffer_preview=self._output_transcription_buffer[:120],
                    )
        
        # Check if model turn is complete - THIS is when we save the final transcription
        turn_complete = content.get("turnComplete", False)

        # Save final transcriptions when turn completes (per API recommendation)
        if turn_complete:
            self._hangup_fallback_turn_complete_seen = True
            # Save user speech if buffered
            if self._input_transcription_buffer:
                self._last_final_user_text = self._input_transcription_buffer
                logger.info(
                    "Google Live final user transcription (turnComplete)",
                    call_id=self._call_id,
                    text=self._input_transcription_buffer[:150],
                )
                await self._track_conversation_message("user", self._input_transcription_buffer)
                self._input_transcription_buffer = ""
                self._last_input_transcription_fragment = ""

            # Save AI speech if buffered (prefer outputTranscription, fall back to modelTurn.text)
            assistant_final_text = (self._output_transcription_buffer or self._model_text_buffer or "").strip()
            if assistant_final_text:
                self._last_final_assistant_text = assistant_final_text
                logger.info(
                    "Google Live final AI transcription (turnComplete)",
                    call_id=self._call_id,
                    text=assistant_final_text[:150],
                )
                await self._track_conversation_message("assistant", assistant_final_text)
                # Fallback: if the model speaks a clear farewell but doesn't emit a hangup_call toolCall,
                # arm engine-level hangup after TTS completion.
                await self._maybe_arm_cleanup_after_tts(
                    user_text=self._last_final_user_text,
                    assistant_text=self._last_final_assistant_text,
                )
                self._output_transcription_buffer = ""
                self._last_output_transcription_fragment = ""
                self._model_text_buffer = ""
            
            # Reset turn tracking for next turn (Milestone 21)
            self._turn_start_time = None
            self._turn_first_audio_received = False
        
        # Extract parts (using camelCase keys from actual API)
        model_turn = content.get("modelTurn", {})
        model_parts = model_turn.get("parts", []) if isinstance(model_turn, dict) else []
        if model_parts:
            self._turn_has_assistant_output = True
        for part in model_parts:
            # Handle audio output
            if "inlineData" in part:
                inline_data = part["inlineData"]
                mime_type = inline_data.get("mimeType", "") or ""
                if mime_type.startswith("audio/pcm"):
                    await self._handle_audio_output(inline_data["data"], mime_type=mime_type)
            
            # Handle text output (for debugging/logging only)
            # Note: We now get cleaner AI transcriptions from outputTranscription field
            if "text" in part:
                text = part["text"]
                logger.debug(
                    "Google Live text response from modelTurn (not saved - using outputTranscription instead)",
                    call_id=self._call_id,
                    text_preview=text[:100],
                )
                # IMPORTANT:
                # `modelTurn.text` is not guaranteed to be spoken text and may include non-audio
                # reasoning/metadata. Do NOT use it for end-of-call detection or cleanup arming,
                # otherwise we can hang up mid-conversation (e.g., during transcript email capture).
                self._model_text_buffer += text

        # Handle turn completion
        if turn_complete:
            await self._handle_turn_complete()

    async def _maybe_arm_cleanup_after_tts(self, *, user_text: str, assistant_text: str) -> None:
        """
        Gemini Live tool calling is model-driven (AUTO) and may not emit a `toolCall` even when it
        speaks a farewell. To keep call teardown reliable, detect obvious end-of-call turns and set
        `cleanup_after_tts=True` so the engine hangs up after audio playback completes.
        """
        # Marker-driven heuristic; keep tool-driven hangups working even when markers are disabled.
        if not self._hangup_markers_enabled:
            return
        if not self._call_id:
            return
        session_store = getattr(self, "_session_store", None)
        if not session_store:
            return

        if self._hangup_fallback_armed:
            return

        user_reason = self._detect_user_end_intent(user_text) or self._user_end_intent
        if not user_reason:
            return
        assistant_reason = self._detect_assistant_farewell(assistant_text) or self._assistant_farewell_intent

        # "No transcript" and explicit hangup intents are strong indicators that the call is ending,
        # even if output transcription is missing or the model doesn't include a canonical farewell.
        #
        # IMPORTANT: Do NOT treat a simple user "goodbye"/"bye" as a strong marker here.
        # Many contexts (including our golden baselines) use a "goodbye → offer transcript" flow.
        # Arming cleanup on the user's goodbye causes the engine to hang up right after the agent's
        # transcript offer, cutting off the caller's ability to respond.
        strong_user_markers = {
            "no transcript",
            "no transcript needed",
            "don't send a transcript",
            "do not send a transcript",
            "no need for a transcript",
            "hang up",
            "hangup",
            "end the call",
            "end call",
        }
        if user_reason not in strong_user_markers and not assistant_reason:
            return

        try:
            session = await session_store.get_by_call_id(self._call_id)
            if not session:
                return
            if getattr(session, "cleanup_after_tts", False):
                return
            session.cleanup_after_tts = True
            await session_store.upsert_call(session)
            self._hangup_fallback_armed = True
            self._hangup_fallback_armed_at = time.monotonic()
            self._hangup_fallback_audio_started = False
            self._hangup_fallback_turn_complete_seen = False
            self._hangup_fallback_wait_logged = False
            await self._ensure_hangup_fallback_watchdog()
            logger.info(
                "🔚 Armed cleanup_after_tts fallback (no toolCall required)",
                call_id=self._call_id,
                user_reason=user_reason,
                assistant_reason=assistant_reason,
                user_hint=(user_text or "")[:120],
                assistant_hint=(assistant_text or "")[:120],
            )
        except Exception as e:
            logger.debug(
                "Failed to arm cleanup_after_tts fallback",
                call_id=self._call_id,
                error=str(e),
                exc_info=True,
            )

    async def _maybe_disarm_cleanup_after_tts_fallback_on_user_speech(self) -> None:
        """
        Cancel heuristic cleanup_after_tts fallback when the user continues speaking.

        This is Google-Live-specific: the Live API can emit "farewell-ish" outputTranscription
        fragments (e.g. "thank you for calling") before the user finishes confirming an email
        address for transcript delivery. If we set cleanup_after_tts too early, the engine may
        hang up as soon as the assistant audio completes, cutting off the caller.

        We do NOT cancel explicit hangups requested via the hangup_call tool (those set
        `_hangup_after_response=True`).
        """
        if not self._call_id:
            return
        if not self._hangup_fallback_armed:
            return
        # If HangupReady was already emitted, disarming is too late — engine already received signal.
        if self._hangup_fallback_emitted:
            return
        # If hangup_call tool was invoked, keep the hangup flow intact.
        if self._hangup_after_response:
            return

        session_store = getattr(self, "_session_store", None)
        if not session_store:
            return
        try:
            session = await session_store.get_by_call_id(self._call_id)
            if not session:
                return
            if not getattr(session, "cleanup_after_tts", False):
                return
            session.cleanup_after_tts = False
            await session_store.upsert_call(session)
            self._hangup_fallback_armed = False
            self._hangup_fallback_armed_at = None
            self._hangup_fallback_audio_started = False
            self._hangup_fallback_turn_complete_seen = False
            self._hangup_fallback_wait_logged = False
            logger.info(
                "🛑 Disarmed cleanup_after_tts fallback due to continued user speech",
                call_id=self._call_id,
            )
        except Exception:
            logger.debug(
                "Failed to disarm cleanup_after_tts fallback on user speech",
                call_id=self._call_id,
                exc_info=True,
            )

    @staticmethod
    def _extract_pcm_rate_from_mime_type(mime_type: str) -> Optional[int]:
        """
        Parse an inlineData mimeType like:
          - "audio/pcm;rate=24000"
          - "audio/pcm; rate=24000"
        """
        if not mime_type:
            return None
        m = re.search(r"(?:^|;)\s*rate\s*=\s*(\d+)\s*(?:;|$)", mime_type, flags=re.IGNORECASE)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    async def _handle_audio_output(self, audio_b64: str, *, mime_type: str = "") -> None:
        """
        Handle audio output from Gemini.

        Args:
            audio_b64: Base64-encoded PCM16 audio
            mime_type: inlineData mimeType (may include `rate=...`)
        """
        try:
            self._last_audio_out_monotonic = time.monotonic()
            if self._hangup_fallback_armed:
                self._hangup_fallback_audio_started = True
            # Decode base64
            pcm16_provider = base64.b64decode(audio_b64)
            
            # Track turn latency on first audio output (Milestone 21 - Call History)
            if self._turn_start_time is not None and not self._turn_first_audio_received:
                self._turn_first_audio_received = True
                turn_latency_ms = (time.time() - self._turn_start_time) * 1000
                # Save to session for call history
                if self._session_store:
                    try:
                        session = await self._session_store.get_by_call_id(self._call_id)
                        if session:
                            session.turn_latencies_ms.append(turn_latency_ms)
                            await self._session_store.upsert_call(session)
                    except Exception:
                        pass
                logger.debug(
                    "Turn latency recorded",
                    call_id=self._call_id,
                    latency_ms=round(turn_latency_ms, 1),
                )
            
            _GOOGLE_LIVE_AUDIO_RECEIVED.inc(len(pcm16_provider))

            # Resample from provider output rate to target wire rate (from config)
            configured_output_rate = int(getattr(self.config, "output_sample_rate_hz", 0) or 0)
            provider_reported_output_rate = int(self._extract_pcm_rate_from_mime_type(mime_type) or 0)
            provider_output_rate = provider_reported_output_rate or configured_output_rate
            target_rate = self.config.target_sample_rate_hz

            # Log output rate negotiation once per call for RCA/debug (INFO-level).
            try:
                if not getattr(self, "_logged_output_rate_mismatch", False):
                    self._logged_output_rate_mismatch = True
                    if provider_reported_output_rate and configured_output_rate and provider_reported_output_rate != configured_output_rate:
                        logger.warning(
                            "Google Live output PCM rate differs from configured output_sample_rate_hz; using provider rate",
                            call_id=self._call_id,
                            provider="google_live",
                            configured_output_sample_rate_hz=configured_output_rate,
                            provider_reported_output_sample_rate_hz=provider_reported_output_rate,
                            used_output_sample_rate_hz=provider_output_rate,
                        )
                    else:
                        logger.info(
                            "Google Live output PCM rate",
                            call_id=self._call_id,
                            provider="google_live",
                            configured_output_sample_rate_hz=configured_output_rate,
                            provider_reported_output_sample_rate_hz=(provider_reported_output_rate or None),
                            used_output_sample_rate_hz=provider_output_rate,
                        )
            except Exception:
                logger.debug("Failed to emit Google Live output PCM rate log", call_id=self._call_id, exc_info=True)
            
            if provider_output_rate != target_rate:
                pcm16_target, _ = resample_audio(
                    pcm16_provider,
                    source_rate=provider_output_rate,
                    target_rate=target_rate,
                )
            else:
                pcm16_target = pcm16_provider

            # Convert to target format (from config: ulaw/linear16/pcm16)
            target_encoding = self.config.target_encoding.lower()
            if target_encoding in ("ulaw", "mulaw", "g711_ulaw"):
                output_audio = convert_pcm16le_to_target_format(pcm16_target, "mulaw")
            else:
                # PCM16/linear16 - no conversion needed
                output_audio = pcm16_target

            # Emit audio event (matching OpenAI Realtime pattern)
            if not self._in_audio_burst:
                self._in_audio_burst = True
            
            if self.on_event:
                await self.on_event(
                    {
                        "type": "AgentAudio",
                        "data": output_audio,
                        "call_id": self._call_id,
                        "encoding": target_encoding,  # Tell engine what format we're sending
                        "sample_rate": target_rate,  # Tell engine what rate we're sending
                    }
                )

        except Exception as e:
            logger.error(
                "Error handling Google Live audio output",
                call_id=self._call_id,
                error=str(e),
                exc_info=True,
            )

    async def _handle_turn_complete(self) -> None:
        """Handle turn completion."""
        had_audio = self._in_audio_burst
        turn_was_assistant = self._turn_has_assistant_output
        self._turn_has_assistant_output = False
        
        # Note: Transcription is now saved in _handle_server_content when turnComplete=true
        # No need to flush here - it's already been handled
        
        if self._in_audio_burst:
            self._in_audio_burst = False
            if self.on_event:
                await self.on_event(
                    {
                        "type": "AgentAudioDone",
                        "call_id": self._call_id,
                        "streaming_done": True,
                    }
                )
            # If we armed cleanup_after_tts, the engine will handle hangup on AgentAudioDone.
            # Prevent the watchdog from emitting duplicate HangupReady events.
            if self._hangup_fallback_armed:
                self._hangup_fallback_emitted = True

        # Mark greeting as complete after first turn
        if not self._greeting_completed:
            self._greeting_completed = True
            logger.info(
                "Google Live greeting completed",
                call_id=self._call_id,
            )
        
        # Handle hangup if requested after this turn
        if self._hangup_after_response:
            # IMPORTANT: Live API emits turnComplete for both user turns and assistant turns.
            # Only hang up after the assistant's farewell turn completes; otherwise we can
            # drop the call right when the user answers the transcript question.
            if not turn_was_assistant:
                logger.info(
                    "🔚 Hangup pending; ignoring non-assistant turnComplete",
                    call_id=self._call_id,
                )
                return
            if self._hangup_ready_emitted:
                self._hangup_after_response = False
                return
            logger.info(
                "🔚 Farewell response completed - triggering hangup",
                call_id=self._call_id,
            )
            
            # Emit HangupReady event to trigger hangup in engine
            try:
                if self.on_event:
                    await self.on_event({
                        "type": "HangupReady",
                        "call_id": self._call_id,
                        "reason": "farewell_completed",
                        "had_audio": had_audio
                    })
                self._hangup_ready_emitted = True
            except Exception as e:
                logger.error(
                    "Failed to emit HangupReady event",
                    call_id=self._call_id,
                    error=str(e)
                )
            
            # Reset hangup flag
            self._hangup_after_response = False
            # Stop the watchdog from double-firing.
            self._hangup_fallback_emitted = True

    async def _handle_tool_call(self, data: Dict[str, Any]) -> None:
        """Handle toolCall message."""
        tool_call = data.get("toolCall", {})
        
        if not self._tool_adapter:
            logger.warning(
                "Received tool call but no tool adapter configured",
                call_id=self._call_id,
            )
            return

        try:
            # Extract function call details (camelCase per official API)
            function_calls = tool_call.get("functionCalls", [])
            
            for func_call in function_calls:
                func_name = func_call.get("name")
                func_args = func_call.get("args", {})
                call_id = func_call.get("id")

                logger.info(
                    "Google Live tool call",
                    call_id=self._call_id,
                    function=func_name,
                    tool_call_id=call_id,
                )

                # Build tool execution context
                from src.tools.context import ToolExecutionContext
                tool_context = ToolExecutionContext(
                    call_id=self._call_id,
                    caller_channel_id=getattr(self, '_caller_channel_id', None),
                    bridge_id=getattr(self, '_bridge_id', None),
                    session_store=getattr(self, '_session_store', None),
                    ari_client=getattr(self, '_ari_client', None),
                    config=getattr(self, '_full_config', None),
                    provider_name="google_live",
                )

                # Enforce allowlist from context
                if not self._allowed_tools or not tool_registry.is_tool_allowed(func_name, self._allowed_tools):
                    result = {
                        "status": "error",
                        "message": f"Tool '{func_name}' not allowed for this call",
                    }
                else:
                # Execute tool
                    result = await self._tool_adapter.execute_tool(
                        func_name,
                        func_args,
                        tool_context,
                    )

                # Check for hangup intent (like OpenAI Realtime pattern)
                if func_name == "hangup_call" and result:
                    if result.get("will_hangup"):
                        self._hangup_after_response = True
                        self._force_farewell_text = str(result.get("message") or "").strip()
                        self._force_farewell_sent = False
                        # Also arm the provider-side watchdog as a safety net if turnComplete never arrives.
                        if not self._hangup_fallback_armed:
                            self._hangup_fallback_armed = True
                            self._hangup_fallback_armed_at = time.monotonic()
                            self._hangup_fallback_audio_started = False
                            self._hangup_fallback_turn_complete_seen = False
                            self._hangup_fallback_wait_logged = False
                            await self._ensure_hangup_fallback_watchdog()
                        logger.info(
                            "🔚 Hangup tool executed - next response will trigger hangup",
                            call_id=self._call_id
                        )

                # Send tool response (camelCase per official API)
                safe_result = self._build_tool_response_payload(func_name, result)
                tool_response = {
                    "toolResponse": {
                        "functionResponses": [
                            {
                                "id": call_id,
                                "name": func_name,
                                "response": safe_result,
                            }
                        ]
                    }
                }
                await self._send_message(tool_response)

                logger.info(
                    "Sent Google Live tool response",
                    call_id=self._call_id,
                    function=func_name,
                )

                if func_name == "hangup_call" and self._force_farewell_text:
                    self._post_hangup_output_detected = False
                    self._schedule_forced_farewell_if_needed()
                
                # Log tool call to session for call history (Milestone 21)
                try:
                    session_store = getattr(self, '_session_store', None)
                    if session_store and self._call_id:
                        from datetime import datetime
                        session = await session_store.get_by_call_id(self._call_id)
                        if session:
                            tool_record = {
                                "name": func_name,
                                "params": func_args,
                                "result": result.get("status", "unknown") if isinstance(result, dict) else "success",
                                "message": result.get("message", "") if isinstance(result, dict) else str(result),
                                "timestamp": datetime.now().isoformat(),
                                "duration_ms": 0,  # TODO: track actual duration
                            }
                            if not hasattr(session, 'tool_calls') or session.tool_calls is None:
                                session.tool_calls = []
                            session.tool_calls.append(tool_record)
                            await session_store.upsert_call(session)
                            logger.debug("Tool call logged to session", call_id=self._call_id, tool=func_name)
                except Exception as e:
                    logger.debug(f"Failed to log tool call to session: {e}", call_id=self._call_id)

        except Exception as e:
            logger.error(
                "Error handling Google Live tool call",
                call_id=self._call_id,
                error=str(e),
                exc_info=True,
            )

    async def _handle_tool_call_cancellation(self, data: Dict[str, Any]) -> None:
        """Handle toolCallCancellation message (server canceled one or more pending tool calls)."""
        cancellation = data.get("toolCallCancellation") or {}
        ids = (
            cancellation.get("ids")
            or cancellation.get("functionCallIds")
            or cancellation.get("toolCallIds")
            or cancellation.get("callIds")
        )
        logger.warning(
            "Google Live tool call cancellation",
            call_id=self._call_id,
            ids=ids,
            cancellation_keys=list(cancellation.keys()) if isinstance(cancellation, dict) else None,
        )

    def _schedule_forced_farewell_if_needed(self) -> None:
        if self._force_farewell_sent:
            return
        if self._force_farewell_task and not self._force_farewell_task.done():
            return
        self._force_farewell_task = asyncio.create_task(self._maybe_force_farewell_after_hangup())

    async def _maybe_force_farewell_after_hangup(self) -> None:
        try:
            # Grace window: if the model starts speaking, don't send a duplicate farewell request.
            # Google Live models typically need 2-2.5s to produce first audio after a tool response;
            # 3.0s avoids firing before the model naturally begins its farewell.
            await asyncio.sleep(3.0)
            if not self._call_id or not self._setup_complete or not self._ws_is_open():
                return
            if not self._hangup_after_response:
                return
            if self._force_farewell_sent:
                return
            if self._hangup_fallback_audio_started:
                return
            if self._post_hangup_output_detected:
                logger.debug(
                    "Skipping forced farewell - model already generating post-hangup speech",
                    call_id=self._call_id,
                )
                return

            farewell = (self._force_farewell_text or "").strip()
            if not farewell:
                return

            msg = {
                "clientContent": {
                    "turns": [
                        {
                            "role": "user",
                            "parts": [{"text": f"Please say exactly this farewell to the caller, then stop: {farewell}"}],
                        }
                    ],
                    "turnComplete": True,
                }
            }
            await self._send_message(msg)
            self._force_farewell_sent = True
            logger.info(
                "✅ Forced farewell prompt sent after hangup_call (no audio observed)",
                call_id=self._call_id,
                farewell_preview=farewell[:80],
            )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug(
                "Failed to send forced farewell prompt",
                call_id=self._call_id,
                exc_info=True,
            )

    async def _track_conversation_message(self, role: str, text: str) -> None:
        """
        Track conversation message to session history for transcripts.
        
        Similar to OpenAI Realtime pattern - saves messages to session.conversation_history
        for email summary/transcript tools.
        
        Args:
            role: "user" or "assistant"
            text: Message content
        """
        if not text or not text.strip():
            return
        
        # Keep local copy for reconnect context replay
        self._conversation_history.append({"role": role, "content": text.strip()})
        
        # Get session_store from provider context (injected by engine)
        session_store = getattr(self, '_session_store', None)
        if not session_store:
            logger.debug(
                "No session_store available for conversation tracking",
                call_id=self._call_id,
                role=role
            )
            return
        
        try:
            session = await session_store.get_by_call_id(self._call_id)
            if session:
                # Add to conversation history
                session.conversation_history.append({
                    "role": role,  # "user" or "assistant"
                    "content": text,
                    "timestamp": time.time()
                })
                # Update session
                await session_store.upsert_call(session)
                logger.debug(
                    "✅ Tracked conversation message",
                    call_id=self._call_id,
                    role=role,
                    text_preview=text[:50] + "..." if len(text) > 50 else text
                )
        except Exception as e:
            logger.warning(
                f"Failed to track conversation message: {e}",
                call_id=self._call_id,
                role=role,
                exc_info=True
            )

    async def _handle_go_away(self, data: Dict[str, Any]) -> None:
        """Handle goAway message (server disconnect warning)."""
        logger.warning(
            "Google Live server sending goAway",
            call_id=self._call_id,
        )
        # Prepare for reconnection if needed

    async def _reconnect_with_backoff(self) -> None:
        """Auto-reconnect on 1008 (Google server-side bug with native audio + tools).

        Opens a new WebSocket, re-sends setup (prompt, tools, config),
        and restarts the receive loop. The caller stays on the line and
        hears a brief silence (~1-2s) during reconnection.
        """
        call_id = self._call_id
        if not call_id:
            return
        backoff = 0.5
        for attempt in range(1, self._max_reconnect_attempts + 1):
            if self._closing or self._closed:
                return
            self._reconnect_count += 1
            logger.info(
                "🔄 Google Live auto-reconnect attempt",
                call_id=call_id,
                attempt=attempt,
                max_attempts=self._max_reconnect_attempts,
            )
            try:
                await asyncio.sleep(backoff)
                if self._closing or self._closed:
                    return

                api_key = self.config.api_key or ""
                endpoint = (self.config.websocket_endpoint or "").strip()
                if not endpoint:
                    endpoint = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
                ws_url = f"{endpoint}?key={api_key}"

                self.websocket = await websockets.connect(
                    ws_url,
                    subprotocols=["gemini-live"],
                    max_size=10 * 1024 * 1024,
                    ping_interval=None,
                    ping_timeout=None,
                )
                _GOOGLE_LIVE_SESSIONS.inc()
                self._session_gauge_incremented = True
                self._ws_unavailable_logged = False
                self._outbound_summaries.clear()

                # Re-create ACK event and start receive loop
                self._setup_ack_event = asyncio.Event()
                self._receive_task = asyncio.create_task(
                    self._receive_loop(),
                    name=f"google-live-receive-{call_id}-r{attempt}",
                )

                # Re-send setup with saved context (no greeting on reconnect).
                # Embed conversation history into system prompt so model has context
                # (sending clientContent turns causes 1007 "invalid argument").
                self._greeting_completed = True  # Skip greeting on reconnect
                augmented_prompt = None
                if self._conversation_history:
                    history_lines = []
                    for msg in self._conversation_history[-10:]:
                        role = msg.get("role", "user")
                        content = msg.get("content", "")
                        if content:
                            label = "Caller" if role == "user" else "You"
                            history_lines.append(f"{label}: {content}")
                    if history_lines:
                        history_text = "\n".join(history_lines)
                        base_prompt = self.config.instructions or ""
                        augmented_prompt = (
                            f"{base_prompt}\n\n"
                            f"[SESSION RESUMED — the connection was briefly interrupted. "
                            f"Continue the conversation naturally from where you left off. "
                            f"Do NOT re-greet or restart. Here is the conversation so far:]\n"
                            f"{history_text}"
                        )
                        logger.info(
                            "📜 Embedded conversation history in system prompt for reconnect",
                            call_id=call_id,
                            turns_count=len(history_lines),
                        )
                await self._send_setup(self._reconnect_context, system_prompt_override=augmented_prompt)

                # Wait for setup ACK
                await asyncio.wait_for(self._setup_ack_event.wait(), timeout=5.0)
                self._setup_complete = True

                # Force-clear TTS gating so audio capture resumes.
                # The old streaming playback's gating token is stale after 1008;
                # without this, the engine keeps sending silence frames forever.
                try:
                    session_store = getattr(self, '_session_store', None)
                    if session_store and call_id:
                        session = await session_store.get_by_call_id(call_id)
                        if session and session.tts_active_count > 0:
                            logger.info(
                                "🔓 Force-clearing stale TTS gating after reconnect",
                                call_id=call_id,
                                stale_tokens=session.tts_active_count,
                            )
                            session.tts_active_count = 0
                            session.tts_playing = False
                            session.audio_capture_enabled = True
                            session.tts_gating_tokens.clear()
                            if session.vad_state:
                                session.vad_state["tts_playing"] = False
                            await session_store.upsert_call(session)
                except Exception:
                    logger.debug("Failed to clear gating after reconnect",
                                 call_id=call_id, exc_info=True)

                logger.info(
                    "✅ Google Live reconnected successfully",
                    call_id=call_id,
                    attempt=attempt,
                    total_reconnects=self._reconnect_count,
                )
                return  # Success
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning(
                    "Google Live reconnect attempt failed",
                    call_id=call_id,
                    attempt=attempt,
                    exc_info=True,
                )
                backoff = min(4.0, backoff * 2)

        # Exhausted all reconnect attempts — signal disconnect to engine
        logger.error(
            "Google Live reconnection exhausted all attempts",
            call_id=call_id,
            total_attempts=self._reconnect_count,
        )
        try:
            await self._emit_provider_disconnected(code=1008, reason="Reconnect attempts exhausted")
        except Exception:
            pass

    async def _keepalive_loop(self) -> None:
        """Send periodic keepalive messages."""
        try:
            interval_sec = float(
                getattr(self.config, "ws_keepalive_interval_sec", _KEEPALIVE_INTERVAL_SEC) or _KEEPALIVE_INTERVAL_SEC
            )
        except (TypeError, ValueError):
            interval_sec = float(_KEEPALIVE_INTERVAL_SEC)
        try:
            idle_sec = float(getattr(self.config, "ws_keepalive_idle_sec", 5.0) or 5.0)
        except (TypeError, ValueError):
            idle_sec = 5.0
        enabled = bool(getattr(self.config, "ws_keepalive_enabled", False))

        if not enabled:
            return

        # Guard against bad config values that could cause a tight loop.
        if (not math.isfinite(interval_sec)) or interval_sec < 1.0:
            interval_sec = float(_KEEPALIVE_INTERVAL_SEC) if float(_KEEPALIVE_INTERVAL_SEC) >= 1.0 else 1.0
        if (not math.isfinite(idle_sec)) or idle_sec < 0.0:
            idle_sec = 0.0

        while self._ws_is_open():
            try:
                await asyncio.sleep(interval_sec)
                # Use WebSocket ping frames (protocol-level) rather than undocumented API messages.
                # The Live API docs require `realtimeInput` messages to have a valid payload; sending
                # `{ "realtimeInput": {} }` can be treated as an unsupported operation (observed as
                # 1008 close + 501 NotImplemented in dashboards).
                if self._setup_complete and self.websocket:
                    # If we're actively streaming audio, don't send pings. Some accounts appear to
                    # close connections (1008) after repeated ping frames even when audio is flowing.
                    last_audio = getattr(self, "_last_realtime_input_sent_monotonic", None)
                    if isinstance(last_audio, (int, float)) and (time.monotonic() - float(last_audio)) < idle_sec:
                        continue
                    ping = getattr(self.websocket, "ping", None)
                    if callable(ping):
                        t0 = time.monotonic()
                        self._ws_ping_seq += 1
                        self._last_ws_ping_monotonic = t0
                        self._last_ws_ping_error = None
                        try:
                            # Add to outbound tail so close diagnostics show ping timing too.
                            self._outbound_summaries.append(
                                {
                                    "type": "ws_ping",
                                    "seq": self._ws_ping_seq,
                                    "ts_monotonic": round(t0, 3),
                                }
                            )
                        except (AttributeError, TypeError, ValueError):
                            pass
                        pong_waiter = ping()
                        if asyncio.iscoroutine(pong_waiter):
                            pong_waiter = await pong_waiter
                        if pong_waiter is not None:
                            await asyncio.wait_for(pong_waiter, timeout=5.0)
                        t1 = time.monotonic()
                        self._ws_pong_seq += 1
                        self._last_ws_pong_monotonic = t1
                        self._last_ws_ping_rtt_ms = (t1 - t0) * 1000.0
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_ws_ping_error = str(e)
                logger.error(
                    "Keepalive error",
                    call_id=self._call_id,
                    error=str(e),
                )

    async def cancel_response(self) -> None:
        """
        Cancel the current response (barge-in).
        
        Note: Gemini Live API supports interruption natively via VAD.
        When user starts speaking, the model automatically stops generating.
        """
        # Gemini handles this automatically, but we can log it
        logger.info(
            "Barge-in detected (handled by Gemini VAD)",
            call_id=self._call_id,
        )

    async def stop_session(self) -> None:
        """Stop the Google Live session and cleanup resources."""
        if self._closing or self._closed:
            return
        if not self._call_id:
            return

        self._closing = True
        try:
            logger.info(
                "Stopping Google Live session",
                call_id=self._call_id,
            )

            # Emit final AgentAudioDone if we were mid-burst (RED-4)
            if self._in_audio_burst and self.on_event:
                self._in_audio_burst = False
                try:
                    await self.on_event({
                        "type": "AgentAudioDone",
                        "call_id": self._call_id,
                        "streaming_done": True,
                    })
                except Exception:
                    logger.debug("Failed to emit AgentAudioDone during stop_session",
                                 call_id=self._call_id, exc_info=True)

            # Cancel background tasks
            if self._reconnect_task and not self._reconnect_task.done():
                self._reconnect_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reconnect_task

            if self._receive_task and not self._receive_task.done():
                self._receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._receive_task

            if self._keepalive_task and not self._keepalive_task.done():
                self._keepalive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._keepalive_task

            if self._hangup_fallback_task and not self._hangup_fallback_task.done():
                self._hangup_fallback_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._hangup_fallback_task

            if self._force_farewell_task and not self._force_farewell_task.done():
                self._force_farewell_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._force_farewell_task

            # Close WebSocket
            if self._ws_is_open():
                await self.websocket.close()
            self._mark_ws_disconnected()
        finally:
            # Clear state
            self._call_id = None
            self._session_id = None
            self._input_buffer.clear()
            self._conversation_history.clear()
            self._hangup_after_response = False
            self._hangup_fallback_armed = False
            self._hangup_fallback_emitted = False
            self._hangup_fallback_armed_at = None
            self._hangup_fallback_audio_started = False
            self._hangup_fallback_turn_complete_seen = False
            self._hangup_fallback_wait_logged = False
            self._force_farewell_text = ""
            self._force_farewell_sent = False
            self._post_hangup_output_detected = False
            self._last_audio_out_monotonic = None
            self._user_end_intent = None
            self._assistant_farewell_intent = None
            self._model_text_buffer = ""
            self._input_transcription_buffer = ""
            self._output_transcription_buffer = ""
            self._closing = False
            self._closed = True

            if self._session_start_time:
                duration = time.time() - self._session_start_time
                logger.info(
                    "Google Live session ended",
                    duration_seconds=round(duration, 2),
                )

            logger.info("Google Live session stopped")
