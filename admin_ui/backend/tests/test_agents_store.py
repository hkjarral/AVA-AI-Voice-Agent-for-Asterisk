import pytest
from agents_store import AgentsStore, slugify

@pytest.fixture
def store(tmp_path):
    return AgentsStore(db_path=str(tmp_path / "agents.db"))

def test_schema_created_with_wal(store):
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(agents)")}
    assert {"slug", "extra_json", "tool_configs_json", "is_operator_managed", "is_default", "source_file"} <= cols


def test_additive_migration_adds_tool_configs_column(tmp_path):
    db = tmp_path / "old-agents.db"
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE agents (id TEXT PRIMARY KEY, slug TEXT UNIQUE, display_name TEXT, "
        "provider TEXT, prompt TEXT, tools_json TEXT, mcp_json TEXT, audio_profile TEXT, "
        "extra_json TEXT, is_operator_managed INTEGER, is_active INTEGER, is_default INTEGER, "
        "source_file TEXT, created_at TEXT, updated_at TEXT, notes TEXT)"
    )
    conn.commit()
    conn.close()
    migrated = AgentsStore(db_path=str(db))
    cols = {r[1] for r in migrated.conn.execute("PRAGMA table_info(agents)")}
    assert "tool_configs_json" in cols

def test_slugify():
    assert slugify("Maria - Vendas") == "maria_vendas"
    assert slugify("Demo Deepgram!") == "demo_deepgram"

def test_create_and_get(store):
    a = store.create(display_name="Maria - Vendas", provider="openai_realtime", prompt="p")
    assert a["slug"] == "maria_vendas"
    assert store.get_by_slug("maria_vendas")["display_name"] == "Maria - Vendas"


def test_tool_configs_roundtrip(store):
    raw = '{"transfer":{"destination_policy":"selected","destination_keys":["sales"]}}'
    row = store.create(
        display_name="Scoped", provider="x", prompt="p", tool_configs_json=raw
    )
    assert row["tool_configs_json"] == raw
    updated = store.update("scoped", tool_configs_json=None)
    assert updated["tool_configs_json"] is None


def test_existing_extra_calendar_bindings_are_promoted(tmp_path):
    db = tmp_path / "agents.db"
    first = AgentsStore(db_path=str(db))
    first.create(
        display_name="Legacy Calendar",
        provider="x",
        prompt="p",
        extra_json='{"tool_overrides":{"google_calendar":{"selected_calendars":["sales"]}}}',
    )
    first.close()

    reopened = AgentsStore(db_path=str(db))
    row = reopened.get_by_slug("legacy_calendar")
    import json
    assert json.loads(row["tool_configs_json"])["google_calendar"] == {
        "calendar_policy": "selected",
        "calendar_keys": ["sales"],
    }

def test_first_agent_becomes_default(store):
    a = store.create(display_name="A", provider="x", prompt="p")
    assert store.get_default()["slug"] == a["slug"]

def test_set_default_atomically_clears_others(store):
    a = store.create(display_name="A", provider="x", prompt="p")
    b = store.create(display_name="B", provider="x", prompt="p")
    store.set_default(b["slug"])
    rows = store.conn.execute("SELECT slug FROM agents WHERE is_default=1").fetchall()
    assert [r[0] for r in rows] == [b["slug"]]

def test_delete_default_promotes_oldest_active(store):
    a = store.create(display_name="A", provider="x", prompt="p")
    b = store.create(display_name="B", provider="x", prompt="p")
    store.delete(a["slug"])
    assert store.get_default()["slug"] == b["slug"]

def test_deactivate_last_active_leaves_no_default(store):
    a = store.create(display_name="A", provider="x", prompt="p")
    store.set_active(a["slug"], False)
    assert store.get_default() is None
    assert store.count_active() == 0

def test_duplicate_slug_rejected(store):
    store.create(display_name="A", provider="x", prompt="p")
    with pytest.raises(ValueError):
        store.create(display_name="A", provider="x", prompt="p2")

def test_set_default_bad_slug_preserves_invariant(store):
    a = store.create(display_name="A", provider="x", prompt="p")
    store.set_default("does_not_exist")          # must NOT leave zero defaults
    assert store.get_default() is not None
    assert store.get_default()["slug"] == a["slug"]

def test_set_default_inactive_slug_preserves_invariant(store):
    a = store.create(display_name="A", provider="x", prompt="p")
    b = store.create(display_name="B", provider="x", prompt="p")
    store.set_active(b["slug"], False)
    store.set_default(b["slug"])                  # b is inactive -> no-op target
    assert store.get_default() is not None
    assert store.get_default()["slug"] == a["slug"]

def test_update_rejects_is_default(store):
    a = store.create(display_name="A", provider="x", prompt="p")
    with pytest.raises(ValueError):
        store.update(a["slug"], is_default=1)

def test_create_allows_empty_provider(store):
    row = store.create(display_name="Hybrid", prompt="p",
                       extra_json='{"pipeline": "local_hybrid"}')
    assert row["provider"] == ""
