"""Contextual provider/profile/pipeline audio baseline recovery."""

import asyncio
import sqlite3
import sys
import threading
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import config  # noqa: E402
from src.config.audio_baselines import (  # noqa: E402
    BUILTIN_PROFILE_BASELINES,
    FULL_AGENT_AUDIO_FIELDS,
    PROVIDER_AUDIO_BASELINES,
    provider_audio_fields,
)
from src.config.normalization import normalize_legacy_openai_audio  # noqa: E402
from src.config.provider_instances import FULL_AGENT_KINDS  # noqa: E402


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")
    return TestClient(app)


def _success_result() -> dict:
    return {
        "status": "success",
        "apply_required": True,
        "restart_required": False,
        "recommended_apply_method": "hot_reload",
        "apply_plan": [],
        "message": "saved",
        "warnings": [],
    }


def test_provider_reset_uses_named_instance_kind_and_preserves_non_audio(
    monkeypatch,
):
    original = {
        "providers": {
            "customer_google": {
                "type": "google_live",
                "enabled": False,
                "api_key_file": "/run/secrets/google",
                "llm_model": "custom-model",
                "tts_voice_name": "Puck",
                "instructions": "Keep this prompt",
                "input_encoding": "linear16",
                "input_sample_rate_hz": 48000,
                "provider_input_encoding": "mulaw",
                "provider_input_sample_rate_hz": 8000,
                "output_encoding": "mulaw",
                "output_sample_rate_hz": 8000,
                "target_encoding": "linear16",
                "target_sample_rate_hz": 48000,
                "output_resampler": "bandlimited",
            }
        }
    }
    captured = {}
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: deepcopy(original))

    async def fake_persist(merged, *, trusted_profile_reset=None):
        captured["merged"] = deepcopy(merged)
        captured["trusted"] = trusted_profile_reset
        return _success_result()

    monkeypatch.setattr(config, "_persist_audio_reset", fake_persist)

    response = _client().post(
        "/api/config/providers/customer_google/audio/reset"
    )

    assert response.status_code == 200
    assert response.json()["provider_kind"] == "google_live"
    updated = captured["merged"]["providers"]["customer_google"]
    for key, expected in PROVIDER_AUDIO_BASELINES["google_live"].items():
        assert updated[key] == expected
    assert updated["enabled"] is False
    assert updated["api_key_file"] == "/run/secrets/google"
    assert updated["llm_model"] == "custom-model"
    assert updated["tts_voice_name"] == "Puck"
    assert updated["instructions"] == "Keep this prompt"
    assert captured["trusted"] is None


@pytest.mark.parametrize(
    "provider_key,provider_cfg,expected_kind",
    [
        (
            "customer_openai_tts",
            {"type": "openai", "capabilities": ["tts"], "voice": "nova"},
            "openai_tts",
        ),
        (
            "elevenlabs_tts",
            {"type": "elevenlabs", "capabilities": ["tts"], "voice_id": "abc"},
            "elevenlabs_tts",
        ),
    ],
)
def test_provider_reset_resolves_modular_audio_kind(
    monkeypatch, provider_key, provider_cfg, expected_kind
):
    monkeypatch.setattr(
        config,
        "_read_merged_config_dict",
        lambda: {"providers": {provider_key: deepcopy(provider_cfg)}},
    )

    async def fake_persist(merged, *, trusted_profile_reset=None):
        return _success_result()

    monkeypatch.setattr(config, "_persist_audio_reset", fake_persist)

    response = _client().post(f"/api/config/providers/{provider_key}/audio/reset")

    assert response.status_code == 200
    assert response.json()["provider_kind"] == expected_kind


def test_local_provider_baseline_does_not_write_ignored_resampler():
    assert PROVIDER_AUDIO_BASELINES["local"] == {
        "target_encoding": "mulaw",
        "target_sample_rate_hz": 8000,
    }


def test_provider_audio_fields_normalizes_kind_once():
    class ProviderKind:
        def __str__(self):
            return "google_live"

    assert provider_audio_fields(ProviderKind()) == FULL_AGENT_AUDIO_FIELDS


def test_full_agent_kinds_have_complete_audio_reset_registrations():
    assert FULL_AGENT_KINDS <= PROVIDER_AUDIO_BASELINES.keys()
    for kind in FULL_AGENT_KINDS:
        assert PROVIDER_AUDIO_BASELINES[kind].keys() <= provider_audio_fields(kind)


