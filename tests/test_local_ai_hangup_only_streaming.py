from __future__ import annotations

import hashlib
import importlib
import json
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


def _scoped_marker_fields(markers):
    normalized = [" ".join(marker.strip().lower().split()) for marker in markers]
    digest = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:16]
    return {
        "protocol_version": _load("constants").PROTOCOL_VERSION,
        "hangup_marker_digest": digest,
    }


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


def test_replace_markers_do_not_retain_explicit_english_commands():
    server, _session = _server_and_session(["hangup_call"])
    assert server._text_has_end_call_intent("Please end call", ["да"]) is False
    assert server._text_has_end_call_intent("да", ["да"]) is True


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
            **_scoped_marker_fields(["да", "нет"]),
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
            "protocol_version": _load("constants").PROTOCOL_VERSION,
            "hangup_marker_digest": "0123456789abcdef",
        },
    )
    assert session.hangup_marker_source == "invalid"
    assert session.hangup_end_call_markers == []
    assert session.allowed_tools == ["request_transcript"]


@pytest.mark.asyncio
async def test_empty_session_marker_policy_disables_hangup_call():
    server, session = _server_and_session([])
    await server._handle_tool_context(
        None,
        session,
        {
            "type": "tool_context",
            "call_id": "empty-policy",
            "allowed_tools": ["hangup_call", "request_transcript"],
            "hangup_policy": {"markers": {"end_call": []}},
            "protocol_version": _load("constants").PROTOCOL_VERSION,
            "hangup_marker_digest": "0123456789abcdef",
        },
    )
    assert session.hangup_marker_source == "invalid"
    assert session.hangup_end_call_markers == []
    assert session.allowed_tools == ["request_transcript"]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol_version", [None, 999])
async def test_scoped_markers_with_unsupported_protocol_disable_hangup_call(
    protocol_version,
):
    server, session = _server_and_session([])
    payload = {
        "type": "tool_context",
        "call_id": "bad-version",
        "allowed_tools": ["hangup_call", "request_transcript"],
        "hangup_policy": {"markers": {"end_call": ["да"]}},
        **_scoped_marker_fields(["да"]),
    }
    payload["protocol_version"] = protocol_version

    await server._handle_tool_context(None, session, payload)

    assert session.hangup_marker_source == "invalid"
    assert session.hangup_end_call_markers == []
    assert session.allowed_tools == ["request_transcript"]


@pytest.mark.asyncio
async def test_scoped_markers_with_mismatched_digest_disable_hangup_call():
    server, session = _server_and_session([])
    await server._handle_tool_context(
        None,
        session,
        {
            "type": "tool_context",
            "call_id": "bad-digest",
            "allowed_tools": ["hangup_call", "request_transcript"],
            "hangup_policy": {"markers": {"end_call": ["да"]}},
            "protocol_version": _load("constants").PROTOCOL_VERSION,
            "hangup_marker_digest": "0123456789abcdef",
        },
    )

    assert session.hangup_marker_source == "invalid"
    assert session.hangup_end_call_markers == []
    assert session.hangup_marker_digest is None
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
            **_scoped_marker_fields(["да", "нет"]),
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


@pytest.mark.asyncio
async def test_audio_call_id_mismatch_clears_stale_session_markers_without_new_context():
    server, session = _server_and_session([])
    await server._handle_tool_context(
        None,
        session,
        {
            "type": "tool_context",
            "call_id": "russian-call",
            "allowed_tools": ["hangup_call"],
            "hangup_policy": {"markers": {"end_call": ["да", "нет"]}},
            **_scoped_marker_fields(["да", "нет"]),
        },
    )

    await server._handle_audio_payload(
        None,
        session,
        {"type": "audio", "call_id": "english-call", "mode": "stt", "data": ""},
    )

    assert session.call_id == "english-call"
    assert session.allowed_tools == []
    assert session.tool_schemas == []
    assert session.tool_policy == "auto"
    assert session.hangup_end_call_markers is None
    assert session.hangup_marker_source == "global"
    assert session.hangup_marker_digest is None
    assert session.tool_context_call_id is None
