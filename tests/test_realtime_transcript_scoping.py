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


def _assistant_events(shape, response_id, item_id, first, second):
    common = {"response_id": response_id, "item_id": item_id}
    if shape == "output_audio_transcript":
        return [
            {
                **common,
                "type": "response.output_audio_transcript.delta",
                "delta": first,
            },
            {
                **common,
                "type": "response.output_audio_transcript.delta",
                "delta": second,
            },
            {**common, "type": "response.output_audio_transcript.done"},
        ]
    if shape == "audio_transcript":
        return [
            {**common, "type": "response.audio_transcript.delta", "delta": first},
            {**common, "type": "response.audio_transcript.delta", "delta": second},
            {**common, "type": "response.audio_transcript.done"},
        ]
    if shape == "response_delta":
        return [
            {
                **common,
                "type": "response.delta",
                "delta": {"type": "output_text.delta", "text": first},
            },
            {
                **common,
                "type": "response.delta",
                "delta": {"type": "output_text.delta", "text": second},
            },
            {
                **common,
                "type": "response.delta",
                "delta": {"type": "output_text.done"},
            },
        ]
    if shape == "output_text":
        return [
            {**common, "type": "response.output_text.delta", "delta": first},
            {**common, "type": "response.output_text.delta", "delta": second},
            {**common, "type": "response.output_text.done"},
        ]
    raise AssertionError(f"unknown event shape: {shape}")


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", [_openai_provider, _grok_provider])
@pytest.mark.parametrize("caller_position", ["before", "during", "after"])
@pytest.mark.parametrize(
    "assistant_shape",
    ["output_audio_transcript", "audio_transcript", "response_delta", "output_text"],
)
async def test_caller_final_never_clears_assistant_response(
    factory, caller_position, assistant_shape
):
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
    first, second, done = _assistant_events(
        assistant_shape,
        "resp-1",
        "assistant-item",
        "Let me think this through carefully for a moment.67",
        " 68 69 70 71 72 73 74 75",
    )

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
async def test_unscoped_terminal_event_finalizes_item_buffer(factory):
    emitted = []
    tracked = []

    async def on_event(event):
        emitted.append(event)

    provider = factory(on_event)

    async def track(role, text):
        tracked.append((role, text))

    provider._track_conversation = track
    await provider._emit_assistant_transcript(
        {"item_id": "assistant-item"}, "Complete response", is_final=False
    )
    await provider._emit_assistant_transcript({}, "", is_final=True)

    assert tracked == [("assistant", "Complete response")]
    assert [event["text"] for event in emitted if event["is_final"]] == [
        "Complete response"
    ]
    assert provider._assistant_transcript_buffers == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", [_openai_provider, _grok_provider])
async def test_terminal_response_separates_multiple_items(factory):
    emitted = []
    tracked = []

    async def on_event(event):
        emitted.append(event)

    provider = factory(on_event)

    async def track(role, text):
        tracked.append((role, text))

    provider._track_conversation = track
    await provider._emit_assistant_transcript(
        {"response_id": "resp-1", "item_id": "item-a"},
        "First item.",
        is_final=False,
    )
    await provider._emit_assistant_transcript(
        {"response_id": "resp-1", "item_id": "item-b"},
        "Second item.",
        is_final=False,
    )
    await provider._emit_assistant_transcript(
        {"response_id": "resp-1"}, "", is_final=True
    )

    assert tracked == [("assistant", "First item. Second item.")]
    assert [event["text"] for event in emitted if event["is_final"]] == [
        "First item. Second item."
    ]
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