def test_profile_baseline_metadata_comes_from_canonical_registry():
    response = _client().get("/api/config/profiles/audio/baselines")

    assert response.status_code == 200
    assert response.json() == {
        "built_in_profiles": sorted(BUILTIN_PROFILE_BASELINES)
    }


def test_audio_reset_persistence_runs_blocking_write_off_event_loop(monkeypatch):
    caller_thread = threading.get_ident()
    captured = {}

    def fake_persist(content, *, trusted_profile_reset=None):
        captured["thread"] = threading.get_ident()
        captured["content"] = content
        captured["trusted"] = trusted_profile_reset
        return _success_result()

    async def fake_reconcile(result):
        return result

    monkeypatch.setattr(config, "persist_config_content", fake_persist)
    monkeypatch.setattr(
        config, "reconcile_apply_result_with_engine_state", fake_reconcile
    )

    result = asyncio.run(
        config._persist_audio_reset(
            {"profiles": {}},
            trusted_profile_reset=("example", {"chunk_ms": "auto"}),
        )
    )

    assert result == _success_result()
    assert captured["thread"] != caller_thread
    assert yaml.safe_load(captured["content"]) == {"profiles": {}}
    assert captured["trusted"] == ("example", {"chunk_ms": "auto"})


def test_profile_reset_restores_builtin_exactly(monkeypatch):
    original = {
        "profiles": {
            "default": "telephony_enhanced_8k",
            "telephony_enhanced_8k": {
                "output_resampler": "linear",
                "transport_out": {"encoding": "slin16", "sample_rate_hz": 16000},
            },
        }
    }
    captured = {}
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: deepcopy(original))

    async def fake_persist(merged, *, trusted_profile_reset=None):
        captured["merged"] = deepcopy(merged)
        captured["trusted"] = deepcopy(trusted_profile_reset)
        return _success_result()

    monkeypatch.setattr(config, "_persist_audio_reset", fake_persist)

    response = _client().post(
        "/api/config/profiles/telephony_enhanced_8k/audio/reset"
    )

    assert response.status_code == 200
    assert response.json()["baseline_kind"] == "built_in"
    expected = dict(BUILTIN_PROFILE_BASELINES["telephony_enhanced_8k"])
    assert captured["merged"]["profiles"]["telephony_enhanced_8k"] == expected
    assert captured["merged"]["profiles"]["default"] == "telephony_enhanced_8k"
    assert captured["trusted"] == ("telephony_enhanced_8k", expected)


def test_custom_profile_reset_preserves_name_and_uses_standard_telephony(
    monkeypatch,
):
    original = {
        "profiles": {
            "default": "custom_customer_audio",
            "custom_customer_audio": {
                "output_resampler": "bandlimited",
                "transport_out": {"encoding": "slin16", "sample_rate_hz": 16000},
            },
        }
    }
    captured = {}
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: deepcopy(original))

    async def fake_persist(merged, *, trusted_profile_reset=None):
        captured["merged"] = deepcopy(merged)
        captured["trusted"] = deepcopy(trusted_profile_reset)
        return _success_result()

    monkeypatch.setattr(config, "_persist_audio_reset", fake_persist)

    response = _client().post(
        "/api/config/profiles/custom_customer_audio/audio/reset"
    )

    assert response.status_code == 200
    assert response.json()["baseline_kind"] == "standard_telephony"
    expected = dict(BUILTIN_PROFILE_BASELINES["telephony_ulaw_8k"])
    assert "custom_customer_audio" in captured["merged"]["profiles"]
    assert captured["merged"]["profiles"]["custom_customer_audio"] == expected
    assert captured["merged"]["profiles"]["default"] == "custom_customer_audio"


def test_trusted_canonical_reset_can_repair_in_use_profile(tmp_path, monkeypatch):
    db_path = tmp_path / "agents.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE agents (slug TEXT, display_name TEXT, audio_profile TEXT)"
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?)",
            ("ava-demo", "Ava Demo", "telephony_ulaw_8k"),
        )
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))
    old = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "slin16"}},
        }
    }
    baseline = dict(BUILTIN_PROFILE_BASELINES["telephony_ulaw_8k"])
    new = deepcopy(old)
    new["profiles"]["telephony_ulaw_8k"] = deepcopy(baseline)

    config._assert_in_use_audio_profiles_unchanged(
        old,
        new,
        trusted_profile_reset=("telephony_ulaw_8k", baseline),
    )

    with pytest.raises(HTTPException) as exc_info:
        config._assert_in_use_audio_profiles_unchanged(old, new)
    assert exc_info.value.status_code == 409


