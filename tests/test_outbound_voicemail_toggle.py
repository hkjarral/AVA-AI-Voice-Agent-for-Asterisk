"""Offline regressions for the campaign checkbox through AMD completion."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.core.outbound_config import voicemail_drop_enabled
from src.core.outbound_store import OutboundStore
from src.engine import Engine


@pytest.mark.parametrize("value,expected", [(None, True), (0, False), (False, False), (1, True), (True, True)])
def test_persisted_voicemail_flag(value, expected):
    assert voicemail_drop_enabled(value) is expected
    assert voicemail_drop_enabled({}.get("voicemail_drop_enabled")) is True


def _make_engine(store, meta):
    """No Engine startup, network clients, providers, or production state."""
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(asterisk=SimpleNamespace(app_name="fixture"))
    engine.outbound_store = store
    engine._outbound_extension_identity = "1000"
    engine._outbound_pbx_type = "generic"
    engine._outbound_amd_context = "fixture-amd"
    engine._outbound_attempt_meta_by_attempt_id = {meta["attempt_id"]: meta}
    engine._outbound_attempt_meta_by_channel_id = {}
    engine._outbound_attempt_amd = {}
    engine._outbound_awaiting_amd_channel_ids = set()
    engine._seen_outbound_channels = set()
    engine._outbound_forced_hangup_tasks = {}
    engine.transport_orchestrator = SimpleNamespace(get_context_config=Mock(return_value=None))
    engine.providers = {}
    engine._outbound_choose_endpoint = AsyncMock(return_value="Local/1001@fixture")
    engine._wait_for_ari_playback = AsyncMock(return_value=True)
    engine._ari_playback_waiters = {}
    engine.playback_manager = SimpleNamespace(on_playback_finished=AsyncMock())
    engine.ari_client = SimpleNamespace(
        originate_channel=AsyncMock(return_value={"id": "originated"}),
        set_channel_var=AsyncMock(return_value=True),
        continue_in_dialplan=AsyncMock(return_value=True),
        hangup_channel=AsyncMock(return_value=True),
        play_media=AsyncMock(return_value={"id": "playback"}),
    )
    async def finish_owned_playback(_channel, _uri, playback_id):
        await engine._on_playback_finished({"playback": {"id": playback_id, "state": "done"}})
        return True
    engine.ari_client.play_media_on_channel_with_id = AsyncMock(side_effect=finish_owned_playback)
    return engine


@pytest.mark.asyncio
@pytest.mark.parametrize("amd_status", ["MACHINE", "NOTSURE"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_saved_checkbox_api_sqlite_start_restart_and_amd(tmp_path, monkeypatch, enabled, amd_status):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from admin_ui.backend.api import outbound

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "history.db")
    store = OutboundStore(db_path=db_path)
    monkeypatch.setattr(outbound, "_get_outbound_store", lambda: store)
    monkeypatch.setattr(outbound, "_media_uri_exists", lambda _uri: False)
    app = FastAPI()
    app.include_router(outbound.router)
    media = "sound:fixture-recording" if enabled else ""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture") as api:
        response = await api.post("/outbound/campaigns", json={
            "name": "Synthetic toggle", "voicemail_drop_enabled": enabled,
            "voicemail_drop_media_uri": media, "consent_enabled": False,
        })
        assert response.status_code == 200, response.text
        campaign_id = response.json()["id"]
        assert response.json()["voicemail_drop_enabled"] == int(enabled)
        # Exercise edits from the opposite value, including literal JSON false.
        for value in [not enabled, enabled]:
            response = await api.patch(f"/outbound/campaigns/{campaign_id}", json={"voicemail_drop_enabled": value})
            assert response.status_code == 200, response.text
            assert response.json()["voicemail_drop_enabled"] == int(value)
        # Reopen the SQLite owner: no in-memory campaign value can mask a lost flag.
        store = OutboundStore(db_path=db_path)
        campaign = (await api.get(f"/outbound/campaigns/{campaign_id}")).json()
        assert campaign["voicemail_drop_enabled"] == int(enabled)
        clone = await api.post(f"/outbound/campaigns/{campaign_id}/clone")
        assert clone.status_code == 200
        assert clone.json()["voicemail_drop_enabled"] == int(enabled)
        imported = await store.import_leads_csv(campaign_id, b"phone_number\n1001\n")
        assert imported["accepted"] == 1
        for status in ["running", "paused", "running"]:
            response = await api.post(f"/outbound/campaigns/{campaign_id}/status", json={"status": status})
            assert response.status_code == 200, response.text
    lead = (await store.lease_pending_leads(campaign_id, limit=1))[0]
    assert await store.mark_lead_dialing(lead["id"])
    attempt_id = await store.create_attempt(campaign_id, lead["id"], context="default")
    meta = await store.get_active_attempt_runtime_context(attempt_id)
    engine = _make_engine(store, meta)
    await engine._outbound_originate_attempt(campaign, lead, attempt_id)
    variables = engine.ari_client.originate_channel.await_args.kwargs["channel_vars"]
    assert variables["AAVA_VM_ENABLED"] == str(int(enabled))
    assert variables["__AAVA_VM_ENABLED"] == str(int(enabled))
    assert variables["AAVA_CONSENT_ENABLED"] == "0"
    # Simulate an engine restart before answer: recover authoritative attempt data.
    engine._outbound_attempt_meta_by_attempt_id.clear()
    engine._outbound_attempt_meta_by_channel_id.clear()
    await engine._handle_outbound_answered("answered", {"id": "answered"}, ["outbound", attempt_id])
    engine.ari_client.set_channel_var.assert_any_await("answered", "AAVA_VM_ENABLED", str(int(enabled)))
    engine.ari_client.set_channel_var.assert_any_await("answered", "AAVA_CONSENT_ENABLED", "0")
    engine.ari_client.continue_in_dialplan.assert_awaited_once()
    assert "answered" in engine._outbound_awaiting_amd_channel_ids
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", attempt_id, amd_status, "fixture"])
    if enabled:
        assert engine.ari_client.play_media_on_channel_with_id.await_args.args[:2] == ("answered", media)
        assert engine.ari_client.play_media_on_channel_with_id.await_args.args[2].startswith("outbound-vm-")
        assert not engine._ari_playback_waiters
    else:
        engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()
        engine._wait_for_ari_playback.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    assert not engine._outbound_attempt_amd
    assert not engine._outbound_awaiting_amd_channel_ids
    # A later ChannelDestroyed must not turn the terminal AMD outcome into no_answer.
    await engine._handle_outbound_channel_destroyed({"channel": {"id": "answered"}, "cause_txt": "Unknown"})
    attempt = (await store.list_attempts(campaign_id))["attempts"][0]
    saved_lead = (await store.list_leads(campaign_id))["leads"][0]
    outcome = "voicemail_dropped" if enabled else "machine_detected"
    assert attempt["outcome"] == outcome
    assert attempt["amd_status"] == "MACHINE"
    assert attempt["ended_at_utc"]
    assert saved_lead["state"] == "completed"
    assert saved_lead["last_outcome"] == outcome


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [{}, {"voicemail_drop_enabled": None}, {"voicemail_drop_enabled": 0}, {"voicemail_drop_enabled": 1}])
async def test_legacy_defaults_at_all_runtime_consumers(flag):
    enabled = flag.get("voicemail_drop_enabled") != 0
    campaign = {"id": "campaign", "voicemail_drop_media_uri": "sound:fixture", **flag}
    meta = {"attempt_id": "attempt", "campaign_id": "campaign", "lead_id": "lead", "context": "default"}
    store = SimpleNamespace(**{name: AsyncMock() for name in ["set_attempt_channel", "set_lead_state", "set_attempt_gate_result", "finish_attempt"]})
    store.get_campaign = AsyncMock(return_value=campaign)
    engine = _make_engine(store, meta)
    await engine._outbound_originate_attempt(campaign, {"id": "lead", "phone_number": "1001"}, "attempt")
    assert engine.ari_client.originate_channel.await_args.kwargs["channel_vars"]["AAVA_VM_ENABLED"] == str(int(enabled))
    await engine._handle_outbound_answered("originated", {}, ["outbound", "attempt"])
    engine.ari_client.set_channel_var.assert_any_await("originated", "AAVA_VM_ENABLED", str(int(enabled)))
    await engine._handle_outbound_amd_result("originated", {}, ["outbound_amd", "attempt", "MACHINE"])
    assert engine.ari_client.play_media_on_channel_with_id.await_count == int(enabled)
    assert store.finish_attempt.await_args.kwargs["outcome"] == ("voicemail_dropped" if enabled else "machine_detected")


@pytest.mark.asyncio
@pytest.mark.parametrize("consent_result", ["denied", "timeout"])
async def test_disabled_voicemail_does_not_bypass_human_consent_gate(consent_result):
    meta = {"attempt_id": "attempt", "campaign_id": "campaign", "lead_id": "lead"}
    store = SimpleNamespace(**{name: AsyncMock() for name in ["set_lead_state", "set_attempt_gate_result", "finish_attempt"]})
    store.get_campaign = AsyncMock(return_value={"voicemail_drop_enabled": 0})
    engine = _make_engine(store, meta)
    engine._outbound_attempt_meta_by_channel_id["answered"] = meta
    engine._handle_caller_stasis_start_hybrid = AsyncMock()
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "HUMAN", "", "", consent_result])
    assert store.finish_attempt.await_args.kwargs["outcome"] == f"consent_{consent_result}"
    store.set_lead_state.assert_awaited_once_with("lead", state="canceled", last_outcome=f"consent_{consent_result}")
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id


def _amd_failure_engine(campaign):
    meta = {"attempt_id": "attempt", "campaign_id": "campaign", "lead_id": "lead"}
    store = SimpleNamespace(**{name: AsyncMock() for name in ["set_lead_state", "set_attempt_gate_result", "finish_attempt"]})
    store.get_campaign = AsyncMock(return_value=campaign)
    engine = _make_engine(store, meta)
    engine._outbound_attempt_meta_by_channel_id["answered"] = meta
    engine._outbound_awaiting_amd_channel_ids.add("answered")
    return engine


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["lookup_error", "deleted", "none", "empty", "missing_uri", "blank_uri", "invalid_uri_type", "invalid_flag"])
async def test_amd_configuration_failure_never_beeps_or_reports_a_drop(case):
    campaign = {"id": "campaign", "voicemail_drop_enabled": 1, "voicemail_drop_media_uri": "sound:fixture"}
    if case == "missing_uri":
        campaign.pop("voicemail_drop_media_uri")
    elif case == "blank_uri":
        campaign["voicemail_drop_media_uri"] = "  "
    elif case == "invalid_uri_type":
        campaign["voicemail_drop_media_uri"] = 123
    elif case == "invalid_flag":
        campaign["voicemail_drop_enabled"] = "corrupt"
    engine = _amd_failure_engine(None if case == "none" else {} if case == "empty" else campaign)
    if case in ("lookup_error", "deleted"):
        engine.outbound_store.get_campaign.side_effect = RuntimeError("synthetic unavailable") if case == "lookup_error" else KeyError("deleted")
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE", "fixture"])
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()
    engine._wait_for_ari_playback.assert_not_awaited()
    assert engine.outbound_store.finish_attempt.await_args.kwargs["outcome"] == "error"
    assert engine.outbound_store.finish_attempt.await_args.kwargs["amd_status"] == "MACHINE"
    engine.outbound_store.set_lead_state.assert_awaited_once_with("lead", state="failed", last_outcome="error")
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    assert not engine._outbound_attempt_amd
    assert not engine._outbound_awaiting_amd_channel_ids
    await engine._handle_outbound_channel_destroyed({"channel": {"id": "answered"}, "cause_txt": "Unknown"})
    assert engine.outbound_store.finish_attempt.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["attempt", "lead", "both"])
async def test_amd_configuration_failure_hangs_up_and_cleans_up_when_persistence_fails(failure):
    engine = _amd_failure_engine(None)
    if failure in ("attempt", "both"):
        engine.outbound_store.finish_attempt.side_effect = RuntimeError("synthetic DB failure")
    if failure in ("lead", "both"):
        engine.outbound_store.set_lead_state.side_effect = RuntimeError("synthetic DB failure")
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "NOTSURE"])
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    assert not engine._outbound_attempt_amd
    assert not engine._outbound_awaiting_amd_channel_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rejected", "exception", "timeout"])
async def test_unconfirmed_voicemail_playback_is_error_without_replay(failure):
    engine = _amd_failure_engine({"id": "campaign", "voicemail_drop_enabled": 1, "voicemail_drop_media_uri": "sound:fixture"})
    if failure == "rejected":
        engine.ari_client.play_media_on_channel_with_id.side_effect = None
        engine.ari_client.play_media_on_channel_with_id.return_value = False
    elif failure == "exception":
        engine.ari_client.play_media_on_channel_with_id.side_effect = RuntimeError("synthetic unavailable media")
    else:
        engine.ari_client.play_media_on_channel_with_id.side_effect = None
        engine.ari_client.play_media_on_channel_with_id.return_value = True
        original_play = engine._play_outbound_voicemail
        async def bounded_play(channel_id, uri):
            return await original_play(channel_id, uri, timeout_sec=0.01)
        engine._play_outbound_voicemail = bounded_play
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    assert engine.ari_client.play_media_on_channel_with_id.await_count == 1
    assert not engine._ari_playback_waiters
    assert engine.outbound_store.finish_attempt.await_args.kwargs["outcome"] == "error"
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id


@pytest.mark.asyncio
async def test_missing_recording_amd_error_is_persisted_with_classification_and_ui_stats(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    store = OutboundStore(db_path=str(tmp_path / "history.db"))
    campaign = await store.create_campaign({"name": "Incomplete synthetic campaign", "voicemail_drop_enabled": True})
    await store.import_leads_csv(campaign["id"], b"phone_number\n1001\n")
    lead = (await store.list_leads(campaign["id"]))["leads"][0]
    attempt = await store.create_attempt(campaign["id"], lead["id"])
    meta = await store.get_active_attempt_runtime_context(attempt)
    engine = _make_engine(store, meta)
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", attempt, "MACHINE", "fixture"])
    saved = (await store.list_attempts(campaign["id"]))["attempts"][0]
    assert saved["outcome"] == "error"
    assert saved["amd_status"] == "MACHINE"
    assert saved["amd_cause"] == "FIXTURE"
    assert saved["error_message"] == "outbound voicemail recording is not configured"
    assert saved["ended_at_utc"]
    saved_lead = (await store.list_leads(campaign["id"]))["leads"][0]
    assert saved_lead["state"] == "failed"
    assert saved_lead["last_outcome"] == "error"
    assert (await store.campaign_stats(campaign["id"]))["attempt_outcomes"] == {"error": 1}
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")


@pytest.mark.asyncio
async def test_amd_rejection_still_hangs_up_when_persistence_is_cancelled():
    import asyncio

    engine = _amd_failure_engine(None)
    engine.outbound_store.finish_attempt.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    assert not engine._outbound_attempt_amd


@pytest.mark.asyncio
async def test_disabled_machine_hangs_up_when_final_outcome_write_fails():
    engine = _amd_failure_engine({"id": "campaign", "voicemail_drop_enabled": 0})
    engine.outbound_store.finish_attempt.side_effect = RuntimeError("synthetic DB failure")
    with pytest.raises(RuntimeError, match="synthetic DB failure"):
        await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    assert not engine._outbound_attempt_amd


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected", [("early_done", "voicemail_dropped"), ("failed_finished", "error")])
async def test_outbound_confirmation_handles_early_events_and_terminal_state(case, expected):
    import asyncio

    engine = _amd_failure_engine({"id": "campaign", "voicemail_drop_enabled": 1, "voicemail_drop_media_uri": "sound:fixture"})
    engine._ari_playback_waiters = {}
    engine.playback_manager = SimpleNamespace(on_playback_finished=AsyncMock())
    async def emit(playback_id):
        await engine._on_playback_finished({"playback": {"id": playback_id, "state": "done" if case == "early_done" else "failed"}})
    async def play_old(_channel, _uri):
        if case == "early_done":
            await emit("fixture-playback")
        else:
            asyncio.get_running_loop().call_soon(lambda: asyncio.create_task(emit("fixture-playback")))
        return {"id": "fixture-playback"}
    async def play_owned(_channel, _uri, playback_id):
        if case == "early_done":
            await emit(playback_id)
        else:
            asyncio.get_running_loop().call_soon(lambda: asyncio.create_task(emit(playback_id)))
        return True
    engine.ari_client.play_media.side_effect = play_old
    engine.ari_client.play_media_on_channel_with_id = AsyncMock(side_effect=play_owned)
    async def bounded_wait(playback_id, **_kwargs):
        return await Engine._wait_for_ari_playback(engine, playback_id, timeout_sec=0.01)
    engine._wait_for_ari_playback = bounded_wait
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    assert engine.outbound_store.finish_attempt.await_args.kwargs["outcome"] == expected
    assert not engine._ari_playback_waiters
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["start_timeout", "cancelled", "unknown_state", "unrelated_playback"])
async def test_owned_voicemail_waiter_is_released_on_all_failure_paths(case):
    import asyncio

    engine = _amd_failure_engine({"id": "campaign", "voicemail_drop_enabled": 1, "voicemail_drop_media_uri": "sound:fixture"})
    async def start(_channel, _uri, playback_id):
        assert playback_id in engine._ari_playback_waiters
        if case == "start_timeout":
            await asyncio.Event().wait()
        if case == "cancelled":
            raise asyncio.CancelledError()
        await engine._on_playback_finished({"playback": {"id": "unrelated" if case == "unrelated_playback" else playback_id, "state": "done" if case == "unrelated_playback" else "failed"}})
        return True
    engine.ari_client.play_media_on_channel_with_id.side_effect = start
    original = engine._play_outbound_voicemail
    async def bounded(channel_id, uri):
        return await original(channel_id, uri, timeout_sec=0.01, start_timeout_sec=0.01)
    engine._play_outbound_voicemail = bounded
    if case == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    else:
        await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    assert engine.ari_client.play_media_on_channel_with_id.await_count == 1
    assert engine.outbound_store.finish_attempt.await_args.kwargs["outcome"] == "error"
    assert not engine._ari_playback_waiters
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    assert not engine._outbound_attempt_amd
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")
