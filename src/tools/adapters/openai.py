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
            logger.error(f"Failed to parse function arguments: {e}", arguments=arguments_str)
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
            parameters=parameters,
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
            called_number=context.get('called_number'),
            session_store=context['session_store'],
            ari_client=context['ari_client'],
            config=context.get('config'),
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
            sanitized = sanitize_tool_result_for_json_string(result)
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
                result=sanitized,
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
        
        try:
            # Step 1: Send function_call_output
            safe_result = sanitize_tool_result_for_json_string(result, max_bytes=12000)
            output_event = {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(safe_result)  # Stringify the result JSON (size-capped)
                }
            }
            await websocket.send(json.dumps(output_event))
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
            if tool_message:
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
            logger.error(f"Failed to send tool result to OpenAI: {e}", exc_info=True)
