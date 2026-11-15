from __future__ import annotations

import pytest

from src.config import LocalProviderConfig
from src.providers.local import LocalProvider
from src.tools.telephony.hangup_policy import resolve_effective_hangup_policy


def test_transfer_intent_detector():
    assert LocalProvider._looks_like_transfer_intent("Can you transfer me to support?")
    assert LocalProvider._looks_like_transfer_intent("Please connect me to extension 7000")
    assert not LocalProvider._looks_like_transfer_intent("How much GPU memory does this need?")
    assert not LocalProvider._looks_like_transfer_intent("What is the setup process?")


def test_extract_hangup_farewell_from_tool_call():
    tool_calls = [
        {"name": "hangup_call", "parameters": {"farewell_message": "It was a pleasure assisting you. Goodbye!"}}
    ]
    assert (
        LocalProvider._extract_hangup_farewell(tool_calls)
        == "It was a pleasure assisting you. Goodbye!"
    )


def test_extract_hangup_farewell_strips_tool_chatter():
    tool_calls = [
        {"name": "hangup_call", "parameters": {"farewell_message": "Goodbye! (hangup_call tool executed)"}}
    ]
    assert LocalProvider._extract_hangup_farewell(tool_calls) == "Goodbye!"


@pytest.mark.asyncio
async def test_emit_local_llm_result_uses_hangup_farewell_and_drops_transfer():
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(), on_event)
    provider._allowed_tools = {"hangup_call", "live_agent_transfer"}
    provider._last_user_transcript_by_call["call-1"] = "That's all, thank you."

    await provider._emit_local_llm_result(
        call_id="call-1",
        llm_text=(
            "It was a pleasure assisting you. Goodbye! "
            "Hangup call successful. Call duration: 1 minute and 23 seconds."
        ),
        clean_text=(
            "It was a pleasure assisting you. Goodbye! "
            "Hangup call successful. Call duration: 1 minute and 23 seconds."
        ),
        tool_calls=[
            {"name": "live_agent_transfer", "parameters": {"target": "support"}},
            {"name": "hangup_call", "parameters": {"farewell_message": "It was a pleasure assisting you. Goodbye!"}},
        ],
        tool_path="structured",
    )

    transcript_event = next(event for event in events if event.get("type") == "agent_transcript")
    assert transcript_event["text"] == "It was a pleasure assisting you. Goodbye!"

    tool_event = next(event for event in events if event.get("type") == "ToolCall")
    assert [call["name"] for call in tool_event["tool_calls"]] == ["hangup_call"]


@pytest.mark.asyncio
async def test_emit_local_llm_result_uses_clean_goodbye_when_hangup_has_no_farewell():
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(), on_event)
    provider._allowed_tools = {"hangup_call"}
    provider._last_user_transcript_by_call["call-2"] = "Thank you. Goodbye."

    await provider._emit_local_llm_result(
        call_id="call-2",
        llm_text="Goodbye! (hangup_call tool executed)",
        clean_text="Goodbye! (hangup_call tool executed)",
        tool_calls=[{"name": "hangup_call", "parameters": {}}],
        tool_path="repair",
    )

    transcript_event = next(event for event in events if event.get("type") == "agent_transcript")
    assert transcript_event["text"] == "Goodbye!"


@pytest.mark.asyncio
async def test_emit_local_llm_result_assigns_unique_ids_and_preserves_upstream_id():
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(), on_event)
    provider._allowed_tools = {"request_transcript"}

    first_call = {"name": "request_transcript", "parameters": {}}
    second_call = {"name": "request_transcript", "parameters": {}}
    upstream_call = {
        "id": "local-upstream-1",
        "name": "request_transcript",
        "parameters": {},
    }
    for tool_call in (first_call, second_call, upstream_call):
        await provider._emit_local_llm_result(
            call_id="call-ids",
            llm_text="",
            clean_text="",
            tool_calls=[tool_call],
            tool_path="structured",
        )

    tool_ids = [
        event["tool_calls"][0]["id"]
        for event in events
        if event.get("type") == "ToolCall"
    ]
    assert tool_ids[0].startswith("generated-")
    assert tool_ids[1].startswith("generated-")
    assert tool_ids[0] != tool_ids[1]
    assert tool_ids[2] == "local-upstream-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_text, expected_hangup",
    [
        ("Can you hand off to a real person?", False),
        ("I was on hold and the call dropped", False),
        ("Please leave a message and call me back", False),
        ("Please end the call now.", True),
        ("Please and the call now.", True),
        ("Okay, hand up the call.", True),
    ],
)
async def test_policy_off_hangup_heuristic_requires_caller_command(user_text, expected_hangup):
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(), on_event)
    provider._allowed_tools = {"hangup_call"}
    provider._effective_tool_policy = "off"
    provider._last_user_transcript_by_call["call-intent"] = user_text

    await provider._process_llm_text_fallback(
        call_id="call-intent", llm_text="Thanks for calling!"
    )

    tool_calls = [
        tool_call
        for event in events
        if event.get("type") == "ToolCall"
        for tool_call in event["tool_calls"]
    ]
    assert [call["name"] for call in tool_calls] == (["hangup_call"] if expected_hangup else [])
    assert any(event.get("type") == "agent_transcript" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_text, expected_hangup",
    [
        ("Please and the call now.", True),
        ("Please end the call now.", True),
        ("Please leave a message and call me back.", False),
        ("I was on hold and the call dropped.", False),
    ],
)
async def test_policy_off_heuristic_preserves_agent_marker_modifiers(user_text, expected_hangup):
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(), on_event)
    provider._allowed_tools = {"hangup_call"}
    provider._effective_tool_policy = "off"
    provider._hangup_policy = resolve_effective_hangup_policy(
        {}, {"strategy": "replace", "end_call": ["end call now"]}
    )["policy"]
    provider._last_user_transcript_by_call["call-modifiers"] = user_text

    await provider._process_llm_text_fallback(
        call_id="call-modifiers", llm_text="Thanks for calling!"
    )

    calls = [
        call
        for event in events
        if event.get("type") == "ToolCall"
        for call in event["tool_calls"]
    ]
    assert [call["name"] for call in calls] == (["hangup_call"] if expected_hangup else [])
