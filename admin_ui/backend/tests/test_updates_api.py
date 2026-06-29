import os
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import system  # noqa: E402


def _jobs_dir(root: Path) -> Path:
    jobs = root / ".agent" / "updates" / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    return jobs


def test_cli_install_path_validation_accepts_simple_absolute_path() -> None:
    assert system._validate_cli_install_path("/usr/local/bin/agent") == "/usr/local/bin/agent"
    assert system._validate_cli_install_path("  /opt/aava-agent_1.2/bin/agent  ") == "/opt/aava-agent_1.2/bin/agent"
    assert system._validate_cli_install_path("") is None


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("v7.2.0", "v7.2.0"),
        ("7.2.0", "7.2.0"),
        ("main", None),
        ("codex/UI-Update-Improvements", None),
        ("feature/foo", None),
    ],
)
def test_updater_pull_preference_only_for_release_targets(ref: str, expected: str | None) -> None:
    assert system._updater_prefer_pull_ref_for_update_target(ref) == expected


@pytest.mark.parametrize(
    "value",
    [
        "agent",
        "/tmp/agent;rm",
        "/tmp/agent $(touch x)",
        "/tmp/../agent",
        "/tmp/agent name",
        "/tmp/agent\x00x",
    ],
)
def test_cli_install_path_validation_rejects_unsafe_paths(value: str) -> None:
    with pytest.raises(HTTPException) as exc:
        system._validate_cli_install_path(value)
    assert exc.value.status_code == 400


def test_read_update_job_marks_running_job_stale(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    job_id = uuid.uuid4().hex
    state = _jobs_dir(tmp_path) / f"{job_id}.json"
    state.write_text(
        '{"job_id":"%s","status":"running","started_at":"2020-01-01T00:00:00Z"}' % job_id,
        encoding="utf-8",
    )
    old = time.time() - system._UPDATE_STALE_AFTER_SEC - 60
    os.utime(state, (old, old))

    job, _state_path, _log_path = system._read_update_job(job_id)

    assert job["status"] == "stale"
    assert job["stale"] is True
    assert "heartbeat" in job["failure_reason"]


def test_find_active_update_job_ignores_stale_jobs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    jobs = _jobs_dir(tmp_path)
    stale_id = uuid.uuid4().hex
    active_id = uuid.uuid4().hex

    stale_state = jobs / f"{stale_id}.json"
    stale_state.write_text(
        '{"job_id":"%s","status":"running","started_at":"2020-01-01T00:00:00Z"}' % stale_id,
        encoding="utf-8",
    )
    old = time.time() - system._UPDATE_STALE_AFTER_SEC - 60
    os.utime(stale_state, (old, old))

    active_state = jobs / f"{active_id}.json"
    active_state.write_text('{"job_id":"%s","status":"running"}' % active_id, encoding="utf-8")

    active = system._find_active_update_job()

    assert active is not None
    assert active["job_id"] == active_id


@pytest.mark.asyncio
async def test_updates_job_log_returns_full_log(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    job_id = uuid.uuid4().hex
    log = _jobs_dir(tmp_path) / f"{job_id}.log"
    log.write_text("line 1\nline 2\n", encoding="utf-8")

    response = await system.updates_job_log(job_id)

    assert response.job_id == job_id
    assert response.log == "line 1\nline 2\n"
