import multiprocessing
import sqlite3
import time

import pytest
import portalocker

from src.core.calendly_store import CalendlyStore
from src.tools.business.calendly_client import CalendlyError

IDENTITY = {'uri': 'https://api.calendly.com/users/test', 'current_organization': 'https://api.calendly.com/organizations/test', 'timezone': 'UTC'}
PAYLOAD = {'event_type': 'https://api.calendly.com/event_types/test', 'start_time': '2030-01-01T12:00:00Z', 'invitee': {'email': 'test@example.com'}}


def test_verified_connection_records_private_footprint_that_survives_disconnect(tmp_path):
    store = CalendlyStore(str(tmp_path / 'calendly.db'), str(tmp_path / 'token'))
    marker = tmp_path / 'calendly.db.configured'
    assert not marker.exists()
    conn = store.save_connection(IDENTITY, 'synthetic-private-token', 0)
    assert marker.read_text() == 'calendly-configured-v1\n'
    assert marker.stat().st_mode & 0o777 == 0o600
    store.disconnect(conn['revision'])
    assert marker.is_file() and not store.token_path.exists()


@pytest.mark.parametrize('fail_marker', [False, True])
def test_disconnect_preserves_legacy_erasure_obligation_before_removing_token(tmp_path, monkeypatch, fail_marker):
    store = CalendlyStore(str(tmp_path / 'calendly.db'), str(tmp_path / 'token'))
    conn = store.save_connection(IDENTITY, 'synthetic-private-token', 0)
    marker = tmp_path / 'calendly.db.configured'
    marker.unlink()  # Connected state from a release without the marker.
    if fail_marker:
        def fail(_):
            raise OSError('synthetic marker write failure')
        monkeypatch.setattr('src.core.calendly_store.record_calendly_footprint', fail)
        with pytest.raises(OSError, match='synthetic marker write failure'):
            store.disconnect(conn['revision'])
        assert store.token_path.is_file() and not store.connection()['enabled']
    else:
        store.disconnect(conn['revision'])
        assert marker.is_file() and not store.token_path.exists()


def claim(paths):
    store = CalendlyStore(*paths)
    _, admitted = store.admit('call:concurrent', store.connection(), PAYLOAD, {})
    return admitted


def test_processes_share_one_admission_and_restart_never_resets_claim(tmp_path):
    paths = (str(tmp_path / 'db'), str(tmp_path / 'token'))
    store = CalendlyStore(*paths)
    conn = store.save_connection(IDENTITY, 'synthetic-private-token', 0)
    store.update_connection(conn['revision'], enabled=True)
    with multiprocessing.get_context('fork').Pool(4) as workers:
        assert sum(workers.map(claim, [paths] * 8)) == 1
    row = CalendlyStore(*paths).prior('call:concurrent')
    assert row['state'] == 'submitting'
    with store.db() as db: db.execute('UPDATE attempts SET lease=0,next_due=0')
    store.lease_check(row['id'], False)
    assert store.attempt(row['id'])['state'] == 'uncertain'
    assert claim(paths) is False


def test_newer_schema_and_journal_lock_fail_closed(tmp_path):
    paths = (str(tmp_path / 'db'), str(tmp_path / 'token'))
    store = CalendlyStore(*paths)
    conn = store.save_connection(IDENTITY, 'synthetic', 0)
    store.update_connection(conn['revision'], enabled=True)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError): claim(paths)
        assert time.monotonic() - start < 2
        db.rollback()
        assert not store.prior('call:concurrent')
        db.execute('PRAGMA user_version=99')
    with pytest.raises(CalendlyError, match='journal_version_unsupported'): CalendlyStore(*paths)


def test_interrupted_token_replace_stays_disabled(tmp_path, monkeypatch):
    store = CalendlyStore(str(tmp_path / 'db'), str(tmp_path / 'token'))
    conn = store.save_connection(IDENTITY, 'old-synthetic', 0)
    store.update_connection(conn['revision'], enabled=True)
    def fail(*args): raise OSError('injected replace failure')
    monkeypatch.setattr('os.replace', fail)
    with pytest.raises(OSError): store.save_connection(IDENTITY, 'new-synthetic', conn['revision'])
    assert not store.connection()['enabled']
    with pytest.raises(CalendlyError, match='credential_revision_mismatch'): store.credentials()


def test_initialized_journal_reads_skip_schema_lock_and_writes(tmp_path):
    paths = (str(tmp_path / 'db'), str(tmp_path / 'token'))
    store = CalendlyStore(*paths)
    with portalocker.Lock(str(store.path) + '.init.lock', timeout=0):
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Recovery/setup opens must not contend on schema initialization,
            # even while another process owns a normal journal transaction.
            assert CalendlyStore(*paths).connection() == {'revision': 0, 'enabled': False}
            db.rollback()


