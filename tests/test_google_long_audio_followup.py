"""Qualify scoped speech detection and terminal races from live calls."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close
from src.config import GoogleProviderConfig
from src.engine import Engine
from tests.test_google_long_audio_playback import ingress_engine, caller_frame, bare_engine, provider, audio_content


@pytest.mark.parametrize('vertex,enabled', [(False,True),(False,False),(True,True),(True,False)])
@pytest.mark.asyncio
async def test_short_syllable_gap_is_only_allowed_for_developer_opt_in(vertex, enabled):
    e,s,p = ingress_engine(vertex=vertex, enabled=enabled)
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'speaking')
    e._apply_barge_in_action = AsyncMock()
    for amplitude in [3000]*3 + [100] + [3000]*3:
        await e._audiosocket_handle_audio('ingress', caller_frame(amplitude))
    assert e._apply_barge_in_action.await_count == int(enabled and not vertex)
    assert all(not any(c.args[0]) for c in p.send_audio.await_args_list)


@pytest.mark.parametrize('quiet_frames', [3, 10])
@pytest.mark.asyncio
async def test_longer_pause_resets_google_candidate(quiet_frames):
    e,s,p = ingress_engine()
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'speaking')
    e._apply_barge_in_action = AsyncMock()
    for amplitude in [3000]*3 + [100]*quiet_frames + [3000]*3:
        await e._audiosocket_handle_audio('ingress', caller_frame(amplitude))
    e._apply_barge_in_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_google_candidate_does_not_accumulate_sparse_noise(monkeypatch):
    e,s,p = ingress_engine()
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'speaking')
    e._apply_barge_in_action = AsyncMock()
    clock = [time.time()]
    s.tts_started_ts = clock[0] - 10
    monkeypatch.setattr('src.engine.time.time', lambda: clock[0])
    for _ in range(20):
        await e._audiosocket_handle_audio('ingress', caller_frame(3000))
        clock[0] += 0.3
    e._apply_barge_in_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_abort_is_not_google_drain_failure():
    p = provider()
    e,s = bare_engine(p)
    await e.session_store.upsert_call(s)
    async def aborted(*args, **kwargs):
        s.cleanup_in_progress = True
        return False
    e._wait_for_call_audio_drain = AsyncMock(side_effect=aborted)
    e._provider_output_drain_tasks[s.call_id] = asyncio.current_task()
    e._fail_google_audio_playback = AsyncMock()
    await e._finish_provider_output_after_drain(s.call_id, reset_timer=True, preserve_policy_state=False)
    assert not p._long_audio_failed
    e._fail_google_audio_playback.assert_not_awaited()
    assert s.call_id not in e._provider_output_drain_tasks


@pytest.mark.parametrize('vertex,enabled', [(False,True),(False,False),(True,True)])
@pytest.mark.asyncio
async def test_disconnect_drain_is_scoped_and_bounded(vertex, enabled):
    p = provider(vertex=vertex, enabled=enabled)
    e,s = bare_engine(p)
    await e.session_store.upsert_call(s)
    e.ari_client = SimpleNamespace(hangup_channel=AsyncMock())
    await e.on_provider_event({'type':'ProviderDisconnected','call_id':s.call_id,'code':1007,'reason':'test'})
    if enabled and not vertex:
        await asyncio.gather(*e._call_bg_tasks[s.call_id])
        e._terminate_call_after_audio.assert_awaited_once_with(
            s.call_id, reason='google_provider_disconnected', drain_timeout_cap_sec=8.0)
        e.ari_client.hangup_channel.assert_not_awaited()
    else:
        e._terminate_call_after_audio.assert_not_awaited()
        e.ari_client.hangup_channel.assert_awaited_once()


@pytest.mark.parametrize('drained', [True, False])
@pytest.mark.asyncio
async def test_disconnect_cap_overrides_120_second_backlog(drained):
    p = provider()
    e,s = bare_engine(p)
    await e.session_store.upsert_call(s)
    e.ari_client = SimpleNamespace(hangup_channel=AsyncMock())
    e._wait_for_call_audio_drain = AsyncMock(return_value=drained)
    e._terminate_call_after_audio = Engine._terminate_call_after_audio.__get__(e)
    assert await e._terminate_call_after_audio(s.call_id, reason='google_provider_disconnected', drain_timeout_cap_sec=8)
    assert e._wait_for_call_audio_drain.await_args.kwargs['timeout_sec'] == 8
    e.ari_client.hangup_channel.assert_awaited_once()
    assert not await e._terminate_call_after_audio(s.call_id, reason='duplicate', drain_timeout_cap_sec=8)
    e.ari_client.hangup_channel.assert_awaited_once()


@pytest.mark.parametrize('vertex,enabled', [(False,True),(False,False),(True,True)])
@pytest.mark.asyncio
async def test_close_seals_audio_before_disconnect_only_for_opt_in(vertex, enabled):
    p = provider(vertex=vertex, enabled=enabled)
    events=[]
    async def event(item): events.append(item)
    p.on_event=event
    p._in_audio_burst=True
    p._audio_response_id=1
    p._turn_has_assistant_output=True
    class Socket:
        def __aiter__(self): return self
        async def __anext__(self):
            raise ConnectionClosedError(Close(1007,'unsupported audio'),Close(1007,'unsupported audio'),True)
    p.websocket=Socket()
    await p._receive_loop()
    kinds=[e['type'] for e in events]
    if enabled and not vertex:
        assert kinds == ['GoogleAudioGenerationComplete','ProviderDisconnected']
        assert not p._in_audio_burst
    else:
        assert kinds == ['ProviderDisconnected']


@pytest.mark.parametrize('vertex,enabled', [(False,True),(False,False),(True,True)])
@pytest.mark.asyncio
async def test_interrupted_farewell_cannot_reuse_old_watchdog_audio(vertex, enabled):
    p=provider(vertex=vertex,enabled=enabled)
    p._hangup_after_response=True
    p._hangup_fallback_armed=True
    p._hangup_fallback_armed_at=time.monotonic()-10
    old_armed=p._hangup_fallback_armed_at
    p._hangup_fallback_audio_started=True
    p._hangup_fallback_turn_complete_seen=True
    p._last_audio_out_monotonic=time.monotonic()-2
    await p._handle_server_content({'serverContent':{'interrupted':True}})
    if enabled and not vertex:
        assert not p._hangup_fallback_audio_started
        assert not p._hangup_fallback_turn_complete_seen
        assert p._last_audio_out_monotonic is None
        assert p._hangup_fallback_armed_at > old_armed
        assert p._should_wait_for_turn_complete_before_fallback(time.monotonic(),p._hangup_fallback_armed_at)
        old=p._hangup_fallback_armed_at
        await p._handle_server_content({'serverContent':{'outputTranscription':{'text':'Thank you'}}})
        assert p._hangup_fallback_armed_at >= old
    else:
        assert p._hangup_fallback_audio_started
        assert p._hangup_fallback_armed_at==old_armed


def test_existing_configuration_remains_opt_out():
    assert not GoogleProviderConfig().long_audio_playback_enabled
    assert not GoogleProviderConfig(api_key='old').long_audio_playback_enabled


@pytest.mark.parametrize('when', ['before_generation_complete','after_generation_complete'])
@pytest.mark.asyncio
async def test_local_farewell_cancel_rejects_late_terminal_boundary(when):
    p=provider()
    events=[]
    async def event(item):events.append(item)
    p.on_event=event
    await p._handle_server_content({'serverContent':audio_content()})
    p._hangup_after_response=True
    p._hangup_fallback_armed=True
    p._hangup_fallback_audio_started=True
    if when=='after_generation_complete':await p._handle_audio_generation_complete()
    await p.handle_local_barge_in()
    if when=='before_generation_complete':await p._handle_audio_generation_complete()
    await p._handle_turn_complete()
    assert not p._hangup_fallback_audio_started
    assert not any(e['type']=='HangupReady' for e in events)
    assert p._hangup_after_response


@pytest.mark.parametrize('interrupt', [False, True])
@pytest.mark.asyncio
async def test_abnormal_close_drains_real_accepted_queue_before_hangup(interrupt):
    e,s,p=ingress_engine()
    p.on_event=e.on_provider_event
    s.provider_session_active=True
    await e.session_store.upsert_call(s)
    e._terminate_call_after_audio=Engine._terminate_call_after_audio.__get__(e)
    e._terminal_transport_quiet_sec=lambda:0.02
    await p._handle_server_content({'serverContent':audio_content()})
    q=e._provider_stream_queues[s.call_id]
    assert q.pending_bytes>0
    async def hangup(channel):
        assert q.empty()
    e.ari_client=SimpleNamespace(hangup_channel=AsyncMock(side_effect=hangup))
    class Socket:
        def __aiter__(self):return self
        async def __anext__(self):
            raise ConnectionClosedError(Close(1007,'unsupported audio'),Close(1007,'unsupported audio'),True)
    p.websocket=Socket()
    task=asyncio.create_task(p._receive_loop())
    await asyncio.sleep(0.03)
    e.ari_client.hangup_channel.assert_not_awaited()
    assert q.pending_bytes>0
    assert s.provider_session_active  # Local detection remains live during drain.
    if interrupt:
        for _ in range(6):await e._audiosocket_handle_audio('ingress',caller_frame())
        assert q.closed and not q.pending_bytes
        assert s.barge_in_count==1
    else:
        while not q.empty():q.get_nowait()
    await asyncio.wait_for(task,timeout=1)
    await asyncio.wait_for(asyncio.gather(*e._call_bg_tasks[s.call_id]),timeout=1)
    e.ari_client.hangup_channel.assert_awaited_once()
    drain=e._provider_output_drain_tasks.get(s.call_id)
    if drain:await drain
    assert not p._long_audio_failed


@pytest.mark.parametrize('phase', ['before','during'])
@pytest.mark.asyncio
async def test_disconnect_hangup_yields_to_attended_transfer(phase):
    p=provider()
    e,s=bare_engine(p)
    await e.session_store.upsert_call(s)
    e._terminate_call_after_audio=Engine._terminate_call_after_audio.__get__(e)
    e.ari_client=SimpleNamespace(hangup_channel=AsyncMock())
    async def drain(*args,**kwargs):
        s.current_action={'type':'attended_transfer','decision':'pending'}
        return True
    e._wait_for_call_audio_drain=AsyncMock(side_effect=drain)
    if phase=='before':s.current_action={'type':'attended_transfer','decision':'pending'}
    assert not await e._terminate_call_after_audio(s.call_id,reason='google_provider_disconnected',drain_timeout_cap_sec=8)
    e.ari_client.hangup_channel.assert_not_awaited()
    if phase=='during':assert s.call_id not in e._terminal_hangup_started


@pytest.mark.parametrize('accepted', [False, True])
@pytest.mark.asyncio
async def test_disconnect_waits_for_transfer_and_closes_only_on_failure(accepted):
    p=provider()
    e,s=bare_engine(p)
    s.current_action={'type':'attended_transfer','decision':'pending'}
    await e.session_store.upsert_call(s)
    task=asyncio.create_task(e.on_provider_event({'type':'ProviderDisconnected','call_id':s.call_id,'code':1007}))
    await asyncio.sleep(0.02)
    e._terminate_call_after_audio.assert_not_awaited()
    assert task.done()  # Serial event dispatch is free while routing is pending.
    worker = next(iter(e._call_bg_tasks[s.call_id]))
    s.current_action={'type':'attended_transfer','decision':'accepted' if accepted else 'declined'}
    if accepted:s.transfer_active=True
    await asyncio.wait_for(task,timeout=1)
    await asyncio.wait_for(worker,timeout=1)
    assert e._terminate_call_after_audio.await_count==int(not accepted)


@pytest.mark.asyncio
async def test_google_transport_failure_rejects_new_tool_work():
    p=provider()
    p._long_audio_transport_failed=True
    p._tool_adapter=SimpleNamespace(execute_tool=AsyncMock())
    await p._handle_tool_call({'toolCall':{'functionCalls':[{'id':'late','name':'hangup_call','args':{}}]}})
    p._tool_adapter.execute_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_disconnect_observer_is_deduplicated_and_bounds_stalled_transfer():
    e,s=bare_engine(provider())
    s.current_action={'type':'attended_transfer','decision':'pending'}
    await e.session_store.upsert_call(s)
    e._google_disconnect_transfer_wait_sec=lambda:0.02
    event={'type':'ProviderDisconnected','call_id':s.call_id,'code':1007}
    await e.on_provider_event(event)
    await e.on_provider_event(event)
    assert len(e._call_bg_tasks[s.call_id])==1
    await asyncio.wait_for(asyncio.gather(*e._call_bg_tasks[s.call_id]),timeout=0.5)
    e._terminate_call_after_audio.assert_not_awaited()
    assert s.current_action['decision']=='pending'


@pytest.mark.asyncio
async def test_disconnect_observer_exits_when_cleanup_starts():
    e,s=bare_engine(provider())
    s.current_action={'type':'attended_transfer','decision':'pending'}
    await e.session_store.upsert_call(s)
    await e.on_provider_event({'type':'ProviderDisconnected','call_id':s.call_id,'code':1007})
    worker=next(iter(e._call_bg_tasks[s.call_id]))
    await asyncio.sleep(0)
    s.cleanup_in_progress=True
    await asyncio.wait_for(worker,timeout=0.5)
    e._terminate_call_after_audio.assert_not_awaited()


def test_disconnect_observer_budget_covers_configured_transfer_phases():
    e,_=bare_engine(provider())
    e.config.tools={'attended_transfer':{'dial_timeout_seconds':60,'caller_screening_max_seconds':12,
        'ai_briefing_timeout_seconds':4,'agent_accept_timeout_seconds':30,'tts_timeout_seconds':10}}
    assert e._google_disconnect_transfer_wait_sec()==316
    e.config.tools['attended_transfer']['accept_timeout_seconds']=20
    assert e._google_disconnect_transfer_wait_sec()==306
    e.config.tools["attended_transfer"]["dial_timeout_seconds"]=1e308
    assert e._google_disconnect_transfer_wait_sec()==600


@pytest.mark.asyncio
async def test_completion_overflow_revokes_tools_before_disconnect_callback_yields():
    p=provider()
    p._in_audio_burst=True
    p._generated_audio_turns=[{}]*8
    entered=asyncio.Event()
    release=asyncio.Event()
    async def disconnect(**kwargs):
        entered.set()
        await release.wait()
    p._emit_provider_disconnected=disconnect
    p._tool_adapter=SimpleNamespace(execute_tool=AsyncMock())
    task=asyncio.create_task(p._handle_audio_generation_complete())
    await asyncio.wait_for(entered.wait(),timeout=0.5)
    try:
        assert p._long_audio_transport_failed
        await p._handle_tool_call({'toolCall':{'functionCalls':[{'id':'late','name':'hangup_call','args':{}}]}})
        p._tool_adapter.execute_tool.assert_not_awaited()
    finally:
        release.set()
        await task
