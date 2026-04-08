import asyncio
import base64
import json

import pytest

from src.config import LocalProviderConfig
from src.providers.local import LocalProvider


class _FakeWebSocket:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []
        self.state = type("State", (), {"name": "OPEN"})()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def send(self, message):
        self.sent.append(message)


class _GatewayFakeWebSocket(_FakeWebSocket):
    async def send(self, message):
        await super().send(message)
        try:
            payload = json.loads(message)
        except Exception:
            return
        if payload.get("type") != "llm_tool_request":
            return
        self._messages.append(
            json.dumps(
                {
                    "type": "llm_tool_response",
                    "call_id": payload.get("call_id", "call-gateway"),
                    "request_id": payload.get("request_id"),
                    "text": "Thanks for calling. Goodbye!",
                    "tool_calls": [
                        {
                            "name": "hangup_call",
                            "parameters": {"farewell_message": "Thanks for calling. Goodbye!"},
                        }
                    ],
                    "finish_reason": "tool_calls",
                    "tool_path": "structured",
                    "tool_parse_failures": 0,
                    "repair_attempts": 0,
                }
            )
        )


class _BargeAckFakeWebSocket(_FakeWebSocket):
    async def send(self, message):
        await super().send(message)
        try:
            payload = json.loads(message)
        except Exception:
            return
        if payload.get("type") != "barge_in":
            return
        self._messages.append(
            json.dumps(
                {
                    "type": "barge_in_ack",
                    "status": "ok",
                    "call_id": payload.get("call_id"),
                    "request_id": payload.get("request_id"),
                }
            )
        )


@pytest.mark.asyncio
async def test_binary_audio_emits_metadata_and_delayed_done():
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(), on_event=on_event)
    provider._active_call_id = "call-1"
    provider.websocket = _FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "tts_audio",
                    "call_id": "call-1",
                    "encoding": "mulaw",
                    "sample_rate_hz": 8000,
                    "byte_length": 800,
                }
            ),
            b"\x00" * 800,
        ]
    )

    await provider._receive_loop()

    agent_events = [e for e in events if e.get("type") == "AgentAudio"]
    done_events = [e for e in events if e.get("type") == "AgentAudioDone"]
    assert len(agent_events) == 1
    assert agent_events[0]["encoding"] == "mulaw"
    assert agent_events[0]["sample_rate"] == 8000
    assert done_events == []

    await asyncio.sleep(0.18)
    done_events = [e for e in events if e.get("type") == "AgentAudioDone"]
    assert len(done_events) == 1
    await provider.clear_active_call_id()


@pytest.mark.asyncio
async def test_tts_response_uses_payload_audio_format():
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(), on_event=on_event)
    provider._active_call_id = "call-2"
    audio_bytes = b"\x01\x02" * 400  # 800 bytes of linear16
    provider.websocket = _FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "tts_response",
                    "text": "hello",
                    "call_id": "call-2",
                    "audio_data": base64.b64encode(audio_bytes).decode("utf-8"),
                    "encoding": "linear16",
                    "sample_rate_hz": 16000,
                }
            )
        ]
    )

    await provider._receive_loop()
    await asyncio.sleep(0.10)

    agent_events = [e for e in events if e.get("type") == "AgentAudio"]
    done_events = [e for e in events if e.get("type") == "AgentAudioDone"]
    assert len(agent_events) == 1
    assert agent_events[0]["encoding"] == "linear16"
    assert agent_events[0]["sample_rate"] == 16000
    assert len(done_events) == 1
    await provider.clear_active_call_id()


@pytest.mark.asyncio
async def test_binary_audio_defaults_to_mulaw_when_metadata_missing():
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(), on_event=on_event)
    provider._active_call_id = "call-3"
    provider.websocket = _FakeWebSocket([b"\x7f" * 80])

    await provider._receive_loop()
    await asyncio.sleep(0.12)

    agent_events = [e for e in events if e.get("type") == "AgentAudio"]
    done_events = [e for e in events if e.get("type") == "AgentAudioDone"]
    assert len(agent_events) == 1
    assert agent_events[0]["encoding"] == "mulaw"
    assert agent_events[0]["sample_rate"] == 8000
    assert len(done_events) == 1
    await provider.clear_active_call_id()


