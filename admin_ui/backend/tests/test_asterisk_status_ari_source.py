"""Tests for the asterisk_status ARI-source unification (I3) and probe hardening (I2).

I3: `live.ari_reachable` should prefer the engine's authoritative, reconnect-supervised
ARI state (exposed on the engine `/health` as top-level `ari_connected`, which the admin_ui
wraps as `ai_engine.details.ari_connected`) over a flappy per-poll direct probe.

I2: the direct-probe fallback must be hardened (httpx retries + split connect/read timeouts)
and the synchronous `.env` reads must run off the event loop.
"""

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import system  # noqa: E402


@pytest.mark.asyncio
async def test_ari_reachable_prefers_engine_connected_true(monkeypatch):
    """When the engine reports ari_connected=True, ari_reachable is True and stays True
    even if the best-effort enrichment probe raises (the engine state is authoritative)."""
    monkeypatch.setattr(
        system,
        "_ari_env_settings",
        lambda: {
            "host": "127.0.0.1",
            "scheme": "http",
            "port": 8088,
            "username": "u",
            "password": "p",
            "ssl_verify": True,
        },
    )

    async def fake_engine_ari():
        return True

    monkeypatch.setattr(system, "_engine_health_ari_connected", fake_engine_ari)

    # Enrichment probe may run for version/module detail, but a probe failure must NOT
    # flip the sticky reachability the engine just confirmed.
    async def failing_enrichment(settings, live):
        raise RuntimeError("transient ARI hiccup")

    monkeypatch.setattr(system, "_probe_asterisk_ari", failing_enrichment)

    result = await system.asterisk_status()
    assert result["live"]["ari_reachable"] is True


@pytest.mark.asyncio
async def test_ari_reachable_prefers_engine_connected_false(monkeypatch):
    """When the engine reports ari_connected=False (sticky, reconnect-supervised),
    ari_reachable is False and we don't fall back to a direct probe."""
    monkeypatch.setattr(
        system,
        "_ari_env_settings",
        lambda: {
            "host": "127.0.0.1",
            "scheme": "http",
            "port": 8088,
            "username": "u",
            "password": "p",
            "ssl_verify": True,
        },
    )

    async def fake_engine_ari():
        return False

    monkeypatch.setattr(system, "_engine_health_ari_connected", fake_engine_ari)

    async def boom(*_a, **_k):
        raise AssertionError("direct ARI probe must not run when engine state is available")

    monkeypatch.setattr(system, "_probe_asterisk_ari", boom)

    result = await system.asterisk_status()
    assert result["live"]["ari_reachable"] is False


@pytest.mark.asyncio
async def test_ari_reachable_falls_back_to_direct_probe_when_engine_unavailable(monkeypatch):
    """When the engine health is unavailable (None), fall back to the direct ARI probe."""
    monkeypatch.setattr(
        system,
        "_ari_env_settings",
        lambda: {
            "host": "127.0.0.1",
            "scheme": "http",
            "port": 8088,
            "username": "u",
            "password": "p",
            "ssl_verify": True,
        },
    )

    async def engine_unavailable():
        return None

    monkeypatch.setattr(system, "_engine_health_ari_connected", engine_unavailable)

    called = {"n": 0}

    async def fake_probe(settings, live):
        called["n"] += 1
        live["ari_reachable"] = True
        live["asterisk_version"] = "20.0.0"

    monkeypatch.setattr(system, "_probe_asterisk_ari", fake_probe)

    result = await system.asterisk_status()
    assert called["n"] == 1
    assert result["live"]["ari_reachable"] is True
    assert result["live"]["asterisk_version"] == "20.0.0"


@pytest.mark.asyncio
async def test_engine_health_ari_connected_reads_top_level_flag(monkeypatch):
    """_engine_health_ari_connected parses the engine /health top-level `ari_connected`."""
    import httpx

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"status": "healthy", "ari_connected": True}

    class FakeClient:
        def __init__(self, *a, **k):
            captured["transport"] = k.get("transport")
            captured["timeout"] = k.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            captured.setdefault("urls", []).append(url)
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    val = await system._engine_health_ari_connected()
    assert val is True
    # Hardened: retries configured on the transport, split timeout object used.
    assert captured["transport"] is not None
    assert captured["timeout"] is not None


@pytest.mark.asyncio
async def test_engine_health_ari_connected_returns_none_on_total_failure(monkeypatch):
    """If every engine URL candidate fails, return None (unavailable), not False."""
    import httpx

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    val = await system._engine_health_ari_connected()
    assert val is None


