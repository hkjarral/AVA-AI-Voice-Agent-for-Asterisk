import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import GoogleProviderConfig, GrokProviderConfig, LLMConfig
from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine
from src.providers.deepgram import DeepgramProvider
from src.providers.google_live import GoogleLiveProvider
from src.providers.grok import GrokProvider
from src.tools.base import Tool, ToolCategory, ToolDefinition
from src.tools.registry import ToolRegistry


class _OpenWebSocket:
    state = SimpleNamespace(name="OPEN")

    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _ProviderAdapter:
    def __init__(self, result):
        self.result = dict(result)
        self.sent = []

    async def handle_tool_call_event(self, _event, _context):
        return dict(self.result)

    async def send_tool_result(self, result, context):
        self.sent.append((dict(result), context))


class _DeepgramAdapter(_ProviderAdapter):
    async def send_tool_result(self, result, context):
        # Match the production adapter's ownership of these routing fields.
        result.pop("function_call_id", None)
        result.pop("function_name", None)
        self.sent.append((dict(result), context))


class _BlockingDeepgramAdapter:
    def __init__(self):
        self.started = asyncio.Event()

    async def handle_tool_call_event(self, _event, _context):
        self.started.set()
        await asyncio.Event().wait()


class _SelfStoppingDeepgramAdapter:
    def __init__(self, provider):
        self.provider = provider
        self.result_sent = False

    async def handle_tool_call_event(self, _event, _context):
        await self.provider.stop_session()
        return {
            "function_call_id": "deepgram-self-stop-1",
            "function_name": "google_calendar",
            "status": "success",
            "message": "Stopped synchronously",
        }

    async def send_tool_result(self, _result, _context):
        self.result_sent = True


class _FailingDeepgramAdapter:
    async def handle_tool_call_event(self, _event, _context):
        raise RuntimeError("adapter exploded")


class _GoogleRegistry:
    @staticmethod
    def is_tool_allowed(_name, _allowed):
        return True


class _GoogleAdapter:
    registry = _GoogleRegistry()

    async def execute_tool(self, _name, _arguments, _context):
        return {"status": "success", "event_id": "event-google", "message": "Created"}


class _CalendarTool(Tool):
    @property
    def definition(self):
        return ToolDefinition(
            name="google_calendar",
            description="Calendar",
            category=ToolCategory.BUSINESS,
        )

    async def execute(self, _parameters, _context):
        return {"status": "success", "event_id": "event-engine", "message": "Created"}


class _BlockingTool(Tool):
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def definition(self):
        return ToolDefinition(name="blocking_tool", description="Blocks", category=ToolCategory.BUSINESS)

    async def execute(self, _parameters, _context):
        self.started.set()
        await self.release.wait()
        return {"status": "success"}


class _StatusTool(Tool):
    @property
    def definition(self):
        return ToolDefinition(name="status_tool", description="Status", category=ToolCategory.BUSINESS)

    async def execute(self, parameters, _context):
        return {"status": parameters["status"], "message": "terminal"}


class _ResultProvider:
    def __init__(self):
        self.calls = []

    async def send_tool_result(self, function_call_id, result, **kwargs):
        self.calls.append((function_call_id, dict(result), dict(kwargs)))
        return True


async def _session_store(call_id: str):
    store = SessionStore()
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    await store.upsert_call(session)
    return store, session


@pytest.mark.asyncio
async def test_grok_provider_persists_native_tool_call_id_before_result_delivery():
    store, session = await _session_store("session-grok")
    provider = GrokProvider(
        GrokProviderConfig(api_key="test", model="grok", voice="eve"),
        on_event=None,
    )
    provider._call_id = session.call_id
    provider._session_store = store
    provider.websocket = _OpenWebSocket()
    provider.tool_adapter = _ProviderAdapter(
        {"status": "success", "event_id": "event-grok", "message": "Created"}
    )
    provider._await_parent_response_done = AsyncMock()

    await provider._handle_function_call(
        {
            "response_id": "response-1",
            "item": {
                "type": "function_call",
                "call_id": "grok-tool-1",
                "name": "google_calendar",
                "arguments": '{"action":"create_event"}',
            },
        }
    )

    event = session.tool_calls[0]
    assert event["tool_call_id"] == "grok-tool-1"
    assert event["action"] == "create_event"
    assert event["target_id"] == "event-grok"
    assert event["status"] == "success"
    assert session.conversation_history == []


