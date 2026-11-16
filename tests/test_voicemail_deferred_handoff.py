"""Voicemail uses real adapter, announcement, drain and tri-state commit owners."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.transfer_handoff import Announcement
from src.providers.base import AIProviderInterface
from src.providers.deepgram import DeepgramProvider
from src.providers.elevenlabs_agent import ElevenLabsAgentProvider
from src.providers.google_live import GoogleLiveProvider
from src.providers.grok import GrokProvider
from src.providers.local import LocalProvider
from src.providers.openai_realtime import OpenAIRealtimeProvider
from src.tools.adapters.deepgram import DeepgramToolAdapter
from src.tools.adapters.elevenlabs import ElevenLabsToolAdapter
from src.tools.adapters.google import GoogleToolAdapter
from src.tools.adapters.openai import OpenAIToolAdapter
from src.tools.context import ToolExecutionContext
from src.tools.execution_history import record_in_call_tool_result
from src.tools.registry import ToolRegistry
from src.tools.telephony.voicemail import VoicemailTool
from tests.test_deferred_transfer_commit_audio_gate import _build_engine
from src.core.models import CallSession


PROVIDERS = [OpenAIRealtimeProvider, GrokProvider, DeepgramProvider,
             GoogleLiveProvider, ElevenLabsAgentProvider, LocalProvider]


async def setup_call(monkeypatch, provider_name="openai_realtime", *, response=True):
    engine = _build_engine()
    session = CallSession(call_id="vm-handoff", caller_channel_id="vm-caller",
                          provider_name=provider_name)
    session.allowed_tools = ["leave_voicemail"]
    session.transfer_active = False
    await engine.session_store.upsert_call(session)
    registry = ToolRegistry.isolated()
    registry.register_instance(VoicemailTool())
    config = {"tools": {
        "leave_voicemail": {"enabled": True, "selected_mailbox_key": "selected",
            "mailboxes": {"selected": {"extension": "6000"}, "other": {"extension": "7000"}}},
        "transfer": {"defer_until_playback_complete": False,
            "handoff": {"enabled": False}, "deferred_audio_drain_quiet_ms": 0,
            "deferred_audio_drain_timeout_sec": 2},
    }}
    engine.ari_client = SimpleNamespace(dialplan_target_exists=AsyncMock(return_value=True),
                                       continue_in_dialplan=AsyncMock(return_value=response))
    monkeypatch.setattr(engine, "_tool_registry_for_session", lambda s: registry)
    monkeypatch.setattr(engine, "_tool_config_for_session", lambda s: config)
    ctx = ToolExecutionContext(call_id=session.call_id, caller_channel_id=session.caller_channel_id,
        session_store=engine.session_store, ari_client=engine.ari_client, config=config,
        tool_registry=registry, provider_name=provider_name, tool_call_id="invocation-vm")
    return engine, session, registry, ctx


async def arm(engine, ctx):
    result = await VoicemailTool().execute({}, ctx)
    await record_in_call_tool_result(session_store=engine.session_store, call_id=ctx.call_id,
        tool_call_id="invocation-vm", tool_name="leave_voicemail", parameters={}, result=result)
    return result


def completed_state(engine, session, action, reason="reply_completed"):
    state = Announcement(action["id"], action["handoff"])
    state.seal(reason)
    engine._transfer_handoffs[session.call_id] = state
    return state


async def commit(engine, session, action):
    return await engine._commit_pending_deferred_transfer_for_call(
        session.call_id, session, expected_action_id=action["id"])


@pytest.mark.parametrize("provider_type", PROVIDERS)
async def test_actual_provider_callback_waits_for_correlated_completion_and_real_buffer_drain(monkeypatch, provider_type):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    result = await arm(engine, ctx)
    action = session.pending_deferred_transfer
    assert result["action"] == "voicemail_initiated"
    assert action["handoff"]["owner"] == "provider"
    assert not session.transfer_active and session.voicemail_handoff_outcome is None
    # Real transport snapshot/drain: 3.5s of queued PCM, still pending >800ms.
    stream = {"buffered_bytes": 112000}
    engine.streaming_playback_manager.active_streams[session.call_id] = stream
    provider = provider_type.__new__(provider_type)
    AIProviderInterface.__init__(provider, engine.on_provider_event)
    now = time.monotonic()
    await provider.prepare_handoff_result(result, session.call_id, "origin")
    await provider.handoff_result_sent(result, session.call_id)
    state = engine._transfer_handoffs[session.call_id]
    # Different action and pre-result response cannot release this owner.
    engine._observe_transfer_handoff(session.call_id, session, {
        "handoff_action_id": "stale-action", "handoff_evidence": {
            "id": "stale", "started_at": now - 1, "completed": True}})
    assert not state.sealed
    provider._handoff_evidence.turn(session.call_id, "reply").update(started_at=time.monotonic())
    provider._handoff_evidence.state(session.call_id)["current"] = "reply"
    await provider.on_event({"type": "HandoffAudioComplete", "call_id": session.call_id,
                             "audio_response_id": "reply"})
    assert state.reason == "reply_completed"
    await asyncio.sleep(0.85)
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    assert session.voicemail_handoff_outcome is None
    stream["buffered_bytes"] = 0
    await asyncio.wait_for(asyncio.gather(*list(engine._call_bg_tasks[session.call_id])), 1)
    engine.ari_client.continue_in_dialplan.assert_awaited_once_with(
        "vm-caller", context="ext-local", extension="vmu6000", priority=1)
    assert session.voicemail_handoff_outcome == "confirmed"
    assert session.pending_deferred_transfer is None
    assert [r["action"] for r in session.tool_calls] == ["voicemail_initiated", "voicemail_completed"]
    assert session.tool_calls[0]["tool_call_id"] == session.tool_calls[1]["tool_call_id"]
    assert await commit(engine, session, action) is None


@pytest.mark.parametrize("reason", ["start_timeout", "finish_timeout", "tool_result_send_failed", "pipeline_no_audio", "pipeline_playback_timeout", "pipeline_tts_failed", "pipeline_announcement_timeout"])
async def test_incomplete_announcement_cannot_continue(monkeypatch, reason):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    await arm(engine, ctx)
    action = session.pending_deferred_transfer
    completed_state(engine, session, action, reason)
    recovery = AsyncMock()
    monkeypatch.setattr(engine, "_recover_failed_deferred_transfer", recovery)
    await commit(engine, session, action)
    recovery.assert_awaited_once_with(session.call_id, action_id=action["id"], preparation_failed=True)
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    assert not session.transfer_active


@pytest.mark.parametrize("response", [None, RuntimeError("synthetic transport failure")])
async def test_uncertain_commit_never_runs_generic_recovery_or_replays(monkeypatch, response):
    engine, session, registry, ctx = await setup_call(monkeypatch, response=response)
    if isinstance(response, Exception):
        engine.ari_client.continue_in_dialplan.side_effect = response
    await arm(engine, ctx)
    action = session.pending_deferred_transfer
    completed_state(engine, session, action)
    recovery = AsyncMock()
    monkeypatch.setattr(engine, "_recover_failed_deferred_transfer", recovery)
    result = await commit(engine, session, action)
    assert result["handoff_indeterminate"]
    recovery.assert_not_awaited()
    assert session.transfer_active and session.transfer_state == "voicemail_indeterminate"
    assert session.pending_deferred_transfer is None
    assert session.tool_calls[-1]["handoff_indeterminate"]
    assert await commit(engine, session, action) is None
    assert (await VoicemailTool().execute({}, ctx))["status"] == "failed"
    engine.ari_client.continue_in_dialplan.assert_awaited_once()


@pytest.mark.parametrize("change", ["deleted_scope", "target_deleted", "cleanup", "replacement", "attended", "transfer"])
async def test_lifecycle_and_deleted_resources_rechecked_after_announcement(monkeypatch, change):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    await arm(engine, ctx)
    action = session.pending_deferred_transfer
    completed_state(engine, session, action)
    if change == "deleted_scope":
        ctx.config["tools"]["leave_voicemail"]["mailboxes"].pop("selected")
    elif change == "target_deleted":
        engine.ari_client.dialplan_target_exists.return_value = False
    elif change == "cleanup":
        session.cleanup_in_progress = True
    elif change == "replacement":
        session.pending_deferred_transfer = {"id": "new-owner"}
    elif change == "attended":
        session.current_action = {"type": "attended_transfer", "decision": "pending"}
    else:
        session.transfer_active = True
        session.transfer_state = "other-owner"
    monkeypatch.setattr(engine, "_recover_failed_deferred_transfer", AsyncMock())
    await commit(engine, session, action)
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    if change == "replacement":
        assert session.pending_deferred_transfer["id"] == "new-owner"
    if change == "transfer":
        assert session.transfer_state == "other-owner"


@pytest.mark.parametrize("during_request", [False, True])
async def test_cancellation_retires_exact_action_and_retains_only_started_request(monkeypatch, during_request):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    await arm(engine, ctx)
    action = session.pending_deferred_transfer
    completed_state(engine, session, action)
    entered = asyncio.Event()
    async def wait(*a, **kw):
        entered.set()
        await asyncio.Event().wait()
    if during_request:
        engine.ari_client.continue_in_dialplan.side_effect = wait
    else:
        monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", wait)
    task = asyncio.create_task(commit(engine, session, action))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.pending_deferred_transfer is None
    assert bool(session.transfer_active) is during_request
    assert session.voicemail_handoff_outcome == ("indeterminate" if during_request else None)
    assert session.tool_calls[-1]["action"] == ("voicemail_indeterminate" if during_request else "voicemail_failed")


@pytest.mark.parametrize("adapter_name", ["openai", "grok", "deepgram", "google", "elevenlabs", "local_engine", "elevenlabs_engine"])
async def test_actual_tool_adapter_or_engine_gateway_only_arms(monkeypatch, adapter_name):
    engine, session, registry, ctx = await setup_call(monkeypatch, provider_name=adapter_name)
    data = {"call_id": ctx.call_id, "caller_channel_id": ctx.caller_channel_id,
            "session_store": ctx.session_store, "ari_client": ctx.ari_client,
            "config": ctx.config, "allowed_tools": ["leave_voicemail"]}
    if adapter_name in {"openai", "grok"}:
        result = await OpenAIToolAdapter(registry).handle_tool_call_event({
            "response_id": "origin", "item": {"type": "function_call", "name": "leave_voicemail",
            "call_id": "invocation-vm", "arguments": "{}"}}, data)
    elif adapter_name == "deepgram":
        result = await DeepgramToolAdapter(registry).handle_tool_call_event({
            "functions": [{"name": "leave_voicemail", "id": "invocation-vm", "arguments": "{}"}]}, data)
    elif adapter_name == "google":
        result = await GoogleToolAdapter(registry).execute_tool("leave_voicemail", {}, ctx)
    elif adapter_name == "elevenlabs":
        # The standalone compatibility adapter accepts the legacy registry
        # interface; the active provider uses the Engine gateway below.
        legacy_registry = SimpleNamespace(get_tool=registry.get)
        wrapped = await ElevenLabsToolAdapter(legacy_registry).execute_tool("leave_voicemail", "invocation-vm", {}, data)
        assert wrapped["success"]
        result = wrapped["result"]
    else:
        result = await engine._execute_provider_tool(call_id=session.call_id,
            function_name="leave_voicemail", function_call_id="invocation-vm", parameters={}, session=session)
    assert result["action"] == "voicemail_initiated"
    assert session.pending_deferred_transfer["target"] == "6000"
    assert result["deferred_transfer"]["handoff"]["owner"] == "provider"
    assert not session.transfer_active
    engine.ari_client.continue_in_dialplan.assert_not_awaited()


@pytest.mark.parametrize("shape", ["success", "empty", "error", "timeout", "playback_failed"])
async def test_pipeline_actual_tts_completion_is_required_before_commit(monkeypatch, shape):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    session.pipeline_name = "test-pipeline"
    result = await arm(engine, ctx)
    action = session.pending_deferred_transfer
    action["handoff"]["finish_timeout_sec"] = 0.05
    async def synth(*args):
        if shape == "error":
            raise RuntimeError("synthetic TTS error")
        if shape == "timeout":
            await asyncio.Event().wait()
        if shape not in {"empty", "error", "timeout"}:
            yield b"\x7f" * 160
    pipeline = SimpleNamespace(tts_adapter=SimpleNamespace(synthesize=synth), tts_options={})
    monkeypatch.setattr(engine, "_pipeline_audio_for_file_playback", lambda options, audio: audio)
    monkeypatch.setattr(engine.playback_manager, "play_audio", AsyncMock(return_value="owned-announcement"))
    monkeypatch.setattr(engine.playback_manager, "wait_for_playback_end", AsyncMock(return_value=shape != "playback_failed"))
    played = await engine._speak_pipeline_transfer_announcement(session.call_id, pipeline, result["message"], action)
    assert played is (shape == "success")
    session.pending_deferred_transfer["_engine_handoff_attempted"] = True
    monkeypatch.setattr(engine, "_complete_deferred_transfer_timeout_recovery", AsyncMock(return_value=True))
    outcome = await commit(engine, session, action)
    assert outcome["status"] == ("success" if shape == "success" else "failed")
    assert engine.ari_client.continue_in_dialplan.await_count == (1 if shape == "success" else 0)
    assert session.pending_deferred_transfer is None


async def test_rejected_commit_restores_native_ownership_after_real_recovery(monkeypatch):
    engine, session, registry, ctx = await setup_call(monkeypatch, response=False)
    await arm(engine, ctx)
    action = session.pending_deferred_transfer
    completed_state(engine, session, action)
    monkeypatch.setattr(engine, "_complete_deferred_transfer_timeout_recovery", AsyncMock(return_value=True))
    outcome = await commit(engine, session, action)
    assert outcome["transfer_cancelled"]
    assert session.pending_deferred_transfer is None
    assert not session.transfer_active and session.voicemail_handoff_outcome is None
    assert session.tool_calls[-1]["action"] == "voicemail_failed"
    assert session.tool_calls[-1]["tool_call_id"] == "invocation-vm"
    assert not engine._session_was_transferred(session)


async def test_no_completion_times_out_without_ari_and_resumes_existing_recovery(monkeypatch):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    result = await arm(engine, ctx)
    action = session.pending_deferred_transfer
    action["handoff"].update(start_timeout_sec=0.01, finish_timeout_sec=0.03)
    state = Announcement(action["id"], action["handoff"], "origin")
    state.observe({"type": "HandoffToolResultSent", "sent_at": time.monotonic(), "success": True})
    engine._transfer_handoffs[session.call_id] = state
    monkeypatch.setattr(engine, "_complete_deferred_transfer_timeout_recovery", AsyncMock(return_value=True))
    outcome = await asyncio.wait_for(commit(engine, session, action), 0.5)
    assert outcome["transfer_cancelled"]
    assert session.pending_deferred_transfer is None
    assert not session.transfer_active
    engine.ari_client.continue_in_dialplan.assert_not_awaited()


@pytest.mark.parametrize("during_request", [False, True])
async def test_new_routing_owner_is_preserved_without_recovery_speech(monkeypatch, during_request):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    await arm(engine, ctx)
    action = session.pending_deferred_transfer
    completed_state(engine, session, action)
    async def takeover(*args, **kwargs):
        session.transfer_active = True
        session.transfer_state = "attended"
        session.transfer_target = "new-owner"
        return False if during_request else True
    if during_request:
        engine.ari_client.continue_in_dialplan.side_effect = takeover
    else:
        engine.ari_client.dialplan_target_exists.side_effect = takeover
    recovery = AsyncMock()
    monkeypatch.setattr(engine, "_complete_deferred_transfer_timeout_recovery", recovery)
    await commit(engine, session, action)
    assert session.transfer_active and session.transfer_target == "new-owner"
    assert session.transfer_state == "attended"
    assert session.pending_deferred_transfer is None
    recovery.assert_not_awaited()
    assert engine.ari_client.continue_in_dialplan.await_count == int(during_request)


async def test_concurrent_commits_only_dispatch_one_continue(monkeypatch):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    await arm(engine, ctx)
    action = session.pending_deferred_transfer
    completed_state(engine, session, action)
    entered, release = asyncio.Event(), asyncio.Event()
    async def request(*args, **kwargs):
        entered.set()
        await release.wait()
        return True
    engine.ari_client.continue_in_dialplan.side_effect = request
    first = asyncio.create_task(commit(engine, session, action))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(commit(engine, session, action))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second)
    assert results[0]["status"] == "success" and results[1] is None
    engine.ari_client.continue_in_dialplan.assert_awaited_once()
    assert sum(row["action"] == "voicemail_completed" for row in session.tool_calls) == 1


@pytest.mark.parametrize("interrupted", [False, True])
async def test_started_reply_without_authoritative_completion_times_out(monkeypatch, interrupted):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    await arm(engine, ctx)
    action = session.pending_deferred_transfer
    action["handoff"].update(start_timeout_sec=0.03, finish_timeout_sec=0.02)
    state = Announcement(action["id"], action["handoff"], "origin")
    sent = time.monotonic()
    state.observe({"type": "HandoffToolResultSent", "sent_at": sent})
    state.observe({"handoff_evidence": {"id": "reply", "started_at": sent,
        "completed": interrupted, "interrupted": interrupted}})
    engine._transfer_handoffs[session.call_id] = state
    monkeypatch.setattr(engine, "_complete_deferred_transfer_timeout_recovery", AsyncMock(return_value=True))
    outcome = await asyncio.wait_for(commit(engine, session, action), 0.5)
    assert outcome["transfer_cancelled"]
    engine.ari_client.continue_in_dialplan.assert_not_awaited()


async def test_armed_announcement_persists_as_completed_call_without_false_handoff(monkeypatch, tmp_path):
    from src.core.call_history import CallHistoryStore
    engine, session, registry, ctx = await setup_call(monkeypatch)
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    history = CallHistoryStore(db_path=str(tmp_path / "history.db"))
    monkeypatch.setattr("src.core.call_history.get_call_history_store", lambda: history)
    session.conversation_history = [{"role": "user", "content": "Synthetic voicemail request"}]
    await arm(engine, ctx)
    await engine._persist_call_history(session, session.call_id)
    record = await history.get_by_call_id(session.call_id)
    assert record.outcome == "completed"
    assert record.tool_calls[0]["action"] == "voicemail_initiated"
    assert not record.tool_calls[0].get("handoff_indeterminate")


async def test_cancelling_duplicate_waiter_does_not_cancel_active_commit(monkeypatch):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    await arm(engine, ctx)
    action = session.pending_deferred_transfer
    completed_state(engine, session, action)
    entered, release = asyncio.Event(), asyncio.Event()
    async def drain(*a):
        entered.set()
        await release.wait()
        return True
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", drain)
    first = asyncio.create_task(commit(engine, session, action))
    await asyncio.wait_for(entered.wait(), 1)
    duplicate = asyncio.create_task(commit(engine, session, action))
    await asyncio.sleep(0)
    duplicate.cancel()
    with pytest.raises(asyncio.CancelledError):
        await duplicate
    assert session.pending_deferred_transfer["id"] == action["id"]
    release.set()
    assert (await first)["status"] == "success"
    engine.ari_client.continue_in_dialplan.assert_awaited_once()


@pytest.mark.parametrize("reason", ["start_timeout", "finish_timeout"])
async def test_announcement_timeout_yields_to_new_routing_owner(monkeypatch, reason):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    await arm(engine, ctx)
    action = session.pending_deferred_transfer
    completed_state(engine, session, action, reason)
    session.transfer_active = True
    session.transfer_state = "attended"
    recovery = AsyncMock()
    monkeypatch.setattr(engine, "_complete_deferred_transfer_timeout_recovery", recovery)
    result = await commit(engine, session, action)
    assert result["transfer_cancelled"] and not result["apology_spoken"]
    assert session.pending_deferred_transfer is None
    assert session.transfer_active and session.transfer_state == "attended"
    recovery.assert_not_awaited()
    engine.ari_client.continue_in_dialplan.assert_not_awaited()


async def test_cancelled_duplicate_preserves_announcements_active_waiter(monkeypatch):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    await arm(engine, ctx)
    action = session.pending_deferred_transfer
    state = Announcement(action["id"], action["handoff"])
    engine._transfer_handoffs[session.call_id] = state
    first = asyncio.create_task(commit(engine, session, action))
    async def owned():
        while not any(m.get("voicemail_owner") for m in engine._deferred_transfer_committing.get(session.call_id, [])):
            await asyncio.sleep(0)
    await asyncio.wait_for(owned(), 1)
    duplicate = asyncio.create_task(commit(engine, session, action))
    await asyncio.sleep(0)
    duplicate.cancel()
    with pytest.raises(asyncio.CancelledError):
        await duplicate
    assert not state.cancelled
    assert engine._transfer_handoffs[session.call_id] is state
    state.seal("reply_completed")
    assert (await asyncio.wait_for(first, 1))["status"] == "success"
    engine.ari_client.continue_in_dialplan.assert_awaited_once()


@pytest.mark.parametrize('qualified,superseded', [(True, False), (False, False), (False, True)])
async def test_elevenlabs_idle_fallback_cannot_release_mandatory_voicemail(monkeypatch, qualified, superseded):
    from src.providers.elevenlabs_config import ElevenLabsAgentConfig
    engine, session, registry, ctx = await setup_call(monkeypatch, provider_name='elevenlabs_agent')
    result = await arm(engine, ctx)
    action = session.pending_deferred_transfer
    action['handoff'].update(start_timeout_sec=0.05, finish_timeout_sec=0.05)
    provider = ElevenLabsAgentProvider(ElevenLabsAgentConfig(agent_id='synthetic'), engine.on_provider_event)
    provider._call_id = session.call_id
    await provider.prepare_handoff_result(result, session.call_id, 'origin')
    await provider.handoff_result_sent(result, session.call_id)
    provider._handoff_evidence.turn(session.call_id, 'reply').update(started_at=time.monotonic())
    provider._handoff_evidence.state(session.call_id)['current'] = 'reply'
    provider._in_audio_burst = True
    await provider._emit_audio_done(reason='audio_idle_fallback')
    state = engine._transfer_handoffs[session.call_id]
    assert not state.sealed
    assert not provider._handoff_evidence.turn(session.call_id, 'reply')['completed']
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    recovery = AsyncMock(return_value=True)
    monkeypatch.setattr(engine, '_complete_deferred_transfer_timeout_recovery', recovery)
    if superseded:
        session.transfer_active = True
        session.transfer_state = 'attended'
    if qualified:
        # The real native handler remains authoritative even after idle closed
        # the legacy audio burst; it must complete the same correlated reply.
        await provider._handle_agent_response_complete({'agent_response_complete_event': {'event_id': 12}})
    await asyncio.wait_for(asyncio.gather(*list(engine._call_bg_tasks[session.call_id])), 1)
    assert engine.ari_client.continue_in_dialplan.await_count == int(qualified)
    assert recovery.await_count == int(not qualified and not superseded)
    assert session.pending_deferred_transfer is None
    if superseded:
        assert session.transfer_active and session.transfer_state == 'attended'


@pytest.mark.parametrize('replace_pending', [False, True])
async def test_cancelled_submitted_continue_records_uncertainty_after_routing_takeover(monkeypatch, replace_pending):
    engine, session, registry, ctx = await setup_call(monkeypatch)
    await arm(engine, ctx)
    action = session.pending_deferred_transfer
    completed_state(engine, session, action)
    entered = asyncio.Event()
    async def submitted(*a, **kw):
        entered.set()
        await asyncio.Event().wait()
    engine.ari_client.continue_in_dialplan.side_effect = submitted
    task = asyncio.create_task(commit(engine, session, action))
    await asyncio.wait_for(entered.wait(), 1)
    session.transfer_state = 'attended'
    session.transfer_target = 'new-routing-owner'
    replacement = {'id': 'new-routing-action', 'kind': 'transfer'}
    if replace_pending:
        session.pending_deferred_transfer = replacement
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    engine.ari_client.continue_in_dialplan.assert_awaited_once()
    assert session.transfer_active
    assert (session.transfer_state, session.transfer_target) == ('attended', 'new-routing-owner')
    assert session.pending_deferred_transfer == (replacement if replace_pending else None)
    assert session.voicemail_handoff_id is None and session.voicemail_handoff_outcome is None
    assert [row['action'] for row in session.tool_calls] == ['voicemail_initiated', 'voicemail_indeterminate']
    assert session.tool_calls[-1]['tool_call_id'] == 'invocation-vm'
    assert session.tool_calls[-1]['status'] == 'failure'
    assert session.tool_calls[-1]['handoff_indeterminate'] is True
    assert await commit(engine, session, action) is None
    engine.ari_client.continue_in_dialplan.assert_awaited_once()
