"""WS-A (MED-A3): per-agent stats must count calls recorded under the raw
context_name (e.g. "Tool_Example") against the slugified agent ("tool_example")."""
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import agents as agents_api
from agents_store import AgentsStore


def test_stats_batch_counts_legacy_context_name(tmp_path, monkeypatch):
    adb = str(tmp_path / "agents.db")
    store = AgentsStore(db_path=adb)
    store.create(display_name="Tool_Example", provider="openai", prompt="p")  # slug tool_example
    monkeypatch.setattr(agents_api, "_store", lambda: AgentsStore(db_path=adb))

    cdb = str(tmp_path / "call_history.db")
    c = sqlite3.connect(cdb)
    c.execute("CREATE TABLE call_records (context_name TEXT, outcome TEXT, "
              "duration_seconds REAL, start_time TEXT)")
    # Relative to now so the rows stay inside any rolling window as real time passes.
    c.execute("INSERT INTO call_records VALUES ('Tool_Example','completed',30,"
              "datetime('now','-1 days'))")
    c.execute("INSERT INTO call_records VALUES ('Tool_Example','transferred',10,"
              "datetime('now','-1 days','+1 hours'))")
    c.commit()
    c.close()
    monkeypatch.setattr(agents_api, "CALL_HISTORY_DB", cdb)

    app = FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    rows = {r["slug"]: r for r in TestClient(app).get("/api/agents/stats-batch").json()}

    assert rows["tool_example"]["calls"] == 2        # both legacy-named calls counted
    assert rows["tool_example"]["transfers"] == 1


def test_single_agent_stats_counts_slugified_context_name(tmp_path, monkeypatch):
    # CodeRabbit Major: GET /agents/{slug}/stats must fold context_name through
    # slugify too (like stats-batch), so a legacy name that slugifies to the slug
    # but matches neither the slug nor the display_name is still counted.
    adb = str(tmp_path / "agents.db")
    store = AgentsStore(db_path=adb)
    store.create(display_name="Tool Example", provider="openai", prompt="p")  # slug tool_example
    monkeypatch.setattr(agents_api, "_store", lambda: AgentsStore(db_path=adb))

    cdb = str(tmp_path / "call_history.db")
    c = sqlite3.connect(cdb)
    c.execute("CREATE TABLE call_records (context_name TEXT, outcome TEXT, "
              "duration_seconds REAL, start_time TEXT)")
    # "TOOL-EXAMPLE" slugifies to tool_example but != slug and != display_name.
    c.execute("INSERT INTO call_records VALUES ('TOOL-EXAMPLE','completed',30,"
              "datetime('now','-1 days'))")
    c.execute("INSERT INTO call_records VALUES ('TOOL-EXAMPLE','completed',20,"
              "datetime('now','-2 days'))")
    c.commit()
    c.close()
    monkeypatch.setattr(agents_api, "CALL_HISTORY_DB", cdb)

    app = FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    body = TestClient(app).get("/api/agents/tool_example/stats").json()

    assert body["calls_30d"] == 2
    assert body["last_call"] is not None
