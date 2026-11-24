"""Pin the released call-history INSERT contract independently of dataclass fields."""

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest

from src.core import call_history as history


# Columns explicitly written by the final pre-versioned INSERT. In particular,
# created_at and the Calendly footprint marker must not be left to SQL defaults.
RELEASED_INSERT = frozenset("""
    id call_id caller_number caller_name called_number start_time end_time duration_seconds
    provider_name pipeline_name pipeline_components context_name routing_method voice voice_source
    conversation_history outcome transfer_destination error_message external_platform external_call_id
    external_direction external_disposition external_metadata call_metadata call_metadata_updates
    tool_calls pre_call_tool_calls post_call_tool_calls avg_turn_latency_ms max_turn_latency_ms total_turns
    caller_audio_format codec_alignment_ok barge_in_count diagnostics_snapshot created_at calendly_erasure_required
""".split())

# The pre-versioned schema shipped to operators. Keep it independent of the
# current writer's CREATE TABLE and migrations.
PRE_VERSIONED_TABLE = """
    CREATE TABLE call_records (
        id TEXT PRIMARY KEY, call_id TEXT NOT NULL, caller_number TEXT, caller_name TEXT, called_number TEXT,
        start_time TEXT NOT NULL, end_time TEXT NOT NULL, duration_seconds REAL,
        provider_name TEXT, pipeline_name TEXT, pipeline_components TEXT, context_name TEXT, routing_method TEXT,
        voice TEXT, voice_source TEXT, conversation_history TEXT, outcome TEXT, transfer_destination TEXT,
        error_message TEXT, external_platform TEXT, external_call_id TEXT, external_direction TEXT,
        external_disposition TEXT, external_metadata TEXT, call_metadata TEXT, call_metadata_updates TEXT,
        tool_calls TEXT, pre_call_tool_calls TEXT, post_call_tool_calls TEXT, avg_turn_latency_ms REAL,
        max_turn_latency_ms REAL, total_turns INTEGER, caller_audio_format TEXT, codec_alignment_ok INTEGER,
        barge_in_count INTEGER, diagnostics_snapshot TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        calendly_erasure_required INTEGER NOT NULL DEFAULT 1
    )
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["fresh", "pre_versioned", "optional_upgrade_unavailable"])
async def test_executed_call_history_insert_columns(origin, tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    path = tmp_path / "synthetic.db"
    expected = RELEASED_INSERT
    if origin != "fresh":
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(PRE_VERSIONED_TABLE)
            if origin == "optional_upgrade_unavailable":
                db.execute("ALTER TABLE call_records DROP COLUMN voice_source")
        if origin == "optional_upgrade_unavailable":
            def optional_ddl_unavailable(_):
                raise sqlite3.OperationalError("synthetic optional upgrade failure")
            monkeypatch.setattr(history, "MIGRATIONS", [(1, optional_ddl_unavailable)])
            expected = RELEASED_INSERT - {"voice_source"}

    store = history.CallHistoryStore(str(path))
    assert store._initialized
    connect = store._get_connection
    observed = []

    def capture(statement):
        columns = re.match(r"INSERT (?:OR REPLACE )?INTO call_records\s*\(([^)]+)\)", statement, re.I)
        if columns:
            observed.append(tuple(name.strip() for name in columns[1].split(",")))

    def instrumented_connection():
        db = connect()
        db.set_trace_callback(capture)
        return db

    monkeypatch.setattr(store, "_get_connection", instrumented_connection)
    now = datetime.now(timezone.utc)
    assert await store.save(history.CallRecord(call_id="synthetic", start_time=now, end_time=now))
    written, = observed
    assert set(written) == expected
    assert len(written) == len(expected)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT calendly_erasure_required FROM call_records").fetchone() == (0,)
