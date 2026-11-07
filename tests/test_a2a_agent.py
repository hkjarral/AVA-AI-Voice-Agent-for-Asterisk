import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from a2a.types import Artifact, Part, StreamResponse, Task, TaskState, TaskStatus

from src.tools.a2a_agent import A2AAgentConfig, A2AAgentTool, _validate_agent_card
from src.tools.context import ToolExecutionContext


def _task_event(state, *, task_id="task-1", text=None):
    artifacts = (
        []
        if text is None
        else [Artifact(artifact_id="artifact-1", parts=[Part(text=text)])]
    )
    return StreamResponse(
        task=Task(
            id=task_id,
            context_id="call-1",
            status=TaskStatus(state=state),
            artifacts=artifacts,
        )
    )


class FakeClient:
    def __init__(self, first, *, followups=(), send_error=None, get_error=None):
        self.first = first
        self.followups = list(followups)
        self.send_error = send_error
        self.get_error = get_error
        self.sent = 0
        self.gets = 0
        self.canceled = []

    async def send_message(self, request, *, context=None):
        self.sent += 1
        if self.send_error:
            raise self.send_error
        yield self.first

    async def get_task(self, request, *, context=None):
        self.gets += 1
        if self.get_error:
            raise self.get_error
        followup = self.followups.pop(0)
        return followup.task if isinstance(followup, StreamResponse) else followup

    async def cancel_task(self, request, *, context=None):
        self.canceled.append(request.id)
        return Task(
            id=request.id,
            context_id="call-1",
            status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
        )


def _tool(client, **overrides):
    config = A2AAgentConfig(
        name="delegate",
        agent_url="https://agent.example",
        poll_interval_ms=50,
        **overrides,
    )
    tool = A2AAgentTool(config)

    @asynccontextmanager
    async def open_client(deadline):
        yield client

    tool._open_client = open_client
    return tool


@pytest.mark.asyncio
async def test_completed_task_returns_bounded_text_artifact():
    client = FakeClient(
        _task_event(TaskState.TASK_STATE_WORKING),
        followups=[_task_event(TaskState.TASK_STATE_COMPLETED, text="Remote answer")],
    )
    result = await _tool(client).execute(
        {"message": "Please investigate"}, ToolExecutionContext(call_id="call-1")
    )

    assert result == {
        "status": "success",
        "message": "Remote answer",
        "result": {"task_id": "task-1", "state": "completed"},
    }
    assert client.sent == 1
    assert client.gets == 1


@pytest.mark.asyncio
async def test_known_task_timeout_requests_cancel_without_retry():
    client = FakeClient(
        _task_event(TaskState.TASK_STATE_WORKING),
        get_error=httpx.ReadTimeout("late"),
    )
    result = await _tool(client).execute(
        {"message": "Please investigate"}, ToolExecutionContext(call_id="call-1")
    )

    assert result["status"] == "pending"
    assert result["result"] == {"task_id": "task-1", "state": "cancel_requested"}
    assert client.sent == 1
    assert client.canceled == ["task-1"]


@pytest.mark.asyncio
async def test_initial_timeout_is_unknown_and_is_not_retried_or_canceled():
    client = FakeClient(None, send_error=httpx.ReadTimeout("unknown"))
    result = await _tool(client).execute(
        {"message": "Please investigate"}, ToolExecutionContext(call_id="call-1")
    )

    assert result["status"] == "pending"
    assert result["result"] == {"task_id": None, "state": "outcome_unknown"}
    assert client.sent == 1
    assert client.canceled == []


@pytest.mark.asyncio
async def test_call_cancellation_reaches_known_remote_task():
    class BlockingClient(FakeClient):
        async def get_task(self, request, *, context=None):
            self.gets += 1
            await asyncio.Event().wait()

    client = BlockingClient(_task_event(TaskState.TASK_STATE_WORKING))
    operation = asyncio.create_task(
        _tool(client).execute(
            {"message": "Please investigate"}, ToolExecutionContext(call_id="call-1")
        )
    )
    while client.gets == 0:
        await asyncio.sleep(0.01)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation

    assert client.canceled == ["task-1"]


