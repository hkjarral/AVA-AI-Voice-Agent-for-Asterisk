"""
Shared Configuration System for Asterisk AI Voice Agent v2.0

This module provides centralized configuration management for all microservices
using Pydantic v2 for validation and type safety.
"""

import os
import yaml
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
import structlog

# Import configuration helpers (AAVA-40 refactor)
from src.config.loaders import resolve_config_path, load_yaml_with_env_expansion, load_yaml_with_local_override
from src.config.security import (
    inject_asterisk_credentials,
    inject_llm_config,
    inject_provider_api_keys,
)
from src.config.defaults import (
    apply_transport_defaults,
    apply_audiosocket_defaults,
    apply_externalmedia_defaults,
    apply_diagnostic_defaults,
    apply_barge_in_defaults,
)
from src.config.normalization import normalize_pipelines, normalize_profiles, normalize_local_provider_tokens

logger = structlog.get_logger(__name__)

# Determine the absolute path to the project root from this file's location
# This makes the config loading independent of the current working directory.
_PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AsteriskConfig(BaseModel):
    host: str
    port: int = Field(default=8088)
    scheme: str = Field(default="http")  # http or https (https uses wss:// for WebSocket)
    ssl_verify: bool = Field(default=True)  # Set to False to skip SSL certificate verification
    username: str
    password: str
    app_name: str = Field(default="ai-voice-agent")

class ExternalMediaConfig(BaseModel):
    # Network configuration
    rtp_host: str = Field(default="127.0.0.1")  # Bind host: IP the RTP server listens on
    advertise_host: Optional[str] = Field(default=None)  # Advertise host: IP Asterisk sends RTP to (defaults to rtp_host if not set)
    rtp_port: int = Field(default=18080)
    port_range: Optional[str] = Field(default=None)
    
    # Asterisk-side configuration (RTP payload)
    codec: str = Field(default="ulaw")  # Asterisk channel codec: ulaw, alaw, slin, slin16
    direction: str = Field(default="both")  # RTP direction: both, sendonly, recvonly
    
    # Engine-side configuration (internal processing)
    # Defines how RTP server delivers audio to engine/providers
    format: str = Field(default="slin16")  # Engine internal format: slin (8kHz), slin16 (16kHz), ulaw (8kHz)
    sample_rate: Optional[int] = Field(default=None)  # Optional: inferred from format if not set (8000 or 16000)
    
    # Note: jitter_buffer_ms removed - RTP has built-in buffering, not configurable
    # streaming.jitter_buffer_ms controls StreamingPlaybackManager buffering instead

    # Security / deployment hardening:
    # - If set, only accept inbound RTP packets from these source IPs/hosts.
    # - If lock_remote_endpoint is True, do not update remote endpoint mid-call.
    allowed_remote_hosts: Optional[List[str]] = Field(default=None)
    lock_remote_endpoint: bool = Field(default=True)


class AudioSocketConfig(BaseModel):
    host: str = Field(default="127.0.0.1")  # Bind host: IP the AudioSocket server listens on
    advertise_host: Optional[str] = Field(default=None)  # Advertise host: IP Asterisk connects to (defaults to host if not set)
    port: int = Field(default=8090)
    format: str = Field(default="ulaw")  # 'ulaw' or 'slin16'


class LocalProviderConfig(BaseModel):
    enabled: bool = Field(default=True)
    # base_url is preferred for full agent mode (consistent with other providers)
    # ws_url is kept for backward compatibility with modular providers
    base_url: Optional[str] = Field(default=None)
    ws_url: Optional[str] = Field(default="ws://127.0.0.1:8765")
    # Optional WS auth token for local-ai-server.
    auth_token: Optional[str] = None
    connect_timeout_sec: float = Field(default=5.0)
    response_timeout_sec: float = Field(default=5.0)
    # Farewell mode: how to play goodbye message when call ends
    # "tts" - Use local TTS (best for fast hardware with <5s LLM response)
    # "asterisk" - Use Asterisk's built-in goodbye sound (reliable for slow hardware)
    farewell_mode: str = Field(default="asterisk")
    # Farewell TTS timeout - how long to wait for goodbye TTS before hanging up
    # Only used when farewell_mode="tts"
    # Set based on your hardware speed (see LLM warmup time in logs)
    # Fast hardware: 5-10s, Slow hardware: 30-60s
    farewell_timeout_sec: float = Field(default=30.0)
    # Farewell hangup delay - seconds to wait after farewell audio completes before hangup
    # Ensures farewell message fully plays through RTP pipeline before disconnecting
    # Increase if farewell gets cut off (typical farewells need 2-4 seconds)
    farewell_hangup_delay_sec: float = Field(default=5.0)
    chunk_ms: int = Field(default=200)
    max_tokens: int = Field(default=150)
    temperature: float = Field(default=0.4)
    llm_model: Optional[str] = None
    greeting: Optional[str] = None
    instructions: Optional[str] = None
    # Mode for local_ai_server: "full" (STT+LLM+TTS), "stt" (STT only for hybrid pipelines)
    mode: str = Field(default="full")
    
    # STT Backend selection: vosk | kroko | sherpa
    stt_backend: str = Field(default="vosk")
    # Vosk STT model path
    stt_model: Optional[str] = None
    # Kroko STT settings
    kroko_url: Optional[str] = Field(default="wss://app.kroko.ai/api/v1/transcripts/streaming")
    kroko_api_key: Optional[str] = None
    kroko_language: str = Field(default="en-US")
    # Sherpa-ONNX STT model path
    sherpa_model_path: Optional[str] = None
    
    # TTS Backend selection: piper | kokoro
    tts_backend: str = Field(default="piper")
    # Piper TTS voice/model path
    tts_voice: Optional[str] = None
    # Kokoro TTS settings
    kokoro_voice: str = Field(default="af_heart")
    kokoro_lang: str = Field(default="a")
    kokoro_model_path: Optional[str] = None
    
    @property
    def effective_ws_url(self) -> str:
        """Return base_url if set, otherwise ws_url."""
        return self.base_url or self.ws_url or "ws://127.0.0.1:8765"


