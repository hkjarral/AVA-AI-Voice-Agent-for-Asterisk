"""Provider audio must not reach the caller while a deferred transfer commits (#701)."""
import asyncio

import pytest

from src.config import AppConfig
from src.core.models import CallSession
from src.engine import Engine


def _build_engine() -> Engine:
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {"initial_greeting": "hi", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"local_only": {}},
        "active_pipeline": "local_only",
        "audio_transport": "audiosocket",
        "audiosocket": {"host": "127.0.0.1", "port": 9092, "format": "ulaw"},
        "tools": {
            "transfer": {
                "destinations": {
                    "support_agent": {
                        "type": "extension",
                        "target": "6000",
                        "description": "Support agent",
                    }
                }
            },
        },
    }
    return Engine(AppConfig(**config_data))


def _pending_action(action_id: str) -> dict:
    return {
        "id": action_id,
        "kind": "transfer",
        "commit_tool": "blind_transfer",
        "transfer_type": "extension",
        "target": "6000",
        "description": "Support agent",
    }


async def _armed_session(engine: Engine, call_id: str) -> CallSession:
    session = CallSession(
        call_id=call_id,
        caller_channel_id=f"caller-{call_id}",
        context_name="support",
    )
    session.pending_deferred_transfer = _pending_action(f"action-{call_id}")
    await engine.session_store.upsert_call(session)
    return session


def _record_stream_starts(engine: Engine, monkeypatch) -> list:
    """Capture every provider stream the engine opens toward the caller."""
    streams = []

    async def fake_start_streaming_playback(call_id, audio_chunks, *args, **kwargs):
        streams.append((call_id, audio_chunks))
        return f"stream-{len(streams)}"

    async def fake_note_output_end(*args, **kwargs):
        return None

    engine.streaming_playback_manager.continuous_stream = False
    monkeypatch.setattr(
        engine.streaming_playback_manager,
        "start_streaming_playback",
        fake_start_streaming_playback,
    )
    monkeypatch.setattr(engine, "_note_provider_output_end", fake_note_output_end)
    return streams


def _mark_committing(engine: Engine, call_id: str, action_id) -> dict:
    """Register a commit in flight for action_id, as the commit wrapper does."""
    marker = {"action_id": action_id}
    engine._deferred_transfer_committing.setdefault(call_id, []).append(marker)
    return marker


def _agent_audio(call_id: str, data: bytes = b"\x7f" * 320) -> dict:
    return {
        "type": "AgentAudio",
        "call_id": call_id,
        "data": data,
        "encoding": "ulaw",
        "sample_rate": 8000,
    }


@pytest.mark.asyncio
async def test_commit_audio_gate_requires_committing_marker_and_pending_action():
    engine = _build_engine()
    armed = await _armed_session(engine, "call-gate")
    other = await _armed_session(engine, "call-gate-other")
    cleared = CallSession(call_id="call-gate", caller_channel_id="caller-call-gate")
    cleared.pending_deferred_transfer = None

    # Armed, but the commit has not started: the handoff line must play.
    assert engine._provider_audio_blocked_by_transfer_commit("call-gate", armed) is False

    # A commit attempt that has not read its action yet blocks nothing.
    unstamped = _mark_committing(engine, "call-gate", None)
    assert engine._provider_audio_blocked_by_transfer_commit("call-gate", armed) is False

    unstamped["action_id"] = armed.pending_deferred_transfer["id"]
    assert engine._provider_audio_blocked_by_transfer_commit("call-gate", armed) is True
    # Only the committing call is affected.
    assert engine._provider_audio_blocked_by_transfer_commit(other.call_id, other) is False
    # The drain-timeout recovery clears the action before its apology.
    assert engine._provider_audio_blocked_by_transfer_commit("call-gate", cleared) is False
    assert engine._provider_audio_blocked_by_transfer_commit("call-gate", None) is False


@pytest.mark.asyncio
async def test_commit_marker_is_set_during_commit_and_cleared_after(monkeypatch):
    engine = _build_engine()
    session = await _armed_session(engine, "call-marker")
    observed = []

    async def fake_inner(call_id, target_session=None, *, commit_marker=None):
        commit_marker["action_id"] = session.pending_deferred_transfer["id"]
        observed.append([dict(m) for m in engine._deferred_transfer_committing[call_id]])
        observed.append(engine._provider_audio_blocked_by_transfer_commit(call_id, session))
        return {"status": "success"}

    monkeypatch.setattr(engine, "_commit_pending_deferred_transfer_for_call_inner", fake_inner)

    result = await engine._commit_pending_deferred_transfer_for_call("call-marker", session)

    assert result == {"status": "success"}
    assert observed == [[{"action_id": "action-call-marker"}], True]
    assert "call-marker" not in engine._deferred_transfer_committing


@pytest.mark.asyncio
async def test_commit_marker_is_cleared_when_commit_raises(monkeypatch):
    engine = _build_engine()
    session = await _armed_session(engine, "call-marker-error")

    async def failing_inner(call_id, target_session=None, *, commit_marker=None):
        commit_marker["action_id"] = session.pending_deferred_transfer["id"]
        assert len(engine._deferred_transfer_committing[call_id]) == 1
        raise RuntimeError("ari unavailable")

    monkeypatch.setattr(engine, "_commit_pending_deferred_transfer_for_call_inner", failing_inner)

    with pytest.raises(RuntimeError, match="ari unavailable"):
        await engine._commit_pending_deferred_transfer_for_call("call-marker-error", session)

    assert "call-marker-error" not in engine._deferred_transfer_committing
    assert engine._provider_audio_blocked_by_transfer_commit("call-marker-error", session) is False


