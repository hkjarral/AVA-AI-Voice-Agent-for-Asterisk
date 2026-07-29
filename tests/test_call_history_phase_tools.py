"""
Tests for the per-phase tool-execution tracking added to CallHistoryStore.

Covers:
- Schema migration (existing DB without the new columns gets them on init).
- ``append_phase_tool`` / ``update_phase_tool`` round-trip for both ``pre_call``
  and ``post_call`` phases.
- ``update_phase_tool`` falling back to append when no matching entry exists.
- Concurrent appends from multiple coroutines do not clobber each other.
- ``CallRecord.from_dict`` decoding NULL columns to ``[]``.
- ``GenericWebhookTool._last_result`` populated on success / non-2xx /
  ``aiohttp.ClientError`` / ``Exception`` / disabled / no URL paths.
- Per-tool ``response_body_max_chars`` override + global env truncation.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(call_id: str = "call-phase-1"):
    """Minimal CallRecord populated enough for save() to succeed."""
    from src.core.call_history import CallRecord
    now = datetime.now(timezone.utc)
    return CallRecord(
        call_id=call_id,
        caller_number="15551234567",
        caller_name="Test",
        start_time=now,
        end_time=now + timedelta(seconds=5),
        duration_seconds=5.0,
        provider_name="openai_realtime",
        pipeline_name=None,
        pipeline_components={},
        context_name="demo",
        conversation_history=[{"role": "user", "content": "hi"}],
        outcome="completed",
        tool_calls=[],
        avg_turn_latency_ms=100.0,
        max_turn_latency_ms=100.0,
        total_turns=1,
        caller_audio_format="ulaw",
        codec_alignment_ok=True,
        barge_in_count=0,
    )


# ---------------------------------------------------------------------------
# Schema / dataclass round-trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_roundtrip_includes_phase_tool_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "ch.db")
    from src.core.call_history import CallHistoryStore

    store = CallHistoryStore(db_path=db_path)
    record = _make_record("call-roundtrip")
    record.pre_call_tool_calls = [
        {"name": "ghl_lookup", "phase": "pre_call", "status": "ok", "duration_ms": 312.0}
    ]
    record.post_call_tool_calls = [
        {"name": "n8n_webhook", "phase": "post_call", "status": "pending", "duration_ms": None}
    ]
    assert await store.save(record) is True

    fetched = await store.get_by_call_id("call-roundtrip")
    assert fetched is not None
    assert len(fetched.pre_call_tool_calls) == 1
    assert fetched.pre_call_tool_calls[0]["name"] == "ghl_lookup"
    assert len(fetched.post_call_tool_calls) == 1
    assert fetched.post_call_tool_calls[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_schema_migration_adds_columns_to_existing_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "old.db")

    # Create a pre-migration database with the old schema (no phase columns).
    legacy_ddl = """
        CREATE TABLE call_records (
            id TEXT PRIMARY KEY, call_id TEXT NOT NULL, caller_number TEXT, caller_name TEXT,
            start_time TEXT NOT NULL, end_time TEXT NOT NULL, duration_seconds REAL,
            provider_name TEXT, pipeline_name TEXT, pipeline_components TEXT, context_name TEXT,
            conversation_history TEXT, outcome TEXT, transfer_destination TEXT, error_message TEXT,
            tool_calls TEXT, avg_turn_latency_ms REAL, max_turn_latency_ms REAL, total_turns INTEGER,
            caller_audio_format TEXT, codec_alignment_ok INTEGER, barge_in_count INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """
    conn = sqlite3.connect(db_path)
    conn.execute(legacy_ddl)
    conn.execute(
        "INSERT INTO call_records (id, call_id, start_time, end_time) VALUES (?, ?, ?, ?)",
        ("legacy-id", "legacy-call", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:05+00:00"),
    )
    conn.commit()
    conn.close()

    # Re-open via the store — migration should add the columns.
    from src.core.call_history import CallHistoryStore
    store = CallHistoryStore(db_path=db_path)

    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(call_records)")}
    conn.close()
    assert "pre_call_tool_calls" in cols
    assert "post_call_tool_calls" in cols

    # Existing legacy row should hydrate with empty lists.
    fetched = await store.get_by_call_id("legacy-call")
    assert fetched is not None
    assert fetched.pre_call_tool_calls == []
    assert fetched.post_call_tool_calls == []


# ---------------------------------------------------------------------------
# append_phase_tool / update_phase_tool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_append_then_update_post_call_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "ch.db")
    from src.core.call_history import CallHistoryStore

    store = CallHistoryStore(db_path=db_path)
    record = _make_record("call-append-update")
    await store.save(record)

    started = "2026-04-30T14:00:00+00:00"
    appended = await store.append_phase_tool(
        "call-append-update",
        "post_call",
        {"name": "wh", "phase": "post_call", "status": "pending", "started_at": started, "attempt": 1},
    )
    assert appended is True

    fetched = await store.get_by_call_id("call-append-update")
    assert len(fetched.post_call_tool_calls) == 1
    assert fetched.post_call_tool_calls[0]["status"] == "pending"

    updated = await store.update_phase_tool(
        "call-append-update",
        "post_call",
        "wh",
        started,
        {"status": "ok", "http_status": 200, "duration_ms": 312.5, "finished_at": "x"},
    )
    assert updated is True

    fetched = await store.get_by_call_id("call-append-update")
    assert len(fetched.post_call_tool_calls) == 1, "update must NOT append a second entry"
    entry = fetched.post_call_tool_calls[0]
    assert entry["status"] == "ok"
    assert entry["http_status"] == 200
    assert entry["duration_ms"] == 312.5
    # Original fields survive the merge.
    assert entry["started_at"] == started
    assert entry["attempt"] == 1


@pytest.mark.asyncio
async def test_update_phase_tool_appends_when_no_match(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "ch.db")
    from src.core.call_history import CallHistoryStore

    store = CallHistoryStore(db_path=db_path)
    await store.save(_make_record("call-orphan-update"))

    # No prior pending entry — update should still create the row.
    ok = await store.update_phase_tool(
        "call-orphan-update", "post_call", "nope", None,
        {"status": "error", "error_message": "no match"},
    )
    assert ok is True
    fetched = await store.get_by_call_id("call-orphan-update")
    assert len(fetched.post_call_tool_calls) == 1
    assert fetched.post_call_tool_calls[0]["status"] == "error"


@pytest.mark.asyncio
async def test_pre_call_phase_tool_independent_from_post_call(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "ch.db")
    from src.core.call_history import CallHistoryStore

    store = CallHistoryStore(db_path=db_path)
    await store.save(_make_record("call-phases-split"))

    await store.append_phase_tool(
        "call-phases-split", "pre_call",
        {"name": "lookup", "phase": "pre_call", "status": "ok"},
    )
    await store.append_phase_tool(
        "call-phases-split", "post_call",
        {"name": "wh", "phase": "post_call", "status": "pending"},
    )

    fetched = await store.get_by_call_id("call-phases-split")
    assert len(fetched.pre_call_tool_calls) == 1
    assert fetched.pre_call_tool_calls[0]["name"] == "lookup"
    assert len(fetched.post_call_tool_calls) == 1
    assert fetched.post_call_tool_calls[0]["name"] == "wh"


@pytest.mark.asyncio
async def test_concurrent_post_call_updates_do_not_clobber(tmp_path, monkeypatch):
    """Two tools updating the same row in parallel must both land."""
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "ch.db")
    from src.core.call_history import CallHistoryStore

    store = CallHistoryStore(db_path=db_path)
    await store.save(_make_record("call-concurrent"))

    # Pre-seed two pending entries.
    s1 = "2026-04-30T14:00:00+00:00"
    s2 = "2026-04-30T14:00:00.500+00:00"
    await store.append_phase_tool(
        "call-concurrent", "post_call",
        {"name": "wh-a", "phase": "post_call", "status": "pending", "started_at": s1},
    )
    await store.append_phase_tool(
        "call-concurrent", "post_call",
        {"name": "wh-b", "phase": "post_call", "status": "pending", "started_at": s2},
    )

    async def finish_a():
        await store.update_phase_tool(
            "call-concurrent", "post_call", "wh-a", s1,
            {"status": "ok", "http_status": 200},
        )

    async def finish_b():
        await store.update_phase_tool(
            "call-concurrent", "post_call", "wh-b", s2,
            {"status": "error", "http_status": 500},
        )

    await asyncio.gather(finish_a(), finish_b())

    fetched = await store.get_by_call_id("call-concurrent")
    by_name = {e["name"]: e for e in fetched.post_call_tool_calls}
    assert by_name["wh-a"]["status"] == "ok"
    assert by_name["wh-a"]["http_status"] == 200
    assert by_name["wh-b"]["status"] == "error"
    assert by_name["wh-b"]["http_status"] == 500


@pytest.mark.asyncio
async def test_phase_methods_no_op_when_call_id_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    db_path = str(tmp_path / "ch.db")
    from src.core.call_history import CallHistoryStore

    store = CallHistoryStore(db_path=db_path)
    # No save() — call row does not exist.
    assert await store.append_phase_tool("ghost", "post_call", {"name": "x"}) is False
    assert await store.update_phase_tool("ghost", "post_call", "x", None, {"status": "ok"}) is False


# ---------------------------------------------------------------------------
# GenericWebhookTool last_result population
# ---------------------------------------------------------------------------

class _FakeContext:
    """Minimal stand-in for PostCallContext (only attributes the tool reads).

    Each test gets a fresh instance with a unique call_id so the per-call
    diagnostics dict in GenericWebhookTool can't bleed across tests.
    """
    _counter = 0

    def __init__(self):
        type(self)._counter += 1
        self.call_id = f"test-call-{type(self)._counter}"
        self.summary = ""
        self.conversation_history = [{"role": "user", "content": "hi"}]
        # Attributes read by GenericWebhookTool._substitute_variables — empty
        # strings are fine; the tool null-coalesces.
        self.caller_number = ""
        self.called_number = ""
        self.caller_name = ""
        self.context_name = ""
        self.provider = ""
        self.call_direction = ""
        self.campaign_id = ""
        self.lead_id = ""

    def to_payload_dict(self):
        return {"call_id": self.call_id}


def _make_webhook(url: str = "", **overrides):
    from src.tools.http.generic_webhook import WebhookConfig, GenericWebhookTool
    cfg = WebhookConfig(name="test_wh", url=url, **overrides)
    return GenericWebhookTool(cfg)


@pytest.mark.asyncio
async def test_webhook_last_result_disabled():
    tool = _make_webhook(url="http://example.invalid/x", enabled=False)
    ctx = _FakeContext()
    await tool.execute(ctx)
    last = tool.get_last_result(call_id=ctx.call_id)
    assert last is not None
    assert last["status"] == "skipped"
    assert "disabled" in (last.get("error_message") or "")
    # Body capture skipped on disabled path.
    assert last.get("response_summary") is None


@pytest.mark.asyncio
async def test_webhook_last_result_no_url():
    tool = _make_webhook(url="")
    ctx = _FakeContext()
    await tool.execute(ctx)
    last = tool.get_last_result(call_id=ctx.call_id)
    assert last is not None
    assert last["status"] == "skipped"
    assert last.get("http_status") is None


@pytest.mark.asyncio
async def test_webhook_last_result_success(monkeypatch):
    """Mock aiohttp to return a 200 with a body and confirm last_result captures it."""
    from src.tools.http import generic_webhook as gw

    class _MockResponse:
        def __init__(self):
            self.status = 200
        async def text(self):
            return '{"ok":true,"id":"abc"}'
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _MockSession:
        def __init__(self, **kwargs): pass
        def request(self, **kwargs): return _MockResponse()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(gw.aiohttp, "ClientSession", _MockSession)

    tool = _make_webhook(url="http://example.invalid/ok", payload_template='{"x":1}')
    ctx = _FakeContext()
    await tool.execute(ctx)
    last = tool.get_last_result(call_id=ctx.call_id)
    assert last["status"] == "ok"
    assert last["http_status"] == 200
    assert last["response_summary"] == '{"ok":true,"id":"abc"}'
    assert last["error_message"] is None
    assert last["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_webhook_last_result_non_2xx(monkeypatch):
    from src.tools.http import generic_webhook as gw

    class _MockResponse:
        def __init__(self):
            self.status = 503
        async def text(self):
            return "<html>upstream down</html>"
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _MockSession:
        def __init__(self, **kwargs): pass
        def request(self, **kwargs): return _MockResponse()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(gw.aiohttp, "ClientSession", _MockSession)

    tool = _make_webhook(url="http://example.invalid/down", payload_template="{}")
    ctx = _FakeContext()
    await tool.execute(ctx)
    last = tool.get_last_result(call_id=ctx.call_id)
    assert last["status"] == "error"
    assert last["http_status"] == 503
    assert "HTTP 503" in (last["error_message"] or "")
    assert "upstream down" in (last["response_summary"] or "")


@pytest.mark.asyncio
async def test_webhook_last_result_client_error(monkeypatch):
    from src.tools.http import generic_webhook as gw
    import aiohttp

    class _BoomSession:
        def __init__(self, **kwargs): pass
        def request(self, **kwargs):
            raise aiohttp.ClientConnectionError("connection refused")
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(gw.aiohttp, "ClientSession", _BoomSession)

    tool = _make_webhook(url="http://example.invalid/x", payload_template="{}")
    ctx = _FakeContext()
    await tool.execute(ctx)
    last = tool.get_last_result(call_id=ctx.call_id)
    assert last["status"] == "error"
    assert "ClientConnectionError" in (last["error_message"] or "")


@pytest.mark.asyncio
async def test_webhook_last_result_timeout(monkeypatch):
    from src.tools.http import generic_webhook as gw
    import aiohttp

    class _TimeoutSession:
        def __init__(self, **kwargs): pass
        def request(self, **kwargs):
            raise aiohttp.ServerTimeoutError("read timed out")
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(gw.aiohttp, "ClientSession", _TimeoutSession)

    tool = _make_webhook(url="http://example.invalid/slow", payload_template="{}")
    ctx = _FakeContext()
    await tool.execute(ctx)
    last = tool.get_last_result(call_id=ctx.call_id)
    assert last["status"] == "timeout"
    assert "ServerTimeoutError" in (last["error_message"] or "")


# ---------------------------------------------------------------------------
# response_body_max_chars resolution (env + per-tool override)
# ---------------------------------------------------------------------------

def test_resolve_body_max_chars_default_when_unset(monkeypatch):
    from src.tools.http.generic_webhook import _resolve_body_max_chars
    monkeypatch.delenv("CALL_HISTORY_RESPONSE_BODY_MAX_CHARS", raising=False)
    assert _resolve_body_max_chars(None) == 512


def test_resolve_body_max_chars_from_env(monkeypatch):
    from src.tools.http.generic_webhook import _resolve_body_max_chars
    monkeypatch.setenv("CALL_HISTORY_RESPONSE_BODY_MAX_CHARS", "128")
    assert _resolve_body_max_chars(None) == 128


def test_resolve_body_max_chars_per_tool_overrides_env(monkeypatch):
    from src.tools.http.generic_webhook import _resolve_body_max_chars
    monkeypatch.setenv("CALL_HISTORY_RESPONSE_BODY_MAX_CHARS", "128")
    assert _resolve_body_max_chars(2048) == 2048


def test_resolve_body_max_chars_zero_disables_capture(monkeypatch):
    from src.tools.http.generic_webhook import _resolve_body_max_chars
    assert _resolve_body_max_chars(0) == 0
    monkeypatch.setenv("CALL_HISTORY_RESPONSE_BODY_MAX_CHARS", "0")
    assert _resolve_body_max_chars(None) == 0


@pytest.mark.asyncio
async def test_webhook_truncates_response_body(monkeypatch):
    from src.tools.http import generic_webhook as gw
    big_body = "Z" * 2000

    class _MockResponse:
        def __init__(self):
            self.status = 200
        async def text(self): return big_body
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _MockSession:
        def __init__(self, **kwargs): pass
        def request(self, **kwargs): return _MockResponse()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(gw.aiohttp, "ClientSession", _MockSession)

    tool = _make_webhook(url="http://example.invalid/big", payload_template="{}", response_body_max_chars=64)
    ctx = _FakeContext()
    await tool.execute(ctx)
    summary = tool.get_last_result(call_id=ctx.call_id)["response_summary"]
    # 64 chars + ellipsis
    assert summary.startswith("Z" * 64)
    assert summary.endswith("…")
    assert len(summary) == 65


@pytest.mark.asyncio
async def test_webhook_concurrent_calls_dont_clobber_each_other(monkeypatch):
    """Two concurrent webhook executions on the same singleton tool must not
    leak diagnostics between calls. Reproduces the Codex P1 finding: with the
    pre-fix shared ``self._last_result`` field, whichever execute() returned
    last would overwrite the other's status/HTTP body."""
    from src.tools.http import generic_webhook as gw

    class _MockResponse200:
        def __init__(self, body: str):
            self.status = 200
            self._body = body
        async def text(self): return self._body
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _MockResponse500:
        def __init__(self): self.status = 500
        async def text(self): return "boom"
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    # Different sessions per call_id so we can simulate distinct outcomes.
    class _MockSession:
        def __init__(self, **kwargs): pass
        def request(self, **kwargs):
            url = kwargs.get("url", "")
            if "/ok" in url:
                return _MockResponse200('{"call":"A"}')
            return _MockResponse500()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(gw.aiohttp, "ClientSession", _MockSession)

    tool = _make_webhook(url="http://example.invalid/{caller_number}", payload_template="{}")
    ctx_a = _FakeContext()
    ctx_a.caller_number = "ok"
    ctx_b = _FakeContext()
    ctx_b.caller_number = "fail"

    # Run both concurrently — pre-fix this would race and one would overwrite
    # the other's _last_result.
    await asyncio.gather(tool.execute(ctx_a), tool.execute(ctx_b))

    a = tool.get_last_result(call_id=ctx_a.call_id)
    b = tool.get_last_result(call_id=ctx_b.call_id)

    assert a is not None and b is not None, "both calls must have isolated diagnostics"
    assert a["status"] == "ok", f"call A should report ok, got {a['status']}"
    assert b["status"] == "error", f"call B should report error, got {b['status']}"
    assert a["http_status"] == 200
    assert b["http_status"] == 500
    # Pop semantics: a second read returns None (no leftover state).
    assert tool.get_last_result(call_id=ctx_a.call_id) is None


def test_webhook_summary_prompt_with_literal_braces_falls_back_to_default():
    """Codex P2 fix: a summary_prompt with literal ``{`` / ``}`` (e.g. JSON
    sample) used to crash ``str.format()`` and silently return ``""``."""
    from src.tools.http.generic_webhook import WebhookConfig, GenericWebhookTool

    cfg = WebhookConfig(
        name="bad_prompt",
        url="http://example.invalid",
        # Literal JSON braces — pre-fix this raised KeyError.
        summary_prompt='Reply with {"summary":"<text>"} in {max_words} words.',
    )
    tool = GenericWebhookTool(cfg)
    # Falls back to the default prompt when format() fails.
    out = tool._resolve_summary_prompt(max_words=50)
    assert "{max_words}" not in out  # interpolated default
    assert "50 words or less" in out

    # Custom prompt with only the documented placeholder works.
    cfg2 = WebhookConfig(
        name="good_prompt",
        url="http://example.invalid",
        summary_prompt="Summarize in {max_words} words. Use casual tone.",
    )
    tool2 = GenericWebhookTool(cfg2)
    out2 = tool2._resolve_summary_prompt(max_words=80)
    assert "Summarize in 80 words. Use casual tone." == out2


@pytest.mark.asyncio
async def test_webhook_zero_max_chars_skips_body(monkeypatch):
    from src.tools.http import generic_webhook as gw

    class _MockResponse:
        def __init__(self):
            self.status = 200
        async def text(self): return "irrelevant"
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _MockSession:
        def __init__(self, **kwargs): pass
        def request(self, **kwargs): return _MockResponse()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(gw.aiohttp, "ClientSession", _MockSession)

    tool = _make_webhook(url="http://example.invalid/no-body", payload_template="{}", response_body_max_chars=0)
    ctx = _FakeContext()
    await tool.execute(ctx)
    last = tool.get_last_result(call_id=ctx.call_id)
    assert last["status"] == "ok"
    assert last["http_status"] == 200
    assert last["response_summary"] is None  # body capture explicitly disabled