class DeepgramProviderConfig(BaseModel):
    api_key: Optional[str] = None
    enabled: bool = Field(default=True)
    model: str = Field(default="nova-2-general")
    tts_model: str = Field(default="aura-asteria-en")
    greeting: Optional[str] = None
    instructions: Optional[str] = None
    input_encoding: str = Field(default="mulaw")
    input_sample_rate_hz: int = Field(default=8000)
    input_gain_target_rms: int = Field(default=0)
    input_gain_max_db: float = Field(default=0.0)
    continuous_input: bool = Field(default=True)
    output_encoding: str = Field(default="mulaw")
    output_sample_rate_hz: int = Field(default=8000)
    allow_output_autodetect: bool = Field(default=False)
    base_url: str = Field(default="https://api.deepgram.com")
    tts_voice: Optional[str] = None
    stt_language: str = Field(default="en-US")
    # Deepgram Voice Agent language (for full agent mode)
    # Supported: en, en-US, es, fr, de, it, pt, nl, ja, zh, ko, hi, ru, pl, uk, tr, sv, da, no, fi, cs, el, he, ar, id, ms, th, vi
    agent_language: str = Field(default="en")
    # Deepgram Voice Agent (monolithic) WebSocket endpoint
    voice_agent_base_url: str = Field(
        default="wss://agent.deepgram.com/v1/agent/converse"
    )
    # Provider-specific farewell hangup delay (overrides global)
    farewell_hangup_delay_sec: Optional[float] = None


class OpenAIProviderConfig(BaseModel):
    """# Milestone7: Canonical defaults for OpenAI pipeline adapters."""
    api_key: Optional[str] = None
    organization: Optional[str] = None
    project: Optional[str] = None
    tools_enabled: bool = Field(default=True)
    # "ga" = GA Realtime API (no beta header, gpt-realtime models)
    # "beta" = Beta Realtime API (OpenAI-Beta header, gpt-4o-realtime-preview models)
    # Default to beta for widest account compatibility out-of-box.
    api_version: str = Field(default="beta")
    realtime_base_url: str = Field(default="wss://api.openai.com/v1/realtime")
    chat_base_url: str = Field(default="https://api.openai.com/v1")
    stt_base_url: str = Field(default="https://api.openai.com/v1/audio/transcriptions")
    tts_base_url: str = Field(default="https://api.openai.com/v1/audio/speech")
    realtime_model: str = Field(default="gpt-4o-realtime-preview-2024-12-17")
    chat_model: str = Field(default="gpt-4o-mini")
    stt_model: str = Field(default="whisper-1")
    # NOTE: Default to widely-available TTS model to avoid silent-call failures when
    # accounts don't have access to newer/limited models.
    tts_model: str = Field(default="tts-1")
    voice: str = Field(default="alloy")
    tts_response_format: str = Field(default="wav")
    default_modalities: List[str] = Field(default_factory=lambda: ["text"])
    input_encoding: str = Field(default="linear16")
    input_sample_rate_hz: int = Field(default=24000)
    target_encoding: str = Field(default="mulaw")
    target_sample_rate_hz: int = Field(default=8000)
    chunk_size_ms: int = Field(default=20)
    response_timeout_sec: float = Field(default=5.0)
    # Provider-specific farewell hangup delay (overrides global)
    farewell_hangup_delay_sec: Optional[float] = None


