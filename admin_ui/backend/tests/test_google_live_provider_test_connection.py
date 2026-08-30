"""Regression tests for #633: Google Live "Test Connection" ignoring managed secrets.

test_provider_connection() resolves api_key_file / api_key_env into
provider_config["api_key"] before it decides which provider branch to run,
but the google_live branch used to ignore that and read GOOGLE_API_KEY
straight out of .env. These tests pin the fixed behavior: a resolved key
(from either source) is what gets sent to Google, the .env fallback still
works when no managed secret is configured, and the endpoint fails fast
with no network call when nothing resolves.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from api import config  # noqa: E402


def _fake_google_models_client(captured):
    """A fake httpx.AsyncClient whose GET records the URL it was called with."""
    fake_resp = MagicMock()
    fake_resp.status_code = 200

    async def fake_get(url, timeout=None):
        captured["url"] = url
        return fake_resp

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    return fake_client


def _google_live_request(**extra_config):
    return config.ProviderTestRequest(
        name="google_live",
        config={
            "type": "google_live",
            "llm_model": "gemini-2.5-flash-native-audio-latest",
            **extra_config,
        },
    )


@pytest.mark.asyncio
async def test_uses_resolved_key_from_api_key_file(monkeypatch, tmp_path):
    """A key delivered through api_key_file is what reaches Google, not .env."""
    env_path = tmp_path / ".env"
    env_path.write_text("GOOGLE_API_KEY=\n", encoding="utf-8")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))
    monkeypatch.setattr(
        config,
        "_provider_instances_module",
        lambda: {"resolve_secret_value": lambda *a, **k: "key-from-file"},
    )

    request = _google_live_request(api_key_file="/app/project/secrets/providers/google_live/api-key")
    captured = {}

    with patch("httpx.AsyncClient", return_value=_fake_google_models_client(captured)):
        result = await config.test_provider_connection(request)

    assert result["success"] is True
    assert "key=key-from-file" in captured["url"]


@pytest.mark.asyncio
async def test_uses_resolved_key_from_api_key_env(monkeypatch, tmp_path):
    """A key delivered through api_key_env is what reaches Google, not .env."""
    env_path = tmp_path / ".env"
    env_path.write_text("GOOGLE_API_KEY=\n", encoding="utf-8")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))
    monkeypatch.setattr(
        config,
        "_provider_instances_module",
        lambda: {"resolve_secret_value": lambda *a, **k: "key-from-env-ref"},
    )

    request = _google_live_request(api_key_env="MY_GOOGLE_KEY_REF")
    captured = {}

    with patch("httpx.AsyncClient", return_value=_fake_google_models_client(captured)):
        result = await config.test_provider_connection(request)

    assert result["success"] is True
    assert "key=key-from-env-ref" in captured["url"]


@pytest.mark.asyncio
async def test_falls_back_to_dotenv_when_no_managed_secret_configured(monkeypatch, tmp_path):
    """No api_key_file or api_key_env set: the plain .env GOOGLE_API_KEY still works."""
    env_path = tmp_path / ".env"
    env_path.write_text("GOOGLE_API_KEY=dotenv-key\n", encoding="utf-8")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))

    request = _google_live_request()
    captured = {}

    with patch("httpx.AsyncClient", return_value=_fake_google_models_client(captured)):
        result = await config.test_provider_connection(request)

    assert result["success"] is True
    assert "key=dotenv-key" in captured["url"]


@pytest.mark.asyncio
async def test_reports_missing_key_without_calling_google(monkeypatch, tmp_path):
    """Nothing resolves anywhere: fail with the new message, never touch the network."""
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))

    request = _google_live_request()

    with patch("httpx.AsyncClient") as mock_client_cls:
        result = await config.test_provider_connection(request)

    mock_client_cls.assert_not_called()
    assert result == {
        "success": False,
        "message": "No Google API key configured (checked api_key_file/api_key_env and GOOGLE_API_KEY in .env)",
    }
