import asyncio
import json
from unittest.mock import patch

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


class _SettingsWebSocket(_EventWebSocket):
    def __init__(self, events):
        super().__init__(events)
        self.messages = []
        self.state = type("_State", (), {"name": "OPEN"})()
        self.closed = False

    async def send(self, payload):
        self.messages.append(payload)

    async def close(self):
        self.closed = True
        self.state = type("_State", (), {"name": "CLOSED"})()


class _FailingSettingsWebSocket(_SettingsWebSocket):
    async def send(self, payload):
        raise RuntimeError("settings retry send failed")


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
    assert "functions" not in provider._last_settings_minimal["agent"]["think"]


@pytest.mark.asyncio
async def test_deepgram_settings_retry_preserves_declared_tools():
    provider = DeepgramProvider({}, LLMConfig(), None)
    provider.call_id = "deepgram-tools"
    provider.websocket = _CapturingWebSocket()
    provider._allowed_tools = ["transfer"]
    declared_tools = [
        {
            "name": "transfer",
            "description": "Transfer the caller",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    provider.tool_adapter.get_tools_config = lambda _allowed: declared_tools

    await provider._configure_agent()

    primary = json.loads(provider.websocket.messages[0])
    retry = provider._last_settings_minimal
    assert primary["agent"]["think"]["functions"] == declared_tools
    assert retry["agent"]["think"]["functions"] == declared_tools


@pytest.mark.asyncio
async def test_deepgram_settings_retry_can_ack_with_tools_intact():
    provider = DeepgramProvider({}, LLMConfig(), None)
    provider.call_id = "deepgram-retry-success"
    provider._ack_event = asyncio.Event()
    provider._last_settings_minimal = {
        "type": "Settings",
        "agent": {
            "think": {
                "functions": [{"name": "transfer"}],
            }
        },
    }
    provider.websocket = _SettingsWebSocket(
        [
            {"type": "Error", "description": "initial settings rejected"},
            {"type": "SettingsApplied"},
        ]
    )

    await provider._receive_loop()

    assert json.loads(provider.websocket.messages[0]) == provider._last_settings_minimal
    assert provider._settings_retry_attempted is True
    assert provider._settings_acked is True
    assert provider._ready_to_stream is True
    assert provider._ack_event.is_set()
    assert provider.websocket.closed is False


@pytest.mark.asyncio
async def test_deepgram_second_settings_rejection_fails_closed():
    provider = DeepgramProvider({}, LLMConfig(), None)
    provider.call_id = "deepgram-retry-rejected"
    provider._ack_event = asyncio.Event()
    provider._last_settings_minimal = {
        "type": "Settings",
        "agent": {
            "think": {
                "functions": [{"name": "transfer"}],
            }
        },
    }
    websocket = _SettingsWebSocket(
        [
            {"type": "Error", "description": "initial settings rejected"},
            {"type": "Error", "description": "retry rejected"},
        ]
    )
    provider.websocket = websocket

    await provider._receive_loop()
    stop_task = provider._settings_failure_stop_task
    assert stop_task is not None
    assert stop_task.done() is False
    await stop_task
    await asyncio.sleep(0)

    assert len(websocket.messages) == 1
    assert provider._settings_retry_attempted is True
    assert provider._settings_acked is False
    assert provider._ready_to_stream is False
    assert provider._ack_event.is_set() is False
    assert websocket.closed is True
    assert provider._settings_failure_stop_task is None


@pytest.mark.asyncio
async def test_deepgram_failed_settings_retry_send_tracks_stop_task():
    provider = DeepgramProvider({}, LLMConfig(), None)
    provider.call_id = "deepgram-retry-send-failed"
    provider._ack_event = asyncio.Event()
    provider._last_settings_minimal = {"type": "Settings"}
    websocket = _FailingSettingsWebSocket(
        [{"type": "Error", "description": "settings rejected"}]
    )
    provider.websocket = websocket

    await provider._receive_loop()
    stop_task = provider._settings_failure_stop_task
    assert stop_task is not None
    assert stop_task.done() is False
    await stop_task
    await asyncio.sleep(0)

    assert provider._settings_retry_attempted is True
    assert provider._settings_acked is False
    assert provider._ready_to_stream is False
    assert websocket.closed is True
    assert provider._settings_failure_stop_task is None


@pytest.mark.asyncio
async def test_deepgram_settings_rejection_logs_when_retry_is_unavailable():
    provider = DeepgramProvider({}, LLMConfig(), None)
    provider.call_id = "deepgram-retry-unavailable"
    provider._ack_event = asyncio.Event()
    websocket = _SettingsWebSocket(
        [{"type": "Error", "description": "settings rejected"}]
    )
    provider.websocket = websocket

    with patch("src.providers.deepgram.logger.error") as log_error:
        await provider._receive_loop()
        stop_task = provider._settings_failure_stop_task
        assert stop_task is not None
        assert stop_task.done() is False
        await stop_task
        await asyncio.sleep(0)

    messages = [call.args[0] for call in log_error.call_args_list]
    assert (
        "Deepgram Settings negotiation failed; retry unavailable; closing session"
        in messages
    )
    assert "Deepgram Settings negotiation failed after retry; closing session" not in messages
    assert provider._settings_retry_attempted is False
    assert provider._settings_acked is False
    assert provider._ready_to_stream is False
    assert provider._ack_event.is_set() is False
    assert websocket.closed is True
    assert provider._settings_failure_stop_task is None


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
