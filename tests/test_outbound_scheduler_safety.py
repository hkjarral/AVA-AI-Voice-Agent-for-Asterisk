from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine, _outbound_attempt_stale_seconds
from src.tools.context import PreCallContext
from src.tools.http.generic_lookup import GenericHTTPLookupTool, HTTPLookupConfig


def _campaign(**overrides):
    campaign = {
        "id": "campaign-1",
        "timezone": "UTC",
        "daily_window_start_local": "09:00",
        "daily_window_end_local": "17:00",
        "run_start_at_utc": None,
        "run_end_at_utc": None,
    }
    campaign.update(overrides)
    return campaign


def test_scheduled_outbound_metadata_populates_called_number_for_pre_call_lookup():
    session = SimpleNamespace(
        caller_number="6789",
        called_number="unknown",
        outbound_campaign_id=None,
        outbound_lead_id=None,
        outbound_attempt_id=None,
    )

    Engine._apply_outbound_session_metadata(
        session,
        {
            "AAVA_OUTBOUND_PHONE": "+15551234567",
            "AAVA_CAMPAIGN_ID": "campaign-1",
            "AAVA_LEAD_ID": "lead-1",
            "AAVA_ATTEMPT_ID": "attempt-1",
        },
    )

    assert session.caller_number == "+15551234567"
    assert session.called_number == "+15551234567"
    assert session.outbound_campaign_id == "campaign-1"
    assert session.outbound_lead_id == "lead-1"
    assert session.outbound_attempt_id == "attempt-1"

    context = PreCallContext(
        call_id="call-1",
        caller_number=session.caller_number,
        called_number=session.called_number,
        context_name="sales",
    )
    tool = GenericHTTPLookupTool(
        HTTPLookupConfig(
            name="customer_lookup",
            url="https://example.invalid/api/v1/customers?phone={called_number}",
        )
    )
    assert tool._substitute_variables(tool.config.url, context).endswith("phone=+15551234567")


def test_missing_outbound_phone_does_not_overwrite_existing_number_fields():
    session = SimpleNamespace(caller_number="existing-caller", called_number="existing-called")

    Engine._apply_outbound_session_metadata(session, {"AAVA_CAMPAIGN_ID": "campaign-1"})

    assert session.caller_number == "existing-caller"
    assert session.called_number == "existing-called"
    assert session.outbound_campaign_id == "campaign-1"


@pytest.mark.asyncio
async def test_outbound_agent_vars_prefer_ai_agent_and_keep_compatibility_alias():
    calls = []

    class _AriClient:
        async def set_channel_var(self, channel_id, name, value):
            calls.append((channel_id, name, value))

    engine = Engine.__new__(Engine)
    engine.ari_client = _AriClient()

    await engine._set_outbound_agent_channel_vars("channel-1", "sales")

    assert calls == [
        ("channel-1", "AI_AGENT", "sales"),
        ("channel-1", "AI_CONTEXT", "sales"),
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"timezone": "Not/A_Timezone"},
        {"daily_window_start_local": "9:00"},
        {"daily_window_end_local": "24:00"},
        {"daily_window_end_local": "17:99"},
        {"run_start_at_utc": "not-a-timestamp"},
        {"run_end_at_utc": "not-a-timestamp"},
        {
            "run_start_at_utc": "2026-07-19T13:00:00Z",
            "run_end_at_utc": "2026-07-19T12:00:00Z",
        },
    ],
)
def test_campaign_window_rejects_invalid_configuration(overrides):
    engine = Engine.__new__(Engine)
    now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)

    assert engine._outbound_campaign_in_window(_campaign(**overrides), now) is False


def test_campaign_window_fails_closed_on_unexpected_input():
    engine = Engine.__new__(Engine)
    now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)

    assert engine._outbound_campaign_in_window(None, now) is False
    assert engine._outbound_campaign_in_window(_campaign(), now.replace(tzinfo=None)) is False


def test_campaign_window_preserves_valid_and_cross_midnight_behavior():
    engine = Engine.__new__(Engine)

    assert engine._outbound_campaign_in_window(
        _campaign(), datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
    )
    assert engine._outbound_campaign_in_window(
        _campaign(daily_window_start_local="22:00", daily_window_end_local="06:00"),
        datetime(2026, 7, 19, 23, 0, tzinfo=timezone.utc),
    )
    assert engine._outbound_campaign_in_window(
        _campaign(daily_window_start_local="00:00", daily_window_end_local="00:00"),
        datetime(2026, 7, 19, 23, 0, tzinfo=timezone.utc),
    )


def test_campaign_window_enforces_absolute_boundaries():
    engine = Engine.__new__(Engine)
    campaign = _campaign(
        daily_window_start_local="00:00",
        daily_window_end_local="00:00",
        run_start_at_utc="2026-07-19T10:00:00Z",
        run_end_at_utc="2026-07-19T14:00:00Z",
    )

    assert not engine._outbound_campaign_in_window(
        campaign, datetime(2026, 7, 19, 9, 59, tzinfo=timezone.utc)
    )
    assert engine._outbound_campaign_in_window(
        campaign, datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)
    )
    assert engine._outbound_campaign_in_window(
        campaign, datetime(2026, 7, 19, 14, 0, tzinfo=timezone.utc)
    )
    assert not engine._outbound_campaign_in_window(
        campaign, datetime(2026, 7, 19, 14, 1, tzinfo=timezone.utc)
    )


def test_stale_attempt_timeout_uses_one_validated_default(monkeypatch):
    monkeypatch.delenv("AAVA_OUTBOUND_ATTEMPT_STALE_SECONDS", raising=False)
    assert _outbound_attempt_stale_seconds() == 120.0

    monkeypatch.setenv("AAVA_OUTBOUND_ATTEMPT_STALE_SECONDS", "45")
    assert _outbound_attempt_stale_seconds() == 45.0

    monkeypatch.setenv("AAVA_OUTBOUND_ATTEMPT_STALE_SECONDS", "1")
    assert _outbound_attempt_stale_seconds() == 10.0

    monkeypatch.setenv("AAVA_OUTBOUND_ATTEMPT_STALE_SECONDS", "invalid")
    assert _outbound_attempt_stale_seconds() == 120.0


@pytest.mark.asyncio
async def test_active_human_session_is_not_double_counted_against_capacity():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine._outbound_attempt_meta_by_attempt_id = {
        "attempt-1": {"attempt_id": "attempt-1", "campaign_id": "campaign-1"}
    }

    active = CallSession(
        call_id="channel-1",
        caller_channel_id="channel-1",
        provider_name="local",
    )
    active.is_outbound = True
    active.outbound_campaign_id = "campaign-1"
    active.outbound_attempt_id = "attempt-1"
    await engine.session_store.upsert_call(active)

    capacity, tracked, additional_active = await engine._outbound_campaign_capacity("campaign-1", 2)

    assert tracked == 1
    assert additional_active == 0
    assert capacity == 1


@pytest.mark.asyncio
async def test_untracked_active_session_still_consumes_capacity():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine._outbound_attempt_meta_by_attempt_id = {
        "attempt-1": {"attempt_id": "attempt-1", "campaign_id": "campaign-1"}
    }

    active = CallSession(
        call_id="channel-2",
        caller_channel_id="channel-2",
        provider_name="local",
    )
    active.is_outbound = True
    active.outbound_campaign_id = "campaign-1"
    active.outbound_attempt_id = "attempt-not-in-memory"
    await engine.session_store.upsert_call(active)

    capacity, tracked, additional_active = await engine._outbound_campaign_capacity("campaign-1", 2)

    assert tracked == 1
    assert additional_active == 1
    assert capacity == 0