@pytest.mark.asyncio
async def test_deepgram_provider_records_result_before_adapter_consumes_routing_fields():
    store, session = await _session_store("session-deepgram")
    provider = DeepgramProvider({}, LLMConfig(), on_event=None)
    provider.call_id = session.call_id
    provider._session_store = store
    provider.tool_adapter = _DeepgramAdapter(
        {
            "function_call_id": "deepgram-tool-1",
            "function_name": "google_calendar",
            "status": "success",
            "event_id": "event-deepgram",
            "message": "Created",
        }
    )

    await provider._handle_function_call(
        {
            "type": "FunctionCallRequest",
            "functions": [
                {
                    "id": "deepgram-tool-1",
                    "name": "google_calendar",
                    "arguments": '{"action":"create_event"}',
                }
            ],
        }
    )

    event = session.tool_calls[0]
    assert event["tool_call_id"] == "deepgram-tool-1"
    assert event["target_id"] == "event-deepgram"
    assert event["status"] == "success"
    assert session.conversation_history == []


@pytest.mark.asyncio
async def test_deepgram_stop_cancels_and_records_tracked_tool_before_clearing_call_id():
    store, session = await _session_store("session-deepgram-cancel")
    provider = DeepgramProvider({}, LLMConfig(), on_event=None)
    provider.call_id = session.call_id
    provider._session_store = store
    provider.tool_adapter = _BlockingDeepgramAdapter()
    event = {
        "type": "FunctionCallRequest",
        "functions": [
            {
                "id": "deepgram-cancel-1",
                "name": "google_calendar",
                "arguments": '{"action":"create_event"}',
            }
        ],
    }

    task = asyncio.create_task(provider._handle_function_call(event))
    provider._tool_call_tasks.add(task)
    await provider.tool_adapter.started.wait()
    await provider.stop_session()

    assert task.done()
    assert provider.call_id is None
    assert len(session.tool_calls) == 1
    assert session.tool_calls[0]["tool_call_id"] == "deepgram-cancel-1"
    assert session.tool_calls[0]["status"] == "failure"
    assert session.tool_calls[0]["result"] == "cancelled"


@pytest.mark.asyncio
async def test_deepgram_tracked_tool_can_stop_its_own_session_synchronously():
    store, session = await _session_store("session-deepgram-self-stop")
    provider = DeepgramProvider({}, LLMConfig(), on_event=None)
    provider.call_id = session.call_id
    provider._session_store = store
    provider.tool_adapter = _SelfStoppingDeepgramAdapter(provider)
    event = {
        "type": "FunctionCallRequest",
        "functions": [
            {
                "id": "deepgram-self-stop-1",
                "name": "google_calendar",
                "arguments": '{"action":"create_event"}',
            }
        ],
    }

    task = asyncio.create_task(provider._handle_function_call(event))
    provider._tool_call_tasks.add(task)
    await asyncio.wait_for(task, timeout=1)

    assert not task.cancelled()
    assert provider._closed is True
    assert provider.call_id is None
    assert provider._tool_call_tasks == set()
    assert provider.tool_adapter.result_sent is True
    assert session.tool_calls[0]["tool_call_id"] == "deepgram-self-stop-1"
    assert session.tool_calls[0]["status"] == "success"


@pytest.mark.asyncio
async def test_deepgram_handler_failure_uses_voice_agent_response_schema():
    store, session = await _session_store("session-deepgram-error")
    provider = DeepgramProvider({}, LLMConfig(), on_event=None)
    provider.call_id = session.call_id
    provider._session_store = store
    provider.websocket = _OpenWebSocket()
    provider.tool_adapter = _FailingDeepgramAdapter()

    await provider._handle_function_call(
        {
            "type": "FunctionCallRequest",
            "functions": [
                {
                    "id": "deepgram-error-1",
                    "name": "google_calendar",
                    "arguments": '{"action":"create_event"}',
                }
            ],
        }
    )

    response = json.loads(provider.websocket.sent[0])
    assert response["type"] == "FunctionCallResponse"
    assert response["id"] == "deepgram-error-1"
    assert response["name"] == "google_calendar"
    assert json.loads(response["content"])["status"] == "error"
    assert session.tool_calls[0]["status"] == "failure"


