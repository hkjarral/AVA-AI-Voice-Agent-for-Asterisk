"""Repeated acoustic bursts must not indefinitely renew caller-idle policy."""

from unittest.mock import AsyncMock, Mock
from array import array
import math
from types import SimpleNamespace

import pytest

from src.core.no_input_watchdog import NoInputPolicy, NoInputWatchdog
from src.core.models import CallSession
from src.engine import Engine


async def clocked_call(name, *, announce=None):
    clock = [1000.0]
    hangup = AsyncMock()
    watchdog = NoInputWatchdog(announce or AsyncMock(return_value=True), hangup, clock=lambda: clock[0])
    await watchdog.register(name, NoInputPolicy(initial_timeout_sec=15, grace_timeout_sec=15), is_outbound=False)
    await watchdog.mark_ready(name)
    return watchdog, watchdog._states[name], clock, hangup


@pytest.mark.asyncio
async def test_cng_sized_bursts_preserve_budget_and_do_not_reset_checkins():
    watchdog, state, clock, hangup = await clocked_call("periodic-bursts")
    try:
        for n in range(6):
            clock[0] = 1002.0 + 3.3 * n
            await watchdog.note_input_state(state.call_id, True, "audio_vad:audiosocket")
            clock[0] += 0.7  # About 0.5 s tone plus detector's end hangover.
            await watchdog.note_input_state(state.call_id, False, "audio_vad:audiosocket")
        assert state.deadline - clock[0] < 3
        assert state.check_ins == 0
        clock[0] = state.deadline
        await watchdog._perform_check_in(state)
        assert state.check_ins == 1
        assert state.phase == "grace"
        deadline = state.deadline
        clock[0] += 3
        await watchdog.note_input_state(state.call_id, True, "asterisk:talk_detect")
        clock[0] += 0.7
        await watchdog.note_input_state(state.call_id, False, "asterisk:talk_detect")
        assert state.check_ins == 1
        assert state.deadline == pytest.approx(deadline + 0.7)
        clock[0] = state.deadline
        await watchdog._finish_for_no_input(state)
        hangup.assert_awaited_once_with(state.call_id)
    finally:
        await watchdog.stop(state.call_id)


@pytest.mark.asyncio
async def test_recognized_short_answer_resets_the_full_window():
    watchdog, state, clock, _ = await clocked_call("short-answer")
    try:
        state.phase, state.check_ins = "grace", 1
        clock[0] += 14
        await watchdog.note_input_state(state.call_id, True, "audio_vad:audiosocket")
        clock[0] += 0.4
        await watchdog.note_activity(state.call_id, "provider:transcript")
        await watchdog.note_input_state(state.call_id, False, "audio_vad:audiosocket")
        assert state.deadline == pytest.approx(clock[0] + 15)
        assert state.check_ins == 0
    finally:
        await watchdog.stop(state.call_id)


@pytest.mark.asyncio
async def test_sustained_speech_is_protected_without_a_transcript():
    watchdog, state, clock, hangup = await clocked_call("sustained")
    try:
        clock[0] += 14
        await watchdog.note_input_state(state.call_id, True, "asterisk:talk_detect")
        clock[0] += 20
        assert not watchdog._can_count(state)
        hangup.assert_not_awaited()
        await watchdog.note_input_state(state.call_id, False, "asterisk:talk_detect")
        assert state.deadline == pytest.approx(clock[0] + 15)
        assert state.last_activity_source == "asterisk:talk_detect"
    finally:
        await watchdog.stop(state.call_id)


@pytest.mark.asyncio
async def test_duplicate_detector_starts_do_not_overwrite_the_remaining_budget():
    watchdog, state, clock, _ = await clocked_call("duplicate-detectors")
    try:
        clock[0] += 5
        await watchdog.note_input_state(state.call_id, True, "audio_vad:audiosocket")
        clock[0] += 0.1
        await watchdog.note_input_state(state.call_id, True, "asterisk:talk_detect")
        clock[0] += 0.5
        await watchdog.note_input_state(state.call_id, False, "audio_vad:audiosocket")
        assert state.deadline == pytest.approx(clock[0] + 10)
    finally:
        await watchdog.stop(state.call_id)


