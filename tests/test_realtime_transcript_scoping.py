import pytest

from src.config import GrokProviderConfig, OpenAIRealtimeProviderConfig
from src.providers.grok import GrokProvider
from src.providers.openai_realtime import OpenAIRealtimeProvider


def _openai_provider(on_event):
    provider = OpenAIRealtimeProvider(
        OpenAIRealtimeProviderConfig(api_key="test"), on_event=on_event
    )
    provider._call_id = "call-transcript"
    provider._greeting_completed = True
    return provider


def _grok_provider(on_event):
    provider = GrokProvider(
        GrokProviderConfig(api_key="test"),
        on_event=on_event,
        provider_key="grok",
    )
    provider._call_id = "call-transcript"
    provider._greeting_completed = True
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", [_openai_provider, _grok_provider])
@pytest.mark.parametrize("caller_position", ["before", "during", "after"])
async def test_caller_final_never_clears_assistant_response(factory, caller_position):
    emitted = []
    tracked = []

    async def on_event(event):
        emitted.append(event)

    provider = factory(on_event)

    async def track(role, text):
        tracked.append((role, text))

    provider._track_conversation = track
    caller = {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "caller-item",
        "transcript": "Forget it. Count from six to seven.",
    }
    first = {
        "type": "response.output_audio_transcript.delta",
        "response_id": "resp-1",
        "item_id": "assistant-item",
        "delta": "Let me think this through carefully for a moment.67",
    }
    second = {
        "type": "response.output_audio_transcript.delta",
        "response_id": "resp-1",
        "item_id": "assistant-item",
        "delta": " 68 69 70 71 72 73 74 75",
    }
    done = {
        "type": "response.output_audio_transcript.done",
        "response_id": "resp-1",
        "item_id": "assistant-item",
    }

    if caller_position == "before":
        sequence = [caller, first, second, done]
    elif caller_position == "during":
        sequence = [first, caller, second, done]
    else:
        sequence = [first, second, done, caller]
    for event in sequence:
        await provider._handle_event(event)

    assistant = "Let me think this through carefully for a moment.67 68 69 70 71 72 73 74 75"
    assert ("assistant", assistant) in tracked
    assert ("user", caller["transcript"]) in tracked
    assert [e["text"] for e in emitted if e["is_final"]] == (
        [caller["transcript"], assistant]
        if caller_position != "after"
        else [assistant, caller["transcript"]]
    )
    assert provider._assistant_transcript_buffers == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", [_openai_provider, _grok_provider])
async def test_terminal_response_finalizes_only_matching_response(factory):
    emitted = []
    tracked = []

    async def on_event(event):
        emitted.append(event)

    provider = factory(on_event)

    async def track(role, text):
        tracked.append((role, text))

    provider._track_conversation = track
    await provider._emit_assistant_transcript(
        {"response_id": "resp-cancelled", "item_id": "item-a"},
        "Partial audible response",
        is_final=False,
    )
    await provider._emit_assistant_transcript(
        {"response": {"id": "resp-cancelled"}}, "", is_final=True
    )
    await provider._emit_assistant_transcript(
        {"response_id": "resp-next", "item_id": "item-b"},
        "Next complete response",
        is_final=False,
    )
    await provider._emit_assistant_transcript(
        {"response_id": "resp-next", "item_id": "item-b"}, "", is_final=True
    )

    assert tracked == [
        ("assistant", "Partial audible response"),
        ("assistant", "Next complete response"),
    ]
    assert [e["text"] for e in emitted if e["is_final"]] == [
        "Partial audible response",
        "Next complete response",
    ]
    assert provider._assistant_transcript_buffers == {}
