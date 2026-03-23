import pytest


class _NoopTool:
    def __init__(self, definition):
        self._definition = definition

    @property
    def definition(self):
        return self._definition

    async def execute(self, parameters, context):
        return {"status": "success", "message": "ok"}


class _BlockedIfExecutedTool(_NoopTool):
    async def execute(self, parameters, context):
        raise AssertionError("Tool execution should be blocked during pending attended transfer")


class _PendingTransferSession:
    def __init__(self):
        self.current_action = {"type": "attended_transfer"}


class _PendingTransferSessionStore:
    async def get_by_call_id(self, _call_id):
        return _PendingTransferSession()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openai_adapter_rejects_disallowed_tool():
    from src.tools.base import ToolDefinition, ToolCategory
    from src.tools.registry import tool_registry
    from src.tools.adapters.openai import OpenAIToolAdapter

    tool_registry.clear()
    tool_registry.register_instance(
        _NoopTool(ToolDefinition(name="allowed_tool", description="x", category=ToolCategory.BUSINESS))
    )

    adapter = OpenAIToolAdapter(tool_registry)

    event = {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "call_id": "call_1",
            "name": "allowed_tool",
            "arguments": "{}",
        },
    }

    context = {
        "call_id": "c1",
        "session_store": object(),
        "ari_client": object(),
        "config": {"tools": {"enabled": True}},
        "allowed_tools": ["some_other_tool"],
    }

    result = await adapter.handle_tool_call_event(event, context)
    assert result["status"] == "error"

    tool_registry.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deepgram_adapter_rejects_when_tools_disabled():
    from src.tools.base import ToolDefinition, ToolCategory
    from src.tools.registry import tool_registry
    from src.tools.adapters.deepgram import DeepgramToolAdapter

    tool_registry.clear()
    tool_registry.register_instance(
        _NoopTool(ToolDefinition(name="t1", description="x", category=ToolCategory.BUSINESS))
    )

    adapter = DeepgramToolAdapter(tool_registry)
    event = {
        "type": "FunctionCallRequest",
        "functions": [{"id": "call_1", "name": "t1", "arguments": "{}"}],
    }
    context = {
        "call_id": "c1",
        "session_store": object(),
        "ari_client": object(),
        "config": {"tools": {"enabled": False}},
    }
    result = await adapter.handle_tool_call_event(event, context)
    assert result["status"] == "error"

    tool_registry.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openai_adapter_allows_transfer_alias_when_blind_transfer_allowlisted():
    from src.tools.base import ToolDefinition, ToolCategory
    from src.tools.registry import tool_registry
    from src.tools.adapters.openai import OpenAIToolAdapter

    tool_registry.clear()
    tool_registry.register_instance(
        _NoopTool(ToolDefinition(name="blind_transfer", description="x", category=ToolCategory.TELEPHONY))
    )

    adapter = OpenAIToolAdapter(tool_registry)
    event = {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "call_id": "call_transfer",
            "name": "transfer",
            "arguments": "{}",
        },
    }
    context = {
        "call_id": "c1",
        "session_store": object(),
        "ari_client": object(),
        "config": {"tools": {"enabled": True}},
        "allowed_tools": ["blind_transfer"],
    }

    result = await adapter.handle_tool_call_event(event, context)
    assert result["status"] == "success"

    tool_registry.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_google_adapter_blocks_tool_during_pending_attended_transfer():
    from src.tools.base import ToolDefinition, ToolCategory
    from src.tools.registry import tool_registry
    from src.tools.adapters.google import GoogleToolAdapter
    from src.tools.context import ToolExecutionContext

    tool_registry.clear()
    tool_registry.register_instance(
        _BlockedIfExecutedTool(ToolDefinition(name="hangup_call", description="x", category=ToolCategory.TELEPHONY))
    )

    adapter = GoogleToolAdapter(tool_registry)
    context = ToolExecutionContext(
        call_id="c1",
        session_store=_PendingTransferSessionStore(),
        ari_client=object(),
        config={"tools": {"enabled": True}},
        provider_name="google_live",
    )

    result = await adapter.execute_tool("hangup_call", {"farewell_message": "Bye"}, context)

    assert result["status"] == "error"
    assert "attended transfer is pending" in result["message"].lower()

    tool_registry.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openai_adapter_blocks_tool_during_pending_attended_transfer():
    from src.tools.base import ToolDefinition, ToolCategory
    from src.tools.registry import tool_registry
    from src.tools.adapters.openai import OpenAIToolAdapter

    tool_registry.clear()
    tool_registry.register_instance(
        _BlockedIfExecutedTool(ToolDefinition(name="hangup_call", description="x", category=ToolCategory.TELEPHONY))
    )

    adapter = OpenAIToolAdapter(tool_registry)
    event = {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "call_id": "call_hangup",
            "name": "hangup_call",
            "arguments": "{\"farewell_message\":\"Bye\"}",
        },
    }
    context = {
        "call_id": "c1",
        "session_store": _PendingTransferSessionStore(),
        "ari_client": object(),
        "config": {"tools": {"enabled": True}},
        "allowed_tools": ["hangup_call", "cancel_transfer"],
    }

    result = await adapter.handle_tool_call_event(event, context)

    assert result["status"] == "error"
    assert result["ai_should_speak"] is False
    assert "attended transfer is pending" in result["message"].lower()

    tool_registry.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deepgram_adapter_blocks_tool_during_pending_attended_transfer():
    from src.tools.base import ToolDefinition, ToolCategory
    from src.tools.registry import tool_registry
    from src.tools.adapters.deepgram import DeepgramToolAdapter

    tool_registry.clear()
    tool_registry.register_instance(
        _BlockedIfExecutedTool(ToolDefinition(name="hangup_call", description="x", category=ToolCategory.TELEPHONY))
    )

    adapter = DeepgramToolAdapter(tool_registry)
    event = {
        "type": "FunctionCallRequest",
        "functions": [{"id": "call_1", "name": "hangup_call", "arguments": "{\"farewell_message\":\"Bye\"}"}],
    }
    context = {
        "call_id": "c1",
        "session_store": _PendingTransferSessionStore(),
        "ari_client": object(),
        "config": {"tools": {"enabled": True}},
        "allowed_tools": ["hangup_call", "cancel_transfer"],
    }

    result = await adapter.handle_tool_call_event(event, context)

    assert result["status"] == "error"
    assert "attended transfer is pending" in result["message"].lower()

    tool_registry.clear()
