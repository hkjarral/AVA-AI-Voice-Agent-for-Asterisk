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

from typing import Dict, Any
import structlog

from ..base import Tool, ToolDefinition, ToolParameter, ToolCategory
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
        
        mailbox_key, extension = self.resolve_mailbox(config)
        if not extension:
            logger.error(
                "Voicemail mailbox not configured or is ambiguous",
                call_id=context.call_id,
            )
            return {
                "status": "failed",
                "message": "Voicemail is not configured properly"
            }
        
        logger.info(
            "Voicemail transfer requested",
            call_id=context.call_id,
            mailbox_key=mailbox_key,
            extension=extension
        )
        
        # Set transfer_active flag BEFORE calling continue
        # This prevents cleanup from hanging up the caller channel
        await context.update_session(
            transfer_active=True,
            transfer_target=f"Voicemail {extension}"
        )
        
        # CRITICAL: Wait briefly to allow AI audio to clear the RTP channel
        # Without this delay, the channel leaves Stasis while AI is still streaming,
        # causing the voicemail greeting to be blocked until caller speaks
        import asyncio
        await asyncio.sleep(0.8)  # Wait 800ms for Deepgram to finish speaking
        
        try:
            # Transfer to FreePBX voicemail context using continue
            # Pattern: ext-local,vmu{extension},1
            asterisk_context = "ext-local"
            asterisk_extension = f"vmu{extension}"
            
            logger.info(
                "Voicemail transfer initiated",
                call_id=context.call_id,
                context=asterisk_context,
                extension=asterisk_extension
            )
            
            # Use continue to leave Stasis and enter dialplan
            await context.ari_client.send_command(
                method="POST",
                resource=f"channels/{context.caller_channel_id}/continue",
                params={
                    "context": asterisk_context,
                    "extension": asterisk_extension,
                    "priority": 1
                }
            )
            
            logger.info(
                "Voicemail transfer executed",
                call_id=context.call_id,
                extension=extension
            )
            
            # Return a question to prompt caller response
            # This triggers voice activity needed for VoiceMail app to play greeting
            return {
                "status": "success",
                "message": "Are you ready to leave a message now?"
            }
            
        except Exception as e:
            logger.error(
                "Voicemail transfer failed",
                call_id=context.call_id,
                error=str(e),
                exc_info=True
            )
            
            # Clear transfer flag on failure
            await context.update_session(
                transfer_active=False,
                transfer_target=None
            )
            
            return {
                "status": "failed",
                "message": "Unable to transfer to voicemail at this time"
            }
