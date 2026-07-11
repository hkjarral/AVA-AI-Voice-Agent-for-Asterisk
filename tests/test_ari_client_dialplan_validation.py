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
