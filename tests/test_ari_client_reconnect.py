import asyncio

import pytest

from src.ari_client import ARIClient
from src.config import AsteriskConfig


class _CleanlyEndingWebSocket:
    def __init__(self):
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


class _InfoResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, content_type=None):
        return {"system": {"version": "22.0.0"}}


class _HTTPSession:
    closed = False

    def get(self, _url):
        return _InfoResponse()


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


@pytest.mark.unit
async def test_ari_connect_uses_explicit_bounded_keepalive(monkeypatch):
    client = ARIClient(
        "user",
        "pass",
        "http://asterisk:8088/ari",
        "ava",
        ws_ping_interval_sec=9,
        ws_ping_timeout_sec=11,
    )
    client.http_session = _HTTPSession()
    websocket = _CleanlyEndingWebSocket()
    connect_kwargs = {}

    async def fake_connect(url, **kwargs):
        connect_kwargs["url"] = url
        connect_kwargs.update(kwargs)
        return websocket

    monkeypatch.setattr("src.ari_client.websockets.connect", fake_connect)

    await client.connect()

    assert client.is_connected is True
    assert connect_kwargs["ping_interval"] == 9.0
    assert connect_kwargs["ping_timeout"] == 11.0
    assert connect_kwargs["ssl"] is None


@pytest.mark.unit
def test_asterisk_keepalive_defaults_bound_silent_failure_to_twenty_seconds():
    config = AsteriskConfig(host="asterisk", username="user", password="pass")

    assert config.ws_ping_interval_sec == 10.0
    assert config.ws_ping_timeout_sec == 10.0
    assert config.ws_ping_interval_sec + config.ws_ping_timeout_sec == 20.0


@pytest.mark.unit
@pytest.mark.parametrize("field", ["ws_ping_interval_sec", "ws_ping_timeout_sec"])
def test_asterisk_keepalive_rejects_aggressive_values(field):
    with pytest.raises(ValueError):
        AsteriskConfig(
            host="asterisk",
            username="user",
            password="pass",
            **{field: 1},
        )
