"""H3 / MED-E1: server-side email-address validation.

Two save paths are guarded:
  * Per-agent  : POST/PATCH /api/agents  (email_recipient / email_from)
  * Tools config: POST /api/config/yaml   (tools.*.admin_email / from_email and
    their *_by_context maps)

Invalid non-empty addresses are rejected with HTTP 422. Empty/None/omitted means
"unset / inherit" and is always allowed. ${ENV:-...} placeholders are not literal
addresses and are not validated.

Reuses the TestClient/store-override fixture pattern from test_agents_email_api_v710.py.
"""
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import agents as agents_api
from api import config as config_api
from agents_store import AgentsStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "agents.db")
    monkeypatch.setattr(agents_api, "_store", lambda: AgentsStore(db_path=db))
    app = FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    return TestClient(app)


# --------------------------- per-agent (agents.py) ---------------------------

def test_create_rejects_invalid_email_recipient(client):
    r = client.post("/api/agents", json={
        "display_name": "Bad", "provider": "x", "prompt": "p",
        "email_recipient": "not-an-email"})
    assert r.status_code == 422, r.text


def test_create_rejects_invalid_email_from(client):
    r = client.post("/api/agents", json={
        "display_name": "Bad2", "provider": "x", "prompt": "p",
        "email_from": "nope@@bad"})
    assert r.status_code == 422, r.text


def test_create_accepts_valid_email(client):
    r = client.post("/api/agents", json={
        "display_name": "Good", "provider": "x", "prompt": "p",
        "email_recipient": "ops@example.com", "email_from": "ava@example.com"})
    assert r.status_code == 201, r.text


def test_create_accepts_omitted_email(client):
    # None / omitted means inherit -> allowed.
    r = client.post("/api/agents", json={
        "display_name": "NoMail", "provider": "x", "prompt": "p"})
    assert r.status_code == 201, r.text


def test_create_accepts_empty_email(client):
    # Empty string clears the field -> allowed.
    r = client.post("/api/agents", json={
        "display_name": "EmptyMail", "provider": "x", "prompt": "p",
        "email_recipient": "", "email_from": ""})
    assert r.status_code == 201, r.text


def test_patch_rejects_invalid_email(client):
    client.post("/api/agents", json={
        "display_name": "PatchMe", "provider": "x", "prompt": "p"})
    r = client.patch("/api/agents/patchme", json={"email_recipient": "broken"})
    assert r.status_code == 422, r.text


def test_patch_accepts_valid_email(client):
    client.post("/api/agents", json={
        "display_name": "PatchOk", "provider": "x", "prompt": "p"})
    r = client.patch("/api/agents/patchok", json={"email_recipient": "ok@example.com"})
    assert r.status_code == 200, r.text


# --------------------------- tools config (config.py) ---------------------------

def _tools_yaml(tools: dict) -> str:
    return yaml.safe_dump({"tools": tools}, default_flow_style=False, sort_keys=False)


def test_tools_config_rejects_invalid_admin_email(monkeypatch):
    # Guard runs before the heavy schema validation; stub the latter so the test is
    # hermetic (it must fail at the email check, returning 422).
    monkeypatch.setattr(config_api, "_validate_ai_agent_config",
                        lambda content: {"warnings": []})
    content = _tools_yaml({"send_email_summary": {"admin_email": "not-an-email"}})
    with pytest.raises(Exception) as exc:
        config_api._assert_tool_emails_valid(content)
    assert getattr(exc.value, "status_code", None) == 422


def test_tools_config_rejects_invalid_by_context_value():
    content = _tools_yaml({"send_email_summary": {
        "admin_email_by_context": {"sales": "ok@example.com", "support": "bad-addr"}}})
    with pytest.raises(Exception) as exc:
        config_api._assert_tool_emails_valid(content)
    assert getattr(exc.value, "status_code", None) == 422


def test_tools_config_accepts_valid_and_placeholder_and_empty():
    content = _tools_yaml({
        "request_transcript": {"from_email": "${SEND_EMAIL_FROM:-noreply@example.com}"},
        "send_email_summary": {
            "admin_email": "",
            "from_email": "ava@example.com",
            "admin_email_by_context": {"sales": "sales@example.com"},
        },
    })
    # Must not raise.
    config_api._assert_tool_emails_valid(content)
