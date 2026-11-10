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
from api import support  # noqa: E402


@pytest.mark.asyncio
async def test_legacy_log_export_uses_safe_bounded_system_bundle(monkeypatch):
    captured = {}

    def fake_system_bundle(request, *, deprecated=False):
        captured["request"] = request
        captured["deprecated"] = deprecated
        return "safe-system-bundle"

    monkeypatch.setattr(support, "_system_bundle_sync", fake_system_bundle)

    result = await config.export_logs()

    assert result == "safe-system-bundle"
    assert captured["request"].hours == 1
    assert captured["deprecated"] is True


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


@pytest.mark.parametrize("pacing", ["legacy", "asterisk"])
def test_yaml_validation_allows_websocket_selection_without_secret_at_save_time(tmp_path, monkeypatch, pacing):
    """The Admin API validates structure, not ai_engine's future env namespace."""
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    parsed["audio_transport"] = "websocket"
    parsed["websocket_media"] = {
        "auth": {"password_env": "ASTERISK_MEDIA_WS_PASSWORD"}, "output_pacing": pacing,
    }
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
            lambda parsed: parsed.setdefault("websocket_media", {}).update(output_pacing="not-a-mode"),
            "websocket_media.output_pacing",
        ),
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


@pytest.mark.parametrize("kind", ["google_live", "openai_realtime", "grok", "deepgram", "elevenlabs_agent"])
@pytest.mark.parametrize("values", [
    {"connect_timeout_sec": 0}, {"connect_max_retries": 4},
    {"connect_max_retries": 1.5}, {"connect_total_timeout_sec": -1},
])
def test_config_validation_rejects_invalid_named_cloud_recovery(kind, values):
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    parsed["providers"]["customer_recovery"] = {"type": kind, **values}
    with pytest.raises(HTTPException) as exc_info:
        config._validate_ai_agent_config(yaml.safe_dump(parsed, sort_keys=False))
    assert exc_info.value.status_code == 400
    assert "customer_recovery" in str(exc_info.value.detail)


def test_unrelated_config_save_preserves_absent_and_zero_recovery_fields(tmp_path, monkeypatch):
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    parsed["providers"]["google_live"].pop("connect_max_retries", None)
    parsed["providers"]["customer_recovery"] = {
        "type": "google_live", "connect_max_retries": 0,
        "connect_timeout_sec": 10, "connect_total_timeout_sec": None,
    }
    original = deepcopy(parsed["providers"])
    monkeypatch.setattr(config, "_migrate_inline_provider_secrets", lambda _cfg: False)
    monkeypatch.setattr(config, "_assert_in_use_audio_profiles_unchanged", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: deepcopy(parsed))
    monkeypatch.setattr(config, "_read_base_config_dict", lambda: {})
    written = []
    monkeypatch.setattr(config, "_write_local_config", written.append)
    config.persist_config_content(yaml.safe_dump(parsed, sort_keys=False))
    saved = yaml.safe_load(written[0])
    assert saved["providers"] == original
    assert "connect_max_retries" not in saved["providers"]["google_live"]
    assert saved["providers"]["customer_recovery"]["connect_max_retries"] == 0
    assert parsed["providers"] == original
    assert "connect_max_retries" not in parsed["providers"]["google_live"]
    assert parsed["providers"]["customer_recovery"]["connect_max_retries"] == 0


@pytest.mark.parametrize("key,maximum", [
    ("greeting_media_ready_delay_ms", 2000), ("greeting_media_ready_timeout_ms", 5000),
])
@pytest.mark.parametrize("value", [-1, 5001, 1.5, 1.0, True, None, "100", "bad"])
def test_greeting_media_validation_rejects_bad_values(key, maximum, value):
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    parsed.setdefault("streaming", {})[key] = value
    with pytest.raises(HTTPException) as exc_info:
        config._validate_ai_agent_config(yaml.safe_dump(parsed, sort_keys=False))
    assert exc_info.value.status_code == 400
    assert key in str(exc_info.value.detail)


