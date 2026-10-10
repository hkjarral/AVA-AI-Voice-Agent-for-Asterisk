"""Upgrade verification uses only synthetic cache/calendar data and mocked Graph HTTP."""

import asyncio
import io
import json
import sys
import urllib.error
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api import config
from src.tools.business.ms_graph_client import MicrosoftGraphClient


@pytest.fixture
def persisted_account(tmp_path, monkeypatch):
    cache = tmp_path / "microsoft-calendar-default-token-cache.json"
    cache.write_text('{"Account":{"synthetic":{"username":"scheduler@example.com"}}}')
    account = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "client_id": "22222222-2222-2222-2222-222222222222",
        "token_cache_path": str(cache),
        "user_principal_name": "scheduler@example.com",
        "calendar_id": "A" * 152,
        "timezone": "America/Phoenix",
    }
    saved = {
        "tools": {
            "microsoft_calendar": {
                "enabled": True,
                "accounts": {"default": deepcopy(account)},
            }
        }
    }
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: deepcopy(saved))
    monkeypatch.setattr(config, "MICROSOFT_CALENDAR_TOKEN_CACHE_PATH", str(cache))
    return account, saved, cache


def graph_response(payload):
    return io.BytesIO(json.dumps(payload).encode())


@pytest.mark.asyncio
@pytest.mark.parametrize("default_calendar", [True, False])
@pytest.mark.parametrize("returned_id", ["A" * 152, "B" * 68])
async def test_upgrade_verifies_original_saved_id_directly_despite_enumerated_alias(
    persisted_account, default_calendar, returned_id
):
    account, saved, cache = persisted_account
    before = deepcopy(saved), cache.read_bytes()
    requests = []

    def send(request, timeout):
        requests.append(request)
        assert "ImmutableId" not in request.get_header("Prefer")
        if request.full_url.endswith("/me"):
            return graph_response({"userPrincipalName": "scheduler@example.com"})
        if request.full_url.endswith("/me/calendars"):
            return graph_response(
                {"value": [{"id": "B" * 68, "name": "Calendar", "canEdit": True}]}
            )
        assert request.full_url.endswith("/me/calendars/" + account["calendar_id"])
        return graph_response(
            {
                "id": returned_id,
                "name": "Calendar" if default_calendar else "Named Calendar",
                "isDefaultCalendar": default_calendar,
                "canEdit": True,
            }
        )

    with patch.object(
        MicrosoftGraphClient, "acquire_token", return_value="synthetic-token"
    ), patch("urllib.request.urlopen", side_effect=send):
        # Discovery has a different representation. Verification must not use that as an identity gate.
        from src.tools.business.ms_graph_client import MicrosoftAccountConfig

        discovery = MicrosoftGraphClient(MicrosoftAccountConfig(**account))
        assert discovery.list_calendars()[0]["id"] != account["calendar_id"]
        requests.clear()
        result = await config.verify_microsoft_calendar(
            config._MicrosoftVerifyRequest()
        )
    assert result["status"] == "ok" and result["can_edit"] is True
    assert result["calendar_id"] == account["calendar_id"]
    assert [r.full_url for r in requests] == [
        "https://graph.microsoft.com/v1.0/me",
        "https://graph.microsoft.com/v1.0/me/calendars/" + account["calendar_id"],
    ]
    assert saved == before[0] and cache.read_bytes() == before[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected", [(404, "calendar_not_found"), (403, "forbidden_calendar")]
)
async def test_upgrade_missing_or_forbidden_calendar_never_falls_back(
    persisted_account, status, expected
):
    account, saved, cache = persisted_account
    original = deepcopy(saved), cache.read_bytes()
    calls = []

    def send(request, timeout):
        calls.append(request.full_url)
        if request.full_url.endswith("/me"):
            return graph_response({"userPrincipalName": "scheduler@example.com"})
        assert request.full_url.endswith("/me/calendars/" + account["calendar_id"])
        raise urllib.error.HTTPError(
            request.full_url,
            status,
            "synthetic failure",
            {},
            io.BytesIO(b'{"error":{"code":"SyntheticFailure"}}'),
        )

    with patch.object(
        MicrosoftGraphClient, "acquire_token", return_value="synthetic-token"
    ), patch("urllib.request.urlopen", side_effect=send):
        with pytest.raises(HTTPException) as caught:
            await config.verify_microsoft_calendar(config._MicrosoftVerifyRequest())
    assert (
        caught.value.status_code == status
        and caught.value.detail["error_code"] == expected
    )
    assert len(calls) == 2
    assert saved == original[0] and cache.read_bytes() == original[1]


