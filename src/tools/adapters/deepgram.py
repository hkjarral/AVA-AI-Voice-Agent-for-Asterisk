"""
Deepgram Voice Agent adapter for tool calling.

Handles translation between unified tool format and Deepgram's function calling format.
"""

from typing import Dict, Any, List, Optional
from src.tools.registry import ToolRegistry
from src.tools.context import ToolExecutionContext
from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string
import structlog
import json

logger = structlog.get_logger(__name__)


class DeepgramToolAdapter:
    """
    Adapter for Deepgram Voice Agent API tool calling.
    
    Translates between unified tool format and Deepgram's specific event format.
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
        Get tools configuration in Deepgram format.
        
        Returns:
            List of tool schemas for Deepgram session initialization
        
        Example:
            [
                {
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
        schemas = self.registry.to_deepgram_schema_filtered(tool_names)
        logger.debug(f"Generated Deepgram schemas for {len(schemas)} tools")
        return schemas
    
    async def handle_tool_call_event(
        self,
        event: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle function call event from Deepgram.
        
        Actual Deepgram format:
        {
            "type": "FunctionCallRequest",
            "functions": [
                {
                    "id": "call_123456",
                    "name": "transfer_call",
                    "arguments": "{\"target\": \"2765\"}",  # JSON string!
                    "client_side": true
                }
            ]
        }
        
        Args:
            event: Function call event from Deepgram
            context: Execution context dict with:
                - call_id
                - caller_channel_id
                - bridge_id
                - session_store
                - ari_client
                - config
        
        Returns:
            Dict with function_call_id and result for sending back to Deepgram
        """
        tools_cfg = (context.get("config") or {}).get("tools") or {}
        if isinstance(tools_cfg, dict) and tools_cfg.get("enabled") is False:
            logger.warning("Tools disabled; rejecting tool call", tool_event_type=event.get("type"))
            return {"status": "error", "message": "Tools are disabled"}

        # Extract function call details from actual Deepgram format
        functions = event.get('functions', [])
        if not functions:
            logger.error("No functions in FunctionCallRequest event")
            return {"status": "error", "message": "No functions in event"}
        
        # Get first function (Deepgram sends array but we process one at a time)
        func = functions[0]
        function_call_id = func.get('id')
        function_name = func.get('name')

        allowed = context.get("allowed_tools", None)
        if allowed is not None and function_name not in allowed:
            error_msg = f"Tool '{function_name}' not allowed for this call"
            logger.warning(error_msg, tool=function_name)
            return {
                "function_call_id": function_call_id,
                "function_name": function_name,
                "status": "error",
                "message": error_msg,
            }
        
        # Parse arguments from JSON string to dict
        arguments_str = func.get('arguments', '{}')
        try:
            parameters = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse function arguments: {e}", arguments=arguments_str)
            parameters = {}
        
        logger.info(f"🔧 Deepgram tool call: {function_name}({parameters})", call_id=function_call_id)
        
        # Get tool from registry
        tool = self.registry.get(function_name)
        if not tool:
            error_msg = f"Unknown tool: {function_name}"
            logger.error(error_msg)
            return {
                "function_call_id": function_call_id,
                "status": "error",
                "message": error_msg
            }
        
        # Build execution context
        exec_context = ToolExecutionContext(
            call_id=context['call_id'],
            caller_channel_id=context.get('caller_channel_id'),
            bridge_id=context.get('bridge_id'),
            session_store=context['session_store'],
            ari_client=context['ari_client'],
            config=context.get('config'),
            provider_name="deepgram",
            user_input=context.get('user_input')
        )
        
        # Execute tool
        try:
            result = await tool.execute(parameters, exec_context)
            logger.info(f"✅ Tool {function_name} executed: {result.get('status')}", function_call_id=function_call_id)
            result['function_call_id'] = function_call_id
            result['function_name'] = function_name  # Pass function name for response
            return result
        except Exception as e:
            error_msg = f"Tool execution failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "function_call_id": function_call_id,
                "function_name": function_name,  # Include function name in error too
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
        Send tool execution result back to Deepgram.
        
        Actual Deepgram format (per official docs):
        {
            "type": "FunctionCallResponse",
            "id": "call_123456",
            "name": "transfer_call",
            "content": "{\"status\": \"success\"}"  // Stringified JSON!
        }
        
        Args:
            result: Tool execution result (must include function_call_id and function_name)
            context: Context dict with websocket connection
        """
        websocket = context.get('websocket')
        if not websocket:
            logger.error("No websocket in context, cannot send tool result")
            return
        
        # Extract function_call_id and function_name from result
        function_call_id = result.pop('function_call_id', None)
        function_name = result.pop('function_name', None)
        
        if not function_call_id:
            logger.error("No function_call_id in result, cannot send response")
            return
        
        if not function_name:
            logger.error("No function_name in result, cannot send response")
            return
        
        # Build response per actual Deepgram spec
        safe_result = sanitize_tool_result_for_json_string(result, max_bytes=12000)
        response = {
            "type": "FunctionCallResponse",
            "id": function_call_id,
            "name": function_name,
            "content": json.dumps(safe_result)  # Stringify JSON (size-capped)
        }
        
        try:
            await websocket.send(json.dumps(response))
            logger.info(f"✅ Sent tool result to Deepgram: {safe_result.get('status')}", function_call_id=function_call_id)
        except Exception as e:
            logger.error(f"Failed to send tool result to Deepgram: {e}", exc_info=True)
