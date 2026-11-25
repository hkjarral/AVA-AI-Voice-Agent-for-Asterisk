"""Tests for config export sanitization — .env excluded by default."""
import io
import zipfile
import secrets
import logging
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
from api import config as config_mod


@pytest.fixture
def password():
    return secrets.token_urlsafe()


@pytest.fixture
def client(tmp_path, monkeypatch, password):
    monkeypatch.setattr(auth, "_export_throttle", auth.LoginThrottle())
    monkeypatch.setattr(auth, "_address_throttle", auth.LoginThrottle(max_failures=30))
    user = auth.UserInDB(username="operator", hashed_password=auth.get_password_hash(password))
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "agents.db"))
    (tmp_path / "ai-agent.yaml").write_text("contexts: {}\n")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-secret\n")
    monkeypatch.setattr(config_mod.settings, "CONFIG_PATH", str(tmp_path / "ai-agent.yaml"))
    monkeypatch.setattr(config_mod.settings, "LOCAL_CONFIG_PATH", str(tmp_path / "ai-agent.local.yaml"))
    monkeypatch.setattr(config_mod.settings, "ENV_PATH", str(tmp_path / ".env"))
    app = FastAPI()
    app.include_router(config_mod.router, prefix="/api/config")
    monkeypatch.setattr(auth, "USERS_PATH", str(tmp_path / "users.json"))
    auth.save_users({user.username: user.model_dump()})
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer " + auth.create_access_token({"sub": user.username})
    return client


def _zip_names(resp):
    return set(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())


def test_export_excludes_env_by_default(client, password):
    resp = client.post("/api/config/export", json={"password": password})
    assert resp.status_code == 200
    names = _zip_names(resp)
    assert ".env" not in names
    assert "EXPORT_README.txt" in names


def test_export_includes_env_only_with_flag(client, password):
    resp = client.post("/api/config/export?include_secrets=true", json={"password": password})
    assert resp.status_code == 200
    names = _zip_names(resp)
    assert ".env" in names
    assert "EXPORT_README.txt" in names


def test_export_readme_mentions_secrets_warning_when_included(client, password):
    resp = client.post("/api/config/export?include_secrets=true", json={"password": password})
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    readme = zf.read("EXPORT_README.txt").decode()
    assert "API KEYS" in readme or "SECRETS" in readme or "credentials" in readme.lower()


def test_export_readme_mentions_excluded_by_default(client, password):
    resp = client.post("/api/config/export", json={"password": password})
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    readme = zf.read("EXPORT_README.txt").decode()
    assert "Excluded by default" in readme or "excluded" in readme.lower()


def test_export_backup_info_still_present(client, password):
    resp = client.post("/api/config/export", json={"password": password})
    assert resp.status_code == 200
    assert "backup_info.txt" in _zip_names(resp)


def test_export_yaml_config_present(client, password):
    resp = client.post("/api/config/export", json={"password": password})
    assert resp.status_code == 200
    assert "ai-agent.yaml" in _zip_names(resp)


def test_export_include_secrets_no_env_file(client, password, tmp_path):
    """include_secrets=true when .env does not exist: .env must NOT appear in the zip
    and the README 'Included:' line must NOT mention .env."""
    (tmp_path / ".env").unlink()
    resp = client.post("/api/config/export?include_secrets=true", json={"password": password})
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert ".env" not in names, ".env must not appear when it does not exist on disk"
    readme = zf.read("EXPORT_README.txt").decode()
    included_line = next((line_text for line_text in readme.splitlines() if line_text.startswith("Included:")), "")
    assert ".env" not in included_line, "README 'Included:' line must not mention .env when file is absent"

def test_export_rejects_get_even_with_credentials(client, password):
    response = client.get("/api/config/export?include_secrets=true")
    assert response.status_code == 405
    assert "POST" in response.headers["allow"]


def test_wrong_password_returns_no_archive_or_audit_entry(client, caplog):
    with caplog.at_level(logging.INFO, logger=config_mod.__name__):
        response = client.post("/api/config/export", json={"password": secrets.token_urlsafe()})
    assert response.status_code == 401
    assert response.headers["X-AVA-Reauthentication"] == "required"
    assert response.headers["content-type"].startswith("application/json")
    assert "config_export actor=" not in caplog.text


def test_success_logs_only_the_actor(client, password, caplog):
    with caplog.at_level(logging.INFO, logger=config_mod.__name__):
        response = client.post("/api/config/export", json={"password": password})
    assert response.status_code == 200
    events = [r.getMessage() for r in caplog.records if r.name == config_mod.__name__]
    assert events.count("config_export actor=operator") == 1
    assert password not in caplog.text
    assert response.headers["cache-control"] == "no-store"

def test_export_audit_encodes_actor_as_one_field(client, password, caplog):
    users = auth.load_users()
    name = "ops team\nbackup"
    users[name] = {**users["operator"], "username": name}
    auth.save_users(users)
    client.headers["Authorization"] = "Bearer " + auth.create_access_token({"sub": name})
    with caplog.at_level(logging.INFO, logger=config_mod.__name__):
        response = client.post("/api/config/export", json={"password": password})
    assert response.status_code == 200
    assert r"config_export actor=ops team\nbackup" in [record.getMessage() for record in caplog.records]

def test_repeated_failed_reauthentication_respects_session_limit(client, password):
    for _ in range(5):
        assert client.post("/api/config/export", json={"password": secrets.token_urlsafe()}).status_code == 401
    blocked = client.post("/api/config/export", json={"password": password})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0

def test_export_sessions_have_independent_password_budgets(client, password):
    second = auth.create_access_token({"sub": "operator"})
    for _ in range(5):
        assert client.post("/api/config/export", json={"password": secrets.token_urlsafe()}).status_code == 401
    assert client.post("/api/config/export", json={"password": password}).status_code == 429
    response = client.post("/api/config/export", json={"password": password}, headers={"Authorization": "Bearer " + second})
    assert response.status_code == 200


def test_export_accepts_existing_sessions_without_jti(client, password):
    legacy = auth.jwt.encode({"sub": "operator", "exp": int(time.time()) + 600}, auth.SECRET_KEY, algorithm=auth.ALGORITHM)
    client.headers["Authorization"] = "Bearer " + legacy
    response = client.post("/api/config/export", json={"password": password})
    assert response.status_code == 200