def test_bearer_token_requires_environment_reference(monkeypatch):
    with pytest.raises(ValueError, match="environment variable name"):
        A2AAgentConfig(
            name="delegate",
            agent_url="https://agent.example",
            bearer_token_env="literal token",
        )

    config = A2AAgentConfig(
        name="delegate",
        agent_url="https://agent.example",
        bearer_token_env="A2A_TOKEN",
    )
    monkeypatch.setenv("A2A_TOKEN", "secret")
    assert A2AAgentTool(config)._headers() == {"Authorization": "Bearer secret"}

    with pytest.raises(ValueError, match="requires HTTPS"):
        A2AAgentConfig(
            name="delegate",
            agent_url="http://agent.example",
            bearer_token_env="A2A_TOKEN",
        )


@pytest.mark.asyncio
async def test_agent_card_discovery_obeys_total_deadline(monkeypatch):
    class BlockingResolver:
        def __init__(self, client, agent_url):
            pass

        async def get_agent_card(self):
            await asyncio.Event().wait()

    monkeypatch.setattr("a2a.client.A2ACardResolver", BlockingResolver)
    tool = A2AAgentTool(
        A2AAgentConfig(
            name="delegate",
            agent_url="https://agent.example",
            timeout_ms=1000,
        )
    )

    result = await tool.execute(
        {"message": "Please investigate"}, ToolExecutionContext(call_id="call-1")
    )

    assert result["status"] == "error"


def test_agent_card_cannot_redirect_credentials_to_another_origin():
    class Interface:
        protocol_binding = "JSONRPC"
        protocol_version = "1.0"
        url = "https://other.example/a2a/jsonrpc"

    class Card:
        def __init__(self):
            self.supported_interfaces = [Interface()]

    with pytest.raises(ValueError, match="configured origin"):
        _validate_agent_card(Card(), "https://agent.example")


def test_registry_builds_a2a_tool_from_in_call_config():
    from src.tools.registry import ToolRegistry

    registry = ToolRegistry.isolated()
    registry.initialize_in_call_http_tools_from_config(
        {
            "delegate": {
                "kind": "a2a_agent",
                "agent_url": "https://agent.example",
            }
        }
    )

    assert isinstance(registry.get("delegate"), A2AAgentTool)


def test_registry_rejects_invalid_a2a_config():
    from src.tools.registry import ToolRegistry

    registry = ToolRegistry.isolated()
    with pytest.raises(ValueError, match="agent_url"):
        registry.initialize_in_call_http_tools_from_config(
            {"delegate": {"kind": "a2a_agent", "agent_url": "file:///tmp/agent"}}
        )


def test_registry_rejects_a2a_name_reserved_by_global_tools():
    from src.tools.runtime_config import ToolRuntimeGeneration

    config = {
        "tools": {"delegate": {"enabled": False}},
        "in_call_tools": {
            "delegate": {
                "kind": "a2a_agent",
                "agent_url": "https://agent.example",
            }
        },
    }
    generation = ToolRuntimeGeneration.build(
        generation_id=1, config=config, skip_invalid_http_templates=True
    )
    assert not generation.registry.has("delegate")

    with pytest.raises(ValueError, match="reserved or invalid"):
        ToolRuntimeGeneration.build(generation_id=2, config=config)


def test_registry_rejects_a2a_alias_shadowing():
    from src.tools.registry import ToolRegistry

    registry = ToolRegistry.isolated()
    with pytest.raises(ValueError, match="reserved or invalid"):
        registry.initialize_in_call_http_tools_from_config(
            {
                "transfer_call": {
                    "kind": "a2a_agent",
                    "agent_url": "https://agent.example",
                }
            }
        )