@pytest.mark.asyncio
async def test_acoustic_input_during_final_warning_gets_grace_without_repeating_warning():
    announcements = []

    async def announce(call_id, text, kind):
        announcements.append(kind)
        if kind == "final":
            await watchdog.note_input_state(call_id, True, "audio_vad:audiosocket")
            clock[0] += 0.6
            await watchdog.note_input_state(call_id, False, "audio_vad:audiosocket")
        return True

    watchdog, state, clock, hangup = await clocked_call("late-burst", announce=announce)
    try:
        state.phase, state.check_ins = "grace", 1
        await watchdog._finish_for_no_input(state)
        hangup.assert_not_awaited()
        assert state.phase == "grace" and state.check_ins == 1
        assert state.deadline == pytest.approx(clock[0] + 15)
        clock[0] = state.deadline
        await watchdog._finish_for_no_input(state)
        assert announcements == ["final"]
        hangup.assert_awaited_once()
    finally:
        await watchdog.stop(state.call_id)


@pytest.mark.asyncio
async def test_late_recognized_answer_renews_policy_including_final_warning():
    watchdog, state, clock, hangup = await clocked_call("late-answer")
    try:
        state.final_announced = True
        state.check_ins = 1
        await watchdog.note_activity(state.call_id, "pipeline:transcript")
        assert state.final_announced is False
        assert state.check_ins == 0
        assert state.deadline == clock[0] + 15
        hangup.assert_not_awaited()
    finally:
        await watchdog.stop(state.call_id)


@pytest.mark.asyncio
async def test_logs_report_sources_and_state_without_per_frame_events(monkeypatch):
    import src.core.no_input_watchdog as module
    logger = Mock()
    monkeypatch.setattr(module, "logger", logger)
    watchdog, state, clock, _ = await clocked_call("observable")
    try:
        await watchdog.note_input_state(state.call_id, True, "audio_vad:audiosocket")
        before = logger.info.call_count
        await watchdog.note_input_state(state.call_id, True, "audio_vad:audiosocket")
        assert logger.info.call_count == before
        clock[0] += 0.6
        await watchdog.note_input_state(state.call_id, False, "audio_vad:audiosocket")
        transitions = [call.kwargs for call in logger.info.call_args_list if call.args == ("Caller inactivity state changed",)]
        assert any(t["transition"] == "input_resume_remaining" and t["source"] == "audio_vad:audiosocket" for t in transitions)
        assert all("remaining_sec" in t and "processing" in t for t in transitions)
    finally:
        await watchdog.stop(state.call_id)


@pytest.mark.asyncio
async def test_outbound_opt_in_keeps_the_existing_acoustic_reset_policy():
    clock = [1000.0]
    watchdog = NoInputWatchdog(AsyncMock(return_value=True), AsyncMock(), clock=lambda: clock[0])
    await watchdog.register("outbound", NoInputPolicy(outbound_enabled=True, initial_timeout_sec=15), is_outbound=True)
    try:
        await watchdog.mark_ready("outbound")
        clock[0] += 14
        await watchdog.note_input_state("outbound", True, "audio_vad:audiosocket")
        clock[0] += 0.5
        await watchdog.note_input_state("outbound", False, "audio_vad:audiosocket")
        assert watchdog.snapshot("outbound")["deadline"] == clock[0] + 15
    finally:
        await watchdog.stop("outbound")


@pytest.mark.asyncio
async def test_periodic_1100_hz_audio_through_the_engine_does_not_renew_full_timeout():
    watchdog, state, clock, _ = await clocked_call("synthetic-cng")
    engine = Engine.__new__(Engine)
    engine.no_input_watchdog = watchdog
    engine._agent_output_active_calls = set()
    # Reproduce the reported WebRTC false positive deterministically, independent
    # of optional VAD wheels. RMS/noise adaptation and detector timing are real.
    engine._no_input_webrtc_vad = SimpleNamespace(is_speech=lambda *_: True)
    session = CallSession(call_id=state.call_id, caller_channel_id=state.call_id)
    tone = array("h", [int(8000 * math.sin(2 * math.pi * 1100 * n / 8000)) for n in range(160)]).tobytes()
    try:
        for frame in range(1000):
            elapsed = frame * 0.02
            clock[0] = 1000 + elapsed
            on = elapsed >= 2 and (elapsed - 2) % 3.3 < 0.5
            await engine._observe_no_input_audio(session, tone if on else bytes(320), 8000, source="audiosocket")
        assert state.input_sequence >= 2  # Actual engine VAD transitions occurred.
        assert state.deadline is not None and state.deadline - clock[0] < 2
    finally:
        await watchdog.stop(state.call_id)


