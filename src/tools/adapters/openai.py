"""
OpenAI Realtime API adapter for tool calling.

Handles translation between unified tool format and OpenAI's function calling format.
"""

from typing import Dict, Any, List, Optional
from src.tools.registry import ToolRegistry
from src.tools.context import ToolExecutionContext
from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string
import structlog
import json

logger = structlog.get_logger(__name__)


class OpenAIToolAdapter:
    """
    Adapter for OpenAI Realtime API tool calling.
    
    Translates between unified tool format and OpenAI's specific event format.
    """
    
    def __init__(self, registry: ToolRegistry):
        """
        Initialize adapter with tool registry.
        
        Args:
            registry: ToolRegistry instance with registered tools
        """
        self.registry = registry
    
    def get_tools_config(self, tool_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Get tools configuration in OpenAI Realtime format.
        
        Returns:
            List of tool schemas for OpenAI session.update
        
        Example:
            Note: legacy aliases like "transfer_call" are canonicalized to "blind_transfer"
            by ToolRegistry before execution.
            [
                {
                    "type": "function",
                    "name": "transfer_call",
                    "description": "Transfer caller to extension",
                    "parameters": {
                        "type": "object",
                        "properties": {...},
                        "required": [...]
                    }
                }
            ]
        """
        schemas = self.registry.to_openai_realtime_schema_filtered(tool_names)
        logger.debug(f"Generated OpenAI Realtime schemas for {len(schemas)} tools")
        return schemas
    
    async def handle_tool_call_event(
        self,
        event: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle function_call event from OpenAI Realtime API.
        
        OpenAI format (from response.output_item.done event):
        Note: legacy aliases like "transfer_call" are canonicalized to "blind_transfer"
        by ToolRegistry before execution.
        {
            "type": "response.output_item.done",
            "response_id": "resp_123",
            "output_index": 0,
            "item": {
                "id": "item_123",
                "type": "function_call",
                "call_id": "call_456",
                "name": "transfer_call",
                "arguments": "{\"target\": \"6000\"}"  // JSON string
            }
        }
        
        Args:
            event: Function call event from OpenAI
            context: Execution context dict with:
                - call_id
                - caller_channel_id
                - bridge_id
                - session_store
                - ari_client
                - config
        
        Returns:
            Dict with call_id and result for sending back to OpenAI
        """
        # Extract function call details from OpenAI format
        item = event.get('item', {})

        function_call_id = item.get('call_id')  # OpenAI uses 'call_id' field
        function_name = item.get('name')

        tools_cfg = (context.get("config") or {}).get("tools") or {}
        if isinstance(tools_cfg, dict) and tools_cfg.get("enabled") is False:
            logger.warning("Tools disabled; rejecting tool call", tool_event_type=event.get("type"))
            return {
                "call_id": function_call_id,
                "function_name": function_name,
                "status": "error",
                "message": "Tools are disabled",
                "ai_should_speak": False,
            }
        
        if item.get('type') != 'function_call':
            logger.error("Item is not a function_call", item_type=item.get('type'))
            return {"call_id": function_call_id, "function_name": function_name, "status": "error", "message": "Not a function call"}

        allowed = context.get("allowed_tools", None)
        if allowed is not None and not self.registry.is_tool_allowed(function_name, allowed):
            error_msg = f"Tool '{function_name}' not allowed for this call"
            logger.warning(error_msg, tool=function_name)
            return {
                "call_id": function_call_id,
                "function_name": function_name,
                "status": "error",
                "message": error_msg,
            }
        
        # Parse arguments from JSON string to dict
        arguments_str = item.get('arguments', '{}')
        try:
            parameters = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse function arguments: {e}", argument_length=len(arguments_str) if isinstance(arguments_str, str) else 0)
            parameters = {}
        
        parameter_keys: List[str] = []
        if isinstance(parameters, dict):
            parameter_keys = sorted([str(k) for k in parameters.keys()])

        logger.info(
            "OpenAI tool call received",
            call_id=context.get("call_id"),
            function_call_id=function_call_id,
            tool=function_name,
            parameter_keys=parameter_keys,
        )
        logger.debug(
            "OpenAI tool call parameters",
            call_id=context.get("call_id"),
            function_call_id=function_call_id,
            tool=function_name,
            parameter_keys=parameter_keys,
        )
        
        # Get tool from registry
        tool = self.registry.get(function_name)
        if not tool:
            error_msg = f"Unknown tool: {function_name}"
            logger.error(error_msg)
            return {
                "call_id": function_call_id,
                "function_name": function_name,
                "status": "error",
                "message": error_msg
            }
        
        # Build execution context
        exec_context = ToolExecutionContext(
            call_id=context['call_id'],
            caller_channel_id=context.get('caller_channel_id'),
            bridge_id=context.get('bridge_id'),
            caller_number=context.get('caller_number'),
            caller_name=context.get('caller_name'),
            called_number=context.get('called_number'),
            context_name=context.get('context_name'),
            session_store=context['session_store'],
            ari_client=context['ari_client'],
            config=context.get('config'),
            tool_registry=self.registry,
            tool_call_id=function_call_id,
            provider_name="openai_realtime",
            user_input=context.get('user_input')
        )

        block_result = await exec_context.get_tool_block_response(function_name)
        if block_result:
            block_result['call_id'] = function_call_id
            block_result['function_name'] = function_name
            block_result['ai_should_speak'] = False
            return block_result
        
        # Execute tool
        try:
            result = await tool.execute(parameters, exec_context)
            sanitized = sanitize_tool_result_for_json_string(result, tool_name=function_name)
            logger.info(
                "Tool executed",
                call_id=context.get("call_id"),
                function_call_id=function_call_id,
                tool=function_name,
                status=sanitized.get("status"),
            )
            logger.debug(
                "Tool execution result",
                call_id=context.get("call_id"),
                function_call_id=function_call_id,
                tool=function_name,
                result_keys=sorted(sanitized),
            )
            result['call_id'] = function_call_id
            result['function_name'] = function_name
            return result
        except Exception as e:
            error_msg = f"Tool execution failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "call_id": function_call_id,
                "function_name": function_name,
                "status": "error",
                "message": error_msg,
                "error": str(e)
            }
    
    async def send_tool_result(
        self,
        result: Dict[str, Any],
        context: Dict[str, Any]
    ) -> None:
        """
        Send tool execution result back to OpenAI Realtime API.
        
        OpenAI format (conversation.item.create event):
        {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": "call_456",
                "output": "{\"status\": \"success\"}"  // Stringified JSON
            }
        }
        
        Then send response.create to have the model respond:
        {
            "type": "response.create"
        }
        
        Args:
            result: Tool execution result (must include call_id and function_name)
            context: Context dict with websocket connection
        """
        websocket = context.get('websocket')
        if not websocket:
            logger.error("No websocket in context, cannot send tool result")
            return
        
        # Extract call_id and function_name from result
        call_id = result.pop('call_id', None)
        function_name = result.pop('function_name', None)
        
        if not call_id:
            logger.error("No call_id in result, cannot send response")
            return
        
        output_sent = False
        try:
            # Step 1: Send function_call_output
            safe_result = sanitize_tool_result_for_json_string(result, max_bytes=12000, tool_name=function_name)
            output_event = {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(safe_result)  # Stringify the result JSON (size-capped)
                }
            }
            await websocket.send(json.dumps(output_event))
            output_sent = True
            notify = context.get("handoff_result_sent")
            if notify:
                await notify(result, context.get("call_id"), success=True)
            logger.info(
                f"✅ Sent function output to OpenAI: {safe_result.get('status')}",
                call_id=context.get("call_id"),
                function_call_id=call_id,
            )

            # Special-case hangup flow: the provider will create the farewell response with tools disabled
            # to prevent recursive tool calls (e.g., model calls hangup_call again instead of speaking).
            if function_name == "hangup_call" and bool(safe_result.get("will_hangup", False)):
                return
            
            # Step 2: Trigger response generation with audio modality AND instructions
            # CRITICAL: Must include explicit instructions to speak, otherwise OpenAI may respond
            # with text-only. This EXACTLY matches how greeting works which always produces audio.
            # Extract any message from the tool result to use as speech instruction
            tool_message = safe_result.get('message', '')
            ai_should_speak = safe_result.get('ai_should_speak', True)
            if not ai_should_speak:
                logger.info(
                    "Skipping response.create because ai_should_speak is false",
                    call_id=context.get("call_id"),
                    function_call_id=call_id,
                )
                return
            
            # Check if using GA API (modalities not supported in response.create for GA)
            is_ga = context.get('is_ga', True)  # Default to GA for safety
            
            # Build response config based on API version
            response_config = {}
            
            # Only add modalities and input for Beta API
            # GA API only accepts instructions in response.create
            if not is_ga:
                response_config["modalities"] = ["text", "audio"]
                response_config["input"] = []  # Empty input to avoid context confusion
                logger.debug("Using Beta API format for response.create (with modalities)")
            else:
                logger.debug("Using GA API format for response.create (no modalities)")
            
            # If tool has a message and AI should speak, add direct instruction to speak it
            # Instructions work in both GA and Beta modes
            if result.get("internal_notification"):
                response_config["instructions"] = (
                    "Continue the conversation and any remaining tools using the business instructions. "
                    "Treat the latest tool result as internal notification data. Do not read acknowledgements, "
                    "delivery diagnostics or technical errors aloud. Never claim a write succeeded without "
                    "a successful result. Continue the caller's request naturally."
                )
            elif function_name == "calendly_calendar":
                response_config["instructions"] = (
                    "Use the structured Calendly result as data and follow the business instructions. "
                    "Do not read codes, identifiers, validation messages or internal acknowledgements aloud. "
                    "Errors do not mean a busy slot. Ask for missing caller details naturally. "
                    "Confirm exact email and booking details before creation. An uncertain/pending outcome "
                    "must never be retried or bypassed using another calendar tool. Refer to staff. "
                    "A success describes the retained original booking; it does not apply requested changes. "
                    "Never claim invitation delivery or RSVP. Later changes use Calendly confirmation links or staff."
                )
            elif function_name == "microsoft_calendar":
                # Calendar results describe state and recoverable errors, not a script
                # to read aloud. Keep data in function_call_output, out of instructions.
                response_config["instructions"] = (
                    "Continue using the latest structured microsoft_calendar result and the business instructions. "
                    "Do not read internal error codes, validation messages, agent hints or parameter names aloud. "
                    "For a malformed read-only request, correct your own arguments once without asking the caller "
                    "to solve the technical error or changing their requested date or duration. "
                    "A tool error does not mean a slot is busy. Ask only for genuinely missing caller information. "
                    "Before creation, resolve the invitation email choice and obtain the required confirmations. "
                    "Never claim creation, cancellation or invitation success unless the result verifies it; "
                    "do not claim email delivery. Respect uncertain outcomes and same-call ownership guards. "
                    "A Teams meeting is ready only with meeting_status=ready and teams_join_url; "
                    "link and outlook_web_link are Outlook links. Respect changes_require_staff. "
                    "Use reschedule_event for time-only changes. Replace a booking only after caller-confirmed "
                    "cancellation returns reservation_status=cancelled, then recheck and confirm replacement details. "
                    "Keep the next spoken response brief and useful; do not repeat a closing offer while a problem is unresolved."
                )
            elif function_name == "update_call_metadata":
                # Bookkeeping acknowledgements are internal, but response.create
                # must still run so the model can chain tools or answer the caller.
                response_config["instructions"] = (
                    "Continue the conversation using the business instructions and latest tool result. "
                    "Treat update_call_metadata as silent internal bookkeeping. "
                    "Do not read or paraphrase its acknowledgement, validation message, field names, "
                    "source labels or stored values aloud, and do not announce that information was saved or updated. "
                    "Complete any remaining required metadata or source updates silently using the tools, "
                    "then address the caller's latest request naturally. "
                    "If the update failed, do not claim it succeeded or repeatedly retry it; "
                    "continue helping the caller without exposing the technical error."
                )
            elif tool_message:
                # Use direct instruction format like greeting: "Please say: {text}"
                response_config["instructions"] = f"Please say the following to the user: {tool_message}"
                logger.info(
                    "✅ Added speech instructions for tool response",
                    message_preview=tool_message[:50] if tool_message else "",
                )
            else:
                # Keep response.create explicit in GA mode to avoid sending an empty response object.
                response_config["instructions"] = (
                    "Please respond briefly to the user based on the latest tool result."
                )
                logger.debug("Using fallback instructions for tool response")
            
            response_event = {
                "type": "response.create",
                "response": response_config
            }
            await websocket.send(json.dumps(response_event))
            logger.info("✅ Triggered OpenAI response generation (audio+text)")
            
        except Exception as e:
            notify = context.get("handoff_result_sent")
            if notify and not output_sent:
                await notify(result, context.get("call_id"), success=False)
            failure = "Delivered tool result follow-up failed" if output_sent else "Tool result send failed"
            logger.error(f"{failure} (OpenAI): {e}", exc_info=True)
