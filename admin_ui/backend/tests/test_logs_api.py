import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import logs  # noqa: E402


class _Containers:
    def __init__(self, container):
        self._container = container

    def list(self, **_kwargs):
        return [self._container]


class _Client:
    def __init__(self, container):
        self.containers = _Containers(container)
        self.closed = False

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_slow_docker_logs_do_not_block_event_loop(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class _Container:
        id = "container-id"
        name = "ai_engine"

        def logs(self, **_kwargs):
            started.set()
            release.wait(timeout=1.0)
            return b'2026-01-01 INFO ready\n'

    client = _Client(_Container())
    monkeypatch.setattr(logs.docker, "from_env", lambda **_kwargs: client)

    started_at = time.monotonic()
    request = asyncio.create_task(
        logs.get_container_logs("ai_engine", tail=500, levels=None, q=None)
    )
    await asyncio.sleep(0.05)

    # With Docker work on the event loop, this sleep cannot resume until the
    # blocking logs call returns one second later.
    assert time.monotonic() - started_at < 0.3
    assert started.is_set()

    release.set()
    result = await request
    assert result["logs"] == "2026-01-01 INFO ready\n"
    assert client.closed is True


@pytest.mark.asyncio
async def test_missing_log_container_keeps_404(monkeypatch):
    class _MissingContainers:
        def list(self, **_kwargs):
            return []

        def get(self, _name):
            raise logs.docker.errors.NotFound("missing")

    client = _Client(None)
    client.containers = _MissingContainers()
    monkeypatch.setattr(logs.docker, "from_env", lambda **_kwargs: client)

    with pytest.raises(HTTPException) as exc_info:
        await logs.get_container_logs("missing", tail=100, levels=None, q=None)

    assert exc_info.value.status_code == 404
    assert client.closed is True


@pytest.mark.asyncio
async def test_log_reader_uses_bounded_docker_timeout(monkeypatch):
    captured = {}

    class _Container:
        id = "container-id"
        name = "ai_engine"

        def logs(self, **_kwargs):
            return b""

    def _from_env(**kwargs):
        captured.update(kwargs)
        return _Client(_Container())

    monkeypatch.setattr(logs.docker, "from_env", _from_env)

    await logs.get_container_logs("ai_engine", tail=10, levels=None, q=None)

    assert captured["timeout"] == logs._DOCKER_LOG_TIMEOUT_SECONDS
