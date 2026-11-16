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
    engine.outbound_store.set_lead_state.assert_awaited_once_with("lead", state="failed", last_outcome="error", only_if_active=True, expected_attempt_id="attempt")
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
    owner = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    assert engine._outbound_attempt_meta_by_channel_id["answered"] is owner
    assert not owner["completion_hangup_pending"]
    assert not engine._outbound_awaiting_amd_channel_ids
    engine.outbound_store.finish_attempt.side_effect = None
    engine.outbound_store.set_lead_state.side_effect = None
    owner["completion_retry_at"] = 0
    await engine._outbound_cleanup_stale_attempts()
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    assert not engine._outbound_attempt_amd


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
    await store.set_lead_state(lead["id"], state="amd_pending")
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
    owner = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    assert not owner["completion_hangup_pending"]
    engine.outbound_store.finish_attempt.side_effect = None
    await engine._outbound_cleanup_stale_attempts()
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    assert not engine._outbound_attempt_amd


@pytest.mark.asyncio
async def test_disabled_machine_hangs_up_when_final_outcome_write_fails():
    engine = _amd_failure_engine({"id": "campaign", "voicemail_drop_enabled": 0})
    engine.outbound_store.finish_attempt.side_effect = RuntimeError("synthetic DB failure")
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")
    owner = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    assert owner["completion_result"]["outcome"] == "machine_detected"
    engine.outbound_store.finish_attempt.side_effect = None
    owner["completion_retry_at"] = 0
    await engine._outbound_cleanup_stale_attempts()
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


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("failed_write", ["attempt", "lead"])
async def test_terminal_write_recovers_after_db_returns_without_replaying(enabled, failed_write):
    import asyncio

    engine = _amd_failure_engine({"id": "campaign", "voicemail_drop_enabled": int(enabled), "voicemail_drop_media_uri": "sound:fixture"})
    store = engine.outbound_store
    target = store.finish_attempt if failed_write == "attempt" else store.set_lead_state
    target.side_effect = RuntimeError("synthetic unavailable DB")
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE", "fixture"])
    owner = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    outcome = "voicemail_dropped" if enabled else "machine_detected"
    assert owner["completion_result"]["outcome"] == outcome
    assert owner["completion_attempt_saved"] is (failed_write == "lead")
    assert not owner["completion_lead_saved"]
    assert not owner["completion_hangup_pending"]
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")
    target.side_effect = None
    owner["completion_retry_at"] = 0
    await asyncio.gather(
        engine._outbound_cleanup_stale_attempts(),
        engine._handle_outbound_channel_destroyed({"channel": {"id": "answered"}, "cause_txt": "Unknown"}),
        engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"]),
    )
    assert store.finish_attempt.await_count == (2 if failed_write == "attempt" else 1)
    assert store.set_lead_state.await_count == (2 if failed_write == "lead" else 1)
    assert store.finish_attempt.await_args.kwargs["outcome"] == outcome
    assert store.finish_attempt.await_args.kwargs["only_if_unfinished"] is True
    assert engine.ari_client.play_media_on_channel_with_id.await_count == int(enabled)
    assert engine.ari_client.hangup_channel.await_count == 1
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id


@pytest.mark.asyncio
async def test_persistent_completion_failure_keeps_owner_with_capped_backoff(monkeypatch):
    engine = _amd_failure_engine(None)
    clock = [100.0]
    monkeypatch.setattr("src.engine.time.monotonic", lambda: clock[0])
    engine.outbound_store.finish_attempt.side_effect = RuntimeError("synthetic persistent DB failure")
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    owner = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    for delay in [1, 2, 4, 8, 16, 30, 30]:
        assert owner["completion_retry_delay"] == delay
        before = engine.outbound_store.finish_attempt.await_count
        await engine._outbound_cleanup_stale_attempts()
        assert engine.outbound_store.finish_attempt.await_count == before
        clock[0] = owner["completion_retry_at"]
        await engine._outbound_cleanup_stale_attempts()
        assert engine._outbound_attempt_meta_by_attempt_id["attempt"] is owner
    engine.outbound_store.finish_attempt.side_effect = None
    clock[0] = owner["completion_retry_at"]
    await engine._outbound_cleanup_stale_attempts()
    assert not engine._outbound_attempt_meta_by_attempt_id
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_awaited_once()


