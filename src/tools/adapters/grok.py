"""
xAI Grok Voice Agent API adapter for tool calling.

Translates between the unified tool registry and Grok's function-calling event
shape. The function-tool JSON schema is identical to OpenAI's
``{"type": "function", "name", "description", "parameters"}``, but the tool-call
EVENT shape differs: Grok emits ``response.function_call_arguments.done`` with
``name``, ``call_id``, and ``arguments`` at the TOP level (not nested under
``item``). Result is sent back via the same ``conversation.item.create`` +
``response.create`` flow.

# SYNC-WITH-OPENAI-REALTIME: handle_tool_call_event diverges from OpenAIToolAdapter
# because xAI's event shape lacks the ``item`` wrapper.
"""

from typing import Dict, Any, List, Optional
from src.tools.registry import ToolRegistry
from src.tools.context import ToolExecutionContext
from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string
import structlog
import json

logger = structlog.get_logger(__name__)


class GrokToolAdapter:
    """Adapter for xAI Grok Voice Agent API tool calling."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def get_tools_config(self, tool_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Get function-tool schemas in Grok's expected format.

        Returns the same shape as OpenAI Realtime (Grok claims wire compatibility for
        function tools): ``{"type": "function", "name", "description", "parameters"}``.
        Reuses the existing registry method.
        """
        schemas = self.registry.to_openai_realtime_schema_filtered(tool_names)
        logger.debug(f"Generated Grok schemas for {len(schemas)} tools")
        return schemas

    async def handle_tool_call_event(
        self,
        event: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle a ``response.function_call_arguments.done`` event from Grok.

        Grok event shape (flat — no ``item`` wrapper):
            {
                "type": "response.function_call_arguments.done",
                "name": "function_name",
                "call_id": "call_123",
                "arguments": "{...JSON string...}"
            }
        """
        function_call_id = event.get("call_id")
        function_name = event.get("name")

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

        arguments_str = event.get("arguments", "{}")
        try:
            parameters = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Grok function arguments: {e}", arguments=arguments_str)
            parameters = {}

        parameter_keys: List[str] = []
        if isinstance(parameters, dict):
            parameter_keys = sorted([str(k) for k in parameters.keys()])

        logger.info(
            "Grok tool call received",
            call_id=context.get("call_id"),
            function_call_id=function_call_id,
            tool=function_name,
            parameter_keys=parameter_keys,
        )
        logger.debug(
            "Grok tool call parameters",
            call_id=context.get("call_id"),
            function_call_id=function_call_id,
            tool=function_name,
            parameters=parameters,
        )

        tool = self.registry.get(function_name)
        if not tool:
            error_msg = f"Unknown tool: {function_name}"
            logger.error(error_msg)
            return {
                "call_id": function_call_id,
                "function_name": function_name,
                "status": "error",
                "message": error_msg,
            }

        exec_context = ToolExecutionContext(
            call_id=context['call_id'],
            caller_channel_id=context.get('caller_channel_id'),
            bridge_id=context.get('bridge_id'),
            called_number=context.get('called_number'),
            context_name=context.get('context_name'),
            session_store=context['session_store'],
            ari_client=context['ari_client'],
            config=context.get('config'),
            provider_name="grok",
            user_input=context.get('user_input'),
        )

        block_result = await exec_context.get_tool_block_response(function_name)
        if block_result:
            block_result['call_id'] = function_call_id
            block_result['function_name'] = function_name
            block_result['ai_should_speak'] = False
            return block_result

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
                "error": str(e),
            }

    async def send_tool_result(
        self,
        result: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        """Send tool execution result back to Grok and trigger a response.

        Wire shape is identical to OpenAI Realtime: ``conversation.item.create``
        with ``function_call_output``, followed by ``response.create``.
        """
        websocket = context.get('websocket')
        if not websocket:
            logger.error("No websocket in context, cannot send tool result")
            return

        call_id = result.pop('call_id', None)
        function_name = result.pop('function_name', None)

        if not call_id:
            logger.error("No call_id in result, cannot send response")
            return

        try:
            safe_result = sanitize_tool_result_for_json_string(result, max_bytes=12000)
            output_event = {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(safe_result),
                },
            }
            await websocket.send(json.dumps(output_event))
            logger.info(
                f"✅ Sent function output to Grok: {safe_result.get('status')}",
                call_id=context.get("call_id"),
                function_call_id=call_id,
            )

            if function_name == "hangup_call" and bool(safe_result.get("will_hangup", False)):
                return

            ai_should_speak = safe_result.get('ai_should_speak', True)
            if not ai_should_speak:
                logger.info(
                    "Skipping response.create because ai_should_speak is false",
                    call_id=context.get("call_id"),
                    function_call_id=call_id,
                )
                return

            tool_message = safe_result.get('message', '')
            response_config: Dict[str, Any] = {}
            if tool_message:
                response_config["instructions"] = (
                    f"Please say the following to the user: {tool_message}"
                )
                logger.info(
                    "✅ Added speech instructions for tool response",
                    message_preview=tool_message[:50] if tool_message else "",
                )
            else:
                response_config["instructions"] = (
                    "Please respond briefly to the user based on the latest tool result."
                )

            response_event = {
                "type": "response.create",
                "response": response_config,
            }
            await websocket.send(json.dumps(response_event))
            logger.info("✅ Triggered Grok response generation (audio+text)")

        except Exception as e:
            logger.error(f"Failed to send tool result to Grok: {e}", exc_info=True)
