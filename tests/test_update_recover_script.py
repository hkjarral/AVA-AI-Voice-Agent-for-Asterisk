import json
import os
import re
import stat
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
    original = _script()
    patched = original.replace('\nmain "$@"\n', "\n")
    assert patched != original, (
        'failed to strip the `main "$@"` invocation from update-recover.sh; '
        "refusing to source it unpatched"
    )
    source.write_text(patched, encoding="utf-8")
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
    target_home = tmp_path / "home"
    target_home.mkdir()
    (target_home / ".gitconfig").write_text("[credential]\n\thelper = store\n", encoding="utf-8")
    (fake_bin / "git").write_text(
        "#!/bin/bash\n"
        "printf 'git %s\\n' \"$*\" >>\"$AAVA_TEST_LOG\"\n"
        "printf 'git_config_global %s\\n' \"${GIT_CONFIG_GLOBAL:-}\" >>\"$AAVA_TEST_LOG\"\n"
        "if [ -n \"${GIT_CONFIG_GLOBAL:-}\" ] && grep -q '^\\[include\\]' \"$GIT_CONFIG_GLOBAL\"; then\n"
        "  printf 'git_config_has_include yes\\n' >>\"$AAVA_TEST_LOG\"\n"
        "fi\n"
        "if [ -n \"${GIT_CONFIG_GLOBAL:-}\" ] && grep -q '^\\[url ' \"$GIT_CONFIG_GLOBAL\"; then\n"
        "  printf 'git_config_has_rewrite yes\\n' >>\"$AAVA_TEST_LOG\"\n"
        "fi\n"
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
git_repo() {{
  case "$*" in
    "ls-remote --get-url origin")
      printf 'https://example.invalid/fork.git\\n'
      ;;
    "config --name-only --get-regexp "*)
      printf 'http.https://example.invalid/.extraHeader\\n'
      printf 'credential.helper\\n'
      printf 'core.fsmonitor\\n'
      ;;
    "config --get-all http.https://example.invalid/.extraHeader")
      printf 'AUTHORIZATION: bearer localtoken\\n'
      ;;
    "config --get-all credential.helper")
      printf 'store --file=/home/owner/.git-credentials\\n'
      ;;
    "config --get-all core.fsmonitor")
      printf 'unsafe-helper\\n'
      ;;
  esac
}}
TARGET_UID=0
TARGET_GID=0
TARGET_GROUPS=0
TARGET_HOME="{target_home}"
REMOTE=origin
AGENT_BIN="{agent_bin}"
install_branch_cli codex/update-recovery-script
"""
    _run_bash_harness(harness, tmp_path)

    commands = log.read_text(encoding="utf-8")
    assert "--branch codex/update-recovery-script" in commands
    assert "aava-recovery-origin:" in commands
    assert "https://example.invalid/fork.git" not in commands
    assert re.search(r"git_config_global \S", commands)
    assert "git_config_has_include yes" in commands
    assert "git_config_has_rewrite yes" in commands
    assert "--add http.https://example.invalid/.extraHeader AUTHORIZATION: bearer localtoken" in commands
    assert "--add credential.helper store --file=/home/owner/.git-credentials" in commands
    assert "unsafe-helper" not in commands
    assert "golang:1.22-bookworm" in commands
    assert "AAVA_CLI_VERSION=codex/update-recovery-script" in commands
    assert ":/src:ro,Z" in commands
    assert ":/out:Z" in commands
    assert agent_bin.exists()


def test_update_recover_branch_bootstrap_does_not_hand_root_temp_to_owner() -> None:
    script = _script()
    start = script.index("install_branch_cli() {")
    end = script.index("\ninstall_target_cli() {", start)
    install_branch = script[start:end]

    assert 'chown "${TARGET_UID}:${TARGET_GID}" "${tmp_src}"' not in install_branch
    assert 'chmod 0711 "${tmp_src}"' in install_branch
    assert 'mkdir -p -- "${tmp_src}/repo" "${tmp_src}/out"' in install_branch
    assert 'chown "${TARGET_UID}:${TARGET_GID}" "${tmp_src}/repo" "${tmp_src}/out"' in install_branch
    assert 'chmod 0400 "${tmp_src}/gitconfig"' in install_branch
    assert ': >"${clone_err}"' in install_branch
    assert 'chmod 0600 "${clone_err}"' in install_branch


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


def test_update_recover_tracked_repair_preserves_runtime_parent_ownership(tmp_path: Path) -> None:
    script = _script()
    start = script.index("safe_chown_tracked_paths() {")
    heredoc_start = script.index("<<'PY'\n", start) + len("<<'PY'\n")
    heredoc_end = script.index("\nPY\n}", heredoc_start)
    python_source = script[heredoc_start:heredoc_end]
    instrumented_source = python_source.replace(
        "import os\n",
        "import os\n"
        "import json\n"
        "_chown_calls = []\n"
        "def _record_chown(name, uid, gid, *, dir_fd=None, follow_symlinks=True):\n"
        "    _chown_calls.append(os.fsdecode(name))\n"
        "os.chown = _record_chown\n",
        1,
    )
    instrumented_source += "\nprint(json.dumps(_chown_calls))\n"

    repo = tmp_path / "repo"
    for path in (
        repo / "src",
        repo / "data",
        repo / "models",
        repo / "secrets",
    ):
        path.mkdir(parents=True)
    for path in (
        repo / "src" / "engine.py",
        repo / "data" / ".gitkeep",
        repo / "models" / "registry.json",
        repo / "secrets" / ".gitkeep",
    ):
        path.write_text("", encoding="utf-8")

    tracked_list = tmp_path / "tracked.z"
    tracked_list.write_bytes(
        b"src/engine.py\0data/.gitkeep\0models/registry.json\0secrets/.gitkeep\0"
    )

    result = subprocess.run(
        [
            "python3",
            "-c",
            instrumented_source,
            str(repo),
            "1234",
            "2345",
            "2345",
            str(tracked_list),
            "",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    chown_calls = json.loads(result.stdout)
    assert "src" in chown_calls
    assert "engine.py" in chown_calls
    assert ".gitkeep" in chown_calls
    assert "registry.json" in chown_calls
    assert "data" not in chown_calls
    assert "models" not in chown_calls
    assert "secrets" not in chown_calls


def test_update_recover_grants_and_records_protected_root_access(tmp_path: Path) -> None:
    script = _script()
    start = script.index("safe_chown_tracked_paths() {")
    heredoc_start = script.index("<<'PY'\n", start) + len("<<'PY'\n")
    heredoc_end = script.index("\nPY\n}", heredoc_start)
    python_source = script[heredoc_start:heredoc_end]
    instrumented_source = python_source.replace(
        "import os\n",
        "import os\n"
        "import json\n"
        "_chmod_calls = []\n"
        "_real_chmod = os.chmod\n"
        "def _ignore_chown(name, uid, gid, *, dir_fd=None, follow_symlinks=True):\n"
        "    return None\n"
        "def _record_chmod(name, mode, *, dir_fd=None, follow_symlinks=True):\n"
        "    _chmod_calls.append([os.fsdecode(name), mode])\n"
        "    _real_chmod(name, mode, dir_fd=dir_fd, follow_symlinks=follow_symlinks)\n"
        "os.chown = _ignore_chown\n"
        "os.chmod = _record_chmod\n",
        1,
    )
    instrumented_source += "\nprint(json.dumps(_chmod_calls))\n"

    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True, mode=0o700)
    (repo / "data" / "call_history.db").write_text("", encoding="utf-8")
    (repo / "data").chmod(0o700)
    tracked_list = tmp_path / "tracked.z"
    tracked_list.write_bytes(b"data/call_history.db\0")
    traversal_state = tmp_path / "restore.tsv"

    result = subprocess.run(
        [
            "python3",
            "-c",
            instrumented_source,
            str(repo),
            "1234",
            "2345",
            "2345",
            str(tracked_list),
            str(traversal_state),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    chmod_calls = json.loads(result.stdout)
    assert ["data", 0o705] in chmod_calls
    assert traversal_state.read_text(encoding="utf-8") == f"700\t{repo / 'data'}\n"


def test_update_recover_respects_permission_class_precedence(tmp_path: Path) -> None:
    script = _script()
    start = script.index("safe_chown_tracked_paths() {")
    heredoc_start = script.index("<<'PY'\n", start) + len("<<'PY'\n")
    heredoc_end = script.index("\nPY\n}", heredoc_start)
    python_source = script[heredoc_start:heredoc_end]
    instrumented_source = python_source.replace(
        "import os\n",
        "import os\n"
        "import json\n"
        "_chmod_calls = []\n"
        "_real_chmod = os.chmod\n"
        "def _ignore_chown(name, uid, gid, *, dir_fd=None, follow_symlinks=True):\n"
        "    return None\n"
        "def _record_chmod(name, mode, *, dir_fd=None, follow_symlinks=True):\n"
        "    _chmod_calls.append([os.fsdecode(name), mode])\n"
        "    _real_chmod(name, mode, dir_fd=dir_fd, follow_symlinks=follow_symlinks)\n"
        "os.chown = _ignore_chown\n"
        "os.chmod = _record_chmod\n",
        1,
    )
    instrumented_source += "\nprint(json.dumps(_chmod_calls))\n"

    repo = tmp_path / "repo"
    (repo / "models").mkdir(parents=True)
    (repo / "models" / "registry.json").write_text("", encoding="utf-8")
    (repo / "models").chmod(0o707)
    models_stat = (repo / "models").stat()
    synthetic_uid = 1 if models_stat.st_uid != 1 else 2
    synthetic_gid = models_stat.st_gid
    tracked_list = tmp_path / "tracked.z"
    tracked_list.write_bytes(b"models/registry.json\0")
    traversal_state = tmp_path / "restore.tsv"

    result = subprocess.run(
        [
            "python3",
            "-c",
            instrumented_source,
            str(repo),
            str(synthetic_uid),
            str(synthetic_gid),
            str(synthetic_gid),
            str(tracked_list),
            str(traversal_state),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    chmod_calls = json.loads(result.stdout)
    assert ["models", 0o757] in chmod_calls
    assert traversal_state.read_text(encoding="utf-8") == f"707\t{repo / 'models'}\n"


def test_update_recover_cleanup_preserves_signal_and_restore_failures() -> None:
    script = _script()

    assert "signal_exit()" in script
    assert "trap cleanup EXIT" in script
    assert "trap 'signal_exit 130' INT" in script
    assert "trap 'signal_exit 143' TERM" in script
    assert "trap 'signal_exit 129' HUP" in script
    assert "restore_traversal_modes()" in script
    assert 'getattr(os, "O_NOFOLLOW", 0)' in script
    assert "os.fchmod(fd, mode)" in script
    assert "failed to restore traversal permissions" in script
    assert 'state file retained at ${TRAVERSAL_STATE}' in script
    assert 'elif [ "${status}" -eq 0 ]; then' in script
    assert "status=2" in script


def test_update_recover_restore_traversal_modes_does_not_follow_symlinks(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_dir.chmod(0o755)
    link = tmp_path / "link"
    link.symlink_to(real_dir, target_is_directory=True)
    state = tmp_path / "restore.tsv"
    state.write_text(f"700\t{link}\n", encoding="utf-8")

    harness = f"""
