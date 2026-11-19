"""Replay real arrival jitter with synthetic PCM; no network or wall-clock sleeps."""
import asyncio
import base64
import json
import struct
import time
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.config import GoogleProviderConfig
from src.providers.google_live import GoogleLiveProvider


def provider(callback, prefill=2000):
    p = GoogleLiveProvider(GoogleProviderConfig(
        api_key='synthetic', playback_prefill_ms=prefill,
        target_encoding='slin16', target_sample_rate_hz=16000,
        output_sample_rate_hz=16000), callback)
    p._call_id = 'prefill-test'
    p._track_conversation_message = AsyncMock()
    return p


async def chunk(p, size=5120, sample=1):
    await p._handle_audio_output(base64.b64encode(struct.pack('<h', sample) * (size // 2)).decode(), mime_type='audio/pcm;rate=16000')


def playback_holes(events):
    """20 ms PCM pacer with the existing ~200 ms startup target."""
    i = available = sent = holes = 0
    started = False
    total = sum(size for _, size in events)
    for tick in range(0, 60000, 20):
        while i < len(events) and events[i][0] <= tick:
            available += events[i][1]
            i += 1
        if not started and (available >= 6400 or i == len(events)):
            started = True
        if started:
            if available >= 640:
                available -= 640
                sent += 640
            elif i < len(events):
                holes += 20
        if sent >= total - total % 640:
            return holes
    raise AssertionError('Pacer did not drain')


@pytest.mark.parametrize('trace', json.loads((Path(__file__).parent / 'fixtures/calendly_recap_arrival_traces.json').read_text()))
async def test_recorded_recap_jitter_drains_without_speech_gaps(trace):
    async def replay(prefill):
        events, clock, received, expected = [], [0], [], []
        async def callback(event):
            if event['type'] == 'AgentAudio':
                events.append((clock[0], len(event['data'])))
                received.append(event['data'])
        p = provider(callback, prefill)
        for index, row in enumerate(trace):
            clock[0] = row['arrival_ms']
            expected.append(struct.pack('<h', index + 1) * (row['bytes'] // 2))
            await chunk(p, row['bytes'], index + 1)
        await p._flush_output_prefill()
        assert sum(size for _, size in events) == sum(row['bytes'] for row in trace)
        assert b''.join(received) == b''.join(expected)
        return playback_holes(events)
    assert await replay(0) >= 700  # Reproduces the old receive/playback gap.
    assert await replay(2000) == 0


async def test_short_turn_flushes_before_completion_and_each_turn_rearms():
    events = []
    async def callback(event):
        events.append(event)
    p = provider(callback)
    for _ in range(2):
        before = len(events)
        await chunk(p)
        assert len(events) == before
        await p._handle_turn_complete()
        assert [e['type'] for e in events[before:]] == ['AgentAudio', 'AgentAudioDone']
        assert not p._output_prefill


async def test_interruption_discards_buffer_and_cancels_in_progress_flush():
    events = []
    async def callback(event):
        events.append(event)
        if event['type'] == 'AgentAudio':
            await p._handle_server_content({'serverContent': {'interrupted': True}})
    p = provider(callback)
    await chunk(p)
    await p._handle_server_content({'serverContent': {'interrupted': True}})
    assert not p._output_prefill and not any(e['type'] == 'AgentAudio' for e in events)
    for _ in range(13):
        await chunk(p)
    assert sum(e['type'] == 'AgentAudio' for e in events) == 1
    assert not p._output_prefill


async def test_closed_call_never_emits_or_retains_late_audio():
    callback = AsyncMock()
    p = provider(callback)
    await chunk(p)
    p._closed = True
    await p.stop_session()
    await chunk(p)
    assert not p._output_prefill
    callback.assert_not_awaited()


async def test_generation_complete_flushes_short_audio_in_long_audio_mode():
    callback = AsyncMock()
    p = provider(callback)
    p.config.long_audio_playback_enabled = True
    await chunk(p)
    await p._handle_audio_generation_complete()
    assert [call.args[0]['type'] for call in callback.await_args_list] == ['AgentAudio', 'GoogleAudioGenerationComplete']


async def test_concurrent_boundary_waits_for_flush_and_interruption_stops_remainder():
    entered, release = asyncio.Event(), asyncio.Event()
    events = []
    async def callback(event):
        events.append(event)
        entered.set()
        await release.wait()
    p = provider(callback)
    await chunk(p, 32000)
    flushing = asyncio.create_task(chunk(p, 32000))
    await entered.wait()
    boundary = asyncio.create_task(p._flush_output_prefill())
    await asyncio.sleep(0)
    assert not boundary.done()
    p._discard_output_prefill()
    release.set()
    await asyncio.gather(flushing, boundary)
    assert len(events) == 1 and not p._output_prefill


async def test_delayed_old_turn_boundary_does_not_release_new_generation():
    callback = AsyncMock()
    p = provider(callback)
    p.config.long_audio_playback_enabled = True
    await chunk(p)
    await p._handle_audio_generation_complete()
    await chunk(p)
    count = callback.await_count
    await p._handle_turn_complete()
    assert callback.await_count == count + 1  # Only the old AgentAudioDone.
    assert len(p._output_prefill) == 1
    await p._handle_audio_generation_complete()
    assert not p._output_prefill


async def test_new_chunk_waits_behind_reserve_callback_in_fifo_order():
    entered, release = asyncio.Event(), asyncio.Event()
    samples = []
    async def callback(event):
        if event['type'] == 'AgentAudio':
            samples.append(struct.unpack('<h', event['data'][:2])[0])
            if len(samples) == 1:
                entered.set()
                await release.wait()
    p = provider(callback)
    await chunk(p, sample=1)
    await chunk(p, sample=2)
    flushing = asyncio.create_task(p._flush_output_prefill())
    await entered.wait()
    arriving = asyncio.create_task(chunk(p, sample=3))
    await asyncio.sleep(0)
    assert samples == [1] and not arriving.done()
    release.set()
    await asyncio.gather(flushing, arriving)
    assert samples == [1, 2, 3]


async def test_repeated_boundary_does_not_flush_new_response_reserve():
    callback = AsyncMock()
    p = provider(callback)
    p.config.long_audio_playback_enabled = True
    await chunk(p)
    await p._handle_audio_generation_complete()
    await p._handle_turn_complete()
    await chunk(p)
    count = callback.await_count
    await p._handle_turn_complete()  # Duplicate A boundary during B.
    assert callback.await_count == count
    assert len(p._output_prefill) == 1
    await p._handle_audio_generation_complete()
    assert callback.await_count == count + 2


async def test_local_interruption_discards_reserve_and_late_generation_chunks():
    callback = AsyncMock()
    p = provider(callback)
    p.config.long_audio_playback_enabled = True
    await chunk(p, sample=1)
    old_response = p._audio_response_id
    await p.handle_local_barge_in()
    await chunk(p, sample=2)
    await p._handle_audio_generation_complete()
    assert not any(c.args[0]['type'] == 'AgentAudio' for c in callback.await_args_list)
    await chunk(p, sample=3)
    await p._handle_audio_generation_complete()
    audio = [c.args[0] for c in callback.await_args_list if c.args[0]['type'] == 'AgentAudio']
    assert len(audio) == 1 and audio[0]['audio_response_id'] > old_response
    assert struct.unpack('<h', audio[0]['data'][:2])[0] == 3


@pytest.mark.parametrize('boundary', ['local', 'server', 'new_response'])
async def test_latency_tracking_await_cannot_reauthorize_stale_audio(boundary):
    entered, release = asyncio.Event(), asyncio.Event()
    callback = AsyncMock()
    p = provider(callback)
    p.config.long_audio_playback_enabled = True
    p._in_audio_burst = True
    p._audio_response_id = 1
    p._turn_start_time = time.time() - .1
    async def get_session(_):
        entered.set()
        await release.wait()
        return SimpleNamespace(turn_latencies_ms=[])
    p._session_store = SimpleNamespace(get_by_call_id=get_session, upsert_call=AsyncMock())
    waiting = asyncio.create_task(chunk(p, sample=2))
    await entered.wait()
    if boundary == 'local': await p.handle_local_barge_in()
    elif boundary == 'server': await p._handle_server_content({'serverContent': {'interrupted': True}})
    else: p._audio_response_id += 1
    release.set()
    await waiting
    await p._flush_output_prefill()
    assert not p._output_prefill
    assert not any(c.args[0]['type'] == 'AgentAudio' for c in callback.await_args_list)
