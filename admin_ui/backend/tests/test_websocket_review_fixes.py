"""Regressions found in review of the WebSocket transport branch."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api import config as config_api  # noqa: E402
from api import system  # noqa: E402


@pytest.mark.asyncio
async def test_reload_maps_engine_409_to_restart_required():
    """Engine refuses hot reload of transport keys with 409; that is not a failure."""
    fake_resp = MagicMock()
    fake_resp.status_code = 409
    fake_resp.json.return_value = {
        "message": "Transport changes require an AI Engine restart and were not applied",
        "changed_keys": ["websocket_media"],
        "restart_required": True,
    }
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(system, "_get_health_api_token", return_value=""), patch(
        "httpx.AsyncClient", return_value=fake_client
    ):
        result = await system.reload_ai_engine()

    assert result["status"] == "partial"
    assert result["restart_required"] is True
    assert result["recommended_apply_method"] == "restart"


def test_restart_guard_ignores_unreadable_yaml_like_main(monkeypatch):
    """Restart never depended on YAML parseability; a read error must not block it."""

    def broken():
        raise ValueError("bad yaml")

    monkeypatch.setattr(system, "_read_merged_config_dict_for_system", broken)
    system._validate_websocket_restart_environment(recreate=True)


@pytest.mark.asyncio
async def test_asterisk_status_prefers_yaml_transport_over_stale_env(monkeypatch):
    """Engine resolves audio_transport YAML-first; the status page must agree."""
    monkeypatch.setattr(
        system,
        "_ari_env_settings",
        lambda: {"host": "127.0.0.1", "scheme": "http", "port": 8088,
                 "username": "", "password": "", "ssl_verify": True},
    )

    async def engine_ari():
        return None

    monkeypatch.setattr(system, "_engine_health_ari_connected", engine_ari)
    monkeypatch.setattr(
        system,
        "_read_merged_config_dict_for_system",
        lambda: {"audio_transport": "websocket", "websocket_media": {}},
    )
    monkeypatch.setattr(
        system, "_dotenv_value",
        lambda key: "audiosocket" if key == "AUDIO_TRANSPORT" else None,
    )
    monkeypatch.setenv("AUDIO_TRANSPORT", "audiosocket")

    result = await system.asterisk_status()

    assert result["live"]["audio_transport"] == "websocket"


def test_system_merged_config_honors_local_null_tombstones(monkeypatch, tmp_path):
    """The apply gate must see the same effective config the engine loads."""
    base = tmp_path / "ai-agent.yaml"
    local = tmp_path / "ai-agent.local.yaml"
    base.write_text(
        "audio_transport: websocket\nwebsocket_media:\n  auth:\n    required: false\n"
    )
    local.write_text("websocket_media:\n  auth:\n    required: null\n")
    monkeypatch.setattr(config_api.settings, "CONFIG_PATH", str(base))
    monkeypatch.setattr(config_api.settings, "LOCAL_CONFIG_PATH", str(local))

    merged = system._read_merged_config_dict_for_system()

    assert "required" not in merged["websocket_media"]["auth"]
