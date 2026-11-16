"""
Voicemail Tool - Route calls to voicemail.

This tool allows the AI to send callers to voicemail when requested.

IMPORTANT BEHAVIOR:
The FreePBX VoiceMail application requires bidirectional RTP and voice activity
before it begins playing the voicemail greeting. When a channel enters the 
VoiceMail application directly from Stasis (via continue), there can be a 
5-8 second delay before the greeting plays.

WORKAROUND:
The tool returns a message that asks the caller a question ("Are you ready to 
leave a message now?"). When the caller responds ("yes", "ok", etc.), it 
triggers voice activity detection and establishes the bidirectional RTP path,
allowing the voicemail greeting to play immediately.

Without this interaction, the VoiceMail app stalls and only begins after the
caller speaks or after an 8-second timeout.

Timeline Evidence (Call 1763009524.4793):
- 04:52:48.275 - continue() called
- 04:52:48.280 - Stasis ended (5ms)
- 21:52:48.xxx - VoiceMail app launched
- 21:52:48-56  - Channel setting write format (waiting for audio path)
- 21:52:56.xxx - Caller said "ok" → greeting played immediately
- Total delay: ~8 seconds until caller spoke
"""

import asyncio
import re
from uuid import uuid4
from typing import Dict, Any
import structlog

from ..base import Tool, ToolDefinition, ToolCategory
from ..context import ToolExecutionContext

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
        if not config:
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
        if (session.cleanup_in_progress or session.cleanup_completed
                or getattr(session, "transfer_active", False)
                or getattr(session, "transfer_state", None)
                or getattr(session, "transfer_destination", None)
                or context.is_pending_attended_transfer(session)
                or isinstance(getattr(session, "pending_deferred_transfer", None), dict)):
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

        # Recheck after discovery, then claim before the first side-effect await.
        if (await context.get_session() is not session or session.cleanup_in_progress
                or getattr(session, "transfer_active", False)
                or getattr(session, "transfer_state", None)
                or context.is_pending_attended_transfer(session)
                or isinstance(getattr(session, "pending_deferred_transfer", None), dict)):
            return {"status": "failed", "message": "The call is no longer available for voicemail."}
        previous = {key: getattr(session, key, None) for key in
                    ("transfer_active", "transfer_state", "transfer_target")}
        claim = uuid4().hex
        session.voicemail_handoff_id = claim
        session.voicemail_handoff_outcome = "pending"
        session.transfer_active = True
        session.transfer_target = f"Voicemail {extension}"
        session.transfer_state = "voicemail_pending"
        request_started = False

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
            # Allow the existing caller-facing announcement to finish before
            # leaving Stasis. Cancellation here has made no external request.
            await asyncio.sleep(0.8)
            latest = await context.get_session()
            if (latest is not session or not still_owned() or session.cleanup_in_progress
                    or session.cleanup_completed):
                restore()
                return {"status": "failed", "message": "The call ended before voicemail started."}
            request_started = True
            handed_off = await context.ari_client.continue_in_dialplan(
                context.caller_channel_id, context="ext-local",
                extension=f"vmu{extension}", priority=1,
            )
        except asyncio.CancelledError:
            if request_started and still_owned():
                session.voicemail_handoff_outcome = "indeterminate"
                session.transfer_state = "voicemail_indeterminate"
            elif not request_started:
                restore()
            raise
        except Exception:
            if not request_started:
                restore()
                return {"status": "failed", "message": "The call ended before voicemail started."}
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
            return {"status": "success", "message": "Are you ready to leave a message now?"}
        if still_owned():
            session.voicemail_handoff_outcome = "indeterminate"
            session.transfer_state = "voicemail_indeterminate"
        return {
            "status": "failed",
            "handoff_indeterminate": True,
            "message": "Voicemail handoff could not be confirmed. Do not retry the transfer; it may already have started.",
        }
