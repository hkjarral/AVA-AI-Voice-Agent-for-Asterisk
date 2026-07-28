"""Dialplan target validation must fail closed before ARI channel continuation."""

from unittest.mock import AsyncMock

import pytest

from src.ari_client import ARIClient


def _client(response):
    client = ARIClient.__new__(ARIClient)
    client.send_command = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_dialplan_target_exists_reads_asterisk_function():
    client = _client({"value": "1"})

    exists = await client.dialplan_target_exists(
        "chan-1", context="aava-provider-failure", extension="s", priority=1
    )

    assert exists is True
    client.send_command.assert_awaited_once_with(
        "GET",
        "channels/chan-1/variable",
        params={"variable": "DIALPLAN_EXISTS(aava-provider-failure,s,1)"},
        tolerate_statuses=[404],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"value": "0"}, False),
        ({"value": "false"}, False),
        ({"status": 404}, None),
        ({}, None),
        (None, None),
    ],
)
async def test_dialplan_target_exists_distinguishes_missing_from_unavailable(response, expected):
    client = _client(response)

    assert await client.dialplan_target_exists(
        "chan-1", context="missing", extension="s", priority=1
    ) is expected


@pytest.mark.asyncio
async def test_dialplan_target_exists_rejects_function_argument_injection():
    client = _client({"value": "1"})

    assert not await client.dialplan_target_exists(
        "chan-1", context="safe),SHELL(id", extension="s", priority=1
    )
    client.send_command.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"status": 200}, True),
        ({"status": 204}, True),
        ({"status": 302}, None),
        ({"status": 400}, False),
        ({"status": 404}, False),
        ({"status": 500}, None),
        ({"status": 503}, None),
        ({"status": "invalid"}, None),
        ({}, None),
        (None, None),
    ],
)
async def test_continue_in_dialplan_returns_tri_state_status(response, expected):
    client = _client(response)

    assert await client.continue_in_dialplan(
        "chan-1", context="ext-queues", extension="1000", priority=1
    ) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [({}, True), ({"status": 204}, True), ({"status": 500}, False), (None, False)],
)
async def test_set_channel_var_checks_ari_response_status(response, expected):
    client = _client(response)

    assert await client.set_channel_var("chan-1", "AI_AGENT", "demo") is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [({"status": 204}, True), ({"status": 404}, True), ({"status": 500}, False)],
)
async def test_hangup_channel_checks_ari_response_status(response, expected):
    client = _client(response)

    assert await client.hangup_channel("chan-1") is expected
