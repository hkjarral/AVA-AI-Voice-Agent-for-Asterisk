import asyncio
import types
from unittest.mock import AsyncMock

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
@pytest.mark.parametrize("state", ["done", "failed", "stopped"])
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

    assert await engine._play_local_asterisk_farewell("call", "caller") == state
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
async def test_old_local_audio_done_cannot_preempt_asterisk_farewell():
    engine = _build_engine()
    call_id = "call-farewell-race"
    session = CallSession(call_id=call_id, caller_channel_id="caller")
    session.provider_name = "local"
    session.last_transcript = "goodbye"
    await engine.session_store.upsert_call(session)
    engine._terminate_call_after_audio = AsyncMock(return_value=True)

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
        call_id, reason="local_asterisk_farewell", audio_already_drained=True
    )
    assert call_id not in engine._local_asterisk_farewell_pending


@pytest.mark.asyncio
async def test_overlapping_blocked_tool_call_cannot_release_first_farewell():
    engine = _build_engine()
    call_id = "call-overlapping-farewell"
    session = CallSession(call_id=call_id, caller_channel_id="caller")
    session.provider_name = "local"
    session.last_transcript = "goodbye"
    await engine.session_store.upsert_call(session)
    engine._terminate_call_after_audio = AsyncMock(return_value=True)
    playback_started = asyncio.Event()
    playback_ids = []

    async def execute_hangup(**kwargs):
        if kwargs["function_call_id"] == "first-hangup":
            session.cleanup_after_tts = True
            await engine.session_store.upsert_call(session)
            return {"status": "success", "will_hangup": True}
        # A concurrent tool call is rejected while the first farewell plays.
        return {"status": "blocked", "will_hangup": False}

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
    await engine.on_provider_event(
        {"type": "AgentAudioDone", "call_id": call_id, "streaming_done": True}
    )
    engine._terminate_call_after_audio.assert_not_awaited()

    await engine._on_playback_finished(
        {"playback": {"id": playback_ids[0], "state": "done"}}
    )
    await asyncio.wait_for(first, timeout=1)
    engine._terminate_call_after_audio.assert_awaited_once_with(
        call_id, reason="local_asterisk_farewell", audio_already_drained=True
    )
    assert call_id not in engine._local_asterisk_farewell_pending
