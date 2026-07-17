import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _drop_to_project_owner_body(runner: str) -> str:
    start_marker = "drop_to_project_owner() {\n"
    end_marker = '\n}\n\ndrop_to_project_owner "$@"'
    start = runner.index(start_marker) + len(start_marker)
    end = runner.index(end_marker, start)
    return runner[start:end]


def test_active_call_probe_keeps_stdin_open_for_embedded_python() -> None:
    runner = (ROOT / "updater" / "run.sh").read_text(encoding="utf-8")

    assert "docker exec -i ai_engine python3 - <<'PY'" in runner


def test_updater_drops_to_the_project_owner_before_writing() -> None:
    runner = (ROOT / "updater" / "run.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "updater" / "Dockerfile").read_text(encoding="utf-8")

    assert 'project_uid="$(stat -c \'%u\' "${PROJECT_ROOT}")"' in runner
    assert 'exec gosu "${user_name}" "$0" "$@"' in runner
    assert 'getent group "${project_gid}" 2>/dev/null' in runner
    assert "|| true" in runner
    assert "gosu" in dockerfile


def test_updater_refuses_privileged_legacy_state_repair() -> None:
    runner = (ROOT / "updater" / "run.sh").read_text(encoding="utf-8")
    drop_body = _drop_to_project_owner_body(runner)

    reexec = 'exec gosu "${user_name}" "$0" "$@"'
    ownership_scan = (
        'find "${PROJECT_ROOT}/.agent" ! -uid "${project_uid}" -print -quit'
    )
    privileged_agent_repair = re.compile(
        r"^\s*(?:chown|chmod|install|mkdir)\b[^\n]*\.agent",
        re.MULTILINE,
    )
    assert not privileged_agent_repair.search(drop_body)
    assert ownership_scan in drop_body
    assert "use host CLI recovery" in drop_body
    assert drop_body.index(ownership_scan) < drop_body.index(reexec)
    assert '[ -L "${PROJECT_ROOT}/.agent" ]' in drop_body


def test_updater_fails_closed_when_any_git_metadata_owner_differs() -> None:
    runner = (ROOT / "updater" / "run.sh").read_text(encoding="utf-8")
    drop_body = _drop_to_project_owner_body(runner)

    symlink_guard = '[ -L "${PROJECT_ROOT}/.agent" ]'
    root_owner_return = 'if [ "${project_uid}" = "0" ]; then'
    ownership_scan = (
        'find "${git_metadata_root}" ! -uid "${project_uid}" -print -quit'
    )
    mixed_owner_check = '[ -n "${git_metadata_path}" ]'
    mixed_owner_start = drop_body.index(f"if {mixed_owner_check}; then")
    mixed_owner_end = drop_body.index("\n      fi", mixed_owner_start)
    mixed_owner_branch = drop_body[mixed_owner_start:mixed_owner_end]
    assert ownership_scan in drop_body
    assert mixed_owner_check in drop_body
    assert "return 2" in mixed_owner_branch
    assert "return 0" not in mixed_owner_branch
    assert "files such as FETCH_HEAD" in drop_body
    assert "running project-controlled updater state operations as root" in drop_body
    assert "updater will remain root" not in drop_body
    assert drop_body.index(symlink_guard) < drop_body.index(root_owner_return)
    assert drop_body.index(symlink_guard) < mixed_owner_start
    assert mixed_owner_end < drop_body.index('exec gosu "${user_name}" "$0" "$@"')


def test_updater_resolves_worktree_gitdirs_before_scanning_ownership() -> None:
    runner = (ROOT / "updater" / "run.sh").read_text(encoding="utf-8")
    drop_body = _drop_to_project_owner_body(runner)

    worktree_gitdir = "rev-parse --absolute-git-dir"
    common_gitdir = "rev-parse --path-format=absolute --git-common-dir"
    ownership_scan = (
        'find "${git_metadata_root}" ! -uid "${project_uid}" -print -quit'
    )
    loop_start = drop_body.index('for git_metadata_root in "${git_metadata_roots[@]}"; do')
    loop_end = drop_body.index("\n    done", loop_start)
    scan_loop = drop_body[loop_start:loop_end]
    assert worktree_gitdir in drop_body
    assert common_gitdir in drop_body
    assert 'git_metadata_roots=("${git_dir}")' in drop_body
    assert 'git_metadata_roots+=("${git_common_dir}")' in drop_body
    assert ownership_scan in scan_loop
    assert drop_body.index(worktree_gitdir) < loop_start
    assert drop_body.index(common_gitdir) < loop_start


def test_updater_makes_container_mount_parents_traversable_before_drop() -> None:
    runner = (ROOT / "updater" / "run.sh").read_text(encoding="utf-8")
    drop_body = _drop_to_project_owner_body(runner)

    traversal = 'chmod a+x "${parent_dir}"'
    reexec = 'exec gosu "${user_name}" "$0" "$@"'
    loop_start = drop_body.index('while [ "${parent_dir}" != "/" ]; do')
    loop_end = drop_body.index("\n  done", loop_start)
    traversal_loop = drop_body[loop_start:loop_end]
    assert 'parent_dir="$(dirname "${PROJECT_ROOT}")"' in drop_body
    assert traversal in traversal_loop
    assert 'parent_dir="$(dirname "${parent_dir}")"' in traversal_loop
    assert loop_end < drop_body.index(reexec)


def test_updater_image_embeds_the_requested_cli_version() -> None:
    dockerfile = (ROOT / "updater" / "Dockerfile").read_text(encoding="utf-8")
    release_workflow = (ROOT / ".github" / "workflows" / "release-images.yml").read_text(
        encoding="utf-8"
    )

    assert "ARG AAVA_CLI_VERSION=dev" in dockerfile
    assert "-X main.version=${AAVA_CLI_VERSION}" in dockerfile
    assert "AAVA_CLI_VERSION=${{ steps.meta.outputs.version }}" in release_workflow


def test_nested_runtime_databases_are_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "data/**/*.db" in gitignore
    assert "data/**/*.db-wal" in gitignore
    assert "data/operator/.migration.lock" in gitignore


def test_rollback_does_not_stash_untracked_runtime_state() -> None:
    runner = (ROOT / "updater" / "run.sh").read_text(encoding="utf-8")

    assert "status --porcelain --untracked-files=no" in runner
    assert 'stash push -m "aava rollback ${JOB_ID}"' in runner
    assert 'stash push -u -m "aava rollback ${JOB_ID}"' not in runner


def test_rollback_stashes_untracked_files_only_when_they_block_checkout() -> None:
    runner = (ROOT / "updater" / "run.sh").read_text(encoding="utf-8")

    conflict_check = 'grep -qi "untracked working tree files would be overwritten"'
    fallback_stash = (
        'stash push -u \\\n'
        '          -m "aava rollback ${JOB_ID} untracked checkout conflicts"'
    )
    assert conflict_check in runner
    assert fallback_stash in runner
    assert runner.index(conflict_check) < runner.index(fallback_stash)


def test_source_built_cli_is_written_as_the_project_owner() -> None:
    runner = (ROOT / "updater" / "run.sh").read_text(encoding="utf-8")

    assert '--user "$(id -u):$(id -g)"' in runner
    assert "-e GOCACHE=/tmp/go-build" in runner
    assert "-e GOMODCACHE=/tmp/go-mod" in runner
