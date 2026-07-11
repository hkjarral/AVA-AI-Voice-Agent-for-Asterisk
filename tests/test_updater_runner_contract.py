from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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


def test_updater_repairs_legacy_root_owned_state_before_privilege_drop() -> None:
    runner = (ROOT / "updater" / "run.sh").read_text(encoding="utf-8")

    repair = (
        'chown -R --no-dereference "${project_uid}:${project_gid}" '
        '"${PROJECT_ROOT}/.agent"'
    )
    reexec = 'exec gosu "${user_name}" "$0" "$@"'
    assert repair in runner
    assert runner.index(repair) < runner.index(reexec)
    assert '[ -L "${PROJECT_ROOT}/.agent" ]' in runner


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
