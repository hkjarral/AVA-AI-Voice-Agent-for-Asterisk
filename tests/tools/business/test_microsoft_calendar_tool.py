"""Tests for Microsoft Calendar tool runtime behavior."""

import asyncio
import copy
import threading
from datetime import datetime, timezone

from unittest.mock import Mock, patch

import pytest

from src.tools.base import ToolCategory
from src.tools.business.microsoft_calendar import MicrosoftCalendarTool
from src.tools.business.ms_graph_client import MicrosoftGraphApiError


class FakeMicrosoftClient:
    """Synthetic named-calendar store; no Microsoft account or network access."""

    def __init__(self):
        self.created = []
        self.deleted = []
        self.updated = []
        self.delete_results = []
        self.delete_etags = []
        self.events = []
        self.creation_error = None
        self.update_error = None

    def list_calendar_view(self, start, end):
        def dt(value):
            parsed = datetime.fromisoformat(value)
            return (
                parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
            )

        return [
            copy.deepcopy(e)
            for e in self.events
            if dt(e["start"]["dateTime"]) < end and start < dt(e["end"]["dateTime"])
        ]

    def create_event(self, summary, description, start_utc, end_utc, **kwargs):
        self.created.append((summary, description, start_utc, end_utc, kwargs))
        event = {
            "id": (
                "ms_event_123"
                if len(self.created) == 1
                else f"ms_event_{len(self.created)}"
            ),
            "webLink": "https://example.test/event",
            "subject": summary,
            "body": {"contentType": "text", "content": description},
            "start": {"dateTime": start_utc.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end_utc.isoformat(), "timeZone": "UTC"},
            "transactionId": kwargs.get("transaction_id"),
            "showAs": "busy",
            "isOrganizer": True,
            "attendees": [
                {"emailAddress": {"address": email}}
                for email in kwargs.get("attendee_emails", [])
            ],
            "@odata.etag": 'W/"version1"',
        }
        self.events.append(event)
        if self.creation_error:
            raise self.creation_error
        return copy.deepcopy(event)

    def delete_event(self, event_id, etag=None):
        self.deleted.append(event_id)
        self.delete_etags.append(etag)
        if self.delete_results:
            outcome = self.delete_results.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if not outcome:
                return False
        self.events = [e for e in self.events if e["id"] != event_id]
        return True

    def get_event(self, event_id):
        return next(
            (copy.deepcopy(e) for e in self.events if e["id"] == event_id), None
        )

    def update_event(self, event_id, body, etag=None):
        self.updated.append((event_id, body, etag))
        event = next(e for e in self.events if e["id"] == event_id)
        event.update(copy.deepcopy(body))
        if self.update_error:
            raise self.update_error
        return copy.deepcopy(event)


@pytest.fixture(autouse=True)
def booking_clock(monkeypatch):
    monkeypatch.setattr(
        "src.tools.business.microsoft_booking.utc_now",
        lambda: datetime(2026, 4, 28, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "src.tools.business.microsoft_calendar.utc_now",
        lambda: datetime(2026, 4, 28, tzinfo=timezone.utc),
    )


@pytest.fixture
def ms_config():
    return {
        "enabled": True,
        "accounts": {
            "default": {
                "tenant_id": "contoso.onmicrosoft.com",
                "client_id": "11111111-1111-1111-1111-111111111111",
                "token_cache_path": "/app/project/secrets/microsoft-calendar-default-token-cache.json",
                "user_principal_name": "scheduler@contoso.com",
                "calendar_id": "calendar-a",
                "timezone": "America/Los_Angeles",
            }
        },
    }


@pytest.fixture
def ms_context(tool_context, ms_config):
    tool_context.get_config_value = Mock(return_value=ms_config)
    return tool_context


def test_definition_name_and_category():
    tool = MicrosoftCalendarTool()
    definition = tool.definition
    assert definition.name == "microsoft_calendar"
    assert definition.category == ToolCategory.BUSINESS
    assert "get_free_slots" in definition.input_schema["properties"]["action"]["enum"]


def test_agent_scoped_config_ignores_stale_context_overlay(tool_context):
    tool = MicrosoftCalendarTool()
    tool_context.context_name = "sales"
    tool_context.get_config_value = Mock(
        side_effect=lambda path, default=None: (
            {
                "enabled": True,
                "selected_accounts": ["agent-account"],
                "_agent_scope_resolved": True,
            }
            if path == "tools.microsoft_calendar"
            else {"selected_accounts": ["stale-context-account"]}
        )
    )
    config = tool._get_config(tool_context)
    assert config["selected_accounts"] == ["agent-account"]
    assert "_agent_scope_resolved" not in config


def test_empty_calendar_id_is_not_rewritten_to_google_primary_alias():
    tool = MicrosoftCalendarTool()
    account = tool._account_config(
        {
            "tenant_id": "contoso.onmicrosoft.com",
            "client_id": "11111111-1111-1111-1111-111111111111",
            "token_cache_path": "/app/project/secrets/microsoft-calendar-default-token-cache.json",
            "user_principal_name": "scheduler@contoso.com",
            "calendar_id": "",
            "timezone": "America/Los_Angeles",
        }
    )
    assert account.calendar_id == ""
    assert "calendar_id" in (tool._validate_account(account) or "")


@pytest.mark.parametrize(
    ("error_code", "expected_substring"),
    [
        ("auth_expired", "not configured"),
        ("forbidden_calendar", "forbidden"),
        ("calendar_not_found", "not configured"),
        ("graph_unavailable", "unavailable"),
    ],
)
def test_error_messages_match_scheduling_recovery_substrings(
    error_code, expected_substring
):
    tool = MicrosoftCalendarTool()
    result = tool._map_api_error(
        MicrosoftGraphApiError("raw graph failure", error_code=error_code, status=503),
        "Could not reach Microsoft Calendar",
    )
    assert result["status"] == "error"
    assert expected_substring in result["message"].lower()


@pytest.mark.asyncio
async def test_freebusy_mode_uses_working_hours_without_open_events(ms_context):
    tool = MicrosoftCalendarTool()
    fake = FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        result = await tool.execute(
            {
                "action": "get_free_slots",
                "time_min": "2026-04-29T00:00:00",
                "time_max": "2026-04-30T00:00:00",
                "duration": 30,
            },
            ms_context,
        )
    assert result["status"] == "success"
    assert result["availability_mode"] == "freebusy"
    assert result["reason"] == "available"
    assert result["slot_duration_minutes"] == 30
    assert len(result["slots"]) == 3
    assert result["slots_truncated"] is True


@pytest.mark.asyncio
async def test_create_event_keeps_event_id_out_of_spoken_message(ms_context):
    tool = MicrosoftCalendarTool()
    fake = FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        result = await tool.execute(
            {
                "action": "create_event",
                "summary": "Consultation",
                "start_datetime": "2026-04-29T10:00:00",
                "end_datetime": "2026-04-29T10:30:00",
            },
            ms_context,
        )
    assert result["status"] == "success"
    assert result["message"] == "Event created."
    assert "ms_event_123" not in result["message"]
    # Post-83b0b2e2: agent_hint deliberately does NOT echo the opaque event_id.
    # Real-time speech-to-speech models can't reliably reproduce long ids
    # across conversation turns, so we tell the model to omit event_id on
    # delete_event and rely on server-side per-call resolution. The id stays
    # addressable on the structured response (`result["event_id"]`) for
    # code paths that genuinely need it.
    assert "ms_event_123" not in result["agent_hint"]
    assert "NO event_id" in result["agent_hint"]
    assert result["event_id"] == "ms_event_123"


@pytest.mark.asyncio
async def test_create_event_refuses_overlong_duration(ms_context):
    tool = MicrosoftCalendarTool()
    fake = FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        result = await tool.execute(
            {
                "action": "create_event",
                "summary": "Consultation",
                "start_datetime": "2026-04-29T10:00:00",
                "end_datetime": "2026-04-29T18:00:00",
            },
            ms_context,
        )
    assert result["status"] == "error"
    assert result["error_code"] == "duration_too_long"
    assert fake.created == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_id", ["hallucinated_id", "AAMkADgzYzQ3YjQy_hallucinated"]
)
async def test_delete_does_not_try_an_untracked_id(ms_context, event_id):
    tool = MicrosoftCalendarTool()
    fake = FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        created = await tool.execute(booking(), ms_context)
        deleted = await tool.execute(
            {
                "action": "delete_event",
                "event_id": event_id,
                "cancellation_confirmed": True,
            },
            ms_context,
        )
    assert created["event_id"] == "ms_event_123"
    assert deleted["error_code"] == "booking_mismatch"
    assert fake.deleted == []


