"""A real sub-frame tail must drain even beyond the filler idle limit."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.core.streaming_playback_manager import StreamingPlaybackManager
from src.engine import Engine


@pytest.mark.parametrize('transport', ['audiosocket', 'externalmedia'])
@pytest.mark.parametrize('tail', [b'\x01\x00', b'\x01\x00' * 159])
async def test_partial_tail_is_paced_and_terminal_drain_finishes(monkeypatch, transport, tail):
    sessions = SessionStore()
    await sessions.upsert_call(CallSession(call_id='tail', caller_channel_id='tail'))
    manager = StreamingPlaybackManager(sessions, SimpleNamespace(), streaming_config={})
    manager.audio_transport = transport
    manager._send_audio_chunk = AsyncMock(return_value=True)
    clock = [100.0]
    monkeypatch.setattr('src.core.streaming_playback_manager.time',
                        SimpleNamespace(time=lambda: clock[0], monotonic=lambda: 50.0))
    info = {'stream_id': 'stream', 'startup_ready': True, 'target_format': 'slin16',
            'target_sample_rate': 16000, 'buffered_bytes': len(tail), 'last_real_emit_ts': 99.0}
    manager.active_streams['tail'] = info
    manager._startup_ready['tail'] = True
    manager.frame_remainders['tail'] = tail
    manager.jitter_buffers['tail'] = asyncio.Queue()
    for _ in range(60):
        await manager._drain_next_frame('tail', 'stream', manager.jitter_buffers['tail'])
        if manager._send_audio_chunk.called: break
        clock[0] += .02
    manager._send_audio_chunk.assert_awaited_once()
    frame = manager._send_audio_chunk.call_args.args[2]
    assert len(frame) == 640 and frame == tail + b'\x00' * (640-len(tail))
    assert not manager.frame_remainders['tail'] and info['buffered_bytes'] == 0
    assert not info['last_emit_was_filler']
    assert info['last_real_emit_monotonic'] == 50.0
    engine = Engine.__new__(Engine)
    engine.session_store = sessions
    engine.config = SimpleNamespace(audio_transport=transport)
    engine.providers = {}
    engine.streaming_playback_manager = manager
    engine._provider_stream_queues = {}
    engine._provider_coalesce_buf = {}
    assert await engine._wait_for_call_audio_drain('tail', timeout_sec=.1, quiet_sec=0, reason='test-tail')


async def test_failed_tail_send_retains_audio_for_retry():
    store = SessionStore()
    await store.upsert_call(CallSession(call_id='tail', caller_channel_id='tail'))
    manager = StreamingPlaybackManager(store, SimpleNamespace(), streaming_config={})
    manager._send_audio_chunk = AsyncMock(return_value=False)
    manager.active_streams['tail'] = {'stream_id': 'stream', 'startup_ready': True,
        'target_format': 'slin16', 'target_sample_rate': 16000, 'buffered_bytes': 2,
        'last_real_emit_ts': 0, 'low_water_expired': True, 'empty_backoff_ticks': 100}
    manager.frame_remainders['tail'] = b'\x01\x00'
    manager._startup_ready['tail'] = True
    assert await manager._drain_next_frame('tail', 'stream', asyncio.Queue()) == 'error'
    assert manager.frame_remainders['tail'].startswith(b'\x01\x00')
    assert manager.active_streams['tail']['buffered_bytes'] == 2
    assert 'last_real_emit_monotonic' not in manager.active_streams['tail']


async def test_filler_does_not_advance_real_emission_clock():
    store = SessionStore()
    await store.upsert_call(CallSession(call_id='call', caller_channel_id='call'))
    manager = StreamingPlaybackManager(store, SimpleNamespace(), streaming_config={})
    manager._send_audio_chunk = AsyncMock(return_value=True)
    manager.active_streams['call'] = {'stream_id': 'stream', 'last_real_emit_monotonic': 50.0}
    assert await manager._emit_frame('call', 'stream', bytes(640), 'slin16', 16000, filler=True) == 'sent'
    assert manager.active_streams['call']['last_real_emit_monotonic'] == 50.0
