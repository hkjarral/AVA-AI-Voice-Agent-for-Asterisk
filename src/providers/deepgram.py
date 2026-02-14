import asyncio
import contextlib
import json
import websockets
import time
import array
import re
import audioop
from typing import Callable, Optional, List, Dict, Any
import websockets.exceptions
from websockets.asyncio.client import ClientConnection

from structlog import get_logger
from prometheus_client import Gauge, Info
from ..audio.resampler import (
    mulaw_to_pcm16le,
    pcm16le_to_mulaw,
    resample_audio,
)
from ..config import LLMConfig
from .base import AIProviderInterface, ProviderCapabilities

# Tool calling support
from src.tools.registry import tool_registry
from src.tools.adapters.deepgram import DeepgramToolAdapter

logger = get_logger(__name__)


def _log_provider_task_exception(task: asyncio.Task) -> None:
    """Done-callback: log exceptions from fire-and-forget provider tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("Provider background task failed", task_name=task.get_name(), error=str(exc), exc_info=exc)


_DEEPGRAM_INPUT_RATE = Gauge(
    "ai_agent_deepgram_input_sample_rate_hz",
    "Configured Deepgram input sample rate per call",
)
_DEEPGRAM_OUTPUT_RATE = Gauge(
    "ai_agent_deepgram_output_sample_rate_hz",
    "Configured Deepgram output sample rate per call",
)
_DEEPGRAM_SESSION_AUDIO_INFO = Info(
    "ai_agent_deepgram_session_audio",
    "Deepgram session audio encodings/sample rates",
)
_DEEPGRAM_SETTINGS_ACK_LATENCY_MS = Gauge(
    "ai_agent_deepgram_settings_ack_latency_ms",
    "Latency from Settings send to SettingsApplied ACK (ms)",
)

class DeepgramProvider(AIProviderInterface):
    @staticmethod
    def _canonicalize_encoding(value: Optional[str]) -> str:
        t = (value or '').strip().lower()
        if t in ('mulaw', 'mu-law', 'g711_ulaw', 'g711ulaw', 'g711-ula', 'g711ulaw', 'ulaw'):
            return 'mulaw'
        if t in ('slin16', 'linear16', 'pcm16'):
            return 'linear16'
        return t or 'mulaw'

    def _get_config_value(self, key: str, default: Optional[Any] = None) -> Optional[Any]:
        try:
            if isinstance(self.config, dict):
                return self.config.get(key, default)
            return getattr(self.config, key, default)
        except Exception:
            return default

    def _extract_request_session_ids(self, payload: Any) -> tuple[Optional[str], Optional[str]]:
        rid: Optional[str] = None
        sid: Optional[str] = None
        try:
            if isinstance(payload, dict):
                v = payload.get("request_id")
                if isinstance(v, str) and not rid:
                    rid = v
                v = payload.get("session_id") or payload.get("sessionId")
                if isinstance(v, str) and not sid:
                    sid = v
                hdrs = payload.get("headers") or {}
                if isinstance(hdrs, dict) and not rid:
                    hv = hdrs.get("x-request-id") or hdrs.get("X-Request-Id")
                    if isinstance(hv, str):
                        rid = hv
                sess = payload.get("session") or {}
                if isinstance(sess, dict) and not sid:
                    sv = sess.get("id") or sess.get("session_id")
                    if isinstance(sv, str):
                        sid = sv
            def walk(obj: Any):
                nonlocal rid, sid
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        kl = str(k).lower()
                        if rid is None and "request" in kl and isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F-]{36}", v or ""):
                            rid = v
                        if sid is None and (kl == "session_id" or kl == "sessionid") and isinstance(v, str):
                            sid = v
                        if isinstance(v, (dict, list)):
                            walk(v)
                elif isinstance(obj, list):
                    for it in obj:
                        walk(it)
            if rid is None or sid is None:
                walk(payload)
        except Exception:
            pass
        return rid, sid

    def _update_output_format(self, encoding: Optional[str], sample_rate: Optional[Any], source: str = "runtime") -> None:
        try:
            if encoding:
                canon = self._canonicalize_encoding(encoding)
                if canon and canon != self._dg_output_encoding:
                    logger.info(
                        "Deepgram output format override",
                        call_id=self.call_id,
                        previous_encoding=self._dg_output_encoding,
                        new_encoding=canon,
                        source=source,
                    )
                    self._dg_output_encoding = canon
            if sample_rate:
                try:
                    rate_val = int(sample_rate)
                    if rate_val > 0 and rate_val != self._dg_output_rate:
                        logger.info(
                            "Deepgram output sample rate override",
                            call_id=self.call_id,
                            previous_rate=self._dg_output_rate,
                            new_rate=rate_val,
                            source=source,
                        )
                        self._dg_output_rate = rate_val
                except Exception:
                    logger.debug("Deepgram output sample rate parse failed", value=sample_rate, exc_info=True)
        except Exception:
            logger.debug("Deepgram output format update failed", encoding=encoding, sample_rate=sample_rate, source=source, exc_info=True)

    def __init__(self, config: Dict[str, Any], llm_config: LLMConfig, on_event: Callable[[Dict[str, Any]], None]):
        super().__init__(on_event)
        self.config = config
        self.llm_config = llm_config
        self.websocket: Optional[ClientConnection] = None
        self._keep_alive_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._is_audio_flowing = False
        self.request_id: Optional[str] = None
        self.session_id: Optional[str] = None
        self.call_id: Optional[str] = None
        
        # Tool calling support
        self.tool_adapter = DeepgramToolAdapter(tool_registry)
        logger.info("🛠️ Deepgram provider initialized with tool support")
        self._allowed_tools: Optional[List[str]] = None
        self._in_audio_burst: bool = False
        self._first_output_chunk_logged: bool = False
        self._closing: bool = False
        self._closed: bool = False
        # Maintain resample state for smoother conversion
        self._input_resample_state = None
        
        # Turn latency tracking (Milestone 21 - Call History)
        self._turn_start_time: Optional[float] = None
        self._turn_first_audio_received: bool = False
        self._session_store = None  # Will be set via set_session_store()
        # Settings/stream readiness
        self._settings_sent: bool = False
        # Only set to True on explicit SettingsApplied lifecycle event
        self._settings_acked: bool = False
        self._ready_to_stream: bool = False
        self._settings_ts: float = 0.0
        self._prestream_queue: list[bytes] = []  # small buffer for early frames
        self._pcm16_accum = bytearray()
        # Settings ACK gating
        self._ack_event: Optional[asyncio.Event] = None
        # Greeting injection guard
        self._greeting_injected: bool = False
        self._greeting_injections: int = 0
        # Upstream RMS tracking
        self._rms_ma: float = 0.0
        self._low_rms_streak: int = 0
        self._rms_log_started: bool = False
        # Per-call low RMS warning suppression (AAVA-15)
        self._low_rms_warnings_logged: Dict[str, int] = {}
        # User transcript counters
        self._user_txn_count: int = 0
        self._user_last_ts: float = 0.0
        # Hangup tracking (for farewell + HangupReady event)
        self._hangup_pending: bool = False
        self._farewell_message: Optional[str] = None
        # Cache declared Deepgram input settings
        try:
            self._dg_input_rate = int(self._get_config_value('input_sample_rate_hz', 8000) or 8000)
        except Exception:
            self._dg_input_rate = 8000
        # Cache provider output settings for downstream conversion/metadata
        self._original_output_encoding = self._get_config_value('output_encoding', None) or 'mulaw'
        self._original_output_rate = self._get_config_value('output_sample_rate_hz', 8000) or 8000
        self._dg_output_encoding = self._canonicalize_encoding(self._original_output_encoding)
        try:
            self._dg_output_rate = int(self._original_output_rate)
        except Exception:
            self._dg_output_rate = 8000
        # Allow optional runtime detection when explicitly enabled
        self.allow_output_autodetect = bool(self._get_config_value('allow_output_autodetect', False))
        self._dg_output_inferred = not self.allow_output_autodetect
        # Settings retry state
        self._settings_retry_attempted: bool = False
        self._last_settings_payload: Optional[dict] = None
        self._last_settings_minimal: Optional[dict] = None

    def set_session_store(self, session_store):
        """Set the session store for turn latency tracking (Milestone 21)."""
        self._session_store = session_store

    @property
    def supported_codecs(self) -> List[str]:
        return ["ulaw"]

    def is_ready(self) -> bool:
        """Check if provider is properly configured with required API key."""
        api_key = self._get_config_value('api_key', '')
        return bool(api_key and str(api_key).strip())

    # P1: Static capability hints for orchestrator
    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            # Audio format capabilities
            input_encodings=["mulaw", "linear16"],
            input_sample_rates_hz=[8000, 16000],
            output_encodings=["mulaw"],
            output_sample_rates_hz=[8000],
            preferred_chunk_ms=20,
            can_negotiate=True,  # Uses SettingsApplied ACK for runtime negotiation
            # Provider type and audio processing capabilities
            is_full_agent=True,  # Full voice agent (STT + LLM + TTS integrated)
            has_native_vad=True,  # Deepgram Voice Agent has built-in VAD
            has_native_barge_in=True,  # Handles interruptions internally
            requires_continuous_audio=True,  # Needs continuous audio for VAD
        )
    
    def parse_ack(self, event_data: Dict[str, Any]) -> Optional[ProviderCapabilities]:
        """
        Parse SettingsApplied event from Deepgram to extract actual negotiated formats.
        
        Returns capabilities based on provider ACK, or None if not a SettingsApplied event.
        """
        event_type = event_data.get('type')
        if event_type != 'SettingsApplied':
            return None
        
        try:
            settings = event_data.get('settings', {})
            audio = settings.get('audio', {})
            input_audio = audio.get('input', {})
            output_audio = audio.get('output', {})
            
            input_enc = self._canonicalize_encoding(input_audio.get('encoding', 'mulaw'))
            output_enc = self._canonicalize_encoding(output_audio.get('encoding', 'mulaw'))
            input_rate = int(input_audio.get('sample_rate', 8000))
            output_rate = int(output_audio.get('sample_rate', 8000))
            
            logger.info(
                "Parsed Deepgram SettingsApplied ACK",
                call_id=self.call_id,
                input_encoding=input_enc,
                input_sample_rate=input_rate,
                output_encoding=output_enc,
                output_sample_rate=output_rate,
            )
            
            return ProviderCapabilities(
                input_encodings=[input_enc],
                input_sample_rates_hz=[input_rate],
                output_encodings=[output_enc],
                output_sample_rates_hz=[output_rate],
                preferred_chunk_ms=20,
                can_negotiate=True,  # ACK confirmed successful negotiation
            )
        except Exception as exc:
            logger.warning(
                "Failed to parse Deepgram SettingsApplied event",
                call_id=self.call_id,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------ #
    # Metrics helpers
    # ------------------------------------------------------------------ #
    def _record_session_audio(
        self,
        *,
        input_encoding: str,
        input_sample_rate_hz: int,
        output_encoding: str,
        output_sample_rate_hz: int,
    ) -> None:
        call_id = self.call_id
        if not call_id:
            return
        try:
            _DEEPGRAM_INPUT_RATE.set(int(input_sample_rate_hz))
        except Exception:
            pass
        try:
            _DEEPGRAM_OUTPUT_RATE.set(int(output_sample_rate_hz))
        except Exception:
            pass
        info_payload = {
            "input_encoding": str(input_encoding or ""),
            "input_sample_rate_hz": str(input_sample_rate_hz),
            "output_encoding": str(output_encoding or ""),
            "output_sample_rate_hz": str(output_sample_rate_hz),
        }
        try:
            _DEEPGRAM_SESSION_AUDIO_INFO.info(info_payload)
        except Exception:
            pass

    def _clear_metrics(self, call_id: Optional[str]) -> None:
        return

    def _apply_dc_block(self, pcm_bytes: bytes, r: float = 0.995) -> bytes:
        """Apply first-order DC-block filter to PCM16 little-endian audio."""
        if not pcm_bytes:
            return pcm_bytes
        try:
            buf = array.array('h')
            buf.frombytes(pcm_bytes)
            prev_x = 0.0
            prev_y = 0.0
            for i, s in enumerate(buf):
                x = float(int(s))
                y = x - prev_x + r * prev_y
                prev_x, prev_y = x, y
                if y > 32767.0:
                    y = 32767.0
                elif y < -32768.0:
                    y = -32768.0
                buf[i] = int(y)
            return buf.tobytes()
        except Exception:
            return pcm_bytes

    async def start_session(self, call_id: str, context: Optional[Dict[str, Any]] = None):
        # Use configurable voice agent endpoint when available; fall back to default.
        base_url = getattr(self.config, "voice_agent_base_url", None) or "wss://agent.deepgram.com/v1/agent/converse"
        ws_url = base_url
        headers = {'Authorization': f'Token {self.config.api_key}'}

        try:
            logger.info("Connecting to Deepgram Voice Agent...", url=ws_url)
            self.websocket = await websockets.connect(ws_url, additional_headers=list(headers.items()))
            logger.info("✅ Successfully connected to Deepgram Voice Agent.")

            # Persist call context for downstream events
            self.call_id = call_id
            # Per-call tool allowlist (contexts are the source of truth).
            # Missing/None is treated as [] for safety.
            if context and "tools" in context:
                self._allowed_tools = list(context.get("tools") or [])
            else:
                self._allowed_tools = []
            # Capture Deepgram request id if provided
            try:
                rid = None
                if hasattr(self.websocket, "response_headers") and self.websocket.response_headers:
                    rid = self.websocket.response_headers.get("x-request-id")
                if rid:
                    self.request_id = rid
                    logger.info("Deepgram request id", call_id=call_id, request_id=rid)
            except Exception:
                logger.debug("Failed to read Deepgram response headers", exc_info=True)

            # Prepare ACK gate and start receiver early to catch server responses
            self._ack_event = asyncio.Event()
            self._receive_task = asyncio.create_task(self._receive_loop())

            await self._configure_agent()
            self._keep_alive_task = asyncio.create_task(self._keep_alive())

        except Exception as e:
            logger.error("Failed to connect to Deepgram Voice Agent", exc_info=True)
            if self._keep_alive_task:
                self._keep_alive_task.cancel()
            raise

    async def _configure_agent(self):
        """Builds and sends the V1 Settings message to the Deepgram Voice Agent."""
        # Derive codec settings from config with safe defaults
        input_encoding = self._get_config_value('input_encoding', None) or 'ulaw'
        input_sample_rate = int(self._get_config_value('input_sample_rate_hz', 8000) or 8000)
        # Choose output based on voice capabilities (fallback to configured defaults)
        output_encoding = self._original_output_encoding
        output_sample_rate = int(self._original_output_rate or 8000)
        self._dg_output_encoding = self._canonicalize_encoding(output_encoding)
        self._dg_output_rate = output_sample_rate
        self._dg_output_inferred = not self.allow_output_autodetect
        # Canonicalize Deepgram V1 audio.format values
        input_format = self._canonicalize_encoding(input_encoding)
        output_format = self._canonicalize_encoding(output_encoding)

        # Determine greeting precedence: provider override > global LLM greeting > safe default
        try:
            greeting_val = (self._get_config_value('greeting', None) or "").strip()
        except Exception:
            greeting_val = ""
        if not greeting_val:
            try:
                greeting_val = (getattr(self.llm_config, 'initial_greeting', None) or "").strip()
            except Exception:
                greeting_val = ""
        if not greeting_val:
            greeting_val = "Hello, how can I help you today?"

        listen_model = self._get_config_value('model', None) or getattr(self.llm_config, 'listen_model', None) or "nova-2-general"
        speak_model = self._get_config_value('tts_model', None) or getattr(self.llm_config, 'tts_model', None) or "aura-asteria-en"

        # Use configured output encoding/sample rate directly (no catalog fetch needed)
        self._dg_output_encoding = self._canonicalize_encoding(output_encoding)
        self._dg_output_rate = int(output_sample_rate)
        logger.info(
            "Deepgram audio profile configured",
            call_id=self.call_id,
            speak_model=speak_model,
            output_encoding=self._dg_output_encoding,
            output_sample_rate=self._dg_output_rate,
        )
        think_model = getattr(self.llm_config, 'model', None) or "gpt-4o"
        # Try context-injected prompt first (can be 'instructions' or 'prompt' key), then provider config, then llm_config, then default
        think_prompt = (
            self._get_config_value('instructions', None) or  # Context injection uses 'instructions' for Deepgram
            self._get_config_value('prompt', None) or
            getattr(self.llm_config, 'prompt', None) or
            "You are a helpful assistant."
        )
        
        # Log prompt source for debugging
        prompt_source = "hardcoded_default"
        if self._get_config_value('instructions', None):
            prompt_source = "context_injection"
        elif self._get_config_value('prompt', None):
            prompt_source = "provider_config"
        elif getattr(self.llm_config, 'prompt', None):
            prompt_source = "global_llm_config"
        
        logger.info(
            "Deepgram Think prompt resolved",
            call_id=self.call_id,
            prompt_source=prompt_source,
            prompt_length=len(think_prompt),
            prompt_preview=think_prompt[:80] + "..." if len(think_prompt) > 80 else think_prompt,
        )

        # Get configured agent language (default: "en")
        agent_language = str(self._get_config_value("agent_language", "en") or "").strip() or "en"
        
        # Build settings with configured audio formats
        settings = {
            "type": "Settings",
            "audio": {
                "input": { "encoding": input_format, "sample_rate": int(input_sample_rate) },
                "output": { "encoding": output_format, "sample_rate": int(output_sample_rate), "container": "none" }
            },
            "agent": {
                "language": agent_language,
                "listen": { 
                    "provider": { 
                        "type": "deepgram", 
                        "model": "nova-3"  # Twilio uses nova-3
                    } 
                },
                "think": { 
                    "provider": { 
                        "type": "open_ai", 
                        "model": "gpt-4o-mini",  # Twilio uses gpt-4o-mini
                        "temperature": 0.7
                    }, 
                    "prompt": think_prompt 
                },
                "speak": {
                    "provider": {"type": "deepgram", "model": speak_model}  # Revert: keep provider format
                },
                "greeting": greeting_val
            }
        }
        
        # Add tools from context allowlist only.
        # Per Deepgram docs: functions go in agent.think.functions.
        try:
            tools_schemas = self.tool_adapter.get_tools_config(list(self._allowed_tools or []))
            if tools_schemas:
                settings["agent"]["think"]["functions"] = tools_schemas
                logger.info(
                    "✅ Deepgram functions configured",
                    call_id=self.call_id,
                    function_count=len(tools_schemas),
                    functions=[t["name"] for t in tools_schemas],
                )
        except Exception as e:
            logger.warning(f"Failed to configure tools: {e}", call_id=self.call_id, exc_info=True)
        # Build and store a minimal Settings payload for fallback retry on UNPARSABLE error
        try:
            self._last_settings_minimal = {
                "type": "Settings",
                "audio": {
                    "input": { "encoding": input_format, "sample_rate": int(input_sample_rate) }
                },
                "agent": {
                    "greeting": greeting_val,
                    "language": agent_language,
                    "listen": { "provider": { "type": "deepgram", "model": listen_model } },
                    "think": { "provider": { "type": "open_ai", "model": think_model }, "prompt": think_prompt },
                    "speak": { "provider": { "type": "deepgram", "model": speak_model } }
                }
            }
        except Exception:
            self._last_settings_minimal = None
        self._last_settings_payload = settings
        # Log the exact Settings payload being sent to Deepgram
        logger.info(
            "Sending Settings to Deepgram Voice Agent",
            call_id=self.call_id,
            settings_payload=settings,
        )
        await self.websocket.send(json.dumps(settings))
        # Mark settings sent; readiness only upon server response (ACK) or timeout
        self._settings_sent = True
        try:
            import time as _t
            self._settings_ts = _t.monotonic()
        except Exception:
            self._settings_ts = 0.0
        # Start a fallback timer to avoid indefinite buffering if ACK never arrives
        # Disable pre-ACK fallback readiness to avoid sending binary before SettingsApplied
        # (prevents Deepgram error BINARY_MESSAGE_BEFORE_SETTINGS). We still keep a timestamp
        # to log if ACK is slow, but we will not open the binary gate without an explicit ACK.
        # async fallback task intentionally removed.

        # Immediately inject greeting once to try to kick off TTS
        async def _inject_greeting_immediate():
            try:
                if self.websocket and self.websocket.state.name == "OPEN" and greeting_val and self._greeting_injections < 1:
                    logger.info("Injecting greeting immediately after Settings", call_id=self.call_id)
                    self._greeting_injections += 1
                    try:
                        await self._inject_message_dual(greeting_val)
                    except Exception:
                        logger.debug("Immediate greeting injection failed", exc_info=True)
            except Exception:
                pass
        # Wait up to 1.0s for a server response to mark readiness
        try:
            if self._ack_event is not None:
                await asyncio.wait_for(self._ack_event.wait(), timeout=1.0)
            else:
                logger.debug("ACK gate not initialized; skipping wait")
        except asyncio.TimeoutError:
            logger.warning("Deepgram settings ACK not received within timeout; fallback readiness may be active")
        # If ready and we haven't seen any audio burst within ~1s, inject greeting once to kick off TTS
        async def _inject_greeting_if_quiet():
            try:
                await asyncio.sleep(1.5)
                if self.websocket and self.websocket.state.name == "OPEN" and not self._in_audio_burst and greeting_val and self._greeting_injections < 2:
                    logger.info("Injecting greeting via fallback as no AgentAudio detected", call_id=self.call_id)
                    try:
                        self._greeting_injections += 1
                        await self._inject_message_dual(greeting_val)
                    except Exception:
                        logger.debug("Greeting injection failed", exc_info=True)
            except Exception:
                pass
        # Disable fallback greeting injection; avoid extra messages pre-ack
        # asyncio.create_task(_inject_greeting_if_quiet())
        summary = {
            "input_encoding": str(input_encoding).lower(),
            "input_sample_rate_hz": int(input_sample_rate),
            "output_encoding": str(self._dg_output_encoding).lower(),
            "output_sample_rate_hz": int(self._dg_output_rate),
        }
        self._record_session_audio(**summary)
        logger.info(
            "Deepgram agent configured",
            call_id=self.call_id,
            **summary,
        )

    async def send_audio(self, audio_chunk: bytes, sample_rate: int = None, encoding: str = None):
        """Send caller audio to Deepgram in the declared input format.

        Args:
            audio_chunk: Audio data bytes
            sample_rate: Source sample rate (if provided by engine)
            encoding: Source encoding format (if provided by engine)
        
        Engine provides explicit encoding/sample_rate when available.
        Falls back to chunk size inference for backward compatibility.
        """
        if self.websocket and audio_chunk:
            try:
                self._is_audio_flowing = True
                chunk_len = len(audio_chunk)
                input_encoding = (self._get_config_value("input_encoding", None) or "mulaw").strip().lower()
                target_rate = int(self._get_config_value("input_sample_rate_hz", 8000) or 8000)
                
                # Use explicit encoding/sample_rate from engine if provided
                if encoding and sample_rate:
                    actual_format = encoding.lower().strip()
                    if actual_format in ("slin16", "linear16", "pcm16"):
                        actual_format = "pcm16"
                    elif actual_format in ("ulaw", "mulaw", "g711_ulaw"):
                        actual_format = "ulaw"
                    src_rate = sample_rate
                else:
                    # Fallback: Infer from chunk size (backward compatibility)
                    # - 160 B ≈ μ-law @ 8 kHz (20 ms)
                    # - 320 B ≈ PCM16 @ 8 kHz (20 ms)
                    # - 640 B ≈ PCM16 @ 16 kHz (20 ms)
                    if chunk_len == 160:
                        actual_format = "ulaw"
                        src_rate = 8000
                    elif chunk_len == 320:
                        actual_format = "pcm16"
                        src_rate = 8000
                    elif chunk_len == 640:
                        actual_format = "pcm16"
                        src_rate = 16000
                    else:
                        actual_format = "pcm16" if input_encoding in ("slin16", "linear16", "pcm16") else "ulaw"
                        try:
                            src_rate = int(self._get_config_value("input_sample_rate_hz", 0) or 0) or (16000 if actual_format == "pcm16" else 8000)
                        except Exception:
                            src_rate = 8000

                try:
                    frame_bytes = 160 if actual_format == "ulaw" else int(max(1, src_rate) / 50) * 2
                except Exception:
                    frame_bytes = 0
                if frame_bytes and chunk_len % frame_bytes != 0:
                    logger.debug(
                        "Deepgram provider irregular chunk size",
                        bytes=chunk_len,
                        frame_bytes=frame_bytes,
                        actual_format=actual_format,
                        src_rate=src_rate,
                    )

                payload: bytes = audio_chunk
                pcm_for_rms: Optional[bytes] = None

                if input_encoding in ("ulaw", "mulaw", "g711_ulaw", "mu-law"):
                    if actual_format == "pcm16":
                        try:
                            payload = audioop.lin2ulaw(audio_chunk, 2)
                        except Exception:
                            logger.warning("Failed to convert PCM to μ-law for Deepgram", exc_info=True)
                            payload = audio_chunk
                    else:
                        payload = audio_chunk

                    pcm_for_rms = mulaw_to_pcm16le(payload)
                    if target_rate and target_rate != 8000:
                        pcm_resampled, self._input_resample_state = resample_audio(
                            pcm_for_rms,
                            8000,
                            target_rate,
                            state=self._input_resample_state,
                        )
                        try:
                            payload = audioop.lin2ulaw(pcm_resampled, 2)
                        except Exception:
                            logger.warning("Failed to convert resampled PCM back to μ-law", exc_info=True)
                            payload = audio_chunk
                        pcm_for_rms = pcm_resampled
                    else:
                        self._input_resample_state = None

                elif input_encoding in ("slin16", "linear16", "pcm16"):
                    # Normalize inbound to PCM16 and resample from detected source rate to target_rate
                    if actual_format == "ulaw":
                        pcm_src = mulaw_to_pcm16le(audio_chunk)
                        src_rate = 8000
                    else:
                        pcm_src = audio_chunk
                    pcm_for_rms = pcm_src
                    if target_rate and target_rate != src_rate:
                        pcm_dst, self._input_resample_state = resample_audio(
                            pcm_src,
                            src_rate,
                            target_rate,
                            state=self._input_resample_state,
                        )
                        payload = pcm_dst
                        # Use the resampled buffer for RMS diagnostics to avoid false low-energy alerts
                        pcm_for_rms = pcm_dst
                    else:
                        self._input_resample_state = None
                        payload = pcm_src
                else:
                    logger.warning(
                        "Unsupported Deepgram input_encoding",
                        input_encoding=input_encoding,
                    )
                    payload = audio_chunk
                    pcm_for_rms = None
                    self._input_resample_state = None

                if pcm_for_rms is not None:
                    try:
                        rms = audioop.rms(pcm_for_rms, 2)
                        alpha = 0.2
                        self._rms_ma = (alpha * float(rms)) + (1.0 - alpha) * float(self._rms_ma or 0.0)
                        protect_elapsed = 0.0
                        try:
                            if getattr(self, "_settings_ts", 0.0):
                                protect_elapsed = max(0.0, time.monotonic() - float(self._settings_ts or 0.0))
                        except Exception:
                            protect_elapsed = 0.0
                        gate = (protect_elapsed >= 0.3) and bool(self._ready_to_stream)
                        threshold = 250
                        if gate and rms < threshold:
                            self._low_rms_streak += 1
                            if self._low_rms_streak % 10 == 0:
                                # AAVA-15: Suppress low RMS warnings after first 3
                                call_id = self.call_id or "unknown"
                                warnings_count = self._low_rms_warnings_logged.get(call_id, 0)
                                
                                if warnings_count < 3:
                                    logger.warning(
                                        "Deepgram upstream low RMS sustained",
                                        rms=rms,
                                        rms_ma=int(self._rms_ma),
                                        streak=self._low_rms_streak,
                                        bytes=chunk_len,
                                        target_rate=target_rate,
                                        call_id=call_id,
                                    )
                                    self._low_rms_warnings_logged[call_id] = warnings_count + 1
                                elif warnings_count == 3:
                                    logger.info(
                                        "Deepgram low RMS warnings suppressed (silence is normal)",
                                        call_id=call_id,
                                        total_warnings=3,
                                        streak=self._low_rms_streak,
                                    )
                                    self._low_rms_warnings_logged[call_id] = warnings_count + 1
                                # Else: suppress (already logged 3 warnings + suppression notice)
                        else:
                            if gate and not self._rms_log_started and rms >= threshold:
                                logger.info(
                                    "Deepgram upstream RMS flow",
                                    rms=rms,
                                    rms_ma=int(self._rms_ma),
                                    bytes=chunk_len,
                                    target_rate=target_rate,
                                )
                                self._rms_log_started = True
                            self._low_rms_streak = 0
                        # Quick integrity check on PCM (zeros ratio)
                        # Note: High zero ratio during silence is normal in conversations
                        try:
                            if pcm_for_rms:
                                zc = pcm_for_rms.count(b"\x00")
                                zr = float(zc) / float(len(pcm_for_rms))
                                if gate and zr > 0.5:
                                    logger.debug(
                                        "Deepgram upstream PCM mostly silent",
                                        zero_ratio=round(zr, 3),
                                        bytes=len(pcm_for_rms),
                                    )
                        except Exception:
                            pass
                    except Exception:
                        logger.debug("Deepgram RMS check failed", exc_info=True)

                if input_encoding in ("slin16", "linear16", "pcm16"):
                    frame_bytes = (int(target_rate * 0.02) * 2) if target_rate else 640
                    if frame_bytes <= 0:
                        frame_bytes = 640
                    self._pcm16_accum.extend(payload)
                    frames_to_send: list[bytes] = []
                    while len(self._pcm16_accum) >= frame_bytes:
                        frames_to_send.append(bytes(self._pcm16_accum[:frame_bytes]))
                        del self._pcm16_accum[:frame_bytes]

                    if not self._settings_acked:
                        try:
                            for fr in frames_to_send:
                                self._prestream_queue.append(fr)
                                if len(self._prestream_queue) > 10:
                                    self._prestream_queue.pop(0)
                        except Exception:
                            pass
                        return

                    if self._prestream_queue and self._settings_acked:
                        try:
                            for q in self._prestream_queue:
                                await self.websocket.send(q)
                        except Exception:
                            logger.debug("Deepgram prestream flush failed", exc_info=True)
                        finally:
                            self._prestream_queue.clear()

                    for fr in frames_to_send:
                        await self.websocket.send(fr)
                else:
                    if not self._settings_acked:
                        try:
                            self._prestream_queue.append(payload)
                            if len(self._prestream_queue) > 10:
                                self._prestream_queue.pop(0)
                        except Exception:
                            pass
                        return

                    if self._prestream_queue:
                        try:
                            for q in self._prestream_queue:
                                await self.websocket.send(q)
                        except Exception:
                            logger.debug("Deepgram prestream flush failed", exc_info=True)
                        finally:
                            self._prestream_queue.clear()

                    await self.websocket.send(payload)
            except websockets.exceptions.ConnectionClosed as e:
                logger.debug("Could not send audio packet: Connection closed.", code=e.code, reason=e.reason)
            except Exception:
                logger.error("An unexpected error occurred while sending audio chunk", exc_info=True)
    
    async def _handle_function_call(self, event_data: Dict[str, Any]):
        """
        Handle function call request from Deepgram.
        
        Routes the function call to the appropriate tool via the tool adapter.
        """
        try:
            # Build context for tool execution
            # These will be injected by the engine when it sets up the provider
            context = {
                'call_id': self.call_id,
                'caller_channel_id': getattr(self, '_caller_channel_id', None),
                'bridge_id': getattr(self, '_bridge_id', None),
                'called_number': getattr(self, '_called_number', None),
                'session_store': getattr(self, '_session_store', None),
                'ari_client': getattr(self, '_ari_client', None),
                'config': getattr(self, '_full_config', None),
                'allowed_tools': self._allowed_tools,
                'websocket': self.websocket
            }
            
            # Execute tool via adapter
            result = await self.tool_adapter.handle_tool_call_event(event_data, context)
            
            # Check if this was a hangup request
            if result.get('function_name') == 'hangup_call' and result.get('status') == 'success':
                self._hangup_pending = True
                self._farewell_message = result.get('farewell_message', '')
                logger.info(
                    "🔚 Hangup tool executed - will trigger after farewell audio completes",
                    call_id=self.call_id,
                    farewell=self._farewell_message
                )
            
            # Capture function name BEFORE send_tool_result (which pops it from result)
            func_name = result.get('function_name')
            func_params = event_data.get('functions', [{}])[0].get('arguments', '{}')
            
            # Send result back to Deepgram
            await self.tool_adapter.send_tool_result(result, context)
            
            # Log tool call to session for call history (Milestone 21)
            try:
                session_store = getattr(self, '_session_store', None)
                if session_store and self.call_id and func_name:
                    from datetime import datetime
                    session = await session_store.get_by_call_id(self.call_id)
                    if session:
                        tool_record = {
                            "name": func_name,
                            "params": func_params,
                            "result": result.get("status", "unknown") if isinstance(result, dict) else "success",
                            "message": result.get("message", "") if isinstance(result, dict) else str(result),
                            "timestamp": datetime.now().isoformat(),
                            "duration_ms": 0,
                        }
                        if not hasattr(session, 'tool_calls') or session.tool_calls is None:
                            session.tool_calls = []
                        session.tool_calls.append(tool_record)
                        await session_store.upsert_call(session)
                        logger.debug("Tool call logged to session", call_id=self.call_id, tool=func_name)
            except Exception as log_err:
                logger.debug(f"Failed to log tool call to session: {log_err}", call_id=self.call_id)
            
        except Exception as e:
            logger.error(
                "Function call handling failed",
                call_id=self.call_id,
                function_name=event_data.get('function_name'),
                error=str(e),
                exc_info=True
            )
            # Send error response to Deepgram in correct format
            try:
                function_call_id = event_data.get("id")
                if function_call_id:
                    error_response = {
                        "type": "function_call_result",
                        "id": function_call_id,
                        "function_call_result": {
                            "status": "error",
                            "message": f"Tool execution failed: {str(e)}",
                            "error": str(e)
                        }
                    }
                    if self.websocket and self.websocket.state.name == "OPEN":
                        await self.websocket.send(json.dumps(error_response))
                        logger.info("Sent error response to Deepgram", function_call_id=function_call_id)
            except Exception as send_error:
                logger.error(f"Failed to send error response: {send_error}")

    async def stop_session(self):
        # Prevent duplicate disconnect logs/ops
        if self._closed or self._closing:
            return
        self._closing = True
        try:
            if self._keep_alive_task:
                self._keep_alive_task.cancel()
            if self._receive_task and not self._receive_task.done():
                self._receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._receive_task
            if self.websocket and self.websocket.state.name == "OPEN":
                await self.websocket.close()
            if not self._closed:
                logger.info("Disconnected from Deepgram Voice Agent.")
            self._closed = True
        finally:
            self._receive_task = None
            self._clear_metrics(self.call_id)
            self.call_id = None
            self._closing = False

    async def _keep_alive(self):
        while True:
            try:
                await asyncio.sleep(10)
                if self.websocket and self.websocket.state.name == "OPEN":
                    if not self._is_audio_flowing:
                        await self.websocket.send(json.dumps({"type": "KeepAlive"}))
                    self._is_audio_flowing = False
                else:
                    break
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Error in keep-alive task", exc_info=True)
                break

    def describe_alignment(
        self,
        *,
        audiosocket_format: str,
        streaming_encoding: str,
        streaming_sample_rate: int,
    ) -> List[str]:
        issues: List[str] = []
        cfg_enc = (self._get_config_value("input_encoding", None) or "").lower()
        try:
            cfg_rate = int(self._get_config_value("input_sample_rate_hz", 0) or 0)
        except Exception:
            cfg_rate = 0

        if cfg_enc in ("ulaw", "mulaw", "g711_ulaw", "mu-law"):
            if cfg_rate and cfg_rate != 8000:
                issues.append(
                    f"Deepgram configuration declares μ-law at {cfg_rate} Hz; μ-law transport must be 8000 Hz."
                )
        if cfg_enc in ("slin16", "linear16", "pcm16") and audiosocket_format != "slin16":
            issues.append(
                f"Deepgram expects PCM16 input but audiosocket.format is {audiosocket_format}. "
                "Set audiosocket.format=slin16 or change deepgram.input_encoding."
            )
        # Check streaming alignment with actual Deepgram output config (not hardcoded assumptions)
        dg_out_enc = self._canonicalize_encoding(self._dg_output_encoding or "mulaw")
        dg_out_rate = int(self._dg_output_rate or 8000)

        stream_enc_canon = self._canonicalize_encoding(streaming_encoding)
        # Treat raw 'slin' as PCM16 for alignment comparisons
        if stream_enc_canon == "slin":
            stream_enc_canon = "linear16"

        audiosocket_canon = self._canonicalize_encoding(audiosocket_format)
        bridge_handled = (
            stream_enc_canon in {"linear16", "slin16"}
            and dg_out_enc == "mulaw"
            and audiosocket_canon in {"slin", "slin16", "linear16"}
        )

        if not bridge_handled and stream_enc_canon != dg_out_enc:
            issues.append(
                f"Streaming manager emits {streaming_encoding} frames but Deepgram output_encoding is {dg_out_enc}. "
                f"Ensure downstream playback matches Deepgram output format."
            )
        if streaming_sample_rate != dg_out_rate:
            issues.append(
                f"Streaming sample rate is {streaming_sample_rate} Hz but Deepgram output_sample_rate is {dg_out_rate} Hz."
            )
        return issues

    async def _receive_loop(self):
        if not self.websocket:
            return
        try:
            async for message in self.websocket:
                if isinstance(message, str):
                    try:
                        event_data = json.loads(message)
                        et = event_data.get("type") if isinstance(event_data, dict) else None
                        try:
                            erid, esid = self._extract_request_session_ids(event_data)
                            if erid and not getattr(self, "request_id", None):
                                self.request_id = erid
                                try:
                                    logger.info("Deepgram request id (event)", call_id=self.call_id, request_id=erid)
                                except Exception:
                                    pass
                            if esid and not getattr(self, "session_id", None):
                                self.session_id = esid
                                try:
                                    logger.info("Deepgram session id (event)", call_id=self.call_id, session_id=esid)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        # Mark readiness only upon SettingsApplied to avoid pre-ACK races
                        if et == "SettingsApplied":
                            self._settings_acked = True
                            self._ready_to_stream = True
                            try:
                                if self._ack_event and not self._ack_event.is_set():
                                    self._ack_event.set()
                            except Exception:
                                pass
                            # Emit SettingsApplied ACK latency
                            try:
                                if self._settings_ts:
                                    latency_ms = max(0.0, (time.monotonic() - float(self._settings_ts)) * 1000.0)
                                    if self.call_id:
                                        _DEEPGRAM_SETTINGS_ACK_LATENCY_MS.set(latency_ms)
                                        logger.info("Deepgram settings ACK latency", call_id=self.call_id, latency_ms=round(latency_ms, 1))
                            except Exception:
                                logger.debug("Failed to record settings ACK latency", exc_info=True)
                        # One-time ACK settings log for effective audio configs (log full payload)
                        try:
                            if getattr(self, "_settings_sent", False) and not getattr(self, "_ack_logged", False):
                                audio_ack = {}
                                if isinstance(event_data, dict):
                                    audio_ack = event_data.get("audio") or {}
                                    # Capture request_id and session_id from ACK/Welcome if header was missing
                                    rid = event_data.get("request_id")
                                    sid = event_data.get("session_id")
                                    if rid and not getattr(self, "request_id", None):
                                        self.request_id = rid
                                        try:
                                            logger.info("Deepgram request id (ack)", call_id=self.call_id, request_id=rid)
                                        except Exception:
                                            pass
                                    if sid and not getattr(self, "session_id", None):
                                        self.session_id = sid
                                        try:
                                            logger.info("Deepgram session id (ack)", call_id=self.call_id, session_id=sid)
                                        except Exception:
                                            pass
                                # Enhanced ACK diagnostics
                                out_cfg = {}
                                in_cfg = {}
                                if isinstance(audio_ack, dict):
                                    out_cfg = audio_ack.get("output") or {}
                                    in_cfg = audio_ack.get("input") or {}
                                
                                ack_encoding = out_cfg.get("encoding")
                                ack_rate = out_cfg.get("sample_rate")
                                ack_container = out_cfg.get("container")
                                ack_bitrate = out_cfg.get("bitrate")
                                
                                in_encoding = in_cfg.get("encoding")
                                in_rate = in_cfg.get("sample_rate")
                                
                                # Determine if settings were accepted
                                requested_encoding = self._dg_output_encoding
                                requested_rate = self._dg_output_rate
                                
                                # Voice Agent API's SettingsApplied event doesn't include audio details
                                # The presence of the event itself means settings were accepted
                                settings_accepted = True  # SettingsApplied received = accepted
                                
                                # Check for explicit mismatch only if ACK has values
                                settings_match = True
                                if ack_encoding and ack_rate:
                                    settings_match = (ack_encoding == requested_encoding and ack_rate == requested_rate)
                                
                                logger.info(
                                    "🔧 DEEPGRAM ACK SETTINGS",
                                    call_id=self.call_id,
                                    request_id=getattr(self, "request_id", None),
                                    # What we requested
                                    requested_output_encoding=requested_encoding,
                                    requested_output_rate=requested_rate,
                                    # What Deepgram acknowledged
                                    ack_output_encoding=ack_encoding,
                                    ack_output_rate=ack_rate,
                                    ack_output_container=ack_container,
                                    ack_output_bitrate=ack_bitrate,
                                    ack_input_encoding=in_encoding,
                                    ack_input_rate=in_rate,
                                    # Validation
                                    settings_accepted=settings_accepted,
                                    settings_match=settings_match,
                                    ack_empty=not audio_ack,
                                    event_type=(event_data.get("type") if isinstance(event_data, dict) else None),
                                    full_ack=event_data,
                                )
                                
                                # Empty ACK is normal for Voice Agent API
                                if not audio_ack or (not ack_encoding and not ack_rate):
                                    logger.debug(
                                        "Deepgram SettingsApplied without audio details (normal for Voice Agent API)",
                                        call_id=self.call_id,
                                        requested_encoding=requested_encoding,
                                        requested_rate=requested_rate,
                                    )
                                elif not settings_match:
                                    logger.warning(
                                        "⚠️ DEEPGRAM CHANGED OUTPUT SETTINGS",
                                        call_id=self.call_id,
                                        requested_encoding=requested_encoding,
                                        requested_rate=requested_rate,
                                        actual_encoding=ack_encoding,
                                        actual_rate=ack_rate,
                                    )
                                
                                try:
                                    self._update_output_format(ack_encoding, ack_rate, source="ack")
                                except Exception:
                                    logger.debug("Deepgram ACK output parsing failed", exc_info=True)
                                try:
                                    self._ack_logged = True
                                except Exception:
                                    pass
                        except Exception:
                            logger.debug("Deepgram ACK logging failed", exc_info=True)
                        # Surface final provider output format to engine for early alignment
                        try:
                            if self.on_event:
                                await self.on_event({
                                    'type': 'ProviderAudioFormat',
                                    'call_id': self.call_id,
                                    'encoding': self._dg_output_encoding,
                                    'sample_rate': self._dg_output_rate,
                                })
                        except Exception:
                            logger.debug("ProviderAudioFormat event emission failed", exc_info=True)
                        # Always log control events with enhanced metadata
                        try:
                            et = event_data.get("type") if isinstance(event_data, dict) else None
                            # Log all lifecycle events with full context
                            logger.info(
                                "Deepgram lifecycle event",
                                call_id=self.call_id,
                                event_type=et,
                                request_id=getattr(self, "request_id", None),
                                session_id=getattr(self, "session_id", None),
                            )
                            
                            # Enhanced logging for specific event types
                            if et == "SettingsApplied":
                                logger.info(
                                    "✅ Deepgram SettingsApplied",
                                    call_id=self.call_id,
                                    request_id=getattr(self, "request_id", None),
                                    session_id=getattr(self, "session_id", None),
                                    settings=event_data,
                                )
                            elif et == "Welcome":
                                logger.info(
                                    "🔌 Deepgram Welcome",
                                    call_id=self.call_id,
                                    request_id=getattr(self, "request_id", None),
                                    session_id=getattr(self, "session_id", None),
                                )
                            elif et == "UserStartedSpeaking":
                                logger.info(
                                    "🎤 Deepgram UserStartedSpeaking",
                                    call_id=self.call_id,
                                    request_id=getattr(self, "request_id", None),
                                )
                            elif et == "UserStoppedSpeaking":
                                logger.info(
                                    "🔇 Deepgram UserStoppedSpeaking",
                                    call_id=self.call_id,
                                    request_id=getattr(self, "request_id", None),
                                )
                                # Track turn start time when user STOPS speaking (Milestone 21)
                                # This measures: speech end → first AI audio response
                                self._turn_start_time = time.time()
                                self._turn_first_audio_received = False
                            elif et == "FunctionCallRequest":
                                # Extract function details for logging (actual Deepgram format)
                                functions = event_data.get("functions", [])
                                func_id = functions[0].get("id") if functions else None
                                func_name = functions[0].get("name") if functions else None
                                logger.info(
                                    "📞 Deepgram FunctionCallRequest",
                                    call_id=self.call_id,
                                    function_call_id=func_id,
                                    function_name=func_name,
                                    function_count=len(functions),
                                    request_id=getattr(self, "request_id", None),
                                )
                                # Handle function call via tool adapter
                                _t = asyncio.create_task(self._handle_function_call(event_data))
                                _t.add_done_callback(_log_provider_task_exception)
                            elif et == "ConnectionClosed":
                                logger.info(
                                    "🔌 Deepgram ConnectionClosed",
                                    call_id=self.call_id,
                                    request_id=getattr(self, "request_id", None),
                                    code=event_data.get("code"),
                                    reason=event_data.get("reason"),
                                )
                            # Set ACK gate only on SettingsApplied (not Welcome)
                            if et == "SettingsApplied" and self._ack_event and not self._ack_event.is_set():
                                try:
                                    self._ack_event.set()
                                except Exception:
                                    pass
                            # Settings Error handling: retry once with minimal Settings, then stop
                            if et == "Error":
                                # Log payload details at error for RCA
                                try:
                                    logger.error("Deepgram error detail", call_id=self.call_id, payload=event_data)
                                except Exception:
                                    pass
                                # If not yet retried and we have a minimal payload, attempt resend once
                                if not self._settings_retry_attempted and self._last_settings_minimal and self.websocket and self.websocket.state.name == "OPEN":
                                    try:
                                        self._settings_retry_attempted = True
                                        logger.warning("Deepgram Settings error; retrying with minimal Settings", call_id=self.call_id)
                                        await self.websocket.send(json.dumps(self._last_settings_minimal))
                                        # Do not continue here; allow loop to process next server message
                                    except Exception:
                                        logger.debug("Failed to send minimal Settings retry", exc_info=True)
                                else:
                                    try:
                                        asyncio.create_task(self.stop_session())
                                    except Exception:
                                        pass
                                    continue
                            if isinstance(event_data, dict) and et == "ConversationText":
                                try:
                                    role = event_data.get("role")
                                    text = event_data.get("text") or event_data.get("content")
                                    logger.info(
                                        "Deepgram conversation text",
                                        call_id=self.call_id,
                                        role=role,
                                        text=text,
                                        segments=event_data.get("segments"),
                                    )
                                    
                                    # Track turn start time on user ConversationText (Milestone 21)
                                    # Deepgram Voice Agent doesn't send UserStoppedSpeaking,
                                    # so we use ConversationText with role="user" as speech end signal
                                    if role == "user" and text:
                                        self._turn_start_time = time.time()
                                        self._turn_first_audio_received = False
                                    
                                    # Track conversation for email tools
                                    # Debug: Check conditions
                                    has_call_id = bool(self.call_id)
                                    has_text = bool(text)
                                    has_attr = hasattr(self, '_session_store')
                                    has_store = bool(getattr(self, '_session_store', None))
                                    
                                    logger.debug(
                                        "🔍 Conversation tracking check",
                                        call_id=self.call_id,
                                        has_call_id=has_call_id,
                                        has_text=has_text,
                                        has_attr=has_attr,
                                        has_store=has_store,
                                        role=role
                                    )
                                    
                                    if self.call_id and text and hasattr(self, '_session_store') and self._session_store:
                                        try:
                                            session = await self._session_store.get_by_call_id(self.call_id)
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
                                                    call_id=self.call_id,
                                                    role=role,
                                                    text_preview=text[:50] + "..." if len(text) > 50 else text
                                                )
                                            else:
                                                logger.warning(
                                                    "⚠️ Session not found for conversation tracking",
                                                    call_id=self.call_id
                                                )
                                        except Exception as e:
                                            logger.error(
                                                "❌ Failed to track conversation",
                                                call_id=self.call_id,
                                                error=str(e),
                                                exc_info=True
                                            )
                                except Exception:
                                    logger.debug("Deepgram conversation text logging failed", exc_info=True)
                            if et in ("Error", "Warning"):
                                try:
                                    logger.warning(
                                        "Deepgram control detail",
                                        call_id=self.call_id,
                                        payload=event_data,
                                    )
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        # Post-ACK injection when readiness events arrive and audio hasn't started
                        # DISABLED: Let Deepgram Voice Agent handle greeting via agent.greeting setting
                        # to avoid duplicate greeting (plays twice otherwise)
                        try:
                            et = event_data.get("type") if isinstance(event_data, dict) else None
                            if et == "SettingsApplied" and not self._in_audio_burst and self._greeting_injections < 2:
                                if self.websocket and self.websocket.state.name == "OPEN":
                                    logger.info("Skipping greeting injection - using Deepgram agent greeting", call_id=self.call_id, event_type=et)
                                    # Greeting injection disabled to prevent duplicate
                                    # self._greeting_injections += 1
                                    # try:
                                    #     await self._inject_message_dual((getattr(self.llm_config, 'initial_greeting', None) or self._get_config_value('greeting', None) or "Hello, how can I help you today?").strip())
                                    # except Exception:
                                    #     logger.debug("Post-ACK greeting injection failed", exc_info=True)
                        except Exception:
                            pass
                        # If we were in an audio burst, a JSON control/event frame marks a boundary
                        if self._in_audio_burst and self.on_event:
                            await self.on_event({
                                'type': 'AgentAudioDone',
                                'streaming_done': True,
                                'call_id': self.call_id
                            })
                            self._in_audio_burst = False
                            
                            # Check if farewell audio completed after hangup request
                            if self._hangup_pending:
                                logger.info(
                                    "🔚 Farewell audio completed - emitting HangupReady",
                                    call_id=self.call_id,
                                    had_audio=True
                                )
                                try:
                                    await self.on_event({
                                        'type': 'HangupReady',
                                        'call_id': self.call_id,
                                        'reason': 'farewell_completed',
                                        'had_audio': True
                                    })
                                except Exception as e:
                                    logger.error("Failed to emit HangupReady event", call_id=self.call_id, error=str(e))
                                
                                # Reset hangup tracking
                                self._hangup_pending = False
                                self._farewell_message = None

                        if self.on_event:
                            await self.on_event(event_data)
                    except json.JSONDecodeError:
                        logger.error("Failed to parse JSON message from Deepgram", message=message)
                elif isinstance(message, bytes):
                    self._ready_to_stream = True
                    
                    # Track turn latency on first audio output (Milestone 21 - Call History)
                    if self._turn_start_time is not None and not self._turn_first_audio_received:
                        self._turn_first_audio_received = True
                        turn_latency_ms = (time.time() - self._turn_start_time) * 1000
                        # Save to session for call history
                        if self._session_store and self.call_id:
                            try:
                                call_id_copy = self.call_id
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
                                _t = asyncio.create_task(save_latency())
                                _t.add_done_callback(_log_provider_task_exception)
                            except Exception as e:
                                logger.debug("Failed to create latency save task", error=str(e))
                        logger.info("Turn latency recorded", call_id=self.call_id, latency_ms=round(turn_latency_ms, 1))
                        # Reset for next turn
                        self._turn_start_time = None
                    
                    # One-time runtime probe: infer output encoding/rate from first bytes
                    can_autodetect = getattr(self, "allow_output_autodetect", False)
                    try:
                        # Run a one-time inference on the first binary payload to guard against
                        # server-side defaults that differ from our declared output.
                        if not getattr(self, "_dg_output_inferred", False):
                            l = len(message)
                            inferred: Optional[str] = None
                            inferred_rate: Optional[int] = None
                            # Quick structural hints
                            if l % 2 == 1:
                                inferred = "mulaw"
                            else:
                                # Compare RMS treating payload as PCM16 vs μ-law→PCM16
                                try:
                                    rms_pcm = audioop.rms(message[: min(960, l - (l % 2))], 2) if l >= 2 else 0
                                except Exception:
                                    rms_pcm = 0
                                try:
                                    pcm_from_ulaw = mulaw_to_pcm16le(message[: min(320, l)])
                                    rms_ulaw = audioop.rms(pcm_from_ulaw, 2) if pcm_from_ulaw else 0
                                except Exception:
                                    rms_ulaw = 0
                                if rms_ulaw > max(50, int(1.5 * (rms_pcm or 1))):
                                    inferred = "mulaw"
                                else:
                                    inferred = "linear16"
                            # Heuristic rate inference for PCM16: check 20ms multiples
                            if inferred == "linear16":
                                # 20ms frame sizes for PCM16: 320@8k, 640@16k, 960@24k
                                if l % 960 == 0:
                                    inferred_rate = 24000
                                elif l % 640 == 0:
                                    inferred_rate = 16000
                                elif l % 320 == 0:
                                    inferred_rate = 8000
                            if inferred and inferred != self._dg_output_encoding:
                                try:
                                    logger.info(
                                        "Deepgram output encoding inferred from runtime payload",
                                        call_id=self.call_id,
                                        previous_encoding=self._dg_output_encoding,
                                        new_encoding=inferred,
                                        bytes=l,
                                        inferred_rate=inferred_rate,
                                    )
                                except Exception:
                                    pass
                                self._dg_output_encoding = inferred
                                if inferred_rate:
                                    self._dg_output_rate = inferred_rate
                                try:
                                    self._dg_output_inferred = True
                                except Exception:
                                    pass
                        else:
                            # Already inferred or no need; mark as completed to avoid repeating
                            if not getattr(self, "_dg_output_inferred", False):
                                self._dg_output_inferred = True
                    except Exception:
                        logger.debug("Deepgram output inference failed", exc_info=True)

                    # Provider-side normalization
                    try:
                        enc = (self._dg_output_encoding or "mulaw").strip().lower()
                        rate = int(self._dg_output_rate or 8000)
                    except Exception:
                        enc = "mulaw"
                        rate = 8000

                    if enc in {"linear16", "pcm16"}:
                        pcm = message
                        if len(pcm) % 2:
                            pcm = pcm[:-1]
                        try:
                            pcm = self._apply_dc_block(pcm)
                        except Exception:
                            pass
                        audio_event = {
                            'type': 'AgentAudio',
                            'data': pcm,
                            'streaming_chunk': True,
                            'call_id': self.call_id,
                            'encoding': 'linear16',
                            'sample_rate': rate,
                        }
                    else:
                        payload_ulaw = message
                        audio_event = {
                            'type': 'AgentAudio',
                            'data': payload_ulaw,
                            'streaming_chunk': True,
                            'call_id': self.call_id,
                            'encoding': 'mulaw',
                            'sample_rate': rate,
                        }
                    if not self._first_output_chunk_logged:
                        logger.info(
                            "Deepgram AgentAudio first chunk",
                            call_id=self.call_id,
                            bytes=len(audio_event['data']),
                            encoding=audio_event['encoding'],
                            sample_rate=audio_event['sample_rate'],
                        )
                        self._first_output_chunk_logged = True
                    self._in_audio_burst = True
                    if self.on_event:
                        await self.on_event(audio_event)
        except websockets.exceptions.ConnectionClosed as e:
            # Only warn once; avoid info duplicate from stop_session
            if not self._closed:
                logger.warning("Deepgram Voice Agent connection closed", reason=str(e))
        except Exception:
            logger.error("Error receiving events from Deepgram Voice Agent", exc_info=True)
        finally:
            # If socket ends mid-burst, close the burst cleanly
            if self._in_audio_burst and self.on_event:
                try:
                    await self.on_event({
                        'type': 'AgentAudioDone',
                        'streaming_done': True,
                        'call_id': self.call_id
                    })
                    
                    # Check if farewell audio completed after hangup request (socket closing)
                    if self._hangup_pending:
                        logger.info(
                            "🔚 Farewell audio completed (socket closing) - emitting HangupReady",
                            call_id=self.call_id,
                            had_audio=True
                        )
                        try:
                            await self.on_event({
                                'type': 'HangupReady',
                                'call_id': self.call_id,
                                'reason': 'farewell_completed',
                                'had_audio': True
                            })
                        except Exception as e:
                            logger.error("Failed to emit HangupReady event", call_id=self.call_id, error=str(e))
                        
                        # Reset hangup tracking
                        self._hangup_pending = False
                        self._farewell_message = None
                except Exception:
                    pass
            self._in_audio_burst = False

    async def speak(self, text: str):
        if not text or not self.websocket:
            return
        inject_message = {"type": "InjectAgentMessage", "content": text}
        try:
            await self.websocket.send(json.dumps(inject_message))
        except websockets.exceptions.ConnectionClosed as e:
            logger.error("Failed to send inject agent message: Connection is closed.", exc_info=True, code=e.code, reason=e.reason)

    async def _inject_message_dual(self, text: str):
        if not text or not self.websocket:
            return
        try:
            await self.websocket.send(json.dumps({"type": "InjectAgentMessage", "content": text}))
        except Exception:
            logger.debug("InjectAgentMessage failed", exc_info=True)
    
    def get_provider_info(self) -> Dict[str, Any]:
        """Get information about the provider and its capabilities."""
        return {
            "name": "DeepgramProvider",
            "type": "cloud",
            "supported_codecs": self.supported_codecs,
            "model": self.config.model,
            "tts_model": self.config.tts_model
        }
    
    def is_ready(self) -> bool:
        """Check if the provider is ready to process audio."""
        # Configuration readiness: we consider the provider ready when it's properly
        # configured and wired to emit events. A live websocket is only established
        # after start_session(call_id) during an actual call.
        try:
            api_key_ok = bool(self._get_config_value('api_key', None))
        except Exception:
            api_key_ok = False
        return api_key_ok and (self.on_event is not None)
