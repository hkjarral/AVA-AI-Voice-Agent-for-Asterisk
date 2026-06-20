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

import json
from unittest.mock import MagicMock

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
