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
    shutil.copy(ROOT / "cli/internal/buildpolicy/compose-build.sh", tmp_path / "scripts/compose-build.sh")
    log = tmp_path / "commands"
    docker = tmp_path / "docker"
    docker.write_text('''#!/bin/bash
if [[ "$1 $2" == 'context show' ]]; then echo default; exit 0; fi
if [[ "$1" == info ]]; then echo synthetic-engine; exit 0; fi
if [[ "$1 $2" == 'buildx inspect' ]]; then printf 'Driver: docker\\nStatus: running\\n'; exit 0; fi
if [[ " $* " == *' --help '* ]]; then
  if [[ "$1 $2" == 'buildx bake' && "${TEST_BAD_CAPABILITY:-}" == buildx ]]; then echo '--load'; exit 0; fi
  if [[ "$1" == compose && "${TEST_BAD_CAPABILITY:-}" == compose ]]; then echo '--no-cache'; exit 0; fi
  echo '--print --allow --load'; exit 0
fi
echo "$*" >> "$TEST_COMMANDS"
echo "${TEST_COMPOSE_EXECUTABLE:-docker}|$*" >> "$TEST_EXECUTABLES"
if [[ "$1" == compose && "${TEST_COMPOSE_MISSING:-}" == 1 && "${TEST_COMPOSE_EXECUTABLE:-docker}" != docker-compose ]]; then
  exit 1
fi
if [[ "$1 $2" == 'compose version' ]]; then echo v5.0.2; exit 0; fi
if [[ "$1 $2" == 'buildx version' ]]; then
  [[ "${TEST_BAD_CAPABILITY:-}" != missing ]] || exit 1
  echo v0.31.1; exit 0
fi
if [[ " $* " == *" --print "* ]]; then
  python3 - "$@" <<'PYBAKE'
import json, sys
print(json.dumps({"target": {s: {"network": "host"} for s in sys.argv if s in ("admin_ui", "ai_engine", "local_ai_server")}}))
PYBAKE
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
    standalone.write_text('#!/bin/bash\nTEST_COMPOSE_EXECUTABLE=docker-compose exec "$(dirname "$0")/docker" compose "$@"\n')
    standalone.chmod(0o700)
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
               TEST_COMMANDS=str(log), TEST_EXECUTABLES=str(tmp_path / "executables"), TEST_FAILURE=failure)
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
    assert f"buildx bake --file - --allow=network.host --load {service}" in calls


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


@pytest.mark.parametrize("guide", ["README.md", "docs/INSTALLATION.md", "admin_ui/UI_Setup_Guide.md"])
@pytest.mark.parametrize("failure", ["none", "entitlement", "unrelated"])
@pytest.mark.parametrize("standalone", [False, True])
def test_copied_quick_start_retains_compose_selection(tmp_path, guide, failure, standalone):
    """Run each guide's actual selector and startup on both fresh-host modes."""
    env, log = prepare_project(tmp_path, failure)
    env["TEST_COMPOSE_MISSING"] = "1" if standalone else "0"
    source = (ROOT / guide).read_text()
    blocks = re.findall(r"^```bash\n(.*?)^```", source, re.S | re.M)
    bootstrap = next(block for block in blocks if "./install.sh --ui" in block)
    shutil.copy(ROOT / "install.sh", tmp_path / "install.sh")
    shutil.copy(ROOT / "preflight.sh", tmp_path / "preflight.sh")
    (tmp_path / ".env.example").write_text("JWT_SECRET=\nPROVIDER_SECRET=synthetic-retained\n")
    env["INSTALL_NONINTERACTIVE"] = "1"
    # Keep system preparation out of this fixture; exercise the real installer
    # and its read-only preflight report before building/starting management.
    (tmp_path / "id").write_text('#!/bin/sh\ncase "$1" in -u) echo 1000;; -g) echo 995;; *) echo synthetic;; esac\n')
    (tmp_path / "id").chmod(0o700)
    # Clone/preflight are outside this CLI-sequence fixture; keep the rest of
    # each copied block intact, including any commands after Admin UI startup.
    for name in ["git", "sudo"]:
        executable = tmp_path / name
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o700)
    (tmp_path / "AVA-AI-Voice-Agent-for-Asterisk").symlink_to(tmp_path, target_is_directory=True)
    result = subprocess.run(["bash", "-c", bootstrap],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert (result.returncode == 0) == (failure != "unrelated"), result.stdout + result.stderr
    assert "PROVIDER_SECRET=synthetic-retained" in (tmp_path / ".env").read_text()
    starts = [call for call in log.read_text().splitlines() if " up " in call]
    assert len(starts) == int(failure != "unrelated")
    if starts:
        assert starts[0].endswith(" admin_ui")
        assert "--no-build --pull never" in starts[0]
    compose_calls = [line for line in (tmp_path / "executables").read_text().splitlines()
                     if " build " in line or " up " in line]
    expected = "docker-compose" if standalone else "docker"
    assert compose_calls and all(line.startswith(expected + "|") for line in compose_calls)
    assert all("ai_engine" not in line and "local_ai_server" not in line for line in compose_calls)


@pytest.mark.parametrize("standalone", [False, True])
def test_legacy_no_cache_recipe_bypasses_helper(tmp_path, standalone):
    """Old checkouts retain cache flags through direct Compose-to-Bake export."""
    env, log = prepare_project(tmp_path, "none")
    env["TEST_COMPOSE_MISSING"] = "1" if standalone else "0"
    source = (ROOT / "docs/CLI_TOOLS_GUIDE.md").read_text()
    blocks = re.findall(r"^```bash\n(.*?)^```", source, re.S | re.M)
    recipe = next(block for block in blocks if "build --no-cache --print admin_ui" in block)
    result = subprocess.run(["bash", "-c", recipe], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    builds = [call for call in log.read_text().splitlines() if " build " in call]
    assert builds == ["compose -p asterisk-ai-voice-agent build --no-cache --print admin_ui"]
    selected = "docker-compose" if standalone else "docker"
    operations = [line for line in (tmp_path / "executables").read_text().splitlines()
                  if " build " in line or " up " in line]
    assert operations and all(line.startswith(selected + "|") for line in operations)
    assert all("ai_engine" not in line and "local_ai_server" not in line for line in operations)


def _preflight_function(name):
    source = (ROOT / "preflight.sh").read_text()
    start = source.index(name + "() {\n")
    end_start = source.index("\nMANIFESTEOF", start) if name == "_write_asterisk_manifest" else start
    return source[start:source.index("\n}\n", end_start) + 3]


@pytest.mark.parametrize("existing", [False, True])
def test_check_only_preflight_never_seeds_or_changes_operator_files(tmp_path, existing):
    (tmp_path / ".env.example").write_text("JWT_SECRET=\n")
    before = "JWT_SECRET=\nGPU_AVAILABLE=true\nPROVIDER_SECRET=synthetic-retained\n"
    if existing:
        (tmp_path / ".env").write_text(before)
    functions = "\n".join(_preflight_function(n) for n in (
        "check_env", "check_host_project_root", "update_env_gpu", "_write_asterisk_manifest",
    ))
    script = functions + '''
log_ok() { :; }; log_warn() { :; }; log_info() { :; }
APPLY_FIXES=false; SCRIPT_DIR="$1"; LOCAL_AI_MODE_OVERRIDE=minimal
check_env
check_host_project_root
update_env_gpu false
_write_asterisk_manifest false unknown /synthetic false unknown '{}'
'''
    subprocess.run(["bash", "-c", script, "fixture", str(tmp_path)], check=True, capture_output=True)
    assert (tmp_path / ".env").exists() == existing
    if existing:
        assert (tmp_path / ".env").read_text() == before
    assert not (tmp_path / "data").exists()


def test_preflight_accepts_standalone_compose_new_major(tmp_path):
    env, _ = prepare_project(tmp_path, "none")
    env["TEST_COMPOSE_MISSING"] = "1"
    script = _preflight_function("check_compose") + '''
log_ok() { echo "$1"; }; log_warn() { echo "$1"; }; log_info() { :; }
log_fail() { echo "FAILED: $1"; }; platform_yaml_get() { return 1; }
github_docs_url() { echo "$1"; }; print_fix_and_docs() { :; }
APPLY_FIXES=false; FIX_CMDS=()
check_compose
'''
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=True)
    assert "FAILED" not in result.stdout
    assert "Docker Compose: 5.0.2" in result.stdout


@pytest.mark.parametrize("family", ["debian", "rhel"])
@pytest.mark.parametrize("capability", ["missing", "buildx", "compose", "healthy", "healthy_without_alias"])
def test_preflight_repairs_actual_cli_capabilities_with_platform_packages(tmp_path, family, capability):
    env, log = prepare_project(tmp_path, "none")
    env["TEST_BAD_CAPABILITY"] = capability
    script = _preflight_function("check_compose") + '''
log_ok() { :; }; log_warn() { :; }; log_info() { :; }; log_fail() { :; }
platform_yaml_get() { return 1; }; github_docs_url() { echo "$1"; }
print_fix_and_docs() { :; }
command() {
    if [[ "$TEST_BAD_CAPABILITY" == healthy_without_alias && "$1 $2" == '-v docker-compose' ]]; then return 1; fi
    builtin command "$@"
}
APPLY_FIXES=false; FIX_CMDS=(); OS_FAMILY="$1"
check_compose
printf '%s\\n' "${FIX_CMDS[@]}"
'''
    result = subprocess.run(["bash", "-c", script, "fixture", family], env=env, capture_output=True, text=True, check=True)
    repairs = result.stdout.strip()
    if capability.startswith("healthy"):
        assert not repairs
    else:
        package = "docker-compose-plugin" if capability == "compose" else "docker-buildx-plugin"
        assert package in repairs
        assert ("apt-get" in repairs) == (family == "debian")
        assert "curl" not in repairs and "amd64" not in repairs and "v0.17" not in repairs
    assert not any("install -y" in c for c in log.read_text().splitlines()), "check-only mode must not execute repairs"


@pytest.mark.parametrize("initial_umask", ["022", "077"])
@pytest.mark.parametrize("existing_directories", [False, True])
def test_ui_bootstrap_prepares_directories_without_leaking_private_umask(tmp_path, initial_umask, existing_directories):
    """Fresh and partially prepared mounts stay usable without changing DB files."""
    env, _ = prepare_project(tmp_path, "none")
    source = (ROOT / "install.sh").read_text().rsplit('\nmain "$@"', 1)[0]
    (tmp_path / "install-functions.sh").write_text(source)
    (tmp_path / ".env.example").write_text("JWT_SECRET=\nPROVIDER_SECRET=synthetic-retained\n")
    if existing_directories:
        for name in ("data", "asterisk_media"):
            (tmp_path / name).mkdir(mode=0o700)
        sentinel = tmp_path / "data/operator-owned.db"
        sentinel.write_bytes(b"private fixture: leave unchanged")
        sentinel.chmod(0o600)
    sudo = tmp_path / "sudo"
    sudo.write_text('#!/bin/sh\ncase "$1" in mkdir|chmod) exec "$@";; *) exit 0;; esac\n')
    sudo.chmod(0o700)
    script = f'''source ./install-functions.sh
SUDO=sudo
getenforce() {{ echo Disabled; }}
choose_compose_cmd || exit $?
umask {initial_umask}
bootstrap_ui || exit $?
printf 'RESTORED_UMASK=%s\\n' "$(umask)"
'''
    result = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    actual_umask = re.search(r"RESTORED_UMASK=([0-7]+)", result.stdout).group(1)
    assert int(actual_umask, 8) == int(initial_umask, 8)
    for name in ("data", "asterisk_media"):
        assert (tmp_path / name).stat().st_mode & 0o2775 == 0o2775
    assert (tmp_path / "secrets").stat().st_mode & 0o2770 == 0o2770
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o640
    assert "PROVIDER_SECRET=synthetic-retained" in (tmp_path / ".env").read_text()
    if existing_directories:
        assert sentinel.read_bytes() == b"private fixture: leave unchanged"
        assert sentinel.stat().st_mode & 0o777 == 0o600
