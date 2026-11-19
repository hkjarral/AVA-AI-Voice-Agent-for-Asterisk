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


@pytest.mark.parametrize('mode', ['single', 'retention'])
@pytest.mark.parametrize('unreadable', ['journal', 'credential', 'marker'])
async def test_unreadable_calendly_footprint_queues_without_blocking_history_deletion(
    tmp_path, monkeypatch, mode, unreadable,
):
    """Unknown footprint state preserves erasure work while history deletion succeeds."""
    from src.core import call_history

    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', '')
    journal = tmp_path / 'calendly.db'
    credential = tmp_path / 'token.json'
    marker = tmp_path / 'calendly.db.configured'
    monkeypatch.setenv('CALENDLY_DB_PATH', str(journal))
    monkeypatch.setenv('CALENDLY_TOKEN_PATH', str(credential))
    target = {'journal': journal, 'credential': credential, 'marker': marker}[unreadable]
    real_is_file = call_history.Path.is_file

    def inaccessible(path):
        if path == target:
            raise PermissionError('synthetic inaccessible footprint')
        return real_is_file(path)

    monkeypatch.setattr(call_history.Path, 'is_file', inaccessible)
    history = CallHistoryStore(str(tmp_path / 'history.db'))
    now = datetime.now(timezone.utc)
    record = CallRecord(call_id='unknown-footprint', start_time=now-timedelta(days=2), end_time=now)
    await history.save(record)
    if mode == 'single':
        assert await history.delete(record.id)
    else:
        assert await history.delete_before(now) == 1
    assert await history.get_by_call_id(record.call_id) is None
    with sqlite3.connect(history._db_path) as db:
        assert db.execute('SELECT call_id FROM pending_calendly_purges').fetchall() == [('unknown-footprint',)]


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


@pytest.mark.parametrize('mode', ['single', 'retention'])
@pytest.mark.parametrize('override', ['unset', 'blank'])
@pytest.mark.parametrize('footprint', ['none', 'journal', 'credential', 'marker'])
async def test_default_paths_allow_deletion_and_preserve_erasure_work(
    tmp_path, monkeypatch, mode, override, footprint,
):
    """Optional blank path overrides behave like omitted container settings."""
    from pathlib import Path
    from src.core import calendly_store, call_history

    journal_path, token_path = tmp_path / 'journal.db', tmp_path / 'token.json'
    now = datetime.now(timezone.utc)
    if footprint != 'none':
        store = CalendlyStore(str(journal_path), str(token_path))
        connection = store.save_connection(
            {'uri': 'https://api.calendly.com/users/test',
             'current_organization': 'https://api.calendly.com/organizations/test'},
            'synthetic-private-token', 0,
        )
        with store.db() as db:
            db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                'attempt', 'call:deleted', connection['id'], 'fingerprint', 'duplicate',
                'tracking-marker', 'confirmed', '{"email":"synthetic@example.com"}',
                '{}', now.timestamp(), 0, 0, 0, 0, 0,
            ))
        if footprint == 'marker':
            store.disconnect(connection['revision'])
        if footprint in {'credential', 'marker'}:
            journal_path.rename(tmp_path / 'unavailable.db')
        if footprint == 'credential':
            (tmp_path / 'journal.db.configured').unlink()
    for name in ['CALENDLY_DB_PATH', 'CALENDLY_TOKEN_PATH']:
        if override == 'blank':
            monkeypatch.setenv(name, '')
        else:
            monkeypatch.delenv(name, raising=False)
    # Map the real default container paths to isolated fixture storage.
    defaults = {'/app/data/operator/calendly.db': journal_path,
                '/app/project/secrets/calendly-default-token.json': token_path}
    def fixture_path(value):
        return Path(defaults.get(str(value), value))
    monkeypatch.setattr(calendly_store, 'Path', fixture_path)
    monkeypatch.setattr(call_history, 'Path', fixture_path)
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', '')
    history = CallHistoryStore(str(tmp_path / 'history.db'))
    await history.save(CallRecord(call_id='deleted', start_time=now-timedelta(days=2), end_time=now))
    row = await history.get_by_call_id('deleted')
    if mode == 'single':
        assert await history.delete(row.id)
    else:
        assert await history.delete_before(now) == 1
    assert await history.get_by_call_id('deleted') is None
    with sqlite3.connect(history._db_path) as db:
        queued = db.execute('SELECT call_id FROM pending_calendly_purges').fetchall()
    assert queued == ([('deleted',)] if footprint in {'credential', 'marker'} else [])
    if footprint == 'journal':
        erased = store.attempt('attempt')
        assert erased['state'] == 'erased' and erased['private'] == erased['public'] == '{}'
    elif footprint == 'none':
        assert not journal_path.exists() and not token_path.exists()


