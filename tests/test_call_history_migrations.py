import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest

from src.core import call_history as history


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    return str(tmp_path / "history.db")


def test_fresh_db_and_repeated_startup(db_path):
    assert history.CallHistoryStore(db_path)._initialized
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version").fetchall()
        assert [row[0] for row in rows] == [version for version, _ in history.MIGRATIONS]
        assert all(datetime.fromisoformat(applied_at) for _, applied_at in rows)
        assert history.current_schema_version(conn) == rows[-1][0]
        assert history.CallHistoryStore(db_path)._initialized
        assert conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall() == rows


@pytest.mark.parametrize("already_has_routing", [False, True])
async def test_additive_upgrade_preserves_legacy_rows(db_path, already_has_routing):
    now = datetime.now(timezone.utc)
    old_call_id = uuid4().hex
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("""
            CREATE TABLE call_records (
                id TEXT PRIMARY KEY, call_id TEXT NOT NULL, caller_number TEXT, caller_name TEXT,
                start_time TEXT NOT NULL, end_time TEXT NOT NULL, duration_seconds REAL,
                provider_name TEXT, pipeline_name TEXT, pipeline_components TEXT, context_name TEXT,
                conversation_history TEXT, outcome TEXT, transfer_destination TEXT, error_message TEXT,
                tool_calls TEXT, avg_turn_latency_ms REAL, max_turn_latency_ms REAL, total_turns INTEGER,
                caller_audio_format TEXT, codec_alignment_ok INTEGER, barge_in_count INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("INSERT INTO call_records(id, call_id, start_time, end_time) VALUES (?,?,?,?)",
                     (uuid4().hex, old_call_id, now.isoformat(), now.isoformat()))
        if already_has_routing:
            conn.execute("ALTER TABLE call_records ADD COLUMN routing_method TEXT")
            conn.execute("UPDATE call_records SET routing_method = 'ai_agent'")
        assert history.current_schema_version(conn) == 0

    store = history.CallHistoryStore(db_path)
    assert store._initialized
    legacy = await store.get_by_call_id(old_call_id)
    assert legacy.routing_method == ("ai_agent" if already_has_routing else None)
    assert legacy.pre_call_tool_calls == legacy.post_call_tool_calls == []
    record = history.CallRecord(call_id=uuid4().hex, start_time=now, end_time=now)
    assert await store.save(record)
    assert (await store.get_by_call_id(record.call_id)).id == record.id
    with closing(sqlite3.connect(db_path)) as conn:
        assert history.current_schema_version(conn) == history.MIGRATIONS[-1][0]
        assert conn.execute("SELECT calendly_erasure_required FROM call_records WHERE call_id = ?",
                            (old_call_id,)).fetchone() == (1,)


def test_failed_migration_rolls_back_ddl_and_retries(db_path, monkeypatch):
    original = history.MIGRATIONS
    next_version = original[-1][0] + 1

    def migrate(conn):
        conn.execute("ALTER TABLE call_records ADD COLUMN upgrade_probe TEXT")

    def fail(conn):
        migrate(conn)
        raise sqlite3.OperationalError("generated migration failure")

    monkeypatch.setattr(history, "MIGRATIONS", [*original, (next_version, fail)])
    assert history.CallHistoryStore(db_path)._enabled
    with closing(sqlite3.connect(db_path)) as conn:
        assert history.current_schema_version(conn) == original[-1][0]
        assert "upgrade_probe" not in {row[1] for row in conn.execute("PRAGMA table_info(call_records)")}

    monkeypatch.setattr(history, "MIGRATIONS", [*original, (next_version, migrate)])
    assert history.CallHistoryStore(db_path)._initialized
    with closing(sqlite3.connect(db_path)) as conn:
        assert history.current_schema_version(conn) == next_version
        assert "upgrade_probe" in {row[1] for row in conn.execute("PRAGMA table_info(call_records)")}


def test_concurrent_stores_apply_pending_migration_once(db_path, monkeypatch):
    assert history.CallHistoryStore(db_path)._initialized
    original = history.MIGRATIONS
    version = original[-1][0] + 1

    def migrate(conn):
        conn.execute("ALTER TABLE call_records ADD COLUMN concurrent_probe TEXT")

    monkeypatch.setattr(history, "MIGRATIONS", [*original, (version, migrate)])
    start = Barrier(4)

    def open_store(_):
        start.wait(timeout=10)
        return history.CallHistoryStore(db_path)._initialized

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(open_store, range(4)))
    with closing(sqlite3.connect(db_path)) as conn:
        assert history.current_schema_version(conn) == version
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (version,)).fetchone() == (1,)


async def test_newer_additive_db_warns_once_and_keeps_recording(db_path, caplog):
    assert history.CallHistoryStore(db_path)._initialized
    future_version = history.MIGRATIONS[-1][0] + 1
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("ALTER TABLE call_records ADD COLUMN future_label TEXT DEFAULT 'future'")
        conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?,?)",
                     (future_version, datetime.now(timezone.utc).isoformat()))

    for _ in range(2):
        store = history.CallHistoryStore(db_path)
        now = datetime.now(timezone.utc)
        record = history.CallRecord(call_id=uuid4().hex, start_time=now, end_time=now)
        assert await store.save(record)
        assert (await store.get_by_call_id(record.call_id)).id == record.id
    with closing(sqlite3.connect(db_path)) as conn:
        assert history.current_schema_version(conn) == future_version
        assert conn.execute("SELECT future_label FROM call_records").fetchall() == [("future",), ("future",)]
    assert sum("newer" in entry.message.lower() for entry in caplog.records) == 1


def test_startup_retries_transient_wal_contention(db_path, monkeypatch):
    connect = history.CallHistoryStore._get_connection
    attempts = 0

    def contended_open(store):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            error = sqlite3.OperationalError("database is locked")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise error
        return connect(store)

    monkeypatch.setattr(history.CallHistoryStore, "_get_connection", contended_open)
    assert history.CallHistoryStore(db_path)._initialized
    assert attempts == 2


async def test_failed_optional_upgrade_keeps_recording_legacy_columns(db_path, monkeypatch, caplog):
    now = datetime.now(timezone.utc)
    assert history.CallHistoryStore(db_path)._initialized
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("DELETE FROM schema_migrations")
        db.execute("DROP INDEX idx_call_records_external_platform_start")
        for col in ("voice", "external_platform", "external_metadata", "call_metadata", "pre_call_tool_calls", "post_call_tool_calls"):
            db.execute(f"ALTER TABLE call_records DROP COLUMN {col}")

    def refused(db):
        db.execute("ALTER TABLE call_records ADD COLUMN voice TEXT")
        raise sqlite3.OperationalError("generated failure")

    original = history.MIGRATIONS
    monkeypatch.setattr(history, "MIGRATIONS", [(1, refused)])
    first = history.CallRecord(call_id=uuid4().hex, start_time=now, end_time=now, outcome="completed",
                               conversation_history=[{"role": "user", "content": "generated"}])
    for _ in range(2):
        store = history.CallHistoryStore(db_path)
        assert store._enabled and await store.save(first)
    assert sum("migration" in row.message.lower() and row.levelname == "WARNING" for row in caplog.records) == 1
    persisted = await store.get_by_call_id(first.call_id)
    assert persisted.conversation_history == first.conversation_history and persisted.voice is None
    assert [item.call_id for item in await store.list(include_details=False)] == [first.call_id]
    assert await store.count(call_metadata_key="generated", call_metadata_value="absent") == 0
    assert await store.list_external_activity("generated", now) == []
    assert not await store.append_phase_tool(first.call_id, "post_call", {"name": "generated"})
    assert not await store.update_external_lifecycle(first.call_id, external_disposition="done", external_metadata={})
    assert not any(row.levelname == "ERROR" for row in caplog.records)
    with closing(sqlite3.connect(db_path)) as db:
        assert history.current_schema_version(db) == 0
        assert db.execute("SELECT calendly_erasure_required FROM call_records").fetchone() == (0,)
    monkeypatch.setattr(history, "MIGRATIONS", original)
    restored = history.CallHistoryStore(db_path)
    assert restored._enabled
    with closing(sqlite3.connect(db_path)) as db:
        assert history.current_schema_version(db) == original[-1][0]
        assert "voice" in {row[1] for row in db.execute("PRAGMA table_info(call_records)")}


async def test_concurrent_async_first_open_runs_once_off_the_event_loop(db_path, monkeypatch):
    monkeypatch.setattr(history, "_call_history_store", None)
    monkeypatch.setenv("CALL_HISTORY_DB_PATH", db_path)
    original = history.CallHistoryStore._init_db
    loop_thread = threading.get_ident()
    init_threads = []

    def init(store):
        init_threads.append(threading.get_ident())
        assert init_threads[-1] != loop_thread
        return original(store)

    monkeypatch.setattr(history.CallHistoryStore, "_init_db", init)
    stores = await asyncio.gather(*(history.get_call_history_store_async() for _ in range(8)))
    assert len(init_threads) == 1
    assert all(store is stores[0] and store._initialized for store in stores)


async def test_abandoned_call_can_open_history_before_engine_warmup(db_path, monkeypatch):
    from src.engine import Engine

    monkeypatch.setenv("CALL_HISTORY_DB_PATH", db_path)
    monkeypatch.setattr(history, "_call_history_store", None)
    threads = []
    original = history.CallHistoryStore._init_db

    def init(store):
        threads.append(threading.get_ident())
        original(store)

    monkeypatch.setattr(history.CallHistoryStore, "_init_db", init)
    call_id = uuid4().hex
    await Engine.__new__(Engine)._persist_abandoned_call_history(call_id)
    assert len(threads) == 1 and threads[0] != threading.get_ident()
    assert (await history.get_call_history_store().get_by_call_id(call_id)).outcome == "abandoned"
