"""Admin saves use runtime model/voice contracts without needing vendor keys."""
import sys
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api import config as config_api


@pytest.mark.parametrize("settings", [
    {"type": "deepgram", "tts_model": "aura-2-thalia-en", "tts_version": "v2"},
    {"type": "groq", "tts_model": "canopylabs/orpheus-arabic-saudi", "voice": "hannah"},
    {"type": "openai", "tts_model": "gpt-realtime-2.1-mini", "tts_transport": "speech"},
    {"type": "elevenlabs", "model_id": "eleven_v3", "stability": 0.3},
])
def test_admin_save_rejects_incompatible_provider_settings(monkeypatch, tmp_path, settings):
    monkeypatch.setattr(config_api.settings, "ENV_PATH", str(tmp_path / ".env"))
    content = {"default_provider": "local", "providers": {
        "local": {}, "customer_tts": {**settings, "api_key": "must-not-leak"},
    }}
    with pytest.raises(HTTPException) as exc:
        config_api._validate_ai_agent_config(yaml.safe_dump(content))
    assert exc.value.status_code == 400
    assert "providers.customer_tts.settings" in exc.value.detail
    assert "must-not-leak" not in exc.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,role,env_name", [
    ("deepgram", "stt", "DEEPGRAM_API_KEY"),
    ("google", "stt", "GOOGLE_API_KEY"),
    ("groq", "tts", "GROQ_API_KEY"),
    ("azure", "stt", "AZURE_SPEECH_KEY"),
    ("cambai", "tts", "CAMB_API_KEY"),
    ("elevenlabs", "tts", "ELEVENLABS_API_KEY"),
])
async def test_custom_speech_credentials_upload_and_status(monkeypatch, tmp_path, kind, role, env_name):
    key = f"customer_{role}"
    content = {"providers": {key: {"type": kind, "capabilities": [role]}}}
    monkeypatch.setattr(config_api, "PROVIDER_SECRETS_ROOT", str(tmp_path / "providers"))
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: content)
    monkeypatch.setattr(config_api, "_save_merged_config", lambda saved: None)
    monkeypatch.setenv(env_name, "legacy-synthetic-key")
    before = await config_api.get_provider_credentials_status(key)
    assert before["credentials"]["api-key"]["env_var"] == env_name

    result = await config_api.upload_provider_api_key(key, {"api_key": "role-owned-secret"})
    assert result["status"] == "success"
    assert Path(result["path"]).read_text() == "role-owned-secret"
    assert content["providers"][key]["api_key_file"] == result["path"]
    after = await config_api.get_provider_credentials_status(key)
    assert after["credentials"]["api-key"]["source"] == "managed_file"
    assert "role-owned-secret" not in str(after)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,role", [("local", "stt"), ("elevenlabs", "stt"), ("invalid", "tts")])
async def test_unsupported_role_cannot_upload_credentials(monkeypatch, kind, role):
    key = f"customer_{role}"
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: {
        "providers": {key: {"type": kind, "capabilities": [role]}},
    })
    with pytest.raises(HTTPException) as exc:
        await config_api.upload_provider_api_key(key, {"api_key": "never-written"})
    assert exc.value.status_code == 400
