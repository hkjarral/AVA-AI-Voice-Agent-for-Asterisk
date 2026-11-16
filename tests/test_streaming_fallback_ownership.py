"""Ownership rejection must never revive caller output through file fallback (#864)."""
import asyncio
import audioop
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.conversation_coordinator import ConversationCoordinator
from src.core.models import CallSession
from src.core.no_input_watchdog import NoInputPolicy, NoInputWatchdog
from src.core.playback_manager import PlaybackManager
from src.core.session_store import SessionStore
from src.core.streaming_playback_manager import StreamingPlaybackManager


@pytest.fixture
async def rig():
    store = SessionStore()
    session = CallSession(call_id="fallback-audit", caller_channel_id="caller",
                          bridge_id="bridge", audiosocket_conn_id="conn")
    await store.upsert_call(session)
    tasks = set()

    def schedule(_call_id, coroutine, **_kwargs):
        task = asyncio.create_task(coroutine)
        tasks.add(task)
        return task

    ari = SimpleNamespace(play_media_on_bridge_with_id=AsyncMock(return_value=True),
                          stop_playback=AsyncMock(return_value=True),
                          engine=SimpleNamespace(_fire_and_forget_for_call=schedule))
    files = PlaybackManager.__new__(PlaybackManager)
    files.session_store, files.ari_client = store, ari
    files.conversation_coordinator = ConversationCoordinator(store, files)
    files._last_playback_ts = files._playback_seq = 0
    files._create_audio_file = AsyncMock(return_value="/tmp/synthetic-fallback-not-written.ulaw")
    files._cleanup_audio_file = AsyncMock()
    files._schedule_gating_fallback = AsyncMock()
    manager = StreamingPlaybackManager(store, ari, fallback_playback_manager=files,
        streaming_config={"diag_enable_taps": False, "provider_grace_ms": 0},
        audio_transport="audiosocket")
    manager.audiosocket_server = SimpleNamespace()
    writer = SimpleNamespace(write=AsyncMock(return_value=False))
    manager._selected_output_transport = lambda: writer
    old, new, queued = [struct.pack("<h", n) * 160 for n in (1000, 2000, 3000)]
    info = {"stream_id": "old-stream", "target_format": "slin", "target_sample_rate": 8000,
            "source_encoding": "slin16", "source_sample_rate": 8000, "startup_ready": True,
            "buffered_bytes": len(old), "playback_type": "response"}
    manager.active_streams[session.call_id] = info
    manager._startup_ready[session.call_id] = True
    manager.frame_remainders[session.call_id] = old
    queue = asyncio.Queue()
    manager.jitter_buffers[session.call_id] = queue
    value = SimpleNamespace(store=store, session=session, ari=ari, files=files, manager=manager,
        writer=writer, old=old, new=new, queued=queued, info=info, queue=queue, replacement=None,
        tasks=tasks)

    def replace():
        value.replacement = dict(info, stream_id="replacement", buffered_bytes=len(new)+len(queued),
                                 end_reason="healthy-replacement")
        manager.active_streams[session.call_id] = value.replacement
        manager.frame_remainders[session.call_id] = new
        manager.jitter_buffers[session.call_id] = asyncio.Queue()
        manager.jitter_buffers[session.call_id].put_nowait(queued)

    value.replace = replace
    async def replace_gated():
        replace()
        await store.set_gating_token(session.call_id, "replacement-token")
    value.replace_gated = replace_gated
    yield value
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def assert_replacement_untouched(rig):
    manager, session = rig.manager, rig.session
    assert manager.active_streams[session.call_id] is rig.replacement
    assert rig.replacement["end_reason"] == "healthy-replacement"
    assert rig.replacement["buffered_bytes"] == len(rig.new)+len(rig.queued)
    assert manager.frame_remainders[session.call_id] == rig.new
    assert manager.jitter_buffers[session.call_id].qsize() == 1


