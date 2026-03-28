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
    # Whisper-only echo guard: suppress STT while Local AI Server is emitting TTS audio.
    stt_suppress_until: float = 0.0
    # Telephony utterance segmentation state (batch STT backends like Whisper).
    stt_segment_preroll: bytes = b""
    stt_segment_buffer: bytes = b""
    stt_segment_last_voice_mono: float = 0.0
    stt_segment_in_speech: bool = False
