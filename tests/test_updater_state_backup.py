"""Exercise private backup contents without a deployment or external services."""
import os
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "cli/cmd/agent/integration_backup.py"


def run_backup(root, destination=None):
    """Invoke the shipped helper as a process with isolated configuration."""
    environment = dict(os.environ)
    for name in ("CALENDLY_DB_PATH", "CALENDLY_TOKEN_PATH"):
        environment.pop(name, None)
    return subprocess.run(
        [sys.executable, str(HELPER), str(root), str(destination or root / "backup")],
        env=environment, capture_output=True, text=True, timeout=40,
    )


def test_recovery_and_cli_embed_identical_backup_logic():
    script = (ROOT / "scripts/update-recover.sh").read_text()
    embedded = script.split("<<'INTEGRATION_BACKUP_PY'\n", 1)[1].split("\nINTEGRATION_BACKUP_PY", 1)[0]
    assert embedded == HELPER.read_text().rstrip()


def test_absent_state_is_optional(tmp_path):
    assert run_backup(tmp_path).returncode == 0
    assert list((tmp_path / "backup").iterdir()) == []


def test_standalone_calendly_footprint_is_preserved_during_journal_outage(tmp_path):
    marker = tmp_path / 'data/operator/calendly.db.configured'
    marker.parent.mkdir(parents=True)
    marker.write_text('calendly-configured-v1\n')
    marker.chmod(0o600)
    assert run_backup(tmp_path).returncode == 0
    copied = tmp_path / 'backup' / marker.relative_to(tmp_path)
    assert copied.read_bytes() == marker.read_bytes()
    assert copied.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / 'backup/data/operator/calendly.db').exists()


def test_live_wal_snapshot_and_private_credentials(tmp_path):
    secrets = tmp_path / "secrets/providers/example"
    secrets.mkdir(parents=True)
    (secrets / "api-key").write_bytes(b"synthetic-key\x00")
    token = tmp_path / "secrets/calendly-default-token.json"
    token.write_text('{"token":"synthetic"}')
    journal = tmp_path / "data/operator/calendly.db"
    journal.parent.mkdir(parents=True)
    marker = journal.with_name(journal.name + ".configured")
    marker.write_text("calendly-configured-v1\n")
    marker.chmod(0o600)
    source = sqlite3.connect(journal)
    try:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("CREATE TABLE attempts (state TEXT)")
        source.execute("INSERT INTO attempts VALUES ('uncertain')")
        source.commit()
        assert Path(str(journal) + "-wal").stat().st_size > 0
        assert run_backup(tmp_path).returncode == 0
        backup = tmp_path / "backup"
        with sqlite3.connect(backup / "data/operator/calendly.db") as copied:
            assert copied.execute("SELECT state FROM attempts").fetchall() == [("uncertain",)]
            assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (backup / token.relative_to(tmp_path)).read_bytes() == token.read_bytes()
        assert (backup / marker.relative_to(tmp_path)).read_bytes() == marker.read_bytes()
        assert (backup / "secrets/providers/example/api-key").read_bytes() == b"synthetic-key\x00"
        assert backup.stat().st_mode & 0o777 == 0o700
        for path in backup.rglob("*"):
            if path.is_file():
                assert path.stat().st_mode & 0o777 == 0o600
        assert not list(backup.rglob("*-wal"))
    finally:
        source.close()


@pytest.mark.parametrize("value", ["/elsewhere/journal.db", "../journal.db", "$CUSTOM/journal.db"])
def test_unmapped_paths_fail_closed(tmp_path, value):
    (tmp_path / ".env").write_text(f'CALENDLY_DB_PATH="{value}"\n')
    assert run_backup(tmp_path).returncode != 0


def test_supported_container_paths_and_encoded_filename(tmp_path):
    journal = tmp_path / "data/custom #journal.db"
    journal.parent.mkdir()
    with sqlite3.connect(journal) as source:
        source.execute("CREATE TABLE attempts (id INTEGER)")
    (tmp_path / ".env").write_text('CALENDLY_DB_PATH="/app/data/custom #journal.db"\n')
    assert run_backup(tmp_path).returncode == 0
    assert (tmp_path / "backup/data/custom #journal.db").is_file()


def test_bad_journal_aborts_without_a_valid_looking_snapshot(tmp_path):
    journal = tmp_path / "data/operator/calendly.db"
    journal.parent.mkdir(parents=True)
    journal.write_text("synthetic-invalid-journal")
    result = run_backup(tmp_path)
    assert result.returncode != 0
    assert "synthetic-invalid-journal" not in result.stderr
    assert not (tmp_path / "backup/data/operator/calendly.db").exists()


