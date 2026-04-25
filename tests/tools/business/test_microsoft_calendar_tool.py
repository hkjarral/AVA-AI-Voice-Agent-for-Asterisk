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
            return self.delete_results.pop(0)
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
