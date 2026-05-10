import asyncio
import base64
import json
import hashlib
import re
from uuid import uuid4
from typing import Callable, Optional, List, Dict, Any
import websockets
import websockets.exceptions
from websockets.asyncio.client import ClientConnection

from structlog import get_logger

import audioop
from ..config import LocalProviderConfig
from ..audio.resampler import resample_audio
from .base import AIProviderInterface, ProviderCapabilities, ProviderCapabilitiesMixin
from ..tools.parser import parse_response_with_tools, validate_tool_call, has_tool_intent_markers

logger = get_logger(__name__)

class LocalProvider(AIProviderInterface, ProviderCapabilitiesMixin):
    """
    AI Provider that connects to the external Local AI Server via WebSockets.
    """
    def __init__(self, config: LocalProviderConfig, on_event: Callable[[Dict[str, Any]], None]):
        super().__init__(on_event)
        self.config = config
        self.websocket: Optional[ClientConnection] = None
        # Use effective_ws_url which prefers base_url over ws_url
        self.ws_url = config.effective_ws_url
        self.auth_token: Optional[str] = getattr(config, "auth_token", None) or None
        self.connect_timeout = float(getattr(config, "connect_timeout_sec", 5.0) or 5.0)
        self.response_timeout = float(getattr(config, "response_timeout_sec", 5.0) or 5.0)
        self._batch_ms = max(5, int(getattr(config, "chunk_ms", 200) or 200))
        self._listener_task: Optional[asyncio.Task] = None
        self._sender_task: Optional[asyncio.Task] = None
        self._send_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._active_call_id: Optional[str] = None
        self.input_mode: str = 'mulaw8k'  # or 'pcm16_8k' or 'pcm16_16k'
        self._pending_tts_responses: Dict[str, asyncio.Future] = {}  # Track pending TTS responses
        self._tts_audio_meta_by_call: Dict[str, Dict[str, Any]] = {}
        self._agent_audio_done_tasks: Dict[str, asyncio.Task] = {}
        # Initial greeting text provided by engine/config (optional)
        self._initial_greeting: Optional[str] = None
        # Mode for local_ai_server: "full" or "stt" (for hybrid pipelines with cloud LLM)
        self._mode: str = getattr(config, 'mode', 'full') or 'full'
        # Track if server port is unavailable (not running at all)
        self._server_unavailable: bool = False
        self._resample_state_stt: Optional[tuple] = None
        # Parse host/port from ws_url for port checking
        self._server_host, self._server_port = self._parse_ws_url(self.ws_url)
        # Track if we were previously connected (for background reconnect on disconnect)
        self._was_connected: bool = False
        # Background reconnect task (runs when previously connected server disconnects)
        self._background_reconnect_task: Optional[asyncio.Task] = None
        # Single-flight guard for _reconnect() — prevents the audio-path
        # background reconnect from racing the _send_loop's direct on-close
        # _reconnect() call (both would otherwise overwrite self.websocket /
        # listener / sender tasks).
        self._reconnect_lock: asyncio.Lock = asyncio.Lock()
        self._warned_audio_drop_disconnected: bool = False
        # Runtime backend reported by local_ai_server in stt_result payloads.
        self._runtime_stt_backend: Optional[str] = None
        # Runtime status snapshot (from local_ai_server status_response)
        self._last_status: Optional[Dict[str, Any]] = None
        self._pending_status_future: Optional[asyncio.Future] = None
        self._status_lock: asyncio.Lock = asyncio.Lock()
        # Track last applied system prompt to avoid spamming switch_model.
        self._last_system_prompt_digest: Optional[str] = None
        # Per-call tool allowlist (from context.tools). Used to drop hallucinated tool calls.
        self._allowed_tools: set[str] = set()
        self._allowed_tool_schemas: List[Dict[str, Any]] = []
        self._last_user_transcript_by_call: Dict[str, str] = {}
        # Request-id keyed futures for internal LLM repair turns.
        self._pending_llm_responses: Dict[str, asyncio.Future] = {}
        self._pending_llm_tool_responses: Dict[str, Dict[str, Any]] = {}
        self._llm_tool_timeout_tasks: Dict[str, asyncio.Task] = {}
        self._pending_barge_in_acks: Dict[str, Dict[str, Any]] = {}
        self._barge_in_ack_tasks: Dict[str, asyncio.Task] = {}
        # LLM tool-calling capability metadata from local_ai_server status.
        self._tool_capability: Dict[str, Any] = {"level": "unknown", "source": "init"}
        # Effective per-call policy: strict | compatible | off
        self._effective_tool_policy: str = "compatible"
        # Feature flag: structured tool gateway for full-local provider only.
        self._tool_gateway_enabled: bool = bool(getattr(config, "tool_gateway_enabled", True))

    def _parse_ws_url(self, ws_url: str) -> tuple:
        """Parse host and port from WebSocket URL."""
        try:
            # ws://127.0.0.1:8765 or ws://127.0.0.1:8765/ws
            url = ws_url.replace('ws://', '').replace('wss://', '')
            host_port = url.split('/')[0]
            if ':' in host_port:
                host, port = host_port.split(':')
                return host, int(port)
            return host_port, 8765  # default port
        except Exception:
            return '127.0.0.1', 8765

    async def _is_port_open(self, timeout: float = 0.5) -> bool:
        """Quick TCP check if local AI server port is listening.
        
        Returns True if port is open (server container is running).
        Returns False if port is closed (server not running/not set up).
        """
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self._server_host, self._server_port),
                timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            return False
        except Exception:
            return False

    def set_initial_greeting(self, text: Optional[str]) -> None:
        try:
            value = (text or "").strip()
        except Exception:
            value = ""
        self._initial_greeting = value or None

    async def notify_barge_in(self, call_id: Optional[str]) -> None:
        """Notify local_ai_server that engine barge-in occurred for this call.

        This allows the server to clear Whisper-family STT suppression timers
        immediately after playback interruption so the caller does not need to
        repeat the first utterance after barge-in.
        """
        if not self.websocket or self.websocket.state.name != "OPEN":
            return
        target_call_id = str(call_id or self._active_call_id or "").strip()
        if not target_call_id:
            return
        request_id = f"barge-{uuid4().hex}"
        loop = asyncio.get_running_loop()
        ack_future: asyncio.Future = loop.create_future()
        self._pending_barge_in_acks[request_id] = {
            "future": ack_future,
            "call_id": target_call_id,
        }
        try:
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "barge_in",
                        "call_id": target_call_id,
                        "request_id": request_id,
                    }
                )
            )
            timeout_sec = min(max(float(self.response_timeout or 0.0), 0.3), 1.5)
            self._barge_in_ack_tasks[request_id] = asyncio.create_task(
                self._await_barge_in_ack(
                    request_id=request_id,
                    call_id=target_call_id,
                    timeout_sec=timeout_sec,
                )
            )
            logger.debug(
                "Sent barge_in notification to Local AI Server",
                call_id=target_call_id,
                request_id=request_id,
            )
        except Exception:
            self._pending_barge_in_acks.pop(request_id, None)
            task = self._barge_in_ack_tasks.pop(request_id, None)
            if task and not task.done():
                task.cancel()
            logger.debug("Failed to send barge_in notification to Local AI Server", call_id=target_call_id, exc_info=True)

    async def _await_barge_in_ack(self, *, request_id: str, call_id: str, timeout_sec: float) -> None:
        pending = self._pending_barge_in_acks.get(request_id)
        if not pending:
            self._barge_in_ack_tasks.pop(request_id, None)
            return
        future = pending.get("future")
        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=timeout_sec)
            logger.debug(
                "Received barge_in_ack from Local AI Server",
                call_id=call_id,
                request_id=request_id,
            )
        except asyncio.TimeoutError:
            pending = self._pending_barge_in_acks.pop(request_id, None)
            if pending:
                fut = pending.get("future")
                if fut and not fut.done():
                    fut.cancel()
            logger.warning(
                "Timed out waiting for barge_in_ack from Local AI Server",
                call_id=call_id,
                request_id=request_id,
                timeout_sec=timeout_sec,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                "Failed while waiting for barge_in_ack from Local AI Server",
                call_id=call_id,
                request_id=request_id,
                exc_info=True,
            )
        finally:
            self._barge_in_ack_tasks.pop(request_id, None)

    def _resolve_tool_policy(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Resolve local tool-call policy.

        auto (default) is derived from runtime capability probe:
        - strict  -> strict
        - partial -> compatible
        - none    -> off
        """
        policy = "auto"
        try:
            cfg_policy = str(getattr(self.config, "tool_call_policy", "auto") or "auto").strip().lower()
            if cfg_policy:
                policy = cfg_policy
        except Exception:
            policy = "auto"

        try:
            if isinstance(context, dict):
                override = context.get("local_tool_call_policy")
                if override:
                    policy = str(override).strip().lower()
        except Exception:
            pass

        if policy in {"strict", "compatible", "off"}:
            return policy

        level = str((self._tool_capability or {}).get("level") or "").strip().lower()
        if level == "strict":
            return "strict"
        if level == "none":
            return "off"
        return "compatible"

    async def _request_llm_text(self, *, text: str, call_id: Optional[str], timeout_sec: float = 2.0) -> Optional[str]:
        if not self.websocket or self.websocket.state.name != "OPEN":
            return None
        request_id = f"tool-repair-{uuid4().hex}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_llm_responses[request_id] = fut
        payload = {
            "type": "llm_request",
            "mode": "llm",
            "request_id": request_id,
            "call_id": call_id or self._active_call_id,
            "text": text,
        }
        try:
            await self.websocket.send(json.dumps(payload))
            result = await asyncio.wait_for(fut, timeout=max(0.5, float(timeout_sec)))
            return str(result or "")
        except Exception:
            logger.debug("LLM repair request failed", call_id=call_id, exc_info=True)
            return None
        finally:
            self._pending_llm_responses.pop(request_id, None)

    @staticmethod
    def _build_allowed_tool_schemas(tool_names: List[str]) -> List[Dict[str, Any]]:
        names = [str(name or "").strip() for name in (tool_names or []) if str(name or "").strip()]
        if not names:
            return []
        try:
            from src.tools.registry import tool_registry
            schemas = tool_registry.to_openai_realtime_schema_filtered(names)
            result: List[Dict[str, Any]] = []
            for schema in schemas:
                if not isinstance(schema, dict):
                    continue
                name = str(schema.get("name") or "").strip()
                if not name:
                    continue
                result.append(
                    {
                        "name": name,
                        "description": str(schema.get("description") or "").strip(),
                        "parameters": schema.get("parameters") if isinstance(schema.get("parameters"), dict) else {},
                    }
                )
            return result
        except Exception:
            logger.debug("Failed building local tool schemas", exc_info=True)
            return []

    async def _attempt_tool_call_repair(
        self,
        *,
        llm_text: str,
        call_id: Optional[str],
        allowed_tools: List[str],
    ) -> Optional[List[Dict[str, Any]]]:
        if not llm_text or not allowed_tools:
            return None

        tools_csv = ", ".join(sorted(set(allowed_tools)))
        repair_prompt = (
            "You are a strict parser. Convert the candidate assistant text into ONE tool call.\n"
            f"Allowed tool names: {tools_csv}\n"
            "Return EXACTLY one of:\n"
            "1) <tool_call>{\"name\":\"tool_name\",\"arguments\":{}}</tool_call>\n"
            "2) NONE\n"
            "No prose. No markdown. No extra text.\n"
            f"Candidate text:\n{llm_text}"
        )
        repaired_text = await self._request_llm_text(
            text=repair_prompt,
            call_id=call_id,
            timeout_sec=min(2.5, self.response_timeout),
        )
        if not repaired_text:
            return None

        _, repaired_calls = parse_response_with_tools(repaired_text)
        if not repaired_calls:
            # Some models may return raw JSON object without wrapper; best-effort parse.
            candidate = repaired_text.strip()
            if candidate.startswith("{") and candidate.endswith("}"):
                try:
                    data = json.loads(candidate)
                    repaired_calls = [{
                        "name": data.get("name"),
                        "parameters": data.get("arguments", data.get("parameters", {})),
                    }]
                except Exception:
                    repaired_calls = None
        if not repaired_calls:
            return None

        filtered: List[Dict[str, Any]] = []
        for tc in repaired_calls:
            if validate_tool_call(tc, allowed_tools):
                filtered.append(tc)
        if not filtered:
            return None

        logger.info(
            "Recovered malformed local LLM tool call via repair turn",
            call_id=call_id,
            tools=[tc.get("name") for tc in filtered],
        )
        return filtered

    def _is_structured_tool_gateway_active(self) -> bool:
        return (
            bool(self._tool_gateway_enabled)
            and str(self._mode or "").strip().lower() == "full"
            and self._effective_tool_policy != "off"
            and bool(self._allowed_tools)
        )

    def _cancel_gateway_timeout(self, request_id: str) -> None:
        task = self._llm_tool_timeout_tasks.pop(request_id, None)
        if task and not task.done():
            task.cancel()

    @staticmethod
    def _looks_like_transfer_intent(text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        direct_phrases = (
            "transfer me",
            "connect me",
            "route me",
            "put me through",
            "send me to",
            "move me to",
            "live agent",
            "human agent",
            "talk to agent",
            "speak to agent",
            "representative",
            "operator",
            "attended transfer",
            "blind transfer",
        )
        if any(phrase in normalized for phrase in direct_phrases):
            return True
        return bool(
            re.search(
                r"\b(?:transfer|connect|route|send|move|speak|talk)\b.{0,24}\b(?:agent|human|operator|representative|support|sales|billing|ext|extension|\d{3,6})\b",
                normalized,
            )
        )

    @staticmethod
    def _sanitize_local_tool_chatter(text: str) -> str:
        clean = str(text or "")
        if not clean:
            return ""
        clean = re.sub(r"<\|\s*(?:system|assistant|user|enduser|end)\s*\|>", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"<\|[^>\n\r]*\|?>?", "", clean)
        # Some local models will "explain" tool usage in plain language (instead of emitting a tool call).
        # Strip these phrases so they are never spoken to the caller.
        clean = re.sub(
            r"\bhangup_call\b\s*tool\s*is\s*used[^.?!]{0,120}[.?!]?",
            "",
            clean,
            flags=re.IGNORECASE,
        )
        clean = re.sub(
            r"\buse\s+the\s+hangup_call\s+tool\b[^.?!]{0,120}[.?!]?",
            "",
            clean,
            flags=re.IGNORECASE,
        )
        clean = re.sub(
            r"\(?\s*hangup_call\s+tool\s+executed\s*\)?",
            "",
            clean,
            flags=re.IGNORECASE,
        )
        clean = re.sub(
            r"\(?\s*tool\s*call(?:s)?\s*(?:executed|successful|succeeded|completed?)\s*\)?",
            "",
            clean,
            flags=re.IGNORECASE,
        )
        clean = re.sub(
            r"\bhangup\s+call\s+(?:successful|succeeded|executed|complete(?:d)?|requested)\b[.!]?",
            "",
            clean,
            flags=re.IGNORECASE,
        )
        clean = re.sub(
            r"\bcall\s+duration\s*[:\-]?\s*[^.?!]{0,96}[.?!]?",
            "",
            clean,
            flags=re.IGNORECASE,
        )
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    @classmethod
    def _extract_hangup_farewell(cls, tool_calls: Optional[List[Dict[str, Any]]]) -> Optional[str]:
        for tool_call in tool_calls or []:
            name = str(tool_call.get("name") or "").strip()
            if name != "hangup_call":
                continue
            params = tool_call.get("parameters") or tool_call.get("arguments") or {}
            if not isinstance(params, dict):
                continue
            farewell = str(params.get("farewell_message") or "").strip()
            farewell = cls._sanitize_local_tool_chatter(farewell)
            if farewell:
                return farewell
        return None

    async def _emit_local_llm_result(
        self,
        *,
        call_id: Optional[str],
        llm_text: str,
        clean_text: Optional[str],
        tool_calls: Optional[List[Dict[str, Any]]],
        tool_path: str,
        parse_failures: int = 0,
        repair_attempts: int = 0,
    ) -> None:
        response_text = self._sanitize_local_tool_chatter(
            ((clean_text if clean_text is not None else llm_text) or "").strip()
        )
        allowed = sorted(self._allowed_tools)
        normalized_tool_calls: Optional[List[Dict[str, Any]]] = tool_calls
        if normalized_tool_calls and self._allowed_tools:
            filtered: List[Dict[str, Any]] = []
            for tc in normalized_tool_calls:
                if validate_tool_call(tc, allowed):
                    filtered.append(tc)
            if filtered:
                normalized_tool_calls = filtered
            else:
                logger.info(
                    "Dropping tool calls from local LLM (not allowlisted)",
                    call_id=call_id,
                    tools=[tc.get("name") for tc in normalized_tool_calls],
                )
                normalized_tool_calls = None

        if normalized_tool_calls and call_id:
            user_text = self._last_user_transcript_by_call.get(call_id, "")
            transfer_tools = {
                "live_agent_transfer",
                "blind_transfer",
                "attended_transfer",
                "request_transfer",
                "transfer",
            }
            if not self._looks_like_transfer_intent(user_text):
                kept_tool_calls = []
                dropped_transfer_tools = []
                for tool_call in normalized_tool_calls:
                    tool_name = str(tool_call.get("name") or "").strip()
                    if tool_name in transfer_tools:
                        dropped_transfer_tools.append(tool_name)
                    else:
                        kept_tool_calls.append(tool_call)
                if dropped_transfer_tools:
                    logger.info(
                        "Dropping transfer-like tool call from local LLM (no transfer intent detected)",
                        call_id=call_id,
                        tools=dropped_transfer_tools,
                        user_preview=(user_text or "")[:80],
                    )
                normalized_tool_calls = kept_tool_calls or None

        hangup_farewell = self._extract_hangup_farewell(normalized_tool_calls)
        if hangup_farewell:
            response_text = hangup_farewell
        elif normalized_tool_calls and any(
            str(tool_call.get("name") or "").strip() == "hangup_call"
            for tool_call in normalized_tool_calls
        ):
            # If the model requested a hangup but did not provide a farewell_message, never risk
            # speaking tool-chatter. Prefer a short, safe farewell.
            safe = response_text or ""
            if re.search(r"\bhangup_call\b|\btool\b", safe, flags=re.IGNORECASE):
                safe = ""
            response_text = safe.strip() or "Goodbye."

        # Emit spoken text only when every remaining tool call is `hangup_call`.
        # The earlier `not any(... == "hangup_call")` guard left
        # `should_emit_text=True` for mixed batches like
        # `["hangup_call", "transfer"]`, so the provider would speak the
        # farewell *before* dispatching the transfer — wrong order, and the
        # caller hears "have a great day" right before being patched through.
        # Per CodeRabbit review of PR #384 comment 3214130574: suppress text
        # whenever any non-hangup tool remains in the batch.
        should_emit_text = bool(response_text)
        if normalized_tool_calls and any(
            str(tool_call.get("name") or "").strip() != "hangup_call"
            for tool_call in normalized_tool_calls
        ):
            should_emit_text = False

        if should_emit_text and self.on_event:
            await self.on_event(
                {
                    "type": "agent_transcript",
                    "call_id": call_id,
                    "text": response_text,
                }
            )
            logger.debug("Emitted agent transcript for history", call_id=call_id, text=response_text[:50])

        logger.info(
            "Local tool gateway result",
            call_id=call_id,
            tool_path=tool_path,
            parse_failures=parse_failures,
            repair_attempts=repair_attempts,
            tool_count=len(normalized_tool_calls or []),
        )

        if normalized_tool_calls:
            logger.info(
                "🔧 Tool calls detected in local LLM response",
                call_id=call_id,
                tools=[tc.get("name") for tc in normalized_tool_calls]
            )
            if self.on_event:
                await self.on_event({
                    "type": "ToolCall",
                    "call_id": call_id,
                    "tool_calls": normalized_tool_calls,
                    "text": response_text,
                })
        else:
            logger.debug(
                "LLM response received (no tools)",
                call_id=call_id,
                preview=llm_text[:80] if llm_text else "(empty)",
                tool_path=tool_path,
            )

    _END_CALL_MARKERS = (
        "no transcript", "no transcript needed", "don't send a transcript",
        "no thanks", "no thank you", "thank you", "thanks",
        "that's all", "nothing else", "end call", "hang up",
        "goodbye", "bye", "have a good day", "have a great day",
        "take care", "talk to you later",
    )

    def _user_has_end_call_intent(self, call_id: Optional[str]) -> bool:
        """Check if the last user transcript signals end-of-call intent."""
        user_text = (self._last_user_transcript_by_call.get(call_id or "", "") or "").strip().lower()
        if not user_text:
            return False
        for marker in self._END_CALL_MARKERS:
            m = marker.lower()
            if " " in m:
                if m in user_text:
                    return True
            else:
                if re.search(rf"(?:^|\b){re.escape(m)}(?:\b|$)", user_text):
                    return True
        return False

    async def _process_llm_text_fallback(
        self,
        *,
        llm_text: str,
        call_id: Optional[str],
        tool_path: str = "parser",
    ) -> None:
        clean_text, tool_calls = parse_response_with_tools(llm_text)
        parse_failures = 0
        repair_attempts = 0
        allowed = sorted(self._allowed_tools)
        if self._effective_tool_policy == "off":
            # Policy=off disables structured tool dispatch, but hangup_call is a
            # heuristic-only tool that doesn't require LLM tool-calling capability.
            # Check if the user expressed end-of-call intent and emit hangup_call.
            tool_calls = None
            if "hangup_call" in self._allowed_tools and self._user_has_end_call_intent(call_id):
                tool_calls = [{"name": "hangup_call", "parameters": {"farewell_message": (clean_text or llm_text or "Goodbye.").strip() or "Goodbye."}}]
                tool_path = "heuristic"
                logger.info("hangup_call heuristic triggered (policy=off)", call_id=call_id)
        elif not tool_calls and allowed and has_tool_intent_markers(llm_text, allowed):
            repair_attempts = 1
            tool_path = "repair"
            tool_calls = await self._attempt_tool_call_repair(
                llm_text=llm_text,
                call_id=call_id,
                allowed_tools=allowed,
            )
        await self._emit_local_llm_result(
            call_id=call_id,
            llm_text=llm_text,
            clean_text=clean_text,
            tool_calls=tool_calls,
            tool_path=tool_path,
            parse_failures=parse_failures,
            repair_attempts=repair_attempts,
        )

    async def _dispatch_llm_tool_gateway_request(
        self,
        *,
        llm_text: str,
        call_id: Optional[str],
    ) -> bool:
        if not self.websocket or self.websocket.state.name != "OPEN":
            return False

        request_id = f"tool-gateway-{uuid4().hex}"
        payload = {
            "type": "llm_tool_request",
            "mode": "llm",
            "protocol_version": 2,
            "request_id": request_id,
            "call_id": call_id or self._active_call_id,
            "text": llm_text,
            "latest_user_text": self._last_user_transcript_by_call.get(call_id or self._active_call_id or "", ""),
            "allowed_tools": sorted(self._allowed_tools),
            "tools": list(self._allowed_tool_schemas or []),
            "tool_policy": self._effective_tool_policy,
            "tool_choice": "auto",
        }
        self._pending_llm_tool_responses[request_id] = {
            "call_id": call_id or self._active_call_id,
            "llm_text": llm_text,
        }
        try:
            await self.websocket.send(json.dumps(payload))
        except Exception:
            self._pending_llm_tool_responses.pop(request_id, None)
            logger.debug("Failed to dispatch llm_tool_request", call_id=call_id, exc_info=True)
            return False

        async def _timeout_fallback() -> None:
            try:
                await asyncio.sleep(max(1.0, min(3.0, self.response_timeout)))
                pending = self._pending_llm_tool_responses.pop(request_id, None)
                if not pending:
                    return
                logger.warning(
                    "llm_tool_request timed out; falling back to parser path",
                    call_id=pending.get("call_id"),
                    request_id=request_id,
                )
                await self._process_llm_text_fallback(
                    llm_text=str(pending.get("llm_text") or ""),
                    call_id=pending.get("call_id"),
                    tool_path="parser",
                )
            except asyncio.CancelledError:
                return
            except Exception:
                logger.error("llm_tool_request timeout fallback failed", call_id=call_id, exc_info=True)
            finally:
                self._llm_tool_timeout_tasks.pop(request_id, None)

        self._llm_tool_timeout_tasks[request_id] = asyncio.create_task(_timeout_fallback())
        return True

    async def _handle_llm_tool_response(self, data: Dict[str, Any]) -> bool:
        request_id = str(data.get("request_id") or "").strip()
        pending = self._pending_llm_tool_responses.pop(request_id, None)
        if not pending and request_id:
            logger.debug("Dropping stale llm_tool_response", request_id=request_id)
            return False
        if request_id:
            self._cancel_gateway_timeout(request_id)

        # After a successful `request_id` correlation, the local pending
        # entry is the authoritative source for `call_id`. Preferring
        # `data["call_id"]` first allowed a stale or echoed `call_id` from
        # the server side to reroute the result to the wrong call. Use the
        # pending entry's call_id first, then fall back to `data` (for
        # responses that arrive without a known pending entry, e.g. server-
        # initiated nudges), then `self._active_call_id` as last resort.
        # Per CodeRabbit review of PR #384 comment 3214130576.
        call_id = (pending or {}).get("call_id") or data.get("call_id") or self._active_call_id
        llm_text = str((pending or {}).get("llm_text") or data.get("text") or "")
        clean_text = str(data.get("text") or "").strip()
        if not clean_text:
            parsed_clean, _ = parse_response_with_tools(llm_text)
            clean_text = (parsed_clean or llm_text or "").strip()
        raw_tool_calls = data.get("tool_calls")
        tool_calls = raw_tool_calls if isinstance(raw_tool_calls, list) else None
        tool_path = str(data.get("tool_path") or "structured").strip().lower() or "structured"
        parse_failures = int(data.get("tool_parse_failures") or 0)
        repair_attempts = int(data.get("repair_attempts") or 0)

        await self._emit_local_llm_result(
            call_id=call_id,
            llm_text=llm_text,
            clean_text=clean_text,
            tool_calls=tool_calls,
            tool_path=tool_path,
            parse_failures=parse_failures,
            repair_attempts=repair_attempts,
        )
        return True

    async def send_tool_result(
        self,
        function_call_id: str,
        result: Any,
        is_error: bool = False,
        call_id: Optional[str] = None,
    ) -> bool:
        """Send an executed local tool result back to local_ai_server for the final LLM turn.

        Returns ``True`` if the payload was handed off to the WebSocket layer,
        ``False`` if the connection was unavailable or the send raised. The
        engine uses this signal to decide whether to retry/fail-over rather
        than silently stalling the post-tool turn. Per CodeRabbit review of
        PR #384 comment 3214158829.

        ``result`` may be any JSON value (object, list, string, int, ``False``,
        ``0``, empty list, ``None``). Pre-fix this method coerced falsy values
        to ``{}`` via ``result or {}``, which silently changed valid outputs
        like ``0``, ``False``, ``""``, ``[]``, or ``None`` into an empty
        object — and the local LLM then composed its follow-up using a
        misleading payload. Per CodeRabbit review of PR #384 comment
        3214117421.

        ``call_id`` is the originating call_id captured at tool dispatch
        time. Pass it explicitly so the tool result is correlated to the
        right session even when ``self._active_call_id`` has rolled over to
        a newer call by the time a slow tool returns. Pre-fix this method
        read ``self._active_call_id`` at result-send time, which could
        misroute the post-tool answer across calls. Falls back to
        ``self._active_call_id`` only if no explicit call_id is supplied
        (back-compat for callers that haven't been updated yet). Per
        CodeRabbit review of PR #384 comment 3214139216.
        """
        if not self.websocket or self.websocket.state.name != "OPEN":
            logger.warning(
                "Cannot send local tool result: WebSocket not open",
                call_id=call_id or self._active_call_id,
                function_call_id=function_call_id,
            )
            return False
        function_call_id = str(function_call_id or "").strip()
        tool_name = function_call_id
        if tool_name.startswith("local-"):
            tool_name = tool_name[len("local-"):]
        # Originating call_id wins; provider-global fallback only as last resort.
        effective_call_id = call_id or self._active_call_id
        payload = {
            "type": "tool_result",
            "protocol_version": 2,
            "call_id": effective_call_id,
            "function_call_id": function_call_id,
            "tool_name": tool_name,
            "result": result,  # preserve falsy values; do NOT coerce to {}
            "is_error": bool(is_error),
            "tool_policy": self._effective_tool_policy,
        }
        try:
            await self.websocket.send(json.dumps(payload, default=str))
            logger.debug(
                "Sent local tool result to Local AI Server",
                call_id=effective_call_id,
                function_call_id=function_call_id,
                tool_name=tool_name,
                is_error=bool(is_error),
            )
            return True
        except Exception:
            logger.error(
                "Failed to send local tool result to Local AI Server",
                call_id=effective_call_id,
                function_call_id=function_call_id,
                exc_info=True,
            )
            return False

    @property
    def supported_codecs(self) -> List[str]:
        return ["ulaw"]

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            input_encodings=["pcm16"],
            input_sample_rates_hz=[16000],
            output_encodings=["ulaw"],
            output_sample_rates_hz=[8000],
            is_full_agent=True,
            requires_continuous_audio=True,
        )

    def is_ready(self) -> bool:
        """
        Readiness for the *provider config* (mirrors other providers):
        - True when the WebSocket URL is configured.
        - Connection establishment happens on-demand when the provider is used.

        Note: connection state is exposed via `is_connected()`.
        """
        ws_url = getattr(self, "ws_url", None) or ""
        return bool(str(ws_url).strip())

    def is_connected(self) -> bool:
        """Return True only when the provider has an active WS connection."""
        return bool(self.websocket is not None and self.websocket.state.name == "OPEN")

    async def _request_status(self, *, timeout_sec: float = 2.0) -> Optional[Dict[str, Any]]:
        """Fetch current runtime status from local_ai_server over WS.

        This is used to learn runtime-selected STT/TTS/LLM configuration (which can be
        switched via Admin UI) without requiring an ai-engine restart.
        """
        if not self.websocket or self.websocket.state.name != "OPEN":
            return None
        async with self._status_lock:
            if self._pending_status_future and not self._pending_status_future.done():
                try:
                    return await asyncio.wait_for(self._pending_status_future, timeout=timeout_sec)
                except Exception:
                    return None

            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending_status_future = fut
            try:
                await self.websocket.send(json.dumps({"type": "status"}))
                data = await asyncio.wait_for(fut, timeout=timeout_sec)
                if isinstance(data, dict):
                    return data
                return None
            except Exception:
                return None
            finally:
                if self._pending_status_future is fut:
                    self._pending_status_future = None

    async def _connect_ws(self):
        # Use conservative client settings; server will drive pings if needed
        return await asyncio.wait_for(
            websockets.connect(
                self.ws_url,
                ping_interval=None,         # disable client pings to avoid false timeouts
                ping_timeout=None,
                close_timeout=10,
                max_size=None
            ),
            timeout=self.connect_timeout,
        )

    async def _authenticate(self) -> None:
        """Authenticate with local-ai-server if auth_token is configured."""
        if not self.auth_token or not self.websocket or self.websocket.state.name != "OPEN":
            return
        await self.websocket.send(
            json.dumps({"type": "auth", "auth_token": self.auth_token})
        )
        try:
            raw = await asyncio.wait_for(
                self.websocket.recv(), timeout=self.connect_timeout
            )
            if isinstance(raw, (bytes, bytearray)):
                raise RuntimeError("Unexpected binary auth response")
            data = json.loads(raw)
        except Exception as exc:
            raise RuntimeError(f"Auth handshake failed: {exc}") from exc

        if data.get("type") != "auth_response" or data.get("status") != "ok":
            raise RuntimeError(f"Auth rejected: {data}")

    async def _reconnect(self):
        # Single-flight: serialize concurrent reconnect attempts so the
        # background task and _send_loop's direct on-close call don't race
        # on self.websocket / listener / sender lifecycle.
        async with self._reconnect_lock:
            return await self._reconnect_locked()

    async def _reconnect_locked(self):
        # If another reconnect already brought us back online, skip.
        if self.is_connected():
            return True
        # HYBRID APPROACH: Quick port check first
        # If port is closed, server is not running at all - skip immediately
        # If port is open, server is starting/running - use retry logic

        port_open = await self._is_port_open(timeout=0.5)
        
        if not port_open:
            # Port closed = server container not running = not set up
            logger.info(
                "⏭️ Local AI Server port not open - skipping connection",
                host=self._server_host,
                port=self._server_port,
                note="Start local-ai-server container if you want to use local STT/TTS/LLM"
            )
            self._server_unavailable = True
            return False
        
        # Port is open - server is running, proceed with retry logic for warmup
        logger.info(
            "🔄 Local AI Server port is open, connecting...",
            host=self._server_host,
            port=self._server_port
        )
        self._server_unavailable = False
        
        # Exponential backoff up to 30s, total ~3 minutes to cover LLM warmup (~111s)
        backoff_schedule = [2, 5, 10, 20, 30, 30, 30, 30]  # Total: ~157s
        total_elapsed = 0
        
        for attempt, delay in enumerate(backoff_schedule, 1):
            try:
                if attempt == 1:
                    logger.info(
                        "🔄 Connecting to Local AI Server...",
                        url=self.ws_url,
                        note="Server may be warming up models (~2 minutes)"
                    )
                else:
                    logger.info(
                        f"🔄 Reconnect attempt {attempt}/{len(backoff_schedule)}",
                        url=self.ws_url,
                        next_retry=f"{delay}s",
                        elapsed=f"{total_elapsed}s"
                    )
                
                self.websocket = await self._connect_ws()
                self._was_connected = True  # Mark that we successfully connected
                logger.info("✅ Connected to Local AI Server", elapsed=f"{total_elapsed}s")

                # Authenticate before starting receive/send loops if required.
                if self.auth_token:
                    await self._authenticate()
                    logger.info("🔐 Authenticated to Local AI Server", url=self.ws_url)
                
                # Cancel old tasks and restart listener/sender loops on new connection
                if self._listener_task and not self._listener_task.done():
                    self._listener_task.cancel()
                    logger.debug("Cancelled old listener task before restart")
                if self._sender_task and not self._sender_task.done():
                    self._sender_task.cancel()
                    logger.debug("Cancelled old sender task before restart")
                
                self._listener_task = asyncio.create_task(self._receive_loop())
                self._sender_task = asyncio.create_task(self._send_loop())
                logger.info("✅ Reconnected to Local AI Server, restarting receive loop")
                return True
                
            except (ConnectionRefusedError, OSError) as e:
                # Port was open but connection failed - server might be restarting
                # Re-check if port is still open before retrying
                if not await self._is_port_open(timeout=0.3):
                    logger.info(
                        "⏭️ Local AI Server port closed during retry - stopping",
                        note="Server may have stopped"
                    )
                    self._server_unavailable = True
                    return False
                
                # Port still open, continue retrying
                if attempt < len(backoff_schedule):
                    logger.debug(
                        f"Connection attempt {attempt} failed (likely warmup)",
                        error=type(e).__name__,
                        next_retry=f"{delay}s"
                    )
                else:
                    logger.warning(
                        "Connection failed after all retries",
                        attempts=len(backoff_schedule),
                        total_elapsed=f"{total_elapsed}s",
                        error=str(e)
                    )
            except Exception as e:
                logger.warning(
                    f"Reconnect attempt {attempt} failed",
                    error=f"{type(e).__name__}: {str(e)}",
                    next_retry=f"{delay}s" if attempt < len(backoff_schedule) else "none"
                )
            
            if attempt < len(backoff_schedule):
                await asyncio.sleep(delay)
                total_elapsed += delay
                
        return False

    async def _background_reconnect_loop(self):
        """Background task that periodically tries to reconnect for up to 12 minutes.
        
        Only runs when we were previously connected and got disconnected (e.g., server restart).
        Does not block anything - runs independently in the background.
        """
        max_duration = 12 * 60  # 12 minutes
        check_interval = 30  # Check every 30 seconds
        start_time = asyncio.get_event_loop().time()
        
        logger.info(
            "🔄 Starting background reconnect (server was previously connected)",
            max_duration="12 minutes",
            check_interval="30s"
        )
        
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= max_duration:
                logger.warning(
                    "⏹️ Background reconnect timed out after 12 minutes",
                    note="Local AI Server did not come back online"
                )
                break
            
            # Wait before checking
            await asyncio.sleep(check_interval)
            
            # Check if port is open
            if await self._is_port_open(timeout=1.0):
                logger.info("🔄 Local AI Server port detected, attempting reconnect...")
                success = await self._reconnect()
                if success:
                    logger.info("✅ Background reconnect successful")
                    self._was_connected = True
                    # Restart listener task
                    if not self._listener_task or self._listener_task.done():
                        self._listener_task = asyncio.create_task(self._receive_loop())
                    break
                else:
                    logger.debug("Reconnect attempt failed, will retry...")
            else:
                remaining = int(max_duration - elapsed)
                logger.debug(
                    f"Local AI Server port still closed, will check again in {check_interval}s",
                    remaining=f"{remaining}s"
                )
        
        self._background_reconnect_task = None

    def _start_background_reconnect(self):
        """Start background reconnect task if not already running."""
        if self._background_reconnect_task and not self._background_reconnect_task.done():
            logger.debug("Background reconnect task already running")
            return
        
        self._background_reconnect_task = asyncio.create_task(self._background_reconnect_loop())

    async def initialize(self):
        """Initialize persistent connection to Local AI Server.
        
        If the server port is not open (server not running), this will
        mark the provider as unavailable and return gracefully without error.
        """
        try:
            if self.websocket and self.websocket.state.name == "OPEN":
                logger.debug("WebSocket already connected, skipping initialization")
                return
            
            logger.info("Initializing connection to Local AI Server...", url=self.ws_url)
            # Use _reconnect method which has port check + retry logic
            success = await self._reconnect()
            if not success:
                if self._server_unavailable:
                    # Port was not open - server not set up, this is OK
                    logger.info(
                        "Local AI Server not available - provider will be inactive",
                        note="This is normal if you haven't set up local-ai-server"
                    )
                    return  # Don't raise, just mark as unavailable
                else:
                    # Port was open but connection failed after retries
                    raise RuntimeError("Failed to connect to Local AI Server after retries")
            logger.info("✅ Successfully connected to Local AI Server.")
        except RuntimeError:
            raise
        except Exception:
            logger.error("Failed to initialize connection to Local AI Server", exc_info=True)
            raise

    async def start_session(self, call_id: str, context: Optional[Dict[str, Any]] = None):
        try:
            # Check if already connected
            if self.is_connected():
                logger.debug("WebSocket already connected, reusing connection", call_id=call_id)
                if self._active_call_id and self._active_call_id != call_id:
                    self._tts_audio_meta_by_call.pop(self._active_call_id, None)
                    self._last_user_transcript_by_call.pop(self._active_call_id, None)
                self._active_call_id = call_id
                self._runtime_stt_backend = None
                self._last_status = None
                # Ensure listener and sender tasks are running (may have crashed)
                if self._listener_task is None or self._listener_task.done():
                    logger.info("Restarting listener task for reused connection", call_id=call_id)
                    self._listener_task = asyncio.create_task(self._receive_loop())
                if self._sender_task is None or self._sender_task.done():
                    logger.info("Restarting sender task for reused connection", call_id=call_id)
                    self._sender_task = asyncio.create_task(self._send_loop())
                # Best-effort: refresh runtime status so engine gating logic is correct early.
                await self._prime_runtime_status_and_context(context=context, call_id=call_id)
                return
            
            # If not connected, initialize first
            await self.initialize()
            if not self.is_connected():
                raise RuntimeError("Local AI Server WebSocket is not connected after initialization")
            if self._active_call_id and self._active_call_id != call_id:
                self._tts_audio_meta_by_call.pop(self._active_call_id, None)
                self._last_user_transcript_by_call.pop(self._active_call_id, None)
            self._active_call_id = call_id
            self._runtime_stt_backend = None
            self._last_status = None
            # Best-effort: refresh runtime status so engine gating logic is correct early.
            await self._prime_runtime_status_and_context(context=context, call_id=call_id)
        except Exception:
            logger.error("Failed to start session", call_id=call_id, exc_info=True)
            raise

    async def _prime_runtime_status_and_context(self, *, context: Optional[Dict[str, Any]], call_id: str) -> None:
        """Sync runtime model state and per-call prompt to local_ai_server.

        - Status sync is required because Admin UI can switch models without restarting ai-engine.
        - Prompt sync is required because ai-engine contexts hold the system prompt, while local-ai-server
          owns the local LLM prompt used in full mode.
        """
        try:
            status = await self._request_status(timeout_sec=float(self.connect_timeout) or 2.0)
            if isinstance(status, dict):
                self._last_status = dict(status)
                backend = self._normalize_stt_backend(status.get("stt_backend"))
                if backend:
                    self._runtime_stt_backend = backend
                    logger.info("Local AI Server runtime STT backend detected", call_id=call_id, stt_backend=backend)
                try:
                    llm_status = ((status.get("models") or {}).get("llm") or {})
                    capability = llm_status.get("tool_capability")
                    if isinstance(capability, dict) and capability:
                        self._tool_capability = dict(capability)
                except Exception:
                    pass
        except Exception:
            logger.debug("Local AI Server status probe failed", call_id=call_id, exc_info=True)

        # Apply system prompt from context (if provided).
        prompt = ""
        allowed_tools: list[str] = []
        try:
            if isinstance(context, dict):
                prompt = str(context.get("prompt") or context.get("instructions") or "").strip()
                tools_raw = context.get("context_tools", context.get("tools"))
                if isinstance(tools_raw, (list, tuple, set)):
                    allowed_tools = [str(x).strip() for x in tools_raw if str(x).strip()]
        except Exception:
            prompt = ""
        try:
            self._allowed_tools = set(allowed_tools or [])
            self._allowed_tool_schemas = self._build_allowed_tool_schemas(allowed_tools)
        except Exception:
            self._allowed_tools = set()
            self._allowed_tool_schemas = []
        self._effective_tool_policy = self._resolve_tool_policy(context=context)
        if not prompt:
            try:
                prompt = str(getattr(self.config, "instructions", None) or "").strip()
            except Exception:
                prompt = ""
        if prompt:
            # Local tool-call guidance is policy-driven:
            # - strict: full schema/rules
            # - compatible: compact instructions (lower leakage risk on weaker models)
            # - off: no injection; rely on text-only interaction / heuristics
            try:
                use_gateway = self._is_structured_tool_gateway_active()
                if use_gateway and allowed_tools:
                    logger.info(
                        "Local tool-call prompt injection skipped (structured gateway active)",
                        call_id=call_id,
                        policy=self._effective_tool_policy,
                        allowed_tools=sorted(self._allowed_tools),
                    )
                elif allowed_tools and "## Available Tools" not in prompt and self._effective_tool_policy != "off":
                    from src.tools.registry import tool_registry

                    if self._effective_tool_policy == "strict":
                        tool_prompt = tool_registry.to_local_llm_prompt_filtered(allowed_tools)
                    else:
                        tool_prompt = tool_registry.to_local_llm_prompt_filtered_compact(allowed_tools)
                    if tool_prompt:
                        prompt = f"{prompt}\n\n{tool_prompt}".strip()
                elif allowed_tools and self._effective_tool_policy == "off":
                    logger.info(
                        "Local tool-call prompt injection skipped (policy=off)",
                        call_id=call_id,
                        capability_level=(self._tool_capability or {}).get("level"),
                    )
            except Exception:
                logger.debug("Failed injecting local tool prompt", call_id=call_id, exc_info=True)
            logger.info(
                "Local tool-call policy resolved",
                call_id=call_id,
                policy=self._effective_tool_policy,
                allowed_tools=sorted(self._allowed_tools),
                capability=(self._tool_capability or {}).get("level"),
            )
            # Fail-closed: same cross-call leakage class as tool_context. On
            # a reused WebSocket, a missed prompt sync leaves the previous
            # call's instructions live on the server. Per CodeRabbit review
            # of PR #384 comment 3214166440.
            prompt_ok = await self._apply_system_prompt(prompt, call_id=call_id)
            if not prompt_ok:
                raise RuntimeError(
                    f"Failed to synchronize system prompt with Local AI Server (call_id={call_id})"
                )
        # Fail-closed: tool_context state is per-WebSocket and we reuse the
        # connection across calls. If the sync fails, the server can keep the
        # previous call's allowlist/policy/schemas, leaking ACL state across
        # calls. Abort this call setup instead of proceeding with stale state.
        # Per CodeRabbit review of PR #384 review 4258719822 (outside-diff).
        ok = await self._send_tool_context(call_id=call_id)
        if not ok:
            raise RuntimeError(
                f"Failed to synchronize tool_context with Local AI Server (call_id={call_id})"
            )

    async def _apply_system_prompt(self, prompt: str, *, call_id: str) -> bool:
        """Send system prompt to local_ai_server. Returns True on success.

        Empty prompt or unchanged digest are treated as success (nothing to
        sync). WebSocket-not-open and send-exception return False so the
        caller can fail-closed and abort call setup instead of running with
        the previous call's instructions on a reused connection. Per
        CodeRabbit review of PR #384 comment 3214166440.
        """
        prompt = (prompt or "").strip()
        if not prompt:
            return True  # nothing to sync, not a failure
        if not self.websocket or self.websocket.state.name != "OPEN":
            logger.warning(
                "Cannot send local system prompt: WebSocket not open",
                call_id=call_id,
                chars=len(prompt),
            )
            return False
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if digest == self._last_system_prompt_digest:
            return True  # already in sync from a prior successful send
        payload = {
            "type": "switch_model",
            "dry_run": True,  # system prompt does not require reload_models()
            "llm_config": {
                "system_prompt": prompt,
            },
        }
        try:
            await self.websocket.send(json.dumps(payload))
            self._last_system_prompt_digest = digest
            logger.info("Applied Local AI Server system prompt (dry_run)", call_id=call_id, chars=len(prompt))
            return True
        except Exception:
            logger.error(
                "Failed applying Local AI Server system prompt",
                call_id=call_id,
                chars=len(prompt),
                exc_info=True,
            )
            return False

    async def _send_tool_context(self, *, call_id: str) -> bool:
        """Send tool_context to local_ai_server. Returns True on success.

        Caller must treat False as fatal: the server caches per-session
        ACL/policy/schemas and a missed sync leaks state across calls. Per
        CodeRabbit review of PR #384 review 4258719822 (outside-diff).
        """
        if not self.websocket or self.websocket.state.name != "OPEN":
            logger.warning(
                "Cannot send local tool_context: WebSocket not open",
                call_id=call_id,
                allowed_tools=sorted(self._allowed_tools),
                policy=self._effective_tool_policy,
            )
            return False
        payload = {
            "type": "tool_context",
            "protocol_version": 2,
            "call_id": call_id,
            "allowed_tools": sorted(self._allowed_tools),
            "tools": list(self._allowed_tool_schemas or []),
            "tool_policy": self._effective_tool_policy,
        }
        try:
            await self.websocket.send(json.dumps(payload, default=str))
            logger.debug(
                "Sent local tool context to Local AI Server",
                call_id=call_id,
                allowed_tools=sorted(self._allowed_tools),
                policy=self._effective_tool_policy,
            )
            return True
        except Exception:
            logger.error(
                "Failed sending local tool context",
                call_id=call_id,
                allowed_tools=sorted(self._allowed_tools),
                policy=self._effective_tool_policy,
                exc_info=True,
            )
            return False

    async def send_audio(self, audio_chunk: bytes, sample_rate: int = 0, encoding: str = ""):
        """Send audio chunk to Local AI Server for STT processing."""
        try:
            if not self.is_connected():
                if not self._warned_audio_drop_disconnected:
                    logger.warning(
                        "Dropping Local AI audio chunk because WebSocket is unavailable; background reconnect requested",
                        bytes=len(audio_chunk),
                        input_mode=self.input_mode,
                    )
                    self._warned_audio_drop_disconnected = True
                # Only kick a background reconnect if we were previously
                # connected; otherwise we'd spin the port-check at frame rate
                # for a server that was never reachable in this session.
                if self._was_connected:
                    self._start_background_reconnect()
                return

            self._warned_audio_drop_disconnected = False

            logger.info("🎵 PROVIDER INPUT - Sending to Local AI Server",
                         bytes=len(audio_chunk),
                         queue_size=self._send_queue.qsize(),
                         input_mode=self.input_mode)
            
            # Enqueue for sender loop; drop if queue is full to avoid backpressure explosions
            try:
                self._send_queue.put_nowait(audio_chunk)
            except asyncio.QueueFull:
                logger.warning(
                    "Local AI Server send queue full; dropping audio chunk",
                    bytes=len(audio_chunk),
                    queue_size=self._send_queue.qsize(),
                    input_mode=self.input_mode,
                )
            
        except Exception as e:
            logger.error("Failed to enqueue audio for Local AI Server", 
                         error=str(e), bytes=len(audio_chunk), exc_info=True)

    async def _send_loop(self):
        batch_ms = max(5, self._batch_ms)
        while True:
            try:
                # Wait for first chunk
                chunk = await self._send_queue.get()
                if chunk is None:
                    continue
                # Coalesce additional chunks available now (non-blocking)
                batch = [chunk]
                try:
                    while True:
                        batch.append(self._send_queue.get_nowait())
                except asyncio.QueueEmpty:
                    pass

                # Convert and send one aggregated message
                # Handle different input modes
                if self.input_mode == 'pcm16_16k':
                    # Already 16kHz PCM, just concatenate
                    pcm16k = b"".join(batch)
                elif self.input_mode == 'pcm16_8k':
                    # 8kHz PCM, resample to 16kHz
                    pcm8k = b"".join(batch)
                    pcm16k, self._resample_state_stt = resample_audio(pcm8k, 8000, 16000, state=self._resample_state_stt)
                else:
                    # µ-law 8kHz, convert to PCM then resample
                    pcm8k = b"".join(audioop.ulaw2lin(b, 2) for b in batch)
                    pcm16k, self._resample_state_stt = resample_audio(pcm8k, 8000, 16000, state=self._resample_state_stt)
                
                # Process audio batch for STT
                total_bytes = sum(len(b) for b in batch)
                logger.info("🔄 PROVIDER BATCH - Processing for STT",
                             frames=len(batch),
                             total_bytes=total_bytes,
                             input_mode=self.input_mode)
                
                msg = json.dumps({
                    "type": "audio", 
                    "data": base64.b64encode(pcm16k).decode('utf-8'),
                    "rate": 16000,
                    "format": "pcm16le",
                    "call_id": self._active_call_id,
                    "mode": self._mode  # "stt" for hybrid, "full" for all-local
                })
                try:
                    await self.websocket.send(msg)
                    logger.debug("WebSocket batch send successful", 
                                 frames=len(batch), 
                                 in_bytes=total_bytes,
                                 call_id=self._active_call_id,
                                 queue_depth=self._send_queue.qsize())
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning("WebSocket closed during send, attempting reconnect", 
                                   code=getattr(e, 'code', None), 
                                   reason=getattr(e, 'reason', None))
                    ok = await self._reconnect()
                    if ok:
                        try:
                            await self.websocket.send(msg)
                            logger.debug("WebSocket resend after reconnect successful", frames=len(batch))
                        except Exception as e:
                            logger.error("WebSocket resend failed after reconnect", error=str(e), exc_info=True)
                except Exception as e:
                    logger.error("WebSocket send error", error=str(e), exc_info=True)
                # Pace the loop
                await asyncio.sleep(batch_ms / 1000.0)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Sender loop error", exc_info=True)
                await asyncio.sleep(0.1)

    @staticmethod
    def _normalize_audio_encoding(encoding: Any) -> str:
        value = (str(encoding or "").strip().lower()) if encoding is not None else ""
        if value in {"ulaw", "g711_ulaw", "g711u"}:
            return "mulaw"
        if value in {"slin16", "slin", "pcm16le"}:
            return "linear16"
        if value in {"linear16", "pcm16"}:
            return value
        return value or "mulaw"

    @staticmethod
    def _coerce_sample_rate(rate: Any) -> Optional[int]:
        try:
            parsed = int(rate)
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_stt_backend(value: Any) -> Optional[str]:
        backend = str(value or "").strip().lower()
        return backend or None

    def get_active_stt_backend(self) -> Optional[str]:
        runtime_backend = self._normalize_stt_backend(self._runtime_stt_backend)
        if runtime_backend:
            return runtime_backend
        return self._normalize_stt_backend(getattr(self.config, "stt_backend", None))

    def is_whisper_stt_active(self) -> bool:
        return (self.get_active_stt_backend() or "") in {"faster_whisper", "whisper_cpp"}

    @staticmethod
    def _bytes_per_sample(encoding: str) -> int:
        return 2 if encoding in {"linear16", "pcm16", "slin", "slin16"} else 1

    def _estimate_audio_duration_seconds(self, audio_bytes: bytes, encoding: str, sample_rate: Optional[int]) -> float:
        if not audio_bytes:
            return 0.0
        normalized = self._normalize_audio_encoding(encoding)
        effective_rate = sample_rate or (8000 if normalized == "mulaw" else 16000)
        bps = self._bytes_per_sample(normalized)
        try:
            return max(0.0, float(len(audio_bytes)) / float(max(1, bps * effective_rate)))
        except Exception:
            return 0.0

    def _schedule_agent_audio_done(self, call_id: str, delay_seconds: float) -> None:
        existing = self._agent_audio_done_tasks.pop(call_id, None)
        if existing and not existing.done():
            existing.cancel()

        delay_seconds = max(0.08, float(delay_seconds))

        async def _emit_done() -> None:
            try:
                await asyncio.sleep(delay_seconds)
                if self.on_event:
                    await self.on_event({
                        "type": "AgentAudioDone",
                        "call_id": call_id,
                        # Local provider emits one audio blob per response; treat each
                        # done event as a completed response boundary so engine can
                        # act on cleanup_after_tts (hangup_call) reliably.
                        "streaming_done": True,
                    })
            except asyncio.CancelledError:
                return
            except Exception:
                logger.error("Failed to emit delayed AgentAudioDone", call_id=call_id, exc_info=True)
            finally:
                current = self._agent_audio_done_tasks.get(call_id)
                if current is asyncio.current_task():
                    self._agent_audio_done_tasks.pop(call_id, None)

        self._agent_audio_done_tasks[call_id] = asyncio.create_task(_emit_done())

    async def _emit_agent_audio(self, call_id: str, audio_bytes: bytes, *, encoding: str, sample_rate: Optional[int]) -> None:
        if not audio_bytes or not self.on_event:
            return

        normalized_encoding = self._normalize_audio_encoding(encoding)
        effective_rate = sample_rate or (8000 if normalized_encoding == "mulaw" else 16000)

        await self.on_event({
            "type": "AgentAudio",
            "data": audio_bytes,
            "call_id": call_id,
            "encoding": normalized_encoding,
            "sample_rate": effective_rate,
        })

        # Hold TTS gating until the audio should be fully played out.
        duration_s = self._estimate_audio_duration_seconds(audio_bytes, normalized_encoding, effective_rate)
        self._schedule_agent_audio_done(call_id, duration_s + 0.05)

    def set_input_mode(self, mode: str):
        # mode: 'mulaw8k' or 'pcm16_8k'
        self.input_mode = mode

    async def play_initial_greeting(self, call_id: str):
        """Play an initial greeting message to the caller."""
        try:
            # Ensure websocket connection exists
            if not self.is_connected():
                await self.initialize()
            if not self.is_connected():
                raise RuntimeError("Local AI Server WebSocket is not connected for greeting playback")

            # Ensure the receive loop will attribute AgentAudio to this call
            self._active_call_id = call_id

            # Compute greeting to speak; skip if none
            greeting_text = self._initial_greeting or ""
            if not greeting_text.strip():
                logger.info("No initial greeting configured; skipping greeting playback", call_id=call_id)
                return

            # Send a TTS request that the local AI server understands; it will
            # reply with metadata (tts_audio) and then a binary payload, which
            # our receive loop will emit as AgentAudio for this call.
            tts_message = {
                "type": "tts_request",
                "call_id": call_id,
                "text": greeting_text,
            }

            await self.websocket.send(json.dumps(tts_message))
            logger.info("Sent greeting TTS request to Local AI Server", call_id=call_id)
            
            # Record greeting in conversation history
            if self.on_event:
                await self.on_event({
                    "type": "agent_transcript",
                    "call_id": call_id,
                    "text": greeting_text,
                })
                logger.debug("Recorded greeting in conversation history", call_id=call_id)
        except Exception as e:
            logger.error("Failed to send greeting message", call_id=call_id, error=str(e), exc_info=True)
            raise

    async def stop_session(self):
        # DON'T cancel the listener task - keep it running to receive AgentAudio events
        # if self._listener_task:
        #     self._listener_task.cancel()
        # DON'T close the WebSocket - keep it alive for reuse
        # if self.websocket:
        #     await self.websocket.close()
        #     logger.info("Disconnected from Local AI Server.")
        
        # Safety guard: drain send queue and discard pending frames
        queue_size = self._send_queue.qsize()
        if queue_size > 0:
            logger.debug("Draining send queue on stop_session", queue_size=queue_size)
            while not self._send_queue.empty():
                try:
                    self._send_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

        if self._pending_llm_tool_responses:
            for request_id in list(self._pending_llm_tool_responses.keys()):
                self._pending_llm_tool_responses.pop(request_id, None)
                self._cancel_gateway_timeout(request_id)
        if self._pending_barge_in_acks:
            for request_id in list(self._pending_barge_in_acks.keys()):
                pending = self._pending_barge_in_acks.pop(request_id, None)
                future = (pending or {}).get("future") if isinstance(pending, dict) else None
                if future and not future.done():
                    future.cancel()
                task = self._barge_in_ack_tasks.pop(request_id, None)
                if task and not task.done():
                    task.cancel()
        
        # DON'T clear the active call ID immediately - keep it for AgentAudio processing
        # The call_id will be cleared when the TTS playback is complete
        # self._active_call_id = None
        logger.info("Provider session stopped, WebSocket connection and listener maintained. Call ID preserved for TTS processing.")

    async def clear_active_call_id(self):
        """Clear the active call ID after TTS playback is complete."""
        old_call_id = self._active_call_id
        if old_call_id:
            self._tts_audio_meta_by_call.pop(old_call_id, None)
            self._last_user_transcript_by_call.pop(old_call_id, None)
            done_task = self._agent_audio_done_tasks.pop(old_call_id, None)
            if done_task and not done_task.done():
                done_task.cancel()
            stale_ids = [
                rid
                for rid, pending in self._pending_llm_tool_responses.items()
                if str((pending or {}).get("call_id") or "") == str(old_call_id)
            ]
            for rid in stale_ids:
                self._pending_llm_tool_responses.pop(rid, None)
                self._cancel_gateway_timeout(rid)
            stale_barge_ack_ids = [
                rid
                for rid, pending in self._pending_barge_in_acks.items()
                if str((pending or {}).get("call_id") or "") == str(old_call_id)
            ]
            for rid in stale_barge_ack_ids:
                pending = self._pending_barge_in_acks.pop(rid, None)
                future = (pending or {}).get("future") if isinstance(pending, dict) else None
                if future and not future.done():
                    future.cancel()
                task = self._barge_in_ack_tasks.pop(rid, None)
                if task and not task.done():
                    task.cancel()
        self._active_call_id = None
        logger.info("Active call ID cleared after TTS completion.")

    async def _receive_loop(self):
        if not self.websocket:
            return
        try:
            async for message in self.websocket:
                # Handle binary messages (raw audio)
                if isinstance(message, bytes):
                    # Safety guard: drop AgentAudio if no active call
                    if self._active_call_id is None:
                        logger.debug("Dropping AgentAudio - no active call", message_size=len(message))
                        continue

                    call_id = self._active_call_id
                    meta = self._tts_audio_meta_by_call.get(call_id, {})
                    encoding = self._normalize_audio_encoding(meta.get("encoding"))
                    sample_rate = self._coerce_sample_rate(meta.get("sample_rate") or meta.get("sample_rate_hz"))
                    await self._emit_agent_audio(
                        call_id,
                        message,
                        encoding=encoding,
                        sample_rate=sample_rate,
                    )
                # Handle JSON messages (TTS responses, etc.)
                elif isinstance(message, str):
                    try:
                        data = json.loads(message)
                        if data.get("type") == "status_response":
                            self._last_status = dict(data)
                            backend = self._normalize_stt_backend(data.get("stt_backend"))
                            if backend:
                                self._runtime_stt_backend = backend
                            fut = self._pending_status_future
                            if fut and not fut.done():
                                fut.set_result(data)
                            continue
                        if data.get("type") == "tts_audio":
                            meta_call_id = data.get("call_id") or self._active_call_id
                            if meta_call_id:
                                self._tts_audio_meta_by_call[meta_call_id] = {
                                    "encoding": self._normalize_audio_encoding(data.get("encoding")),
                                    "sample_rate": self._coerce_sample_rate(data.get("sample_rate_hz") or data.get("sample_rate")),
                                    "byte_length": data.get("byte_length"),
                                }
                            continue
                        # Handle TTS responses
                        if data.get("type") == "tts_response":
                            # Find the pending TTS response and complete it
                            text = data.get("text", "")
                            if text in self._pending_tts_responses:
                                future = self._pending_tts_responses.pop(text)
                                if not future.done():
                                    future.set_result(data)
                                    logger.info("TTS response received and delivered", text=text[:50])
                                else:
                                    logger.warning("TTS response received but future already completed", text=text[:50])
                            else:
                                logger.warning("TTS response received but no pending request found", text=text[:50])

                            # Additionally, if the TTS response carries base64 audio, decode and emit as AgentAudio
                            audio_b64 = data.get("audio_data") or data.get("audio")
                            if audio_b64:
                                try:
                                    audio_bytes = base64.b64decode(audio_b64)
                                except Exception:
                                    logger.warning("Invalid base64 in tts_response from Local AI Server")
                                    audio_bytes = b""

                                if audio_bytes and self.on_event:
                                    target_call_id = data.get("call_id") or self._active_call_id
                                    if target_call_id:
                                        try:
                                            encoding = self._normalize_audio_encoding(data.get("encoding"))
                                            sample_rate = self._coerce_sample_rate(data.get("sample_rate_hz") or data.get("sample_rate"))
                                            await self._emit_agent_audio(
                                                target_call_id,
                                                audio_bytes,
                                                encoding=encoding,
                                                sample_rate=sample_rate,
                                            )
                                            # Signal farewell TTS received for hangup coordination
                                            if text and text.lower() == "goodbye":
                                                await self.on_event({
                                                    "type": "FarewellTTSReceived",
                                                    "call_id": target_call_id,
                                                    "audio_size": len(audio_bytes),
                                                })
                                                logger.info("🎤 Farewell TTS audio emitted", call_id=target_call_id, audio_size=len(audio_bytes))
                                        except Exception:
                                            logger.error("Failed to emit AgentAudio(/Done) for tts_response", exc_info=True)
                                    else:
                                        logger.debug("Dropping TTS audio - no active call to attribute", size=len(audio_bytes))
                        elif data.get("type") == "stt_result":
                            # Handle STT result - emit as transcript for conversation history
                            reported_backend = self._normalize_stt_backend(data.get("stt_backend"))
                            if reported_backend:
                                self._runtime_stt_backend = reported_backend
                            text = data.get("text", "").strip()
                            call_id = data.get("call_id") or self._active_call_id
                            is_final = data.get("is_final", True)
                            
                            if text and is_final and self.on_event:
                                # Defense-in-depth: reject punctuation-only transcripts (e.g. "?", ".")
                                # that Kroko/other STT backends emit from silence/noise
                                if not any(ch.isalnum() for ch in text):
                                    logger.info("Suppressed non-linguistic stt_result", call_id=call_id, text=text[:20])
                                    continue
                                if call_id:
                                    self._last_user_transcript_by_call[call_id] = text
                                await self.on_event({
                                    "type": "transcript",
                                    "call_id": call_id,
                                    "text": text,
                                })
                                logger.debug("Emitted user transcript for history", call_id=call_id, text=text[:50])
                        elif data.get("type") == "llm_tool_response":
                            handled = await self._handle_llm_tool_response(data)
                            if not handled:
                                logger.debug("Received unmatched llm_tool_response", request_id=data.get("request_id"))
                        elif data.get("type") == "barge_in_ack":
                            request_id = str(data.get("request_id") or "").strip()
                            call_id = str(data.get("call_id") or "").strip()
                            matched_id = request_id
                            if matched_id and matched_id not in self._pending_barge_in_acks:
                                matched_id = ""
                            if not matched_id and call_id:
                                for rid, pending in self._pending_barge_in_acks.items():
                                    if str((pending or {}).get("call_id") or "") == call_id:
                                        matched_id = rid
                                        break
                            if matched_id:
                                pending = self._pending_barge_in_acks.pop(matched_id, None)
                                future = (pending or {}).get("future") if isinstance(pending, dict) else None
                                if future and not future.done():
                                    future.set_result(data)
                                logger.debug(
                                    "Received barge_in_ack from Local AI Server",
                                    call_id=call_id,
                                    request_id=matched_id,
                                )
                            else:
                                logger.debug(
                                    "Received unmatched barge_in_ack from Local AI Server",
                                    call_id=call_id,
                                    request_id=request_id,
                                )
                        elif data.get("type") == "llm_response":
                            request_id = str(data.get("request_id") or "").strip()
                            if request_id:
                                pending = self._pending_llm_responses.get(request_id)
                                if pending and not pending.done():
                                    pending.set_result(data.get("text", ""))
                                    continue
                                if request_id.startswith("tool-repair-"):
                                    logger.debug("Dropping stale tool-repair response", call_id=self._active_call_id, request_id=request_id)
                                    continue
                            llm_text = data.get("text", "")
                            call_id = data.get("call_id") or self._active_call_id

                            # Honor tool-gateway completion markers at both
                            # the top level AND nested under `extra` —
                            # docs/local-ai-server/PROTOCOL.md describes the
                            # post-tool final answer as carrying
                            # `extra.tool_result_final = true`, while the
                            # legacy in-band path emits the same markers at
                            # the top level. Pre-fix this branch only
                            # checked the top level, so a documented
                            # `extra.*` payload would fall through into
                            # `_dispatch_llm_tool_gateway_request()` and get
                            # reparsed as another tool turn instead of being
                            # emitted as the final answer. Per CodeRabbit
                            # review of PR #384 comment 3214117422.
                            _extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
                            if (
                                data.get("tool_gateway_done")
                                or data.get("tool_result_final")
                                or _extra.get("tool_gateway_done")
                                or _extra.get("tool_result_final")
                            ):
                                await self._emit_local_llm_result(
                                    call_id=call_id,
                                    llm_text=llm_text,
                                    clean_text=llm_text,
                                    tool_calls=None,
                                    tool_path=str(
                                        data.get("tool_path")
                                        or _extra.get("tool_path")
                                        or "none"
                                    ),
                                )
                                continue

                            # Structured tool gateway is enabled only for full local provider mode.
                            if self._is_structured_tool_gateway_active():
                                dispatched = await self._dispatch_llm_tool_gateway_request(
                                    llm_text=llm_text,
                                    call_id=call_id,
                                )
                                if dispatched:
                                    continue
                                logger.warning(
                                    "llm_tool_request dispatch failed; using parser fallback",
                                    call_id=call_id,
                                )

                            await self._process_llm_text_fallback(
                                llm_text=llm_text,
                                call_id=call_id,
                                tool_path="parser",
                            )
                        else:
                            logger.debug("Received JSON message from Local AI Server", message=data)
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON string message from Local AI Server", message=message)
                else:
                    logger.warning("Received unknown message type from Local AI Server", message_type=type(message))
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("Local AI Server connection closed", reason=str(e))
            # Attempt immediate reconnect
            logger.info("Attempting to reconnect to Local AI Server...")
            success = await self._reconnect()
            if success:
                logger.info("✅ Reconnected to Local AI Server, restarting receive loop")
                # Restart the receive loop
                if not self._listener_task or self._listener_task.done():
                    self._listener_task = asyncio.create_task(self._receive_loop())
            else:
                # Immediate reconnect failed - if we were previously connected,
                # start background reconnect task (non-blocking, up to 12 minutes)
                if self._was_connected:
                    logger.info(
                        "Immediate reconnect failed, starting background reconnect task",
                        note="Will check every 30s for up to 12 minutes"
                    )
                    self._start_background_reconnect()
                else:
                    logger.error("Failed to reconnect to Local AI Server")
        except Exception:
            logger.error("Error receiving events from Local AI Server", exc_info=True)

    async def speak(self, text: str):
        # This provider works by streaming STT->LLM->TTS on the server side.
        # Direct speech injection is not the primary mode of operation.
        logger.warning("Direct 'speak' method not implemented for this provider. Use the streaming pipeline.")
    
    async def text_to_speech(self, text: str) -> Optional[bytes]:
        """Generate TTS audio for the given text."""
        try:
            if not self.websocket or self.websocket.state.name != "OPEN":
                logger.error("WebSocket not connected for TTS")
                return None
            
            # Send TTS request to Local AI Server
            tts_message = {
                "type": "tts_request",
                "text": text,
                "call_id": self._active_call_id or "greeting"
            }
            
            await self.websocket.send(json.dumps(tts_message))
            logger.info("Sent TTS request to Local AI Server", text=text[:50] + "..." if len(text) > 50 else text)
            
            # Wait for TTS response using a future-based approach
            response_future = asyncio.Future()
            self._pending_tts_responses[text] = response_future
            
            try:
                # Wait for response with timeout
                response_data = await asyncio.wait_for(response_future, timeout=self.response_timeout)
                
                if response_data.get("type") == "tts_response" and response_data.get("audio_data"):
                    # Decode base64 audio data
                    audio_data = base64.b64decode(response_data["audio_data"])
                    logger.info("Received TTS audio data", size=len(audio_data))
                    return audio_data
                else:
                    logger.warning("Unexpected TTS response format", response=response_data)
                    return None
                    
            except asyncio.TimeoutError:
                logger.error("TTS request timed out")
                return None
            finally:
                # Clean up the pending response
                self._pending_tts_responses.pop(text, None)
                
        except Exception as e:
            logger.error("Failed to generate TTS", text=text, error=str(e), exc_info=True)
            return None
    
    def get_provider_info(self) -> Dict[str, Any]:
        return {
            "name": "LocalProvider",
            "type": "local_stream",
            "supported_codecs": self.supported_codecs,
        }
    
    # Backwards-compatible alias for older callers that treated readiness as "connected".
    # Prefer `is_connected()` for connection state.
    def is_connected_ready(self) -> bool:
        return self.is_connected()
