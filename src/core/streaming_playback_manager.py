"""
StreamingPlaybackManager - Handles streaming audio playback via AudioSocket/ExternalMedia.

This module provides streaming audio playback capabilities that send audio chunks
directly over the AudioSocket connection instead of using file-based playback.
It includes automatic fallback to file playback on errors or timeouts.
"""

import asyncio
import time
import audioop
import array
from contextlib import suppress
from typing import Optional, Dict, Any, TYPE_CHECKING, Set, Callable, Awaitable, Tuple
import structlog
from prometheus_client import Counter, Gauge, Histogram
import math
import os
import wave

from src.audio.resampler import (
    mulaw_to_pcm16le,
    pcm16le_to_mulaw,
    resample_audio,
)
from src.core.session_store import SessionStore
from src.core.models import CallSession, PlaybackRef
from .adaptive_streaming import (
    StreamCharacterizer,
    AdaptiveBufferController,
    calculate_optimal_buffer,
    get_pattern_cache,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.core.conversation_coordinator import ConversationCoordinator
    from src.core.playback_manager import PlaybackManager

logger = structlog.get_logger(__name__)

_JITTER_SENTINEL = object()

# Prometheus metrics for streaming playback (module-scope, registered once)
_STREAMING_ACTIVE_GAUGE = Gauge(
    "ai_agent_streaming_active",
    "Number of calls with streaming playback active",
)
_STREAMING_BYTES_TOTAL = Counter(
    "ai_agent_streaming_bytes_total",
    "Total bytes queued to streaming playback (pre-conversion)",
)
_STREAMING_FALLBACKS_TOTAL = Counter(
    "ai_agent_streaming_fallbacks_total",
    "Number of times streaming fell back to file playback",
)
_STREAMING_JITTER_DEPTH = Gauge(
    "ai_agent_streaming_jitter_buffer_depth",
    "Max jitter buffer depth across active streams (queued chunks)",
)
_STREAMING_LAST_CHUNK_AGE = Gauge(
    "ai_agent_streaming_last_chunk_age_seconds",
    "Max seconds since last streaming chunk across active streams",
)
_STREAMING_KEEPALIVES_SENT_TOTAL = Counter(
    "ai_agent_streaming_keepalives_sent_total",
    "Count of keepalive ticks sent while streaming",
)
_STREAMING_KEEPALIVE_TIMEOUTS_TOTAL = Counter(
    "ai_agent_streaming_keepalive_timeouts_total",
    "Count of keepalive-detected streaming timeouts",
)
_STREAM_TX_BYTES = Counter(
    "ai_agent_stream_tx_bytes_total",
    "Outbound audio bytes sent to caller",
)

# Additional pacing/underflow metrics
_STREAM_UNDERFLOW_EVENTS_TOTAL = Counter(
    "ai_agent_stream_underflow_events_total",
    "Underflow events (20ms fillers inserted)",
)
_STREAM_FILLER_BYTES_TOTAL = Counter(
    "ai_agent_stream_filler_bytes_total",
    "Filler bytes injected on underflow",
)
_STREAM_FRAMES_SENT_TOTAL = Counter(
    "ai_agent_stream_frames_sent_total",
    "Frames (20ms) actually sent",
)

# New observability metrics for tuning
_STREAM_STARTED_TOTAL = Counter(
    "ai_agent_stream_started_total",
    "Number of streaming segments started",
    labelnames=("playback_type",),
)
_STREAM_FIRST_FRAME_SECONDS = Histogram(
    "ai_agent_stream_first_frame_seconds",
    "Time from stream start to first outbound frame",
    buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0),
    labelnames=("playback_type",),
)
_STREAM_SEGMENT_DURATION_SECONDS = Histogram(
    "ai_agent_stream_segment_duration_seconds",
    "Streaming segment duration",
    buckets=(0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 15.0, 30.0),
    labelnames=("playback_type",),
)
_STREAM_END_REASON_TOTAL = Counter(
    "ai_agent_stream_end_reason_total",
    "Count of stream end reasons",
    labelnames=("reason",),
)
_STREAM_ENDIAN_CORRECTIONS_TOTAL = Counter(
    "ai_agent_stream_endian_corrections_total",
    "Count of PCM16 egress byte-order corrections applied automatically",
    labelnames=("mode",),
)


