"""Tests for Microsoft Calendar tool runtime behavior."""

from unittest.mock import Mock, patch

import pytest

from src.tools.base import ToolCategory
from src.tools.business.microsoft_calendar import MicrosoftCalendarTool
from src.tools.business.ms_graph_client import MicrosoftGraphApiError


class FakeMicrosoftClient:
    def __init__(self):
        self.created = []
        self.deleted = []
        self.delete_results = []

    def get_schedule(self, _start_utc, _end_utc):
        return []

    def list_calendar_view(self, _start_utc, _end_utc):
        return []

    def create_event(self, summary, description, start_utc, end_utc):
        self.created.append((summary, description, start_utc, end_utc))
        return {"id": "ms_event_123", "webLink": "https://example.test/event"}

    def delete_event(self, event_id):
        self.deleted.append(event_id)
        if self.delete_results:
            outcome = self.delete_results.pop(0)
            # Allow tests to inject a MicrosoftGraphApiError to simulate
            # Microsoft Graph rejecting a malformed event_id (HTTP 400).
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return True

    def get_event(self, event_id):
        return {
            "id": event_id,
            "subject": "Demo",
            "body": {"content": "Body"},
            "start": {"dateTime": "2026-04-29T16:00:00"},
            "end": {"dateTime": "2026-04-29T16:30:00"},
        }


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


def test_empty_calendar_id_is_not_rewritten_to_google_primary_alias():
    tool = MicrosoftCalendarTool()
    account = tool._account_config({
        "tenant_id": "contoso.onmicrosoft.com",
        "client_id": "11111111-1111-1111-1111-111111111111",
        "token_cache_path": "/app/project/secrets/microsoft-calendar-default-token-cache.json",
        "user_principal_name": "scheduler@contoso.com",
        "calendar_id": "",
        "timezone": "America/Los_Angeles",
    })
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
def test_error_messages_match_scheduling_recovery_substrings(error_code, expected_substring):
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
async def test_delete_event_falls_back_to_last_created_event(ms_context):
    tool = MicrosoftCalendarTool()
    fake = FakeMicrosoftClient()
    fake.delete_results = [False, True]
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
            {
                "action": "delete_event",
                "event_id": "hallucinated_id",
            },
            ms_context,
        )
    assert created["event_id"] == "ms_event_123"
    assert deleted["status"] == "success"
    assert deleted["event_id"] == "ms_event_123"
    assert fake.deleted == ["hallucinated_id", "ms_event_123"]


@pytest.mark.asyncio
async def test_delete_event_falls_back_when_first_delete_raises_400(ms_context):
    """The malformed-id case from PR #357 commit b02cd308.

    Live testing surfaced LLMs passing structurally-invalid event_ids on
    reschedule (Gemini hallucinated base64 with the wrong mailbox prefix,
    Deepgram passed the literal "event_id_here", OpenAI passed "1"). All
    three tripped Microsoft Graph's HTTP 400 "The Id is invalid." path,
    raising `MicrosoftGraphApiError` instead of returning False — which
    short-circuited the existing 404 fallback. The fix routes 400s through
    the same tracked-id retry. Pin the behavior here.
    """
    tool = MicrosoftCalendarTool()
    fake = FakeMicrosoftClient()
    fake.delete_results = [
        MicrosoftGraphApiError(
            "The Id is invalid.",
            error_code="microsoft_graph_error",
            status=400,
        ),
        True,
    ]
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
            {
                "action": "delete_event",
                "event_id": "AAMkADgzYzQ3YjQy_hallucinated",
            },
            ms_context,
        )
    assert created["event_id"] == "ms_event_123"
    assert deleted["status"] == "success"
    assert deleted["event_id"] == "ms_event_123"
    # First call hits the hallucinated id (raises 400), second call hits
    # the tracked id from the same call's prior create_event.
    assert fake.deleted == ["AAMkADgzYzQ3YjQy_hallucinated", "ms_event_123"]


@pytest.mark.asyncio
async def test_delete_event_with_no_event_id_resolves_from_same_call_cache(ms_context):
    """The ergonomic path from PR #357 commit 83b0b2e2.

    The new schema makes `event_id` optional on `delete_event`. Models that
    follow the updated `agent_hint` from `create_event` are told to OMIT
    `event_id` on the typical reschedule flow — the tool resolves the
    authoritative id from `_last_event_per_call[call_id]` instead. This is
    the clean path; the 400/404 fallbacks above remain as a safety net for
    models that still try to echo an id and get it wrong.
    """
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
            {"action": "delete_event"},
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
            {"action": "delete_event"},
            ms_context,
        )
    assert result["status"] == "error"
    assert "event_id" in result["message"].lower()
    assert fake.deleted == []
