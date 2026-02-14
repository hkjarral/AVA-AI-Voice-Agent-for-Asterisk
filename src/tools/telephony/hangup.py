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
from src.tools.context import ToolExecutionContext
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
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="hangup_call",
            description=(
                "End the current call. Call this when the caller says goodbye or thank you and is ready to hang up. "
                "Set farewell_message to your goodbye sentence."
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
                )
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
            await context.update_session(cleanup_after_tts=True)
            logger.info("✅ Call will hangup after farewell", call_id=context.call_id)
            
            return {
                "status": "success",
                "message": farewell,
                "will_hangup": True
            }
            
        except Exception as e:
            logger.error(f"Error preparing hangup: {e}", exc_info=True)
            return {
                "status": "error",
                "message": "Goodbye!",
                "will_hangup": True,
                "error": str(e)
            }
