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

import sqlite3 as _sqlite3

def _seed_history(path, rows):
    c = _sqlite3.connect(path)
    c.execute(
        "CREATE TABLE call_records (id TEXT, call_id TEXT, context_name TEXT, outcome TEXT, "
        "duration_seconds REAL, start_time TEXT, routing_method TEXT)"
    )
    for i, (ctx, outcome, dur, rm) in enumerate(rows):
        c.execute(
            "INSERT INTO call_records VALUES (?,?,?,?,?,?,?)",
            (str(i), str(i), ctx, outcome, dur, "2026-06-13T00:0%d:00" % (i % 6), rm),
        )
    c.commit()
    c.close()


def test_summary_counts(tmp_path, monkeypatch):
    from api import agents as agents_api
    from agents_store import AgentsStore
    db = str(tmp_path / "agents.db")
    monkeypatch.setattr(agents_api, "_store", lambda: AgentsStore(db_path=db))
    store = AgentsStore(db_path=db)
    store.create(display_name="Alpha", provider="x", prompt="p")
    store.create(display_name="Beta", provider="x", prompt="p")

    hist = str(tmp_path / "calls.db")
    _seed_history(hist, [
        ("alpha", None, 60.0, "ai_agent"),
        ("alpha", "transferred", 30.0, "ai_agent"),
        ("beta", "transferred", 45.0, "ai_context"),
        ("beta", None, 90.0, "default"),
        ("beta", None, 120.0, "ai_agent"),
    ])
    monkeypatch.setattr(agents_api, "CALL_HISTORY_DB", hist)

    app = __import__("fastapi").FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/api/agents/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["total_routed"] == 5
    assert data["total_transfers"] == 2
    assert data["active_agents"] == 2
    assert data["active_calls"] == 0  # no engine running in tests


def test_stats_batch(tmp_path, monkeypatch):
    from api import agents as agents_api
    from agents_store import AgentsStore
    db = str(tmp_path / "agents.db")
    monkeypatch.setattr(agents_api, "_store", lambda: AgentsStore(db_path=db))
    store = AgentsStore(db_path=db)
    store.create(display_name="Agent One", provider="x", prompt="p")
    store.create(display_name="Agent Two", provider="x", prompt="p")

    hist = str(tmp_path / "calls.db")
    _seed_history(hist, [
        ("agent_one", None, 60.0, "ai_agent"),
        ("agent_one", "transferred", 40.0, "ai_agent"),
        ("agent_one", None, 80.0, "ai_agent"),
    ])
    monkeypatch.setattr(agents_api, "CALL_HISTORY_DB", hist)

    app = __import__("fastapi").FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/api/agents/stats-batch")
    assert r.status_code == 200
    batch = {row["slug"]: row for row in r.json()}
    assert batch["agent_one"]["calls"] == 3
    assert batch["agent_one"]["transfers"] == 1
    assert batch["agent_one"]["avg_duration_seconds"] == round((60.0 + 40.0 + 80.0) / 3, 1)
    assert batch["agent_two"]["calls"] == 0
    assert batch["agent_two"]["transfers"] == 0
    assert batch["agent_two"]["avg_duration_seconds"] == 0.0
    assert batch["agent_two"]["last_call"] is None


def test_distribution(tmp_path, monkeypatch):
    from api import agents as agents_api
    from agents_store import AgentsStore
    db = str(tmp_path / "agents.db")
    monkeypatch.setattr(agents_api, "_store", lambda: AgentsStore(db_path=db))

    hist = str(tmp_path / "calls.db")
    _seed_history(hist, [
        ("alpha", None, 10.0, "ai_agent"),
        ("alpha", None, 10.0, "ai_agent"),
        ("alpha", None, 10.0, "ai_agent"),
        ("beta", None, 10.0, "ai_agent"),
        ("beta", None, 10.0, "ai_agent"),
        ("gamma", None, 10.0, "ai_agent"),
        (None, None, 10.0, "ai_agent"),   # NULL context — should be excluded
        ("", None, 10.0, "ai_agent"),     # empty context — should be excluded
    ])
    monkeypatch.setattr(agents_api, "CALL_HISTORY_DB", hist)

    app = __import__("fastapi").FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/api/agents/distribution")
    assert r.status_code == 200
    items = r.json()
    assert items[0] == {"context_name": "alpha", "count": 3}
    assert items[1] == {"context_name": "beta", "count": 2}
    assert items[2] == {"context_name": "gamma", "count": 1}
    assert len(items) == 3  # NULL/empty excluded


