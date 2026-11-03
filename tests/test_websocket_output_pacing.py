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
def test_provider_pacing_policy_is_call_local(transport, mode, expected, provider_class, config_class):
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(audio_transport=transport, websocket_media=WebSocketMediaConfig(output_pacing=mode))
    saved = config_class(api_key="test", egress_pacer_enabled=True)
    provider = provider_class(saved, on_event=AsyncMock())
    engine._apply_provider_output_pacing(provider, SimpleNamespace(call_id="call", provider_name="custom-instance"))
    assert provider._egress_pacer_enabled is expected
    assert provider.config.egress_pacer_enabled is expected
    assert saved.egress_pacer_enabled is True


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
