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


def test_ai_engine_sessions_stats_urls_use_configured_health_port(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AI_ENGINE_HEALTH_URL", raising=False)
    monkeypatch.delenv("HEALTH_BIND_PORT", raising=False)
    monkeypatch.setattr(system, "_dotenv_value", lambda key: "18000" if key == "HEALTH_BIND_PORT" else None)

    urls = system._ai_engine_sessions_stats_urls()

    assert "http://127.0.0.1:18000/sessions/stats" in urls
    assert "http://ai_engine:18000/sessions/stats" in urls
    assert "http://ai-engine:18000/sessions/stats" in urls


def test_configured_ai_engine_health_port_reads_yaml(monkeypatch, tmp_path) -> None:
    import settings

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    base = config_dir / "ai-agent.yaml"
    local = config_dir / "ai-agent.local.yaml"
    base.write_text("health:\n  port: 16000\n", encoding="utf-8")
    local.write_text("health:\n  port: 17000\n", encoding="utf-8")

    monkeypatch.delenv("HEALTH_BIND_PORT", raising=False)
    monkeypatch.setattr(system, "_dotenv_value", lambda _key: None)
    monkeypatch.setattr(settings, "CONFIG_PATH", str(base))
    monkeypatch.setattr(settings, "LOCAL_CONFIG_PATH", str(local))

    assert system._configured_ai_engine_health_port() == 17000


@pytest.mark.parametrize(
    "value",
    [
        "agent",
        "/opt/agent;rm",
        "/opt/agent $(touch x)",
        "/opt/../agent",
        "/opt/agent name",
        "/opt/agent\x00x",
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

    active_state = jobs / f"{active_id}.json"
    active_state.write_text('{"job_id":"%s","status":"running"}' % active_id, encoding="utf-8")

    stale_state = jobs / f"{stale_id}.json"
    stale_state.write_text(
        '{"job_id":"%s","status":"running","started_at":"2020-01-01T00:00:00Z"}' % stale_id,
        encoding="utf-8",
    )

    def fake_stale(job: dict, **_kwargs) -> bool:
        return job.get("job_id") == stale_id

    monkeypatch.setattr(system, "_is_update_job_stale", fake_stale)

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


@pytest.mark.asyncio
async def test_updater_image_status_reads_persisted_progress(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))

    system._write_updater_image_status(
        status="running",
        phase="building",
        image="aava-updater:test",
        message="Building updater image from local source",
        detail_tail=["#1 loading", "#2 building"],
        started_at="2026-01-01T00:00:00Z",
    )

    response = await system.updates_updater_image_status()

    assert response.status["status"] == "running"
    assert response.status["phase"] == "building"
    assert response.status["image"] == "aava-updater:test"
    assert response.status["detail_tail"] == ["#1 loading", "#2 building"]
