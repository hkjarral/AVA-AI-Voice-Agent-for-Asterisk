"""Replay the recorded Google burst and pin isolation/cancellation boundaries."""
import asyncio
import base64
import json
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import GoogleProviderConfig
from src.core.audio_backlog import AudioBacklogQueue
from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine
from src.providers.google_live import GoogleLiveProvider


def provider(*, enabled=True, vertex=False):
    p = GoogleLiveProvider(GoogleProviderConfig(
        api_key="test", use_vertex_ai=vertex, long_audio_playback_enabled=enabled,
        output_encoding="slin16", output_sample_rate_hz=16000,
    ), AsyncMock())
    p._call_id = "long-test"
    p._track_conversation_message = AsyncMock()
    return p


def audio_content():
    return {"modelTurn": {"parts": [{"inlineData": {
        "mimeType": "audio/pcm;rate=24000",
        "data": base64.b64encode(b"\0\0" * 960).decode(),
    }}]}}


def bare_engine(p, *, coalesce=False, continuous=True):
    e = Engine.__new__(Engine)
    e.config = SimpleNamespace(
        default_provider="google-test", audio_transport="audiosocket", downstream_mode="stream",
        streaming=SimpleNamespace(sample_rate=16000, coalesce_enabled=coalesce,
                                  coalesce_min_ms=600, micro_fallback_ms=300),
    )
    e.provider_kinds = {"google-test": "google_live"}
    e._call_providers = {p._call_id: p}
    e.providers = {"google-test": p}
    e.session_store = SessionStore()
    e.conversation_coordinator = None
    e.no_input_watchdog = SimpleNamespace(note_agent_output_start=AsyncMock(), note_agent_output_end=AsyncMock())
    e.audio_capture = SimpleNamespace(append_encoded=lambda *a: None)
    e._stop_connection_audio = AsyncMock()
    e._emit_transport_card = lambda *a, **kw: None
    e._resolve_stream_targets = lambda *a: ("slin16", 16000, None)
    e._update_audio_diagnostics = lambda *a: None
    e._save_session = AsyncMock()
    e._call_bg_tasks = {}
    e._provider_output_operations = {}
    e._provider_output_drain_tasks = {}
    e._agent_output_active_calls = set()
    e._runtime_alignment_logged = set()
    e.provider_alignment_issues = {}
    e._segment_tts_active = set()
    e._downstream_file_streaming_logged = set()
    for name in ("_provider_stream_queues", "_provider_stream_formats", "_provider_coalesce_buf",
                 "_provider_bytes", "_enqueued_bytes", "_provider_chunk_seq", "_provider_segment_start_ts",
                 "_downstream_file_audio_events"):
        setattr(e, name, {})
    e.streaming_playback_manager = SimpleNamespace(
        continuous_stream=continuous, active_streams={}, jitter_buffers={}, frame_remainders={},
        start_streaming_playback=AsyncMock(), start_segment_gating=AsyncMock(),
        mark_segment_boundary=AsyncMock(), end_segment_gating=AsyncMock(),
        stop_streaming_playback=AsyncMock(), record_provider_bytes=lambda *a: None,
    )
    e._terminate_call_after_audio = AsyncMock()
    s = CallSession(call_id=p._call_id, caller_channel_id=p._call_id, provider_name="google-test")
    s.transport_profile = SimpleNamespace(format="slin16", wire_sample_rate=16000, sample_rate=16000)
    return e, s


async def send_audio(e, data, response_id=1):
    await e.on_provider_event({"type": "AgentAudio", "call_id": "long-test", "data": data,
                               "encoding": "slin16", "sample_rate": 16000,
                               "audio_response_id": response_id})


def completion(response_id=1, *, turn=False):
    return {"type": "AgentAudioDone" if turn else "GoogleAudioGenerationComplete",
            "call_id": "long-test", "audio_response_id": response_id, "streaming_done": turn}