@pytest.mark.asyncio
async def test_stt_result_updates_runtime_backend_for_whisper():
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(stt_backend="vosk"), on_event=on_event)
    provider._active_call_id = "call-whisper"
    provider.websocket = _FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "stt_result",
                    "call_id": "call-whisper",
                    "text": "hello",
                    "is_final": True,
                    "stt_backend": "faster_whisper",
                }
            )
        ]
    )

    await provider._receive_loop()

    assert provider.get_active_stt_backend() == "faster_whisper"
    assert provider.is_whisper_stt_active() is True


@pytest.mark.asyncio
async def test_status_response_updates_runtime_backend():
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(stt_backend="vosk"), on_event=on_event)
    provider._active_call_id = "call-status"
    provider.websocket = _FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "status_response",
                    "status": "ok",
                    "stt_backend": "whisper_cpp",
                    "tts_backend": "piper",
                    "models": {},
                }
            )
        ]
    )

    await provider._receive_loop()
    assert provider.get_active_stt_backend() == "whisper_cpp"
    assert provider.is_whisper_stt_active() is True


def test_stt_backend_falls_back_to_config_when_runtime_unknown():
    async def on_event(_event):
        return None

    provider = LocalProvider(LocalProviderConfig(stt_backend="sherpa"), on_event=on_event)
    assert provider.get_active_stt_backend() == "sherpa"
    assert provider.is_whisper_stt_active() is False


def test_local_tool_policy_auto_uses_capability_probe():
    async def on_event(_event):
        return None

    provider = LocalProvider(LocalProviderConfig(), on_event=on_event)
    provider._tool_capability = {"level": "strict"}
    assert provider._resolve_tool_policy() == "strict"
    provider._tool_capability = {"level": "partial"}
    assert provider._resolve_tool_policy() == "compatible"
    provider._tool_capability = {"level": "none"}
    assert provider._resolve_tool_policy() == "off"


def test_local_tool_policy_can_be_overridden_in_config():
    async def on_event(_event):
        return None

    provider = LocalProvider(LocalProviderConfig(tool_call_policy="strict"), on_event=on_event)
    provider._tool_capability = {"level": "none"}
    assert provider._resolve_tool_policy() == "strict"


@pytest.mark.asyncio
async def test_full_local_uses_structured_tool_gateway_for_tool_events():
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(tool_gateway_enabled=True), on_event=on_event)
    provider._mode = "full"
    provider._effective_tool_policy = "compatible"
    provider._allowed_tools = {"hangup_call"}
    provider._active_call_id = "call-gateway"
    provider.websocket = _GatewayFakeWebSocket(
        [
            json.dumps(
                {
                    "type": "llm_response",
                    "call_id": "call-gateway",
                    "text": "Goodbye and thank you.",
                }
            )
        ]
    )

    await provider._receive_loop()

    sent_payloads = [json.loads(msg) for msg in provider.websocket.sent]
    assert any(payload.get("type") == "llm_tool_request" for payload in sent_payloads)
    tool_events = [e for e in events if e.get("type") == "ToolCall"]
    assert len(tool_events) == 1
    assert tool_events[0]["tool_calls"][0]["name"] == "hangup_call"
    transcript_events = [e for e in events if e.get("type") == "agent_transcript"]
    assert transcript_events


@pytest.mark.asyncio
async def test_modular_mode_skips_structured_tool_gateway():
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(tool_gateway_enabled=True), on_event=on_event)
    provider._mode = "stt"
    provider._effective_tool_policy = "compatible"
    provider._allowed_tools = {"hangup_call"}
    provider._active_call_id = "call-modular"
    provider.websocket = _FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "llm_response",
                    "call_id": "call-modular",
                    "text": '<tool_call>{"name":"hangup_call","arguments":{"farewell_message":"Bye"}}'
                            "</tool_call>",
                }
            )
        ]
    )

    await provider._receive_loop()

    sent_payloads = [json.loads(msg) for msg in provider.websocket.sent]
    assert not any(payload.get("type") == "llm_tool_request" for payload in sent_payloads)
    tool_events = [e for e in events if e.get("type") == "ToolCall"]
    assert len(tool_events) == 1


