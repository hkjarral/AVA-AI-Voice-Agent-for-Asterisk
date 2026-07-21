import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update-recover.sh"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


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
