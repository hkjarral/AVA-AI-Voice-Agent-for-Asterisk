"""HIGH-1a: pre-session-registration call failures must still leave a history row.

Calls that end before a CallSession is registered (StasisStart exception, codec
abort, immediate hangup before setup) used to produce no row in `call_records`,
making them invisible in Call History. The no-session cleanup path must persist a
minimal "abandoned" record keyed by the channel id, without double-writing when a
session does exist.
"""

from unittest.mock import AsyncMock

import pytest

from src.core.session_store import SessionStore
from src.engine import Engine


@pytest.mark.asyncio
async def test_cleanup_without_session_persists_minimal_abandoned_record(monkeypatch):
    """No registered session -> exactly one persisted record, abandoned, call_id=channel."""
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()  # empty: get_by_* returns None
    engine._attended_transfer_agent_channel_to_call_id = {}

    saved_records = []

    class _FakeStore:
        _enabled = True

        async def save(self, record):
            saved_records.append(record)
            return True

    fake_store = _FakeStore()
    monkeypatch.setattr(
        "src.core.call_history.get_call_history_store", lambda: fake_store
    )

    channel_id = "channel-no-session-123"
    await engine._cleanup_call(channel_id)

    assert len(saved_records) == 1, "expected exactly one persisted record"
    rec = saved_records[0]
    assert rec.call_id == channel_id
    assert rec.outcome in ("abandoned", "error")
    assert rec.start_time is not None
    assert rec.end_time is not None
