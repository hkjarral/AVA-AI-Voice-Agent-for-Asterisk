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
async def test_outbound_store_recovers_active_attempt_lead_context(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")

    from src.core.outbound_store import OutboundStore

    db_path = str(tmp_path / "call_history.db")
    store = OutboundStore(db_path=db_path)
    campaign = await store.create_campaign(
        {
            "name": "Restart Recovery",
            "timezone": "UTC",
            "default_context": "sales",
        }
    )
    imported = await store.import_leads_csv(
        campaign["id"],
        (
            'name,phone_number,agent,custom_vars\n'
            'Alice,+15551230021,sales,"{\"\"task\"\":\"\"confirm\"\"}"\n'
        ).encode("utf-8"),
        known_agents=["sales"],
    )
    assert imported["accepted"] == 1
    lead = (await store.list_leads(campaign["id"]))["leads"][0]
    attempt_id = await store.create_attempt(
        campaign["id"],
        lead["id"],
        context="sales",
        provider="deepgram",
    )

    recovered = await store.get_active_attempt_runtime_context(attempt_id)

    assert recovered == {
        "attempt_id": attempt_id,
        "campaign_id": campaign["id"],
        "lead_id": lead["id"],
        "context": "sales",
        "provider": "deepgram",
        "channel_id": None,
        "phone_number": "+15551230021",
        "lead_name": "Alice",
        "custom_vars_valid": True,
        "custom_vars": {"task": "confirm"},
        "routing_method": "ai_agent",
    }

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE outbound_leads SET custom_vars_json='not-json' WHERE id=?",
            (lead["id"],),
        )
        conn.commit()
    invalid = await store.get_active_attempt_runtime_context(attempt_id)
    assert invalid is not None
    assert invalid["lead_id"] == lead["id"]
    assert invalid["custom_vars"] == {}
    assert invalid["custom_vars_valid"] is False

    await store.finish_attempt(attempt_id, outcome="error")
    assert await store.get_active_attempt_runtime_context(attempt_id) is None


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


@pytest.mark.asyncio
@pytest.mark.parametrize("cleared", ["", "  ", None])
async def test_running_voicemail_edits_preserve_valid_merged_recording(tmp_path, monkeypatch, cleared):
    from src.core.outbound_store import OutboundStore

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    store = OutboundStore(db_path=str(tmp_path / "history.db"))
    campaign = await store.create_campaign({"name": "Synthetic", "voicemail_drop_enabled": False})
    cid = campaign["id"]
    await store.set_campaign_status(cid, "running")
    with pytest.raises(ValueError, match="Voicemail drop is enabled"):
        await store.update_campaign(cid, {"voicemail_drop_enabled": True})
    assert (await store.get_campaign(cid))["voicemail_drop_enabled"] == 0
    await store.update_campaign(cid, {"voicemail_drop_enabled": True, "voicemail_drop_media_uri": "sound:fixture"})
    # An omitted URI inherits the saved recording; unrelated edits remain possible.
    saved = await store.update_campaign(cid, {"voicemail_drop_enabled": True, "name": "Renamed"})
    assert saved["voicemail_drop_media_uri"] == "sound:fixture"
    with pytest.raises(ValueError, match="Voicemail drop is enabled"):
        await store.update_campaign(cid, {"voicemail_drop_media_uri": cleared})
    assert (await store.get_campaign(cid))["voicemail_drop_media_uri"] == "sound:fixture"
    saved = await store.update_campaign(cid, {"voicemail_drop_enabled": False, "voicemail_drop_media_uri": cleared})
    assert saved["voicemail_drop_enabled"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "paused"])
async def test_incomplete_voicemail_setup_remains_editable_but_cannot_start(tmp_path, monkeypatch, status):
    from src.core.outbound_store import OutboundStore

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    store = OutboundStore(db_path=str(tmp_path / "history.db"))
    campaign = await store.create_campaign({"name": "Setup", "voicemail_drop_enabled": False})
    cid = campaign["id"]
    await store.set_campaign_status(cid, status)
    await store.update_campaign(cid, {"voicemail_drop_enabled": True, "voicemail_drop_media_uri": ""})
    await store.update_campaign(cid, {"name": "Finish recording later"})
    with pytest.raises(ValueError, match="Voicemail drop is enabled"):
        await store.set_campaign_status(cid, "running")
    assert (await store.get_campaign(cid))["status"] == status
    await store.update_campaign(cid, {"voicemail_drop_media_uri": "sound:fixture"})
    assert (await store.set_campaign_status(cid, "running"))["status"] == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", ["enable", "clear"])
