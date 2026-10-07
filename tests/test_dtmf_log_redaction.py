"""DTMF log privacy regressions for the native ARI and AudioSocket paths."""

import asyncio
import copy
import io
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import structlog

import src.ari_client as ari_module
import src.audio.audiosocket_server as audio_module
import src.engine as engine_module
from src.ari_client import ARIClient
from src.audio.audiosocket_server import AudioSocketServer
from src.engine import Engine
from src.logging_config import sanitize_secrets


class _LogCapture:
    def __init__(self):
        self.records = []

    def _add(self, level, event, **fields):
        self.records.append({"level": level, "event": event, **fields})

    def info(self, event, **fields):
        self._add("info", event, **fields)

    def warning(self, event, **fields):
        self._add("warning", event, **fields)

    def debug(self, event, **fields):
        self._add("debug", event, **fields)

    def error(self, event, **fields):
        self._add("error", event, **fields)


@pytest.fixture
def logs(monkeypatch):
    capture = _LogCapture()
    for module in (ari_module, audio_module, engine_module):
        monkeypatch.setattr(module, "logger", capture)
    return capture


@pytest.mark.asyncio
async def test_ari_decision_digit_reaches_waiter_and_session_but_not_logs(logs):
    engine = Engine.__new__(Engine)
    engine._attended_transfer_agent_channel_to_call_id = {"agent-channel": "test-call"}
    engine._attended_transfer_dtmf_digits = {}
    waiter = asyncio.get_running_loop().create_future()
    engine._attended_transfer_dtmf_waiters = {"agent-channel": waiter}
    session = SimpleNamespace(current_action={"type": "attended_transfer"})
    engine.session_store = SimpleNamespace(get_by_call_id=AsyncMock(return_value=session))
    engine._save_session = AsyncMock()

    event = {"channel": {"id": "agent-channel"}, "digit": "9"}
    before = copy.deepcopy(event)
    await engine._handle_dtmf_received(event)

    assert event == before
    assert waiter.result() == "9"
    assert engine._attended_transfer_dtmf_digits["agent-channel"] == "9"
    assert session.current_action["decision_digit"] == "9"
    engine._save_session.assert_awaited_once_with(session)
    assert all("digit" not in row for row in logs.records)
    assert '"9"' not in json.dumps(logs.records)


@pytest.mark.asyncio
async def test_malformed_ari_message_is_not_logged_and_valid_event_dispatches(logs):
    client = ARIClient.__new__(ARIClient)
    client._should_reconnect = True
    client._connected = True
    client.running = True
    client._connection_generation = 0
    event = {"type": "ChannelDtmfReceived", "channel": {"id": "test-channel"}, "digit": "9"}
    received = []

    async def handler(value):
        received.append(value)

    class _WebSocket:
        async def __aiter__(self):
            yield json.dumps(event)[:-1]
            yield json.dumps(event)
            await asyncio.sleep(0)
            client._should_reconnect = False

        async def close(self):
            pass

    client.websocket = _WebSocket()
    client.event_handlers = {"ChannelDtmfReceived": [handler]}
    await client._listen_with_reconnect()

    assert received == [event]
    warning = next(row for row in logs.records if row["event"] == "Failed to decode ARI event JSON")
    assert warning["error_type"] == "JSONDecodeError"
    assert "message" not in warning
    assert '"digit"' not in json.dumps(logs.records)


@pytest.mark.asyncio
async def test_audiosocket_callback_still_runs_without_leaking_exception_payload(logs):
    callback = AsyncMock(side_effect=RuntimeError("sensitive digit 9"))
    disconnected = AsyncMock()
    server = AudioSocketServer(
        "::",
        0,
        on_uuid=AsyncMock(return_value=True),
        on_audio=AsyncMock(),
        on_dtmf=callback,
        on_disconnect=disconnected,
    )
    reader = asyncio.StreamReader()
    call_uuid = uuid.uuid4().bytes
    digit = b"9"
    reader.feed_data(bytes([audio_module.TYPE_UUID]) + len(call_uuid).to_bytes(2, "big") + call_uuid)
    reader.feed_data(bytes([audio_module.TYPE_DTMF]) + len(digit).to_bytes(2, "big") + digit)
    reader.feed_eof()
    writer = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())

    await server._connection_loop("test-connection", reader, writer)

    callback.assert_awaited_once()
    disconnected.assert_awaited_once()
    error = next(row for row in logs.records if row["event"] == "AudioSocket connection error")
    assert error["error_type"] == "RuntimeError"
    assert "error" not in error and "exc_info" not in error
    assert "sensitive digit 9" not in json.dumps(logs.records)


@pytest.mark.parametrize(
    "key",
    ["digit", "digits", "dtmf", "DTMF_Digit", "consent_dtmf", "decision-digit"],
)
def test_structured_dtmf_values_are_fully_redacted_without_mutating_input(key):
    event = {"event": "call", "payload": [{"details": {key: "987654"}}], "duration_ms": 5}
    before = copy.deepcopy(event)
    sanitized = sanitize_secrets(None, "info", event)

    assert event == before
    assert sanitized["payload"][0]["details"][key] == "***REDACTED***"
    assert sanitized["duration_ms"] == 5
    stream = io.StringIO()
    render = structlog.wrap_logger(
        structlog.PrintLogger(stream),
        processors=[sanitize_secrets, structlog.processors.JSONRenderer()],
    )
    render.info("call", payload=event["payload"])
    assert "987654" not in stream.getvalue()
    assert "98***REDACTED***" not in stream.getvalue()
