from src.tools.telephony.hangup_policy import (
    resolve_hangup_policy,
    text_contains_marker,
    text_contains_end_call_intent,
    text_is_short_polite_closing,
)


def test_default_end_call_markers_include_natural_closing_phrases():
    policy = resolve_hangup_policy({})
    markers = (policy.get("markers") or {}).get("end_call", [])

    assert "thank you" in markers
    assert "thanks" in markers
    assert "have a good day" in markers


def test_end_call_detection_matches_polite_goodbye_phrase():
    policy = resolve_hangup_policy({})
    markers = (policy.get("markers") or {}).get("end_call", [])

    assert text_contains_marker("Okay. Thank you. Have a good day.", markers)


def test_end_call_detection_does_not_trigger_on_unrelated_text():
    policy = resolve_hangup_policy({})
    markers = (policy.get("markers") or {}).get("end_call", [])

    assert not text_contains_marker("Can you explain setup pricing again?", markers)


def test_end_call_detection_handles_common_stt_misrecognitions():
    policy = resolve_hangup_policy({})
    markers = (policy.get("markers") or {}).get("end_call", [])

    assert text_contains_end_call_intent("Okay, hand up the call.", markers)
    assert text_contains_end_call_intent("Please and the call now.", markers)


def test_short_polite_closing_detection_accepts_terminal_thanks():
    assert text_is_short_polite_closing("Okay, thank you")
    assert text_is_short_polite_closing("thanks goodbye")
    assert text_is_short_polite_closing("No. Leave it. Thank you.")
    assert text_is_short_polite_closing("That's all, thank you")


def test_short_polite_closing_detection_rejects_long_mid_call_phrases():
    assert not text_is_short_polite_closing("Thanks, can you also explain pricing and setup again")
    assert not text_is_short_polite_closing("No thank you, but please tell me about pricing")
