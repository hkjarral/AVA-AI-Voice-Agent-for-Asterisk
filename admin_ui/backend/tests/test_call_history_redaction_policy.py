import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from api import calls, config  # noqa: E402
from src.tools.execution_history import CALL_HISTORY_TOOL_REDACTION_MODES  # noqa: E402


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(calls.router, prefix="/api")
    return TestClient(app)


def test_config_validation_uses_canonical_redaction_modes():
    assert config.CALL_HISTORY_TOOL_REDACTION_MODES is CALL_HISTORY_TOOL_REDACTION_MODES


def test_policy_endpoint_returns_only_normalized_policy_and_restart_state(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "CALL_HISTORY_TOOL_REDACTION_MODE=show_routing\nOPENAI_API_KEY=must-not-leak\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(calls.settings, "ENV_PATH", str(env_path))

    async def fake_status():
        return {"drift": {"ai_engine": ["CALL_HISTORY_TOOL_REDACTION_MODE"]}}

    monkeypatch.setattr(config, "get_env_status", fake_status)

    response = _client().get("/api/calls/redaction-policy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured_mode"] == "show_routing"
    assert payload["configured_value_valid"] is True
    assert payload["pending_restart"] is True
    assert set(payload) == {
        "configured_mode",
        "configured_value_valid",
        "pending_restart",
        "modes",
    }
    assert "must-not-leak" not in response.text


def test_invalid_policy_fails_closed_and_is_reported(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("CALL_HISTORY_TOOL_REDACTION_MODE=everything\n", encoding="utf-8")
    monkeypatch.setattr(calls.settings, "ENV_PATH", str(env_path))

    async def fake_status():
        return {"drift": {"ai_engine": []}}

    monkeypatch.setattr(config, "get_env_status", fake_status)

    response = _client().get("/api/calls/redaction-policy")

    assert response.status_code == 200
    assert response.json()["configured_mode"] == "strict"
    assert response.json()["configured_value_valid"] is False
    assert response.json()["pending_restart"] is False


def test_env_update_rejects_unknown_policy_before_writing(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("CALL_HISTORY_TOOL_REDACTION_MODE=strict\n", encoding="utf-8")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))

    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")
    response = TestClient(app).post(
        "/api/config/env",
        json={"CALL_HISTORY_TOOL_REDACTION_MODE": "not-a-policy"},
    )

    assert response.status_code == 400
    assert "must be one of" in response.json()["detail"]
    assert env_path.read_text(encoding="utf-8") == "CALL_HISTORY_TOOL_REDACTION_MODE=strict\n"


def test_env_update_accepts_blank_policy_as_default_reset(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("CALL_HISTORY_TOOL_REDACTION_MODE=show_routing\n", encoding="utf-8")
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))
    monkeypatch.setattr(config, "_running_container_names", lambda: set())

    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")
    response = TestClient(app).post(
        "/api/config/env",
        json={"CALL_HISTORY_TOOL_REDACTION_MODE": "   "},
    )

    assert response.status_code == 200
    assert response.json()["changed_keys"] == ["CALL_HISTORY_TOOL_REDACTION_MODE"]
    assert env_path.read_text(encoding="utf-8") == 'CALL_HISTORY_TOOL_REDACTION_MODE=""\n'
