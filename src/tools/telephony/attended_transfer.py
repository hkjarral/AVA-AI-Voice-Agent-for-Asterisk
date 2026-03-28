"""
Attended (Warm) Transfer Tool - MOH + agent announcement + DTMF accept/decline.

This tool originates a separate "agent" call leg to a configured destination extension.
The engine then:
  - plays an announcement to the destination agent (TTS via Local AI Server),
  - waits for DTMF acceptance (1=accept, 2=decline),
  - bridges caller <-> destination and removes AI media on accept.
"""

import asyncio
from typing import Any, Dict, Optional
import time
import structlog

from src.tools.base import Tool, ToolCategory, ToolDefinition, ToolParameter
from src.tools.context import ToolExecutionContext

logger = structlog.get_logger(__name__)


class AttendedTransferTool(Tool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="attended_transfer",
            description=(
                "Warm transfer to a configured extension with a one-way announcement to the agent, "
                "then DTMF acceptance (1=accept, 2=decline). Caller is placed on MOH while the agent is contacted. "
                "The screening payload can be a basic TTS briefing, an experimental AI-generated summary, or a caller-recorded screening clip, depending on config. "
                "Use when you must brief a human before connecting the caller. "
                "Use exact configured destination keys exposed in the runtime prompt/context."
            ),
            category=ToolCategory.TELEPHONY,
            requires_channel=True,
            max_execution_time=30,
            parameters=[
                ToolParameter(
                    name="destination",
                    type="string",
                    description=(
                        "Name of the configured destination to dial (must be an extension destination with attended transfer allowed). "
                        "Use a destination key configured in Tools -> Transfer Destinations."
                    ),
                    required=True,
                )
            ],
        )

    async def execute(self, parameters: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
        destination = parameters.get("destination") or parameters.get("target")
        if not destination:
            return {"status": "failed", "message": "Missing destination"}

        cfg = context.get_config_value("tools.attended_transfer") or {}

        transfer_cfg = context.get_config_value("tools.transfer") or {}
        destinations = (transfer_cfg.get("destinations") or {}) if isinstance(transfer_cfg, dict) else {}
        destination = str(destination).strip()
        allowed_attended = self._allowed_attended_destinations(destinations)
        resolved_key = self._resolve_destination_key(destination, destinations, allowed_attended)
        if not resolved_key:
            allowed = sorted(allowed_attended.keys())
            return {
                "status": "failed",
                "message": (
                    f"Unknown destination: {destination}. "
                    + (f"Allowed attended destinations: {', '.join(allowed)}. " if allowed else "")
                    + "Use one of the configured destination keys (Tools → Transfer Destinations)."
                ),
            }

        destination = resolved_key

        dest_cfg = destinations[destination] or {}
        if dest_cfg.get("type") != "extension":
            return {"status": "failed", "message": "Attended transfer is only supported for extension destinations"}

        if not bool(dest_cfg.get("attended_allowed", False)):
            allowed = [
                k
                for k, v in destinations.items()
                if isinstance(v, dict)
                and v.get("type") == "extension"
                and bool(v.get("attended_allowed", False))
            ]
            return {
                "status": "failed",
                "message": (
                    f"Attended transfer is not enabled for destination: {destination}. "
                    + (f"Allowed attended destinations: {', '.join(sorted(allowed))}. " if allowed else "")
                    + "Enable it in Tools → Transfer Destinations (Allow Attended Transfer), then retry."
                ),
            }

        extension = str(dest_cfg.get("target") or "").strip()
        if not extension:
            return {"status": "failed", "message": f"Invalid destination target for: {destination}"}

        description = str(dest_cfg.get("description") or destination)

        # Determine dial endpoint.
        dial_endpoint = self._resolve_dial_endpoint(extension, dest_cfg, transfer_cfg, context)
        if not dial_endpoint:
            return {"status": "failed", "message": f"Unable to resolve dial endpoint for {destination}"}

        dial_timeout_sec = int(cfg.get("dial_timeout_seconds", 30) or 30)
        moh_class = str(cfg.get("moh_class", "default") or "default")
        screening_mode = self._resolve_screening_mode(cfg)
        raw_screening_mode = str((cfg or {}).get("screening_mode") or "").strip().lower()
        if screening_mode == "ai_briefing" and (
            raw_screening_mode == "ai_summary" or bool((cfg or {}).get("pass_caller_info_to_context", False))
        ):
            logger.warning(
                "Deprecated attended transfer ai_summary config mapped to ai_briefing",
                call_id=context.call_id,
                screening_mode=raw_screening_mode or "pass_caller_info_to_context",
                config_key="tools.attended_transfer.pass_caller_info_to_context",
                replacement="tools.attended_transfer.screening_mode=ai_briefing",
            )
        caller_screening_prompt = str(
            cfg.get("caller_screening_prompt")
            or "Before I connect you, please say your name and the reason for your call."
        ).strip()
        caller_screening_max_seconds = float(cfg.get("caller_screening_max_seconds", 6) or 6)
        caller_screening_silence_ms = int(cfg.get("caller_screening_silence_ms", 1200) or 1200)

        session = await context.get_session()
        call_id = session.call_id

        logger.info(
            "📞 Attended transfer requested",
            call_id=call_id,
            destination_key=destination,
            extension=extension,
            dial_endpoint=dial_endpoint,
        )

        session.current_action = {
            "type": "attended_transfer",
            "destination_key": destination,
            "target": extension,
            "target_name": description,
            "dial_endpoint": dial_endpoint,
            "dial_timeout_seconds": dial_timeout_sec,
            "moh_class": moh_class,
            "started_at": time.time(),
            "agent_channel_id": None,
            "answered": False,
            "decision": None,
            "decision_digit": None,
            "screening_mode": screening_mode,
        }
        await context.session_store.upsert_call(session)

        if screening_mode == "caller_recording":
            session.current_action["screening_status"] = "pending"
            session.current_action["caller_screening_prompt"] = caller_screening_prompt
            session.current_action["caller_screening_max_seconds"] = caller_screening_max_seconds
            session.current_action["caller_screening_silence_ms"] = caller_screening_silence_ms
            await context.session_store.upsert_call(session)

            engine = getattr(context.ari_client, "engine", None)
            workflow = self._complete_caller_recording_transfer(
                context=context,
                destination=destination,
                extension=extension,
                description=description,
                dial_endpoint=dial_endpoint,
                dial_timeout_sec=dial_timeout_sec,
                moh_class=moh_class,
                screening_max_seconds=caller_screening_max_seconds,
                screening_silence_ms=caller_screening_silence_ms,
            )
            if engine and hasattr(engine, "_fire_and_forget_for_call"):
                engine._fire_and_forget_for_call(call_id, workflow, name=f"attx-screening-{call_id}")
            else:
                task = asyncio.create_task(workflow, name=f"attx-screening-{call_id}")
                task.add_done_callback(self._log_screening_task_result(call_id))
            return {
                "status": "success",
                "message": caller_screening_prompt,
                "destination": destination,
                "type": "attended_transfer",
            }

        result = await self._originate_attended_transfer_leg(
            context=context,
            destination=destination,
            extension=extension,
            dial_endpoint=dial_endpoint,
            dial_timeout_sec=dial_timeout_sec,
            moh_class=moh_class,
        )
        if not result:
            return {
                "status": "failed",
                "message": f"Unable to place the transfer call to {description}.",
            }

        return {
            "status": "success",
            "message": f"Please hold while I connect you to {description}.",
            "destination": destination,
            "type": "attended_transfer",
        }

    def _allowed_attended_destinations(self, destinations: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        allowed: Dict[str, Dict[str, Any]] = {}
        for key, cfg in (destinations or {}).items():
            if not isinstance(cfg, dict):
                continue
            if cfg.get("type") != "extension":
                continue
            if not bool(cfg.get("attended_allowed", False)):
                continue
            allowed[str(key)] = cfg
        return allowed

    def _resolve_destination_key(
        self,
        user_value: str,
        destinations: Dict[str, Any],
        allowed_attended: Dict[str, Dict[str, Any]],
    ) -> Optional[str]:
        # Exact key match.
        if user_value in destinations:
            return user_value

        raw = (user_value or "").strip()
        if not raw:
            return None
        raw_lower = raw.lower()

        # Case-insensitive exact key match.
        for key in destinations.keys():
            if str(key).lower() == raw_lower:
                return str(key)

        # Prefer matching only against attended-allowed extension destinations.
        candidates = allowed_attended if allowed_attended else {
            str(k): v for k, v in (destinations or {}).items() if isinstance(v, dict)
        }

        # If user provides an extension number (target), match by target.
        for key, cfg in candidates.items():
            target = str(cfg.get("target") or "").strip()
            if target and (raw == target or raw_lower == target.lower()):
                return key

        # If user uses common shorthand like "sales"/"support"/"agent", match by key/description.
        matches = []
        for key, cfg in candidates.items():
            key_lower = key.lower()
            desc_lower = str(cfg.get("description") or "").lower()
            if raw_lower in key_lower or raw_lower in desc_lower:
                matches.append(key)

        # Common aliases (non-exhaustive) to reduce first-attempt failures.
        if not matches:
            alias_map = {
                "sales": ["sales"],
                "support": ["support", "tech"],
                "agent": ["agent", "human", "representative", "rep", "person", "operator"],
                "human": ["agent", "human", "representative", "rep", "person", "operator"],
                "real person": ["agent", "human", "representative", "rep", "person", "operator"],
                "live agent": ["agent", "human", "representative", "rep", "person", "operator"],
            }
            tokens = alias_map.get(raw_lower)
            if tokens:
                for key, cfg in candidates.items():
                    key_lower = key.lower()
                    desc_lower = str(cfg.get("description") or "").lower()
                    if any(t in key_lower or t in desc_lower for t in tokens):
                        matches.append(key)

        # Deterministic: if exactly one match, use it; if multiple, prefer *_agent if present.
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            preferred = [m for m in matches if m.lower().endswith("_agent")]
            if len(preferred) == 1:
                return preferred[0]
        return None

    def _resolve_dial_endpoint(
        self,
        extension: str,
        dest_cfg: Dict[str, Any],
        transfer_cfg: Dict[str, Any],
        context: ToolExecutionContext,
    ) -> Optional[str]:
        if isinstance(dest_cfg, dict):
            dial_string = dest_cfg.get("dial_string")
            if dial_string:
                return str(dial_string)

        ext_cfg = context.get_config_value(f"tools.extensions.internal.{extension}") or {}
        if isinstance(ext_cfg, dict) and ext_cfg.get("dial_string"):
            return str(ext_cfg.get("dial_string"))

        technology = None
        if isinstance(transfer_cfg, dict):
            technology = transfer_cfg.get("technology")
        technology = str(technology or "PJSIP")
        return f"{technology}/{extension}"

    def _build_ai_caller_id(self, context: ToolExecutionContext) -> str:
        ai_name = str(context.get_config_value("tools.ai_identity.name", "AI Agent") or "AI Agent")
        ai_number = str(context.get_config_value("tools.ai_identity.number", "6789") or "6789")
        return f"\"{ai_name}\" <{ai_number}>"

    def _resolve_screening_mode(self, attended_cfg: Dict[str, Any]) -> str:
        raw_mode = str((attended_cfg or {}).get("screening_mode") or "").strip().lower()
        if raw_mode in {"basic_tts", "caller_recording", "ai_briefing"}:
            return raw_mode
        if raw_mode == "ai_summary":
            return "ai_briefing"
        if bool((attended_cfg or {}).get("pass_caller_info_to_context", False)):
            return "ai_briefing"
        return "basic_tts"

    @staticmethod
    def _log_screening_task_result(call_id: str):
        def _callback(task: asyncio.Task) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc:
                logger.error(
                    "Attended transfer screening background task failed",
                    call_id=call_id,
                    error=str(exc),
                    exc_info=exc,
                )
        return _callback

    async def _clear_pending_attended_transfer_state(
        self,
        context: ToolExecutionContext,
        *,
        clear_current_action: bool,
        restore_audio_capture: bool,
        reason: str,
    ) -> None:
        try:
            session = await context.get_session()
        except Exception:
            logger.debug("Failed to load session while clearing attended transfer state", call_id=context.call_id, reason=reason, exc_info=True)
            return

        action = session.current_action or {}
        if not isinstance(action, dict) or action.get("type") != "attended_transfer":
            return

        prior_audio_capture = action.pop("pre_transfer_audio_capture_enabled", None)
        if clear_current_action:
            session.current_action = None
        else:
            session.current_action = action

        if restore_audio_capture and prior_audio_capture is not None:
            session.audio_capture_enabled = bool(prior_audio_capture)

        try:
            await context.session_store.upsert_call(session)
        except Exception:
            logger.debug("Failed to persist cleared attended transfer state", call_id=context.call_id, reason=reason, exc_info=True)

    async def _start_moh(self, context: ToolExecutionContext, moh_class: str) -> None:
        await context.ari_client.send_command(
            method="POST",
            resource=f"channels/{context.caller_channel_id}/moh",
            params={"mohClass": moh_class},
        )

    async def _originate_attended_transfer_leg(
        self,
        *,
        context: ToolExecutionContext,
        destination: str,
        extension: str,
        dial_endpoint: str,
        dial_timeout_sec: int,
        moh_class: str,
    ) -> Optional[Dict[str, Any]]:
        call_id = context.call_id
        try:
            await self._start_moh(context, moh_class)
        except Exception:
            logger.warning("Failed to start MOH for attended transfer", call_id=call_id, exc_info=True)

        try:
            session = await context.get_session()
            if session.current_action and session.current_action.get("type") == "attended_transfer":
                session.current_action["pre_transfer_audio_capture_enabled"] = bool(
                    getattr(session, "audio_capture_enabled", True)
                )
                session.current_action["screening_status"] = session.current_action.get("screening_status") or "skipped"
            session.audio_capture_enabled = False
            await context.session_store.upsert_call(session)
        except Exception:
            logger.debug("Failed to update session before attended transfer originate", call_id=call_id, exc_info=True)

        caller_id = self._build_ai_caller_id(context)
        app = str(context.get_config_value("asterisk.app_name", "asterisk-ai-voice-agent") or "asterisk-ai-voice-agent")

        try:
            result = await context.ari_client.send_command(
                method="POST",
                resource="channels",
                data={
                    "endpoint": dial_endpoint,
                    "callerId": caller_id,
                    "timeout": dial_timeout_sec,
                    "variables": {
                        "AGENT_ACTION": "attended_transfer",
                        "AGENT_CALL_ID": call_id,
                        "AGENT_TARGET": extension,
                        "AAVA_TRANSFER_DESTINATION_KEY": destination,
                    },
                },
                params={"app": app, "appArgs": f"attended-transfer,{call_id},{destination}"},
            )
        except Exception:
            result = None
            logger.error("Failed to originate attended transfer agent leg", call_id=call_id, exc_info=True)

        if not result or not isinstance(result, dict) or not result.get("id"):
            await self._cleanup_failed_originate(context, call_id)
            return None

        agent_channel_id = result["id"]
        try:
            session = await context.get_session()
            if session.current_action and session.current_action.get("type") == "attended_transfer":
                session.current_action["agent_channel_id"] = agent_channel_id
                session.current_action["screening_status"] = session.current_action.get("screening_status") or "skipped"
                await context.session_store.upsert_call(session)
        except Exception:
            logger.debug("Failed to persist attended transfer originate state", call_id=call_id, exc_info=True)

        try:
            engine = getattr(context.ari_client, "engine", None)
            if engine and hasattr(engine, "register_attended_transfer_agent_channel"):
                engine.register_attended_transfer_agent_channel(call_id, agent_channel_id)
            if engine and hasattr(engine, "start_attended_transfer_timeout_guard"):
                engine.start_attended_transfer_timeout_guard(call_id, agent_channel_id, timeout_sec=dial_timeout_sec)
        except Exception:
            logger.debug("Failed to register attended transfer runtime helpers", call_id=call_id, exc_info=True)

        logger.info(
            "📞 Attended transfer agent leg originated",
            call_id=call_id,
            agent_channel_id=agent_channel_id,
            destination_key=destination,
        )
        return result

    async def _complete_caller_recording_transfer(
        self,
        *,
        context: ToolExecutionContext,
        destination: str,
        extension: str,
        description: str,
        dial_endpoint: str,
        dial_timeout_sec: int,
        moh_class: str,
        screening_max_seconds: float,
        screening_silence_ms: int,
    ) -> None:
        call_id = context.call_id
        engine = getattr(context.ari_client, "engine", None)
        screening_result = None
        try:
            if engine and hasattr(engine, "collect_attended_transfer_screening"):
                screening_result = await engine.collect_attended_transfer_screening(
                    call_id=call_id,
                    max_seconds=screening_max_seconds,
                    silence_ms=screening_silence_ms,
                )

            session = await context.get_session()
            action = session.current_action or {}
            if action.get("type") != "attended_transfer":
                return

            if screening_result and screening_result.get("audio_ulaw"):
                action["screening_status"] = "captured"
                action["screening_payload"] = {
                    "kind": "caller_recording",
                    "audio_ulaw": screening_result.get("audio_ulaw"),
                    "duration_ms": int(screening_result.get("duration_ms") or 0),
                }
            else:
                action["screening_status"] = "fallback"
                action.pop("screening_payload", None)
            session.current_action = action
            await context.session_store.upsert_call(session)
        except Exception:
            logger.error("Caller-recording attended transfer screening failed", call_id=call_id, exc_info=True)
            await self._clear_pending_attended_transfer_state(
                context,
                clear_current_action=True,
                restore_audio_capture=True,
                reason="screening-failed",
            )
            return

        result = await self._originate_attended_transfer_leg(
            context=context,
            destination=destination,
            extension=extension,
            dial_endpoint=dial_endpoint,
            dial_timeout_sec=dial_timeout_sec,
            moh_class=moh_class,
        )
        if result:
            return

        logger.warning("Caller-recording attended transfer fell back before origination", call_id=call_id, destination=description)

    async def _cleanup_failed_originate(self, context: ToolExecutionContext, call_id: str) -> None:
        try:
            await context.ari_client.send_command(
                method="DELETE",
                resource=f"channels/{context.caller_channel_id}/moh",
            )
        except Exception:
            logger.debug("Failed to stop MOH after originate failure", call_id=call_id, exc_info=True)

        await self._clear_pending_attended_transfer_state(
            context,
            clear_current_action=True,
            restore_audio_capture=True,
            reason="originate-failed",
        )
