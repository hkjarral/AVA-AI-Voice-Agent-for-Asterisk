import time

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine, _ts_msg


@pytest.mark.asyncio
async def test_transcript_event_stamps_latency_timestamps():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()

    session = CallSession(call_id="call-latency", caller_channel_id="call-latency")
    await engine.session_store.upsert_call(session)

    await engine.on_provider_event(
        {
            "type": "transcript",
            "call_id": "call-latency",
            "text": "thank you",
        }
    )

    updated = await engine.session_store.get_by_call_id("call-latency")
    assert updated is not None
    assert updated.last_transcription_ts > 0.0
    assert updated.last_user_speech_end_ts > 0.0
    assert updated.conversation_history[-1]["role"] == "user"


@pytest.mark.asyncio
async def test_transcript_event_includes_timestamp():
    """Verify that user transcript events include a timestamp in conversation history."""
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()

    session = CallSession(call_id="call-ts", caller_channel_id="call-ts")
    await engine.session_store.upsert_call(session)

    before = time.time()
    await engine.on_provider_event(
        {
            "type": "transcript",
            "call_id": "call-ts",
            "text": "hello there",
        }
    )
    after = time.time()

    updated = await engine.session_store.get_by_call_id("call-ts")
    assert updated is not None
    entry = updated.conversation_history[-1]
    assert entry["role"] == "user"
    assert "timestamp" in entry, "conversation history entry must include a timestamp"
    assert before <= entry["timestamp"] <= after


@pytest.mark.asyncio
async def test_agent_transcript_event_includes_timestamp():
    """Verify that agent transcript events include a timestamp in conversation history."""
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()

    session = CallSession(call_id="call-agent-ts", caller_channel_id="call-agent-ts")
    await engine.session_store.upsert_call(session)

    before = time.time()
    await engine.on_provider_event(
        {
            "type": "agent_transcript",
            "call_id": "call-agent-ts",
            "text": "How can I help you?",
        }
    )
    after = time.time()

    updated = await engine.session_store.get_by_call_id("call-agent-ts")
    assert updated is not None
    entry = updated.conversation_history[-1]
    assert entry["role"] == "assistant"
    assert "timestamp" in entry, "agent transcript must include a timestamp"
    assert before <= entry["timestamp"] <= after


def test_ts_msg_helper_includes_timestamp():
    """Verify the _ts_msg helper always includes a timestamp and passes extra kwargs."""
    before = time.time()
    msg = _ts_msg("user", "test content")
    after = time.time()

    assert msg["role"] == "user"
    assert msg["content"] == "test content"
    assert before <= msg["timestamp"] <= after


def test_ts_msg_helper_extra_kwargs():
    """Verify _ts_msg passes through extra keyword arguments."""
    msg = _ts_msg("tool", "result text", tool_call_id="call_foo")
    assert msg["role"] == "tool"
    assert msg["content"] == "result text"
    assert msg["tool_call_id"] == "call_foo"
    assert "timestamp" in msg
