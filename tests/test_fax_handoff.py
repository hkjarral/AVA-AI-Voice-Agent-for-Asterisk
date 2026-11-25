"""Caller ownership when Asterisk redirects an inbound call to ReceiveFAX."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import AppConfig
from src.core.fax_handoff import fax_dialplan
from src.core.models import CallSession
from src.engine import Engine, _cleanup_completed_at, _cleanup_in_progress


def channel(call_id, *, context="ava", extension="fax", application="Stasis"):
    return {"id": call_id, "dialplan": {
        "context": context, "exten": extension, "app_name": application,
    }}


@pytest.mark.parametrize("snapshot", [
    channel("caller"),
    channel("caller", context="ext-fax", extension="s", application="Set"),
    channel("caller", context="custom", extension="s", application="ReceiveFAX"),
])
def test_fax_redirect_event_and_live_receiver(snapshot):
    assert fax_dialplan(snapshot, stasis_ended=True)


@pytest.mark.parametrize("snapshot", [
    {}, {"dialplan": []}, {"dialplan": {"context": None}},
    channel("caller", extension="s"),
    channel("caller", context="ext-fax-unrelated", extension="s"),
    channel("caller", context="ext-faxlike", extension="s", application="Set"),
])
def test_unconfirmed_live_snapshots_are_not_a_fax_handoff(snapshot):
    assert fax_dialplan(snapshot, stasis_ended=False) is None


@pytest.fixture
def engine():
    engine = Engine(AppConfig(
        default_provider="local", providers={"local": {"enabled": True}},
        asterisk={"host": "127.0.0.1", "port": 8088, "username": "test", "password": "test",
                  "app_name": "asterisk-ai-voice-agent"},
        llm={"initial_greeting": "", "prompt": "test", "model": "test"},
        audio_transport="audiosocket",
    ))
    engine.ari_client.send_command = AsyncMock(return_value={"status": 404})
    engine.ari_client.hangup_channel = AsyncMock(return_value=True)
    engine.ari_client.destroy_bridge = AsyncMock(return_value=True)
    engine._persist_call_history = AsyncMock()
    engine._execute_post_call_tools = AsyncMock()
    engine._stop_call_provider_instance = AsyncMock()
    return engine


async def call(engine, name):
    _cleanup_completed_at.pop(name, None)
    _cleanup_in_progress.discard(name)
    session = CallSession(call_id=name, caller_channel_id=name, bridge_id="bridge-" + name)
    session.audiosocket_channel_id = "media-" + name
    await engine.session_store.upsert_call(session)
    engine._call_providers[name] = SimpleNamespace()
    return session


@pytest.mark.asyncio
async def test_fax_stasis_end_cleans_media_and_preserves_receiver(engine):
    session = await call(engine, "fax-receiver")
    await engine._handle_stasis_end({"channel": channel(session.call_id)})

    engine.ari_client.hangup_channel.assert_awaited_once_with(session.audiosocket_channel_id)
    engine.ari_client.destroy_bridge.assert_awaited_once_with(session.bridge_id)
    engine._stop_call_provider_instance.assert_awaited_once()
    engine._execute_post_call_tools.assert_awaited_once()
    assert session.call_outcome == "transferred"
    assert session.audio_diagnostics["dialplan_handoff"]["reason"] == "fax"
    assert await engine.session_store.get_by_call_id(session.call_id) is None
    engine.ari_client.send_command.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", [
    {"status": 404}, {"status": 500}, {},
    channel("another-caller", application="ReceiveFAX"),
    channel("normal-end", extension="s", application="Hangup"),
])
async def test_non_fax_and_failed_lookups_keep_normal_cleanup(engine, lookup):
    session = await call(engine, "normal-end")
    engine.ari_client.send_command.return_value = lookup
    await engine._handle_stasis_end({"channel": channel(session.call_id, extension="s")})
    assert session.fax_handoff is False
    assert any(c.args == (session.call_id,) for c in engine.ari_client.hangup_channel.await_args_list)


@pytest.mark.asyncio
async def test_stale_event_snapshot_is_resolved_by_current_fax_dialplan(engine):
    session = await call(engine, "late-route")
    engine.ari_client.send_command.return_value = channel(
        session.call_id, context="ext-fax", extension="s", application="ReceiveFAX")
    await engine._handle_stasis_end({"channel": channel(session.call_id, extension="s")})
    assert session.fax_handoff is True
    engine.ari_client.hangup_channel.assert_awaited_once_with(session.audiosocket_channel_id)


@pytest.mark.asyncio
async def test_aux_cleanup_winning_first_rechecks_caller_handoff(engine):
    session = await call(engine, "race-fax")
    stopping, release = asyncio.Event(), asyncio.Event()

    async def stop(*args):
        stopping.set()
        await release.wait()

    engine._stop_call_provider_instance.side_effect = stop
    first = asyncio.create_task(engine._cleanup_call(session.audiosocket_channel_id))
    await asyncio.wait_for(stopping.wait(), 2)
    await engine._handle_stasis_end({"channel": channel(session.call_id)})
    release.set()
    await asyncio.wait_for(first, 2)
    engine.ari_client.hangup_channel.assert_awaited_once_with(session.audiosocket_channel_id)
    engine._persist_call_history.assert_awaited_once()
    assert session.call_outcome == "transferred"


@pytest.mark.asyncio
async def test_aux_end_cannot_claim_fax_ownership(engine):
    session = await call(engine, "aux-only")
    await engine._handle_stasis_end({"channel": channel(session.audiosocket_channel_id)})
    assert session.fax_handoff is False
    assert any(c.args == (session.call_id,) for c in engine.ari_client.hangup_channel.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["outbound", "vicidial"])
async def test_fax_recognition_does_not_override_external_or_outbound_ownership(engine, kind):
    session = await call(engine, "scope-" + kind)
    session.caller_stasis_ended = True
    session.is_outbound = kind == "outbound"
    session.external_platform = "vicidial" if kind == "vicidial" else None
    assert engine._note_fax_handoff(session, channel(session.call_id), source="stasis_end") is False
    assert await engine._refresh_fax_handoff(session) is False
    engine.ari_client.send_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_callback_yields_to_confirmed_fax_handoff(engine):
    session = await call(engine, "terminal-fax")
    session.caller_stasis_ended = True
    engine._note_fax_handoff(session, channel(session.call_id), source="stasis_end")
    assert await engine._terminate_call_after_audio(session.call_id, reason="late-provider") is False
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_stop_callback_cannot_hang_up_receiver(engine):
    session = await call(engine, "provider-stop-fax")

    async def stop(*args):
        await engine.on_provider_event({"type": "ProviderDisconnected", "call_id": session.call_id})

    engine._stop_call_provider_instance.side_effect = stop
    await engine._handle_stasis_end({"channel": channel(session.call_id)})
    engine.ari_client.hangup_channel.assert_awaited_once_with(session.audiosocket_channel_id)


@pytest.mark.asyncio
async def test_slow_lookup_is_bounded_and_does_not_claim_handoff(engine):
    session = await call(engine, "slow-probe")
    cancelled = asyncio.Event()

    async def slow(*args, **kwargs):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    engine.ari_client.send_command.side_effect = slow
    assert await asyncio.wait_for(engine._refresh_fax_handoff(session), 1) is False
    assert cancelled.is_set()
    assert session.fax_handoff is False


@pytest.mark.asyncio
async def test_lookup_cancellation_propagates(engine):
    session = await call(engine, "cancel-probe")
    engine.ari_client.send_command.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await engine._refresh_fax_handoff(session)