@pytest.mark.asyncio
async def test_recorded_94_second_arrival_schedule_replays_without_loss():
    fixture = json.loads((Path(__file__).parent / "fixtures/google_2_5_long_audio_schedule.json").read_text())
    q = AudioBacklogQueue(120 * 32000)
    legacy = asyncio.Queue(maxsize=256)
    expected, delivered = [], []
    # Virtual telephony clock; preserve recorded arrival ordering, with a
    # whole-chunk consumer limited to real-time output (no wall-clock sleep).
    playout_end = 0.0
    legacy_drops = 0
    for i, (arrival, size) in enumerate(fixture["chunks"]):
        while not q.empty() and playout_end + len(q._queue[0]) / 32000 <= arrival:
            chunk = q.get_nowait()
            playout_end += len(chunk) / 32000
            delivered.append(chunk)
            if not legacy.empty():
                legacy.get_nowait()
        chunk = struct.pack('<I', i) * (size // 4)
        expected.append(chunk)
        q.put_nowait(chunk)
        try:
            legacy.put_nowait(chunk)
        except asyncio.QueueFull:
            legacy_drops += 1
    while not q.empty():
        delivered.append(q.get_nowait())
    assert legacy_drops > 0  # The fixture actually exercises the old limitation.
    assert b''.join(delivered) == b''.join(expected)
    assert sum(map(len, delivered)) == 3011840
    assert q.pending_bytes == q.audio_items == 0


@pytest.mark.asyncio
async def test_engine_admits_the_entire_recorded_burst_even_without_a_consumer():
    p = provider()
    e, s = bare_engine(p)
    await e.session_store.upsert_call(s)
    fixture = json.loads((Path(__file__).parent / "fixtures/google_2_5_long_audio_schedule.json").read_text())
    expected = []
    for i, (_, size) in enumerate(fixture["chunks"]):
        chunk = struct.pack('<I', i) * (size // 4)
        expected.append(chunk)
        await send_audio(e, chunk)
    q = e._provider_stream_queues[s.call_id]
    assert isinstance(q, AudioBacklogQueue)
    assert q.qsize() == 2330
    assert e._enqueued_bytes[s.call_id] == 3011840
    assert b''.join(q.get_nowait() for _ in range(q.qsize())) == b''.join(expected)
    assert not p._long_audio_failed


@pytest.mark.parametrize('name,vertex,enabled,expected', [
    ('google-test',False,True,AudioBacklogQueue), ('google-test',True,True,asyncio.Queue),
    ('google-test',False,False,asyncio.Queue), ('openai',False,True,asyncio.Queue),
    ('pipeline',False,True,asyncio.Queue), ('deepgram',False,True,asyncio.Queue),
    ('elevenlabs',False,True,asyncio.Queue), ('grok',False,True,asyncio.Queue),
    ('local',False,True,asyncio.Queue),
])
def test_queue_selection_is_per_call_and_respects_backend(name, vertex, enabled, expected):
    p = provider(enabled=enabled, vertex=vertex)
    e, s = bare_engine(p)
    if name != 'google-test':
        s.provider_name = name
    q = e._new_provider_audio_queue(s.call_id, s)
    assert type(q) is expected
    if expected is asyncio.Queue:
        assert q.maxsize == 256
    else:
        assert q.max_bytes == 3840000


@pytest.mark.asyncio
async def test_byte_and_item_limits_reserve_control_space_and_close_rejects_late_audio():
    q = AudioBacklogQueue(4, max_audio_items=2)
    q.put_nowait(b'ab')
    q.put_nowait(b'cd')
    with pytest.raises(asyncio.QueueFull):
        q.put_nowait(b'e')
    q.put_nowait(None)  # Completion is admitted even at full audio capacity.
    assert q.get_nowait() == b'ab'
    q.put_nowait(b'ef')
    assert q.pending_bytes == 4
    q.close()
    assert q.pending_bytes == q.audio_items == 0
    assert await q.get() is None
    with pytest.raises(asyncio.QueueFull):
        q.put_nowait(b'late')
    q = AudioBacklogQueue(100, max_audio_items=2)
    q.put_nowait(b'a'); q.put_nowait(b'b')
    with pytest.raises(asyncio.QueueFull):
        q.put_nowait(b'c')


@pytest.mark.asyncio
async def test_async_put_cancel_and_close_do_not_leak_waiters():
    q = AudioBacklogQueue(2)
    q.put_nowait(b'ab')
    task = asyncio.create_task(q.put(b'cd'))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not q._putters
    task = asyncio.create_task(q.put(b'cd'))
    await asyncio.sleep(0)
    q.close()
    with pytest.raises(asyncio.QueueFull):
        await task
    assert not q._putters


@pytest.mark.asyncio
@pytest.mark.parametrize('vertex,enabled', [(True,True),(False,False)])
async def test_vertex_and_default_adapter_keep_the_original_completion_contract(vertex, enabled):
    p = provider(vertex=vertex, enabled=enabled)
    await p._handle_server_content({'serverContent': {**audio_content(), 'generationComplete': True}})
    assert [c.args[0]['type'] for c in p.on_event.await_args_list] == ['AgentAudio']
    await p._handle_server_content({'serverContent': {'turnComplete': True}})
    assert [c.args[0]['type'] for c in p.on_event.await_args_list] == ['AgentAudio', 'AgentAudioDone']
    assert 'audio_response_id' not in p.on_event.await_args.args[0]


@pytest.mark.asyncio
async def test_generation_completion_persists_text_once_and_waits_for_real_turn_before_hangup():
    p = provider()
    p._hangup_after_response = True
    await p._handle_server_content({'serverContent': {**audio_content(),
        'outputTranscription': {'text': 'Goodbye.'}, 'generationComplete': True}})
    assert [c.args[0]['type'] for c in p.on_event.await_args_list] == ['AgentAudio', 'GoogleAudioGenerationComplete']
    p._track_conversation_message.assert_awaited_once_with('assistant', 'Goodbye.')
    await p._handle_server_content({'serverContent': {'generationComplete': True}})
    assert p.on_event.await_count == 2
    await p._handle_server_content({'serverContent': {'turnComplete': True}})
    assert [c.args[0]['type'] for c in p.on_event.await_args_list][-2:] == ['AgentAudioDone', 'HangupReady']
    assert p.on_event.await_args_list[-2].args[0]['audio_response_id'] == 1
    assert p._track_conversation_message.await_count == 1
    await p._handle_server_content({'serverContent': {'turnComplete': True}})
    assert p.on_event.await_count == 4


@pytest.mark.asyncio
async def test_late_turn_a_does_not_clear_response_b_audio_or_text():
    p = provider()
    await p._handle_server_content({'serverContent': {**audio_content(), 'generationComplete': True}})
    await p._handle_server_content({'serverContent': {**audio_content(), 'outputTranscription': {'text': 'Response B'}}})
    await p._handle_server_content({'serverContent': {'turnComplete': True}})
    assert p._in_audio_burst
    assert p._turn_has_assistant_output
    assert p._output_transcription_buffer == 'Response B'
    assert p.on_event.await_args.args[0]['audio_response_id'] == 1
    await p._handle_server_content({'serverContent': {'generationComplete': True}})
    assert p.on_event.await_args.args[0]['audio_response_id'] == 2


@pytest.mark.asyncio
async def test_server_interruption_after_generation_does_not_commit_farewell():
    p = provider()
    p._hangup_after_response = True
    await p._handle_server_content({'serverContent': {**audio_content(), 'generationComplete': True}})
    await p._handle_server_content({'serverContent': {'interrupted': True}})
    await p._handle_server_content({'serverContent': {'turnComplete': True}})
    assert [c.args[0]['type'] for c in p.on_event.await_args_list] == [
        'AgentAudio', 'GoogleAudioGenerationComplete', 'ProviderBargeIn']


@pytest.mark.asyncio
async def test_generation_end_keeps_gating_until_drain_and_duplicate_turn_does_not_reset_it():
    p = provider()
    e, s = bare_engine(p)
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'segment-token')
    await send_audio(e, b'\0\0' * 640)
    hold = asyncio.Event()
    started = asyncio.Event()
    async def drain(call_id, **kwargs):
        assert kwargs['timeout_sec'] == 150
        started.set()
        await hold.wait()
        return True
    e._wait_for_call_audio_drain = drain
    p.release_greeting_transport_guard = AsyncMock()
    await e.on_provider_event(completion())
    await started.wait()
    task = e._provider_output_drain_tasks[s.call_id]
    assert s.tts_tokens == {'segment-token'}
    assert not s.audio_capture_enabled
    await e.on_provider_event(completion())
    await e.on_provider_event(completion(turn=True))
    assert e._provider_output_drain_tasks[s.call_id] is task
    e._terminate_call_after_audio.assert_not_awaited()
    hold.set()
    await task
    assert not s.tts_tokens and s.audio_capture_enabled
    assert s.call_id not in e._agent_output_active_calls
    e.no_input_watchdog.note_agent_output_end.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_response_cancels_old_drain_and_ignores_its_late_completion():
    p = provider()
    e, s = bare_engine(p)
    await e.session_store.upsert_call(s)
    await send_audio(e, b'ab')
    started = asyncio.Event()
    async def drain(*a, **kw):
        started.set()
        await asyncio.Event().wait()
    e._wait_for_call_audio_drain = drain
    await e.on_provider_event(completion())
    await started.wait()
    stale = e._provider_output_drain_tasks[s.call_id]
    await send_audio(e, b'cd', response_id=2)
    await asyncio.sleep(0)
    await e.on_provider_event(completion(turn=True))
    assert stale.done()
    assert s.call_id not in e._provider_output_drain_tasks
    assert s.call_id in e._agent_output_active_calls
    e.no_input_watchdog.note_agent_output_end.assert_not_awaited()


@pytest.mark.asyncio
async def test_overflow_discards_backlog_and_schedules_one_explicit_recovery():
    p = provider()
    e, s = bare_engine(p)
    await e.session_store.upsert_call(s)
    q = AudioBacklogQueue(2)
    e._provider_stream_queues[s.call_id] = q
    q.put_nowait(b'ab')
    await send_audio(e, b'cd')
    assert p._long_audio_failed and q.closed and q.pending_bytes == 0
    await asyncio.gather(*list(e._call_bg_tasks[s.call_id]))
    e._terminate_call_after_audio.assert_awaited_once_with(
        s.call_id, reason='google_audio_backlog_overflow', audio_already_drained=True)
    await send_audio(e, b'ef')
    assert e._terminate_call_after_audio.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('coalesce,size', [(False,1280),(True,20000),(True,16000)])
async def test_all_queue_creation_paths_use_the_bounded_queue(coalesce,size):
    p = provider()
    e, s = bare_engine(p, coalesce=coalesce)
    e._note_provider_output_end = AsyncMock()
    await e.session_store.upsert_call(s)
    await send_audio(e, b'\0' * size)
    if s.call_id not in e._provider_stream_queues:
        await e.on_provider_event(completion())
    assert isinstance(e._provider_stream_queues[s.call_id], AudioBacklogQueue)


@pytest.mark.asyncio
async def test_noncontinuous_drain_snapshot_sees_the_detached_source_queue():
    p = provider()
    e, s = bare_engine(p, continuous=False)
    await e.session_store.upsert_call(s)
    q = AudioBacklogQueue(100)
    q.put_nowait(b'ab')
    e.streaming_playback_manager.active_streams[s.call_id] = {'audio_source_queue': q}
    assert (await e._call_audio_drain_snapshot(s.call_id))['pending_provider_chunks'] == 1


@pytest.mark.asyncio
async def test_stalled_google_drain_recovers_without_claiming_playback_complete():
    p = provider()
    e, s = bare_engine(p)
    await e.session_store.upsert_call(s)
    e._wait_for_call_audio_drain = AsyncMock(return_value=False)
    e._provider_output_drain_tasks[s.call_id] = asyncio.current_task()
    await e._finish_provider_output_after_drain(s.call_id, reset_timer=True, preserve_policy_state=False)
    assert p._long_audio_failed
    e._terminate_call_after_audio.assert_awaited_once_with(
        s.call_id, reason='google_audio_drain_timeout', audio_already_drained=True)
    e.no_input_watchdog.note_agent_output_end.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_turn_boundary_cannot_end_a_newer_audio_response():
    p = provider()
    await p._handle_server_content({'serverContent': {**audio_content(), 'generationComplete': True}})
    await p._handle_server_content({'serverContent': {'turnComplete': True}})
    await p._handle_server_content({'serverContent': audio_content()})
    p.on_event.reset_mock()
    await p._handle_server_content({'serverContent': {'turnComplete': True}})
    assert p._in_audio_burst and p._audio_response_id == 2
    p.on_event.assert_not_awaited()
    await p._handle_server_content({'serverContent': {'generationComplete': True, 'turnComplete': True}})
    assert [c.args[0]['type'] for c in p.on_event.await_args_list] == [
        'GoogleAudioGenerationComplete', 'AgentAudioDone']


@pytest.mark.asyncio
async def test_old_farewell_boundary_cannot_arm_hangup_for_newer_response():
    p = provider()
    p._maybe_arm_cleanup_after_tts = AsyncMock()
    await p._handle_server_content({'serverContent': {**audio_content(),
        'outputTranscription': {'text': 'Goodbye.'}, 'generationComplete': True}})
    await p._handle_server_content({'serverContent': audio_content()})
    p._hangup_after_response = True
    p.on_event.reset_mock()
    await p._handle_server_content({'serverContent': {'turnComplete': True}})
    p._maybe_arm_cleanup_after_tts.assert_not_awaited()
    assert [c.args[0]['type'] for c in p.on_event.await_args_list] == ['AgentAudioDone']
    assert p._in_audio_burst


@pytest.mark.asyncio
async def test_terminal_transfer_is_deferred_until_the_real_turn_boundary():
    p = provider()
    e, s = bare_engine(p)
    await e.session_store.upsert_call(s)
    s.pending_deferred_transfer = {'action_id': 'transfer-test'}
    e._commit_pending_deferred_transfer_for_call = AsyncMock()
    e._note_provider_output_end = AsyncMock()
    e._farewell_done_events = {'farewell_done_long-test': asyncio.Event()}
    await send_audio(e, b'ab')
    await e.on_provider_event(completion())
    e._commit_pending_deferred_transfer_for_call.assert_not_awaited()
    assert not e._farewell_done_events['farewell_done_long-test'].is_set()
    await e.on_provider_event(completion(turn=True))
    await asyncio.gather(*list(e._call_bg_tasks[s.call_id]))
    e._commit_pending_deferred_transfer_for_call.assert_awaited_once_with(s.call_id, s)
    assert e._farewell_done_events['farewell_done_long-test'].is_set()
    e._terminate_call_after_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_backlog_is_released_and_new_stream_has_no_old_tail():
    p = provider()
    e, s = bare_engine(p)
    await e.session_store.upsert_call(s)
    await send_audio(e, b'ab' * 500000)
    q = e._provider_stream_queues[s.call_id]
    e._close_google_audio_backlog(s.call_id, remove=True)
    assert q.pending_bytes == 0 and q.closed
    assert await q.get() is None
    assert s.call_id not in e._provider_stream_queues
    await send_audio(e, b'cd', response_id=2)
    q2 = e._provider_stream_queues[s.call_id]
    assert q2 is not q
    assert q2.get_nowait() == b'cd' and q2.empty()


@pytest.mark.asyncio
async def test_cancel_during_gating_release_cannot_unmute_new_response():
    p = provider()
    e, s = bare_engine(p)
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'shared-stream-token')
    await send_audio(e, b'ab')
    e._wait_for_call_audio_drain = AsyncMock(return_value=True)
    entered = asyncio.Event()
    async def release_guard():
        entered.set()
        await asyncio.Event().wait()
    p.release_greeting_transport_guard = release_guard
    await e.on_provider_event(completion())
    await asyncio.wait_for(entered.wait(), timeout=0.5)
    stale = e._provider_output_drain_tasks[s.call_id]
    await send_audio(e, b'cd', response_id=2)
    await asyncio.sleep(0)
    assert stale.cancelled()
    assert s.tts_tokens == {'shared-stream-token'} and not s.audio_capture_enabled
    e.no_input_watchdog.note_agent_output_end.assert_not_awaited()


