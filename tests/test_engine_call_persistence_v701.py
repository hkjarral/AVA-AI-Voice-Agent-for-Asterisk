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
async def test_cleanup_without_session_persists_stasis_caller_abandoned_record(monkeypatch):
    """A caller channel that entered AAVA Stasis still leaves an abandoned record."""
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()  # empty: get_by_* returns None
    engine._attended_transfer_agent_channel_to_call_id = {}
    channel_id = "channel-no-session-123"
    engine._seen_caller_stasis_channels = {channel_id}

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

    await engine._cleanup_call(channel_id)

    assert len(saved_records) == 1, "expected exactly one persisted record"
    rec = saved_records[0]
    assert rec.call_id == channel_id
    assert rec.outcome in ("abandoned", "error")
    assert rec.start_time is not None
    assert rec.end_time is not None
    assert channel_id not in engine._seen_caller_stasis_channels


@pytest.mark.asyncio
async def test_cleanup_non_aava_channel_does_not_persist_abandoned_record(monkeypatch):
    """PBX/FreePBX channels observed by ARI but never handled by AAVA must not pollute history."""
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()  # empty: get_by_* returns None
    engine._attended_transfer_agent_channel_to_call_id = {}
    engine._seen_aux_channels = set()
    engine._seen_outbound_channels = set()
    engine._seen_caller_stasis_channels = set()

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

    await engine._cleanup_call("PJSIP/freepbx-extension-00000001")

    assert saved_records == [], "non-AAVA channel must not persist an abandoned record"


@pytest.mark.asyncio
async def test_outbound_amd_human_path_marks_caller_before_attach(monkeypatch):
    """Outbound human-call returns with Stasis args but still represents an AAVA caller channel."""
    engine = Engine.__new__(Engine)
    channel_id = "PJSIP-outbound-human-001"
    engine._outbound_awaiting_amd_channel_ids = {channel_id}
    engine._outbound_attempt_meta_by_attempt_id = {
        "attempt-123": {
            "attempt_id": "attempt-123",
            "lead_id": "lead-123",
            "campaign_id": "campaign-123",
            "phone_number": "15551234567",
            "lead_name": "Test Lead",
            "context": "sales",
        }
    }
    engine._outbound_attempt_meta_by_channel_id = {}
    engine._outbound_attempt_amd = {}
    engine._seen_caller_stasis_channels = set()
    handled = []

    class _FakeOutboundStore:
        async def set_attempt_gate_result(self, *args, **kwargs):
            return None

        async def set_lead_state(self, *args, **kwargs):
            return None

    class _FakeAriClient:
        async def set_channel_var(self, *args, **kwargs):
            return None

    engine.outbound_store = _FakeOutboundStore()
    engine.ari_client = _FakeAriClient()

    async def _fake_handle_caller_stasis_start_hybrid(observed_channel_id, channel):
        handled.append((observed_channel_id, channel))
        assert observed_channel_id in engine._seen_caller_stasis_channels

    monkeypatch.setattr(
        engine,
        "_handle_caller_stasis_start_hybrid",
        _fake_handle_caller_stasis_start_hybrid,
    )

    await engine._handle_outbound_amd_result(
        channel_id,
        {"id": channel_id, "name": "PJSIP/outbound-human-00000001"},
        ["outbound_amd", "attempt-123", "HUMAN"],
    )

    assert handled[0][0] == channel_id
    assert handled[0][1]["caller"] == {"name": "Test Lead", "number": "15551234567"}
    assert channel_id in engine._seen_caller_stasis_channels


@pytest.mark.asyncio
async def test_outbound_amd_machine_path_does_not_mark_caller(monkeypatch):
    """Machine/voicemail outbound AMD results are finalized outside Call History."""
    engine = Engine.__new__(Engine)
    channel_id = "PJSIP-outbound-machine-001"
    engine._outbound_awaiting_amd_channel_ids = {channel_id}
    engine._outbound_attempt_meta_by_attempt_id = {
        "attempt-456": {
            "attempt_id": "attempt-456",
            "lead_id": "lead-456",
            "campaign_id": "campaign-456",
            "context": "sales",
        }
    }
    engine._outbound_attempt_meta_by_channel_id = {
        channel_id: engine._outbound_attempt_meta_by_attempt_id["attempt-456"]
    }
    engine._outbound_attempt_amd = {}
    engine._seen_caller_stasis_channels = set()
    finished = []
    hung_up = []

    class _FakeOutboundStore:
        async def set_attempt_gate_result(self, *args, **kwargs):
            return None

        async def get_campaign(self, campaign_id):
            return {"voicemail_drop_enabled": 1}

        async def finish_attempt(self, attempt_id, **kwargs):
            finished.append((attempt_id, kwargs))

        async def set_lead_state(self, *args, **kwargs):
            return None

    class _FakeAriClient:
        async def play_media(self, *args, **kwargs):
            return {}

        async def hangup_channel(self, channel_id):
            hung_up.append(channel_id)

    engine.outbound_store = _FakeOutboundStore()
    engine.ari_client = _FakeAriClient()

    await engine._handle_outbound_amd_result(
        channel_id,
        {"id": channel_id, "name": "PJSIP/outbound-machine-00000001"},
        ["outbound_amd", "attempt-456", "MACHINE"],
    )

    assert channel_id not in engine._seen_caller_stasis_channels
    assert finished[0][1]["outcome"] == "voicemail_dropped"
    assert hung_up == [channel_id]


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
    engine._seen_caller_stasis_channels = set()

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
    engine._seen_caller_stasis_channels.add(caller_channel_id)
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
    engine._seen_caller_stasis_channels = {outbound_channel_id}

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
    assert outbound_channel_id not in engine._seen_outbound_channels
    assert outbound_channel_id not in engine._seen_caller_stasis_channels

    # A genuine pre-session INBOUND caller channel (never recorded as outbound) still persists.
    inbound_channel_id = "PJSIP-inbound-caller-002"
    engine._seen_caller_stasis_channels.add(inbound_channel_id)
    await engine._cleanup_call(inbound_channel_id)
    assert len(saved_records) == 1, "genuine pre-session inbound caller must still persist (HIGH-1a)"
    assert saved_records[0].call_id == inbound_channel_id
    assert saved_records[0].outcome in ("abandoned", "error")