@pytest.mark.asyncio
async def test_probe_uses_retrying_transport(monkeypatch):
    """The direct-probe fallback builds an httpx client with a retrying transport and a
    split connect/read timeout so a single RST/jitter doesn't yield False."""
    import httpx

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"system": {"version": "20.0.0"}, "status": {}}

    class FakeClient:
        def __init__(self, *a, **k):
            captured["transport"] = k.get("transport")
            captured["timeout"] = k.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, auth=None):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    settings = {
        "host": "127.0.0.1",
        "scheme": "http",
        "port": 8088,
        "username": "u",
        "password": "p",
        "ssl_verify": True,
    }
    live = {"ari_reachable": False, "modules": {}, "app_name": "x", "app_registered": False}
    await system._probe_asterisk_ari(settings, live)

    assert live["ari_reachable"] is True
    assert isinstance(captured["transport"], httpx.AsyncHTTPTransport)
    assert isinstance(captured["timeout"], httpx.Timeout)


@pytest.mark.asyncio
async def test_engine_health_returns_none_when_field_missing(monkeypatch):
    """A 200 /health response that lacks `ari_connected` (schema drift) must yield None
    (unknown → caller probes), NOT False (which would mis-report disconnected)."""
    import httpx

    class FakeResp:
        status_code = 200

        def json(self):
            return {"status": "healthy"}  # no ari_connected field

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    assert await system._engine_health_ari_connected() is None


@pytest.mark.asyncio
async def test_engine_state_used_without_direct_probe_credentials(monkeypatch):
    """The engine's ARI state is authoritative even when THIS service has no direct-probe
    credentials — reachability must reflect the engine, and the probe must not run."""
    monkeypatch.setattr(
        system,
        "_ari_env_settings",
        lambda: {
            "host": "127.0.0.1",
            "scheme": "http",
            "port": 8088,
            "username": "",
            "password": "",
            "ssl_verify": True,
        },
    )

    async def engine_connected():
        return True

    monkeypatch.setattr(system, "_engine_health_ari_connected", engine_connected)

    async def boom(*_a, **_k):
        raise AssertionError("direct probe must not run without credentials")

    monkeypatch.setattr(system, "_probe_asterisk_ari", boom)

    result = await system.asterisk_status()
    assert result["live"]["ari_reachable"] is True


@pytest.mark.asyncio
async def test_app_registered_true_when_engine_confirmed_and_probe_fails(monkeypatch):
    """An engine-confirmed ARI connection implies the Stasis app is registered; a failed
    enrichment probe must not leave a false "Not Registered" (app_registered=False)."""
    monkeypatch.setattr(
        system,
        "_ari_env_settings",
        lambda: {
            "host": "127.0.0.1",
            "scheme": "http",
            "port": 8088,
            "username": "u",
            "password": "p",
            "ssl_verify": True,
        },
    )

    async def engine_connected():
        return True

    monkeypatch.setattr(system, "_engine_health_ari_connected", engine_connected)

    async def failing_enrichment(settings, live):
        raise RuntimeError("transient ARI hiccup")

    monkeypatch.setattr(system, "_probe_asterisk_ari", failing_enrichment)

    result = await system.asterisk_status()
    assert result["live"]["app_registered"] is True
    assert result["live"]["ari_reachable"] is True


@pytest.mark.asyncio
async def test_app_registration_disproval_preserved(monkeypatch):
    """If the enrichment probe authoritatively checks /ari/applications and the configured
    app is NOT registered (e.g. app renamed without an engine restart), preserve that
    disproval rather than assuming registered — and don't leak the internal sentinel."""
    monkeypatch.setattr(
        system,
        "_ari_env_settings",
        lambda: {
            "host": "127.0.0.1",
            "scheme": "http",
            "port": 8088,
            "username": "u",
            "password": "p",
            "ssl_verify": True,
        },
    )

    async def engine_connected():
        return True

    monkeypatch.setattr(system, "_engine_health_ari_connected", engine_connected)

    async def probe_disproves(settings, live):
        live["_app_registration_checked"] = True  # the app list was queried...
        live["app_registered"] = False            # ...and the configured app wasn't there

    monkeypatch.setattr(system, "_probe_asterisk_ari", probe_disproves)

    result = await system.asterisk_status()
    assert result["live"]["app_registered"] is False
    assert "_app_registration_checked" not in result["live"]
    assert result["live"]["ari_reachable"] is True
