"""Overflow and announcement failure must never authorize successful routing."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from src.core.models import CallSession
from src.tools.context import ToolExecutionContext
from tests.test_websocket_engine_integration import _bare_engine
from tests.test_deferred_transfer_commit_audio_gate import _build_engine, _armed_session, _agent_audio


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_failed_announcement_wakes_waiter_without_claiming_spoken_audio(started):
    engine = _bare_engine()
    session = CallSession(call_id="call", caller_channel_id="call")
    await engine.session_store.upsert_call(session)
    operation = engine._begin_provider_output_operation("call", "apology", "transfer_recovery")
    if started:
        operation["audio_started"].set()
    engine._wait_for_call_audio_drain = AsyncMock(return_value=True)
    waiting = asyncio.create_task(engine._wait_for_no_input_announcement("call", announcement_id="apology"))
    await asyncio.sleep(0)
    await engine._note_provider_output_end("call", session, failed=True)
    assert await asyncio.wait_for(waiting, 0.2) is False
    engine._wait_for_call_audio_drain.assert_not_awaited()


@pytest.mark.asyncio
async def test_announcement_failure_racing_drain_cannot_report_success():
    engine = _bare_engine()
    session = CallSession(call_id="call", caller_channel_id="call")
    await engine.session_store.upsert_call(session)
    operation = engine._begin_provider_output_operation("call", "apology", "transfer_recovery")
    operation["audio_started"].set()
    await engine._note_provider_output_end("call", session)
    async def drain(*args, **kwargs):
        operation["failed"] = True
        return True
    engine._wait_for_call_audio_drain = drain
    assert not await engine._wait_for_no_input_announcement("call", announcement_id="apology")
    assert not (await engine.session_store.get_by_call_id("call")).tts_tokens


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "asterisk"])
async def test_full_provider_queue_fails_remote_clock_call_once_and_blocks_late_events(mode):
    engine = _build_engine()
    engine.config.audio_transport = "websocket"
    engine.config.websocket_media.output_pacing = mode
    session = CallSession(call_id="overflow", caller_channel_id="overflow")
    await engine.session_store.upsert_call(session)
    q = asyncio.Queue(maxsize=1)
    q.put_nowait(b"old")
    engine._provider_stream_queues[session.call_id] = q
    scheduled = []
    def schedule(call, coroutine, **kwargs):
        scheduled.append((call, kwargs))
        coroutine.close()  # Verify ownership separately below, with no live ARI.
    engine._fire_and_forget_for_call = schedule
    engine._commit_pending_deferred_transfer_for_call = AsyncMock()
    await engine.on_provider_event(_agent_audio(session.call_id))
    if mode == "legacy":
        assert not session.audio_diagnostics.get("provider_queue_failed")
        assert not scheduled
        return
    assert session.audio_diagnostics["provider_queue_failed"] is True
    assert len(scheduled) == 1
    await engine.on_provider_event(_agent_audio(session.call_id))
    await engine.on_provider_event({"type": "AgentAudioDone", "call_id": session.call_id, "streaming_done": True})
    await engine.on_provider_event({"type": "HandoffAudioComplete", "call_id": session.call_id})
    assert len(scheduled) == 1
    engine._commit_pending_deferred_transfer_for_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("committing", [False, True])
async def test_overflow_owner_cancels_only_uncommitted_deferred_action(committing):
    engine = _build_engine()
    session = await _armed_session(engine, "overflow")
    session.audio_diagnostics["provider_queue_failed"] = True
    engine.on_provider_event = AsyncMock()
    engine._provider_audio_blocked_by_transfer_commit = Mock(return_value=committing)
    engine._recover_failed_deferred_transfer = AsyncMock()
    engine._terminate_call_after_audio = AsyncMock()
    await engine._terminate_overflowed_provider_audio(session.call_id)
    if committing:
        engine._recover_failed_deferred_transfer.assert_not_awaited()
    else:
        engine._recover_failed_deferred_transfer.assert_awaited_once_with(session.call_id, action_id="action-overflow")
    engine._terminate_call_after_audio.assert_awaited_once_with(
        session.call_id, reason="provider_audio_queue_overflow", audio_already_drained=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("predial", [False, True])
async def test_overflow_transfer_recovery_records_failure_without_requesting_apology(predial):
    engine = _build_engine()
    session = await _armed_session(engine, "overflow")
    session.audio_diagnostics["provider_queue_failed"] = True
    if predial:
        session.pending_deferred_transfer["payload"] = {"predial": {"channel_id": "destination"}}
    engine._record_deferred_transfer_timeout_tool_result = AsyncMock()
    engine._speak_no_input_announcement = AsyncMock(return_value=True)
    engine._terminate_call_after_audio = AsyncMock()
    engine._schedule_deferred_predial_forced_hangup_retry = Mock()
    engine._release_deferred_predial_hangup_ownership = Mock()
    engine.ari_client.hangup_channel = AsyncMock(return_value=True)
    engine.ari_client.send_command = AsyncMock()
    result = await engine._recover_failed_deferred_transfer(session.call_id, action_id="action-overflow")
    assert result["error_code"] == "provider_audio_queue_overflow"
    assert result["status"] == "failed" and result["transfer_cancelled"] is True
    assert result["apology_spoken"] is False
    assert session.pending_deferred_transfer is None
    engine._speak_no_input_announcement.assert_not_awaited()
    engine._terminate_call_after_audio.assert_awaited_once_with(
        session.call_id, reason="provider_audio_queue_overflow", audio_already_drained=True,
    )
    assert engine._record_deferred_transfer_timeout_tool_result.await_args.kwargs["error_code"] == "provider_audio_queue_overflow"
    if predial:
        engine._schedule_deferred_predial_forced_hangup_retry.assert_called_once_with(call_id="overflow", channel_id="destination")
        engine.ari_client.hangup_channel.assert_awaited_once_with("destination")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["blind_transfer", "cancel_transfer", "hangup_call", "calendar_create_event"])
async def test_native_tool_boundary_is_closed_after_audio_queue_failure(tool):
    engine = _bare_engine()
    session = CallSession(call_id="call", caller_channel_id="call")
    session.audio_diagnostics["provider_queue_failed"] = True
    await engine.session_store.upsert_call(session)
    context = ToolExecutionContext(call_id="call", caller_channel_id="call", session_store=engine.session_store)
    blocked = await context.get_tool_block_response(tool)
    assert blocked["error_code"] == "provider_audio_queue_overflow"
    session.audio_diagnostics.pop("provider_queue_failed")
    assert await context.get_tool_block_response(tool) is None
