"""Offline lifecycle regressions for bounded diagnostic and endpoint state (#864)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import src.engine as engine_module
import src.core.streaming_playback_manager as playback_module
from src.audio.audiosocket_protocol import AudioSocketAudioFrame
from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.core.streaming_playback_manager import StreamingPlaybackManager
from src.engine import Engine
from tests.test_deferred_transfer_commit_audio_gate import _build_engine


@pytest.fixture
def endpoint_engine(monkeypatch):
    engine = Engine.__new__(Engine)
    engine._outbound_dial_context = "from-internal"
    engine._outbound_dial_prefix = "9"
    engine._outbound_channel_tech = "auto"
    engine._outbound_pjsip_endpoint_cache = {}
    engine._outbound_pjsip_endpoint_cache_ttl_seconds = 10
    engine.ari_client = SimpleNamespace(send_command=AsyncMock(return_value={"status": 404}))
    clock = [100.0]
    # Replace the module binding, not the process-wide clock used by asyncio.
    monkeypatch.setattr(engine_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return engine, clock


@pytest.mark.parametrize("tech", [None, "PJSIP", "SIP"])
async def test_endpoint_results_reused_until_exact_ttl_then_reprobed(endpoint_engine, tech):
    engine, clock = endpoint_engine
    engine._outbound_channel_tech = (tech or "PJSIP").lower()
    engine.ari_client.send_command.return_value = {"technology": tech} if tech else {"status": 404}
    expected = f"{tech}/1000" if tech else "Local/91000@from-internal"
    assert await engine._outbound_choose_endpoint("1000") == expected
    clock[0] += 9.99
    assert await engine._outbound_choose_endpoint("1000") == expected
    assert engine.ari_client.send_command.await_count == 1
    clock[0] = 110.0
    assert await engine._outbound_choose_endpoint("1000") == expected
    assert engine.ari_client.send_command.await_count == 2


async def test_expired_unqueried_numbers_are_evicted_on_new_number(endpoint_engine):
    engine, clock = endpoint_engine
    for n in range(20):
        await engine._outbound_choose_endpoint(str(1000 + n))
    clock[0] += 100
    await engine._outbound_choose_endpoint("2000")
    assert set(engine._outbound_pjsip_endpoint_cache) == {"2000"}


async def test_cache_is_bounded_and_eviction_keeps_probe_fallback_order(endpoint_engine):
    engine, _ = endpoint_engine
    engine.ari_client.send_command.side_effect = lambda _method, path, **_kw: (
        {"technology": "SIP"} if "/SIP/" in path else {"status": 404}
    )
    limit = engine._OUTBOUND_ENDPOINT_CACHE_MAX_ENTRIES
    for n in range(limit + 1):
        assert await engine._outbound_choose_endpoint(str(n)) == f"SIP/{n}"
    assert len(engine._outbound_pjsip_endpoint_cache) == limit
    assert "0" not in engine._outbound_pjsip_endpoint_cache
    engine.ari_client.send_command.reset_mock()
    assert await engine._outbound_choose_endpoint(str(limit)) == f"SIP/{limit}"
    engine.ari_client.send_command.assert_not_awaited()
    assert await engine._outbound_choose_endpoint("0") == "SIP/0"
    assert [c.args[1] for c in engine.ari_client.send_command.await_args_list] == [
        "endpoints/PJSIP/0", "endpoints/SIP/0",
    ]
    assert len(engine._outbound_pjsip_endpoint_cache) == limit


async def test_concurrent_cache_misses_recheck_bound_after_probe(endpoint_engine, monkeypatch):
    engine, clock = endpoint_engine
    engine._outbound_channel_tech = "pjsip"
    monkeypatch.setattr(engine, "_OUTBOUND_ENDPOINT_CACHE_MAX_ENTRIES", 3)
    release = asyncio.Event()
    started = 0

    async def probe(*_args, **_kwargs):
        nonlocal started
        started += 1
        await release.wait()
        return {"technology": "PJSIP"}

    engine.ari_client.send_command.side_effect = probe
    tasks = [asyncio.create_task(engine._outbound_choose_endpoint(str(n))) for n in range(8)]
    for _ in range(10):
        if started == len(tasks):
            break
        await asyncio.sleep(0)
    assert started == 8
    clock[0] += 20
    release.set()
    assert await asyncio.gather(*tasks) == [f"PJSIP/{n}" for n in range(8)]
    assert len(engine._outbound_pjsip_endpoint_cache) == 3
    assert all(e["ts"] == clock[0] for e in engine._outbound_pjsip_endpoint_cache.values())


@pytest.mark.parametrize("ttl", [0, -1])
async def test_disabled_cache_does_not_accumulate_results(endpoint_engine, ttl):
    engine, _ = endpoint_engine
    engine._outbound_pjsip_endpoint_cache_ttl_seconds = ttl
    for _ in range(2):
        assert await engine._outbound_choose_endpoint("1000") == "Local/91000@from-internal"
    assert engine._outbound_pjsip_endpoint_cache == {}
    assert engine.ari_client.send_command.await_count == 4


async def test_local_only_and_probe_failure_keep_existing_routes(endpoint_engine):
    engine, _ = endpoint_engine
    engine._outbound_channel_tech = "local_only"
    assert await engine._outbound_choose_endpoint("1000") == "Local/91000@from-internal"
    engine.ari_client.send_command.assert_not_awaited()
    engine._outbound_channel_tech = "auto"
    engine.ari_client.send_command.side_effect = RuntimeError("synthetic probe failure")
    assert await engine._outbound_choose_endpoint("1000") == "Local/91000@from-internal"
    assert engine.ari_client.send_command.await_count == 2


async def socket_engine():
    engine = Engine.__new__(Engine)
    engine.conn_to_channel = {}
    engine.channel_to_conns = {}
    engine.audiosocket_primary_conn = {}
    engine._audiosocket_frame_count = {}
    engine.uuidext_to_channel = {"uuid": "socket-call"}
    engine.session_store = SessionStore()
    engine._save_session = AsyncMock(side_effect=engine.session_store.upsert_call)
    session = CallSession(call_id="socket-call", caller_channel_id="socket-call")
    await engine.session_store.upsert_call(session)
    return engine, session


async def test_last_socket_disconnect_clears_counter_and_reconnect_is_independent():
    engine, session = await socket_engine()
    for n in range(10):
        conn = f"conn-{n}"
        assert await engine._audiosocket_handle_uuid(conn, "uuid")
        engine._audiosocket_frame_count[session.call_id] = 10
        await engine._audiosocket_handle_disconnect(conn)
        await engine._audiosocket_handle_disconnect(conn)
        assert engine.conn_to_channel == engine.channel_to_conns == engine.audiosocket_primary_conn == {}
        assert engine._audiosocket_frame_count == {}
        assert session.audiosocket_conn_id is None
    assert not hasattr(engine, "channel_to_conn")


async def test_old_disconnect_preserves_new_socket_and_call_counter():
    engine, session = await socket_engine()
    assert await engine._audiosocket_handle_uuid("old", "uuid")
    assert await engine._audiosocket_handle_uuid("new", "uuid")
    engine._audiosocket_frame_count[session.call_id] = 25
    await engine._audiosocket_handle_disconnect("old")
    await engine._audiosocket_handle_disconnect("old")
    assert engine.conn_to_channel == {"new": session.call_id}
    assert engine.channel_to_conns == {session.call_id: {"new"}}
    assert engine.audiosocket_primary_conn == {session.call_id: "new"}
    assert engine._audiosocket_frame_count == {session.call_id: 25}
    assert session.audiosocket_conn_id == session.media_connection_id == "new"
    assert session.media_connection_state == "ready"


@pytest.mark.parametrize("state", ["missing", "cleanup_in_progress", "cleanup_completed"])
async def test_late_audio_cannot_recreate_counter(state):
    engine, session = await socket_engine()
    assert await engine._audiosocket_handle_uuid("conn", "uuid")
    if state == "missing":
        await engine.session_store.remove_call(session.call_id)
    else:
        setattr(session, state, True)
    frame = AudioSocketAudioFrame(bytes(320), 0x10, "slin", 8000)
    await engine._audiosocket_handle_audio("conn", frame)
    assert engine._audiosocket_frame_count == {}


async def playback_manager(transport="audiosocket"):
    store = SessionStore()
    session = CallSession(call_id="playback-call", caller_channel_id="playback-call", audiosocket_conn_id="conn")
    await store.upsert_call(session)
    manager = StreamingPlaybackManager(store, SimpleNamespace(), audio_transport=transport,
                                       streaming_config={"provider_grace_ms": 0})
    manager.audiosocket_server = SimpleNamespace()
    manager.websocket_server = SimpleNamespace()
    manager.rtp_server = SimpleNamespace(has_remote_endpoint=Mock(return_value=False))
    writer = SimpleNamespace(write=AsyncMock(return_value=transport == "audiosocket"),
                             begin_output=AsyncMock(return_value=7))
    manager._selected_output_transport = Mock(return_value=writer)
    return manager, session, writer


def segment(manager, call_id, stream_id):
    manager.active_streams[call_id] = {"stream_id": stream_id, "end_reason": "barge-in"}


async def test_first_send_logged_once_across_segments_until_final_call_cleanup(monkeypatch):
    manager, session, writer = await playback_manager()
    log = Mock()
    monkeypatch.setattr(playback_module, "logger", log)
    for n in range(3):
        stream_id = f"stream-{n}"
        segment(manager, session.call_id, stream_id)
        assert await manager._send_audio_chunk(session.call_id, stream_id, bytes(160))
        await manager._cleanup_stream(session.call_id, stream_id)
        assert session.call_id in manager._first_send_logged
    first_frames = [c for c in log.info.call_args_list if "First frame" in c.args[0]]
    assert len(first_frames) == 1
    assert writer.write.await_count == 3
    manager.cleanup_call(session.call_id)
    manager.cleanup_call(session.call_id)
    assert not manager._first_send_logged
    assert not hasattr(manager, "_last_segment_end_ts")


@pytest.mark.parametrize("finish", ["normal", "stop", "failure", "cancellation"])
async def test_rtp_wait_diagnostics_retired_on_every_stream_exit(finish):
    manager, session, _ = await playback_manager("externalmedia")
    stream_id = f"stream-{finish}"
    segment(manager, session.call_id, stream_id)
    assert not await manager._send_audio_chunk(session.call_id, stream_id, bytes(160))
    assert manager._rtp_remote_wait_logged == {stream_id}
    if finish in {"failure", "cancellation"}:
        error = RuntimeError("synthetic cleanup failure") if finish == "failure" else asyncio.CancelledError()
        manager.session_store.clear_gating_token = AsyncMock(side_effect=error)
    if finish == "cancellation":
        with pytest.raises(asyncio.CancelledError):
            await manager._cleanup_stream(session.call_id, stream_id)
    elif finish == "stop":
        assert await manager.stop_streaming_playback(session.call_id)
    else:
        await manager._cleanup_stream(session.call_id, stream_id)
    assert not manager._rtp_remote_wait_logged
    assert not manager._cleanup_in_progress


async def test_stale_stream_cleanup_preserves_replacement_diagnostics():
    manager, session, _ = await playback_manager("externalmedia")
    segment(manager, session.call_id, "new")
    replacement = manager.active_streams[session.call_id]
    manager._rtp_remote_wait_logged.update({"old", "new"})
    manager._first_send_logged.add(session.call_id)
    await manager._cleanup_stream(session.call_id, "old")
    assert manager.active_streams[session.call_id] is replacement
    assert manager._rtp_remote_wait_logged == {"new"}
    assert manager._first_send_logged == {session.call_id}


async def test_late_rtp_write_result_cannot_recreate_retired_stream_diagnostics():
    manager, session, writer = await playback_manager("externalmedia")
    segment(manager, session.call_id, "old")
    started, release = asyncio.Event(), asyncio.Event()

    async def write(_frame):
        started.set()
        await release.wait()
        return False

    writer.write.side_effect = write
    send = asyncio.create_task(manager._send_audio_chunk(session.call_id, "old", bytes(160)))
    await asyncio.wait_for(started.wait(), timeout=1)
    await manager._cleanup_stream(session.call_id, "old")
    segment(manager, session.call_id, "new")
    release.set()
    assert not await asyncio.wait_for(send, timeout=1)
    assert not manager._rtp_remote_wait_logged
    assert manager.active_streams[session.call_id]["stream_id"] == "new"


@pytest.mark.parametrize("finish", ["normal", "failure", "cancellation"])
async def test_final_cleanup_retires_call_state_even_if_cleanup_is_interrupted(monkeypatch, tmp_path, finish):
    # Real engine/session/playback owners, fake external effects and private data.
    monkeypatch.setenv("CALL_HISTORY_DB_PATH", str(tmp_path / "history.db"))
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "agents.db"))
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "false")
    engine = _build_engine()
    engine.ari_client.hangup_channel = AsyncMock(return_value=True)
    engine.ari_client.destroy_bridge = AsyncMock(return_value=True)
    engine._persist_call_history = AsyncMock()
    for n in range(3):
        session = CallSession(call_id=f"final-{finish}-{n}", caller_channel_id=f"caller-{finish}-{n}")
        await engine.session_store.upsert_call(session)
        engine._last_transcript_ts.update({session.call_id: 1.0, "other-call": 2.0})
        engine._audiosocket_frame_count[session.caller_channel_id] = 10
        engine.channel_to_conns[session.caller_channel_id] = {"owned"}
        engine.audiosocket_primary_conn[session.caller_channel_id] = "owned"
        engine.conn_to_channel.update({"owned": session.caller_channel_id, "other": "other-call"})
        manager = engine.streaming_playback_manager
        manager._first_send_logged.update({session.call_id, "other-call"})
        # A cleanup await after ownership is claimed can fail or be cancelled.
        if finish != "normal":
            error = RuntimeError("synthetic cleanup failure") if finish == "failure" else asyncio.CancelledError()
            engine._stop_background_music = AsyncMock(side_effect=error)
            if finish == "failure":
                engine._close_google_audio_backlog = Mock(side_effect=error)
        if finish == "cancellation":
            with pytest.raises(asyncio.CancelledError):
                await engine._cleanup_call(session.call_id)
        else:
            await engine._cleanup_call(session.call_id)
        assert engine._last_transcript_ts == {"other-call": 2.0}
        if finish == "normal":
            assert await engine.session_store.get_by_call_id(session.call_id) is None
        assert session.caller_channel_id not in engine._audiosocket_frame_count
        assert session.caller_channel_id not in engine.channel_to_conns
        assert session.caller_channel_id not in engine.audiosocket_primary_conn
        assert engine.conn_to_channel == {"other": "other-call"}
        assert manager._first_send_logged == {"other-call"}
        assert session.call_id not in engine_module._cleanup_in_progress
        await engine._cleanup_call(session.call_id)  # Paired/late terminal event.


async def test_cancelled_endpoint_probe_is_not_cached_and_retry_still_probes(endpoint_engine):
    engine, _ = endpoint_engine
    engine._outbound_channel_tech = "pjsip"
    engine.ari_client.send_command.side_effect = [asyncio.CancelledError(), {"technology": "PJSIP"}]
    with pytest.raises(asyncio.CancelledError):
        await engine._outbound_choose_endpoint("1000")
    assert engine._outbound_pjsip_endpoint_cache == {}
    assert await engine._outbound_choose_endpoint("1000") == "PJSIP/1000"
    assert engine.ari_client.send_command.await_count == 2


async def test_real_ingress_counts_frames_then_disconnect_releases_counter():
    from tests.test_google_long_audio_playback import ingress_engine

    engine, session, provider = ingress_engine()
    await engine.session_store.upsert_call(session)
    engine.channel_to_conns = {session.call_id: {"ingress"}}
    engine.audiosocket_primary_conn = {session.call_id: "ingress"}
    frame = AudioSocketAudioFrame(bytes(640), 0x12, "slin16", 16000)
    await engine._audiosocket_handle_audio("ingress", frame)
    await engine._audiosocket_handle_audio("ingress", frame)
    assert engine._audiosocket_frame_count == {session.call_id: 2}
    assert provider.send_audio.await_count == 2
    await engine._audiosocket_handle_disconnect("ingress")
    await engine._audiosocket_handle_audio("ingress", frame)
    assert engine._audiosocket_frame_count == {}


async def test_disconnect_waiting_for_session_does_not_close_reconnected_socket():
    engine, session = await socket_engine()
    assert await engine._audiosocket_handle_uuid("old", "uuid")
    original_get = engine.session_store.get_by_call_id
    started, release = asyncio.Event(), asyncio.Event()
    lookups = 0

    async def get(call_id):
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            started.set()
            await release.wait()
        return await original_get(call_id)

    engine.session_store.get_by_call_id = get
    disconnect = asyncio.create_task(engine._audiosocket_handle_disconnect("old"))
    await asyncio.wait_for(started.wait(), timeout=1)
    assert await engine._audiosocket_handle_uuid("new", "uuid")
    engine._audiosocket_frame_count[session.call_id] = 1
    release.set()
    await asyncio.wait_for(disconnect, timeout=1)
    assert session.audiosocket_conn_id == session.media_connection_id == "new"
    assert session.media_connection_state == "ready"
    assert engine.channel_to_conns == {session.call_id: {"new"}}
    assert engine._audiosocket_frame_count == {session.call_id: 1}


async def test_cleanup_yielding_during_grace_only_retires_old_stream_diagnostics(monkeypatch):
    manager, session, _ = await playback_manager("externalmedia")
    manager.provider_grace_ms = 100
    segment(manager, session.call_id, "old")
    manager.active_streams[session.call_id]["end_reason"] = "provider-complete"
    manager._rtp_remote_wait_logged.add("old")
    started, release = asyncio.Event(), asyncio.Event()

    async def grace(_seconds):
        started.set()
        await release.wait()

    monkeypatch.setattr(playback_module.asyncio, "sleep", grace)
    cleanup = asyncio.create_task(manager._cleanup_stream(session.call_id, "old"))
    await asyncio.wait_for(started.wait(), timeout=1)
    segment(manager, session.call_id, "new")
    manager._rtp_remote_wait_logged.add("new")
    release.set()
    await asyncio.wait_for(cleanup, timeout=1)
    assert manager.active_streams[session.call_id]["stream_id"] == "new"
    assert manager._rtp_remote_wait_logged == {"new"}


async def test_frame_waiting_for_session_cannot_recreate_disconnected_counter():
    engine, session = await socket_engine()
    assert await engine._audiosocket_handle_uuid("conn", "uuid")
    engine._audiosocket_frame_count[session.call_id] = 1
    original_get = engine.session_store.get_by_call_id
    started, release = asyncio.Event(), asyncio.Event()
    lookups = 0

    async def get(call_id):
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            started.set()
            await release.wait()
        return await original_get(call_id)

    engine.session_store.get_by_call_id = get
    frame = AudioSocketAudioFrame(bytes(320), 0x10, "slin", 8000)
    ingress = asyncio.create_task(engine._audiosocket_handle_audio("conn", frame))
    await asyncio.wait_for(started.wait(), timeout=1)
    await engine._audiosocket_handle_disconnect("conn")
    release.set()
    await asyncio.wait_for(ingress, timeout=1)
    assert engine._audiosocket_frame_count == {}
    assert engine.conn_to_channel == engine.channel_to_conns == {}


@pytest.mark.parametrize("streaming", [False, True])
async def test_stt_finishing_after_cancelled_cleanup_cannot_restore_transcript_timestamp(monkeypatch, tmp_path, streaming):
    from src.pipelines.base import STTComponent
    from tests.test_pipeline_runner_lifecycle import (
        _RecordingLLM, _ResultStreamingStubSTT, _SilentTTS, _StubResolution,
    )

    class DelayedSTT(STTComponent):
        def __init__(self):
            self.started, self.release = asyncio.Event(), asyncio.Event()

        async def transcribe(self, *_args):
            self.started.set()
            await self.release.wait()
            return "late synthetic transcript"

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "false")
    monkeypatch.setenv("CALL_HISTORY_DB_PATH", str(tmp_path / "history.db"))
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "agents.db"))
    engine = _build_engine()
    engine.config.llm.initial_greeting = ""
    engine.pipeline_orchestrator._started = True
    stt = _ResultStreamingStubSTT() if streaming else DelayedSTT()
    llm = _RecordingLLM()
    resolution = _StubResolution(stt_adapter=stt, stt_options={"streaming": streaming},
                                 llm_adapter=llm, tts_adapter=_SilentTTS())
    monkeypatch.setattr(engine.pipeline_orchestrator, "get_pipeline", lambda *_args: resolution)
    session = CallSession(call_id=f"late-stt-{streaming}", caller_channel_id=f"late-stt-{streaming}")
    session.pipeline_name = "local_only"
    await engine.session_store.upsert_call(session)
    await engine._ensure_pipeline_runner(session, forced=True)
    runner = engine._pipeline_tasks[session.call_id]
    try:
        if streaming:
            await asyncio.wait_for(stt.started.wait(), timeout=1)
            await stt.results.put("healthy synthetic transcript")
            await asyncio.wait_for(llm.called.wait(), timeout=1)
            assert session.call_id in engine._last_transcript_ts
        else:
            await engine._pipeline_queues[session.call_id].put(bytes(16000))
            await asyncio.wait_for(stt.started.wait(), timeout=1)
        # Cancel the first cleanup await, before producer cancellation. The
        # owned finally still retires diagnostics; a surviving STT must honor
        # the captured session's terminal marker when its request completes.
        original_upsert = engine.session_store.upsert_call
        engine.session_store.upsert_call = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await engine._cleanup_call(session.call_id)
        engine.session_store.upsert_call = original_upsert
        assert session.call_id not in engine._last_transcript_ts
        if streaming:
            await stt.results.put("late synthetic transcript")
        else:
            stt.release.set()
        for _ in range(10):
            await asyncio.sleep(0)
        assert session.call_id not in engine._last_transcript_ts
    finally:
        runner.cancel()
        await asyncio.wait_for(runner, timeout=1)


@pytest.mark.parametrize("transport", ["audiosocket", "externalmedia", "websocket"])
@pytest.mark.parametrize("terminal_flag", ["cleanup_in_progress", "cleanup_completed"])
async def test_terminal_send_cannot_emit_media_or_restore_call_diagnostics(transport, terminal_flag):
    manager, session, writer = await playback_manager(transport)
    segment(manager, session.call_id, "late-stream")
    setattr(session, terminal_flag, True)
    manager.cleanup_call(session.call_id)
    assert not await manager._send_audio_chunk(session.call_id, "late-stream", bytes(160))
    writer.write.assert_not_awaited()
    assert not manager._first_send_logged
    assert not manager._rtp_remote_wait_logged


@pytest.mark.parametrize("transport", ["audiosocket", "externalmedia", "websocket"])
async def test_stale_stream_argument_is_rejected_even_when_entering_after_replacement(transport):
    manager, session, writer = await playback_manager(transport)
    segment(manager, session.call_id, "replacement")
    replacement = dict(manager.active_streams[session.call_id])
    assert not await manager._send_audio_chunk(session.call_id, "old-stream", bytes(160))
    writer.write.assert_not_awaited()
    assert manager.active_streams[session.call_id] == replacement
    assert not manager._first_send_logged
    assert not manager._rtp_remote_wait_logged


@pytest.mark.parametrize("transport", ["audiosocket", "externalmedia", "websocket"])
@pytest.mark.parametrize("callback_fails", [False, True])
async def test_terminal_during_diagnostic_callback_prevents_media_and_state_resurrection(transport, callback_fails):
    manager, session, writer = await playback_manager(transport)
    segment(manager, session.call_id, "late-stream")

    async def diagnostic(*_args):
        session.cleanup_in_progress = True
        manager.cleanup_call(session.call_id)
        if callback_fails:
            raise RuntimeError("synthetic late diagnostic failure")

    manager.audio_diag_callback = diagnostic
    assert not await manager._send_audio_chunk(session.call_id, "late-stream", bytes(160))
    writer.write.assert_not_awaited()
    assert not manager._first_send_logged
    assert not manager._rtp_remote_wait_logged
    assert "diag_callback_failed" not in manager.active_streams[session.call_id]


@pytest.mark.parametrize("terminal_flag", ["cleanup_in_progress", "cleanup_completed"])
async def test_terminal_during_websocket_begin_prevents_late_write(terminal_flag):
    manager, session, writer = await playback_manager("websocket")
    manager.websocket_server = SimpleNamespace()
    segment(manager, session.call_id, "late-stream")

    async def begin(_call_id):
        setattr(session, terminal_flag, True)
        manager.cleanup_call(session.call_id)
        return 7

    writer.begin_output = AsyncMock(side_effect=begin)
    assert not await manager._send_audio_chunk(session.call_id, "late-stream", bytes(160))
    writer.write.assert_not_awaited()
    assert "websocket_output_generation" not in manager.active_streams[session.call_id]


@pytest.mark.parametrize("transport", ["audiosocket", "externalmedia", "websocket"])
@pytest.mark.parametrize("write_succeeded", [False, True])
async def test_late_transport_result_cannot_restore_diagnostics_or_update_retired_state(transport, write_succeeded):
    manager, session, writer = await playback_manager(transport)
    manager.websocket_server = SimpleNamespace()
    writer.begin_output = AsyncMock(return_value=7)
    segment(manager, session.call_id, "late-stream")
    info = manager.active_streams[session.call_id]

    async def write(_frame):
        # The write was submitted while healthy; its result arrives only after
        # terminal teardown. It cannot be unsent, but must cause no new effects.
        session.cleanup_in_progress = True
        manager.cleanup_call(session.call_id)
        return write_succeeded

    writer.write.side_effect = write
    assert not await manager._send_audio_chunk(session.call_id, "late-stream", bytes(160))
    writer.write.assert_awaited_once()
    assert not manager._first_send_logged
    assert not manager._rtp_remote_wait_logged
    assert "tx_bytes" not in info
    assert "first_frame_observed" not in info


async def test_rtp_result_rechecks_stream_argument_after_in_place_owner_change():
    manager, session, writer = await playback_manager("externalmedia")
    segment(manager, session.call_id, "old-stream")
    info = manager.active_streams[session.call_id]

    async def write(_frame):
        info["stream_id"] = "replacement"
        return False

    writer.write.side_effect = write
    assert not await manager._send_audio_chunk(session.call_id, "old-stream", bytes(160))
    assert manager.active_streams[session.call_id] is info
    assert info["stream_id"] == "replacement"
    assert not manager._rtp_remote_wait_logged


async def test_failed_stale_diagnostic_callback_does_not_mutate_replacement():
    manager, session, writer = await playback_manager("externalmedia")
    segment(manager, session.call_id, "old-stream")
    replacement = {"stream_id": "new-stream"}

    async def diagnostic(*_args):
        manager.active_streams[session.call_id] = replacement
        raise RuntimeError("synthetic diagnostic failure after replacement")

    manager.audio_diag_callback = diagnostic
    assert not await manager._send_audio_chunk(session.call_id, "old-stream", bytes(160))
    writer.write.assert_not_awaited()
    assert replacement == {"stream_id": "new-stream"}
    assert not manager._rtp_remote_wait_logged


async def test_debug_broadcast_stops_remaining_recipients_after_terminal_write_result():
    manager, session, writer = await playback_manager()
    manager.audiosocket_broadcast_debug = True
    session.audiosocket_conns = ["conn", "other-conn"]
    segment(manager, session.call_id, "late-stream")

    async def write(_frame):
        session.cleanup_in_progress = True
        manager.cleanup_call(session.call_id)
        return True

    writer.write.side_effect = write
    assert not await manager._send_audio_chunk(session.call_id, "late-stream", bytes(160))
    writer.write.assert_awaited_once()
    assert not manager._first_send_logged