@pytest.mark.asyncio
async def test_connection_backend_and_queue_choice_stay_per_call():
    dev, vertex = provider(), provider(vertex=True)
    e, dev_s = bare_engine(dev)
    vertex._call_id = 'vertex-call'
    e.provider_kinds['google-vertex'] = 'google_live'
    e._call_providers[vertex._call_id] = vertex
    vertex_s = CallSession(call_id=vertex._call_id, caller_channel_id=vertex._call_id, provider_name='google-vertex')
    vertex_s.transport_profile = dev_s.transport_profile
    assert type(e._new_provider_audio_queue(dev_s.call_id, dev_s)) is AudioBacklogQueue
    assert type(e._new_provider_audio_queue(vertex_s.call_id, vertex_s)) is asyncio.Queue
    # A configured Vertex connection that actually fell back to Developer is
    # treated by the endpoint in use, not its display name or original flag.
    vertex._vertex_active = False
    assert type(e._new_provider_audio_queue(vertex_s.call_id, vertex_s)) is AudioBacklogQueue


@pytest.mark.asyncio
async def test_real_barge_in_action_flushes_large_backlog_and_restores_output_lifecycle():
    p = provider()
    e, s = bare_engine(p)
    s.media_rx_confirmed = True
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'stream-token')
    await send_audio(e, b'ab' * 500000)
    q = e._provider_stream_queues[s.call_id]
    e._wait_for_call_audio_drain = AsyncMock(return_value=True)
    async def stop(call_id):
        assert q.closed and q.pending_bytes == 0
    e.streaming_playback_manager.stop_streaming_playback.side_effect = stop
    await e._apply_barge_in_action(s.call_id, source='provider_event', reason='interrupted')
    task = e._provider_output_drain_tasks.get(s.call_id)
    if task:
        await task
    assert s.call_id not in e._provider_stream_queues
    assert s.call_id not in e._agent_output_active_calls
    assert s.audio_capture_enabled and not s.tts_tokens
    assert s.barge_in_count == 1
    assert not p._long_audio_failed


