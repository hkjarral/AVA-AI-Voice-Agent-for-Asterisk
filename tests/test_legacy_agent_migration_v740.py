import hashlib
import json
import sqlite3

import pytest

from src.core.legacy_agent_migration import (
    LegacyAgentMigrationError,
    ensure_legacy_contexts_imported,
)


def test_import_is_atomic_complete_and_collision_safe(tmp_path):
    database = tmp_path / "agents.db"
    result = ensure_legacy_contexts_imported(
        {
            "Sales-East": {
                "provider": "openai_realtime",
                "voice": "alloy",
                "greeting": "Hello",
                "prompt": "Sell safely",
                "tools": ["blind_transfer"],
                "tool_configs": {
                    "transfer": {
                        "destination_policy": "selected",
                        "destination_keys": ["support"],
                    }
                },
                "pipeline": "custom",
                "tool_overrides": {
                    "google_calendar": {"selected_calendars": ["sales-calendar"]},
                    "microsoft_calendar": {"selected_accounts": ["sales-account"]},
                },
                "email_enabled": False,
            },
            "sales_east": {"provider": "local", "prompt": "Second"},
        },
        db_path=str(database),
    )

    assert result == {"imported": 2, "default_slug": "sales_east"}
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM agents ORDER BY created_at, slug").fetchall()
        assert {row["slug"] for row in rows} == {"sales_east", "sales_east_2"}
        first = next(row for row in rows if row["display_name"] == "Sales-East")
        assert json.loads(first["tools_json"]) == ["blind_transfer"]
        assert json.loads(first["tool_configs_json"])["transfer"]["destination_keys"] == ["support"]
        policies = json.loads(first["tool_configs_json"])
        assert policies["google_calendar"] == {
            "calendar_policy": "selected",
            "calendar_keys": ["sales-calendar"],
        }
        assert policies["microsoft_calendar"] == {
            "account_policy": "selected",
            "account_keys": ["sales-account"],
        }
        assert json.loads(first["extra_json"])["pipeline"] == "custom"
        assert "tool_overrides" not in json.loads(first["extra_json"])
        assert first["email_enabled"] == 0
        assert sum(int(row["is_default"]) for row in rows) == 1


def test_engine_import_records_admin_compatible_context_hash(tmp_path):
    database = tmp_path / "agents.db"
    contexts = {
        "sales": {
            "provider": "local",
            "prompt": "Sell safely",
            "_source_file": "contexts/sales.yaml",
        }
    }

    ensure_legacy_contexts_imported(contexts, db_path=str(database))

    canonical = json.dumps(
        {"sales": {"provider": "local", "prompt": "Sell safely"}},
        sort_keys=True,
        separators=(",", ":"),
    )
    expected = hashlib.sha256(canonical.encode()).hexdigest()
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT contexts_hash FROM schema_migrations WHERE version=1"
        ).fetchone()[0]
    assert stored == expected


def test_completed_migration_backfills_only_a_missing_context_hash(tmp_path):
    database = tmp_path / "agents.db"
    contexts = {"sales": {"provider": "local", "prompt": "Sell safely"}}
    ensure_legacy_contexts_imported(contexts, db_path=str(database))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_migrations SET contexts_hash=NULL WHERE version=1"
        )
        connection.commit()

    result = ensure_legacy_contexts_imported(contexts, db_path=str(database))

    canonical = json.dumps(contexts, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(canonical.encode()).hexdigest()
    assert result["already_configured"] is True
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT contexts_hash FROM schema_migrations WHERE version=1"
        ).fetchone()[0]
        rows = connection.execute("SELECT slug FROM agents").fetchall()
    assert stored == expected
    assert rows == [("sales",)]

    ensure_legacy_contexts_imported(
        {"sales": {"provider": "local", "prompt": "Changed legacy YAML"}},
        db_path=str(database),
    )
    with sqlite3.connect(database) as connection:
        preserved = connection.execute(
            "SELECT contexts_hash FROM schema_migrations WHERE version=1"
        ).fetchone()[0]
    assert preserved == expected