@pytest.mark.asyncio
async def test_google_provider_persists_native_id_and_calendar_target():
    store, session = await _session_store("session-google")
    provider = GoogleLiveProvider(GoogleProviderConfig(), on_event=None)
    provider._call_id = session.call_id
    provider._session_store = store
    provider._tool_adapter = _GoogleAdapter()
    provider._allowed_tools = ["google_calendar"]
    provider._send_message = AsyncMock()

    await provider._handle_tool_call(
        {
            "toolCall": {
                "functionCalls": [
                    {
                        "id": "google-tool-1",
                        "name": "google_calendar",
                        "args": {"action": "create_event"},
                    }
                ]
            }
        }
    )

    event = session.tool_calls[0]
    assert event["tool_call_id"] == "google-tool-1"
    assert event["target_id"] == "event-google"
    assert event["status"] == "success"
    assert session.conversation_history == []


@pytest.mark.asyncio
async def test_engine_provider_path_preserves_id_for_elevenlabs_and_local_dispatch():
    store, session = await _session_store("session-engine")
    registry = ToolRegistry.isolated()
    registry.register_instance(_CalendarTool())
    session.allowed_tools = ["google_calendar"]

    engine = Engine.__new__(Engine)
    engine.session_store = store
    engine.ari_client = object()
    engine.config = SimpleNamespace(default_provider="elevenlabs")
    engine._call_providers = {}
    engine._tool_registry_for_session = lambda _session: registry
    engine._tool_config_for_session = lambda _session: {}
    engine._get_provider_kind = lambda _provider_name: "elevenlabs"
    engine._should_send_provider_tool_result = lambda **_kwargs: True

    await engine._execute_provider_tool(
        call_id=session.call_id,
        function_name="google_calendar",
        function_call_id="elevenlabs-tool-1",
        parameters={"action": "create_event"},
        session=session,
    )

    event = session.tool_calls[0]
    assert event["tool_call_id"] == "elevenlabs-tool-1"
    assert event["target_id"] == "event-engine"
    assert event["status"] == "success"
    assert session.conversation_history == []


@pytest.mark.asyncio
async def test_engine_records_cancelled_provider_tool_and_reraises():
    store, session = await _session_store("session-cancelled")
    tool = _BlockingTool()
    registry = ToolRegistry.isolated()
    registry.register_instance(tool)
    session.allowed_tools = ["blocking_tool"]

    engine = Engine.__new__(Engine)
    engine.session_store = store
    engine.ari_client = object()
    engine.config = SimpleNamespace(default_provider="elevenlabs")
    engine._call_providers = {}
    engine._tool_registry_for_session = lambda _session: registry
    engine._tool_config_for_session = lambda _session: {}
    engine._get_provider_kind = lambda _provider_name: "elevenlabs"
    engine._should_send_provider_tool_result = lambda **_kwargs: True

    execution = asyncio.create_task(
        engine._execute_provider_tool(
            call_id=session.call_id,
            function_name="blocking_tool",
            function_call_id="cancelled-tool-1",
            parameters={},
            session=session,
        )
    )
    await tool.started.wait()
    execution.cancel()

    with pytest.raises(asyncio.CancelledError):
        await execution

    assert len(session.tool_calls) == 1
    assert session.tool_calls[0]["tool_call_id"] == "cancelled-tool-1"
    assert session.tool_calls[0]["status"] == "failure"
    assert session.tool_calls[0]["result"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_kind,raw_status", [("local", "failed"), ("elevenlabs", "disabled")])
async def test_provider_delivery_marks_all_normalized_failures_as_errors(provider_kind, raw_status):
    store, session = await _session_store(f"session-{provider_kind}-{raw_status}")
    registry = ToolRegistry.isolated()
    registry.register_instance(_StatusTool())
    session.allowed_tools = ["status_tool"]
    session.provider_name = provider_kind
    provider = _ResultProvider()

    engine = Engine.__new__(Engine)
    engine.session_store = store
    engine.ari_client = object()
    engine.config = SimpleNamespace(default_provider=provider_kind)
    engine._call_providers = {session.call_id: provider}
    engine._tool_registry_for_session = lambda _session: registry
    engine._tool_config_for_session = lambda _session: {}
    engine._get_provider_kind = lambda _provider_name: provider_kind
    engine._should_send_provider_tool_result = lambda **_kwargs: True

    await engine._execute_provider_tool(
        call_id=session.call_id,
        function_name="status_tool",
        function_call_id=f"{provider_kind}-tool-1",
        parameters={"status": raw_status},
        session=session,
    )

    _, _, kwargs = provider.calls[0]
    assert kwargs["is_error"] is True
    if provider_kind == "local":
        assert kwargs["call_id"] == session.call_id
        assert kwargs["tool_name"] == "status_tool"
    assert session.tool_calls[0]["status"] == "failure"
