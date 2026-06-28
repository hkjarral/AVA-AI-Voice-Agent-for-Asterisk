import asyncio
import math
import pytest

from src.core.streaming_playback_manager import StreamingPlaybackManager


class Dummy:
    pass


def make_manager(**overrides):
    cfg = {
        'continuous_stream': True,
        'min_start_ms': 120,
        'low_watermark_ms': 80,
        'chunk_size_ms': 20,
        'sample_rate': 8000,
        'normalizer': {'enabled': True, 'target_rms': 1400, 'max_gain_db': 9.0},
    }
    cfg.update(overrides)
    return StreamingPlaybackManager(
        session_store=Dummy(),
        ari_client=Dummy(),
        conversation_coordinator=None,
        fallback_playback_manager=None,
        streaming_config=cfg,
        audio_transport="audiosocket",
    )


def test_continuous_stream_skips_warmup_for_non_first_segment():
    mgr = make_manager()
    call_id = "test-call-1"
    stream_id = "stream:resp:test-call-1:1"
    # Simulate active stream entry minimal fields
    mgr.active_streams[call_id] = {
        'stream_id': stream_id,
        'min_start_chunks': mgr.min_start_chunks,
    }
    mgr._startup_ready[call_id] = False

    # Non-first segment
    stream_info = {
        'segments_played': 1,
        'min_start_chunks': mgr.min_start_chunks,
    }
    jitter = asyncio.Queue()

    ready = mgr._ensure_startup_ready(call_id, stream_id, jitter, stream_info)
    assert ready is True
    assert mgr._startup_ready.get(call_id) is True
    assert stream_info.get('startup_ready') is True


def test_first_segment_requires_min_start_when_empty():
    mgr = make_manager()
    call_id = "test-call-2"
    stream_id = "stream:resp:test-call-2:1"
    mgr._startup_ready[call_id] = False
    stream_info = {
        'segments_played': 0,
        'min_start_chunks': 4,
    }
    jitter = asyncio.Queue()
    # empty jitter buffer -> available_frames = 0 < 4
    ready = mgr._ensure_startup_ready(call_id, stream_id, jitter, stream_info)
    assert ready is False
    assert mgr._startup_ready.get(call_id) is False


def test_first_segment_releases_short_audio_after_producer_closes():
    mgr = make_manager()
    call_id = "test-call-short"
    stream_id = "stream:resp:test-call-short:1"
    mgr._startup_ready[call_id] = False
    stream_info = {
        'segments_played': 0,
        'min_start_chunks': 7,
        'producer_closed': True,
        'target_format': 'ulaw',
        'target_sample_rate': 8000,
    }
    jitter = asyncio.Queue()
    jitter.put_nowait(b"\xff" * 160)

    ready = mgr._ensure_startup_ready(call_id, stream_id, jitter, stream_info)

    assert ready is True
    assert mgr._startup_ready.get(call_id) is True
    assert stream_info.get('startup_ready') is True


@pytest.mark.asyncio
async def test_mark_segment_boundary_increments_and_resets_attack():
    mgr = make_manager()
    call_id = "test-call-3"
    # Prepare active stream with sample rate and existing fields
    mgr.active_streams[call_id] = {
        'stream_id': "stream:resp:test-call-3:1",
        'target_sample_rate': 8000,
        'segments_played': 0,
    }
    # attack bytes expected: sr * (attack_ms/1000) * 2
    expected_attack = int(max(0, int(8000 * (mgr.attack_ms / 1000.0)) * 2))

    await mgr.mark_segment_boundary(call_id)

    info = mgr.active_streams[call_id]
    assert info['segments_played'] == 1
    assert info.get('attack_bytes_remaining') == expected_attack
