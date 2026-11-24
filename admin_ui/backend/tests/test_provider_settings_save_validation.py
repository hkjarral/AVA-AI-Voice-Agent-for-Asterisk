"""Admin saves use runtime model/voice contracts without needing vendor keys."""
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api import config as config_api


def _legacy_provider_config():
    return {"default_provider": "local", "providers": {
        "local": {},
        "unused_tts": {"type": "openai", "tts_model": "gpt-realtime-2.1-mini", "tts_transport": "speech"},
    }}


def test_unrelated_save_keeps_an_unchanged_unused_legacy_provider(monkeypatch, tmp_path):
    previous = _legacy_provider_config()
    monkeypatch.setattr(config_api.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: deepcopy(previous))
    desired = {**deepcopy(previous), "audio_transport": "audiosocket"}
    result = config_api._validate_ai_agent_config(yaml.safe_dump(desired))
    assert result["provider_settings_scope"] == []


@pytest.mark.parametrize("patch", [{"tts_model": "gpt-realtime-2.1"}, {"api_key_env": "NEW_KEY"}])
def test_changed_provider_settings_and_explicit_credentials_remain_validated(monkeypatch, tmp_path, patch):
    previous = _legacy_provider_config()
    monkeypatch.setattr(config_api.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: deepcopy(previous))
    desired = deepcopy(previous)
    desired["providers"]["unused_tts"].update(patch)
    with pytest.raises(HTTPException, match="providers.unused_tts.settings"):
        config_api._validate_ai_agent_config(yaml.safe_dump(desired))


def test_changed_pipeline_validates_its_previously_untouched_provider(monkeypatch, tmp_path):
    previous = _legacy_provider_config()
    previous["pipelines"] = {"test": {"stt": "local_stt", "llm": "local_llm", "tts": "local_tts"}}
    monkeypatch.setattr(config_api.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: deepcopy(previous))
    desired = deepcopy(previous)
    desired["pipelines"]["test"]["tts"] = "unused_tts"
    with pytest.raises(HTTPException, match="providers.unused_tts.settings"):
        config_api._validate_ai_agent_config(yaml.safe_dump(desired))


def test_changed_default_validates_its_previously_untouched_provider(monkeypatch, tmp_path):
    previous = _legacy_provider_config()
    previous["providers"]["unused_agent"] = {"type": "openai_realtime", "reasoning_effort": "invalid"}
    monkeypatch.setattr(config_api.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: deepcopy(previous))
    desired = {**deepcopy(previous), "default_provider": "unused_agent"}
    with pytest.raises(HTTPException, match="providers.unused_agent.reasoning_effort"):
        config_api._validate_ai_agent_config(yaml.safe_dump(desired))


def test_shared_openai_edits_include_inheriting_canonical_roles():
    previous = {"providers": {"openai": {"chat_model": "old"}, "openai_tts": {}, "openai_llm": {}, "custom_tts": {"type": "openai"}}}
    desired = deepcopy(previous)
    desired["providers"]["openai"]["chat_model"] = "new"
    assert config_api._affected_provider_settings(previous, desired, desired) == {"openai", "openai_tts", "openai_llm"}


def test_changed_shorthand_pipeline_includes_its_shared_provider_dependency():
    previous = {"providers": {"deepgram": {"tts_model": "legacy"}}}
    desired = {**deepcopy(previous), "pipelines": {"new": "deepgram"}}
    effective = {**deepcopy(previous), "pipelines": {"new": {"stt": "deepgram_stt", "llm": "deepgram_llm", "tts": "deepgram_tts"}}}
    assert config_api._affected_provider_settings(previous, desired, effective) == {"deepgram"}


@pytest.mark.parametrize("referenced", [True, False])
def test_deleted_role_validates_shared_fallback_only_when_still_referenced(monkeypatch, tmp_path, referenced):
    previous = {"default_provider": "local", "providers": {
        "local": {},
        "openai": {"tts_model": "gpt-realtime-2.1-mini", "tts_transport": "speech"},
        "openai_tts": {"type": "openai", "tts_model": "tts-1", "tts_transport": "speech"},
    }, "pipelines": {"test": {
        "stt": "local_stt", "llm": "local_llm", "tts": "openai_tts" if referenced else "local_tts",
    }}}
    monkeypatch.setattr(config_api.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: deepcopy(previous))
    desired = deepcopy(previous)
    del desired["providers"]["openai_tts"]
    if referenced:
        with pytest.raises(HTTPException, match="providers.openai.settings"):
            config_api._validate_ai_agent_config(yaml.safe_dump(desired))
    else:
        assert config_api._validate_ai_agent_config(yaml.safe_dump(desired))["provider_settings_scope"] == []


def test_deleted_role_does_not_revalidate_an_unrelated_unused_canonical_role(monkeypatch, tmp_path):
    previous = {"default_provider": "local", "providers": {
        "local": {},
        "openai": {"tts_model": "tts-1", "tts_transport": "speech"},
        "openai_tts": {"type": "openai", "tts_model": "tts-1-hd", "tts_transport": "speech"},
        "openai_stt": {"type": "openai", "tts_model": "gpt-realtime-2.1-mini", "tts_transport": "speech"},
    }, "pipelines": {"test": {"stt": "local_stt", "llm": "local_llm", "tts": "openai_tts"}}}
    monkeypatch.setattr(config_api.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: deepcopy(previous))
    desired = deepcopy(previous)
    del desired["providers"]["openai_tts"]
    assert config_api._validate_ai_agent_config(yaml.safe_dump(desired))["provider_settings_scope"] == ["openai"]


def test_automatic_secret_migration_preserves_original_validation_scope(monkeypatch, tmp_path):
    previous = _legacy_provider_config()
    previous["providers"]["unused_tts"]["api_key"] = "legacy-private-key"
    monkeypatch.setattr(config_api.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: deepcopy(previous))
    monkeypatch.setattr(config_api, "_read_base_config_dict", lambda: deepcopy(previous))
    saved = []
    monkeypatch.setattr(config_api, "_write_local_config", lambda content, **kwargs: saved.append(content))
    def migrate(doc):
        provider = doc["providers"]["unused_tts"]
        provider.pop("api_key")
        provider["api_key_file"] = "/managed/provider-secret"
        return True
    monkeypatch.setattr(config_api, "_migrate_inline_provider_secrets", migrate)
    desired = {**deepcopy(previous), "audio_transport": "audiosocket"}
    assert config_api.persist_config_content(yaml.safe_dump(desired))["status"] == "success"
    assert len(saved) == 1
    assert "legacy-private-key" not in saved[0]


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
