"""Run the installer's and preflight's published sequences against a Docker shim."""

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def prepare_project(tmp_path, failure):
    """Create isolated Docker/Compose shims with selectable build failures."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts/compose-build.sh", tmp_path / "scripts/compose-build.sh")
    log = tmp_path / "commands"
    docker = tmp_path / "docker"
    docker.write_text('''#!/bin/bash
echo "$*" >> "$TEST_COMMANDS"
if [[ "$*" == "compose version" && "${TEST_COMPOSE_MISSING:-}" == 1 ]]; then
  exit 1
fi
if [[ " $* " == *" --print "* ]]; then
  echo '{"target":{"admin_ui":{"network":"host"}}}'
elif [[ "$1 $2" == "buildx bake" ]]; then
  cat >/dev/null
elif [[ " $* " == *" build "* ]]; then
  case "$TEST_FAILURE" in
    none) exit 0;;
    unrelated) echo "package download failed" >&2; exit 1;;
    *) echo 'additional privileges requested: pass "--allow=network.host"' >&2; exit 1;;
  esac
fi
''')
    docker.chmod(0o700)
    standalone = tmp_path / "docker-compose"
    standalone.write_text('#!/bin/bash\nexec "$(dirname "$0")/docker" compose "$@"\n')
    standalone.chmod(0o700)
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
               TEST_COMMANDS=str(log), TEST_FAILURE=failure)
    return env, log


@pytest.mark.parametrize("failure", ["none", "entitlement", "unrelated"])
@pytest.mark.parametrize("standalone", [False, True])
def test_installer_build_failure_never_starts_service(tmp_path, failure, standalone):
    """Start only the selected image, and only after its build succeeds."""
    env, log = prepare_project(tmp_path, failure)
    source = (ROOT / "install.sh").read_text()
    start = source.index("build_and_start_service() (")
    end = source.index("\n)\n", start) + 3
    installer = tmp_path / "install.sh"
    compose_bin = "docker-compose" if standalone else ""
    compose = "docker-compose" if standalone else "docker compose"
    installer.write_text(source[start:end] + f'\nCOMPOSE_BIN={compose_bin}\nCOMPOSE="{compose} -p asterisk-ai-voice-agent"\nbuild_and_start_service admin_ui\n')
    result = subprocess.run(["bash", str(installer)], env=env, capture_output=True, text=True)
    assert (result.returncode == 0) == (failure != "unrelated"), result.stderr
    calls = log.read_text().splitlines()
    starts = [call for call in calls if " up " in call]
    assert len(starts) == int(failure != "unrelated")
    if starts:
        assert starts == ["compose -p asterisk-ai-voice-agent up -d --no-build --pull never admin_ui"]
    assert all("ai_engine" not in call and "local_ai_server" not in call for call in calls)


@pytest.mark.parametrize("warnings,local_only", [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("standalone", [False, True])
def test_preflight_prints_working_selected_build_sequence(tmp_path, warnings, local_only, standalone):
    """Execute preflight's printed commands in both supported Compose modes."""
    env, log = prepare_project(tmp_path, "entitlement")
    source = (ROOT / "preflight.sh").read_text()
    start = source.index("print_summary() {\n")
    end = source.index("\n}\n", start) + 3
    script = tmp_path / "summary.sh"
    script.write_text(source[start:end] + f'''
SCRIPT_DIR="$PWD"
COMPOSE_CMD="{'docker-compose' if standalone else 'docker compose'}"
FAILURES=(); WARNINGS=({'warning' if warnings else ''}); MANUAL_CMDS=(); FIX_CMDS=()
LOCAL_SERVER_ONLY={'true' if local_only else 'false'}
print_summary
''')
    result = subprocess.run(["bash", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    service = "local_ai_server" if local_only else "admin_ui"
    sequence = re.search(
        rf"((?:AAVA_COMPOSE_BIN=docker-compose )?COMPOSE_PROJECT_NAME=asterisk-ai-voice-agent ./scripts/compose-build.sh {service} &&\n\s*docker(?: compose|-compose)[^\n]+ {service})",
        result.stdout,
    )
    assert sequence, result.stdout
    run = subprocess.run(["bash", "-c", sequence.group(1)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    calls = log.read_text().splitlines()
    assert calls[-1] == f"compose -p asterisk-ai-voice-agent up -d --no-build --pull never {service}"
    assert "buildx bake --file - --allow=network.host --load" in calls


@pytest.mark.parametrize("options", [[], ["--no-cache"], ["--push", "admin_ui"], ["-f"], ["--build-arg", "--no-cache", "admin_ui"]])
def test_helper_rejects_missing_services_and_unsupported_options(tmp_path, options):
    """Reject invalid input before invoking Docker or creating runtime effects."""
    env, log = prepare_project(tmp_path, "none")
    result = subprocess.run(["bash", str(ROOT / "scripts/compose-build.sh"), *options], env=env, capture_output=True)
    assert result.returncode == 2
    assert not log.exists()


@pytest.mark.parametrize("failure", ["none", "entitlement", "unrelated"])
@pytest.mark.parametrize("standalone,selected", [(False, False), (True, False), (False, True), (True, True)])
def test_preflight_recovery_hint_builds_before_recreation(tmp_path, failure, standalone, selected):
    """Recovery hints work before Compose detection and with no existing image."""
    env, log = prepare_project(tmp_path, failure)
    env["TEST_COMPOSE_MISSING"] = "1" if standalone else "0"
    source = (ROOT / "preflight.sh").read_text()
    start = source.index("admin_ui_recreate_command() {\n")
    end = source.index("\n}\n", start) + 3
    mode = ("docker-compose" if standalone else "docker compose") if selected else ""
    script = source[start:end] + f'\nCOMPOSE_CMD="{mode}"\nadmin_ui_recreate_command\n'
    printed = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=True)
    command = printed.stdout.strip()
    assert ("AAVA_COMPOSE_BIN=docker-compose" in command) == standalone
    assert "./scripts/compose-build.sh admin_ui &&" in command
    result = subprocess.run(["bash", "-c", command], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert (result.returncode == 0) == (failure != "unrelated"), result.stderr
    starts = [call for call in log.read_text().splitlines() if " up " in call]
    assert starts == ([] if failure == "unrelated" else [
        "compose -p asterisk-ai-voice-agent up -d --no-build --pull never --force-recreate admin_ui",
    ])