class StreamingPlaybackManager:
    """
    Manages streaming audio playback with automatic fallback to file playback.
    
    Responsibilities:
    - Stream audio chunks directly over AudioSocket/ExternalMedia
    - Handle jitter buffering and timing
    - Implement automatic fallback to file playback
    - Manage streaming state and cleanup
    - Coordinate with ConversationCoordinator for gating
    """
    
    def __init__(
        self,
        session_store: SessionStore,
        ari_client,
        conversation_coordinator: Optional["ConversationCoordinator"] = None,
        fallback_playback_manager: Optional["PlaybackManager"] = None,
        streaming_config: Optional[Dict[str, Any]] = None,
        audio_transport: str = "externalmedia",
        rtp_server: Optional[Any] = None,
        audiosocket_server: Optional[Any] = None,
        audio_diag_callback: Optional[Callable[[str, str, bytes, str, int], Awaitable[None]]] = None,
        audio_capture_manager: Optional[Any] = None,
    ):
        self.session_store = session_store
        self.ari_client = ari_client
        self.conversation_coordinator = conversation_coordinator
        self.fallback_playback_manager = fallback_playback_manager
        self.streaming_config = streaming_config or {}
        self.audio_transport = audio_transport
        self.rtp_server = rtp_server
        self.audiosocket_server = audiosocket_server
        self.audio_diag_callback = audio_diag_callback
        self.audio_capture_manager = audio_capture_manager
        self.audiosocket_format: str = "ulaw"  # default format expected by dialplan
        # Debug: when True, send frames to all AudioSocket conns for the call
        self.audiosocket_broadcast_debug: bool = bool(self.streaming_config.get('audiosocket_broadcast_debug', False))
        # Egress endianness override mode: 'auto' | 'force_true' | 'force_false' | 'disabled'
        swap_mode_cfg = str(self.streaming_config.get('egress_swap_mode', 'disabled') or 'disabled').lower()
        if swap_mode_cfg not in {'auto', 'force_true', 'force_false', 'disabled'}:
            swap_mode_cfg = 'disabled'
        self.egress_swap_mode: str = swap_mode_cfg
        self.egress_force_mulaw: bool = bool(self.streaming_config.get('egress_force_mulaw', False))
        # Output conditioning: limiter and attack envelope
        try:
            self.limiter_enabled: bool = bool(self.streaming_config.get('limiter_enabled', True))
        except Exception:
            self.limiter_enabled = True
        try:
            self.limiter_headroom_ratio: float = float(self.streaming_config.get('limiter_headroom_ratio', 0.65))
        except Exception:
            self.limiter_headroom_ratio = 0.65
        try:
            # Explicitly handle 0 value (don't treat as falsy)
            attack_val = self.streaming_config.get('attack_ms')
            self.attack_ms: int = int(attack_val) if attack_val is not None else 20
        except Exception:
            self.attack_ms = 20
        
        # Streaming state
        self.active_streams: Dict[str, Dict[str, Any]] = {}  # call_id -> stream_info
        self.jitter_buffers: Dict[str, asyncio.Queue] = {}  # call_id -> audio_queue
        self.keepalive_tasks: Dict[str, asyncio.Task] = {}  # call_id -> keepalive_task
        self._cleanup_in_progress: Set[str] = set()
        # Per-call remainder buffer for precise frame sizing
        self.frame_remainders: Dict[str, bytes] = {}
        # Per-call resampler state (used when converting between rates)
        self._resample_states: Dict[str, Optional[tuple]] = {}
        # Per-call DC-block filter state: last_x, last_y
        self._dc_block_state: Dict[str, Tuple[float, float]] = {}
        # First outbound frame logged tracker
        self._first_send_logged: Set[str] = set()
        # RTP codec cache for performance (avoid repeated codec checks on every packet)
        self._rtp_codec_cache: Dict[str, bool] = {}
        # Startup gating to allow jitter buffers to fill before playback begins
        self._startup_ready: Dict[str, bool] = {}
        # Track last segment end time per call for adaptive warm-up
        self._last_segment_end_ts: Dict[str, float] = {}
        # Call-level diagnostic accumulators
        self.call_tap_pre_pcm16: Dict[str, bytearray] = {}
        self.call_tap_post_pcm16: Dict[str, bytearray] = {}
        self.call_tap_rate: Dict[str, int] = {}
        
        # Configuration defaults
        self.sample_rate = self.streaming_config.get('sample_rate', 8000)
        self.jitter_buffer_ms = self.streaming_config.get('jitter_buffer_ms', 50)
        self.keepalive_interval_ms = self.streaming_config.get('keepalive_interval_ms', 5000)
        self.connection_timeout_ms = self.streaming_config.get('connection_timeout_ms', 10000)
        self.fallback_timeout_ms = self.streaming_config.get('fallback_timeout_ms', 4000)
        self.chunk_size_ms = self._resolve_chunk_size_ms(self.streaming_config.get('chunk_size_ms'))
        self.idle_cutoff_ms = self._resolve_idle_cutoff_ms(self.streaming_config.get('idle_cutoff_ms'))
        # Continuous streaming across provider segments
        try:
            self.continuous_stream: bool = bool(self.streaming_config.get('continuous_stream', True))
        except Exception:
            self.continuous_stream = True
        # Simple audio normalizer (make-up gain before μ-law encode)
        try:
            norm = self.streaming_config.get('normalizer', {}) or {}
        except Exception:
            norm = {}
        try:
            self.normalizer_enabled: bool = bool(norm.get('enabled', True))
        except Exception:
            self.normalizer_enabled = True
        try:
            self.normalizer_target_rms: int = int(norm.get('target_rms', 1400))
        except Exception:
            self.normalizer_target_rms = 1400
        try:
            self.normalizer_max_gain_db: float = float(norm.get('max_gain_db', 9.0))
        except Exception:
            self.normalizer_max_gain_db = 9.0
        # Derived configuration (chunk counts)
        self.min_start_ms = max(0, int(self.streaming_config.get('min_start_ms', 120)))
        self.low_watermark_ms = max(0, int(self.streaming_config.get('low_watermark_ms', 80)))
        self.provider_grace_ms = max(0, int(self.streaming_config.get('provider_grace_ms', 500)))
        self.min_start_chunks = max(1, int(math.ceil(self.min_start_ms / max(1, self.chunk_size_ms))))
        self.low_watermark_chunks = max(0, int(math.ceil(self.low_watermark_ms / max(1, self.chunk_size_ms))))
        # Greeting-specific warm-up (optional)
        try:
            self.greeting_min_start_ms = int(self.streaming_config.get('greeting_min_start_ms', 0))
        except Exception:
            self.greeting_min_start_ms = 0
        # ExternalMedia greeting: safety net timeout for RTP endpoint establishment.
        # With RTP kick fix, RTP establishes in ~40-50ms. This is just a fallback if kick fails.
        try:
            self.greeting_rtp_wait_ms = int(self.streaming_config.get('greeting_rtp_wait_ms', 1000))
        except Exception:
            self.greeting_rtp_wait_ms = 1000
        self.greeting_min_start_chunks = (
            max(1, int(math.ceil(self.greeting_min_start_ms / max(1, self.chunk_size_ms))))
            if self.greeting_min_start_ms > 0 else self.min_start_chunks
        )
        # Logging verbosity override
        self.logging_level = (self.streaming_config.get('logging_level') or "info").lower()
        if self.logging_level == "debug":
            logger.debug("Streaming playback logging level set to DEBUG")
        elif self.logging_level == "warning":
            logger.warning("Streaming playback logging level set to WARNING")
        elif self.logging_level not in ("info", "debug", "warning"):
            logger.info("Streaming playback logging level", value=self.logging_level)
        try:
            self.diag_enable_taps = bool(self.streaming_config.get('diag_enable_taps', False))
        except Exception:
            self.diag_enable_taps = False
        # If explicit flag is not set, enable taps when logging is DEBUG to aid diagnostics
        if not self.diag_enable_taps and self.logging_level == "debug":
            self.diag_enable_taps = True
        # Log guards (avoid warning spam while waiting for ExternalMedia RTP endpoint).
        self._rtp_remote_wait_logged: set[str] = set()
        try:
            self.diag_pre_secs = int(self.streaming_config.get('diag_pre_secs', 2))
        except Exception:
            self.diag_pre_secs = 2
        try:
            self.diag_post_secs = int(self.streaming_config.get('diag_post_secs', 2))
        except Exception:
            self.diag_post_secs = 2
        try:
            self.diag_out_dir = str(self.streaming_config.get('diag_out_dir', '/tmp/ai-engine-taps') or '/tmp/ai-engine-taps')
        except Exception:
            self.diag_out_dir = '/tmp/ai-engine-taps'
        if self.diag_enable_taps:
            try:
                os.makedirs(self.diag_out_dir, mode=0o700, exist_ok=True)
                try:
                    os.chmod(self.diag_out_dir, 0o700)
                except Exception:
                    pass
            except Exception:
                pass
        # μ-law fast-path sanity guard (enabled by default)
        try:
            self.ulaw_fastpath_guard: bool = bool(self.streaming_config.get('ulaw_fastpath_guard', True))
        except Exception:
            self.ulaw_fastpath_guard = True
        # Low-buffer adaptive backoff configuration
        try:
            self.empty_backoff_ticks_max: int = int(self.streaming_config.get('empty_backoff_ticks_max', 5))
        except Exception:
            self.empty_backoff_ticks_max = 5
        try:
            self.max_filler_idle_ms: int = int(self.streaming_config.get('max_filler_idle_ms', 400))
        except Exception:
            self.max_filler_idle_ms = 400
        
        logger.info(
            "StreamingPlaybackManager initialized",
            sample_rate=self.sample_rate,
            jitter_buffer_ms=self.jitter_buffer_ms,
            diag_enable_taps=bool(self.diag_enable_taps),
            diag_out_dir=str(self.diag_out_dir),
        )
        try:
            logger.info(
                "Streaming mode",
                continuous_stream=bool(self.continuous_stream),
                normalizer_enabled=bool(self.normalizer_enabled),
                normalizer_target_rms=int(self.normalizer_target_rms),
                normalizer_max_gain_db=float(self.normalizer_max_gain_db),
            )
        except Exception:
            pass
    
    @staticmethod
    def _canonicalize_encoding(value: Optional[str]) -> str:
        if not value:
            return ""
        token = str(value).strip().lower()
        mapping = {
            "mu-law": "ulaw",
            "mulaw": "ulaw",
            "g711_ulaw": "ulaw",
            "g711ulaw": "ulaw",
            "linear16": "slin16",
            "pcm16": "slin16",
            # "slin": "slin16",  # REMOVED: slin should remain slin (8kHz PCM16)
            "slin": "slin",      # Keep slin as-is for 8kHz PCM16
            "slin12": "slin16",
            "slin16": "slin16",
        }
        return mapping.get(token, token)

    @staticmethod
    def _is_mulaw(value: Optional[str]) -> bool:
        canonical = StreamingPlaybackManager._canonicalize_encoding(value)
        return canonical in {"ulaw", "mulaw", "g711_ulaw", "mu-law"}

    def _ensure_call_tap_buffers(self, call_id: str, sample_rate: int) -> None:
        if not getattr(self, "diag_enable_taps", False):
            return
        try:
            self.call_tap_pre_pcm16.setdefault(call_id, bytearray())
            self.call_tap_post_pcm16.setdefault(call_id, bytearray())
            if sample_rate > 0 and call_id not in self.call_tap_rate:
                self.call_tap_rate[call_id] = int(sample_rate)
        except Exception:
            logger.debug("Call tap buffer init failed", call_id=call_id, exc_info=True)

    def _append_call_taps(self, call_id: str, pre: Optional[bytes], post: Optional[bytes], sample_rate: int) -> None:
        if not getattr(self, "diag_enable_taps", False):
            return
        try:
            if sample_rate > 0 and call_id not in self.call_tap_rate:
                self.call_tap_rate[call_id] = int(sample_rate)
            if pre:
                self.call_tap_pre_pcm16.setdefault(call_id, bytearray()).extend(pre)
            if post:
                self.call_tap_post_pcm16.setdefault(call_id, bytearray()).extend(post)
        except Exception:
            logger.debug("Call-level tap accumulation failed", call_id=call_id, exc_info=True)

    @staticmethod
    def _default_sample_rate_for_format(fmt: Optional[str], fallback: int) -> int:
        canonical = StreamingPlaybackManager._canonicalize_encoding(fmt)
        if canonical in {"ulaw", "mulaw", "g711_ulaw", "mu-law"}:
            return 8000
        if canonical == "slin":
            return 8000  # slin is always 8kHz PCM16
        if canonical in {"slin16", "linear16", "pcm16"}:
            return fallback if fallback > 0 else 16000
        return fallback if fallback > 0 else 8000

    def _refresh_streaming_summary_metrics(self) -> None:
        """Update low-cardinality streaming gauges (aggregate across calls)."""
        try:
            _STREAMING_ACTIVE_GAUGE.set(len(self.active_streams))
            max_depth = 0
            max_age = 0.0
            for info in (self.active_streams or {}).values():
                try:
                    max_depth = max(max_depth, int(info.get("jitter_depth", 0) or 0))
                except Exception:
                    pass
                try:
                    max_age = max(max_age, float(info.get("last_chunk_age_s", 0.0) or 0.0))
                except Exception:
                    pass
            _STREAMING_JITTER_DEPTH.set(max_depth)
            _STREAMING_LAST_CHUNK_AGE.set(max_age)
        except Exception:
            logger.debug("Streaming summary metrics update failed", exc_info=True)

    async def start_streaming_playback(
        self,
        call_id: str,
        audio_chunks: asyncio.Queue,
        playback_type: str = "response",
        source_encoding: Optional[str] = None,
        source_sample_rate: Optional[int] = None,
        target_encoding: Optional[str] = None,
        target_sample_rate: Optional[int] = None,
    ) -> Optional[str]:
        """
        Start streaming audio playback for a call.
        
        Args:
            call_id: Canonical call ID
            audio_chunks: Queue of audio chunks to stream
            playback_type: Type of playback (greeting, response, etc.)
            source_encoding: Provider audio encoding reported for this stream.
            source_sample_rate: Provider audio sample rate for this stream.
        
        Returns:
            stream_id if successful, None if failed
        """
        try:
            # Reuse active stream if one already exists
            if self.is_stream_active(call_id):
                existing = self.active_streams[call_id]['stream_id']
                logger.debug("Streaming already active for call", call_id=call_id, stream_id=existing)
                return existing

            # Get session to determine target channel
            session = await self.session_store.get_by_call_id(call_id)
            if not session:
                logger.error("Cannot start streaming - call session not found",
                           call_id=call_id)
                return None
            
            # Generate stream ID
            stream_id = self._generate_stream_id(call_id, playback_type)
            
            # 🧠 ADAPTIVE STREAMING: Get wire format and provider rate for intelligent buffering
            provider_name = getattr(session, 'provider_name', None) or getattr(session, 'provider', 'unknown')
            wire_sample_rate = 8000  # Default
            provider_sample_rate = 16000  # Default
            
            # Get wire sample rate from transport profile
            if hasattr(session, 'transport_profile'):
                try:
                    if hasattr(session.transport_profile, 'wire_sample_rate'):
                        wire_sample_rate = int(session.transport_profile.wire_sample_rate)
                    if hasattr(session.transport_profile, 'provider_output_sample_rate'):
                        provider_sample_rate = int(session.transport_profile.provider_output_sample_rate)
                except Exception:
                    pass
            
            # Override with source_sample_rate if provided
            if source_sample_rate and source_sample_rate > 0:
                provider_sample_rate = int(source_sample_rate)
            
            # 🧠 Check for cached provider pattern
            pattern_cache = get_pattern_cache()
            cached_pattern = pattern_cache.get_hint(provider_name, wire_sample_rate)
            
            # 🧠 Calculate intelligent buffer size
            base_config_ms = max(1, int(self.greeting_min_start_ms if playback_type == "greeting" else self.min_start_ms))
            intelligent_buffer_ms = calculate_optimal_buffer(
                stream_pattern=cached_pattern,
                wire_sample_rate=wire_sample_rate,
                provider_sample_rate=provider_sample_rate,
                base_config_ms=base_config_ms
            )
            
            logger.info(
                "🧠 Intelligent buffer calculated",
                call_id=call_id,
                provider=provider_name,
                wire_rate=wire_sample_rate,
                provider_rate=provider_sample_rate,
                base_config_ms=base_config_ms,
                intelligent_buffer_ms=intelligent_buffer_ms,
                cached_pattern=cached_pattern.type if cached_pattern else "none"
            )
            
            # Initialize jitter buffer sized from intelligent calculation
            try:
                chunk_ms = max(1, int(self.chunk_size_ms))
                jb_ms = max(0, int(self.jitter_buffer_ms))
                jb_chunks = max(1, int(math.ceil(jb_ms / chunk_ms)))
            except Exception:
                jb_chunks = 10
            jitter_buffer = asyncio.Queue(maxsize=jb_chunks)
            self.jitter_buffers[call_id] = jitter_buffer
            
            # 🧠 Initialize adaptive streaming components
            stream_characterizer = StreamCharacterizer()
            adaptive_controller = AdaptiveBufferController(intelligent_buffer_ms)
            
            # Store adaptive components for this stream
            if not hasattr(self, 'adaptive_controllers'):
                self.adaptive_controllers = {}
            if not hasattr(self, 'stream_characterizers'):
                self.stream_characterizers = {}
            
            self.adaptive_controllers[call_id] = adaptive_controller
            self.stream_characterizers[call_id] = stream_characterizer
            # 🧠 Use intelligent buffer instead of legacy heuristics
            # Convert intelligent_buffer_ms to chunks
            min_start_chunks = max(1, int(math.ceil(intelligent_buffer_ms / chunk_ms)))
            
            # Ensure we don't exceed jitter buffer capacity
            max_startable = max(1, jb_chunks - 1)
            min_start_chunks = max(1, min(min_start_chunks, max_startable))
            
            # Resume floor matches intelligent buffer
            resume_floor_ms = intelligent_buffer_ms
            resume_floor_chunks = min_start_chunks
            # Scale low watermark proportionally to the adaptive warm-up
            # Use ~2/3 of min_start by default, but do not go BELOW configured low_watermark (treat as floor).
            try:
                scaled_lw = int(max(0, math.ceil(min_start_chunks * (2.0/3.0))))
            except Exception:
                scaled_lw = min_start_chunks // 2
            configured_low_watermark = max(self.low_watermark_chunks, scaled_lw)
            low_watermark_chunks = 0
            if configured_low_watermark:
                max_low = max(0, min_start_chunks - 1)
                half_capacity = max(0, jb_chunks // 2)
                effective_cap = max(0, min(max_low, half_capacity))
                low_watermark_chunks = min(configured_low_watermark, effective_cap)
                if configured_low_watermark > low_watermark_chunks:
                    logger.debug(
                        "Streaming low_watermark clamped",
                        call_id=call_id,
                        playback_type=playback_type,
                        configured_chunks=configured_low_watermark,
                        jitter_chunks=jb_chunks,
                        applied_chunks=low_watermark_chunks,
                        min_start_chunks=min_start_chunks,
                    )
            # Decide initial startup readiness based on recent gap (reuse buffer for back-to-back)
            # Get gap from cached pattern if available, otherwise assume cold start
            gap_ms = cached_pattern.max_gap_ms if cached_pattern else 999999
            try:
                initial_startup_ready = bool(gap_ms <= int(self.provider_grace_ms or 500))
            except Exception:
                initial_startup_ready = bool(gap_ms <= 500)
            # Log adaptive warm-up decision for observability
            try:
                logger.info(
                    "🎚️ STREAMING ADAPTIVE WARM-UP",
                    call_id=call_id,
                    playback_type=playback_type,
                    gap_ms=gap_ms,
                    adaptive_min_ms=(adaptive_min_ms if playback_type != "greeting" else base_min_ms),
                    adaptive_warmup_ms=(adaptive_min_ms if playback_type != "greeting" else base_min_ms),
                    resume_floor_ms=resume_floor_ms,
                    resume_floor_chunks=resume_floor_chunks,
                    min_start_chunks=min_start_chunks,
                    low_watermark_chunks=low_watermark_chunks,
                    chunk_ms=chunk_ms,
                    jb_chunks=jb_chunks,
                    initial_startup_ready=initial_startup_ready,
                    startup_ready_reused=initial_startup_ready,
                    provider_grace_ms=int(self.provider_grace_ms) if getattr(self, 'provider_grace_ms', None) is not None else 0,
                )
            except Exception:
                pass

            # Mark streaming active in session (metrics are aggregate; updated after stream registration)
            if session:
                session.streaming_started = True
                session.current_stream_id = stream_id
                await self.session_store.upsert_call(session)
            
            # Set TTS gating before starting stream
            # Skip gating for full agent providers that handle turn-taking internally
            # These providers have server-side VAD and don't need client-side audio gating
            # NOTE: google_live is intentionally EXCLUDED — it lacks server-side echo cancellation,
            # so engine-side gating is required to prevent echoed model audio from confusing its VAD.
            FULL_AGENT_PROVIDERS = {'deepgram', 'openai_realtime', 'elevenlabs_agent'}
            provider_name = getattr(session, 'provider_name', None) if session else None
            skip_gating = provider_name in FULL_AGENT_PROVIDERS
            
            gating_success = True
            if skip_gating:
                logger.debug("Skipping TTS gating for full agent provider",
                           call_id=call_id,
                           provider=provider_name,
                           stream_id=stream_id)
            elif self.conversation_coordinator:
                gating_success = await self.conversation_coordinator.on_tts_start(call_id, stream_id)
            else:
                gating_success = await self.session_store.set_gating_token(call_id, stream_id)

            if not gating_success:
                logger.error("Failed to start streaming gating",
                           call_id=call_id,
                           stream_id=stream_id)
                return None
            
            # Start streaming task
            streaming_task = asyncio.create_task(
                self._stream_audio_loop(call_id, stream_id, audio_chunks, jitter_buffer)
            )
            
            # Start pacer (consumer) task to drain jitter buffer independently of producer
            pacer_task = asyncio.create_task(
                self._pacer_loop(call_id, stream_id, jitter_buffer)
            )
            # Start keepalive task
            keepalive_task = asyncio.create_task(
                self._keepalive_loop(call_id, stream_id)
            )
            self.keepalive_tasks[call_id] = keepalive_task
            
            src_encoding = self._canonicalize_encoding(source_encoding) or "slin16"
            try:
                src_rate = int(source_sample_rate) if source_sample_rate is not None else self.sample_rate
            except Exception:
                src_rate = self.sample_rate

            # Determine downstream target format/sample rate for this stream.
            resolved_target_format = (
                self._canonicalize_encoding(target_encoding)
                or self._canonicalize_encoding(self.audiosocket_format)
                or "ulaw"
            )
            try:
                resolved_target_rate = (
                    int(target_sample_rate)
                    if target_sample_rate is not None
                    else int(self.sample_rate)
                )
            except Exception:
                resolved_target_rate = self.sample_rate
            if resolved_target_rate <= 0:
                resolved_target_rate = self._default_sample_rate_for_format(
                    resolved_target_format,
                    self.sample_rate,
                )
            # For ExternalMedia/RTP, use codec from session instead of audiosocket_format
            transport_format = self.audiosocket_format
            if self.audio_transport == "externalmedia":
                session = await self.session_store.get_by_call_id(call_id)
                if session and hasattr(session, 'external_media_codec') and session.external_media_codec:
                    transport_format = session.external_media_codec
                    logger.debug(
                        "Using ExternalMedia codec for target format",
                        call_id=call_id,
                        codec=transport_format
                    )
                else:
                    # Greeting may fire before ExternalMedia codec is stored;
                    # default to ulaw which is the standard ExternalMedia codec.
                    transport_format = "ulaw"
                    logger.debug(
                        "ExternalMedia codec not yet available, defaulting to ulaw",
                        call_id=call_id,
                    )
            
            mulaw_transport = self._is_mulaw(transport_format)
            pcm_transport = self._canonicalize_encoding(transport_format)
            if mulaw_transport:
                resolved_target_format = "ulaw"
                resolved_target_rate = 8000
            elif pcm_transport in {"slin16", "linear16", "pcm16"}:
                resolved_target_format = "slin16"
                # CRITICAL FIX: slin16 means 16kHz PCM16, NOT 8kHz!
                # Asterisk codec naming: slin=8k, slin16=16k, slin48=48k
                # The "16" suffix indicates sample rate (16kHz), not just bit depth
                resolved_target_rate = 16000  # ✅ Correct for slin16
                logger.debug(
                    "Using slin16 at native 16kHz",
                    call_id=call_id,
                    provider_rate=src_rate,
                    target_rate=resolved_target_rate
                )
            elif pcm_transport == "slin":
                resolved_target_format = "slin"
                # CRITICAL FIX #4: slin is ALWAYS 8kHz (AudioSocket Type 0x10)
                # Never use provider rate for AudioSocket target - it causes sample rate mismatch
                # Provider sends 16kHz (OpenAI) but AudioSocket channel expects 8kHz c(slin)
                # This mismatch causes buffer overflow and premature segment ending
                resolved_target_rate = 8000  # Fixed to 8kHz, ignore provider rate
                logger.debug(
                    "AudioSocket slin format locked to 8kHz",
                    call_id=call_id,
                    provider_rate=src_rate,
                    target_rate=resolved_target_rate
                )
            else:
                resolved_target_rate = self._default_sample_rate_for_format(
                    resolved_target_format,
                    resolved_target_rate,
                )

            self._resample_states[call_id] = None
            # Store stream info
            try:
                idle_cutoff_ticks = max(1, int(math.ceil(self.idle_cutoff_ms / max(1, self.chunk_size_ms))))
            except Exception:
                idle_cutoff_ticks = 60
            # Small pre-start wait to allow inbound endianness probe to populate session.vad_state
            # This helps avoid a race where the first greeting frames are sent with the wrong byte order
            # Initialize call-level taps if enabled
            self._ensure_call_tap_buffers(call_id, resolved_target_rate)

            self.active_streams[call_id] = {
                'stream_id': stream_id,
                'playback_type': playback_type,
                'streaming_task': streaming_task,
                'pacer_task': pacer_task,
                'keepalive_task': keepalive_task,
                'start_time': time.time(),
                'seg_start_ts': time.time(),
                'chunks_sent': 0,
                'last_chunk_time': time.time(),
                'idle_cutoff_ms': self.idle_cutoff_ms,
                'startup_ready': bool(initial_startup_ready),
                'first_frame_observed': False,
                'min_start_chunks': min_start_chunks,
                'empty_backoff_ticks': 0,
                'buffer_depth_max_frames': 0,
                'buffer_depth_min_frames': None,
                'filler_frames': 0,
                'idle_cutoff_debug': [],
                'source_encoding': src_encoding,
                'source_sample_rate': src_rate,
                'target_format': resolved_target_format,
                'target_sample_rate': resolved_target_rate,
                'tx_bytes': 0,
                'queued_bytes': 0,
                'frames_sent': 0,
                'underflow_events': 0,
                'provider_bytes': 0,
                'warned_grace_cap': False,
                'tap_pre_pcm16': bytearray(),
                'tap_post_pcm16': bytearray(),
                'tap_rate': (self.call_tap_rate.get(call_id, resolved_target_rate if self.diag_enable_taps else 0)),
                'diag_enabled': self.diag_enable_taps,
                'tap_first_window_pre': bytearray(),
                'tap_first_window_post': bytearray(),
                'tap_first_window_done': False,
                'segments_played': 0,
                'idle_ticks': 0,
                'idle_cutoff_ticks': idle_cutoff_ticks,
                'last_real_emit_ts': None,
                'last_emit_was_filler': False,
            }
            self._startup_ready[call_id] = bool(initial_startup_ready)
            try:
                _STREAM_STARTED_TOTAL.labels(playback_type).inc()
            except Exception:
                pass
            self._refresh_streaming_summary_metrics()
            
            logger.info("🎵 STREAMING PLAYBACK - Started",
                       call_id=call_id,
                       stream_id=stream_id,
                       playback_type=playback_type)
            # Explicit per-stream log for continuous streaming mode
            try:
                if bool(self.continuous_stream):
                    logger.info(
                        "⚡ CONTINUOUS STREAM - Enabled for stream",
                        call_id=call_id,
                        stream_id=stream_id,
                        segments_played=int(self.active_streams[call_id].get('segments_played', 0)),
                        min_start_chunks=int(self.active_streams[call_id].get('min_start_chunks', 0)),
                        low_watermark_chunks=int(self.low_watermark_chunks),
                    )
            except Exception:
                pass

            # Outbound setup probe
            try:
                logger.info(
                    "🎵 STREAMING OUTBOUND - Setup",
                    call_id=call_id,
                    stream_id=stream_id,
                    source_encoding=src_encoding,
                    source_sample_rate=src_rate,
                    target_format=resolved_target_format,
                    target_sample_rate=resolved_target_rate,
                )
            except Exception:
                pass
            # One-time frame size observability at start
            try:
                logger.info(
                    "🎼 STREAM FRAME SIZE",
                    call_id=call_id,
                    frame_size_bytes=self._frame_size_bytes(call_id),
                    chunk_ms=int(self.chunk_size_ms),
                    target_format=resolved_target_format,
                    target_rate=resolved_target_rate,
                )
            except Exception:
                pass
            
            return stream_id
            
        except Exception as e:
            logger.error("Error starting streaming playback",
                        call_id=call_id,
                        playback_type=playback_type,
                        error=str(e),
                        exc_info=True)
            return None
    
    async def _stream_audio_loop(
        self, 
        call_id: str, 
        stream_id: str, 
        audio_chunks: asyncio.Queue,
        jitter_buffer: asyncio.Queue
    ) -> None:
        """Main streaming loop that processes audio chunks."""
        sentinel_sent = False
        try:
            fallback_timeout = self.fallback_timeout_ms / 1000.0
            last_send_time = time.time()
            
            while True:
                try:
                    # Wait for audio chunk with timeout
                    chunk = await asyncio.wait_for(audio_chunks.get(), timeout=fallback_timeout)

                    if chunk is None:  # End of stream signal from provider
                        logger.info("🎵 STREAMING PLAYBACK - End of stream", call_id=call_id, stream_id=stream_id)
                        try:
                            if call_id in self.active_streams:
                                self.active_streams[call_id]['end_reason'] = 'end-of-stream'
                        except Exception:
                            pass
                        try:
                            await jitter_buffer.put(_JITTER_SENTINEL)
                            sentinel_sent = True
                        except Exception:
                            pass
                        break

                    # Update timing and metrics
                    last_send_time = time.time()
                    try:
                        _STREAMING_BYTES_TOTAL.inc(len(chunk))
                        info = self.active_streams.get(call_id)
                        if info is not None:
                            info["jitter_depth"] = jitter_buffer.qsize()
                            info["last_chunk_age_s"] = 0.0
                        self._refresh_streaming_summary_metrics()
                        # Track per-call queued total as well as segment-local queued_bytes
                        if info is not None:
                            info['queued_total_bytes'] = int(info.get('queued_total_bytes', 0) or 0) + len(chunk)
                        sess = await self.session_store.get_by_call_id(call_id)
                        if sess:
                            sess.streaming_bytes_sent += len(chunk)
                            sess.streaming_jitter_buffer_depth = jitter_buffer.qsize()
                            await self.session_store.upsert_call(sess)
                    except Exception:
                        logger.debug("Streaming metrics update failed", call_id=call_id)

                    # Enqueue provider chunk for downstream processing
                    await jitter_buffer.put(chunk)
                    
                    # 🧠 ADAPTIVE STREAMING: Characterize stream pattern during first 500ms
                    if call_id in self.stream_characterizers:
                        characterizer = self.stream_characterizers[call_id]
                        if not characterizer.characterization_done:
                            characterizer.add_chunk(len(chunk))
                            
                            # Check if we should analyze now
                            if characterizer.should_analyze():
                                pattern = characterizer.analyze()
                                
                                if pattern and call_id in self.adaptive_controllers:
                                    # Get session info for provider name and rates
                                    sess = await self.session_store.get_by_call_id(call_id)
                                    if sess:
                                        provider_name = getattr(sess, 'provider_name', None) or getattr(sess, 'provider', 'unknown')
                                        wire_rate = 8000
                                        if hasattr(sess, 'transport_profile') and hasattr(sess.transport_profile, 'wire_sample_rate'):
                                            wire_rate = int(sess.transport_profile.wire_sample_rate)
                                        
                                        # Cache this pattern for future calls
                                        pattern_cache = get_pattern_cache()
                                        pattern_cache.update_pattern(provider_name, wire_rate, pattern)
                                        
                                        logger.info(
                                            "🧠 Stream characterized and pattern cached",
                                            call_id=call_id,
                                            provider=provider_name,
                                            pattern_type=pattern.type,
                                            optimal_buffer_ms=pattern.optimal_buffer_ms
                                        )

                    # Normalize buffered_bytes accounting to target (egress) bytes so warm-up gating matches wire frame size
                    try:
                        if call_id in self.active_streams:
                            info = self.active_streams[call_id]
                            src_enc = self._canonicalize_encoding(info.get('source_encoding')) or "slin16"
                            try:
                                src_rate = int(info.get('source_sample_rate') or 0) or int(self.sample_rate)
                            except Exception:
                                src_rate = int(self.sample_rate)
                            tgt_fmt = self._canonicalize_encoding(info.get('target_format')) or self._canonicalize_encoding(self.audiosocket_format) or "ulaw"
                            try:
                                tgt_rate = int(info.get('target_sample_rate') or 0) or int(self.sample_rate)
                            except Exception:
                                tgt_rate = int(self.sample_rate)
                            if tgt_rate <= 0:
                                tgt_rate = self._default_sample_rate_for_format(
                                    tgt_fmt,
                                    int(self.sample_rate),
                                )
                            src_bps = 1 if self._is_mulaw(src_enc) else 2
                            tgt_bps = 1 if self._is_mulaw(tgt_fmt) else 2
                            try:
                                ratio = (tgt_bps / float(max(1, src_bps))) * (float(tgt_rate) / float(max(1, src_rate)))
                                egress_bytes = int(max(1, round(len(chunk) * max(0.5, ratio))))
                            except Exception:
                                egress_bytes = len(chunk)
                            info['buffered_bytes'] = int(info.get('buffered_bytes', 0)) + egress_bytes
                            info['queued_bytes'] = int(info.get('queued_bytes', 0)) + len(chunk)
                    except Exception:
                        pass

                except asyncio.TimeoutError:
                    # No audio chunk received within timeout
                    if not self.continuous_stream:
                        if time.time() - last_send_time > fallback_timeout:
                            logger.warning("🎵 STREAMING PLAYBACK - Timeout, falling back to file playback", call_id=call_id, stream_id=stream_id, timeout=fallback_timeout)
                            await self._record_fallback(call_id, f"timeout>{fallback_timeout}s")
                            await self._fallback_to_file_playback(call_id, stream_id)
                            if not sentinel_sent:
                                try:
                                    await jitter_buffer.put(_JITTER_SENTINEL)
                                    sentinel_sent = True
                                except Exception:
                                    pass
                            break
                        continue
                    # Continuous stream: stay alive, pacer will inject fillers as needed
                    try:
                        info = self.active_streams.get(call_id)
                        if info is not None:
                            info["last_chunk_age_s"] = float(time.time() - last_send_time)
                        self._refresh_streaming_summary_metrics()
                    except Exception:
                        pass
                    continue
        finally:
            if not sentinel_sent:
                with suppress(asyncio.CancelledError, Exception):
                    await jitter_buffer.put(_JITTER_SENTINEL)
                sentinel_sent = True
            pacer_task: Optional[asyncio.Task] = None
            stream_info = self.active_streams.get(call_id)
            if stream_info is not None:
                stream_info['producer_closed'] = True
                pacer_task = stream_info.get('pacer_task')
            if pacer_task and not pacer_task.done():
                try:
                    frames_remaining = self._estimate_available_frames(call_id, jitter_buffer, include_remainder=True)
                except Exception:
                    frames_remaining = 0
                chunk_sec = max(0.02, self.chunk_size_ms / 1000.0)
                # NOTE: Do not cap drain time too aggressively.
                # In downstream streaming mode, providers/pipelines may enqueue a large amount of audio quickly
                # and then signal end-of-stream. The pacer must be allowed to drain frame remainders;
                # otherwise playback truncates (observed ~2s cutoff when drain_timeout was capped at 2.0s).
                #
                # We still cap the wait to avoid hanging indefinitely on transport failures.
                drain_timeout = max(0.5, (frames_remaining * chunk_sec) + 0.5)
                drain_timeout = min(120.0, drain_timeout)
                with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                    await asyncio.wait_for(pacer_task, timeout=drain_timeout)
            if pacer_task and not pacer_task.done():
                pacer_task.cancel()
                with suppress(asyncio.CancelledError):
                    await pacer_task
            keepalive_task = self.keepalive_tasks.pop(call_id, None)
            if keepalive_task:
                keepalive_task.cancel()
                with suppress(asyncio.CancelledError):
                    await keepalive_task
            await self._cleanup_stream(call_id, stream_id)

    async def _pacer_loop(
        self,
        call_id: str,
        stream_id: str,
        jitter_buffer: asyncio.Queue,
    ) -> None:
        """Drain jitter buffer at steady cadence so producer and consumer are independent."""
        tick_seconds = max(0.02, self.chunk_size_ms / 1000.0)
        next_tick = time.perf_counter()
        try:
            while True:
                now = time.perf_counter()
                sleep_for = next_tick - now
                if sleep_for > 0:
                    try:
                        await asyncio.sleep(sleep_for)
                    except asyncio.CancelledError:
                        raise
                else:
                    next_tick = now
                if call_id not in self.active_streams:
                    break
                status = await self._drain_next_frame(
                    call_id, stream_id, jitter_buffer
                )
                if status == "error":
                    try:
                        await self._record_fallback(call_id, "transport-failure")
                        await self._fallback_to_file_playback(call_id, stream_id)
                        if call_id in self.active_streams:
                            self.active_streams[call_id]['end_reason'] = 'transport-failure'
                    except Exception:
                        pass
                    break
                self._update_idle_tracking(call_id, status)
                if self._should_stop_for_idle(call_id, stream_id, jitter_buffer):
                    break
                if status == "finished":
                    break
                next_tick += tick_seconds
                now_after = time.perf_counter()
                if next_tick < now_after:
                    next_tick = now_after
        except asyncio.CancelledError:
            logger.debug("Pacer loop cancelled", call_id=call_id, stream_id=stream_id)
        except Exception as e:
            logger.error("Error in pacer loop", call_id=call_id, stream_id=stream_id, error=str(e), exc_info=True)
    
    async def _drain_next_frame(
        self,
        call_id: str,
        stream_id: str,
        jitter_buffer: asyncio.Queue,
    ) -> str:
        """Send one 20ms frame (or filler) per tick."""
        stream_info = self.active_streams.get(call_id)
        if not stream_info:
            return "finished"

        try:
            stream_info["jitter_depth"] = jitter_buffer.qsize()
            self._refresh_streaming_summary_metrics()
        except Exception:
            pass

        if not self._ensure_startup_ready(call_id, stream_id, jitter_buffer, stream_info):
            return "wait"

        target_fmt = (
            self._canonicalize_encoding(stream_info.get("target_format"))
            or self._canonicalize_encoding(self.audiosocket_format)
            or "ulaw"
        )
        try:
            target_rate = int(stream_info.get("target_sample_rate", self.sample_rate))
        except Exception:
            target_rate = int(self.sample_rate)
        if target_rate <= 0:
            target_rate = self._default_sample_rate_for_format(target_fmt, int(self.sample_rate))

        frame_size = self._frame_size_bytes(call_id)
        sentinel_seen = bool(stream_info.get('sentinel_seen', False))
        pending = self.frame_remainders.get(call_id, b"")

        while len(pending) < frame_size:
            try:
                chunk = jitter_buffer.get_nowait()
            except asyncio.QueueEmpty:
                break
            if chunk is _JITTER_SENTINEL:
                sentinel_seen = True
                stream_info['sentinel_seen'] = True
                continue
            processed_chunk = await self._process_audio_chunk(call_id, chunk)
            if not processed_chunk:
                try:
                    self._decrement_buffered_bytes(call_id, len(chunk))
                except Exception:
                    pass
                continue
            pending += processed_chunk

        self.frame_remainders[call_id] = pending
        available_frames = self._estimate_available_frames(call_id, jitter_buffer, include_remainder=True)
        try:
            info = self.active_streams.get(call_id, {})
            current_max = int(info.get('buffer_depth_max_frames', 0) or 0)
            if available_frames > current_max:
                info['buffer_depth_max_frames'] = available_frames
            current_min = info.get('buffer_depth_min_frames')
            if current_min is None or available_frames < current_min:
                info['buffer_depth_min_frames'] = available_frames
        except Exception:
            pass

        if self._should_wait_for_low_water(call_id, stream_info, available_frames, sentinel_seen):
            return "wait"

        if len(pending) >= frame_size:
            frame = pending[:frame_size]
            self.frame_remainders[call_id] = pending[frame_size:]
            return await self._emit_frame(
                call_id,
                stream_id,
                frame,
                target_fmt,
                target_rate,
                filler=False,
            )

        if sentinel_seen:
            if pending:
                filler_byte = b"\xFF" if self._is_mulaw(target_fmt) else b"\x00"
                padded = pending + (filler_byte * max(0, frame_size - len(pending)))
                self.frame_remainders[call_id] = b""
                return await self._emit_frame(
                    call_id,
                    stream_id,
                    padded[:frame_size],
                    target_fmt,
                    target_rate,
                    filler=False,
                )
            if jitter_buffer.empty():
                return "finished"

        if (
            stream_info.get('startup_ready')
            and not sentinel_seen
            and jitter_buffer.empty()
        ):
            # Adaptive low-buffer backoff: occasionally wait instead of emitting filler
            try:
                backoff = int(stream_info.get('empty_backoff_ticks', 0) or 0)
            except Exception:
                backoff = 0
            if backoff < self.empty_backoff_ticks_max:
                stream_info['empty_backoff_ticks'] = backoff + 1
                try:
                    logger.debug("Low-buffer backoff tick", call_id=call_id, streak=stream_info['empty_backoff_ticks'])
                except Exception:
                    pass
                return "wait"
            # After short backoff streak, send one filler and reset the streak
            stream_info['empty_backoff_ticks'] = 0
            # Suppress filler if prolonged idle since last real frame (prevents tail drift)
            try:
                lri = stream_info.get('last_real_emit_ts')
                if lri is not None:
                    elapsed_ms = (time.time() - float(lri)) * 1000.0
                    if elapsed_ms >= float(getattr(self, 'max_filler_idle_ms', 400)):
                        return "wait"
            except Exception:
                pass
            filler_byte = b"\xFF" if self._is_mulaw(target_fmt) else b"\x00"
            if pending:
                pending_len = len(pending)
                frame = pending + (filler_byte * max(0, frame_size - pending_len))
                self.frame_remainders[call_id] = b""
                try:
                    if pending_len:
                        self._decrement_buffered_bytes(call_id, pending_len)
                except Exception:
                    pass
            else:
                frame = filler_byte * frame_size
            return await self._emit_frame(
                call_id,
                stream_id,
                frame[:frame_size],
                target_fmt,
                target_rate,
                filler=True,
            )

        try:
            info = self.active_streams.get(call_id)
            if info is not None:
                info["jitter_depth"] = jitter_buffer.qsize()
            self._refresh_streaming_summary_metrics()
        except Exception:
            pass

        return "wait"

    def _ensure_startup_ready(
        self,
        call_id: str,
        stream_id: str,
        jitter_buffer: asyncio.Queue,
        stream_info: Dict[str, Any],
    ) -> bool:
        if self._startup_ready.get(call_id, False):
            return True
        
        # OPTIMIZATION: Skip warm-up for non-first segments in continuous stream
        # Buffer is already primed from previous segment - start immediately
        is_first_segment = stream_info.get('segments_played', 0) == 0
        if not is_first_segment and self.continuous_stream:
            self._startup_ready[call_id] = True
            stream_info['startup_ready'] = True
            try:
                logger.info(
                    "⚡ CONTINUOUS STREAM - Skipping warm-up for subsequent segment",
                    call_id=call_id,
                    stream_id=stream_id,
                    segment_num=stream_info.get('segments_played', 0),
                    jitter_depth=jitter_buffer.qsize(),
                )
            except Exception:
                pass
            return True
        
        # Original warm-up logic for first segment
        try:
            min_need = int(stream_info.get('min_start_chunks', self.min_start_chunks))
        except Exception:
            min_need = self.min_start_chunks
        available_frames = self._estimate_available_frames(call_id, jitter_buffer, include_remainder=True)
        if available_frames < min_need:
            return False
        self._startup_ready[call_id] = True
        stream_info['startup_ready'] = True
        try:
            logger.debug(
                "Streaming jitter buffer warm-up complete",
                call_id=call_id,
                stream_id=stream_id,
                buffered_chunks=jitter_buffer.qsize(),
            )
        except Exception:
            pass
        return True

    def _note_idle_block(self, stream_info: Dict[str, Any], reason: str) -> None:
        try:
            dbg_list = stream_info.setdefault('idle_cutoff_debug', [])
            if reason not in dbg_list and len(dbg_list) < 8:
                dbg_list.append(reason)
        except Exception:
            pass

    def _should_wait_for_low_water(
        self,
        call_id: str,
        stream_info: Dict[str, Any],
        available_frames: int,
        sentinel_seen: bool,
    ) -> bool:
        if sentinel_seen:
            stream_info.pop('low_water_deadline', None)
            return False
        if not stream_info.get('startup_ready'):
            return False
        low_watermark_chunks = self._get_low_watermark_frames(call_id)
        if not low_watermark_chunks:
            stream_info.pop('low_water_deadline', None)
            return False
        # Post-start, only rebuild-wait when we are truly empty.
        # If any frames exist, keep flowing to maintain continuous 20ms cadence.
        if available_frames > 0:
            stream_info.pop('low_water_deadline', None)
            return False
        # After startup, do not couple rebuild target to min_start; aim for low_water + 1.
        target_frames = low_watermark_chunks + 1
        try:
            cfg_wait = max(0.0, float(self.provider_grace_ms) / 1000.0)
        except Exception:
            cfg_wait = 0.5
        # Avoid pathological "wait forever" configs, but don't hard-cap so low that
        # bursty providers (e.g., Google Live) constantly drain the buffer and stutter.
        max_wait_cap_sec = 2.0
        if cfg_wait > max_wait_cap_sec and not bool(stream_info.get('warned_grace_cap', False)):
            try:
                logger.warning(
                    "provider_grace_ms clamped",
                    call_id=call_id,
                    configured_ms=int(self.provider_grace_ms),
                    clamp_ms=int(max_wait_cap_sec * 1000),
                )
            except Exception:
                pass
            stream_info['warned_grace_cap'] = True
        max_wait = min(max_wait_cap_sec, cfg_wait)
        if max_wait <= 0.0:
            stream_info.pop('low_water_deadline', None)
            return False
        now = time.time()
        deadline = stream_info.get('low_water_deadline')
        if deadline is None:
            stream_info['low_water_deadline'] = now + max_wait
            return True
        # Continue waiting only while still truly empty; once any frames arrive, resume sending.
        if now < deadline and available_frames == 0:
            return True
        stream_info.pop('low_water_deadline', None)
        return False

    async def _emit_frame(
        self,
        call_id: str,
        stream_id: str,
        frame: bytes,
        target_fmt: str,
        target_rate: int,
        *,
        filler: bool,
    ) -> str:
        success = await self._send_audio_chunk(
            call_id,
            stream_id,
            frame,
            target_fmt=target_fmt,
            target_rate=target_rate,
        )
        if not success:
            # Preserve audio for retry/fallback. Without this, a failed first outbound send can
            # drain the jitter buffer and leave nothing for file playback fallback.
            try:
                rem = self.frame_remainders.get(call_id, b"") or b""
                # FIX: Append frame to end (chronological order), not prepend
                # Previously: frame + rem (wrong - newest first)
                # Now: rem + frame (correct - oldest first)
                self.frame_remainders[call_id] = rem + frame
            except Exception:
                pass

            # ExternalMedia greeting can fail before we learn the remote RTP endpoint (Asterisk may
            # not emit RTP until the caller speaks). Wait for greeting to complete, then fall back.
            try:
                info = self.active_streams.get(call_id, {}) or {}
                is_greeting = str(info.get("playback_type") or "") == "greeting"
                if (
                    is_greeting
                    and self.audio_transport == "externalmedia"
                    and self.rtp_server is not None
                    and hasattr(self.rtp_server, "has_remote_endpoint")
                    and not self.rtp_server.has_remote_endpoint(call_id)
                ):
                    # Simple safety-net timeout for RTP endpoint establishment.
                    # With RTP kick fix, RTP establishes in ~40-50ms. This fallback should
                    # rarely trigger - it's just a safety net if kick fails for some reason.
                    wait_ms = int(getattr(self, "greeting_rtp_wait_ms", 1000) or 1000)
                    if wait_ms > 0:
                        now = time.time()
                        start_ts = float(info.get("rtp_wait_started_ts") or 0.0) or now
                        info["rtp_wait_started_ts"] = start_ts
                        waited_ms = (now - start_ts) * 1000.0
                        self.active_streams[call_id] = info
                        
                        if waited_ms < float(wait_ms):
                            return "wait"
                        
                        # Timeout expired - trigger fallback to file playback
                        info["end_reason"] = "rtp-remote-endpoint-timeout"
                        self.active_streams[call_id] = info
                        logger.warning(
                            "🎵 GREETING FALLBACK - RTP endpoint not established (RTP kick may have failed)",
                            call_id=call_id,
                            waited_ms=round(waited_ms),
                            timeout_ms=wait_ms,
                        )
            except Exception:
                logger.debug("Greeting fallback check failed", call_id=call_id, exc_info=True)
            return "error"
        if not filler:
            self._decrement_buffered_bytes(call_id, len(frame))
            # Reset low-buffer backoff when sending real audio
            try:
                info = self.active_streams.get(call_id)
                if info is not None and 'empty_backoff_ticks' in info:
                    info['empty_backoff_ticks'] = 0
            except Exception:
                pass
        try:
            _STREAM_FRAMES_SENT_TOTAL.inc(1)
        except Exception:
            pass
        info = self.active_streams.get(call_id)
        now = time.time()
        if info is not None:
            try:
                info['frames_sent'] = int(info.get('frames_sent', 0)) + 1
                info['last_frame_ts'] = now
                info['last_emit_was_filler'] = bool(filler)
                if not filler:
                    info['last_real_emit_ts'] = now
                    info['idle_ticks'] = 0
            except Exception:
                pass
            if filler:
                try:
                    _STREAM_UNDERFLOW_EVENTS_TOTAL.inc(1)
                    _STREAM_FILLER_BYTES_TOTAL.inc(len(frame))
                except Exception:
                    pass
                try:
                    info['underflow_events'] = int(info.get('underflow_events', 0)) + 1
                    info['filler_frames'] = int(info.get('filler_frames', 0)) + 1
                except Exception:
                    pass
        return "sent"
    
    async def _process_audio_chunk(self, call_id: str, chunk: bytes) -> Optional[bytes]:
        """Process audio chunk for streaming transport."""
        if not chunk:
            return None

        # ExternalMedia/RTP path: pass-through (conversion handled by RTP layer)
        if self.audio_transport != "audiosocket":
            return chunk

        try:
            # Ensure chunk is bytes for audioop/mulaw conversion
            if not isinstance(chunk, (bytes, bytearray)):
                try:
                    chunk = bytes(chunk)
                except Exception:
                    logger.debug("Non-bytes audio chunk could not be coerced", call_id=call_id, chunk_type=str(type(chunk)))
            stream_info = self.active_streams.get(call_id, {}) if call_id in self.active_streams else {}

            target_fmt = (
                self._canonicalize_encoding(stream_info.get("target_format"))
                or self._canonicalize_encoding(self.audiosocket_format)
                or "ulaw"
            )
            try:
                target_rate = int(stream_info.get("target_sample_rate", self.sample_rate))
            except Exception:
                target_rate = int(self.sample_rate)
            if target_rate <= 0:
                target_rate = self._default_sample_rate_for_format(target_fmt, int(self.sample_rate))

            src_encoding_raw = self._canonicalize_encoding(stream_info.get("source_encoding"))
            try:
                src_rate = int(stream_info.get("source_sample_rate") or target_rate)
            except Exception:
                src_rate = target_rate
            if not src_encoding_raw:
                src_encoding_raw = "slin16"

            # Determine if we must swap bytes for PCM16 egress
            # Fast path: already matches target format and rate
            if (
                self._is_mulaw(src_encoding_raw)
                and self._is_mulaw(target_fmt)
                and src_rate == target_rate
            ):
                self._resample_states[call_id] = None
                
                # μ-law fast-path sanity guard: verify round-trip μ-law -> PCM16 -> μ-law preserves bytes
                guard_ok = True
                try:
                    if getattr(self, 'ulaw_fastpath_guard', True):
                        window = chunk[: min(320, len(chunk))]
                        if window:
                            back_pcm_guard = mulaw_to_pcm16le(window)
                            re_ulaw_guard = pcm16le_to_mulaw(back_pcm_guard)
                            if len(re_ulaw_guard) != len(window):
                                guard_ok = False
                            else:
                                matches = 0
                                lw = len(window)
                                for i in range(lw):
                                    if window[i] == re_ulaw_guard[i]:
                                        matches += 1
                                ratio = matches / float(max(1, lw))
                                if ratio < 0.98:
                                    guard_ok = False
                except Exception:
                    guard_ok = True

                # If guard passes and normalizer is disabled, keep provider μ-law as-is (passthrough).
                # If normalizer is enabled, override passthrough to allow deterministic gain normalization.
                if guard_ok and not (self.normalizer_enabled and self.normalizer_target_rms > 0):
                    try:
                        logger.info(
                            "μ-law PASSTHROUGH - Skipping processing (normalizer disabled)",
                            call_id=call_id,
                            chunk_bytes=len(chunk),
                            source=src_encoding_raw,
                            target=target_fmt,
                            sample_rate=src_rate,
                        )
                    except Exception:
                        pass
                    return chunk
                else:
                    try:
                        info = self.active_streams.get(call_id, {}) if call_id in self.active_streams else {}
                        if not info.get('normalizer_passthrough_override_logged'):
                            logger.info(
                                "μ-law PASSTHROUGH OVERRIDDEN - Normalizer enabled",
                                call_id=call_id,
                                target_rms=int(self.normalizer_target_rms),
                                max_gain_db=float(self.normalizer_max_gain_db),
                            )
                            info['normalizer_passthrough_override_logged'] = True
                            if call_id in self.active_streams:
                                self.active_streams[call_id] = info
                    except Exception:
                        pass
                # Guard failed: decode → normalize (bounded) → optional limit → re-encode back to μ-law
                try:
                    logger.debug("MULAW DECODE ATTEMPT", call_id=call_id, chunk_size=len(chunk), chunk_type=type(chunk).__name__)
                    back_pcm = mulaw_to_pcm16le(chunk)
                    logger.debug("MULAW DECODE SUCCESS", call_id=call_id, pcm_size=len(back_pcm))
                except Exception as e:
                    logger.error("MULAW DECODE FAILED", call_id=call_id, error=str(e), chunk_size=len(chunk), exc_info=True)
                    back_pcm = b""

                working_pcm = back_pcm
                
                # Trim leading silence before normalization
                if working_pcm:
                    original_size = len(working_pcm)
                    working_pcm = self._trim_leading_silence(working_pcm, threshold_rms=100)
                    if len(working_pcm) < original_size:
                        logger.debug("SILENCE TRIMMING APPLIED",
                                    call_id=call_id,
                                    original_bytes=original_size,
                                    trimmed_bytes=len(working_pcm),
                                    removed_bytes=original_size - len(working_pcm))
                
                try:
                    logger.debug(
                        "NORMALIZER CONDITION CHECK (ulaw guarded)",
                        call_id=call_id,
                        working_pcm_size=(len(working_pcm) if working_pcm else 0),
                        normalizer_enabled=bool(self.normalizer_enabled),
                        target_rms=int(self.normalizer_target_rms),
                    )
                    if working_pcm and self.normalizer_enabled and self.normalizer_target_rms > 0:
                        logger.debug("ENTERING NORMALIZER (ulaw guarded)", call_id=call_id)
                        working_pcm = self._apply_normalizer(working_pcm, self.normalizer_target_rms, self.normalizer_max_gain_db)
                    elif not working_pcm:
                        logger.warning("EMPTY PCM AFTER DECODE - NORMALIZER SKIPPED", call_id=call_id)
                except Exception:
                    logger.debug("Normalizer failed in μ-law guarded path", call_id=call_id, exc_info=True)
                try:
                    if working_pcm and self.limiter_enabled:
                        working_pcm = self._apply_soft_limiter(working_pcm, self.limiter_headroom_ratio)
                except Exception:
                    pass
                try:
                    ulaw_bytes = pcm16le_to_mulaw(working_pcm) if working_pcm else chunk
                except Exception:
                    ulaw_bytes = chunk

                # Diagnostics on processed PCM
                try:
                    if getattr(self, 'diag_enable_taps', False) and call_id in self.active_streams:
                        info = self.active_streams.get(call_id, {})
                        try:
                            rate = int(target_rate)
                        except Exception:
                            rate = int(self.sample_rate)
                        try:
                            if not info.get('tap_first_snapshot_done', False):
                                stream_id_first = str(info.get('stream_id', 'seg'))
                                if working_pcm:
                                    fn2 = os.path.join(self.diag_out_dir, f"post_compand_pcm16_{call_id}_{stream_id_first}_first.wav")
                                    with wave.open(fn2, 'wb') as wf:
                                        wf.setnchannels(1)
                                        wf.setsampwidth(2)
                                        wf.setframerate(rate)
                                        wf.writeframes(working_pcm)
                                    try:
                                        os.chmod(fn2, 0o600)
                                    except Exception:
                                        pass
                                    logger.info("Wrote post-compand PCM16 tap snapshot", call_id=call_id, stream_id=stream_id_first, path=fn2, bytes=len(working_pcm), rate=rate, snapshot="first")
                                info['tap_first_snapshot_done'] = True
                        except Exception:
                            logger.debug("Fast-path first-chunk tap snapshot failed", call_id=call_id, exc_info=True)
                        try:
                            pre_lim = max(0, int(self.diag_pre_secs * rate * 2))
                        except Exception:
                            pre_lim = 0
                        if pre_lim and isinstance(info.get('tap_pre_pcm16'), (bytearray, bytes)) and working_pcm:
                            pre_buf = info['tap_pre_pcm16']
                            if len(pre_buf) < pre_lim:
                                need = pre_lim - len(pre_buf)
                                pre_buf.extend(working_pcm[:need])
                        try:
                            post_lim = max(0, int(self.diag_post_secs * rate * 2))
                        except Exception:
                            post_lim = 0
                        if post_lim and isinstance(info.get('tap_post_pcm16'), (bytearray, bytes)) and working_pcm:
                            post_buf = info['tap_post_pcm16']
                            if len(post_buf) < post_lim:
                                need2 = post_lim - len(post_buf)
                                post_buf.extend(working_pcm[:need2])
                        # Maintain post window in guarded path
                        try:
                            win_rate = int(rate)
                        except Exception:
                            win_rate = int(self.sample_rate)
                        try:
                            win_bytes = max(1, int(win_rate * 0.2 * 2))
                        except Exception:
                            win_bytes = 3200
                        if isinstance(info.get('tap_first_window_post'), bytearray) and working_pcm:
                            post_w = info['tap_first_window_post']
                            if len(post_w) < win_bytes:
                                needw2 = win_bytes - len(post_w)
                                post_w.extend(working_pcm[:needw2])
                            if not info.get('tap_first_window_done') and len(post_w) >= win_bytes:
                                sid = str(info.get('stream_id', 'seg'))
                                try:
                                    fnq200 = os.path.join(self.diag_out_dir, f"post_compand_pcm16_{call_id}_{sid}_first200ms.wav")
                                    with wave.open(fnq200, 'wb') as wf:
                                        wf.setnchannels(1)
                                        wf.setsampwidth(2)
                                        wf.setframerate(win_rate)
                                        wf.writeframes(bytes(post_w[:win_bytes]))
                                    try:
                                        os.chmod(fnq200, 0o600)
                                    except Exception:
                                        pass
                                    logger.info("Wrote post-compand 200ms snapshot", call_id=call_id, stream_id=sid, path=fnq200, bytes=win_bytes, rate=win_rate, snapshot="first200ms")
                                except Exception:
                                    logger.warning("Failed 200ms post snapshot (guarded)", call_id=call_id, stream_id=sid, rate=win_rate, exc_info=True)
                                info['tap_first_window_done'] = True
                except Exception:
                    logger.debug("Fast-path tap capture failed", call_id=call_id, exc_info=True)
                return ulaw_bytes
            
            # NEW: Fast path for μ-law → PCM16 (AudioSocket requires PCM16)
            # Just decode, skip attack/normalize/limiter/encode
            if (
                self._is_mulaw(src_encoding_raw)
                and target_fmt in ("slin", "slin16", "linear16", "pcm16")
                and src_rate == target_rate
            ):
                self._resample_states[call_id] = None
                try:
                    logger.info(
                        "🎯 μ-law → PCM16 FAST PATH - Simple decode only",
                        call_id=call_id,
                        chunk_bytes=len(chunk),
                        source=src_encoding_raw,
                        target=target_fmt,
                        sample_rate=src_rate,
                    )
                except Exception:
                    pass
                # Simple decode: mulaw → PCM16
                try:
                    pcm16_bytes = mulaw_to_pcm16le(chunk)
                    # Deterministic normalization on fast-path when enabled
                    try:
                        if self.normalizer_enabled and self.normalizer_target_rms > 0 and pcm16_bytes:
                            pcm16_bytes = self._apply_normalizer(pcm16_bytes, self.normalizer_target_rms, self.normalizer_max_gain_db)
                            try:
                                info = self.active_streams.get(call_id, {}) if call_id in self.active_streams else {}
                                if not info.get('normalizer_applied_fastpath_logged'):
                                    logger.info(
                                        "Normalizer applied (mulaw→pcm fast path)",
                                        call_id=call_id,
                                        target_rms=int(self.normalizer_target_rms),
                                        max_gain_db=float(self.normalizer_max_gain_db),
                                    )
                                    info['normalizer_applied_fastpath_logged'] = True
                                    if call_id in self.active_streams:
                                        self.active_streams[call_id] = info
                            except Exception:
                                pass
                    except Exception:
                        logger.debug("Normalizer failed in mulaw→pcm fast path", call_id=call_id, exc_info=True)
                    # Accumulate diagnostics taps and snapshots in fast path
                    try:
                        if getattr(self, 'diag_enable_taps', False) and call_id in self.active_streams and pcm16_bytes:
                            info = self.active_streams.get(call_id, {})
                            try:
                                rate = int(target_rate)
                            except Exception:
                                rate = int(self.sample_rate)
                            # Per-segment pre/post buffers
                            try:
                                pre_lim = max(0, int(self.diag_pre_secs * rate * 2))
                            except Exception:
                                pre_lim = 0
                            if pre_lim and isinstance(info.get('tap_pre_pcm16'), (bytearray, bytes)):
                                pre_buf = info['tap_pre_pcm16']
                                if len(pre_buf) < pre_lim:
                                    need = pre_lim - len(pre_buf)
                                    pre_buf.extend(pcm16_bytes[:need])
                            try:
                                post_lim = max(0, int(self.diag_post_secs * rate * 2))
                            except Exception:
                                post_lim = 0
                            if post_lim and isinstance(info.get('tap_post_pcm16'), (bytearray, bytes)):
                                post_buf = info['tap_post_pcm16']
                                if len(post_buf) < post_lim:
                                    need2 = post_lim - len(post_buf)
                                    post_buf.extend(pcm16_bytes[:need2])
                            # Call-level accumulation
                            self._append_call_taps(call_id, pcm16_bytes, pcm16_bytes, int(rate))
                            # First-window (200ms) snapshots
                            try:
                                win_rate = int(rate)
                            except Exception:
                                win_rate = int(self.sample_rate)
                            try:
                                win_bytes = max(1, int(win_rate * 0.2 * 2))
                            except Exception:
                                win_bytes = 3200
                            try:
                                if isinstance(info.get('tap_first_window_pre'), bytearray):
                                    pre_w = info['tap_first_window_pre']
                                    if len(pre_w) < win_bytes:
                                        needw = win_bytes - len(pre_w)
                                        pre_w.extend(pcm16_bytes[:needw])
                                if isinstance(info.get('tap_first_window_post'), bytearray):
                                    post_w = info['tap_first_window_post']
                                    if len(post_w) < win_bytes:
                                        needw2 = win_bytes - len(post_w)
                                        post_w.extend(pcm16_bytes[:needw2])
                                if not info.get('tap_first_window_done'):
                                    pre_w = info.get('tap_first_window_pre') or bytearray()
                                    post_w = info.get('tap_first_window_post') or bytearray()
                                    if len(pre_w) >= win_bytes and len(post_w) >= win_bytes:
                                        sid = str(info.get('stream_id', 'seg'))
                                        try:
                                            fnp200 = os.path.join(self.diag_out_dir, f"pre_compand_pcm16_{call_id}_{sid}_first200ms.wav")
                                            with wave.open(fnp200, 'wb') as wf:
                                                wf.setnchannels(1)
                                                wf.setsampwidth(2)
                                                wf.setframerate(win_rate)
                                                wf.writeframes(bytes(pre_w[:win_bytes]))
                                            try:
                                                os.chmod(fnp200, 0o600)
                                            except Exception:
                                                pass
                                            logger.info("Wrote pre-compand 200ms snapshot", call_id=call_id, stream_id=sid, path=fnp200, bytes=win_bytes, rate=win_rate, snapshot="first200ms")
                                        except Exception:
                                            logger.warning("Failed 200ms pre snapshot (mulaw->pcm fast)", call_id=call_id, stream_id=sid, rate=win_rate, exc_info=True)
                                        try:
                                            fnq200 = os.path.join(self.diag_out_dir, f"post_compand_pcm16_{call_id}_{sid}_first200ms.wav")
                                            with wave.open(fnq200, 'wb') as wf:
                                                wf.setnchannels(1)
                                                wf.setsampwidth(2)
                                                wf.setframerate(win_rate)
                                                wf.writeframes(bytes(post_w[:win_bytes]))
                                            try:
                                                os.chmod(fnq200, 0o600)
                                            except Exception:
                                                pass
                                            logger.info("Wrote post-compand 200ms snapshot", call_id=call_id, stream_id=sid, path=fnq200, bytes=win_bytes, rate=win_rate, snapshot="first200ms")
                                        except Exception:
                                            logger.warning("Failed 200ms post snapshot (mulaw->pcm fast)", call_id=call_id, stream_id=sid, rate=win_rate, exc_info=True)
                                        info['tap_first_window_done'] = True
                            except Exception:
                                logger.debug("First-window snapshot failed (mulaw->pcm fast path)", call_id=call_id, exc_info=True)
                    except Exception:
                        logger.debug("Fast-path tap accumulation failed (mulaw->pcm)", call_id=call_id, exc_info=True)
                    return pcm16_bytes
                except Exception as e:
                    logger.error(
                        "μ-law decode failed in fast path",
                        call_id=call_id,
                        error=str(e),
                        exc_info=True,
                    )
                    return chunk  # Fallback to original
            
            if (
                src_encoding_raw in ("slin", "slin16", "linear16", "pcm16")
                and target_fmt in ("slin", "slin16", "linear16", "pcm16")
                and src_rate == target_rate
            ):
                # Fast path PCM16->PCM16: still apply egress swap if required (with auto-probe)
                self._resample_states[call_id] = None
                return chunk

            working = chunk
            resample_state = self._resample_states.get(call_id)

            # Convert source to PCM16 for resampling/format conversion when needed
            if self._is_mulaw(src_encoding_raw):
                working = mulaw_to_pcm16le(working)
                working, _ = self._remove_dc_from_pcm16(
                    call_id,
                    working,
                    threshold=256,
                    stage="stream-pipeline",
                )
                working = self._apply_dc_block(call_id, working)
                src_encoding = "pcm16"
            else:
                # Source is PCM16. Probe endianness once and auto-correct to little-endian for downstream ops.
                src_encoding = "pcm16"
                try:
                    if not stream_info.get('src_endian_probe_done', False):
                        import audioop
                        rms_native = audioop.rms(working, 2)
                        avg_native = audioop.avg(working, 2)
                        try:
                            swapped = audioop.byteswap(working, 2)
                            rms_swapped = audioop.rms(swapped, 2)
                            avg_swapped = audioop.avg(swapped, 2)
                        except Exception:
                            swapped = None
                            rms_swapped = 0
                            avg_swapped = 0
                        stream_info['src_endian_probe_done'] = True
                        # Decide if swapped is clearly better: much higher RMS or much lower DC offset
                        prefer_swapped = False
                        if swapped is not None:
                            if rms_swapped >= max(1024, 4 * max(1, rms_native)):
                                prefer_swapped = True
                            else:
                                try:
                                    if abs(avg_native) >= 8 * max(1, abs(avg_swapped)) and rms_swapped >= max(256, rms_native // 2):
                                        prefer_swapped = True
                                except Exception:
                                    pass
                        try:
                            logger.info(
                                "Streaming source PCM16 endian probe",
                                call_id=call_id,
                                rms_native=rms_native,
                                rms_swapped=rms_swapped,
                                avg_native=avg_native,
                                avg_swapped=avg_swapped,
                                prefer_swapped=prefer_swapped,
                            )
                        except Exception:
                            pass
                        if prefer_swapped and swapped is not None:
                            stream_info['src_endian_swapped'] = True
                            working = swapped
                    else:
                        if stream_info.get('src_endian_swapped', False):
                            try:
                                import audioop
                                working = audioop.byteswap(working, 2)
                            except Exception:
                                pass
                except Exception:
                    # Probe failures should not break streaming; continue with native bytes
                    pass

                # Remove significant DC offset before further processing
                try:
                    import audioop
                    dc = audioop.avg(working, 2)
                    if abs(dc) >= 1024:
                        try:
                            working = audioop.bias(working, 2, -int(dc))
                            if not stream_info.get('src_dc_correction_logged', False):
                                logger.info(
                                    "Streaming source PCM16 DC correction applied",
                                    call_id=call_id,
                                    dc_before=int(dc),
                                )
                                stream_info['src_dc_correction_logged'] = True
                        except Exception:
                            pass
                except Exception:
                    pass
                working = self._apply_dc_block(call_id, working)

            # Resample to target rate when necessary
            if src_rate != target_rate:
                working, resample_state = resample_audio(
                    working,
                    src_rate,
                    target_rate,
                    state=resample_state,
                )
            else:
                resample_state = None
            # Post-resample DC offset correction (secondary clamp)
            try:
                import audioop
                dc2 = audioop.avg(working, 2)
                # Use a lower threshold post-resample to clamp small residual bias
                if abs(dc2) >= 256:
                    working = audioop.bias(working, 2, -int(dc2))
                    if not stream_info.get('post_resample_dc_correction_logged', False):
                        logger.info(
                            "Streaming PCM16 post-resample DC correction applied",
                            call_id=call_id,
                            dc_before=int(dc2),
                        )
                        stream_info['post_resample_dc_correction_logged'] = True
            except Exception:
                pass
            working = self._apply_dc_block(call_id, working)
            self._resample_states[call_id] = resample_state

            # Apply a light DC-block filter on PCM16 prior to target encoding
            try:
                if not self._is_mulaw(target_fmt):
                    working = self._apply_dc_block(call_id, working)
            except Exception:
                pass

            # Convert to target encoding
            if self._is_mulaw(target_fmt):
                working, _ = self._remove_dc_from_pcm16(
                    call_id,
                    working,
                    threshold=256,
                    stage="stream-pre-encode",
                )
                working = self._apply_dc_block(call_id, working)
                # Apply short attack envelope at segment start and a soft limiter before μ-law encode
                try:
                    info = self.active_streams.get(call_id, {})
                    rate_attack = int(target_rate) if isinstance(target_rate, int) else int(self.sample_rate)
                    working = self._apply_attack_envelope(call_id, working, rate_attack, info)
                except Exception:
                    pass
                # Apply make-up gain normalization before limiter if enabled
                try:
                    logger.debug(
                        "NORMALIZER CONDITION CHECK (pcm->ulaw)",
                        call_id=call_id,
                        normalizer_enabled=bool(self.normalizer_enabled),
                        target_rms=int(self.normalizer_target_rms),
                        pcm_size=len(working) if working else 0,
                    )
                    if self.normalizer_enabled and self.normalizer_target_rms > 0:
                        logger.debug("ENTERING NORMALIZER (pcm->ulaw)", call_id=call_id)
                        working = self._apply_normalizer(working, self.normalizer_target_rms, self.normalizer_max_gain_db)
                        # One-time info log per stream to confirm normalizer activation in production
                        try:
                            sinfo = self.active_streams.get(call_id, {})
                            if not sinfo.get('normalizer_applied_ulaw_logged'):
                                logger.info(
                                    "Normalizer applied (pcm->ulaw encode path)",
                                    call_id=call_id,
                                    target_rms=int(self.normalizer_target_rms),
                                    max_gain_db=float(self.normalizer_max_gain_db),
                                )
                                sinfo['normalizer_applied_ulaw_logged'] = True
                                self.active_streams[call_id] = sinfo
                        except Exception:
                            pass
                    else:
                        logger.warning(
                            "NORMALIZER SKIPPED - WHY? (pcm->ulaw)",
                            call_id=call_id,
                            normalizer_enabled=bool(self.normalizer_enabled),
                            target_rms=int(self.normalizer_target_rms),
                        )
                except Exception:
                    logger.debug("Normalizer failed; continuing without gain", call_id=call_id, exc_info=True)
                if self.limiter_enabled:
                    try:
                        working = self._apply_soft_limiter(working, self.limiter_headroom_ratio)
                    except Exception:
                        pass
                if getattr(self, 'diag_enable_taps', False) and call_id in self.active_streams:
                    info = self.active_streams.get(call_id, {})
                    try:
                        rate = int(target_rate)
                    except Exception:
                        rate = target_rate
                    try:
                        pre_lim = max(0, int(self.diag_pre_secs * rate * 2))
                    except Exception:
                        pre_lim = 0
                    if pre_lim and isinstance(info.get('tap_pre_pcm16'), (bytearray, bytes)):
                        pre_buf = info['tap_pre_pcm16']
                        if len(pre_buf) < pre_lim:
                            need = pre_lim - len(pre_buf)
                            pre_buf.extend(working[:need])
                    # Encode to μ-law and back-convert for post snapshot
                    ulaw_bytes = pcm16le_to_mulaw(working)
                    back_pcm = mulaw_to_pcm16le(ulaw_bytes)
                    # First-chunk direct snapshot: write from current frame data if not yet snapped
                    try:
                        if not info.get('tap_first_snapshot_done', False):
                            stream_id_first = str(info.get('stream_id', 'seg'))
                            # Pre-compand snapshot
                            if working:
                                fn = os.path.join(self.diag_out_dir, f"pre_compand_pcm16_{call_id}_{stream_id_first}_first.wav")
                                try:
                                    with wave.open(fn, 'wb') as wf:
                                        wf.setnchannels(1)
                                        wf.setsampwidth(2)
                                        wf.setframerate(int(rate) if isinstance(rate, int) else int(self.sample_rate))
                                        wf.writeframes(working)
                                    try:
                                        os.chmod(fn, 0o600)
                                    except Exception:
                                        pass
                                    logger.info("Wrote pre-compand PCM16 tap snapshot", call_id=call_id, stream_id=stream_id_first, path=fn, bytes=len(working), rate=rate, snapshot="first")
                                except Exception:
                                    logger.warning("Failed to write pre-compand tap snapshot", call_id=call_id, stream_id=stream_id_first, path=fn, rate=rate, snapshot="first", exc_info=True)
                            # Post-compand snapshot (decoded back to PCM16)
                            if back_pcm:
                                fn2 = os.path.join(self.diag_out_dir, f"post_compand_pcm16_{call_id}_{stream_id_first}_first.wav")
                                try:
                                    with wave.open(fn2, 'wb') as wf:
                                        wf.setnchannels(1)
                                        wf.setsampwidth(2)
                                        wf.setframerate(int(rate) if isinstance(rate, int) else int(self.sample_rate))
                                        wf.writeframes(back_pcm)
                                    try:
                                        os.chmod(fn2, 0o600)
                                    except Exception:
                                        pass
                                    logger.info("Wrote post-compand PCM16 tap snapshot", call_id=call_id, stream_id=stream_id_first, path=fn2, bytes=len(back_pcm), rate=rate, snapshot="first")
                                except Exception:
                                    logger.warning("Failed to write post-compand tap snapshot", call_id=call_id, stream_id=stream_id_first, path=fn2, rate=rate, snapshot="first", exc_info=True)
                            info['tap_first_snapshot_done'] = True
                    except Exception:
                        logger.debug("First-chunk tap snapshot failed", call_id=call_id, exc_info=True)
                    try:
                        post_lim = max(0, int(self.diag_post_secs * rate * 2))
                    except Exception:
                        post_lim = 0
                    if post_lim and isinstance(info.get('tap_post_pcm16'), (bytearray, bytes)):
                        post_buf = info['tap_post_pcm16']
                        if len(post_buf) < post_lim:
                            need2 = post_lim - len(post_buf)
                            post_buf.extend(back_pcm[:need2])
                    tap_rate = int(rate) if isinstance(rate, int) else int(self.sample_rate)
                    self._append_call_taps(call_id, working, back_pcm, tap_rate)
                    # First-window (200ms) per-segment snapshots
                    try:
                        win_rate = int(rate) if isinstance(rate, int) else int(self.sample_rate)
                    except Exception:
                        win_rate = int(self.sample_rate)
                    try:
                        win_bytes = max(1, int(win_rate * 0.2 * 2))
                    except Exception:
                        win_bytes = 3200  # ~200ms @ 8k, 16-bit
                    try:
                        if isinstance(info.get('tap_first_window_pre'), bytearray) and working:
                            pre_w = info['tap_first_window_pre']
                            if len(pre_w) < win_bytes:
                                needw = win_bytes - len(pre_w)
                                pre_w.extend(working[:needw])
                    except Exception:
                        pass
                try:
                    if isinstance(info.get('tap_first_window_post'), bytearray) and back_pcm:
                        post_w = info['tap_first_window_post']
                        if len(post_w) < win_bytes:
                            needw2 = win_bytes - len(post_w)
                            post_w.extend(back_pcm[:needw2])
                            if not info.get('tap_first_window_done'):
                                pre_w = info.get('tap_first_window_pre') or bytearray()
                                post_w = info.get('tap_first_window_post') or bytearray()
                                if len(pre_w) >= win_bytes and len(post_w) >= win_bytes:
                                    sid = str(info.get('stream_id', 'seg'))
                                    # Write 200ms snapshots
                                    try:
                                        fnp200 = os.path.join(self.diag_out_dir, f"pre_compand_pcm16_{call_id}_{sid}_first200ms.wav")
                                        with wave.open(fnp200, 'wb') as wf:
                                            wf.setnchannels(1)
                                            wf.setsampwidth(2)
                                            wf.setframerate(win_rate)
                                            wf.writeframes(bytes(pre_w[:win_bytes]))
                                        try:
                                            os.chmod(fnp200, 0o600)
                                        except Exception:
                                            pass
                                        logger.info("Wrote pre-compand 200ms snapshot", call_id=call_id, stream_id=sid, path=fnp200, bytes=win_bytes, rate=win_rate, snapshot="first200ms")
                                    except Exception:
                                        logger.warning("Failed 200ms pre snapshot", call_id=call_id, stream_id=sid, rate=win_rate, exc_info=True)
                                    try:
                                        fnq200 = os.path.join(self.diag_out_dir, f"post_compand_pcm16_{call_id}_{sid}_first200ms.wav")
                                        with wave.open(fnq200, 'wb') as wf:
                                            wf.setnchannels(1)
                                            wf.setsampwidth(2)
                                            wf.setframerate(win_rate)
                                            wf.writeframes(bytes(post_w[:win_bytes]))
                                        try:
                                            os.chmod(fnq200, 0o600)
                                        except Exception:
                                            pass
                                        logger.info("Wrote post-compand 200ms snapshot", call_id=call_id, stream_id=sid, path=fnq200, bytes=win_bytes, rate=win_rate, snapshot="first200ms")
                                    except Exception:
                                        logger.warning("Failed 200ms post snapshot", call_id=call_id, stream_id=sid, rate=win_rate, exc_info=True)
                                    info['tap_first_window_done'] = True
                except Exception:
                    logger.debug("First-window snapshot failed (ulaw)", call_id=call_id, exc_info=True)
                    return ulaw_bytes
                return pcm16le_to_mulaw(working)
            # Otherwise target PCM16, apply optional envelope/limiter, with optional (or auto) egress byteswap
            # Short attack envelope to avoid hot-start clicks on PCM path too
            try:
                info = self.active_streams.get(call_id, {})
                try:
                    rate = int(target_rate)
                except Exception:
                    rate = target_rate
                working = self._apply_attack_envelope(call_id, working, int(rate) if isinstance(rate, int) else int(self.sample_rate), info)
            except Exception:
                pass
            out_pcm = working
            # Apply normalization and soft limiter on PCM egress to ensure consistent loudness
            try:
                logger.debug(
                    "🔊 PCM EGRESS: Normalizer check",
                    call_id=call_id,
                    normalizer_enabled=bool(self.normalizer_enabled),
                    target_rms=int(self.normalizer_target_rms),
                    has_pcm=bool(out_pcm),
                    pcm_size=len(out_pcm) if out_pcm else 0,
                )
                if self.normalizer_enabled and self.normalizer_target_rms > 0 and out_pcm:
                    logger.debug("🔊 PCM EGRESS: Applying normalizer", call_id=call_id)
                    out_pcm = self._apply_normalizer(out_pcm, self.normalizer_target_rms, self.normalizer_max_gain_db)
                    logger.info(
                        "🔊 Normalizer applied (pcm egress)",
                        call_id=call_id,
                        target_rms=int(self.normalizer_target_rms),
                        max_gain_db=float(self.normalizer_max_gain_db),
                    )
                else:
                    logger.warning(
                        "🔊 PCM EGRESS: Normalizer SKIPPED",
                        call_id=call_id,
                        normalizer_enabled=bool(self.normalizer_enabled),
                        target_rms=int(self.normalizer_target_rms),
                        has_pcm=bool(out_pcm),
                    )
                if self.limiter_enabled and out_pcm:
                    out_pcm = self._apply_soft_limiter(out_pcm, self.limiter_headroom_ratio)
            except Exception as e:
                logger.error(
                    "🔊 PCM EGRESS: Normalizer exception",
                    call_id=call_id,
                    error=str(e),
                    exc_info=True,
                )
            if getattr(self, 'diag_enable_taps', False) and call_id in self.active_streams:
                info = self.active_streams.get(call_id, {})
                try:
                    rate = int(target_rate)
                except Exception:
                    rate = target_rate
                # First-chunk direct snapshot: use current PCM16 frame data
                try:
                    if not info.get('tap_first_snapshot_done', False):
                        stream_id_first = str(info.get('stream_id', 'seg'))
                        if working:
                            fnp = os.path.join(self.diag_out_dir, f"pre_compand_pcm16_{call_id}_{stream_id_first}_first.wav")
                            try:
                                with wave.open(fnp, 'wb') as wf:
                                    wf.setnchannels(1)
                                    wf.setsampwidth(2)
                                    wf.setframerate(int(rate) if isinstance(rate, int) else int(self.sample_rate))
                                    wf.writeframes(working)
                                try:
                                    os.chmod(fnp, 0o600)
                                except Exception:
                                    pass
                                logger.info("Wrote pre-compand PCM16 tap snapshot", call_id=call_id, stream_id=stream_id_first, path=fnp, bytes=len(working), rate=rate, snapshot="first")
                            except Exception:
                                logger.warning("Failed to write pre-compand tap snapshot", call_id=call_id, stream_id=stream_id_first, path=fnp, rate=rate, snapshot="first", exc_info=True)
                        if out_pcm:
                            fnq = os.path.join(self.diag_out_dir, f"post_compand_pcm16_{call_id}_{stream_id_first}_first.wav")
                            try:
                                with wave.open(fnq, 'wb') as wf:
                                    wf.setnchannels(1)
                                    wf.setsampwidth(2)
                                    wf.setframerate(int(rate) if isinstance(rate, int) else int(self.sample_rate))
                                    wf.writeframes(out_pcm)
                                try:
                                    os.chmod(fnq, 0o600)
                                except Exception:
                                    pass
                                logger.info("Wrote post-compand PCM16 tap snapshot", call_id=call_id, stream_id=stream_id_first, path=fnq, bytes=len(out_pcm), rate=rate, snapshot="first")
                            except Exception:
                                logger.warning("Failed to write post-compand tap snapshot", call_id=call_id, stream_id=stream_id_first, path=fnq, rate=rate, snapshot="first", exc_info=True)
                        info['tap_first_snapshot_done'] = True
                except Exception:
                    logger.debug("First-chunk tap snapshot failed (PCM)", call_id=call_id, exc_info=True)
                # Continue with buffer accumulation
                try:
                    pre_lim = max(0, int(self.diag_pre_secs * rate * 2))
                except Exception:
                    pre_lim = 0
                if pre_lim and isinstance(info.get('tap_pre_pcm16'), (bytearray, bytes)):
                    pre_buf = info['tap_pre_pcm16']
                    if len(pre_buf) < pre_lim:
                        need = pre_lim - len(pre_buf)
                        pre_buf.extend(working[:need])
                try:
                    post_lim = max(0, int(self.diag_post_secs * rate * 2))
                except Exception:
                    post_lim = 0
                if post_lim and isinstance(info.get('tap_post_pcm16'), (bytearray, bytes)):
                    post_buf = info['tap_post_pcm16']
                    if len(post_buf) < post_lim:
                        need2 = post_lim - len(post_buf)
                        post_buf.extend(out_pcm[:need2])
                tap_rate = int(rate) if isinstance(rate, int) else int(self.sample_rate)
                self._append_call_taps(call_id, working, out_pcm, tap_rate)
                # First-window (200ms) per-segment snapshots
                try:
                    win_rate = int(target_rate) if isinstance(target_rate, int) else int(self.sample_rate)
                except Exception:
                    win_rate = int(self.sample_rate)
                try:
                    win_bytes = max(1, int(win_rate * 0.2 * 2))
                except Exception:
                    win_bytes = 3200
                try:
                    if isinstance(info.get('tap_first_window_pre'), bytearray) and working:
                        pre_w = info['tap_first_window_pre']
                        if len(pre_w) < win_bytes:
                            needw = win_bytes - len(pre_w)
                            pre_w.extend(working[:needw])
                    if isinstance(info.get('tap_first_window_post'), bytearray) and out_pcm:
                        post_w = info['tap_first_window_post']
                        if len(post_w) < win_bytes:
                            needw2 = win_bytes - len(post_w)
                            post_w.extend(out_pcm[:needw2])
                    if not info.get('tap_first_window_done'):
                        pre_w = info.get('tap_first_window_pre') or bytearray()
                        post_w = info.get('tap_first_window_post') or bytearray()
                        if len(pre_w) >= win_bytes and len(post_w) >= win_bytes:
                            sid = str(info.get('stream_id', 'seg'))
                            try:
                                fnp200 = os.path.join(self.diag_out_dir, f"pre_compand_pcm16_{call_id}_{sid}_first200ms.wav")
                                with wave.open(fnp200, 'wb') as wf:
                                    wf.setnchannels(1)
                                    wf.setsampwidth(2)
                                    wf.setframerate(win_rate)
                                    wf.writeframes(bytes(pre_w[:win_bytes]))
                                try:
                                    os.chmod(fnp200, 0o600)
                                except Exception:
                                    pass
                                logger.info("Wrote pre-compand 200ms snapshot", call_id=call_id, stream_id=sid, path=fnp200, bytes=win_bytes, rate=win_rate, snapshot="first200ms")
                            except Exception:
                                logger.warning("Failed 200ms pre snapshot", call_id=call_id, stream_id=sid, rate=win_rate, exc_info=True)
                            try:
                                fnq200 = os.path.join(self.diag_out_dir, f"post_compand_pcm16_{call_id}_{sid}_first200ms.wav")
                                with wave.open(fnq200, 'wb') as wf:
                                    wf.setnchannels(1)
                                    wf.setsampwidth(2)
                                    wf.setframerate(win_rate)
                                    wf.writeframes(bytes(post_w[:win_bytes]))
                                try:
                                    os.chmod(fnq200, 0o600)
                                except Exception:
                                    pass
                                logger.info("Wrote post-compand 200ms snapshot", call_id=call_id, stream_id=sid, path=fnq200, bytes=win_bytes, rate=win_rate, snapshot="first200ms")
                            except Exception:
                                logger.warning("Failed 200ms post snapshot", call_id=call_id, stream_id=sid, rate=win_rate, exc_info=True)
                            info['tap_first_window_done'] = True
                except Exception:
                    logger.debug("First-window snapshot failed (pcm)", call_id=call_id, exc_info=True)
            return out_pcm
        except Exception as exc:
            logger.error(
                "Audio chunk processing failed",
                call_id=call_id,
                error=str(exc),
                exc_info=True,
            )
            return None

    def _apply_dc_block(self, call_id: str, pcm_bytes: bytes, r: float = 0.995) -> bytes:
        """DISABLED - DC-block filter was corrupting audio samples."""
        return pcm_bytes

    def _apply_soft_limiter(self, pcm_bytes: bytes, headroom_ratio: float = 0.8) -> bytes:
        """DISABLED - limiter was causing unnecessary audio processing."""
        return pcm_bytes

    def _trim_leading_silence(self, pcm_bytes: bytes, threshold_rms: int = 100) -> bytes:
        """
        Remove silent frames from the start of audio chunk.
        Returns trimmed audio or original if no leading silence detected.
        
        Args:
            pcm_bytes: PCM16 LE audio data
            threshold_rms: RMS threshold below which audio is considered silent (default 100)
        
        Returns:
            Trimmed PCM16 bytes with leading silence removed
        """
        if not pcm_bytes or len(pcm_bytes) < 320:  # Less than 20ms at 8kHz
            return pcm_bytes
        
        try:
            import array
            import math
            
            buf = array.array('h')
            buf.frombytes(pcm_bytes)
            
            # Process in 20ms frames (160 samples at 8kHz)
            frame_size = 160
            
            for i in range(0, len(buf), frame_size):
                frame = buf[i:i+frame_size]
                if len(frame) < frame_size:
                    break
                
                # Calculate frame RMS
                acc = sum(float(s) * float(s) for s in frame)
                frame_rms = int(math.sqrt(acc / len(frame)))
                
                if frame_rms > threshold_rms:  # Found real audio
                    if i > 0:
                        # Trim everything before this frame
                        trimmed = buf[i:]
                        trimmed_ms = int(i / 8)  # 8 samples per ms at 8kHz
                        logger.info("SILENCE TRIMMED FROM CHUNK",
                                   trimmed_samples=i,
                                   trimmed_ms=trimmed_ms,
                                   first_audio_rms=frame_rms,
                                   original_bytes=len(pcm_bytes),
                                   trimmed_bytes=len(trimmed) * 2)
                        return trimmed.tobytes()
                    else:
                        # No leading silence
                        return pcm_bytes
            
            # All frames were silent
            logger.warning("ENTIRE CHUNK SILENT",
                          chunk_size=len(buf),
                          threshold=threshold_rms)
            return pcm_bytes
            
        except Exception as e:
            logger.error("SILENCE TRIM FAILED", error=str(e), exc_info=True)
            return pcm_bytes

    def _apply_normalizer(self, pcm_bytes: bytes, target_rms: int, max_gain_db: float) -> bytes:
        """Apply simple RMS-based make-up gain to PCM16 LE audio.

        - Computes RMS of the current buffer and applies a scalar gain to approach
          target_rms, capped by max_gain_db.
          - Clips to int16 range.
          - Returns original input on any error.
        """
        # Entry diagnostics
        try:
            logger.debug(
                "NORMALIZER FUNCTION ENTRY",
                pcm_bytes_len=(len(pcm_bytes) if pcm_bytes else 0),
                target_rms=int(target_rms),
                max_gain_db=float(max_gain_db),
            )
        except Exception:
            pass
        if not pcm_bytes or target_rms <= 0:
            try:
                logger.debug(
                    "NORMALIZER EARLY RETURN #1",
                    empty_pcm=(not bool(pcm_bytes)),
                    invalid_target=bool(target_rms <= 0),
                )
            except Exception:
                pass
            return pcm_bytes
        try:
            import array, math
            buf = array.array('h')
            buf.frombytes(pcm_bytes)
            try:
                logger.debug(
                    "NORMALIZER BUFFER DECODED",
                    buf_itemsize=int(buf.itemsize),
                    buf_len=int(len(buf)),
                )
            except Exception:
                pass
            if buf.itemsize != 2 or len(buf) == 0:
                try:
                    logger.debug(
                        "NORMALIZER EARLY RETURN #2",
                        wrong_itemsize=bool(buf.itemsize != 2),
                        empty_buffer=bool(len(buf) == 0),
                    )
                except Exception:
                    pass
                return pcm_bytes
            # Compute RMS
            acc = 0.0
            for s in buf:
                acc += float(s) * float(s)
            rms = math.sqrt(acc / float(len(buf))) if len(buf) > 0 else 0.0
            # Do NOT early-return for low RMS; boost very quiet audio too.
            # Prevent divide-by-zero by clamping effective RMS to >= 1.0
            effective_rms = max(1.0, float(rms))
            # Compute linear gain toward target, limited by max_gain_db
            desired = float(target_rms) / effective_rms
            max_lin = math.pow(10.0, float(max_gain_db) / 20.0)
            gain = min(desired, max_lin)
            # Diagnostics: always log RMS/gain decision for RCA
            try:
                logger.debug(
                    "NORMALIZER RMS CHECK",
                    current_rms=int(rms),
                    target_rms=int(target_rms),
                    calculated_gain=round(gain, 3),
                    will_skip=bool(gain <= 1.01),
                )
            except Exception:
                pass
            if gain <= 1.01:
                # Avoid tiny changes to reduce CPU
                try:
                    logger.debug("NORMALIZER SKIPPED - gain too small", gain=round(gain, 3), current_rms=int(rms))
                except Exception:
                    pass
                return pcm_bytes
            try:
                gain_db = 20.0 * math.log10(max(1e-6, gain))
                logger.debug("Normalizer applied", target_rms=target_rms, current_rms=int(rms), gain_db=round(gain_db, 2))
            except Exception:
                pass
            # Apply and clip
            for i, s in enumerate(buf):
                y = float(s) * gain
                if y > 32767.0:
                    y = 32767.0
                elif y < -32768.0:
                    y = -32768.0
                buf[i] = int(y)
            return buf.tobytes()
        except Exception:
            return pcm_bytes

    def _apply_attack_envelope(self, call_id: str, pcm_bytes: bytes, sample_rate: int, stream_info: Dict[str, Any]) -> bytes:
        """Apply a short linear attack envelope at the start of a streaming segment to avoid hot starts.
        Maintains per-stream remaining bytes in stream_info['attack_bytes_remaining'].
        """
        if not pcm_bytes or sample_rate <= 0 or self.attack_ms <= 0:
            return pcm_bytes
        try:
            import array
            total_attack_bytes = int(max(0, int(sample_rate * (self.attack_ms / 1000.0)) * 2))
            remaining = int(stream_info.get('attack_bytes_remaining', total_attack_bytes))
            if remaining <= 0:
                return pcm_bytes
            buf = array.array('h')
            buf.frombytes(pcm_bytes)
            if buf.itemsize != 2:
                return pcm_bytes
            # Number of samples to shape in this buffer
            shape_samples = min(len(buf), remaining // 2)
            if shape_samples <= 0:
                return pcm_bytes
            # Linear ramp from ~0 -> 1 over remaining bytes
            for i in range(shape_samples):
                # Progress across total attack window (bytes consumed so far)
                consumed_bytes = (total_attack_bytes - remaining) + (i * 2)
                alpha = max(0.0, min(1.0, consumed_bytes / float(max(1, total_attack_bytes))))
                s = int(buf[i])
                buf[i] = int(round(s * alpha))
            remaining -= shape_samples * 2
            if remaining <= 0:
                stream_info['attack_bytes_remaining'] = 0
            else:
                stream_info['attack_bytes_remaining'] = remaining
            return buf.tobytes()
        except Exception:
            return pcm_bytes

    async def _send_audio_chunk(
        self,
        call_id: str,
        stream_id: str,
        chunk: bytes,
        *,
        target_fmt: Optional[str] = None,
        target_rate: Optional[int] = None,
    ) -> bool:
        """Send audio chunk via configured streaming transport."""
        try:
            session = await self.session_store.get_by_call_id(call_id)
            if not session:
                logger.warning("Cannot stream audio - session not found", call_id=call_id)
                return False
            stream_info = self.active_streams.get(call_id, {})
            if self.audio_diag_callback:
                try:
                    effective_fmt = (
                        self._canonicalize_encoding(target_fmt)
                        or self._canonicalize_encoding(stream_info.get("target_format"))
                        or self._canonicalize_encoding(self.audiosocket_format)
                        or "ulaw"
                    )
                    try:
                        effective_rate = int(
                            target_rate
                            or stream_info.get("target_sample_rate")
                            or self.sample_rate
                        )
                    except Exception:
                        effective_rate = self.sample_rate
                    if effective_rate <= 0:
                        effective_rate = self._default_sample_rate_for_format(effective_fmt, self.sample_rate)
                    stage = f"transport_out:{stream_info.get('playback_type', 'response')}"
                    await self.audio_diag_callback(call_id, stage, chunk, effective_fmt, effective_rate)
                except Exception:
                    try:
                        info = self.active_streams.get(call_id) or {}
                        info['diag_callback_failed'] = True
                        self.active_streams[call_id] = info
                    except Exception:
                        pass
                    logger.debug("Streaming diagnostics callback failed", call_id=call_id, exc_info=True)

            if self.audio_transport == "externalmedia":
                if not self.rtp_server:
                    logger.warning("Streaming transport unavailable (no RTP server)", call_id=call_id)
                    return False

                # RTP expects PCM16 in network byte order (big-endian) for slin16 codec
                # Streaming manager produces little-endian PCM16, so we need to byte-swap
                # Cache codec check per call for performance
                rtp_chunk = chunk
                if call_id not in self._rtp_codec_cache:
                    codec_str = str(getattr(session, 'external_media_codec', 'ulaw')).lower()
                    self._rtp_codec_cache[call_id] = codec_str in ('slin16', 'slin', 'pcm16', 'linear16')
                    
                # Fast path: byte-swap only if needed for PCM16
                if self._rtp_codec_cache.get(call_id, False) and len(chunk) > 0:
                    try:
                        import audioop
                        rtp_chunk = audioop.byteswap(chunk, 2)
                    except Exception as e:
                        logger.warning("RTP byte-swap failed, sending original", call_id=call_id, error=str(e))
                        rtp_chunk = chunk

                ssrc = getattr(session, "ssrc", None)
                success = await self.rtp_server.send_audio(call_id, rtp_chunk, ssrc=ssrc)
                if not success:
                    # If the remote RTP endpoint isn't known yet, early sends are expected to be deferred.
                    # Avoid warning spam; higher-level logic will wait briefly and then fall back for greetings.
                    try:
                        has_endpoint = True
                        if hasattr(self.rtp_server, "has_remote_endpoint"):
                            has_endpoint = bool(self.rtp_server.has_remote_endpoint(call_id))
                        if not has_endpoint:
                            if stream_id not in self._rtp_remote_wait_logged:
                                logger.info(
                                    "RTP send deferred; waiting for remote endpoint",
                                    call_id=call_id,
                                    stream_id=stream_id,
                                    transport="externalmedia",
                                )
                                self._rtp_remote_wait_logged.add(stream_id)
                        else:
                            logger.warning("RTP streaming send failed", call_id=call_id, stream_id=stream_id)
                    except Exception:
                        logger.warning("RTP streaming send failed", call_id=call_id, stream_id=stream_id)
                else:
                    try:
                        _STREAM_TX_BYTES.inc(len(rtp_chunk))
                        if call_id in self.active_streams:
                            info = self.active_streams[call_id]
                            info['tx_bytes'] = int(info.get('tx_bytes', 0)) + len(rtp_chunk)
                            info['tx_total_bytes'] = int(info.get('tx_total_bytes', 0) or 0) + len(rtp_chunk)
                    except Exception:
                        pass
                return success

            if self.audio_transport == "audiosocket":
                if not self.audiosocket_server:
                    logger.warning("Streaming transport unavailable (no AudioSocket server)", call_id=call_id)
                    return False
                conn_id = getattr(session, "audiosocket_conn_id", None)
                if not conn_id:
                    logger.warning("Streaming transport missing AudioSocket connection", call_id=call_id)
                    return False
                if self.audio_capture_manager:
                    try:
                        self.audio_capture_manager.append_encoded(
                            call_id,
                            "agent_out_to_caller",
                            chunk,
                            target_fmt or self.audiosocket_format,
                            int(target_rate or self.sample_rate),
                        )
                    except Exception:
                        logger.debug("Outbound audio capture failed", call_id=call_id, exc_info=True)
                # One-time debug for first outbound frame to identify codec/format
                if call_id not in self._first_send_logged:
                    fmt = (
                        self._canonicalize_encoding(target_fmt)
                        or self._canonicalize_encoding(self.audiosocket_format)
                        or "ulaw"
                    )
                    try:
                        sample_rate = int(target_rate if target_rate is not None else self.sample_rate)
                    except Exception:
                        sample_rate = self.sample_rate
                    if sample_rate <= 0:
                        sample_rate = self._default_sample_rate_for_format(fmt, self.sample_rate)
                    logger.info(
                        "🎵 STREAMING OUTBOUND - First frame",
                        call_id=call_id,
                        stream_id=stream_id,
                        transport=self.audio_transport,
                        audiosocket_format=fmt,
                        frame_bytes=len(chunk),
                        sample_rate=sample_rate,
                        chunk_size_ms=self.chunk_size_ms,
                        conn_id=conn_id,
                    )
                    self._first_send_logged.add(call_id)
                    # Per-segment diag tap flush (snapshot on first frame)
                    try:
                        info = self.active_streams.get(call_id, {})
                        if info and bool(info.get('diag_enabled')):
                            try:
                                raw_rate = int(info.get('tap_rate') or 0)
                            except Exception:
                                raw_rate = 0
                            rate = raw_rate if raw_rate > 0 else int(self.sample_rate)
                            pre = bytes(info.get('tap_pre_pcm16') or b"")
                            post = bytes(info.get('tap_post_pcm16') or b"")
                            # Write snapshots opportunistically (errors non-fatal)
                            if pre:
                                fn = os.path.join(self.diag_out_dir, f"pre_compand_pcm16_{call_id}_{stream_id}_first.wav")
                                try:
                                    with wave.open(fn, 'wb') as wf:
                                        wf.setnchannels(1)
                                        wf.setsampwidth(2)
                                        wf.setframerate(rate)
                                        wf.writeframes(pre)
                                    try:
                                        os.chmod(fn, 0o600)
                                    except Exception:
                                        pass
                                    logger.info("Wrote pre-compand PCM16 tap snapshot", call_id=call_id, stream_id=stream_id, path=fn, bytes=len(pre), rate=rate, snapshot="first")
                                except Exception:
                                    logger.warning("Failed to write pre-compand tap snapshot", call_id=call_id, stream_id=stream_id, path=fn, rate=rate, snapshot="first", exc_info=True)
                            if post:
                                fn2 = os.path.join(self.diag_out_dir, f"post_compand_pcm16_{call_id}_{stream_id}_first.wav")
                                try:
                                    with wave.open(fn2, 'wb') as wf:
                                        wf.setnchannels(1)
                                        wf.setsampwidth(2)
                                        wf.setframerate(rate)
                                        wf.writeframes(post)
                                    try:
                                        os.chmod(fn2, 0o600)
                                    except Exception:
                                        pass
                                    logger.info("Wrote post-compand PCM16 tap snapshot", call_id=call_id, stream_id=stream_id, path=fn2, bytes=len(post), rate=rate, snapshot="first")
                                except Exception:
                                    logger.warning("Failed to write post-compand tap snapshot", call_id=call_id, stream_id=stream_id, path=fn2, rate=rate, snapshot="first", exc_info=True)
                    except Exception:
                        logger.debug("Per-segment tap snapshot failed", call_id=call_id, stream_id=stream_id, exc_info=True)
                if self.audiosocket_broadcast_debug:
                    conns = list(set(getattr(session, 'audiosocket_conns', []) or []))
                    sent = 0
                    for cid in conns or [conn_id]:
                        if await self.audiosocket_server.send_audio(cid, chunk):
                            sent += 1
                    if sent == 0:
                        logger.warning("AudioSocket broadcast send failed (no recipients)", call_id=call_id, stream_id=stream_id)
                        return False
                    if len(conns) > 1:
                        logger.debug("AudioSocket broadcast sent", call_id=call_id, stream_id=stream_id, recipients=len(conns))
                    return True
                # Normal single-conn send
                success = await self.audiosocket_server.send_audio(conn_id, chunk)
                if not success:
                    logger.warning("AudioSocket streaming send failed", call_id=call_id, stream_id=stream_id)
                else:
                    try:
                        _STREAM_TX_BYTES.inc(len(chunk))
                        if call_id in self.active_streams:
                            info = self.active_streams[call_id]
                            info['tx_bytes'] = int(info.get('tx_bytes', 0)) + len(chunk)
                            info['tx_total_bytes'] = int(info.get('tx_total_bytes', 0) or 0) + len(chunk)
                    except Exception:
                        pass
                # First-frame observability
                try:
                    if call_id in self.active_streams and not self.active_streams[call_id].get('first_frame_observed', False) and success:
                        start_time = float(self.active_streams[call_id].get('start_time', time.time()))
                        pb_type = str(self.active_streams[call_id].get('playback_type', 'response'))
                        first_s = max(0.0, time.time() - start_time)
                        _STREAM_FIRST_FRAME_SECONDS.labels(pb_type).observe(first_s)
                        self.active_streams[call_id]['first_frame_observed'] = True
                except Exception:
                    pass
                return success

            logger.warning("Streaming transport not implemented for audio_transport",
                           call_id=call_id,
                           audio_transport=self.audio_transport)
            return False

        except Exception as e:
            logger.error("Error sending streaming audio chunk",
                        call_id=call_id,
                        stream_id=stream_id,
                        error=str(e),
                        exc_info=True)
            return False

    def _remove_dc_from_pcm16(
        self,
        call_id: str,
        pcm_bytes: bytes,
        *,
        threshold: int = 256,
        stage: str = "",
    ) -> Tuple[bytes, bool]:
        """Clamp DC offset on PCM16 audio."""
        if not pcm_bytes:
            return pcm_bytes, False
        try:
            import audioop
        except Exception:
            return pcm_bytes, False

        try:
            dc = audioop.avg(pcm_bytes, 2)
        except Exception:
            return pcm_bytes, False

        if abs(dc) < max(0, int(threshold)):
            return pcm_bytes, False

        try:
            cleaned = audioop.bias(pcm_bytes, 2, -int(dc))
        except Exception:
            return pcm_bytes, False

        if call_id in self.active_streams:
            info = self.active_streams.get(call_id, {}) or {}
            if stage:
                key = f"_dc_logged_{stage}"
                if not info.get(key):
                    try:
                        logger.info(
                            "Streaming PCM16 DC correction applied",
                            call_id=call_id,
                            stage=stage,
                            dc_before=int(dc),
                        )
                    except Exception:
                        pass
                    info[key] = True
                    self.active_streams[call_id] = info
        return cleaned, True

    def _update_idle_tracking(self, call_id: str, status: str) -> None:
        info = self.active_streams.get(call_id)
        if not info:
            return
        try:
            current_ticks = int(info.get('idle_ticks', 0) or 0)
        except Exception:
            current_ticks = 0
        if status == "sent":
            if bool(info.get('last_emit_was_filler')):
                info['idle_ticks'] = current_ticks + 1
            else:
                info['idle_ticks'] = 0
        elif status == "wait":
            info['idle_ticks'] = current_ticks + 1
        elif status == "finished":
            info['idle_ticks'] = current_ticks + 1

    def _should_stop_for_idle(self, call_id: str, stream_id: str, jitter_buffer: asyncio.Queue) -> bool:
        info = self.active_streams.get(call_id)
        if not info or info.get('idle_cutoff_triggered'):
            return False
        try:
            cutoff_ms = int(info.get('idle_cutoff_ms', self.idle_cutoff_ms) or 0)
        except Exception:
            cutoff_ms = self.idle_cutoff_ms
        if cutoff_ms <= 0:
            return False
        if not bool(info.get('sentinel_seen', False)):
            self._note_idle_block(info, 'waiting-for-sentinel')
            return False
        if not jitter_buffer.empty():
            self._note_idle_block(info, 'buffer-not-empty')
            return False
        remainder = self.frame_remainders.get(call_id, b"")
        if remainder:
            self._note_idle_block(info, 'pending-remainder')
            return False
        try:
            idle_ticks = int(info.get('idle_ticks', 0) or 0)
        except Exception:
            idle_ticks = 0
        cutoff_ticks = int(info.get('idle_cutoff_ticks', 0) or 0)
        if cutoff_ticks and idle_ticks < cutoff_ticks:
            self._note_idle_block(info, 'tick-threshold')
            return False
        last_real_emit_ts = info.get('last_real_emit_ts')
        if last_real_emit_ts is None:
            self._note_idle_block(info, 'no-real-frame-yet')
            return False
        elapsed_ms = max(0.0, (time.time() - float(last_real_emit_ts)) * 1000.0)
        if elapsed_ms < cutoff_ms:
            self._note_idle_block(info, 'elapsed-below-cutoff')
            return False
        info['idle_cutoff_triggered'] = True
        info['end_reason'] = info.get('end_reason') or 'idle-cutoff'
        try:
            logger.info(
                "🎵 STREAMING PACER - Idle cutoff",
                call_id=call_id,
                stream_id=stream_id,
                idle_elapsed_ms=int(elapsed_ms),
                idle_cutoff_ms=cutoff_ms,
            )
        except Exception:
            pass
        return True

    def _resolve_chunk_size_ms(self, cfg_value: Optional[Any]) -> int:
        """Resolve outbound chunk cadence (ms)."""
        default_ms = 20
        if cfg_value is None:
            return default_ms
        try:
            # Allow string values like "auto" or numbers
            if isinstance(cfg_value, str):
                val = cfg_value.strip().lower()
                if not val or val == "auto":
                    return default_ms
                return max(5, min(120, int(float(val))))
            return max(5, min(120, int(cfg_value)))
        except Exception:
            return default_ms

    def _resolve_idle_cutoff_ms(self, cfg_value: Optional[Any]) -> int:
        """Resolve idle cutoff in milliseconds for pacer."""
        default_ms = 1200
        if cfg_value is None:
            return default_ms
        try:
            if isinstance(cfg_value, str):
                val = cfg_value.strip().lower()
                if not val or val == "auto":
                    return default_ms
                return max(200, min(5000, int(float(val))))
            return max(200, min(5000, int(cfg_value)))
        except Exception:
            return default_ms

    def _frame_size_bytes(self, call_id: Optional[str] = None) -> int:
        fmt = (
            self._canonicalize_encoding(self.audiosocket_format)
            or "ulaw"
        )
        sample_rate = self.sample_rate
        if call_id and call_id in self.active_streams:
            info = self.active_streams.get(call_id, {})
            fmt = (
                self._canonicalize_encoding(info.get('target_format'))
                or fmt
            )
            try:
                sr = int(info.get('target_sample_rate', 0) or 0)
            except Exception:
                sr = 0
            if sr > 0:
                sample_rate = sr
            elif call_id:
                # CRITICAL: target_sample_rate missing from active_streams!
                # This causes wrong frame sizes (e.g., 320 bytes @ 8kHz instead of 640 bytes @ 16kHz)
                # which leads to chipmunk audio
                logger.warning(
                    "⚠️  target_sample_rate missing in active_streams - using fallback",
                    call_id=call_id,
                    fallback_sample_rate=sample_rate,
                    stream_info_keys=list(info.keys()) if info else [],
                )
        bytes_per_sample = 1 if self._is_mulaw(fmt) else 2
        frame_size = int(sample_rate * (self.chunk_size_ms / 1000.0) * bytes_per_sample)
        if frame_size <= 0:
            frame_size = 160 if bytes_per_sample == 1 else 320
        return frame_size

    def _estimate_available_frames(
        self,
        call_id: str,
        jitter_buffer: asyncio.Queue,
        *,
        include_remainder: bool = False,
    ) -> int:
        frame_size = self._frame_size_bytes(call_id)
        try:
            info = self.active_streams.get(call_id, {})
            buffered_bytes = int(info.get('buffered_bytes', 0))
        except Exception:
            buffered_bytes = 0

        if buffered_bytes <= 0:
            # Approximate using queue depth when buffered_bytes not yet initialised
            buffered_bytes = jitter_buffer.qsize() * frame_size

        if include_remainder:
            remainder = self.frame_remainders.get(call_id, b"")
            if remainder:
                buffered_bytes += len(remainder)

        frames = int(buffered_bytes / max(1, frame_size))
        return max(0, frames)

    def _get_low_watermark_frames(self, call_id: str) -> int:
        try:
            info = self.active_streams.get(call_id, {})
            lw = int(info.get('low_watermark_chunks', self.low_watermark_chunks))
        except Exception:
            lw = self.low_watermark_chunks
        return max(0, lw)

    def _decrement_buffered_bytes(self, call_id: str, byte_count: int) -> None:
        if byte_count <= 0:
            return
        try:
            info = self.active_streams.get(call_id)
            if info is None:
                return
            current = int(info.get('buffered_bytes', 0))
            info['buffered_bytes'] = max(0, current - byte_count)
        except Exception:
            pass

    def set_transport(
        self,
        *,
        rtp_server: Optional[Any] = None,
        audiosocket_server: Optional[Any] = None,
        audio_transport: Optional[str] = None,
        audiosocket_format: Optional[str] = None,
    ) -> None:
        """Configure transport dependencies after engine initialization."""
        if rtp_server is not None:
            self.rtp_server = rtp_server
        if audiosocket_server is not None:
            self.audiosocket_server = audiosocket_server
        if audio_transport is not None:
            self.audio_transport = audio_transport
        if audiosocket_format is not None:
            self.audiosocket_format = audiosocket_format

    def record_provider_bytes(self, call_id: str, provider_bytes: int) -> None:
        try:
            info = self.active_streams.get(call_id)
            if info is not None:
                # Per-segment bytes (last segment)
                info['provider_bytes'] = int(provider_bytes)
                # Call-total accumulation for provider bytes
                prev_total = int(info.get('provider_total_bytes', 0) or 0)
                info['provider_total_bytes'] = prev_total + int(provider_bytes)
        except Exception:
            pass

    async def _record_fallback(self, call_id: str, reason: str) -> None:
        """Increment fallback counters and persist the last error."""
        try:
            _STREAMING_FALLBACKS_TOTAL.inc()
            sess = await self.session_store.get_by_call_id(call_id)
            if sess:
                sess.streaming_fallback_count += 1
                sess.last_streaming_error = reason
                await self.session_store.upsert_call(sess)
        except Exception:
            logger.debug("Failed to record streaming fallback", call_id=call_id, reason=reason, exc_info=True)
    
    async def _fallback_to_file_playback(
        self, 
        call_id: str, 
        stream_id: str
    ) -> None:
        """Fallback to file-based playback when streaming fails."""
        try:
            if not self.fallback_playback_manager:
                logger.error("No fallback playback manager available",
                           call_id=call_id,
                           stream_id=stream_id)
                return
            
            # Get session
            session = await self.session_store.get_by_call_id(call_id)
            if not session:
                logger.error("Cannot fallback - session not found",
                           call_id=call_id)
                return
            
            # Collect any remaining audio chunks
            remaining_audio = bytearray()
            # Include any remainder frames already drained from the jitter buffer.
            # Otherwise, a failed first outbound send can leave nothing to play on fallback.
            try:
                rem = self.frame_remainders.pop(call_id, b"") or b""
                if rem:
                    remaining_audio.extend(rem)
            except Exception:
                pass
            if call_id in self.jitter_buffers:
                jitter_buffer = self.jitter_buffers[call_id]
                while not jitter_buffer.empty():
                    chunk = jitter_buffer.get_nowait()
                    # Skip sentinel objects and non-bytes data
                    if chunk is _JITTER_SENTINEL:
                        continue
                    if chunk and isinstance(chunk, (bytes, bytearray)):
                        remaining_audio.extend(chunk)
                        self._decrement_buffered_bytes(call_id, len(chunk))
            
            if remaining_audio:
                raw_buf = bytes(remaining_audio)

                # Convert provider-encoded buffer to μ-law @ 8 kHz for Asterisk file playback
                try:
                    info = self.active_streams.get(call_id, {})
                    src_encoding = (
                        self._canonicalize_encoding(info.get('source_encoding'))
                        or 'slin16'
                    )
                    try:
                        src_rate = int(info.get('source_sample_rate') or 0) or self.sample_rate
                    except Exception:
                        src_rate = self.sample_rate

                    # Normalize to PCM16
                    if self._is_mulaw(src_encoding):
                        pcm = mulaw_to_pcm16le(raw_buf)
                        src_rate = 8000
                    else:
                        pcm = raw_buf

                    # Resample to 8 kHz for μ-law file playback
                    if src_rate != 8000:
                        pcm, _ = resample_audio(pcm, src_rate, 8000)

                    # Convert to μ-law
                    mulaw_audio = pcm16le_to_mulaw(pcm)
                except Exception:
                    logger.warning("Fallback conversion failed; passing raw bytes to file playback",
                                   call_id=call_id,
                                   stream_id=stream_id,
                                   exc_info=True)
                    mulaw_audio = raw_buf

                # Use fallback playback manager
                fallback_playback_id = await self.fallback_playback_manager.play_audio(
                    call_id,
                    mulaw_audio,
                    "streaming-fallback"
                )
                
                if fallback_playback_id:
                    logger.info("🎵 STREAMING FALLBACK - Switched to file playback",
                               call_id=call_id,
                               stream_id=stream_id,
                               fallback_id=fallback_playback_id)
                else:
                    logger.error("Failed to start fallback file playback",
                               call_id=call_id,
                               stream_id=stream_id)
            
        except Exception as e:
            logger.error("Error in fallback to file playback",
                        call_id=call_id,
                        stream_id=stream_id,
                        error=str(e),
                        exc_info=True)
    
    async def _keepalive_loop(self, call_id: str, stream_id: str) -> None:
        """Keepalive loop to maintain streaming connection."""
        try:
            while call_id in self.active_streams:
                await asyncio.sleep(self.keepalive_interval_ms / 1000.0)
                
                # Check if stream is still active
                if call_id not in self.active_streams:
                    break
                
                # Check for timeout
                stream_info = self.active_streams[call_id]
                time_since_last_chunk = time.time() - stream_info['last_chunk_time']
                stream_info["last_chunk_age_s"] = max(0.0, float(time_since_last_chunk))
                self._refresh_streaming_summary_metrics()
                _STREAMING_KEEPALIVES_SENT_TOTAL.inc()
                try:
                    sess = await self.session_store.get_by_call_id(call_id)
                    if sess:
                        sess.streaming_keepalive_sent += 1
                        await self.session_store.upsert_call(sess)
                except Exception:
                    pass
                
                if time_since_last_chunk > (self.connection_timeout_ms / 1000.0):
                    logger.warning("🎵 STREAMING PLAYBACK - Connection timeout",
                                 call_id=call_id,
                                 stream_id=stream_id,
                                 time_since_last_chunk=time_since_last_chunk)
                    _STREAMING_KEEPALIVE_TIMEOUTS_TOTAL.inc()
                    # In continuous-stream mode, do NOT fallback or end the stream; continue pacing
                    if not self.continuous_stream:
                        try:
                            if call_id in self.active_streams:
                                self.active_streams[call_id]['end_reason'] = 'keepalive-timeout'
                        except Exception:
                            pass
                        try:
                            sess = await self.session_store.get_by_call_id(call_id)
                            if sess:
                                sess.streaming_keepalive_timeouts += 1
                                sess.last_streaming_error = f"keepalive-timeout>{time_since_last_chunk:.2f}s"
                                await self.session_store.upsert_call(sess)
                        except Exception:
                            pass
                        await self._fallback_to_file_playback(call_id, stream_id)
                        break
                    else:
                        # Continuous: just keep the pacer alive; no action required
                        continue
                
                # Send keepalive (placeholder)
                logger.debug("🎵 STREAMING KEEPALIVE - Sending keepalive",
                           call_id=call_id,
                           stream_id=stream_id)
        
        except asyncio.CancelledError:
            logger.debug("Keepalive loop cancelled",
                        call_id=call_id,
                        stream_id=stream_id)
        except Exception as e:
            logger.error("Error in keepalive loop",
                        call_id=call_id,
                        stream_id=stream_id,
                        error=str(e))

    
    async def stop_streaming_playback(self, call_id: str) -> bool:
        """Stop streaming playback for a call."""
        try:
            stream_info = self.active_streams.get(call_id)
            if not stream_info:
                logger.warning("No active streaming to stop", call_id=call_id)
                return False
            stream_id = stream_info.get('stream_id') or ''
            # Cancel streaming task
            try:
                task = stream_info.get('streaming_task')
                if task:
                    task.cancel()
            except Exception:
                pass
            # Cancel pacer task
            try:
                ptask = stream_info.get('pacer_task')
                if ptask:
                    ptask.cancel()
            except Exception:
                pass
            # Cancel keepalive task
            if call_id in self.keepalive_tasks:
                try:
                    self.keepalive_tasks[call_id].cancel()
                except Exception:
                    pass
                self.keepalive_tasks.pop(call_id, None)
            # Cleanup resources and emit summaries
            await self._cleanup_stream(call_id, stream_id)
            logger.info("🎵 STREAMING PLAYBACK - Stopped", call_id=call_id, stream_id=stream_id)
            return True
        except Exception as e:
            logger.error("Error stopping streaming playback", call_id=call_id, error=str(e), exc_info=True)
            return False

    async def mark_segment_boundary(self, call_id: str) -> None:
        """Mark a segment boundary for a continuous stream.

        Resets attack envelope so the next provider segment starts with a short
        fade-in, without tearing down the stream/pacer.
        """
        try:
            info = self.active_streams.get(call_id)
            if not info:
                return
            
            # Increment segment counter for warm-up optimization
            info['segments_played'] = info.get('segments_played', 0) + 1
            
            try:
                rate = int(info.get('target_sample_rate') or 0)
            except Exception:
                rate = 0
            if rate <= 0:
                rate = int(self.sample_rate)
            total_attack_bytes = int(max(0, int(rate * (self.attack_ms / 1000.0)) * 2))
            info['attack_bytes_remaining'] = total_attack_bytes
            logger.info(
                "⚡ CONTINUOUS STREAM - Segment boundary",
                call_id=call_id,
                segment_num=info['segments_played'],
                attack_bytes=total_attack_bytes,
                attack_ms=self.attack_ms,
                rate=rate
            )
        except Exception:
            logger.debug("Failed to mark segment boundary", call_id=call_id, exc_info=True)

    async def start_segment_gating(self, call_id: str) -> None:
        """Begin per-segment TTS gating in continuous-stream mode."""
        try:
            info = self.active_streams.get(call_id)
            if not info:
                return
            stream_id = str(info.get('stream_id') or '')
            if not stream_id:
                return
            ok = True
            if self.conversation_coordinator:
                ok = await self.conversation_coordinator.on_tts_start(call_id, stream_id)
            else:
                ok = await self.session_store.set_gating_token(call_id, stream_id)
            if not ok:
                logger.warning("Failed to start segment gating", call_id=call_id, stream_id=stream_id)
        except Exception:
            logger.debug("start_segment_gating failed", call_id=call_id, exc_info=True)

    async def end_segment_gating(self, call_id: str) -> None:
        """End per-segment TTS gating in continuous-stream mode."""
        try:
            info = self.active_streams.get(call_id)
            if not info:
                return
            stream_id = str(info.get('stream_id') or '')
            if not stream_id:
                return
            if self.conversation_coordinator:
                try:
                    await self.conversation_coordinator.on_tts_end(call_id, stream_id, reason="segment-end")
                except Exception:
                    pass
            else:
                try:
                    await self.session_store.clear_gating_token(call_id, stream_id)
                except Exception:
                    pass
            logger.debug("Ended segment gating", call_id=call_id, stream_id=stream_id)
        except Exception:
            logger.debug("end_segment_gating failed", call_id=call_id, exc_info=True)

    async def _cleanup_stream(self, call_id: str, stream_id: str) -> None:
        """Clean up streaming resources."""
        if call_id in self._cleanup_in_progress:
            return
        self._cleanup_in_progress.add(call_id)
        try:
            # Diagnostic: write pre/post tap WAVs if enabled
            try:
                info = self.active_streams.get(call_id, {})
                if info and bool(info.get('diag_enabled')):
                    try:
                        raw_rate = int(info.get('tap_rate') or 0)
                    except Exception:
                        raw_rate = 0
                    rate = raw_rate if raw_rate > 0 else int(self.sample_rate)
                    pre = bytes(info.get('tap_pre_pcm16') or b"")
                    post = bytes(info.get('tap_post_pcm16') or b"")
                    # Always log counts so we know what reached cleanup
                    logger.info(
                        "Streaming diag taps summary",
                        call_id=call_id,
                        tap_pre_bytes=len(pre),
                        tap_post_bytes=len(post),
                        tap_rate=rate,
                    )
                    # Only write files when there is data
                    if pre:
                        fn = os.path.join(self.diag_out_dir, f"pre_compand_pcm16_{call_id}.wav")
                        try:
                            with wave.open(fn, 'wb') as wf:
                                wf.setnchannels(1)
                                wf.setsampwidth(2)
                                wf.setframerate(rate)
                                wf.writeframes(pre)
                            try:
                                os.chmod(fn, 0o600)
                            except Exception:
                                pass
                            logger.info("Wrote pre-compand PCM16 tap", call_id=call_id, path=fn, bytes=len(pre), rate=rate)
                        except Exception:
                            logger.warning("Failed to write pre-compand tap", call_id=call_id, path=fn, rate=rate, exc_info=True)
                        # Segment-specific snapshot (end of stream)
                        try:
                            fn_seg = os.path.join(self.diag_out_dir, f"pre_compand_pcm16_{call_id}_{stream_id}_end.wav")
                            with wave.open(fn_seg, 'wb') as wf:
                                wf.setnchannels(1)
                                wf.setsampwidth(2)
                                wf.setframerate(rate)
                                wf.writeframes(pre)
                            try:
                                os.chmod(fn_seg, 0o600)
                            except Exception:
                                pass
                            logger.info("Wrote pre-compand PCM16 tap snapshot", call_id=call_id, stream_id=stream_id, path=fn_seg, bytes=len(pre), rate=rate, snapshot="end")
                        except Exception:
                            logger.warning("Failed to write pre-compand tap snapshot", call_id=call_id, stream_id=stream_id, rate=rate, snapshot="end", exc_info=True)
                    if post:
                        fn2 = os.path.join(self.diag_out_dir, f"post_compand_pcm16_{call_id}.wav")
                        try:
                            with wave.open(fn2, 'wb') as wf:
                                wf.setnchannels(1)
                                wf.setsampwidth(2)
                                wf.setframerate(rate)
                                wf.writeframes(post)
                            try:
                                os.chmod(fn2, 0o600)
                            except Exception:
                                pass
                            logger.info("Wrote post-compand PCM16 tap", call_id=call_id, path=fn2, bytes=len(post), rate=rate)
                        except Exception:
                            logger.warning("Failed to write post-compand tap", call_id=call_id, path=fn2, rate=rate, exc_info=True)
                        # Segment-specific snapshot (end of stream)
                        try:
                            fn2_seg = os.path.join(self.diag_out_dir, f"post_compand_pcm16_{call_id}_{stream_id}_end.wav")
                            with wave.open(fn2_seg, 'wb') as wf:
                                wf.setnchannels(1)
                                wf.setsampwidth(2)
                                wf.setframerate(rate)
                                wf.writeframes(post)
                            try:
                                os.chmod(fn2_seg, 0o600)
                            except Exception:
                                pass
                            logger.info("Wrote post-compand PCM16 tap snapshot", call_id=call_id, stream_id=stream_id, path=fn2_seg, bytes=len(post), rate=rate, snapshot="end")
                        except Exception:
                            logger.warning("Failed to write post-compand tap snapshot", call_id=call_id, stream_id=stream_id, rate=rate, snapshot="end", exc_info=True)
                    # Call-level summary and writes (accumulate across segments)
                    try:
                        cpre = bytes(self.call_tap_pre_pcm16.get(call_id, b""))
                        cpost = bytes(self.call_tap_post_pcm16.get(call_id, b""))
                        try:
                            crate = int(self.call_tap_rate.get(call_id, rate))
                        except Exception:
                            crate = rate
                        logger.info(
                            "Streaming diag taps call-level summary",
                            call_id=call_id,
                            call_tap_pre_bytes=len(cpre),
                            call_tap_post_bytes=len(cpost),
                            call_tap_rate=crate,
                        )
                        # Segment byte summary v2 (provider vs queued vs sent, frames, underflows, wall)
                        try:
                            info = self.active_streams.get(call_id, {}) if call_id in self.active_streams else {}
                            qbytes = int(info.get('queued_bytes', 0) or 0)
                            txbytes = int(info.get('tx_bytes', 0) or 0)
                            bbytes = int(info.get('buffered_bytes', 0) or 0)
                            frames_sent = int(info.get('frames_sent', 0) or 0)
                            underflows = int(info.get('underflow_events', 0) or 0)
                            provider_bytes = int(info.get('provider_bytes', 0) or 0)
                            # Accumulated totals
                            provider_total = int(info.get('provider_total_bytes', 0) or 0)
                            queued_total = int(info.get('queued_total_bytes', 0) or 0)
                            tx_total = int(info.get('tx_total_bytes', 0) or 0)
                            # compute wall_seconds
                            try:
                                seg_start = float(info.get('seg_start_ts', 0.0) or info.get('start_time', 0.0) or 0.0)
                            except Exception:
                                seg_start = 0.0
                            wall_seconds = 0.0
                            if seg_start:
                                import time as _t
                                wall_seconds = max(0.0, _t.time() - seg_start)
                            logger.info(
                                "Streaming segment bytes summary v2",
                                call_id=call_id,
                                stream_id=stream_id,
                                provider_bytes=provider_bytes,
                                provider_total_bytes=provider_total,
                                queued_bytes=qbytes,
                                queued_total_bytes=queued_total,
                                tx_bytes=txbytes,
                                tx_total_bytes=tx_total,
                                frames_sent=frames_sent,
                                underflow_events=underflows,
                                wall_seconds=wall_seconds,
                                buffered_bytes=bbytes,
                            )
                        except Exception:
                            logger.debug("Streaming segment bytes summary v2 failed", call_id=call_id, exc_info=True)
                        if cpre:
                            fnc = os.path.join(self.diag_out_dir, f"pre_compand_pcm16_{call_id}_call.wav")
                            try:
                                with wave.open(fnc, 'wb') as wf:
                                    wf.setnchannels(1)
                                    wf.setsampwidth(2)
                                    wf.setframerate(crate)
                                    wf.writeframes(cpre)
                                try:
                                    os.chmod(fnc, 0o600)
                                except Exception:
                                    pass
                                logger.info("Wrote call-level pre-compand PCM16 tap", call_id=call_id, path=fnc, bytes=len(cpre), rate=crate)
                            except Exception:
                                logger.warning("Failed to write call-level pre-compand tap", call_id=call_id, path=fnc, rate=crate, exc_info=True)
                        if cpost:
                            fnc2 = os.path.join(self.diag_out_dir, f"post_compand_pcm16_{call_id}_call.wav")
                            try:
                                with wave.open(fnc2, 'wb') as wf:
                                    wf.setnchannels(1)
                                    wf.setsampwidth(2)
                                    wf.setframerate(crate)
                                    wf.writeframes(cpost)
                                try:
                                    os.chmod(fnc2, 0o600)
                                except Exception:
                                    pass
                                logger.info("Wrote call-level post-compand PCM16 tap", call_id=call_id, path=fnc2, bytes=len(cpost), rate=crate)
                            except Exception:
                                logger.warning("Failed to write call-level post-compand tap", call_id=call_id, path=fnc2, rate=crate, exc_info=True)
                        if not getattr(self, "diag_enable_taps", False):
                            try:
                                if os.path.isdir(self.diag_out_dir):
                                    prefix_pre = f"pre_compand_pcm16_{call_id}"
                                    prefix_post = f"post_compand_pcm16_{call_id}"
                                    for name in os.listdir(self.diag_out_dir):
                                        if name.startswith(prefix_pre) or name.startswith(prefix_post):
                                            fpath = os.path.join(self.diag_out_dir, name)
                                            try:
                                                if os.path.isfile(fpath):
                                                    os.remove(fpath)
                                            except Exception:
                                                pass
                            except Exception:
                                pass
                    except Exception:
                        logger.debug("Call-level tap write failed", call_id=call_id, exc_info=True)
            except Exception:
                logger.debug("Diagnostic tap write failed", call_id=call_id, exc_info=True)
            finally:
                if getattr(self, "diag_enable_taps", False):
                    self.call_tap_pre_pcm16.pop(call_id, None)
                    self.call_tap_post_pcm16.pop(call_id, None)
                    self.call_tap_rate.pop(call_id, None)

            # Before clearing gating/state, give provider a grace period and flush any remaining audio
            # to avoid chopping off the tail of the playback.
            try:
                if self.provider_grace_ms:
                    await asyncio.sleep(self.provider_grace_ms / 1000.0)
            except Exception:
                pass

            # Flush any remainder bytes as a final frame
            try:
                rem = self.frame_remainders.get(call_id, b"") or b""
                if rem:
                    self._decrement_buffered_bytes(call_id, len(rem))
                    if self.audio_transport == "audiosocket":
                        fmt = (
                            self._canonicalize_encoding(self.audiosocket_format)
                            or "ulaw"
                        )
                        info = self.active_streams.get(call_id, {})
                        fmt = (
                            self._canonicalize_encoding(info.get('target_format'))
                            or fmt
                        )
                        try:
                            sr = int(info.get('target_sample_rate', self.sample_rate))
                        except Exception:
                            sr = self.sample_rate
                        if sr <= 0:
                            sr = self._default_sample_rate_for_format(fmt, self.sample_rate)
                        bytes_per_sample = 1 if self._is_mulaw(fmt) else 2
                        frame_size = int(sr * (self.chunk_size_ms / 1000.0) * bytes_per_sample) or (160 if bytes_per_sample == 1 else 320)
                        # Zero-pad to a full frame boundary to avoid truncation artifacts
                        if len(rem) < frame_size:
                            rem = rem + (b"\x00" * (frame_size - len(rem)))
                        await self._send_audio_chunk(call_id, stream_id, rem[:frame_size], target_fmt=fmt, target_rate=sr)
                        # small pacing to let Asterisk play the last frame
                        await asyncio.sleep(self.chunk_size_ms / 1000.0)
                    else:
                        await self._send_audio_chunk(call_id, stream_id, rem)
            except Exception:
                logger.debug("Remainder flush failed", call_id=call_id, stream_id=stream_id)

            # Clear TTS gating after flushing
            if self.conversation_coordinator:
                await self.conversation_coordinator.on_tts_end(
                    call_id, stream_id, "streaming-ended"
                )
                await self.conversation_coordinator.update_conversation_state(
                    call_id, "listening"
                )
            else:
                await self.session_store.clear_gating_token(call_id, stream_id)
            
            # Observe segment duration and end reason
            try:
                if call_id in self.active_streams:
                    info = self.active_streams[call_id]
                    pb_type = str(info.get('playback_type', 'response'))
                    dur = max(0.0, time.time() - float(info.get('start_time', time.time())))
                    _STREAM_SEGMENT_DURATION_SECONDS.labels(pb_type).observe(dur)
                    reason = str(info.get('end_reason') or 'streaming-ended')
                    _STREAM_END_REASON_TOTAL.labels(reason).inc()
            except Exception:
                pass

            # Emit tuning summary for observability BEFORE removing stream info
            try:
                if call_id in self.active_streams:
                    info = self.active_streams[call_id]
                    try:
                        fmt = (
                            self._canonicalize_encoding(info.get('target_format'))
                            or self._canonicalize_encoding(self.audiosocket_format)
                            or "ulaw"
                        )
                        bps = 1 if self._is_mulaw(fmt) else 2
                        try:
                            sr_candidate = int(info.get('target_sample_rate', 0) or 0)
                        except Exception:
                            sr_candidate = 0
                        sr = max(1, int(sr_candidate or self.sample_rate))
                        tx = int(info.get('tx_bytes', 0))
                        eff_seconds = float(tx) / float(max(1, bps * sr))
                    except Exception:
                        eff_seconds = 0.0
                    try:
                        start_ts = float(info.get('start_time', time.time()))
                        end_ts = float(info.get('last_real_emit_ts') or info.get('last_frame_ts') or time.time())
                        wall_seconds = max(0.0, end_ts - start_ts)
                    except Exception:
                        wall_seconds = 0.0
                    try:
                        drift_pct = 0.0 if wall_seconds <= 0.0 else ((eff_seconds - wall_seconds) / wall_seconds) * 100.0
                    except Exception:
                        drift_pct = 0.0
                    logger.info(
                        "🎛️ STREAMING TUNING SUMMARY",
                        call_id=call_id,
                        stream_id=stream_id,
                        bytes_sent=tx,
                        effective_seconds=round(eff_seconds, 3),
                        wall_seconds=round(wall_seconds, 3),
                        drift_pct=round(drift_pct, 1),
                        low_watermark=self.low_watermark_ms,
                        min_start=self.min_start_ms,
                        provider_grace_ms=self.provider_grace_ms,
                    )
            except Exception:
                logger.debug("Streaming tuning summary unavailable", call_id=call_id)
            # Remove from active streams
            if call_id in self.active_streams:
                del self.active_streams[call_id]
            self._refresh_streaming_summary_metrics()
            # Record last segment end timestamp for adaptive gating of next segment
            try:
                self._last_segment_end_ts[call_id] = time.time()
            except Exception:
                pass
            
            # Clean up jitter buffer
            if call_id in self.jitter_buffers:
                del self.jitter_buffers[call_id]
            self._startup_ready.pop(call_id, None)
            self._resample_states.pop(call_id, None)
            self._dc_block_state.pop(call_id, None)
            self._rtp_codec_cache.pop(call_id, None)
            # Metrics are aggregate; refreshed when active_streams changes.
            
            # Reset session streaming flags
            try:
                sess = await self.session_store.get_by_call_id(call_id)
                if sess:
                    sess.streaming_started = False
                    sess.current_stream_id = None
                    await self.session_store.upsert_call(sess)
            except Exception:
                pass
            # Clear any remainder record after flushing
            self.frame_remainders.pop(call_id, None)
            
            
            
            logger.debug("Streaming cleanup completed",
                        call_id=call_id,
                        stream_id=stream_id)
            
        except Exception as e:
            logger.error("Error cleaning up stream",
                        call_id=call_id,
                        stream_id=stream_id,
                        error=str(e))
        finally:
            self._cleanup_in_progress.discard(call_id)
    
    def _generate_stream_id(self, call_id: str, playback_type: str) -> str:
        """Generate deterministic stream ID."""
        timestamp = int(time.time() * 1000)
        return f"stream:{playback_type}:{call_id}:{timestamp}"
    
    def is_stream_active(self, call_id: str) -> bool:
        """Return True if a streaming playback is active for the call."""
        info = self.active_streams.get(call_id)
        if not info:
            return False
        task = info.get('streaming_task')
        return task is not None and not task.done()

    async def get_active_streams(self) -> Dict[str, Dict[str, Any]]:
        """Get information about active streams."""
        return dict(self.active_streams)
    
    async def cleanup_expired_streams(self, max_age_seconds: float = 300) -> int:
        """Clean up expired streams."""
        current_time = time.time()
        expired_calls = []
        
        for call_id, stream_info in self.active_streams.items():
            age = current_time - stream_info['start_time']
            if age > max_age_seconds:
                expired_calls.append(call_id)
        
        for call_id in expired_calls:
            stream_info = self.active_streams[call_id]
            await self._cleanup_stream(call_id, stream_info['stream_id'])
        
        return len(expired_calls)
