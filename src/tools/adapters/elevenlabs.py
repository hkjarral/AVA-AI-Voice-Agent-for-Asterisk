"""
ElevenLabs Tool Adapter

Handles tool schema conversion and execution for ElevenLabs Conversational AI.
"""
import logging
from typing import Any, Callable, Dict, List, Optional
from src.tools.context import ToolExecutionContext

logger = logging.getLogger(__name__)


class ElevenLabsToolAdapter:
    """
    Adapter for ElevenLabs tool/function calling.
    
    Converts tool registry to ElevenLabs format and handles tool execution.
    """
    
    def __init__(
        self,
        registry: Any,  # ToolRegistry
        execute_callback: Optional[Callable] = None,
    ):
        """
        Initialize the adapter.
        
        Args:
            registry: The tool registry containing tool definitions
            execute_callback: Optional callback for tool execution
        """
        self.registry = registry
        self.execute_callback = execute_callback
    
    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """
        Get tools in ElevenLabs format.
        
        ElevenLabs uses a format similar to OpenAI's function calling:
        {
            "type": "client",  # or "webhook"
            "name": "tool_name",
            "description": "What the tool does",
            "parameters": {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        }
        """
        if not self.registry:
            return []
        
        try:
            return self.registry.to_elevenlabs_schema()
        except AttributeError:
            # Fallback: manually convert from registry
            return self._convert_tools_to_elevenlabs()
    
    def _convert_tools_to_elevenlabs(self) -> List[Dict[str, Any]]:
        """Convert tools from registry to ElevenLabs format."""
        tools = []
        
        try:
            for tool_name, tool in self.registry._tools.items():
                definition = tool.definition
                
                # Use input_schema if available (e.g., MCP tools), otherwise build from parameters
                if isinstance(getattr(definition, 'input_schema', None), dict) and definition.input_schema:
                    # MCP tools and others with raw JSON schema
                    schema = definition.input_schema.copy()
                    if schema.get("type") is None:
                        schema["type"] = "object"
                    parameters = schema
                else:
                    # Built-in tools with ToolParameter list
                    parameters = {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    }
                    for param in definition.parameters:
                        param_schema = {
                            "type": param.type,
                            "description": param.description,
                        }
                        if param.enum:
                            param_schema["enum"] = param.enum
                        parameters["properties"][param.name] = param_schema
                        if param.required:
                            parameters["required"].append(param.name)
                
                # Build ElevenLabs tool schema
                tool_schema = {
                    "type": "client",  # Client-side tool execution
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": parameters
                }
                
                tools.append(tool_schema)
                
        except Exception as e:
            logger.error(f"[elevenlabs_adapter] Error converting tools: {e}")
        
        return tools
    
    async def execute_tool(
        self,
        tool_name: str,
        tool_call_id: str,
        parameters: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a tool and return the result.
        
        Args:
            tool_name: Name of the tool to execute
            tool_call_id: Unique ID for this tool call
            parameters: Tool parameters
            context: Execution context (call_id, session, etc.)
            
        Returns:
            Tool execution result
        """
        if self.execute_callback:
            return await self.execute_callback(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                parameters=parameters,
                context=context,
            )
        
        if not self.registry:
            return {
                "success": False,
                "error": "No tool registry configured",
            }
        
        try:
            tool = self.registry.get_tool(tool_name)
            if not tool:
                return {
                    "success": False,
                    "error": f"Tool '{tool_name}' not found",
                }

            exec_context = context
            if isinstance(context, dict):
                exec_context = ToolExecutionContext(
                    call_id=str(context.get("call_id") or ""),
                    caller_channel_id=context.get("caller_channel_id"),
                    bridge_id=context.get("bridge_id"),
                    caller_number=context.get("caller_number"),
                    caller_name=context.get("caller_name"),
                    called_number=context.get("called_number"),
                    context_name=context.get("context_name"),
                    session_store=context.get("session_store"),
                    ari_client=context.get("ari_client"),
                    config=context.get("config"),
                    provider_name="elevenlabs",
                    user_input=context.get("user_input"),
                )

            if isinstance(exec_context, ToolExecutionContext):
                block_result = await exec_context.get_tool_block_response(tool_name)
                if block_result:
                    return {
                        "success": False,
                        "result": block_result,
                    }
            
            # Execute the tool
            result = await tool.execute(parameters, exec_context)
            return {
                "success": True,
                "result": result,
            }
            
        except Exception as e:
            logger.error(f"[elevenlabs_adapter] Tool execution error: {e}")
            return {
                "success": False,
                "error": str(e),
            }
