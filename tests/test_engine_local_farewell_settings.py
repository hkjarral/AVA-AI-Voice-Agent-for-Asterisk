import asyncio
import types
from unittest.mock import AsyncMock, Mock

import pytest

from src.engine import Engine
from src.core.models import CallSession
from tests.test_deferred_transfer_commit_audio_gate import _build_engine


def _engine_with_local_farewell(mode: str) -> Engine:
    engine = Engine.__new__(Engine)
    engine.provider_kinds = {"local": "local"}
    engine.config = types.SimpleNamespace(
        providers={"local": {"farewell_mode": mode}}
    )
    return engine


def test_resolve_local_farewell_settings_supports_dict_config():
    mode, timeout_sec = Engine._resolve_local_farewell_settings(
        {
            "farewell_mode": "tts",
            "farewell_timeout_sec": "12.5",
        }
    )

    assert mode == "tts"
    assert timeout_sec == 12.5


def test_resolve_local_farewell_settings_supports_object_config():
    local_config = types.SimpleNamespace(
        farewell_mode="TTS",
        farewell_timeout_sec=8,
    )

    mode, timeout_sec = Engine._resolve_local_farewell_settings(local_config)

    assert mode == "tts"
    assert timeout_sec == 8.0


@pytest.mark.parametrize("raw_mode", ["", "invalid", "whisper"])
def test_resolve_local_farewell_settings_rejects_unknown_mode(raw_mode: str):
    mode, timeout_sec = Engine._resolve_local_farewell_settings(
        {
            "farewell_mode": raw_mode,
            "farewell_timeout_sec": 3,
        }
    )

    assert mode == "asterisk"
    assert timeout_sec == 3.0


@pytest.mark.parametrize("raw_timeout", ["", "nope", -1, 0])
def test_resolve_local_farewell_settings_rejects_invalid_timeout(raw_timeout):
    mode, timeout_sec = Engine._resolve_local_farewell_settings(
        {
            "farewell_mode": "tts",
            "farewell_timeout_sec": raw_timeout,
        }
    )

    assert mode == "tts"
    assert timeout_sec == 30.0


def test_resolve_local_farewell_settings_defaults_when_missing():
    mode, timeout_sec = Engine._resolve_local_farewell_settings(None)

    assert mode == "asterisk"
    assert timeout_sec == 30.0


def test_asterisk_local_farewell_skips_redundant_hangup_result():
    engine = _engine_with_local_farewell("asterisk")

    assert not engine._should_send_provider_tool_result(
        provider_name="local",
        function_name="hangup_call",
        result={"will_hangup": True},
    )


def test_local_tts_farewell_keeps_hangup_result_turn():
    engine = _engine_with_local_farewell("tts")

    assert engine._should_send_provider_tool_result(
        provider_name="local",
        function_name="hangup_call",
        result={"will_hangup": True},
    )


def test_local_nonterminal_tool_keeps_result_turn():
    engine = _engine_with_local_farewell("asterisk")

    assert engine._should_send_provider_tool_result(
        provider_name="local",
        function_name="request_transcript",
        result={"status": "success"},
    )


def test_named_local_provider_uses_its_own_farewell_settings():
    engine = Engine.__new__(Engine)
    engine.provider_kinds = {"local-gpu": "local"}
    engine.config = types.SimpleNamespace(
        providers={
            "local": {"farewell_mode": "asterisk"},
            "local-gpu": {"farewell_mode": "tts"},
        }
    )

    assert engine._should_send_provider_tool_result(
        provider_name="local-gpu",
        function_name="hangup_call",
        result={"will_hangup": True},
    )


@pytest.mark.asyncio
async def test_local_farewell_fallback_clears_boundary_and_marks_agent_hangup():
    engine = Engine.__new__(Engine)
    session = types.SimpleNamespace(cleanup_after_tts=True)
    engine.session_store = types.SimpleNamespace(
        get_by_call_id=AsyncMock(return_value=session)
    )
    engine._local_tts_farewell_pending = {"call-timeout"}
    engine._terminal_fallback_tasks = {}
    engine._terminate_call_after_audio = AsyncMock(return_value=True)

    engine._schedule_terminal_fallback(
        "call-timeout",
        reason="local:farewell",
        timeout_sec=0,
        call_outcome="agent_hangup",
        clear_local_farewell_pending=True,
    )
    await asyncio.sleep(1.05)

    assert "call-timeout" not in engine._local_tts_farewell_pending
    engine._terminate_call_after_audio.assert_awaited_once_with(
        "call-timeout",
        reason="local:farewell:fallback_timeout",
        call_outcome="agent_hangup",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [
    pytest.param("done", id="completed-stopped-or-canceled"),
    "failed",
    pytest.param("", id="missing-state"),
])
async def test_asterisk_farewell_waits_for_its_exact_playback_state(state):
    engine = Engine.__new__(Engine)
    engine._ari_playback_waiters = {}
    playback_ids = []

    async def play_media(channel_id, media_uri, playback_id):
        assert (channel_id, media_uri) == ("caller", "sound:goodbye")
        # The waiter must exist before the ARI request, since short audio can
        # complete before the request itself returns.
        assert playback_id in engine._ari_playback_waiters
        playback_ids.append(playback_id)
        await engine._on_playback_finished(
            {"playback": {"id": "older-playback", "state": "done"}}
        )
        assert not engine._ari_playback_waiters[playback_id].done()
        await engine._on_playback_finished(
            {"playback": {"id": playback_id, "state": state}}
        )
        return True

    engine.ari_client = types.SimpleNamespace(
        play_media_on_channel_with_id=play_media
    )
    engine.playback_manager = types.SimpleNamespace(
        on_playback_finished=AsyncMock()
    )

    assert await engine._play_local_asterisk_farewell("call", "caller") == (state or "unknown")
    assert playback_ids[0].startswith("local-farewell-")
    assert not engine._ari_playback_waiters