@pytest.mark.parametrize('mode', ['single', 'retention'])
@pytest.mark.parametrize('history_enabled_after_restart', ['true', 'false'])
async def test_legacy_history_keeps_erasure_when_journal_is_absent_before_first_upgrade(
    tmp_path, monkeypatch, mode, history_enabled_after_restart,
):
    """Missing adapter files cannot revoke the old writer's erasure contract."""
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', '')
    monkeypatch.setenv('CALENDLY_DB_PATH', str(tmp_path / 'journal.db'))
    monkeypatch.setenv('CALENDLY_TOKEN_PATH', str(tmp_path / 'token.json'))
    journal = CalendlyStore()
    connection = journal.save_connection(
        {'uri': 'https://api.calendly.com/users/test',
         'current_organization': 'https://api.calendly.com/organizations/test'},
        'synthetic-private-token', 0,
    )
    now = datetime.now(timezone.utc)
    with journal.db() as db:
        for key in ['deleted', 'preserved']:
            db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                key, 'call:' + key, connection['id'], 'fingerprint', 'duplicate-' + key,
                'tracking-marker', 'confirmed', '{"email":"synthetic@example.com"}',
                '{}', now.timestamp(), 0, 0, 0, 0, 0,
            ))
    journal.disconnect(connection['revision'])
    (tmp_path / 'journal.db.configured').unlink()
    unavailable = tmp_path / 'unavailable.db'
    journal.path.rename(unavailable)

    path = str(tmp_path / 'history.db')
    legacy_history = CallHistoryStore(path)
    target = CallRecord(call_id='deleted', start_time=now-timedelta(days=2), end_time=now)
    await legacy_history.save(target)
    await legacy_history.save(CallRecord(call_id='preserved', start_time=now+timedelta(days=1), end_time=now+timedelta(days=1)))
    with sqlite3.connect(path) as db:
        columns = {r[1] for r in db.execute('PRAGMA table_info(call_records)')}
        if 'calendly_erasure_required' in columns:
            db.execute('ALTER TABLE call_records DROP COLUMN calendly_erasure_required')
    # No credential, marker or journal is available when migration first runs.
    history = CallHistoryStore(path)
    assert await history.save(target)  # A duplicate save cannot clear old obligations.
    ordinary = CallRecord(call_id='new-unused-call', start_time=now, end_time=now)
    await history.save(ordinary)
    assert await history.delete(ordinary.id)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM pending_calendly_purges').fetchall() == []
    if mode == 'single':
        assert await history.delete(target.id)
    else:
        assert await history.delete_before(now) == 1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT call_id FROM pending_calendly_purges').fetchall() == [('deleted',)]
    assert not journal.path.exists()
    unavailable.rename(journal.path)
    monkeypatch.setenv('CALL_HISTORY_ENABLED', history_enabled_after_restart)
    await CallHistoryStore(path).retry_pending_calendly_purges()
    erased = journal.attempt('deleted')
    assert erased['state'] == 'erased' and erased['private'] == erased['public'] == '{}'
    assert erased['operation'] == 'call:deleted' and erased['duplicate_key'] == 'duplicate-deleted'
    assert journal.attempt('preserved')['private'] != '{}'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM pending_calendly_purges').fetchall() == []


async def test_older_history_writer_remains_conservative_after_code_rollback(tmp_path, monkeypatch):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALENDLY_DB_PATH', str(tmp_path / 'missing-journal.db'))
    monkeypatch.setenv('CALENDLY_TOKEN_PATH', str(tmp_path / 'missing-token.json'))
    history = CallHistoryStore(str(tmp_path / 'history.db'))
    now = datetime.now(timezone.utc)
    fresh = CallRecord(call_id='new-writer', start_time=now, end_time=now)
    assert await history.save(fresh)
    with sqlite3.connect(history._db_path) as db:
        # The previous INSERT column list omits the new compatibility field.
        db.execute('INSERT INTO call_records(id,call_id,start_time,end_time) VALUES(?,?,?,?)',
                   ('old-row', 'old-writer', now.isoformat(), now.isoformat()))
        assert db.execute('SELECT call_id,calendly_erasure_required FROM call_records ORDER BY call_id').fetchall() == [('new-writer', 0), ('old-writer', 1)]
    assert await history.delete(fresh.id)
    with sqlite3.connect(history._db_path) as db:
        assert db.execute('SELECT * FROM pending_calendly_purges').fetchall() == []
    assert await history.delete('old-row')
    with sqlite3.connect(history._db_path) as db:
        assert db.execute('SELECT call_id FROM pending_calendly_purges').fetchall() == [('old-writer',)]


