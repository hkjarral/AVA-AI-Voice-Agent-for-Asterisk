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
async def test_missing_allowed_log_container_keeps_404(monkeypatch):
    class _MissingContainers:
        def list(self, **_kwargs):
            return []

        def get(self, _name):
            raise logs.docker.errors.NotFound("missing")

    client = _Client(None)
    client.containers = _MissingContainers()
    monkeypatch.setattr(logs.docker, "from_env", lambda **_kwargs: client)

    with pytest.raises(HTTPException) as exc_info:
        await logs.get_container_logs("admin_ui", tail=100, levels=None, q=None)

    assert exc_info.value.status_code == 404
    assert client.closed is True


@pytest.mark.asyncio
async def test_unknown_log_container_is_rejected_before_docker_lookup(monkeypatch):
    called = False

    def _from_env(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Docker should not be queried")

    monkeypatch.setattr(logs.docker, "from_env", _from_env)
    with pytest.raises(HTTPException) as exc_info:
        await logs.get_container_logs("arbitrary_container", tail=100, levels=None, q=None)

    assert exc_info.value.status_code == 400
    assert called is False


def test_related_ids_expand_through_an_already_known_bridge():
    parsed = []
    for line in (
        '2026-01-01T00:00:00Z [INFO] joined [src.engine] call_id=1789247215.144 bridge_id=bridge-1',
        '2026-01-01T00:00:01Z [INFO] media joined [src.engine] external_media_id=media-2 bridge_id=bridge-1',
    ):
        parsed.append(logs.parse_log_line(line))

    related, bridges = logs._compute_related_ids(parsed, "1789247215.144")

    assert related == ["1789247215.144", "media-2"]
    assert bridges == ["bridge-1"]


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


@pytest.mark.parametrize(
    ("endpoint", "kwargs"),
    [
        (logs.get_container_logs, {"tail": 10, "levels": None, "q": None}),
        (logs.get_container_log_events, {"levels": None, "categories": None}),
    ],
)
@pytest.mark.asyncio
async def test_log_read_failures_sanitize_container_name(endpoint, kwargs, monkeypatch, caplog):
    def _fail(*_args, **_kwargs):
        raise RuntimeError("docker unavailable")

    monkeypatch.setattr(logs, "_read_container_logs_sync", _fail)

    with pytest.raises(HTTPException) as exc_info:
        await endpoint("ai_engine\r\nforged-entry", **kwargs)

    assert exc_info.value.status_code == 500
    messages = [record.getMessage() for record in caplog.records]
    assert any("ai_engineforged-entry" in message for message in messages)
    assert all("\r" not in message and "\n" not in message for message in messages)
