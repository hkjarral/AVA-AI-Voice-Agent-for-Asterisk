"""Call History voice recording (v7.3.0).

Each call records the resolved session voice and its source
("override" | "agent" | "provider-default") so operators can verify
whether a per-agent voice override actually applied — without log access.
Columns are additive/nullable per the store's migration pattern.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.core.call_history import CallHistoryStore, CallRecord


def _record(call_id="call-voice-1", **kw):
    now = datetime.now(timezone.utc)
    return CallRecord(
        call_id=call_id,
        caller_number="15551234567",
        start_time=now,
        end_time=now + timedelta(seconds=5),
        duration_seconds=5.0,
        provider_name="openai_realtime",
        context_name="front_desk",
        conversation_history=[],
        outcome="completed",
        **kw,
    )


def test_call_record_voice_fields_default_none():
    rec = _record()
    assert rec.voice is None
    assert rec.voice_source is None


@pytest.mark.asyncio
async def test_voice_and_source_round_trip(tmp_path):
    store = CallHistoryStore(db_path=str(tmp_path / "ch.db"))
    rec = _record(voice="marin", voice_source="agent")
    assert await store.save(rec)

    loaded = await store.get_by_call_id("call-voice-1")
    assert loaded.voice == "marin"
    assert loaded.voice_source == "agent"


@pytest.mark.asyncio
async def test_null_voice_round_trip(tmp_path):
    store = CallHistoryStore(db_path=str(tmp_path / "ch.db"))
    assert await store.save(_record(call_id="call-voice-2"))
    loaded = await store.get_by_call_id("call-voice-2")
    assert loaded.voice is None
    assert loaded.voice_source is None


@pytest.mark.asyncio
async def test_pre_migration_db_gains_voice_columns(tmp_path):
    """A pre-7.3.0 call_history.db (no voice columns) must migrate additively."""
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE call_records (
            id TEXT PRIMARY KEY, call_id TEXT UNIQUE NOT NULL,
            caller_number TEXT, caller_name TEXT,
            start_time TEXT NOT NULL, end_time TEXT NOT NULL,
            duration_seconds REAL, provider_name TEXT, pipeline_name TEXT,
            pipeline_components TEXT, context_name TEXT,
            conversation_history TEXT, outcome TEXT, transfer_destination TEXT,
            error_message TEXT, tool_calls TEXT,
            avg_turn_latency_ms REAL, max_turn_latency_ms REAL, total_turns INTEGER,
            caller_audio_format TEXT, codec_alignment_ok INTEGER,
            barge_in_count INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.commit()
    conn.close()

    store = CallHistoryStore(db_path=db)
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(call_records)")}
    assert "voice" in cols
    assert "voice_source" in cols

    assert await store.save(_record(call_id="call-voice-3", voice="eve", voice_source="override"))
    loaded = await store.get_by_call_id("call-voice-3")
    assert loaded.voice == "eve"
    assert loaded.voice_source == "override"