def test_populated_agent_store_is_never_overwritten(tmp_path):
    database = tmp_path / "agents.db"
    ensure_legacy_contexts_imported(
        {"existing": {"provider": "local", "prompt": "Keep me"}},
        db_path=str(database),
    )
    result = ensure_legacy_contexts_imported(
        {"replacement": {"provider": "local", "prompt": "Do not import"}},
        db_path=str(database),
    )
    assert result["already_configured"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT display_name FROM agents").fetchall() == [("existing",)]


def test_completed_migration_does_not_resurrect_deleted_agents(tmp_path):
    database = tmp_path / "agents.db"
    contexts = {"existing": {"provider": "local", "prompt": "Keep me"}}
    ensure_legacy_contexts_imported(contexts, db_path=str(database))
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM agents")
        connection.commit()

    result = ensure_legacy_contexts_imported(contexts, db_path=str(database))

    assert result == {
        "imported": 0,
        "already_configured": True,
        "resource_policies_upgraded": 0,
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0


def test_prepopulated_store_records_completion_before_agents_are_deleted(tmp_path):
    database = tmp_path / "agents.db"
    ensure_legacy_contexts_imported(
        {"existing": {"provider": "local", "prompt": "Keep me"}},
        db_path=str(database),
    )
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM schema_migrations")
        connection.commit()

    result = ensure_legacy_contexts_imported(
        {"replacement": {"provider": "local", "prompt": "Do not import"}},
        db_path=str(database),
    )

    assert result["already_configured"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=1"
        ).fetchone()[0] == 1
        connection.execute("DELETE FROM agents")
        connection.commit()

    second = ensure_legacy_contexts_imported(
        {"replacement": {"provider": "local", "prompt": "Do not import"}},
        db_path=str(database),
    )
    assert second["already_configured"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0


def test_empty_wal_database_sidecars_are_removed_before_import(tmp_path):
    database = tmp_path / "agents.db"
    ensure_legacy_contexts_imported(
        {"old": {"provider": "local", "prompt": "Old"}},
        db_path=str(database),
    )

    stale_connection = sqlite3.connect(database)
    assert stale_connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    stale_connection.execute("DELETE FROM agents")
    stale_connection.execute("DELETE FROM schema_migrations")
    stale_connection.commit()
    wal_path = tmp_path / "agents.db-wal"
    shm_path = tmp_path / "agents.db-shm"
    assert wal_path.exists()
    assert shm_path.exists()

    try:
        result = ensure_legacy_contexts_imported(
            {"new": {"provider": "local", "prompt": "New"}},
            db_path=str(database),
        )
    finally:
        stale_connection.close()

    assert result == {"imported": 1, "default_slug": "new"}
    assert not wal_path.exists()
    assert not shm_path.exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT slug, prompt FROM agents").fetchall() == [
            ("new", "New")
        ]


def test_existing_early_v740_rows_promote_calendar_bindings(tmp_path):
    database = tmp_path / "agents.db"
    ensure_legacy_contexts_imported(
        {"existing": {"provider": "local", "prompt": "Keep me"}},
        db_path=str(database),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE agents SET extra_json=? WHERE slug='existing'",
            (json.dumps({
                "tool_overrides": {
                    "google_calendar": {"selected_calendars": ["legacy"]},
                },
            }),),
        )
        connection.commit()

    result = ensure_legacy_contexts_imported({}, db_path=str(database))
    assert result["resource_policies_upgraded"] == 1
    with sqlite3.connect(database) as connection:
        raw = connection.execute(
            "SELECT tool_configs_json FROM agents WHERE slug='existing'"
        ).fetchone()[0]
    assert json.loads(raw)["google_calendar"]["calendar_keys"] == ["legacy"]
    assert ensure_legacy_contexts_imported({}, db_path=str(database))[
        "resource_policies_upgraded"
    ] == 0


def test_invalid_legacy_context_fails_without_creating_database(tmp_path):
    database = tmp_path / "agents.db"
    with pytest.raises(LegacyAgentMigrationError, match="expected mapping"):
        ensure_legacy_contexts_imported(
            {"broken": ["not", "a", "mapping"]}, db_path=str(database)
        )
    assert not database.exists()


def test_empty_contexts_leave_store_for_starter_setup(tmp_path):
    database = tmp_path / "agents.db"
    assert ensure_legacy_contexts_imported({}, db_path=str(database))["imported"] == 0
    assert not database.exists()


def test_bundled_project_demo_does_not_suppress_fresh_starter_setup(tmp_path):
    database = tmp_path / "agents.db"
    result = ensure_legacy_contexts_imported(
        {
            "demo_project_expert": {
                "description": (
                    "AI agent that answers questions about the Asterisk AI Voice Agent project"
                ),
                "provider": "local",
                "system_prompt": "Bundled project demo",
            }
        },
        db_path=str(database),
    )

    assert result == {"imported": 0, "already_configured": False}
    assert not database.exists()
