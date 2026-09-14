import asyncio
import sys
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import config  # noqa: E402
from api import system  # noqa: E402


def test_get_config_returns_merged_structured_config(monkeypatch):
    monkeypatch.setattr(
        config,
        "_read_merged_config_dict",
        lambda: {"providers": {"local": {"type": "local"}}},
    )

    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")

    response = TestClient(app).get("/api/config")

    assert response.status_code == 200
    assert response.json() == {"providers": {"local": {"type": "local"}}}


def test_get_config_redacts_hand_written_websocket_secret(monkeypatch):
    monkeypatch.setattr(
        config,
        "_read_merged_config_dict",
        lambda: {
            "websocket_media": {
                "auth": {
                    "username": "aava_media",
                    "password": "must-not-leak",
                    "password_env": "ASTERISK_MEDIA_WS_PASSWORD",
                }
            }
        },
    )

    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")
    response = TestClient(app).get("/api/config")

    assert response.status_code == 200
    assert response.json()["websocket_media"]["auth"] == {
        "username": "aava_media",
        "password_env": "ASTERISK_MEDIA_WS_PASSWORD",
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_config_validation_rejects_non_finite_provider_numbers(value):
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    parsed["providers"]["google_live"]["input_gain_max_db"] = value

    with pytest.raises(HTTPException) as exc_info:
        config._validate_ai_agent_config(yaml.safe_dump(parsed, sort_keys=False))

    assert exc_info.value.status_code == 400
    assert "providers.google_live.input_gain_max_db" in str(exc_info.value.detail)


def test_get_config_reports_existing_non_finite_value_with_recovery_path(monkeypatch):
    monkeypatch.setattr(
        config,
        "_read_merged_config_dict",
        lambda: {"providers": {"google_live": {"input_gain_max_db": float("nan")}}},
    )

    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")
    response = TestClient(app, raise_server_exceptions=False).get("/api/config")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "providers.google_live.input_gain_max_db" in detail
    assert "Advanced > Raw YAML" in detail


def test_non_finite_config_path_quotes_dotted_provider_names():
    with pytest.raises(HTTPException) as exc_info:
        config._assert_finite_config_numbers(
            {"providers": {"acme.google": {"input_gain_max_db": float("nan")}}}
        )

    assert 'providers["acme.google"].input_gain_max_db' in str(exc_info.value.detail)


def test_config_update_rejects_recursive_yaml_alias_before_write(monkeypatch):
    monkeypatch.setattr(
        config,
        "_write_local_config",
        lambda _content: pytest.fail("recursive config must not be written"),
    )
    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")

    response = TestClient(app).post(
        "/api/config/yaml",
        json={"content": "loop: &loop [*loop]\n"},
    )

    assert response.status_code == 400
    assert "recursive YAML alias" in response.json()["detail"]


def test_finite_number_validation_rejects_recursive_loaded_config():
    loop = []
    loop.append(loop)

    with pytest.raises(HTTPException) as exc_info:
        config._assert_finite_config_numbers({"loop": loop}, status_code=422)

    assert exc_info.value.status_code == 422
    assert "recursive YAML alias at loop[0]" in str(exc_info.value.detail)


def test_config_validation_allows_non_recursive_yaml_aliases():
    parsed = config._safe_load_no_duplicates(
        "first: &shared [1, 2]\nsecond: *shared\n"
    )

    config._assert_finite_config_numbers(parsed)
    assert parsed == {"first": [1, 2], "second": [1, 2]}


def test_get_config_reports_recursive_local_override(monkeypatch, tmp_path):
    base_path = tmp_path / "ai-agent.yaml"
    local_path = tmp_path / "ai-agent.local.yaml"
    base_path.write_text("providers:\n  local:\n    type: local\n")
    local_path.write_text("loop: &loop [*loop]\n")
    monkeypatch.setattr(config.settings, "CONFIG_PATH", str(base_path))
    monkeypatch.setattr(config.settings, "LOCAL_CONFIG_PATH", str(local_path))

    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")
    response = TestClient(app, raise_server_exceptions=False).get("/api/config")

    assert response.status_code == 422
    assert "recursive YAML alias" in response.json()["detail"]
    assert "local configuration file" in response.json()["detail"]


@pytest.mark.parametrize("key", [".nan", ".inf", "-.inf"])
def test_get_config_reports_non_finite_local_override_key(monkeypatch, tmp_path, key):
    base_path = tmp_path / "ai-agent.yaml"
    local_path = tmp_path / "ai-agent.local.yaml"
    base_path.write_text("providers:\n  local:\n    type: local\n")
    local_path.write_text(f"{key}: malformed\n")
    monkeypatch.setattr(config.settings, "CONFIG_PATH", str(base_path))
    monkeypatch.setattr(config.settings, "LOCAL_CONFIG_PATH", str(local_path))

    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")
    response = TestClient(app, raise_server_exceptions=False).get("/api/config")

    assert response.status_code == 422
    assert "non-finite numeric mapping key" in response.json()["detail"]
    assert "local configuration file" in response.json()["detail"]


def test_config_update_rejects_non_finite_value_before_write(monkeypatch):
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    parsed["providers"]["google_live"]["input_gain_max_db"] = float("nan")
    monkeypatch.setattr(
        config,
        "_write_local_config",
        lambda _content: pytest.fail("invalid config must not be written"),
    )

    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")
    response = TestClient(app).post(
        "/api/config/yaml",
        json={"content": yaml.safe_dump(parsed, sort_keys=False)},
    )

    assert response.status_code == 400
    assert "providers.google_live.input_gain_max_db" in response.json()["detail"]


def test_health_api_token_impacts_local_ai_when_used_as_live_status_fallback():
    assert config._local_ai_env_key("HEALTH_API_TOKEN") is True


def test_websocket_secret_env_change_requires_ai_engine_recreate():
    assert config._ai_engine_env_key("ASTERISK_MEDIA_WS_PASSWORD") is True


def test_websocket_secret_env_update_returns_ai_engine_recreate_plan(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING=value\n")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))
    monkeypatch.setattr(config, "_running_container_names", lambda: {"ai_engine"})

    result = asyncio.run(
        config.update_env({"ASTERISK_MEDIA_WS_PASSWORD": "new-test-secret"})
    )

    assert result["restart_required"] is True
    assert result["recommended_apply_method"] == "recreate"
    assert result["apply_plan"] == [
        {
            "service": "ai_engine",
            "method": "recreate",
            "endpoint": "/api/system/containers/ai_engine/restart",
        }
    ]


@pytest.mark.parametrize("replacement", ["changed-test-value", ""])
def test_custom_websocket_secret_update_or_delete_requires_recreate(tmp_path, monkeypatch, replacement):
    env_path = tmp_path / ".env"
    env_path.write_text("CUSTOM_MEDIA_PASSWORD=old-test-value\n")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))
    monkeypatch.setattr(config, "_running_container_names", lambda: {"ai_engine"})
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: {
        "audio_transport": "websocket",
        "websocket_media": {"auth": {"password_env": "CUSTOM_MEDIA_PASSWORD"}},
    })
    result = asyncio.run(config.update_env({"CUSTOM_MEDIA_PASSWORD": replacement}))
    assert result["restart_required"] is True
    assert result["apply_plan"] == [{
        "service": "ai_engine", "method": "recreate",
        "endpoint": "/api/system/containers/ai_engine/restart",
    }]


