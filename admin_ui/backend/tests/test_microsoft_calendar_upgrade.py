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
