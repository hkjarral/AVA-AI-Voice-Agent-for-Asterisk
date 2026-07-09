import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.providers.deepgram import DeepgramProvider
from src.providers.elevenlabs_agent import ElevenLabsAgentProvider
from src.providers.google_live import GoogleLiveProvider
from src.providers.grok import GrokProvider
from src.providers.local import LocalProvider
from src.providers.openai_realtime import OpenAIRealtimeProvider


class _WebSocket:
    def __init__(self):
        self.state = SimpleNamespace(name="OPEN")
        self.send = AsyncMock()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_cls,provider_name", [
    (OpenAIRealtimeProvider, "OpenAI"),
    (GrokProvider, "Grok"),
])
async def test_realtime_providers_create_tools_disabled_voice_response(provider_cls, provider_name):
    provider = provider_cls.__new__(provider_cls)
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
async def test_google_live_uses_active_session_for_announcement():
    provider = GoogleLiveProvider.__new__(GoogleLiveProvider)
    provider._call_id = "call-google"
    provider._send_message = AsyncMock()

    assert await provider.speak_text("Are you still there?") is True
    payload = provider._send_message.await_args.args[0]
    text = payload["clientContent"]["turns"][0]["parts"][0]["text"]
    assert "Are you still there?" in text
    assert payload["clientContent"]["turnComplete"] is True


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
