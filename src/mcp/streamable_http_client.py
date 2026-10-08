from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar
from urllib.parse import urlsplit

import anyio
import httpx2
import structlog
from mcp.client import Client
from mcp.client.streamable_http import StreamableHTTPTransport
from mcp.shared._compat import resync_tracer
from mcp.shared._context_streams import create_context_streams
from mcp.shared.message import SessionMessage
from mcp_types import JSONRPCError, JSONRPCResponse, jsonrpc_message_adapter
from mcp.types import CallToolResult, Implementation

from .errors import MCPError, MCPProtocolError, MCPServerExited

logger = structlog.get_logger(__name__)

_T = TypeVar("_T")
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_AUTH_SCHEME_REFERENCE = re.compile(r"[A-Za-z][A-Za-z0-9_-]* \$\{[A-Za-z_][A-Za-z0-9_]*\}")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]+$")
_PROTOCOL_HEADERS = {
    "accept",
    "content-type",
    "mcp-protocol-version",
    "mcp-session-id",
    "host",
}


class _ValidatingStreamableHTTPTransport(StreamableHTTPTransport):
    """Reject SSE replies whose JSON-RPC ID is not the request being answered."""

    async def _handle_sse_event(
        self, sse, read_stream_writer, original_request_id=None, resumption_callback=None
    ) -> bool:
        if original_request_id is not None and sse.event == "message" and sse.data:
            try:
                message = jsonrpc_message_adapter.validate_json(sse.data, by_name=False)
            except ValueError:
                # Leave malformed-response handling to the SDK.
                message = None
            if isinstance(message, (JSONRPCResponse, JSONRPCError)) and message.id != original_request_id:
                raise httpx2.SSEError("MCP SSE response ID did not match the request")
        return await super()._handle_sse_event(
            sse,
            read_stream_writer,
            original_request_id=original_request_id,
            resumption_callback=resumption_callback,
        )


@asynccontextmanager
async def _validated_streamable_http_client(url: str, *, http_client: httpx2.AsyncClient):
    """Use the SDK transport with one response-ID guard until upstream fixes it.

    The SDK factory always constructs its own transport, so this small lifecycle
    wrapper retains its stream/task behavior while installing the guarded class.
    """
    transport = _ValidatingStreamableHTTPTransport(url)
    read_writer, read_stream = create_context_streams[SessionMessage | Exception](0)
    write_stream, write_reader = create_context_streams[SessionMessage](0)
    async with read_writer, read_stream, write_stream, write_reader, anyio.create_task_group() as tg:
        def start_get_stream() -> None:
            tg.start_soon(transport.handle_get_stream, http_client, read_writer)

        tg.start_soon(
            transport.post_writer,
            http_client,
            write_reader,
            read_writer,
            write_stream,
            start_get_stream,
            tg,
        )
        try:
            yield read_stream, write_stream
        finally:
            if transport.session_id:
                await transport.terminate_session(http_client)
            tg.cancel_scope.cancel()
    await resync_tracer()