set -euo pipefail
source "$SOURCE"
TRAVERSAL_STATE="{state}"
restore_traversal_modes
"""
    result = _run_bash_harness(harness, tmp_path, check=False)

    assert result.returncode == 1
    assert "failed to restore traversal permissions 700" in result.stderr
    assert oct(real_dir.stat().st_mode & 0o777) == "0o755"


def test_update_recover_preflights_runtime_dependencies() -> None:
    script = _script()

    main = script.split("main() {", 1)[1]
    for command in ("bash", "git", "stat", "mktemp", "chown", "chmod", "install", "date", "awk", "sed", "tr", "tee", "cp"):
        assert f"need_cmd {command}" in main
    assert "need_python3" in main
    assert "Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y python3" in script
    assert "need_cmd find" not in main
    assert "need_cmd sort" not in main
    assert "need_cmd xargs" not in main
    assert "command -v realpath >/dev/null 2>&1 || need_cmd readlink" in main


def test_update_recover_hands_new_metadata_to_checkout_owner_with_no_repair(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    uid = os.getuid()
    gid = os.getgid()

    harness = f"""
set -euo pipefail
source "$SOURCE"
REPO="{repo}"
TARGET_UID={uid}
TARGET_GID={gid}
SKIP_REPAIR=true
prepare_updater_state_dirs
"""
    _run_bash_harness(harness, tmp_path)

    for path in (repo / ".agent", repo / ".agent" / "updates", repo / ".agent" / "update-backups"):
        st = path.stat()
        assert stat.S_IMODE(st.st_mode) == 0o750
        assert st.st_uid == uid
        assert st.st_gid == gid


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
    printf 'origin\\thttps://example.invalid/repo.git?ACCESS_TOKEN=secret789&foo=bar (fetch)\\n'
    printf 'origin\\tssh://user:token@example.invalid/repo.git (fetch)\\n'
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
    assert "secret789" not in remotes
    assert "https://[redacted]@example.invalid/repo.git" in remotes
    assert "ssh://[redacted]@example.invalid/repo.git" in remotes
    assert "access_token=[redacted]" in remotes
    assert "private-token=[redacted]" in remotes
    assert "ACCESS_TOKEN=[redacted]" in remotes


def test_update_recover_branch_clone_failure_redacts_remote_query(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "git").write_text(
        "#!/bin/bash\n"
        "if [ \"$1\" = clone ]; then\n"
        "  cat \"$GIT_CONFIG_GLOBAL\" >&2\n"
        "  exit 1\n"
        "fi\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "chown").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "git").chmod(0o755)
    (fake_bin / "docker").chmod(0o755)
    (fake_bin / "chown").chmod(0o755)
    agent_bin = tmp_path / "agent-bin" / "agent"

    harness = f"""