def test_asterisk_module_prerequisites_follow_selected_transport():
    externalmedia = system._required_modules_for_transport("externalmedia")
    audiosocket = system._required_modules_for_transport("audiosocket")
    websocket = system._required_modules_for_transport("websocket")

    assert "app_audiosocket" not in externalmedia
    assert "app_audiosocket" in audiosocket
    assert {"chan_websocket", "res_websocket_client", "res_http_websocket", "res_ari_channels"}.issubset(websocket)


def test_transport_switch_override_preserves_provider_prompt_pipeline_and_profiles():
    base = {
        "audio_transport": "externalmedia",
        "llm": {"prompt": "Keep this exact operator prompt."},
        "providers": {"local": {"enabled": True, "base_url": "ws://provider"}},
        "pipelines": {"voice": {"stt": "local_stt", "llm": "native_llm", "tts": "local_tts"}},
        "profiles": {"default": "telephony_ulaw_8k"},
        "contexts": {"sales": {"prompt": "Do not lose this context."}},
        "websocket_media": {"auth": {"password_env": "ASTERISK_MEDIA_WS_PASSWORD"}},
    }

    for target in ("externalmedia", "audiosocket", "websocket"):
        desired = deepcopy(base)
        desired["audio_transport"] = target
        override = config._compute_local_override(base, desired)
        assert config._deep_merge_dicts(base, override) == desired
        assert desired["llm"] == base["llm"]
        assert desired["providers"] == base["providers"]
        assert desired["pipelines"] == base["pipelines"]
        assert desired["profiles"] == base["profiles"]
        assert desired["contexts"] == base["contexts"]
        assert desired["websocket_media"] == base["websocket_media"]