@pytest.mark.parametrize("broken", [False, True])
def test_symlinked_secret_is_not_followed(tmp_path, broken):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    if not broken:
        outside.write_text("synthetic-external-secret")
    (secrets / "key").symlink_to(outside)
    result = run_backup(tmp_path)
    assert result.returncode != 0
    assert "synthetic-external-secret" not in result.stdout + result.stderr


def test_non_regular_secret_fails_without_blocking(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    os.mkfifo(secrets / "pipe")
    assert run_backup(tmp_path).returncode != 0


def test_symlinked_destination_is_rejected(tmp_path):
    (tmp_path / "backup").symlink_to(tmp_path)
    assert run_backup(tmp_path).returncode != 0


def test_existing_backup_is_never_overwritten(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "key").write_text("first")
    assert run_backup(tmp_path).returncode == 0
    (secrets / "key").write_text("second")
    assert run_backup(tmp_path).returncode != 0
    assert (tmp_path / "backup/secrets/key").read_text() == "first"


def test_real_recovery_layout_retains_both_preservation_passes(tmp_path):
    root = tmp_path / "checkout"
    (root / "secrets").mkdir(parents=True)
    (root / "secrets/key").write_text("synthetic")
    recovery = tmp_path / "recovery/pre-update-files"
    recovery.mkdir(parents=True, mode=0o700)
    script = tmp_path / "recovery.sh"
    script.write_text((ROOT / "scripts/update-recover.sh").read_text().replace('\nmain "$@"\n', "\n"))
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; REPO="$2"; backup_integration_state "$3"; backup_integration_state "$3"',
         "_", str(script), str(root), str(recovery)],
        capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stderr
    snapshots = list(recovery.glob("integration-state.*"))
    assert len(snapshots) == 2
    assert all((entry / "secrets/key").read_text() == "synthetic" for entry in snapshots)


def test_external_backup_destination_must_be_private(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    destination = tmp_path / "public-output"
    destination.mkdir(mode=0o755)
    assert run_backup(root, destination).returncode != 0
    destination.chmod(0o700)
    assert run_backup(root, destination).returncode == 0


def test_updater_image_supplies_the_required_backup_runtime():
    dockerfile = (ROOT / "updater/Dockerfile").read_text()
    assert "    python3 " in dockerfile
    assert "hasattr(sqlite3.Connection, 'backup')" in dockerfile


def test_non_directory_secret_root_fails_closed(tmp_path):
    (tmp_path / "secrets").write_text("synthetic")
    assert run_backup(tmp_path).returncode != 0


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read owner-unreadable directories")
def test_unreadable_secret_subdirectory_fails_closed(tmp_path):
    directory = tmp_path / "secrets/providers"
    directory.mkdir(parents=True)
    (directory / "key").write_text("synthetic")
    directory.chmod(0)
    try:
        assert run_backup(tmp_path).returncode != 0
    finally:
        directory.chmod(0o700)


@pytest.mark.parametrize("expired_step", ["PRAGMA journal_mode=DELETE", "PRAGMA integrity_check"])
def test_verification_steps_respect_snapshot_deadline(tmp_path, monkeypatch, expired_step):
    spec = importlib.util.spec_from_file_location("updater_state_backup", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    for name in ("CALENDLY_DB_PATH", "CALENDLY_TOKEN_PATH"):
        monkeypatch.delenv(name, raising=False)
    journal = tmp_path / "data/operator/calendly.db"
    journal.parent.mkdir(parents=True)
    with sqlite3.connect(journal) as source:
        source.execute("CREATE TABLE attempts (state TEXT)")
        source.executemany("INSERT INTO attempts VALUES (?)", [("synthetic",)] * 10000)
    clock = [0.0]

    class DeadlineConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            if statement == expired_step:
                clock[0] = 31.0
            return super().execute(statement, *args, **kwargs)

    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=DeadlineConnection)

    monkeypatch.setattr(helper, "sqlite3", SimpleNamespace(connect=connect))
    monkeypatch.setattr(helper, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    expected = sqlite3.OperationalError if expired_step.endswith("integrity_check") else TimeoutError
    with pytest.raises(expected):
        helper.backup(tmp_path, tmp_path / "backup")
    assert not (tmp_path / "backup/data/operator/calendly.db").exists()
