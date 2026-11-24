import secrets
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import docker
import httpx
import pytest
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import config, system, wizard


@pytest.fixture
def env_api(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(config.settings, "ENV_PATH", str(path))
    monkeypatch.setattr(wizard, "ENV_PATH", str(path))
    monkeypatch.setattr(config, "_read_merged_config_dict", lambda: {
        "websocket_media": {"auth": {"password_env": "CUSTOM_MEDIA"}},
        "providers": {"local": {"api_key_env": "CUSTOM_MODEL"}},
    })
    monkeypatch.setattr(config, "_running_container_names", lambda: {"ai_engine"})
    app = FastAPI()
    app.include_router(config.router, prefix="/config")
    app.include_router(wizard.router, prefix="/wizard")
    app.include_router(system.router, prefix="/system")
    return TestClient(app), path


def test_get_env_masks_credentials_and_preserves_literal_references(env_api, monkeypatch):
    client, path = env_api
    value = secrets.token_hex()
    monkeypatch.setenv("EXTERNAL_SECRET", value)
    path.write_text(f"OPENAI_API_KEY={value}\nCUSTOM_MEDIA={value}\nCUSTOM_MODEL={value}\nLOG_LEVEL=info\n"
                    "PUBLIC_REF=${EXTERNAL_SECRET}\nSMTP_PASSWORD=\n")
    response = client.get("/config/env")
    assert value not in response.text
    assert response.json() == {
        "OPENAI_API_KEY": {"set": True}, "CUSTOM_MEDIA": {"set": True},
        "CUSTOM_MODEL": {"set": True}, "LOG_LEVEL": "info",
        "PUBLIC_REF": "${EXTERNAL_SECRET}", "SMTP_PASSWORD": {"set": False},
    }


def test_env_status_only_returns_names_and_detects_custom_credentials(env_api, monkeypatch):
    client, path = env_api
    desired, actual = secrets.token_hex(), secrets.token_hex()
    path.write_text(f"CUSTOM_MEDIA={desired}\n")
    container = SimpleNamespace(attrs={"Config": {"Env": [f"CUSTOM_MEDIA={actual}"]}})
    monkeypatch.setattr(docker, "from_env", lambda: SimpleNamespace(containers={"ai_engine": container}))
    result = client.get("/config/env/status")
    assert result.json()["drift"]["ai_engine"] == ["CUSTOM_MEDIA"]
    assert desired not in result.text and actual not in result.text


@pytest.mark.parametrize("marker", [{"set": True}, {"set": False}])
def test_post_env_preserves_unchanged_secrets_and_marks_real_changes(env_api, marker):
    client, path = env_api
    key = secrets.token_hex()
    path.write_text(f'OPENAI_API_KEY="{key}" # keep\nCUSTOM_MEDIA={key}\nLOG_LEVEL=info\n')
    result = client.post("/config/env", json={"OPENAI_API_KEY": marker, "CUSTOM_MEDIA": marker, "LOG_LEVEL": "debug"})
    assert result.status_code == 200
    assert result.json()["changed_keys"] == ["LOG_LEVEL"]
    assert path.read_text() == f'OPENAI_API_KEY="{key}" # keep\nCUSTOM_MEDIA={key}\nLOG_LEVEL=debug\n'


def test_only_markers_dont_write_or_request_recreate(env_api):
    client, path = env_api
    result = client.post("/config/env", json={"OPENAI_API_KEY": {"set": False}})
    assert result.status_code == 200
    assert result.json()["changed_keys"] == [] and not result.json()["restart_required"]
    assert not path.exists() and not list(path.parent.glob(".env.bak*"))


@pytest.mark.parametrize("value", ["", None])
def test_secret_strings_replace_and_null_deletes_all_dotenv_occurrences(env_api, value):
    client, path = env_api
    old = secrets.token_hex()
    replacement = secrets.token_hex()
    path.write_text(f'export OPENAI_API_KEY="{old}\n{old}"\nOPENAI_API_KEY={old}\n')
    result = client.post("/config/env", json={"OPENAI_API_KEY": replacement})
    assert result.status_code == 200
    assert replacement not in result.text and old not in path.read_text()
    assert dotenv_values(path)["OPENAI_API_KEY"] == replacement
    assert client.post("/config/env", json={"OPENAI_API_KEY": value}).status_code == 200
    assert dotenv_values(path) == ({} if value is None else {"OPENAI_API_KEY": value})


@pytest.mark.parametrize("invalid", [[], {"set": "false"}, {"set": 1}, {"set": True, "extra": True}])
def test_invalid_values_dont_echo_or_write_any_inputs(env_api, invalid):
    client, path = env_api
    key = secrets.token_hex()
    result = client.post("/config/env", json={"OPENAI_API_KEY": key, "JWT_SECRET": invalid})
    assert result.status_code == 400 and key not in result.text
    assert not path.exists()


def test_wizard_uses_saved_provider_only_at_its_provider(env_api, monkeypatch):
    client, path = env_api
    value = secrets.token_hex()
    path.write_text(f"GOOGLE_API_KEY={value}\n")
    stub = AsyncMock()
    stub.get.return_value = httpx.Response(403, json={"error": {"message": value}})
    stub.__aenter__.return_value = stub
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: stub)
    result = client.post("/wizard/validate-key", json={"provider": "google"})
    assert result.status_code == 200 and not result.json()["valid"]
    assert value not in result.text
    kwargs = stub.get.await_args.kwargs
    assert kwargs["headers"]["x-goog-api-key"] == value and "params" not in kwargs
    assert not client.post("/wizard/validate-key", json={"provider": "openai"}).json()["valid"]
    assert stub.get.await_count == 1


