"""Issue 808: model Asterisk's clock separately from the application's sender."""
import asyncio
import base64
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import OpenAIRealtimeProviderConfig, GrokProviderConfig, WebSocketMediaConfig
from src.engine import Engine
from src.providers.openai_realtime import OpenAIRealtimeProvider
from src.providers.grok import GrokProvider
from src.core.streaming_playback_manager import _JITTER_SENTINEL
import src.core.streaming_playback_manager as playback
from tests.test_websocket_streaming_playback import manager


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,starves", [("legacy", True), ("asterisk", False)])
async def test_independently_clocked_consumer_keeps_buffered_audio_under_scheduler_delay(monkeypatch, mode, starves):
    mgr, server, store = manager("slin", 8000)
    mgr.websocket_output_pacing = mode
    await store.upsert_call(playback.CallSession(call_id="call", caller_channel_id="call"))
    wire_queue, received = deque(), []
    clock = SimpleNamespace(now=0.0, next_play=0.020, gaps=0)
    real_sleep = asyncio.sleep

    async def sleep(delay):
        # Independent 20 ms consumer; one application wake can miss a tick.
        clock.now += delay + (0.026 if delay else 0)
        while clock.next_play <= clock.now:
            if wire_queue:
                wire_queue.popleft()
            else:
                clock.gaps += 1
            clock.next_play += 0.020
        await real_sleep(0)

    async def send(call, payload, generation):
        received.append(payload)
        wire_queue.append(payload)
        return True

    monkeypatch.setattr(playback, "time", SimpleNamespace(
        perf_counter=lambda: clock.now, time=lambda: clock.now,
    ))
    monkeypatch.setattr(playback, "asyncio", SimpleNamespace(**{**vars(asyncio), "sleep": sleep}))
    server.send_audio.side_effect = send
    queue = asyncio.Queue()
    expected = [bytes([i, 0]) * 160 for i in range(1, 41)]
    for frame in expected:
        queue.put_nowait(frame)
    queue.put_nowait(_JITTER_SENTINEL)
    await mgr._pacer_loop("call", "stream", queue)
    assert received == expected
    assert bool(clock.gaps) is starves
    if not starves:
        assert len(wire_queue) > 1  # Buffered reserve is forwarded to Asterisk.


@pytest.mark.parametrize("transport,mode,expected", [
    ("websocket", "asterisk", False), ("websocket", "legacy", True),
    ("audiosocket", "asterisk", True), ("externalmedia", "asterisk", True),
])
@pytest.mark.parametrize("provider_class,config_class", [
    (OpenAIRealtimeProvider, OpenAIRealtimeProviderConfig), (GrokProvider, GrokProviderConfig),
])
@pytest.mark.parametrize("downstream", ["stream", "file"])
def test_provider_pacing_policy_is_call_local(transport, mode, expected, provider_class, config_class, downstream):
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(audio_transport=transport, downstream_mode=downstream,
                                    websocket_media=WebSocketMediaConfig(output_pacing=mode))
    expected = expected or downstream == "file"
    saved = config_class(api_key="test", egress_pacer_enabled=True)
    provider = provider_class(saved, on_event=AsyncMock())
    engine._apply_provider_output_pacing(provider, SimpleNamespace(call_id="call", provider_name="custom-instance"))
    assert provider._egress_pacer_enabled is expected
    assert provider.config.egress_pacer_enabled is expected
    assert saved.egress_pacer_enabled is True


@pytest.mark.asyncio
async def test_long_remote_burst_recovers_flow_control_and_drains_naturally():
    """An independently consumed 25 s burst must finish, without a caller FLUSH."""
    mgr, server, store = manager("slin", 8000)
    mgr.websocket_output_pacing = "asterisk"
    await store.upsert_call(playback.CallSession(call_id="call", caller_channel_id="call"))
    expected = [i.to_bytes(2, "little") * 160 for i in range(1, 1262)]
    wire, heard = deque(), []
    writable, drained = asyncio.Event(), asyncio.Event()
    writable.set()
    xoff = 0

    async def send(call, payload, generation):
        nonlocal xoff
        await writable.wait()
        wire.append(payload)
        if len(wire) >= 40:
            writable.clear()
            xoff += 1
        return True

    async def consume():
        while len(heard) < len(expected):
            for _ in range(3):
                await asyncio.sleep(0)
            if wire:
                heard.append(wire.popleft())
            if len(wire) <= 20:
                writable.set()
        drained.set()

    async def finish(call, generation):
        await drained.wait()
        return True

    server.send_audio.side_effect = send
    server.finish_output.side_effect = finish
    q = asyncio.Queue()
    for frame in expected:
        q.put_nowait(frame)
    boundary = playback._TransportBoundary()
    q.put_nowait(boundary)
    q.put_nowait(_JITTER_SENTINEL)
    await asyncio.wait_for(asyncio.gather(mgr._pacer_loop("call", "stream", q), consume()), 5)
    assert heard == expected
    assert xoff >= 4
    server.finish_output.assert_awaited_once()
    assert boundary.completed.result() is True
    server.abort_output.assert_not_awaited()


