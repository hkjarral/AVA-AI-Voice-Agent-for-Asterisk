import asyncio

import pytest

from src.ari_client import ARIClient


class _CleanlyEndingWebSocket:
    def __init__(self):
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


@pytest.mark.unit
async def test_ari_listener_handles_clean_iterator_end_without_tight_loop(monkeypatch):
    client = ARIClient("user", "pass", "http://asterisk:8088/ari", "ava")
    websocket = _CleanlyEndingWebSocket()
    client._should_reconnect = True
    client.running = True
    client._connected = True
    client.websocket = websocket

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        client._should_reconnect = False

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await client._listen_with_reconnect()

    assert client.running is False
    assert client._connected is False
    assert client.websocket is None
    assert websocket.closed is True
    assert sleeps == [0.5]
    assert client._reconnect_attempt == 1


@pytest.mark.unit
async def test_ari_listener_connect_failure_backoff_stops_on_shutdown(monkeypatch):
    client = ARIClient("user", "pass", "http://asterisk:8088/ari", "ava")
    client._should_reconnect = True

    async def fail_connect():
        raise ConnectionError("ARI not ready")

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        client._should_reconnect = False

    monkeypatch.setattr(client, "connect", fail_connect)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await client._listen_with_reconnect()

    assert client.running is False
    assert client._connected is False
    assert client.websocket is None
    assert sleeps == [0.5]
    assert client._reconnect_attempt == 1
