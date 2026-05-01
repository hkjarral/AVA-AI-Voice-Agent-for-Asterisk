import asyncio
import time
from types import SimpleNamespace

import pytest

from src.config import OpenAIRealtimeProviderConfig
import src.providers.openai_realtime as openai_realtime_module
from src.providers.openai_realtime import (
    OpenAIRealtimeProvider,
    _OPENAI_ASSUMED_OUTPUT_RATE,
    _OPENAI_MEASURED_OUTPUT_RATE,
    _OPENAI_PROVIDER_OUTPUT_RATE,
    _OPENAI_SESSION_AUDIO_INFO,
)


@pytest.fixture
def openai_config():
    return OpenAIRealtimeProviderConfig(
        api_key="test-key",
        model="gpt-test",
        voice="alloy",
        base_url="wss://api.openai.com/v1/realtime",
        input_encoding="ulaw",
        input_sample_rate_hz=8000,
        provider_input_encoding="linear16",
        provider_input_sample_rate_hz=24000,
        output_encoding="linear16",
        output_sample_rate_hz=24000,
        target_encoding="mulaw",
        target_sample_rate_hz=8000,
        response_modalities=["audio"],
    )


def _cleanup_metrics(call_id: str) -> None:
    return


def _function_call_event(response_id="resp-1", call_id="call-1", name="lookup"):
    return {
        "type": "response.output_item.done",
        "response_id": response_id,
        "item": {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": "{}",
        },
    }


def _response_done_event(response_id="resp-1"):
    return {
        "type": "response.done",
        "response": {"id": response_id},
    }


class _OpenWebSocket:
    state = SimpleNamespace(name="OPEN")


class _ToolAdapter:
    def __init__(self, *, result=None, error=None):
        self.result = {"status": "ok"} if result is None else result
        self.error = error
        self.sent_results = []

    async def handle_tool_call_event(self, event_data, context):
        if self.error:
            raise self.error
        return self.result

    async def send_tool_result(self, result, context):
        self.sent_results.append((result, context))


class _RecordingLogger:
    def __init__(self):
        self.warning_calls = []
        self.error_calls = []
        self.debug_calls = []
        self.info_calls = []

    def warning(self, *args, **kwargs):
        self.warning_calls.append((args, kwargs))

    def error(self, *args, **kwargs):
        self.error_calls.append((args, kwargs))

    def debug(self, *args, **kwargs):
        self.debug_calls.append((args, kwargs))

    def info(self, *args, **kwargs):
        self.info_calls.append((args, kwargs))


def test_output_rate_drift_adjusts_active_rate(openai_config):
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    call_id = "call-test"
    provider._call_id = call_id
    provider._reset_output_meter()

    # Simulate 2 seconds of runtime before first chunk is processed
    provider._output_meter_start_ts = time.monotonic() - 2.0
    provider._output_meter_last_log_ts = provider._output_meter_start_ts

    # Feed enough bytes to represent ~9 kHz PCM16 audio over the 2 second window.
    provider._update_output_meter(36000)

    try:
        assert provider._output_rate_warned is True
        # Measured bytes/time reflects real-time playback pacing, not PCM sample rate.
        # Provider should keep the configured sample rate for correct resampling.
        assert provider._active_output_sample_rate_hz is not None
        assert provider._active_output_sample_rate_hz == pytest.approx(openai_config.output_sample_rate_hz)
    finally:
        _cleanup_metrics(call_id)


@pytest.mark.asyncio
async def test_session_requests_pcm_when_ga_mode(openai_config):
    """GA mode uses nested audio.output.format with MIME types, not flat output_audio_format."""
    openai_config.api_version = "ga"
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    captured = {}

    async def fake_send(payload):
        captured.update(payload)

    provider._send_json = fake_send  # type: ignore

    await provider._send_session_update()

    session = captured.get("session", {})
    # GA mode: no flat output_audio_format key
    assert "output_audio_format" not in session
    # GA mode: nested audio.output.format with MIME type
    audio_output = session.get("audio", {}).get("output", {})
    assert audio_output.get("format", {}).get("type") == "audio/pcm"
    assert audio_output.get("format", {}).get("rate") == 24000
    # Provider internal state defaults to pcm16 until ACK
    assert provider._provider_output_format == "pcm16"
    assert provider._session_output_bytes_per_sample == 2


@pytest.mark.asyncio
async def test_session_requests_g711_when_beta_mode():
    """Beta mode uses flat output_audio_format string tokens."""
    beta_config = OpenAIRealtimeProviderConfig(
        api_key="test-key",
        api_version="beta",
        model="gpt-4o-realtime-preview",
        voice="alloy",
        base_url="wss://api.openai.com/v1/realtime",
        input_encoding="ulaw",
        input_sample_rate_hz=8000,
        provider_input_encoding="linear16",
        provider_input_sample_rate_hz=24000,
        output_encoding="linear16",
        output_sample_rate_hz=24000,
        target_encoding="mulaw",
        target_sample_rate_hz=8000,
        response_modalities=["audio"],
    )
    provider = OpenAIRealtimeProvider(beta_config, on_event=None)
    captured = {}

    async def fake_send(payload):
        captured.update(payload)

    provider._send_json = fake_send  # type: ignore

    await provider._send_session_update()

    session = captured.get("session", {})
    # Beta mode: flat string token
    assert session.get("output_audio_format") == "pcm16"
    assert provider._provider_output_format == "pcm16"
    assert provider._session_output_bytes_per_sample == 2


