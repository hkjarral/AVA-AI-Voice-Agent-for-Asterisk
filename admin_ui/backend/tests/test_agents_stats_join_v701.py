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
    c.execute("INSERT INTO call_records VALUES ('Tool_Example','completed',30,'2026-06-19T00:00:00')")
    c.execute("INSERT INTO call_records VALUES ('Tool_Example','transferred',10,'2026-06-19T01:00:00')")
    c.commit()
    c.close()
    monkeypatch.setattr(agents_api, "CALL_HISTORY_DB", cdb)

    app = FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    rows = {r["slug"]: r for r in TestClient(app).get("/api/agents/stats-batch").json()}

    assert rows["tool_example"]["calls"] == 2        # both legacy-named calls counted
    assert rows["tool_example"]["transfers"] == 1
