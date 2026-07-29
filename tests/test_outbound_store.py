import sqlite3

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
    assert campaign["agent_routing_method"] == "ai_agent"

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


@pytest.mark.asyncio
async def test_outbound_store_prefers_agent_csv_header_and_accepts_context_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")

    from src.core.outbound_store import OutboundStore

    store = OutboundStore(db_path=str(tmp_path / "call_history.db"))
    campaign = await store.create_campaign(
        {
            "name": "Agent Routing Campaign",
            "timezone": "UTC",
            "daily_window_start_local": "09:00",
            "daily_window_end_local": "17:00",
            "default_context": "sales",
        }
    )

    preferred = await store.import_leads_csv(
        campaign["id"],
        b"phone_number,agent\n+15551230011,support\n",
        known_agents=["sales", "support"],
    )
    compatible = await store.import_leads_csv(
        campaign["id"],
        b"phone_number,context\n+15551230012,sales\n",
        known_agents=["sales", "support"],
    )
    compatible_display_name = await store.import_leads_csv(
        campaign["id"],
        b"phone_number,context\n+15551230015,Sales East Team\n",
        known_agents=["sales", "support"],
        known_contexts=["sales", "support", "Sales East Team"],
    )
    conflicting = await store.import_leads_csv(
        campaign["id"],
        b"phone_number,agent,context\n+15551230014,support,sales\n",
        known_agents=["sales", "support"],
    )
    unknown = await store.import_leads_csv(
        campaign["id"],
        b"phone_number,agent\n+15551230013,missing-agent\n",
        known_agents=["sales", "support"],
    )

    assert preferred["accepted"] == 1
    assert compatible["accepted"] == 1
    assert compatible_display_name["accepted"] == 1
    assert conflicting["accepted"] == 1
    assert unknown["accepted"] == 1
    assert "Unknown Agent slug" in unknown["warnings"][0]["warning_reason"]

    leads = await store.list_leads(campaign["id"], page=1, page_size=50)
    agents_by_phone = {lead["phone_number"]: lead["context_override"] for lead in leads["leads"]}
    routing_by_phone = {
        lead["phone_number"]: lead["agent_routing_method"] for lead in leads["leads"]
    }
    assert agents_by_phone["+15551230011"] == "support"
    assert agents_by_phone["+15551230012"] == "sales"
    assert agents_by_phone["+15551230015"] == "Sales East Team"
    assert agents_by_phone["+15551230013"] == "sales"
    assert agents_by_phone["+15551230014"] == "support"
    assert routing_by_phone["+15551230011"] == "ai_agent"
    assert routing_by_phone["+15551230012"] == "ai_context"
    assert routing_by_phone["+15551230015"] == "ai_context"
    assert routing_by_phone["+15551230013"] == "ai_agent"
    assert routing_by_phone["+15551230014"] == "ai_agent"


@pytest.mark.asyncio
async def test_empty_agent_set_rejects_unknown_and_campaign_edits_preserve_legacy_routing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")

    from src.core.outbound_store import OutboundStore

    db_path = str(tmp_path / "call_history.db")
    store = OutboundStore(db_path=db_path)
    campaign = await store.create_campaign(
        {
            "name": "Legacy campaign",
            "timezone": "UTC",
            "default_context": "legacy_sales",
        }
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE outbound_campaigns SET agent_routing_method='ai_context' WHERE id=?",
            (campaign["id"],),
        )
        conn.commit()

    imported = await store.import_leads_csv(
        campaign["id"],
        b"phone_number,agent\n+15551230016,missing-agent\n",
        known_agents=[],
        known_contexts=[],
    )
    assert "Unknown Agent slug" in imported["warnings"][0]["warning_reason"]

    unchanged_selector = await store.update_campaign(
        campaign["id"],
        {"name": "Renamed", "default_context": "legacy_sales"},
    )
    assert unchanged_selector["agent_routing_method"] == "ai_context"

    canonical_selector = await store.update_campaign(
        campaign["id"],
        {"default_context": "sales"},
    )
    assert canonical_selector["agent_routing_method"] == "ai_agent"


def test_legacy_agent_selector_rejects_unicode_controls():
    from src.core.outbound_store import _is_safe_legacy_agent_selector

    assert _is_safe_legacy_agent_selector("Ventes – Montréal")
    assert not _is_safe_legacy_agent_selector("Sales\u0085Team")


def test_outbound_schema_migration_marks_existing_selectors_as_ai_context(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")

    from src.core.outbound_store import OutboundStore

    store = OutboundStore(db_path=str(tmp_path / "current.db"))
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE outbound_campaigns (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE outbound_leads (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE outbound_attempts (id TEXT PRIMARY KEY)")
        store._ensure_schema_sync(conn)
        conn.execute("INSERT INTO outbound_campaigns (id) VALUES ('legacy-campaign')")
        conn.execute("INSERT INTO outbound_leads (id) VALUES ('legacy-lead')")

        campaign_method = conn.execute(
            "SELECT agent_routing_method FROM outbound_campaigns"
        ).fetchone()[0]
        lead_method = conn.execute(
            "SELECT agent_routing_method FROM outbound_leads"
        ).fetchone()[0]
    finally:
        conn.close()

    assert campaign_method == "ai_context"
    assert lead_method == "ai_context"
