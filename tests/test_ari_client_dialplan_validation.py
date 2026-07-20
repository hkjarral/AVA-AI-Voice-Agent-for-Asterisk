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
@pytest.mark.parametrize("response", [{"value": "0"}, {"status": 404}, {}, None])
async def test_dialplan_target_exists_fails_closed(response):
    client = _client(response)

    assert not await client.dialplan_target_exists(
        "chan-1", context="missing", extension="s", priority=1
    )


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
