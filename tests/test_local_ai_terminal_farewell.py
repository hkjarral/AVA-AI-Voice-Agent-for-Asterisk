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


@pytest.mark.asyncio
async def test_hangup_tool_result_speaks_exact_farewell_without_second_llm_turn():
    server_mod = _load("server")
    session_mod = _load("session")
    server = object.__new__(server_mod.LocalAIServer)
    server.process_tts = AsyncMock(return_value=b"farewell-audio")
    server._emit_llm_response = AsyncMock(return_value=True)
    server._emit_tts_audio = AsyncMock()
    session = session_mod.SessionContext(call_id="call-farewell")

    await server._handle_tool_result(
        websocket=object(),
        session=session,
        data={
            "type": "tool_result",
            "call_id": "call-farewell",
            "request_id": "tool-result-123",
            "function_call_id": "local-hangup_call",
            "tool_name": "hangup_call",
            "result": {
                "status": "success",
                "farewell_message": "Thank you for calling Ava. Goodbye!",
                "will_hangup": True,
            },
        },
    )

    server.process_tts.assert_awaited_once_with("Thank you for calling Ava. Goodbye!")
    assert session.llm_messages[-1] == {
        "role": "assistant",
        "content": "Thank you for calling Ava. Goodbye!",
    }
    llm_args = server._emit_llm_response.await_args.args
    assert llm_args[1] == "Thank you for calling Ava. Goodbye!"
    tts_kwargs = server._emit_tts_audio.await_args.kwargs
    assert tts_kwargs["source_mode"] == "tool_result"