@pytest.mark.asyncio
@pytest.mark.parametrize("started,expected", [(False, "start_failed"), (True, "timeout")])
async def test_asterisk_farewell_unconfirmed_playback_cleans_waiter(started, expected):
    engine = Engine.__new__(Engine)
    engine._ari_playback_waiters = {}
    engine.ari_client = types.SimpleNamespace(
        play_media_on_channel_with_id=AsyncMock(return_value=started)
    )

    assert await engine._play_local_asterisk_farewell(
        "call", "caller", timeout_sec=0.01
    ) == expected
    assert not engine._ari_playback_waiters


@pytest.mark.asyncio
async def test_old_local_audio_done_cannot_preempt_asterisk_farewell(monkeypatch):
    engine = _build_engine()
    call_id = "call-farewell-race"
    session = CallSession(call_id=call_id, caller_channel_id="caller")
    session.provider_name = "local"
    session.last_transcript = "goodbye"
    await engine.session_store.upsert_call(session)
    engine._terminate_call_after_audio = AsyncMock(return_value=True)
    log = Mock()
    monkeypatch.setattr("src.engine.logger", log)

    async def stale_audio_done():
        await engine.on_provider_event(
            {"type": "AgentAudioDone", "call_id": call_id, "streaming_done": True}
        )
        engine._terminate_call_after_audio.assert_not_awaited()

    async def execute_hangup(**kwargs):
        assert call_id in engine._local_asterisk_farewell_pending
        session.cleanup_after_tts = True
        await engine.session_store.upsert_call(session)
        await stale_audio_done()
        return {"status": "success", "will_hangup": True}

    async def play_media(channel_id, media_uri, playback_id):
        assert (channel_id, media_uri) == ("caller", "sound:goodbye")
        await stale_audio_done()
        await engine._on_playback_finished(
            {"playback": {"id": playback_id, "state": "done"}}
        )
        return True

    engine._execute_provider_tool = execute_hangup
    engine.ari_client.play_media_on_channel_with_id = play_media
    await engine.on_provider_event(
        {"type": "ToolCall", "call_id": call_id,
         "tool_calls": [{"id": "hangup-1", "name": "hangup_call", "parameters": {}}]}
    )

    engine._terminate_call_after_audio.assert_awaited_once_with(
        call_id, reason="local_asterisk_farewell", call_outcome="agent_hangup",
        audio_already_drained=True,
    )
    assert call_id not in engine._local_asterisk_farewell_pending
    # The real ARI payload is also "done" for stopped/canceled playback.
    # A terminal event must never be promoted to proof of audible delivery.
    assert any(args.args[0] == "Asterisk goodbye playback finished" for args in log.info.call_args_list)
    assert not any("Goodbye sound played" in str(args) for args in log.info.call_args_list)


