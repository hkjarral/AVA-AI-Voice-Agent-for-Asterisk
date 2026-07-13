import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.models import CallSession
from src.core.no_input_watchdog import NoInputPolicy, NoInputWatchdog
from src.core.conversation_coordinator import ConversationCoordinator
from src.core.session_store import SessionStore
from src.config import NoInputConfig
from src.engine import Engine


async def _wait_until(predicate, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not reached before timeout")
        await asyncio.sleep(0.005)


def test_policy_coerces_raw_overrides_without_truthy_string_or_numeric_surprises():
    policy = NoInputPolicy.from_mapping(
        {
            "enabled": "false",
            "inbound_enabled": "0",
            "outbound_enabled": "yes",
            "initial_timeout_sec": float("nan"),
            "grace_timeout_sec": 5000,
            "max_check_ins": 1.5,
            "check_in_message": "   ",
            "final_message": "",
        }
    )

    assert policy.enabled is False
    assert policy.inbound_enabled is False
    assert policy.outbound_enabled is True
    assert policy.initial_timeout_sec == 30.0
    assert policy.grace_timeout_sec == 15.0
    assert policy.max_check_ins == 1
    assert policy.check_in_message == "Are you still there?"
    assert policy.final_message == "I still can't hear you, so I'll end the call now. Goodbye."


def test_global_config_rejects_blank_announcement_messages():
    with pytest.raises(ValueError):
        NoInputConfig(final_message="   ")
    with pytest.raises(ValueError):
        NoInputConfig(check_in_message="")


@pytest.mark.asyncio
async def test_watchdog_checks_in_then_says_final_message_and_hangs_up():
    announcements = []
    hangups = []

    async def announce(call_id, text, kind):
        announcements.append((call_id, text, kind))
        return True

    async def hangup(call_id):
        hangups.append(call_id)

    watchdog = NoInputWatchdog(announce, hangup)
    policy = NoInputPolicy(
        initial_timeout_sec=0.04,
        grace_timeout_sec=0.03,
        max_check_ins=1,
        check_in_message="Still there?",
        final_message="Goodbye.",
    )
    await watchdog.register("call-1", policy, is_outbound=False)
    try:
        await watchdog.mark_ready("call-1")
        await _wait_until(lambda: hangups == ["call-1"])
        assert announcements == [
            ("call-1", "Still there?", "check_in"),
            ("call-1", "Goodbye.", "final"),
        ]
        assert watchdog.snapshot("call-1")["phase"] == "hangup"
    finally:
        await watchdog.stop("call-1")


@pytest.mark.asyncio
async def test_final_announcement_exception_still_attempts_hangup():
    hangup = AsyncMock()

    async def announce(_call_id, _text, kind):
        if kind == "final":
            raise RuntimeError("provider unavailable")
        return True

    watchdog = NoInputWatchdog(announce, hangup)
    policy = NoInputPolicy(
        initial_timeout_sec=0.03,
        grace_timeout_sec=0.02,
        max_check_ins=0,
    )
    await watchdog.register("call-final-failure", policy, is_outbound=False)
    try:
        await watchdog.mark_ready("call-final-failure")
        await _wait_until(lambda: hangup.await_count == 1)
        assert watchdog.snapshot("call-final-failure")["phase"] == "hangup"
    finally:
        await watchdog.stop("call-final-failure")


@pytest.mark.asyncio
async def test_check_in_announcement_exception_keeps_watchdog_running():
    kinds = []
    hangup = AsyncMock()

    async def announce(_call_id, _text, kind):
        kinds.append(kind)
        if kind == "check_in":
            raise RuntimeError("provider unavailable")
        return True

    watchdog = NoInputWatchdog(announce, hangup)
    policy = NoInputPolicy(
        initial_timeout_sec=0.03,
        grace_timeout_sec=0.02,
        max_check_ins=1,
    )
    await watchdog.register("call-check-in-failure", policy, is_outbound=False)
    try:
        await watchdog.mark_ready("call-check-in-failure")
        await _wait_until(lambda: hangup.await_count == 1)
        assert kinds == ["check_in", "final"]
    finally:
        await watchdog.stop("call-check-in-failure")


@pytest.mark.asyncio
async def test_unexpected_watchdog_failure_removes_active_state():
    async def should_pause(_call_id):
        raise RuntimeError("session store unavailable")

    watchdog = NoInputWatchdog(AsyncMock(return_value=True), AsyncMock(), should_pause=should_pause)
    policy = NoInputPolicy(initial_timeout_sec=0.02, grace_timeout_sec=0.02, max_check_ins=1)
    await watchdog.register("call-run-failure", policy, is_outbound=False)
    await watchdog.mark_ready("call-run-failure")

    await _wait_until(lambda: not watchdog.has_call("call-run-failure"))


@pytest.mark.asyncio
async def test_caller_activity_resets_the_initial_window():
    announcements = []

    async def announce(call_id, text, kind):
        announcements.append(kind)
        return True

    watchdog = NoInputWatchdog(announce, AsyncMock())
    policy = NoInputPolicy(initial_timeout_sec=0.06, grace_timeout_sec=0.03, max_check_ins=1)
    await watchdog.register("call-2", policy, is_outbound=False)
    try:
        await watchdog.mark_ready("call-2")
        await asyncio.sleep(0.04)
        await watchdog.note_activity("call-2", "test:transcript")
        await asyncio.sleep(0.04)
        assert announcements == []
        await _wait_until(lambda: announcements == ["check_in"])
    finally:
        await watchdog.stop("call-2")


@pytest.mark.asyncio
async def test_sustained_caller_speech_and_agent_output_pause_the_clock():
    announcements = []

    async def announce(call_id, text, kind):
        announcements.append(kind)
        return True

    watchdog = NoInputWatchdog(announce, AsyncMock())
    policy = NoInputPolicy(initial_timeout_sec=0.04, grace_timeout_sec=0.03, max_check_ins=1)
    await watchdog.register("call-3", policy, is_outbound=False)
    try:
        await watchdog.mark_ready("call-3")
        await watchdog.note_input_state("call-3", True, "test:audio")
        await asyncio.sleep(0.08)
        assert announcements == []
        await watchdog.note_input_state("call-3", False, "test:audio")
        await watchdog.note_agent_output_start("call-3")
        await asyncio.sleep(0.08)
        assert announcements == []
        await watchdog.note_agent_output_end("call-3")
        await _wait_until(lambda: announcements == ["check_in"])
    finally:
        await watchdog.stop("call-3")


@pytest.mark.asyncio
async def test_unmatched_input_end_does_not_extend_check_in_grace_deadline():
    watchdog = NoInputWatchdog(AsyncMock(return_value=True), AsyncMock(), clock=lambda: 1000.0)
    policy = NoInputPolicy(initial_timeout_sec=30.0, grace_timeout_sec=15.0, max_check_ins=1)
    await watchdog.register("call-unmatched-end", policy, is_outbound=False)
    try:
        state = watchdog._states["call-unmatched-end"]
        state.ready = True
        state.input_active = False
        state.phase = "grace"
        state.check_ins = 1
        state.deadline = 1015.0

        await watchdog.note_input_state(
            "call-unmatched-end",
            False,
            "asterisk:talk_detect",
        )

        snapshot = watchdog.snapshot("call-unmatched-end")
        assert snapshot["phase"] == "grace"
        assert snapshot["check_ins"] == 1
        assert snapshot["deadline"] == 1015.0
    finally:
        await watchdog.stop("call-unmatched-end")


@pytest.mark.asyncio
async def test_hosted_silence_output_pauses_without_resetting_deadline():
    announcements = []

    async def announce(call_id, text, kind):
        announcements.append(kind)
        return True

    watchdog = NoInputWatchdog(announce, AsyncMock())
    policy = NoInputPolicy(initial_timeout_sec=0.08, grace_timeout_sec=0.03, max_check_ins=1)
    await watchdog.register("call-hosted-silence", policy, is_outbound=False)
    try:
        await watchdog.mark_ready("call-hosted-silence")
        await asyncio.sleep(0.05)
        await watchdog.note_agent_output_start("call-hosted-silence")
        await asyncio.sleep(0.05)
        assert announcements == []
        await watchdog.note_agent_output_end("call-hosted-silence", reset_timer=False)
        # Only the ~30ms remaining before hosted output should be restored.
        await _wait_until(lambda: announcements == ["check_in"], timeout=0.07)
    finally:
        await watchdog.stop("call-hosted-silence")


@pytest.mark.asyncio
async def test_self_announcement_drain_completion_preserves_grace_state():
    watchdog = NoInputWatchdog(AsyncMock(return_value=True), AsyncMock())
    policy = NoInputPolicy(initial_timeout_sec=30.0, grace_timeout_sec=15.0, max_check_ins=1)
    await watchdog.register("call-self-announcement", policy, is_outbound=False)
    try:
        await watchdog.mark_ready("call-self-announcement")
        state = watchdog._states["call-self-announcement"]
        state.output_active = True
        state.self_announcement = False
        state.phase = "grace"
        state.check_ins = 1
        state.deadline = 1234.5

        await watchdog.note_agent_output_end(
            "call-self-announcement",
            reset_timer=True,
            preserve_policy_state=True,
        )

        snapshot = watchdog.snapshot("call-self-announcement")
        assert snapshot["output_active"] is False
        assert snapshot["phase"] == "grace"
        assert snapshot["check_ins"] == 1
        assert snapshot["deadline"] == 1234.5
    finally:
        await watchdog.stop("call-self-announcement")


@pytest.mark.asyncio
async def test_raw_audio_detector_ignores_audio_during_native_provider_output():
    engine = Engine.__new__(Engine)
    engine.no_input_watchdog = SimpleNamespace(
        has_call=lambda _call_id: True,
        note_input_state=AsyncMock(),
    )
    engine._agent_output_active_calls = {"call-native-output"}
    session = CallSession(
        call_id="call-native-output",
        caller_channel_id="channel-native-output",
    )
    session.tts_playing = False

    await engine._observe_no_input_audio(
        session,
        b"\xff\x7f" * 160,
        16000,
        source="audiosocket",
    )

    engine.no_input_watchdog.note_input_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_talk_detect_echo_tail_does_not_pause_no_input_watchdog():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine.config = SimpleNamespace(
        barge_in=SimpleNamespace(enabled=True, post_tts_end_protection_ms=600)
    )
    engine._no_input_note_input_state = AsyncMock()
    session = CallSession(
        call_id="call-post-tts-echo",
        caller_channel_id="channel-post-tts-echo",
    )
    session.audio_capture_enabled = True
    session.tts_playing = False
    session.tts_ended_ts = time.time()
    await engine.session_store.upsert_call(session)

    await engine._handle_channel_talking_started(
        {"channel": {"id": session.caller_channel_id}}
    )

    engine._no_input_note_input_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_talk_detect_after_post_tts_guard_pauses_no_input_watchdog():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine.config = SimpleNamespace(
        barge_in=SimpleNamespace(enabled=True, post_tts_end_protection_ms=600)
    )
    engine._no_input_note_input_state = AsyncMock()
    session = CallSession(
        call_id="call-real-talking",
        caller_channel_id="channel-real-talking",
    )
    session.audio_capture_enabled = True
    session.tts_playing = False
    session.tts_ended_ts = time.time() - 1.0
    await engine.session_store.upsert_call(session)

    await engine._handle_channel_talking_started(
        {"channel": {"id": session.caller_channel_id}}
    )

    engine._no_input_note_input_state.assert_awaited_once_with(
        session.call_id,
        True,
        "asterisk:talk_detect",
    )


@pytest.mark.asyncio
async def test_no_input_provider_output_drains_without_resetting_policy_state():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine._call_bg_tasks = {}
    engine._provider_output_operations = {}
    engine._provider_output_drain_tasks = {}
    engine._agent_output_active_calls = set()
    engine.config = SimpleNamespace(audio_transport="audiosocket")
    engine.no_input_watchdog = SimpleNamespace(
        note_agent_output_start=AsyncMock(),
        note_agent_output_end=AsyncMock(),
    )
    engine._save_session = AsyncMock()
    drain_started = asyncio.Event()
    release_drain = asyncio.Event()

    async def wait_for_drain(_call_id, **_kwargs):
        drain_started.set()
        await release_drain.wait()
        return True

    engine._wait_for_call_audio_drain = wait_for_drain
    session = CallSession(
        call_id="call-provider-tail",
        caller_channel_id="channel-provider-tail",
    )
    await engine.session_store.upsert_call(session)
    engine._provider_output_operations[session.call_id] = {
        "output_id": "no-input-check-in",
        "purpose": "no_input_check_in",
        "audio_started": asyncio.Event(),
        "generation_done": asyncio.Event(),
    }

    await engine._note_provider_output_start(session.call_id)
    await engine._note_provider_output_end(session.call_id, session)
    await asyncio.wait_for(drain_started.wait(), timeout=0.2)

    assert session.call_id in engine._agent_output_active_calls
    engine.no_input_watchdog.note_agent_output_end.assert_not_awaited()

    release_drain.set()
    await _wait_until(lambda: session.call_id not in engine._agent_output_active_calls)
    engine.no_input_watchdog.note_agent_output_end.assert_awaited_once_with(
        session.call_id,
        reset_timer=True,
        preserve_policy_state=True,
    )

    await engine._note_provider_output_end(session.call_id, session)
    assert engine.no_input_watchdog.note_agent_output_end.await_count == 2
    assert engine.no_input_watchdog.note_agent_output_end.await_args.kwargs == {
        "reset_timer": True,
        "preserve_policy_state": True,
    }


@pytest.mark.asyncio
async def test_new_provider_audio_cancels_stale_drain_without_clearing_output_state():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine._call_bg_tasks = {}
    engine._provider_output_operations = {}
    engine._provider_output_drain_tasks = {}
    engine._agent_output_active_calls = set()
    engine.config = SimpleNamespace(audio_transport="externalmedia")
    engine.no_input_watchdog = SimpleNamespace(
        note_agent_output_start=AsyncMock(),
        note_agent_output_end=AsyncMock(),
    )
    engine._save_session = AsyncMock()
    drain_started = asyncio.Event()
    hold_drain = asyncio.Event()

    async def wait_for_drain(_call_id, **_kwargs):
        drain_started.set()
        await hold_drain.wait()
        return True

    engine._wait_for_call_audio_drain = wait_for_drain
    session = CallSession(
        call_id="call-overlapping-output",
        caller_channel_id="channel-overlapping-output",
    )
    await engine.session_store.upsert_call(session)

    await engine._note_provider_output_start(session.call_id)
    await engine._note_provider_output_end(session.call_id, session)
    await asyncio.wait_for(drain_started.wait(), timeout=0.2)
    stale_task = engine._provider_output_drain_tasks[session.call_id]

    await engine._note_provider_output_end(session.call_id, session)
    assert engine._provider_output_drain_tasks[session.call_id] is stale_task

    await engine._note_provider_output_start(session.call_id)
    await asyncio.sleep(0)

    assert stale_task.cancelled() or stale_task.done()
    assert session.call_id in engine._agent_output_active_calls
    assert session.call_id not in engine._provider_output_drain_tasks
    engine.no_input_watchdog.note_agent_output_end.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchdog_observer_failure_does_not_break_tts_gating():
    session_store = SessionStore()
    session = CallSession(
        call_id="call-observer-failure",
        caller_channel_id="channel-observer-failure",
    )
    await session_store.upsert_call(session)
    coordinator = ConversationCoordinator(session_store)
    coordinator.set_no_input_watchdog(
        SimpleNamespace(
            note_agent_output_start=AsyncMock(side_effect=RuntimeError("start failed")),
            note_agent_output_end=AsyncMock(side_effect=RuntimeError("end failed")),
        )
    )

    assert await coordinator.on_tts_start("call-observer-failure", "playback-1") is True
    during = await session_store.get_by_call_id("call-observer-failure")
    assert during.tts_playing is True
    assert during.audio_capture_enabled is False

    assert await coordinator.on_tts_end("call-observer-failure", "playback-1") is True
    after = await session_store.get_by_call_id("call-observer-failure")
    assert after.tts_playing is False
    assert after.audio_capture_enabled is True


@pytest.mark.asyncio
async def test_transport_gating_can_end_without_ending_provider_output_timing():
    session_store = SessionStore()
    session = CallSession(
        call_id="call-provider-drain-gating",
        caller_channel_id="channel-provider-drain-gating",
    )
    await session_store.upsert_call(session)
    watchdog = SimpleNamespace(
        note_agent_output_start=AsyncMock(),
        note_agent_output_end=AsyncMock(),
    )
    coordinator = ConversationCoordinator(session_store)
    coordinator.set_no_input_watchdog(watchdog)

    assert await coordinator.on_tts_start(session.call_id, "stream-1") is True
    assert await coordinator.on_tts_end(
        session.call_id,
        "stream-1",
        reason="provider-generation-complete",
        notify_no_input=False,
    ) is True

    watchdog.note_agent_output_start.assert_awaited_once_with(session.call_id)
    watchdog.note_agent_output_end.assert_not_awaited()
    after = await session_store.get_by_call_id(session.call_id)
    assert after.tts_playing is False
    assert after.audio_capture_enabled is True


@pytest.mark.asyncio
async def test_transfer_policy_callback_prevents_prompts_while_caller_is_on_hold():
    announcements = []
    paused = True

    async def announce(call_id, text, kind):
        announcements.append(kind)
        return True

    async def should_pause(call_id):
        return paused

    watchdog = NoInputWatchdog(announce, AsyncMock(), should_pause=should_pause)
    policy = NoInputPolicy(initial_timeout_sec=0.04, grace_timeout_sec=0.03, max_check_ins=1)
    await watchdog.register("call-hold", policy, is_outbound=False)
    try:
        await watchdog.mark_ready("call-hold")
        await asyncio.sleep(0.1)
        assert announcements == []
        paused = False
        await _wait_until(lambda: announcements == ["check_in"])
    finally:
        await watchdog.stop("call-hold")


@pytest.mark.asyncio
async def test_outbound_calls_require_context_level_opt_in_even_if_global_is_true():
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(
        no_input=SimpleNamespace(
            model_dump=lambda: {
                "enabled": True,
                "inbound_enabled": True,
                "outbound_enabled": True,
                "initial_timeout_sec": 30,
                "grace_timeout_sec": 15,
                "max_check_ins": 1,
            }
        )
    )
    engine.no_input_watchdog = SimpleNamespace(register=AsyncMock())
    engine._save_session = AsyncMock()
    session = CallSession(
        call_id="outbound-1",
        caller_channel_id="channel-1",
        is_outbound=True,
    )

    await engine._configure_no_input_watchdog(session, SimpleNamespace(no_input={}))
    disabled_policy = engine.no_input_watchdog.register.await_args.args[1]
    assert disabled_policy.outbound_enabled is False

    await engine._configure_no_input_watchdog(
        session,
        SimpleNamespace(no_input={"outbound_enabled": "true", "initial_timeout_sec": 45}),
    )
    enabled_policy = engine.no_input_watchdog.register.await_args.args[1]
    assert enabled_policy.outbound_enabled is True
    assert enabled_policy.initial_timeout_sec == 45

    await engine._configure_no_input_watchdog(
        session,
        SimpleNamespace(no_input={"outbound_enabled": "false"}),
    )
    disabled_string_policy = engine.no_input_watchdog.register.await_args.args[1]
    assert disabled_string_policy.outbound_enabled is False


@pytest.mark.asyncio
async def test_engine_hangup_records_a_distinct_policy_outcome():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine.conversation_coordinator = None
    engine.ari_client = SimpleNamespace(hangup_channel=AsyncMock())
    session = CallSession(call_id="silent-call", caller_channel_id="channel-silent")
    await engine.session_store.upsert_call(session)

    await engine._hangup_for_no_input("silent-call")

    updated = await engine.session_store.get_by_call_id("silent-call")
    assert updated.call_outcome == "no_input_timeout"
    assert updated.no_input_state["timed_out"] is True
    engine.ari_client.hangup_channel.assert_awaited_once_with("channel-silent")


@pytest.mark.asyncio
async def test_caller_audio_drain_waits_for_stream_buffers_and_quiet_tail():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine._provider_stream_queues = {}
    engine._provider_coalesce_buf = {}

    call_id = "buffered-announcement"
    await engine.session_store.upsert_call(
        CallSession(call_id=call_id, caller_channel_id="channel-buffered")
    )

    jitter_buffer = asyncio.Queue()
    jitter_buffer.put_nowait(b"audio")
    engine.streaming_playback_manager = SimpleNamespace(
        active_streams={
            call_id: {
                "buffered_bytes": 160,
                "last_real_emit_ts": None,
            }
        },
        jitter_buffers={call_id: jitter_buffer},
        frame_remainders={call_id: b"tail"},
    )

    drain_task = asyncio.create_task(
        engine._wait_for_call_audio_drain(
            call_id,
            timeout_sec=1.0,
            quiet_sec=0.03,
            reason="test",
        )
    )
    await asyncio.sleep(0.05)
    assert drain_task.done() is False

    engine.streaming_playback_manager.active_streams[call_id]["buffered_bytes"] = 0
    engine.streaming_playback_manager.active_streams[call_id]["last_real_emit_ts"] = time.time()
    jitter_buffer.get_nowait()
    engine.streaming_playback_manager.frame_remainders[call_id] = b""

    assert await drain_task is True


def test_terminal_quiet_tail_covers_audiosocket_and_externalmedia():
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(audio_transport="audiosocket")
    assert engine._terminal_transport_quiet_sec() == 0.35
    engine.config.audio_transport = "externalmedia"
    assert engine._terminal_transport_quiet_sec() == 0.5


@pytest.mark.asyncio
async def test_terminal_hangup_is_idempotent_and_uses_shared_drain():
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(audio_transport="audiosocket")
    engine.session_store = SessionStore()
    engine.conversation_coordinator = None
    engine.ari_client = SimpleNamespace(hangup_channel=AsyncMock())
    engine._wait_for_call_audio_drain = AsyncMock(return_value=True)
    session = CallSession(call_id="terminal-call", caller_channel_id="channel-terminal")
    await engine.session_store.upsert_call(session)

    assert await engine._terminate_call_after_audio(
        "terminal-call",
        reason="test",
        call_outcome="agent_hangup",
    ) is True
    assert await engine._terminate_call_after_audio("terminal-call", reason="duplicate") is False
    updated = await engine.session_store.get_by_call_id("terminal-call")
    assert updated.call_outcome == "agent_hangup"
    engine._wait_for_call_audio_drain.assert_awaited_once()
    engine.ari_client.hangup_channel.assert_awaited_once_with("channel-terminal")


@pytest.mark.asyncio
async def test_terminal_hangup_yields_to_transfer_state():
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(audio_transport="externalmedia")
    engine.session_store = SessionStore()
    engine.conversation_coordinator = None
    engine.ari_client = SimpleNamespace(hangup_channel=AsyncMock())
    engine._wait_for_call_audio_drain = AsyncMock(return_value=True)
    session = CallSession(call_id="transfer-call", caller_channel_id="channel-transfer")
    session.transfer_active = True
    await engine.session_store.upsert_call(session)

    assert await engine._terminate_call_after_audio("transfer-call", reason="test") is False
    engine._wait_for_call_audio_drain.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_input_wait_keeps_gating_active_until_transport_drains(monkeypatch):
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine.conversation_coordinator = ConversationCoordinator(engine.session_store)

    call_id = "gated-announcement"
    session = CallSession(call_id=call_id, caller_channel_id="channel-gated")
    session.tts_started_ts = 2.0
    session.tts_playing = False
    await engine.session_store.upsert_call(session)
    operation = engine._begin_provider_output_operation(
        call_id,
        "no-input:final:test",
        "no_input_final",
    )
    operation["audio_started"].set()
    operation["generation_done"].set()

    async def fake_drain(_call_id, **_kwargs):
        during = await engine.session_store.get_by_call_id(call_id)
        assert "no_input_drain:no-input:final:test" in during.tts_tokens
        assert during.tts_playing is True
        return True

    monkeypatch.setattr(engine, "_wait_for_call_audio_drain", fake_drain)

    assert await engine._wait_for_no_input_announcement(
        call_id,
        announcement_id="no-input:final:test",
        previous_tts_started_ts=1.0,
        timeout_sec=0.2,
    ) is True

    after = await engine.session_store.get_by_call_id(call_id)
    assert "no_input_drain:no-input:final:test" not in after.tts_tokens
    assert after.tts_playing is False
