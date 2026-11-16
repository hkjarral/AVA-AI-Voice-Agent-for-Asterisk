import importlib.util

import pytest

# admin_ui backend imports fastapi at module load. Skip the whole module on
# environments that don't have it (CI's engine-only jobs run without admin_ui
# deps), matching tests/test_admin_call_recordings.py.
if importlib.util.find_spec("fastapi") is None:
    pytest.skip("fastapi not installed; admin_ui outbound tests skipped", allow_module_level=True)

from fastapi import HTTPException

from admin_ui.backend.api import outbound
from admin_ui.backend.agents_store import AgentsStore


def test_media_ulaw_lookup_only_relaxes_extension_case(tmp_path, monkeypatch):
    upper_dir = tmp_path / "upper"
    lower_dir = tmp_path / "lower"
    upper_dir.mkdir()
    lower_dir.mkdir()

    monkeypatch.setattr(outbound, "_media_dir", lambda: str(upper_dir))
    promo_upper = upper_dir / "Promo.ULAW"
    promo_upper.write_bytes(b"upper")

    assert outbound._find_media_ulaw_path("Promo") == str(promo_upper)
    assert outbound._read_media_ulaw("sound:ai-generated/Promo") == b"upper"
    assert outbound._find_media_ulaw_path("promo") is None

    monkeypatch.setattr(outbound, "_media_dir", lambda: str(lower_dir))
    promo_lower = lower_dir / "promo.ulaw"
    promo_lower.write_bytes(b"lower")

    assert outbound._find_media_ulaw_path("promo") == str(promo_lower)
    assert outbound._read_media_ulaw("sound:ai-generated/promo") == b"lower"


@pytest.mark.asyncio
async def test_list_recordings_includes_uppercase_ulaw_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(outbound, "_media_dir", lambda: str(tmp_path))
    (tmp_path / "Greeting.ULAW").write_bytes(b"abc")
    (tmp_path / "notes.txt").write_text("ignore")

    rows = await outbound.list_recordings()

    assert len(rows) == 1
    assert rows[0].filename == "Greeting.ULAW"
    assert rows[0].media_uri == "sound:ai-generated/Greeting"
    assert rows[0].size_bytes == 3


@pytest.mark.asyncio
async def test_outbound_meta_lists_active_agent_slugs(tmp_path, monkeypatch):
    agents_db = tmp_path / "agents.db"
    monkeypatch.setenv("AGENTS_DB_PATH", str(agents_db))
    with AgentsStore(db_path=str(agents_db)) as store:
        sales = store.create(display_name="Sales", slug="sales", provider="local", prompt="Sell")
        disabled = store.create(display_name="Old", slug="old", provider="local", prompt="Old")
        with store.conn:
            store.conn.execute("UPDATE agents SET is_default=1 WHERE id=?", (sales["id"],))
            store.conn.execute("UPDATE agents SET is_active=0 WHERE id=?", (disabled["id"],))

    meta = await outbound.outbound_meta()

    assert meta["agents"] == [{"slug": "sales", "display_name": "Sales", "is_default": True}]


@pytest.mark.asyncio
async def test_sample_csv_prefers_configured_default_agent_header(monkeypatch):
    monkeypatch.setattr(
        outbound,
        "_load_active_agents",
        lambda: [
            {"slug": "support", "display_name": "Support", "is_default": False},
            {"slug": "sales", "display_name": "Sales", "is_default": True},
        ],
    )
    response = await outbound.download_sample_csv()
    body = response.body.decode("utf-8")

    assert body.splitlines()[0] == "name,phone_number,agent,timezone,caller_id,custom_vars"
    assert ",sales," in body
    assert ",context," not in body