class GoogleProviderConfig(BaseModel):
    api_key: Optional[str] = None
    project_id: Optional[str] = None
    stt_base_url: str = Field(default="https://speech.googleapis.com/v1")
    tts_base_url: str = Field(default="https://texttospeech.googleapis.com/v1")
    llm_base_url: str = Field(default="https://generativelanguage.googleapis.com/v1")
    stt_language_code: str = Field(default="en-US")
    tts_voice_name: str = Field(default="en-US-Neural2-C")
    tts_audio_encoding: str = Field(default="MULAW")
    tts_sample_rate_hz: int = Field(default=8000)
    llm_model: str = Field(default="models/gemini-1.5-pro-latest")
    greeting: Optional[str] = None  # For Google Live API initial greeting
    instructions: Optional[str] = None  # System prompt/instructions for Google Live API
    enabled: bool = Field(default=True)  # Provider enabled flag
    
    # Google Live LLM generation configuration
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)  # Temperature for response generation
    llm_max_output_tokens: int = Field(default=8192, ge=1, le=8192)  # Max output tokens (Gemini supports up to 8192)
    llm_top_p: float = Field(default=0.95, ge=0.0, le=1.0)  # Nucleus sampling parameter
    llm_top_k: int = Field(default=40, ge=1, le=100)  # Top-k sampling parameter
    
    # Google Live response configuration
    response_modalities: str = Field(default="audio")  # "audio", "text", or "audio_text"
    
    # Google Live transcription configuration (for email summaries/conversation history)
    enable_input_transcription: bool = Field(default=True)  # Enable user speech transcription
    enable_output_transcription: bool = Field(default=True)  # Enable AI speech transcription
    
    # Google Live audio format configuration (aligns with OpenAI Realtime pattern)
    input_encoding: str = Field(default="ulaw")  # Wire format from AudioSocket/RTP (ulaw/slin16)
    input_sample_rate_hz: int = Field(default=8000)  # Wire sample rate
    provider_input_encoding: str = Field(default="linear16")  # Gemini Live expects PCM16
    provider_input_sample_rate_hz: int = Field(default=16000)  # Gemini Live input rate
    input_gain_target_rms: int = Field(default=0)
    input_gain_max_db: float = Field(default=0.0)
    output_encoding: str = Field(default="linear16")  # Gemini Live outputs PCM16
    output_sample_rate_hz: int = Field(default=24000)  # Gemini Live native output rate
    target_encoding: str = Field(default="ulaw")  # Target wire format for playback
    target_sample_rate_hz: int = Field(default=8000)  # Target wire sample rate
    # Google Live WebSocket endpoint (monolithic agent)
    websocket_endpoint: str = Field(
        default="wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    )
    # Provider-specific farewell hangup delay (overrides global)
    farewell_hangup_delay_sec: Optional[float] = None
    # Fallback watchdog tuning (Google Live only)
    hangup_fallback_audio_idle_sec: float = Field(default=1.25)
    hangup_fallback_min_armed_sec: float = Field(default=0.8)
    hangup_fallback_no_audio_timeout_sec: float = Field(default=4.0)
    # Guard against premature fallback hangup before the provider emits turnComplete.
    # If turnComplete never arrives, fallback still proceeds after this timeout.
    hangup_fallback_turn_complete_timeout_sec: float = Field(default=2.5)
    # Google Live only: heuristic hangup detection based on transcript markers (end_call / assistant_farewell).
    # For production, prefer tool-driven hangup (`hangup_call`) to avoid marker-driven premature cleanup.
    hangup_markers_enabled: bool = Field(default=False)
    # Google Live only: protocol-level WebSocket ping keepalive.
    # NOTE: Google Live typically receives continuous `realtimeInput` audio frames; pings are only needed
    # if the transport becomes idle. Some accounts/models appear to close connections (1008) after repeated
    # ping frames, so we default to pinging only when idle (see provider implementation).
    ws_keepalive_enabled: bool = Field(default=False)
    ws_keepalive_interval_sec: float = Field(default=15.0)
    ws_keepalive_idle_sec: float = Field(default=5.0)


class GroqSTTProviderConfig(BaseModel):
    """Groq Speech-to-Text (OpenAI-compatible audio/transcriptions + audio/translations)."""

    enabled: bool = Field(default=True)
    api_key: Optional[str] = None
    stt_base_url: str = Field(default="https://api.groq.com/openai/v1/audio/transcriptions")
    # Groq STT supported models: whisper-large-v3-turbo, whisper-large-v3
    stt_model: str = Field(default="whisper-large-v3-turbo")
    language: Optional[str] = None  # ISO-639-1 (e.g., en, tr)
    prompt: Optional[str] = None
    response_format: str = Field(default="json")  # json | verbose_json | text
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    timestamp_granularities: Optional[List[str]] = None  # ["segment"] and/or ["word"] (requires verbose_json)
    request_timeout_sec: float = Field(default=15.0)


class GroqTTSProviderConfig(BaseModel):
    """Groq Text-to-Speech (OpenAI-compatible audio/speech; Orpheus models)."""

    enabled: bool = Field(default=True)
    api_key: Optional[str] = None
    tts_base_url: str = Field(default="https://api.groq.com/openai/v1/audio/speech")
    # Groq TTS supported models: canopylabs/orpheus-v1-english, canopylabs/orpheus-arabic-saudi
    tts_model: str = Field(default="canopylabs/orpheus-v1-english")
    voice: str = Field(default="hannah")
    response_format: str = Field(default="wav")  # Orpheus docs: only supported response_format is wav.
    # Orpheus docs: input max 200 characters; adapter should chunk longer strings.
    max_input_chars: int = Field(default=200, ge=1)
    # Output format expected by downstream playback
    target_encoding: str = Field(default="mulaw")
    target_sample_rate_hz: int = Field(default=8000)
    chunk_size_ms: int = Field(default=20)
    request_timeout_sec: float = Field(default=15.0)


class ElevenLabsProviderConfig(BaseModel):
    """ElevenLabs TTS provider configuration.
    
    API Reference: https://elevenlabs.io/docs/api-reference/text-to-speech
    """
    enabled: bool = Field(default=True)
    api_key: Optional[str] = None
    # Default voice: Rachel (warm, professional)
    voice_id: str = Field(default="21m00Tcm4TlvDq8ikWAM")
    model_id: str = Field(default="eleven_turbo_v2_5")  # Fast, high-quality
    base_url: str = Field(default="https://api.elevenlabs.io/v1")
    # Audio settings
    output_format: str = Field(default="ulaw_8000")  # ulaw_8000, mp3_44100, pcm_16000, etc.
    # Voice settings
    stability: float = Field(default=0.5)
    similarity_boost: float = Field(default=0.75)
    style: float = Field(default=0.0)
    use_speaker_boost: bool = Field(default=True)
    # Provider-specific farewell hangup delay (overrides global)
    farewell_hangup_delay_sec: Optional[float] = None


class MCPToolConfig(BaseModel):
    """Configuration for a single MCP-backed tool exposed to the LLM."""

    name: str
    expose_as: Optional[str] = None  # Provider-safe name (e.g., mcp_weather_get_forecast)
    description: Optional[str] = None

    # Voice UX
    speech_field: Optional[str] = None
    speech_template: Optional[str] = None

    # Timing / UX
    timeout_ms: Optional[int] = None
    slow_response_threshold_ms: Optional[int] = None
    slow_response_message: Optional[str] = None


class MCPServerRestartConfig(BaseModel):
    enabled: bool = Field(default=True)
    max_restarts: int = Field(default=5, ge=0)
    backoff_ms: int = Field(default=1000, ge=0)


class MCPServerDefaultsConfig(BaseModel):
    timeout_ms: int = Field(default=10000, ge=1)
    slow_response_threshold_ms: int = Field(default=0, ge=0)
    slow_response_message: str = Field(default="Let me look that up for you, one moment...")


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    enabled: bool = Field(default=True)
    transport: str = Field(default="stdio")  # currently: stdio
    command: List[str] = Field(default_factory=list)  # e.g., ["python3", "-m", "my_mcp_server"]
    cwd: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)
    restart: MCPServerRestartConfig = Field(default_factory=MCPServerRestartConfig)
    defaults: MCPServerDefaultsConfig = Field(default_factory=MCPServerDefaultsConfig)
    tools: List[MCPToolConfig] = Field(default_factory=list)  # optional allowlist; if empty => expose all discovered


