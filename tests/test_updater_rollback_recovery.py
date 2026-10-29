"""Exercise the complete rollback runner with local Git and a Docker shim.

Linux CI uses Bash directly. Set AAVA_UPDATER_TEST_IMAGE to run the same fixtures
inside the built updater image, including its packaged Compose build helper.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("failure", ["none", "build", "late-call", "remote-local", "remote-compose"])
def test_rollback_recovery_preserves_runtime_and_pending_state(tmp_path, failure):
    image = os.getenv("AAVA_UPDATER_TEST_IMAGE")
    bash = shutil.which("bash")
    if not image and (not bash or subprocess.run([bash, "-c", "type mapfile >/dev/null"], capture_output=True).returncode):
        pytest.skip("rollback runner requires Bash 4+ or AAVA_UPDATER_TEST_IMAGE")
    if not image and (not shutil.which("jq") or not shutil.which("flock")):
        pytest.skip("rollback runner requires jq and flock")

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-b", "main")
    git("config", "user.name", "Synthetic updater fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / ".gitignore").write_text(".agent/\nbin/\n*.log\nprobes\n.env\n")
    (tmp_path / "payload").write_text("before")
    git("add", ".")
    git("commit", "-m", "baseline")
    before = git("rev-parse", "HEAD")
    git("branch", "baseline")
    (tmp_path / "payload").write_text("after")
    git("commit", "-am", "update")
    after = git("rev-parse", "HEAD")
    state = tmp_path / ".agent/updates"
    jobs = state / "jobs"
    jobs.mkdir(parents=True)
    backup = tmp_path / ".agent/update-backups/failed"
    backup.mkdir(parents=True)
    (backup / ".env").write_text("SYNTHETIC=baseline\n")
    remote = failure.startswith("remote-")
    (jobs / "failed.json").write_text(json.dumps({
        "pre_update_branch": "baseline", "pre_update_sha": before,
        "backup_dir_rel": ".agent/update-backups/failed", "include_ui": False,
        "pre_update_running_services": ["local_ai_server"] if remote else ["ai_engine"],
        # Legacy plans may omit Local AI despite reconciling its running container.
        "plan": {"services_rebuild": (["ai_engine"] if failure == "remote-compose" else
                                       ["local_ai_server"] if remote else ["ai_engine", "local_ai_server"]),
                 "services_restart": [], "compose_changed": failure != "remote-local"},
    }))
    pending = state / "pending-docker.json"
    pending.write_text(json.dumps({"version": 1, "base_sha": before, "target_sha": after,
                                   "rebuild": ["ai_engine", "local_ai_server"], "restart": [], "running": ["ai_engine"]}))
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    docker = bin_path / "docker"
    docker.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_COMMANDS"
if [ "$1 $2" = 'compose ps' ]; then
  case "$TEST_FAILURE" in remote-*) echo local_ai_server;; *)
    case "$*" in *--all*) printf 'ai_engine\\nlocal_ai_server\\n';; *) echo ai_engine;; esac;; esac
elif [ "$1" = exec ]; then
  n=0; [ -f "$TEST_PROBES" ] && n=$(cat "$TEST_PROBES")
  n=$((n + 1)); echo "$n" > "$TEST_PROBES"
  if [ "$TEST_FAILURE" = late-call ] && [ "$n" -gt 1 ]; then echo 2; else echo 0; fi
elif [ "$1 $2 $3" = 'compose build --print' ]; then
  echo '{"group":{"default":{"targets":["selected"]}},"target":{"selected":{"network":"host","tags":["synthetic:latest"]}}}'
elif [ "$1 $2" = 'compose build' ]; then
  echo 'additional privileges requested: pass "--allow=network.host" to grant requested privileges' >&2
  exit 1
elif [ "$1 $2" = 'buildx bake' ]; then
  cat >/dev/null
  [ "$TEST_FAILURE" = build ] && exit 7
else
  exit 0
fi
exit 0
""")
    docker.chmod(0o755)

    def invoke(job, scenario):
        location = "/fixture" if image else str(tmp_path)
        values = {"PROJECT_ROOT": location, "AAVA_UPDATE_MODE": "rollback", "AAVA_UPDATE_JOB_ID": job,
                  "AAVA_UPDATE_ROLLBACK_FROM_JOB": "failed", "AAVA_UPDATE_UPDATE_CLI_HOST": "false",
                  "AAVA_UPDATE_INCLUDE_UI": "false", "AAVA_UPDATE_FORCE_ACTIVE_CALLS": "true" if scenario == "remote-drained" else "false",
                  "TEST_COMMANDS": f"{location}/commands.log", "TEST_PROBES": f"{location}/probes",
                  "TEST_FAILURE": scenario,
                  "PATH": f"{location}/bin:" + ("/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" if image else os.environ["PATH"])}
        if image:
            args = [shutil.which("docker"), "run", "--rm", "--mount", f"type=bind,source={tmp_path.resolve()},target=/fixture"]
            for key, value in values.items():
                args += ["-e", f"{key}={value}"]
            args += [image]
            return subprocess.run(args, capture_output=True, text=True, timeout=90)
        values["AAVA_COMPOSE_BUILD_HELPER"] = str(ROOT / "scripts/compose-build.sh")
        return subprocess.run([bash, str(ROOT / "updater/run.sh")], env=dict(os.environ, **values),
                              capture_output=True, text=True, timeout=90)

    result = invoke("rollback", failure)
    assert (result.returncode == 0) == (failure == "none"), result.stdout + result.stderr
    commands = (tmp_path / "commands.log").read_text().splitlines()
    changes = [command for command in commands if command.startswith("compose up") or command.startswith("compose restart")]
    if failure != "none":
        assert not changes, "failed build/late active calls must not change containers"
        assert pending.exists(), "failed rollback must preserve pending actions"
        if remote:
            assert "remote active calls" in result.stdout + result.stderr
            assert git("rev-parse", "HEAD") == after, "unverified remote drain must block before checkout"
        (tmp_path / "probes").unlink(missing_ok=True)
        result = invoke("retry", "remote-drained" if remote else "none")
        assert result.returncode == 0, result.stdout + result.stderr
        archive = state / "rolled-back/retry.pending-docker.json"
    else:
        archive = state / "rolled-back/rollback.pending-docker.json"
    assert git("rev-parse", "HEAD") == before
    assert (tmp_path / ".env").read_text() == "SYNTHETIC=baseline\n"
    assert archive.exists() and not pending.exists()
    commands = (tmp_path / "commands.log").read_text().splitlines()
    changes = [command for command in commands if command.startswith("compose up") or command.startswith("compose restart")]
    assert changes
    if not remote:
        assert all("local_ai_server" not in command for command in changes), "stopped Local AI must stay stopped"
    if failure == "remote-compose":
        assert any("local_ai_server" in command for command in changes)
        assert not any(command.startswith("compose build") for command in commands), "legacy plan has no installed rebuild target"
    else:
        target = "local_ai_server" if remote else "ai_engine"
        assert commands.index(f"compose build {target}") < next(i for i, command in enumerate(commands) if command.startswith("compose up"))
        assert "buildx bake --file - --allow=network.host --load" in commands
