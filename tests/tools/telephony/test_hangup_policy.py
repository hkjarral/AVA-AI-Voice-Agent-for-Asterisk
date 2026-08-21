import json

import pytest

from src.tools.telephony.hangup_policy import (
    HangupPolicyConfigError,
    dump_agent_hangup_policy,
    normalize_agent_hangup_policy,
    resolve_effective_hangup_policy,
    resolve_hangup_policy,
    text_contains_marker,
    text_contains_end_call_intent,
    text_is_short_polite_closing,
)


def test_agent_hangup_policy_inherits_by_default():
    resolved = resolve_effective_hangup_policy({}, None)
    assert resolved["source"] == "global"
    assert resolved["strategy"] == "inherit"
    assert "goodbye" in resolved["policy"]["markers"]["end_call"]


def test_agent_hangup_policy_extends_global_markers_without_mutation():
    tools = {"hangup_call": {"policy": {"markers": {"end_call": ["goodbye"]}}}}
    resolved = resolve_effective_hangup_policy(
        tools,
        {"strategy": "extend", "end_call": ["Да", "нет", "да"]},
    )
    assert resolved["source"] == "agent_extend"
    assert resolved["policy"]["markers"]["end_call"] == ["goodbye", "да", "нет"]
    assert tools["hangup_call"]["policy"]["markers"]["end_call"] == ["goodbye"]


def test_agent_hangup_policy_can_replace_global_markers():
    resolved = resolve_effective_hangup_policy(
        {}, {"strategy": "replace", "end_call": ["до свидания"]}
    )
    assert resolved["source"] == "agent_replace"
    assert resolved["policy"]["markers"]["end_call"] == ["до свидания"]
    assert resolved["marker_count"] == 1
    assert len(resolved["marker_digest"]) == 16


def test_agent_hangup_policy_dump_is_stable_and_inherit_is_null():
    assert dump_agent_hangup_policy(None) is None
    dumped = dump_agent_hangup_policy(
        {"strategy": "extend", "end_call": [" Да ", "нет"]}
    )
    assert json.loads(dumped) == {
        "strategy": "extend",
        "end_call": ["да", "нет"],
    }


@pytest.mark.parametrize(
    "value",
    [
        "not-json",
        [],
        {"strategy": "all", "end_call": ["bye"]},
        {"strategy": "inherit", "end_call": ["bye"]},
        {"strategy": "extend", "end_call": []},
        {"strategy": "replace", "end_call": [""]},
        {"strategy": "extend", "end_call": [123]},
        {"strategy": "extend", "end_call": ["x"], "unknown": True},
        {1: "not-a-string-key"},
    ],
)
def test_invalid_agent_hangup_policy_is_rejected(value):
    with pytest.raises(HangupPolicyConfigError):
        normalize_agent_hangup_policy(value)


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
    assert not text_is_short_polite_closing("No, thank you")
    assert not text_is_short_polite_closing("No thanks")