@pytest.mark.parametrize("route", ["/wizard/validate-connection", "/system/test-ari"])
def test_saved_ari_password_is_bound_to_saved_destination(env_api, monkeypatch, route):
    client, path = env_api
    value = secrets.token_hex()
    path.write_text(f"ASTERISK_HOST=pbx.invalid\nASTERISK_ARI_USERNAME=operator\nASTERISK_ARI_PASSWORD={value}\n")
    stub = AsyncMock()
    stub.__aenter__.return_value = stub
    stub.get.return_value = httpx.Response(200, json={})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: stub)
    request = {"username": "operator", "host": "pbx.invalid"}
    result = client.post(route, json=request)
    assert result.status_code == 200 and value not in result.text
    assert stub.get.await_args.kwargs["auth"] == ("operator", value)
    rejected = client.post(route, json={**request, "host": "changed.invalid"})
    assert rejected.status_code == 400 and stub.get.await_count == 1
    assert value not in rejected.text


def test_saved_smtp_password_is_bound_to_saved_destination(env_api, monkeypatch):
    client, path = env_api
    value = secrets.token_hex()
    path.write_text(f"SMTP_HOST=mail.invalid\nSMTP_USERNAME=operator\nSMTP_PASSWORD={value}\n")
    smtp = Mock(side_effect=AssertionError("Unexpected SMTP connection"))
    monkeypatch.setattr(config.smtplib, "SMTP", smtp)
    result = client.post("/config/env/smtp/test", json={"to_email": "test@example.invalid", "smtp_host": "changed.invalid"})
    assert result.status_code == 400 and not smtp.called
    assert value not in result.text


def test_secret_delete_sentinel_is_rejected_atomically_and_paths_remain_editable(env_api):
    client, path = env_api
    key = secrets.token_hex()
    before = "OPENAI_API_KEY=" + key + "\n"
    paths = {name: "/settings/credential-file.json" for name in
             ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CALENDAR_CREDENTIALS", "CALENDLY_TOKEN_PATH")}
    before += "".join(f"{name}={value}\n" for name, value in paths.items())
    path.write_text(before)
    assert client.get("/config/env").json() == {"OPENAI_API_KEY": {"set": True}, **paths}
    reply = client.post("/config/env", json={"PUBLIC": "changed", "OPENAI_API_KEY": "__DELETE__"})
    assert reply.status_code == 400 and key not in reply.text and path.read_text() == before
