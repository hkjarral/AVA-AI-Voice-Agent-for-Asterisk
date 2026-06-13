import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import agents as agents_api
from agents_store import AgentsStore

@pytest.fixture
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "agents.db")
    monkeypatch.setattr(agents_api, "_store", lambda: AgentsStore(db_path=db))
    # stats endpoint reads a call-history DB path that won't exist in tests -> returns zeros
    app = FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    return TestClient(app)

def test_crud_roundtrip(client):
    r = client.post("/api/agents", json={"display_name": "Maria - Vendas",
        "provider": "openai_realtime", "prompt": "p", "extension": "801"})
    assert r.status_code == 201 and r.json()["slug"] == "maria_vendas"
    assert any(a["slug"] == "maria_vendas" for a in client.get("/api/agents").json())
    r = client.patch("/api/agents/maria_vendas", json={"role_label": "Vendas"})
    assert r.json()["role_label"] == "Vendas"
    assert client.delete("/api/agents/maria_vendas").status_code == 204

def test_delete_default_with_others_promotes(client):
    client.post("/api/agents", json={"display_name": "A", "provider": "x", "prompt": "p"})
    client.post("/api/agents", json={"display_name": "B", "provider": "x", "prompt": "p"})
    client.delete("/api/agents/a")
    agents = {a["slug"]: a for a in client.get("/api/agents").json()}
    assert agents["b"]["is_default"] == 1

def test_dialplan_snippet(client):
    client.post("/api/agents", json={"display_name": "Sales", "provider": "x",
                                     "prompt": "p", "extension": "801"})
    text = client.get("/api/agents/sales/dialplan").json()["dialplan"]
    assert "Set(AI_AGENT=sales)" in text and "Stasis(" in text and "801" in text

def test_templates_listed(client):
    names = {t["id"] for t in client.get("/api/agents/templates").json()}
    assert {"receptionist", "after_hours", "appointment_booker"} <= names

def test_stats_zero_for_new_agent(client):
    client.post("/api/agents", json={"display_name": "S", "provider": "x", "prompt": "p"})
    s = client.get("/api/agents/s/stats").json()
    assert s == {"calls_30d": 0, "last_call": None}

def test_create_duplicate_slug_returns_422(client):
    client.post("/api/agents", json={"display_name": "Dup", "provider": "x", "prompt": "p"})
    r = client.post("/api/agents", json={"display_name": "Dup", "provider": "x", "prompt": "p2"})
    assert r.status_code == 422

def test_patch_missing_agent_404(client):
    assert client.patch("/api/agents/ghost", json={"role_label": "x"}).status_code == 404

def test_dialplan_missing_agent_404(client):
    assert client.get("/api/agents/ghost/dialplan").status_code == 404

def test_set_default_endpoint_switches_default(client):
    client.post("/api/agents", json={"display_name": "A", "provider": "x", "prompt": "p"})
    client.post("/api/agents", json={"display_name": "B", "provider": "x", "prompt": "p"})
    client.post("/api/agents/b/default")
    agents = {a["slug"]: a for a in client.get("/api/agents").json()}
    assert agents["b"]["is_default"] == 1 and agents["a"]["is_default"] == 0

def test_deactivate_via_patch_promotes_other(client):
    client.post("/api/agents", json={"display_name": "A", "provider": "x", "prompt": "p"})
    client.post("/api/agents", json={"display_name": "B", "provider": "x", "prompt": "p"})
    # a is default (created first); deactivate it -> b should become default
    client.patch("/api/agents/a", json={"is_active": False})
    agents = {a["slug"]: a for a in client.get("/api/agents").json()}
    assert agents["b"]["is_default"] == 1
    assert agents["a"]["is_active"] == 0

def test_reconcile_adds_new_yaml_context(client, tmp_path, monkeypatch):
    import yaml as _yaml
    from api import agents as agents_api
    (tmp_path / "contexts").mkdir()
    (tmp_path / "ai-agent.yaml").write_text(_yaml.dump({"contexts": {"newctx": {"provider": "a", "prompt": "hello"}}}))
    monkeypatch.setattr(agents_api, "_yaml_path", lambda: str(tmp_path / "ai-agent.yaml"))
    monkeypatch.setattr(agents_api, "_contexts_dir", lambda: str(tmp_path / "contexts"))
    r = client.post("/api/agents-migration/reconcile")
    assert r.status_code == 200
    assert any(c == ["added", "newctx"] or tuple(c) == ("added", "newctx") for c in r.json()["changed"])
    assert any(a["slug"] == "newctx" and a["is_operator_managed"] == 0 for a in client.get("/api/agents").json())
