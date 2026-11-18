"""SenseVoice environment edits must remain pending until Local AI is recreated."""
import asyncio
import sys
from types import SimpleNamespace

import pytest

from api import config


@pytest.mark.parametrize(
    "key,old_value,new_value",
    [
        ("SHERPA_SENSE_VOICE_LANGUAGE", "auto", "zh"),
        ("SHERPA_SENSE_VOICE_USE_ITN", "true", "false"),
    ],
)
@pytest.mark.parametrize("delete", [False, True])
def test_sense_voice_env_save_and_refresh_require_local_ai_recreate(
    tmp_path, monkeypatch, key, old_value, new_value, delete
):
    env_path = tmp_path / ".env"
    env_path.write_text(f"{key}={old_value}\n")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))
    monkeypatch.setattr(config, "_running_container_names", lambda: {"local_ai_server"})
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: {})

    # Docker reports the old environment until the requested recreate completes.
    container = SimpleNamespace(attrs={"Config": {"Env": [f"{key}={old_value}"]}})
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda name: container))
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: client))
    plan = [{
        "service": "local_ai_server",
        "method": "recreate",
        "endpoint": "/api/system/containers/local_ai_server/restart",
    }]

    result = asyncio.run(config.update_env({key: None if delete else new_value}))
    assert result["restart_required"] is True
    assert result["apply_plan"] == plan
    persisted = env_path.read_text()
    if delete:
        assert key not in persisted
    else:
        assert f"{key}={new_value}" in persisted

    # Refresh must not hide the saved change while the container still uses old values.
    status = asyncio.run(config.get_env_status())
    assert status["pending_restart"] is True
    assert status["apply_plan"] == plan
    assert status["drift"]["local_ai_server"] == [key]

    container.attrs["Config"]["Env"] = [] if delete else [f"{key}={new_value}"]
    status = asyncio.run(config.get_env_status())
    assert status["pending_restart"] is False
    assert status["apply_plan"] == []


def test_sense_voice_env_save_does_not_request_starting_an_absent_service(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(config, "_running_container_names", lambda: set())
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: {})

    result = asyncio.run(config.update_env({"SHERPA_SENSE_VOICE_LANGUAGE": "en"}))
    assert result["restart_required"] is False
    assert result["apply_plan"] == []
