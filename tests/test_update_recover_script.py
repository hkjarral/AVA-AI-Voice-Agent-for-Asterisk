import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update-recover.sh"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _run_bash_harness(
    script: str,
    tmp_path: Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    source = tmp_path / "update-recover-source.sh"
    source.write_text(_script().replace('\nmain "$@"\n', "\n"), encoding="utf-8")
    return subprocess.run(
        ["/bin/bash", "-c", script],
        check=check,
        capture_output=True,
        text=True,
        env={
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
            "SCRIPT": str(SCRIPT),
            "SOURCE": str(source),
        },
    )


def test_update_recover_script_has_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_update_recover_help_documents_operator_choices() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--local-changes POLICY" in result.stdout
    assert "retain     Stash tracked local changes" in result.stdout
    assert "overwrite  Discard tracked source-code edits" in result.stdout
    assert "--plan-only" in result.stdout
    assert "Include untracked files in retain-mode updater stash" in result.stdout
    assert "Git-tracked path" in result.stdout


def test_update_recover_supports_release_and_branch_cli_bootstrap() -> None:
    script = _script()

    assert "releases/download/${version}" in script
    assert "mktemp -d /tmp/aava-cli-install.XXXXXXXXXX" in script
    assert "SHA256SUMS" in script
    assert "install_branch_cli()" in script
    assert "git clone --quiet --depth 1 --single-branch --branch" in script
    assert "golang:1.22-bookworm" in script
    assert "AAVA_CLI_VERSION=${ref}" in script
    assert "--self-update=false" in script


def test_update_recover_release_bootstrap_fetches_pinned_installer(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    (fake_bin / "curl").write_text(
        "#!/bin/bash\n"
        "out=''\n"
        "url=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    -o) out=\"$2\"; shift 2 ;;\n"
        "    http*) url=\"$1\"; shift ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "printf 'curl %s %s\\n' \"$url\" \"$out\" >>\"$AAVA_TEST_LOG\"\n"
        "case \"$url\" in\n"
        "  */SHA256SUMS) printf 'abc123  agent-linux-amd64\\n' >\"$out\" ;;\n"
        "  *) printf 'binary\\n' >\"$out\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (fake_bin / "sha256sum").write_text(
        "#!/bin/sh\n"
        "printf 'abc123  %s\\n' \"$1\"\n",
        encoding="utf-8",
    )
    (fake_bin / "uname").write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  -s) printf 'Linux\\n' ;;\n"
        "  -m) printf 'x86_64\\n' ;;\n"
        "  *) /usr/bin/uname \"$@\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (fake_bin / "curl").chmod(0o755)
    (fake_bin / "sha256sum").chmod(0o755)
    (fake_bin / "uname").chmod(0o755)

    harness = f"""
    set -euo pipefail
    export AAVA_TEST_LOG={log}
    source "$SOURCE"
AGENT_BIN="{tmp_path}/install/agent"
install_release_cli v7.4.2
"""
    _run_bash_harness(harness, tmp_path)

    commands = log.read_text(encoding="utf-8")
    assert "/releases/download/v7.4.2/agent-linux-amd64" in commands
    assert "/releases/download/v7.4.2/SHA256SUMS" in commands
    assert "/main/scripts/install-cli.sh" not in commands
    assert (tmp_path / "install" / "agent").read_text(encoding="utf-8") == "binary\n"


def test_update_recover_release_bootstrap_rejects_checksum_mismatch(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    (fake_bin / "curl").write_text(
        "#!/bin/bash\n"
        "out=''\n"
        "url=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    -o) out=\"$2\"; shift 2 ;;\n"
        "    http*) url=\"$1\"; shift ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "printf 'curl %s %s\\n' \"$url\" \"$out\" >>\"$AAVA_TEST_LOG\"\n"
        "case \"$url\" in\n"
        "  */SHA256SUMS) printf 'expected123  agent-linux-amd64\\n' >\"$out\" ;;\n"
        "  *) printf 'tampered\\n' >\"$out\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (fake_bin / "sha256sum").write_text(
        "#!/bin/sh\n"
        "printf 'actual456  %s\\n' \"$1\"\n",
        encoding="utf-8",
    )
    (fake_bin / "uname").write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  -s) printf 'Linux\\n' ;;\n"
        "  -m) printf 'x86_64\\n' ;;\n"
        "  *) /usr/bin/uname \"$@\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (fake_bin / "curl").chmod(0o755)
    (fake_bin / "sha256sum").chmod(0o755)
    (fake_bin / "uname").chmod(0o755)

    harness = f"""
    set -euo pipefail
    export AAVA_TEST_LOG={log}
    source "$SOURCE"
AGENT_BIN="{tmp_path}/install/agent"
install_release_cli v7.4.2
"""
    result = _run_bash_harness(harness, tmp_path, check=False)

    assert result.returncode == 2
    assert "checksum mismatch for agent-linux-amd64" in result.stderr
    assert not (tmp_path / "install" / "agent").exists()


