from pathlib import Path
from secrets import token_hex
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import config


VALIDATE = config._validate_ai_agent_config


@pytest.fixture
def editor(tmp_path, monkeypatch):
    base = tmp_path / "ai-agent.yaml"
    local = tmp_path / "ai-agent.local.yaml"
    base.write_text("public_setting: base\n")
    monkeypatch.setattr(config.settings, "CONFIG_PATH", str(base))
    monkeypatch.setattr(config.settings, "LOCAL_CONFIG_PATH", str(local))
    monkeypatch.setattr(config.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(config, "_fetch_engine_config_state", AsyncMock(return_value=None))
    monkeypatch.setattr(config, "_validate_ai_agent_config", lambda *a, **k: {"warnings": []})
    app = FastAPI()
    app.include_router(config.router, prefix="/config")
    return TestClient(app), base, local


def test_repair_preserves_unambiguous_secret_lines_and_backs_up_original_bytes(editor):
    client, base, local = editor
    secret = token_hex()
    original = f"password: {secret}\r\nlog_level: [ # {secret}\r\n".encode()
    local.write_bytes(original)
    result = client.get("/config/yaml")
    assert result.status_code == 400 and secret not in result.text
    repair = result.json()["detail"]["repair"]
    assert repair["partial"] and "«secret:" in repair["content"]
    updated = repair["content"] + "log_level: debug\n"
    result = client.post("/config/yaml", json={"content": updated, "repair_revision": repair["revision"]})
    assert result.status_code == 200 and secret not in result.text
    assert yaml.safe_load(local.read_text()) == {"password": secret, "log_level": "debug"}
    assert next(local.parent.glob("*.bak.*")).read_bytes() == original
    assert base.read_text() == "public_setting: base\n"


def test_ambiguous_repair_requires_fresh_revision_and_complete_valid_mapping(editor):
    client, _, local = editor
    secret = token_hex()
    original = "tools:\n  service:\n    auth: [\n" + secret
    local.write_text(original)
    result = client.get("/config/yaml")
    assert secret not in result.text
    repair = result.json()["detail"]["repair"]
    assert not repair["partial"] and not repair["content"].strip()
    request = {"content": "repaired: true\n", "repair_revision": repair["revision"]}
    for invalid in [" ", "repaired: [", "password: «secret:invented»"]:
        assert client.post("/config/yaml", json={**request, "content": invalid}).status_code == 400
        assert local.read_text() == original
    local.write_text(original + "\n")
    assert client.post("/config/yaml", json=request).status_code == 409
    request["repair_revision"] = client.get("/config/yaml").json()["detail"]["repair"]["revision"]
    assert client.post("/config/yaml", json=request).status_code == 200
    assert yaml.safe_load(local.read_text()) == {"repaired": True}
    assert client.post("/config/yaml", json=request).status_code == 409


def test_shipped_base_is_repaired_only_by_its_owner(editor):
    client, base, local = editor
    secret = token_hex()
    base.write_text("password: [" + secret)
    local.write_text("{}")
    response = client.get("/config/yaml")
    assert response.status_code == 400 and secret not in response.text
    assert "ai-agent.yaml" in response.json()["detail"] and "repair" not in response.json()
    refused = client.post("/config/yaml", json={"content": "{}", "repair_revision": "wrong"})
    assert refused.status_code in (400, 409) and base.read_text() == "password: [" + secret

def test_repair_validates_merged_schema_before_backup_or_write(editor, monkeypatch):
    client, base, local = editor
    monkeypatch.setattr(config, "_validate_ai_agent_config", VALIDATE)
    shipped = Path(__file__).resolve().parents[3] / "config" / "ai-agent.yaml"
    base.write_bytes(shipped.read_bytes())
    original = "streaming: [bad"
    local.write_text(original)
    rev = client.get("/config/yaml").json()["detail"]["repair"]["revision"]
    reply = client.post("/config/yaml", json={"repair_revision": rev, "content": "audio_transport: unknown"})
    assert reply.status_code == 400 and local.read_text() == original and not list(local.parent.glob("*.bak.*"))
    result = client.post("/config/yaml", json={"repair_revision": rev, "content": "{}"})
    assert result.status_code == 200 and yaml.safe_load(local.read_text()) == {}