class MCPConfig(BaseModel):
    enabled: bool = Field(default=False)
    servers: Dict[str, MCPServerConfig] = Field(default_factory=dict)


class OpenAIRealtimeProviderConfig(BaseModel):
    enabled: bool = Field(default=True)
    api_key: Optional[str] = None
    # "ga" = GA Realtime API (no beta header, gpt-realtime models)
    # "beta" = Beta Realtime API (OpenAI-Beta header, gpt-4o-realtime-preview models)
    # Default to beta for widest account compatibility out-of-box.
    api_version: str = Field(default="beta")
    model: str = Field(default="gpt-4o-realtime-preview-2024-12-17")
    voice: str = Field(default="alloy")
    base_url: str = Field(default="wss://api.openai.com/v1/realtime")
    instructions: Optional[str] = None
    organization: Optional[str] = None
    project_id: Optional[str] = None  # OpenAI project ID for usage tracking (OpenAI-Project header)
    input_encoding: str = Field(default="slin16")  # AudioSocket inbound default (8 kHz PCM16)
    input_sample_rate_hz: int = Field(default=8000)  # AudioSocket source sample rate
    provider_input_encoding: str = Field(default="linear16")  # Provider expects PCM16 LE
    provider_input_sample_rate_hz: int = Field(default=24000)  # OpenAI Realtime input sample rate
    input_gain_target_rms: int = Field(default=0)
    input_gain_max_db: float = Field(default=0.0)
    output_encoding: str = Field(default="linear16")  # Provider emits PCM16 frames
    output_sample_rate_hz: int = Field(default=24000)
    target_encoding: str = Field(default="ulaw")  # Downstream AudioSocket expectations
    target_sample_rate_hz: int = Field(default=8000)
    response_modalities: List[str] = Field(default_factory=lambda: ["text", "audio"])
    egress_pacer_enabled: bool = Field(default=False)
    egress_pacer_warmup_ms: int = Field(default=320)
    # Optional explicit greeting to speak immediately on connect
    greeting: Optional[str] = None
    # Optional server-side turn detection configuration
    # If provided, will be sent in session.update
    class TurnDetectionConfig(BaseModel):
        type: str = Field(default="server_vad")
        silence_duration_ms: int = Field(default=200)
        threshold: float = Field(default=0.5)
        prefix_padding_ms: int = Field(default=200)

    turn_detection: Optional[TurnDetectionConfig] = None

class BargeInConfig(BaseModel):
    enabled: bool = Field(default=True)
    initial_protection_ms: int = Field(default=200)
    min_ms: int = Field(default=250)
    energy_threshold: int = Field(default=1000)
    cooldown_ms: int = Field(default=500)
    # Pipeline (hybrid/local) barge-in tuning: pipelines play TTS locally (file playback),
    # so we can use a more sensitive detector without colliding with provider-owned VAD.
    pipeline_min_ms: int = Field(default=120)
    pipeline_energy_threshold: int = Field(default=300)
    # Pipelines: prefer Asterisk-side talk detection (TALK_DETECT) for robust barge-in,
    # because ExternalMedia RTP can be paused/altered during channel playback.
    pipeline_talk_detect_enabled: bool = Field(default=True)
    # TALK_DETECT(set)=<dsp_silence_threshold_ms>,<dsp_talking_threshold>
    pipeline_talk_detect_silence_ms: int = Field(default=1200)
    pipeline_talk_detect_talking_threshold: int = Field(default=128)
    # New: short guard window after TTS ends to avoid self-echo re-capture
    post_tts_end_protection_ms: int = Field(default=250)
    # Extra protection during the first greeting turn
    greeting_protection_ms: int = Field(default=0)
    # Provider-owned mode: local VAD fallback only for providers that don't emit explicit interruption events.
    provider_fallback_enabled: bool = Field(default=True)
    provider_fallback_providers: List[str] = Field(default_factory=lambda: ["google_live", "deepgram"])
    # Provider-owned mode: suppress outbound provider audio locally after barge-in so continuing provider audio
    # doesn't immediately restart streaming playback.
    provider_output_suppress_ms: int = Field(default=1200)
    provider_output_suppress_extend_ms: int = Field(default=600)
    # While suppressed, extend the suppression window when provider chunks keep arriving.
    # This prevents "tail resume" if a provider keeps streaming already-generated audio after barge-in.
    provider_output_suppress_chunk_extend_ms: int = Field(default=250)


class LLMConfig(BaseModel):
    # Defaults are generic; inject_llm_config() applies YAML/env precedence.
    initial_greeting: str = "Hello, how can I help you today?"
    prompt: str = "You are a helpful assistant."
    # Note: model field removed - not used by any provider (each provider has its own model config)
    api_key: Optional[str] = None


