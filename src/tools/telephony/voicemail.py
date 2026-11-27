"""Route voicemail only after its announcement and caller audio finish.

The engine owns the deferred action. ARI acceptance is a separate, tri-state
outcome; an uncertain request must never be replayed or reclaim caller ownership.
"""

import asyncio
import re
from uuid import uuid4
from typing import Dict, Any, Optional
import structlog

from ..base import Tool, ToolDefinition, ToolCategory
from ..context import ToolExecutionContext
from ..execution_history import record_in_call_tool_result
from .deferred_transfer import (
    build_deferred_transfer_action, build_deferred_transfer_result,
    store_pending_deferred_transfer,
)

logger = structlog.get_logger(__name__)


class VoicemailTool(Tool):
    """
    Tool for sending callers to voicemail.
    
    Uses ARI continue() to transfer to FreePBX ext-local context
    with vmu{extension} pattern for voicemail.
    """

    @staticmethod
    def resolve_mailbox(config: Dict[str, Any]) -> tuple[str, str]:
        """Return ``(mailbox_key, extension)`` with legacy compatibility.

        Agent-scoped runtime resolution writes ``selected_mailbox_key`` and an
        effective root extension. Global configurations may instead provide a
        default mailbox. A multi-mailbox inventory without an explicit default
        fails closed rather than routing to dictionary insertion order.
        """
        mailboxes = config.get("mailboxes") or {}
        if not isinstance(mailboxes, dict):
            mailboxes = {}

        selected_key = str(config.get("selected_mailbox_key") or "").strip()
        if selected_key:
            selected = mailboxes.get(selected_key) or {}
            if isinstance(selected, dict):
                extension = str(selected.get("extension") or "").strip()
                if extension:
                    return selected_key, extension

            return "", ""  # A deleted selected resource must never widen scope.

        legacy_extension = str(config.get("extension") or "").strip()
        if legacy_extension:
            return "default", legacy_extension

        default_key = str(config.get("default_mailbox_key") or "").strip()
        if default_key:
            default_mailbox = mailboxes.get(default_key) or {}
            if isinstance(default_mailbox, dict):
                extension = str(default_mailbox.get("extension") or "").strip()
                if extension:
                    return default_key, extension

            return "", ""  # An explicitly configured default was deleted.

        valid = [
            (str(key), str(value.get("extension") or "").strip())
            for key, value in mailboxes.items()
            if isinstance(value, dict) and str(value.get("extension") or "").strip()
        ]
        return valid[0] if len(valid) == 1 else ("", "")
    
    @property
    def definition(self) -> ToolDefinition:
        """Return tool definition."""
        return ToolDefinition(
            name="leave_voicemail",
            description="Send the caller to voicemail so they can leave a message",
            category=ToolCategory.TELEPHONY,
            requires_channel=True,
            max_execution_time=15,
            parameters=[]  # No parameters - uses config
        )
    
    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute voicemail transfer.
        
        Args:
            parameters: Empty dict (no parameters)
            context: Tool execution context
        
        Returns:
            Dict with status and message
        """
        # Get voicemail config
        config = context.get_config_value("tools.leave_voicemail")
        if not isinstance(config, dict) or not config:
            logger.warning("Voicemail tool not configured", call_id=context.call_id)
            return {
                "status": "failed",
                "message": "Voicemail is not available",
            }
        if isinstance(config, dict) and config.get("enabled") is False:
            logger.warning("Voicemail tool disabled", call_id=context.call_id)
            return {
                "status": "failed",
                "message": "Voicemail is not available",
            }
        
        _mailbox_key, extension = self.resolve_mailbox(config)
        if not extension or not re.fullmatch(r"[A-Za-z0-9_*#.-]+", extension):
            logger.error(
                "Voicemail mailbox not configured or is ambiguous",
                call_id=context.call_id,
            )
            return {
                "status": "failed",
                "message": "Voicemail is not configured properly"
            }
        
        session = await context.get_session()
        if self._unavailable(session):
            return {"status": "failed", "message": "The call is no longer available for voicemail."}

        validator = getattr(context.ari_client, "dialplan_target_exists", None)
        if callable(validator):
            try:
                exists = await validator(context.caller_channel_id, context="ext-local",
                                         extension=f"vmu{extension}", priority=1)
            except Exception:
                exists = None
            if exists is False:
                return {"status": "failed", "message": "Voicemail destination is not available."}

        # No await separates the ownership recheck from arming the action.
        if (await context.get_session() is not session or self._unavailable(session)):
            return {"status": "failed", "message": "The call is no longer available for voicemail."}
        action = build_deferred_transfer_action(
            source_tool="leave_voicemail", commit_tool="leave_voicemail",
            transfer_type="voicemail", target=extension, description="voicemail",
            dialplan_context="ext-local", payload={"mailbox_key": _mailbox_key},
        )
        await store_pending_deferred_transfer(context, action, session=session)
        return build_deferred_transfer_result(
            action=action, message=action["handoff"]["message"],
            extra={"action": "voicemail_initiated"},
        )

    @staticmethod
    def _unavailable(session: Any, *, action_id: Optional[str] = None) -> bool:
        pending = getattr(session, "pending_deferred_transfer", None)
        return (session.cleanup_in_progress or session.cleanup_completed
                or getattr(session, "transfer_active", False)
                or getattr(session, "transfer_state", None)
                or getattr(session, "transfer_destination", None)
                or ToolExecutionContext.is_pending_attended_transfer(session)
                or (isinstance(pending, dict) and (action_id is None or pending.get("id") != action_id)))

    async def commit_deferred_action(
        self, action: Dict[str, Any], context: ToolExecutionContext,
    ) -> Dict[str, Any]:
        session = await context.get_session()
        pending = getattr(session, "pending_deferred_transfer", None)
        if (not isinstance(pending, dict) or pending.get("id") != action.get("id")
                or self._unavailable(session, action_id=action.get("id"))):
            return {"status": "failed", "message": "The call is no longer available for voicemail."}
        config = context.get_config_value("tools.leave_voicemail") or {}
        if not isinstance(config, dict):
            return {"status": "failed", "message": "Voicemail destination is not available."}
        mailbox_key, extension = self.resolve_mailbox(config)
        if (config.get("enabled") is False or not extension
                or not re.fullmatch(r"[A-Za-z0-9_*#.-]+", extension)
                or action.get("target") != extension
                or (action.get("payload") or {}).get("mailbox_key") != mailbox_key):
            return {"status": "failed", "message": "Voicemail destination is not available."}
        validator = getattr(context.ari_client, "dialplan_target_exists", None)
        if callable(validator):
            try:
                exists = await validator(context.caller_channel_id, context="ext-local",
                                         extension=f"vmu{extension}", priority=1)
            except Exception:
                exists = None
            if exists is False:
                return {"status": "failed", "message": "Voicemail destination is not available."}
        latest = await context.get_session()
        pending = getattr(session, "pending_deferred_transfer", None)
        if (latest is not session or not isinstance(pending, dict)
                or pending.get("id") != action.get("id")
                or self._unavailable(session, action_id=action.get("id"))):
            return {"status": "failed", "message": "The call ended before voicemail started."}
        previous = {key: getattr(session, key, None) for key in
                    ("transfer_active", "transfer_state", "transfer_target")}
        claim = uuid4().hex
        session.voicemail_handoff_id = claim
        session.voicemail_handoff_outcome = "pending"
        session.transfer_active = True
        session.transfer_target = f"Voicemail {extension}"
        session.transfer_state = "voicemail_pending"

        def still_owned():
            return (session.voicemail_handoff_id == claim
                    and getattr(session, "transfer_target", None) == f"Voicemail {extension}"
                    and getattr(session, "transfer_state", None) in
                    {"voicemail_pending", "voicemail", "voicemail_indeterminate"})

        def restore():
            if still_owned():
                for key, value in previous.items():
                    setattr(session, key, value)
                session.voicemail_handoff_outcome = None
                session.voicemail_handoff_id = None

        try:
            handed_off = await context.ari_client.continue_in_dialplan(
                context.caller_channel_id, context="ext-local",
                extension=f"vmu{extension}", priority=1,
            )
        except asyncio.CancelledError:
            if still_owned():
                session.voicemail_handoff_outcome = "indeterminate"
                session.transfer_state = "voicemail_indeterminate"
            self._retire_action(session, action)
            await self._record_outcome(context, action, {
                "status": "failed", "action": "voicemail_indeterminate",
                "handoff_indeterminate": True,
                "message": "Voicemail handoff could not be confirmed. Do not retry the transfer.",
            })
            raise
        except Exception:
            # The request may have reached Asterisk. Never replay or reclaim a
            # potentially handed-off caller after a timeout/transport failure.
            handed_off = None
            logger.warning("Voicemail handoff outcome unavailable", call_id=context.call_id)
        finally:
            if session.voicemail_handoff_id == claim and not still_owned():
                # Another routing action superseded this invocation. Retire only
                # our diagnostic marker, never its ownership flags.
                session.voicemail_handoff_id = None
                session.voicemail_handoff_outcome = None

        if handed_off is False:
            restore()
            return {"status": "failed", "message": "Unable to transfer to voicemail at this time."}
        if handed_off is True:
            if still_owned():
                session.voicemail_handoff_outcome = "confirmed"
                session.transfer_state = "voicemail"
            self._retire_action(session, action)
            result = {"status": "success", "action": "voicemail_completed",
                      "message": "Voicemail handoff accepted."}
            await self._record_outcome(context, action, result)
            return result
        if still_owned():
            session.voicemail_handoff_outcome = "indeterminate"
            session.transfer_state = "voicemail_indeterminate"
        self._retire_action(session, action)
        result = {
            "status": "failed", "action": "voicemail_indeterminate",
            "handoff_indeterminate": True,
            "message": "Voicemail handoff could not be confirmed. Do not retry the transfer; it may already have started.",
        }
        await self._record_outcome(context, action, result)
        return result

    @staticmethod
    def _retire_action(session: Any, action: Dict[str, Any]) -> None:
        pending = getattr(session, "pending_deferred_transfer", None)
        if isinstance(pending, dict) and pending.get("id") == action.get("id"):
            session.pending_deferred_transfer = None

    @staticmethod
    async def _record_outcome(
        context: ToolExecutionContext, action: Dict[str, Any], result: Dict[str, Any],
    ) -> None:
        origin = action.get("_tool_history_origin") or {}
        await record_in_call_tool_result(
            expected_session=context.expected_session,
            session_store=context.session_store, call_id=context.call_id,
            tool_call_id=origin.get("tool_call_id") or action.get("id"),
            tool_name="leave_voicemail", parameters={}, result=result,
        )