@pytest.mark.parametrize('retention', ['', 'invalid', ' 7 ', '0', '-1'])
@pytest.mark.parametrize('mode', ['single', 'retention'])
def test_cleanup_scrubs_resolution_notes_and_details_but_retains_audit_and_barriers(tmp_path, monkeypatch, retention, mode):
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', retention)
    store = CalendlyStore(str(tmp_path / 'db'), str(tmp_path / 'token'))
    with store.db() as db:
        for key in ['target', 'preserved']:
            created = time.time() - 40 * 86400 if mode == 'retention' and key == 'target' else time.time()
            db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                key, 'call:'+key, 'conn', 'fingerprint', 'duplicate-'+key, 'marker', 'needs_operator',
                '{"email":"test@example.com"}', '{"appointment_name":"Test"}', created, 0, 0, 0, 0, 0))
    for key in ['target', 'preserved']:
        store.resolve(key, 'operator', 'Test private note test@example.com')
    store.cleanup(['target'] if mode == 'single' else None)
    target = store.attempt('target')
    assert target['private'] == target['public'] == '{}'
    assert target['duplicate_key'] == 'duplicate-target' and target['operation'] == 'call:target'
    assert store.attempt('preserved')['public'] != '{}'
    with store.db() as db:
        assert tuple(db.execute("SELECT actor,outcome,note FROM resolutions WHERE attempt_id='target'").fetchone()) == ('operator', 'resolved_externally', '')
        assert db.execute("SELECT note FROM resolutions WHERE attempt_id='preserved'").fetchone()[0] == 'Test private note test@example.com'


@pytest.mark.parametrize('state', ['submitting', 'uncertain', 'needs_operator', 'confirmed', 'rejected', 'aborted_before_submit', 'resolved_remote_cancelled', 'resolved_externally', 'verified_cancelled'])
def test_call_erasure_is_an_immutable_barrier_against_late_writers(tmp_path, state):
    store = CalendlyStore(str(tmp_path / 'db'), str(tmp_path / 'token'))
    conn = store.save_connection(IDENTITY, 'synthetic', 0)
    store.update_connection(conn['revision'], enabled=True)
    original, admitted = store.admit('call:deleted', store.connection(), PAYLOAD, {'public':'value'})
    assert admitted
    key = original['id']
    with store.db() as db:
        db.execute('UPDATE attempts SET state=?,lease=500 WHERE id=?', (state, key))
    before = store.attempt(key)
    store.cleanup(['deleted'])
    store.finish(key, 'confirmed', {'late': 'details'}, {'late': 'private'})
    assert store.release_cancelled_duplicate(before) is False
    row = store.attempt(key)
    assert row['state'] == 'erased' and row['lease'] == 0
    assert row['private'] == row['public'] == '{}'
    assert store.prior('call:deleted')['id'] == key
    # The erased operation never replays, including for a different payload.
    assert store.admit('call:deleted', store.connection(), PAYLOAD, {})[1] is False
    released = state in {'rejected', 'aborted_before_submit', 'verified_cancelled'}
    assert row['duplicate_key'] == ('erased:'+key if released else before['duplicate_key'])
    assert store.admit('call:other', store.connection(), PAYLOAD, {})[1] is released
    assert store.attempt(key)['private'] == store.attempt(key)['public'] == '{}'



def test_erasure_transaction_blocks_admission_between_scrub_and_missing_operation_barrier(tmp_path, monkeypatch):
    import threading
    from contextlib import contextmanager
    from concurrent.futures import ThreadPoolExecutor
    store = CalendlyStore(str(tmp_path / 'db'), str(tmp_path / 'token'))
    conn = store.save_connection(IDENTITY, 'synthetic', 0)
    store.update_connection(conn['revision'], enabled=True)
    entered, release, started = threading.Event(), threading.Event(), threading.Event()
    original = store.db
    class PausedConnection:
        def __init__(self, db): self.db = db
        def __getattr__(self, name): return getattr(self.db, name)
        def execute(self, sql, parameters=()):
            result = self.db.execute(sql, parameters)
            if sql.startswith("UPDATE attempts SET state='erased'"):
                entered.set()
                assert release.wait(3)
            return result
    @contextmanager
    def paused_db():
        with original() as db: yield PausedConnection(db)
    monkeypatch.setattr(store, 'db', paused_db)
    def admit():
        started.set()
        return store.admit('call:deleted', store.connection(), PAYLOAD, {'private':'must not persist'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        erasure = pool.submit(store.cleanup, ['deleted'])
        assert entered.wait(2)
        admission = pool.submit(admit)
        assert started.wait(2)
        try:
            with pytest.raises(TimeoutError): admission.result(timeout=0.05)
        finally:
            release.set()
        erasure.result(timeout=3)
        row, admitted = admission.result(timeout=3)
    assert not admitted and row['state'] == 'erased'
    assert row['private'] == row['public'] == '{}'
    assert len(store.list_attempts()) == 0
    assert CalendlyStore(str(store.path), str(store.token_path)).prior('call:deleted')['state'] == 'erased'
