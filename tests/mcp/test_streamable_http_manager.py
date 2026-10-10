import asyncio

from src.config import MCPConfig, MCPServerConfig
from src.mcp.manager import MCPClientManager
from src.mcp.streamable_http_client import MCPStreamableHTTPClient


def test_manager_builds_streamable_http_client(monkeypatch):
    calls = []
    monkeypatch.setenv("MCP_TEST_AUTH", "fixture")

    async def fake_start(self):
        calls.append((self.server_id, self.url, dict(self.headers)))

    async def fake_list_tools(self):
        return [
            {
                "name": "health",
                "description": "Health",
                "inputSchema": {"type": "object"},
            }
        ]

    async def fake_stop(self):
        return None

    monkeypatch.setattr(MCPStreamableHTTPClient, "start", fake_start)
    monkeypatch.setattr(MCPStreamableHTTPClient, "list_tools", fake_list_tools)
    monkeypatch.setattr(MCPStreamableHTTPClient, "stop", fake_stop)

    config = MCPConfig(
        enabled=True,
        servers={
            "isla": MCPServerConfig(
                transport="streamable-http",
                url="https://isla.example/mcp/codex",
                headers={"Authorization": "Bearer ${MCP_TEST_AUTH}"},
            )
        },
    )
    manager = MCPClientManager(config)
    asyncio.run(manager.start())

    assert calls == [
        ("isla", "https://isla.example/mcp/codex", {"Authorization": "Bearer ${MCP_TEST_AUTH}"})
    ]
    assert manager._clients["isla"]._resolve_headers() == {"Authorization": "Bearer fixture"}
    status = manager.get_status()
    assert status["servers"]["isla"]["up"] is True
    assert status["servers"]["isla"]["transport"] == "streamable-http"
    assert "headers" not in status["servers"]["isla"]
    asyncio.run(manager.stop())
