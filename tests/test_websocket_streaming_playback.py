import asyncio
import audioop
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.core.streaming_playback_manager import (
    StreamingPlaybackManager,
    _TransportBoundary,
)


class MediaServer:
    def __init__(self, codec="ulaw", rate=8000):
        self.binding = SimpleNamespace(codec=codec, sample_rate=rate)
        self.begin_output = AsyncMock(return_value=1)
        self.send_audio = AsyncMock(return_value=True)
        self.finish_output = AsyncMock(return_value=True)
        self.abort_output = AsyncMock(return_value=True)

    def get_binding(self, call_id):
        return self.binding


def manager(codec="ulaw", rate=8000):
    server = MediaServer(codec, rate)
    store = SessionStore()
    mgr = StreamingPlaybackManager(
        store, SimpleNamespace(), audio_transport="websocket",
        websocket_server=server,
        streaming_config={
            "provider_grace_ms": 0, "min_start_ms": 20,
            "attack_ms": 0, "limiter_enabled": False,
            "normalizer": {"enabled": False},
        },
    )
    mgr.active_streams["call"] = {
        "stream_id": "stream", "source_encoding": "slin",
        "source_sample_rate": rate, "target_format": codec,
        "target_sample_rate": rate, "startup_ready": True,
        "min_start_chunks": 1, "buffered_bytes": 0,
    }
    mgr._startup_ready["call"] = True
    return mgr, server, store


@pytest.mark.parametrize("codec,rate,size", [
    ("ulaw", 8000, 160), ("alaw", 8000, 160),
    ("slin", 8000, 320), ("slin16", 16000, 640),
])
def test_wire_conversion_frame_size_and_silence(codec, rate, size):
    mgr, _, _ = manager(codec, rate)
    pcm = struct.pack("<hhhh", -16000, -1000, 1000, 16000)
    expected = (
        audioop.lin2ulaw(pcm, 2) if codec == "ulaw"
        else audioop.lin2alaw(pcm, 2) if codec == "alaw" else pcm
    )
    assert mgr._process_websocket_chunk("call", pcm) == expected
    assert mgr._frame_size_bytes("call") == size
    assert mgr._silence_byte(codec) == (
        b"\xff" if codec == "ulaw" else b"\xd5" if codec == "alaw" else b"\x00"
    )


@pytest.mark.parametrize("codec", ["ulaw", "alaw"])
def test_companded_provider_input_is_decoded_and_matching_wire_is_preserved(codec):
    mgr, _, _ = manager("slin")
    info = mgr.active_streams["call"]
    info["source_encoding"] = codec
    encoded = bytes(range(256))
    expected = audioop.ulaw2lin(encoded, 2) if codec == "ulaw" else audioop.alaw2lin(encoded, 2)
    assert mgr._process_websocket_chunk("call", encoded) == expected
    info["target_format"] = codec
    assert mgr._process_websocket_chunk("call", encoded) == encoded


def test_pcm_split_sample_preserved_across_chunks_and_calls():
    mgr, _, _ = manager("slin")
    pcm = struct.pack("<hh", 0x1234, -1234)
    first = mgr._process_websocket_chunk("call", pcm[:3])
    second = mgr._process_websocket_chunk("call", pcm[3:])
    assert first + second == pcm
    mgr.active_streams["other"] = dict(mgr.active_streams["call"])
    assert mgr._process_websocket_chunk("other", pcm) == pcm


@pytest.mark.parametrize("codec", ["ulaw", "alaw"])
def test_companded_diagnostic_taps_do_not_change_the_wire_fast_path(codec):
    mgr, _, _ = manager(codec)
    info = mgr.active_streams["call"]
    info["source_encoding"] = codec
    info["diag_enabled"] = True
    mgr.diag_enable_taps = True
    encoded = bytes(range(256))
    assert mgr._process_websocket_chunk("call", encoded) == encoded
    decoded = audioop.ulaw2lin(encoded, 2) if codec == "ulaw" else audioop.alaw2lin(encoded, 2)
    assert bytes(info["tap_pre_pcm16"]) == bytes(info["tap_post_pcm16"]) == decoded
    assert info["tap_rate"] == 8000


@pytest.mark.asyncio
async def test_websocket_send_preserves_pcm_little_endian_and_rejects_old_stream():
    mgr, server, store = manager("slin16", 16000)
    await store.upsert_call(CallSession(call_id="call", caller_channel_id="call", provider_name="local"))
    payload = struct.pack("<hh", 0x1234, -1234)
    assert await mgr._send_audio_chunk("call", "stream", payload)
    server.send_audio.assert_awaited_once_with("call", payload, 1)
    assert not await mgr._send_audio_chunk("call", "old-stream", payload)
    assert server.send_audio.await_count == 1