def test_update_recover_branch_bootstrap_builds_selected_ref(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    agent_bin = tmp_path / "agent-bin" / "agent"
    (fake_bin / "git").write_text(
        "#!/bin/bash\n"
        "printf 'git %s\\n' \"$*\" >>\"$AAVA_TEST_LOG\"\n"
        "if [ \"$1\" = clone ]; then\n"
        "  dest=\"${@: -1}\"\n"
        "  mkdir -p \"$dest/cli\"\n"
        "fi\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text(
        "#!/bin/bash\n"
        "printf 'docker %s\\n' \"$*\" >>\"$AAVA_TEST_LOG\"\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in\n"
        "    *:/out) out=\"${arg%:/out}\" ;;\n"
        "    *:/out:*) out=\"${arg%%:/out:*}\" ;;\n"
        "  esac\n"
        "done\n"
        "mkdir -p \"$out\"\n"
        "printf '#!/bin/sh\\n' >\"$out/agent\"\n",
        encoding="utf-8",
    )
    (fake_bin / "chown").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "git").chmod(0o755)
    (fake_bin / "docker").chmod(0o755)
    (fake_bin / "chown").chmod(0o755)

    harness = f"""
set -euo pipefail
export AAVA_TEST_LOG={log}
source "$SOURCE"
git_repo() {{ printf 'https://example.invalid/fork.git\\n'; }}
TARGET_UID=0
TARGET_GID=0
TARGET_GROUPS=0
TARGET_HOME=/tmp
REMOTE=origin
AGENT_BIN="{agent_bin}"
install_branch_cli codex/update-recovery-script
"""
    _run_bash_harness(harness, tmp_path)

    commands = log.read_text(encoding="utf-8")
    assert "--branch codex/update-recovery-script" in commands
    assert "https://example.invalid/fork.git" in commands
    assert "golang:1.22-bookworm" in commands
    assert "AAVA_CLI_VERSION=codex/update-recovery-script" in commands
    assert ":/src:ro,Z" in commands
    assert ":/out:Z" in commands
    assert agent_bin.exists()


def test_update_recover_repair_is_bounded() -> None:
    script = _script()

    assert "refusing symlinked recovery state" in script
    assert 'mktemp -d "${recovery_base}/aava-update-recovery-${ts}.XXXXXX"' in script
    assert "refusing automatic repair for linked, symlinked, or missing .git metadata" in script
    assert 'safe_chown_tree "${expected_git_dir}"' in script
    assert 'safe_chown_tree "${REPO}/.agent"' in script
    assert "git_repo ls-files -z" in script
    assert 'safe_chown_tracked_paths "${tracked_list}"' in script
    assert "TEMP_BRANCH_CLI_DIR" in script
    assert not re.search(r"chown\s+-R[^\n]*\"\$\{REPO\}\"", script)
    assert "rm -rf -- \"${REPO}\"" not in script


def test_update_recover_preflights_runtime_dependencies() -> None:
    script = _script()

    main = script.split("main() {", 1)[1]
    for command in ("bash", "git", "python3", "stat", "mktemp", "chown", "chmod", "install", "date", "awk", "sed", "tr", "tee", "cp"):
        assert f"need_cmd {command}" in main
    assert "need_cmd find" not in main
    assert "need_cmd sort" not in main
    assert "need_cmd xargs" not in main
    assert "command -v realpath >/dev/null 2>&1 || need_cmd readlink" in main


