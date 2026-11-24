"""Per-instance Cloud credentials preserve operator choices over ambient keys."""
from unittest.mock import AsyncMock

import pytest

from src.config import GoogleProviderConfig
from src.pipelines.google import _GoogleCredentialManager


@pytest.mark.asyncio
@pytest.mark.parametrize("config,environment,expected_key,expected_path", [
    ({"api_key": "explicit", "credentials_path": "/configured.json"}, {"GOOGLE_API_KEY": "ambient"}, "explicit", "/configured.json"),
    ({"credentials_path": "/configured.json"}, {"GOOGLE_API_KEY": "ambient", "GOOGLE_APPLICATION_CREDENTIALS": "/ambient.json"}, None, "/configured.json"),
    ({}, {"GOOGLE_API_KEY": "ambient", "GOOGLE_APPLICATION_CREDENTIALS": "/ambient.json"}, "ambient", "/ambient.json"),
    ({}, {"GOOGLE_APPLICATION_CREDENTIALS": "/ambient.json"}, None, "/ambient.json"),
    ({}, {}, None, None),
])
async def test_google_credential_precedence(monkeypatch, config, environment, expected_key, expected_path):
    for name in ("GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    manager = _GoogleCredentialManager(GoogleProviderConfig(**config))
    manager._get_service_account_token = AsyncMock(return_value="access-token")
    headers, params = await manager.build_auth(("scope",))
    assert manager._service_account_path == expected_path
    if expected_key:
        assert (headers, params) == ({}, {"key": expected_key})
        manager._get_service_account_token.assert_not_awaited()
    elif expected_path:
        assert (headers, params) == ({"Authorization": "Bearer access-token"}, {})
        manager._get_service_account_token.assert_awaited_once_with(("scope",))
    else:
        assert (headers, params) == ({}, {})
        manager._get_service_account_token.assert_not_awaited()