@pytest.mark.parametrize("transport", ["audiosocket", "externalmedia"])
@pytest.mark.parametrize("change", ["healthy_failure", "cleanup_in_progress", "cleanup_completed", "replacement"])
async def test_real_pacer_preserves_healthy_fallback_but_cancels_lost_owner(rig, transport, change):
    rig.manager.audio_transport = transport
    rig.manager.rtp_server = SimpleNamespace(has_remote_endpoint=lambda _call_id: True)
    observed = {}
    original_emit = rig.manager._emit_frame

    async def emit(*args, **kwargs):
        status = await original_emit(*args, **kwargs)
        observed.update(status=status, retained=rig.manager.frame_remainders.get(rig.session.call_id))
        return status

    rig.manager._emit_frame = emit
    if change != "healthy_failure":
        async def diagnostic(*_args):
            if change == "replacement":
                rig.replace()
            else:
                setattr(rig.session, change, True)
                rig.manager.frame_remainders.pop(rig.session.call_id, None)
                rig.manager.cleanup_call(rig.session.call_id)
        rig.manager.audio_diag_callback = diagnostic
    await asyncio.wait_for(rig.manager._pacer_loop(rig.session.call_id, "old-stream", rig.queue), timeout=1)
    if change == "healthy_failure":
        assert observed == {"status": "error", "retained": rig.old}
        rig.ari.play_media_on_bridge_with_id.assert_awaited_once()
        assert rig.files._create_audio_file.await_args.args[0] == audioop.lin2ulaw(rig.old, 2)
        assert rig.session.streaming_fallback_count == 1
        return
    assert observed["status"] == "cancelled"
    rig.writer.write.assert_not_awaited()
    rig.ari.play_media_on_bridge_with_id.assert_not_awaited()
    assert rig.session.streaming_fallback_count == 0
    assert not rig.session.tts_tokens
    if change == "replacement":
        assert_replacement_untouched(rig)
    else:
        assert observed["retained"] is None
        assert rig.session.call_id not in rig.manager.frame_remainders


@pytest.mark.parametrize("change", ["cleanup_in_progress", "cleanup_completed", "replacement", "deleted_session"])
async def test_direct_fallback_rejects_invalid_owner_without_consuming_buffers(rig, change):
    if change == "replacement":
        rig.replace()
    elif change == "deleted_session":
        await rig.store.remove_call(rig.session.call_id)
    else:
        setattr(rig.session, change, True)
    await rig.manager._fallback_to_file_playback(rig.session.call_id, "old-stream")
    rig.files._create_audio_file.assert_not_awaited()
    rig.ari.play_media_on_bridge_with_id.assert_not_awaited()
    if change == "replacement":
        assert_replacement_untouched(rig)
    else:
        assert rig.manager.frame_remainders[rig.session.call_id] == rig.old


@pytest.mark.parametrize("stage", ["session_lookup", "conversion", "fallback_record"])
async def test_continuation_awaits_cannot_consume_or_mark_replacement(rig, stage):
    if stage == "session_lookup":
        get = rig.store.get_by_call_id
        async def lookup(call_id):
            session = await get(call_id)
            rig.replace()
            return session
        rig.store.get_by_call_id = lookup
        await rig.manager._fallback_to_file_playback(rig.session.call_id, "old-stream")
    elif stage == "conversion":
        rig.manager.frame_remainders.clear()
        rig.queue.put_nowait(rig.old)
        async def process(*_args):
            rig.replace()
            return rig.old
        rig.manager._process_audio_chunk = process
        assert await rig.manager._drain_next_frame(rig.session.call_id,"old-stream",rig.queue)=="cancelled"
    else:
        upsert = rig.store.upsert_call
        async def save(session):
            await upsert(session)
            rig.replace()
        rig.store.upsert_call = save
        await asyncio.wait_for(rig.manager._pacer_loop(rig.session.call_id,"old-stream",rig.queue), timeout=1)
    assert_replacement_untouched(rig)
    rig.ari.play_media_on_bridge_with_id.assert_not_awaited()


@pytest.mark.parametrize("stage", ["file", "gating", "ref"])
@pytest.mark.parametrize("change", ["terminal", "replacement"])
async def test_file_playback_preparation_revalidates_owner_before_ari(rig, stage, change):
    async def lose_owner():
        if change == "replacement":
            await rig.replace_gated()
        else:
            rig.session.cleanup_in_progress = True
    if stage == "file":
        async def create(*_args):
            await lose_owner()
            return "/tmp/synthetic-fallback-not-written.ulaw"
        rig.files._create_audio_file.side_effect = create
    elif stage == "gating":
        original = rig.files.conversation_coordinator.on_tts_start
        async def gating(*args):
            result = await original(*args)
            await lose_owner()
            return result
        rig.files.conversation_coordinator.on_tts_start = gating
    else:
        original = rig.store.add_playback
        async def ref(value):
            await original(value)
            await lose_owner()
        rig.store.add_playback = ref
    await rig.manager._fallback_to_file_playback(rig.session.call_id, "old-stream")
    rig.ari.play_media_on_bridge_with_id.assert_not_awaited()
    rig.files._cleanup_audio_file.assert_awaited_once()
    assert rig.session.tts_tokens == ({"replacement-token"} if change == "replacement" else set())
    assert not await rig.store.list_playbacks_for_call(rig.session.call_id)
    if change == "replacement":
        assert_replacement_untouched(rig)