@pytest.mark.asyncio
async def test_completion_owner_serializes_duplicate_destroyed_and_scheduler_events():
    import asyncio

    engine = _amd_failure_engine(None)
    engine.outbound_store.finish_attempt.side_effect = RuntimeError("synthetic initial failure")
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    owner = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    owner["completion_retry_at"] = 0
    entered, release = asyncio.Event(), asyncio.Event()
    async def finish(*_args, **_kwargs):
        entered.set()
        await release.wait()
    engine.outbound_store.finish_attempt.side_effect = finish
    retry = asyncio.create_task(engine._outbound_cleanup_stale_attempts())
    await entered.wait()
    await asyncio.gather(
        engine._handle_outbound_channel_destroyed({"channel": {"id": "answered"}}),
        engine._outbound_cleanup_stale_attempts(),
        engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"]),
    )
    assert engine.outbound_store.finish_attempt.await_count == 2
    release.set()
    await retry
    assert engine.outbound_store.set_lead_state.await_count == 1
    assert not engine._outbound_attempt_meta_by_attempt_id


@pytest.mark.asyncio
async def test_old_completion_cannot_release_or_write_lead_for_replacement_owner():
    engine = _amd_failure_engine(None)
    engine.outbound_store.finish_attempt.side_effect = RuntimeError("synthetic initial failure")
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    old = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    old["completion_retry_at"] = 0
    replacement = {"attempt_id": "attempt", "channel_id": "answered", "lead_id": "replacement"}
    async def replace(*_args, **_kwargs):
        engine._outbound_attempt_meta_by_attempt_id["attempt"] = replacement
        engine._outbound_attempt_meta_by_channel_id["answered"] = replacement
    engine.outbound_store.finish_attempt.side_effect = replace
    await engine._outbound_cleanup_stale_attempts()
    engine._release_outbound_rejection_ownership(attempt_id="attempt", channel_id="answered", expected_meta=old)
    assert engine._outbound_attempt_meta_by_attempt_id["attempt"] is replacement
    assert engine._outbound_attempt_meta_by_channel_id["answered"] is replacement
    engine.outbound_store.set_lead_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_recent_unfinished_attempt_is_revisited_without_redial(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    path = str(tmp_path / "history.db")
    store = OutboundStore(db_path=path)
    campaign = await store.create_campaign({"name": "Synthetic restart", "voicemail_drop_enabled": False})
    await store.import_leads_csv(campaign["id"], b"phone_number\n1001\n")
    lead = (await store.lease_pending_leads(campaign["id"], limit=1))[0]
    attempt = await store.create_attempt(campaign["id"], lead["id"])
    assert await store.mark_lead_dialing(lead["id"], attempt_id=attempt)
    await store.set_lead_state(lead["id"], state="amd_pending")
    # Fresh Engine has no terminal RAM intent; first cleanup preserves recent rows.
    engine = _make_engine(OutboundStore(db_path=path), {"attempt_id": attempt})
    engine._outbound_attempt_meta_by_attempt_id.clear()
    await engine._outbound_cleanup_stale_attempts()
    assert (await store.list_attempts(campaign["id"]))["attempts"][0]["ended_at_utc"] is None
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE outbound_attempts SET started_at_utc='2000-01-01T00:00:00+00:00' WHERE id=?", (attempt,))
        conn.execute("UPDATE outbound_leads SET last_attempt_at_utc='2000-01-01T00:00:00+00:00' WHERE id=?", (lead["id"],))
    engine._outbound_next_durable_cleanup_ts = 0
    await engine._outbound_cleanup_stale_attempts()
    assert (await store.list_attempts(campaign["id"]))["attempts"][0]["outcome"] == "error"
    assert (await store.list_leads(campaign["id"]))["leads"][0]["state"] == "failed"
    engine.ari_client.originate_channel.assert_not_awaited()
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("failure", ["attempt_before", "attempt_after", "lead_before", "lead_after"])
async def test_sqlite_completion_recovery_is_idempotent_after_lost_write_ack(tmp_path, monkeypatch, enabled, failure):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    store = OutboundStore(db_path=str(tmp_path / "history.db"))
    campaign = await store.create_campaign({"name": "Synthetic completion", "voicemail_drop_enabled": enabled, "voicemail_drop_media_uri": "sound:fixture"})
    await store.import_leads_csv(campaign["id"], b"phone_number\n1001\n")
    lead = (await store.lease_pending_leads(campaign["id"], limit=1))[0]
    attempt = await store.create_attempt(campaign["id"], lead["id"])
    assert await store.mark_lead_dialing(lead["id"], attempt_id=attempt)
    await store.set_lead_state(lead["id"], state="amd_pending")
    meta = await store.get_active_attempt_runtime_context(attempt)
    engine = _make_engine(store, meta)
    target = "finish_attempt" if failure.startswith("attempt") else "set_lead_state"
    real_write = getattr(store, target)
    writes = [0]
    async def lose_ack(*args, **kwargs):
        writes[0] += 1
        if writes[0] == 1:
            if failure.endswith("after"):
                await real_write(*args, **kwargs)
            raise RuntimeError("synthetic lost DB write acknowledgment")
        return await real_write(*args, **kwargs)
    monkeypatch.setattr(store, target, lose_ack)
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", attempt, "MACHINE", "fixture"])
    owner = engine._outbound_attempt_meta_by_attempt_id[attempt]
    before = (await store.list_attempts(campaign["id"]))["attempts"][0]
    assert owner["completion_result"]["outcome"] == ("voicemail_dropped" if enabled else "machine_detected")
    assert not owner["completion_hangup_pending"]
    owner["completion_retry_at"] = 0
    await engine._outbound_cleanup_stale_attempts()
    after = (await store.list_attempts(campaign["id"]))["attempts"][0]
    assert after["outcome"] == owner["completion_result"]["outcome"]
    assert after["amd_status"] == "MACHINE"
    assert after["ended_at_utc"]
    if before["ended_at_utc"]:
        assert after == before
    saved_lead = (await store.list_leads(campaign["id"]))["leads"][0]
    assert saved_lead["state"] == "completed"
    assert saved_lead["last_outcome"] == after["outcome"]
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    await engine._handle_outbound_channel_destroyed({"channel": {"id": "answered"}, "cause_txt": "Unknown"})
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", attempt, "MACHINE"])
    assert (await store.list_attempts(campaign["id"]))["attempts"][0] == after
    assert engine.ari_client.play_media_on_channel_with_id.await_count == int(enabled)
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")


@pytest.mark.asyncio
async def test_destroyed_channel_releases_hangup_retry_but_keeps_failed_completion():
    import asyncio

    engine = _amd_failure_engine(None)
    engine.outbound_store.finish_attempt.side_effect = RuntimeError("synthetic DB unavailable")
    engine.ari_client.hangup_channel.return_value = False
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    owner = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    task = engine._outbound_forced_hangup_tasks["answered"]
    await asyncio.sleep(0)
    await engine._handle_outbound_channel_destroyed({"channel": {"id": "answered"}})
    await task
    assert engine._outbound_attempt_meta_by_attempt_id["attempt"] is owner
    assert not owner["completion_hangup_pending"]
    assert not engine._outbound_forced_hangup_tasks
    engine.outbound_store.finish_attempt.side_effect = None
    owner["completion_retry_at"] = 0
    await engine._outbound_cleanup_stale_attempts()
    assert not engine._outbound_attempt_meta_by_attempt_id
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_campaign_during_pending_completion_is_not_recreated(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    store = OutboundStore(db_path=str(tmp_path / "history.db"))
    campaign = await store.create_campaign({"name": "Synthetic deleted", "voicemail_drop_enabled": False})
    await store.import_leads_csv(campaign["id"], b"phone_number\n1001\n")
    lead = (await store.lease_pending_leads(campaign["id"], limit=1))[0]
    attempt = await store.create_attempt(campaign["id"], lead["id"])
    assert await store.mark_lead_dialing(lead["id"], attempt_id=attempt)
    meta = await store.get_active_attempt_runtime_context(attempt)
    engine = _make_engine(store, meta)
    finish = store.finish_attempt
    monkeypatch.setattr(store, "finish_attempt", AsyncMock(side_effect=RuntimeError("synthetic DB unavailable")))
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", attempt, "MACHINE"])
    owner = engine._outbound_attempt_meta_by_attempt_id[attempt]
    await store.delete_campaign(campaign["id"])
    monkeypatch.setattr(store, "finish_attempt", finish)
    owner["completion_retry_at"] = 0
    await engine._outbound_cleanup_stale_attempts()
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    with pytest.raises(KeyError):
        await store.get_campaign(campaign["id"])
    assert (await store.list_attempts(campaign["id"]))["total"] == 0
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_hangup_keeps_its_retry_owner_after_terminal_writes():
    import asyncio

    engine = _amd_failure_engine(None)
    engine.ari_client.hangup_channel.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    owner = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    assert owner["completion_attempt_saved"] and owner["completion_lead_saved"]
    assert owner["completion_hangup_pending"]
    task = engine._outbound_forced_hangup_tasks["answered"]
    await asyncio.sleep(0)
    await engine._handle_outbound_channel_destroyed({"channel": {"id": "answered"}})
    await task
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_forced_hangup_tasks
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_destroyed_continuation_cannot_overwrite_completed_sqlite_result(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    store = OutboundStore(db_path=str(tmp_path / "history.db"))
    campaign = await store.create_campaign({"name": "Destroyed continuation", "voicemail_drop_enabled": False})
    await store.import_leads_csv(campaign["id"], b"phone_number\n1001\n")
    lead = (await store.lease_pending_leads(campaign["id"], limit=1))[0]
    attempt = await store.create_attempt(campaign["id"], lead["id"])
    assert await store.mark_lead_dialing(lead["id"], attempt_id=attempt)
    await store.set_lead_state(lead["id"], state="amd_pending")
    meta = await store.get_active_attempt_runtime_context(attempt)
    engine = _make_engine(store, meta)
    engine._outbound_attempt_meta_by_channel_id["answered"] = meta
    entered, release = asyncio.Event(), asyncio.Event()
    async def session_lookup(_channel):
        entered.set()
        await release.wait()
        return None
    engine.session_store = SimpleNamespace(get_by_channel_id=session_lookup)
    destroyed = asyncio.create_task(engine._handle_outbound_channel_destroyed({"channel": {"id": "answered"}, "cause_txt": "Unknown"}))
    await entered.wait()
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", attempt, "MACHINE"])
    terminal = (await store.list_attempts(campaign["id"]))["attempts"][0]
    assert terminal["outcome"] == "machine_detected"
    replacement = {"attempt_id": "replacement", "channel_id": "answered"}
    engine._outbound_attempt_meta_by_channel_id["answered"] = replacement
    release.set()
    await destroyed
    assert (await store.list_attempts(campaign["id"]))["attempts"][0] == terminal
    assert (await store.list_leads(campaign["id"]))["leads"][0]["state"] == "completed"
    assert engine._outbound_attempt_meta_by_channel_id["answered"] is replacement


@pytest.mark.asyncio
async def test_generic_destroyed_write_await_keeps_exact_replacement_owner():
    import asyncio

    engine = _amd_failure_engine({"voicemail_drop_enabled": 0})
    engine.session_store = SimpleNamespace(get_by_channel_id=AsyncMock(return_value=None))
    entered, release = asyncio.Event(), asyncio.Event()
    async def finish(*_args, **_kwargs):
        entered.set()
        await release.wait()
    engine.outbound_store.finish_attempt.side_effect = finish
    task = asyncio.create_task(engine._handle_outbound_channel_destroyed({"channel": {"id": "answered"}, "cause_txt": "Unknown"}))
    await entered.wait()
    replacement = {"attempt_id": "attempt", "channel_id": "answered", "lead_id": "replacement"}
    engine._outbound_attempt_meta_by_attempt_id["attempt"] = replacement
    engine._outbound_attempt_meta_by_channel_id["answered"] = replacement
    release.set()
    await task
    assert engine._outbound_attempt_meta_by_attempt_id["attempt"] is replacement
    assert engine._outbound_attempt_meta_by_channel_id["answered"] is replacement
    engine.outbound_store.set_lead_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_watchdog_snapshot_cannot_replace_terminal_or_new_owner():
    import asyncio

    engine = _amd_failure_engine({"id": "campaign", "voicemail_drop_enabled": 0})
    meta = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    meta["created_at_ts"] = 1.0
    meta["channel_id"] = "answered"
    engine._outbound_awaiting_amd_channel_ids.clear()
    entered, release = asyncio.Event(), asyncio.Event()
    async def lookup(_channel):
        entered.set()
        await release.wait()
        return None
    engine.session_store = SimpleNamespace(get_by_channel_id=lookup)
    engine.ari_client.send_command = AsyncMock(return_value={"status": 404})
    watchdog = asyncio.create_task(engine._outbound_cleanup_stale_attempts())
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    replacement = {"attempt_id": "attempt", "channel_id": "answered", "lead_id": "replacement"}
    engine._outbound_attempt_meta_by_attempt_id["attempt"] = replacement
    engine._outbound_attempt_meta_by_channel_id["answered"] = replacement
    release.set()
    await watchdog
    assert engine._outbound_attempt_meta_by_attempt_id["attempt"] is replacement
    assert engine._outbound_attempt_meta_by_channel_id["answered"] is replacement
    assert engine.outbound_store.finish_attempt.await_count == 1
    assert engine.outbound_store.finish_attempt.await_args.kwargs["outcome"] == "machine_detected"
    engine.ari_client.send_command.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [0, 1])
async def test_late_answer_preserves_busy_completion_owner_without_reentering_amd(enabled):
    import asyncio

    engine = _amd_failure_engine({"id": "campaign", "voicemail_drop_enabled": enabled, "voicemail_drop_media_uri": "sound:fixture"})
    engine.outbound_store.set_attempt_channel = AsyncMock()
    engine._set_outbound_agent_channel_vars = AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()
    async def finish(*_args, **_kwargs):
        entered.set()
        await release.wait()
    engine.outbound_store.finish_attempt.side_effect = finish
    terminal = asyncio.create_task(engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"]))
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    owner = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    assert owner["completion_busy"] is True
    try:
        await asyncio.wait_for(engine._handle_outbound_answered("answered", {}, ["outbound", "attempt"]), timeout=1.0)
        assert engine._outbound_attempt_meta_by_attempt_id["attempt"] is owner
        assert engine._outbound_attempt_meta_by_channel_id["answered"] is owner
        engine.outbound_store.set_attempt_channel.assert_not_awaited()
        engine.ari_client.continue_in_dialplan.assert_not_awaited()
    finally:
        release.set()
        await asyncio.wait_for(terminal, timeout=1.0)
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    assert engine.ari_client.play_media_on_channel_with_id.await_count == enabled
    engine.outbound_store.set_lead_state.assert_awaited_once_with("lead", state="completed", last_outcome="voicemail_dropped" if enabled else "machine_detected", only_if_active=True, expected_attempt_id="attempt")


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [0, 1])
@pytest.mark.parametrize("failed_write", ["finish_attempt", "set_lead_state"])
async def test_late_answer_recovers_pending_completion_without_playback_or_amd(enabled, failed_write):
    engine = _amd_failure_engine({"id": "campaign", "voicemail_drop_enabled": enabled, "voicemail_drop_media_uri": "sound:fixture"})
    engine.outbound_store.set_attempt_channel = AsyncMock()
    getattr(engine.outbound_store, failed_write).side_effect = RuntimeError("synthetic unavailable")
    await engine._handle_outbound_amd_result("answered", {}, ["outbound_amd", "attempt", "MACHINE"])
    owner = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    assert not owner["completion_busy"]
    getattr(engine.outbound_store, failed_write).side_effect = None
    owner["completion_retry_at"] = 0
    await engine._handle_outbound_answered("answered", {}, ["outbound", "attempt"])
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    assert engine.ari_client.play_media_on_channel_with_id.await_count == enabled
    engine.ari_client.hangup_channel.assert_awaited_once_with("answered")
    engine.outbound_store.set_attempt_channel.assert_not_awaited()
    engine.ari_client.continue_in_dialplan.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["recovery", "correlation", "lead_state", "custom_vars", "channel_vars", "handoff"])
async def test_answer_continuation_retires_when_terminal_owner_wins_during_await(boundary):
    import asyncio

    engine = _amd_failure_engine({"id": "campaign", "voicemail_drop_enabled": 0})
    engine.outbound_store.set_attempt_channel = AsyncMock()
    engine._set_outbound_agent_channel_vars = AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()
    original_meta = engine._outbound_attempt_meta_by_attempt_id["attempt"]
    async def paused(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return dict(original_meta) if boundary == "recovery" else boundary != "handoff"
    if boundary == "recovery":
        engine._outbound_attempt_meta_by_attempt_id.clear()
        engine._outbound_attempt_meta_by_channel_id.clear()
        engine.outbound_store.get_active_attempt_runtime_context = AsyncMock(side_effect=paused)
    elif boundary == "correlation":
        engine.outbound_store.set_attempt_channel.side_effect = paused
    elif boundary == "lead_state":
        async def lead_write(*args, **kwargs):
            if kwargs.get("state") == "amd_pending":
                await paused()
        engine.outbound_store.set_lead_state.side_effect = lead_write
    elif boundary == "custom_vars":
        original_meta["custom_vars"] = {"synthetic": "value"}
        engine._set_and_confirm_outbound_custom_vars = AsyncMock(side_effect=paused)
    elif boundary == "channel_vars":
        engine.ari_client.set_channel_var.side_effect = paused
    else:
        engine.ari_client.continue_in_dialplan.side_effect = paused
    answered = asyncio.create_task(engine._handle_outbound_answered("answered", {}, ["outbound", "attempt"]))
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    await engine._complete_outbound_attempt("answered", "attempt", original_meta, result={"outcome": "machine_detected"}, lead_state="completed")
    completed_writes = engine.outbound_store.set_lead_state.await_count
    release.set()
    await asyncio.wait_for(answered, timeout=1.0)
    assert engine.outbound_store.set_lead_state.await_count == completed_writes
    assert not engine._outbound_attempt_meta_by_attempt_id
    assert not engine._outbound_attempt_meta_by_channel_id
    assert engine.ari_client.continue_in_dialplan.await_count == int(boundary == "handoff")
    engine.ari_client.play_media_on_channel_with_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_inflight_answer_lead_write_cannot_reset_completed_sqlite_lead(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    store = OutboundStore(db_path=str(tmp_path / "history.db"))
    campaign = await store.create_campaign({"name": "Late answer", "voicemail_drop_enabled": False})
    await store.import_leads_csv(campaign["id"], b"phone_number\n1001\n")
    lead = (await store.lease_pending_leads(campaign["id"], limit=1))[0]
    attempt = await store.create_attempt(campaign["id"], lead["id"])
    assert await store.mark_lead_dialing(lead["id"], attempt_id=attempt)
    meta = await store.get_active_attempt_runtime_context(attempt)
    engine = _make_engine(store, meta)
    entered, release = asyncio.Event(), asyncio.Event()
    original_write = store.set_lead_state
    async def delayed_write(*args, **kwargs):
        if kwargs.get("state") == "amd_pending":
            entered.set()
            await release.wait()
        await original_write(*args, **kwargs)
    store.set_lead_state = delayed_write
    answered = asyncio.create_task(engine._handle_outbound_answered("answered", {}, ["outbound", attempt]))
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    await engine._complete_outbound_attempt("answered", attempt, meta, result={"outcome": "machine_detected"}, lead_state="completed")
    release.set()
    await asyncio.wait_for(answered, timeout=1.0)
    row = (await store.list_leads(campaign["id"]))["leads"][0]
    assert (row["state"], row["last_outcome"]) == ("completed", "machine_detected")
    assert not engine._outbound_attempt_meta_by_attempt_id
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