def test_update_recover_hands_new_metadata_to_checkout_owner_with_no_repair(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    log = tmp_path / "commands.log"
    (fake_bin / "stat").write_text(
        "#!/bin/sh\n"
        "case \"$2\" in\n"
        "  %u) printf '1234\\n' ;;\n"
        "  %g) printf '2345\\n' ;;\n"
        "  *) /usr/bin/stat \"$@\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (fake_bin / "chown").write_text(
        "#!/bin/sh\n"
        "printf 'chown %s\\n' \"$*\" >>\"$AAVA_TEST_LOG\"\n",
        encoding="utf-8",
    )
    (fake_bin / "stat").chmod(0o755)
    (fake_bin / "chown").chmod(0o755)

    harness = f"""
set -euo pipefail
export AAVA_TEST_LOG={log}
source "$SOURCE"
REPO="{repo}"
TARGET_UID=1234
TARGET_GID=2345
SKIP_REPAIR=true
prepare_updater_state_dirs
"""
    _run_bash_harness(harness, tmp_path)

    commands = log.read_text(encoding="utf-8")
    assert "chown --no-dereference 1234:2345" in commands
    assert f"{repo}/.agent" in commands
    assert f"{repo}/.agent/updates" in commands
    assert f"{repo}/.agent/update-backups" in commands


def test_update_recover_rejects_symlinked_updater_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".agent").mkdir()
    (repo / ".agent" / "update-backups").symlink_to(tmp_path)

    harness = f"""
set -euo pipefail
source "$SOURCE"
REPO="{repo}"
TARGET_UID=1234
TARGET_GID=2345
prepare_updater_state_dirs
"""
    result = _run_bash_harness(harness, tmp_path, check=False)

    assert result.returncode == 2
    assert "refusing symlinked recovery state" in result.stderr


def test_update_recover_redacts_credentials_from_remote_diagnostics(tmp_path: Path) -> None:
    recovery = tmp_path / "recovery"
    recovery.mkdir()

    harness = f"""
set -euo pipefail
source "$SOURCE"
RECOVERY_DIR="{recovery}"
    git_repo() {{
  if [ "$1" = remote ] && [ "$2" = -v ]; then
    printf 'origin\\thttps://user:token@example.invalid/repo.git (fetch)\\n'
    printf 'origin\\thttps://example.invalid/repo.git?access_token=secret123&foo=bar (fetch)\\n'
    printf 'origin\\thttps://example.invalid/repo.git?foo=bar&private-token=secret456 (fetch)\\n'
    printf 'origin\\thttps://user:token@example.invalid/repo.git (push)\\n'
  fi
}}
capture_git_remotes
"""
    _run_bash_harness(harness, tmp_path)

    remotes = (recovery / "remotes.log").read_text(encoding="utf-8")
    assert "user:token" not in remotes
    assert "secret123" not in remotes
    assert "secret456" not in remotes
    assert "https://[redacted]@example.invalid/repo.git" in remotes
    assert "access_token=[redacted]" in remotes
    assert "private-token=[redacted]" in remotes


def test_update_recover_preserves_state_before_overwrite_can_run() -> None:
    script = _script()
    main = script.split("main() {", 1)[1]

    owner = main.index("prepare_owner_execution")
    git_repair = main.index("repair_git_metadata_ownership")
    tracked_repair = main.index("repair_tracked_paths_ownership")
    updater_state = main.index("prepare_updater_state_dirs")
    diagnostics = main.index("capture_diagnostics")
    prompt = main.index("prompt_local_changes_if_needed")
    preserve = main.index("capture_preupdate_artifacts")
    install = main.index("install_target_cli")
    agent_repair = main.index("repair_agent_state_ownership")
    docker_check = main.index("check_owner_docker_access")
    update = main.index("run_update")

    assert owner < git_repair < tracked_repair < diagnostics < prompt < preserve < install
    assert preserve < agent_repair < updater_state < docker_check < update
    assert "staged-tracked.patch" in script
    assert "unstaged-tracked.patch" in script
    assert "pre-update-files" in script
    assert "Tracked source-code edits will be discarded" in script
    assert "copy_unmerged_files" in script
    assert "Retain is disabled for this checkout" in script