@pytest.mark.asyncio
async def test_function_call_event_registers_shared_response_done_sentinel(openai_config):
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    handled = []

    async def fake_handle(event):
        handled.append(event)

    provider._handle_function_call = fake_handle

    await provider._handle_event(_function_call_event("resp-shared", "call-a"))
    first_sentinel = provider._response_done_events["resp-shared"]
    await provider._handle_event(_function_call_event("resp-shared", "call-b"))

    assert provider._response_done_events["resp-shared"] is first_sentinel
    assert provider._is_recent_tool_call_id("call-a") is True
    assert provider._is_recent_tool_call_id("call-b") is True

    await asyncio.sleep(0)
    assert [event["item"]["call_id"] for event in handled] == ["call-a", "call-b"]


@pytest.mark.asyncio
async def test_response_done_signals_and_removes_sentinel(openai_config):
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)

    async def fake_handle(event):
        return None

    provider._handle_function_call = fake_handle

    await provider._handle_event(_function_call_event("resp-done", "call-a"))
    sentinel = provider._response_done_events["resp-done"]

    await provider._handle_event(_response_done_event("resp-done"))

    assert sentinel.is_set()
    assert "resp-done" not in provider._response_done_events


@pytest.mark.asyncio
async def test_await_parent_response_done_returns_after_sentinel_is_set(openai_config):
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    sentinel = asyncio.Event()
    provider._response_done_events["resp-ready"] = sentinel
    sentinel.set()

    await asyncio.wait_for(
        provider._await_parent_response_done(
            _function_call_event("resp-ready", "call-ready"),
            timeout=0.1,
        ),
        timeout=0.2,
    )


@pytest.mark.asyncio
async def test_await_parent_response_done_times_out_without_raising(openai_config):
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    provider._response_done_events["resp-missing"] = asyncio.Event()

    await asyncio.wait_for(
        provider._await_parent_response_done(
            _function_call_event("resp-missing", "call-timeout"),
            timeout=0.01,
        ),
        timeout=0.2,
    )


@pytest.mark.asyncio
async def test_stop_session_releases_pending_response_done_sentinels(openai_config):
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    sentinel = asyncio.Event()
    provider._response_done_events["resp-stop"] = sentinel

    await provider.stop_session()

    assert sentinel.is_set()
    assert provider._response_done_events == {}


@pytest.mark.asyncio
async def test_reconnect_releases_pending_response_done_sentinels(openai_config):
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    sentinel = asyncio.Event()
    provider._call_id = "call-reconnect"
    provider._closing = True
    provider._response_done_events["resp-reconnect"] = sentinel

    await provider._reconnect_with_backoff()

    assert sentinel.is_set()
    assert provider._response_done_events == {}


def test_recent_tool_call_ids_respect_ttl_and_evict_expired(openai_config, monkeypatch):
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    provider._recent_tool_call_id_ttl_s = 30.0
    now = 100.0
    monkeypatch.setattr(openai_realtime_module.time, "monotonic", lambda: now)

    provider._recent_tool_call_ids["old-call"] = 69.0
    provider._record_recent_tool_call_id("new-call")

    assert "old-call" not in provider._recent_tool_call_ids
    assert provider._is_recent_tool_call_id("new-call") is True

    now = 131.0

    assert provider._is_recent_tool_call_id("new-call") is False


@pytest.mark.asyncio
async def test_invalid_tool_call_id_is_downgraded_only_for_recent_call_ids(
    openai_config, monkeypatch
):
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    provider._record_recent_tool_call_id("call-known")
    logger = _RecordingLogger()
    monkeypatch.setattr(openai_realtime_module, "logger", logger)

    await provider._handle_event(
        {
            "type": "error",
            "error": {
                "code": "invalid_tool_call_id",
                "message": "Tool call ID 'call-known' not found in conversation.",
            },
        }
    )
    await provider._handle_event(
        {
            "type": "error",
            "error": {
                "code": "invalid_tool_call_id",
                "message": "Tool call ID 'call-unknown' not found in conversation.",
            },
        }
    )

    assert len(logger.warning_calls) == 1
    assert logger.warning_calls[0][1]["rejected_call_id"] == "call-known"
    assert len(logger.error_calls) == 1
    assert logger.error_calls[0][1]["rejected_call_id"] == "call-unknown"


@pytest.mark.asyncio
async def test_success_tool_result_waits_for_parent_response_done(openai_config):
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    adapter = _ToolAdapter(result={"status": "ok", "message": "done"})
    sentinel = asyncio.Event()
    provider.websocket = _OpenWebSocket()
    provider.tool_adapter = adapter
    provider._response_done_events["resp-gated"] = sentinel

    task = asyncio.create_task(
        provider._handle_function_call(_function_call_event("resp-gated", "call-gated"))
    )
    await asyncio.sleep(0.05)

    assert adapter.sent_results == []

    sentinel.set()
    await asyncio.wait_for(task, timeout=0.5)

    assert len(adapter.sent_results) == 1
    assert adapter.sent_results[0][0]["status"] == "ok"


@pytest.mark.asyncio
async def test_error_tool_output_waits_for_parent_response_done(openai_config):
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    sentinel = asyncio.Event()
    sent_payloads = []
    provider.websocket = _OpenWebSocket()
    provider.tool_adapter = _ToolAdapter(error=RuntimeError("boom"))
    provider._response_done_events["resp-error"] = sentinel

    async def fake_send_json(payload):
        sent_payloads.append(payload)

    provider._send_json = fake_send_json

    task = asyncio.create_task(
        provider._handle_function_call(_function_call_event("resp-error", "call-error"))
    )
    await asyncio.sleep(0.05)

    assert sent_payloads == []

    sentinel.set()
    await asyncio.wait_for(task, timeout=0.5)

    assert sent_payloads == [
        {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": "call-error",
                "output": (
                    '{"status": "error", "message": "Tool execution failed: boom", '
                    '"error": "boom"}'
                ),
            },
        }
    ]