async def test_start_and_patch_serialize_voicemail_invariant_across_store_instances(tmp_path, monkeypatch, edit):
    import asyncio
    import threading
    from src.core.outbound_store import OutboundStore

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    path = str(tmp_path / "history.db")
    starter, editor = OutboundStore(db_path=path), OutboundStore(db_path=path)
    campaign = await starter.create_campaign({
        "name": "Race", "voicemail_drop_enabled": edit == "clear",
        "voicemail_drop_media_uri": "sound:fixture" if edit == "clear" else "",
    })
    start_at_write, edit_at_transaction, release_start, timed_out = (threading.Event() for _ in range(4))
    original_start_connection, original_edit_connection = starter._get_connection, editor._get_connection

    def start_connection():
        conn = original_start_connection()
        def trace(sql):
            if sql.startswith("UPDATE outbound_campaigns SET status"):
                start_at_write.set()
                if not release_start.wait(5):
                    timed_out.set()
        conn.set_trace_callback(trace)
        return conn

    def edit_connection():
        conn = original_edit_connection()
        conn.set_trace_callback(lambda sql: edit_at_transaction.set() if sql.startswith("BEGIN") else None)
        return conn

    monkeypatch.setattr(starter, "_get_connection", start_connection)
    monkeypatch.setattr(editor, "_get_connection", edit_connection)
    start = asyncio.create_task(starter.set_campaign_status(campaign["id"], "running"))
    change = None
    try:
        assert await asyncio.to_thread(start_at_write.wait, 5)
        payload = {"voicemail_drop_enabled": True} if edit == "enable" else {"voicemail_drop_media_uri": ""}
        change = asyncio.create_task(editor.update_campaign(campaign["id"], payload))
        assert await asyncio.to_thread(edit_at_transaction.wait, 5)
    finally:
        release_start.set()
        results = await asyncio.gather(start, *([change] if change else []), return_exceptions=True)
    assert not timed_out.is_set()
    assert results[0]["status"] == "running"
    assert isinstance(results[1], ValueError)
    saved = await starter.get_campaign(campaign["id"])
    assert saved["voicemail_drop_enabled"] == int(edit == "clear")
    assert saved["voicemail_drop_media_uri"] == ("sound:fixture" if edit == "clear" else None)


async def _synthetic_dialed_attempt(store):
    campaign = await store.create_campaign({"name": "Synthetic recovery", "voicemail_drop_enabled": False})
    await store.import_leads_csv(campaign["id"], b"phone_number\n1001\n")
    lead = (await store.lease_pending_leads(campaign["id"], limit=1))[0]
    attempt = await store.create_attempt(campaign["id"], lead["id"])
    assert await store.mark_lead_dialing(lead["id"], attempt_id=attempt)
    await store.set_lead_state(lead["id"], state="amd_pending")
    return campaign, lead["id"], attempt


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["retry", "restart"])
@pytest.mark.parametrize("replacement", ["none", "recycled", "leased", "dialing", "new_attempt", "canceled", "failed"])
async def test_terminal_completion_never_overwrites_replacement_lead_generation(tmp_path, monkeypatch, path, replacement):
    from src.core.outbound_store import OutboundStore

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "history.db")
    store = OutboundStore(db_path=db_path)
    campaign, lead_id, attempt = await _synthetic_dialed_attempt(store)
    await store.finish_attempt(attempt, outcome="machine_detected", only_if_unfinished=True)
    if replacement in ("recycled", "leased", "dialing", "new_attempt"):
        await store.recycle_lead(lead_id)
        if replacement != "recycled":
            await store.lease_pending_leads(campaign["id"], limit=1)
        if replacement == "dialing":
            assert await store.mark_lead_dialing(lead_id)
        if replacement == "new_attempt":
            newer = await store.create_attempt(campaign["id"], lead_id)
            assert await store.mark_lead_dialing(lead_id, attempt_id=newer)
            await store.set_lead_state(lead_id, state="amd_pending")
    elif replacement in ("canceled", "failed"):
        await store.set_lead_state(lead_id, state=replacement, last_outcome="operator decision")
    before = (await store.list_leads(campaign["id"]))["leads"][0]
    # A fresh owner represents restart; no recovery depends on surviving RAM.
    recovered = OutboundStore(db_path=db_path)
    if path == "restart":
        await recovered.cleanup_stale_attempts_and_leads()
    else:
        await recovered.set_lead_state(lead_id, state="completed", last_outcome="machine_detected", only_if_active=True, expected_attempt_id=attempt)
    after = (await recovered.list_leads(campaign["id"]))["leads"][0]
    if replacement == "none":
        assert after["state"] == "completed"
        assert after["last_outcome"] == "machine_detected"
    else:
        assert after["state"] == before["state"]
        assert after["last_outcome"] == before["last_outcome"]
        assert after["leased_until_utc"] == before["leased_until_utc"]
        assert after["last_attempt_at_utc"] == before["last_attempt_at_utc"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["voicemail_dropped", "machine_detected", "error"])