@pytest.mark.asyncio
async def test_overlapping_commits_keep_marker_until_the_last_one_finishes(monkeypatch):
    engine = _build_engine()
    call_id = "call-marker-overlap"
    session = await _armed_session(engine, call_id)
    release = {"first": asyncio.Event(), "second": asyncio.Event()}
    started = {"first": asyncio.Event(), "second": asyncio.Event()}
    order = iter(("first", "second"))

    async def blocking_inner(target_call_id, target_session=None, *, commit_marker=None):
        name = next(order)
        commit_marker["action_id"] = session.pending_deferred_transfer["id"]
        started[name].set()
        await release[name].wait()
        return None

    monkeypatch.setattr(engine, "_commit_pending_deferred_transfer_for_call_inner", blocking_inner)

    first = asyncio.create_task(engine._commit_pending_deferred_transfer_for_call(call_id, session))
    await asyncio.wait_for(started["first"].wait(), timeout=1)
    second = asyncio.create_task(engine._commit_pending_deferred_transfer_for_call(call_id, session))
    await asyncio.wait_for(started["second"].wait(), timeout=1)
    assert len(engine._deferred_transfer_committing[call_id]) == 2

    release["first"].set()
    await asyncio.wait_for(first, timeout=1)
    assert len(engine._deferred_transfer_committing[call_id]) == 1
    assert engine._provider_audio_blocked_by_transfer_commit(call_id, session) is True

    release["second"].set()
    await asyncio.wait_for(second, timeout=1)
    assert call_id not in engine._deferred_transfer_committing


@pytest.mark.asyncio
async def test_agent_audio_is_dropped_while_commit_is_in_progress(monkeypatch):
    engine = _build_engine()
    call_id = "call-drop"
    session = await _armed_session(engine, call_id)
    streams = _record_stream_starts(engine, monkeypatch)
    _mark_committing(engine, call_id, session.pending_deferred_transfer["id"])

    await engine.on_provider_event(_agent_audio(call_id, b"\x7f" * 320))
    await engine.on_provider_event(_agent_audio(call_id, b"\x7f" * 160))

    assert streams == []
    assert call_id not in engine._provider_stream_queues
    dropped = session.vad_state["transfer_commit_dropped"]
    assert dropped == {"chunks": 2, "bytes": 480}


@pytest.mark.asyncio
async def test_agent_audio_plays_once_the_pending_transfer_is_cleared(monkeypatch):
    engine = _build_engine()
    call_id = "call-apology"
    session = await _armed_session(engine, call_id)
    streams = _record_stream_starts(engine, monkeypatch)
    # Drain-timeout recovery: still inside the commit routine, but the action
    # has been cleared before the provider is asked to apologise.
    _mark_committing(engine, call_id, session.pending_deferred_transfer["id"])
    session.pending_deferred_transfer = None
    await engine.session_store.upsert_call(session)

    await engine.on_provider_event(_agent_audio(call_id))

    assert [stream_call_id for stream_call_id, _ in streams] == [call_id]
    assert "transfer_commit_dropped" not in session.vad_state


@pytest.mark.asyncio
async def test_replacement_transfer_keeps_its_handoff_audio(monkeypatch):
    """A pending action that replaced the one being committed must still play."""
    engine = _build_engine()
    call_id = "call-replaced"
    session = await _armed_session(engine, call_id)
    streams = _record_stream_starts(engine, monkeypatch)
    # A commit is draining for the original action...
    _mark_committing(engine, call_id, session.pending_deferred_transfer["id"])
    # ...when the transfer is cancelled and a different one is armed.
    session.pending_deferred_transfer = _pending_action("action-replacement")
    await engine.session_store.upsert_call(session)

    await engine.on_provider_event(_agent_audio(call_id))

    assert [stream_call_id for stream_call_id, _ in streams] == [call_id]
    assert "transfer_commit_dropped" not in session.vad_state


@pytest.mark.asyncio
async def test_repeated_handoff_turn_is_not_played_while_transfer_drains(monkeypatch):
    """Reproduce #701: a second model turn arrives during the drain wait."""
    engine = _build_engine()
    call_id = "call-repeat"
    session = await _armed_session(engine, call_id)
    streams = _record_stream_starts(engine, monkeypatch)
    drain_started = asyncio.Event()
    release_drain = asyncio.Event()
    committed = []

    async def no_local_handoff(*args, **kwargs):
        return False

    async def blocked_drain(target_call_id):
        drain_started.set()
        await release_drain.wait()
        return True

    async def fake_commit(context):
        committed.append(context.call_id)
        latest = await engine.session_store.get_by_call_id(context.call_id)
        latest.pending_deferred_transfer = None
        await engine.session_store.upsert_call(latest)
        return {"status": "success", "message": "Transferred"}

    monkeypatch.setattr(engine, "_play_deferred_transfer_local_handoff", no_local_handoff)
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", blocked_drain)
    monkeypatch.setattr(
        "src.tools.telephony.deferred_transfer.commit_pending_deferred_transfer",
        fake_commit,
    )

    # Turn 1: the handoff line after the tool call. It plays.
    await engine.on_provider_event(_agent_audio(call_id))
    assert len(streams) == 1
    await engine.on_provider_event(
        {"type": "AgentAudioDone", "call_id": call_id, "streaming_done": True}
    )
    await asyncio.wait_for(drain_started.wait(), timeout=1)

    # Turn 2: the provider answers its own tool response with the same line.
    await engine.on_provider_event(_agent_audio(call_id))
    await engine.on_provider_event(_agent_audio(call_id))

    assert len(streams) == 1
    assert session.vad_state["transfer_commit_dropped"]["chunks"] == 2

    release_drain.set()
    await asyncio.wait_for(asyncio.gather(*list(engine._call_bg_tasks[call_id])), timeout=1)

    assert committed == [call_id]
    assert call_id not in engine._deferred_transfer_committing