@pytest.mark.asyncio
async def test_talkdetect_barge_in_during_checkin_is_still_acoustic_evidence():
    async def announce(call_id, *_):
        await watchdog.note_activity(call_id, "asterisk:talk_detect_barge_in")
        clock[0] += 0.7
        await watchdog.note_input_state(call_id, False, "asterisk:talk_detect")
        return True

    watchdog, state, clock, _ = await clocked_call("acoustic-barge", announce=announce)
    try:
        await watchdog._perform_check_in(state)
        assert state.check_ins == 1
        assert state.phase == "grace"
        assert state.deadline == clock[0] + 15
    finally:
        await watchdog.stop(state.call_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('pause', ['output', 'processing', 'suspended'])
async def test_sustained_input_renews_policy_even_when_its_end_is_paused(pause):
    watchdog, state, clock, _ = await clocked_call('paused-sustained-' + pause)
    try:
        state.phase, state.check_ins, state.final_announced = 'grace', 1, True
        clock[0] += 13
        await watchdog.note_input_state(state.call_id, True, 'asterisk:talk_detect')
        if pause == 'output':
            await watchdog.note_agent_output_start(state.call_id)
        elif pause == 'processing':
            await watchdog.note_processing(state.call_id, True)
        else:
            await watchdog.set_suspended(state.call_id, True)
        clock[0] += 1.2
        await watchdog.note_input_state(state.call_id, False, 'asterisk:talk_detect')
        assert state.check_ins == 0 and state.final_announced is False
        assert state.last_activity_source == 'asterisk:talk_detect'
        assert state.deadline is None
        if pause == 'output':
            await watchdog.note_agent_output_end(state.call_id, reset_timer=False)
        elif pause == 'processing':
            await watchdog.note_processing(state.call_id, False)
        else:
            await watchdog.set_suspended(state.call_id, False)
        assert state.deadline == clock[0] + 15
    finally:
        await watchdog.stop(state.call_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('reset_timer', [False, True])
async def test_silence_output_restores_deferred_short_input_budget(reset_timer):
    watchdog, state, clock, _ = await clocked_call('paused-short-' + str(reset_timer))
    try:
        state.phase, state.check_ins, state.final_announced = 'grace', 1, True
        clock[0] += 13
        await watchdog.note_input_state(state.call_id, True, 'asterisk:talk_detect')
        await watchdog.note_agent_output_start(state.call_id)
        clock[0] += 0.6
        await watchdog.note_input_state(state.call_id, False, 'asterisk:talk_detect')
        assert state.deadline is None
        await watchdog.note_agent_output_end(state.call_id, reset_timer=reset_timer)
        assert state.deadline == clock[0] + (15 if reset_timer else 2)
        assert state.final_announced is (not reset_timer)
        assert state.check_ins == (0 if reset_timer else 1)
    finally:
        await watchdog.stop(state.call_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('pause', ['processing', 'suspended'])
async def test_short_input_retains_its_budget_across_other_pauses(pause):
    watchdog, state, clock, _ = await clocked_call('short-pause-' + pause)
    try:
        state.phase, state.check_ins, state.final_announced = 'grace', 1, True
        clock[0] += 13
        await watchdog.note_input_state(state.call_id, True, 'asterisk:talk_detect')
        if pause == 'processing':
            await watchdog.note_processing(state.call_id, True)
        else:
            await watchdog.set_suspended(state.call_id, True)
        clock[0] += 0.6
        await watchdog.note_input_state(state.call_id, False, 'asterisk:talk_detect')
        if pause == 'processing':
            await watchdog.note_processing(state.call_id, False)
        else:
            await watchdog.set_suspended(state.call_id, False)
        assert state.deadline == clock[0] + 2
        assert state.check_ins == 1 and state.final_announced is True
    finally:
        await watchdog.stop(state.call_id)


@pytest.mark.asyncio
async def test_silence_output_end_does_not_start_counting_while_input_is_active():
    watchdog, state, clock, _ = await clocked_call('input-outlasts-output')
    try:
        clock[0] += 13
        await watchdog.note_input_state(state.call_id, True, 'asterisk:talk_detect')
        await watchdog.note_agent_output_start(state.call_id)
        clock[0] += 0.2
        await watchdog.note_agent_output_end(state.call_id, reset_timer=False)
        assert state.deadline is None and not watchdog._can_count(state)
        clock[0] += 0.4
        await watchdog.note_input_state(state.call_id, False, 'asterisk:talk_detect')
        assert state.deadline == clock[0] + 2
    finally:
        await watchdog.stop(state.call_id)
