"""Tests for config export sanitization — .env excluded by default."""
import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import config as config_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "ai-agent.yaml").write_text("contexts: {}\n")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-secret\n")
    monkeypatch.setattr(config_mod.settings, "CONFIG_PATH", str(tmp_path / "ai-agent.yaml"))
    monkeypatch.setattr(config_mod.settings, "LOCAL_CONFIG_PATH", str(tmp_path / "ai-agent.local.yaml"))
    monkeypatch.setattr(config_mod.settings, "ENV_PATH", str(tmp_path / ".env"))
    app = FastAPI()
    app.include_router(config_mod.router, prefix="/api/config")
    return TestClient(app)


def _zip_names(resp):
    return set(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())


def test_export_excludes_env_by_default(client):
    resp = client.get("/api/config/export")
    assert resp.status_code == 200
    names = _zip_names(resp)
    assert ".env" not in names
    assert "EXPORT_README.txt" in names


def test_export_includes_env_only_with_flag(client):
    resp = client.get("/api/config/export?include_secrets=true")
    assert resp.status_code == 200
    names = _zip_names(resp)
    assert ".env" in names
    assert "EXPORT_README.txt" in names


def test_export_readme_mentions_secrets_warning_when_included(client):
    resp = client.get("/api/config/export?include_secrets=true")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    readme = zf.read("EXPORT_README.txt").decode()
    assert "API KEYS" in readme or "SECRETS" in readme or "credentials" in readme.lower()


def test_export_readme_mentions_excluded_by_default(client):
    resp = client.get("/api/config/export")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    readme = zf.read("EXPORT_README.txt").decode()
    assert "Excluded by default" in readme or "excluded" in readme.lower()


def test_export_backup_info_still_present(client):
    resp = client.get("/api/config/export")
    assert resp.status_code == 200
    assert "backup_info.txt" in _zip_names(resp)


def test_export_yaml_config_present(client):
    resp = client.get("/api/config/export")
    assert resp.status_code == 200
    assert "ai-agent.yaml" in _zip_names(resp)


def test_export_include_secrets_no_env_file(tmp_path, monkeypatch):
    """include_secrets=true when .env does not exist: .env must NOT appear in the zip
    and the README 'Included:' line must NOT mention .env."""
    (tmp_path / "ai-agent.yaml").write_text("contexts: {}\n")
    # Deliberately do NOT create a .env file
    monkeypatch.setattr(config_mod.settings, "CONFIG_PATH", str(tmp_path / "ai-agent.yaml"))
    monkeypatch.setattr(config_mod.settings, "LOCAL_CONFIG_PATH", str(tmp_path / "ai-agent.local.yaml"))
    monkeypatch.setattr(config_mod.settings, "ENV_PATH", str(tmp_path / ".env"))
    app = FastAPI()
    app.include_router(config_mod.router, prefix="/api/config")
    no_env_client = TestClient(app)

    resp = no_env_client.get("/api/config/export?include_secrets=true")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert ".env" not in names, ".env must not appear when it does not exist on disk"
    readme = zf.read("EXPORT_README.txt").decode()
    included_line = next((line_text for line_text in readme.splitlines() if line_text.startswith("Included:")), "")
    assert ".env" not in included_line, "README 'Included:' line must not mention .env when file is absent"