class MCPStreamableHTTPClient:
    """Official MCP SDK transport for explicitly configured remote servers.

    Each operation owns a fresh negotiated session. A failed tools/call is never
    automatically replayed, even if the server accepted it before the link failed.
    """

    def __init__(
        self,
        *,
        server_id: str,
        url: str,
        headers: Dict[str, str],
        default_timeout_ms: int = 10000,
    ):
        self.server_id = server_id
        self.url = str(url or "").strip()
        self.headers = dict(headers or {})
        self.default_timeout_ms = int(default_timeout_ms)
        self._closing = False
        self._validate_url()

    def _validate_url(self) -> None:
        try:
            parsed = urlsplit(self.url)
            _ = parsed.port  # Reject malformed port syntax.
            valid = (
                len(self.url) <= 2048
                and parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
            )
        except ValueError:
            valid = False
            parsed = None
        if (
            not valid
            or parsed is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise MCPError(f"MCP server '{self.server_id}' has an invalid HTTP URL")

    def _resolve_headers(self) -> Dict[str, str]:
        resolved: Dict[str, str] = {}
        for name, template in self.headers.items():
            if (
                not _HEADER_NAME.fullmatch(name)
                or name.lower() in _PROTOCOL_HEADERS
                or not isinstance(template, str)
            ):
                raise MCPError(f"MCP server '{self.server_id}' has an invalid header configuration")
            # Any custom header could carry credentials, including vendor-specific
            # names such as X-Auth-Token. Require the whole value to come from an
            # environment reference; only HTTP auth permits a literal scheme.
            env_only = _ENV_REFERENCE.fullmatch(template)
            if name.lower() in {"authorization", "proxy-authorization"}:
                env_only = env_only or _AUTH_SCHEME_REFERENCE.fullmatch(template)
            if not env_only:
                raise MCPError(f"MCP server '{self.server_id}' requires an environment-only header")

            def expand(match: re.Match[str]) -> str:
                value = os.environ.get(match.group(1))
                if not value:
                    raise MCPError(f"MCP server '{self.server_id}' is missing a header environment variable")
                return value

            value = _ENV_REFERENCE.sub(expand, template)
            if "\r" in value or "\n" in value or "${" in value:
                raise MCPError(f"MCP server '{self.server_id}' has an invalid header value")
            resolved[name] = value
        return resolved

    async def start(self) -> None:
        if self._closing:
            raise MCPServerExited(f"MCP server '{self.server_id}' is shutting down")
        # Discovery is performed by the manager immediately after start.

    async def stop(self) -> None:
        self._closing = True

    async def list_tools(self) -> List[Dict[str, Any]]:
        return await self._execute("tools/list", self.default_timeout_ms, self._list_all)

    async def call_tool(
        self,
        *,
        name: str,
        arguments: Dict[str, Any],
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        async def invoke(client: Client) -> Dict[str, Any]:
            # The modern protocol can mirror schema-annotated arguments into
            # headers; absorb all listing pages before the low-level call.
            tools = await self._list_all(client)
            if not any(tool["name"] == name for tool in tools):
                raise MCPProtocolError(f"MCP server '{self.server_id}' did not advertise tool '{name}'")
            result = await client.session.call_tool(
                name,
                arguments or {},
                read_timeout_seconds=max(0.001, float(timeout_ms or self.default_timeout_ms) / 1000.0),
            )
            if not isinstance(result, CallToolResult):
                raise MCPProtocolError(f"MCP server '{self.server_id}' returned an unsupported tool result")
            return result.model_dump(by_alias=True, exclude_none=True)

        return await self._execute("tools/call", timeout_ms or self.default_timeout_ms, invoke)

    async def _list_all(self, client: Client) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        seen: set[str] = set()
        for _ in range(100):
            page = await client.list_tools(cursor=cursor)
            tools.extend(tool.model_dump(by_alias=True, exclude_none=True) for tool in page.tools)
            cursor = page.next_cursor
            if not cursor:
                return tools
            if cursor in seen:
                raise MCPProtocolError(f"MCP server '{self.server_id}' repeated a tool-list cursor")
            seen.add(cursor)
        raise MCPProtocolError(f"MCP server '{self.server_id}' exceeded the tool-list page limit")

    async def _execute(
        self,
        operation: str,
        timeout_ms: int,
        action: Callable[[Client], Awaitable[_T]],
    ) -> _T:
        if self._closing:
            raise MCPServerExited(f"MCP server '{self.server_id}' is shutting down")
        headers = self._resolve_headers()
        # The SDK logs raw messages, SSE data and session IDs at DEBUG/INFO.
        # Our own failure log below contains only server and exception type.
        logging.getLogger("mcp.client.streamable_http").setLevel(logging.CRITICAL + 1)
        tool_post_attempted = False

        async def note_request(request: httpx2.Request) -> None:
            nonlocal tool_post_attempted
            if operation != "tools/call" or request.method != "POST":
                return
            try:
                envelope = json.loads(request.content)
            except (ValueError, TypeError, httpx2.RequestNotRead):
                return
            if isinstance(envelope, dict) and envelope.get("method") == "tools/call":
                tool_post_attempted = True

        try:
            async with asyncio.timeout(max(0.001, float(timeout_ms) / 1000.0)):
                async with httpx2.AsyncClient(
                    headers=headers,
                    timeout=httpx2.Timeout(300.0, connect=5.0, write=5.0, pool=5.0),
                    follow_redirects=False,
                    event_hooks={"request": [note_request]},
                ) as http_client:
                    transport = _validated_streamable_http_client(self.url, http_client=http_client)
                    async with Client(
                        transport,
                        mode="auto",
                        cache=None,
                        client_info=Implementation(name="Asterisk-AI-Voice-Agent", version="dev"),
                    ) as client:
                        return await action(client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "MCP HTTP operation failed",
                server=self.server_id,
                operation=operation,
                error_type=type(exc).__name__,
            )
            if operation == "tools/call" and tool_post_attempted:
                raise MCPError(
                    f"MCP server '{self.server_id}' tool outcome unknown; do not retry without reconciliation"
                ) from exc
            if operation == "tools/call":
                raise MCPError(f"MCP server '{self.server_id}' tool was not invoked ({type(exc).__name__})") from exc
            raise MCPError(f"MCP server '{self.server_id}' discovery failed ({type(exc).__name__})") from exc