@pytest.mark.parametrize("delay,timeout", [(0, 0), (0, 1000), (2000, 5000)])
def test_greeting_media_save_reopen_and_unrelated_edit_preserve_yaml(monkeypatch, delay, timeout):
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    parsed["streaming"].update(greeting_media_ready_delay_ms=delay, greeting_media_ready_timeout_ms=timeout)
    original = deepcopy(parsed)
    monkeypatch.setattr(config, "_migrate_inline_provider_secrets", lambda _cfg: False)
    monkeypatch.setattr(config, "_assert_in_use_audio_profiles_unchanged", lambda *_a, **_kw: None)
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: deepcopy(original))
    monkeypatch.setattr(config, "_read_base_config_dict", lambda: {})
    writes = []
    monkeypatch.setattr(config, "_write_local_config", writes.append)
    config.persist_config_content(yaml.safe_dump(parsed, sort_keys=False))
    reopened = yaml.safe_load(writes[-1])
    reopened["streaming"]["jitter_buffer_ms"] = 111
    result = config.persist_config_content(yaml.safe_dump(reopened, sort_keys=False))
    saved = yaml.safe_load(writes[-1])
    assert saved["streaming"]["greeting_media_ready_delay_ms"] == delay
    assert saved["streaming"]["greeting_media_ready_timeout_ms"] == timeout
    assert saved["streaming"]["greeting_min_start_ms"] == original["streaming"]["greeting_min_start_ms"]
    assert saved["streaming"]["greeting_rtp_wait_ms"] == original["streaming"]["greeting_rtp_wait_ms"]
    assert saved["providers"] == original["providers"]
    assert result["recommended_apply_method"] == "restart"


_COMMENTED_LOCAL = """# Why these values are pinned
llm:
  # keep low, see ticket 12
  temperature: 0.3  # inline note
old_key: 1
"""


def _use_local_file(monkeypatch, tmp_path, text):
    local_path = tmp_path / "ai-agent.local.yaml"
    local_path.write_text(text)
    monkeypatch.setattr(config.settings, "LOCAL_CONFIG_PATH", str(local_path))
    return local_path


def test_local_write_keeps_operator_comments(monkeypatch, tmp_path):
    local_path = _use_local_file(monkeypatch, tmp_path, _COMMENTED_LOCAL)
    desired = {"llm": {"temperature": 0.3, "max_tokens": 512}, "new": {"mode": "on"}}

    config._write_local_config(yaml.dump(desired, sort_keys=False))

    saved = local_path.read_text()
    assert "# Why these values are pinned" in saved
    assert "# keep low, see ticket 12" in saved
    assert "# inline note" in saved
    assert "old_key" not in saved
    assert yaml.safe_load(saved) == desired


def test_raw_yaml_save_keeps_operator_comments(monkeypatch, tmp_path):
    local_path = _use_local_file(monkeypatch, tmp_path, _COMMENTED_LOCAL)
    merged = yaml.safe_load(_COMMENTED_LOCAL)
    merged["llm"]["max_tokens"] = 512
    monkeypatch.setattr(config, "_migrate_inline_provider_secrets", lambda _cfg: False)
    monkeypatch.setattr(config, "_assert_in_use_audio_profiles_unchanged", lambda *_a, **_kw: None)
    monkeypatch.setattr(config, "_validate_ai_agent_config", lambda _content: {})
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: yaml.safe_load(_COMMENTED_LOCAL))
    monkeypatch.setattr(config, "_read_base_config_dict", lambda: {})

    config.persist_config_content(yaml.safe_dump(merged, sort_keys=False))

    saved = local_path.read_text()
    assert "# keep low, see ticket 12" in saved
    assert yaml.safe_load(saved) == merged


@pytest.mark.parametrize("previous", [
    "llm:\n  temperature: 0.3\n",  # no comments: nothing to keep
    "# note\nllm: [unclosed\n",  # unreadable: fall back
    "# note\n- a list\n",  # not a mapping: fall back
    "# note\r\nllm:\r\n  temperature: 0.3\r\n",  # CRLF: would come out mixed
])
def test_local_write_falls_back_to_plain_dump(monkeypatch, tmp_path, previous):
    content = yaml.dump({"llm": {"temperature": 0.5}}, sort_keys=False)

    assert config._keep_local_comments(previous.encode(), content) == content


def test_kept_comments_never_change_the_saved_data(monkeypatch, tmp_path):
    local_path = _use_local_file(
        monkeypatch, tmp_path, "# note\nflags:\n  a: yes\n  n: 0\n  f: 1.0\n  t: true\n"
    )
    desired = {"flags": {"a": True, "n": False, "f": 1, "t": 1, "b": "off", "c": "12:30", "d": "~"}}

    config._write_local_config(yaml.dump(desired, sort_keys=False))

    saved = local_path.read_text()
    assert "# note" in saved
    loaded = yaml.safe_load(saved)["flags"]
    assert loaded == desired["flags"]
    assert [type(loaded[k]) for k in desired["flags"]] == [type(v) for v in desired["flags"].values()]