def test_in_use_profile_reset_persists_through_shared_backup_path(
    tmp_path, monkeypatch
):
    base_path = tmp_path / "ai-agent.yaml"
    local_path = tmp_path / "ai-agent.local.yaml"
    env_path = tmp_path / ".env"
    base_path.write_text(Path(config.settings.CONFIG_PATH).read_text(), encoding="utf-8")
    local_path.write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    "telephony_ulaw_8k": {
                        "output_resampler": "bandlimited",
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    env_path.write_text("", encoding="utf-8")

    db_path = tmp_path / "agents.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE agents (slug TEXT, display_name TEXT, audio_profile TEXT)"
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?)",
            ("ava-demo", "Ava Demo", "telephony_ulaw_8k"),
        )

    monkeypatch.setattr(config.settings, "CONFIG_PATH", str(base_path))
    monkeypatch.setattr(config.settings, "LOCAL_CONFIG_PATH", str(local_path))
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))

    async def no_engine_state():
        return None

    monkeypatch.setattr(config, "_fetch_engine_config_state", no_engine_state)

    response = _client().post(
        "/api/config/profiles/telephony_ulaw_8k/audio/reset"
    )

    assert response.status_code == 200, response.text
    assert response.json()["recommended_apply_method"] in {
        "none",
        "hot_reload",
        "restart",
    }
    merged = config._read_merged_config_dict()
    assert merged["profiles"]["telephony_ulaw_8k"] == dict(
        BUILTIN_PROFILE_BASELINES["telephony_ulaw_8k"]
    )
    assert list(tmp_path.glob("ai-agent.local.yaml.bak.*"))


def test_trusted_reset_name_does_not_bypass_noncanonical_value(tmp_path, monkeypatch):
    db_path = tmp_path / "agents.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE agents (slug TEXT, display_name TEXT, audio_profile TEXT)"
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?)",
            ("ava-demo", "Ava Demo", "telephony_ulaw_8k"),
        )
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))
    old = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "ulaw"}},
        }
    }
    new = deepcopy(old)
    new["profiles"]["telephony_ulaw_8k"] = {
        "transport_out": {"encoding": "slin16"}
    }

    with pytest.raises(HTTPException) as exc_info:
        config._assert_in_use_audio_profiles_unchanged(
            old,
            new,
            trusted_profile_reset=(
                "telephony_ulaw_8k",
                dict(BUILTIN_PROFILE_BASELINES["telephony_ulaw_8k"]),
            ),
        )
    assert exc_info.value.status_code == 409


def test_pipeline_reset_removes_only_audio_overrides(monkeypatch):
    original = {
        "pipelines": {
            "hybrid": {
                "stt": "openai_stt",
                "llm": "openai_llm",
                "tts": "openai_tts",
                "tools": ["hangup_call"],
                "options": {
                    "stt": {
                        "encoding": "linear16",
                        "sample_rate": 48000,
                        "language": "en",
                    },
                    "llm": {"temperature": 0.2},
                    "tts": {
                        "format": {"encoding": "linear16", "sample_rate": 48000},
                        "target_encoding": "linear16",
                        "target_sample_rate_hz": 48000,
                        "output_resampler": "bandlimited",
                        "streaming_overlap": False,
                    },
                },
            }
        }
    }
    captured = {}
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: deepcopy(original))

    async def fake_persist(merged, *, trusted_profile_reset=None):
        captured["merged"] = deepcopy(merged)
        return _success_result()

    monkeypatch.setattr(config, "_persist_audio_reset", fake_persist)

    response = _client().post("/api/config/pipelines/hybrid/audio/reset")

    assert response.status_code == 200
    updated = captured["merged"]["pipelines"]["hybrid"]
    assert updated["stt"] == "openai_stt"
    assert updated["llm"] == "openai_llm"
    assert updated["tts"] == "openai_tts"
    assert updated["tools"] == ["hangup_call"]
    assert updated["options"]["stt"] == {"language": "en"}
    assert updated["options"]["llm"] == {"temperature": 0.2}
    assert updated["options"]["tts"] == {"streaming_overlap": False}


def test_pipeline_shorthand_reset_is_noop_without_persistence(monkeypatch):
    monkeypatch.setattr(
        config,
        "_read_merged_config_dict",
        lambda: {"pipelines": {"legacy": "openai_realtime"}},
    )

    async def fail_if_persisted(*args, **kwargs):
        pytest.fail("a shorthand pipeline reset must not rewrite config")

    monkeypatch.setattr(config, "_persist_audio_reset", fail_if_persisted)

    response = _client().post("/api/config/pipelines/legacy/audio/reset")

    assert response.status_code == 200
    assert response.json()["recommended_apply_method"] == "none"
    assert response.json()["apply_required"] is False
    assert response.json()["pipeline_name"] == "legacy"
    assert response.json()["removed_audio_overrides"] == {}