@pytest.mark.parametrize("stop_confirmed", [True, False])
async def test_owner_loss_during_ari_reuses_exact_playback_stop_and_retry_owner(rig, stop_confirmed):
    rig.ari.stop_playback.return_value = stop_confirmed
    async def play(*_args):
        await rig.replace_gated()
        return True
    rig.ari.play_media_on_bridge_with_id.side_effect = play
    await rig.manager._fallback_to_file_playback(rig.session.call_id, "old-stream")
    rig.ari.play_media_on_bridge_with_id.assert_awaited_once()
    rig.ari.stop_playback.assert_awaited_once()
    playback_id = rig.ari.play_media_on_bridge_with_id.await_args.args[2]
    assert rig.ari.stop_playback.await_args.args == (playback_id,)
    assert_replacement_untouched(rig)
    assert "replacement-token" in rig.session.tts_tokens
    assert rig.session.tts_playing and not rig.session.audio_capture_enabled
    ref = await rig.store.get_playback(playback_id)
    if stop_confirmed:
        assert ref is None and playback_id not in rig.session.tts_tokens
        rig.files._cleanup_audio_file.assert_awaited_once()
    else:
        assert ref is not None and ref.cancel_cleanup_pending
        assert playback_id in rig.session.tts_tokens
        rig.files._cleanup_audio_file.assert_not_awaited()
    rig.files._schedule_gating_fallback.assert_not_awaited()


@pytest.mark.parametrize("stage", ["playback_lookup", "timer"])
async def test_file_playback_checks_ownership_after_its_own_awaits(rig, stage):
    if stage == "playback_lookup":
        original_play = rig.files.play_audio
        async def delayed_play(*args, **kwargs):
            original_get = rig.store.get_by_call_id
            async def lookup(call_id):
                session = await original_get(call_id)
                await rig.replace_gated()
                return session
            rig.store.get_by_call_id = lookup
            return await original_play(*args, **kwargs)
        rig.files.play_audio = delayed_play
    else:
        async def timer(*_args):
            await rig.replace_gated()
        rig.files._schedule_gating_fallback.side_effect = timer
    await rig.manager._fallback_to_file_playback(rig.session.call_id, "old-stream")
    assert_replacement_untouched(rig)
    assert rig.session.tts_tokens == {"replacement-token"}
    if stage == "playback_lookup":
        rig.files._create_audio_file.assert_not_awaited()
        rig.ari.play_media_on_bridge_with_id.assert_not_awaited()
    else:
        rig.ari.play_media_on_bridge_with_id.assert_awaited_once()
        rig.ari.stop_playback.assert_awaited_once()


async def test_fallback_bookkeeping_failure_does_not_disable_owned_transport_recovery(rig):
    rig.store.upsert_call = AsyncMock(side_effect=RuntimeError("synthetic diagnostic store failure"))
    await asyncio.wait_for(rig.manager._pacer_loop(rig.session.call_id, "old-stream", rig.queue), timeout=1)
    rig.ari.play_media_on_bridge_with_id.assert_awaited_once()
    assert rig.files._create_audio_file.await_args.args[0] == audioop.lin2ulaw(rig.old, 2)
    assert rig.info["end_reason"] == "transport-failure"