class VADConfig(BaseModel):
    use_provider_vad: bool = Field(default=False)
    enhanced_enabled: bool = Field(default=False)
    # WebRTC VAD settings - optimized for real-time conversation
    webrtc_aggressiveness: int = 1
    webrtc_start_frames: int = 2
    webrtc_end_silence_frames: int = 15
    # Enhanced VAD thresholds
    energy_threshold: int = 1500
    confidence_threshold: float = 0.6
    adaptive_threshold_enabled: bool = True
    noise_adaptation_rate: float = 0.1
    
    # Utterance settings - optimized for real-time conversation
    min_utterance_duration_ms: int = 800
    max_utterance_duration_ms: int = 8000
    utterance_padding_ms: int = 100
    
    # Fallback settings
    fallback_enabled: bool = True
    fallback_interval_ms: int = 1500
    fallback_buffer_size: int = 128000

    # Provider-agnostic upstream squelch (helps server-side VAD providers terminate turns in noisy environments).
    # Applies only to providers that require continuous audio.
    upstream_squelch_enabled: bool = True
    # Base RMS threshold in PCM16 space. Lower values are more permissive (less likely to clip quiet speakers).
    upstream_squelch_base_rms: int = 200
    # Dynamic threshold multiplier: threshold = max(base_rms, noise_floor_rms * noise_factor)
    upstream_squelch_noise_factor: float = 2.5
    # Exponential moving average factor for noise floor updates (0..1). Higher adapts faster.
    upstream_squelch_noise_ema_alpha: float = 0.06
    # Hysteresis: require N speech frames to enter speaking state, and M silence frames to exit.
    upstream_squelch_min_speech_frames: int = 2
    upstream_squelch_end_silence_frames: int = 15


class StreamingConfig(BaseModel):
    sample_rate: int = Field(default=8000)
    jitter_buffer_ms: int = Field(default=50)
    keepalive_interval_ms: int = Field(default=5000)
    connection_timeout_ms: int = Field(default=10000)
    fallback_timeout_ms: int = Field(default=4000)
    chunk_size_ms: int = Field(default=20)
    min_start_ms: int = Field(default=120)
    low_watermark_ms: int = Field(default=80)
    provider_grace_ms: int = Field(default=500)
    logging_level: str = Field(default="info")
    # Smaller warm-up only for the initial greeting to get first audio out sooner
    greeting_min_start_ms: int = Field(default=0)
    # ExternalMedia-specific: safety net timeout (ms) for RTP endpoint establishment.
    # With RTP kick fix, RTP establishes in ~40-50ms. This fallback rarely triggers.
    greeting_rtp_wait_ms: int = Field(default=1000)
    # Egress endianness control for PCM16 slin16 over AudioSocket: 'auto'|'force_true'|'force_false'
    # - auto: derive from inbound probe (current behavior)
    # - force_true: always byteswap outbound PCM16
    # - force_false: never byteswap outbound PCM16 (send native LE)
    egress_swap_mode: str = Field(default="auto")
    # When true, force outbound streaming audio to μ-law regardless of provider encoding.
    egress_force_mulaw: bool = Field(default=False)


class LoggingConfig(BaseModel):
    """Top-level logging configuration for the ai-engine service."""
    level: str = Field(default="info")  # debug|info|warning|error|critical


class HealthConfig(BaseModel):
    """Health/metrics HTTP endpoint configuration."""
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=15000)


class PipelineEntry(BaseModel):
    stt: str
    llm: str
    tts: str
    tools: List[str] = Field(default_factory=list)
    options: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


# Milestone7: Compose canonical component names for provider-backed pipelines.
def _compose_provider_components(provider: str) -> Dict[str, Any]:
    return {
        "stt": f"{provider}_stt",
        "llm": f"{provider}_llm",
        "tts": f"{provider}_tts",
        "tools": [],
        "options": {}
    }


# Milestone7: Normalize pipeline definitions into the PipelineEntry schema.
def _normalize_pipelines(config_data: Dict[str, Any]) -> None:
    default_provider = config_data.get("default_provider", "openai_realtime")
    pipelines_cfg = config_data.get("pipelines")

    if not pipelines_cfg:
        _generate_default_pipeline(config_data)
        return

    normalized: Dict[str, Dict[str, Any]] = {}

    for pipeline_name, raw_entry in pipelines_cfg.items():
        if raw_entry is None:
            normalized[pipeline_name] = _compose_provider_components(default_provider)
            continue

        if isinstance(raw_entry, str):
            normalized[pipeline_name] = _compose_provider_components(raw_entry)
            continue

        if isinstance(raw_entry, dict):
            provider_hint = raw_entry.get("provider")
            provider_for_defaults = provider_hint or default_provider
            components = _compose_provider_components(provider_for_defaults)

            options_block = raw_entry.get("options") or {}
            if not isinstance(options_block, dict):
                raise TypeError(
                    f"Unsupported pipeline options type for '{pipeline_name}': {type(options_block).__name__}"
                )

            normalized_entry = {
                "stt": raw_entry.get("stt", components["stt"]),
                "llm": raw_entry.get("llm", components["llm"]),
                "tts": raw_entry.get("tts", components["tts"]),
                "tools": raw_entry.get("tools") or [],
                "options": options_block,
            }

            normalized[pipeline_name] = normalized_entry
            continue

        raise TypeError(f"Unsupported pipeline definition for '{pipeline_name}': {type(raw_entry).__name__}")

    config_data["pipelines"] = normalized
    config_data.setdefault("active_pipeline", next(iter(normalized.keys())))