def test_update_recover_plan_and_update_pass_owner_args_in_order(tmp_path: Path) -> None:
    recovery = tmp_path / "recovery"
    recovery.mkdir()
    log = tmp_path / "owner.log"
    harness = f"""
set -euo pipefail
source "$SOURCE"
run_as_owner() {{
  printf 'CALL\\n' >>"{log}"
  printf '<%s>\\n' "$@" >>"{log}"
}}
RECOVERY_DIR="{recovery}"
AGENT_BIN=/usr/local/bin/agent
REMOTE=origin
REF=v7.4.2
CHECKOUT_MODE=auto
INCLUDE_UI=true
LOCAL_CHANGES=retain
SKIP_CHECK=true
STASH_UNTRACKED=true
run_plan
run_update
"""
    _run_bash_harness(harness, tmp_path)

    sections = log.read_text(encoding="utf-8").split("CALL\n")
    calls = [
        [line[1:-1] for line in section.splitlines() if line.startswith("<")]
        for section in sections
        if section.strip()
    ]
    assert len(calls) == 2
    assert calls[0][:4] == ["/usr/local/bin/agent", "update", "--self-update=false", "--plan"]
    assert "--plan-json" in calls[0]
    assert "--ref=v7.4.2" in calls[0]
    assert "--checkout=false" in calls[0]
    assert "--include-ui=true" in calls[0]
    assert "--local-changes=retain" in calls[0]
    assert calls[0][-2:] == ["--skip-check", "--stash-untracked"]
    assert calls[1][:3] == ["/usr/local/bin/agent", "update", "--self-update=false"]
    assert "--remote=origin" in calls[1]
    assert "--plan-json" not in calls[1]
    assert "--stash-untracked" in calls[1]


def test_update_recover_runs_as_checkout_owner_without_adding_docker_socket_group() -> None:
    script = _script()

    assert "setpriv is required to inspect and update checkout as owner" in script
    assert '--reuid="${TARGET_UID}" --regid="${TARGET_GID}" --groups="${TARGET_GROUPS}"' in script
    assert "UPDATER_GROUPS" not in script
    assert 'docker_gid="$(stat -c' not in script
    assert "check_owner_docker_access" in script
    assert 'HOME=${update_home}' in script
    assert 'chmod a+x -- "${parent}"' in script
    assert 'chmod "${mode}" -- "${parent}"' in script


def test_update_recover_fails_fast_when_owner_cannot_access_docker_socket(tmp_path: Path) -> None:
    sock_path = Path("/tmp") / f"aava-test-docker-{os.getpid()}.sock"
    subprocess.run(
        [
            "python3",
            "-c",
            (
                "import socket, sys; "
                "sock = socket.socket(socket.AF_UNIX); "
                "sock.bind(sys.argv[1]); "
                "sock.close()"
            ),
            str(sock_path),
        ],
        check=True,
    )

    try:
        harness = f"""
set -euo pipefail
source "$SOURCE"
TARGET_UID=1234
TARGET_GID=2345
DOCKER_SOCK="{sock_path}"
run_as_checkout_owner_home() {{ return 1; }}
check_owner_docker_access
"""
        result = _run_bash_harness(harness, tmp_path, check=False)

        assert result.returncode == 2
        assert f"checkout owner UID 1234 cannot access {sock_path}" in result.stderr
    finally:
        sock_path.unlink(missing_ok=True)


def test_update_recover_uses_checkout_home_for_branch_updates() -> None:
    script = _script()

    assert "run_as_checkout_owner_home git clone" in script
    assert 'if ! is_release_ref "${REF}" && [ -n "${TARGET_HOME}" ] && [ -d "${TARGET_HOME}" ]; then' in script
    assert 'update_home="${TARGET_HOME}"' in script


def test_update_recover_can_pass_untracked_stash_when_requested() -> None:
    script = _script()

    assert 'STASH_UNTRACKED="false"' in script
    assert "--stash-untracked" in script
    assert 'if [ "${STASH_UNTRACKED}" = "true" ]; then' in script
    assert "args+=(--stash-untracked)" in script


def test_update_recover_rejects_overwrite_with_untracked_stash(tmp_path: Path) -> None:
    harness = """
set -euo pipefail
source "$SOURCE"
LOCAL_CHANGES=overwrite
STASH_UNTRACKED=true
validate_args
"""
    result = _run_bash_harness(harness, tmp_path, check=False)

    assert result.returncode == 2
    assert "--stash-untracked cannot be combined with --local-changes=overwrite" in result.stderr
