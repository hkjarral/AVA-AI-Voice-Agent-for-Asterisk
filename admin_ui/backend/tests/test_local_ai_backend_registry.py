"""HTTP regressions for the documented Local AI registry endpoints (#856)."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings
from api import local_ai


ENDPOINTS = [
    ("/backends", {"type": "backends"}),
    ("/backends/stt/vosk/schema", {
        "type": "backend_schema", "backend_type": "stt", "backend_name": "vosk",
    }),
]


@pytest.fixture
def registry(monkeypatch):
    values = {"HEALTH_CHECK_LOCAL_AI_URL": "ws://configured-local-ai:8765"}
    monkeypatch.setattr(settings, "get_setting", lambda key, default="": values.get(key, default))
    ws = AsyncMock()
    connection = AsyncMock()
    connection.__aenter__.return_value = ws
    connect = Mock(return_value=connection)
    monkeypatch.setattr(local_ai.websockets, "connect", connect)
    return values, ws, connect, connection


async def get(path):
    app = FastAPI()
    app.include_router(local_ai.router, prefix="/api/local-ai")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/api/local-ai" + path)


@pytest.mark.asyncio
@pytest.mark.parametrize("path,command", ENDPOINTS)
@pytest.mark.parametrize("authenticated", [False, True])
async def test_registry_uses_configured_url_and_returns_server_response(registry, path, command, authenticated):
    values, ws, connect, connection = registry
    payload = {"type": command["type"] + "_response", "schema": {"type": "object"}}
    if authenticated:
        values["LOCAL_WS_AUTH_TOKEN"] = " synthetic-token "
        ws.recv.side_effect = [json.dumps({"type": "auth_response", "status": "ok"}), json.dumps(payload)]
    else:
        ws.recv.return_value = json.dumps(payload)
    response = await get(path)
    assert response.status_code == 200, response.text
    assert response.json() == payload
    connect.assert_called_once_with(values["HEALTH_CHECK_LOCAL_AI_URL"], open_timeout=5, close_timeout=5, proxy=None)
    messages = [json.loads(call.args[0]) for call in ws.send.await_args_list]
    assert messages == ([{"type": "auth", "auth_token": "synthetic-token"}] if authenticated else []) + [command]
    connection.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("path,command", ENDPOINTS)
async def test_registry_connection_failure_is_503(registry, path, command):
    _, _, connect, _ = registry
    connect.side_effect = OSError("server unavailable")
    response = await get(path)
    assert response.status_code == 503
    assert "server unavailable" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,command", ENDPOINTS)
async def test_registry_rejects_failed_auth_without_sending_command(registry, path, command):
    values, ws, _, connection = registry
    values["LOCAL_WS_AUTH_TOKEN"] = "synthetic-token"
    ws.recv.return_value = json.dumps({"type": "auth_response", "status": "error"})
    response = await get(path)
    assert response.status_code == 503
    assert "authentication failed" in response.json()["detail"]
    assert ws.send.await_count == 1
    connection.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("path,command", ENDPOINTS)
async def test_registry_bounds_auth_and_response_waits(monkeypatch, registry, authenticated, path, command):
    values, ws, _, connection = registry
    if authenticated:
        values["LOCAL_WS_AUTH_TOKEN"] = "synthetic-token"
    async def never_respond():
        await asyncio.Future()
    ws.recv.side_effect = never_respond
    wait_for = asyncio.wait_for
    budgets = []
    async def short_wait(awaitable, timeout):
        budgets.append(timeout)
        return await wait_for(awaitable, timeout=0.01)
    monkeypatch.setattr(local_ai.asyncio, "wait_for", short_wait)
    async with asyncio.timeout(1):
        response = await get(path)
    assert response.status_code == 503
    assert budgets == [5]
    connection.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_backend_schema_remains_404(registry):
    _, ws, _, _ = registry
    ws.recv.return_value = json.dumps({"type": "backend_schema_response", "error": "Unknown backend: missing"})
    response = await get("/backends/stt/missing/schema")
    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown backend: missing"}
