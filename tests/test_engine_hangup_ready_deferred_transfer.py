"""HangupReady must commit a pending deferred transfer before hanging up (#675)."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from src.config import GoogleProviderConfig, GrokProviderConfig, OpenAIRealtimeProviderConfig
from src.providers.google_live import GoogleLiveProvider
from src.providers.grok import GrokProvider
from src.providers.openai_realtime import OpenAIRealtimeProvider
from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine
from tests.test_deferred_transfer_commit_audio_gate import _build_engine, _armed_session


def _pending_action(action_id: str) -> dict:
    return {
        "id": action_id,
        "kind": "transfer",
        "commit_tool": "blind_transfer",
        "transfer_type": "extension",
        "target": "6000",
        "description": "Support agent",
    }


def _hangup_ready(call_id: str, reason: str = "farewell_without_tool") -> dict:
    return {
        "type": "HangupReady",
        "call_id": call_id,
        "reason": reason,
        "had_audio": True,
    }


@pytest.mark.asyncio
async def test_hangup_ready_commits_pending_deferred_transfer_before_hangup(monkeypatch):
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine._call_bg_tasks = {}
    engine._terminate_call_after_audio = AsyncMock(return_value=True)

    call_id = "call-hangup-ready-transfer"
    session = CallSession(call_id=call_id, caller_channel_id="caller")
    session.pending_deferred_transfer = _pending_action("action-1")
    await engine.session_store.upsert_call(session)

    committed = []

    async def fake_commit(target_call_id, target_session=None, *, expected_action_id=None):
        committed.append((target_call_id, expected_action_id))
        return {"status": "success"}

    monkeypatch.setattr(engine, "_commit_pending_deferred_transfer_for_call", fake_commit)

    await engine.on_provider_event(_hangup_ready(call_id))

    # The commit is scheduled as a fire-and-forget task; let it run.
    await asyncio.wait_for(asyncio.gather(*list(engine._call_bg_tasks[call_id])), timeout=1)

    assert committed == [(call_id, "action-1")]
    engine._terminate_call_after_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_hangup_ready_hangs_up_when_no_transfer_is_pending():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine._call_bg_tasks = {}
    engine._terminate_call_after_audio = AsyncMock(return_value=True)

    call_id = "call-hangup-ready-plain"
    session = CallSession(call_id=call_id, caller_channel_id="caller")
    await engine.session_store.upsert_call(session)

    await engine.on_provider_event(_hangup_ready(call_id))

    engine._terminate_call_after_audio.assert_awaited_once_with(
        call_id,
        reason="hangup_ready:farewell_without_tool",
        call_outcome="agent_hangup",
        audio_already_drained=False,
    )


@pytest.mark.asyncio
async def test_hangup_ready_transfer_timeout_keeps_recovered_call_open(monkeypatch):
    engine = _build_engine()
    call_id = 'review-hangup-timeout'
    session = await _armed_session(engine, call_id)
    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=engine.on_provider_event)
    provider._call_id = call_id
    provider._session_store = engine.session_store
    provider._hangup_fallback_armed = True
    provider._hangup_fallback_emitted = True
    provider._hangup_ready_emitted = True
    engine._call_providers[call_id] = provider
    # Google fallback sets this before it emits HangupReady.
    session.cleanup_after_tts = True
    await engine.session_store.upsert_call(session)
    engine.ari_client.hangup_channel = AsyncMock(return_value=True)
    monkeypatch.setattr(engine, "_wait_for_call_audio_drain", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, '_play_deferred_transfer_local_handoff', AsyncMock(return_value=False))
    monkeypatch.setattr(engine, '_wait_for_deferred_transfer_audio_drain', AsyncMock(return_value=False))
    monkeypatch.setattr(engine.streaming_playback_manager, 'stop_streaming_playback', AsyncMock(return_value=True))
    engine.streaming_playback_manager.continuous_stream = False
    spoken = []
    async def speak(target_call_id, text, kind):
        spoken.append((text, kind))
        # The provider completes the recovery apology via the normal event path.
        await engine.on_provider_event({'type': 'AgentAudioDone', 'call_id': target_call_id, 'streaming_done': True})
        return True
    monkeypatch.setattr(engine, '_speak_no_input_announcement', speak)
    await engine.on_provider_event({'type': 'HangupReady', 'call_id': call_id, 'reason': 'fallback_no_audio', 'had_audio': False})
    await asyncio.wait_for(asyncio.gather(*list(engine._call_bg_tasks[call_id])), timeout=2)
    recovered = await engine.session_store.get_by_call_id(call_id)
    assert recovered.pending_deferred_transfer is None
    assert recovered.audio_capture_enabled is True
    assert spoken and spoken[0][1] == 'deferred_transfer_timeout'
    engine.ari_client.hangup_channel.assert_not_awaited()
    assert recovered.cleanup_after_tts is False
    assert not provider._hangup_fallback_armed
    assert not provider._hangup_fallback_emitted
    assert not provider._hangup_ready_emitted
    # Recovery must still permit a later, independent hangup.
    await engine.on_provider_event(_hangup_ready(call_id))
    engine.ari_client.hangup_channel.assert_awaited_once_with(session.caller_channel_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_before_drain", [True, False])
async def test_terminal_hangup_yields_to_transfer_before_or_during_drain(monkeypatch, pending_before_drain):
    engine = _build_engine()
    call_id = "call-terminal-transfer-race"
    session = CallSession(call_id=call_id, caller_channel_id="caller")
    if pending_before_drain:
        session.pending_deferred_transfer = _pending_action("transfer-1")
    await engine.session_store.upsert_call(session)
    engine.ari_client.hangup_channel = AsyncMock()
    async def drain(*args, **kwargs):
        session.pending_deferred_transfer = _pending_action("transfer-1")
        await engine.session_store.upsert_call(session)
        return True
    monkeypatch.setattr(engine, "_wait_for_call_audio_drain", drain)
    assert not await engine._terminate_call_after_audio(call_id, reason="test")
    engine.ari_client.hangup_channel.assert_not_awaited()
    assert call_id not in engine._terminal_hangup_started


@pytest.mark.asyncio
async def test_duplicate_hangup_ready_commits_transfer_once(monkeypatch):
    from src.tools.telephony import deferred_transfer
    engine = _build_engine()
    call_id = "call-duplicate-hangup"
    session = await _armed_session(engine, call_id)
    session.cleanup_after_tts = True
    await engine.session_store.upsert_call(session)
    engine.ari_client.hangup_channel = AsyncMock()
    monkeypatch.setattr(engine, "_play_deferred_transfer_local_handoff", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", AsyncMock(return_value=True))
    commit = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(deferred_transfer, "commit_deferred_transfer_action", commit)
    await asyncio.gather(engine.on_provider_event(_hangup_ready(call_id)), engine.on_provider_event(_hangup_ready(call_id)))
    await asyncio.gather(*list(engine._call_bg_tasks.get(call_id, [])))
    commit.assert_awaited_once()
    updated = await engine.session_store.get_by_call_id(call_id)
    assert updated.pending_deferred_transfer is None
    assert not updated.cleanup_after_tts
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class, config_class", [
    (GrokProvider, GrokProviderConfig),
    (OpenAIRealtimeProvider, OpenAIRealtimeProviderConfig),
])
async def test_transfer_release_cancels_provider_farewell_timer(provider_class, config_class):
    provider = provider_class(config=config_class(), on_event=None)
    provider._hangup_after_response = True
    provider._farewell_response_id = "old-farewell"
    provider._farewell_waiting_for_audio_done = True
    provider._start_farewell_timeout()
    timeout = provider._farewell_timeout_task
    provider.release_terminal_output_protection()
    await asyncio.gather(timeout, return_exceptions=True)
    assert timeout.cancelled()
    assert provider._farewell_response_id is None
    assert not provider._farewell_waiting_for_audio_done
    assert not provider._hangup_after_response


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("ARI unavailable"), {"status": "error"}, None])
async def test_failed_transfer_recovers_without_replay_and_allows_later_hangup(monkeypatch, failure):
    from src.tools.telephony import deferred_transfer
    engine = _build_engine()
    call_id = "call-transfer-failure"
    session = await _armed_session(engine, call_id)
    session.cleanup_after_tts = True
    session.pending_deferred_transfer["_tool_history_origin"] = {
        "tool_call_id": "transfer-tool", "name": "blind_transfer", "params": {},
    }
    await engine.session_store.upsert_call(session)
    engine.ari_client.hangup_channel = AsyncMock(return_value=True)
    monkeypatch.setattr(engine, "_wait_for_call_audio_drain", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_play_deferred_transfer_local_handoff", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", AsyncMock(return_value=True))
    monkeypatch.setattr(engine.streaming_playback_manager, "stop_streaming_playback", AsyncMock(return_value=True))
    speak = AsyncMock(return_value=True)
    monkeypatch.setattr(engine, "_speak_no_input_announcement", speak)
    commit = AsyncMock(side_effect=failure) if isinstance(failure, Exception) else AsyncMock(return_value=failure)
    monkeypatch.setattr(deferred_transfer, "commit_deferred_transfer_action", commit)
    await engine.on_provider_event(_hangup_ready(call_id))
    await asyncio.gather(*list(engine._call_bg_tasks.get(call_id, [])))
    updated = await engine.session_store.get_by_call_id(call_id)
    assert not updated.cleanup_after_tts
    assert updated.pending_deferred_transfer is None
    assert updated.audio_capture_enabled
    assert updated.tool_calls[-1]["result"] == "cancelled"
    speak.assert_awaited_once_with(
        call_id, "I'm sorry, I couldn't complete that transfer. How else can I help?",
        "deferred_transfer_commit_failed",
    )
    engine.ari_client.hangup_channel.assert_not_awaited()
    # Apology completion cannot retry the failed action, and a fresh farewell works.
    await engine.on_provider_event({"type": "AgentAudioDone", "call_id": call_id, "streaming_done": True})
    await engine.on_provider_event(_hangup_ready(call_id))
    commit.assert_awaited_once()
    engine.ari_client.hangup_channel.assert_awaited_once_with(session.caller_channel_id)


@pytest.mark.asyncio
async def test_provider_release_failure_does_not_abort_transfer(monkeypatch):
    from src.tools.telephony import deferred_transfer
    engine = _build_engine()
    call_id = "call-release-error"
    session = await _armed_session(engine, call_id)
    provider = Mock()
    provider.release_terminal_output_protection.side_effect = RuntimeError("reset failed")
    engine._call_providers[call_id] = provider
    monkeypatch.setattr(engine, "_play_deferred_transfer_local_handoff", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", AsyncMock(return_value=True))
    commit = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(deferred_transfer, "commit_deferred_transfer_action", commit)
    assert await engine._commit_pending_deferred_transfer_for_call(call_id, session) == {"status": "success"}
    commit.assert_awaited_once()
    assert session.pending_deferred_transfer is None


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [True, False])
async def test_failed_commit_does_not_restore_or_cancel_replacement_action(monkeypatch, raises):
    from src.tools.telephony import deferred_transfer
    engine = _build_engine()
    call_id = "call-replaced-action"
    session = await _armed_session(engine, call_id)
    replacement = _pending_action("replacement")
    monkeypatch.setattr(engine, "_play_deferred_transfer_local_handoff", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", AsyncMock(return_value=True))
    speak = AsyncMock()
    monkeypatch.setattr(engine, "_speak_no_input_announcement", speak)
    async def replace_then_fail(*args):
        session.pending_deferred_transfer = replacement
        await engine.session_store.upsert_call(session)
        if raises:
            raise RuntimeError("old commit failed")
        return {"status": "error"}
    monkeypatch.setattr(deferred_transfer, "commit_deferred_transfer_action", replace_then_fail)
    await engine._commit_pending_deferred_transfer_for_call(call_id, session)
    assert session.pending_deferred_transfer == replacement
    speak.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_recovery_revokes_hangup_already_draining(monkeypatch):
    engine = _build_engine()
    call_id = "call-inflight-hangup"
    session = CallSession(call_id=call_id, caller_channel_id="caller")
    session.cleanup_after_tts = True
    await engine.session_store.upsert_call(session)
    engine.ari_client.hangup_channel = AsyncMock()
    draining = asyncio.Event()
    release_drain = asyncio.Event()
    async def drain(*args, **kwargs):
        draining.set()
        await release_drain.wait()
        return True
    monkeypatch.setattr(engine, "_wait_for_call_audio_drain", drain)
    monkeypatch.setattr(engine, "_play_deferred_transfer_local_handoff", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", AsyncMock(return_value=False))
    monkeypatch.setattr(engine.streaming_playback_manager, "stop_streaming_playback", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_speak_no_input_announcement", AsyncMock(return_value=True))
    old_hangup = asyncio.create_task(engine._terminate_call_after_audio(call_id, reason="old-fallback"))
    await asyncio.wait_for(draining.wait(), timeout=1)
    session.pending_deferred_transfer = _pending_action("transfer-1")
    await engine.session_store.upsert_call(session)
    await engine.on_provider_event(_hangup_ready(call_id))
    await asyncio.wait_for(asyncio.gather(*list(engine._call_bg_tasks[call_id])), timeout=1)
    assert (await engine.session_store.get_by_call_id(call_id)).pending_deferred_transfer is None
    release_drain.set()
    assert not await asyncio.wait_for(old_hangup, timeout=1)
    engine.ari_client.hangup_channel.assert_not_awaited()
