"""H4 (v7.0.1): migrate legacy per-context email overrides onto agent rows.

The legacy email system stored per-context overrides in the global tools config
as ``{key}_by_context`` maps keyed by the ORIGINAL context name. During the
YAML->agents.db migration, each context's override is carried onto the agent's
``email_recipient``/``email_from`` columns, and the surviving map is re-keyed
from the original context name to the agent's slug (so the global-tools
resolution path still resolves once context_name is the slug at runtime).
"""
import yaml as _yaml

from agents_store import AgentsStore
from agents_migration import run_migration


def _write_yaml(tmp_path, contexts, tools=None):
    p = tmp_path / "ai-agent.yaml"
    doc = {"contexts": contexts}
    if tools is not None:
        doc["tools"] = tools
    p.write_text(_yaml.safe_dump(doc))
    return str(p)


def test_legacy_per_context_email_carried_onto_agent_row(tmp_path):
    yaml_path = _write_yaml(
        tmp_path,
        {"Sales East": {"prompt": "se", "provider": "openai"}},
        tools={
            "send_email_summary": {
                "admin_email": "g@x.test",
                "admin_email_by_context": {"Sales East": "se@x.test"},
                "from_email_by_context": {"Sales East": "from@x.test"},
            }
        },
    )
    db = str(tmp_path / "agents.db")
    store = AgentsStore(db_path=db)
    run_migration(store, yaml_path, str(tmp_path / "contexts"))

    row = store.conn.execute(
        "SELECT slug, email_recipient, email_from, email_enabled "
        "FROM agents WHERE display_name=?",
        ("Sales East",),
    ).fetchone()
    assert row is not None
    slug, email_recipient, email_from, email_enabled = row
    assert slug == "sales_east"
    assert email_recipient == "se@x.test"
    assert email_from == "from@x.test"
    assert email_enabled is None  # no legacy equivalent -> stays inherit


def test_surviving_map_rekeyed_to_slug_and_default_untouched(tmp_path):
    yaml_path = _write_yaml(
        tmp_path,
        {"Sales East": {"prompt": "se", "provider": "openai"}},
        tools={
            "send_email_summary": {
                "admin_email": "g@x.test",
                "admin_email_by_context": {"Sales East": "se@x.test"},
                "from_email_by_context": {"Sales East": "from@x.test"},
            }
        },
    )
    db = str(tmp_path / "agents.db")
    store = AgentsStore(db_path=db)
    result = run_migration(store, yaml_path, str(tmp_path / "contexts"))

    rekeyed = result["email_by_context_rekey"]["send_email_summary"]
    # Map entry re-keyed from original name to slug.
    assert rekeyed["admin_email_by_context"] == {"sales_east": "se@x.test"}
    assert rekeyed["from_email_by_context"] == {"sales_east": "from@x.test"}
    # Global default untouched.
    assert rekeyed["admin_email"] == "g@x.test"


def test_top_level_email_keys_migrate_into_first_class_columns(tmp_path):
    """bot re-review (Finding 1): export_agents_yaml.py emits per-agent email as
    TOP-LEVEL context keys (email_recipient/email_from/email_enabled). An
    export -> re-migrate cycle must restore those into the first-class columns,
    not drop them into extra_json (which EngineAgentStore ignores for email)."""
    yaml_path = _write_yaml(
        tmp_path,
        {
            "Sales East": {
                "prompt": "se",
                "provider": "openai",
                "email_recipient": "exported@x.test",
                "email_from": "exfrom@x.test",
                "email_enabled": 0,  # tri-state: explicit OFF must round-trip
            }
        },
    )
    db = str(tmp_path / "agents.db")
    store = AgentsStore(db_path=db)
    run_migration(store, yaml_path, str(tmp_path / "contexts"))

    row = store.conn.execute(
        "SELECT email_recipient, email_from, email_enabled, extra_json "
        "FROM agents WHERE display_name=?",
        ("Sales East",),
    ).fetchone()
    assert row["email_recipient"] == "exported@x.test"
    assert row["email_from"] == "exfrom@x.test"
    assert row["email_enabled"] == 0
    # The email keys must NOT leak into extra_json.
    extra = row["extra_json"]
    if extra:
        import json as _json
        parsed = _json.loads(extra)
        assert "email_recipient" not in parsed
        assert "email_from" not in parsed
        assert "email_enabled" not in parsed


def test_top_level_email_key_wins_over_legacy_by_context_map(tmp_path):
    """Precedence: an explicit top-level per-context email key beats the legacy
    *_by_context map; email_enabled tri-state is preserved."""
    yaml_path = _write_yaml(
        tmp_path,
        {
            "Sales East": {
                "prompt": "se",
                "provider": "openai",
                "email_recipient": "toplevel@x.test",
                "email_enabled": 1,
            }
        },
        tools={
            "send_email_summary": {
                "admin_email_by_context": {"Sales East": "legacy@x.test"},
                "from_email_by_context": {"Sales East": "legacyfrom@x.test"},
            }
        },
    )
    db = str(tmp_path / "agents.db")
    store = AgentsStore(db_path=db)
    run_migration(store, yaml_path, str(tmp_path / "contexts"))

    row = store.conn.execute(
        "SELECT email_recipient, email_from, email_enabled FROM agents WHERE display_name=?",
        ("Sales East",),
    ).fetchone()
    # Top-level recipient wins; from has no top-level key so legacy map fills it.
    assert row["email_recipient"] == "toplevel@x.test"
    assert row["email_from"] == "legacyfrom@x.test"
    assert row["email_enabled"] == 1


def test_no_email_override_leaves_columns_null(tmp_path):
    yaml_path = _write_yaml(
        tmp_path,
        {"Sales East": {"prompt": "se", "provider": "openai"}},
        tools={"send_email_summary": {"admin_email": "g@x.test"}},
    )
    db = str(tmp_path / "agents.db")
    store = AgentsStore(db_path=db)
    run_migration(store, yaml_path, str(tmp_path / "contexts"))
    row = store.conn.execute(
        "SELECT email_recipient, email_from FROM agents WHERE display_name=?",
        ("Sales East",),
    ).fetchone()
    assert row["email_recipient"] is None
    assert row["email_from"] is None