def test_websocket_config_api_status_allows_inactive_missing_secret(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("ASTERISK_ARI_USERNAME=test\nASTERISK_ARI_PASSWORD=test\n")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))
    monkeypatch.setattr(
        config,
        "_read_merged_config_dict",
        lambda: {"audio_transport": "externalmedia", "websocket_media": {"auth": {"password_env": "ASTERISK_MEDIA_WS_PASSWORD"}}},
    )

    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")
    response = TestClient(app).get("/api/config/websocket-media-status")

    assert response.status_code == 200
    assert response.json()["secret_present"] is False
    assert response.json()["secret_reference"] == "ASTERISK_MEDIA_WS_PASSWORD"


def test_yaml_validation_allows_websocket_selection_without_secret_at_save_time(tmp_path, monkeypatch):
    """The Admin API validates structure, not ai_engine's future env namespace."""
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    parsed["audio_transport"] = "websocket"
    parsed["websocket_media"] = {"auth": {"password_env": "ASTERISK_MEDIA_WS_PASSWORD"}}
    env_path = tmp_path / ".env"
    env_path.write_text("ASTERISK_ARI_USERNAME=test\nASTERISK_ARI_PASSWORD=test\n")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))
    monkeypatch.delenv("ASTERISK_MEDIA_WS_PASSWORD", raising=False)

    result = config._validate_ai_agent_config(yaml.safe_dump(parsed, sort_keys=False))

    assert result["warnings"] is not None


@pytest.mark.parametrize(
    ("mutate", "location"),
    [
        (
            lambda parsed: parsed["providers"]["openai_realtime"].update(
                output_resampler="not-a-mode"
            ),
            "providers.openai_realtime.output_resampler",
        ),
        (
            lambda parsed: parsed["pipelines"]["local_hybrid"]
            .setdefault("options", {})
            .setdefault("tts", {})
            .update(output_resampler="not-a-mode"),
            "pipelines.local_hybrid.options.tts.output_resampler",
        ),
        (
            lambda parsed: parsed["pipelines"]["local_hybrid"]
            .setdefault("options", {})
            .setdefault("tts", {})
            .update(streaming_overlap="false"),
            "pipelines.local_hybrid.options.tts.streaming_overlap",
        ),
        (
            lambda parsed: parsed["pipelines"]["local_hybrid"]
            .setdefault("options", {})
            .setdefault("stt", {})
            .update(segment_silence_ms=50),
            "pipelines.local_hybrid.options.stt.segment_silence_ms",
        ),
    ],
)
def test_yaml_validation_rejects_invalid_audio_transport_policy(mutate, location):
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    mutate(parsed)

    with pytest.raises(HTTPException) as exc_info:
        config._validate_ai_agent_config(yaml.safe_dump(parsed, sort_keys=False))

    assert exc_info.value.status_code == 400
    assert location in str(exc_info.value.detail)


