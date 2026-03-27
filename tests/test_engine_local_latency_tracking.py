import time

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine, _ts_msg, _sanitize_for_llm


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


def test_ts_msg_rejects_timestamp_override():
    """Verify _ts_msg ignores caller-supplied timestamp to preserve invariants."""
    msg = _ts_msg("user", "hello", timestamp=0)
    assert msg["timestamp"] != 0, "caller must not override the auto-generated timestamp"
    assert msg["timestamp"] > 0


def test_sanitize_for_llm_strips_timestamp():
    """Verify _sanitize_for_llm removes non-standard keys before LLM adapter."""
    history = [
        {"role": "user", "content": "hi", "timestamp": 1234567890.0},
        {"role": "assistant", "content": "hello", "timestamp": 1234567891.0},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}], "timestamp": 1234567892.0},
        {"role": "tool", "content": "ok", "tool_call_id": "c1", "timestamp": 1234567893.0},
    ]
    sanitized = _sanitize_for_llm(history)
    for msg in sanitized:
        assert "timestamp" not in msg, f"timestamp leaked into LLM message: {msg}"
    assert sanitized[0] == {"role": "user", "content": "hi"}
    assert sanitized[2] == {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]}
    assert sanitized[3] == {"role": "tool", "content": "ok", "tool_call_id": "c1"}


def test_sanitize_for_llm_skips_malformed_entries():
    """Verify _sanitize_for_llm skips non-dict entries and entries missing role."""
    history = [
        {"role": "user", "content": "hi", "timestamp": 1.0},
        "not a dict",
        None,
        {"content": "missing role", "timestamp": 2.0},
        {"role": "assistant", "content": "ok"},
    ]
    sanitized = _sanitize_for_llm(history)
    assert len(sanitized) == 2
    assert sanitized[0] == {"role": "user", "content": "hi"}
    assert sanitized[1] == {"role": "assistant", "content": "ok"}