class AppConfig(BaseModel):
    # Config schema marker used by migration tooling and release docs.
    config_version: int = Field(default=6, ge=1)
    default_provider: str
    providers: Dict[str, Any]
    asterisk: AsteriskConfig
    llm: LLMConfig
    audio_transport: str = Field(default="externalmedia")  # 'externalmedia' | 'legacy'
    downstream_mode: str = Field(default="stream")  # 'file' | 'stream'
    external_media: Optional[ExternalMediaConfig] = Field(default_factory=ExternalMediaConfig)
    audiosocket: Optional[AudioSocketConfig] = Field(default_factory=AudioSocketConfig)
    vad: Optional[VADConfig] = Field(default_factory=VADConfig)
    streaming: Optional[StreamingConfig] = Field(default_factory=StreamingConfig)
    barge_in: Optional[BargeInConfig] = Field(default_factory=BargeInConfig)
    logging: Optional[LoggingConfig] = Field(default_factory=LoggingConfig)
    health: Optional[HealthConfig] = Field(default_factory=HealthConfig)
    pipelines: Dict[str, PipelineEntry] = Field(default_factory=dict)
    active_pipeline: Optional[str] = None
    # P1: profiles/contexts for transport orchestration
    profiles: Dict[str, Any] = Field(default_factory=dict)
    contexts: Dict[str, Any] = Field(default_factory=dict)
    # Tool calling configuration (v4.1)
    tools: Dict[str, Any] = Field(default_factory=dict)
    # In-call HTTP tool definitions (Milestone 24)
    # Admin UI stores AI-invokable HTTP tool configs under `in_call_tools:`.
    in_call_tools: Dict[str, Any] = Field(default_factory=dict)
    # MCP tool configuration (experimental)
    mcp: Optional[MCPConfig] = None
    # Farewell hangup delay - seconds to wait after farewell audio completes before hangup
    # Ensures farewell message fully plays through RTP pipeline before disconnecting
    # Increase if farewell gets cut off (typical farewells need 2-4 seconds)
    farewell_hangup_delay_sec: float = Field(default=5.0)

    # Ensure tests that construct AppConfig(**dict) directly still get normalized pipelines
    # similar to load_config(), which calls _normalize_pipelines().
    from pydantic import model_validator  # local import to keep top clear

    @model_validator(mode="before")
    @classmethod
    def _normalize_before(cls, data: Any):  # type: ignore[override]
        try:
            if isinstance(data, dict):
                _normalize_pipelines(data)
        except Exception as e:
            # Non-fatal: if normalization fails, Pydantic will raise a more specific error later
            logger.debug("Pipeline normalization failed (will be caught by Pydantic)", error=str(e))
            pass
        return data

def _generate_default_pipeline(config_data: Dict[str, Any]) -> None:
    """Populate a default pipeline entry when none are provided."""
    default_provider = config_data.get("default_provider", "openai_realtime")
    pipeline_name = "default"
    # Milestone7: Align implicit defaults with the PipelineEntry schema.
    default_components = _compose_provider_components(default_provider)

    pipelines = config_data.setdefault("pipelines", {})
    existing_entry = pipelines.get(pipeline_name)

    if existing_entry is None:
        pipelines[pipeline_name] = _compose_provider_components(default_provider)
    elif isinstance(existing_entry, str):
        pipelines[pipeline_name] = _compose_provider_components(existing_entry)
    elif isinstance(existing_entry, dict):
        existing_entry.setdefault("stt", default_components["stt"])
        existing_entry.setdefault("llm", default_components["llm"])
        existing_entry.setdefault("tts", default_components["tts"])
        if not isinstance(existing_entry.get("options"), dict):
            existing_entry["options"] = {}
    else:
        pipelines[pipeline_name] = _compose_provider_components(default_provider)

    config_data.setdefault("active_pipeline", pipeline_name)


def load_config(path: str = "config/ai-agent.yaml") -> AppConfig:
    """
    Load and validate configuration from YAML file.
    
    AAVA-40: Refactored to use dedicated helper functions for improved
    testability and reduced complexity (was 250 lines, now <30).
    
    Args:
        path: Path to YAML configuration file (absolute or relative to project root)
        
    Returns:
        Validated AppConfig instance
        
    Raises:
        FileNotFoundError: If configuration file doesn't exist
        yaml.YAMLError: If YAML parsing fails
        
    Complexity: 5 (down from ~20)
    """
    # Phase 1: Load YAML file with environment variable expansion and local overrides
    path = resolve_config_path(path)
    config_data = load_yaml_with_local_override(path)
    if isinstance(config_data, dict):
        config_data.setdefault("config_version", 6)

    # Backward compatibility: older docs/configs used `in_call_http_tools` at the top-level.
    # Canonical key is now `in_call_tools` (dict of tool_name -> config).
    try:
        if (
            isinstance(config_data, dict)
            and isinstance(config_data.get("in_call_http_tools"), dict)
            and not isinstance(config_data.get("in_call_tools"), dict)
        ):
            logger.warning(
                "Config uses deprecated top-level `in_call_http_tools`; migrating to `in_call_tools`",
            )
            config_data["in_call_tools"] = config_data.pop("in_call_http_tools")
    except Exception:
        logger.debug("Failed normalizing in-call tool key alias", exc_info=True)

    # Phase 2: Security - Inject credentials from environment variables only
    inject_asterisk_credentials(config_data)
    inject_llm_config(config_data)
    inject_provider_api_keys(config_data)

    # Phase 2b: Merge external context YAML files (config/contexts/*.yaml)
    try:
        _merge_external_contexts(config_data)
    except Exception as e:
        # Non-fatal; log debug and continue with inline contexts only
        logger.debug("External context merge failed", error=str(e))
    
    # Phase 3: Apply default values
    apply_transport_defaults(config_data)
    apply_audiosocket_defaults(config_data)
    apply_externalmedia_defaults(config_data)
    apply_diagnostic_defaults(config_data)
    apply_barge_in_defaults(config_data)
    
    # Phase 4: Normalize configuration
    normalize_pipelines(config_data)
    normalize_profiles(config_data)
    normalize_local_provider_tokens(config_data)
    
    # Phase 4b: Validate normalized configuration
    from src.config.normalization import validate_providers, validate_pipelines, ConfigValidationError
    try:
        validate_providers(config_data)
        validate_pipelines(config_data)
    except ConfigValidationError as e:
        logger.warning("Configuration validation warning", error=str(e))
        # Log warning but don't fail - allow backward compatibility
    
    # Phase 5: Validate and return
    return AppConfig(**config_data)