@pytest.mark.asyncio
async def test_upgrade_read_only_calendar_is_rejected_without_selection_changes(
    persisted_account,
):
    account, saved, cache = persisted_account
    original = deepcopy(saved), cache.read_bytes()
    with patch.object(
        MicrosoftGraphClient, "acquire_token", return_value="synthetic-token"
    ), patch(
        "urllib.request.urlopen",
        side_effect=[
            graph_response({"userPrincipalName": "scheduler@example.com"}),
            graph_response({"id": "B" * 68, "name": "Calendar", "canEdit": False}),
        ],
    ) as send:
        with pytest.raises(HTTPException) as caught:
            await config.verify_microsoft_calendar(config._MicrosoftVerifyRequest())
    assert (
        caught.value.status_code == 403
        and caught.value.detail["error_code"] == "calendar_read_only"
    )
    assert (
        send.call_args_list[-1]
        .args[0]
        .full_url.endswith("/me/calendars/" + account["calendar_id"])
    )
    assert saved == original[0] and cache.read_bytes() == original[1]


@pytest.mark.parametrize(
    "cached_accounts,claim,expected",
    [
        (
            [{"username": "canonical@example.com"}],
            "alias@example.com",
            "canonical@example.com",
        ),
        (
            [{"username": "canonical@example.com"}, {"username": "other@example.com"}],
            "canonical@example.com",
            "canonical@example.com",
        ),
        (
            [{"username": "canonical@example.com"}, {"username": "other@example.com"}],
            "alias@example.com",
            None,
        ),
        ([{"username": ""}], "alias@example.com", None),
        ([], "alias@example.com", None),
    ],
)
def test_connect_returns_canonical_fresh_cache_username_without_ambiguous_identity(
    monkeypatch, cached_accounts, claim, expected
):
    import msal
    from unittest.mock import Mock

    app = Mock()
    app.get_accounts.return_value = cached_accounts
    app.acquire_token_by_device_flow.return_value = {
        "access_token": "synthetic-token",
        "id_token_claims": {"preferred_username": claim},
    }
    flow_id = "synthetic-canonical-flow"
    monkeypatch.setattr(
        config, "_MS_DEVICE_FLOWS", {flow_id: {"flow": {"synthetic": True}}}
    )
    persist = Mock()
    monkeypatch.setattr(config, "_persist_ms_token_cache", persist)
    monkeypatch.setattr(
        config, "_ms_token_cache_path_for_key", lambda key: "/synthetic/cache.json"
    )
    graph = Mock(
        side_effect=[
            {
                "userPrincipalName": "graph-upn@example.com",
                "mail": "graph-mail@example.com",
            },
            {"value": [{"id": "saved-calendar", "name": "Calendar"}]},
        ]
    )
    monkeypatch.setattr(config, "_ms_graph_request_with_token", graph)
    with patch.object(msal, "PublicClientApplication", return_value=app):
        config._ms_device_flow_worker(
            flow_id, "synthetic-tenant", "synthetic-client", "default"
        )
    state = config._MS_DEVICE_FLOWS[flow_id]
    if expected:
        assert state["status"] == "success"
        assert state["result"]["user_principal_name"] == expected
        assert state["result"]["user_principal_name"] != "graph-mail@example.com"
        assert persist.call_count == 1 and graph.call_count == 2
    else:
        assert (
            state["status"] == "error"
            and state["error"]["error_code"] == "authorization_failed"
        )
        persist.assert_not_called()
        graph.assert_not_called()


@pytest.mark.asyncio
async def test_verify_reports_identity_mismatch_without_reconnect_or_graph_access(
    persisted_account,
):
    from unittest.mock import Mock
    import msal

    account, saved, cache = persisted_account
    before = deepcopy(saved), cache.read_bytes()
    app = Mock()
    app.get_accounts.side_effect = [[], [{"username": "other@example.com"}]]
    with patch.object(msal, "PublicClientApplication", return_value=app), patch(
        "urllib.request.urlopen"
    ) as send:
        with pytest.raises(HTTPException) as caught:
            await config.verify_microsoft_calendar(config._MicrosoftVerifyRequest())
    assert caught.value.status_code == 401
    assert caught.value.detail["error_code"] == "account_identity_mismatch"
    assert "correct user_principal_name" in caught.value.detail["message"]
    assert "reconnect" not in caught.value.detail["message"].lower()
    app.acquire_token_silent.assert_not_called()
    send.assert_not_called()
    assert saved == before[0] and cache.read_bytes() == before[1]
