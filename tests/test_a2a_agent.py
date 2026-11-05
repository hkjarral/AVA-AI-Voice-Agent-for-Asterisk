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
    async def open_client():
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