def test_pipeline_dict_without_audio_overrides_is_noop_without_persistence(
    monkeypatch,
):
    original = {
        "pipelines": {
            "hybrid": {
                "stt": "openai_stt",
                "llm": "openai_llm",
                "tts": "openai_tts",
                "options": {
                    "stt": {"language": "en"},
                    "llm": {"temperature": 0.2},
                    "tts": {"streaming_overlap": False},
                },
            }
        }
    }
    monkeypatch.setattr(
        config, "_read_merged_config_dict", lambda: deepcopy(original)
    )

    async def fail_if_persisted(*args, **kwargs):
        pytest.fail("a pipeline without audio overrides must not rewrite config")

    monkeypatch.setattr(config, "_persist_audio_reset", fail_if_persisted)

    response = _client().post("/api/config/pipelines/hybrid/audio/reset")

    assert response.status_code == 200
    assert response.json()["recommended_apply_method"] == "none"
    assert response.json()["apply_required"] is False
    assert response.json()["pipeline_name"] == "hybrid"
    assert response.json()["removed_audio_overrides"] == {}


def test_provider_validation_rejects_named_instance_g711_at_non_8khz():
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    parsed["providers"]["customer_google"] = {
        "type": "google_live",
        "capabilities": ["stt", "llm", "tts"],
        "input_encoding": "ulaw",
        "input_sample_rate_hz": 16000,
    }

    with pytest.raises(HTTPException) as exc_info:
        config._validate_ai_agent_config(yaml.safe_dump(parsed, sort_keys=False))

    assert exc_info.value.status_code == 400
    detail = str(exc_info.value.detail)
    assert "providers.customer_google" in detail
    assert "input_encoding/input_sample_rate_hz" in detail
    assert "ulaw requires 8000 Hz" in detail
    assert "/api/config/providers/customer_google/audio/reset" in detail


def test_provider_validation_rejects_fractional_g711_sample_rate():
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    parsed["providers"]["customer_google"] = {
        "type": "google_live",
        "capabilities": ["stt", "llm", "tts"],
        "input_encoding": "ulaw",
        "input_sample_rate_hz": 8000.5,
    }

    with pytest.raises(HTTPException) as exc_info:
        config._validate_ai_agent_config(yaml.safe_dump(parsed, sort_keys=False))

    assert exc_info.value.status_code == 400
    assert (
        "providers.customer_google.input_sample_rate_hz must be an integer"
        in str(exc_info.value.detail)
    )


@pytest.mark.parametrize("valid_rate", [8000, 8000.0, "8000"])
def test_provider_validation_accepts_integral_g711_sample_rate(valid_rate):
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    parsed["providers"]["customer_google"] = {
        "type": "google_live",
        "capabilities": ["stt", "llm", "tts"],
        "input_encoding": "ulaw",
        "input_sample_rate_hz": valid_rate,
    }

    result = config._validate_ai_agent_config(
        yaml.safe_dump(parsed, sort_keys=False)
    )

    assert isinstance(result.get("warnings"), list)


@pytest.mark.parametrize("legacy_encoding", ["mulaw", "ulaw", "mu-law"])
def test_legacy_openai_audio_migration_covers_named_instances_only_for_exact_pair(
    legacy_encoding,
):
    config_data = {
        "providers": {
            "customer_openai": {
                "type": "openai_realtime",
                "output_encoding": legacy_encoding,
                "output_sample_rate_hz": 24000,
            },
            "valid_openai": {
                "type": "openai_realtime",
                "output_encoding": "mulaw",
                "output_sample_rate_hz": 8000,
            },
            "customer_google": {
                "type": "google_live",
                "output_encoding": "mulaw",
                "output_sample_rate_hz": 24000,
            },
        }
    }

    assert normalize_legacy_openai_audio(config_data) is True
    assert config_data["providers"]["customer_openai"]["output_encoding"] == "linear16"
    assert config_data["providers"]["valid_openai"]["output_encoding"] == "mulaw"
    assert config_data["providers"]["customer_google"]["output_encoding"] == "mulaw"


def test_builtin_profile_registry_matches_shipped_config():
    shipped = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    for profile_name, baseline in BUILTIN_PROFILE_BASELINES.items():
        assert shipped["profiles"][profile_name] == baseline
