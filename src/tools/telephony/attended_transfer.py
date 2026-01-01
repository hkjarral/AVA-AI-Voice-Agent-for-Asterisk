"""
Attended (Warm) Transfer Tool - MOH + agent announcement + DTMF accept/decline.

This tool originates a separate "agent" call leg to a configured destination extension.
The engine then:
  - plays an announcement to the destination agent (TTS via Local AI Server),
  - waits for DTMF acceptance (1=accept, 2=decline),
  - bridges caller <-> destination and removes AI media on accept.
"""

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
                "Use when you must brief a human before connecting the caller."
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
                        "Example: 'support_agent'."
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

        session = await context.get_session()
        call_id = session.call_id

        logger.info(
            "📞 Attended transfer requested",
            call_id=call_id,
            destination_key=destination,
            extension=extension,
            dial_endpoint=dial_endpoint,
        )

        # Start MOH for caller while we dial the agent.
        try:
            await context.ari_client.send_command(
                method="POST",
                resource=f"channels/{context.caller_channel_id}/moh",
                params={"mohClass": moh_class},
            )
        except Exception:
            logger.warning("Failed to start MOH for attended transfer", call_id=call_id, exc_info=True)

        # Record current_action for cancel/engine handlers.
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
        }
        # Disable capture while caller is on MOH (prevents MOH audio feeding STT/providers).
        try:
            session.audio_capture_enabled = False
        except Exception:
            pass
        await context.session_store.upsert_call(session)

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
            # Originate failed: stop MOH and clear action.
            await self._cleanup_failed_originate(context, call_id)
            return {
                "status": "failed",
                "message": f"Unable to place the transfer call to {description}.",
            }

        agent_channel_id = result["id"]
        session = await context.get_session()
        if session.current_action and session.current_action.get("type") == "attended_transfer":
            session.current_action["agent_channel_id"] = agent_channel_id
            await context.session_store.upsert_call(session)

        # Best-effort: register agent channel for DTMF routing and schedule no-answer cleanup.
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

    async def _cleanup_failed_originate(self, context: ToolExecutionContext, call_id: str) -> None:
        try:
            await context.ari_client.send_command(
                method="DELETE",
                resource=f"channels/{context.caller_channel_id}/moh",
            )
        except Exception:
            logger.debug("Failed to stop MOH after originate failure", call_id=call_id, exc_info=True)

        try:
            session = await context.get_session()
            if session.current_action and session.current_action.get("type") == "attended_transfer":
                session.current_action = None
                await context.session_store.upsert_call(session)
        except Exception:
            logger.debug("Failed to clear current_action after originate failure", call_id=call_id, exc_info=True)
