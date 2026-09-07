import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api import system


def configure(monkeypatch, transport="websocket"):
    monkeypatch.setattr(system, "_read_merged_config_dict_for_system", lambda: {
        "audio_transport": transport,
        "websocket_media": {"auth": {"password_env": "CUSTOM_MEDIA_SECRET"}},
    })


@pytest.mark.asyncio
async def test_missing_recreate_secret_does_not_stop_engine_even_when_forced(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("CUSTOM_MEDIA_SECRET", "admin-process-is-not-proof")
    monkeypatch.setattr(system, "_dotenv_value", lambda key: None)
    recreate = AsyncMock()
    monkeypatch.setattr(system, "_recreate_via_compose", recreate)
    with pytest.raises(HTTPException) as exc:
        await system.restart_container("ai_engine", force=True, recreate=True)
    assert exc.value.status_code == 409
    assert "CUSTOM_MEDIA_SECRET" in exc.value.detail
    assert "admin-process-is-not-proof" not in exc.value.detail
    recreate.assert_not_awaited()


def test_recreate_uses_saved_secret_reference(monkeypatch):
    configure(monkeypatch)
    source = Mock(return_value="test-only-secret")
    monkeypatch.setattr(system, "_dotenv_value", source)
    system._validate_websocket_restart_environment(recreate=True)
    source.assert_called_once_with("CUSTOM_MEDIA_SECRET")


@pytest.mark.parametrize("present", [True, False])
def test_plain_restart_checks_container_not_saved_file(monkeypatch, present):
    configure(monkeypatch)
    monkeypatch.setattr(system, "_dotenv_value", Mock(return_value="saved-only"))
    client = Mock()
    client.containers.get.return_value.attrs = {
        "Config": {"Env": ["CUSTOM_MEDIA_SECRET=test-only"] if present else []},
    }
    monkeypatch.setattr(system.docker, "from_env", lambda: client)
    if present:
        system._validate_websocket_restart_environment(recreate=False)
    else:
        with pytest.raises(HTTPException) as exc:
            system._validate_websocket_restart_environment(recreate=False)
        assert exc.value.status_code == 409
    client.close.assert_called_once_with()


def test_legacy_transport_does_not_require_websocket_secret(monkeypatch):
    configure(monkeypatch, "audiosocket")
    source = Mock(side_effect=AssertionError("must not read credential"))
    monkeypatch.setattr(system, "_dotenv_value", source)
    system._validate_websocket_restart_environment(recreate=True)
    source.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", ["test-only-secret", None])
async def test_missing_container_validates_compose_env_before_recovery(monkeypatch, secret):
    configure(monkeypatch)
    monkeypatch.setattr(system, "_dotenv_value", lambda key: secret)
    client = Mock()
    client.containers.get.side_effect = system.docker.errors.NotFound("missing")
    monkeypatch.setattr(system.docker, "from_env", lambda: client)
    monkeypatch.setattr(system, "_check_active_calls", AsyncMock(return_value={"active_calls": 0}))
    start = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(system, "_start_via_compose", start)
    if secret:
        assert (await system.restart_container("ai_engine"))["status"] == "success"
        start.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as exc:
            await system.restart_container("ai_engine")
        assert exc.value.status_code == 409
        assert "project .env file" in exc.value.detail
        start.assert_not_awaited()
    client.close.assert_called_once_with()


def test_docker_access_failure_does_not_use_compose_secret(monkeypatch):
    configure(monkeypatch)
    source = Mock(return_value="saved-only-secret")
    monkeypatch.setattr(system, "_dotenv_value", source)
    client = Mock()
    client.containers.get.side_effect = system.docker.errors.APIError("unavailable")
    monkeypatch.setattr(system.docker, "from_env", lambda: client)
    with pytest.raises(HTTPException) as exc:
        system._validate_websocket_restart_environment(recreate=False)
    assert exc.value.status_code == 409
    source.assert_not_called()
    client.close.assert_called_once_with()


def test_invalid_config_error_does_not_echo_raw_password(monkeypatch):
    monkeypatch.setattr(system, "_read_merged_config_dict_for_system", lambda: {
        "audio_transport": "websocket", "websocket_media": {"auth": {"password": "never-echo"}},
    })
    with pytest.raises(HTTPException) as exc:
        system._validate_websocket_restart_environment(recreate=True)
    assert exc.value.status_code == 400
    assert "never-echo" not in exc.value.detail


@pytest.mark.parametrize("requested,version,effective", [
    ("auto", "20.17.0", "plain"), ("json", "20.17.0", None),
    ("auto", "22.10.1", "json"), ("plain", "22.10.1", None),
    ("auto", None, None), ("auto", "24.0.0", None),
])
def test_saved_control_capability_matches_engine(requested, version, effective):
    live = {"audio_transport": "websocket", "asterisk_version": version, "websocket_control_format": requested}
    system._set_websocket_capability(live)
    assert live["websocket_effective_control_format"] == effective
    assert live["websocket_media_supported"] is (effective is not None)


@pytest.mark.asyncio
async def test_restart_all_cannot_bypass_missing_media_secret(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(system, "_dotenv_value", lambda key: None)
    run = Mock()
    monkeypatch.setattr(system.subprocess, "run", run)
    with pytest.raises(HTTPException) as exc:
        await system.restart_all_containers()
    assert exc.value.status_code == 409
    run.assert_not_called()
