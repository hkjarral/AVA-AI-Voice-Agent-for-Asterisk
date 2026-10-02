"""Mock HTTP boundary tests; never contact Microsoft or load real tokens."""

import io
import json
import urllib.error
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from src.tools.business.ms_graph_client import (
    MicrosoftAccountConfig,
    MicrosoftGraphApiError,
    MicrosoftGraphClient,
    MS_CALENDAR_SCOPES,
)


def client():
    graph = object.__new__(MicrosoftGraphClient)
    graph.account = MicrosoftAccountConfig(
        "example",
        "synthetic",
        "/synthetic/cache",
        "scheduler@example.com",
        "named/calendar",
        "America/Phoenix",
    )
    graph.timeout = 20
    graph.acquire_token = Mock(return_value="synthetic-token")
    return graph


def response(payload):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.return_value = json.dumps(payload).encode()
    return result


def test_create_event_http_body_calendar_attendees_transaction_and_scopes():
    graph = client()
    start = datetime(2026, 10, 5, 20, tzinfo=timezone.utc)
    end = datetime(2026, 10, 5, 20, 30, tzinfo=timezone.utc)
    with patch(
        "urllib.request.urlopen", return_value=response({"id": "event"})
    ) as send:
        graph.create_event(
            "Consultation",
            "Confirmed plain-text notes",
            start,
            end,
            attendee_emails=["caller@example.com"],
            transaction_id="stable-retry-id",
        )
    request = send.call_args.args[0]
    body = json.loads(request.data)
    assert (
        request.full_url
        == "https://graph.microsoft.com/v1.0/me/calendars/named%2Fcalendar/events"
    )
    assert request.method == "POST"
    assert body["attendees"] == [
        {"emailAddress": {"address": "caller@example.com"}, "type": "required"}
    ]
    assert body["transactionId"] == "stable-retry-id"
    assert body["body"] == {
        "contentType": "text",
        "content": "Confirmed plain-text notes",
    }
    assert body["start"] == {"dateTime": "2026-10-05T20:00:00", "timeZone": "UTC"}
    assert (
        "Mail.Send" not in MS_CALENDAR_SCOPES
        and "Calendars.ReadWrite" in MS_CALENDAR_SCOPES
    )
    assert 'IdType="ImmutableId"' in request.get_header("Prefer")


def test_legacy_graph_create_without_attendees_keeps_appointment_body():
    graph = client()
    with patch(
        "urllib.request.urlopen", return_value=response({"id": "event"})
    ) as send:
        graph.create_event(
            "Appointment",
            "Notes",
            datetime(2026, 10, 5, 20, tzinfo=timezone.utc),
            datetime(2026, 10, 5, 20, 30, tzinfo=timezone.utc),
        )
    assert "attendees" not in json.loads(send.call_args.args[0].data)


def test_selected_calendar_view_pagination_keeps_all_busy_events():
    graph = client()
    next_link = "https://graph.microsoft.com/v1.0/me/calendars/named%2Fcalendar/calendarView?$skip=1"
    with patch(
        "urllib.request.urlopen",
        side_effect=[
            response({"value": [{"id": "first"}], "@odata.nextLink": next_link}),
            response({"value": [{"id": "second"}]}),
        ],
    ) as send:
        events = graph.list_calendar_view(
            datetime(2026, 10, 5, tzinfo=timezone.utc),
            datetime(2026, 10, 6, tzinfo=timezone.utc),
        )
    assert [e["id"] for e in events] == ["first", "second"]
    assert "/named%2Fcalendar/calendarView?" in send.call_args_list[0].args[0].full_url
    assert send.call_args_list[1].args[0].full_url == next_link


def test_foreign_next_link_never_receives_authorization():
    graph = client()
    with patch("urllib.request.urlopen") as send:
        with pytest.raises(MicrosoftGraphApiError, match="Unexpected Graph pagination"):
            graph._request("GET", "https://untrusted.example/v1.0/events")
    send.assert_not_called()


def test_update_http_preserves_attendees_by_omitting_them():
    graph = client()
    with patch(
        "urllib.request.urlopen", return_value=response({"id": "event"})
    ) as send:
        graph.update_event(
            "event",
            {"start": {"dateTime": "2026-10-05T21:00:00", "timeZone": "UTC"}},
            'W/"version1"',
        )
    request = send.call_args.args[0]
    assert (
        request.method == "PATCH" and request.get_header("If-match") == 'W/"version1"'
    )
    assert "attendees" not in json.loads(request.data)


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("synthetic timeout"),
        urllib.error.URLError("unreachable"),
        OSError("connection reset"),
    ],
)
def test_transport_uncertainty_is_typed_and_does_not_retry(failure):
    graph = client()
    with patch("urllib.request.urlopen", side_effect=failure) as send:
        with pytest.raises(MicrosoftGraphApiError) as caught:
            graph._request("POST", "/me/calendars/test/events", body={})
    assert caught.value.error_code == "graph_unavailable" and send.call_count == 1


def test_delete_204_and_deleted_resource_404():
    graph = client()
    success = response({})
    success.read.return_value = b""
    with patch("urllib.request.urlopen", return_value=success):
        assert graph.delete_event("event")
    failure = urllib.error.HTTPError(
        "https://graph.microsoft.com",
        404,
        "gone",
        {},
        io.BytesIO(b'{"error":{"code":"ErrorItemNotFound"}}'),
    )
    with patch("urllib.request.urlopen", side_effect=failure):
        assert graph.delete_event("event") is False
