import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.models import CallSession
from src.core.no_input_watchdog import NoInputPolicy, NoInputWatchdog
from src.core.conversation_coordinator import ConversationCoordinator
from src.core.session_store import SessionStore
from src.engine import Engine


async def _wait_until(predicate, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not reached before timeout")
        await asyncio.sleep(0.005)


def test_policy_preserves_an_explicit_empty_final_message():
    policy = NoInputPolicy.from_mapping({"final_message": ""})
    assert policy.final_message == ""


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
        SimpleNamespace(no_input={"outbound_enabled": True, "initial_timeout_sec": 45}),
    )
    enabled_policy = engine.no_input_watchdog.register.await_args.args[1]
    assert enabled_policy.outbound_enabled is True
    assert enabled_policy.initial_timeout_sec == 45


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

    assert await engine._terminate_call_after_audio("terminal-call", reason="test") is True
    assert await engine._terminate_call_after_audio("terminal-call", reason="duplicate") is False
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
