from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from constants import DEFAULT_MODE
from optional_imports import KaldiRecognizer


@dataclass
class SessionContext:
    """Track per-connection defaults for selective mode handling."""

    call_id: str = "unknown"
    mode: str = DEFAULT_MODE
    recognizer: Optional[KaldiRecognizer] = None
    last_partial: str = ""
    partial_emitted: bool = False
    last_audio_at: float = 0.0
    idle_task: Optional[asyncio.Task] = None
    last_request_meta: Dict[str, Any] = field(default_factory=dict)
    last_final_text: str = ""
    last_final_norm: str = ""
    last_final_at: float = 0.0
    llm_user_turns: List[str] = field(default_factory=list)
    llm_messages: List[Dict[str, str]] = field(default_factory=list)
    # Set when caller barge-in abandons the active exchange.  The next
    # successful assistant turn receives a strong one-shot focus instruction
    # so small models do not resume an older completed topic.
    interruption_pending: bool = False
    # Per-call instructions. ``None`` means a legacy client did not send a
    # session-scoped prompt, in which case the server-level default is used.
    # An empty string is authoritative and intentionally clears instructions.
    system_prompt: Optional[str] = None
    prompt_context_call_id: Optional[str] = None
    # Monotonic response generation used to quarantine output from work that
    # completes after barge-in, a replacement turn, or connection teardown.
    output_generation: int = 0
    closed: bool = False
    response_tasks: set[asyncio.Task] = field(default_factory=set)
    allowed_tools: List[str] = field(default_factory=list)
    tool_schemas: List[Dict[str, Any]] = field(default_factory=list)
    tool_policy: str = "auto"
    # `call_id` of the most recent `tool_context` message that populated the
    # three fields above. Used to detect cross-call leakage on long-lived
    # WebSocket sessions: when a different `call_id` arrives, the server
    # clears the cached tool ACL/schemas/policy instead of inheriting the
    # previous call's context. Per CodeRabbit review of PR #384 comment
    # 3214130571.
    tool_context_call_id: Optional[str] = None
    audio_buffer: bytes = b""
    # Kroko-specific session state
    kroko_ws: Optional[Any] = None
    kroko_connected: bool = False
    # Sherpa-onnx session state
    sherpa_stream: Optional[Any] = None
    sherpa_offline_vad: Optional[Any] = None  # Per-session Silero VAD for offline mode
    # T-one session state
    tone_state: Optional[Any] = None
    tone_buffer_8k: bytes = b""
    # Optional auth state (enabled if LOCAL_WS_AUTH_TOKEN set)
    authenticated: bool = False
    # Set once we've logged a protocol_version mismatch for this connection,
    # so the warning is emitted at most once per session instead of per message.
    protocol_version_warned: bool = False
    # Whisper-only echo guard: suppress STT while Local AI Server is emitting TTS audio.
    stt_suppress_until: float = 0.0
    # Telephony utterance segmentation state (batch STT backends like Whisper).
    stt_segment_preroll: bytes = b""
    stt_segment_buffer: bytes = b""
    stt_segment_last_voice_mono: float = 0.0
    stt_segment_in_speech: bool = False