async def test_restart_repairs_partial_lead_write_from_latest_durable_terminal_result(tmp_path, monkeypatch, outcome):
    from src.core.outbound_store import OutboundStore

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    path = str(tmp_path / "history.db")
    store = OutboundStore(db_path=path)
    campaign, lead, attempt = await _synthetic_dialed_attempt(store)
    await store.finish_attempt(attempt, outcome=outcome, amd_status="MACHINE", only_if_unfinished=True)
    terminal = (await store.list_attempts(campaign["id"]))["attempts"][0]
    # Replaying a possibly committed write must not change its terminal result/time.
    await store.finish_attempt(attempt, outcome="error", only_if_unfinished=True)
    assert (await store.list_attempts(campaign["id"]))["attempts"][0] == terminal
    fresh = OutboundStore(db_path=path)
    result = await fresh.cleanup_stale_attempts_and_leads()
    assert result["leads_repaired"] == 1
    saved = (await fresh.list_leads(campaign["id"]))["leads"][0]
    assert saved["state"] == ("failed" if outcome == "error" else "completed")
    assert saved["last_outcome"] == outcome
    assert (await fresh.list_attempts(campaign["id"]))["attempts"][0] == terminal
    assert (await fresh.cleanup_stale_attempts_and_leads())["leads_repaired"] == 0


@pytest.mark.asyncio
async def test_durable_stale_recovery_excludes_live_owned_attempts_and_newer_leases(tmp_path, monkeypatch):
    from src.core.outbound_store import OutboundStore

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    path = str(tmp_path / "history.db")
    store = OutboundStore(db_path=path)
    campaign, lead, attempt = await _synthetic_dialed_attempt(store)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE outbound_attempts SET started_at_utc='2000-01-01T00:00:00+00:00' WHERE id=?", (attempt,))
        conn.execute("UPDATE outbound_leads SET last_attempt_at_utc='2000-01-01T00:00:00+00:00' WHERE id=?", (lead,))
    assert (await store.cleanup_stale_attempts_and_leads(exclude_attempt_ids=[attempt]))["attempts_closed"] == 0
    assert (await store.list_attempts(campaign["id"]))["attempts"][0]["ended_at_utc"] is None
    await store.recycle_lead(lead)
    await store.lease_pending_leads(campaign["id"], limit=1)
    await store.cleanup_stale_attempts_and_leads()
    assert (await store.list_attempts(campaign["id"]))["attempts"][0]["outcome"] == "error"
    assert (await store.list_leads(campaign["id"]))["leads"][0]["state"] == "leased"


@pytest.mark.asyncio
async def test_completion_lead_guard_respects_an_already_different_terminal_result(tmp_path, monkeypatch):
    from src.core.outbound_store import OutboundStore

    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    store = OutboundStore(db_path=str(tmp_path / "history.db"))
    campaign, lead, attempt = await _synthetic_dialed_attempt(store)
    await store.finish_attempt(attempt, outcome="error", only_if_unfinished=True)
    await store.set_lead_state(lead, state="completed", last_outcome="machine_detected", only_if_active=True, expected_attempt_id=attempt)
    assert (await store.list_leads(campaign["id"]))["leads"][0]["state"] == "amd_pending"
    await store.cleanup_stale_attempts_and_leads()
    saved = (await store.list_leads(campaign["id"]))["leads"][0]
    assert saved["state"] == "failed"
    assert saved["last_outcome"] == "error"
