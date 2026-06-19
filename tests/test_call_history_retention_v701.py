"""WS-D (HIGH-7 + LOW-CH4): call-history retention actually deletes old records,
using a UTC-aware cutoff that matches the stored UTC start_time."""
from datetime import datetime, timedelta, timezone

from src.core.call_history import CallHistoryStore, CallRecord


def _store(tmp_path, monkeypatch, retention_days):
    monkeypatch.setenv("CALL_HISTORY_DB_PATH", str(tmp_path / "ch.db"))
    monkeypatch.setenv("CALL_HISTORY_RETENTION_DAYS", str(retention_days))
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    return CallHistoryStore()


async def test_cleanup_deletes_past_retention_keeps_recent(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, retention_days=30)
    now = datetime.now(timezone.utc)
    await store.save(CallRecord(call_id="old", start_time=now - timedelta(days=40),
                               end_time=now - timedelta(days=40)))
    await store.save(CallRecord(call_id="recent", start_time=now - timedelta(days=1),
                               end_time=now - timedelta(days=1)))

    deleted = await store.cleanup_old_records()

    assert deleted == 1
    remaining = {r.call_id for r in await store.get_recent_calls(limit=10)} \
        if hasattr(store, "get_recent_calls") else None
    # Fall back to a direct query if the convenience reader isn't present.
    if remaining is None:
        import sqlite3
        rows = sqlite3.connect(str(tmp_path / "ch.db")).execute(
            "SELECT call_id FROM call_records").fetchall()
        remaining = {r[0] for r in rows}
    assert remaining == {"recent"}


async def test_cleanup_noop_when_retention_unset(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, retention_days=0)
    old = datetime.now(timezone.utc) - timedelta(days=999)
    await store.save(CallRecord(call_id="old", start_time=old, end_time=old))
    assert await store.cleanup_old_records() == 0
