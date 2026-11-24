import asyncio
import json

import pytest

from src.config import AppConfig, DeepgramProviderConfig
from src.pipelines import deepgram_flux as flux_module
from src.pipelines.deepgram_flux import DeepgramFluxSTTAdapter


class _MockFluxWebSocket:
    def __init__(self):
        self.closed = False
        self._queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self._queue.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def send(self, data):
        return None

    async def close(self):
        self.closed = True
        await self._queue.put(None)


def _app_config():
    return AppConfig(
        default_provider="deepgram",
        providers={
            "deepgram": {
                "api_key": "test-key",
                "base_url": "https://api.deepgram.com",
                "input_encoding": "linear16",
                "input_sample_rate_hz": 8000,
            }
        },
        asterisk={"host": "127.0.0.1", "username": "u", "password": "p"},
        llm={"initial_greeting": "", "prompt": "prompt", "model": "gpt-4o"},
    )


@pytest.mark.asyncio
async def test_flux_connection_and_start_use_the_same_engine_audio_format(monkeypatch):
    config = _app_config()
    provider = DeepgramProviderConfig(**config.providers["deepgram"])
    adapter = DeepgramFluxSTTAdapter(
        "deepgram_flux_stt",
        config,
        provider,
        {"sample_rate": 8000, "encoding": "mulaw"},
    )
    captured = {}

    async def fake_connect(url, **kwargs):
        captured["url"] = url
        return _MockFluxWebSocket()

    monkeypatch.setattr(flux_module.websockets, "connect", fake_connect)
    runtime_options = {
        "sample_rate": 16000,
        "encoding": "linear16",
        "stream_format": "pcm16_16k",
    }
    await adapter.open_call("call-flux", runtime_options)
    await adapter.start_stream(
        "call-flux",
        runtime_options,
        sample_rate_hz=16000,
        fmt="pcm16_16k",
    )

    assert "/v2/listen" in captured["url"]
    assert "encoding=linear16" in captured["url"]
    assert "sample_rate=16000" in captured["url"]
    await adapter.close_call("call-flux")


async def _open_flux(monkeypatch):
    socket = _MockFluxWebSocket()
    async def connect(*args, **kwargs):
        return socket
    monkeypatch.setattr(flux_module.websockets, "connect", connect)
    adapter = DeepgramFluxSTTAdapter("deepgram_flux_stt", None, DeepgramProviderConfig(api_key="synthetic", model="flux-general-en"))
    await adapter.open_call("call-flux", {})
    return adapter, socket, adapter._sessions["call-flux"]


async def _finish_remote(socket, session):
    await socket._queue.put(None)
    await asyncio.wait_for(session.receiver_task, 1)


@pytest.mark.asyncio
async def test_native_turn_info_emits_only_final_turn_and_deduplicates_turn_index(monkeypatch):
    adapter, socket, session = await _open_flux(monkeypatch)
    events = [
        {"event": "StartOfTurn", "transcript": ""},
        {"event": "Update", "transcript": "provisional", "is_final": True},
        {"event": "EagerEndOfTurn", "transcript": "speculative"},
        {"event": "TurnResumed", "transcript": "continued"},
        {"event": "EndOfTurn", "transcript": "First final answer."},
        {"event": "EndOfTurn", "transcript": "First final answer."},
        {"event": "Update", "transcript": "later unfinished", "turn_index": 1},
        {"event": "EndOfTurn", "transcript": " First final answer. ", "turn_index": 1},
        {"event": "EndOfTurn", "transcript": "", "turn_index": 2},
    ]
    for sequence, event in enumerate(events):
        await socket._queue.put(json.dumps({"type": "TurnInfo", "turn_index": 0, "sequence_id": sequence, **event}))
    await _finish_remote(socket, session)
    assert [text async for text in adapter.iter_results("call-flux")] == ["First final answer.", "First final answer."]
    assert session.turn_complete_event.is_set()
    await adapter.close_call("call-flux")


@pytest.mark.asyncio
async def test_native_turn_resumed_does_not_commit_eager_transcript(monkeypatch):
    adapter, socket, session = await _open_flux(monkeypatch)
    session.turn_complete_event.set()
    for event in ("StartOfTurn", "Update", "EagerEndOfTurn", "TurnResumed"):
        await socket._queue.put(json.dumps({"type": "TurnInfo", "event": event, "turn_index": 0, "transcript": "unfinished"}))
    await _finish_remote(socket, session)
    assert [text async for text in adapter.iter_results("call-flux")] == []
    assert not session.turn_complete_event.is_set()
    await adapter.close_call("call-flux")


@pytest.mark.asyncio
async def test_remote_disconnect_with_full_final_transcript_queue_finishes_after_drain(monkeypatch):
    adapter, socket, session = await _open_flux(monkeypatch)
    for turn in range(9):
        await socket._queue.put(json.dumps({"type": "TurnInfo", "event": "EndOfTurn", "turn_index": turn, "transcript": f"Turn {turn}"}))
    await _finish_remote(socket, session)
    async def consume():
        return [text async for text in adapter.iter_results("call-flux")]
    assert await asyncio.wait_for(consume(), 0.1) == [f"Turn {turn}" for turn in range(1, 9)]
    await adapter.close_call("call-flux")


@pytest.mark.asyncio
async def test_legacy_results_and_end_of_turn_remain_compatible(monkeypatch):
    adapter, socket, session = await _open_flux(monkeypatch)
    for message in [
        {"type": "Results", "is_final": False, "channel": {"alternatives": [{"transcript": "partial"}]}},
        {"type": "Results", "is_final": True, "channel": {"alternatives": [{"transcript": "legacy final"}]}},
        {"type": "EndOfTurn"},
    ]:
        await socket._queue.put(json.dumps(message))
    await _finish_remote(socket, session)
    assert [text async for text in adapter.iter_results("call-flux")] == ["legacy final"]
    assert session.turn_complete_event.is_set()
    await adapter.close_call("call-flux")


@pytest.mark.asyncio
async def test_call_close_releases_blocked_result_consumer(monkeypatch):
    adapter, socket, session = await _open_flux(monkeypatch)
    async def consume():
        return [text async for text in adapter.iter_results("call-flux")]
    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await adapter.close_call("call-flux")
    assert await asyncio.wait_for(consumer, 0.1) == []
    assert socket.closed and session.receiver_task.done() and not adapter._sessions
    await adapter.close_call("call-flux")


@pytest.mark.asyncio
async def test_reopening_same_call_closes_previous_socket_and_receiver(monkeypatch):
    adapter, old_socket, old_session = await _open_flux(monkeypatch)
    replacement = _MockFluxWebSocket()
    async def connect(*args, **kwargs):
        return replacement
    monkeypatch.setattr(flux_module.websockets, "connect", connect)
    await adapter.open_call("call-flux", {})
    assert old_socket.closed and old_session.receiver_task.done()
    assert not old_session.active
    assert adapter._sessions["call-flux"].websocket is replacement
    await adapter.close_call("call-flux")
    assert replacement.closed
