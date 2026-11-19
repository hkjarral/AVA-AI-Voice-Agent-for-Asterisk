"""Exercise real Local AI connections with an ambient proxy configured."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import settings  # noqa: E402
from api import config, local_ai  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["status", "capabilities", "provider_test", "backends", "backend_schema"])
async def test_local_ai_connects_directly_despite_environment_proxy(monkeypatch, operation):
    proxy_requests = []
    messages = []
    token = "synthetic-direct-connection-token"

    async def reject_proxy(reader, writer):
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
            proxy_requests.append(request.split(b"\r\n", 1)[0])
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def local_server(ws):
        auth = json.loads(await ws.recv())
        messages.append(auth)
        assert auth == {"type": "auth", "auth_token": token}
        await ws.send(json.dumps({"type": "auth_response", "status": "ok"}))
        command = json.loads(await ws.recv())
        messages.append(command)
        if command["type"] == "capabilities":
            payload = {"type": "capabilities_response", "capabilities": {"vosk": True, "piper": True}}
        elif command["type"] == "backends":
            payload = {"type": "backends_response", "stt": {"vosk": {}}, "tts": {}, "llm": {}}
        elif command["type"] == "backend_schema":
            assert command == {"type": "backend_schema", "backend_type": "stt", "backend_name": "vosk"}
            payload = {"type": "backend_schema_response", "schema": {"type": "object"}}
        else:
            payload = {
                "type": "status_response", "status": "ok", "stt_backend": "vosk",
                "tts_backend": "piper", "models": {"stt": {"loaded": True}, "tts": {"loaded": True}},
            }
        await ws.send(json.dumps(payload))

    async with await asyncio.start_server(reject_proxy, "127.0.0.1", 0) as proxy:
        proxy_url = f"http://127.0.0.1:{proxy.sockets[0].getsockname()[1]}"
        for name in ("ws_proxy", "wss_proxy", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.setenv(name, proxy_url)
            monkeypatch.setenv(name.upper(), proxy_url)
        monkeypatch.setenv("no_proxy", "")
        monkeypatch.setenv("NO_PROXY", "")
        async with serve(local_server, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            values = {"HEALTH_CHECK_LOCAL_AI_URL": url, "LOCAL_WS_AUTH_TOKEN": token}
            monkeypatch.setattr(settings, "get_setting", lambda key, default=None: values.get(key, default))
            if operation == "status":
                result = await asyncio.wait_for(local_ai.get_local_ai_status(), timeout=3)
                assert result["connected"] is True, result
            elif operation == "capabilities":
                result = await asyncio.wait_for(local_ai.get_backend_capabilities(), timeout=3)
                assert "error" not in result, result
                assert result["stt"]["vosk"]["available"] is True
            elif operation == "backends":
                result = await asyncio.wait_for(local_ai.list_backends(), timeout=3)
                assert result["type"] == "backends_response"
                assert "vosk" in result["stt"]
            elif operation == "backend_schema":
                result = await asyncio.wait_for(local_ai.get_backend_schema("stt", "vosk"), timeout=3)
                assert result == {"type": "backend_schema_response", "schema": {"type": "object"}}
            else:
                request = config.ProviderTestRequest(name="local", config={
                    "type": "local", "capabilities": ["stt", "tts"],
                    "ws_url": url, "base_url": url, "auth_token": token,
                })
                result = await asyncio.wait_for(config.test_provider_connection(request), timeout=3)
                assert result["success"] is True, result
    assert proxy_requests == [], "Local AI connections must not reach the ambient proxy"
    assert messages[0] == {"type": "auth", "auth_token": token}
    expected_command = operation if operation in {"capabilities", "backends", "backend_schema"} else "status"
    assert messages[1]["type"] == expected_command