@pytest.mark.asyncio
async def test_cleanup_in_progress_rejects_late_experimental_audio():
    p = provider()
    e, s = bare_engine(p)
    s.cleanup_in_progress = True
    await e.session_store.upsert_call(s)
    await send_audio(e, b'ab')
    assert not e._provider_stream_queues
    e.streaming_playback_manager.start_streaming_playback.assert_not_awaited()


def ingress_engine(*, vertex=False, enabled=True, enhanced=False, rate=16000):
    """Exercise the real AudioSocket route and fallback, with no provider socket."""
    import time
    from src.config import BargeInConfig
    from src.core.vad_manager import VADResult

    p = provider(vertex=vertex, enabled=enabled)
    p.config.llm_model = 'gemini-2.5-flash-native-audio-latest'
    p.send_audio = AsyncMock()
    e, s = bare_engine(p)
    e.config.barge_in = BargeInConfig(greeting_protection_ms=5000)
    # The archived failing configuration resolves auto + this legacy flag to
    # provider mode, leaving the local VAD manager unset at startup.
    e.config.vad = SimpleNamespace(vad_mode='auto', use_provider_vad=not enhanced,
                                   upstream_squelch_enabled=False)
    e.config.audiosocket = SimpleNamespace(format='slin16')
    e.conn_to_channel = {'ingress': s.call_id}
    e.audio_socket_server = None
    e._pipeline_forced = {}
    e._pipeline_queues = {}
    e._resample_state_pipeline16k = {}
    e._resample_state_vad8k = {}
    e.audio_capture.append_pcm16 = lambda *a: None
    e._observe_no_input_audio = AsyncMock()
    e._consume_attended_transfer_screening_audio = lambda *a: False
    e._encode_for_provider = lambda call_id, name, p, pcm, hz: (pcm, 'slin16', hz)
    e.streaming_playback_manager.is_stream_active = lambda _: True
    e.vad_manager = None
    if enhanced:
        e.vad_manager = SimpleNamespace(
            confidence_threshold=0.6,
            process_frame=AsyncMock(return_value=VADResult(True, 0.9, 6000, True)),
        )
    s.provider_session_active = True
    s.audio_capture_enabled = True
    s.media_rx_confirmed = True
    s.conversation_state = 'active'
    s.tts_started_ts = time.time() - 10
    s.vad_state['format_probe_done'] = True
    s.audio_diagnostics['inbound_first_frame'] = True
    s.transport_profile = SimpleNamespace(format='slin16', wire_sample_rate=rate, sample_rate=rate)
    # Tests inject frames into an already-speaking stream, rather than sleeping
    # through SessionStore's fresh-token protection timestamp.
    add_token = e.session_store.set_gating_token
    async def existing_stream_token(*args, **kwargs):
        started = s.tts_started_ts
        await add_token(*args, **kwargs)
        s.tts_started_ts = started
    e.session_store.set_gating_token = existing_stream_token
    return e, s, p


