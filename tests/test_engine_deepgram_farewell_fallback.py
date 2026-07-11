import pytest

from src.core.models import CallSession
from src.engine import Engine
from src.tools.telephony.hangup_policy import normalize_hangup_policy


@pytest.fixture
def session():
    return CallSession(
        call_id="deepgram-farewell",
        caller_channel_id="deepgram-farewell",
        provider_name="deepgram",
    )


def test_explicit_end_intent_plus_assistant_farewell_arms_fallback(session):
    policy = normalize_hangup_policy({})

    assert Engine._track_deepgram_farewell_fallback(
        session,
        role="user",
        text="Okay. That's all. Thank you. Goodbye.",
        hangup_policy=policy,
    )
    assert not Engine._consume_deepgram_farewell_fallback(session)

    assert Engine._track_deepgram_farewell_fallback(
        session,
        role="assistant",
        text="Thanks for calling!",
        hangup_policy=policy,
    )
    assert Engine._consume_deepgram_farewell_fallback(session)
    assert "deepgram_farewell_fallback" not in session.vad_state


def test_casual_thanks_does_not_arm_fallback(session):
    policy = normalize_hangup_policy({})

    assert not Engine._track_deepgram_farewell_fallback(
        session,
        role="user",
        text="Thanks, how much does the Deepgram agent cost?",
        hangup_policy=policy,
    )
    assert not Engine._track_deepgram_farewell_fallback(
        session,
        role="assistant",
        text="Thanks for calling!",
        hangup_policy=policy,
    )
    assert not Engine._consume_deepgram_farewell_fallback(session)


def test_new_non_terminal_user_turn_clears_pending_fallback(session):
    policy = normalize_hangup_policy({})

    Engine._track_deepgram_farewell_fallback(
        session,
        role="user",
        text="That's all.",
        hangup_policy=policy,
    )
    assert Engine._track_deepgram_farewell_fallback(
        session,
        role="user",
        text="Actually, one more question.",
        hangup_policy=policy,
    )
    Engine._track_deepgram_farewell_fallback(
        session,
        role="assistant",
        text="Have a great day!",
        hangup_policy=policy,
    )
    assert not Engine._consume_deepgram_farewell_fallback(session)


def test_farewell_without_explicit_user_end_intent_does_not_hang_up(session):
    policy = normalize_hangup_policy({})

    Engine._track_deepgram_farewell_fallback(
        session,
        role="assistant",
        text="Thanks for calling!",
        hangup_policy=policy,
    )
    assert not Engine._consume_deepgram_farewell_fallback(session)
