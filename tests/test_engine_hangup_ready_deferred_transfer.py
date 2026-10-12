"""HangupReady must commit a pending deferred transfer before hanging up (#675)."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine


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
