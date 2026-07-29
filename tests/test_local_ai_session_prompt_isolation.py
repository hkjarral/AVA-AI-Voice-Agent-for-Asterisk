from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


LOCAL_AI_DIR = str(Path(__file__).resolve().parents[1] / "local_ai_server")


def _load(name: str):
    if LOCAL_AI_DIR not in sys.path:
        sys.path.insert(0, LOCAL_AI_DIR)
    return importlib.import_module(name)


class _WebSocket:
    def __init__(self):
        self.sent: list[dict] = []


class _Server:
    def __init__(self):
        self.ws_auth_token = ""
        self.config = SimpleNamespace(ws_host="127.0.0.1")
        self.model_manager = SimpleNamespace()

    async def _send_json(self, websocket, payload):
        websocket.sent.append(payload)


@pytest.mark.asyncio
async def test_session_scoped_prompts_are_isolated_and_reset_history():
    protocol_mod = _load("ws_protocol")
    session_mod = _load("session")
    server = _Server()
    protocol = protocol_mod.WebSocketProtocol(server)
    ws_a, ws_b = _WebSocket(), _WebSocket()
    session_a = session_mod.SessionContext(llm_messages=[{"role": "user", "content": "old"}])
    session_b = session_mod.SessionContext()

    await protocol.handle_json_message(
        ws_a,
        session_a,
        json.dumps({
            "type": "switch_model",
            "scope": "session",
            "call_id": "call-a",
            "request_id": "prompt-a",
            "llm_config": {"system_prompt": "You are agent A"},
        }),
    )
    await protocol.handle_json_message(
        ws_b,
        session_b,
        json.dumps({
            "type": "switch_model",
            "scope": "session",
            "call_id": "call-b",
            "llm_config": {"system_prompt": "You are agent B"},
        }),
    )

    assert session_a.system_prompt == "You are agent A"
    assert session_b.system_prompt == "You are agent B"
    assert session_a.llm_messages == []
    assert ws_a.sent[-1]["status"] == ws_b.sent[-1]["status"] == "success"
    assert ws_a.sent[-1]["request_id"] == "prompt-a"


@pytest.mark.asyncio
async def test_new_call_with_same_prompt_resets_conversation():
    protocol_mod = _load("ws_protocol")
    session_mod = _load("session")
    protocol = protocol_mod.WebSocketProtocol(_Server())
    ws = _WebSocket()
    session = session_mod.SessionContext(
        call_id="call-one",
        prompt_context_call_id="call-one",
        system_prompt="shared",
        llm_messages=[{"role": "assistant", "content": "private history"}],
    )

    await protocol.handle_json_message(
        ws,
        session,
        json.dumps({
            "type": "switch_model",
            "scope": "session",
            "call_id": "call-two",
            "llm_config": {"system_prompt": "shared"},
        }),
    )

    assert session.call_id == "call-two"
    assert session.llm_messages == []
