import os
from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.asyncio
async def test_call_history_list_count_filter_parity(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "call_history.db")

    from src.core.call_history import CallHistoryStore, CallRecord

    store = CallHistoryStore(db_path=db_path)

    now = datetime.now(timezone.utc)

    r1 = CallRecord(
        call_id="call-1",
        caller_number="1001",
        caller_name="Alice",
        start_time=now,
        end_time=now + timedelta(seconds=10),
        duration_seconds=10.0,
        provider_name="openai_realtime",
        pipeline_name=None,
        pipeline_components={},
        context_name="demo",
        conversation_history=[{"role": "user", "content": "hi"}],
        outcome="completed",
        tool_calls=[],
        avg_turn_latency_ms=100.0,
        max_turn_latency_ms=100.0,
        total_turns=1,
        caller_audio_format="ulaw",
        codec_alignment_ok=True,
        barge_in_count=0,
    )

    r2 = CallRecord(
        call_id="call-2",
        caller_number="1002",
        caller_name="Bob",
        start_time=now + timedelta(minutes=1),
        end_time=now + timedelta(minutes=1, seconds=5),
        duration_seconds=5.0,
        provider_name="deepgram",
        pipeline_name=None,
        pipeline_components={},
        context_name="demo",
        conversation_history=[{"role": "user", "content": "transfer me"}],
        outcome="transferred",
        tool_calls=[{"name": "transfer_call", "params": {"target": "6000"}, "result": "success"}],
        avg_turn_latency_ms=250.0,
        max_turn_latency_ms=250.0,
        total_turns=1,
        caller_audio_format="ulaw",
        codec_alignment_ok=True,
        barge_in_count=0,
    )

    assert await store.save(r1) is True
    assert await store.save(r2) is True

    # Filter parity: has_tool_calls must match for list/count.
    listed = await store.list(has_tool_calls=True, include_details=False)
    counted = await store.count(has_tool_calls=True)
    assert counted == len(listed) == 1
    assert listed[0].call_id == "call-2"
    # include_details=False should not hydrate the heavy fields.
    assert listed[0].conversation_history == []
    assert listed[0].tool_calls == []

    # Caller-name filter parity.
    listed = await store.list(caller_name="Ali", include_details=False)
    counted = await store.count(caller_name="Ali")
    assert counted == len(listed) == 1
    assert listed[0].call_id == "call-1"


@pytest.mark.asyncio
async def test_routing_method_round_trips(tmp_path, monkeypatch):
    """routing_method persists to DB and reads back unchanged."""
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "routing_method.db")

    from src.core.call_history import CallHistoryStore, CallRecord

    store = CallHistoryStore(db_path=db_path)

    now = datetime.now(timezone.utc)

    record = CallRecord(
        call_id="rm-1",
        caller_number="5550001",
        start_time=now,
        end_time=now + timedelta(seconds=30),
        duration_seconds=30.0,
        routing_method="ai_agent",
    )

    assert await store.save(record) is True

    fetched = await store.get_by_call_id("rm-1")
    assert fetched is not None
    assert fetched.routing_method == "ai_agent"


@pytest.mark.asyncio
async def test_routing_method_defaults_to_none(tmp_path, monkeypatch):
    """A record saved without routing_method reads back as None."""
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "routing_method_none.db")

    from src.core.call_history import CallHistoryStore, CallRecord

    store = CallHistoryStore(db_path=db_path)

    now = datetime.now(timezone.utc)

    record = CallRecord(
        call_id="rm-2",
        caller_number="5550002",
        start_time=now,
        end_time=now + timedelta(seconds=10),
        duration_seconds=10.0,
        # routing_method intentionally omitted — should default to None
    )

    assert await store.save(record) is True

    fetched = await store.get_by_call_id("rm-2")
    assert fetched is not None
    assert fetched.routing_method is None


