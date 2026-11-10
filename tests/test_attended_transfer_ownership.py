"""A superseded attended-transfer workflow must not disturb its replacement."""

import asyncio

import pytest

from src.tools.telephony.attended_transfer import AttendedTransferTool
from tests.test_attended_transfer_pre_dial_audio import CFG, _tool_fixture
from tests.test_attended_transfer_streaming import _build_engine


@pytest.mark.asyncio
@pytest.mark.parametrize("screening_mode", ["basic_tts", "ai_briefing"])
@pytest.mark.parametrize("old_result", ["audio", "empty", "error"])
async def test_superseded_preparation_preserves_new_transfer(screening_mode, old_result):
    cfg = {**CFG, "screening_mode": screening_mode}
    engine = _build_engine(cfg)
    engine.events = []
    session, ari, context = _tool_fixture(cfg, engine, "ownership-race")
    await engine.session_store.upsert_call(session)
    started = asyncio.Event()
    release = asyncio.Event()

    class Synth:
        count = 0

        async def synthesize_once(self, *args, **kwargs):
            self.count += 1
            # Basic screening prepares two texts; AI briefing prepares only
            # the accept prompt. Hold all of the first attempt's requests.
            if self.count <= (2 if screening_mode == "basic_tts" else 1):
                started.set()
                await release.wait()
                if old_result == "error":
                    raise RuntimeError("synthesis failed")
                if old_result == "empty":
                    return b""
            return b"\xff" * 160

    engine.pipeline_orchestrator = Synth()
    tool = AttendedTransferTool()
    first = asyncio.create_task(tool.execute({"destination": "support_agent"}, context))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        second = await asyncio.wait_for(
            tool.execute({"destination": "support_agent"}, context), timeout=2
        )
        assert second["status"] == "success"
        replacement = session.current_action
        assert replacement["agent_channel_id"] == "agent-chan"
        calls_before = list(ari.calls)
        release.set()
        result = await asyncio.wait_for(first, timeout=2)
        assert result["status"] == "failed"
        assert session.current_action is replacement
        assert session.audio_capture_enabled is False
        # No stale MOH stop, second originate, or destination hangup.
        assert ari.calls == calls_before
    finally:
        release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_superseded_origination_hangs_up_only_its_own_leg():
    # Exercise the legacy/default TTS path too, where there is no preparation.
    session, ari, context = _tool_fixture({}, None, "origination-race")
    started = asyncio.Event()
    release = asyncio.Event()
    original_send = ari.send_command
    originates = 0

    async def delayed_originate(**kwargs):
        nonlocal originates
        result = await original_send(**kwargs)
        if kwargs["method"] == "POST" and kwargs["resource"] == "channels":
            originates += 1
            if originates == 1:
                started.set()
                await release.wait()
                return {"id": "old-agent"}
        return result

    ari.send_command = delayed_originate
    tool = AttendedTransferTool()
    first = asyncio.create_task(tool.execute({"destination": "support_agent"}, context))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        second = await tool.execute({"destination": "support_agent"}, context)
        assert second["status"] == "success"
        replacement = session.current_action
        calls_before = len(ari.calls)
        release.set()
        result = await asyncio.wait_for(first, timeout=2)
        assert result["status"] == "failed"
        assert session.current_action is replacement
        assert replacement["agent_channel_id"] == "agent-chan"
        assert session.audio_capture_enabled is False
        assert ari.calls[calls_before:] == [
            {"method": "DELETE", "resource": "channels/old-agent", "data": None, "params": None}
        ]
    finally:
        release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_stale_cleanup_leaves_replacement_hold_music_and_capture_alone():
    session, ari, context = _tool_fixture({}, None, "cleanup-race")
    old_attempt = {"type": "attended_transfer"}
    replacement = {"type": "attended_transfer", "pre_transfer_audio_capture_enabled": True}
    session.current_action = replacement
    session.audio_capture_enabled = False

    await AttendedTransferTool()._cleanup_failed_originate(
        context, session.call_id, attempt=old_attempt
    )

    assert ari.calls == []
    assert session.current_action is replacement
    assert session.audio_capture_enabled is False
    assert replacement["pre_transfer_audio_capture_enabled"] is True


