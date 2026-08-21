from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


LOCAL_AI_DIR = str(Path(__file__).resolve().parents[1] / "local_ai_server")


def _load(name: str):
    if LOCAL_AI_DIR not in sys.path:
        sys.path.insert(0, LOCAL_AI_DIR)
    return importlib.import_module(name)


def _server_and_session(tools):
    server_mod = _load("server")
    session_mod = _load("session")
    server = object.__new__(server_mod.LocalAIServer)
    server.tool_gateway_enabled = True
    return server, session_mod.SessionContext(allowed_tools=tools)


def test_hangup_only_normal_turn_can_stream():
    server, session = _server_and_session(["hangup_call"])
    assert server._tool_gateway_blocks_streaming(session, "How do I install Ava?") is False


def test_hangup_only_end_turn_remains_server_gated():
    server, session = _server_and_session(["hangup_call"])
    assert server._tool_gateway_blocks_streaming(session, "Thank you, goodbye") is True


def test_metalinguistic_goodbye_does_not_trigger_hangup():
    server, _session = _server_and_session(["hangup_call"])
    assert server._text_has_end_call_intent("Reply with the word goodbye") is False
    assert server._text_has_end_call_intent("Goodbye with the only word") is False
    assert server._text_has_end_call_intent("I did not say goodbye") is False


def test_explicit_hangup_and_plain_goodbye_remain_terminal():
    server, _session = _server_and_session(["hangup_call"])
    assert server._text_has_end_call_intent("Please hang up now") is True
    assert server._text_has_end_call_intent("That's all. Goodbye.") is True


def test_other_tools_keep_serial_gateway_path():
    server, session = _server_and_session(["hangup_call", "transfer"])
    assert server._tool_gateway_blocks_streaming(session, "What are your hours?") is True


def test_disabled_gateway_never_blocks_streaming():
    server, session = _server_and_session(["transfer"])
    server.tool_gateway_enabled = False
    assert server._tool_gateway_blocks_streaming(session, "Transfer me") is False


def test_session_markers_are_used_instead_of_global_defaults():
    server, session = _server_and_session(["hangup_call"])
    session.hangup_end_call_markers = ["да", "нет"]
    assert server._tool_gateway_blocks_streaming(session, "да") is True
    assert server._tool_gateway_blocks_streaming(session, "goodbye") is False


@pytest.mark.asyncio
async def test_tool_context_resets_markers_between_agents_on_reused_session():
    server, session = _server_and_session([])
    await server._handle_tool_context(
        None,
        session,
        {
            "type": "tool_context",
            "call_id": "russian-call",
            "allowed_tools": ["hangup_call"],
            "hangup_policy": {"markers": {"end_call": ["да", "нет"]}},
            "hangup_marker_source": "agent_replace",
        },
    )
    assert server._tool_gateway_blocks_streaming(session, "да") is True
    assert session.hangup_marker_source == "agent_replace"

    await server._handle_tool_context(
        None,
        session,
        {
            "type": "tool_context",
            "call_id": "english-call",
            "allowed_tools": ["hangup_call"],
        },
    )
    assert session.hangup_end_call_markers is None
    assert server._tool_gateway_blocks_streaming(session, "да") is False
    assert server._tool_gateway_blocks_streaming(session, "goodbye") is True


@pytest.mark.asyncio
async def test_invalid_session_marker_policy_disables_hangup_call():
    server, session = _server_and_session([])
    await server._handle_tool_context(
        None,
        session,
        {
            "type": "tool_context",
            "call_id": "invalid-policy",
            "allowed_tools": ["hangup_call", "request_transcript"],
            "hangup_policy": {"markers": {"end_call": "да"}},
        },
    )
    assert session.hangup_marker_source == "invalid"
    assert session.hangup_end_call_markers == []
    assert session.allowed_tools == ["request_transcript"]


@pytest.mark.asyncio
async def test_call_id_mismatch_clears_stale_session_markers_without_new_context():
    server, session = _server_and_session([])
    await server._handle_tool_context(
        None,
        session,
        {
            "type": "tool_context",
            "call_id": "russian-call",
            "allowed_tools": ["hangup_call"],
            "hangup_policy": {"markers": {"end_call": ["да", "нет"]}},
        },
    )
    server._build_llm_tool_response_payload = AsyncMock(return_value={"type": "llm_tool_response"})
    server._send_json = AsyncMock()

    await server._handle_llm_tool_request(
        None,
        session,
        {"type": "llm_tool_request", "call_id": "english-call", "text": "hello"},
    )

    assert session.allowed_tools == []
    assert session.hangup_end_call_markers is None
    assert session.hangup_marker_source == "global"
    assert session.hangup_marker_digest is None
