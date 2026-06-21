"""H2: per-agent email fields round-trip through the admin API and YAML export.

Reuses the TestClient/store-override fixture pattern from test_agents_api.py.
email_enabled is a tri-state: None=inherit, True/False explicit.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import agents as agents_api
from agents_store import AgentsStore
from export_agents_yaml import export_yaml


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "agents.db")
    monkeypatch.setattr(agents_api, "_store", lambda: AgentsStore(db_path=db))
    app = FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    return TestClient(app)


def test_create_with_email_fields_roundtrips(client):
    r = client.post("/api/agents", json={
        "display_name": "Mailer", "provider": "x", "prompt": "p",
        "email_recipient": "ops@example.com",
        "email_from": "ava@example.com",
        "email_enabled": True,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email_recipient"] == "ops@example.com"
    assert body["email_from"] == "ava@example.com"
    assert body["email_enabled"] is True

    got = client.get("/api/agents/mailer").json()
    assert got["email_recipient"] == "ops@example.com"
    assert got["email_from"] == "ava@example.com"
    assert got["email_enabled"] is True


def test_email_enabled_tristate_none_inherits(client):
    # Not sending email_enabled leaves it NULL -> inherit (None), not coerced to False.
    client.post("/api/agents", json={
        "display_name": "Inherit", "provider": "x", "prompt": "p"})
    got = client.get("/api/agents/inherit").json()
    assert got["email_enabled"] is None
    assert got["email_recipient"] is None
    assert got["email_from"] is None


def test_patch_updates_email_fields(client):
    client.post("/api/agents", json={
        "display_name": "Edit", "provider": "x", "prompt": "p",
        "email_recipient": "old@example.com", "email_enabled": True})
    r = client.patch("/api/agents/edit", json={
        "email_recipient": "new@example.com", "email_enabled": False})
    assert r.status_code == 200, r.text
    got = client.get("/api/agents/edit").json()
    assert got["email_recipient"] == "new@example.com"
    assert got["email_enabled"] is False


def test_export_yaml_emits_email_fields(tmp_path):
    db = str(tmp_path / "agents.db")
    store = AgentsStore(db_path=db)
    store.create(display_name="Exp", provider="x", prompt="p",
                 email_recipient="ops@example.com", email_from="ava@example.com",
                 email_enabled=1)
    store.create(display_name="Plain", provider="x", prompt="p")
    out = export_yaml(store)
    assert "ops@example.com" in out
    assert "ava@example.com" in out
    # the plain agent has no email keys emitted
    import yaml as _yaml
    parsed = _yaml.safe_load(out)["contexts"]
    assert parsed["exp"]["email_recipient"] == "ops@example.com"
    assert parsed["exp"]["email_from"] == "ava@example.com"
    assert parsed["exp"]["email_enabled"] == 1
    assert "email_recipient" not in parsed["plain"]
    assert "email_enabled" not in parsed["plain"]