set -euo pipefail
source "$SOURCE"
git_repo() {{ printf 'https://example.invalid/repo.git?access_token=secret123&foo=bar\\n'; }}
TARGET_UID=0
TARGET_GID=0
TARGET_GROUPS=0
TARGET_HOME=/tmp
REMOTE=origin
AGENT_BIN="{agent_bin}"
install_branch_cli feature/ref
"""
    result = _run_bash_harness(harness, tmp_path, check=False)

    assert result.returncode == 2
    assert "secret123" not in result.stderr
    assert "access_token=[redacted]" in result.stderr
    assert not agent_bin.exists()


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
    plan = main.index("run_plan")
    update = main.index("run_update")

    assert owner < git_repair < tracked_repair < diagnostics < prompt < preserve < install
    assert install < agent_repair < updater_state < plan < docker_check < update
    assert "staged-tracked.patch" in script
    assert "unstaged-tracked.patch" in script
    assert "pre-update-files" in script
    assert "Tracked source-code edits will be discarded" in script
    assert "copy_unmerged_files" in script
    assert "Retain is disabled for this checkout" in script
    assert "git_repo_preserve_diff" in script
    assert "-u GIT_EXTERNAL_DIFF -u GIT_DIFF_OPTS git" in script
    assert "--no-ext-diff --no-textconv" in script
    assert "Overwrite is unavailable with --stash-untracked" in script


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


def test_update_recover_redacts_failed_plan_output_and_artifact(tmp_path: Path) -> None:
    recovery = tmp_path / "recovery"
    recovery.mkdir()
    harness = f"""
