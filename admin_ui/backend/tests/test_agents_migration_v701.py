"""WS-A reproduction tests (v7.0.1): YAML->agents.db migration safety.

Covers CRIT-3: slug collisions must NOT raise IntegrityError and must NOT leave
an empty-but-present (authoritative) agents.db.
"""
import os
import sqlite3

import yaml as _yaml

from agents_store import AgentsStore
from agents_migration import run_migration, migrate_if_needed


def _write_yaml(tmp_path, contexts):
    p = tmp_path / "ai-agent.yaml"
    p.write_text(_yaml.safe_dump({"contexts": contexts}))
    return str(p)


def test_slug_collision_disambiguates_and_keeps_both(tmp_path):
    # CRIT-3: "Sales-East" and "sales_east" both slugify to "sales_east".
    yaml_path = _write_yaml(tmp_path, {
        "Sales-East": {"prompt": "east", "provider": "openai"},
        "sales_east": {"prompt": "plain", "provider": "openai"},
    })
    db = str(tmp_path / "agents.db")
    store = AgentsStore(db_path=db)
    result = run_migration(store, yaml_path, str(tmp_path / "contexts"))
    rows = store.conn.execute(
        "SELECT slug, display_name FROM agents ORDER BY slug").fetchall()
    slugs = [r[0] for r in rows]
    assert result["imported"] == 2
    assert len(slugs) == 2 and len(set(slugs)) == 2          # unique slugs
    assert {r[1] for r in rows} == {"Sales-East", "sales_east"}  # both names kept


def test_migrate_if_needed_no_contexts_leaves_no_db(tmp_path):
    # CRIT-3 corollary: nothing to migrate must NOT leave an authoritative empty DB.
    op = tmp_path / "operator"
    op.mkdir()
    yaml_path = _write_yaml(tmp_path, {})
    result = migrate_if_needed(str(op), yaml_path, str(tmp_path / "contexts"))
    assert result["imported"] == 0
    assert not (op / "agents.db").exists()


def test_migrate_if_needed_promotes_on_success(tmp_path):
    op = tmp_path / "operator"
    op.mkdir()
    yaml_path = _write_yaml(tmp_path, {
        "Sales-East": {"prompt": "e", "provider": "openai"},
        "sales_east": {"prompt": "p", "provider": "openai"},
    })
    result = migrate_if_needed(str(op), yaml_path, str(tmp_path / "contexts"))
    assert result["imported"] == 2
    assert (op / "agents.db").exists()
    n = sqlite3.connect(str(op / "agents.db")).execute(
        "SELECT count(*) FROM agents").fetchone()[0]
    assert n == 2


def test_migrate_if_needed_honors_custom_db_filename(tmp_path):
    # MED-C2 follow-up: when AGENTS_DB_PATH relocates the DB to a custom basename,
    # the migration must seed THAT file (matching the stores' read path), not the
    # hardcoded agents.db. main.py derives op_dir + basename from AGENTS_DB_PATH and
    # passes the basename through as db_filename.
    op = tmp_path / "custom"
    op.mkdir()
    yaml_path = _write_yaml(tmp_path, {"default": {"prompt": "p", "provider": "openai"}})
    result = migrate_if_needed(str(op), yaml_path, str(tmp_path / "contexts"),
                               "relocated.db")
    assert result["imported"] == 1
    # Seeded at the relocated basename, NOT the default agents.db.
    assert (op / "relocated.db").exists()
    assert not (op / "agents.db").exists()
    n = sqlite3.connect(str(op / "relocated.db")).execute(
        "SELECT count(*) FROM agents").fetchone()[0]
    assert n == 1


def test_relative_agents_db_path_yields_nonempty_dirname():
    # MED-C2 follow-up: a bare AGENTS_DB_PATH (e.g. "agents.db") must resolve to a
    # real directory before dirname(); dirname("agents.db") == "" would make
    # os.makedirs("") raise at startup. abspath() guards against that.
    agents_db = os.path.abspath("agents.db")
    op_dir = os.path.dirname(agents_db)
    assert op_dir, "abspath() must yield a non-empty parent dir for a bare filename"
    assert os.path.basename(agents_db) == "agents.db"
