"""Network-free coverage for ARI Media WebSocket capability inventory."""

import asyncio
import time
from types import MappingProxyType

import pytest

from src.ari_client import (
    ARIClient,
    ARIModuleInventory,
    websocket_media_module_capability,
)


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, content_type=None):
        return self._payload


class _Session:
    closed = False

    def __init__(self, modules, *, module_status=200):
        self.modules = modules
        self.module_status = module_status
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if url.endswith("/asterisk/modules"):
            return _Response(self.module_status, self.modules)
        return _Response(200, {"system": {"version": "22.10.1"}})


class _WebSocket:
    async def close(self):
        return None


class _BlockingModulesResponse(_Response):
    def __init__(self, status, payload, entered, release):
        super().__init__(status, payload)
        self._entered = entered
        self._release = release

    async def __aenter__(self):
        self._entered.set()
        await self._release.wait()
        return self


class _BlockingSession(_Session):
    def __init__(self, modules):
        super().__init__(modules)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.module_requests = 0

    def get(self, url):
        self.urls.append(url)
        if url.endswith("/asterisk/modules"):
            self.module_requests += 1
            return _BlockingModulesResponse(
                self.module_status, self.modules, self.entered, self.release
            )
        return _Response(200, {"system": {"version": "22.10.1"}})


def _connected_client(session):
    client = ARIClient("user", "pass", "http://asterisk:8088/ari", "aava")
    client.http_session = session
    client.websocket = _WebSocket()
    client.running = True
    client._connected = True
    client._connection_generation = 1
    client._set_module_inventory_unavailable("test inventory pending")
    return client


def _websocket_modules():
    return [
        {"name": "chan_websocket.so", "status": "Running"},
        {"name": "res_websocket_client.so", "status": "Running"},
        {"name": "res_http_websocket.so", "status": "Running"},
        {"name": "res_ari_channels.so", "status": "Running"},
        {"name": "res_timing_timerfd.so", "status": "Running"},
    ]


@pytest.mark.asyncio
async def test_connect_caches_exact_modules_and_running_timing_backend(monkeypatch):
    client = ARIClient("user", "pass", "http://asterisk:8088/ari", "aava")
    client.http_session = _Session(_websocket_modules())

    async def _connect(*_args, **_kwargs):
        return _WebSocket()

    monkeypatch.setattr("src.ari_client.websockets.connect", _connect)

    await client.connect()

    snapshot = client.module_inventory
    assert client.is_connected is True
    assert snapshot.available is True
    assert snapshot.modules["chan_websocket"] == "Running"
    assert snapshot.connection_generation == 1
    with pytest.raises(TypeError):
        snapshot.modules["res_timing_timerfd"] = "Not Running"

    capability = client.websocket_media_module_capability()
    assert capability.ready is True
    assert capability.running_timing_modules == ("res_timing_timerfd",)


def test_websocket_capability_does_not_accept_similarly_named_modules():
    snapshot = ARIModuleInventory(
        modules=MappingProxyType(
            {
                "chan_websocket_extra": "Running",
                "res_websocket_client": "Running",
                "res_http_websocket": "Running",
                "res_ari_channels": "Running",
                "res_timing_timerfd": "Running",
            }
        ),
        available=True,
        connection_generation=7,
        captured_at_monotonic=1.0,
    )

    capability = websocket_media_module_capability(snapshot)

    assert capability.ready is False
    assert capability.missing_required_modules == ("chan_websocket",)
    assert capability.inventory_generation == 7


@pytest.mark.asyncio
async def test_inventory_failure_does_not_break_legacy_ari_connection(monkeypatch):
    client = ARIClient("user", "pass", "http://asterisk:8088/ari", "aava")
    client.http_session = _Session([], module_status=403)

    async def _connect(*_args, **_kwargs):
        return _WebSocket()

    monkeypatch.setattr("src.ari_client.websockets.connect", _connect)

    await client.connect()

    assert client.is_connected is True
    assert client.module_inventory.available is False
    assert client.websocket_media_module_capability().ready is False


@pytest.mark.asyncio
async def test_disconnect_invalidates_previous_generation_inventory():
    client = ARIClient("user", "pass", "http://asterisk:8088/ari", "aava")
    client._connection_generation = 3
    client._module_inventory = ARIModuleInventory(
        modules=MappingProxyType({"chan_websocket": "Running"}),
        available=True,
        connection_generation=3,
        captured_at_monotonic=1.0,
    )

    await client.disconnect()

    assert client.module_inventory.available is False
    assert client.module_inventory.connection_generation == 3
    assert client.websocket_media_module_capability().ready is False


@pytest.mark.asyncio
async def test_concurrent_inventory_refreshes_coalesce_to_one_request():
    session = _BlockingSession(_websocket_modules())
    client = _connected_client(session)

    first = asyncio.create_task(client.refresh_module_inventory(timeout_sec=0.5))
    await session.entered.wait()
    second = asyncio.create_task(client.refresh_module_inventory(timeout_sec=0.5))
    await asyncio.sleep(0)
    assert session.module_requests == 1

    session.release.set()
    first_snapshot, second_snapshot = await asyncio.gather(first, second)

    assert session.module_requests == 1
    assert first_snapshot is second_snapshot
    assert first_snapshot.available is True


@pytest.mark.asyncio
async def test_inventory_timeout_includes_lock_wait_and_fails_closed_until_owner_finishes():
    session = _BlockingSession(_websocket_modules())
    client = _connected_client(session)
    # Prove a waiting caller cannot retain an old successful result after it
    # misses its total refresh deadline.
    client._module_inventory = ARIModuleInventory(
        modules=MappingProxyType({"chan_websocket": "Running"}),
        available=True,
        connection_generation=1,
        captured_at_monotonic=0.0,
    )
    owner = asyncio.create_task(client.refresh_module_inventory(timeout_sec=0.5))
    await session.entered.wait()

    started = time.monotonic()
    timed_out_snapshot = await client.refresh_module_inventory(timeout_sec=0.01)
    elapsed = time.monotonic() - started

    assert elapsed < 0.15
    assert session.module_requests == 1
    assert timed_out_snapshot.available is False
    assert client.websocket_media_module_capability().ready is False

    session.release.set()
    completed_snapshot = await owner
    assert completed_snapshot.available is True
    assert client.websocket_media_module_capability().ready is True


@pytest.mark.asyncio
async def test_cancelled_waiter_cannot_leave_an_old_ready_snapshot_usable():
    session = _BlockingSession(_websocket_modules())
    client = _connected_client(session)
    client._module_inventory = ARIModuleInventory(
        modules=MappingProxyType(
            {
                "chan_websocket": "Running",
                "res_websocket_client": "Running",
                "res_http_websocket": "Running",
                "res_ari_channels": "Running",
                "res_timing_timerfd": "Running",
            }
        ),
        available=True,
        connection_generation=1,
        captured_at_monotonic=0.0,
    )
    owner = asyncio.create_task(client.refresh_module_inventory(timeout_sec=0.5))
    await session.entered.wait()
    waiter = asyncio.create_task(client.refresh_module_inventory(timeout_sec=0.5))
    await asyncio.sleep(0)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert client.module_inventory.available is False
    assert client.websocket_media_module_capability().ready is False

    session.release.set()
    assert (await owner).available is True