@pytest.mark.asyncio
async def test_capture_failure_does_not_fail_an_already_sent_frame():
    mgr, server, store = manager()
    await store.upsert_call(CallSession(call_id="call", caller_channel_id="call", provider_name="local"))
    def broken_capture(*args):
        raise OSError("capture volume full")
    mgr.audio_capture_manager = SimpleNamespace(append_encoded=broken_capture)
    assert await mgr._send_audio_chunk("call", "stream", b"\xff" * 160)
    assert server.send_audio.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["audiosocket", "externalmedia", "websocket"])
async def test_output_rechecks_stream_ownership_after_diagnostic_await(kind):
    mgr, server, store = manager()
    mgr.audio_transport = kind
    mgr.rtp_server = mgr.audiosocket_server = server
    await store.upsert_call(CallSession(call_id="call", caller_channel_id="call", provider_name="local"))
    async def diagnostic(*args):
        mgr.active_streams["call"] = {"stream_id": "replacement"}
        await asyncio.sleep(0)
    mgr.audio_diag_callback = diagnostic
    assert not await mgr._send_audio_chunk("call", "stream", b"\xff" * 160)
    server.send_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_boundary_waits_for_all_wire_frames_and_correlated_ack():
    mgr, server, store = manager("alaw")
    await store.upsert_call(CallSession(call_id="call", caller_channel_id="call", provider_name="local"))
    queue = asyncio.Queue()
    mgr.jitter_buffers["call"] = queue
    boundary = _TransportBoundary()
    # 25 ms of PCM -> 200 A-law bytes -> two paced frames, padded with A-law silence.
    queue.put_nowait(b"\x00\x00" * 200)
    queue.put_nowait(boundary)
    assert await mgr._drain_next_frame("call", "stream", queue) == "sent"
    assert not boundary.completed.done()
    assert await mgr._drain_next_frame("call", "stream", queue) == "sent"
    assert server.send_audio.await_args.args[1] == b"\xd5" * 160
    assert await mgr._drain_next_frame("call", "stream", queue) == "wait"
    assert boundary.completed.result() is True
    server.finish_output.assert_awaited_once_with("call", 1)
    assert "websocket_output_generation" not in mgr.active_streams["call"]


@pytest.mark.asyncio
async def test_idle_websocket_does_not_send_filler_or_reopen_segment():
    mgr, server, _ = manager()
    mgr.active_streams["call"]["empty_backoff_ticks"] = 100
    queue = asyncio.Queue()
    assert await mgr._drain_next_frame("call", "stream", queue) == "wait"
    server.begin_output.assert_not_awaited()
    server.send_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_boundary_does_not_clear_new_segment_gating():
    mgr, _, store = manager()
    await store.upsert_call(CallSession(call_id="call", caller_channel_id="call", provider_name="local"))
    info = mgr.active_streams["call"]
    boundary = _TransportBoundary()
    info["websocket_boundary"] = boundary
    store.clear_gating_token = AsyncMock()
    await mgr.end_segment_gating("call")
    info["segment_gate_epoch"] = 1
    boundary.completed.set_result(True)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    store.clear_gating_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_flushes_remote_audio_even_without_live_local_stream():
    mgr, server, _ = manager()
    mgr.active_streams.clear()
    assert await mgr.stop_streaming_playback("call")
    server.abort_output.assert_awaited_once_with("call")


