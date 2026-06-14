import pytest
from agents_store import AgentsStore, slugify

@pytest.fixture
def store(tmp_path):
    return AgentsStore(db_path=str(tmp_path / "agents.db"))

def test_schema_created_with_wal(store):
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(agents)")}
    assert {"slug", "extra_json", "is_operator_managed", "is_default", "source_file"} <= cols

def test_slugify():
    assert slugify("Maria - Vendas") == "maria_vendas"
    assert slugify("Demo Deepgram!") == "demo_deepgram"

def test_create_and_get(store):
    a = store.create(display_name="Maria - Vendas", provider="openai_realtime", prompt="p")
    assert a["slug"] == "maria_vendas"
    assert store.get_by_slug("maria_vendas")["display_name"] == "Maria - Vendas"

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