def test_routing_methods(tmp_path, monkeypatch):
    from api import agents as agents_api
    from agents_store import AgentsStore
    db = str(tmp_path / "agents.db")
    monkeypatch.setattr(agents_api, "_store", lambda: AgentsStore(db_path=db))

    hist = str(tmp_path / "calls.db")
    _seed_history(hist, [
        ("ctx", None, 10.0, "ai_agent"),
        ("ctx", None, 10.0, "ai_agent"),
        ("ctx", None, 10.0, "ai_context"),
        ("ctx", None, 10.0, "default"),
        ("ctx", None, 10.0, None),        # NULL → unknown
    ])
    monkeypatch.setattr(agents_api, "CALL_HISTORY_DB", hist)

    app = __import__("fastapi").FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/api/agents/routing-methods")
    assert r.status_code == 200
    data = r.json()
    assert data["ai_agent"] == 2
    assert data["ai_context"] == 1
    assert data["default"] == 1
    assert data["unknown"] == 1


def test_routing_methods_missing_column(tmp_path, monkeypatch):
    from api import agents as agents_api
    from agents_store import AgentsStore
    db = str(tmp_path / "agents.db")
    monkeypatch.setattr(agents_api, "_store", lambda: AgentsStore(db_path=db))

    # Create call_records table WITHOUT routing_method column
    hist = str(tmp_path / "calls.db")
    conn = _sqlite3.connect(hist)
    conn.execute(
        "CREATE TABLE call_records (id TEXT, context_name TEXT, outcome TEXT, duration_seconds REAL)"
    )
    conn.execute("INSERT INTO call_records VALUES ('1','ctx',NULL,30.0)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(agents_api, "CALL_HISTORY_DB", hist)

    app = __import__("fastapi").FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/api/agents/routing-methods")
    assert r.status_code == 200
    data = r.json()
    # Column missing but the table has 1 legacy row: count it as 'unknown' (not hidden),
    # so the panel agrees with the other dashboards on an upgraded-but-unmigrated install.
    assert data == {"ai_agent": 0, "ai_context": 0, "default": 0, "unknown": 1}


def test_aggregate_endpoints_resilient_to_missing_table(tmp_path, monkeypatch):
    """summary, stats-batch, distribution return 200 with zero/empty when DB has no call_records."""
    from api import agents as agents_api
    from agents_store import AgentsStore

    db = str(tmp_path / "agents.db")
    monkeypatch.setattr(agents_api, "_store", lambda: AgentsStore(db_path=db))
    store = AgentsStore(db_path=db)
    store.create(display_name="Alpha", provider="x", prompt="p")

    # DB file exists but has NO call_records table
    hist = str(tmp_path / "empty.db")
    conn = _sqlite3.connect(hist)
    conn.execute("CREATE TABLE other_table (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(agents_api, "CALL_HISTORY_DB", hist)

    app = __import__("fastapi").FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    from fastapi.testclient import TestClient
    c = TestClient(app)

    r = c.get("/api/agents/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["total_routed"] == 0
    assert data["total_transfers"] == 0

    r = c.get("/api/agents/stats-batch")
    assert r.status_code == 200
    batch = r.json()
    assert isinstance(batch, list)
    assert all(row["calls"] == 0 for row in batch)

    r = c.get("/api/agents/distribution")
    assert r.status_code == 200
    assert r.json() == []


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
