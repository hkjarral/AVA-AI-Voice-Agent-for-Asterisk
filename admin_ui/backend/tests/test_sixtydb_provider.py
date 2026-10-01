"""Operator credential checks for the modular 60db TTS provider."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api import config, wizard


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog, expected", [
    ({"data": [{"voice_id": "workspace-voice"}]}, True),
    ({"data": [{"voice_id": "another-voice"}]}, False),
    ([], False),
])
async def test_workspace_voice_check_uses_managed_key(monkeypatch, catalog, expected):
    monkeypatch.setattr(config, "_provider_instances_module", lambda: {
        "resolve_secret_value": lambda *args, **kwargs: "managed-secret",
    })
    response = MagicMock(status_code=200)
    response.json.return_value = catalog
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    request = config.ProviderTestRequest(name="custom_tts", config={
        "type": "sixtydb", "voice_id": "workspace-voice",
        "api_key_file": "/app/project/secrets/providers/custom_tts/api-key",
    })
    with patch("httpx.AsyncClient", return_value=client) as factory:
        result = await config.test_provider_connection(request)
    assert result["success"] is expected
    factory.assert_called_once_with(timeout=10.0, follow_redirects=False)
    assert client.get.await_count == (1 if expected else 2)
    assert client.get.await_args_list[0].args == ("https://api.60db.ai/voices?model=quality",)
    assert all(call.kwargs["headers"] == {"Authorization": "Bearer managed-secret"}
               for call in client.get.await_args_list)
    assert "managed-secret" not in str(result)


@pytest.mark.asyncio
async def test_key_verification_rejects_redirects(monkeypatch):
    monkeypatch.setattr(config, "_get_provider_block", lambda key: (
        {}, {"type": "sixtydb"}, "sixtydb",
    ))
    monkeypatch.setattr(config, "_provider_instances_module", lambda: {
        "resolve_secret_value": lambda *args, **kwargs: "managed-secret",
    })
    client = MagicMock()
    client.get = AsyncMock(return_value=MagicMock(status_code=302))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("httpx.AsyncClient", return_value=client), pytest.raises(HTTPException) as error:
        await config.verify_provider_credentials("custom_tts")
    assert error.value.status_code == 400
    assert "managed-secret" not in str(error.value.detail)


@pytest.mark.parametrize("provider", ["sixtydb", "sixtydb_tts"])
def test_wizard_cannot_select_tts_as_a_full_agent(provider):
    setup = wizard.SetupConfig(
        provider=provider, asterisk_host="127.0.0.1", asterisk_username="asterisk",
        asterisk_password="test-password", greeting="Hello", ai_name="Ava", ai_role="assistant",
    )
    with pytest.raises(HTTPException) as error:
        wizard._validate_setup_provider_credentials(setup)
    assert error.value.status_code == 400
    assert "modular TTS" in error.value.detail


@pytest.mark.asyncio
async def test_fast_workspace_voice_is_available(monkeypatch):
    monkeypatch.setattr(config, "_provider_instances_module", lambda: {
        "resolve_secret_value": lambda *args, **kwargs: "managed-secret",
    })
    quality, fast = MagicMock(status_code=200), MagicMock(status_code=200)
    quality.json.return_value = {"data": []}
    fast.json.return_value = {"data": [{"voice_id": "fast-voice"}]}
    client = MagicMock()
    client.get = AsyncMock(side_effect=[quality, fast])
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    request = config.ProviderTestRequest(name="custom_tts", config={
        "type": "sixtydb", "api_key": "managed-secret", "voice_id": "fast-voice",
    })
    with patch("httpx.AsyncClient", return_value=client):
        result = await config.test_provider_connection(request)
    assert result["success"] is True
    assert client.get.await_args_list[1].args == ("https://api.60db.ai/voices?model=fast",)