def test_profile_usage_guard_blocks_mutating_agent_profile(tmp_path, monkeypatch):
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
    new = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "slin"}},
        }
    }

    with pytest.raises(HTTPException) as exc_info:
        config._assert_in_use_audio_profiles_unchanged(old, new)

    assert exc_info.value.status_code == 409
    assert "Ava Demo" in str(exc_info.value.detail)


def test_profile_usage_guard_allows_new_or_unreferenced_profile(tmp_path, monkeypatch):
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
    old = {"profiles": {"default": "telephony_ulaw_8k"}}
    new = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "experimental": {"transport_out": {"encoding": "slin16"}},
        }
    }

    config._assert_in_use_audio_profiles_unchanged(old, new)


def test_profile_usage_guard_blocks_default_inherited_agent(tmp_path, monkeypatch):
    db_path = tmp_path / "agents.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE agents (slug TEXT, display_name TEXT, audio_profile TEXT)"
        )
        conn.executemany(
            "INSERT INTO agents VALUES (?, ?, ?)",
            [
                ("default-agent", "Default Agent", None),
                ("blank-agent", "Blank Agent", "  "),
            ],
        )
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))
    old = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "ulaw"}},
        }
    }
    new = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "slin"}},
        }
    }

    with pytest.raises(HTTPException) as exc_info:
        config._assert_in_use_audio_profiles_unchanged(old, new)

    assert exc_info.value.status_code == 409
    assert "Default Agent" in str(exc_info.value.detail)
    assert "Blank Agent" in str(exc_info.value.detail)


def test_profile_usage_guard_blocks_default_only_change_for_inherited_agent(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "agents.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE agents (slug TEXT, display_name TEXT, audio_profile TEXT)"
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?)",
            ("default-agent", "Default Agent", None),
        )
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))
    profiles = {
        "telephony_ulaw_8k": {"transport_out": {"encoding": "ulaw"}},
        "telephony_enhanced_8k": {"transport_out": {"encoding": "ulaw"}},
    }
    old = {"profiles": {"default": "telephony_ulaw_8k", **profiles}}
    new = {"profiles": {"default": "telephony_enhanced_8k", **profiles}}

    with pytest.raises(HTTPException) as exc_info:
        config._assert_in_use_audio_profiles_unchanged(old, new)

    assert exc_info.value.status_code == 409
    assert "profiles.default" in str(exc_info.value.detail)
    assert "Default Agent" in str(exc_info.value.detail)


def test_profile_usage_guard_allows_default_change_without_inherited_agents(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "agents.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE agents (slug TEXT, display_name TEXT, audio_profile TEXT)"
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?)",
            ("explicit-agent", "Explicit Agent", "telephony_ulaw_8k"),
        )
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))
    profiles = {
        "telephony_ulaw_8k": {"transport_out": {"encoding": "ulaw"}},
        "telephony_enhanced_8k": {"transport_out": {"encoding": "ulaw"}},
    }
    old = {"profiles": {"default": "telephony_ulaw_8k", **profiles}}
    new = {"profiles": {"default": "telephony_enhanced_8k", **profiles}}

    config._assert_in_use_audio_profiles_unchanged(old, new)


def test_profile_usage_guard_fails_closed_when_agent_store_is_invalid(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "agents.db"
    db_path.write_text("not a sqlite database", encoding="utf-8")
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))
    old = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "ulaw"}},
        }
    }
    new = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "slin"}},
        }
    }

    with pytest.raises(HTTPException) as exc_info:
        config._assert_in_use_audio_profiles_unchanged(old, new)

    assert exc_info.value.status_code == 503