@pytest.mark.parametrize("previous,desired,expected", [
    (  # a deleted key takes its own comment with it
        "a: 1\n# B is pinned, ticket 9\nb: 2\n# C: do not touch\nc: 3\n",
        {"a": 1, "c": 3},
        "a: 1\n# C: do not touch\nc: 3\n",
    ),
    (  # first key; a header above a blank line stays
        "# header\n\n# A explains a\na: 1\n# B explains b\nb: 2\n",
        {"b": 2},
        "# header\n\n# B explains b\nb: 2\n",
    ),
    (  # last nested key; the comment above the next top-level key stays there
        "p:\n  x: 1\n  # y note\n  y: 2\n# Q note\nq: 1\n",
        {"p": {"x": 1}, "q": 1},
        "p:\n  x: 1\n# Q note\nq: 1\n",
    ),
    (  # a key added to a nested map goes above the next key's comment
        "p:\n  x: 1\n# Q note\nq: 1\n",
        {"p": {"x": 1, "z": 2}, "q": 1},
        "p:\n  x: 1\n  z: 2\n# Q note\nq: 1\n",
    ),
    (  # a shortened list keeps the comment above the next key
        "s:\n- a\n- b\n# T note\nt: 1\n",
        {"s": ["a"], "t": 1},
        "s:\n- a\n# T note\nt: 1\n",
    ),
    (  # a flow value keeps its trailing comments on its own key
        "a: [1, 2]  # a eol\n# B why\nb: 2\n# C why\nc: 3\n",
        {"a": [1, 2], "c": 3},
        "a: [1, 2]  # a eol\n# C why\nc: 3\n",
    ),
    (  # after a block scalar there is no end-of-line comment
        "a: |\n  t\n# B why\nb: 2\n# C why\nc: 3\n",
        {"a": "t\n", "c": 3},
        "a: |\n  t\n# C why\nc: 3\n",
    ),
    (  # deleting a first key with no comment above it keeps the next one's
        "# Local overrides\nproviders:\n  openai: 1\n  # Deepgram for STT\n  deepgram: 2\n",
        {"providers": {"deepgram": 2}},
        "# Local overrides\nproviders:\n  # Deepgram for STT\n  deepgram: 2\n",
    ),
    (  # first nested key with a comment above it
        "p:\n  # X why\n  x: 1\n  # Y why\n  y: 2\n",
        {"p": {"y": 2}},
        "p:\n  # Y why\n  y: 2\n",
    ),
    (  # a comment indented inside the map above is not the deleted key's
        "p:\n  x: 1\n  # inside p\n# Q why\nq: 2\nr: 1\n",
        {"p": {"x": 1}, "r": 1},
        "p:\n  x: 1\n  # inside p\nr: 1\n",
    ),
    (  # deleting the key after a block scalar leaves no empty comment behind
        "# hdr\na: |\n  t\n# B\nb: 2\n",
        {"a": "t\n"},
        "# hdr\na: |\n  t\n",
    ),
    (  # adding a key after a block scalar keeps the footer last
        "# hdr\na: |\n  t\n# footer\n",
        {"a": "t\n", "z": 1},
        "# hdr\na: |\n  t\nz: 1\n# footer\n",
    ),
    (  # first nested key under a parent with an end-of-line comment
        "# hdr\np:  # P eol\n  # X why\n  x: 1\n  # Y why\n  y: 2\n",
        {"p": {"y": 2}},
        "# hdr\np:  # P eol\n  # Y why\n  y: 2\n",
    ),
    (  # a document start marker stays
        "---\n# n\na: 1\n",
        {"a": 2},
        "---\n# n\na: 2\n",
    ),
])
def test_kept_comments_stay_with_their_keys(previous, desired, expected):
    assert config._keep_local_comments(previous.encode(), yaml.dump(desired, sort_keys=False)) == expected


def test_kept_comments_keep_the_file_layout():
    previous = "a:\n    b:\n        - x\n        - y  # y\n    t: null\n    q: '18080:18099'\n"
    desired = {"a": {"b": ["x", "y"], "t": None, "q": "18080:18099", "n": 2}}

    kept = config._keep_local_comments(previous.encode(), yaml.dump(desired, sort_keys=False))

    assert kept == previous + "    n: 2\n"


def test_comment_only_local_file_keeps_its_comments():
    previous = "# Operator overrides go here\n# llm:\n#   temperature: 0.2\n"
    content = yaml.dump({"llm": {"temperature": 0.3}}, sort_keys=False)

    assert config._keep_local_comments(previous.encode(), content) == previous + content


def test_secret_migration_drops_local_comments(monkeypatch):
    writes = []
    monkeypatch.setattr(config, "_migrate_inline_provider_secrets", lambda _cfg: True)
    monkeypatch.setattr(config, "_assert_in_use_audio_profiles_unchanged", lambda *_a, **_kw: None)
    monkeypatch.setattr(config, "_validate_ai_agent_config", lambda _content: {})
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: {})
    monkeypatch.setattr(config, "_read_base_config_dict", lambda: {})
    monkeypatch.setattr(config, "_write_local_config", lambda content, **kw: writes.append(kw))

    config.persist_config_content("llm:\n  temperature: 0.3\n")

    assert writes == [{"keep_comments": False}]


