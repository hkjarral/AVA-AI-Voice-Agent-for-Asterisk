"""Optional in-call delegation to a remote A2A 1.0 agent."""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import structlog
from a2a.client import (
    A2ACardResolver,
    A2AClientTimeoutError,
    ClientCallContext,
    ClientConfig,
    ClientFactory,
)
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    TaskState,
)

from src.tools.base import Tool, ToolCategory, ToolDefinition, ToolParameter
from src.tools.context import ToolExecutionContext

logger = structlog.get_logger(__name__)

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TERMINAL_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
}


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    default_port = 443 if parsed.scheme == "https" else 80
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        parsed.port or default_port,
    )


def _validate_base_url(url: str) -> str:
    value = str(url or "").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("A2A agent_url is invalid") from exc
    if (
        len(value) > 2048
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "A2A agent_url must be an HTTP(S) base URL without credentials, query or fragment"
        )
    return value


def _validate_agent_card(card: Any, base_url: str) -> None:
    interfaces = list(getattr(card, "supported_interfaces", []) or [])
    jsonrpc = [
        interface
        for interface in interfaces
        if interface.protocol_binding == "JSONRPC"
        and interface.protocol_version == "1.0"
    ]
    if not jsonrpc:
        raise ValueError("A2A Agent Card does not advertise JSONRPC 1.0")
    if any(_origin(interface.url) != _origin(base_url) for interface in jsonrpc):
        raise ValueError(
            "A2A Agent Card JSONRPC endpoint must use the configured origin"
        )


@dataclass(frozen=True)
class A2AAgentConfig:
    name: str
    agent_url: str
    description: str = "Delegate a request to a remote A2A agent."
    bearer_token_env: str | None = None
    timeout_ms: int = 15000
    poll_interval_ms: int = 250
    max_input_chars: int = 4000
    max_response_chars: int = 1000
    error_message: str = "The remote agent could not complete that request."

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_url", _validate_base_url(self.agent_url))
        if self.bearer_token_env is not None and not _ENV_NAME.fullmatch(
            self.bearer_token_env
        ):
            raise ValueError(
                "A2A bearer_token_env must be an environment variable name"
            )
        if self.bearer_token_env is not None:
            parsed = urlsplit(self.agent_url)
            if parsed.scheme != "https" and parsed.hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                raise ValueError(
                    "A2A Bearer authentication requires HTTPS except on loopback"
                )
        if not 1000 <= int(self.timeout_ms) <= 120000:
            raise ValueError("A2A timeout_ms must be between 1000 and 120000")
        if not 50 <= int(self.poll_interval_ms) <= 5000:
            raise ValueError("A2A poll_interval_ms must be between 50 and 5000")
        if not 1 <= int(self.max_input_chars) <= 16000:
            raise ValueError("A2A max_input_chars must be between 1 and 16000")
        if not 1 <= int(self.max_response_chars) <= 8000:
            raise ValueError("A2A max_response_chars must be between 1 and 8000")