@pytest.mark.parametrize("during_poll", [False, True])
@pytest.mark.asyncio
async def test_sdk_connection_loss_preserves_uncertain_outcome(
    monkeypatch, during_poll
):
    import json

    calls = []

    def handle(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "name": "Test agent",
                    "supportedInterfaces": [
                        {
                            "url": "https://agent.example/rpc",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                },
            )

        rpc = json.loads(request.content)
        method = rpc["method"]
        calls.append(method)
        if method == ("GetTask" if during_poll else "SendMessage"):
            raise httpx.ReadError("Response lost", request=request)
        task = {"id": "task-1", "status": {"state": "TASK_STATE_WORKING"}}
        if method == "SendMessage":
            result = {"task": task}
        else:
            assert method == "CancelTask"
            assert rpc["params"]["id"] == "task-1"
            result = {**task, "status": {"state": "TASK_STATE_CANCELED"}}
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": result}
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "src.tools.a2a_agent.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    tool = A2AAgentTool(
        A2AAgentConfig(
            name="delegate", agent_url="https://agent.example", poll_interval_ms=50
        )
    )
    result = await tool.execute(
        {"message": "Please investigate"}, ToolExecutionContext(call_id="call-1")
    )

    assert result["status"] == "pending"
    assert calls == (
        ["SendMessage", "GetTask", "CancelTask"] if during_poll else ["SendMessage"]
    )
    assert result["result"] == (
        {"task_id": "task-1", "state": "cancel_requested"}
        if during_poll
        else {"task_id": None, "state": "outcome_unknown"}
    )


@pytest.mark.parametrize("model", ["gemini-3.8-live", "gemini-2.5-flash-native-audio"])
@pytest.mark.asyncio
async def test_google_teardown_allows_remote_cancel_to_finish(monkeypatch, model):
    from src.config import GoogleProviderConfig
    from src.providers.google_live import GoogleLiveProvider
    from src.tools.registry import ToolRegistry

    polling = asyncio.Event()
    cancel_started = asyncio.Event()
    finish_cancel = asyncio.Event()

    class BlockingClient(FakeClient):
        async def get_task(self, request, *, context=None):
            polling.set()
            await asyncio.Event().wait()

        async def cancel_task(self, request, *, context=None):
            cancel_started.set()
            await finish_cancel.wait()
            return await super().cancel_task(request, context=context)

    client = BlockingClient(_task_event(TaskState.TASK_STATE_WORKING))
    provider = GoogleLiveProvider(
        config=GoogleProviderConfig(llm_model=model),
        on_event=lambda event: None,
    )
    provider._call_id = "call-1"
    provider._allowed_tools = ["delegate"]
    registry = ToolRegistry.isolated()
    registry.register_instance(_tool(client))
    monkeypatch.setattr(provider._tool_adapter, "registry", registry)
    await provider._handle_server_message(
        {
            "toolCall": {
                "functionCalls": [
                    {
                        "id": "func-1",
                        "name": "delegate",
                        "args": {"message": "Please investigate"},
                    },
                ]
            }
        }
    )
    await asyncio.wait_for(polling.wait(), 1)
    await provider._handle_server_message({"toolCallCancellation": {"ids": ["func-1"]}})
    await asyncio.wait_for(cancel_started.wait(), 1)
    teardown = asyncio.create_task(provider.stop_session())
    await asyncio.sleep(0)
    finish_cancel.set()
    await asyncio.wait_for(teardown, 1)
    assert client.canceled == ["task-1"]


