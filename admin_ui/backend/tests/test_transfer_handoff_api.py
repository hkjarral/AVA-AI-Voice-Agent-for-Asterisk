"""Both config-write surfaces share handoff validation before persistence."""
import copy
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import config as config_api
from api import tools as tools_api


@pytest.fixture
def app_client(monkeypatch):
    original = yaml.safe_load(Path(config_api.settings.CONFIG_PATH).read_text())
    state = {"config": original, "writes": 0}
    monkeypatch.setattr(tools_api, "_load_cfg", lambda: copy.deepcopy(state["config"]))
    async def persist(payload):
        config_api._validate_ai_agent_config(yaml.safe_dump(payload))
        state["config"] = copy.deepcopy(payload)
        state["writes"] += 1
        return {"status": "success", "apply_required": True, "recommended_apply_method": "hot_reload"}
    monkeypatch.setattr(tools_api, "_persist_cfg", persist)
    monkeypatch.setattr(config_api, "_write_local_config", lambda text: pytest.fail("Invalid raw YAML must not be persisted"))
    app = FastAPI()
    app.include_router(config_api.router, prefix="/api/config")
    app.include_router(tools_api.router, prefix="/api/tools")
    return TestClient(app), state


@pytest.mark.parametrize("handoff", [{"enabled": True}, {"start_timeout_sec": -1}, {"finish_timeout_sec": 61}, {"enabled": "yes"}])
@pytest.mark.parametrize("surface", ["tools", "yaml"])
def test_invalid_handoff_is_rejected_without_write(app_client, handoff, surface):
    client, state = app_client
    if surface == "tools":
        result = client.patch("/api/tools/builtin/transfer", json={"handoff": handoff})
    else:
        payload = copy.deepcopy(state["config"])
        payload["tools"]["transfer"]["handoff"] = handoff
        result = client.post("/api/config/yaml", json={"content": yaml.safe_dump(payload)})
    assert result.status_code == 400, result.text
    assert state["writes"] == 0


def test_structured_config_round_trip_preserves_destination_override(app_client):
    client, state = app_client
    handoff = {"enabled": True, "message": "Please leave a message.", "start_timeout_sec": 4, "finish_timeout_sec": 20}
    override = {"mode": "custom", "message": "The desk is closed."}
    result = client.patch("/api/tools/builtin/transfer", json={"handoff": handoff,
        "destinations": {"test": {"type": "extension", "target": "6000", "handoff": override}}})
    assert result.status_code == 200, result.text
    result = client.get("/api/tools/builtin/transfer")
    assert result.json()["config"]["handoff"] == handoff
    assert result.json()["config"]["destinations"]["test"]["handoff"] == override
    result = client.patch("/api/tools/builtin/transfer", json={"handoff": {"enabled": False}})
    assert result.status_code == 200
    assert state["config"]["tools"]["transfer"]["handoff"]["message"] == handoff["message"]