def _merge_external_contexts(config_data: Dict[str, Any]) -> None:
    """
    Merge contexts from config/contexts/*.yaml into config_data['contexts'].

    Precedence:
    - Inline contexts in ai-agent.yaml win over external files on key collision.
    - External context files must define a 'name' field used as the context key.
    - 'system_prompt' in external files is mapped to 'prompt' if 'prompt' is absent.
    """
    try:
        import glob

        contexts_dir = os.path.join(_PROJ_DIR, "config", "contexts")
        if not os.path.isdir(contexts_dir):
            return

        # Start from any existing inline contexts
        existing_contexts = config_data.get("contexts") or {}
        if not isinstance(existing_contexts, dict):
            existing_contexts = {}

        pattern_yaml = os.path.join(contexts_dir, "*.yaml")
        pattern_yml = os.path.join(contexts_dir, "*.yml")
        files = glob.glob(pattern_yaml) + glob.glob(pattern_yml)

        for ctx_path in files:
            try:
                with open(ctx_path, "r") as f:
                    raw = f.read()
                raw = os.path.expandvars(raw)
                ctx_data = yaml.safe_load(raw) or {}
            except Exception:
                continue

            if not isinstance(ctx_data, dict):
                continue

            name = ctx_data.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip()

            # Map system_prompt → prompt if prompt not explicitly provided
            if "prompt" not in ctx_data and "system_prompt" in ctx_data:
                ctx_data["prompt"] = ctx_data["system_prompt"]

            # Only add external context if not already defined inline
            if name not in existing_contexts:
                existing_contexts[name] = ctx_data

        config_data["contexts"] = existing_contexts
    except Exception:
        # Let caller decide how to handle/log; keep non-fatal here.
        raise

