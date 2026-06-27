import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import config  # noqa: E402


def test_get_config_returns_merged_structured_config(monkeypatch):
    monkeypatch.setattr(
        config,
        "_read_merged_config_dict",
        lambda: {"providers": {"local": {"type": "local"}}},
    )

    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")

    response = TestClient(app).get("/api/config")

    assert response.status_code == 200
    assert response.json() == {"providers": {"local": {"type": "local"}}}


def test_health_api_token_impacts_local_ai_when_used_as_live_status_fallback():
    assert config._local_ai_env_key("HEALTH_API_TOKEN") is True
