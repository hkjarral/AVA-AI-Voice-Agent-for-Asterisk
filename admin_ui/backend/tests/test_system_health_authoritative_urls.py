import asyncio
import json
import sys
from pathlib import Path

import httpx
import websockets

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import system  # noqa: E402
import settings  # noqa: E402


class _LocalAIConnection:
    def __init__(self, uri, calls, *, succeeds):
        self.uri = uri
        self.calls = calls
        self.succeeds = succeeds

    async def __aenter__(self):
        self.calls.append(self.uri)
        if not self.succeeds:
            raise ConnectionRefusedError(self.uri)
        return self

    async def __aexit__(self, *_args):
        return False

    async def send(self, _payload):
        return None

    async def recv(self):
        return json.dumps({"type": "status_response", "models": {}})


class _AIEngineResponse:
    status_code = 200

    @staticmethod
    def json():
        return {"status": "healthy"}


class _AIEngineClient:
    def __init__(self, calls, *, succeeds):
        self.calls = calls
        self.succeeds = succeeds

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url):
        self.calls.append(url)
        if not self.succeeds(url):
            raise httpx.ConnectError(f"unreachable: {url}")
        return _AIEngineResponse()


def test_explicit_health_urls_are_authoritative(monkeypatch):
    local_url = "ws://configured-local-ai:9000"
    engine_url = "http://configured-ai-engine:16000/health"
    local_calls = []
    engine_calls = []

    monkeypatch.setattr(
        system,
        "_dotenv_value",
        lambda key: {
            "HEALTH_CHECK_LOCAL_AI_URL": local_url,
            "HEALTH_CHECK_AI_ENGINE_URL": engine_url,
        }.get(key),
    )
    monkeypatch.setattr(settings, "get_setting", lambda _key, default=None: default)
    monkeypatch.delenv("LOCAL_WS_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        websockets,
        "connect",
        lambda uri, **_kwargs: _LocalAIConnection(
            uri, local_calls, succeeds=False
        ),
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **_kwargs: _AIEngineClient(
            engine_calls, succeeds=lambda _url: False
        ),
    )

    result = asyncio.run(system.get_system_health())

    assert local_calls == [local_url]
    assert engine_calls == [engine_url]
    assert result["local_ai_server"]["status"] == "error"
    assert result["local_ai_server"]["probe"]["attempted"] == [local_url]
    assert result["ai_engine"]["status"] == "error"
    assert result["ai_engine"]["probe"]["attempted"] == [engine_url]


def test_unset_health_urls_keep_discovery_fallbacks(monkeypatch):
    local_calls = []
    engine_calls = []
    monkeypatch.setattr(system, "_dotenv_value", lambda _key: None)
    monkeypatch.setattr(settings, "get_setting", lambda _key, default=None: default)
    monkeypatch.delenv("HEALTH_CHECK_LOCAL_AI_URL", raising=False)
    monkeypatch.delenv("HEALTH_CHECK_AI_ENGINE_URL", raising=False)
    monkeypatch.delenv("LOCAL_WS_AUTH_TOKEN", raising=False)

    def local_connect(uri, **_kwargs):
        return _LocalAIConnection(
            uri,
            local_calls,
            succeeds=uri == "ws://local_ai_server:8765",
        )

    monkeypatch.setattr(websockets, "connect", local_connect)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **_kwargs: _AIEngineClient(
            engine_calls,
            succeeds=lambda url: url == "http://ai_engine:15000/health",
        ),
    )

    result = asyncio.run(system.get_system_health())

    assert local_calls == [
        "ws://127.0.0.1:8765",
        "ws://local_ai_server:8765",
    ]
    assert engine_calls == [
        "http://127.0.0.1:15000/health",
        "http://ai_engine:15000/health",
    ]
    assert result["local_ai_server"]["status"] == "connected"
    assert result["local_ai_server"]["probe"]["selected"] == local_calls[-1]
    assert result["ai_engine"]["status"] == "connected"
    assert result["ai_engine"]["probe"]["selected"] == engine_calls[-1]
