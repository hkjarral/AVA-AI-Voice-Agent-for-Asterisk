import sqlite3, yaml
from agents_store import AgentsStore
from agents_migration import run_migration, merged_effective_contexts, contexts_hash, current_drift, acknowledge_drift

def _setup(tmp_path, inline_contexts, external=None):
    (tmp_path / "contexts").mkdir()
    (tmp_path / "ai-agent.yaml").write_text(yaml.dump({"contexts": inline_contexts}))
    for fname, doc in (external or {}).items():
        (tmp_path / "contexts" / fname).write_text(yaml.dump(doc))
    store = AgentsStore(db_path=str(tmp_path / "agents.db"))
    return store, str(tmp_path / "ai-agent.yaml"), str(tmp_path / "contexts")

def test_migration_imports_all_with_provenance(tmp_path):
    store, y, c = _setup(tmp_path,
        {"default": {"provider": "a", "prompt": "p1"},
         "sales":   {"provider": "a", "prompt": "p2", "pipeline": "local_hybrid"}},
        {"expert.yaml": {"name": "expert", "provider": "b", "system_prompt": "p3"}})
    result = run_migration(store, y, c)
    assert result["imported"] == 3
    assert store.get_by_slug("expert")["source_file"].endswith("expert.yaml")
    assert store.get_by_slug("sales")["source_file"] == "ai-agent.yaml"

def test_legacy_flag_and_default(tmp_path):
    store, y, c = _setup(tmp_path, {"default": {"provider": "a", "prompt": "p"}})
    run_migration(store, y, c)
    row = store.get_by_slug("default")
    assert row["is_operator_managed"] == 0      # no-rug-pull: never counts toward any future cap
    assert row["is_default"] == 1

def test_extra_fields_preserved_in_extra_json(tmp_path):
    store, y, c = _setup(tmp_path, {"d": {"provider": "a", "prompt": "p",
        "pipeline": "local_hybrid", "background_music": "jazz",
        "pre_call_tools": ["enrich"], "disable_global_in_call_tools": ["x"]}})
    run_migration(store, y, c)
    import json
    extra = json.loads(store.get_by_slug("d")["extra_json"])
    assert extra["pipeline"] == "local_hybrid"
    assert extra["pre_call_tools"] == ["enrich"]

def test_migration_idempotent(tmp_path):
    store, y, c = _setup(tmp_path, {"d": {"provider": "a", "prompt": "p"}})
    assert run_migration(store, y, c)["imported"] == 1
    assert run_migration(store, y, c)["imported"] == 0     # second run: no-op

def test_migration_skips_invalid_context_imports_valid(tmp_path):
    store, y, c = _setup(tmp_path,
        {"good": {"provider": "a", "prompt": "p"},
         "bad":  {"provider": "a"}})                        # no prompt → invalid
    result = run_migration(store, y, c)
    # invalid context is skipped-with-error, valid ones import (spec §13.4 file-level skip)
    assert result["imported"] == 1 and result["skipped"] == [("bad", "missing prompt")]

def test_contexts_hash_recorded(tmp_path):
    store, y, c = _setup(tmp_path, {"d": {"provider": "a", "prompt": "p"}})
    run_migration(store, y, c)
    h = store.conn.execute("SELECT contexts_hash FROM schema_migrations").fetchone()[0]
    assert h == contexts_hash(merged_effective_contexts(y, c))

def test_current_drift_none_when_unchanged(tmp_path):
    store, y, c = _setup(tmp_path, {"d": {"provider": "a", "prompt": "p"}})
    run_migration(store, y, c)
    assert current_drift(store, y, c) is None

def test_current_drift_detects_change(tmp_path):
    store, y, c = _setup(tmp_path, {"d": {"provider": "a", "prompt": "p"}})
    run_migration(store, y, c)
    (tmp_path / "ai-agent.yaml").write_text(yaml.dump({"contexts": {"d": {"provider": "b", "prompt": "p2"}}}))
    drift = current_drift(store, y, c)
    assert drift is not None
    assert drift["stored_hash"] != drift["current_hash"]

def test_acknowledge_drift_clears_it(tmp_path):
    store, y, c = _setup(tmp_path, {"d": {"provider": "a", "prompt": "p"}})
    run_migration(store, y, c)
    (tmp_path / "ai-agent.yaml").write_text(yaml.dump({"contexts": {"d": {"provider": "b", "prompt": "p2"}}}))
    assert current_drift(store, y, c) is not None
    acknowledge_drift(store, y, c)
    assert current_drift(store, y, c) is None
