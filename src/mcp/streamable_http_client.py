from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar
from urllib.parse import urlsplit

import httpx2
import structlog
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Implementation

from .errors import MCPError, MCPProtocolError, MCPServerExited

logger = structlog.get_logger(__name__)

_T = TypeVar("_T")
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]+$")
_SECRET_HEADERS = {"authorization", "proxy-authorization", "x-api-key", "api-key"}
_PROTOCOL_HEADERS = {
    "accept",
    "content-type",
    "mcp-protocol-version",
    "mcp-session-id",
    "host",
}


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
            if name.lower() in _SECRET_HEADERS and not _ENV_REFERENCE.search(template):
                raise MCPError(f"MCP server '{self.server_id}' requires an environment reference for authentication")

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
        try:
            async with asyncio.timeout(max(0.001, float(timeout_ms) / 1000.0)):
                async with httpx2.AsyncClient(
                    headers=headers,
                    timeout=httpx2.Timeout(300.0, connect=5.0, write=5.0, pool=5.0),
                    follow_redirects=False,
                ) as http_client:
                    transport = streamable_http_client(self.url, http_client=http_client)
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
            if operation == "tools/call":
                raise MCPError(
                    f"MCP server '{self.server_id}' tool outcome unknown; do not retry without reconciliation"
                ) from exc
            raise MCPError(f"MCP server '{self.server_id}' discovery failed ({type(exc).__name__})") from exc