def caller_frame(amplitude=6000, rate=16000, *, ulaw=False):
    import audioop
    from src.audio.audiosocket_protocol import AudioSocketAudioFrame
    pcm = struct.pack('<hh', amplitude, -amplitude) * (rate // 100)
    if ulaw:
        return AudioSocketAudioFrame(audioop.lin2ulaw(pcm, 2), 0x10, 'ulaw', 8000)
    return AudioSocketAudioFrame(pcm, 0x12 if rate == 16000 else 0x10,
                                'slin16' if rate == 16000 else 'slin', rate)


@pytest.mark.asyncio
@pytest.mark.parametrize('vertex', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('enhanced', [False, True])
@pytest.mark.parametrize('rate', [8000, 16000])
async def test_google_gated_audiosocket_detects_speech_without_changing_upstream(
    vertex, enabled, enhanced, rate,
):
    e, s, p = ingress_engine(vertex=vertex, enabled=enabled, enhanced=enhanced, rate=rate)
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'speaking')
    e._apply_barge_in_action = AsyncMock()
    frame = caller_frame(rate=rate)
    trigger_frames = 6 if enabled and not vertex else 13
    for _ in range(trigger_frames - 1):
        await e._audiosocket_handle_audio('ingress', frame)
    e._apply_barge_in_action.assert_not_awaited()
    await e._audiosocket_handle_audio('ingress', frame)
    e._apply_barge_in_action.assert_awaited_once_with(
        s.call_id, source='local_vad_fallback', reason='google-test:audiosocket',
    )
    assert p.send_audio.await_count == trigger_frames
    assert all(not any(c.args[0]) for c in p.send_audio.await_args_list)
    assert not s.audio_capture_enabled
    assert e.config.vad.vad_mode == 'auto'
    assert e.config.vad.use_provider_vad is (not enhanced)


@pytest.mark.asyncio
async def test_google_companded_ingress_detects_normalized_speech():
    e, s, p = ingress_engine(rate=8000)
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'speaking')
    e._apply_barge_in_action = AsyncMock()
    for _ in range(6):
        await e._audiosocket_handle_audio('ingress', caller_frame(rate=8000, ulaw=True))
    e._apply_barge_in_action.assert_awaited_once()
    assert all(not any(c.args[0]) for c in p.send_audio.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize('amplitude', [0, 500])
async def test_google_gated_silence_and_subthreshold_noise_do_not_interrupt(amplitude):
    e, s, p = ingress_engine()
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'speaking')
    e._apply_barge_in_action = AsyncMock()
    for _ in range(40):
        await e._audiosocket_handle_audio('ingress', caller_frame(amplitude))
    e._apply_barge_in_action.assert_not_awaited()
    assert s.barge_in_candidate_ms == 0
    assert p.send_audio.await_count == 40


