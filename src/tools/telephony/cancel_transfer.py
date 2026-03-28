"""
Cancel Transfer Tool - Cancel an in-progress transfer.

Allows caller to cancel a transfer while it's ringing.
"""

from typing import Dict, Any
from src.tools.base import Tool, ToolDefinition, ToolParameter, ToolCategory
from src.tools.context import ToolExecutionContext
import structlog

logger = structlog.get_logger(__name__)


class CancelTransferTool(Tool):
    """
    Cancel an in-progress transfer.
    
    Can cancel while target is ringing (before answer).
    Cannot cancel after target has answered (transfer complete).
    """
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="cancel_transfer",
            description="Cancel the current transfer if it hasn't been answered yet. Use when caller changes their mind.",
            category=ToolCategory.TELEPHONY,
            requires_channel=True,
            max_execution_time=5,
            parameters=[]
        )
    
    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Cancel the current transfer.
        
        Args:
            parameters: No parameters needed
            context: Tool execution context
        
        Returns:
            {
                status: "success" | "error" | "no_transfer",
                message: "Human-readable message"
            }
        """
        logger.info("🚫 Cancel transfer requested", call_id=context.call_id)
        
        try:
            session = await context.get_session()
            if not session:
                return {
                    "status": "error",
                    "message": "Session not found"
                }
            
            # Check if there's an active transfer
            if not session.current_action or session.current_action.get('type') not in {'transfer', 'attended_transfer'}:
                return {
                    "status": "no_transfer",
                    "message": "There's no transfer in progress to cancel."
                }
            
            action = session.current_action
            channel_id = action.get('channel_id') or action.get('agent_channel_id')
            engine = getattr(context.ari_client, "engine", None)
            
            # Check if transfer was answered
            # (If we're here and it was answered, the engine would have already
            # cleared current_action, so this check is safety)
            if action.get('answered', False):
                return {
                    "status": "error",
                    "message": "The transfer has already been connected. I cannot cancel it now."
                }
            
            # Hangup the transfer channel if it exists
            if channel_id:
                try:
                    # If this is an attended transfer agent leg, unregister the in-memory mapping
                    # BEFORE hanging up so the agent leg teardown doesn't trigger full call cleanup.
                    if action.get("type") == "attended_transfer" and engine and hasattr(engine, "_unregister_attended_transfer_agent_channel"):
                        engine._unregister_attended_transfer_agent_channel(channel_id)
                except Exception:
                    pass
                try:
                    await context.ari_client.hangup_channel(channel_id)
                    logger.info(f"Hung up transfer channel: {channel_id}")
                except Exception as e:
                    logger.warning(f"Failed to hangup transfer channel: {e}")
            
            # Stop MOH on caller
            try:
                await context.ari_client.send_command(
                    method="DELETE",
                    resource=f"channels/{context.caller_channel_id}/moh"
                )
                logger.info("MOH stopped on caller")
            except Exception as e:
                logger.warning(f"Failed to stop MOH: {e}")
            
            # Clear the action from session
            session.current_action = None
            session.transfer_context = None
            try:
                session.audio_capture_enabled = True
            except Exception:
                pass
            await context.session_store.upsert_call(session)
            try:
                if engine and hasattr(engine, "_cancel_attended_transfer_screening"):
                    engine._cancel_attended_transfer_screening(context.call_id, reason="cancel-transfer")
            except Exception:
                logger.warning(
                    "Failed to cancel attended-transfer screening hook",
                    call_id=context.call_id,
                    exc_info=True,
                )
            
            logger.info("✅ Transfer cancelled", call_id=context.call_id)
            
            return {
                "status": "success",
                "message": "Transfer cancelled. How else can I help you?"
            }
            
        except Exception as e:
            logger.error(f"Error cancelling transfer: {e}", exc_info=True)
            return {
                "status": "error",
                "message": "I encountered an error cancelling the transfer.",
                "error": str(e)
            }
