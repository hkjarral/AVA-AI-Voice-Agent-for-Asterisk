"""v7 additive call-history aliases: agent_slug / agent_name.

These exercise the pure converter logic in api.calls directly (no call-history DB,
no docker), proving the aliases are additive and that context_name/routing_method
are never altered."""
from types import SimpleNamespace
from api import calls as calls_api


def _rec(**over):
    base = dict(
        id="r1", call_id="c1", caller_number=None, caller_name=None,
        start_time=None, end_time=None, duration_seconds=0.0,
        provider_name="openai", pipeline_name=None, pipeline_components={},
        context_name="sales", routing_method="ai_agent",
        conversation_history=[], outcome="completed", transfer_destination=None,
        error_message=None, tool_calls=[], pre_call_tool_calls=[],
        post_call_tool_calls=[], avg_turn_latency_ms=0.0, max_turn_latency_ms=0.0,
        total_turns=0, caller_audio_format="ulaw", codec_alignment_ok=True,
        barge_in_count=0, created_at=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_agent_slug_set_for_resolved_agent_routing():
    names = {"sales": "Sales Agent"}
    for rm in ("ai_agent", "ai_context", "default"):
        r = calls_api._record_to_summary_response(_rec(routing_method=rm), names)
        assert r.context_name == "sales"          # unchanged
        assert r.routing_method == rm              # unchanged -- still explains how
        assert r.agent_slug == "sales", rm
        assert r.agent_name == "Sales Agent", rm


def test_both_aliases_none_for_unresolved_routing():
    names = {"sales": "Sales Agent"}
    for rm in ("unknown", None):
        r = calls_api._record_to_response(_rec(routing_method=rm), names)
        assert r.agent_slug is None and r.agent_name is None, rm
        assert r.context_name == "sales"       # unchanged
        assert r.routing_method == rm           # unchanged


def test_aliases_null_when_no_context_or_no_match():
    r = calls_api._record_to_summary_response(_rec(context_name=None, routing_method=None), {})
    assert r.agent_slug is None and r.agent_name is None
    # ai_agent branch but no context_name -> both aliases stay null
    r_no_ctx = calls_api._record_to_response(_rec(context_name=None, routing_method="ai_agent"), {})
    assert r_no_ctx.agent_slug is None and r_no_ctx.agent_name is None
    r2 = calls_api._record_to_response(_rec(context_name="ghost", routing_method="ai_agent"), {})
    assert r2.agent_slug == "ghost"            # slug echoes context even with no name match
    assert r2.agent_name is None


def test_converters_work_without_a_name_map():
    # default arg path (e.g. CSV/legacy callers) must not raise and still set slug
    r = calls_api._record_to_response(_rec())
    assert r.agent_slug == "sales" and r.agent_name is None


def test_agent_name_map_never_raises(monkeypatch):
    # a broken / missing agents.db must degrade to an empty map, not an exception.
    # Use the realistic operational failures (locked DB, unreadable file): these
    # are swallowed, while genuine logic bugs are intentionally left to propagate.
    import sqlite3

    def _locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr("agents_store.AgentsStore", _locked, raising=False)
    assert calls_api._agent_name_map() == {}

    def _unreadable(*a, **k):
        raise OSError("permission denied")
    monkeypatch.setattr("agents_store.AgentsStore", _unreadable, raising=False)
    assert calls_api._agent_name_map() == {}
