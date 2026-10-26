"""Cross-store deletion survives a journal fault and process restart."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.core.call_history import CallHistoryStore, CallRecord
from src.core.calendly_store import CalendlyStore
from src.tools.business.calendly_client import CalendlyError


@pytest.mark.parametrize('mode', ['single', 'retention'])
@pytest.mark.parametrize('fault', [sqlite3.OperationalError, CalendlyError, OSError, None])
@pytest.mark.parametrize('history_enabled_after_restart', ['true', 'false'])
async def test_history_deletion_retries_private_erasure_after_restart(tmp_path, monkeypatch, mode, fault, history_enabled_after_restart):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', '')
    monkeypatch.setenv('CALENDLY_DB_PATH', str(tmp_path / 'calendly.db'))
    monkeypatch.setenv('CALENDLY_TOKEN_PATH', str(tmp_path / 'token.json'))
    journal = CalendlyStore()
    (tmp_path / 'token.json').write_text('{"synthetic":"configured"}')
    with journal.db() as db:
        db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            'attempt', 'call:purged', 'conn', 'digest', 'duplicate', 'marker', 'confirmed',
            '{"email":"test@example.com"}', '{"appointment_name":"Test"}',
            datetime.now(timezone.utc).timestamp(), 0, 0, 0, 0, 0))
    with journal.db() as db:
        db.execute("INSERT INTO attempts SELECT 'preserved','call:preserved',connection_id,fingerprint,'duplicate-preserved',marker,state,private,public,created,updated,next_due,passes,lease,checks FROM attempts WHERE id='attempt'")
    path = str(tmp_path / 'history.db')
    history = CallHistoryStore(path)
    now = datetime.now(timezone.utc)
    await history.save(CallRecord(call_id='purged', start_time=now-timedelta(days=2), end_time=now))
    await history.save(CallRecord(call_id='preserved', start_time=now+timedelta(days=1), end_time=now+timedelta(days=1)))
    row = await history.get_by_call_id('purged')
    from src.core import calendly_store
    real = calendly_store.scrub_deleted_calls
    def fail(_): raise fault('injected')
    if fault is None:
        journal.path.rename(tmp_path / 'temporarily-unavailable.db')
    else:
        monkeypatch.setattr(calendly_store, 'scrub_deleted_calls', fail)
    if mode == 'single': assert await history.delete(row.id)
    else: assert await history.delete_before(now) == 1
    assert await history.get_by_call_id('purged') is None
    assert await history.get_by_call_id('preserved') is not None
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT call_id FROM pending_calendly_purges').fetchall() == [('purged',)]
    monkeypatch.setattr(calendly_store, 'scrub_deleted_calls', real)
    if fault is None:
        assert not journal.path.exists()  # No empty replacement journal was created.
        (tmp_path / 'temporarily-unavailable.db').rename(journal.path)
    monkeypatch.setenv('CALL_HISTORY_ENABLED', history_enabled_after_restart)
    await CallHistoryStore(path).retry_pending_calendly_purges()
    erased = journal.attempt('attempt')
    assert erased['private'] == erased['public'] == '{}'
    assert erased['state'] == 'erased' and erased['lease'] == 0
    assert journal.attempt('preserved')['private'] != '{}'
    assert erased['operation'] == 'call:purged' and erased['duplicate_key'] == 'duplicate'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM pending_calendly_purges').fetchall() == []


@pytest.mark.parametrize('legacy_file', [False, True])
async def test_disabled_history_retry_does_not_initialize_a_new_queue(tmp_path, monkeypatch, legacy_file):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'false')
    path = tmp_path / 'disabled.db'
    if legacy_file:
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE legacy (id TEXT)')
    store = CallHistoryStore(str(path))
    await store.retry_pending_calendly_purges()
    if not legacy_file:
        assert not path.exists()
    else:
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('legacy',)]


@pytest.mark.parametrize('mode', ['single', 'retention'])
async def test_history_deletion_without_calendly_footprint_does_not_queue_provider_work(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', '')
    monkeypatch.setenv('CALENDLY_DB_PATH', str(tmp_path / 'missing-calendly.db'))
    monkeypatch.setenv('CALENDLY_TOKEN_PATH', str(tmp_path / 'missing-token.json'))
    history = CallHistoryStore(str(tmp_path / 'history.db'))
    now = datetime.now(timezone.utc)
    await history.save(CallRecord(call_id='ordinary-call', start_time=now-timedelta(days=2), end_time=now))
    if mode == 'single':
        row = await history.get_by_call_id('ordinary-call')
        assert await history.delete(row.id)
    else:
        assert await history.delete_before(now) == 1
    with sqlite3.connect(history._db_path) as db:
        assert db.execute('SELECT * FROM pending_calendly_purges').fetchall() == []


async def test_saved_calendly_credential_keeps_erasure_queue_when_journal_is_temporarily_missing(tmp_path, monkeypatch):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', '')
    monkeypatch.setenv('CALENDLY_DB_PATH', str(tmp_path / 'missing-calendly.db'))
    token = tmp_path / 'calendly-token.json'
    token.write_text('{"synthetic":"configured"}')
    monkeypatch.setenv('CALENDLY_TOKEN_PATH', str(token))
    history = CallHistoryStore(str(tmp_path / 'history.db'))
    now = datetime.now(timezone.utc)
    await history.save(CallRecord(call_id='configured-call', start_time=now, end_time=now))
    row = await history.get_by_call_id('configured-call')
    assert await history.delete(row.id)
    with sqlite3.connect(history._db_path) as db:
        assert db.execute('SELECT call_id FROM pending_calendly_purges').fetchall() == [('configured-call',)]


async def test_disconnected_calendly_footprint_queues_erasure_during_journal_outage(tmp_path, monkeypatch):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', '')
    journal_path = tmp_path / 'calendly.db'
    token_path = tmp_path / 'token.json'
    monkeypatch.setenv('CALENDLY_DB_PATH', str(journal_path))
    monkeypatch.setenv('CALENDLY_TOKEN_PATH', str(token_path))
    journal = CalendlyStore()
    conn = journal.save_connection(
        {'uri': 'https://api.calendly.com/users/test',
         'current_organization': 'https://api.calendly.com/organizations/test'},
        'synthetic-private-token', 0,
    )
    journal.disconnect(conn['revision'])
    journal_path.rename(tmp_path / 'temporarily-unavailable.db')
    assert not journal_path.exists() and not token_path.exists()

    history = CallHistoryStore(str(tmp_path / 'history.db'))
    now = datetime.now(timezone.utc)
    await history.save(CallRecord(call_id='disconnected-call', start_time=now, end_time=now))
    row = await history.get_by_call_id('disconnected-call')
    assert await history.delete(row.id)
    with sqlite3.connect(history._db_path) as db:
        assert db.execute('SELECT call_id FROM pending_calendly_purges').fetchall() == [('disconnected-call',)]


@pytest.mark.parametrize('mode', ['single', 'retention'])
@pytest.mark.parametrize('disconnected_before_upgrade', [False, True])
@pytest.mark.parametrize('history_enabled_after_restart', ['true', 'false'])
async def test_legacy_upgrade_preserves_erasure_after_disconnect_and_journal_outage(
    tmp_path, monkeypatch, mode, disconnected_before_upgrade, history_enabled_after_restart,
):
    """Upgrade the pre-marker layout without resaving a credential or booking."""
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', '')
    monkeypatch.setenv('CALENDLY_DB_PATH', str(tmp_path / 'calendly.db'))
    monkeypatch.setenv('CALENDLY_TOKEN_PATH', str(tmp_path / 'token.json'))
    legacy = CalendlyStore()
    conn = legacy.save_connection(
        {'uri': 'https://api.calendly.com/users/test',
         'current_organization': 'https://api.calendly.com/organizations/test'},
        'synthetic-private-token', 0,
    )
    now = datetime.now(timezone.utc)
    with legacy.db() as db:
        for key in ['deleted', 'preserved']:
            db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                key, 'call:' + key, conn['id'], 'fingerprint', 'duplicate-' + key,
                'tracking-marker', 'confirmed', '{"email":"synthetic@example.com"}',
                '{"appointment_name":"Synthetic"}', now.timestamp(), 0, 0, 0, 0, 0,
            ))
    if disconnected_before_upgrade:
        legacy.disconnect(conn['revision'])
    marker = tmp_path / 'calendly.db.configured'
    # The schema/version and saved state are unchanged from pre-marker releases.
    # Remove only the new sidecar to reproduce their exact persistent layout.
    marker.unlink()
    upgraded = CalendlyStore()
    assert marker.is_file() and marker.stat().st_mode & 0o777 == 0o600
    if not disconnected_before_upgrade:
        upgraded.disconnect(conn['revision'])
    assert not upgraded.token_path.exists()

    path = str(tmp_path / 'history.db')
    history = CallHistoryStore(path)
    await history.save(CallRecord(call_id='deleted', start_time=now-timedelta(days=2), end_time=now))
    await history.save(CallRecord(call_id='preserved', start_time=now+timedelta(days=1), end_time=now+timedelta(days=1)))
    row = await history.get_by_call_id('deleted')
    unavailable = tmp_path / 'temporarily-unavailable.db'
    upgraded.path.rename(unavailable)
    if mode == 'single':
        assert await history.delete(row.id)
    else:
        assert await history.delete_before(now) == 1
    assert await history.get_by_call_id('deleted') is None
    assert await history.get_by_call_id('preserved') is not None
    assert not upgraded.path.exists()  # Deletion must not create an empty journal.
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT call_id FROM pending_calendly_purges').fetchall() == [('deleted',)]

    unavailable.rename(upgraded.path)
    monkeypatch.setenv('CALL_HISTORY_ENABLED', history_enabled_after_restart)
    await CallHistoryStore(path).retry_pending_calendly_purges()
    erased = upgraded.attempt('deleted')
    assert erased['state'] == 'erased' and erased['private'] == erased['public'] == '{}'
    assert erased['operation'] == 'call:deleted' and erased['duplicate_key'] == 'duplicate-deleted'
    assert upgraded.attempt('preserved')['private'] != '{}'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM pending_calendly_purges').fetchall() == []


async def test_erasure_retry_processes_bounded_batches_without_dropping_remaining_work(tmp_path, monkeypatch):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    history = CallHistoryStore(str(tmp_path / 'history.db'))
    ids = {f'call-{i}' for i in range(105)}
    with sqlite3.connect(history._db_path) as db:
        db.executemany('INSERT INTO pending_calendly_purges VALUES(?)', [(key,) for key in ids])
    batches = []
    def scrub(calls):
        batches.append(set(calls))
        return True
    monkeypatch.setattr('src.core.calendly_store.scrub_deleted_calls', scrub)
    await history.retry_pending_calendly_purges()
    with sqlite3.connect(history._db_path) as db:
        remaining = {r[0] for r in db.execute('SELECT call_id FROM pending_calendly_purges')}
    assert len(batches[0]) == 100 and len(remaining) == 5
    assert batches[0].isdisjoint(remaining) and batches[0] | remaining == ids
    await history.retry_pending_calendly_purges()
    assert batches[1] == remaining
    with sqlite3.connect(history._db_path) as db:
        assert db.execute('SELECT * FROM pending_calendly_purges').fetchall() == []


@pytest.mark.parametrize('retention', ['', 'invalid'])
def test_blank_or_invalid_retention_does_not_prevent_history_erasure_worker_start(tmp_path, monkeypatch, retention):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', retention)
    history = CallHistoryStore(str(tmp_path / 'history.db'))
    assert history._enabled and history._initialized and history._retention_days == 0