@pytest.mark.asyncio
async def test_repeated_caller_cancellation_keeps_client_open_for_remote_cancel():
    polling = asyncio.Event()
    cancel_started = asyncio.Event()
    finish_cancel = asyncio.Event()
    client_closed = False

    class BlockingClient(FakeClient):
        async def get_task(self, request, *, context=None):
            polling.set()
            await asyncio.Event().wait()

        async def cancel_task(self, request, *, context=None):
            assert not client_closed
            cancel_started.set()
            await finish_cancel.wait()
            assert not client_closed
            return await super().cancel_task(request, context=context)

    client = BlockingClient(_task_event(TaskState.TASK_STATE_WORKING))
    tool = _tool(client)

    @asynccontextmanager
    async def open_client(deadline):
        nonlocal client_closed
        try:
            yield client
        finally:
            client_closed = True

    tool._open_client = open_client
    operation = asyncio.create_task(
        tool.execute(
            {"message": "Please investigate"},
            ToolExecutionContext(call_id="call-1"),
        )
    )
    await asyncio.wait_for(polling.wait(), 1)
    operation.cancel()
    await asyncio.wait_for(cancel_started.wait(), 1)
    operation.cancel()
    await asyncio.sleep(0)
    assert not client_closed
    finish_cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert client_closed
    assert client.canceled == ["task-1"]


def test_startup_skips_invalid_a2a_but_reload_rejects_it():
    from src.tools.runtime_config import ToolRuntimeGeneration

    config = {
        "in_call_tools": {
            "broken": {"kind": "a2a_agent", "agent_url": "file:///tmp/agent"},
            "delegate": {"kind": "a2a_agent", "agent_url": "https://agent.example"},
        }
    }
    generation = ToolRuntimeGeneration.build(
        generation_id=1, config=config, skip_invalid_http_templates=True
    )
    assert generation.registry.has("delegate")
    assert not generation.registry.has("broken")
    assert generation.registry.has("hangup_call")

    with pytest.raises(ValueError, match="agent_url"):
        ToolRuntimeGeneration.build(generation_id=2, config=config)


@pytest.mark.parametrize("cancel_completes", [True, False])
@pytest.mark.asyncio
async def test_hangup_during_timeout_cancel_keeps_transport_open(cancel_completes):
    cancel_started = asyncio.Event()
    release_cancel = asyncio.Event()
    cancel_finished = asyncio.Event()
    client_closed = False
    cancel_attempts = 0

    class TimeoutClient(FakeClient):
        async def cancel_task(self, request, *, context=None):
            nonlocal cancel_attempts
            cancel_attempts += 1
            assert not client_closed
            cancel_started.set()
            try:
                await release_cancel.wait()
                return await super().cancel_task(request, context=context)
            finally:
                assert not client_closed
                cancel_finished.set()

    client = TimeoutClient(
        _task_event(TaskState.TASK_STATE_WORKING),
        get_error=httpx.ReadTimeout("poll response lost"),
    )
    tool = _tool(client)

    @asynccontextmanager
    async def open_client(deadline):
        nonlocal client_closed
        try:
            yield client
        finally:
            client_closed = True

    tool._open_client = open_client
    operation = asyncio.create_task(
        tool.execute(
            {"message": "Please investigate"}, ToolExecutionContext(call_id="call-1")
        )
    )
    try:
        await asyncio.wait_for(cancel_started.wait(), 1)
        operation.cancel("caller hung up")
        await asyncio.sleep(0)
        assert not operation.done()
        assert not cancel_finished.is_set()
        assert not client_closed
        operation.cancel("duplicate signal")
        await asyncio.sleep(0)
        assert not operation.done()
        assert not cancel_finished.is_set()
        assert not client_closed
        if cancel_completes:
            release_cancel.set()
        with pytest.raises(asyncio.CancelledError) as exc:
            await asyncio.wait_for(operation, 2)
        assert exc.value.args == ("caller hung up",)
        assert cancel_finished.is_set()
        assert client_closed
        assert cancel_attempts == 1
        assert client.canceled == (["task-1"] if cancel_completes else [])
        assert client.sent == 1
    finally:
        release_cancel.set()
        if not operation.done():
            operation.cancel()
        try:
            await operation
        except asyncio.CancelledError:
            pass
