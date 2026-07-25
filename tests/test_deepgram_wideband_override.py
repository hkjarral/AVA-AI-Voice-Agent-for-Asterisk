import asyncio
import json

import pytest

from src.config import LLMConfig
from src.providers.deepgram import DeepgramProvider


class _CapturingWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, payload):
        self.messages.append(payload)


class _EventWebSocket:
    def __init__(self, events):
        self._messages = iter(json.dumps(event) for event in events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._messages)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


@pytest.mark.asyncio
async def test_deepgram_session_uses_late_per_call_wideband_output_override():
    provider = DeepgramProvider(
        {
            "input_encoding": "mulaw",
            "input_sample_rate_hz": 8000,
            "output_encoding": "mulaw",
            "output_sample_rate_hz": 8000,
        },
        LLMConfig(),
        None,
    )

    # Engine._apply_provider_overrides runs after provider construction.
    provider.config.update({
        "input_encoding": "linear16",
        "input_sample_rate_hz": 16000,
        "output_encoding": "linear16",
        "output_sample_rate_hz": 16000,
    })
    provider.call_id = "deepgram-wideband"
    provider.websocket = _CapturingWebSocket()
    provider._allowed_tools = []
    provider._ack_event = asyncio.Event()
    provider._ack_event.set()

    await provider._configure_agent()

    settings = json.loads(provider.websocket.messages[0])
    assert settings["audio"] == {
        "input": {"encoding": "linear16", "sample_rate": 16000},
        "output": {
            "encoding": "linear16",
            "sample_rate": 16000,
            "container": "none",
        },
    }
    assert provider._dg_output_encoding == "linear16"
    assert provider._dg_output_rate == 16000
    assert provider._last_settings_minimal["audio"] == settings["audio"]


@pytest.mark.asyncio
async def test_deepgram_receive_loop_bridges_user_started_to_platform_playback_flush():
    events = []

    async def on_event(event):
        events.append(event)

    provider = DeepgramProvider({}, LLMConfig(), on_event)
    provider.call_id = "deepgram-barge-in"
    provider.websocket = _EventWebSocket([{"type": "UserStartedSpeaking"}])

    await provider._receive_loop()

    barge_in_event = next(event for event in events if event["type"] == "ProviderBargeIn")
    assert barge_in_event == {
        "type": "ProviderBargeIn",
        "call_id": "deepgram-barge-in",
        "provider": "deepgram",
        "event": "UserStartedSpeaking",
    }
    assert {"type": "UserStartedSpeaking"} in events


@pytest.mark.asyncio
async def test_deepgram_user_started_speaking_preserves_terminal_farewell():
    events = []

    async def on_event(event):
        events.append(event)

    provider = DeepgramProvider({}, LLMConfig(), on_event)
    provider.call_id = "deepgram-terminal-farewell"
    provider._terminal_turn_suppressed = True

    await provider._handle_user_started_speaking()

    assert events == []
