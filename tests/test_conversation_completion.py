"""Completion routing uses one terminal request and never deletes uncertain handoffs."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.core.handoff_journal import HandoffJournal
from src.engine import Engine
from src.tools.context import ToolExecutionContext
from src.tools.runtime_config import ToolRuntimeGeneration, resolve_agent_tool_config, dump_agent_tool_configs
from src.tools.telephony.completion import effective_completion, validate_completion_config, resolve_completion
from src.tools.telephony.hangup import HangupCallTool
from src.tools.telephony.unified_transfer import UnifiedTransferTool


def config():
    return {"tools": {"hangup_call": {"completion": {"destination_keys": ["survey", "menu"], "default_next_step": "hangup"}},
        "transfer": {"defer_until_playback_complete": False, "destinations": {
            "survey": {"type": "dialplan", "dialplan_context": "ava-survey", "target": "s", "priority": 2, "requires_opt_in": True},
            "menu": {"type": "dialplan", "dialplan_context": "ava-menu", "target": "s", "priority": 1},
        }}}}


@pytest.fixture(autouse=True)
def private_journal(monkeypatch, tmp_path):
    monkeypatch.setenv("HANDOFF_JOURNAL_PATH", str(tmp_path / "actions.db"))


@pytest.mark.parametrize("requested,supplied,expected", [(None, False, "hangup"), ("survey", True, "survey"), ("hangup", True, "hangup"), ("bad", True, "hangup"), (None, True, "hangup"), ([], True, "hangup")])
def test_exact_completion_selection(requested, supplied, expected):
    assert resolve_completion(config(), requested, supplied=supplied)["next_step"] == expected


def test_default_is_automatic_but_explicit_hangup_always_wins():
    cfg = config()
    cfg["tools"]["hangup_call"]["completion"]["default_next_step"] = "menu"
    assert resolve_completion(cfg)["next_step"] == "menu"
    assert resolve_completion(cfg, "hangup", supplied=True)["next_step"] == "hangup"
    assert resolve_completion(cfg, "deleted", supplied=True)["next_step"] == "hangup"


def test_opt_in_cannot_be_an_automatic_default():
    cfg = config()
    cfg["tools"]["hangup_call"]["completion"]["default_next_step"] = "survey"
    with pytest.raises(ValueError, match="Opt-in"):
        validate_completion_config(cfg["tools"])
    assert effective_completion(cfg)["default_next_step"] == "hangup"


@pytest.mark.parametrize("field,value", [("target", "s,evil"), ("dialplan_context", ""), ("priority", True), ("priority", 0), ("priority", 1.5), ("requires_opt_in", "false"), ("attended_allowed", True)])
def test_invalid_destination_rejected_in_runtime_generation(field, value):
    cfg = config()
    cfg["tools"]["transfer"]["destinations"]["survey"][field] = value
    with pytest.raises(ValueError):
        ToolRuntimeGeneration.build(generation_id=1, config=cfg)


def test_agent_completion_can_only_narrow_scoped_global_inventory():
    cfg = config()
    effective = resolve_agent_tool_config(cfg, {"transfer": {"destination_policy": "selected", "destination_keys": ["menu"]}, "hangup_call": {"destination_keys": ["survey", "menu", "invented"], "default_next_step": "survey"}})
    assert effective.config["tools"]["hangup_call"]["completion"] == {"default_next_step": "hangup", "destination_keys": ["menu"]}
    assert cfg == config()
    assert '"hangup_call"' in dump_agent_tool_configs({"hangup_call": {"destination_keys": []}})


@pytest.mark.parametrize("tool", ["transfer", "hangup_call"])
def test_global_disable_removes_completion_routes(tool):
    cfg = config()
    cfg["tools"][tool]["enabled"] = False
    assert effective_completion(cfg)["destination_keys"] == []


async def setup_engine():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    session = CallSession(call_id="caller", caller_channel_id="caller")
    session.tool_runtime_config = config()
    await engine.session_store.upsert_call(session)
    engine.config = SimpleNamespace(audio_transport="audiosocket")
    engine.ari_client = SimpleNamespace(hangup_channel=AsyncMock(return_value=True),
        dialplan_target_exists=AsyncMock(return_value=True), continue_in_dialplan=AsyncMock(return_value=True),
        send_command=AsyncMock())
    engine._wait_for_call_audio_drain = AsyncMock(return_value=True)
    engine._terminal_transport_quiet_sec = lambda: 0
    engine._google_long_audio_provider = lambda _: None
    engine._call_providers = {}
    engine._save_session = AsyncMock(return_value=True)
    engine._fire_and_forget_for_call = lambda _id, coro, **kw: coro.close()
    ctx = ToolExecutionContext(call_id=session.call_id, caller_channel_id=session.caller_channel_id,
        config=session.tool_runtime_config, session_store=engine.session_store, ari_client=engine.ari_client)
    return engine, session, ctx


@pytest.mark.asyncio
async def test_survey_acceptance_drains_then_continues_once_without_hangup():
    engine, session, ctx = await setup_engine()
    order = []
    async def drain(*args, **kwargs):
        order.append("drain")
        return True
    async def redirect(*args, **kwargs):
        assert session.transfer_active is True
        assert session.call_outcome == "transferred"
        assert (await HandoffJournal().pending())[0][1]["result"] == "dispatching"
        order.append("continue")
        return True
    engine._wait_for_call_audio_drain.side_effect = drain
    engine.ari_client.continue_in_dialplan.side_effect = redirect
    result = await HangupCallTool().execute({"next_step": "survey", "farewell_message": "Please stay for the survey."}, ctx)
    assert result["will_hangup"] is True  # retained terminal-audio adapter contract
    for _ in range(2):
        await engine._terminate_call_after_audio(session.call_id, reason="test", call_outcome="agent_hangup")
    assert order == ["drain", "continue"]
    engine.ari_client.continue_in_dialplan.assert_awaited_once_with("caller", context="ava-survey", extension="s", priority=2)
    engine.ari_client.hangup_channel.assert_not_awaited()
    assert session.completion_action["result"] == "accepted"


@pytest.mark.asyncio
async def test_decline_is_not_overwritten_by_a_duplicate_terminal_tool():
    engine, session, ctx = await setup_engine()
    await asyncio.gather(HangupCallTool().execute({"next_step": "hangup"}, ctx), HangupCallTool().execute({"next_step": "survey"}, ctx))
    await engine._terminate_call_after_audio(session.call_id, reason="test", call_outcome="agent_hangup")
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_awaited_once()


@pytest.mark.parametrize("outcome", [False, None, RuntimeError("response lost")])
@pytest.mark.asyncio
async def test_rejected_vs_uncertain_handoff(outcome):
    engine, session, ctx = await setup_engine()
    if isinstance(outcome, Exception):
        engine.ari_client.continue_in_dialplan.side_effect = outcome
    else:
        engine.ari_client.continue_in_dialplan.return_value = outcome
    await HangupCallTool().execute({"next_step": "survey"}, ctx)
    await engine._terminate_call_after_audio(session.call_id, reason="test", call_outcome="agent_hangup")
    assert engine.ari_client.hangup_channel.await_count == int(outcome is False)
    assert bool(getattr(session, "transfer_active", False)) is (outcome is not False)


@pytest.mark.asyncio
async def test_missing_target_hangs_up_without_dispatch():
    engine, session, ctx = await setup_engine()
    engine.ari_client.dialplan_target_exists.return_value = False
    await HangupCallTool().execute({"next_step": "survey"}, ctx)
    await engine._terminate_call_after_audio(session.call_id, reason="test", call_outcome="agent_hangup")
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_awaited_once()


@pytest.mark.asyncio
async def test_early_stasis_end_never_falls_back_to_hangup():
    engine, session, ctx = await setup_engine()
    async def redirect(*args, **kwargs):
        assert session.transfer_active is True
        session.cleanup_in_progress = True
        await engine.session_store.remove_call(session.call_id)
        return True
    engine.ari_client.continue_in_dialplan.side_effect = redirect
    await HangupCallTool().execute({"next_step": "survey"}, ctx)
    await engine._terminate_call_after_audio(session.call_id, reason="test", call_outcome="agent_hangup")
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_caller_disconnect_during_drain_prevents_routing():
    engine, session, ctx = await setup_engine()
    async def drain(*args, **kwargs):
        await engine.session_store.remove_call(session.call_id)
        return True
    engine._wait_for_call_audio_drain.side_effect = drain
    await HangupCallTool().execute({"next_step": "survey"}, ctx)
    await engine._terminate_call_after_audio(session.call_id, reason="test", call_outcome="agent_hangup")
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_midcall_dialplan_handoff_uses_same_destination_and_priority():
    engine, session, ctx = await setup_engine()
    result = await UnifiedTransferTool().execute({"destination": "survey"}, ctx)
    assert result["status"] == "success"
    engine.ari_client.continue_in_dialplan.assert_awaited_once_with("caller", context="ava-survey", extension="s", priority=2)


@pytest.mark.asyncio
async def test_deferred_dialplan_never_predials_and_keeps_priority():
    engine, session, ctx = await setup_engine()
    ctx.config["tools"]["transfer"].update(defer_until_playback_complete=True, deferred_strategy="predial_then_bridge")
    tool = UnifiedTransferTool()
    await tool.execute({"destination": "survey"}, ctx)
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    assert session.pending_deferred_transfer["payload"]["priority"] == 2
    result = await tool.commit_deferred_action(session.pending_deferred_transfer, ctx)
    assert result["status"] == "success"
    engine.ari_client.continue_in_dialplan.assert_awaited_once_with("caller", context="ava-survey", extension="s", priority=2)


@pytest.mark.asyncio
async def test_restart_observes_pending_journal_without_replaying_or_hanging_up():
    engine, session, ctx = await setup_engine()
    await HandoffJournal().write("pending", {"channel_id": "caller", "result": "indeterminate"})
    engine.ari_client.send_command.return_value = {"id": "caller", "dialplan": {"app_name": "Playback"}}
    await engine._recover_completion_handoffs()
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()
    assert await HandoffJournal().pending() == []

@pytest.mark.asyncio
async def test_async_journal_preparation_cannot_redirect_a_retired_session(monkeypatch):
    engine, session, ctx = await setup_engine()
    original = HandoffJournal.write
    async def retire(self, action_id, payload):
        await original(self, action_id, payload)
        if payload.get("result") == "dispatching":
            session.cleanup_in_progress = True
            await engine.session_store.remove_call(session.call_id)
    monkeypatch.setattr(HandoffJournal, "write", retire)
    await HangupCallTool().execute({"next_step": "survey"}, ctx)
    await engine._terminate_call_after_audio(session.call_id, reason="test", call_outcome="agent_hangup")
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_schema_is_call_scoped_and_survives_generation_changes():
    engine, session, ctx = await setup_engine()
    generation = ToolRuntimeGeneration.build(generation_id=1, config=config())
    engine._tool_generation = generation
    session.tool_runtime_generation = generation
    agent = SimpleNamespace(tool_configs={"hangup_call": {"destination_keys": ["survey"]}}, hangup_policy=None, in_call_http_tools=None)
    engine._resolve_session_tool_runtime(session, agent)
    schema = session.tool_runtime_registry.to_openai_schema_filtered(["hangup_call"])[0]
    assert schema["function"]["parameters"]["properties"]["next_step"]["enum"] == ["hangup", "survey"]
    updated = config()
    updated["tools"]["transfer"]["destinations"].pop("survey")
    engine._tool_generation = ToolRuntimeGeneration.build(generation_id=2, config=updated)
    assert session.tool_runtime_registry.to_openai_schema_filtered(["hangup_call"])[0] == schema
    assert resolve_completion(session.tool_runtime_config, "survey", supplied=True)["next_step"] == "survey"


def test_nullable_hangup_config_resolves_without_losing_safe_defaults():
    cfg = config()
    cfg['tools']['hangup_call'] = None
    assert resolve_agent_tool_config(cfg, {}).config['tools']['hangup_call']['completion']['default_next_step'] == 'hangup'


@pytest.mark.asyncio
async def test_target_validation_cannot_claim_a_returning_conversation():
    engine, session, ctx = await setup_engine()
    replacement = CallSession(call_id=session.call_id, caller_channel_id=session.caller_channel_id)
    async def return_from_ivr(*args, **kwargs):
        await engine.session_store.remove_call(session.call_id)
        await engine.session_store.upsert_call(replacement)
        return True
    engine.ari_client.dialplan_target_exists.side_effect = return_from_ivr
    await HangupCallTool().execute({'next_step': 'survey'}, ctx)
    await engine._terminate_call_after_audio(session.call_id, reason='test', call_outcome='agent_hangup')
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()
    assert not getattr(replacement, 'transfer_active', False)


@pytest.mark.asyncio
async def test_cancel_during_target_validation_prevents_deferred_redirect():
    from src.tools.telephony.cancel_transfer import CancelTransferTool
    engine, session, ctx = await setup_engine()
    ctx.config['tools']['transfer']['defer_until_playback_complete'] = True
    tool = UnifiedTransferTool()
    await tool.execute({'destination': 'menu'}, ctx)
    action = session.pending_deferred_transfer
    async def cancel(*args, **kwargs):
        assert (await CancelTransferTool().execute({}, ctx))['status'] == 'success'
        return True
    engine.ari_client.dialplan_target_exists.side_effect = cancel
    assert (await tool.commit_deferred_action(action, ctx))['ownership_lost']
    engine.ari_client.continue_in_dialplan.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_after_dispatch_claim_does_not_report_success():
    from src.tools.telephony.cancel_transfer import CancelTransferTool
    engine, session, ctx = await setup_engine()
    ctx.config['tools']['transfer']['defer_until_playback_complete'] = True
    tool = UnifiedTransferTool()
    await tool.execute({'destination': 'menu'}, ctx)
    async def dispatch(*args, **kwargs):
        assert (await CancelTransferTool().execute({}, ctx))['status'] == 'error'
        assert session.pending_deferred_transfer
        return True
    engine.ari_client.continue_in_dialplan.side_effect = dispatch
    assert (await tool.commit_deferred_action(session.pending_deferred_transfer, ctx))['status'] == 'success'


@pytest.mark.parametrize('during', ['validation', 'journal'])
@pytest.mark.asyncio
async def test_completion_checks_competing_pending_action_at_claim_and_dispatch(monkeypatch, during):
    engine, session, ctx = await setup_engine()
    await HangupCallTool().execute({'next_step': 'survey'}, ctx)
    async def validation(*args, **kwargs):
        if during == 'validation':
            session.pending_deferred_transfer = {'id': 'new-transfer'}
        return True
    original = HandoffJournal.write
    async def preparation(journal, key, payload):
        await original(journal, key, payload)
        if during == 'journal' and payload.get('result') == 'dispatching':
            session.pending_deferred_transfer = {'id': 'new-transfer'}
    engine.ari_client.dialplan_target_exists.side_effect = validation
    monkeypatch.setattr(HandoffJournal, 'write', preparation)
    await engine._terminate_call_after_audio(session.call_id, reason='test', call_outcome='agent_hangup')
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_dispatch_timeout_releases_claim_but_post_dispatch_timeout_keeps_it():
    from src.tools.telephony.dialplan_handoff import continue_to_dialplan
    engine, session, ctx = await setup_engine()
    state = {}
    async def blocked():
        await asyncio.Event().wait()
    kwargs = dict(context=ctx, target='s', description='menu', transfer_type='dialplan',
                  dialplan_context='ava-menu', transfer_state='conversation_completion', dispatch_state=state)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(continue_to_dialplan(**kwargs, before_dispatch=blocked), timeout=0.01)
    assert state['started'] is False
    assert not session.transfer_active
    assert session.handoff_action_id is None
    engine.ari_client.continue_in_dialplan.side_effect = lambda *a, **k: None
    async def lost_response(*args, **kwargs):
        await blocked()
    engine.ari_client.continue_in_dialplan.side_effect = lost_response
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(continue_to_dialplan(**kwargs), timeout=0.01)
    assert state['started'] is True
    assert session.transfer_active


@pytest.mark.asyncio
async def test_failed_predial_originate_without_channel_preserves_dialplan_fallback():
    engine, session, ctx = await setup_engine()
    ctx.config['tools']['transfer'].update(defer_until_playback_complete=True, deferred_strategy='predial_then_bridge')
    ctx.config['tools']['transfer']['destinations']['desk'] = {'type': 'extension', 'target': '6000'}
    engine.ari_client.send_command.return_value = {'status': 400}
    tool = UnifiedTransferTool()
    await tool.execute({'destination': 'desk'}, ctx)
    assert session.current_action is None
    assert (await tool.commit_deferred_action(session.pending_deferred_transfer, ctx))['status'] == 'success'
    engine.ari_client.continue_in_dialplan.assert_awaited_once_with('caller', context='from-internal', extension='6000', priority=1)


@pytest.mark.asyncio
async def test_reconcile_cleanup_survives_failed_confirmation_write(monkeypatch):
    engine, session, ctx = await setup_engine()
    session.completion_action = {'id': 'action', 'result': 'indeterminate'}
    engine.ari_client.send_command.return_value = {'id': 'caller', 'dialplan': {'app_name': 'Read'}}
    engine._cleanup_call = AsyncMock()
    monkeypatch.setattr(HandoffJournal, 'write', AsyncMock(side_effect=OSError('read-only')))
    await engine._reconcile_completion_handoff(session)
    engine._cleanup_call.assert_awaited_once_with('caller')


@pytest.mark.asyncio
async def test_restart_recovery_can_observe_again_after_pbx_reconnects():
    engine, session, ctx = await setup_engine()
    await HandoffJournal().write('pending', {'channel_id': 'caller', 'result': 'dispatching'})
    engine.ari_client.send_command.side_effect = [RuntimeError('not connected'), {'status': 404}]
    await engine._recover_completion_handoffs()
    assert len(await HandoffJournal().pending()) == 1
    await engine._recover_completion_handoffs()
    assert not await HandoffJournal().pending()
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_returning_stasis_entry_waits_past_old_history_write_timeout(monkeypatch):
    import src.engine as module
    engine, session, ctx = await setup_engine()
    session.cleanup_in_progress = True
    engine.ari_client.answer_channel = AsyncMock()
    class AdmissionReached(BaseException):
        pass
    engine.ari_client.answer_channel.side_effect = AdmissionReached
    engine.ari_client.send_command.return_value = {'id': 'caller', 'dialplan': {'app_name': 'Stasis'}}
    original_sleep = asyncio.sleep
    waits = 0
    async def wait(seconds):
        nonlocal waits
        waits += 1
        if waits == 125:
            await engine.session_store.remove_call(session.call_id)
            module._cleanup_in_progress.discard(session.call_id)
        await original_sleep(0)
    monkeypatch.setattr(module.asyncio, 'sleep', wait)
    module._cleanup_in_progress.add(session.call_id)
    try:
        with pytest.raises(AdmissionReached):
            await engine._handle_caller_stasis_start_hybrid('caller', {'caller': {}})
        assert waits == 125
    finally:
        module._cleanup_in_progress.discard(session.call_id)


def test_outbound_guidance_never_promises_completion_handoff():
    from src.tools.runtime_guidance import build_in_call_tool_runtime_guidance
    cfg = config()
    cfg['tools']['hangup_call']['completion']['default_next_step'] = 'menu'
    guidance = build_in_call_tool_runtime_guidance(cfg, ['hangup_call'], completion_eligible=False)
    assert 'uses `hangup`' in guidance
    assert 'next_step `survey`' not in guidance
    assert 'uses `menu`' not in guidance


@pytest.mark.asyncio
async def test_unconfirmed_playback_is_not_reported_as_drained():
    engine, session, ctx = await setup_engine()
    await HangupCallTool().execute({'next_step': 'survey'}, ctx)
    await engine._terminate_call_after_audio('caller', reason='test', call_outcome='agent_hangup',
                                           audio_already_drained=True, audio_drain_confirmed=False)
    assert session.completion_action['audio_drained'] is False
    assert session.completion_action['result'] == 'accepted'


@pytest.mark.asyncio
async def test_completion_cannot_take_over_predial_before_deferred_action_is_stored():
    engine, session, ctx = await setup_engine()
    session.current_action = {'type': 'predial_transfer', 'deferred_action_id': 'ringing'}
    assert (await HangupCallTool().execute({'next_step': 'survey'}, ctx))['status'] == 'error'
    assert not session.completion_action


@pytest.mark.parametrize('key', ['Live Agent', '受付メニュー'])
def test_completion_reuses_existing_inventory_labels_with_spaces_and_unicode(key):
    cfg = config()
    cfg['tools']['transfer']['destinations'][key] = cfg['tools']['transfer']['destinations'].pop('menu')
    cfg['tools']['hangup_call']['completion'] = {'destination_keys': [key], 'default_next_step': key}
    generation = ToolRuntimeGeneration.build(generation_id=1, config=cfg)
    scoped = resolve_agent_tool_config(generation.config, {'hangup_call': {'destination_keys': [key], 'default_next_step': key}})
    assert resolve_completion(scoped.config)['next_step'] == key
    assert resolve_completion(scoped.config, key, supplied=True)['route']['dialplan_context'] == 'ava-menu'


@pytest.mark.asyncio
async def test_reentry_audio_starts_a_new_consumer_after_previous_cleanup(monkeypatch):
    from src.config import AppConfig
    engine = Engine(AppConfig(default_provider='local', providers={'local': {'enabled': True}},
        asterisk={'host': '127.0.0.1', 'username': 'u', 'password': 'p'},
        llm={'initial_greeting': '', 'prompt': 'Help'},
        audio_transport='audiosocket', downstream_mode='stream'))
    engine.ari_client.hangup_channel = AsyncMock(return_value=True)
    engine._persist_call_history = AsyncMock()
    engine._execute_post_call_tools = AsyncMock()
    first = CallSession(call_id='returning', caller_channel_id='returning', provider_name='local')
    first.transfer_active = True
    await engine.session_store.upsert_call(first)
    retired_queue = asyncio.Queue()
    retired_queue.put_nowait(b'old audio')
    engine._provider_stream_queues[first.call_id] = retired_queue
    engine._provider_stream_formats[first.call_id] = {'encoding': 'slin', 'sample_rate': 8000}
    engine._provider_coalesce_buf[first.call_id] = bytearray(b'old partial frame')
    engine._segment_tts_active.add(first.call_id)
    await engine._cleanup_call(first.call_id)
    assert first.cleanup_completed
    assert first.call_id not in engine._provider_stream_queues
    assert first.call_id not in engine._provider_stream_formats
    assert first.call_id not in engine._provider_coalesce_buf
    assert first.call_id not in engine._segment_tts_active
    second = CallSession(call_id='returning', caller_channel_id='returning', provider_name='local')
    await engine.session_store.upsert_call(second)
    engine.streaming_playback_manager.start_streaming_playback = AsyncMock(return_value='new-stream')
    await engine.on_provider_event({'type': 'AgentAudio', 'call_id': second.call_id,
        'data': b'\xff' * 160, 'encoding': 'ulaw', 'sample_rate': 8000})
    engine.streaming_playback_manager.start_streaming_playback.assert_awaited_once()
    assert engine._provider_stream_queues[second.call_id] is not retired_queue
    assert retired_queue.qsize() == 1
    await engine._cleanup_call(second.call_id)


@pytest.mark.asyncio
async def test_deferred_route_identity_includes_priority_and_exact_repeat_keeps_owner():
    engine, session, ctx = await setup_engine()
    cfg = ctx.config['tools']['transfer']
    cfg['defer_until_playback_complete'] = True
    cfg['destinations']['menu'].update(dialplan_context='ava-survey', priority=3)
    tool = UnifiedTransferTool()
    first = await tool.execute({'destination': 'survey'}, ctx)
    owner = session.pending_deferred_transfer
    conflict = await tool.execute({'destination': 'menu'}, ctx)
    assert conflict['status'] == 'failed'
    assert session.pending_deferred_transfer is owner
    repeat = await tool.execute({'destination': 'survey'}, ctx)
    assert repeat['duplicate_suppressed'] is True
    assert repeat['deferred_transfer']['id'] == first['deferred_transfer']['id']
    assert repeat['deferred_transfer']['payload']['priority'] == 2
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    assert (await tool.commit_deferred_action(owner, ctx))['status'] == 'success'
    engine.ari_client.continue_in_dialplan.assert_awaited_once_with('caller', context='ava-survey', extension='s', priority=2)

@pytest.mark.asyncio
@pytest.mark.parametrize('second', ['desk', 'other'])
async def test_concurrent_predial_reserves_before_originate_and_never_overwrites(second):
    engine, session, ctx = await setup_engine()
    ctx.config['tools']['transfer'].update(defer_until_playback_complete=True, deferred_strategy='predial_then_bridge')
    ctx.config['tools']['transfer']['destinations'].update(desk={'type': 'extension', 'target': '6000'}, other={'type': 'extension', 'target': '6001'})
    started, release = asyncio.Event(), asyncio.Event()
    async def originate(**kwargs):
        assert session.pending_deferred_transfer
        started.set()
        await release.wait()
        return {'id': 'destination-leg'}
    engine.ari_client.send_command.side_effect = originate
    tool = UnifiedTransferTool()
    first = asyncio.create_task(tool.execute({'destination': 'desk'}, ctx))
    await started.wait()
    action_id = session.pending_deferred_transfer['id']
    other = asyncio.create_task(tool.execute({'destination': second}, ctx))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, other)
    assert results[0]['status'] == 'success'
    assert (results[1].get('duplicate_suppressed') is True) if second == 'desk' else results[1]['status'] == 'failed'
    assert session.pending_deferred_transfer['id'] == action_id
    assert session.current_action['predial_channel_id'] == 'destination-leg'
    engine.ari_client.send_command.assert_awaited_once()


@pytest.mark.asyncio
async def test_completion_blocks_predial_before_any_pbx_io():
    engine, session, ctx = await setup_engine()
    await HangupCallTool().execute({'next_step': 'survey'}, ctx)
    ctx.config['tools']['transfer'].update(defer_until_playback_complete=True, deferred_strategy='predial_then_bridge')
    ctx.config['tools']['transfer']['destinations']['desk'] = {'type': 'extension', 'target': '6000'}
    assert (await UnifiedTransferTool().execute({'destination': 'desk'}, ctx))['status'] != 'success'
    engine.ari_client.send_command.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('replacement', [False, True])
async def test_cancel_during_predial_retires_late_leg_without_restoring_owner(replacement):
    engine, session, ctx = await setup_engine()
    ctx.config['tools']['transfer'].update(defer_until_playback_complete=True, deferred_strategy='predial_then_bridge')
    ctx.config['tools']['transfer']['destinations']['desk'] = {'type': 'extension', 'target': '6000'}
    session.tts_tokens.add('announcement')
    async def originate(**kwargs):
        assert await engine.session_store.cancel_deferred_transfer(session, session.pending_deferred_transfer['id'])
        assert session.audio_capture_enabled is False
        if replacement:
            await engine.session_store.remove_call('caller')
            await engine.session_store.upsert_call(CallSession(call_id='caller', caller_channel_id='caller'))
        return {'id': 'late-leg'}
    engine.ari_client.send_command.side_effect = originate
    assert (await UnifiedTransferTool().execute({'destination': 'desk'}, ctx))['status'] != 'success'
    engine.ari_client.hangup_channel.assert_awaited_once_with('late-leg')
    assert session.pending_deferred_transfer is None
    assert session.current_action is None
    assert (await engine.session_store.get_by_call_id('caller') is session) is not replacement


@pytest.mark.asyncio
async def test_natural_farewell_without_tool_does_not_select_automatic_destination():
    engine, session, ctx = await setup_engine()
    ctx.config['tools']['hangup_call']['completion']['default_next_step'] = 'menu'
    await engine._terminate_call_after_audio('caller', reason='natural-farewell', call_outcome='agent_hangup')
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_awaited_once_with('caller')
    assert session.completion_action == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('replacement', [False, True])
async def test_hosted_missing_completion_fallback_preserves_selected_action_and_owner(monkeypatch, replacement):
    engine, session, ctx = await setup_engine()
    await HangupCallTool().execute({'next_step': 'survey'}, ctx)
    original_sleep = asyncio.sleep
    async def tick(_):
        if replacement:
            await engine.session_store.remove_call('caller')
            await engine.session_store.upsert_call(CallSession(call_id='caller', caller_channel_id='caller'))
        await original_sleep(0)
    monkeypatch.setattr('src.engine.asyncio.sleep', tick)
    engine._schedule_terminal_fallback('caller', reason='hosted', timeout_sec=1, call_outcome='agent_hangup', expected_session=session)
    task = engine._terminal_fallback_tasks['caller']
    await task
    engine.ari_client.hangup_channel.assert_not_awaited()
    if replacement:
        engine.ari_client.continue_in_dialplan.assert_not_awaited()
    else:
        engine.ari_client.continue_in_dialplan.assert_awaited_once()
        assert session.completion_action['audio_drained'] is False


@pytest.mark.asyncio
async def test_recovery_cannot_overwrite_live_terminal_result_during_lookup():
    engine, session, ctx = await setup_engine()
    journal = HandoffJournal()
    await journal.write('race', {'channel_id': 'caller', 'result': 'dispatching'})
    async def lookup(*args, **kwargs):
        await journal.write('race', {'channel_id': 'caller', 'result': 'rejected'})
        return {'status': 404}
    engine.ari_client.send_command.side_effect = lookup
    await engine._recover_completion_handoffs()
    assert journal.get('race')['result'] == 'rejected'


@pytest.mark.asyncio
async def test_later_stasis_start_retires_transferred_owner_when_end_was_lost():
    engine, session, ctx = await setup_engine()
    session.transfer_active = True
    session._stasis_entry_id = '2026-11-27T01:00:00.000+0000'
    async def retire(call_id):
        assert await engine.session_store.get_by_call_id(call_id) is session
        await engine.session_store.remove_call(call_id)
    engine._cleanup_call = AsyncMock(side_effect=retire)
    class Admitted(BaseException):
        pass
    engine.ari_client.answer_channel = AsyncMock(side_effect=Admitted)
    with pytest.raises(Admitted):
        await engine._handle_caller_stasis_start_hybrid('caller', {'caller': {}, '_stasis_entry_id': '2026-11-27T01:01:00.000+0000'})
    engine._cleanup_call.assert_awaited_once()
    engine.ari_client.hangup_channel.assert_not_awaited()


def test_invalid_requested_next_step_is_not_retained_as_diagnostics():
    value = resolve_completion(config(), 'caller email private@example.com', supplied=True)
    assert value['requested_next_step'] == '<invalid>'
    assert value['result'] == 'unavailable'


def test_dialplan_destinations_cannot_be_live_agent_overrides_or_legacy_defaults():
    from src.tools.telephony.live_agent_transfer import LiveAgentTransferTool
    cfg = config()
    cfg['tools']['transfer']['live_agent_destination_key'] = 'survey'
    with pytest.raises(ValueError, match='Live agent'):
        validate_completion_config(cfg['tools'])
    assert LiveAgentTransferTool._resolve_live_agent_destination_key(cfg['tools']['transfer'])[0] is None
    cfg['tools']['transfer']['destinations']['live_agent'] = {'type': 'dialplan', 'target': 's', 'context': 'ivr'}
    assert LiveAgentTransferTool._resolve_live_agent_destination_key_from_destinations(cfg['tools']['transfer'])[0] is None

@pytest.mark.asyncio
async def test_post_call_webhook_uses_pbx_id_but_writes_conversation_history(monkeypatch):
    from src.core import call_history
    engine, session, ctx = await setup_engine()
    session.history_call_id = 'caller:conversation'
    captured, tasks = [], []
    async def execute(context):
        captured.append(context.call_id)
    tool = SimpleNamespace(definition=SimpleNamespace(name='webhook', timeout_ms=1000), execute=execute)
    engine._tool_registry_for_session = lambda _: SimpleNamespace(get_tools_for_context=lambda **kw: [tool])
    engine._fire_and_forget = lambda coro, **kw: tasks.append(asyncio.create_task(coro))
    engine.config.default_provider = 'google_live'
    history = SimpleNamespace(append_phase_tool=AsyncMock(), update_phase_tool=AsyncMock())
    monkeypatch.setattr(call_history, 'get_call_history_store_async', AsyncMock(return_value=history))
    await engine._execute_post_call_tools('caller', session, call_duration_seconds=10, call_outcome='transferred')
    await asyncio.gather(*tasks)
    assert captured == ['caller']
    assert history.append_phase_tool.await_args.kwargs['call_id'] == 'caller:conversation'
    assert history.update_phase_tool.await_args.kwargs['call_id'] == 'caller:conversation'


@pytest.mark.asyncio
async def test_outbound_history_link_retires_tracking_by_pbx_channel_id(monkeypatch):
    from src.core import call_history
    engine, session, ctx = await setup_engine()
    session.history_call_id = 'caller:conversation'
    session.is_outbound = True
    session.outbound_attempt_id = 'attempt'
    session.conversation_history = [{'role': 'user', 'content': 'hello'}]
    engine._outbound_attempt_meta_by_attempt_id = {'attempt': {}}
    engine._outbound_attempt_meta_by_channel_id = {'caller': {}}
    engine._outbound_attempt_amd = {}
    engine.outbound_store = SimpleNamespace(finish_attempt=AsyncMock())
    history = SimpleNamespace(_enabled=True, save=AsyncMock(return_value=True), get_by_call_id=AsyncMock(return_value=SimpleNamespace(id='persisted')))
    monkeypatch.setattr(call_history, 'get_call_history_store_async', AsyncMock(return_value=history))
    await engine._persist_call_history(session, 'caller')
    assert history.save.await_args.args[0].call_id == 'caller:conversation'
    assert engine._outbound_attempt_meta_by_channel_id == {}
    assert engine.outbound_store.finish_attempt.await_args.kwargs['call_history_call_id'] == 'persisted'


@pytest.mark.asyncio
@pytest.mark.parametrize('history_key', [None, 'caller:conversation'])
async def test_durable_external_result_updates_original_conversation_history(monkeypatch, history_key):
    from src.core import call_history
    engine, _, _ = await setup_engine()
    history = SimpleNamespace(update_external_lifecycle=AsyncMock(return_value=True))
    monkeypatch.setattr(call_history, 'get_call_history_store_async', AsyncMock(return_value=history))
    store = SimpleNamespace(complete_pending_action=lambda _: True)
    await engine._retry_pending_vicidial_action({'id': 'retry', 'call_id': 'caller', 'payload': {'history_call_id': history_key}, 'terminal_result': {'disposition': 'DNC'}}, store)
    assert history.update_external_lifecycle.await_args.args[0] == (history_key or 'caller')


@pytest.mark.asyncio
@pytest.mark.parametrize('presence,cleaned', [({'status': 404}, True), ({'id': 'caller', 'dialplan': {'app_name': 'Read'}}, True), ({'id': 'caller', 'dialplan': {'app_name': 'Stasis'}}, False)])
async def test_provider_failure_uncertainty_observes_without_redirect_replay(monkeypatch, presence, cleaned):
    engine, session, _ = await setup_engine()
    engine.config.on_provider_failure = 'dialplan_redirect'
    engine.config.provider_failure_redirect_context = 'fallback'
    engine._stop_connection_audio = AsyncMock()
    engine._announce_provider_failure_and_hangup = AsyncMock()
    engine._cleanup_call = AsyncMock()
    engine.ari_client.continue_in_dialplan.return_value = None
    engine.ari_client.send_command.return_value = presence
    observers = []
    engine._fire_and_forget_for_call = lambda _, coro, **kw: observers.append(coro)
    original_sleep = asyncio.sleep
    async def tick(_):
        await original_sleep(0)
    monkeypatch.setattr('src.engine.asyncio.sleep', tick)
    await engine._handle_provider_start_failure(session)
    assert len(observers) == 1
    await observers[0]
    engine.ari_client.continue_in_dialplan.assert_awaited_once()
    assert bool(engine._cleanup_call.await_count) is cleaned
    engine._announce_provider_failure_and_hangup.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('leg_action', ['current', 'retired'])
async def test_predial_answer_is_bound_to_attempt_even_when_destination_label_has_commas(leg_action):
    engine, session, _ = await setup_engine()
    engine._predial_transfer_channel_to_call_id = {}
    engine._seen_aux_channels = set()
    session.current_action = {'type': 'predial_transfer', 'deferred_action_id': 'current', 'destination_key': 'desk, support'}
    await engine._handle_predial_transfer_answered('leg', ['predial-transfer', 'caller', 'desk', ' support', leg_action])
    if leg_action == 'current':
        assert session.current_action['predial_channel_id'] == 'leg'
        engine.ari_client.hangup_channel.assert_not_awaited()
    else:
        assert 'predial_channel_id' not in session.current_action
        engine.ari_client.hangup_channel.assert_awaited_once_with('leg')
