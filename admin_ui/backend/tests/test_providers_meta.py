"""GET /api/config/providers/meta — per-provider voice metadata for the Agent form."""
import sys
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

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
        "deepgram_es": {
            "type": "deepgram",
            "tts_model": "aura-2-celeste-es",
            "agent_language": "es-419",
            "enabled": True,
        },
        "elevenlabs": {"type": "elevenlabs_agent", "agent_id": "abc", "enabled": True},
        "grok": {"voice": "eve"},  # legacy canonical key, no explicit type
    })
    body = client.get("/api/config/providers/meta").json()
    by_name = {p["name"]: p for p in body["providers"]}

    openai = by_name["openai_realtime"]
    assert openai["is_full_agent"] is True
    assert openai["voice_mode"] == "static"
    assert openai["default_voice"] == "marin"
    assert openai["agent_language"] is None
    assert any(v["id"] == "cedar" for v in openai["voices"])

    deepgram = by_name["deepgram_es"]
    assert deepgram["kind"] == "deepgram"
    assert deepgram["default_voice"] == "aura-2-celeste-es"
    assert deepgram["agent_language"] == "es"
    assert deepgram["tts_version"] == "v1"
    # The backend retains the complete catalog; the Agent form applies the
    # instance-specific language filter while preserving a stored mismatch.
    assert any(v["id"] == "aura-2-thalia-en" for v in deepgram["voices"])
    assert any(v["id"] == "aura-2-celeste-es" for v in deepgram["voices"])

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
    assert entry["agent_language"] is None


def test_meta_defaults_deepgram_agent_language_to_english(monkeypatch):
    client = _client(monkeypatch, {
        "deepgram": {"type": "deepgram", "tts_model": "aura-2-thalia-en"},
    })

    entry = client.get("/api/config/providers/meta").json()["providers"][0]

    assert entry["agent_language"] == "en"


def test_meta_exposes_flux_tts_version_for_agent_voice_filter(monkeypatch):
    client = _client(monkeypatch, {
        "deepgram": {"type": "deepgram", "tts_model": "flux-kit-en", "tts_version": "v2"},
    })
    entry = client.get("/api/config/providers/meta").json()["providers"][0]
    assert entry["tts_version"] == "v2"
    assert any(v["id"] == "flux-kit-en" for v in entry["voices"])


@pytest.mark.parametrize("expression,value,expected", [
    ("${DG_TTS_VERSION:-v2}", None, "v2"),
    ("${DG_TTS_VERSION:=v2}", "", "v2"),
    ("${DG_TTS_VERSION}", "v2", "v2"),
    ("$DG_TTS_VERSION", "v2", "v2"),
    (None, None, "v1"),
])
def test_meta_expands_deepgram_version_like_runtime_loader(monkeypatch, tmp_path, expression, value, expected):
    monkeypatch.setattr(config.settings, "ENV_PATH", str(tmp_path / ".env"))
    if value is None:
        monkeypatch.delenv("DG_TTS_VERSION", raising=False)
    else:
        monkeypatch.setenv("DG_TTS_VERSION", value)
    client = _client(monkeypatch, {"deepgram": {"type": "deepgram", "tts_version": expression}})
    assert client.get("/api/config/providers/meta").json()["providers"][0]["tts_version"] == expected


def test_meta_uses_dotenv_version_without_changing_process_environment(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("DG_TTS_VERSION=v2\n")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env))
    monkeypatch.setenv("DG_TTS_VERSION", "v1")
    client = _client(monkeypatch, {"deepgram": {"type": "deepgram", "tts_version": "${DG_TTS_VERSION:-v1}"}})
    assert client.get("/api/config/providers/meta").json()["providers"][0]["tts_version"] == "v2"
    assert os.environ["DG_TTS_VERSION"] == "v1"


def test_legacy_options_endpoint_uses_verified_shared_models(monkeypatch):
    client = _client(monkeypatch, {})
    options = client.get("/api/config/options/openai").json()
    assert options["defaults"]["tts_model"] == "gpt-realtime-2.1-mini"
    assert "gpt-transcribe" in {m["id"] for m in options["stt_models"]}
    assert "gpt-4-turbo" not in {m["id"] for m in options["llm_models"]}
    elevenlabs = client.get("/api/config/options/elevenlabs").json()
    assert "eleven_monolingual_v1" not in {m["id"] for m in elevenlabs["models"]}
    google = client.get("/api/config/options/google").json()
    assert {v["id"] for v in google["voices"]} == {
        "en-US-Standard-A", "en-US-Standard-B", "en-US-Neural2-A",
        "en-US-Neural2-C", "en-US-Studio-O", "en-US-Studio-Q",
    }
    assert google["defaults"]["tts_voice_name"] in {v["id"] for v in google["voices"]}


def test_meta_survives_non_dict_and_empty_providers(monkeypatch):
    client = _client(monkeypatch, {"weird": "not-a-dict"})
    assert client.get("/api/config/providers/meta").json() == {"providers": []}

    client = _client(monkeypatch, {})
    assert client.get("/api/config/providers/meta").json() == {"providers": []}


def test_grok_editor_and_agent_share_catalog_even_for_first_instance(monkeypatch):
    empty_client = _client(monkeypatch, {})
    catalog = empty_client.get("/api/config/providers/voice-catalog/grok").json()
    client = _client(monkeypatch, {"customer_grok": {"type": "grok", "voice": "myCloneID"}})
    agent_meta, = client.get("/api/config/providers/meta").json()["providers"]
    assert agent_meta["voices"] == catalog["voices"]
    assert len(catalog["voices"]) == 28
    assert agent_meta["default_voice"] == "myCloneID"


def test_meta_never_returns_a_non_version_credential_from_expansion(monkeypatch, tmp_path):
    from secrets import token_hex

    value = token_hex()
    env = tmp_path / ".env"
    env.write_text(f"TEST_API_KEY={value}\n")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env))
    client = _client(monkeypatch, {"deepgram": {"type": "deepgram", "tts_version": "${TEST_API_KEY}"}})
    response = client.get("/api/config/providers/meta")
    assert value not in response.text
    assert response.json()["providers"][0]["tts_version"] == "v1"
