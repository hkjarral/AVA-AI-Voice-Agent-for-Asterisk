import asyncio
import types
from unittest.mock import AsyncMock

import pytest

from src.engine import Engine


def _engine_with_local_farewell(mode: str) -> Engine:
    engine = Engine.__new__(Engine)
    engine.provider_kinds = {"local": "local"}
    engine.config = types.SimpleNamespace(
        providers={"local": {"farewell_mode": mode}}
    )
    return engine


def test_resolve_local_farewell_settings_supports_dict_config():
    mode, timeout_sec = Engine._resolve_local_farewell_settings(
        {
            "farewell_mode": "tts",
            "farewell_timeout_sec": "12.5",
        }
    )

    assert mode == "tts"
    assert timeout_sec == 12.5


def test_resolve_local_farewell_settings_supports_object_config():
    local_config = types.SimpleNamespace(
        farewell_mode="TTS",
        farewell_timeout_sec=8,
    )

    mode, timeout_sec = Engine._resolve_local_farewell_settings(local_config)

    assert mode == "tts"
    assert timeout_sec == 8.0


@pytest.mark.parametrize("raw_mode", ["", "invalid", "whisper"])
def test_resolve_local_farewell_settings_rejects_unknown_mode(raw_mode: str):
    mode, timeout_sec = Engine._resolve_local_farewell_settings(
        {
            "farewell_mode": raw_mode,
            "farewell_timeout_sec": 3,
        }
    )

    assert mode == "asterisk"
    assert timeout_sec == 3.0


@pytest.mark.parametrize("raw_timeout", ["", "nope", -1, 0])
def test_resolve_local_farewell_settings_rejects_invalid_timeout(raw_timeout):
    mode, timeout_sec = Engine._resolve_local_farewell_settings(
        {
            "farewell_mode": "tts",
            "farewell_timeout_sec": raw_timeout,
        }
    )

    assert mode == "tts"
    assert timeout_sec == 30.0


def test_resolve_local_farewell_settings_defaults_when_missing():
    mode, timeout_sec = Engine._resolve_local_farewell_settings(None)

    assert mode == "asterisk"
    assert timeout_sec == 30.0


def test_asterisk_local_farewell_skips_redundant_hangup_result():
    engine = _engine_with_local_farewell("asterisk")

    assert not engine._should_send_provider_tool_result(
        provider_name="local",
        function_name="hangup_call",
        result={"will_hangup": True},
    )


def test_local_tts_farewell_keeps_hangup_result_turn():
    engine = _engine_with_local_farewell("tts")

    assert engine._should_send_provider_tool_result(
        provider_name="local",
        function_name="hangup_call",
        result={"will_hangup": True},
    )


def test_local_nonterminal_tool_keeps_result_turn():
    engine = _engine_with_local_farewell("asterisk")

    assert engine._should_send_provider_tool_result(
        provider_name="local",
        function_name="request_transcript",
        result={"status": "success"},
    )


def test_named_local_provider_uses_its_own_farewell_settings():
    engine = Engine.__new__(Engine)
    engine.provider_kinds = {"local-gpu": "local"}
    engine.config = types.SimpleNamespace(
        providers={
            "local": {"farewell_mode": "asterisk"},
            "local-gpu": {"farewell_mode": "tts"},
        }
    )

    assert engine._should_send_provider_tool_result(
        provider_name="local-gpu",
        function_name="hangup_call",
        result={"will_hangup": True},
    )


@pytest.mark.asyncio
async def test_local_farewell_fallback_clears_boundary_and_marks_agent_hangup():
    engine = Engine.__new__(Engine)
    session = types.SimpleNamespace(cleanup_after_tts=True)
    engine.session_store = types.SimpleNamespace(
        get_by_call_id=AsyncMock(return_value=session)
    )
    engine._local_tts_farewell_pending = {"call-timeout"}
    engine._terminal_fallback_tasks = {}
    engine._terminate_call_after_audio = AsyncMock(return_value=True)

    engine._schedule_terminal_fallback(
        "call-timeout",
        reason="local:farewell",
        timeout_sec=0,
        call_outcome="agent_hangup",
        clear_local_farewell_pending=True,
    )
    await asyncio.sleep(1.05)

    assert "call-timeout" not in engine._local_tts_farewell_pending
    engine._terminate_call_after_audio.assert_awaited_once_with(
        "call-timeout",
        reason="local:farewell:fallback_timeout",
        call_outcome="agent_hangup",
    )
