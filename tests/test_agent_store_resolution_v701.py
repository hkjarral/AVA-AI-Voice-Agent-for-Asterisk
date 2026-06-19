"""WS-A reproduction tests (v7.0.1): engine-side agent resolution.

Covers CRIT-1 (legacy raw context name must resolve) and HIGH-9 (corrupt DB must
be distinguishable from not-found so the caller can fall back to YAML, while a
genuinely deleted/inactive agent stays unroutable).
"""
import sqlite3

import pytest

from src.core.agent_store import EngineAgentStore, AgentStoreReadError

_CREATE = """CREATE TABLE agents (
    id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
    extension TEXT, role_label TEXT, provider TEXT, voice TEXT, greeting TEXT,
    prompt TEXT, tools_json TEXT, mcp_json TEXT, audio_profile TEXT, extra_json TEXT,
    is_operator_managed INTEGER DEFAULT 1, is_active INTEGER DEFAULT 1,
    is_default INTEGER DEFAULT 0, source_file TEXT, created_at TEXT, updated_at TEXT,
    notes TEXT)"""


def _seed(db_path, rows):
    """rows: list of (slug, display_name, prompt, is_active, is_default)."""
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE)
    for i, (slug, name, prompt, active, default) in enumerate(rows):
        conn.execute(
            "INSERT INTO agents (id,slug,display_name,provider,prompt,is_active,is_default) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(i), slug, name, "openai", prompt, active, default),
        )
    conn.commit()
    conn.close()


def test_legacy_raw_context_name_resolves(tmp_path):
    # CRIT-1: dialplan still sends the original key (e.g. AI_CONTEXT=Tool_Example).
    db = str(tmp_path / "agents.db")
    _seed(db, [("tool_example", "Tool_Example", "hi", 1, 1)])
    cfg = EngineAgentStore(db_path=db).resolve("Tool_Example")
    assert cfg is not None and cfg.prompt == "hi"


def test_slug_name_still_resolves(tmp_path):
    db = str(tmp_path / "agents.db")
    _seed(db, [("tool_example", "Tool_Example", "hi", 1, 1)])
    assert EngineAgentStore(db_path=db).resolve("tool_example") is not None


def test_collision_both_names_resolve_to_their_own_agent(tmp_path):
    # Post-migration disambiguation: "Sales-East"->slug sales_east, "sales_east"->slug sales_east_2.
    # display_name-exact lookup must win so each original name reaches its own agent.
    db = str(tmp_path / "agents.db")
    _seed(db, [("sales_east", "Sales-East", "east", 1, 1),
               ("sales_east_2", "sales_east", "plain", 1, 0)])
    store = EngineAgentStore(db_path=db)
    assert store.resolve("Sales-East").prompt == "east"
    assert store.resolve("sales_east").prompt == "plain"


def test_corrupt_db_raises_read_error(tmp_path):
    # HIGH-9: a present-but-corrupt DB must raise (db error), not return None,
    # so the orchestrator can distinguish it and fall back to YAML.
    db = str(tmp_path / "agents.db")
    with open(db, "wb") as f:
        f.write(b"not a sqlite file")
    with pytest.raises(AgentStoreReadError):
        EngineAgentStore(db_path=db).resolve("x")


def test_deleted_or_inactive_agent_returns_none(tmp_path):
    # HIGH-9: not-found / inactive must stay unroutable (None), NOT a db error,
    # so the orchestrator does NOT resurrect them from YAML.
    db = str(tmp_path / "agents.db")
    _seed(db, [("active_one", "active_one", "p", 1, 1),
               ("gone", "gone", "p", 0, 0)])
    store = EngineAgentStore(db_path=db)
    assert store.resolve("nonexistent") is None
    assert store.resolve("gone") is None
