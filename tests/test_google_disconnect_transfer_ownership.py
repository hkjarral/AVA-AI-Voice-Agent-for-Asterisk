"""A dead Google socket must not take routing from an unfinished transfer."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.engine import Engine
from tests.test_google_long_audio_playback import bare_engine, provider


@pytest.mark.parametrize("decision", ["accepted", "declined"])
async def test_decision_retains_owner_until_bridge_or_abort_cleanup(decision):
    engine, session = bare_engine(provider(enabled=False))
    session.current_action = {"type": "attended_transfer", "decision": decision}
    session.provider_session_active = True
    await engine.session_store.upsert_call(session)
    await engine.on_provider_event({"type": "ProviderDisconnected", "call_id": session.call_id, "code": 1006})
    worker = next(iter(engine._call_bg_tasks[session.call_id]))
    try:
        await asyncio.sleep(0.02)
        engine._terminate_call_after_audio.assert_not_awaited()
        assert session.provider_session_active
        if decision == "accepted":
            session.transfer_destination = "synthetic-destination"
        else:
            session.current_action = None
        await asyncio.wait_for(worker, 1)
        assert engine._terminate_call_after_audio.await_count == int(decision == "declined")
    finally:
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.parametrize("routed", [False, True])
@pytest.mark.parametrize("owner", ["deferred", "predial"])
async def test_disconnect_observer_survives_until_routing_outcome(routed, owner):
    engine, session = bare_engine(provider(enabled=False))
    if owner == "deferred":
        session.pending_deferred_transfer = {"id": "synthetic-action"}
    else:
        session.current_action = {"type": "predial_transfer", "answered": False}
        assert session.pending_deferred_transfer is None
    session.provider_session_active = True
    await engine.session_store.upsert_call(session)
    await engine.on_provider_event({"type": "ProviderDisconnected", "call_id": session.call_id, "code": 1006})
    worker = next(iter(engine._call_bg_tasks[session.call_id]))
    try:
        await asyncio.sleep(0.02)
        engine._terminate_call_after_audio.assert_not_awaited()
        assert session.provider_session_active
        assert not worker.done()
        session.pending_deferred_transfer = None
        session.current_action = None
        session.transfer_active = routed
        await asyncio.wait_for(worker, 1)
        assert engine._terminate_call_after_audio.await_count == int(not routed)
        assert session.provider_session_active is routed
    finally:
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.parametrize("phase", ["before", "during"])
@pytest.mark.parametrize("action", [
    {"type": "attended_transfer", "decision": "accepted"},
    {"type": "attended_transfer", "decision": "declined"},
    {"type": "predial_transfer", "answered": False},
])
async def test_terminal_drain_cannot_override_transfer_owner(phase, action):
    engine, session = bare_engine(provider())
    await engine.session_store.upsert_call(session)
    engine._terminate_call_after_audio = Engine._terminate_call_after_audio.__get__(engine)
    engine.ari_client = SimpleNamespace(hangup_channel=AsyncMock())

    async def drain(*args, **kwargs):
        session.current_action = dict(action)
        return True

    engine._wait_for_call_audio_drain = AsyncMock(side_effect=drain)
    if phase == "before":
        session.current_action = dict(action)
    assert not await engine._terminate_call_after_audio(session.call_id, reason="google_provider_disconnected", drain_timeout_cap_sec=8)
    engine.ari_client.hangup_channel.assert_not_awaited()


async def test_deferred_owner_wait_is_bounded_without_revoking_routing():
    engine, session = bare_engine(provider(enabled=False))
    session.pending_deferred_transfer = {"id": "synthetic-stalled-action"}
    session.provider_session_active = True
    engine._google_disconnect_transfer_wait_sec = lambda: 0.01
    await engine.session_store.upsert_call(session)
    await asyncio.wait_for(engine._finish_google_provider_disconnect(session.call_id), 0.5)
    engine._terminate_call_after_audio.assert_not_awaited()
    assert session.pending_deferred_transfer
    assert session.provider_session_active