@pytest.mark.asyncio
@pytest.mark.parametrize('protection', ['greeting', 'initial', 'cooldown', 'terminal', 'disabled', 'allowlist', 'bridge_moh'])
async def test_google_gated_detection_retains_existing_protections(protection, monkeypatch):
    import time
    e, s, p = ingress_engine()
    if protection == 'greeting':
        s.conversation_state = 'greeting'
        s.tts_started_ts = time.time() - 1
    elif protection == 'initial':
        s.tts_started_ts = time.time()
    elif protection == 'cooldown':
        s.last_barge_in_ts = time.time()
    elif protection == 'terminal':
        monkeypatch.setattr(type(p), 'terminal_output_protected', property(lambda _: True))
    elif protection == 'disabled':
        e.config.barge_in.provider_fallback_enabled = False
    elif protection == 'allowlist':
        e.config.barge_in.provider_fallback_providers = ['grok']
    elif protection == 'bridge_moh':
        s.music_snoop_channel_id = 'bridge-moh:test'
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'speaking')
    e._apply_barge_in_action = AsyncMock()
    for _ in range(13):
        await e._audiosocket_handle_audio('ingress', caller_frame())
    e._apply_barge_in_action.assert_not_awaited()
    assert all(not any(c.args[0]) for c in p.send_audio.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['openai_realtime', 'grok', 'deepgram', 'elevenlabs_agent', 'local'])