class A2AAgentTool(Tool):
    """Send one caller-approved text request to a separately deployed A2A service."""

    def __init__(self, config: A2AAgentConfig):
        self.config = config
        self._definition = ToolDefinition(
            name=config.name,
            description=config.description,
            category=ToolCategory.BUSINESS,
            parameters=[
                ToolParameter(
                    name="message",
                    type="string",
                    description="The complete request to send to the remote agent.",
                    required=True,
                )
            ],
            max_execution_time=max(1, int(config.timeout_ms / 1000)),
            timeout_ms=config.timeout_ms,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def _headers(self) -> dict[str, str]:
        if not self.config.bearer_token_env:
            return {}
        token = os.environ.get(self.config.bearer_token_env)
        if not token or token != token.strip() or "\r" in token or "\n" in token:
            raise ValueError(
                "A2A bearer token environment variable is missing or invalid"
            )
        return {"Authorization": f"Bearer {token}"}

    @asynccontextmanager
    async def _open_client(self, deadline: float) -> AsyncIterator[Any]:
        timeout = self.config.timeout_ms / 1000
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False
        ) as discovery:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            card = await asyncio.wait_for(
                A2ACardResolver(discovery, self.config.agent_url).get_agent_card(),
                timeout=remaining,
            )
        _validate_agent_card(card, self.config.agent_url)
        transport = httpx.AsyncClient(
            headers=self._headers(), timeout=timeout, follow_redirects=False
        )
        client = ClientFactory(
            ClientConfig(
                streaming=False,
                polling=True,
                httpx_client=transport,
                supported_protocol_bindings=["JSONRPC"],
                accepted_output_modes=["text/plain"],
            )
        ).create(card)
        try:
            yield client
        finally:
            await client.close()

    @staticmethod
    def _task_text(task: Any) -> str:
        chunks: list[str] = []
        for artifact in getattr(task, "artifacts", []) or []:
            for part in getattr(artifact, "parts", []) or []:
                if part.HasField("text") and part.text:
                    chunks.append(part.text)
        return " ".join(chunks).strip()

    @staticmethod
    def _message_text(message: Any) -> str:
        return " ".join(
            part.text
            for part in (getattr(message, "parts", []) or [])
            if part.HasField("text") and part.text
        ).strip()

    def _bounded_text(self, text: str) -> str:
        if not text:
            raise ValueError("A2A response contained no text")
        if len(text) > self.config.max_response_chars:
            raise ValueError("A2A response text exceeded the configured limit")
        return text

    async def _cancel(self, client: Any, task_id: str, remaining: float) -> str:
        try:
            await asyncio.wait_for(
                client.cancel_task(
                    CancelTaskRequest(id=task_id),
                    context=ClientCallContext(timeout=max(0.05, remaining)),
                ),
                timeout=max(0.05, remaining),
            )
            return "cancel_requested"
        except Exception as exc:  # noqa: BLE001 - cancellation is best effort after timeout
            logger.warning(
                "A2A cancellation outcome unknown", error_type=type(exc).__name__
            )
            return "outcome_unknown"

    async def _run(
        self,
        client: Any,
        *,
        message: str,
        deadline: float,
        request_state: dict[str, str | None],
    ) -> dict[str, Any]:
        request = SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER,
                message_id=str(uuid4()),
                context_id=str(uuid4()),
                parts=[Part(text=message)],
            ),
            configuration=SendMessageConfiguration(
                accepted_output_modes=["text/plain"], return_immediately=True
            ),
        )
        response = None
        async for event in client.send_message(
            request,
            context=ClientCallContext(timeout=max(0.05, deadline - time.monotonic())),
        ):
            response = event
            break
        if response is None:
            raise ValueError("A2A agent returned no response")
        if response.HasField("message"):
            text = self._message_text(response.message)
            return {"status": "success", "message": self._bounded_text(text)}
        if not response.HasField("task"):
            raise ValueError("A2A response contained neither task nor message")

        task = response.task
        task_id = task.id
        if not task_id:
            raise ValueError("A2A task response did not include a task ID")
        request_state["task_id"] = task_id
        while task.status.state not in _TERMINAL_STATES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            await asyncio.sleep(min(self.config.poll_interval_ms / 1000, remaining))
            task = await client.get_task(
                GetTaskRequest(id=task_id),
                context=ClientCallContext(
                    timeout=max(0.05, deadline - time.monotonic())
                ),
            )

        state_name = (
            TaskState.Name(task.status.state).removeprefix("TASK_STATE_").lower()
        )
        if task.status.state != TaskState.TASK_STATE_COMPLETED:
            return {
                "status": "error",
                "message": self.config.error_message,
                "result": {"task_id": task_id, "state": state_name},
            }
        text = self._task_text(task)
        return {
            "status": "success",
            "message": self._bounded_text(text),
            "result": {"task_id": task_id, "state": state_name},
        }

    async def execute(
        self, parameters: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        message = parameters.get("message")
        if not isinstance(message, str) or not message.strip():
            return {
                "status": "error",
                "message": "A non-empty A2A request is required.",
            }
        message = message.strip()
        if len(message) > self.config.max_input_chars:
            return {"status": "error", "message": "The A2A request is too long."}

        deadline = time.monotonic() + (self.config.timeout_ms / 1000)
        request_state: dict[str, str | None] = {"task_id": None}
        try:
            async with self._open_client(deadline) as client:
                try:
                    return await asyncio.wait_for(
                        self._run(
                            client,
                            message=message,
                            deadline=deadline,
                            request_state=request_state,
                        ),
                        timeout=max(0.05, deadline - time.monotonic()),
                    )
                except asyncio.CancelledError:
                    if request_state["task_id"]:
                        await asyncio.shield(
                            self._cancel(client, request_state["task_id"], 1.0)
                        )
                    raise
                except (TimeoutError, A2AClientTimeoutError, httpx.TimeoutException):
                    state = "outcome_unknown"
                    if request_state["task_id"]:
                        state = await self._cancel(
                            client, request_state["task_id"], 1.0
                        )
                    return {
                        "status": "pending",
                        "message": "The remote agent response is uncertain; it was not retried.",
                        "result": {"task_id": request_state["task_id"], "state": state},
                    }
        except Exception as exc:  # noqa: BLE001 - return a bounded provider-neutral failure
            logger.warning("A2A tool request failed", error_type=type(exc).__name__)
            return {"status": "error", "message": self.config.error_message}


def create_a2a_agent_tool(name: str, config: dict[str, Any]) -> A2AAgentTool:
    allowed = {
        "agent_url",
        "description",
        "bearer_token_env",
        "timeout_ms",
        "poll_interval_ms",
        "max_input_chars",
        "max_response_chars",
        "error_message",
    }
    values = {key: value for key, value in config.items() if key in allowed}
    return A2AAgentTool(A2AAgentConfig(name=name, **values))
