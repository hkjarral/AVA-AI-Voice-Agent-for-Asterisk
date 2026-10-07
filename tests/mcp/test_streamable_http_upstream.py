from __future__ import annotations

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import uvicorn
from mcp.server import MCPServer
from starlette.responses import JSONResponse, PlainTextResponse

from src.config import MCPConfig, MCPServerConfig
from src.mcp.errors import MCPError, MCPProtocolError
from src.mcp.manager import MCPClientManager
from src.mcp.streamable_http_client import MCPStreamableHTTPClient
from src.tools.registry import ToolRegistry


@asynccontextmanager
async def local_http(app):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.02)
        assert server.started
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("json_response", [True, False])
async def test_official_sdk_negotiates_json_and_sse(json_response):
    server = MCPServer("local-test")

    @server.tool()
    def echo(value: str) -> str:
        return value

    app = server.streamable_http_app(host="127.0.0.1", stateless_http=True, json_response=json_response)
    async with local_http(app) as url:
        client = MCPStreamableHTTPClient(server_id="remote", url=url, headers={})
        tools = await client.list_tools()
        assert [tool["name"] for tool in tools] == ["echo"]
        result = await client.call_tool(name="echo", arguments={"value": "hello"})
        assert result["isError"] is False
        assert result["content"][0]["text"] == "hello"
        await client.stop()


