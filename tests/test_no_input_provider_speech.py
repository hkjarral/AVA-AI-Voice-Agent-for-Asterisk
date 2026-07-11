import json
import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.providers.deepgram import DeepgramProvider
from src.providers.elevenlabs_agent import ElevenLabsAgentProvider
from src.providers.google_live import GoogleLiveProvider
from src.providers.grok import GrokProvider
from src.providers.local import LocalProvider
from src.providers.openai_realtime import OpenAIRealtimeProvider
from src.providers.elevenlabs_config import ElevenLabsAgentConfig
from src.config import LLMConfig


class _WebSocket:
    def __init__(self):
        self.state = SimpleNamespace(name="OPEN")
        self.send = AsyncMock()


@pytest.mark.asyncio
async def test_openai_realtime_creates_tools_disabled_voice_response():
    provider = OpenAIRealtimeProvider.__new__(OpenAIRealtimeProvider)
    provider.websocket = _WebSocket()
    provider._call_id = "call-1"
    provider.config = SimpleNamespace(api_version="ga")
    provider._pending_response = False
    provider._send_json = AsyncMock()

    assert await provider.speak_text("Are you still there?") is True
    payload = provider._send_json.await_args.args[0]
    assert payload["type"] == "response.create"
    assert payload["response"]["tools"] == []
    assert "Are you still there?" in payload["response"]["instructions"]
    assert provider._pending_response is True


@pytest.mark.asyncio
async def test_grok_uses_force_message_for_exact_no_input_announcement():
    provider = GrokProvider.__new__(GrokProvider)
    provider.websocket = _WebSocket()
    provider._call_id = "call-grok"
    provider._pending_response = False
    provider._send_json = AsyncMock()

    assert await provider.speak_text("Are you still there?") is True
    payload = provider._send_json.await_args.args[0]
    assert payload["type"] == "conversation.item.create"
    assert payload["item"] == {
        "type": "force_message",
        "content": [{"type": "input_text", "text": "Are you still there?"}],
        "interruptible": True,
    }
    assert provider._pending_response is True


@pytest.mark.asyncio
async def test_google_live_uses_active_session_for_announcement():
    provider = GoogleLiveProvider.__new__(GoogleLiveProvider)
    provider._call_id = "call-google"
    provider.websocket = _WebSocket()
    provider._send_message = AsyncMock(return_value=True)

    assert await provider.speak_text("Are you still there?") is True
    payload = provider._send_message.await_args.args[0]
    text = payload["clientContent"]["turns"][0]["parts"][0]["text"]
    assert "Are you still there?" in text
    assert payload["clientContent"]["turnComplete"] is True


@pytest.mark.asyncio
async def test_google_live_rejects_announcement_when_websocket_is_closed():
    provider = GoogleLiveProvider.__new__(GoogleLiveProvider)
    provider._call_id = "call-google-closed"
    provider.websocket = _WebSocket()
    provider.websocket.state.name = "CLOSED"
    provider._send_message = AsyncMock(return_value=True)

    assert await provider.speak_text("Are you still there?") is False
    provider._send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_deepgram_injects_an_agent_message():
    provider = DeepgramProvider.__new__(DeepgramProvider)
    provider.websocket = _WebSocket()

    assert await provider.speak_text("Are you still there?") is True
    payload = json.loads(provider.websocket.send.await_args.args[0])
    assert payload == {"type": "InjectAgentMessage", "content": "Are you still there?"}


@pytest.mark.asyncio
async def test_elevenlabs_injects_system_user_message_for_agent_voice():
    provider = ElevenLabsAgentProvider.__new__(ElevenLabsAgentProvider)
    provider._ws = _WebSocket()
    provider._connected = True
    provider._call_id = "call-eleven"

    assert await provider.speak_text("Are you still there?") is True
    payload = json.loads(provider._ws.send.await_args.args[0])
    assert payload["type"] == "user_message"
    assert "Are you still there?" in payload["text"]


