"""Regression coverage for effective provider credential status (#660)."""

import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("fastapi")

from api import config as config_api  # noqa: E402


def _google_provider(**overrides):
    """Build a minimal Google Live provider configuration for status tests."""
    provider = {
        "type": "google_live",
        "enabled": True,
        "api_key": "${GOOGLE_API_KEY}",
    }
    provider.update(overrides)
    return {"providers": {"google_live": provider}}


def _service_account_payload(project_id="test-project"):
    """Build service-account JSON accepted by Google's credential loader."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return {
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": "test-private-key-id",
        "private_key": private_key_pem,
        "client_email": f"{project_id}@example.test",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


@pytest.mark.asyncio
async def test_unresolved_google_api_key_placeholder_is_not_configured(monkeypatch, tmp_path):
    """An unresolved YAML env placeholder must not appear ready."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(config_api, "PROVIDER_SECRETS_ROOT", str(tmp_path / "providers"))
    monkeypatch.setattr(config_api, "VERTEX_CREDENTIALS_PATH", str(tmp_path / "missing-legacy.json"))
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: _google_provider())

    response = await config_api.get_provider_credentials_status("google_live")
    status = response["credentials"]["api-key"]

    assert status["configured"] is False
    assert status["uploaded"] is False
    assert status["source"] == "env_var"
    assert status["env_var"] == "GOOGLE_API_KEY"


@pytest.mark.asyncio
async def test_google_api_key_placeholder_reports_only_resolved_environment(monkeypatch, tmp_path):
    """A resolved env placeholder reports its name but never its value."""
    monkeypatch.setenv("GOOGLE_API_KEY", "runtime-google-key")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(config_api, "PROVIDER_SECRETS_ROOT", str(tmp_path / "providers"))
    monkeypatch.setattr(config_api, "VERTEX_CREDENTIALS_PATH", str(tmp_path / "missing-legacy.json"))
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: _google_provider())

    response = await config_api.get_provider_credentials_status("google_live")
    status = response["credentials"]["api-key"]

    assert status["configured"] is True
    assert status["source"] == "env_var"
    assert status["env_var"] == "GOOGLE_API_KEY"
    assert "runtime-google-key" not in json.dumps(response)


@pytest.mark.asyncio
async def test_external_api_key_file_is_not_mislabeled_as_managed(monkeypatch, tmp_path):
    """An explicit external file wins without inheriting managed-file metadata."""
    provider_root = tmp_path / "providers"
    managed_path = provider_root / "google_live" / "api-key"
    managed_path.parent.mkdir(parents=True)
    managed_path.write_text("managed-key", encoding="utf-8")
    external_path = tmp_path / "external-google-key"
    external_path.write_text("external-key", encoding="utf-8")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(config_api, "PROVIDER_SECRETS_ROOT", str(provider_root))
    monkeypatch.setattr(config_api, "VERTEX_CREDENTIALS_PATH", str(tmp_path / "missing-legacy.json"))
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: _google_provider(api_key="", api_key_file=str(external_path)),
    )

    response = await config_api.get_provider_credentials_status("google_live")
    status = response["credentials"]["api-key"]

    assert status["configured"] is True
    assert status["uploaded"] is False
    assert status["source"] == "configured_file"
    assert status["path"] == str(external_path)
    assert "managed-key" not in json.dumps(response)
    assert "external-key" not in json.dumps(response)


@pytest.mark.asyncio
async def test_legacy_vertex_file_is_reported_without_copying(monkeypatch, tmp_path):
    """A valid shared legacy file remains usable without migration or copying."""
    provider_root = tmp_path / "providers"
    legacy_path = tmp_path / "gcp-service-account.json"
    service_account = _service_account_payload("legacy-project")
    legacy_path.write_text(json.dumps(service_account), encoding="utf-8")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(config_api, "PROVIDER_SECRETS_ROOT", str(provider_root))
    monkeypatch.setattr(config_api, "VERTEX_CREDENTIALS_PATH", str(legacy_path))
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: _google_provider(use_vertex_ai=True))

    response = await config_api.get_provider_credentials_status("google_live")
    status = response["credentials"]["vertex-json"]

    assert status["configured"] is True
    assert status["uploaded"] is False
    assert status["source"] == "legacy_shared_file"
    assert status["path"] == str(legacy_path)
    assert status["project_id"] == "legacy-project"
    assert not (provider_root / "google_live" / "vertex-service-account.json").exists()
    assert service_account["private_key"] not in json.dumps(response)


