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


def test_updater_image_embeds_the_requested_cli_version() -> None:
    dockerfile = (ROOT / "updater" / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG AAVA_CLI_VERSION=dev" in dockerfile
    assert "-X main.version=${AAVA_CLI_VERSION}" in dockerfile


def test_nested_runtime_databases_are_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "data/**/*.db" in gitignore
    assert "data/**/*.db-wal" in gitignore
    assert "data/operator/.migration.lock" in gitignore
