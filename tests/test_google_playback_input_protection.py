"""Google greeting drain and post-playback input contracts (#849 / #850)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import src.engine as engine_module
from src.audio.audiosocket_protocol import AudioSocketAudioFrame
from src.core.conversation_coordinator import ConversationCoordinator
from src.core.models import CallSession
from tests.test_websocket_provider_contracts import _engine, _Provider, _capabilities, _register_session
from tests.test_google_long_audio_playback import bare_engine, provider

PCM = b"\x34\x12\xcc\xed" * 160  # AC signal survives AudioSocket DC removal.


async def ingress_fixture(transport, *, full_duplex=False, kind="google_live", guard_ms=600):
    e = _engine()
    e.config.audio_transport = transport
    e.config.barge_in.post_tts_end_protection_ms = guard_ms
    e.conn_to_channel = {"conn": "call-1"}
    e.audio_socket_server = None
    e.audio_capture.append_pcm16 = lambda *a: None
    e._update_transport_profile = AsyncMock()
    e._save_session = AsyncMock()
    p = _Provider(_capabilities())
    p.uses_full_duplex_barge_in = lambda: full_duplex
    e._call_providers["call-1"] = p
    e.provider_kinds["named-instance"] = kind
    s = await _register_session(e, "call-1", provider_name="named-instance", capture_enabled=True)
    s.media_rx_confirmed = True
    s.audio_diagnostics["inbound_first_frame"] = True
    s.vad_state["format_probe_done"] = True
    return e, s, p


async def receive(e, transport):
    if transport == "audiosocket":
        await e._audiosocket_handle_audio("conn", AudioSocketAudioFrame(PCM, 0x12, "slin16", 16000))
    else:
        await e._on_transport_pcm("call-1", PCM, 16000, source=transport)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["audiosocket", "externalmedia", "websocket"])
@pytest.mark.parametrize("age_ms,guard_ms,silenced", [(100, 600, True), (600, 600, False), (800, 600, False), (100, 0, False), (100, 1500, True)])
async def test_post_playback_guard_controls_actual_payload(monkeypatch, transport, age_ms, guard_ms, silenced):
    e, s, p = await ingress_fixture(transport, guard_ms=guard_ms)
    s.google_playback_drained_monotonic = 100 - age_ms / 1000
    monkeypatch.setattr(engine_module, "time", SimpleNamespace(monotonic=lambda: 100, time=lambda: 1000))
    await receive(e, transport)
    assert p.send_audio.await_count == 1
    assert p.send_audio.await_args.args[0] == (bytes(len(PCM)) if silenced else PCM)
    if silenced:
        assert s.audio_diagnostics["google_post_tts_protection"] == {"frames": 1, "duration_ms": 20.0}
        # The same protected audio cannot accumulate a local barge-in candidate.
        e._maybe_provider_barge_in_fallback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["audiosocket", "externalmedia", "websocket"])
@pytest.mark.parametrize("full_duplex,kind", [(True, "google_live"), (False, "openai_realtime"), (False, "grok"), (False, "deepgram"), (False, "elevenlabs_agent")])
async def test_native_providers_keep_real_caller_audio(monkeypatch, transport, full_duplex, kind):
    e, s, p = await ingress_fixture(transport, full_duplex=full_duplex, kind=kind)
    s.google_playback_drained_monotonic = 99.9
    s.audio_capture_enabled = False
    s.tts_playing = True
    monkeypatch.setattr(engine_module, "time", SimpleNamespace(monotonic=lambda: 100, time=lambda: 1000))
    await receive(e, transport)
    assert p.send_audio.await_args.args[0] == PCM


@pytest.mark.asyncio
async def test_guard_uses_monotonic_clock_and_duplicate_token_clear_is_idempotent():
    e, s, _ = await ingress_fixture("audiosocket")
    await e.session_store.set_gating_token(s.call_id, "tts")
    await e.session_store.clear_gating_token(s.call_id, "tts")
    ended = s.tts_ended_monotonic
    await e.session_store.clear_gating_token(s.call_id, "tts")
    assert s.tts_ended_monotonic == ended
    assert e._google_post_tts_protection_active(s)
    assert CallSession(call_id="fresh", caller_channel_id="fresh").tts_ended_monotonic == 0


@pytest.mark.asyncio
async def test_protected_echo_does_not_count_as_raw_activity_or_local_barge_in():
    e, s, _ = await ingress_fixture("audiosocket")
    e.no_input_watchdog = SimpleNamespace(has_call=lambda _: True, note_input_state=AsyncMock())
    s.google_playback_drained_monotonic = engine_module.time.monotonic()
    s.barge_in_candidate_ms = 500
    # Enter the real observer and fallback, not their mocked ingress wrappers.
    await e._observe_no_input_audio(s, PCM, 16000, source="audiosocket")
    e.config.barge_in.provider_fallback_enabled = True
    e.config.default_provider = "named-instance"
    await engine_module.Engine._maybe_provider_barge_in_fallback(e, s, pcm16=PCM, pcm_rate_hz=16000,
                                                                audiosocket_wire=PCM, source="audiosocket")
    e.no_input_watchdog.note_input_state.assert_not_awaited()
    assert s.barge_in_candidate_ms == 0


async def greeting_fixture(*, full_duplex=False):
    p = provider(enabled=False, vertex=True)
    p.uses_full_duplex_barge_in = lambda: full_duplex
    e, s = bare_engine(p)
    e.config.barge_in = SimpleNamespace(post_tts_end_protection_ms=600)
    e.conversation_coordinator = ConversationCoordinator(e.session_store)
    s.conversation_state = "greeting"
    s.provider_session_active = True
    await e.session_store.upsert_call(s)
    await e.conversation_coordinator.on_tts_start(s.call_id, "stream:greeting")
    await e.conversation_coordinator.on_tts_start(s.call_id, f"tts_segment:{s.call_id}")
    async def end_segment(call_id, **kwargs):
        await e.conversation_coordinator.on_tts_end(call_id, "stream:greeting", **kwargs)
    e.streaming_playback_manager.end_segment_gating.side_effect = end_segment
    await e._note_provider_output_start(s.call_id, s)
    started, release = asyncio.Event(), asyncio.Event()
    async def drain(*args, **kwargs):
        started.set()
        await release.wait()
        return True
    e._wait_for_call_audio_drain = drain
    return e, s, started, release


@pytest.mark.asyncio
@pytest.mark.parametrize("full_duplex", [False, True])
async def test_google_agent_audio_done_holds_only_engine_owned_greeting_until_drain(full_duplex):
    e, s, started, release = await greeting_fixture(full_duplex=full_duplex)
    await e.on_provider_event({"type": "AgentAudioDone", "call_id": s.call_id, "streaming_done": True})
    await asyncio.wait_for(started.wait(), 1)
    task = e._provider_output_drain_tasks[s.call_id]
    assert bool(s.tts_tokens) is (not full_duplex)
    assert s.conversation_state == "greeting"
    release.set()
    await asyncio.wait_for(task, 1)
    assert s.audio_capture_enabled is True
    assert s.tts_tokens == set()
    assert s.conversation_state == "listening"
    assert e._google_post_tts_protection_active(s) is (not full_duplex)
    assert e._provider_output_drain_tasks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("removed", [False, True])
async def test_new_output_or_deleted_call_cannot_be_released_by_stale_drain(removed):
    e, s, started, release = await greeting_fixture()
    await e._note_provider_output_end(s.call_id, s, clear_tts_gating_after_drain=True)
    await asyncio.wait_for(started.wait(), 1)
    task = e._provider_output_drain_tasks[s.call_id]
    if removed:
        await e.session_store.remove_call(s.call_id)
    else:
        await e._note_provider_output_start(s.call_id, s)
        await e.conversation_coordinator.on_tts_start(s.call_id, "next-response")
    release.set()
    await asyncio.wait_for(task, 1)
    assert s.google_playback_drained_monotonic == 0
    if not removed:
        assert "next-response" in s.tts_tokens
        assert not s.audio_capture_enabled
    else:
        assert await e.session_store.get_by_call_id(s.call_id) is None


@pytest.mark.asyncio
async def test_drain_failure_terminates_google_greeting_without_opening_capture():
    e, s, _, _ = await greeting_fixture()
    e._wait_for_call_audio_drain = AsyncMock(return_value=False)
    await e._note_provider_output_end(s.call_id, s, clear_tts_gating_after_drain=True)
    task = e._provider_output_drain_tasks[s.call_id]
    await asyncio.wait_for(task, 1)
    assert not s.audio_capture_enabled
    e._terminate_call_after_audio.assert_awaited_once()
    assert e._provider_output_drain_tasks == {}


@pytest.mark.asyncio
async def test_accepted_barge_in_does_not_mute_the_callers_next_frame():
    e, s, p = await ingress_fixture("audiosocket")
    e._provider_stream_queues = {}
    e._provider_stream_formats = {}
    e._provider_coalesce_buf = {}
    e.streaming_playback_manager = SimpleNamespace(active_streams={}, stop_streaming_playback=AsyncMock())
    s.google_playback_drained_monotonic = engine_module.time.monotonic()
    await e._apply_barge_in_action(s.call_id, source="local_vad_fallback", reason="speech")
    assert s.google_output_interrupted
    await receive(e, "audiosocket")
    assert p.send_audio.await_args.args[0] == PCM


@pytest.mark.asyncio
async def test_pending_midcall_playback_does_not_apply_generation_end_guard():
    e, s, p = await ingress_fixture("audiosocket")
    s.conversation_state = "listening"
    s.tts_ended_monotonic = engine_module.time.monotonic()
    e._agent_output_active_calls.add(s.call_id)
    await receive(e, "audiosocket")
    assert p.send_audio.await_args.args[0] == PCM


@pytest.mark.asyncio
async def test_transport_quiet_tail_is_credited_and_duplicate_completion_does_not_extend_guard():
    e, s, started, release = await greeting_fixture()
    await e._note_provider_output_end(s.call_id, s, clear_tts_gating_after_drain=True)
    await asyncio.wait_for(started.wait(), 1)
    task = e._provider_output_drain_tasks[s.call_id]
    await e._note_provider_output_end(s.call_id, s, clear_tts_gating_after_drain=True)
    assert e._provider_output_drain_tasks[s.call_id] is task
    release.set()
    await asyncio.wait_for(task, 1)
    ended = s.google_playback_drained_monotonic
    assert engine_module.time.monotonic() - ended >= 0.35
    await e._note_provider_output_end(s.call_id, s, clear_tts_gating_after_drain=True)
    assert s.google_playback_drained_monotonic == ended


@pytest.mark.asyncio
@pytest.mark.parametrize("age_ms,guard_ms,protected", [(100, 600, True), (800, 600, False), (100, 0, False)])
async def test_talk_detect_rechecks_google_guard_after_playback_drains(monkeypatch, age_ms, guard_ms, protected):
    e, s, _ = await ingress_fixture("audiosocket", guard_ms=guard_ms)
    e.config.barge_in.talk_detect_initial_protection_ms = 0
    e.config.barge_in.cooldown_ms = 0
    e._no_input_note_input_state = AsyncMock()
    e._apply_barge_in_action = AsyncMock()
    s.audio_capture_enabled = False
    s.tts_playing = True
    s.tts_started_ts = 998
    s.conversation_state = "listening"
    e._agent_output_active_calls.add(s.call_id)
    monkeypatch.setattr(engine_module, "time", SimpleNamespace(monotonic=lambda: 100, time=lambda: 1000))
    paused, resume = asyncio.Event(), asyncio.Event()

    async def pending_activity(*args):
        paused.set()
        await resume.wait()

    e._no_input_note_activity = AsyncMock(side_effect=pending_activity)
    task = asyncio.create_task(e._handle_channel_talking_started({"channel": {"id": s.caller_channel_id}}))
    try:
        await asyncio.wait_for(paused.wait(), 1)
        # Drain completes while the event is waiting, opening a new monotonic
        # protection interval after its initial guard check already passed.
        e._agent_output_active_calls.discard(s.call_id)
        s.audio_capture_enabled = True
        s.tts_playing = False
        s.google_playback_drained_monotonic = 100 - age_ms / 1000
        s.tts_ended_ts = 1000 - age_ms / 1000
        resume.set()
        await asyncio.wait_for(task, 1)
    finally:
        resume.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    e._apply_barge_in_action.assert_not_awaited()
    if protected:
        e._no_input_note_input_state.assert_not_awaited()
    else:
        e._no_input_note_input_state.assert_awaited_once_with(s.call_id, True, "asterisk:talk_detect")


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["audiosocket", "externalmedia", "websocket"])
@pytest.mark.parametrize("protected", [False, True])
async def test_send_await_does_not_reclassify_protected_frame(monkeypatch, transport, protected):
    e, s, p = await ingress_fixture(transport)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(engine_module, "time", SimpleNamespace(monotonic=lambda: clock.now, time=lambda: 1000))
    s.google_playback_drained_monotonic = 99.9 if protected else 99.0
    s.barge_in_candidate_ms = 250
    s.barge_start_ts = 990
    paused, resume = asyncio.Event(), asyncio.Event()

    async def blocked_send(*args, **kwargs):
        paused.set()
        await resume.wait()

    async def count_candidate(*args, **kwargs):
        s.barge_in_candidate_ms += 20

    p.send_audio.side_effect = blocked_send
    e._maybe_provider_barge_in_fallback.side_effect = count_candidate
    task = asyncio.create_task(receive(e, transport))
    try:
        await asyncio.wait_for(paused.wait(), 1)
        assert p.send_audio.await_args.args[0] == (bytes(len(PCM)) if protected else PCM)
        if protected:
            assert s.barge_in_candidate_ms == 0
            assert s.barge_start_ts == 0.0
        # The original frame stays protected even though its send finishes
        # after expiry. Do not erase a newer frame's candidate on completion.
        clock.now = 101.0
        s.barge_in_candidate_ms = 80
        s.barge_start_ts = 1001
        resume.set()
        await asyncio.wait_for(task, 1)
    finally:
        resume.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    if protected:
        e._maybe_provider_barge_in_fallback.assert_not_awaited()
        assert s.barge_in_candidate_ms == 80
        assert s.barge_start_ts == 1001
    else:
        e._maybe_provider_barge_in_fallback.assert_awaited_once()
        assert s.barge_in_candidate_ms == 100