@pytest.mark.parametrize("acknowledged,expected", [(0, 0), (4000, 4000), (25000, 20000)])
def test_remote_position_never_counts_unacknowledged_queue(acknowledged, expected):
    mgr, server, _ = manager("slin", 8000)
    mgr.websocket_output_pacing = "asterisk"
    mgr.active_streams["call"]["real_tx_bytes"] = 20 * 16000
    mgr.active_streams["call"]["websocket_output_item_key"] = ("assistant-1", 0)
    server.get_confirmed_playback_ms = lambda call: acknowledged
    assert mgr.get_playback_position_ms("call") == expected
    mgr.websocket_output_pacing = "legacy"
    assert mgr.get_playback_position_ms("call") == 20000


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [0, 4000])
async def test_grok_interruption_truncates_to_confirmed_progress_including_zero(confirmed):
    mgr, server, store = manager("slin", 8000)
    mgr.websocket_output_pacing = "asterisk"
    mgr.active_streams["call"]["real_tx_bytes"] = 20 * 16000
    mgr.active_streams["call"]["websocket_output_item_key"] = ("assistant-1", 0)
    server.get_confirmed_playback_ms = lambda call: confirmed
    mgr.stop_streaming_playback = AsyncMock()
    session = playback.CallSession(call_id="call", caller_channel_id="call", media_rx_confirmed=True)
    await store.upsert_call(session)
    grok = GrokProvider(GrokProviderConfig(api_key="test"), on_event=AsyncMock())
    grok._assistant_audio_item_id = "assistant-1"
    grok.websocket = SimpleNamespace(state=SimpleNamespace(name="OPEN"))
    grok._send_json = AsyncMock()
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(audio_transport="websocket")
    engine.session_store = store
    engine.streaming_playback_manager = mgr
    engine._call_providers = {"call": grok}
    engine._close_google_audio_backlog = lambda call: None
    engine._google_long_audio_provider = lambda session: None
    engine._provider_stream_queues = {}
    engine._provider_stream_formats = {}
    engine._provider_coalesce_buf = {}
    engine.conversation_coordinator = None
    await engine._apply_barge_in_action("call", source="provider_event", reason="test")
    mgr.stop_streaming_playback.assert_awaited_once_with("call")
    truncation = grok._send_json.await_args.args[0]
    assert truncation["type"] == "conversation.item.truncate"
    assert truncation["item_id"] == "assistant-1"
    assert truncation["audio_end_ms"] == confirmed


@pytest.mark.asyncio
async def test_new_item_queued_behind_old_audio_cannot_inherit_its_playback_ack():
    """Provider generation may advance while an older item drains remotely."""
    mgr, server, store = manager("slin", 8000)
    mgr.websocket_output_pacing = "asterisk"
    await store.upsert_call(playback.CallSession(call_id="call", caller_channel_id="call"))
    state = {"generation": 0, "played": 0}
    async def begin(call):
        state["generation"] += 1
        state["played"] = 0
        return state["generation"]
    server.begin_output.side_effect = begin
    server.get_confirmed_playback_ms = lambda call: state["played"]
    entered, release = asyncio.Event(), asyncio.Event()
    async def finish(call, generation):
        entered.set()
        await release.wait()
        return True
    server.finish_output.side_effect = finish
    boundary = playback._TransportBoundary()
    q = asyncio.Queue()
    q.put_nowait(playback.ProviderAudioChunk(b"\x01\x00" * 320, "old-item"))
    q.put_nowait(boundary)
    q.put_nowait(playback.ProviderAudioChunk(b"\x02\x00" * 160, "new-item"))
    assert await mgr._drain_next_frame("call", "stream", q) == "sent"
    assert await mgr._drain_next_frame("call", "stream", q) == "sent"
    state["played"] = 40
    assert mgr.get_playback_position_ms("call", audio_item_id="old-item") == 40
    assert mgr.get_playback_position_ms("call", audio_item_id="old-item", content_index=1) == 0
    assert mgr.get_playback_position_ms("call", audio_item_id="new-item") == 0
    finishing = asyncio.create_task(mgr._drain_next_frame("call", "stream", q))
    await entered.wait()
    assert mgr.get_playback_position_ms("call", audio_item_id="new-item") == 0
    release.set()
    await finishing
    assert boundary.completed.result() is True
    assert await mgr._drain_next_frame("call", "stream", q) == "sent"
    assert mgr.get_playback_position_ms("call", audio_item_id="new-item") == 0
    state["played"] = 20
    assert mgr.get_playback_position_ms("call", audio_item_id="new-item") == 20
    assert mgr.get_playback_position_ms("call", audio_item_id="old-item") == 0


def test_pacing_config_round_trip_and_validation():
    from pydantic import ValidationError
    assert WebSocketMediaConfig().output_pacing == "legacy"
    cfg = WebSocketMediaConfig(output_pacing="asterisk")
    assert WebSocketMediaConfig.model_validate(cfg.model_dump()).output_pacing == "asterisk"
    with pytest.raises(ValidationError):
        WebSocketMediaConfig(output_pacing="typo")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class,config_class", [
    (OpenAIRealtimeProvider, OpenAIRealtimeProviderConfig), (GrokProvider, GrokProviderConfig),
])
async def test_transport_paced_provider_does_not_retain_duplicate_audio(provider_class, config_class):
    obj = provider_class(config_class(
        api_key="test", egress_pacer_enabled=False, target_encoding="mulaw", target_sample_rate_hz=8000,
    ), on_event=AsyncMock())
    obj._call_id = "call"
    obj._outfmt_acknowledged = True
    obj._provider_output_format = "g711_ulaw"
    obj._active_output_sample_rate_hz = 8000
    await obj._handle_output_audio(base64.b64encode(b"audio").decode())
    assert not obj._outbuf