async def test_required_erasure_migration_failure_does_not_allow_history_deletion(tmp_path, monkeypatch):
    from src.core import call_history
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    path = str(tmp_path / 'history.db')
    history = CallHistoryStore(path)
    now = datetime.now(timezone.utc)
    target = CallRecord(call_id='old-call', start_time=now, end_time=now)
    assert await history.save(target)
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE call_records DROP COLUMN calendly_erasure_required')
    connect = sqlite3.connect
    def migration_denied(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ALTER_TABLE else sqlite3.SQLITE_OK)
        return db
    monkeypatch.setattr(call_history.sqlite3, 'connect', migration_denied)
    unavailable = CallHistoryStore(path)
    assert not unavailable._enabled and not unavailable._initialized
    assert await unavailable.delete(target.id) is False
    assert await unavailable.delete_before(now+timedelta(days=1)) == 0
    with connect(path) as db:
        assert db.execute('SELECT call_id FROM call_records').fetchall() == [('old-call',)]
    monkeypatch.setattr(call_history.sqlite3, 'connect', connect)
    restored = CallHistoryStore(path)
    assert restored._enabled and restored._initialized
    with connect(path) as db:
        assert db.execute('SELECT calendly_erasure_required FROM call_records').fetchall() == [(1,)]


@pytest.mark.parametrize('mode', ['single', 'retention'])
@pytest.mark.parametrize('footprint', ['journal', 'credential', 'marker'])
async def test_io_error_adapter_footprint_keeps_erasure_without_blocking_deletion(
    tmp_path, monkeypatch, mode, footprint,
):
    from pathlib import Path
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', '')
    journal, credential = tmp_path / 'journal.db', tmp_path / 'token.json'
    monkeypatch.setenv('CALENDLY_DB_PATH', str(journal))
    monkeypatch.setenv('CALENDLY_TOKEN_PATH', str(credential))
    history = CallHistoryStore(str(tmp_path / 'history.db'))
    now = datetime.now(timezone.utc)
    record = CallRecord(call_id='unknown-footprint', start_time=now-timedelta(days=2), end_time=now)
    assert await history.save(record)
    target = {'journal': journal, 'credential': credential,
              'marker': tmp_path / 'journal.db.configured'}[footprint]
    is_file = Path.is_file
    def unavailable(path):
        if path == target:
            raise OSError('synthetic footprint stat failure')
        return is_file(path)
    monkeypatch.setattr(Path, 'is_file', unavailable)
    if mode == 'single':
        assert await history.delete(record.id)
    else:
        assert await history.delete_before(now) == 1
    assert await history.get_by_call_id(record.call_id) is None
    with sqlite3.connect(history._db_path) as db:
        assert db.execute('SELECT call_id FROM pending_calendly_purges').fetchall() == [('unknown-footprint',)]
    assert not journal.exists()  # No replacement journal can discharge the retry.


@pytest.mark.parametrize('mode', ['single', 'retention'])
async def test_invalid_footprint_path_keeps_erasure_without_blocking_deletion(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', '')
    monkeypatch.setenv('CALENDLY_DB_PATH', '.')
    monkeypatch.setenv('CALENDLY_TOKEN_PATH', str(tmp_path / 'missing-token.json'))
    monkeypatch.setattr('src.core.calendly_store.scrub_deleted_calls', lambda _: False)
    history = CallHistoryStore(str(tmp_path / 'history.db'))
    now = datetime.now(timezone.utc)
    record = CallRecord(call_id='invalid-footprint', start_time=now-timedelta(days=2), end_time=now)
    assert await history.save(record)
    if mode == 'single':
        assert await history.delete(record.id)
    else:
        assert await history.delete_before(now) == 1
    with sqlite3.connect(history._db_path) as db:
        assert db.execute('SELECT call_id FROM pending_calendly_purges').fetchall() == [('invalid-footprint',)]


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
