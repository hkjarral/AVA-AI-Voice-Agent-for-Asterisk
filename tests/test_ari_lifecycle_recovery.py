"""Offline ARI ownership regressions for rejected/uncertain side effects (#863)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.ari_client import ARIClient
from src.core.call_history import CallHistoryStore
from src.core.models import CallSession
from src.core.pre_call_hold import stop_pre_call_holds
from src.core.session_store import SessionStore
from src.engine import Engine
from src.tools.context import ToolExecutionContext
from src.tools.execution_history import build_in_call_tool_record
from src.tools.telephony.voicemail import VoicemailTool
import src.tools.telephony.voicemail as voicemail_module


def ari_client(responses):
    ari = ARIClient.__new__(ARIClient)
    ari.send_command = AsyncMock(side_effect=responses)
    return ari


async def voicemail_context(monkeypatch, responses, config=None):
    monkeypatch.setattr(voicemail_module, 'asyncio', SimpleNamespace(
        sleep=AsyncMock(), CancelledError=asyncio.CancelledError))
    session = CallSession(call_id='call-vm', caller_channel_id='call-vm')
    store = SessionStore()
    await store.upsert_call(session)
    # First response is DIALPLAN_EXISTS; next response is authoritative continue.
    ari = ari_client([{'value': '1'}, {'value': '1'}, *responses])
    ctx = ToolExecutionContext(call_id=session.call_id, caller_channel_id=session.caller_channel_id,
        session_store=store, ari_client=ari,
        config={'tools': {'leave_voicemail': config or {'enabled': True, 'extension': '100'}}})
    return session, ctx


async def commit_voicemail(ctx):
    tool = VoicemailTool()
    armed = await tool.execute({}, ctx)
    if not armed.get('deferred_transfer'):
        return armed
    return await tool.commit_deferred_action(armed['deferred_transfer'], ctx)


def native_engine(session, ari):
    engine = Engine.__new__(Engine)
    engine.session_store = SimpleNamespace(get_by_call_id=AsyncMock(return_value=session))
    engine.ari_client = ari
    engine._save_session = AsyncMock()
    engine.config = SimpleNamespace(audio_transport='audiosocket')
    return engine


@pytest.mark.asyncio
@pytest.mark.parametrize('response,expected,guard', [
    ({'status': 204}, 'success', True),
    ({'status': 409}, 'failed', False),
    ({'status': 404}, 'failed', False),
    ({'status': 503}, 'failed', True),
    ({'status': 500}, 'failed', True),
    ({}, 'failed', True),
    (RuntimeError('synthetic transport failure'), 'failed', True),
])
async def test_voicemail_handoff_results_and_actual_engine_ownership(monkeypatch, response, expected, guard):
    session, ctx = await voicemail_context(monkeypatch, [response])
    result = await commit_voicemail(ctx)
    assert result['status'] == expected
    assert bool(getattr(session, 'transfer_active', False)) is guard
    assert Engine._session_was_transferred(session) is guard
    uncertain = guard and expected == 'failed'
    assert bool(result.get('handoff_indeterminate')) is uncertain
    if uncertain:
        assert session.voicemail_handoff_outcome == 'indeterminate'
        calls = ctx.ari_client.send_command.await_count
        assert (await VoicemailTool().execute({}, ctx))['status'] == 'failed'
        assert ctx.ari_client.send_command.await_count == calls  # never replay
    if not guard:
        # The engine's rejection recovery retires the announcement action.
        session.pending_deferred_transfer = None
        engine = native_engine(session, ari_client([{'status': 204}]))
        assert await engine._terminate_call_after_audio(session.call_id, reason='after-vm-rejection', audio_already_drained=True)


@pytest.mark.asyncio
async def test_voicemail_missing_dialplan_target_does_not_claim_or_continue(monkeypatch):
    session, ctx = await voicemail_context(monkeypatch, [])
    ctx.ari_client.send_command.side_effect = [{'value': '0'}]
    assert (await VoicemailTool().execute({}, ctx))['status'] == 'failed'
    assert not Engine._session_was_transferred(session)
    assert ctx.ari_client.send_command.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('config', [
    {'extension': '100', 'selected_mailbox_key': 'deleted', 'mailboxes': {}},
    {'default_mailbox_key': 'deleted', 'mailboxes': {'other': {'extension': '200'}}},
    {'extension': '100),SHELL(id'},
    {'enabled': False, 'extension': '100'},
])
async def test_voicemail_invalid_or_deleted_resources_fail_closed(monkeypatch, config):
    session, ctx = await voicemail_context(monkeypatch, [], config)
    assert (await VoicemailTool().execute({}, ctx))['status'] == 'failed'
    ctx.ari_client.send_command.assert_not_awaited()
    assert not Engine._session_was_transferred(session)


@pytest.mark.asyncio
@pytest.mark.parametrize('during_request', [False, True])
async def test_voicemail_cancellation_only_reclaims_before_request(monkeypatch, during_request):
    session, ctx = await voicemail_context(monkeypatch, [])
    reached = asyncio.Event()
    async def wait(*args, **kwargs):
        reached.set()
        await asyncio.Event().wait()
    if during_request:
        ctx.ari_client.continue_in_dialplan = wait
    else:
        ctx.ari_client.dialplan_target_exists = wait
    task = asyncio.create_task(commit_voicemail(ctx))
    await reached.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert Engine._session_was_transferred(session) is during_request
    assert session.voicemail_handoff_outcome == ('indeterminate' if during_request else None)


@pytest.mark.asyncio
async def test_voicemail_rejection_does_not_overwrite_newer_transfer_owner(monkeypatch):
    session, ctx = await voicemail_context(monkeypatch, [])
    async def rejected(*args, **kwargs):
        session.transfer_state = 'attended'
        session.transfer_target = 'new-owner'
        return False
    ctx.ari_client.continue_in_dialplan = rejected
    assert (await commit_voicemail(ctx))['status'] == 'failed'
    assert session.transfer_state == 'attended'
    assert session.transfer_target == 'new-owner'
    assert session.transfer_active


@pytest.mark.asyncio
@pytest.mark.parametrize('response,outcome', [({'status': 409}, 'completed'), ({'status': 503}, 'error'), ({'status': 204}, 'transferred')])
async def test_voicemail_result_persists_uncertainty_without_false_transfer(monkeypatch, tmp_path, response, outcome):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    session, ctx = await voicemail_context(monkeypatch, [response])
    session.conversation_history = [{'role': 'user', 'content': 'Synthetic voicemail request'}]
    result = await commit_voicemail(ctx)
    session.tool_calls = [build_in_call_tool_record(call_id=session.call_id, tool_call_id='vm-tool',
        tool_name='leave_voicemail', parameters={}, result=result)]
    store = CallHistoryStore(db_path=str(tmp_path / 'history.db'))
    monkeypatch.setattr('src.core.call_history.get_call_history_store', lambda: store)
    engine = native_engine(session, ctx.ari_client)
    await engine._persist_call_history(session, session.call_id)
    record = await store.get_by_call_id(session.call_id)
    assert record.outcome == outcome
    assert record.tool_calls[0]['status'] == ('success' if result['status'] == 'success' else 'failure')
    assert bool(record.tool_calls[0].get('handoff_indeterminate')) == (response['status'] == 503)
    assert '100' not in record.tool_calls[0]['message']


@pytest.mark.asyncio
@pytest.mark.parametrize('first', [{'status': 503}, RuntimeError('offline failure')])
async def test_native_hangup_retries_until_accepted_and_is_idempotent(first):
    session = CallSession(call_id='native', caller_channel_id='native')
    ari = ari_client([first, {'status': 204}])
    engine = native_engine(session, ari)
    assert await engine._terminate_call_after_audio('native', reason='test', audio_already_drained=True)
    assert not await engine._terminate_call_after_audio('native', reason='duplicate', audio_already_drained=True)
    assert ari.send_command.await_count == 2


@pytest.mark.asyncio
async def test_native_exhaustion_releases_owner_and_later_request_can_retry():
    session = CallSession(call_id='native', caller_channel_id='native')
    ari = ari_client([{'status': 503}] * 3 + [{'status': 404}])
    engine = native_engine(session, ari)
    assert not await engine._terminate_call_after_audio('native', reason='test', audio_already_drained=True)
    assert 'native' not in engine._terminal_hangup_started
    assert ari.send_command.await_count == 3
    assert await engine._terminate_call_after_audio('native', reason='later', audio_already_drained=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('new_owner', ['transfer', 'attended', 'deferred', 'cleanup', 'replacement', 'reclaimed'])
async def test_native_retry_yields_to_lifecycle_changes(new_owner):
    session = CallSession(call_id='native', caller_channel_id='native')
    engine = native_engine(session, None)
    async def rejected(*args, **kwargs):
        if new_owner == 'transfer': session.transfer_active = True
        elif new_owner == 'attended': session.current_action = {'type': 'attended_transfer'}
        elif new_owner == 'deferred': session.pending_deferred_transfer = {'id': 'new'}
        elif new_owner == 'cleanup': session.cleanup_in_progress = True
        elif new_owner == 'replacement':
            engine.session_store.get_by_call_id.return_value = CallSession(call_id='native', caller_channel_id='new-channel')
        else:
            engine._terminal_hangup_locks['native'] = asyncio.Lock()
            engine._terminal_hangup_started.add('native')
        return {'status': 503}
    engine.ari_client = ari_client(rejected)
    assert not await engine._terminate_call_after_audio('native', reason='test', audio_already_drained=True)
    engine.ari_client.send_command.assert_awaited_once()
    if new_owner == 'reclaimed': assert 'native' in engine._terminal_hangup_started


@pytest.mark.asyncio
@pytest.mark.parametrize('during_drain', [False, True])
async def test_native_cancellation_releases_owner(during_drain):
    session = CallSession(call_id='native', caller_channel_id='native')
    reached = asyncio.Event()
    async def wait(*args, **kwargs):
        reached.set()
        await asyncio.Event().wait()
    engine = native_engine(session, SimpleNamespace(hangup_channel=wait))
    engine._wait_for_call_audio_drain = wait
    task = asyncio.create_task(engine._terminate_call_after_audio('native', reason='cancel', audio_already_drained=not during_drain))
    await reached.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert 'native' not in engine._terminal_hangup_started


async def pre_call_engine(execute, *, timeout_ms=1000, threshold_ms=1, count=1, play=None, stop=None):
    engine = Engine.__new__(Engine)
    session = CallSession(call_id='pre-call', caller_channel_id='pre-call', context_name='test')
    events = []
    started = asyncio.Event()
    async def command(method, resource, data=None, **kwargs):
        events.append((method, resource, data))
        if method == 'POST':
            started.set()
            if play: await play()
            return {'id': data['playbackId'], 'state': 'playing', 'target_uri': 'channel:pre-call'}
        return (await stop()) if stop else {'status': 204}
    engine.ari_client = ari_client(command)
    tools = [SimpleNamespace(definition=SimpleNamespace(name=f'enrich-{i}', timeout_ms=timeout_ms,
        hold_audio_file='custom/synthetic-hold', hold_audio_threshold_ms=threshold_ms,
        output_variables=['value']), execute=execute,
        config=SimpleNamespace(kind='http', call_metadata_fields={})) for i in range(count)]
    engine._tool_registry_for_session = Mock(return_value=SimpleNamespace(get_tools_for_context=lambda **kwargs: tools))
    engine._tool_config_for_session = Mock(return_value={})
    engine.transport_orchestrator = SimpleNamespace(get_context_config=lambda *args: SimpleNamespace(pre_call_tools=['enrich'], disable_global_pre_call_tools=[]))
    engine._stop_connection_audio = AsyncMock()
    engine._save_session = AsyncMock()
    return engine, session, events, started


@pytest.mark.asyncio
@pytest.mark.parametrize('ending', ['success', 'error', 'timeout', 'cancel', 'cleanup'])
async def test_pre_call_hold_stops_exact_owned_playback_for_every_exit(ending):
    async def execute(ctx):
        await started.wait()
        await asyncio.sleep(.005)
        if ending == 'error': raise RuntimeError('synthetic failure')
        if ending in {'timeout', 'cancel', 'cleanup'}: await asyncio.Event().wait()
        return {'value': 'ok'}
    engine, session, events, started = await pre_call_engine(execute, timeout_ms=50)
    task = asyncio.create_task(engine._execute_pre_call_tools(session.call_id, session))
    if ending in {'cancel', 'cleanup'}:
        await started.wait()
        if ending == 'cleanup':
            session.cleanup_in_progress = True
            await stop_pre_call_holds(session)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    else:
        result = await task
        assert result == {'value': 'ok' if ending == 'success' else ''}
    posts = [data['playbackId'] for method, _, data in events if method == 'POST']
    deletes = [resource.split('/')[-1] for method, resource, _ in events if method == 'DELETE']
    assert len(posts) == 1
    assert deletes and set(deletes) == set(posts)
    assert not session.pre_call_hold_audio
    engine._stop_connection_audio.assert_awaited_once()
    assert session.pre_call_tool_calls[0]['status'] == {'success': 'ok', 'error': 'error', 'timeout': 'timeout', 'cancel': 'error', 'cleanup': 'error'}[ending]


@pytest.mark.asyncio
async def test_pre_call_fast_tools_cancel_hold_timer_without_starting_playback():
    async def execute(ctx): return {'value': 'ok'}
    engine, session, events, _ = await pre_call_engine(execute, threshold_ms=1000)
    assert await engine._execute_pre_call_tools(session.call_id, session) == {'value': 'ok'}
    assert events == []
    assert not session.pre_call_hold_audio


@pytest.mark.asyncio
async def test_pre_call_multiple_tools_release_only_their_own_playbacks():
    async def execute(ctx):
        await asyncio.sleep(.02)
        return {'value': 'ok'}
    engine, session, events, _ = await pre_call_engine(execute, count=3)
    await engine._execute_pre_call_tools(session.call_id, session)
    posts = {data['playbackId'] for method, _, data in events if method == 'POST'}
    deletes = {resource.split('/')[-1] for method, resource, _ in events if method == 'DELETE'}
    assert len(posts) == 3 and deletes == posts
    assert not session.pre_call_hold_audio


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_pre_call_late_start_is_stopped_after_acceptance(cancel):
    release = asyncio.Event()
    async def play(): await release.wait()
    async def execute(ctx):
        await started.wait()
        if cancel: await asyncio.Event().wait()
        return {'value': 'ok'}
    engine, session, events, started = await pre_call_engine(execute, play=play)
    task = asyncio.create_task(engine._execute_pre_call_tools(session.call_id, session))
    await started.wait()
    if cancel: task.cancel()
    await asyncio.sleep(.01)
    release.set()
    if cancel:
        with pytest.raises(asyncio.CancelledError): await task
    else: await task
    assert events[-1][0] == 'DELETE'
    assert not session.pre_call_hold_audio


@pytest.mark.asyncio
async def test_pre_call_stop_retries_rejection_and_accepts_already_deleted():
    responses = iter([{'status': 503}, {'status': 404}])
    async def stop(): return next(responses)
    async def execute(ctx):
        await started.wait()
        return {'value': 'ok'}
    engine, session, events, started = await pre_call_engine(execute, stop=stop)
    await engine._execute_pre_call_tools(session.call_id, session)
    assert len([1 for method, _, _ in events if method == 'DELETE']) == 2
    assert not session.pre_call_hold_audio


@pytest.mark.asyncio
async def test_native_exhaustion_releases_provider_terminal_protection():
    session = CallSession(call_id='native', caller_channel_id='native')
    engine = native_engine(session, ari_client([{'status': 503}] * 3))
    provider = SimpleNamespace(release_terminal_output_protection=Mock())
    engine._call_providers = {'native': provider}
    assert not await engine._terminate_call_after_audio('native', reason='retryable', audio_already_drained=True)
    provider.release_terminal_output_protection.assert_called_once()


@pytest.mark.asyncio
async def test_native_concurrent_terminal_requests_do_not_duplicate_accepted_hangup():
    session = CallSession(call_id='native', caller_channel_id='native')
    engine = native_engine(session, ari_client([{'status': 204}]))
    results = await asyncio.gather(*(engine._terminate_call_after_audio('native', reason='concurrent', audio_already_drained=True) for _ in range(3)))
    assert sorted(results) == [False, False, True]
    engine.ari_client.send_command.assert_awaited_once()


@pytest.mark.asyncio
async def test_pre_call_finalizer_retains_late_start_after_tool_close_budget():
    release = asyncio.Event()
    async def play(): await release.wait()
    async def execute(ctx):
        await started.wait()
        return {'value': 'ok'}
    engine, session, events, started = await pre_call_engine(execute, play=play)
    task = asyncio.create_task(engine._execute_pre_call_tools(session.call_id, session))
    await started.wait()
    await task  # returns after bounded close, without cancelling an in-flight POST
    assert session.pre_call_hold_audio
    owner = next(iter(session.pre_call_hold_audio))
    assert not owner.task.cancelled()
    release.set()
    await owner.task
    assert events[-1][0] == 'DELETE'
    assert not session.pre_call_hold_audio


@pytest.mark.asyncio
async def test_pre_call_cleanup_before_threshold_cancels_timer():
    async def execute(ctx): await asyncio.Event().wait()
    engine, session, events, _ = await pre_call_engine(execute, threshold_ms=1000)
    task = asyncio.create_task(engine._execute_pre_call_tools(session.call_id, session))
    while not session.pre_call_hold_audio: await asyncio.sleep(0)
    session.cleanup_in_progress = True
    await stop_pre_call_holds(session)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert events == []
    assert not session.pre_call_hold_audio


@pytest.mark.asyncio
async def test_pre_call_unconfirmed_stop_remains_owned_for_cleanup_recovery():
    responses = iter([{'status': 503}] * 3 + [{'status': 204}])
    async def stop(): return next(responses)
    async def execute(ctx):
        await started.wait()
        return {'value': 'ok'}
    engine, session, events, started = await pre_call_engine(execute, stop=stop)
    await engine._execute_pre_call_tools(session.call_id, session)
    assert session.pre_call_hold_audio
    await stop_pre_call_holds(session)
    assert not session.pre_call_hold_audio
    assert len([1 for method, _, _ in events if method == 'DELETE']) == 4


@pytest.mark.asyncio
async def test_voicemail_rejection_restores_prior_nontransfer_fields(monkeypatch):
    session, ctx = await voicemail_context(monkeypatch, [{'status': 409}])
    session.transfer_active = False
    session.transfer_state = ''
    session.transfer_target = 'previous-diagnostic-target'
    assert (await commit_voicemail(ctx))['status'] == 'failed'
    assert (session.transfer_active, session.transfer_state, session.transfer_target) == (False, '', 'previous-diagnostic-target')


@pytest.mark.asyncio
async def test_pending_voicemail_history_is_uncertain_even_before_tool_result(monkeypatch, tmp_path):
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    session = CallSession(call_id='pending-vm', caller_channel_id='pending-vm')
    session.voicemail_handoff_outcome = 'pending'
    session.transfer_active = True
    session.call_outcome = 'no_input_timeout'
    store = CallHistoryStore(db_path=str(tmp_path / 'history.db'))
    monkeypatch.setattr('src.core.call_history.get_call_history_store', lambda: store)
    engine = native_engine(session, None)
    await engine._persist_call_history(session, session.call_id)
    record = await store.get_by_call_id(session.call_id)
    assert record.outcome == 'error'
    assert 'could not be confirmed' in record.error_message


@pytest.mark.asyncio
async def test_pre_call_cancellation_during_close_retains_outcome_and_late_cleanup():
    release = asyncio.Event()
    async def play(): await release.wait()
    async def execute(ctx):
        await started.wait()
        return {'value': 'ok'}
    engine, session, events, started = await pre_call_engine(execute, play=play)
    task = asyncio.create_task(engine._execute_pre_call_tools(session.call_id, session))
    await started.wait()
    while not session.pre_call_tool_calls: await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert session.pre_call_tool_calls[0]['status'] == 'ok'
    assert session.pre_call_hold_audio
    owner = next(iter(session.pre_call_hold_audio))
    release.set()
    await owner.task
    assert events[-1][0] == 'DELETE'
    assert not session.pre_call_hold_audio


@pytest.mark.asyncio
@pytest.mark.parametrize('new_owner', ['deferred', 'transfer', 'attended', 'cleanup', 'completed', 'session', 'channel', 'lock'])
async def test_native_delete_admission_rechecks_after_actual_wait_for_scheduling(new_owner):
    session = CallSession(call_id='scheduled-native', caller_channel_id='scheduled-native')
    ari = ari_client([{'status': 204}])
    engine = native_engine(session, ari)
    scheduled = False

    def take_ownership():
        if new_owner == 'deferred':
            session.pending_deferred_transfer = {'id': 'new-action', 'kind': 'transfer'}
        elif new_owner == 'transfer':
            session.transfer_active = True
            session.transfer_state = 'voicemail'
        elif new_owner == 'attended':
            session.current_action = {'type': 'attended_transfer', 'decision': 'pending'}
        elif new_owner == 'cleanup':
            session.cleanup_in_progress = True
        elif new_owner == 'completed':
            session.cleanup_completed = True
        elif new_owner == 'session':
            engine.session_store.get_by_call_id.return_value = CallSession(
                call_id=session.call_id, caller_channel_id='replacement-channel')
        elif new_owner == 'channel':
            session.caller_channel_id = 'replacement-channel'
        else:
            engine._terminal_hangup_locks[session.call_id] = asyncio.Lock()

    def save(*args, **kwargs):
        nonlocal scheduled
        if not scheduled:
            scheduled = True
            # A queued routing task runs before wait_for's separately scheduled
            # coroutine. Neither wait_for nor the ARI wrapper is replaced here.
            asyncio.get_running_loop().call_soon(take_ownership)
    engine._save_session.side_effect = save
    result = await engine._terminate_call_after_audio(session.call_id, reason='scheduled-owner', audio_already_drained=True)
    assert scheduled
    assert result is False
    ari.send_command.assert_not_awaited()
