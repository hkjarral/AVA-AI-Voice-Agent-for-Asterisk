from __future__ import annotations

import importlib
import sys
from pathlib import Path


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


def test_other_tools_keep_serial_gateway_path():
    server, session = _server_and_session(["hangup_call", "transfer"])
    assert server._tool_gateway_blocks_streaming(session, "What are your hours?") is True


def test_disabled_gateway_never_blocks_streaming():
    server, session = _server_and_session(["transfer"])
    server.tool_gateway_enabled = False
    assert server._tool_gateway_blocks_streaming(session, "Transfer me") is False
