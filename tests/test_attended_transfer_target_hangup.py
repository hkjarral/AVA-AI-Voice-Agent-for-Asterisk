"""An answered destination that hangs up before accepting is a decline.

Synthetic ARI end events drive the real cleanup and recovery paths. The
caller must be resumed once, never torn down, and the screening handler must
end cleanly whichever step it was in.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

import src.engine as engine_module
from src.core.models import CallSession
from tests.test_attended_transfer_streaming import _build_engine

DECLINED = "Sorry, {destination_description} is not available."
PROMPT = "Press 1 to accept this transfer, or 2 to decline."


async def _end_leg(engine, channel_id, kind):
    event = {"type": kind, "channel": {"id": channel_id}, "cause": 16, "cause_txt": "Normal Clearing"}
    if kind == "StasisEnd":
        await engine._handle_stasis_end(event)
    else:
        await engine._handle_channel_destroyed(event)


class _ClaimLog(dict):
    """Attempt dict that logs every recovery claim."""

    def __init__(self, rec, *args):
        super().__init__(*args)
        self._rec = rec

    def __setitem__(self, key, value):
        if key == "recovery_started" and value:
            self._rec["claims"].append(self.get("agent_channel_id"))
        super().__setitem__(key, value)


@pytest.fixture
async def screening(monkeypatch, request):
    monkeypatch.setattr(engine_module, "_cleanup_lock", asyncio.Lock())
    engine = _build_engine({
        "enabled": True,
        "caller_declined_prompt": DECLINED,
        "accept_timeout_seconds": 5,
        "tts_timeout_seconds": 1,
    })
    call_id = "target-hangup-" + request.node.name
    session = CallSession(call_id=call_id, caller_channel_id=f"{call_id}-caller")
    session.audio_capture_enabled = False
    rec = {"events": [], "tts": [], "plays": [], "claims": []}
    session.current_action = _ClaimLog(rec, {
        "type": "attended_transfer",
        "agent_channel_id": "agent-ringing",
        "target_name": "Support agent",
    })
    await engine.session_store.upsert_call(session)
    engine.register_attended_transfer_agent_channel(call_id, "agent-ringing")

    real_unregister = engine._unregister_attended_transfer_agent_channel
    real_abort = engine._attended_transfer_abort_and_resume

    def unregister(channel_id):
        rec["events"].append(("unregister", channel_id))
        real_unregister(channel_id)

    async def abort(session_obj, agent_channel_id, *, reason):
        rec["events"].append(("abort", agent_channel_id, reason))
        await real_abort(session_obj, agent_channel_id, reason=reason)

    async def tts(*, call_id, text, timeout_sec):
        rec["tts"].append(text)
        return b"\xff" * 160

    async def play(*, channel_id, audio_bytes, playback_id_prefix, timeout_sec):
        rec["plays"].append((channel_id, playback_id_prefix))
        return f"{playback_id_prefix}-ok"

    monkeypatch.setattr(engine, "_unregister_attended_transfer_agent_channel", unregister)
    monkeypatch.setattr(engine, "_attended_transfer_abort_and_resume", abort)
    monkeypatch.setattr(engine, "_local_ai_server_tts", tts)
    monkeypatch.setattr(engine, "_play_ulaw_bytes_on_channel_and_wait", play)
    monkeypatch.setattr(
        engine.ari_client, "hangup_channel",
        AsyncMock(side_effect=lambda ch: rec["events"].append(("hangup", ch))),
    )
    for name in ("send_command", "destroy_bridge", "add_channel_to_bridge", "remove_channel_from_bridge"):
        monkeypatch.setattr(engine.ari_client, name, AsyncMock(return_value=True))
    for name in ("_persist_call_history", "_persist_abandoned_call_history", "_execute_post_call_tools"):
        monkeypatch.setattr(engine, name, AsyncMock())
    return engine, session, rec


def _outcomes(session):
    return [c.get("message") for c in session.tool_calls]


def _assert_declined_once(engine, session, rec, agent):
    events = rec["events"]
    first_abort = next(e for e in events if e[0] == "abort")
    assert first_abort == ("abort", agent, "target-hangup")
    assert events.index(("unregister", agent)) < events.index(first_abort)
    assert events.index(first_abort) < events.index(("hangup", agent))
    assert [e for e in events if e[0] == "hangup"] == [("hangup", agent)]
    assert len(rec["claims"]) == 1
    assert _outcomes(session) == ["Transfer failed (target-hangup)."]
    assert rec["tts"].count("Sorry, Support agent is not available.") == 1
    assert (session.caller_channel_id, "attx-decline") in rec["plays"]
    assert session.current_action is None
    assert session.audio_capture_enabled is True
    assert agent not in engine._attended_transfer_agent_channel_to_call_id
    assert agent not in engine._attended_transfer_dtmf_waiters


@pytest.mark.asyncio
@pytest.mark.parametrize("announcement_result", [None, "attx-ann-ok"])
async def test_hangup_during_announcement_declines(screening, monkeypatch, announcement_result):
    engine, session, rec = screening
    agent = "agent-ann"
    base_play = engine._play_ulaw_bytes_on_channel_and_wait

    async def play(*, channel_id, audio_bytes, playback_id_prefix, timeout_sec):
        if playback_id_prefix == "attx-ann":
            rec["plays"].append((channel_id, playback_id_prefix))
            await _end_leg(engine, agent, "ChannelDestroyed")
            # The playback can report failure or race to a finished event.
            return announcement_result
        return await base_play(channel_id=channel_id, audio_bytes=audio_bytes,
                               playback_id_prefix=playback_id_prefix, timeout_sec=timeout_sec)

    monkeypatch.setattr(engine, "_play_ulaw_bytes_on_channel_and_wait", play)
    await engine._handle_attended_transfer_answered(agent, ["attended-transfer", session.call_id, "support_agent"])

    assert await engine.session_store.get_by_call_id(session.call_id) is session
    _assert_declined_once(engine, session, rec, agent)
    assert PROMPT not in rec["tts"]


@pytest.mark.asyncio
async def test_hangup_during_digit_wait_declines_and_handler_ends_cleanly(screening):
    engine, session, rec = screening
    agent = "agent-dtmf"
    handler = asyncio.create_task(
        engine._handle_attended_transfer_answered(agent, ["attended-transfer", session.call_id, "support_agent"])
    )
    for _ in range(200):
        if agent in engine._attended_transfer_dtmf_waiters:
            break
        await asyncio.sleep(0)
    assert agent in engine._attended_transfer_dtmf_waiters

    await _end_leg(engine, agent, "StasisEnd")
    await _end_leg(engine, agent, "ChannelDestroyed")
    await asyncio.wait_for(handler, 1)

    assert not handler.cancelled()
    assert await engine.session_store.get_by_call_id(session.call_id) is session
    _assert_declined_once(engine, session, rec, agent)
    assert [e for e in rec["events"] if e[0] == "abort"] == [("abort", agent, "target-hangup")]


@pytest.mark.asyncio
async def test_paired_end_events_resume_once(screening):
    engine, session, rec = screening
    agent = "agent-ringing"
    session.current_action.update({"answered": True})

    await asyncio.gather(_end_leg(engine, agent, "StasisEnd"), _end_leg(engine, agent, "ChannelDestroyed"))

    assert await engine.session_store.get_by_call_id(session.call_id) is session
    _assert_declined_once(engine, session, rec, agent)
    engine._persist_call_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_accepted_leg_end_keeps_whole_call_cleanup(screening):
    engine, session, rec = screening
    session.current_action.update({"answered": True, "decision": "accepted"})

    await _end_leg(engine, "agent-ringing", "ChannelDestroyed")

    assert not [e for e in rec["events"] if e[0] == "abort"]
    assert await engine.session_store.get_by_call_id(session.call_id) is None
    assert "Sorry, Support agent is not available." not in rec["tts"]


@pytest.mark.asyncio
async def test_leg_end_after_caller_hung_up_does_not_resume(screening):
    engine, session, rec = screening
    session.current_action.update({"answered": True})
    await engine.session_store.remove_call(session.call_id)

    await _end_leg(engine, "agent-ringing", "ChannelDestroyed")

    assert not [e for e in rec["events"] if e[0] == "abort"]
    assert rec["tts"] == []
    assert "agent-ringing" not in engine._attended_transfer_agent_channel_to_call_id
    assert await engine.session_store.get_by_call_id(session.call_id) is None


@pytest.mark.asyncio
async def test_retired_leg_ends_dtmf_wait_but_task_cancel_propagates():
    engine = _build_engine({"enabled": True})
    engine.register_attended_transfer_agent_channel("call-wait", "agent-wait")

    async def registered(task):
        for _ in range(50):
            if "agent-wait" in engine._attended_transfer_dtmf_waiters:
                return
            await asyncio.sleep(0)
        raise AssertionError("waiter never registered")

    waiter = asyncio.create_task(engine._wait_for_attended_transfer_dtmf("agent-wait", timeout_sec=5))
    await registered(waiter)
    engine._unregister_attended_transfer_agent_channel("agent-wait")
    assert await asyncio.wait_for(waiter, 1) is None

    engine.register_attended_transfer_agent_channel("call-wait", "agent-wait")
    waiter = asyncio.create_task(engine._wait_for_attended_transfer_dtmf("agent-wait", timeout_sec=5))
    await registered(waiter)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter


@pytest.mark.asyncio
async def test_helper_media_created_after_abort_is_released(screening, monkeypatch):
    engine, session, rec = screening
    agent = "agent-late-helper"
    base_play = engine._play_ulaw_bytes_on_channel_and_wait

    async def play(*, channel_id, audio_bytes, playback_id_prefix, timeout_sec):
        if playback_id_prefix == "attx-ann":
            await _end_leg(engine, agent, "StasisEnd")
            # Stream mode: helper media saved after the abort's own cleanup.
            engine._attended_transfer_helper_state_by_agent_channel[agent] = {
                "bridge_id": "helper-bridge",
                "external_media_id": "helper-em",
            }
            engine._attended_transfer_helper_external_media_to_agent_channel["helper-em"] = agent
            return "attx-ann-ok"
        return await base_play(channel_id=channel_id, audio_bytes=audio_bytes,
                               playback_id_prefix=playback_id_prefix, timeout_sec=timeout_sec)

    monkeypatch.setattr(engine, "_play_ulaw_bytes_on_channel_and_wait", play)
    await engine._handle_attended_transfer_answered(agent, ["attended-transfer", session.call_id, "support_agent"])

    assert [e for e in rec["events"] if e[0] == "abort"] == [("abort", agent, "target-hangup")]
    assert len(rec["claims"]) == 1
    assert _outcomes(session) == ["Transfer failed (target-hangup)."]
    assert [e for e in rec["events"] if e[0] == "hangup"] == [("hangup", agent), ("hangup", "helper-em")]
    assert agent not in engine._attended_transfer_helper_state_by_agent_channel
    assert "helper-em" not in engine._attended_transfer_helper_external_media_to_agent_channel
    engine.ari_client.destroy_bridge.assert_awaited_with("helper-bridge")


@pytest.mark.asyncio
async def test_accept_then_hangup_while_store_lock_is_busy_resumes_caller(screening, monkeypatch):
    """Accept digit, then hangup, while another call holds the session-store lock.

    The leg's end event queues on the lock ahead of the screening handler, so
    recovery starts before the handler can record the accept. The handler must
    not bridge or stop the AI afterwards, or the caller is left in silence.
    """
    engine, session, rec = screening
    agent = "agent-accept-race"
    store = engine.session_store
    base_play = engine._play_ulaw_bytes_on_channel_and_wait
    real_by_channel = store.get_by_channel_id
    end_queued = asyncio.Event()
    tasks = []

    async def hold_lock():
        async with store._lock:
            # Long enough for the handler to queue behind the end event.
            for _ in range(20):
                await asyncio.sleep(0)

    async def by_channel(channel_id):
        result = await real_by_channel(channel_id)
        if channel_id == agent and not end_queued.is_set():
            # Another call takes the lock just before the end event's
            # mapped-session lookup.
            tasks.append(asyncio.create_task(hold_lock()))
            await asyncio.sleep(0)
            end_queued.set()
        return result

    async def play(*, channel_id, audio_bytes, playback_id_prefix, timeout_sec):
        if playback_id_prefix == "attx-ann":
            # Accept pressed during the announcement, then the leg hangs up.
            engine._attended_transfer_dtmf_digits[agent] = "1"
            tasks.append(asyncio.create_task(_end_leg(engine, agent, "StasisEnd")))
            await end_queued.wait()
            return "attx-ann-ok"
        return await base_play(channel_id=channel_id, audio_bytes=audio_bytes,
                               playback_id_prefix=playback_id_prefix, timeout_sec=timeout_sec)

    provider = object()
    engine._call_providers[session.call_id] = provider
    monkeypatch.setattr(store, "get_by_channel_id", by_channel)
    monkeypatch.setattr(engine, "_play_ulaw_bytes_on_channel_and_wait", play)

    await engine._handle_attended_transfer_answered(agent, ["attended-transfer", session.call_id, "support_agent"])
    await asyncio.wait_for(asyncio.gather(*tasks), 1)

    assert await store.get_by_call_id(session.call_id) is session
    _assert_declined_once(engine, session, rec, agent)
    engine.ari_client.add_channel_to_bridge.assert_not_awaited()
    assert engine._call_providers.get(session.call_id) is provider
    assert "Sorry, Support agent is not available." in rec["tts"]


@pytest.mark.asyncio
async def test_finalize_after_recovery_started_does_not_bridge(screening):
    engine, session, rec = screening
    session.current_action.update({"answered": True, "decision": "accepted", "recovery_started": True})
    provider = object()
    engine._call_providers[session.call_id] = provider

    await engine._attended_transfer_finalize_bridge(
        session,
        agent_channel_id="agent-ringing",
        destination_description="Support agent",
        caller_connected_prompt="Connecting you now.",
        tts_timeout=1,
    )

    engine.ari_client.add_channel_to_bridge.assert_not_awaited()
    engine.ari_client.send_command.assert_not_awaited()
    assert rec["tts"] == []
    assert engine._call_providers.get(session.call_id) is provider


@pytest.mark.asyncio
@pytest.mark.parametrize("decline", ["buffered-digit", "recorded-decision"])
async def test_decline_then_hangup_is_reported_as_declined(screening, decline):
    engine, session, rec = screening
    agent = "agent-ringing"
    session.current_action.update({"answered": True})
    if decline == "buffered-digit":
        engine._attended_transfer_dtmf_digits[agent] = "2"
    else:
        session.current_action.update({"decision": "declined", "decision_digit": "2"})

    await _end_leg(engine, agent, "StasisEnd")

    assert [e for e in rec["events"] if e[0] == "abort"] == [("abort", agent, "declined")]
    assert len(rec["claims"]) == 1
    assert _outcomes(session) == ["Transfer failed (declined)."]
    assert rec["tts"].count("Sorry, Support agent is not available.") == 1
    assert await engine.session_store.get_by_call_id(session.call_id) is session
    assert session.current_action is None
