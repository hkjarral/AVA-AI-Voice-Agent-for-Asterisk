"""GET /api/config/providers/meta — per-provider voice metadata for the Agent form."""
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import config  # noqa: E402


def _client(monkeypatch, providers):
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: {"providers": providers})
    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")
    return TestClient(app)


def test_meta_reports_full_agents_with_voice_info(monkeypatch):
    client = _client(monkeypatch, {
        "openai_realtime": {"type": "openai_realtime", "voice": "marin", "enabled": True},
        "elevenlabs": {"type": "elevenlabs_agent", "agent_id": "abc", "enabled": True},
        "grok": {"voice": "eve"},  # legacy canonical key, no explicit type
    })
    body = client.get("/api/config/providers/meta").json()
    by_name = {p["name"]: p for p in body["providers"]}

    openai = by_name["openai_realtime"]
    assert openai["is_full_agent"] is True
    assert openai["voice_mode"] == "static"
    assert openai["default_voice"] == "marin"
    assert any(v["id"] == "cedar" for v in openai["voices"])

    el = by_name["elevenlabs"]
    assert el["voice_mode"] == "platform_managed"
    assert el["voices"] == []

    grok = by_name["grok"]
    assert grok["is_full_agent"] is True
    assert grok["voice_mode"] == "freeform"
    assert grok["default_voice"] == "eve"


def test_meta_marks_modular_providers_not_full_agent(monkeypatch):
    client = _client(monkeypatch, {
        "azure_tts": {"type": "azure", "voice_name": "en-US-JennyNeural"},
    })
    body = client.get("/api/config/providers/meta").json()
    entry = body["providers"][0]
    assert entry["is_full_agent"] is False
    assert entry["voice_mode"] == "unsupported"


def test_meta_survives_non_dict_and_empty_providers(monkeypatch):
    client = _client(monkeypatch, {"weird": "not-a-dict"})
    assert client.get("/api/config/providers/meta").json() == {"providers": []}

    client = _client(monkeypatch, {})
    assert client.get("/api/config/providers/meta").json() == {"providers": []}