def test_local_write_without_keep_comments_writes_plain_dump(monkeypatch, tmp_path):
    local_path = _use_local_file(monkeypatch, tmp_path, "# old: sk-old\nllm:\n  temperature: 0.3\n")
    content = yaml.dump({"llm": {"temperature": 0.5}}, sort_keys=False)

    config._write_local_config(content, keep_comments=False)

    assert local_path.read_text() == content


@pytest.mark.parametrize("reference", ["${AAVA_TEST_GREETING}", "$AAVA_TEST_GREETING"])
def test_yaml_save_preserves_engine_config_with_environment_reference(monkeypatch, tmp_path, reference):
    from src.config.loaders import load_yaml_with_local_override

    base = tmp_path / "ai-agent.yaml"
    base.write_text(
        "default_provider: openai_realtime\n"
        "providers:\n  openai_realtime:\n    enabled: true\n    greeting: Base greeting\n"
        "asterisk:\n  app_name: base-route\n"
    )
    local = _use_local_file(
        monkeypatch,
        tmp_path,
        "# Keep the greeting short\n"
        "providers:\n  openai_realtime:\n    greeting: 'Hello'\n"
        "asterisk:\n  app_name: local-route\n",
    )
    monkeypatch.setattr(config.settings, "CONFIG_PATH", str(base))
    monkeypatch.setattr(config.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("AAVA_TEST_GREETING", "Welcome to O'Brien's office")
    monkeypatch.setenv("ASTERISK_ARI_USERNAME", "test")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "test")

    desired = config._read_merged_config_dict()
    desired["providers"]["openai_realtime"]["greeting"] = reference
    config.persist_config_content(yaml.safe_dump(desired, sort_keys=False))

    assert f"greeting: {reference}\n" in local.read_text()
    effective = load_yaml_with_local_override(str(base))
    assert effective["providers"]["openai_realtime"]["greeting"] == "Welcome to O'Brien's office"
    assert effective["asterisk"]["app_name"] == "local-route"


def test_comment_only_file_with_environment_reference_falls_back():
    content = yaml.safe_dump({"asterisk": {"app_name": "local-route"}})

    assert config._keep_local_comments(b"# Example: ${AAVA_TEST_GREETING}\n", content) == content


@pytest.mark.asyncio
async def test_wizard_save_keeps_comments_in_worker_thread(monkeypatch, tmp_path):
    import threading

    from api import wizard

    base = tmp_path / "ai-agent.yaml"
    base.write_text("asterisk:\n  app_name: base-route\n")
    local = _use_local_file(monkeypatch, tmp_path, "# Keep this note\nasterisk:\n  app_name: local-route\n")
    monkeypatch.setattr(config.settings, "CONFIG_PATH", str(base))
    monkeypatch.setattr(wizard, "CONFIG_PATH", str(base))
    monkeypatch.setattr(wizard, "LOCAL_CONFIG_PATH", str(local))
    monkeypatch.setattr(wizard, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "agents.db"))
    monkeypatch.setattr(config.settings, "PROJECT_ROOT", str(tmp_path))
    loop_thread = threading.get_ident()
    rewrite_threads = []
    keep_comments = config._keep_local_comments

    def track_rewrite(previous, content):
        rewrite_threads.append(threading.get_ident())
        return keep_comments(previous, content)

    monkeypatch.setattr(config, "_keep_local_comments", track_rewrite)
    setup = wizard.SetupConfig(
        asterisk_host="127.0.0.1",
        asterisk_username="test",
        asterisk_password="test",
        asterisk_app="local-route",
        openai_key="test",
        greeting="Hello",
        ai_name="Test",
        ai_role="Test assistant",
    )

    result = await wizard.save_setup_config(setup)

    assert result["status"] == "success"
    assert len(rewrite_threads) == 1
    assert rewrite_threads[0] != loop_thread
    assert "# Keep this note\n" in local.read_text()
    assert yaml.safe_load(local.read_text())["asterisk"]["app_name"] == "local-route"


@pytest.mark.parametrize("settings", [
    {},
    {"agent_url": "file:///tmp/agent"},
    {"agent_url": "http://agent.example", "bearer_token_env": "A2A_TOKEN"},
    {"agent_url": "https://agent.example", "timeout_ms": "1500"},
])
def test_config_validation_rejects_invalid_a2a_settings(settings):
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    parsed.setdefault("in_call_tools", {})["delegate"] = {"kind": "a2a_agent", **settings}

    with pytest.raises(HTTPException) as exc:
        config._validate_ai_agent_config(yaml.safe_dump(parsed))

    assert exc.value.status_code == 400
    assert exc.value.detail.startswith("A2A ")
