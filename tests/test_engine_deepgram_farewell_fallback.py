import pytest

from src.providers.deepgram import DeepgramProvider


def test_explicit_end_intent_plus_assistant_farewell_arms_fallback():
    state = DeepgramProvider.next_farewell_fallback_state(
        {},
        role="user",
        text="Okay. That's all. Thank you. Goodbye.",
    )
    assert state["pending"] is True
    assert state["farewell_seen"] is False

    state = DeepgramProvider.next_farewell_fallback_state(
        state,
        role="assistant",
        text="Thanks for calling!",
    )
    assert state["farewell_seen"] is True


def test_split_user_closing_and_split_assistant_farewell_match_live_sequence():
    state = DeepgramProvider.next_farewell_fallback_state(
        {}, role="user", text="No. That's all. Thank you."
    )
    state = DeepgramProvider.next_farewell_fallback_state(
        state, role="user", text="Goodbye."
    )
    for text in (
        "Thanks for calling!",
        "If you're in the US or Canada, you'll get a text with helpful links in just a moment.",
        "Have a great day!",
    ):
        state = DeepgramProvider.next_farewell_fallback_state(
            state, role="assistant", text=text
        )

    assert state["pending"] is True
    assert state["farewell_seen"] is True


def test_casual_thanks_does_not_arm_fallback():
    state = DeepgramProvider.next_farewell_fallback_state(
        {},
        role="user",
        text="Thanks, how much does the Deepgram agent cost?",
    )
    state = DeepgramProvider.next_farewell_fallback_state(
        state,
        role="assistant",
        text="Thanks for calling!",
    )
    assert state == {}


def test_new_non_terminal_user_turn_clears_pending_fallback():
    state = DeepgramProvider.next_farewell_fallback_state(
        {}, role="user", text="That's all."
    )
    state = DeepgramProvider.next_farewell_fallback_state(
        state, role="user", text="Actually, one more question."
    )
    state = DeepgramProvider.next_farewell_fallback_state(
        state, role="assistant", text="Have a great day!"
    )
    assert state == {}


def test_farewell_without_explicit_user_end_intent_does_not_arm_fallback():
    state = DeepgramProvider.next_farewell_fallback_state(
        {}, role="assistant", text="Thanks for calling!"
    )
    assert state == {}


def test_provider_does_not_consume_fallback_when_hangup_tool_already_arrived():
    provider = DeepgramProvider.__new__(DeepgramProvider)
    provider._farewell_fallback_state = {"pending": True, "farewell_seen": True}
    provider._hangup_pending = True

    assert provider._consume_farewell_fallback() is False


def test_provider_consumes_missed_tool_fallback_once():
    provider = DeepgramProvider.__new__(DeepgramProvider)
    provider._farewell_fallback_state = {"pending": True, "farewell_seen": True}
    provider._hangup_pending = False

    assert provider._consume_farewell_fallback() is True
    assert provider._consume_farewell_fallback() is False


@pytest.mark.asyncio
async def test_provider_emits_hangup_ready_at_audio_boundary():
    events = []

    async def on_event(event):
        events.append(event)

    provider = DeepgramProvider.__new__(DeepgramProvider)
    provider.call_id = "call-live-sequence"
    provider.on_event = on_event
    provider._farewell_fallback_state = {"pending": True, "farewell_seen": True}
    provider._hangup_pending = False

    assert await provider._emit_farewell_fallback_if_needed() is True
    assert events == [
        {
            "type": "HangupReady",
            "call_id": "call-live-sequence",
            "reason": "farewell_without_tool",
            "had_audio": True,
        }
    ]