@pytest.mark.asyncio
async def test_replacement_failure_restores_capture_from_before_first_transfer():
    from tests.test_attended_transfer_pre_dial_audio import _PrepEngine

    engine = _PrepEngine([], ok=False)
    session, ari, context = _tool_fixture(CFG, engine, "capture-race")
    # First transfer already paused capture while waiting for its provider.
    session.current_action = {
        "type": "attended_transfer", "pre_transfer_audio_capture_enabled": True
    }
    session.audio_capture_enabled = False

    result = await AttendedTransferTool().execute({"destination": "support_agent"}, context)

    assert result["status"] == "failed"
    assert session.current_action is None
    assert session.audio_capture_enabled is True


@pytest.mark.asyncio
async def test_replaced_during_originate_persistence_does_not_register_old_leg():
    from unittest.mock import Mock

    session, ari, context = _tool_fixture({}, None, "persist-race")
    started = asyncio.Event()
    release = asyncio.Event()
    engine = Mock(spec=["register_attended_transfer_agent_channel", "start_attended_transfer_timeout_guard"])
    ari.engine = engine
    original_upsert = context.session_store.upsert_call
    original_send = ari.send_command
    originates = 0

    async def named_originate(**kwargs):
        nonlocal originates
        result = await original_send(**kwargs)
        if kwargs["method"] == "POST" and kwargs["resource"] == "channels":
            originates += 1
            return {"id": "old-agent" if originates == 1 else "new-agent"}
        return result

    async def delayed_upsert(call):
        await original_upsert(call)
        if (call.current_action or {}).get("agent_channel_id") == "old-agent":
            started.set()
            await release.wait()

    ari.send_command = named_originate
    context.session_store.upsert_call = delayed_upsert
    tool = AttendedTransferTool()
    first = asyncio.create_task(tool.execute({"destination": "support_agent"}, context))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        second = await tool.execute({"destination": "support_agent"}, context)
        assert second["status"] == "success"
        replacement = session.current_action
        release.set()
        result = await asyncio.wait_for(first, timeout=2)
        assert result["status"] == "failed"
        assert session.current_action is replacement
        assert replacement["agent_channel_id"] == "new-agent"
        engine.register_attended_transfer_agent_channel.assert_called_once_with(session.call_id, "new-agent")
        engine.start_attended_transfer_timeout_guard.assert_called_once_with(
            session.call_id, "new-agent", timeout_sec=30
        )
        assert any(c["method"] == "DELETE" and c["resource"] == "channels/old-agent" for c in ari.calls)
    finally:
        release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_task", [False, True], ids=["cancel-tool", "cancel-task"])
async def test_cancel_during_preparation_restores_caller_without_dialling(cancel_task):
    from src.tools.telephony.cancel_transfer import CancelTransferTool
    from tests.test_attended_transfer_pre_dial_audio import _PrepEngine

    started = asyncio.Event()
    release = asyncio.Event()

    class WaitingEngine(_PrepEngine):
        async def prepare_attended_transfer_audio(self, **kwargs):
            started.set()
            await release.wait()
            return True

    engine = WaitingEngine([])
    session, ari, context = _tool_fixture(CFG, engine, "cancel-preparation")
    session.audio_capture_enabled = True
    task = asyncio.create_task(AttendedTransferTool().execute({"destination": "support_agent"}, context))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert session.audio_capture_enabled is False
        if cancel_task:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await CancelTransferTool().execute({}, context)
            assert result["status"] == "success"
            release.set()
            result = await asyncio.wait_for(task, timeout=2)
            assert result["status"] == "failed"
        assert session.current_action is None
        assert session.audio_capture_enabled is True
        assert not any(c["method"] == "POST" and c["resource"] == "channels" for c in ari.calls)
        stops = [c for c in ari.calls if c["method"] == "DELETE" and c["resource"].endswith("/moh")]
        assert len(stops) == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
