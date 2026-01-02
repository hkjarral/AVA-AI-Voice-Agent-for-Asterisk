import pytest


@pytest.mark.asyncio
async def test_outbound_store_campaign_import_and_leasing(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "call_history.db")

    from src.core.outbound_store import OutboundStore

    store = OutboundStore(db_path=db_path)

    campaign = await store.create_campaign(
        {
            "name": "Test Campaign",
            "timezone": "UTC",
            "daily_window_start_local": "09:00",
            "daily_window_end_local": "17:00",
            "max_concurrent": 1,
            "min_interval_seconds_between_calls": 0,
            "default_context": "demo",
            "voicemail_drop_mode": "upload",
            "voicemail_drop_media_uri": "sound:ai-generated/test-vm",
        }
    )
    campaign_id = campaign["id"]

    csv_bytes = (
        "phone_number,custom_vars,context,timezone\n"
        '+15551230001,"{""name"":""Alice""}",demo,UTC\n'
        '+15551230001,"{""name"":""Alice""}",demo,UTC\n'
        '+15551230002,"{""name"":""Bob""}",demo,UTC\n'
    ).encode("utf-8")

    imported = await store.import_leads_csv(campaign_id, csv_bytes, skip_existing=True, max_error_rows=20)
    assert imported["accepted"] == 2
    assert imported["duplicates"] == 1
    assert imported["rejected"] == 0

    leads_page = await store.list_leads(campaign_id, page=1, page_size=50)
    assert leads_page["total"] == 2
    lead_ids = {l["id"] for l in leads_page["leads"]}
    assert len(lead_ids) == 2

    leased = await store.lease_pending_leads(campaign_id, limit=1, lease_seconds=60)
    assert len(leased) == 1
    lead = leased[0]
    assert lead["state"] == "leased"
    assert lead["phone_number"].startswith("+1555")
    assert isinstance(lead.get("custom_vars"), dict)

    marked = await store.mark_lead_dialing(lead["id"])
    assert marked is True

    # Second mark should fail (not leased anymore)
    marked2 = await store.mark_lead_dialing(lead["id"])
    assert marked2 is False

    await store.set_lead_state(lead["id"], state="completed", last_outcome="answered_human")

    # Leasing again should pick the other pending lead.
    leased2 = await store.lease_pending_leads(campaign_id, limit=1, lease_seconds=60)
    assert len(leased2) == 1
    assert leased2[0]["id"] != lead["id"]
