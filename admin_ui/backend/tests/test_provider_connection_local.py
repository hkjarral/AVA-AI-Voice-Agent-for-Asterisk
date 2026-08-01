import json
import sys
from pathlib import Path

import pytest
import websockets

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import config  # noqa: E402


class _FakeLocalAIWebSocket:
    def __init__(self, sent_messages, status_payload, auth_status="ok"):
        self._sent_messages = sent_messages
        self._status_payload = status_payload
        self._auth_status = auth_status
        self._last_message_type = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def send(self, raw):
        message = json.loads(raw)
        self._sent_messages.append(message)
        self._last_message_type = message.get("type")

    async def recv(self):
        if self._last_message_type == "auth":
            return json.dumps({"type": "auth_response", "status": self._auth_status})
        return json.dumps(self._status_payload)


def _status_payload(*, stt_loaded=True, llm_loaded=False, tts_loaded=True):
    return {
        "type": "status_response",
        "status": "ok",
        "stt_backend": "vosk",
        "tts_backend": "kokoro",
        "models": {
            "stt": {"loaded": stt_loaded},
            "llm": {"loaded": llm_loaded, "path": None},
            "tts": {"loaded": tts_loaded},
        },
    }


def _connect_factory(sent_messages, status_payload, auth_status="ok"):
    def _connect(*_args, **_kwargs):
        return _FakeLocalAIWebSocket(sent_messages, status_payload, auth_status)

    return _connect


@pytest.mark.asyncio
async def test_local_provider_authenticates_before_status(monkeypatch, tmp_path):
    sent_messages = []
    monkeypatch.setattr(config.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("LOCAL_WS_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(
        websockets,
        "connect",
        _connect_factory(sent_messages, _status_payload()),
    )

    result = await config.test_provider_connection(
        config.ProviderTestRequest(
            name="local_stt",
            config={
                "type": "local",
                "capabilities": ["stt"],
                "auth_token": "${LOCAL_WS_AUTH_TOKEN:-}",
                "ws_url": "ws://127.0.0.1:8765",
            },
        )
    )

    assert result["success"] is True
    assert sent_messages == [
        {"type": "auth", "auth_token": "test-secret"},
        {"type": "status"},
    ]


@pytest.mark.asyncio
async def test_local_provider_requires_only_declared_capabilities(monkeypatch, tmp_path):
    sent_messages = []
    monkeypatch.setattr(config.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.delenv("LOCAL_WS_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        websockets,
        "connect",
        _connect_factory(sent_messages, _status_payload()),
    )

    modular_result = await config.test_provider_connection(
        config.ProviderTestRequest(
            name="local_tts",
            config={
                "type": "local",
                "capabilities": ["tts"],
                "ws_url": "ws://127.0.0.1:8765",
            },
        )
    )
    full_result = await config.test_provider_connection(
        config.ProviderTestRequest(
            name="local",
            config={
                "type": "full",
                "capabilities": ["stt", "llm", "tts"],
                "ws_url": "ws://127.0.0.1:8765",
            },
        )
    )

    assert modular_result["success"] is True
    assert full_result["success"] is False
    assert sent_messages == [{"type": "status"}, {"type": "status"}]


@pytest.mark.asyncio
async def test_local_provider_stops_after_failed_auth(monkeypatch, tmp_path):
    sent_messages = []
    monkeypatch.setattr(config.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("LOCAL_WS_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(
        websockets,
        "connect",
        _connect_factory(sent_messages, _status_payload(), auth_status="error"),
    )

    result = await config.test_provider_connection(
        config.ProviderTestRequest(
            name="local_stt",
            config={
                "type": "local",
                "capabilities": ["stt"],
                "auth_token": "${LOCAL_WS_AUTH_TOKEN:-}",
                "ws_url": "ws://127.0.0.1:8765",
            },
        )
    )

    assert result["success"] is False
    assert "test-secret" not in result["message"]
    assert sent_messages == [{"type": "auth", "auth_token": "test-secret"}]
