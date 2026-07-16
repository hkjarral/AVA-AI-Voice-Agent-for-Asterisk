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
        assert json.loads(first["extra_json"])["pipeline"] == "custom"
        assert first["email_enabled"] == 0
        assert sum(int(row["is_default"]) for row in rows) == 1


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
