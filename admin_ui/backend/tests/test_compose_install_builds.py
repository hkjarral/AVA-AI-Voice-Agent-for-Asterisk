"""Exercise the selected build/start sequence without a real Docker daemon."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api import system, wizard, rebuild_jobs  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("failure", ["none", "entitlement", "unrelated", "export", "bake"])
def test_build_start_retains_options_and_stops_on_failure(tmp_path, monkeypatch, failure):
    project = tmp_path / "project with spaces"
    (project / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "cli/internal/buildpolicy/compose-build.sh", project / "scripts/compose-build.sh")
    commands = tmp_path / "commands.jsonl"
    docker = tmp_path / "docker"
    docker.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["compose", "version"]: sys.exit(0)
if args[:2] == ["context", "show"]: print("default"); sys.exit(0)
if args[0] == "info": print("synthetic-engine"); sys.exit(0)
if args[:2] == ["buildx", "inspect"]: print("Driver: docker\\nStatus: running"); sys.exit(0)
if "--help" in args: print("--print --allow --load"); sys.exit(0)
with open(os.environ["TEST_COMMANDS"], "a") as log:
    log.write(json.dumps(args) + "\\n")
failure = os.environ["TEST_FAILURE"]
if args[:2] == ["buildx", "bake"]:
    definition = json.load(sys.stdin)
    assert definition["args"][-1] == "local_ai_server"
    assert "--no-cache" in definition["args"]
    assert definition["args"][definition["args"].index("--build-arg") + 1] == "INCLUDE_TEST=value with spaces;$(touch injected)"
    sys.exit(4 if failure == "bake" else 0)
if "build" in args:
    if "--print" in args:
        if failure == "export":
            sys.exit(3)
        print("warning on stderr", file=sys.stderr)
        print(json.dumps({"args": args, "target": {"local_ai_server": {"network": "host"}}}))
        sys.exit(0)
    if failure == "none":
        sys.exit(0)
    print("package download failed" if failure == "unrelated" else 'additional privileges requested: pass "--allow=network.host"', file=sys.stderr)
    sys.exit(1)
assert "up" in args
assert "--no-build" in args
assert args[args.index("--pull") + 1] == "never"
''')
    docker.chmod(0o700)
    monkeypatch.setattr(system, "_compose_files_flags_for_service", lambda _: "-f docker-compose.yml -f docker-compose.gpu.yml")
    command = system._compose_service_command(
        "local_ai_server", build=True, no_cache=True, recreate=True,
        build_args=["--build-arg", "INCLUDE_TEST=value with spaces;$(touch injected)"],
    )
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
               PROJECT_ROOT=str(project), TEST_COMMANDS=str(commands), TEST_FAILURE=failure)
    result = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)
    assert (result.returncode == 0) == (failure in {"none", "entitlement"}), result.stdout + result.stderr
    calls = [json.loads(line) for line in commands.read_text().splitlines()]
    compose_options = ["compose", "-f", "docker-compose.yml", "-f", "docker-compose.gpu.yml", "-p", "asterisk-ai-voice-agent"]
    assert calls[0][:len(compose_options)] == compose_options
    if failure in {"entitlement", "export", "bake"}:
        exported = calls[1]
        assert exported == calls[0][:-1] + ["--print", "local_ai_server"]
    up_calls = [call for call in calls if "up" in call]
    assert len(up_calls) == int(failure in {"none", "entitlement"})
    if up_calls:
        assert up_calls[0][:len(compose_options)] == compose_options
        assert up_calls[0][-1] == "local_ai_server"
        assert "--force-recreate" in up_calls[0]
    assert not (project / "injected").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("action,exists,build_fails", [
    ("start", False, False), ("start", False, True),
    ("rebuild", False, False), ("rebuild", True, False),
    ("restart", True, False),
])
async def test_wizard_engine_builds_once_before_start(monkeypatch, action, exists, build_fails):
    monkeypatch.setattr(wizard, "setup_media_paths", lambda: {"success": True})
    monkeypatch.setattr(wizard.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(wizard.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

    def get_container(_):
        if not exists:
            raise wizard.docker.errors.NotFound("missing")
        return SimpleNamespace(status="running")

    monkeypatch.setattr(wizard.docker, "from_env", lambda: SimpleNamespace(containers=SimpleNamespace(get=get_container)))
    monkeypatch.setattr(system, "_project_host_root_from_admin_ui_container", lambda: "/synthetic/host/project")
    calls = []

    def run(host_root, **kwargs):
        assert host_root == kwargs["env"]["PROJECT_ROOT"] == "/synthetic/host/project"
        calls.append(kwargs["command"])
        return (1, "build failed") if build_fails else (0, "ok")

    monkeypatch.setattr(system, "_run_updater_ephemeral", run)

    class HealthyClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, _):
            return SimpleNamespace(status_code=200, json=lambda: {"providers": {}})

    monkeypatch.setattr(wizard.httpx, "AsyncClient", HealthyClient)
    result = await wizard.start_engine(action)
    assert result["success"] is not build_fails
    builds = [call for call in calls if "compose-build.sh" in call]
    assert len(builds) == int(action == "rebuild" or not exists)
    if builds:
        assert ("--no-cache" in builds[0]) == (action == "rebuild")
        assert " up " not in builds[0]
    starts = [call for call in calls if " up " in call]
    assert len(starts) == int(not build_fails)
    if starts:
        assert "--no-build --pull never" in starts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("rebuild,first_code", [(False, 0), (False, 1), (True, 0)])
async def test_local_wizard_uses_selected_build_path(monkeypatch, rebuild, first_code):
    monkeypatch.setattr(wizard, "setup_media_paths", lambda: {})
    monkeypatch.setattr(rebuild_jobs, "_read_env_file", lambda _: {"INCLUDE_FASTER_WHISPER": "true"} if rebuild else {})
    monkeypatch.setattr(system, "_project_host_root_from_admin_ui_container", lambda: "/synthetic/project")
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs["command"])
        return (first_code, "No such image") if len(calls) == 1 else (0, "ok")

    monkeypatch.setattr(system, "_run_updater_ephemeral", run)
    result = await wizard.start_local_ai_server()
    assert result["success"]
    assert ("compose-build.sh" in calls[-1]) == (rebuild or first_code != 0)
    assert all("--no-build --pull never" in call for call in calls)


def test_rebuild_job_preserves_backend_arguments(monkeypatch):
    monkeypatch.setattr(system, "_project_host_root_from_admin_ui_container", lambda: "/synthetic/project")
    monkeypatch.setattr(rebuild_jobs, "get_rebuild_job", lambda _: SimpleNamespace(backend="faster_whisper"))
    monkeypatch.setattr(rebuild_jobs, "_read_env_file", lambda _: {})
    monkeypatch.setattr(rebuild_jobs, "_job_output", lambda *a: None)
    monkeypatch.setattr(rebuild_jobs.threading, "Thread", lambda **k: SimpleNamespace(start=lambda: None))
    calls = []
    monkeypatch.setattr(system, "_run_updater_ephemeral", lambda *a, **k: (calls.append(k["command"]) or 0, "ok"))
    assert rebuild_jobs._run_docker_build("synthetic-job")
    assert "compose-build.sh" in calls[0]
    assert "--no-cache --build-arg INCLUDE_FASTER_WHISPER=true" in calls[0]
    assert " up " not in calls[0]


def test_updater_build_timeout_cancels_runner_and_children(tmp_path, monkeypatch):
    """Replacing Docker with a shell runner must not leave builds on timeout."""
    import time

    child_pid = tmp_path / "child.pid"
    real_popen = subprocess.Popen
    captured = []

    def launch(command, **kwargs):
        captured.append(command)
        assert kwargs["start_new_session"] is True
        return real_popen(["bash", "-c", 'sleep 60 & echo $! > "$1"; wait', "fixture", str(child_pid)], **kwargs)

    monkeypatch.setattr(system.subprocess, "Popen", launch)
    monkeypatch.setattr(system, "_write_updater_image_status", lambda **_: None)
    code, _ = system._run_docker_with_updater_status(
        ["build", "-f", "updater/Dockerfile", "."], cwd=str(tmp_path),
        timeout_sec=0.2, image="synthetic:latest", phase="building", message="fixture",
    )
    assert code == 124
    assert captured[0][0] == "bash" and captured[0][2] == "--docker-build"
    assert "--network=host" not in captured[0], "the runner owns build policy"
    monkeypatch.setattr(system.subprocess, "Popen", real_popen)
    pid = child_pid.read_text().strip()
    for _ in range(20):
        state = subprocess.run(["ps", "-o", "stat=", "-p", pid], capture_output=True, text=True).stdout.strip()
        if not state or state.startswith("Z"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("timed-out runner left a live child process")
