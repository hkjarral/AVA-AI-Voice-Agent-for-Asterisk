"""Tests for the /api/system/config-state proxy route.

The route proxies GET /config/state from the AI Engine health server so the
Admin UI frontend can poll restart-reconciliation state without reaching the
engine directly.

Auth is covered by the router-level Depends(auth.get_current_user); these tests
exercise the async handler function directly, mirroring the pattern used by
test_reload_proxy_auth.py and test_restart_recreate_routing.py.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from api import system  # noqa: E402


@pytest.mark.asyncio
async def test_config_state_proxies_engine() -> None:
    """On a 200 response the route returns the engine's JSON plus engine_reachable=True."""
    engine_payload = {
        "running_config_hash": "abc123",
        "disk_config_hash": "abc123",
        "restart_required": False,
        "disk_config_valid": True,
    }

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = dict(engine_payload)

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=fake_client):
        result = await system.get_config_state()

    assert result["running_config_hash"] == "abc123"
    assert result["disk_config_hash"] == "abc123"
    assert result["restart_required"] is False
    assert result["disk_config_valid"] is True
    assert result["engine_reachable"] is True


@pytest.mark.asyncio
async def test_config_state_engine_unreachable_safe() -> None:
    """When the engine raises (unreachable/timeout), the route returns the safe fallback with HTTP 200.

    Specifically: restart_required must be False and engine_reachable must be False so
    the frontend banner never false-alarms when the engine is simply down.
    """
    import httpx

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=fake_client):
        result = await system.get_config_state()

    assert result["restart_required"] is False, (
        "Must not report restart_required=True just because the engine is unreachable"
    )
    assert result["engine_reachable"] is False
    assert result["running_config_hash"] is None
    assert result["disk_config_hash"] is None
    assert result["disk_config_valid"] is True


@pytest.mark.asyncio
async def test_config_state_non_200_returns_safe_fallback() -> None:
    """A non-200 HTTP response from the engine also yields the safe fallback."""
    fake_resp = MagicMock()
    fake_resp.status_code = 503

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=fake_client):
        result = await system.get_config_state()

    assert result["restart_required"] is False
    assert result["engine_reachable"] is False


@pytest.mark.asyncio
async def test_config_state_uses_ai_engine_health_url_env(monkeypatch) -> None:
    """The proxy constructs the engine URL from AI_ENGINE_HEALTH_URL env var."""
    monkeypatch.setenv("AI_ENGINE_HEALTH_URL", "http://ai_engine:15000")

    captured = {}

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "running_config_hash": "xyz",
        "disk_config_hash": "xyz",
        "restart_required": False,
        "disk_config_valid": True,
    }

    async def fake_get(url, **kwargs):
        captured["url"] = url
        return fake_resp

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=fake_client):
        await system.get_config_state()

    assert captured["url"] == "http://ai_engine:15000/config/state"