@pytest.mark.asyncio
async def test_cancelled_asterisk_farewell_wait_cleans_exact_id():
    engine = _build_engine()
    called = asyncio.Event()

    async def start(*args):
        called.set()
        return True

    engine.ari_client.play_media_on_channel_with_id = start
    task = asyncio.create_task(engine._play_local_asterisk_farewell("call", "caller"))
    await asyncio.wait_for(called.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not engine._ari_playback_waiters


@pytest.mark.asyncio
async def test_stalled_asterisk_farewell_start_is_cancelled_and_cleans_waiter():
    engine = _build_engine()
    cancelled = asyncio.Event()

    async def stalled_start(*args):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    engine.ari_client.play_media_on_channel_with_id = stalled_start
    state = await asyncio.wait_for(engine._play_local_asterisk_farewell(
        "call", "caller", start_timeout_sec=0.01,
    ), timeout=1)
    assert state == "start_timeout"
    assert cancelled.is_set()
    assert not engine._ari_playback_waiters


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_rejected_or_failed_local_hangup_releases_reservation(raises):
    engine = _build_engine()
    session = CallSession(call_id="call", caller_channel_id="caller", provider_name="local")
    session.last_transcript = "goodbye"
    await engine.session_store.upsert_call(session)

    async def reject(**kwargs):
        assert engine._local_asterisk_farewell_pending["call"] == 1
        if raises:
            raise RuntimeError("tool failed")
        return {"status": "error", "message": "blocked"}

    engine._execute_provider_tool = reject
    engine._play_local_asterisk_farewell = AsyncMock()
    engine._terminate_call_after_audio = AsyncMock()
    await engine.on_provider_event({
        "type": "ToolCall", "call_id": "call",
        "tool_calls": [{"id": "one", "name": "hangup_call", "parameters": {}}],
    })
    assert not engine._local_asterisk_farewell_pending
    engine._play_local_asterisk_farewell.assert_not_awaited()
    engine._terminate_call_after_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_claim_during_asterisk_farewell_prevents_terminal_hangup():
    engine = _build_engine()
    session = CallSession(call_id="call", caller_channel_id="caller", provider_name="local")
    session.last_transcript = "goodbye"
    await engine.session_store.upsert_call(session)

    async def hangup(**kwargs):
        session.cleanup_after_tts = True
        return {"status": "success", "will_hangup": True}

    async def play(channel_id, media_uri, playback_id):
        session.pending_deferred_transfer = {"id": "pending", "kind": "transfer"}
        await engine._on_playback_finished({"playback": {"id": playback_id, "state": "done"}})
        return True

    engine._execute_provider_tool = hangup
    engine.ari_client.play_media_on_channel_with_id = play
    engine.ari_client.hangup_channel = AsyncMock()
    await engine.on_provider_event({
        "type": "ToolCall", "call_id": "call",
        "tool_calls": [{"id": "one", "name": "hangup_call", "parameters": {}}],
    })
    engine.ari_client.hangup_channel.assert_not_awaited()
    assert not engine._local_asterisk_farewell_pending
    assert not engine._ari_playback_waiters


@pytest.mark.asyncio
async def test_overlapping_hangup_is_coalesced_into_first_farewell():
    engine = _build_engine()
    call_id = "call-overlapping-farewell"
    session = CallSession(call_id=call_id, caller_channel_id="caller")
    session.provider_name = "local"
    session.last_transcript = "goodbye"
    await engine.session_store.upsert_call(session)
    engine._terminate_call_after_audio = AsyncMock(return_value=True)
    playback_started = asyncio.Event()
    playback_ids = []
    executed = []

    async def execute_hangup(**kwargs):
        executed.append(kwargs["function_call_id"])
        if kwargs["function_call_id"] == "first-hangup":
            session.cleanup_after_tts = True
            await engine.session_store.upsert_call(session)
            return {"status": "success", "will_hangup": True}
        # Without coalescing, this would start a second farewell and race the
        # first terminal hangup.
        return {"status": "success", "will_hangup": True}

    async def play_media(channel_id, media_uri, playback_id):
        assert (channel_id, media_uri) == ("caller", "sound:goodbye")
        playback_ids.append(playback_id)
        playback_started.set()
        return True

    def tool_event(tool_id):
        return {
            "type": "ToolCall", "call_id": call_id,
            "tool_calls": [{"id": tool_id, "name": "hangup_call", "parameters": {}}],
        }

    engine._execute_provider_tool = execute_hangup
    engine.ari_client.play_media_on_channel_with_id = play_media
    first = asyncio.create_task(engine.on_provider_event(tool_event("first-hangup")))
    await asyncio.wait_for(playback_started.wait(), timeout=1)
    await engine.on_provider_event(tool_event("second-hangup"))

    assert engine._local_asterisk_farewell_pending[call_id] == 1
    assert executed == ["first-hangup"]
    assert len(playback_ids) == 1
    await engine.on_provider_event(
        {"type": "AgentAudioDone", "call_id": call_id, "streaming_done": True}
    )
    engine._terminate_call_after_audio.assert_not_awaited()

    await engine._on_playback_finished(
        {"playback": {"id": playback_ids[0], "state": "done"}}
    )
    await asyncio.wait_for(first, timeout=1)
    engine._terminate_call_after_audio.assert_awaited_once_with(
        call_id, reason="local_asterisk_farewell", call_outcome="agent_hangup",
        audio_already_drained=True,
    )
    assert call_id not in engine._local_asterisk_farewell_pending


@pytest.mark.asyncio
@pytest.mark.parametrize("playback_state", ["done", "failed", "timeout", "start_timeout"])
async def test_asterisk_farewell_records_agent_outcome_before_terminal_cleanup(playback_state):
    engine = _build_engine()
    session = CallSession(call_id="call-outcome", caller_channel_id="caller", provider_name="local")
    session.last_transcript = "goodbye"
    await engine.session_store.upsert_call(session)

    async def hangup(**kwargs):
        session.cleanup_after_tts = True
        return {"status": "success", "will_hangup": True}

    engine._execute_provider_tool = hangup
    engine._play_local_asterisk_farewell = AsyncMock(return_value=playback_state)
    engine.ari_client.hangup_channel = AsyncMock()
    await engine.on_provider_event({
        "type": "ToolCall", "call_id": session.call_id,
        "tool_calls": [{"id": "hangup", "name": "hangup_call", "parameters": {}}],
    })
    engine.ari_client.hangup_channel.assert_awaited_once_with("caller")
    current = await engine.session_store.get_by_call_id(session.call_id)
    assert current.call_outcome == "agent_hangup"
    assert not current.cleanup_after_tts
    assert not engine._local_asterisk_farewell_pending
