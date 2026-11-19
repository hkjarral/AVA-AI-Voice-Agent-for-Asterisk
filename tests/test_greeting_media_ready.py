"""Deterministic caller-output fixtures; no PBX, provider, or destination call."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError

from src.config import AppConfig, StreamingConfig
from src.config_apply import classify_config_change
from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.core.streaming_playback_manager import StreamingPlaybackManager


@pytest.mark.parametrize('field,maximum', [
    ('greeting_media_ready_delay_ms', 2000), ('greeting_media_ready_timeout_ms', 5000),
])
def test_schema_bounds_and_round_trip(field, maximum):
    for value in (0, maximum):
        model = StreamingConfig(**{field: value})
        assert StreamingConfig.model_validate_json(model.model_dump_json()) == model
    for value in (-1, maximum + 1, 1.5, 1.0, True, False, None, '100', 'bad', float('inf')):
        with pytest.raises(ValidationError):
            StreamingConfig(**{field: value})


def test_defaults_and_restart_classification():
    config = StreamingConfig()
    assert config.greeting_media_ready_delay_ms == 0
    assert config.greeting_media_ready_timeout_ms == 1000
    decision = classify_config_change({'streaming': {}}, {'streaming': {'greeting_media_ready_delay_ms': 40}})
    assert decision.restart_required
    assert decision.recommended_apply_method == 'restart'


async def make_stream(monkeypatch, *, transport='audiosocket', kind='greeting',
                      delay=40, timeout=1000, fmt='ulaw', rate=8000):
    store = SessionStore()
    session = CallSession(call_id='call', caller_channel_id='call', provider_name='local',
                          audiosocket_conn_id='conn', external_media_id='rtp-leg')
    session.external_media_codec = fmt
    await store.upsert_call(session)
    manager = StreamingPlaybackManager(
        store, Mock(), streaming_config={
            'greeting_media_ready_delay_ms': delay, 'greeting_media_ready_timeout_ms': timeout,
            'provider_grace_ms': 0, 'attack_ms': 0, 'normalizer': {'enabled': False},
        }, audio_transport=transport,
        websocket_server=SimpleNamespace(get_binding=lambda _call: SimpleNamespace(codec=fmt, sample_rate=rate)),
    )
    manager.audiosocket_format = fmt
    clock = SimpleNamespace(now=10.0)
    monkeypatch.setattr(manager, '_greeting_now', lambda: clock.now)
    manager._send_audio_chunk = AsyncMock(return_value=True)
    # Exercise real stream registration but drive the pacer explicitly.
    def suspended(coro):
        coro.close()
        return Mock(done=lambda: False)
    with patch('asyncio.create_task', side_effect=suspended):
        sid = await manager.start_streaming_playback(
            'call', asyncio.Queue(maxsize=4), playback_type=kind,
            source_encoding=fmt, source_sample_rate=rate,
        )
    assert sid
    info = manager.active_streams['call']
    info.update(target_format=fmt, target_sample_rate=rate, sentinel_seen=True, startup_ready=True)
    manager._startup_ready['call'] = True
    frame = b'\x21' * manager._frame_size_bytes('call')
    manager.frame_remainders['call'] = frame
    info['buffered_bytes'] = len(frame)
    async def tick():
        return await manager._drain_next_frame('call', sid, manager.jitter_buffers['call'])
    def ready(identity=None):
        manager.note_greeting_media_rx(session, transport, identity or ('conn' if transport == 'audiosocket' else 'rtp-leg'))
    return SimpleNamespace(manager=manager, session=session, info=info, sid=sid, frame=frame,
                           clock=clock, tick=tick, ready=ready)


@pytest.mark.parametrize('transport', ['audiosocket', 'externalmedia'])
@pytest.mark.parametrize('kind', ['greeting', 'pipeline-tts-greeting'])
@pytest.mark.parametrize('fmt,rate,silence', [('ulaw', 8000, b'\xff'), ('alaw', 8000, b'\xd5'), ('slin', 8000, b'\x00'), ('slin16', 16000, b'\x00')])
@pytest.mark.asyncio
async def test_ready_silence_then_exact_greeting_only_once(monkeypatch, transport, kind, fmt, rate, silence):
    rig = await make_stream(monkeypatch, transport=transport, kind=kind, fmt=fmt, rate=rate)
    rig.ready()  # Silent inbound audio counts; no speech threshold involved.
    assert await rig.tick() == 'sent'
    assert rig.manager.frame_remainders['call'] == rig.frame
    rig.clock.now += .021
    assert await rig.tick() == 'sent'
    rig.clock.now += .021
    assert await rig.tick() == 'sent'
    sent = rig.manager._send_audio_chunk.await_args_list
    assert [c.args[2] for c in sent] == [silence * len(rig.frame)] * 2 + [rig.frame]
    assert rig.info['buffered_bytes'] == 0
    assert rig.info['real_tx_bytes'] == len(rig.frame)
    assert rig.info['underflow_events'] == 0
    assert rig.session.greeting_media.phase == 'done'
    # A second chunk, and a new greeting-labelled stream, cannot re-arm it.
    rig.manager.frame_remainders['call'] = rig.frame
    assert await rig.tick() == 'sent'
    replacement = {'playback_type': kind}
    rig.manager._attach_greeting_media_guard(rig.session, replacement)
    assert 'greeting_media_guard' not in replacement


@pytest.mark.asyncio
async def test_deadline_starts_at_prepared_audio_and_never_restarts(monkeypatch):
    rig = await make_stream(monkeypatch, timeout=100)
    rig.manager.frame_remainders['call'] = b''
    rig.info['sentinel_seen'] = False
    assert await rig.tick() == 'wait'
    assert rig.session.greeting_media.deadline is None
    rig.clock.now = 20
    rig.manager.frame_remainders['call'] = rig.frame
    assert await rig.tick() == 'wait'
    deadline = rig.session.greeting_media.deadline
    rig.clock.now += .05
    rig.ready('stale-connection')
    rig.session.media_rx_confirmed = True  # ARI/talk detection is not evidence.
    assert await rig.tick() == 'wait'
    assert rig.session.greeting_media.deadline == deadline
    rig.clock.now += .06
    rig.ready()  # Arrived after the deadline: cannot win the race at next tick.
    assert await rig.tick() == 'sent'
    assert rig.manager._send_audio_chunk.await_args.args[2] == rig.frame
    assert rig.session.greeting_media.phase == 'done'


@pytest.mark.asyncio
async def test_delayed_readiness_before_deadline(monkeypatch):
    rig = await make_stream(monkeypatch, delay=1, timeout=100)
    assert await rig.tick() == 'wait'
    rig.clock.now += .05
    rig.ready()
    assert await rig.tick() == 'sent'
    # Exactly 1ms of silence shares the first normal-size packet with speech;
    # RTP must not receive a short packet with its usual full-packet timestamp.
    assert rig.manager._send_audio_chunk.await_args.args[2] == b'\xff' * 8 + rig.frame[:-8]
    assert rig.manager.frame_remainders['call'] == rig.frame[-8:]
    assert rig.info['real_tx_bytes'] == len(rig.frame) - 8
    rig.info['sentinel_seen'] = True
    rig.clock.now += .02
    assert await rig.tick() == 'sent'
    assert rig.manager._send_audio_chunk.await_args.args[2].startswith(rig.frame[-8:])


@pytest.mark.parametrize('transport', ['audiosocket', 'externalmedia'])
@pytest.mark.parametrize('fmt,rate,silence', [('ulaw', 8000, b'\xff'), ('alaw', 8000, b'\xd5'), ('slin', 8000, b'\x00'), ('slin16', 16000, b'\x00')])
@pytest.mark.asyncio
async def test_non_packet_aligned_delay_keeps_wire_frames_and_speech_order(monkeypatch, transport, fmt, rate, silence):
    rig = await make_stream(monkeypatch, transport=transport, delay=31, fmt=fmt, rate=rate)
    rig.ready()
    for _ in range(3):
        assert await rig.tick() == 'sent'
        rig.clock.now += .021
    calls = rig.manager._send_audio_chunk.await_args_list
    assert all(len(c.args[2]) == len(rig.frame) for c in calls)
    actual = b''.join(c.args[2] for c in calls)
    bps = rig.manager._bytes_per_sample(fmt)
    expected = silence * (rate * 31 // 1000 * bps) + rig.frame
    assert actual[:len(expected)] == expected
    assert rig.info['real_tx_bytes'] >= len(rig.frame)


@pytest.mark.asyncio
async def test_silence_failure_fallback_uses_original_unconverted_source(monkeypatch):
    import audioop
    rig = await make_stream(monkeypatch, fmt='ulaw', rate=8000)
    rig.ready()
    pcm = b'\x00\x10' * 160
    rig.info.update(source_encoding='slin', source_sample_rate=8000, sentinel_seen=False)
    rig.manager.frame_remainders['call'] = b''
    rig.manager.jitter_buffers['call'].put_nowait(pcm)
    rig.manager._process_audio_chunk = AsyncMock(return_value=audioop.lin2ulaw(pcm, 2))
    rig.manager._send_audio_chunk.return_value = False
    assert await rig.tick() == 'error'
    playback = AsyncMock(return_value='file')
    rig.manager.fallback_playback_manager = SimpleNamespace(play_audio=playback)
    await rig.manager._fallback_to_file_playback('call', rig.sid)
    assert playback.await_args.args[1] == audioop.lin2ulaw(pcm, 2)


@pytest.mark.parametrize('transport,fmt,rate', [('audiosocket', 'slin', 8000), ('externalmedia', 'ulaw', 8000), ('externalmedia', 'slin16', 16000)])
@pytest.mark.asyncio
async def test_guard_uses_existing_wire_delegate(monkeypatch, transport, fmt, rate):
    rig = await make_stream(monkeypatch, transport=transport, fmt=fmt, rate=rate, delay=1)
    server = SimpleNamespace(send_audio=AsyncMock(return_value=True))
    rig.manager.audiosocket_server = server
    rig.manager.rtp_server = server
    rig.manager._send_audio_chunk = StreamingPlaybackManager._send_audio_chunk.__get__(rig.manager)
    rig.ready()
    assert await rig.tick() == 'sent'
    silence_bytes = rate // 1000 * rig.manager._bytes_per_sample(fmt)
    silence_byte = b'\xff' if fmt == 'ulaw' else b'\x00'
    args = server.send_audio.await_args
    assert args.args[0] == ('conn' if transport == 'audiosocket' else 'call')
    assert args.args[1] == silence_byte * silence_bytes + rig.frame[:-silence_bytes]


@pytest.mark.asyncio
async def test_guard_rechecks_stream_after_final_output_session_lookup(monkeypatch):
    rig = await make_stream(monkeypatch)
    server = SimpleNamespace(send_audio=AsyncMock(return_value=True))
    rig.manager.audiosocket_server = server
    lookup = rig.manager.session_store.get_by_call_id
    lookups = 0

    async def replace_stream_during_lookup(call_id):
        nonlocal lookups
        session = await lookup(call_id)
        lookups += 1
        if lookups == 2:
            rig.manager.active_streams['call'] = dict(rig.info, stream_id='replacement')
        return session

    monkeypatch.setattr(rig.manager.session_store, 'get_by_call_id', replace_stream_during_lookup)
    result = await StreamingPlaybackManager._send_audio_chunk(
        rig.manager, 'call', rig.sid, rig.frame, target_fmt='ulaw', target_rate=8000,
    )
    assert result is False
    server.send_audio.assert_not_awaited()


@pytest.mark.parametrize('ready', [False, True])
@pytest.mark.asyncio
async def test_timeout_zero_checks_once(monkeypatch, ready):
    rig = await make_stream(monkeypatch, timeout=0)
    if ready:
        rig.ready()
    assert await rig.tick() == 'sent'
    expected = b'\xff' * len(rig.frame) if ready else rig.frame
    assert rig.manager._send_audio_chunk.await_args.args[2] == expected


@pytest.mark.parametrize('transport,kind,delay', [
    ('audiosocket', 'greeting', 0), ('externalmedia', 'pipeline-tts-greeting', 0),
    ('websocket', 'greeting', 200), ('audiosocket', 'pipeline-tts', 200),
])
@pytest.mark.asyncio
async def test_disabled_or_unaffected_modes_have_no_guard(monkeypatch, transport, kind, delay):
    rig = await make_stream(monkeypatch, transport=transport, kind=kind, delay=delay)
    assert 'greeting_media_guard' not in rig.info
    rig.manager._greeting_now = Mock(side_effect=AssertionError('disabled guard must not check time'))
    assert await rig.tick() == 'sent'
    assert rig.manager._send_audio_chunk.await_args.args[2] == rig.frame


@pytest.mark.parametrize('during_silence', [False, True])
@pytest.mark.parametrize('change', ['hangup', 'transfer', 'connection', 'session', 'stream'])
@pytest.mark.asyncio
async def test_cancellation_never_emits_or_falls_back(monkeypatch, during_silence, change):
    rig = await make_stream(monkeypatch)
    if during_silence:
        rig.ready()
    await rig.tick()
    rig.manager._send_audio_chunk.reset_mock()
    if change == 'hangup':
        rig.session.cleanup_in_progress = True
    elif change == 'transfer':
        rig.session.current_action = {'type': 'transfer'}
    elif change == 'connection':
        rig.session.audiosocket_conn_id = 'replacement'
    elif change == 'session':
        await rig.manager.session_store.upsert_call(CallSession(call_id='call', caller_channel_id='call'))
    else:
        rig.manager.active_streams['call'] = dict(rig.info, stream_id='replacement')
    rig.clock.now += .03
    assert await rig.tick() == 'greeting-cancelled'
    rig.manager._send_audio_chunk.assert_not_awaited()
    assert not rig.session.greeting_media.fallback_started


@pytest.mark.asyncio
async def test_ownership_loss_during_send_is_not_a_fallback(monkeypatch):
    rig = await make_stream(monkeypatch)
    rig.ready()
    async def lose_owner(*_args, **_kwargs):
        rig.session.audiosocket_conn_id = 'replacement'
        return False
    rig.manager._send_audio_chunk.side_effect = lose_owner
    assert await rig.tick() == 'greeting-cancelled'
    assert rig.manager.frame_remainders['call'] == rig.frame


@pytest.mark.asyncio
async def test_failed_silence_is_not_replayed_and_fallback_is_once(monkeypatch):
    rig = await make_stream(monkeypatch)
    rig.ready()
    rig.manager._send_audio_chunk.return_value = False
    assert await rig.tick() == 'error'
    assert rig.manager.frame_remainders['call'] == rig.frame
    rig.manager.fallback_playback_manager = SimpleNamespace(play_audio=AsyncMock(return_value='file'))
    await rig.manager._fallback_to_file_playback('call', rig.sid)
    await rig.manager._fallback_to_file_playback('call', rig.sid)
    rig.manager.fallback_playback_manager.play_audio.assert_awaited_once()


@pytest.mark.parametrize('change', ['hangup', 'transfer', 'connection', 'session', 'stream'])
@pytest.mark.asyncio
async def test_fallback_rechecks_ownership_after_session_lookup(monkeypatch, change):
    rig = await make_stream(monkeypatch)
    playback = AsyncMock(return_value='file')
    rig.manager.fallback_playback_manager = SimpleNamespace(play_audio=playback)
    lookup = rig.manager.session_store.get_by_call_id
    lookups = 0

    async def ownership_changes_during_lookup(call_id):
        nonlocal lookups
        session = await lookup(call_id)
        lookups += 1
        if lookups == 2:
            if change == 'hangup':
                session.cleanup_in_progress = True
            elif change == 'transfer':
                session.current_action = {'type': 'transfer'}
            elif change == 'connection':
                session.audiosocket_conn_id = 'replacement'
            elif change == 'session':
                session = CallSession(call_id='call', caller_channel_id='call')
                await rig.manager.session_store.upsert_call(session)
            else:
                rig.manager.active_streams['call'] = dict(rig.info, stream_id='replacement')
        return session

    monkeypatch.setattr(rig.manager.session_store, 'get_by_call_id', ownership_changes_during_lookup)
    await rig.manager._fallback_to_file_playback('call', rig.sid)
    playback.assert_not_awaited()
    assert rig.manager.frame_remainders['call'] == rig.frame


def test_engine_passes_both_settings_without_activating_old_control(monkeypatch):
    import src.engine as module
    monkeypatch.setattr(module, 'get_outbound_store', lambda: Mock())
    config = AppConfig(default_provider='local', providers={},
                       asterisk={'host': '127.0.0.1', 'username': 'test', 'password': 'test'},
                       llm={}, audio_transport='audiosocket',
                       streaming={'greeting_media_ready_delay_ms': 55, 'greeting_media_ready_timeout_ms': 99,
                                  'greeting_min_start_ms': 180})
    engine = module.Engine(config)
    manager = engine.streaming_playback_manager
    assert manager.greeting_media_ready_delay_ms == 55
    assert manager.greeting_media_ready_timeout_ms == 99
    assert manager.greeting_min_start_ms == 0  # #754 / runtime repairs remain separate.


@pytest.mark.asyncio
async def test_explicit_file_playback_does_not_enter_guard(monkeypatch):
    import src.engine as module
    monkeypatch.setattr(module, 'get_outbound_store', lambda: Mock())
    config = AppConfig(default_provider='local', providers={'local': {'enabled': True}},
                       asterisk={'host': '127.0.0.1', 'username': 'test', 'password': 'test'},
                       llm={}, audio_transport='audiosocket', downstream_mode='file',
                       streaming={'greeting_media_ready_delay_ms': 2000})
    engine = module.Engine(config)
    engine.playback_manager.play_audio = AsyncMock(return_value='file-playback')
    engine.streaming_playback_manager.start_streaming_playback = AsyncMock()
    engine._stop_connection_audio = AsyncMock()
    engine._note_provider_output_start = AsyncMock()
    session = CallSession(call_id='file', caller_channel_id='file', provider_name='local')
    await engine.session_store.upsert_call(session)
    await engine.on_provider_event({'type': 'AgentAudio', 'call_id': 'file', 'data': b'\x21' * 160,
                                   'encoding': 'ulaw', 'sample_rate': 8000})
    engine.playback_manager.play_audio.assert_awaited_once()
    engine.streaming_playback_manager.start_streaming_playback.assert_not_awaited()
    assert not session.greeting_media.claimed


@pytest.mark.parametrize('transport', ['audiosocket', 'externalmedia'])
@pytest.mark.parametrize('valid', [True, False])
@pytest.mark.asyncio
async def test_engine_readiness_requires_current_decoded_inbound_frame(monkeypatch, transport, valid):
    from src.audio.audiosocket_protocol import AudioSocketAudioFrame
    from src.engine import Engine
    rig = await make_stream(monkeypatch, transport=transport)
    engine = Engine.__new__(Engine)
    engine.streaming_playback_manager = rig.manager
    engine.session_store = rig.manager.session_store
    engine.config = SimpleNamespace(audio_transport=transport, barge_in=SimpleNamespace(enabled=False))
    engine._save_session = AsyncMock()
    engine._update_transport_profile = AsyncMock()
    engine._observe_no_input_audio = AsyncMock()
    engine._consume_attended_transfer_screening_audio = lambda *_a: True  # stop after transport intake
    engine._pipeline_forced = {}
    engine.conn_to_channel = {'conn': 'call', 'stale': 'call'}
    engine.audio_capture = Mock()
    if transport == 'audiosocket':
        # Valid PCM silence, not a speech event or an ARI answer/bridge event.
        await engine._audiosocket_handle_audio(
            'conn' if valid else 'stale', AudioSocketAudioFrame(b'\x00' * 320, 0x10, 'slin', 8000),
        )
    else:
        engine.rtp_server = SimpleNamespace(
            get_session_info=lambda _call: {'ssrc': 123}, has_remote_endpoint=lambda _call: True,
        )
        await engine._on_transport_pcm('call', b'\x00' * 640, 16000, source='externalmedia', ssrc=123 if valid else 999)
    assert (rig.session.greeting_media.inbound_token is not None) is valid


@pytest.mark.asyncio
async def test_readiness_is_call_scoped_and_first_timestamp_is_stable(monkeypatch):
    rig = await make_stream(monkeypatch)
    other = CallSession(call_id='other', caller_channel_id='other', audiosocket_conn_id='other-conn')
    await rig.manager.session_store.upsert_call(other)
    rig.manager.note_greeting_media_rx(other, 'audiosocket', 'other-conn')
    assert await rig.tick() == 'wait'
    rig.ready()
    first = rig.session.greeting_media.inbound_at
    rig.clock.now += .5
    rig.ready()
    assert rig.session.greeting_media.inbound_at == first
    assert other.greeting_media is not rig.session.greeting_media


@pytest.mark.asyncio
async def test_short_closed_greeting_survives_readiness_wait_longer_than_old_drain_budget():
    store = SessionStore()
    session = CallSession(call_id='short', caller_channel_id='short', audiosocket_conn_id='conn')
    await store.upsert_call(session)
    manager = StreamingPlaybackManager(store, Mock(), audio_transport='audiosocket', streaming_config={
        'greeting_media_ready_delay_ms': 20, 'greeting_media_ready_timeout_ms': 700,
        'provider_grace_ms': 0, 'normalizer': {'enabled': False}, 'attack_ms': 0,
    })
    manager._send_audio_chunk = AsyncMock(return_value=True)
    q = asyncio.Queue(maxsize=4)
    q.put_nowait(b'\x21' * 160)
    q.put_nowait(None)
    started = asyncio.get_running_loop().time()
    sid = await manager.start_streaming_playback('short', q, playback_type='greeting', source_encoding='ulaw', source_sample_rate=8000)
    assert sid
    producer = manager.active_streams['short']['streaming_task']
    await asyncio.wait_for(producer, timeout=2)
    assert asyncio.get_running_loop().time() - started >= .7
    manager._send_audio_chunk.assert_awaited_once()
    assert manager._send_audio_chunk.await_args.args[2] == b'\x21' * 160
    assert 'short' not in manager.active_streams


@pytest.mark.parametrize('kind', ['greeting', 'pipeline-tts-greeting'])
@pytest.mark.asyncio
async def test_guard_keeps_consuming_realtime_greeting_without_queue_drops(kind):
    store = SessionStore()
    session = CallSession(call_id='buffer', caller_channel_id='buffer', audiosocket_conn_id='conn')
    await store.upsert_call(session)
    manager = StreamingPlaybackManager(store, Mock(), audio_transport='audiosocket', streaming_config={
        'greeting_media_ready_delay_ms': 40, 'greeting_media_ready_timeout_ms': 400,
        'jitter_buffer_ms': 40, 'provider_grace_ms': 0,
        'normalizer': {'enabled': False}, 'attack_ms': 0,
    })
    manager._process_audio_chunk = AsyncMock(side_effect=lambda _call, chunk: chunk)
    manager._send_audio_chunk = AsyncMock(return_value=True)
    q = asyncio.Queue(maxsize=2)
    sid = await manager.start_streaming_playback('buffer', q, playback_type=kind,
                                               source_encoding='ulaw', source_sample_rate=8000)
    info = manager.active_streams['buffer']
    chunks = [bytes([i + 1]) * 160 for i in range(14)]
    try:
        for chunk in chunks:
            if kind == 'greeting':
                q.put_nowait(chunk)  # The provider path drops on QueueFull.
            else:
                await asyncio.wait_for(q.put(chunk), timeout=.06)
            await asyncio.sleep(.02)
        manager.note_greeting_media_rx(session, 'audiosocket', 'conn')
        await asyncio.wait_for(q.put(None), timeout=.1)
        await asyncio.wait_for(info['streaming_task'], timeout=2)
        actual = b''.join(c.args[2] for c in manager._send_audio_chunk.await_args_list)
        assert actual == b'\xff' * 320 + b''.join(chunks)
    finally:
        if 'buffer' in manager.active_streams:
            await manager.stop_streaming_playback('buffer')


@pytest.mark.parametrize('source_frames', [1, 4])
@pytest.mark.asyncio
async def test_guard_prefetch_is_bounded_and_preserves_end_sentinel(monkeypatch, source_frames):
    from src.core.streaming_playback_manager import _JITTER_SENTINEL
    rig = await make_stream(monkeypatch, delay=40, timeout=100)
    rig.info['sentinel_seen'] = False
    assert await rig.tick() == 'wait'
    queue = rig.manager.jitter_buffers['call']
    for _ in range(20):
        if not queue.full():
            queue.put_nowait(rig.frame * source_frames)
        assert await rig.tick() == 'wait'
    # At most the configured 140ms window plus the first prepared 20ms
    # frame is retained; a frozen clock must not permit unbounded growth.
    assert 8 * len(rig.frame) <= len(rig.manager.frame_remainders['call']) < (8 + source_frames) * len(rig.frame)
    assert not queue.empty()
    rig.clock.now += .11
    while not queue.empty() or rig.manager.frame_remainders['call']:
        assert await rig.tick() == 'sent'
    queue.put_nowait(_JITTER_SENTINEL)
    assert await rig.tick() == 'finished'
    assert rig.info['sentinel_seen']


@pytest.mark.asyncio
async def test_guard_prefetch_does_not_cross_transport_boundary(monkeypatch):
    from src.core.streaming_playback_manager import _TransportBoundary
    rig = await make_stream(monkeypatch)
    rig.info['sentinel_seen'] = False
    assert await rig.tick() == 'wait'
    boundary = _TransportBoundary()
    queue = rig.manager.jitter_buffers['call']
    queue.put_nowait(boundary)
    queue.put_nowait(b'\x42' * len(rig.frame))
    assert await rig.tick() == 'wait'
    assert rig.info['transport_pending_boundary'] is boundary
    assert queue.qsize() == 1
    assert not boundary.completed.done()
    rig.manager.finish_transport_segment = AsyncMock(return_value=True)
    rig.clock.now += 1.01
    assert await rig.tick() == 'sent'
    assert rig.manager._send_audio_chunk.await_args.args[2] == rig.frame
    assert await rig.tick() == 'wait'
    assert boundary.completed.result() is True
    assert queue.qsize() == 1
    assert await rig.tick() == 'sent'
    assert rig.manager._send_audio_chunk.await_args.args[2] == b'\x42' * len(rig.frame)


@pytest.mark.asyncio
async def test_pacer_cancels_pending_greeting_and_cleans_full_queue_on_transfer():
    store = SessionStore()
    session = CallSession(call_id='stop', caller_channel_id='stop', audiosocket_conn_id='conn')
    await store.upsert_call(session)
    manager = StreamingPlaybackManager(store, Mock(), audio_transport='audiosocket', streaming_config={
        'greeting_media_ready_delay_ms': 2000, 'greeting_media_ready_timeout_ms': 5000,
        'jitter_buffer_ms': 40, 'provider_grace_ms': 0,
    })
    manager._send_audio_chunk = AsyncMock(return_value=True)
    q = asyncio.Queue(maxsize=8)
    for _ in range(8):
        q.put_nowait(b'\x21' * 160)
    sid = await manager.start_streaming_playback('stop', q, playback_type='greeting', source_encoding='ulaw', source_sample_rate=8000)
    assert sid
    info = manager.active_streams['stop']
    await asyncio.sleep(.04)
    session.current_action = {'type': 'transfer'}
    await asyncio.wait_for(info['pacer_task'], timeout=1)
    assert info['streaming_task'].done()
    assert info['keepalive_task'].done()
    assert session.greeting_media.phase == 'cancelled'
    assert 'stop' not in manager.active_streams
    manager._send_audio_chunk.assert_not_awaited()


@pytest.mark.parametrize('transport', ['audiosocket', 'externalmedia'])
@pytest.mark.parametrize('kind', ['greeting', 'pipeline-tts-greeting'])
@pytest.mark.parametrize('close_queue', [True, False])
@pytest.mark.asyncio
async def test_failed_preroll_collects_full_greeting_and_releases_stream_gating(transport, kind, close_queue):
    """A failed first silence send must not strand the producer behind its pacer."""
    store = SessionStore()
    session = CallSession(call_id='fallback', caller_channel_id='fallback', audiosocket_conn_id='conn', external_media_id='rtp-leg')
    await store.upsert_call(session)
    playback = AsyncMock(return_value='fallback-file')
    manager = StreamingPlaybackManager(store, Mock(), audio_transport=transport, streaming_config={
        'greeting_media_ready_delay_ms': 40, 'greeting_media_ready_timeout_ms': 100,
        'fallback_timeout_ms': 50,
        'jitter_buffer_ms': 40, 'provider_grace_ms': 0, 'normalizer': {'enabled': False}, 'attack_ms': 0,
    }, fallback_playback_manager=SimpleNamespace(play_audio=playback))
    manager._send_audio_chunk = AsyncMock(return_value=False)
    manager.note_greeting_media_rx(session, transport, 'conn' if transport == 'audiosocket' else 'rtp-leg')
    queue = asyncio.Queue(maxsize=2)
    sid = await manager.start_streaming_playback('fallback', queue, playback_type=kind,
                                                 source_encoding='ulaw', source_sample_rate=8000)
    info = manager.active_streams['fallback']
    chunks = [bytes([i + 1]) * 160 for i in range(20)]

    async def produce():
        for chunk in chunks:
            await queue.put(chunk)
        if close_queue:
            await queue.put(None)

    producer = asyncio.create_task(produce())
    try:
        await asyncio.wait_for(asyncio.gather(producer, info['pacer_task']), timeout=2)
        await asyncio.gather(info['streaming_task'], return_exceptions=True)
        playback.assert_awaited_once()
        assert playback.await_args.args == ('fallback', b''.join(chunks), 'streaming-fallback')
        assert 'fallback' not in manager.active_streams
        assert not session.tts_tokens
        assert session.audio_capture_enabled
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        if 'fallback' in manager.active_streams:
            await manager.stop_streaming_playback('fallback')