def test_campaign_schedule_start_validation_accepts_valid_and_cross_midnight_windows():
    for start_local, end_local in (("22:00", "06:00"), (" 9:00 ", "17:00")):
        outbound._validate_campaign_schedule_for_start(
            {
                "timezone": "America/Phoenix",
                "daily_window_start_local": start_local,
                "daily_window_end_local": end_local,
                "run_start_at_utc": "2026-07-18T00:00:00Z",
                "run_end_at_utc": "2026-07-19T00:00:00+00:00",
            }
        )


@pytest.mark.parametrize(
    "campaign,detail_fragment",
    [
        (
            {"timezone": "Invalid/Zone", "daily_window_start_local": "09:00", "daily_window_end_local": "17:00"},
            "Invalid timezone",
        ),
        (
            {"timezone": "UTC", "daily_window_start_local": "24:00", "daily_window_end_local": "17:00"},
            "H:MM",
        ),
        (
            {
                "timezone": "UTC",
                "daily_window_start_local": "09:00",
                "daily_window_end_local": "17:00",
                "run_start_at_utc": "not-a-timestamp",
            },
            "Invalid absolute run start",
        ),
        (
            {
                "timezone": "UTC",
                "daily_window_start_local": "09:00",
                "daily_window_end_local": "17:00",
                "run_start_at_utc": "2026-07-19T00:00:00Z",
                "run_end_at_utc": "2026-07-18T00:00:00Z",
            },
            "start must be before",
        ),
    ],
)
def test_campaign_schedule_start_validation_rejects_invalid_values(campaign, detail_fragment):
    with pytest.raises(HTTPException) as exc_info:
        outbound._validate_campaign_schedule_for_start(campaign)

    assert exc_info.value.status_code == 400
    assert detail_fragment in str(exc_info.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flag,consent,expected_detail",
    [
        ({"voicemail_drop_enabled": 0}, False, None),
        ({"voicemail_drop_enabled": False}, False, None),
        ({"voicemail_drop_enabled": 1}, False, "Voicemail drop is enabled"),
        ({"voicemail_drop_enabled": True}, False, "Voicemail drop is enabled"),
        ({}, False, "Voicemail drop is enabled"),
        ({"voicemail_drop_enabled": None}, False, "Voicemail drop is enabled"),
        ({"voicemail_drop_enabled": 0}, True, "Consent gate is enabled"),
    ],
)
async def test_start_recording_guards_preserve_disabled_and_legacy_defaults(monkeypatch, flag, consent, expected_detail):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    campaign = {
        "timezone": "UTC", "daily_window_start_local": "09:00", "daily_window_end_local": "17:00",
        "consent_enabled": int(consent), **flag,
    }
    store = SimpleNamespace(
        get_campaign=AsyncMock(return_value=campaign),
        campaign_stats=AsyncMock(return_value={"lead_states": {"pending": 1}}),
        set_campaign_status=AsyncMock(return_value={"status": "running"}),
    )
    monkeypatch.setattr(outbound, "_get_outbound_store", lambda: store)
    request = outbound.CampaignStatusRequest(status="running")
    if expected_detail:
        with pytest.raises(HTTPException) as exc:
            await outbound.set_campaign_status("fixture", request)
        assert exc.value.status_code == 400
        assert expected_detail in exc.value.detail
        store.set_campaign_status.assert_not_awaited()
    else:
        assert await outbound.set_campaign_status("fixture", request) == {"status": "running"}
        store.set_campaign_status.assert_awaited_once_with("fixture", "running", cancel_pending=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,consent,detail", [(True, False, "Voicemail drop"), (False, True, "Consent gate")])
async def test_start_rejects_enabled_feature_without_recording_after_api_persistence(tmp_path, monkeypatch, enabled, consent, detail):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from src.core.outbound_store import OutboundStore

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    store = OutboundStore(db_path=str(tmp_path / "history.db"))
    monkeypatch.setattr(outbound, "_get_outbound_store", lambda: store)
    monkeypatch.setattr(outbound, "_media_uri_exists", lambda _uri: False)
    app = FastAPI()
    app.include_router(outbound.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture") as api:
        response = await api.post("/outbound/campaigns", json={
            "name": "Synthetic missing recording", "voicemail_drop_enabled": enabled, "consent_enabled": consent,
        })
        assert response.status_code == 200
        campaign_id = response.json()["id"]
        assert response.json()["voicemail_drop_enabled"] == int(enabled)
        await store.import_leads_csv(campaign_id, b"phone_number\n1001\n")
        response = await api.post(f"/outbound/campaigns/{campaign_id}/status", json={"status": "running"})
        assert response.status_code == 400
        assert detail in response.json()["detail"]
        assert (await store.get_campaign(campaign_id))["status"] == "draft"


@pytest.mark.asyncio
async def test_running_campaign_patch_checks_merged_recording_and_preserves_setup_workflow(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from src.core.outbound_store import OutboundStore

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    store = OutboundStore(db_path=str(tmp_path / "history.db"))
    monkeypatch.setattr(outbound, "_get_outbound_store", lambda: store)
    app = FastAPI()
    app.include_router(outbound.router)
    campaign = await store.create_campaign({"name": "Synthetic", "voicemail_drop_enabled": False})
    cid = campaign["id"]
    await store.set_campaign_status(cid, "running")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture") as api:
        response = await api.patch(f"/outbound/campaigns/{cid}", json={"voicemail_drop_enabled": True})
        assert response.status_code == 400
        assert response.json()["detail"] == outbound.VOICEMAIL_RECORDING_REQUIRED
        response = await api.patch(f"/outbound/campaigns/{cid}", json={"voicemail_drop_enabled": True, "voicemail_drop_media_uri": "sound:fixture"})
        assert response.status_code == 200
        response = await api.patch(f"/outbound/campaigns/{cid}", json={"voicemail_drop_enabled": True, "name": "Renamed"})
        assert response.status_code == 200
        assert response.json()["voicemail_drop_media_uri"] == "sound:fixture"
        response = await api.patch(f"/outbound/campaigns/{cid}", json={"voicemail_drop_media_uri": None})
        assert response.status_code == 400
        assert (await store.get_campaign(cid))["voicemail_drop_media_uri"] == "sound:fixture"
        await store.set_campaign_status(cid, "paused")
        response = await api.patch(f"/outbound/campaigns/{cid}", json={"voicemail_drop_enabled": True, "voicemail_drop_media_uri": ""})
        assert response.status_code == 200
        response = await api.patch(f"/outbound/campaigns/{cid}", json={"name": "Finish recording later"})
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_start_revalidates_after_concurrent_patch_changes_api_snapshot(tmp_path, monkeypatch):
    from src.core.outbound_store import OutboundStore

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    path = str(tmp_path / "history.db")
    store, editor = OutboundStore(db_path=path), OutboundStore(db_path=path)
    campaign = await store.create_campaign({"name": "Synthetic stale API snapshot", "voicemail_drop_enabled": False})
    cid = campaign["id"]
    await store.import_leads_csv(cid, b"phone_number\n1001\n")
    original_stats = store.campaign_stats
    async def stats_after_concurrent_edit(campaign_id):
        # The API has already loaded the disabled campaign. A separate writer
        # enables voicemail during setup before the status transaction begins.
        await editor.update_campaign(campaign_id, {"voicemail_drop_enabled": True, "voicemail_drop_media_uri": ""})
        return await original_stats(campaign_id)
    monkeypatch.setattr(store, "campaign_stats", stats_after_concurrent_edit)
    monkeypatch.setattr(outbound, "_get_outbound_store", lambda: store)
    with pytest.raises(HTTPException) as exc:
        await outbound.set_campaign_status(cid, outbound.CampaignStatusRequest(status="running"))
    assert exc.value.status_code == 400
    assert exc.value.detail == outbound.VOICEMAIL_RECORDING_REQUIRED
    assert (await store.get_campaign(cid))["status"] == "draft"