@pytest.mark.asyncio
async def test_notify_barge_in_ack_roundtrip():
    async def on_event(_event):
        return None

    provider = LocalProvider(LocalProviderConfig(response_timeout_sec=0.2), on_event=on_event)
    provider._active_call_id = "call-barge-ack"
    provider.websocket = _BargeAckFakeWebSocket([])

    await provider.notify_barge_in("call-barge-ack")
    await provider._receive_loop()
    await asyncio.sleep(0.05)

    sent_payloads = [json.loads(msg) for msg in provider.websocket.sent]
    barge_payloads = [payload for payload in sent_payloads if payload.get("type") == "barge_in"]
    assert len(barge_payloads) == 1
    assert barge_payloads[0].get("request_id")
    assert provider._pending_barge_in_acks == {}


@pytest.mark.asyncio
async def test_notify_barge_in_ack_timeout_clears_pending():
    async def on_event(_event):
        return None

    provider = LocalProvider(LocalProviderConfig(response_timeout_sec=0.2), on_event=on_event)
    provider._active_call_id = "call-barge-timeout"
    provider.websocket = _FakeWebSocket([])

    await provider.notify_barge_in("call-barge-timeout")
    assert len(provider._pending_barge_in_acks) == 1

    await asyncio.sleep(0.45)
    assert provider._pending_barge_in_acks == {}


@pytest.mark.asyncio
async def test_multi_chunk_streaming_tts_produces_multiple_agent_audio():
    """Multiple tts_audio + binary pairs should produce multiple AgentAudio events."""
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(), on_event=on_event)
    provider._active_call_id = "call-multi"
    provider.websocket = _FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "tts_audio",
                    "call_id": "call-multi",
                    "encoding": "mulaw",
                    "sample_rate_hz": 8000,
                    "byte_length": 400,
                    "utterance_id": "utt-1",
                    "chunk_index": 0,
                    "is_final": False,
                }
            ),
            b"\x00" * 400,
            json.dumps(
                {
                    "type": "tts_audio",
                    "call_id": "call-multi",
                    "encoding": "mulaw",
                    "sample_rate_hz": 8000,
                    "byte_length": 400,
                    "utterance_id": "utt-1",
                    "chunk_index": 1,
                    "is_final": True,
                }
            ),
            b"\x00" * 400,
        ]
    )

    await provider._receive_loop()

    agent_events = [e for e in events if e.get("type") == "AgentAudio"]
    assert len(agent_events) == 2
    assert agent_events[0]["encoding"] == "mulaw"
    assert agent_events[1]["encoding"] == "mulaw"

    # Only one AgentAudioDone should fire (the last done timer wins)
    await asyncio.sleep(0.18)
    done_events = [e for e in events if e.get("type") == "AgentAudioDone"]
    assert len(done_events) == 1
    await provider.clear_active_call_id()


@pytest.mark.asyncio
async def test_multi_chunk_metadata_fields_are_backward_compatible():
    """tts_audio without utterance_id/chunk_index/is_final should still work (v1 compat)."""
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(LocalProviderConfig(), on_event=on_event)
    provider._active_call_id = "call-compat"
    provider.websocket = _FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "tts_audio",
                    "call_id": "call-compat",
                    "encoding": "mulaw",
                    "sample_rate_hz": 8000,
                    "byte_length": 200,
                }
            ),
            b"\x00" * 200,
        ]
    )

    await provider._receive_loop()

    agent_events = [e for e in events if e.get("type") == "AgentAudio"]
    assert len(agent_events) == 1

    await asyncio.sleep(0.18)
    done_events = [e for e in events if e.get("type") == "AgentAudioDone"]
    assert len(done_events) == 1
    await provider.clear_active_call_id()