@pytest.mark.asyncio
async def test_unreferenced_per_instance_vertex_file_is_not_configured(monkeypatch, tmp_path):
    """An uploaded but unreferenced Vertex file is reported as orphaned."""
    provider_root = tmp_path / "providers"
    managed_path = provider_root / "google_live" / "vertex-service-account.json"
    managed_path.parent.mkdir(parents=True)
    managed_path.write_text(
        json.dumps(_service_account_payload("orphaned-project")), encoding="utf-8"
    )
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(config_api, "PROVIDER_SECRETS_ROOT", str(provider_root))
    monkeypatch.setattr(config_api, "VERTEX_CREDENTIALS_PATH", str(tmp_path / "missing-legacy.json"))
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: _google_provider(use_vertex_ai=True))

    response = await config_api.get_provider_credentials_status("google_live")
    status = response["credentials"]["vertex-json"]

    assert status["uploaded"] is True
    assert status["configured"] is False
    assert status["source"] == "orphaned_managed_file"
    assert status["path"] == str(managed_path)


@pytest.mark.asyncio
async def test_referenced_per_instance_vertex_file_is_configured(monkeypatch, tmp_path):
    """A valid managed Vertex file referenced by YAML is ready."""
    provider_root = tmp_path / "providers"
    managed_path = provider_root / "google_live" / "vertex-service-account.json"
    managed_path.parent.mkdir(parents=True)
    managed_path.write_text(
        json.dumps(_service_account_payload("managed-project")), encoding="utf-8"
    )
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(config_api, "PROVIDER_SECRETS_ROOT", str(provider_root))
    monkeypatch.setattr(config_api, "VERTEX_CREDENTIALS_PATH", str(tmp_path / "missing-legacy.json"))
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: _google_provider(use_vertex_ai=True, credentials_path=str(managed_path)),
    )

    response = await config_api.get_provider_credentials_status("google_live")
    status = response["credentials"]["vertex-json"]

    assert status["uploaded"] is True
    assert status["configured"] is True
    assert status["source"] == "managed_file"


@pytest.mark.asyncio
async def test_invalid_referenced_vertex_file_is_not_configured(monkeypatch, tmp_path):
    """Malformed managed credentials remain uploaded but are not ready."""
    provider_root = tmp_path / "providers"
    managed_path = provider_root / "google_live" / "vertex-service-account.json"
    managed_path.parent.mkdir(parents=True)
    managed_path.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(config_api, "PROVIDER_SECRETS_ROOT", str(provider_root))
    monkeypatch.setattr(config_api, "VERTEX_CREDENTIALS_PATH", str(tmp_path / "missing-legacy.json"))
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: _google_provider(use_vertex_ai=True, credentials_path=str(managed_path)),
    )

    response = await config_api.get_provider_credentials_status("google_live")
    status = response["credentials"]["vertex-json"]

    assert status["uploaded"] is True
    assert status["configured"] is False
    assert status["source"] == "managed_file"
    assert status["error"] == "Invalid Google service-account credential file"


@pytest.mark.asyncio
async def test_explicit_missing_vertex_path_does_not_fall_back_to_legacy(monkeypatch, tmp_path):
    """An explicit broken override fails closed instead of using shared legacy state."""
    legacy_path = tmp_path / "gcp-service-account.json"
    legacy_path.write_text(
        json.dumps(_service_account_payload("legacy-project")), encoding="utf-8"
    )
    missing_path = tmp_path / "missing-instance.json"
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(config_api, "PROVIDER_SECRETS_ROOT", str(tmp_path / "providers"))
    monkeypatch.setattr(config_api, "VERTEX_CREDENTIALS_PATH", str(legacy_path))
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: _google_provider(use_vertex_ai=True, credentials_path=str(missing_path)),
    )

    response = await config_api.get_provider_credentials_status("google_live")
    status = response["credentials"]["vertex-json"]

    assert status["configured"] is False
    assert status["source"] == "configured_file"
    assert status["path"] == str(missing_path)