@pytest.mark.asyncio
async def test_auth_header_is_resolved_but_never_exposed(monkeypatch):
    monkeypatch.setenv("MCP_TEST_SECRET", "private-sentinel")
    server = MCPServer("local-test")

    @server.tool()
    def echo(value: str) -> str:
        return value

    inner_app = server.streamable_http_app(host="127.0.0.1", stateless_http=True, json_response=True)
    seen_auth = []

    async def auth_app(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            seen_auth.append(headers.get(b"authorization"))
            if headers.get(b"authorization") != b"Bearer private-sentinel":
                await PlainTextResponse("denied", status_code=401)(scope, receive, send)
                return
        await inner_app(scope, receive, send)

    async with local_http(auth_app) as url:
        cfg = MCPConfig(
            enabled=True,
            servers={
                "remote": MCPServerConfig(
                    transport="streamable_http",
                    url=url,
                    headers={"Authorization": "Bearer ${MCP_TEST_SECRET}"},
                    command=[],
                )
            },
        )
        manager = MCPClientManager(cfg)
        await manager.start()
        registry = ToolRegistry.isolated()
        assert manager.register_tools(registry) == ["mcp_remote_echo"]
        status = str(manager.get_status())
        assert "private-sentinel" not in status
        assert url not in status
        result = await manager.call_tool(
            server_id="remote", tool_name="echo", arguments={"value": "ok"}, timeout_ms=3000
        )
        assert result["content"][0]["text"] == "ok"
        assert seen_auth and all(value == b"Bearer private-sentinel" for value in seen_auth)
        await manager.stop()


@pytest.mark.asyncio
async def test_rejected_auth_does_not_expose_header_or_url(monkeypatch):
    monkeypatch.setenv("MCP_TEST_SECRET", "private-sentinel")

    async def reject_app(scope, receive, send):
        if scope["type"] == "http":
            await PlainTextResponse("private-sentinel", status_code=401)(scope, receive, send)

    async with local_http(reject_app) as url:
        cfg = MCPConfig(
            enabled=True,
            servers={
                "remote": MCPServerConfig(
                    transport="streamable_http",
                    url=url,
                    headers={"Authorization": "Bearer ${MCP_TEST_SECRET}"},
                )
            },
        )
        manager = MCPClientManager(cfg)
        await manager.start()
        status = str(manager.get_status())
        assert manager.get_status()["servers"]["remote"]["up"] is False
        assert "private-sentinel" not in status
        assert url not in status
        await manager.stop()


@pytest.mark.asyncio
async def test_bad_remote_does_not_prevent_other_server_discovery():
    server = MCPServer("local-good")

    @server.tool()
    def echo(value: str) -> str:
        return value

    app = server.streamable_http_app(host="127.0.0.1", stateless_http=True, json_response=True)
    async with local_http(app) as url:
        manager = MCPClientManager(
            MCPConfig(
                enabled=True,
                servers={
                    "bad": MCPServerConfig(transport="streamable_http", url="https://bad.example/mcp?token=no"),
                    "good": MCPServerConfig(transport="streamable_http", url=url),
                },
            )
        )
        await manager.start()
        registry = ToolRegistry.isolated()
        assert manager.register_tools(registry) == ["mcp_good_echo"]
        assert manager.get_status()["servers"]["bad"]["up"] is False
        assert manager.get_status()["servers"]["good"]["up"] is True
        await manager.stop()


@pytest.mark.asyncio
async def test_lost_mutating_response_is_not_replayed():
    effects = []
    server = MCPServer("local-test")

    @server.tool()
    async def record(value: str) -> str:
        effects.append(value)
        await asyncio.sleep(2)
        return "done"

    app = server.streamable_http_app(host="127.0.0.1", stateless_http=True, json_response=True)
    async with local_http(app) as url:
        client = MCPStreamableHTTPClient(server_id="remote", url=url, headers={})
        with pytest.raises(MCPError, match="outcome unknown"):
            await client.call_tool(name="record", arguments={"value": "once"}, timeout_ms=750)
        assert effects == ["once"]
        await client.stop()


@pytest.mark.asyncio
async def test_mismatched_tool_response_does_not_trigger_second_call():
    server = MCPServer("local-mismatch")

    @server.tool()
    def record(value: str) -> str:
        return value

    inner_app = server.streamable_http_app(host="127.0.0.1", stateless_http=True, json_response=True)
    calls = []

    async def mismatch_app(scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            await inner_app(scope, receive, send)
            return
        chunks = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        request = json.loads(body)
        if request.get("method") == "tools/call":
            calls.append(request["id"])
            await JSONResponse(
                {"jsonrpc": "2.0", "id": "not-the-request", "result": {"content": []}}
            )(scope, receive, send)
            return
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await inner_app(scope, replay, send)

    async with local_http(mismatch_app) as url:
        client = MCPStreamableHTTPClient(server_id="remote", url=url, headers={})
        with pytest.raises(MCPError, match="outcome unknown"):
            await client.call_tool(name="record", arguments={"value": "once"}, timeout_ms=750)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_remote_tool_error_is_not_reported_as_success():
    server = MCPServer("local-test")

    @server.tool()
    def fail() -> str:
        raise ValueError("test failure")

    app = server.streamable_http_app(host="127.0.0.1", stateless_http=True, json_response=True)
    async with local_http(app) as url:
        cfg = MCPConfig(
            enabled=True,
            servers={"remote": MCPServerConfig(transport="streamable_http", url=url)},
        )
        manager = MCPClientManager(cfg)
        await manager.start()
        registry = ToolRegistry.isolated()
        manager.register_tools(registry)
        tool = registry.get("mcp_remote_fail")
        assert tool is not None
        result = await tool.execute({}, SimpleNamespace(call_id="test"))
        assert result["status"] == "error"
        assert result["message"] == "The tool reported an error."
        assert result["result"]["isError"] is True
        await manager.stop()


@pytest.mark.asyncio
async def test_tool_list_pagination_and_repeat_cursor_guard():
    first = SimpleNamespace(
        tools=[SimpleNamespace(model_dump=lambda **_: {"name": "one"})], next_cursor="next"
    )
    second = SimpleNamespace(
        tools=[SimpleNamespace(model_dump=lambda **_: {"name": "two"})], next_cursor=None
    )

    class PagedClient:
        def __init__(self, pages):
            self.pages = pages
            self.calls = []

        async def list_tools(self, *, cursor):
            self.calls.append(cursor)
            return self.pages[len(self.calls) - 1]

    client = MCPStreamableHTTPClient(server_id="remote", url="https://example.test/mcp", headers={})
    paged = PagedClient([first, second])
    assert [tool["name"] for tool in await client._list_all(paged)] == ["one", "two"]
    assert paged.calls == [None, "next"]
    repeated = PagedClient([first, first])
    with pytest.raises(MCPProtocolError, match="repeated"):
        await client._list_all(repeated)


@pytest.mark.asyncio
async def test_legacy_server_fallback_negotiates_without_fixed_version():
    server = MCPServer("legacy-local")

    @server.tool()
    def echo(value: str) -> str:
        return value

    inner_app = server.streamable_http_app(host="127.0.0.1", stateless_http=True, json_response=True)
    methods = []

    async def legacy_app(scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            await inner_app(scope, receive, send)
            return
        chunks = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        request = json.loads(body)
        methods.append(request.get("method"))
        if request.get("method") == "server/discover":
            await JSONResponse(
                {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Method not found"}}
            )(scope, receive, send)
            return
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await inner_app(scope, replay, send)

    async with local_http(legacy_app) as url:
        client = MCPStreamableHTTPClient(server_id="remote", url=url, headers={})
        assert [tool["name"] for tool in await client.list_tools()] == ["echo"]
        result = await client.call_tool(name="echo", arguments={"value": "legacy"})
        assert result["content"][0]["text"] == "legacy"
    assert "server/discover" in methods
    assert "initialize" in methods


@pytest.mark.asyncio
async def test_expired_session_is_replaced_on_next_operation():
    server = MCPServer("local-expiry")

    @server.tool()
    def echo(value: str) -> str:
        return value

    app = server.streamable_http_app(
        host="127.0.0.1", stateless_http=False, json_response=True, session_idle_timeout=0.1
    )
    async with local_http(app) as url:
        client = MCPStreamableHTTPClient(server_id="remote", url=url, headers={})
        assert [tool["name"] for tool in await client.list_tools()] == ["echo"]
        await asyncio.sleep(0.3)
        result = await client.call_tool(name="echo", arguments={"value": "after-expiry"})
        assert result["content"][0]["text"] == "after-expiry"


@pytest.mark.asyncio
async def test_concurrent_calls_have_independent_sessions():
    server = MCPServer("local-concurrent")

    @server.tool()
    async def echo(value: str) -> str:
        await asyncio.sleep(0.02)
        return value

    app = server.streamable_http_app(host="127.0.0.1", stateless_http=True, json_response=True)
    async with local_http(app) as url:
        client = MCPStreamableHTTPClient(server_id="remote", url=url, headers={})
        results = await asyncio.gather(
            client.call_tool(name="echo", arguments={"value": "first"}),
            client.call_tool(name="echo", arguments={"value": "second"}),
        )
        assert [result["content"][0]["text"] for result in results] == ["first", "second"]


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/mcp",
        "https://user:secret@example.test/mcp",
        "https://example.test/mcp?token=secret",
        "https://example.test/mcp#fragment",
        "https://example.test:bad/mcp",
    ],
)
def test_rejects_url_with_ambiguous_or_embedded_auth(url):
    with pytest.raises(MCPError, match="invalid HTTP URL"):
        MCPStreamableHTTPClient(server_id="remote", url=url, headers={})


def test_requires_secret_environment_reference_and_rejects_protocol_headers(monkeypatch):
    client = MCPStreamableHTTPClient(
        server_id="remote",
        url="https://example.test/mcp",
        headers={"Authorization": "Bearer literal-secret"},
    )
    with pytest.raises(MCPError, match="environment reference"):
        client._resolve_headers()
    client.headers = {"MCP-Protocol-Version": "2024-11-05"}
    with pytest.raises(MCPError, match="invalid header configuration"):
        client._resolve_headers()
    client.headers = {"Authorization": "Bearer ${MISSING_SECRET}"}
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    with pytest.raises(MCPError, match="missing a header environment variable"):
        client._resolve_headers()