def validate_production_config(config: AppConfig) -> tuple[list[str], list[str]]:
    """Validate configuration for production deployment (AAVA-21).
    
    Args:
        config: AppConfig instance to validate
        
    Returns:
        (errors, warnings): Lists of validation errors and warnings
        
    Errors block startup, warnings are logged but non-blocking.
    """
    errors = []
    warnings = []
    
    # Critical checks (errors)
    try:
        # Config version guidance (non-blocking): allows older configs but makes drift explicit.
        config_version = getattr(config, "config_version", 6)
        if config_version < 6:
            warnings.append(
                f"config_version={config_version} is older than the v6 baseline (6); "
                "review release migration notes before production rollout"
            )

        # VAD configuration consistency
        if hasattr(config, 'vad') and config.vad:
            if getattr(config.vad, 'enhanced_enabled', False):
                if not hasattr(config.vad, 'webrtc_aggressiveness') or config.vad.webrtc_aggressiveness is None:
                    errors.append("VAD enabled but webrtc_aggressiveness not set")
        
        # AudioSocket format validation
        if hasattr(config, 'audiosocket') and config.audiosocket:
            format_val = getattr(config.audiosocket, 'format', None)
            if format_val and format_val not in ['slin', 'slin16', 'slin24', 'ulaw', 'alaw']:
                errors.append(f"Invalid audiosocket format: {format_val} (must be slin, slin16, slin24, ulaw, or alaw)")
        
        # Provider API keys validation (non-blocking for local-only setups)
        has_openai = bool(os.getenv('OPENAI_API_KEY'))
        has_deepgram = bool(os.getenv('DEEPGRAM_API_KEY'))
        has_google = bool(os.getenv('GOOGLE_API_KEY'))
        providers_in_use = set()
        try:
            for entry in (getattr(config, 'pipelines', {}) or {}).values():
                stt = getattr(entry, 'stt', None) if not isinstance(entry, dict) else entry.get('stt')
                llm = getattr(entry, 'llm', None) if not isinstance(entry, dict) else entry.get('llm')
                tts = getattr(entry, 'tts', None) if not isinstance(entry, dict) else entry.get('tts')
                for name in (stt, llm, tts):
                    if not name:
                        continue
                    lower = str(name).lower()
                    if lower.startswith('local'):
                        continue
                    providers_in_use.add(lower)
        except Exception:
            pass

        if providers_in_use and not (has_openai or has_deepgram or has_google):
            warnings.append(
                "No provider API keys configured; pipelines referencing non-local providers will fall back to placeholders or fail."
            )
        
        # Port validation
        if hasattr(config, 'audiosocket') and config.audiosocket:
            port = getattr(config.audiosocket, 'port', None)
            if port and (port < 1024 or port > 65535):
                errors.append(f"AudioSocket port {port} out of valid range (1024-65535)")
        
        # Production warnings (non-blocking)
        log_level = os.getenv('LOG_LEVEL', 'info').lower()
        if log_level == 'debug':
            warnings.append("Debug logging enabled (security/performance risk in production)")
        # Streaming logging verbosity warnings
        try:
            streaming_log_level = os.getenv('STREAMING_LOG_LEVEL', 'info').lower()
            if streaming_log_level == 'debug':
                warnings.append("Streaming log level is DEBUG (increases log volume; set STREAMING_LOG_LEVEL=info for production)")
        except Exception as e:
            logger.debug("Failed to check streaming log level", error=str(e))
            pass
        
        # Streaming configuration warnings
        if hasattr(config, 'streaming') and config.streaming:
            jitter_buffer = getattr(config.streaming, 'jitter_buffer_ms', 100)
            if jitter_buffer < 100:
                warnings.append(f"Jitter buffer very small: {jitter_buffer}ms (recommend >= 150ms for production)")
            elif jitter_buffer > 1000:
                warnings.append(f"Jitter buffer very large: {jitter_buffer}ms (adds latency, consider reducing)")
        
        # Binding exposure warnings
        try:
            if hasattr(config, 'audiosocket') and config.audiosocket:
                if getattr(config.audiosocket, 'host', None) == '0.0.0.0':
                    warnings.append("AudioSocket bound to 0.0.0.0; ensure firewall/segmentation is in place")
            if hasattr(config, 'external_media') and config.external_media:
                if getattr(config.external_media, 'rtp_host', None) == '0.0.0.0':
                    warnings.append("ExternalMedia RTP bound to 0.0.0.0; ensure firewall/segmentation is in place")
        except Exception as e:
            logger.debug("Failed to check bind addresses", error=str(e))
            pass

        # Check for deprecated/test settings
        if hasattr(config, 'streaming') and config.streaming:
            if hasattr(config.streaming, 'diag_enable_taps'):
                if getattr(config.streaming, 'diag_enable_taps', False):
                    warnings.append("Diagnostic taps enabled (performance impact, disable in production)")

        # Transport/provider compatibility warnings (non-blocking)
        try:
            providers = getattr(config, "providers", {}) or {}
            # Ensure providers is dict-like
            if not isinstance(providers, dict):
                providers = {}

            # Audio transport vs provider/pipeline availability
            audio_transport = getattr(config, "audio_transport", "externalmedia")
            
            # Check for monolithic providers
            monolithic_names = ("openai_realtime", "deepgram", "google_live")
            monolithic_enabled = []
            for name, cfg in providers.items():
                if name not in monolithic_names:
                    continue
                enabled = True
                if isinstance(cfg, dict):
                    enabled = bool(cfg.get("enabled", True))
                monolithic_enabled.append((name, enabled))
            has_monolithic = any(enabled for _, enabled in monolithic_enabled)
            
            # Check for pipelines
            pipelines = getattr(config, "pipelines", {}) or {}
            if not isinstance(pipelines, dict):
                pipelines = {}
            has_pipelines = bool(pipelines)
            
            # Warn if transport has neither providers nor pipelines to use
            if audio_transport == "audiosocket":
                if not has_monolithic and not has_pipelines:
                    warnings.append(
                        "audio_transport=audiosocket but neither monolithic providers "
                        "(openai_realtime, deepgram, google_live) nor pipelines are configured; "
                        "AudioSocket requires at least one provider type to function"
                    )
            
            if audio_transport == "externalmedia":
                if not has_monolithic and not has_pipelines:
                    warnings.append(
                        "audio_transport=externalmedia but neither monolithic providers "
                        "nor pipelines are configured; ExternalMedia requires at least one provider type"
                    )

                # ExternalMedia RTP security: require explicit allowlist when ASTERISK host is not an IP literal.
                try:
                    if getattr(config, "external_media", None):
                        allowed = getattr(config.external_media, "allowed_remote_hosts", None)
                        allowed_list = [str(x).strip() for x in (allowed or []) if str(x).strip()]
                        asterisk_host = str(getattr(getattr(config, "asterisk", None), "host", "") or "").strip()

                        import ipaddress  # local import to avoid global dependency assumptions

                        asterisk_host_is_ip = False
                        try:
                            ipaddress.ip_address(asterisk_host)
                            asterisk_host_is_ip = True
                        except Exception:
                            asterisk_host_is_ip = False

                        # If `asterisk.host` is a hostname, we cannot safely auto-allowlist (DNS changes / ambiguity).
                        # Require explicit IP allowlist to prevent first-packet hijack.
                        if not asterisk_host_is_ip and not allowed_list:
                            errors.append(
                                "external_media.allowed_remote_hosts is required when asterisk.host is a hostname; "
                                "set it to one or more IP addresses allowed to send RTP (e.g., your Asterisk server IP)."
                            )

                        # Validate allowlist entries are IP literals (RTP source address is always an IP).
                        for entry in allowed_list:
                            try:
                                ipaddress.ip_address(entry)
                            except Exception:
                                errors.append(
                                    f"external_media.allowed_remote_hosts contains non-IP entry '{entry}'; "
                                    "use IP literals only (e.g., '192.0.2.10')."
                                )
                except Exception as e:
                    logger.debug("ExternalMedia allowlist validation failed", error=str(e))
                
                # Optional: downstream_mode hint for ExternalMedia + pipelines
                if has_pipelines:
                    downstream_mode = getattr(config, "downstream_mode", "file")
                    if downstream_mode != "file":
                        warnings.append(
                            f"ExternalMedia + pipelines: downstream_mode='{downstream_mode}' enabled; "
                            "pipelines will stream playback when possible and fall back to file playback on errors"
                        )

            # default_provider-specific key hints (warnings only)
            default_provider = getattr(config, "default_provider", None)
            if default_provider == "google_live" and not has_google:
                warnings.append(
                    "default_provider='google_live' but GOOGLE_API_KEY is not set; "
                    "Google Live provider will fail to connect"
                )
        except Exception as e:
            logger.debug("Transport/provider compatibility checks failed", error=str(e))
    
    except Exception as e:
        # Don't let validation errors crash startup
        logger.warning("Configuration validation encountered an error", error=str(e), exc_info=True)
        warnings.append(f"Validation check failed: {str(e)}")
    
    return errors, warnings
