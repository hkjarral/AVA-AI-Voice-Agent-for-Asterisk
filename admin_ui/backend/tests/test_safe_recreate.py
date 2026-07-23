import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND_ROOT))

from api import system  # noqa: E402


@pytest.mark.asyncio
async def test_updater_preparation_failure_does_not_touch_service():
    with patch.object(
        system, "_project_host_root_from_admin_ui_container", return_value="/srv/aava"
    ), patch.object(system, "_current_project_head_sha", return_value="abc123"), patch.object(
        system,
        "_ensure_updater_image_for_ref",
        side_effect=HTTPException(status_code=500, detail="build context unreadable"),
    ), patch.object(system.docker, "from_env") as docker_from_env:
        with pytest.raises(HTTPException) as exc:
            await system._recreate_via_compose("ai_engine", health_check=False)

    assert exc.value.detail == "build context unreadable"
    docker_from_env.assert_not_called()


@pytest.mark.asyncio
async def test_compose_failure_keeps_still_running_service_available():
    previous = MagicMock()
    previous.status = "running"
    previous.attrs = {"Config": {"Image": "asterisk-ai-voice-agent-ai-engine:latest"}}
    previous.image.tag.return_value = True
    client = MagicMock()
    client.images.remove.return_value = None

    with patch.object(
        system, "_project_host_root_from_admin_ui_container", return_value="/srv/aava"
    ), patch.object(system, "_current_project_head_sha", return_value="abc123"), patch.object(
        system, "_ensure_updater_image_for_ref", return_value="updater:test"
    ), patch.object(system.docker, "from_env", return_value=client), patch.object(
        system, "_find_compose_service_container", return_value=previous
    ), patch.object(
        system, "_run_updater_ephemeral", return_value=(1, "compose rejected config")
    ):
        with pytest.raises(HTTPException) as exc:
            await system._recreate_via_compose("ai_engine", health_check=False)

    assert exc.value.detail["service_available"] is True
    assert exc.value.detail["recovery_status"] == "not_needed"
    previous.stop.assert_not_called()
    previous.remove.assert_not_called()


@pytest.mark.asyncio
async def test_compose_failure_restores_previous_image_when_service_disappears(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    previous = MagicMock()
    previous.status = "running"
    previous.attrs = {"Config": {"Image": "asterisk-ai-voice-agent-ai-engine:latest"}}
    previous.attrs["Config"]["Env"] = [
        "OPENAI_API_KEY=old-secret",
        "TOKEN=ab$cd",
        "TZ=UTC",
    ]
    previous.image.tag.return_value = True
    recovered = MagicMock()
    recovered.status = "running"
    rollback_image = MagicMock()
    rollback_image.tag.return_value = True
    client = MagicMock()
    client.images.get.return_value = rollback_image

    captured_recovery_environment = []
    updater_calls = 0

    def run_updater(*args, **kwargs):
        nonlocal updater_calls
        updater_calls += 1
        if updater_calls == 1:
            return 1, "replacement failed after stop"
        override_paths = list(
            (tmp_path / ".agent" / "recreate-recovery").glob("*.yml")
        )
        assert len(override_paths) == 1
        override = system.yaml.safe_load(
            override_paths[0].read_text(encoding="utf-8")
        )
        captured_recovery_environment.extend(
            override["services"]["ai_engine"]["environment"]
        )
        return 0, "previous image restored"

    with patch.object(
        system, "_project_host_root_from_admin_ui_container", return_value="/srv/aava"
    ), patch.object(system, "_current_project_head_sha", return_value="abc123"), patch.object(
        system, "_ensure_updater_image_for_ref", return_value="updater:test"
    ), patch.object(system.docker, "from_env", return_value=client), patch.object(
        system,
        "_find_compose_service_container",
        side_effect=[previous, None, recovered],
    ), patch.object(
        system,
        "_run_updater_ephemeral",
        side_effect=run_updater,
    ) as run_updater:
        with pytest.raises(HTTPException) as exc:
            await system._recreate_via_compose("ai_engine", health_check=False)

    assert exc.value.detail["service_available"] is True
    assert exc.value.detail["recovery_status"] == "recovered"
    assert run_updater.call_count == 2
    rollback_image.tag.assert_called_once_with(
        "asterisk-ai-voice-agent-ai-engine", tag="latest", force=True
    )
    # Compose reduces $$ to a literal $, so the restored container receives the
    # original TOKEN=ab$cd rather than interpolating $cd from the host.
    assert "TOKEN=ab$$cd" in captured_recovery_environment
    assert list((tmp_path / ".agent" / "recreate-recovery").glob("*.yml")) == []
    previous.stop.assert_not_called()
    previous.remove.assert_not_called()


def test_runtime_data_and_secrets_are_excluded_from_root_build_context():
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "data/" in dockerignore.splitlines()
    assert "secrets/" in dockerignore.splitlines()
    assert ".agent/" in dockerignore.splitlines()
    assert "**/node_modules/" in dockerignore.splitlines()


def test_admin_image_packages_shared_config_apply_policy():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (PROJECT_ROOT / "admin_ui" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "context: ." in compose
    assert "dockerfile: admin_ui/Dockerfile" in compose
    assert "COPY src/config_apply.py ./config_apply.py" in dockerfile


def test_recovery_uses_previous_gpu_topology(tmp_path):
    (tmp_path / "docker-compose.gpu.yml").touch()

    flags = system._compose_files_flags_for_recovery(
        "local_ai_server",
        ["GPU_AVAILABLE=true", "TZ=UTC"],
        str(tmp_path),
    )

    assert flags == "-f docker-compose.yml -f docker-compose.gpu.yml"


def test_recovery_does_not_use_new_gpu_setting_for_previous_cpu_service(tmp_path):
    (tmp_path / "docker-compose.gpu.yml").touch()

    flags = system._compose_files_flags_for_recovery(
        "local_ai_server",
        ["GPU_AVAILABLE=false", "TZ=UTC"],
        str(tmp_path),
    )

    assert flags == "-f docker-compose.yml"