@pytest.mark.asyncio
@pytest.mark.parametrize("codec,rate", [
    ("ulaw", 8000), ("alaw", 8000), ("slin", 8000), ("slin16", 16000),
])
async def test_complete_stream_waits_for_asterisk_boundary_before_cleanup(codec, rate):
    mgr, server, store = manager(codec, rate)
    mgr.active_streams.clear()
    await store.upsert_call(CallSession(call_id="call", caller_channel_id="call", provider_name="pipeline"))
    reached_finish = asyncio.Event()
    release_finish = asyncio.Event()

    async def finish(call_id, generation):
        reached_finish.set()
        await release_finish.wait()
        return True

    server.finish_output.side_effect = finish
    source = asyncio.Queue()
    await source.put(struct.pack("<h", 1500) * (rate // 25))
    await source.put(None)
    stream_id = await mgr.start_streaming_playback(
        "call", source, source_encoding="linear16", source_sample_rate=rate,
        target_encoding=codec, target_sample_rate=rate,
    )
    assert stream_id
    task = mgr.active_streams["call"]["streaming_task"]
    try:
        await asyncio.wait_for(reached_finish.wait(), 3)
        assert "call" in mgr.active_streams
        assert not task.done()
        release_finish.set()
        await asyncio.wait_for(task, 3)
        assert "call" not in mgr.active_streams
        assert server.send_audio.await_count >= 1
        server.finish_output.assert_awaited_once_with("call", 1)
        server.abort_output.assert_not_awaited()
    finally:
        release_finish.set()
        if "call" in mgr.active_streams:
            await mgr.stop_streaming_playback("call")


@pytest.mark.asyncio
async def test_final_boundary_is_not_cancelled_by_local_empty_queue_watchdog():
    mgr, server, store = manager()
    mgr.active_streams.clear()
    await store.upsert_call(CallSession(call_id="call", caller_channel_id="call", provider_name="local"))
    reached_finish, release_finish = asyncio.Event(), asyncio.Event()
    async def finish(*args):
        reached_finish.set()
        await release_finish.wait()
        return True
    server.finish_output.side_effect = finish
    source = asyncio.Queue()
    await source.put(b"\x00\x00" * 160)
    boundary = _TransportBoundary()
    await source.put(boundary)
    await source.put(None)
    await mgr.start_streaming_playback(
        "call", source, source_encoding="slin", source_sample_rate=8000,
    )
    task = mgr.active_streams["call"]["streaming_task"]
    try:
        await asyncio.wait_for(reached_finish.wait(), 2)
        await asyncio.sleep(0.8)
        assert not boundary.completed.done()
        assert not task.done()
        release_finish.set()
        await asyncio.wait_for(task, 2)
        assert boundary.completed.result() is True
        server.finish_output.assert_awaited_once()
        server.abort_output.assert_not_awaited()
    finally:
        release_finish.set()
        if "call" in mgr.active_streams:
            await mgr.stop_streaming_playback("call")


@pytest.mark.asyncio
async def test_stop_owns_single_flush_when_it_invalidates_a_blocked_writer():
    mgr, server, store = manager()
    mgr.active_streams.clear()
    await store.upsert_call(CallSession(call_id="call", caller_channel_id="call", provider_name="local"))
    entered_send, release_send = asyncio.Event(), asyncio.Event()
    async def send(*args):
        entered_send.set()
        await release_send.wait()
        return False  # Generation was invalidated by the interruption.
    async def abort(*args):
        release_send.set()
        # Let the pacer observe its invalidated write while the fence is pending.
        await asyncio.sleep(0.05)
        return True
    server.send_audio.side_effect = send
    server.abort_output.side_effect = abort
    source = asyncio.Queue()
    await source.put(b"\x00\x00" * 800)
    await mgr.start_streaming_playback(
        "call", source, source_encoding="slin", source_sample_rate=8000,
    )
    info = mgr.active_streams["call"]
    try:
        await asyncio.wait_for(entered_send.wait(), 2)
        assert await asyncio.wait_for(mgr.stop_streaming_playback("call"), 2)
        server.abort_output.assert_awaited_once_with("call")
        server.finish_output.assert_not_awaited()
        assert "call" not in mgr.active_streams
        assert "call" not in mgr.frame_remainders
        assert info["streaming_task"].done()
        assert info["pacer_task"].done()
    finally:
        release_send.set()
        if "call" in mgr.active_streams:
            await mgr.stop_streaming_playback("call")


@pytest.mark.asyncio
async def test_noncontinuous_idle_provider_drains_without_keepalive_file_replay():
    mgr, server, store = manager()
    mgr.active_streams.clear()
    mgr.continuous_stream = False
    mgr.fallback_timeout_ms = 50
    mgr.connection_timeout_ms = 1
    mgr.keepalive_interval_ms = 1
    mgr._fallback_to_file_playback = AsyncMock()
    await store.upsert_call(CallSession(call_id="call", caller_channel_id="call", provider_name="local"))
    reached_finish, release_finish = asyncio.Event(), asyncio.Event()

    async def finish(*args):
        reached_finish.set()
        await release_finish.wait()
        return True

    server.finish_output.side_effect = finish
    source = asyncio.Queue()
    await mgr.start_streaming_playback(
        "call", source, source_encoding="slin", source_sample_rate=8000,
    )
    info = mgr.active_streams["call"]
    info["last_chunk_time"] = 0
    task = info["streaming_task"]
    await source.put(b"\x00\x00" * 160)
    # No EOF: the producer's bounded idle timeout must own natural drain.
    try:
        await asyncio.wait_for(reached_finish.wait(), 2)
        assert info["last_chunk_time"] > 0
        assert info["end_reason"] == "provider-timeout"
        await asyncio.sleep(0.05)
        assert not task.done()
        mgr._fallback_to_file_playback.assert_not_awaited()
        server.abort_output.assert_not_awaited()
        release_finish.set()
        await asyncio.wait_for(task, 2)
        server.finish_output.assert_awaited_once_with("call", 1)
        assert "call" not in mgr.active_streams
        assert "call" not in mgr.keepalive_tasks
    finally:
        release_finish.set()
        if "call" in mgr.active_streams:
            await mgr.stop_streaming_playback("call")


def test_unknown_source_encoding_is_treated_as_pcm16_like_audiosocket():
    """A provider token outside the alias map must not kill the pacer."""
    mgr, _, _ = manager("ulaw", 8000)
    mgr.active_streams["call"]["source_encoding"] = "pcm"
    pcm = struct.pack("<hhhh", -16000, -1000, 1000, 16000)

    assert mgr._process_websocket_chunk("call", pcm) == audioop.lin2ulaw(pcm, 2)
