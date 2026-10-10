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


def test_conditional_delete_version_conflict_is_typed_without_retry():
    graph = client()
    failure = urllib.error.HTTPError(
        "https://graph.microsoft.com",
        412,
        "conflict",
        {},
        io.BytesIO(b'{"error":{"code":"ErrorPreconditionFailed"}}'),
    )
    with patch("urllib.request.urlopen", side_effect=failure) as send:
        with pytest.raises(MicrosoftGraphApiError) as caught:
            graph.delete_event("event", etag='W/"version1"')
    request = send.call_args.args[0]
    assert (
        request.method == "DELETE" and request.get_header("If-match") == 'W/"version1"'
    )
    assert caught.value.error_code == "booking_changed" and caught.value.status == 412
    assert send.call_count == 1


def test_container_discovery_and_direct_calendar_keep_legacy_ids_without_event_preferences():
    graph = client()
    original = graph.account
    next_link = "https://graph.microsoft.com/v1.0/me/calendars?$skip=1"
    with patch(
        "urllib.request.urlopen",
        side_effect=[
            response({"id": "synthetic-user"}),
            response(
                {
                    "value": [{"id": "different-representation"}],
                    "@odata.nextLink": next_link,
                }
            ),
            response({"value": [{"id": "named-calendar"}]}),
            response({"id": "different-representation", "canEdit": True}),
        ],
    ) as send:
        graph.me()
        assert len(graph.list_calendars()) == 2
        assert graph.get_calendar()["canEdit"] is True
    assert graph.account == original
    assert (
        send.call_args_list[-1]
        .args[0]
        .full_url.endswith("/me/calendars/named%2Fcalendar")
    )
    for call in send.call_args_list:
        prefer = call.args[0].get_header("Prefer")
        assert "ImmutableId" not in prefer and "body-content-type" not in prefer


def test_event_preferences_survive_pagination_and_all_conditional_mutations():
    graph = client()
    next_link = "https://graph.microsoft.com/v1.0/me/calendars/named%2Fcalendar/calendarView?$skip=1"
    with patch(
        "urllib.request.urlopen",
        side_effect=[
            response({"value": [], "@odata.nextLink": next_link}),
            response({"value": []}),
            response({"id": "event"}),
            response({"id": "event"}),
            response({"id": "event"}),
            response({}),
        ],
    ) as send:
        start = datetime(2026, 10, 5, 20, tzinfo=timezone.utc)
        end = datetime(2026, 10, 5, 20, 30, tzinfo=timezone.utc)
        graph.list_calendar_view(start, end)
        graph.create_event(
            "Synthetic", "Notes", start, end, transaction_id="synthetic-transaction"
        )
        graph.get_event("event")
        graph.update_event("event", {"subject": "Synthetic"}, etag='W/"version1"')
        graph.delete_event("event", etag='W/"version2"')
    for call in send.call_args_list:
        request = call.args[0]
        assert 'IdType="ImmutableId"' in request.get_header("Prefer")
        assert 'outlook.body-content-type="text"' in request.get_header("Prefer")
        assert "/me/calendars/named%2Fcalendar/" in request.full_url
    assert send.call_args_list[-2].args[0].get_header("If-match") == 'W/"version1"'
    assert send.call_args_list[-1].args[0].get_header("If-match") == 'W/"version2"'


@pytest.mark.parametrize(
    "cached_users",
    [
        ["scheduler@example.com"],
        ["alias@example.com"],
        ["other@example.com"],
        ["other@example.com", "alias@example.com"],
        [],
    ],
)
def test_existing_cache_loads_unchanged_and_never_falls_back_to_another_identity(
    tmp_path, cached_users
):
    import msal
    from dataclasses import replace

    graph = MicrosoftGraphClient(
        replace(
            client().account, token_cache_path=str(tmp_path / "synthetic-cache.json")
        )
    )
    cache_data = {
        "Account": {
            f"synthetic-account-{index}": {
                "username": username,
                "home_account_id": f"synthetic-home-{index}",
                "environment": "login.microsoftonline.com",
                "realm": "synthetic",
            }
            for index, username in enumerate(cached_users)
        }
    }
    original = json.dumps(cache_data).encode()
    (tmp_path / "synthetic-cache.json").write_bytes(original)
    app = Mock()

    def application(*args, **kwargs):
        cache = kwargs["token_cache"]
        app.get_accounts.side_effect = lambda username=None: [
            a
            for a in cache.find(msal.TokenCache.CredentialType.ACCOUNT)
            if username is None or a["username"] == username
        ]
        app.acquire_token_silent.return_value = {"access_token": "synthetic-token"}
        return app

    with patch.object(graph._msal, "PublicClientApplication", side_effect=application):
        if "scheduler@example.com" in cached_users:
            assert graph.acquire_token() == "synthetic-token"
        else:
            with pytest.raises(MicrosoftGraphApiError) as caught:
                graph.acquire_token()
            assert caught.value.error_code == (
                "account_identity_mismatch" if cached_users else "auth_expired"
            )
            app.acquire_token_silent.assert_not_called()
    assert app.get_accounts.call_args_list[0].kwargs == {
        "username": "scheduler@example.com"
    }
    assert app.get_accounts.call_count == (
        1 if "scheduler@example.com" in cached_users else 2
    )
    assert (tmp_path / "synthetic-cache.json").read_bytes() == original
    assert graph.account.calendar_id == "named/calendar"
