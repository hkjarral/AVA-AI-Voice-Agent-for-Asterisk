"""Regression coverage for onward transfers after an AVA attended handoff (#699).

Synthetic ARI events preserve the reporter's StasisStart/replace_channel shape.
No PBX, provider, personal data, or live external effects are used.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

import src.engine as engine_module
from src.core.models import CallSession
from tests.test_attended_transfer_streaming import _build_engine


def replacement(old="agent-203", new="local-202", *, sip=False, args=None):
    return {
        "type": "StasisStart",
        "args": [] if args is None else args,
        "channel": {
            "id": new,
            "name": "PJSIP/202-test" if sip else "Local/202@from-internal-xfer-test;1",
            "state": "Down",
        },
        "replace_channel": {"id": old, "name": "PJSIP/203-test", "state": "Up"},
    }


@pytest.fixture
async def handoff(monkeypatch, request):
    monkeypatch.setattr(engine_module, "_cleanup_lock", asyncio.Lock())
    engine = _build_engine({"enabled": True})
    call_id = "issue699-" + request.node.name
    session = CallSession(
        call_id=call_id, caller_channel_id=call_id, bridge_id="caller-bridge",
    )
    session.transfer_destination = "Technical support 203"
    session.audio_capture_enabled = False
    session.current_action = {
        "type": "attended_transfer", "agent_channel_id": "agent-203",
        "answered": True, "decision": "accepted",
    }
    await engine.session_store.upsert_call(session)
    engine.register_attended_transfer_agent_channel(call_id, "agent-203")
    for name in ("send_command", "add_channel_to_bridge", "destroy_bridge", "hangup_channel"):
        monkeypatch.setattr(engine.ari_client, name, AsyncMock(return_value=True))
    for name in (
        "_ensure_provider_session_started", "_handle_local_stasis_start_hybrid",
        "_handle_caller_stasis_start_hybrid", "_handle_agent_action_stasis",
        "_persist_call_history", "_persist_abandoned_call_history", "_execute_post_call_tools",
    ):
        monkeypatch.setattr(engine, name, AsyncMock())
    return engine, session


async def terminal(engine, channel_id, kind):
    event = {
        "type": kind, "channel": {"id": channel_id},
        "cause": 16, "cause_txt": "Normal Clearing",
    }
    if kind == "StasisEnd":
        await engine._handle_stasis_end(event)
    else:
        await engine._handle_channel_destroyed(event)


def assert_no_teardown(engine):
    engine.ari_client.hangup_channel.assert_not_awaited()
    engine.ari_client.destroy_bridge.assert_not_awaited()
    engine._persist_call_history.assert_not_awaited()
    engine._execute_post_call_tools.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("sip", [False, True], ids=["blind-local", "attended-sip"])
@pytest.mark.parametrize("args", [[], ["attended-transfer", "original-call", "support"]])
@pytest.mark.parametrize("first_end", ["StasisEnd", "ChannelDestroyed"])
async def test_replacement_preserves_call_and_final_leg_cleans_up_once(handoff, sip, args, first_end):
    engine, session = handoff
    event = replacement(sip=sip, args=args)
    await engine._handle_stasis_start(event)
    for kind in [first_end, "ChannelDestroyed", "StasisEnd"]:
        await terminal(engine, "agent-203", kind)

    assert await engine.session_store.get_by_call_id(session.call_id) is session
    assert session.bridge_id == "caller-bridge"
    assert session.current_action["agent_channel_id"] == "local-202"
    assert session.current_action["decision"] == "accepted"
    assert session.transfer_destination == "Technical support 203"
    assert not session.audio_capture_enabled
    assert "agent-203" not in engine._attended_transfer_agent_channel_to_call_id
    assert engine._attended_transfer_agent_channel_to_call_id["local-202"] == session.call_id
    assert "local-202" not in engine._seen_aux_channels
    engine._handle_local_stasis_start_hybrid.assert_not_awaited()
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine._handle_agent_action_stasis.assert_not_awaited()
    engine._ensure_provider_session_started.assert_not_awaited()
    engine.ari_client.add_channel_to_bridge.assert_not_awaited()  # Asterisk already swapped it.
    assert_no_teardown(engine)

    # A genuine final destination end still owns cleanup.
    await terminal(engine, "local-202", first_end)
    await terminal(engine, "local-202", "ChannelDestroyed")
    await terminal(engine, session.caller_channel_id, "StasisEnd")
    engine.ari_client.destroy_bridge.assert_awaited_once_with("caller-bridge")
    assert sum(c.args == (session.caller_channel_id,) for c in engine.ari_client.hangup_channel.await_args_list) == 1
    assert any(c.args == ("local-202",) for c in engine.ari_client.hangup_channel.await_args_list)
    engine._persist_call_history.assert_awaited_once()
    engine._execute_post_call_tools.assert_awaited_once()
    assert await engine.session_store.get_by_call_id(session.call_id) is None
    assert not engine._attended_transfer_agent_channel_to_call_id


@pytest.mark.asyncio
async def test_repeated_transfers_ignore_duplicate_and_stale_starts(handoff):
    engine, session = handoff
    first = replacement()
    second = replacement("local-202", "agent-201", sip=True)
    await engine._handle_stasis_start(first)
    await engine._handle_stasis_start(first)
    await engine._handle_stasis_start(second)
    await engine._handle_stasis_start(first)  # Late old duplicate after a further transfer.
    await engine._handle_stasis_start(second)
    for retired in ("agent-203", "local-202"):
        await terminal(engine, retired, "StasisEnd")
        await terminal(engine, retired, "ChannelDestroyed")
    assert session.current_action["agent_channel_id"] == "agent-201"
    assert session.current_action["replaced_agent_channel_ids"] == ["agent-203", "local-202"]
    assert engine._attended_transfer_agent_channel_to_call_id == {"agent-201": session.call_id}
    assert_no_teardown(engine)
    engine._handle_agent_action_stasis.assert_not_awaited()
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine._ensure_provider_session_started.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("incomplete", ["ringing", "screening", "not-bridged"])
async def test_replacement_does_not_bypass_initial_pickup_acceptance(handoff, incomplete):
    engine, session = handoff
    if incomplete == "ringing":
        session.current_action["answered"] = False
    elif incomplete == "screening":
        session.current_action["decision"] = ""
    else:
        session.transfer_destination = None
    event = replacement(sip=True, args=["attended-transfer", session.call_id, "support"])
    await engine._handle_stasis_start(event)
    engine._handle_agent_action_stasis.assert_awaited_once_with(
        "local-202", event["channel"], event["args"],
    )
    assert session.current_action["agent_channel_id"] == "agent-203"
    assert_no_teardown(engine)


@pytest.mark.asyncio
async def test_unknown_replacement_keeps_existing_local_routing(handoff):
    engine, session = handoff
    event = replacement("unknown-agent")
    await engine._handle_stasis_start(event)
    engine._handle_local_stasis_start_hybrid.assert_awaited_once_with("local-202", event["channel"])
    assert session.current_action["agent_channel_id"] == "agent-203"
    assert_no_teardown(engine)


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["caller", "media", "attended-leg", "predial-leg"])
async def test_replacement_cannot_take_existing_channel_ownership(handoff, owner):
    engine, session = handoff
    other = CallSession(call_id="other-call", caller_channel_id="other-call")
    if owner == "caller":
        new = other.caller_channel_id
    else:
        new = "occupied"
    if owner == "media":
        other.external_media_id = new
    await engine.session_store.upsert_call(other)
    if owner == "attended-leg":
        engine.register_attended_transfer_agent_channel(other.call_id, new)
    if owner == "predial-leg":
        engine.register_predial_transfer_channel(other.call_id, new)
    await engine._handle_stasis_start(replacement(new=new))
    assert session.current_action["agent_channel_id"] == "agent-203"
    assert engine._attended_transfer_agent_channel_to_call_id["agent-203"] == session.call_id
    assert await engine.session_store.get_by_call_id(other.call_id) is other
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine._handle_local_stasis_start_hybrid.assert_not_awaited()
    assert_no_teardown(engine)


@pytest.mark.asyncio
async def test_cleanup_rechecks_ownership_after_waiting_for_replacement(handoff, monkeypatch):
    engine, session = handoff
    resolving = asyncio.Event()
    proceed = asyncio.Event()
    get_call = engine.session_store.get_by_call_id

    async def delayed_lookup(call_id):
        if asyncio.current_task() is adoption and call_id == session.call_id:
            resolving.set()
            await proceed.wait()
        return await get_call(call_id)

    monkeypatch.setattr(engine.session_store, "get_by_call_id", delayed_lookup)
    adoption = asyncio.create_task(engine._handle_stasis_start(replacement()))
    await asyncio.wait_for(resolving.wait(), 1)
    # It resolves the old owner while adoption holds the terminal-claim lock.
    cleanup = asyncio.create_task(terminal(engine, "agent-203", "StasisEnd"))
    await asyncio.sleep(0)
    assert not cleanup.done()
    proceed.set()
    await asyncio.wait_for(asyncio.gather(adoption, cleanup), 1)
    assert session.current_action["agent_channel_id"] == "local-202"
    assert not session.cleanup_in_progress
    assert_no_teardown(engine)


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["cleanup_in_progress", "cleanup_completed"])
async def test_replacement_does_not_resurrect_ending_call(handoff, marker):
    engine, session = handoff
    setattr(session, marker, True)
    await engine._handle_stasis_start(replacement())
    assert session.current_action["agent_channel_id"] == "agent-203"
    assert "local-202" not in engine._attended_transfer_agent_channel_to_call_id
    engine.ari_client.hangup_channel.assert_awaited_once_with("local-202")
    engine._ensure_provider_session_started.assert_not_awaited()


@pytest.mark.asyncio
async def test_caller_hangup_during_replacement_save_cannot_resurrect_session(handoff, monkeypatch):
    engine, session = handoff
    saving = asyncio.Event()
    proceed = asyncio.Event()
    save = engine._save_session

    async def delayed_save(value, **kwargs):
        saving.set()
        await proceed.wait()
        await save(value, **kwargs)

    monkeypatch.setattr(engine, "_save_session", delayed_save)
    adoption = asyncio.create_task(engine._handle_stasis_start(replacement()))
    await asyncio.wait_for(saving.wait(), 1)
    await terminal(engine, session.caller_channel_id, "StasisEnd")
    proceed.set()
    await asyncio.wait_for(adoption, 1)
    assert await engine.session_store.get_by_call_id(session.call_id) is None
    assert not engine._attended_transfer_agent_channel_to_call_id
    engine._persist_call_history.assert_awaited_once()
    engine._execute_post_call_tools.assert_awaited_once()


@pytest.mark.asyncio
async def test_media_leg_cannot_be_adopted_as_transfer_destination(handoff):
    engine, session = handoff
    event = replacement()
    event["channel"]["name"] = "AudioSocket/127.0.0.1:8090-media"
    assert not await engine._handle_attended_transfer_replacement(event)
    assert session.current_action["agent_channel_id"] == "agent-203"
    assert_no_teardown(engine)


@pytest.mark.asyncio
async def test_no_session_after_restart_is_not_recreated_by_replacement(handoff):
    engine, session = handoff
    await engine.session_store.remove_call(session.call_id)
    engine._attended_transfer_agent_channel_to_call_id.clear()
    assert not await engine._handle_attended_transfer_replacement(replacement())
    assert await engine.session_store.get_all_sessions() == []
    assert_no_teardown(engine)


@pytest.mark.asyncio
async def test_replacement_preserves_original_acceptance_digit(handoff):
    engine, session = handoff
    session.current_action["decision_digit"] = "1"
    engine._attended_transfer_dtmf_digits["agent-203"] = "1"
    await engine._handle_stasis_start(replacement())
    await engine._handle_dtmf_received({
        "channel": {"id": "local-202"}, "digit": "2",
    })
    assert session.current_action["decision_digit"] == "1"
    assert engine._attended_transfer_dtmf_digits["local-202"] == "1"
    assert "agent-203" not in engine._attended_transfer_dtmf_digits
    assert_no_teardown(engine)


@pytest.mark.asyncio
async def test_original_timeout_guard_cannot_cancel_replacement(handoff, monkeypatch):
    engine, session = handoff
    await engine._handle_stasis_start(replacement())
    monkeypatch.setattr(engine_module.asyncio, "sleep", AsyncMock())
    await engine._attended_transfer_timeout_guard(session.call_id, "agent-203", timeout_sec=0)
    assert session.current_action["agent_channel_id"] == "local-202"
    assert session.current_action["decision"] == "accepted"
    assert_no_teardown(engine)


@pytest.mark.asyncio
async def test_answered_leg_without_persisted_action_id_keeps_existing_cleanup(handoff):
    engine, session = handoff
    del session.current_action["agent_channel_id"]
    await terminal(engine, "agent-203", "StasisEnd")
    assert await engine.session_store.get_by_call_id(session.call_id) is None
    engine.ari_client.destroy_bridge.assert_awaited_once_with("caller-bridge")
    engine._persist_call_history.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", [17, 18, 19], ids=["busy", "no-user-response", "no-answer"])
async def test_failed_onward_destination_still_terminates_cleanly(handoff, cause):
    engine, session = handoff
    await engine._handle_stasis_start(replacement())
    await engine._handle_channel_destroyed({
        "channel": {"id": "local-202"}, "cause": cause,
    })
    assert await engine.session_store.get_by_call_id(session.call_id) is None
    engine.ari_client.destroy_bridge.assert_awaited_once_with("caller-bridge")
    engine._persist_call_history.assert_awaited_once()
    engine._execute_post_call_tools.assert_awaited_once()
    engine._ensure_provider_session_started.assert_not_awaited()
