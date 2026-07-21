import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update-recover.sh"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _run_bash_harness(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    source = tmp_path / "update-recover-source.sh"
    source.write_text(_script().replace('\nmain "$@"\n', "\n"), encoding="utf-8")
    return subprocess.run(
        ["/bin/bash", "-c", script],
        check=True,
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
    assert "--stash-untracked" in result.stdout
    assert "never recursively chowns the" in result.stdout


def test_update_recover_supports_release_and_branch_cli_bootstrap() -> None:
    script = _script()

    assert "AGENT_VERSION=${version}" in script
    assert "install-cli.sh" in script
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
        "#!/bin/sh\n"
        "printf 'curl %s\\n' \"$*\" >>\"$AAVA_TEST_LOG\"\n"
        "printf 'exit 0\\n'\n",
        encoding="utf-8",
    )
    (fake_bin / "bash").write_text(
        "#!/bin/sh\n"
        "printf 'bash AGENT_VERSION=%s INSTALL_DIR=%s\\n' \"$AGENT_VERSION\" \"$INSTALL_DIR\" >>\"$AAVA_TEST_LOG\"\n"
        "cat >/dev/null\n",
        encoding="utf-8",
    )
    (fake_bin / "curl").chmod(0o755)
    (fake_bin / "bash").chmod(0o755)

    harness = f"""
set -euo pipefail
export AAVA_TEST_LOG={log}
source "$SOURCE"
AGENT_BIN="{tmp_path}/install/agent"
install_release_cli v7.4.2
"""
    _run_bash_harness(harness, tmp_path)

    commands = log.read_text(encoding="utf-8")
    assert "/v7.4.2/scripts/install-cli.sh" in commands
    assert "/main/scripts/install-cli.sh" not in commands
    assert f"bash AGENT_VERSION=v7.4.2 INSTALL_DIR={tmp_path}/install" in commands


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
        "  esac\n"
        "done\n"
        "mkdir -p \"$out\"\n"
        "printf '#!/bin/sh\\n' >\"$out/agent\"\n",
        encoding="utf-8",
    )
    (fake_bin / "git").chmod(0o755)
    (fake_bin / "docker").chmod(0o755)

    harness = f"""
set -euo pipefail
export AAVA_TEST_LOG={log}
source "$SOURCE"
git_repo() {{ printf 'https://example.invalid/fork.git\\n'; }}
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
    assert agent_bin.exists()


def test_update_recover_repair_is_bounded() -> None:
    script = _script()

    assert "refusing symlinked updater state" in script
    assert "refusing automatic repair for linked, symlinked, or missing .git metadata" in script
    assert 'chown -R --no-dereference "${TARGET_UID}:${TARGET_GID}" "${expected_git_dir}"' in script
    assert 'chown -R --no-dereference "${TARGET_UID}:${TARGET_GID}" "${REPO}/.agent"' in script
    assert "git_repo ls-files -z" in script
    assert "Refusing symlinked tracked parent" in script
    assert not re.search(r"chown\s+-R[^\n]*\"\$\{REPO\}\"", script)
    assert "rm -rf -- \"${REPO}\"" not in script


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
SKIP_REPAIR=true
prepare_recovery_dir
printf 'recovery=%s\\n' "$RECOVERY_DIR"
"""
    result = _run_bash_harness(harness, tmp_path)

    commands = log.read_text(encoding="utf-8")
    assert "chown --no-dereference 1234:2345" in commands
    assert f"{repo}/.agent" in commands
    assert f"{repo}/.agent/update-recovery" in commands
    assert "recovery=" in result.stdout


def test_update_recover_preserves_state_before_overwrite_can_run() -> None:
    script = _script()
    main = script.split("main() {", 1)[1]

    prompt = main.index("prompt_local_changes_if_needed")
    preserve = main.index("capture_preupdate_artifacts")
    install = main.index("install_target_cli")
    repair = main.index("repair_metadata_ownership")
    update = main.index("run_update")

    assert prompt < preserve < install < repair < update
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
  printf '%s\\n' "$*" >>"{log}"
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

    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2
    assert calls[0].startswith("/usr/local/bin/agent update --self-update=false --plan --plan-json")
    assert "--ref=v7.4.2" in calls[0]
    assert "--checkout=false" in calls[0]
    assert "--include-ui=true" in calls[0]
    assert "--local-changes=retain" in calls[0]
    assert "--skip-check --stash-untracked" in calls[0]
    assert calls[1].startswith("/usr/local/bin/agent update --self-update=false --remote=origin")
    assert "--plan-json" not in calls[1]
    assert "--stash-untracked" in calls[1]


def test_update_recover_runs_as_checkout_owner_with_docker_socket_group() -> None:
    script = _script()

    assert "setpriv is required to run the update as checkout owner" in script
    assert '--reuid="${TARGET_UID}" --regid="${TARGET_GID}" --groups="${TARGET_GROUPS}"' in script
    assert 'docker_gid="$(stat -c' in script
    assert 'TARGET_GROUPS="${TARGET_GROUPS},${docker_gid}"' in script
    assert 'HOME=${TEMP_HOME}' in script
    assert 'chmod a+x -- "${parent}"' in script
    assert 'chmod "${mode}" -- "${parent}"' in script


def test_update_recover_can_pass_untracked_stash_when_requested() -> None:
    script = _script()

    assert 'STASH_UNTRACKED="false"' in script
    assert "--stash-untracked" in script
    assert 'if [ "${STASH_UNTRACKED}" = "true" ]; then' in script
    assert "args+=(--stash-untracked)" in script
