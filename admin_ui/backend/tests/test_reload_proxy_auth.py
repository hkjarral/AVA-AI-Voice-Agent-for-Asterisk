"""P2 (bot re-review): the admin_ui -> ai_engine /reload proxy must authenticate.

The engine's /reload handler requires localhost OR a valid HEALTH_API_TOKEN. In the
shipped docker-compose setup admin_ui reaches ai_engine over service DNS (not
localhost), so reload_ai_engine must attach the HEALTH_API_TOKEN as a Bearer
Authorization header (mirroring the /sessions/stats proxy) or "Apply Changes" fails.
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
async def test_reload_proxy_sends_authorization_header() -> None:
    """reload_ai_engine attaches Bearer HEALTH_API_TOKEN so reload works over service DNS."""
    captured = {}

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"message": "Configuration reloaded", "changes": []}

    async def fake_post(url, headers=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers or {}
        return fake_resp

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=fake_post)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(
        system, "_get_health_api_token", return_value="secret-token-123"
    ), patch("httpx.AsyncClient", return_value=fake_client):
        result = await system.reload_ai_engine()

    assert result["status"] == "success"
    assert captured["headers"].get("Authorization") == "Bearer secret-token-123", (
        "reload proxy must send the HEALTH_API_TOKEN so the engine accepts it over service DNS"
    )


@pytest.mark.asyncio
async def test_reload_proxy_no_token_sends_no_auth_header() -> None:
    """With no token configured, no Authorization header is sent (localhost-only deploys)."""
    captured = {}

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"message": "Configuration reloaded", "changes": []}

    async def fake_post(url, headers=None, **kwargs):
        captured["headers"] = headers or {}
        return fake_resp

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=fake_post)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(
        system, "_get_health_api_token", return_value=""
    ), patch("httpx.AsyncClient", return_value=fake_client):
        result = await system.reload_ai_engine()

    assert result["status"] == "success"
    assert "Authorization" not in captured["headers"]
