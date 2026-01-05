"""
Core data models for the Asterisk AI Voice Agent.

This module defines the typed data structures that replace the dict soup
in the original engine.py implementation.
"""

from dataclasses import dataclass, field
from typing import Optional, Set, Dict, Any, List, TYPE_CHECKING
import time
from datetime import datetime

if TYPE_CHECKING:
    from .transport_orchestrator import TransportProfile as OrchestratorTransportProfile


@dataclass
class LegacyTransportProfile:
    """Legacy transport characteristics (kept for backward compatibility)."""
    format: str = "ulaw"
    sample_rate: int = 8000
    channels: int = 1
    source: str = "config"  # config | dialplan | audiosocket | detected
    last_updated: float = field(default_factory=time.time)

    def update(self, *, format: Optional[str] = None, sample_rate: Optional[int] = None, channels: Optional[int] = None, source: Optional[str] = None) -> None:
        if format:
            self.format = format
        if sample_rate:
            self.sample_rate = sample_rate
        if channels:
            self.channels = channels
        if source:
            self.source = source
        self.last_updated = time.time()


@dataclass
class PlaybackRef:
    """Reference to an active audio playback."""
    playback_id: str
    call_id: str
    channel_id: str
    bridge_id: Optional[str]
    media_uri: str
    audio_file: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class CallSession:
    """Complete session state for a call."""
    # Core identifiers
    call_id: str              # canonical (caller_channel_id)
    caller_channel_id: str
    caller_name: Optional[str] = None     # CALLERID(name) for personalized greetings
    caller_number: Optional[str] = None   # CALLERID(num) for context
    local_channel_id: Optional[str] = None
    external_media_id: Optional[str] = None
    external_media_call_id: Optional[str] = None
    external_media_port: Optional[int] = None
    audiosocket_channel_id: Optional[str] = None
    audiosocket_conn_id: Optional[str] = None
    audiosocket_uuid: Optional[str] = None
    provider_session_active: bool = False
    bridge_id: Optional[str] = None
    
    # Provider and conversation state
    provider_name: str = "local"
    pipeline_name: Optional[str] = None
    pipeline_components: Dict[str, str] = field(default_factory=dict)
    context_name: Optional[str] = None  # AI_CONTEXT from dialplan (for pipeline greeting/prompt resolution)
    # Per-call provider config overrides (do NOT mutate global provider templates).
    provider_overrides: Dict[str, Any] = field(default_factory=dict)
    conversation_state: str = "greeting"  # greeting | listening | processing
    status: str = "initializing"
    last_transcript: Optional[str] = None
    last_agent_response: Optional[str] = None
    
    # Conversation tracking for email tools
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    start_time: Optional[datetime] = None
    
    # Audio capture and TTS gating
    audio_capture_enabled: bool = False
    tts_playing: bool = False
    tts_tokens: Set[str] = field(default_factory=set)
    tts_active_count: int = 0
    # TTS timing for barge-in/protection windows
    tts_started_ts: float = 0.0
    tts_ended_ts: float = 0.0
    # Barge-in detection accumulators
    barge_in_candidate_ms: int = 0
    last_barge_in_ts: float = 0.0
    barge_start_ts: float = 0.0
    
    # VAD and audio processing state
    vad_state: Dict[str, Any] = field(default_factory=dict)
    fallback_state: Dict[str, Any] = field(default_factory=dict)
    enhanced_vad_enabled: bool = False
    enhanced_vad_frames: int = 0
    enhanced_vad_speech_frames: int = 0
    last_provider_audio_ts: float = 0.0
    
    # Cleanup and lifecycle
    cleanup_after_tts: bool = False
    cleanup_in_progress: bool = False
    cleanup_completed: bool = False
    pending_local_channel_id: Optional[str] = None
    pending_external_media_id: Optional[str] = None
    ssrc: Optional[int] = None
    
    # Background music (AAVA-89)
    music_snoop_channel_id: Optional[str] = None  # Snoop channel for background music playback
    created_at: float = field(default_factory=time.time)
    agent_audio_buffer: bytearray = field(default_factory=bytearray)
    last_agent_audio_ts: float = 0.0
    # Latency instrumentation (all values in absolute unix seconds)
    last_user_speech_end_ts: float = 0.0
    last_transcription_ts: float = 0.0
    last_agent_response_ts: float = 0.0
    last_response_start_ts: float = 0.0
    # Cached latency readings for observability (seconds)
    last_turn_latency_s: float = 0.0
    last_transcription_latency_s: float = 0.0
    
    # Streaming state and observability
    streaming_ready: bool = False
    streaming_response: bool = False
    streaming_started: bool = False
    current_stream_id: Optional[str] = None
    # Media path confirmation (first inbound audio frame observed)
    media_rx_confirmed: bool = False
    first_media_rx_ts: float = 0.0
    streaming_bytes_sent: int = 0
    streaming_fallback_count: int = 0
    streaming_jitter_buffer_depth: int = 0
    streaming_keepalive_sent: int = 0
    streaming_keepalive_timeouts: int = 0
    last_streaming_error: Optional[str] = None
    caller_audio_format: str = "ulaw"
    caller_sample_rate: int = 8000
    transport_profile: Any = field(default_factory=LegacyTransportProfile)  # OrchestratorTransportProfile compatible
    codec_alignment_ok: bool = True
    codec_alignment_message: Optional[str] = None
    audio_diagnostics: Dict[str, Any] = field(default_factory=dict)
    
    # Agent action tracking (transfers, hangup, etc.)
    pending_actions: list = field(default_factory=list)  # Queue of pending actions
    current_action: Optional[Dict[str, Any]] = None      # Currently executing action
    transfer_context: Optional[Dict[str, Any]] = None    # Context to pass to transfer target
    
    # Call history tracking (Milestone 21)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)  # [{name, params, result, timestamp, duration_ms}]
    turn_latencies_ms: List[float] = field(default_factory=list)    # Per-turn latency tracking
    barge_in_count: int = 0                                          # Total barge-in attempts
    error_message: Optional[str] = None                              # Error if call failed
    transfer_destination: Optional[str] = None                       # Transfer target if transferred

    # Outbound campaign dialer (Milestone 22)
    is_outbound: bool = False
    outbound_campaign_id: Optional[str] = None
    outbound_lead_id: Optional[str] = None
    outbound_attempt_id: Optional[str] = None
    outbound_custom_vars: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Initialize default VAD and fallback state."""
        if not self.vad_state:
            self.vad_state = {
                "state": "listening",
                "speaking": False,
                "speech_real_start_fired": False,
                "pre_roll_buffer": b"",
                "utterance_buffer": b"",
                "utterance_id": 0,
                "last_utterance_end_ms": 0,
                "webrtc_speech_frames": 0,
                "webrtc_silence_frames": 0,
                "webrtc_last_decision": False,
                "audio_buffer": b"",
                "frame_buffer": b"",  # ARCHITECT FIX: Add frame_buffer for 20ms frame buffering
                "frame_count": 0,     # ARCHITECT FIX: Add frame_count for VAD processing
                "last_voice_ms": 0,
                "tts_playing": False,
                "consecutive_silence_frames": 0,
            }
        
        if not self.fallback_state:
            self.fallback_state = {
                "audio_buffer": b"",
                "last_vad_speech_time": time.time(),
                "buffer_start_time": None,
                "frame_count": 0
            }


@dataclass
class ProviderSession:
    """Session state for a provider connection."""
    call_id: str
    provider_name: str
    websocket_connected: bool = False
    input_mode: str = "pcm16_16k"  # pcm16_16k | pcm16_8k
    created_at: float = field(default_factory=time.time)


@dataclass
class TransportConfig:
    """Configuration for ExternalMedia transport settings."""
    transport_type: str = "externalmedia"  # Only ExternalMedia supported
    rtp_host: str = "0.0.0.0"
    rtp_port: int = 18080
    codec: str = "ulaw"
    direction: str = "both"
    # Note: jitter_buffer_ms removed - not used by RTP server
