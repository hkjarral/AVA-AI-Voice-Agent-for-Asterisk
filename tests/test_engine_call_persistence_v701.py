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


@pytest.mark.asyncio
async def test_cleanup_aux_channel_does_not_persist_abandoned_record(monkeypatch):
    """Codex P1: an auxiliary/media channel (Local/AudioSocket/ExternalMedia) destroyed
    after the main session is already cleaned up must NOT persist its own 'abandoned'
    row. A single call has many channels; recording each aux leg pollutes call history
    and inflates abandoned stats for otherwise-successful calls.
    """
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()  # empty: get_by_* returns None
    engine._attended_transfer_agent_channel_to_call_id = {}
    # Aux leg was recorded as such when it entered Stasis (Local/AudioSocket/UnicastRTP).
    aux_channel_id = "UnicastRTP-aux-leg-999"
    engine._seen_aux_channels = {aux_channel_id}

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

    await engine._cleanup_call(aux_channel_id)

    assert saved_records == [], "aux channel must not persist an abandoned record"
    # And a genuine pre-session caller channel (never recorded as aux) still persists.
    caller_channel_id = "PJSIP-caller-leg-001"
    await engine._cleanup_call(caller_channel_id)
    assert len(saved_records) == 1, "genuine pre-session caller must still persist (HIGH-1a)"
    assert saved_records[0].call_id == caller_channel_id
    assert saved_records[0].outcome in ("abandoned", "error")


@pytest.mark.asyncio
async def test_cleanup_outbound_channel_does_not_persist_abandoned_record(monkeypatch):
    """P2 (bot re-review): an OUTBOUND dial channel destroyed before a CallSession exists
    (busy/no-answer/originate timeout) is finalized by _handle_outbound_channel_destroyed,
    which records it in _seen_outbound_channels. The subsequent _cleanup_call must NOT
    write a duplicate 'abandoned' row for that already-accounted-for outbound attempt,
    while a genuine pre-session INBOUND caller channel still persists (HIGH-1a).
    """
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()  # empty: get_by_* returns None
    engine._attended_transfer_agent_channel_to_call_id = {}
    engine._seen_aux_channels = set()
    # Outbound channel already finalized by _handle_outbound_channel_destroyed.
    outbound_channel_id = "PJSIP-outbound-leg-777"
    engine._seen_outbound_channels = {outbound_channel_id}

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

    await engine._cleanup_call(outbound_channel_id)
    assert saved_records == [], "outbound channel must not persist a duplicate abandoned record"

    # A genuine pre-session INBOUND caller channel (never recorded as outbound) still persists.
    inbound_channel_id = "PJSIP-inbound-caller-002"
    await engine._cleanup_call(inbound_channel_id)
    assert len(saved_records) == 1, "genuine pre-session inbound caller must still persist (HIGH-1a)"
    assert saved_records[0].call_id == inbound_channel_id
    assert saved_records[0].outcome in ("abandoned", "error")