@pytest.mark.asyncio
async def test_delete_event_with_no_event_id_resolves_from_same_call_cache(ms_context):
    """Confirmed same-call cancellation resolves its tracked id server-side."""
    tool = MicrosoftCalendarTool()
    fake = FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        created = await tool.execute(
            {
                "action": "create_event",
                "summary": "Consultation",
                "start_datetime": "2026-04-29T10:00:00",
                "end_datetime": "2026-04-29T10:30:00",
            },
            ms_context,
        )
        deleted = await tool.execute(
            {"action": "delete_event", "cancellation_confirmed": True},
            ms_context,
        )
    assert created["event_id"] == "ms_event_123"
    assert deleted["status"] == "success"
    assert deleted["event_id"] == "ms_event_123"
    # Single delete call against the tracked id — no hallucinated-id round
    # trip because the model didn't supply an event_id at all.
    assert fake.deleted == ["ms_event_123"]


@pytest.mark.asyncio
async def test_delete_event_no_event_id_and_no_tracked_event_returns_error(ms_context):
    """Defensive coverage: the no-event_id ergonomic path requires a same-
    call create to fall back on. Without one, the tool returns a clear
    error instead of silently picking a stale id from a different call.
    """
    tool = MicrosoftCalendarTool()
    fake = FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        result = await tool.execute(
            {"action": "delete_event", "cancellation_confirmed": True},
            ms_context,
        )
    assert result["status"] == "error"
    assert result["error_code"] == "staff_assistance_required"
    assert fake.deleted == []


def booking(**kwargs):
    return {
        "action": "create_event",
        "summary": "Consultation",
        "start_datetime": "2026-04-29T13:00:00-07:00",
        "end_datetime": "2026-04-29T13:30:00-07:00",
        **kwargs,
    }


def busy_event(
    start="2026-04-29T20:00:00+00:00", end="2026-04-29T20:30:00+00:00", **kwargs
):
    return {
        "id": "other-event",
        "subject": "Reserved",
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": end, "timeZone": "UTC"},
        "showAs": "busy",
        **kwargs,
    }


@pytest.fixture
def phoenix(ms_context, ms_config):
    ms_config.update(
        enforce_booking_limits=True,
        working_hours_start=8,
        working_hours_end=17,
        working_days=[0, 1, 2, 3, 4],
    )
    ms_config["accounts"]["default"]["timezone"] = "America/Phoenix"
    return ms_context


@pytest.mark.asyncio
async def test_truncated_morning_suggestions_do_not_hide_requested_1300(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        suggestions = await tool.execute(
            {
                "action": "get_free_slots",
                "time_min": "2026-04-29T00:00:00",
                "time_max": "2026-04-30T00:00:00",
                "duration": 30,
            },
            phoenix,
        )
        requested = await tool.execute(
            {
                "action": "check_availability",
                "start_datetime": "2026-04-29T13:00:00",
                "end_datetime": "2026-04-29T13:30:00",
            },
            phoenix,
        )
        created = await tool.execute(booking(), phoenix)
    assert [s[11:16] for s in suggestions["slots"]] == ["08:00", "08:30", "09:00"]
    assert suggestions["slots_truncated"] and suggestions["total_slots_available"] == 18
    assert suggestions["slots_returned"] == 3
    assert "Never infer" in suggestions["agent_hint"]
    assert requested["available"] is True and created["status"] == "success"
    assert fake.created[0][2].hour == 20


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "end", "available"),
    [
        ("2026-04-29T08:00:00", "2026-04-29T08:30:00", True),
        ("2026-04-29T16:30:00", "2026-04-29T17:00:00", True),
        ("2026-04-29T16:45:00", "2026-04-29T17:15:00", False),
        ("2026-04-29T07:59:00", "2026-04-29T08:29:00", False),
        ("2026-05-02T13:00:00", "2026-05-02T13:30:00", False),
        ("2026-04-29T13:15:00", "2026-04-29T13:45:00", True),
        ("2026-04-29T20:00:00Z", "2026-04-29T20:30:00Z", True),
    ],
)
async def test_exact_boundaries_offsets_and_off_grid_times(
    phoenix, start, end, available
):
    tool = MicrosoftCalendarTool()
    with patch.object(tool, "_client_for_config", return_value=FakeMicrosoftClient()):
        result = await tool.execute(
            {
                "action": "check_availability",
                "start_datetime": start,
                "end_datetime": end,
            },
            phoenix,
        )
    assert result["available"] is available


@pytest.mark.asyncio
async def test_create_rechecks_selected_calendar_after_availability(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        assert (
            await tool.execute(
                {**booking(), "action": "check_availability", "summary": "ignored"},
                phoenix,
            )
        )["available"]
        fake.events.append(busy_event())
        result = await tool.execute(booking(), phoenix)
    assert result["error_code"] == "slot_busy" and not fake.created


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("emails", "code"),
    [
        (["bad email"], "invalid_attendee_email"),
        (["a@b"], "invalid_attendee_email"),
        (["caller@example.com\r\nBcc:other@example.com"], "invalid_attendee_email"),
        ("caller@example.com", "invalid_attendee_email"),
        ([None], "invalid_attendee_email"),
        (["caller@example.com"], "confirmation_required"),
    ],
)
async def test_bad_or_unconfirmed_emails_never_reach_graph(
    phoenix, ms_config, emails, code
):
    ms_config["invitations_enabled"] = True
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        result = await tool.execute(booking(attendee_emails=emails), phoenix)
    assert result["error_code"] == code and not fake.created


@pytest.mark.asyncio
async def test_invitation_policy_consent_and_plain_text_body(phoenix, ms_config):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    args = booking(
        attendee_emails=["Caller@example.com", "caller@example.com"],
        booking_confirmed=True,
        invitation_confirmed=True,
        caller_name="Pat",
        meeting_purpose="Service consultation",
        confirmed_notes="Caller reports intermittent issues.",
    )
    with patch.object(tool, "_client_for_config", return_value=fake):
        assert (await tool.execute(args, phoenix))[
            "error_code"
        ] == "invitations_disabled"
        ms_config.update(
            invitations_enabled=True,
            business_name="Example Business",
            location="Phone consultation",
        )
        assert (await tool.execute({**args, "invitation_confirmed": False}, phoenix))[
            "error_code"
        ] == "confirmation_required"
        result = await tool.execute(args, phoenix)
    assert fake.created[0][4]["attendee_emails"] == ["Caller@example.com"]
    assert "Hello Pat" in fake.created[0][1] and "13:00–13:30" in fake.created[0][1]
    assert "Example Business" in fake.created[0][1]
    assert result["invitation_status"] == "request_accepted"
    assert (
        result["delivery_status"] == result["attendee_acceptance_status"] == "unknown"
    )


@pytest.mark.asyncio
async def test_declined_invitation_and_absent_email(phoenix, ms_config):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    ms_config["invitations_enabled"] = True
    with patch.object(tool, "_client_for_config", return_value=fake):
        missing = await tool.execute(
            booking(booking_confirmed=True, invitation_confirmed=True), phoenix
        )
        created = await tool.execute(
            booking(booking_confirmed=True, invitation_confirmed=False), phoenix
        )
    assert missing["error_code"] == "missing_attendee_email"
    assert (
        created["invitation_status"] == "not_requested"
        and not fake.created[0][4]["attendee_emails"]
    )