@pytest.mark.asyncio
async def test_elevenlabs_response_complete_emits_real_audio_boundary():
    events = []

    async def on_event(event):
        events.append(event)

    provider = ElevenLabsAgentProvider(
        ElevenLabsAgentConfig(agent_id="agent-test"),
        on_event,
    )
    provider._call_id = "call-eleven-boundary"
    audio = base64.b64encode(b"\x00\x00" * 160).decode()

    await provider._handle_message(json.dumps({
        "type": "audio",
        "audio_event": {"audio_base_64": audio},
    }))
    await provider._handle_message(json.dumps({
        "type": "agent_response_complete",
        "agent_response_complete_event": {"event_id": "evt-1"},
    }))

    assert [event["type"] for event in events] == ["AgentAudio", "AgentAudioDone"]
    assert events[-1]["provider_event_id"] == "evt-1"
    assert provider._in_audio_burst is False


@pytest.mark.asyncio
async def test_elevenlabs_audio_idle_fallback_emits_boundary_without_client_event():
    events = []

    async def on_event(event):
        events.append(event)

    provider = ElevenLabsAgentProvider(
        ElevenLabsAgentConfig(agent_id="agent-test"),
        on_event,
    )
    provider._call_id = "call-eleven-idle"
    provider._in_audio_burst = True
    provider._last_audio_monotonic = 1.0
    provider._schedule_audio_idle_completion(idle_sec=0.01)
    await asyncio.sleep(0.55)

    assert events[-1]["type"] == "AgentAudioDone"
    assert events[-1]["reason"] == "audio_idle_fallback"


class _MessageWebSocket:
    def __init__(self, messages):
        self._messages = list(messages)

    def __aiter__(self):
        self._iterator = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.mark.asyncio
async def test_deepgram_control_text_does_not_split_audio_burst():
    events = []

    async def on_event(event):
        events.append(event)

    provider = DeepgramProvider(
        {"output_encoding": "mulaw", "output_sample_rate_hz": 8000},
        LLMConfig(),
        on_event,
    )
    provider.call_id = "call-deepgram-boundary"
    provider.websocket = _MessageWebSocket([
        b"\xff" * 160,
        json.dumps({"type": "ConversationText", "role": "assistant", "text": "Hello"}),
        b"\xff" * 160,
        json.dumps({"type": "AgentAudioDone"}),
    ])

    await provider._receive_loop()

    event_types = [event.get("type") for event in events]
    assert event_types.count("AgentAudioDone") == 1
    media_lifecycle = [
        event_type
        for event_type in event_types
        if event_type in {"AgentAudio", "ConversationText", "AgentAudioDone"}
    ]
    assert media_lifecycle == ["AgentAudio", "ConversationText", "AgentAudio", "AgentAudioDone"]


@pytest.mark.asyncio
async def test_deepgram_hangup_tool_uses_canonical_farewell_and_arms_fallback():
    provider = DeepgramProvider(
        {"output_encoding": "mulaw", "output_sample_rate_hz": 8000},
        LLMConfig(),
        AsyncMock(),
    )
    provider.call_id = "call-deepgram-tool"
    provider.tool_adapter.handle_tool_call_event = AsyncMock(return_value={
        "function_call_id": "tool-1",
        "function_name": "hangup_call",
        "status": "success",
        "message": "Goodbye from Deepgram.",
        "will_hangup": True,
    })
    provider.tool_adapter.send_tool_result = AsyncMock()

    await provider._handle_function_call({
        "type": "FunctionCallRequest",
        "functions": [{"id": "tool-1", "name": "hangup_call", "arguments": "{}"}],
    })

    assert provider._hangup_pending is True
    assert provider._hangup_audio_started is False
    assert provider._farewell_message == "Goodbye from Deepgram."
    assert provider._hangup_fallback_task is not None
    provider._cancel_hangup_audio_fallback()


@pytest.mark.asyncio
async def test_local_provider_requests_tts_for_active_call():
    provider = LocalProvider.__new__(LocalProvider)
    provider.websocket = _WebSocket()
    provider._active_call_id = "call-local"

    assert await provider.speak_text("Are you still there?") is True
    payload = json.loads(provider.websocket.send.await_args.args[0])
    assert payload == {
        "type": "tts_request",
        "text": "Are you still there?",
        "call_id": "call-local",
    }

    provider.speak_text = AsyncMock(return_value=True)
    assert await provider.speak("Still there?") is True
