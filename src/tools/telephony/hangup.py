"""
Hangup Call Tool - End the current call.

Allows full AI agents to end calls when appropriate (e.g., after goodbye).

Simplified design (v5.0):
- Trust the AI to manage conversation flow via system prompt
- No complex guardrails that cause race conditions
- Just: farewell message → mark for cleanup → hangup after audio
"""

from typing import Dict, Any
from src.tools.base import Tool, ToolDefinition, ToolParameter, ToolCategory
from src.tools.context import ToolExecutionContext, StaleToolContextError
import structlog

logger = structlog.get_logger(__name__)


class HangupCallTool(Tool):
    """
    End the current call.
    
    Use when:
    - Caller says goodbye/thank you/that's all
    - Call purpose is complete
    - Caller explicitly asks to end the call
    
    Only available to full agents (not partial/assistant agents).
    """
    
    def __init__(self, completion=None):
        self.completion = completion or {}

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="hangup_call",
            description=(
                "End the current call. Call this when the caller says goodbye or thank you and is ready to hang up. "
                "Set farewell_message to your final sentence. next_step selects an approved destination after the farewell; "
                "use hangup when the caller declines a survey or wants to disconnect. Omission uses the configured default."
            ),
            category=ToolCategory.TELEPHONY,
            requires_channel=True,
            max_execution_time=5,
            parameters=[
                ToolParameter(
                    name="farewell_message",
                    type="string",
                    description="Farewell message to speak before hanging up. Should be warm and professional.",
                    required=False
                ),
                ToolParameter(name="next_step", type="string", required=False,
                    description="After the farewell: hangup or an approved destination key. Offer opt-in surveys first; select them only after acceptance.",
                    enum=["hangup", *self.completion.get("destination_keys", [])])
            ]
        )
    
    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        End the call.
        
        Simplified v5.0 design:
        - Get farewell message (from parameter or config default)
        - Mark session for cleanup after TTS
        - Return success with will_hangup flag
        
        The AI manages transcript offers via system prompt - no guardrails needed.
        
        Args:
            parameters: {farewell_message: Optional[str]}
            context: Tool execution context
        
        Returns:
            {
                status: "success" | "error",
                message: "Farewell message",
                will_hangup: true
            }
        """
        farewell = parameters.get('farewell_message')
        
        if not farewell:
            farewell = context.get_config_value(
                'tools.hangup_call.farewell_message',
                "Thank you for calling. Goodbye!"
            )
        
        logger.info("📞 Hangup requested", 
                   call_id=context.call_id,
                   farewell=farewell)
        
        try:
            # Mark the session so the engine will hang up after the farewell audio finishes.
            from .completion import resolve_completion
            session = await context.get_session()
            selected = resolve_completion(context.config or {}, parameters.get("next_step"), supplied="next_step" in parameters)
            if getattr(session, "is_outbound", False) or getattr(session, "external_platform", None):
                selected = {**selected, "next_step": "hangup", "result": "ineligible"}
            # First accepted terminal request wins, including a deliberate decline.
            from src.core.session_store import SessionStore
            if isinstance(context.session_store, SessionStore):
                if not await context.session_store.arm_completion(session, selected):
                    return {"status": "error", "message": "The call is no longer available for completion."}
            else:
                if not getattr(session, "completion_action", None):
                    session.completion_action = selected
                await context.update_session(cleanup_after_tts=True)
            logger.info("✅ Call will hangup after farewell", call_id=context.call_id)
            
            return {
                "status": "success",
                "message": farewell,
                # Canonical terminal-audio field for provider adapters. Keep
                # `message` for backwards compatibility with generic tools.
                "farewell_message": farewell,
                "will_hangup": True,
                "next_step": session.completion_action["next_step"],
            }
            
        except StaleToolContextError:
            return {"status": "error", "error_code": "stale_conversation",
                    "message": "This conversation has ended.", "will_hangup": False}
        except Exception as e:
            logger.error(f"Error preparing hangup: {e}", exc_info=True)
            return {
                "status": "error",
                "message": "Goodbye!",
                "farewell_message": "Goodbye!",
                "will_hangup": True,
                "error": str(e)
            }