@pytest.mark.asyncio
async def test_identical_retry_and_restart_reconcile_without_second_post(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    fake.creation_error = MicrosoftGraphApiError(
        "timeout", error_code="graph_unavailable"
    )
    with patch.object(tool, "_client_for_config", return_value=fake):
        uncertain = await tool.execute(booking(), phoenix)
        retry = await tool.execute(booking(), phoenix)
    restarted = MicrosoftCalendarTool()
    with patch.object(restarted, "_client_for_config", return_value=fake):
        recovered = await restarted.execute(booking(), phoenix)
    assert uncertain["reservation_status"] == "unknown"
    assert retry["reconciled"] and recovered["reconciled"]
    assert len(fake.created) == 1


@pytest.mark.asyncio
async def test_unresolved_creation_blocks_changed_retry(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    fake.creation_error = MicrosoftGraphApiError(
        "timeout", error_code="graph_unavailable"
    )
    with patch.object(tool, "_client_for_config", return_value=fake):
        await tool.execute(booking(), phoenix)
        fake.events.clear()  # no visible reconciliation evidence yet
        unresolved = await tool.execute(booking(), phoenix)
        changed = await tool.execute(booking(summary="Different booking"), phoenix)
    assert unresolved["error_code"] == "mutation_uncertain"
    assert changed["error_code"] == "existing_booking" and len(fake.created) == 1


@pytest.mark.asyncio
async def test_later_call_restart_and_wrong_calendar_cannot_cancel(phoenix, ms_config):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    cancel = {
        "action": "delete_event",
        "cancellation_confirmed": True,
        "event_id": "ms_event_123",
    }
    with patch.object(tool, "_client_for_config", return_value=fake):
        await tool.execute(booking(), phoenix)
        other_call = copy.copy(phoenix)
        other_call.call_id = "later_call"
        assert (await tool.execute(cancel, other_call))[
            "error_code"
        ] == "staff_assistance_required"
        restarted = MicrosoftCalendarTool()
        assert (await restarted.execute(cancel, phoenix))[
            "error_code"
        ] == "staff_assistance_required"
        ms_config["accounts"]["default"]["calendar_id"] = "different-calendar"
        assert (await tool.execute(cancel, phoenix))[
            "error_code"
        ] == "staff_assistance_required"
    assert not fake.deleted


@pytest.mark.asyncio
async def test_reschedule_preserves_booking_attendees_and_updates_body(
    phoenix, ms_config
):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    ms_config["invitations_enabled"] = True
    args = booking(
        attendee_emails=["caller@example.com"],
        booking_confirmed=True,
        invitation_confirmed=True,
        caller_name="Pat",
    )
    with patch.object(tool, "_client_for_config", return_value=fake):
        created = await tool.execute(args, phoenix)
        moved = await tool.execute(
            {
                "action": "reschedule_event",
                "booking_confirmed": True,
                "start_datetime": "2026-04-29T14:00:00",
                "end_datetime": "2026-04-29T14:30:00",
            },
            phoenix,
        )
        cancelled = await tool.execute(
            {"action": "delete_event", "cancellation_confirmed": True}, phoenix
        )
    assert moved["event_id"] == created["event_id"]
    assert "14:00–14:30" in fake.updated[0][1]["body"]["content"]
    assert "attendees" not in fake.updated[0][1] and len(fake.created) == 1
    assert (
        cancelled["invitation_status"] == "cancellation_request_accepted"
        and cancelled["delivery_status"] == "unknown"
    )
    assert fake.deleted == [created["event_id"]]


@pytest.mark.asyncio
async def test_busy_reschedule_keeps_original_and_uncertain_update_reconciles(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    move = {
        "action": "reschedule_event",
        "booking_confirmed": True,
        "start_datetime": "2026-04-29T14:00:00",
        "end_datetime": "2026-04-29T14:30:00",
    }
    with patch.object(tool, "_client_for_config", return_value=fake):
        await tool.execute(booking(), phoenix)
        fake.events.append(busy_event("2026-04-29T21:00:00Z", "2026-04-29T21:30:00Z"))
        assert (await tool.execute(move, phoenix))["error_code"] == "slot_busy"
        assert not fake.updated and not fake.deleted
        fake.events.pop()
        fake.update_error = MicrosoftGraphApiError(
            "timeout", error_code="graph_unavailable"
        )
        assert (await tool.execute(move, phoenix))["error_code"] == "mutation_uncertain"
        assert (await tool.execute(move, phoenix))["reconciled"]
    assert len(fake.updated) == 1 and not fake.deleted


@pytest.mark.asyncio
async def test_concurrent_callers_do_not_both_book_same_slot(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    other = copy.copy(phoenix)
    other.call_id = "other-caller"
    with patch.object(tool, "_client_for_config", return_value=fake):
        results = await asyncio.gather(
            tool.execute(booking(), phoenix), tool.execute(booking(), other)
        )
    assert sorted(r["status"] for r in results) == ["error", "success"]
    assert len(fake.created) == 1


@pytest.mark.asyncio
async def test_cancelled_awaiter_does_not_release_mutation_early(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = fake.create_event

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        result = original(*args, **kwargs)
        finished.set()
        return result

    fake.create_event = delayed
    with patch.object(tool, "_client_for_config", return_value=fake):
        task = asyncio.create_task(tool.execute(booking(), phoenix))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        retry = await tool.execute(booking(), phoenix)
    assert retry["reconciled"] and len(fake.created) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code", ["auth_expired", "calendar_not_found", "forbidden_calendar"]
)
async def test_preflight_errors_do_not_create(phoenix, error_code):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    fake.list_calendar_view = Mock(
        side_effect=MicrosoftGraphApiError(
            "private graph payload", error_code=error_code, status=401
        )
    )
    with patch.object(tool, "_client_for_config", return_value=fake):
        result = await tool.execute(booking(), phoenix)
    assert result["error_code"] == error_code and not fake.created
    assert "private graph payload" not in result["message"]


@pytest.mark.asyncio
async def test_prefix_mode_treats_unmarked_created_booking_as_busy(phoenix, ms_config):
    ms_config["free_prefix"] = "Open"
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    fake.events = [
        busy_event(
            "2026-04-29T15:00:00Z",
            "2026-04-30T00:00:00Z",
            id="window",
            subject="Open",
            showAs="free",
        ),
        busy_event(),
    ]
    with patch.object(tool, "_client_for_config", return_value=fake):
        result = await tool.execute(
            {
                "action": "check_availability",
                "start_datetime": "2026-04-29T13:00:00",
                "end_datetime": "2026-04-29T13:30:00",
            },
            phoenix,
        )
    assert result["available"] is False


@pytest.mark.parametrize(
    "value",
    ["2026-11-01T01:30:00", "2026-03-08T02:30:00", "2026-04-29", "2026-04-29T10:00:01"],
)
def test_ambiguous_nonexistent_date_only_and_subminute_values_rejected(value):
    from src.tools.business.microsoft_booking import (
        strict_datetime,
        BookingValidationError,
    )

    with pytest.raises(BookingValidationError):
        strict_datetime(value, "America/Los_Angeles")


def test_templates_reject_unknown_placeholders_and_never_expand_caller_text():
    from src.tools.business.microsoft_booking import (
        render_template,
        BookingValidationError,
    )

    assert (
        render_template(
            "Hello {{caller_name}}", {"caller_name": "{{contact_details}}"}, limit=255
        )
        == "Hello {{contact_details}}"
    )
    with pytest.raises(BookingValidationError):
        render_template("{{credentials}}", {}, limit=255)


def test_schema_and_results_survive_provider_boundaries():
    from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string

    definition = MicrosoftCalendarTool().definition
    for schema in [
        definition.to_openai_realtime_schema()["parameters"],
        definition.to_deepgram_schema()["parameters"],
    ]:
        assert schema["properties"]["attendee_emails"]["items"]["type"] == "string"
        assert "reschedule_event" in schema["properties"]["action"]["enum"]
    result = {
        "status": "success",
        "available": True,
        "invitation_status": "request_accepted",
        "delivery_status": "unknown",
        "attendee_acceptance_status": "unknown",
        "reservation_status": "created",
        "reconciled": True,
    }
    assert all(
        sanitize_tool_result_for_json_string(result, tool_name="microsoft_calendar")[k]
        == v
        for k, v in result.items()
    )


@pytest.mark.asyncio
async def test_external_time_change_and_recurring_event_require_staff(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        await tool.execute(booking(), phoenix)
        fake.events[0]["start"]["dateTime"] = "2026-04-29T19:00:00Z"
        result = await tool.execute(
            {"action": "delete_event", "cancellation_confirmed": True}, phoenix
        )
        assert result["error_code"] == "booking_changed" and not fake.deleted
        fake.events[0]["type"] = "seriesMaster"
        result = await tool.execute(
            {"action": "delete_event", "cancellation_confirmed": True}, phoenix
        )
        assert result["error_code"] == "unsupported_event" and not fake.deleted


@pytest.mark.asyncio
async def test_invalid_timezone_hours_and_malformed_busy_interval_fail_closed(
    phoenix, ms_config
):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        ms_config["working_days"] = []
        assert (await tool.execute(booking(), phoenix))[
            "error_code"
        ] == "invalid_configuration"
        ms_config["working_days"] = [0, 1, 2, 3, 4]
        ms_config["accounts"]["default"]["timezone"] = "Invalid/Zone"
        assert (await tool.execute(booking(), phoenix))["status"] == "error"
        ms_config["accounts"]["default"]["timezone"] = "America/Phoenix"
        fake.list_calendar_view = Mock(
            return_value=[{"id": "broken", "subject": "Busy"}]
        )
        assert (await tool.execute(booking(), phoenix))[
            "error_code"
        ] == "malformed_calendar_event"
    assert not fake.created


@pytest.mark.asyncio
async def test_availability_exposes_auth_expiry_instead_of_reporting_busy(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    fake.list_calendar_view = Mock(
        side_effect=MicrosoftGraphApiError(
            "synthetic expiry", error_code="auth_expired", status=401
        )
    )
    with patch.object(tool, "_client_for_config", return_value=fake):
        result = await tool.execute(
            {
                "action": "check_availability",
                "start_datetime": "2026-04-29T13:00:00",
                "end_datetime": "2026-04-29T13:30:00",
            },
            phoenix,
        )
    assert result["error_code"] == "auth_expired" and "reconnect" in result["message"]
    assert "available" not in result


@pytest.mark.asyncio
async def test_booking_subject_starting_open_is_never_an_availability_marker(
    phoenix, ms_config
):
    ms_config["free_prefix"] = "Open"
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    fake.events = [
        busy_event(
            "2026-04-29T15:00:00Z",
            "2026-04-30T00:00:00Z",
            id="window",
            subject="Open",
            showAs="free",
        )
    ]
    with patch.object(tool, "_client_for_config", return_value=fake):
        assert (await tool.execute(booking(summary="Open discussion"), phoenix))[
            "status"
        ] == "success"
        result = await tool.execute(
            {
                "action": "check_availability",
                "start_datetime": "2026-04-29T13:00:00",
                "end_datetime": "2026-04-29T13:30:00",
            },
            phoenix,
        )
    assert result["available"] is False


@pytest.mark.asyncio
async def test_provider_to_tool_to_graph_complete_invitation_lifecycle(
    phoenix, ms_config
):
    """Actual provider adapter/tool/client; only OAuth and HTTP are mocked."""
    import io
    import json
    from unittest.mock import AsyncMock
    from src.tools.adapters.google import GoogleToolAdapter
    from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string
    from src.tools.business.ms_graph_client import MicrosoftGraphClient
    from src.tools.registry import ToolRegistry

    ms_config.update(invitations_enabled=True, business_name="Synthetic Business")
    tool = MicrosoftCalendarTool()
    graph = object.__new__(MicrosoftGraphClient)
    graph.account = tool._account_config(ms_config["accounts"]["default"])
    graph.timeout = 20
    graph.acquire_token = Mock(return_value="synthetic-token")
    registry = ToolRegistry.isolated()
    registry.register_instance(tool)
    adapter = GoogleToolAdapter(registry)
    store, requests = {}, []

    def send(request, timeout):
        requests.append(
            (
                request.method,
                request.full_url,
                json.loads(request.data) if request.data else None,
            )
        )
        body = requests[-1][2]
        if request.method == "GET" and "/calendarView?" in request.full_url:
            payload = {"value": list(store.values())}
        elif request.method == "POST":
            payload = {
                **body,
                "id": "graph-event",
                "isOrganizer": True,
                "@odata.etag": 'W/"synthetic-version"',
            }
            store["graph-event"] = payload
        elif request.method == "PATCH":
            store["graph-event"].update(body)
            payload = store["graph-event"]
        elif request.method == "DELETE":
            store.clear()
            return io.BytesIO(b"")
        else:
            payload = store["graph-event"]
        return io.BytesIO(json.dumps(payload).encode())

    with patch.object(tool, "_client_for_config", return_value=graph), patch.object(
        phoenix, "get_tool_block_response", AsyncMock(return_value=None)
    ), patch("urllib.request.urlopen", side_effect=send), patch(
        "src.tools.adapters.google.logger"
    ) as logs:
        checked = await adapter.execute_tool(
            "microsoft_calendar",
            {
                "action": "check_availability",
                "duration": 30.0,
                "start_datetime": "2026-04-29T13:00:00",
                "end_datetime": "2026-04-29T13:30:00",
            },
            phoenix,
        )
        created = await adapter.execute_tool(
            "microsoft_calendar",
            booking(
                booking_confirmed=True,
                invitation_confirmed=True,
                attendee_emails=["caller@example.com"],
                caller_name="Pat",
                confirmed_notes="Agreed consultation details.",
            ),
            phoenix,
        )
        moved = await adapter.execute_tool(
            "microsoft_calendar",
            {
                "action": "reschedule_event",
                "booking_confirmed": True,
                "start_datetime": "2026-04-29T14:00:00",
                "end_datetime": "2026-04-29T14:30:00",
            },
            phoenix,
        )
        cancelled = await adapter.execute_tool(
            "microsoft_calendar",
            {"action": "delete_event", "cancellation_confirmed": True},
            phoenix,
        )
    assert (
        checked["available"]
        and created["event_id"] == moved["event_id"] == cancelled["event_id"]
    )
    assert [method for method, _, _ in requests].count("POST") == 1
    post = next(body for method, _, body in requests if method == "POST")
    update = next(body for method, _, body in requests if method == "PATCH")
    assert post["attendees"][0]["emailAddress"]["address"] == "caller@example.com"
    assert "Agreed consultation details." in post["body"]["content"]
    assert "14:00–14:30" in update["body"]["content"] and "attendees" not in update
    assert all("/calendars/calendar-a/" in url for _, url, _ in requests)
    assert (
        sanitize_tool_result_for_json_string(created, tool_name="microsoft_calendar")[
            "delivery_status"
        ]
        == "unknown"
    )
    assert "caller@example.com" not in str(logs.mock_calls)
    assert store == {}


@pytest.mark.asyncio
async def test_graph_crlf_body_normalization_does_not_block_rescheduling(
    phoenix, ms_config
):
    ms_config["invitations_enabled"] = True
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        await tool.execute(
            booking(
                attendee_emails=["caller@example.com"],
                booking_confirmed=True,
                invitation_confirmed=True,
            ),
            phoenix,
        )
        fake.events[0]["body"]["content"] = (
            fake.events[0]["body"]["content"].replace("\n", "\r\n") + "\r\n"
        )
        result = await tool.execute(
            {
                "action": "reschedule_event",
                "booking_confirmed": True,
                "start_datetime": "2026-04-29T14:00:00",
                "end_datetime": "2026-04-29T14:30:00",
            },
            phoenix,
        )
    assert result["reservation_status"] == "rescheduled" and len(fake.updated) == 1


@pytest.mark.parametrize(
    ("raw", "hour"),
    [
        ("2026-04-29T20:00:00.1234567", 13),
        ("2026-04-29T20:00:00.1234567Z", 13),
        ("2026-04-29T13:00:00.1234567-07:00", 13),
        ("2026-04-29T22:00:00.1234567+02:00", 13),
    ],
)
def test_graph_seven_digit_fraction_preserves_instant(raw, hour):
    parsed = MicrosoftCalendarTool()._parse_event_dt(
        {"dateTime": raw, "timeZone": "UTC"}, "America/Phoenix"
    )
    assert parsed == datetime(2026, 4, 29, 20, 0, 0, 123456, tzinfo=timezone.utc)
    assert parsed.hour == hour


@pytest.mark.asyncio
async def test_graph_seven_digit_event_blocks_booking(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    fake.events = [
        busy_event("2026-04-29T20:00:00.0000000", "2026-04-29T20:30:00.0000000")
    ]
    with patch.object(tool, "_client_for_config", return_value=fake):
        checked = await tool.execute(
            {**booking(), "action": "check_availability"}, phoenix
        )
        created = await tool.execute(booking(), phoenix)
    assert checked["available"] is False
    assert created["error_code"] == "slot_busy"
    assert not fake.created


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["check_availability", "get_free_slots"])
@pytest.mark.parametrize("override", ["free_prefix", "busy_prefix"])
async def test_availability_prefix_overrides_cannot_bypass_operator_policy(
    phoenix, ms_config, action, override
):
    ms_config.update(free_prefix="Open", busy_prefix="Reserved")
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    # Reserved is marked free in Graph but explicitly blocked by operator policy.
    # A free-prefix override would instead turn it into an open window.
    fake.events = [busy_event(showAs="free")]
    if override == "busy_prefix":
        fake.events.append(
            busy_event(
                "2026-04-29T15:00:00Z",
                "2026-04-30T00:00:00Z",
                id="open-window",
                subject="Open",
                showAs="free",
            )
        )
    params = {
        "action": action,
        override: "Reserved" if override == "free_prefix" else "Busy",
        "start_datetime": booking()["start_datetime"],
        "end_datetime": booking()["end_datetime"],
        "time_min": booking()["start_datetime"],
        "time_max": booking()["end_datetime"],
        "duration": 30,
    }
    with patch.object(tool, "_client_for_config", return_value=fake):
        checked = await tool.execute(params, phoenix)
        created = await tool.execute(booking(), phoenix)
    assert checked["status"] == "success"
    if action == "check_availability":
        assert checked["available"] is False
    else:
        assert checked["slots"] == []
    assert created["error_code"] == "slot_busy"
    assert not fake.created


@pytest.mark.asyncio
async def test_generic_event_read_never_exposes_another_callers_invitation_body(
    phoenix,
):
    from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string

    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    private_body = "Caller: Synthetic Example; reason: confidential appointment notes"
    fake.events = [busy_event(body={"contentType": "text", "content": private_body})]
    with patch.object(tool, "_client_for_config", return_value=fake):
        listed = await tool.execute(
            {
                "action": "list_events",
                "time_min": "2026-04-29T00:00:00",
                "time_max": "2026-04-30T00:00:00",
            },
            phoenix,
        )
        result = await tool.execute(
            {"action": "get_event", "event_id": listed["events"][0]["id"]}, phoenix
        )
    assert result["status"] == "success"
    assert result["id"] == "other-event"
    assert result["summary"] == "Reserved"
    assert "description" not in result
    assert private_body not in str(result)
    assert private_body not in str(
        sanitize_tool_result_for_json_string(result, tool_name="microsoft_calendar")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["get_free_slots", "check_availability"])
@pytest.mark.parametrize("duration", [30, 30.0])
async def test_integral_duration_from_provider_numbers_is_accepted(
    phoenix, action, duration
):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        result = await tool.execute(
            {
                "action": action,
                "duration": duration,
                "time_min": booking()["start_datetime"],
                "time_max": booking()["end_datetime"],
                "start_datetime": booking()["start_datetime"],
                "end_datetime": booking()["end_datetime"],
            },
            phoenix,
        )
    assert result["status"] == "success"
    if action == "check_availability":
        assert result["available"] is True
    else:
        assert result["slot_duration_minutes"] == 30
        assert type(result["slot_duration_minutes"]) is int
        assert len(result["slots"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "duration", [True, False, 30.5, "30", 0.0, 1441.0, float("nan"), float("inf")]
)
async def test_invalid_duration_values_still_fail_closed(phoenix, duration):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        result = await tool.execute(
            {
                "action": "get_free_slots",
                "duration": duration,
                "time_min": booking()["start_datetime"],
                "time_max": booking()["end_datetime"],
            },
            phoenix,
        )
    assert result["error_code"] == "invalid_duration"
    assert not fake.created


@pytest.mark.asyncio
@pytest.mark.parametrize("uncertain", [False, True])
async def test_reschedule_refreshes_subject_and_reconciles_uncertain_update(
    phoenix, ms_config, uncertain
):
    ms_config.update(
        invitations_enabled=True,
        invitation_subject_template="{{meeting_purpose}}: {{appointment_date}} {{start_time}}–{{end_time}}",
    )
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    move = {
        "action": "reschedule_event",
        "booking_confirmed": True,
        "start_datetime": "2026-04-29T14:00:00-07:00",
        "end_datetime": "2026-04-29T14:30:00-07:00",
    }
    with patch.object(tool, "_client_for_config", return_value=fake):
        created = await tool.execute(
            booking(
                attendee_emails=["caller@example.com"],
                booking_confirmed=True,
                invitation_confirmed=True,
                caller_name="Pat",
            ),
            phoenix,
        )
        original_subject = fake.events[0]["subject"]
        if uncertain:
            fake.update_error = MicrosoftGraphApiError(
                "synthetic timeout", error_code="graph_unavailable"
            )
        moved = await tool.execute(move, phoenix)
        if uncertain:
            assert moved["error_code"] == "mutation_uncertain"
            fake.update_error = None
            moved = await tool.execute(move, phoenix)
            assert moved["reconciled"] is True
        assert moved["event_id"] == created["event_id"]
        assert len(fake.updated) == 1
        assert (
            fake.events[0]["subject"]
            == "Consultation: Wednesday, 2026-04-29 14:00–14:30"
        )
        assert original_subject != fake.events[0]["subject"]
        assert fake.updated[0][1]["subject"] == fake.events[0]["subject"]
        assert "14:00–14:30" in fake.events[0]["body"]["content"]
        # A second move exercises the promoted subject/body snapshot after reconciliation.
        again = await tool.execute(
            {
                **move,
                "start_datetime": "2026-04-29T15:00:00-07:00",
                "end_datetime": "2026-04-29T15:30:00-07:00",
            },
            phoenix,
        )
        assert again["status"] == "success"
        assert fake.events[0]["subject"].endswith("15:00–15:30")
        cancelled = await tool.execute(
            {"action": "delete_event", "cancellation_confirmed": True}, phoenix
        )
    assert cancelled["reservation_status"] == "cancelled"
    assert len(fake.updated) == 2 and len(fake.created) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed_subject", ["Externally edited title", "13:00 appointment"]
)
async def test_uncertain_subject_update_never_authorizes_external_subject_edits(
    phoenix, ms_config, changed_subject
):
    ms_config.update(
        invitations_enabled=True,
        invitation_subject_template="{{start_time}} appointment",
    )
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    move = {
        "action": "reschedule_event",
        "booking_confirmed": True,
        "start_datetime": "2026-04-29T14:00:00-07:00",
        "end_datetime": "2026-04-29T14:30:00-07:00",
    }
    with patch.object(tool, "_client_for_config", return_value=fake):
        await tool.execute(
            booking(
                attendee_emails=["caller@example.com"],
                booking_confirmed=True,
                invitation_confirmed=True,
            ),
            phoenix,
        )
        fake.update_error = MicrosoftGraphApiError(
            "synthetic timeout", error_code="graph_unavailable"
        )
        assert (await tool.execute(move, phoenix))["error_code"] == "mutation_uncertain"
        fake.events[0]["subject"] = changed_subject
        result = await tool.execute(move, phoenix)
    assert result["error_code"] == "booking_changed"
    assert len(fake.updated) == 1


def mutation_args(action):
    if action == "create_event":
        return booking()
    if action == "delete_event":
        return {"action": action, "cancellation_confirmed": True}
    return {
        "action": action,
        "booking_confirmed": True,
        "start_datetime": "2026-04-29T14:00:00-07:00",
        "end_datetime": "2026-04-29T14:30:00-07:00",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create_event", "delete_event", "reschedule_event"])
async def test_contended_mutation_lock_returns_busy_without_calendar_access(
    phoenix, action
):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake) as factory:
        if action != "create_event":
            await tool.execute(booking(), phoenix)
        before = copy.deepcopy(tool._last_event_per_call)
        factory.reset_mock()
        assert tool._MUTATION_LOCK.acquire(timeout=1)
        try:
            with patch.object(tool, "_MUTATION_LOCK_WAIT_SECONDS", 0.02):
                result = await asyncio.wait_for(
                    tool.execute(mutation_args(action), phoenix), 1
                )
        finally:
            tool._MUTATION_LOCK.release()
    assert result["error_code"] == "calendar_busy"
    factory.assert_not_called()
    assert tool._last_event_per_call == before
    assert len(fake.created) == (0 if action == "create_event" else 1)
    assert not fake.updated and not fake.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create_event", "delete_event", "reschedule_event"])
async def test_cancelled_lock_waiter_cannot_mutate_after_lock_release(phoenix, action):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    lock = tool._MUTATION_LOCK
    entered, finished = threading.Event(), threading.Event()

    class ObservedLock:
        def acquire(self, *, timeout):
            entered.set()
            return lock.acquire(timeout=timeout)

        def release(self):
            lock.release()
            finished.set()

    with patch.object(tool, "_client_for_config", return_value=fake) as factory:
        if action != "create_event":
            await tool.execute(booking(), phoenix)
        before = copy.deepcopy(tool._last_event_per_call)
        factory.reset_mock()
        assert lock.acquire(timeout=1)
        try:
            with patch.object(tool, "_MUTATION_LOCK", ObservedLock()):
                task = asyncio.create_task(tool.execute(mutation_args(action), phoenix))
                assert await asyncio.to_thread(entered.wait, 3)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                lock.release()
                assert await asyncio.to_thread(finished.wait, 3)
        finally:
            if lock.locked():
                lock.release()
    factory.assert_not_called()
    assert tool._last_event_per_call == before
    assert len(fake.created) == (0 if action == "create_event" else 1)
    assert not fake.updated and not fake.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create_event", "delete_event", "reschedule_event"])
async def test_worker_deadline_starts_before_executor_submission(phoenix, action):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake) as factory:
        if action != "create_event":
            await tool.execute(booking(), phoenix)
        factory.reset_mock()
        # Simulate a worker starting only after the declared tool deadline elapsed.
        with patch(
            "src.tools.business.microsoft_calendar.monotonic", side_effect=[0, 31]
        ):
            result = await tool.execute(mutation_args(action), phoenix)
    assert result["error_code"] == "calendar_busy"
    factory.assert_not_called()
    assert not fake.updated and not fake.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create_event", "delete_event", "reschedule_event"])
async def test_cancelled_preflight_does_not_start_a_new_write_and_releases_lock(
    phoenix, action
):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    with patch.object(tool, "_client_for_config", return_value=fake):
        if action != "create_event":
            await tool.execute(booking(), phoenix)
        before = copy.deepcopy(tool._last_event_per_call)
        method = "list_calendar_view" if action == "create_event" else "get_event"
        original_read = getattr(fake, method)
        original_worker = (
            tool._create_booking if action == "create_event" else tool._change_booking
        )
        worker_name = (
            "_create_booking" if action == "create_event" else "_change_booking"
        )

        def delayed_read(*args, **kwargs):
            entered.set()
            assert release.wait(3)
            return original_read(*args, **kwargs)

        def observed_worker(*args):
            try:
                return original_worker(*args)
            finally:
                finished.set()

        with patch.object(fake, method, delayed_read), patch.object(
            tool, worker_name, observed_worker
        ):
            task = asyncio.create_task(tool.execute(mutation_args(action), phoenix))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
            assert await asyncio.to_thread(finished.wait, 3)
        assert tool._MUTATION_LOCK.acquire(timeout=1)
        tool._MUTATION_LOCK.release()
    assert tool._last_event_per_call == before
    assert len(fake.created) == (0 if action == "create_event" else 1)
    assert not fake.updated and not fake.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create_event", "delete_event", "reschedule_event"])
async def test_slow_preflight_deadline_does_not_dispatch_write(phoenix, action):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        if action != "create_event":
            await tool.execute(booking(), phoenix)
        before = copy.deepcopy(tool._last_event_per_call)
        method = "list_calendar_view" if action == "create_event" else "get_event"
        read = getattr(fake, method)
        now = [0]

        def slow_read(*args, **kwargs):
            value = read(*args, **kwargs)
            now[0] = 31
            return value

        with patch.object(fake, method, slow_read), patch(
            "src.tools.business.microsoft_calendar.monotonic",
            side_effect=lambda: now[0],
        ):
            result = await tool.execute(mutation_args(action), phoenix)
    assert result["error_code"] == "calendar_busy"
    assert tool._last_event_per_call == before
    assert len(fake.created) == (0 if action == "create_event" else 1)
    assert not fake.updated and not fake.deleted
    assert tool._MUTATION_LOCK.acquire(timeout=1)
    tool._MUTATION_LOCK.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
@pytest.mark.parametrize("status", [None, 503])
async def test_applied_uncertain_cancel_reconciles_and_allows_new_slot(
    phoenix, ms_config, missing, status
):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    ms_config["invitations_enabled"] = True
    args = booking(
        attendee_emails=["caller@example.com"],
        booking_confirmed=True,
        invitation_confirmed=True,
    )
    cancel = {"action": "delete_event", "cancellation_confirmed": True}
    original_delete = fake.delete_event

    def applied_delete(event_id, etag=None):
        if missing:
            original_delete(event_id, etag)
        else:
            fake.deleted.append(event_id)
            fake.delete_etags.append(etag)
            fake.events[0]["isCancelled"] = True
        raise MicrosoftGraphApiError(
            "synthetic lost response", error_code="graph_unavailable", status=status
        )

    with patch.object(tool, "_client_for_config", return_value=fake):
        await tool.execute(args, phoenix)
        fake.delete_event = applied_delete
        uncertain = await tool.execute(cancel, phoenix)
        assert uncertain["error_code"] == "mutation_uncertain"
        reconciled = await tool.execute(cancel, phoenix)
        assert reconciled["reservation_status"] == "cancelled"
        assert reconciled["invitation_status"] == "unknown"
        tracked = tool._last_event_per_call[phoenix.call_id]
        assert all(
            tracked.get(key) is None
            for key in (
                "pending_operation",
                "pending_target",
                "pending_body",
                "pending_subject",
            )
        )
        assert (await tool.execute(args, phoenix))["error_code"] == "cancelled_booking"
        fresh = await tool.execute(
            {
                **args,
                "start_datetime": "2026-04-29T14:00:00-07:00",
                "end_datetime": "2026-04-29T14:30:00-07:00",
            },
            phoenix,
        )
    assert fresh["reservation_status"] == "created"
    assert len(fake.created) == 2 and len(fake.deleted) == 1
    assert fake.delete_etags == ['W/"version1"']


@pytest.mark.asyncio
@pytest.mark.parametrize("etag", [None, "", "*"])
async def test_cancel_without_concrete_version_requires_staff(phoenix, etag):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        await tool.execute(booking(), phoenix)
        fake.events[0]["@odata.etag"] = etag
        result = await tool.execute(
            {"action": "delete_event", "cancellation_confirmed": True}, phoenix
        )
    assert result["error_code"] == "booking_changed" and not fake.deleted


@pytest.mark.asyncio
async def test_cancel_external_body_edit_requires_staff(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        await tool.execute(booking(), phoenix)
        fake.events[0]["body"]["content"] = "Staff edited this appointment"
        result = await tool.execute(
            {"action": "delete_event", "cancellation_confirmed": True}, phoenix
        )
    assert result["error_code"] == "booking_changed" and not fake.deleted


@pytest.mark.asyncio
async def test_cancel_version_conflict_retains_booking_without_retry(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    attempts = []

    def conflict(event_id, etag=None):
        attempts.append((event_id, etag))
        fake.events[0]["subject"] = "Staff changed this booking after the guard read"
        fake.events[0]["@odata.etag"] = 'W/"version2"'
        raise MicrosoftGraphApiError(
            "synthetic conflict", error_code="booking_changed", status=412
        )

    with patch.object(tool, "_client_for_config", return_value=fake):
        await tool.execute(booking(), phoenix)
        fake.delete_event = conflict
        result = await tool.execute(
            {"action": "delete_event", "cancellation_confirmed": True}, phoenix
        )
        assert result["error_code"] == "booking_changed"
        tracked = tool._last_event_per_call[phoenix.call_id]
        assert tracked["state"] == "active" and tracked["pending_operation"] is None
        retry = await tool.execute(
            {"action": "delete_event", "cancellation_confirmed": True}, phoenix
        )
    assert retry["error_code"] == "booking_changed"
    assert attempts == [("ms_event_123", 'W/"version1"')]
    assert len(fake.events) == 1 and not fake.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-04-29T18:00:00-07:00", "2026-04-29T18:30:00-07:00"),
        ("2026-05-02T13:00:00-07:00", "2026-05-02T13:30:00-07:00"),
        ("2027-04-29T13:00:00-07:00", "2027-04-29T13:30:00-07:00"),
    ],
)
async def test_unchanged_legacy_settings_keep_exact_and_create_limits_unrestricted(
    ms_context, ms_config, start, end
):
    original = copy.deepcopy(ms_config)
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        available = await tool.execute(
            {
                "action": "check_availability",
                "start_datetime": start,
                "end_datetime": end,
            },
            ms_context,
        )
        created = await tool.execute(
            booking(start_datetime=start, end_datetime=end), ms_context
        )
    assert available["available"] is True and created["reservation_status"] == "created"
    assert created["invitation_status"] == "not_requested"
    assert fake.created[0][4]["attendee_emails"] == []
    assert ms_config == original and "enforce_booking_limits" not in ms_config


@pytest.mark.asyncio
async def test_limit_adoption_and_rollback_preserve_saved_values_and_conflict_guards(
    ms_context, ms_config
):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    ms_config.update(
        working_hours_start=8, working_hours_end=17, booking_horizon_days=365
    )
    original = copy.deepcopy(ms_config)
    args = booking(
        start_datetime="2026-05-02T13:00:00-07:00",
        end_datetime="2026-05-02T13:30:00-07:00",
    )
    with patch.object(tool, "_client_for_config", return_value=fake):
        ms_config["enforce_booking_limits"] = True
        assert (await tool.execute(args, ms_context))[
            "error_code"
        ] == "outside_working_hours"
        ms_config["enforce_booking_limits"] = False
        assert (await tool.execute(args, ms_context))["reservation_status"] == "created"
        other = copy.copy(ms_context)
        other.call_id = "another-synthetic-call"
        assert (await tool.execute(args, other))["error_code"] == "slot_busy"
    assert {
        k: v for k, v in ms_config.items() if k != "enforce_booking_limits"
    } == original


@pytest.mark.asyncio
async def test_multiple_same_call_bookings_retry_and_mutate_only_owned_events(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    second_args = booking(
        summary="Separate appointment",
        start_datetime="2026-04-29T14:00:00-07:00",
        end_datetime="2026-04-29T14:30:00-07:00",
    )
    with patch.object(tool, "_client_for_config", return_value=fake):
        first = await tool.execute(booking(), phoenix)
        second = await tool.execute(second_args, phoenix)
        assert second["reservation_status"] == "created"
        assert (await tool.execute(booking(), phoenix))["event_id"] == first["event_id"]
        assert (await tool.execute(second_args, phoenix))["event_id"] == second[
            "event_id"
        ]
        assert len(fake.created) == 2
        moved = await tool.execute(
            {
                "action": "reschedule_event",
                "event_id": first["event_id"],
                "booking_confirmed": True,
                "start_datetime": "2026-04-29T15:00:00-07:00",
                "end_datetime": "2026-04-29T15:30:00-07:00",
            },
            phoenix,
        )
        assert moved["event_id"] == first["event_id"]
        # An explicit older owned event may be selected. Omitted ID follows the most recent successful selection.
        cancelled = await tool.execute(
            {"action": "delete_event", "cancellation_confirmed": True}, phoenix
        )
        assert cancelled["event_id"] == first["event_id"]
        assert fake.get_event(second["event_id"]) is not None
        later = copy.copy(phoenix)
        later.call_id = "later-synthetic-call"
        assert (
            await tool.execute(
                {
                    "action": "delete_event",
                    "event_id": second["event_id"],
                    "cancellation_confirmed": True,
                },
                later,
            )
        )["error_code"] == "staff_assistance_required"
        assert (
            await tool.execute(
                {
                    "action": "delete_event",
                    "event_id": "untracked-event",
                    "cancellation_confirmed": True,
                },
                phoenix,
            )
        )["error_code"] == "booking_mismatch"
        assert (
            await tool.execute(
                {
                    "action": "delete_event",
                    "event_id": second["event_id"],
                    "cancellation_confirmed": True,
                },
                phoenix,
            )
        )["reservation_status"] == "cancelled"
    assert fake.deleted == [first["event_id"], second["event_id"]]


@pytest.mark.asyncio
async def test_an_uncertain_owned_booking_blocks_mutations_of_other_same_call_events(
    phoenix,
):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake):
        first = await tool.execute(booking(), phoenix)
        fake.creation_error = MicrosoftGraphApiError(
            "synthetic timeout", error_code="graph_unavailable"
        )
        second_args = booking(
            start_datetime="2026-04-29T14:00:00-07:00",
            end_datetime="2026-04-29T14:30:00-07:00",
        )
        assert (await tool.execute(second_args, phoenix))[
            "error_code"
        ] == "mutation_uncertain"
        assert (
            await tool.execute(
                {
                    "action": "delete_event",
                    "event_id": first["event_id"],
                    "cancellation_confirmed": True,
                },
                phoenix,
            )
        )["error_code"] == "mutation_uncertain"
        assert not fake.deleted
        assert (await tool.execute(second_args, phoenix))["reconciled"] is True
        assert (
            await tool.execute(
                {
                    "action": "delete_event",
                    "event_id": first["event_id"],
                    "cancellation_confirmed": True,
                },
                phoenix,
            )
        )["reservation_status"] == "cancelled"


@pytest.mark.asyncio
async def test_evicted_same_call_record_never_authorizes_an_explicit_id(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    tool._LAST_EVENT_CACHE_CAP = 1
    with patch.object(tool, "_client_for_config", return_value=fake):
        first = await tool.execute(booking(), phoenix)
        await tool.execute(
            booking(
                start_datetime="2026-04-29T14:00:00-07:00",
                end_datetime="2026-04-29T14:30:00-07:00",
            ),
            phoenix,
        )
        result = await tool.execute(
            {
                "action": "delete_event",
                "event_id": first["event_id"],
                "cancellation_confirmed": True,
            },
            phoenix,
        )
    assert result["error_code"] == "booking_mismatch" and not fake.deleted
    assert len(tool._owned_bookings) == len(tool._last_event_per_call) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("account_shape", ["flat", "default", "named"])
async def test_persisted_legacy_upgrade_reloads_preserve_bindings_and_active_call_policy(
    tmp_path, account_shape
):
    import yaml
    from src.config.loaders import load_yaml_with_local_override
    from src.tools.context import ToolExecutionContext
    from src.tools.runtime_config import resolve_agent_tool_config

    cache = tmp_path / "microsoft-calendar-default-token-cache.json"
    cache.write_text('{"Account":{"synthetic":{"username":"scheduler@example.com"}}}')
    cache.chmod(0o600)
    account = {
        "tenant_id": "contoso.onmicrosoft.com",
        "client_id": "11111111-1111-1111-1111-111111111111",
        "token_cache_path": str(cache),
        "user_principal_name": "scheduler@example.com",
        "calendar_id": "A" * 152,
        "timezone": "America/Phoenix",
    }
    key = "dispatch" if account_shape == "named" else "default"
    legacy = {"enabled": True, "working_hours_start": 9, "working_hours_end": 17}
    legacy.update(account if account_shape == "flat" else {"accounts": {key: account}})
    if account_shape == "named":
        legacy["accounts"]["unselected"] = {**account, "calendar_id": "unselected"}
    base = tmp_path / "ai-agent.yaml"
    local = tmp_path / "ai-agent.local.yaml"
    base.write_text(yaml.safe_dump({"tools": {"microsoft_calendar": legacy}}))
    local.write_text(
        yaml.safe_dump({"tools": {"microsoft_calendar": {"free_prefix": ""}}})
    )
    original = base.read_bytes(), local.read_bytes(), cache.read_bytes()
    loaded = load_yaml_with_local_override(str(base))
    policy = {
        "microsoft_calendar": {"account_policy": "selected", "account_keys": [key]}
    }
    snapshot = resolve_agent_tool_config(loaded, policy).config
    assert snapshot["tools"]["microsoft_calendar"]["selected_accounts"] == [key]
    context = ToolExecutionContext(call_id="legacy-upgrade-call", config=snapshot)
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    # Overnight booking crossed midnight before this PR; opting out must not introduce a daily boundary.
    args = booking(
        account_key=key,
        start_datetime="2026-05-02T23:30:00-07:00",
        end_datetime="2026-05-03T00:30:00-07:00",
    )

    def selected_client(binding):
        assert binding.calendar_id == account["calendar_id"]
        assert binding.token_cache_path == str(cache)
        assert binding.user_principal_name == account["user_principal_name"]
        return fake

    with patch.object(tool, "_client_for_config", side_effect=selected_client):
        assert (await tool.execute({**args, "action": "check_availability"}, context))[
            "available"
        ] is True
        created = await tool.execute(args, context)
        assert created["reservation_status"] == "created"
        assert created["invitation_status"] == "not_requested"
        assert fake.created[0][4]["attendee_emails"] == []
        # Repeated config loads do not migrate storage or implicitly adopt the new policy.
        assert load_yaml_with_local_override(str(base)) == loaded
        assert (await tool.execute(args, context))["reconciled"] is True
        assert len(fake.created) == 1
        adopted = copy.deepcopy(loaded)
        adopted["tools"]["microsoft_calendar"]["enforce_booking_limits"] = True
        new_context = ToolExecutionContext(
            call_id="new-policy-call",
            config=resolve_agent_tool_config(adopted, policy).config,
        )
        assert (await tool.execute(args, new_context))[
            "error_code"
        ] == "outside_working_hours"
        # An active call still uses its captured legacy policy after a new generation opts in.
        moved = await tool.execute(
            {
                "action": "reschedule_event",
                "account_key": key,
                "booking_confirmed": True,
                "start_datetime": "2026-05-03T23:30:00-07:00",
                "end_datetime": "2026-05-04T00:30:00-07:00",
            },
            context,
        )
        assert moved["event_id"] == created["event_id"]
        assert (
            await tool.execute(
                {
                    "action": "delete_event",
                    "account_key": key,
                    "cancellation_confirmed": True,
                },
                context,
            )
        )["reservation_status"] == "cancelled"
    assert (base.read_bytes(), local.read_bytes(), cache.read_bytes()) == original
    assert load_yaml_with_local_override(str(base)) == loaded


@pytest.mark.asyncio
async def test_multiple_bookings_do_not_broaden_account_ownership(phoenix):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    with patch.object(tool, "_client_for_config", return_value=fake) as client:
        first = await tool.execute(booking(), phoenix)
        scoped = phoenix.get_config_value.return_value
        scoped["accounts"]["other"] = {
            **scoped["accounts"]["default"],
            "calendar_id": "other-calendar",
        }
        scoped["selected_accounts"] = ["default", "other"]
        result = await tool.execute(
            {
                "action": "delete_event",
                "account_key": "other",
                "event_id": first["event_id"],
                "cancellation_confirmed": True,
            },
            phoenix,
        )
        assert result["error_code"] == "staff_assistance_required"
        # The second account must not be queried using an ID owned by the first calendar.
        assert client.call_count == 1 and not fake.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("etag", [None, "", "   ", "*", " * ", 42, {}])
async def test_reschedule_without_concrete_version_never_writes_or_leaves_pending_state(
    phoenix, etag
):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    args = {
        "action": "reschedule_event",
        "booking_confirmed": True,
        "start_datetime": "2026-04-29T14:00:00-07:00",
        "end_datetime": "2026-04-29T14:30:00-07:00",
    }
    with patch.object(tool, "_client_for_config", return_value=fake):
        await tool.execute(booking(), phoenix)
        original = copy.deepcopy(fake.events[0])
        fake.events[0]["@odata.etag"] = etag
        result = await tool.execute(args, phoenix)
        assert result["error_code"] == "booking_changed" and not fake.updated
        record = tool._call_bookings(phoenix.call_id)[0]
        assert record["state"] == "active" and not record.get("pending_operation")
        assert not record.get("pending_target") and not record.get("pending_body")
        assert fake.events[0]["start"] == original["start"]
        assert fake.events[0]["end"] == original["end"]
        # Refusal must not poison later confirmed attempts with a concrete fresh version.
        fake.events[0]["@odata.etag"] = 'W/"fresh-version"'
        assert (await tool.execute(args, phoenix))[
            "reservation_status"
        ] == "rescheduled"
        assert fake.updated[0][2] == 'W/"fresh-version"'


@pytest.mark.asyncio
async def test_reschedule_version_conflict_keeps_original_time_and_clears_pending(
    phoenix,
):
    tool, fake = MicrosoftCalendarTool(), FakeMicrosoftClient()
    attempts = []
    args = {
        "action": "reschedule_event",
        "booking_confirmed": True,
        "start_datetime": "2026-04-29T14:00:00-07:00",
        "end_datetime": "2026-04-29T14:30:00-07:00",
    }

    def conflict(event_id, body, etag=None):
        attempts.append((event_id, etag))
        fake.events[0]["subject"] = "Staff changed this booking after the guard read"
        fake.events[0]["@odata.etag"] = 'W/"version2"'
        raise MicrosoftGraphApiError(
            "synthetic conflict", error_code="booking_changed", status=412
        )

    with patch.object(tool, "_client_for_config", return_value=fake):
        await tool.execute(booking(), phoenix)
        original_start = copy.deepcopy(fake.events[0]["start"])
        fake.update_event = conflict
        assert (await tool.execute(args, phoenix))["error_code"] == "booking_changed"
        tracked = tool._call_bookings(phoenix.call_id)[0]
        assert tracked["state"] == "active" and tracked["pending_operation"] is None
        assert tracked["pending_target"] is None
        assert (await tool.execute(args, phoenix))["error_code"] == "booking_changed"
    assert fake.events[0]["start"] == original_start
    assert attempts == [("ms_event_123", 'W/"version1"')]