@pytest.mark.parametrize("finish", ["retry", "ari_event", "repeated_retry"])
@pytest.mark.parametrize("notify", ["default", True, False])
async def test_cancelled_playback_finish_preserves_caller_idle_deadline(rig, monkeypatch, finish, notify):
    """A stale playback must not buy the ready caller a fresh inactivity window."""
    import src.core.playback_manager as playback_module

    clock = [100.0]
    watchdog = NoInputWatchdog(AsyncMock(), AsyncMock(), clock=lambda: clock[0])
    await watchdog.register(rig.session.call_id, NoInputPolicy(), is_outbound=False)
    await watchdog.mark_ready(rig.session.call_id)
    rig.files.conversation_coordinator._no_input_watchdog = watchdog
    retry_allowed = asyncio.Event()
    monkeypatch.setattr(playback_module, "_CANCEL_RETRY_DELAY", 0.001)
    try:
        playback_id = await rig.files.play_audio(rig.session.call_id, b"synthetic")
        assert playback_id
        stops = 0

        async def stop_exact(actual_id):
            nonlocal stops
            assert actual_id == playback_id
            stops += 1
            if stops == 1:
                return False
            await retry_allowed.wait()
            return True

        rig.ari.stop_playback.side_effect = stop_exact
        options = {} if notify == "default" else {"notify_no_input": notify}
        await rig.files._cancel_playback_start(
            rig.session.call_id, playback_id, "/tmp/synthetic-fallback-not-written.ulaw",
            True, **options,
        )
        assert (await rig.store.get_playback(playback_id)).cancel_cleanup_pending
        if finish == "repeated_retry":
            # A later default caller must not weaken an already suppressed owner.
            await rig.files.retain_cancelled_playback(rig.session.call_id, playback_id)

        # The current provider output lifecycle has already ended. The old
        # playback's eventual stop/finish cannot reset that established deadline.
        await watchdog.note_agent_output_end(rig.session.call_id)
        before = watchdog.snapshot(rig.session.call_id)["deadline"]
        assert before == 130.0
        clock[0] = 105.0
        if finish == "ari_event":
            from src.engine import Engine
            engine = Engine.__new__(Engine)
            engine._ari_playback_waiters = {}
            engine.playback_manager = rig.files
            await engine._on_playback_finished({"playback": {"id": playback_id}})
        retry_allowed.set()
        await asyncio.wait_for(asyncio.gather(*list(rig.tasks)), timeout=1)
        assert watchdog.snapshot(rig.session.call_id)["deadline"] == (before if notify is False else 135.0)
        assert await rig.store.get_playback(playback_id) is None
        assert not rig.session.tts_tokens
        rig.files._cleanup_audio_file.assert_awaited_once()
    finally:
        retry_allowed.set()
        await watchdog.stop(rig.session.call_id)


async def test_duplicate_cancel_keeps_suppression_when_ari_finish_pops_ref_during_stop(rig, monkeypatch):
    """A default outer cancellation must retain the old owner's false policy."""
    import src.core.playback_manager as playback_module
    from src.engine import Engine

    clock = [100.0]
    watchdog = NoInputWatchdog(AsyncMock(), AsyncMock(), clock=lambda: clock[0])
    await watchdog.register(rig.session.call_id, NoInputPolicy(), is_outbound=False)
    await watchdog.mark_ready(rig.session.call_id)
    rig.files.conversation_coordinator._no_input_watchdog = watchdog
    monkeypatch.setattr(playback_module, "_CANCEL_RETRY_DELAY", 60.0)
    try:
        playback_id = await rig.files.play_audio(rig.session.call_id, b"synthetic")
        rig.ari.stop_playback.return_value = False
        await rig.files._cancel_playback_start(
            rig.session.call_id, playback_id, "/tmp/synthetic-fallback-not-written.ulaw",
            True, notify_no_input=False,
        )
        await watchdog.note_agent_output_end(rig.session.call_id)
        assert watchdog.snapshot(rig.session.call_id)["deadline"] == 130.0
        clock[0] = 105.0
        engine = Engine.__new__(Engine)
        engine._ari_playback_waiters = {}
        engine.playback_manager = rig.files

        async def stop_after_finish(actual_id):
            assert actual_id == playback_id
            await engine._on_playback_finished({"playback": {"id": playback_id}})
            assert await rig.store.get_playback(playback_id) is None
            assert watchdog.snapshot(rig.session.call_id)["deadline"] == 130.0
            return True

        rig.ari.stop_playback.side_effect = stop_after_finish
        await rig.files._cancel_playback_start(
            rig.session.call_id, playback_id, "/tmp/synthetic-fallback-not-written.ulaw", True,
        )
        assert watchdog.snapshot(rig.session.call_id)["deadline"] == 130.0
        assert not rig.session.tts_tokens
        assert await rig.store.get_playback(playback_id) is None
    finally:
        await watchdog.stop(rig.session.call_id)
