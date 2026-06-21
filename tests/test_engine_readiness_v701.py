"""HIGH-2: /ready must reflect local default-provider connectivity.

Readiness used to flip healthy as soon as ARI + transport bind, because the
default-provider check used `is_ready()` (URL-present) rather than
`is_connected()`. While the local AI server is still loading models (a 5-10 min
window) the local provider's WS is not connected, yet `/ready` returned 200 and
load balancers routed calls into an engine whose default provider could not
serve -> caller answered into dead air.

The stricter check is scoped strictly to local default providers; non-local
default providers keep the existing URL-present behavior (admin_ui also consumes
/ready, so non-local readiness must not get stricter).
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine import Engine


def _make_engine(*, default_provider, providers, provider_kinds):
    """Minimal Engine wired only for the readiness path."""
    engine = Engine.__new__(Engine)
    engine.config = MagicMock()
    engine.config.default_provider = default_provider
    engine.config.audio_transport = "audiosocket"
    engine.config.pipelines = {}
    engine.providers = providers
    engine.provider_kinds = provider_kinds
    engine.pipeline_orchestrator = None

    # ARI + transport up so the only variable under test is default-provider readiness.
    engine.ari_client = MagicMock()
    engine.ari_client.is_connected = True
    engine.audio_socket_server = MagicMock()
    engine.rtp_server = None
    return engine


class _LocalProviderStub:
    """Mirrors the local provider: is_ready() = URL present, is_connected() = WS open."""

    def __init__(self, connected):
        self._connected = connected

    def is_ready(self):
        return True  # URL is always configured in these scenarios

    def is_connected(self):
        return self._connected


class _CloudProviderStub:
    def is_ready(self):
        return True

    def is_connected(self):
        return False  # cloud providers connect on demand; must not gate readiness


async def _call_ready(engine):
    resp = await engine._ready_handler(MagicMock())
    body = json.loads(resp.body.decode())
    return resp.status, body


@pytest.mark.asyncio
async def test_local_default_not_connected_is_not_ready():
    engine = _make_engine(
        default_provider="local",
        providers={"local": _LocalProviderStub(connected=False)},
        provider_kinds={"local": "local"},
    )
    status, body = await _call_ready(engine)
    assert status == 503
    assert body["ready"] is False


@pytest.mark.asyncio
async def test_local_default_connected_is_ready():
    engine = _make_engine(
        default_provider="local",
        providers={"local": _LocalProviderStub(connected=True)},
        provider_kinds={"local": "local"},
    )
    status, body = await _call_ready(engine)
    assert status == 200
    assert body["ready"] is True


@pytest.mark.asyncio
async def test_cloud_default_ready_regardless_of_connection():
    """Non-local default provider keeps URL-present behavior (old behavior)."""
    engine = _make_engine(
        default_provider="openai_realtime",
        providers={"openai_realtime": _CloudProviderStub()},
        provider_kinds={"openai_realtime": "openai_realtime"},
    )
    status, body = await _call_ready(engine)
    assert status == 200
    assert body["ready"] is True


# ---------------------------------------------------------------------------
# Readiness-deadlock fix: a startup warm probe must connect the local provider
# WITHOUT requiring a prior call. HIGH-2 made /ready depend on is_connected(),
# but LocalProvider only opens its WS lazily in start_session() on the first
# admitted call. On a local-default install that produced a deadlock: /ready
# stayed 503 forever (no call -> no WS -> not connected), so a load balancer
# gating on /ready never routed the first call that would have opened the WS.
# ---------------------------------------------------------------------------


class _WarmLocalProviderStub:
    """Local provider whose WS opens only when initialize() is awaited.

    Mirrors LocalProvider: initialize() is idempotent and, on success, leaves
    the WS open so is_connected() flips True without a call.
    """

    def __init__(self, *, connect_succeeds=True):
        self._connected = False
        self._connect_succeeds = connect_succeeds
        self.initialize_calls = 0

    async def initialize(self):
        self.initialize_calls += 1
        if self._connect_succeeds:
            self._connected = True

    def is_ready(self):
        return True

    def is_connected(self):
        return self._connected


def _make_warm_engine(*, default_provider, providers, provider_kinds):
    engine = _make_engine(
        default_provider=default_provider,
        providers=providers,
        provider_kinds=provider_kinds,
    )
    engine._local_warm_task = None
    return engine


@pytest.mark.asyncio
async def test_local_warm_probe_connects_without_a_call():
    """Deadlock fix: warm loop opens the WS so /ready -> 200 with no call."""
    prov = _WarmLocalProviderStub(connect_succeeds=True)
    engine = _make_warm_engine(
        default_provider="local",
        providers={"local": prov},
        provider_kinds={"local": "local"},
    )

    # Before warm: not connected -> not ready (this is the deadlock state).
    status, _ = await _call_ready(engine)
    assert status == 503

    # Run one warm pass (no call has occurred).
    await engine._local_warm_probe_once()
    assert prov.initialize_calls == 1
    assert prov.is_connected() is True

    # After warm: ready WITHOUT a call ever arriving.
    status, body = await _call_ready(engine)
    assert status == 200
    assert body["ready"] is True


@pytest.mark.asyncio
async def test_local_warm_probe_server_down_stays_not_ready():
    """HIGH-2 preserved: server down/loading -> warm fails -> still not ready."""
    prov = _WarmLocalProviderStub(connect_succeeds=False)
    engine = _make_warm_engine(
        default_provider="local",
        providers={"local": prov},
        provider_kinds={"local": "local"},
    )
    await engine._local_warm_probe_once()
    assert prov.initialize_calls == 1
    assert prov.is_connected() is False

    status, body = await _call_ready(engine)
    assert status == 503
    assert body["ready"] is False


@pytest.mark.asyncio
async def test_local_warm_probe_noop_for_non_local_default():
    """Non-local default: warm probe must not touch the provider."""
    prov = _WarmLocalProviderStub(connect_succeeds=True)
    engine = _make_warm_engine(
        default_provider="openai_realtime",
        providers={"openai_realtime": prov},
        provider_kinds={"openai_realtime": "openai_realtime"},
    )
    await engine._local_warm_probe_once()
    assert prov.initialize_calls == 0


@pytest.mark.asyncio
async def test_local_warm_loop_stops_once_connected():
    """The warm loop should connect, then idle (not re-initialize each tick)."""
    prov = _WarmLocalProviderStub(connect_succeeds=True)
    engine = _make_warm_engine(
        default_provider="local",
        providers={"local": prov},
        provider_kinds={"local": "local"},
    )
    # Tiny interval so the loop ticks fast under the test timeout.
    task = asyncio.create_task(engine._local_warm_loop(interval_sec=0.01))
    try:
        for _ in range(100):
            if prov.is_connected():
                break
            await asyncio.sleep(0.01)
        assert prov.is_connected() is True
        first = prov.initialize_calls
        await asyncio.sleep(0.05)
        # Already connected -> loop must not keep calling initialize().
        assert prov.initialize_calls == first
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