@pytest.mark.parametrize('squelch', [False, True])
async def test_other_continuous_provider_audio_and_fallback_input_are_unchanged(name, squelch):
    e, s, _ = ingress_engine()
    p = SimpleNamespace(
        send_audio=AsyncMock(),
        get_capabilities=lambda: SimpleNamespace(requires_continuous_audio=True, has_native_vad=True),
    )
    e.provider_kinds[name] = name
    s.provider_name = name
    e._call_providers[s.call_id] = p
    e.config.vad.upstream_squelch_enabled = squelch
    if squelch:
        s.vad_state['upstream_squelch'] = {'noise_ema': 100}
    e._maybe_provider_barge_in_fallback = AsyncMock()
    await e.session_store.upsert_call(s)
    if not squelch:
        await e.session_store.set_gating_token(s.call_id, 'speaking')
    # Two frames distinguish unchanged gated real audio from unchanged
    # ungated upstream squelch behavior for native providers.
    for _ in range(2):
        await e._audiosocket_handle_audio('ingress', caller_frame())
    assert p.send_audio.await_count == 2
    assert e._maybe_provider_barge_in_fallback.await_count == 2
    for forwarded, fallback in zip(p.send_audio.await_args_list, e._maybe_provider_barge_in_fallback.await_args_list):
        assert forwarded.args[0] == fallback.kwargs['pcm16']
    assert any(p.send_audio.await_args.args[0])
    if squelch:
        assert not any(p.send_audio.await_args_list[0].args[0])