set -euo pipefail
source "$SOURCE"
run_as_owner() {{
  printf 'fatal: unable to access https://example.invalid/repo.git?access_token=secret123&x=1\\n' >&2
  return 9
}}
RECOVERY_DIR="{recovery}"
AGENT_BIN=/usr/local/bin/agent
REMOTE=origin
REF=v7.4.2
CHECKOUT_MODE=auto
INCLUDE_UI=true
LOCAL_CHANGES=retain
SKIP_CHECK=false
STASH_UNTRACKED=false
run_plan
"""
    result = _run_bash_harness(harness, tmp_path, check=False)

    assert result.returncode == 2
    assert "secret123" not in result.stderr
    assert "access_token=[redacted]" in result.stderr
    assert not (recovery / "update-plan.err.raw").exists()
    plan_err = (recovery / "update-plan.err").read_text(encoding="utf-8")
    assert "secret123" not in plan_err
    assert "access_token=[redacted]" in plan_err


def test_update_recover_redacts_update_output_and_log(tmp_path: Path) -> None:
    recovery = tmp_path / "recovery"
    recovery.mkdir()
    harness = f"""
set -euo pipefail
source "$SOURCE"
run_as_owner() {{
  printf 'fatal: unable to access https://example.invalid/repo.git?private-token=secret456&x=1\\n'
  return 7
}}
RECOVERY_DIR="{recovery}"
TARGET_UID="$(id -u)"
TARGET_GID="$(id -g)"
AGENT_BIN=/usr/local/bin/agent
REMOTE=origin
REF=v7.4.2
CHECKOUT_MODE=auto
INCLUDE_UI=true
LOCAL_CHANGES=retain
SKIP_CHECK=false
STASH_UNTRACKED=false
run_update
"""
    result = _run_bash_harness(harness, tmp_path, check=False)

    assert result.returncode == 2
    assert "secret456" not in result.stdout
    assert "private-token=[redacted]" in result.stdout
    update_log = (recovery / "agent-update.log").read_text(encoding="utf-8")
    assert "secret456" not in update_log
    assert "private-token=[redacted]" in update_log


def test_update_recover_validates_installed_cli_as_checkout_owner() -> None:
    script = _script()
    install_target_cli = script.split("install_target_cli() {", 1)[1].split("\n}", 1)[0]

    assert 'run_as_checkout_owner_home "${AGENT_BIN}" version' in install_target_cli
    assert not re.search(r'^\s*"\$\{AGENT_BIN\}" version', install_target_cli, re.MULTILINE)


def test_update_recover_runs_as_checkout_owner_without_adding_docker_socket_group() -> None:
    script = _script()

    assert "setpriv is required to inspect and update checkout as owner" in script
    assert '--reuid="${TARGET_UID}" --regid="${TARGET_GID}" --groups="${TARGET_GROUPS}"' in script
    assert "UPDATER_GROUPS" not in script
    assert 'docker_gid="$(stat -c' not in script
    assert "check_owner_docker_access" in script
    assert 'HOME=${update_home}' in script
    assert 'make_dir_traversable_for_owner "${parent}"' in script
    assert 'chmod a+x -- "${parent}"' not in script
    assert 'os.fchmod(fd, mode)' in script
    assert 'os.fchmod(fd, new_mode)' in script


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


def test_update_recover_uses_checkout_home_for_updates() -> None:
    script = _script()
    run_as_owner = script.split("run_as_owner() {", 1)[1].split("\n}", 1)[0]

    assert 'run_as_checkout_owner_home /usr/bin/env "GIT_CONFIG_GLOBAL=${tmp_src}/gitconfig"' in script
    assert "aava-recovery-origin:" in script
    assert '[include]\\n\\tpath = "%s"\\n' in script
    assert "owner_execution_home()" in script
    assert 'update_home="$(owner_execution_home)"' in run_as_owner
    assert 'TARGET_HOME="/tmp"' not in script
    assert 'if ! is_release_ref "${REF}"' not in run_as_owner


def test_update_recover_uses_private_home_when_owner_home_is_missing() -> None:
    script = _script()
    ensure_owner = script.split("ensure_owner_context() {", 1)[1].split("\n}", 1)[0]
    owner_home = script.split("owner_execution_home() {", 1)[1].split("\n}", 1)[0]
    install_branch = script.split("install_branch_cli() {", 1)[1].split("\n}", 1)[0]

    assert 'TARGET_HOME=""' in ensure_owner
    assert 'printf \'%s\\n\' "${TEMP_HOME}"' in owner_home
    assert 'die "temporary owner HOME is not prepared"' in owner_home
    assert 'if [ -n "${TARGET_HOME}" ] && [ -d "${TARGET_HOME}" ]; then' in install_branch


def test_update_recover_keeps_recovery_artifacts_owner_only() -> None:
    script = _script()

    assert "secure_recovery_artifacts()" in script
    assert "os.fchmod(dirfd, 0o700)" in script
    assert "os.fchmod(fd, 0o600)" in script
    assert "chmod 0750 -- \"${RECOVERY_DIR}\"" not in script
    assert "chown -R --no-dereference \"${TARGET_UID}:${TARGET_GID}\" \"${RECOVERY_DIR}\"" not in script


def test_update_recover_can_pass_untracked_stash_when_requested() -> None:
    script = _script()

    assert 'STASH_UNTRACKED="false"' in script
    assert "--stash-untracked" in script
    assert 'if [ "${STASH_UNTRACKED}" = "true" ]; then' in script
    assert "args+=(--stash-untracked)" in script


def test_update_recover_rejects_untracked_stash_without_retain(tmp_path: Path) -> None:
    harness = """
set -euo pipefail
source "$SOURCE"
LOCAL_CHANGES=ask
STASH_UNTRACKED=true
validate_args
"""
    result = _run_bash_harness(harness, tmp_path, check=False)

    assert result.returncode == 2
    assert "--stash-untracked requires --local-changes=retain" in result.stderr