@pytest.mark.asyncio
@pytest.mark.parametrize('duplex', [False, True])
async def test_google_3_8_preserves_native_full_duplex_interruption(duplex):
    e, s, p = ingress_engine()
    p.config.llm_model = 'gemini-3.8-live'
    p.config.full_duplex_barge_in_3_8 = duplex
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'speaking')
    e._maybe_provider_barge_in_fallback = AsyncMock()
    await e._audiosocket_handle_audio('ingress', caller_frame())
    assert bool(any(p.send_audio.await_args.args[0])) is duplex
    if duplex:
        e._maybe_provider_barge_in_fallback.assert_not_awaited()
    else:
        assert any(e._maybe_provider_barge_in_fallback.await_args.kwargs['pcm16'])


@pytest.mark.asyncio
async def test_pipeline_talk_detect_retains_ownership_of_gated_ingress():
    e, s, p = ingress_engine()
    e._pipeline_forced[s.call_id] = True
    s.vad_state['pipeline_talk_detect'] = {'enabled': True}
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'speaking')
    e._maybe_provider_barge_in_fallback = AsyncMock()
    await e._audiosocket_handle_audio('ingress', caller_frame())
    p.send_audio.assert_not_awaited()
    e._maybe_provider_barge_in_fallback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('squelch', [False, True])
async def test_detected_google_interruption_flushes_backlog_and_accepts_followup(squelch):
    e, s, p = ingress_engine()
    e.config.vad.upstream_squelch_enabled = squelch
    await e.session_store.upsert_call(s)
    if squelch:
        # Real calls establish the upstream noise floor while listening before
        # agent output. Follow-up speech retains its existing onset guard.
        await e._audiosocket_handle_audio('ingress', caller_frame(20))
        p.send_audio.reset_mock()
    await e.session_store.set_gating_token(s.call_id, 'speaking')
    await send_audio(e, b'ab' * 1000000)
    q = e._provider_stream_queues[s.call_id]
    assert q.pending_bytes == 2000000
    e._wait_for_call_audio_drain = AsyncMock(return_value=True)
    e.ari_client = SimpleNamespace(stop_playback=AsyncMock())
    async def stop(call_id):
        assert q.closed and q.pending_bytes == 0
    e.streaming_playback_manager.stop_streaming_playback.side_effect = stop
    for _ in range(6):
        await e._audiosocket_handle_audio('ingress', caller_frame())
    task = e._provider_output_drain_tasks.get(s.call_id)
    if task:
        await task
    assert s.barge_in_count == 1
    assert q.closed and q.pending_bytes == 0
    assert s.call_id not in e._provider_stream_queues
    assert s.audio_capture_enabled and not s.tts_tokens
    assert all(not any(c.args[0]) for c in p.send_audio.await_args_list)
    for _ in range(2):
        await e._audiosocket_handle_audio('ingress', caller_frame())
    assert any(p.send_audio.await_args.args[0])
    assert not p._long_audio_failed


@pytest.mark.asyncio
async def test_google_gated_detector_uses_dc_conditioned_pcm_not_raw_wire_energy():
    from src.audio.audiosocket_protocol import AudioSocketAudioFrame
    e, s, p = ingress_engine()
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'speaking')
    e._apply_barge_in_action = AsyncMock()
    # A large constant wire offset is removed before local detection; using
    # raw wire energy in provider-VAD mode would incorrectly interrupt here.
    frame = AudioSocketAudioFrame(struct.pack('<h', 6000) * 320, 0x12, 'slin16', 16000)
    for _ in range(20):
        await e._audiosocket_handle_audio('ingress', frame)
    e._apply_barge_in_action.assert_not_awaited()
    assert all(not any(c.args[0]) for c in p.send_audio.await_args_list)


@pytest.mark.asyncio
async def test_pending_transfer_suspends_google_ingress_and_interruption_detection():
    e, s, p = ingress_engine()
    await e.session_store.upsert_call(s)
    await e.session_store.set_gating_token(s.call_id, 'speaking')
    e._session_has_pending_attended_transfer = lambda _: True
    e._maybe_provider_barge_in_fallback = AsyncMock()
    await e._audiosocket_handle_audio('ingress', caller_frame())
    p.send_audio.assert_not_awaited()
    e._maybe_provider_barge_in_fallback.assert_not_awaited()
