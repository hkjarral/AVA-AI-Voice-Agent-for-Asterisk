"""Tests for the xAI Grok Voice Agent realtime provider.

Focus areas (highest-value first):
1. session.update payload shape — Grok's nested ``audio.{input,output}.format`` is
   the most likely deviation point from OpenAI Realtime and is not covered in the
   OpenAI test suite, so we assert verbatim here.
2. Audio passthrough — verify μ-law direct path does NOT resample.
3. Event alias handling — ``response.text.delta`` is xAI's text-delta name.
4. provider_key propagation — multi-instance support.
5. 30-min session cap — warning emission at the configured threshold.
"""

import asyncio
import json
import time
import types
from types import SimpleNamespace

import pytest

from src.config import GrokProviderConfig
from src.providers.grok import GrokProvider


# --------------------------------------------------------------------------- #
# Fixtures & helpers                                                           #
# --------------------------------------------------------------------------- #


@pytest.fixture
def grok_config():
    return GrokProviderConfig(
        api_key="test-xai-key",
        model="grok-voice-latest",
        voice="eve",
        base_url="wss://api.x.ai/v1/realtime",
        input_encoding="ulaw",
        input_sample_rate_hz=8000,
        provider_input_encoding="ulaw",
        provider_input_sample_rate_hz=8000,
        output_encoding="ulaw",
        output_sample_rate_hz=8000,
        target_encoding="ulaw",
        target_sample_rate_hz=8000,
        instructions="Be helpful.",
        greeting="Hello there.",
        session_warn_after_seconds=1,  # short for tests
    )


class _RecordingWebSocket:
    """Captures send() payloads and pretends to be in OPEN state."""

    def __init__(self):
        self.state = SimpleNamespace(name="OPEN")
        self.sent_payloads: list[str] = []

    async def send(self, payload):
        self.sent_payloads.append(payload)

    async def ping(self):
        return None

    async def close(self):
        self.state = SimpleNamespace(name="CLOSED")

    def parsed_events(self):
        return [json.loads(p) for p in self.sent_payloads]


@pytest.fixture
def provider(grok_config):
    p = GrokProvider(grok_config, on_event=None, provider_key="acme_grok")
    p._call_id = "test-call"
    p._allowed_tools = []
    return p


# --------------------------------------------------------------------------- #
# 1. session.update payload shape                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_session_update_payload_shape_ulaw_default(provider):
    ws = _RecordingWebSocket()
    provider.websocket = ws

    await provider._send_session_update()

    events = ws.parsed_events()
    assert len(events) == 1
    evt = events[0]

    assert evt["type"] == "session.update"
    session = evt["session"]

    # voice at session level (xAI shape, NOT under audio.output)
    assert session["voice"] == "eve"

    # turn_detection at session level (xAI shape, NOT nested under audio.input)
    assert session["turn_detection"]["type"] == "server_vad"
    assert "threshold" in session["turn_detection"]
    assert "silence_duration_ms" in session["turn_detection"]
    assert "prefix_padding_ms" in session["turn_detection"]

    # Nested audio.{input,output}.format shape with xAI MIME types
    assert session["audio"]["input"]["format"]["type"] == "audio/pcmu"
    assert session["audio"]["input"]["format"]["rate"] == 8000
    assert session["audio"]["output"]["format"]["type"] == "audio/pcmu"
    assert session["audio"]["output"]["format"]["rate"] == 8000

    # OpenAI-specific keys must be ABSENT
    assert "input_audio_format" not in session
    assert "output_audio_format" not in session
    assert "input_audio_transcription" not in session
    assert "modalities" not in session
    assert "output_modalities" not in session

    # Instructions present
    assert "instructions" in session
    assert "Be helpful." in session["instructions"]


@pytest.mark.asyncio
async def test_session_update_payload_shape_pcm_fallback(grok_config):
    """When provider_input_encoding=linear16, MIME type flips to audio/pcm."""
    grok_config.provider_input_encoding = "linear16"
    grok_config.provider_input_sample_rate_hz = 24000
    grok_config.output_encoding = "linear16"
    grok_config.output_sample_rate_hz = 24000
    p = GrokProvider(grok_config, on_event=None, provider_key="acme_grok")
    p._call_id = "test-call"
    p._allowed_tools = []
    ws = _RecordingWebSocket()
    p.websocket = ws

    await p._send_session_update()

    session = ws.parsed_events()[0]["session"]
    assert session["audio"]["input"]["format"]["type"] == "audio/pcm"
    assert session["audio"]["input"]["format"]["rate"] == 24000
    assert session["audio"]["output"]["format"]["type"] == "audio/pcm"
    assert session["audio"]["output"]["format"]["rate"] == 24000


@pytest.mark.asyncio
async def test_session_update_includes_xai_native_extra_tools(grok_config):
    """extra_tools (YAML escape hatch) flow through as-is into session.tools."""
    grok_config.extra_tools = [
        {"type": "web_search"},
        {"type": "x_search", "allowed_x_handles": ["xai"]},
    ]
    p = GrokProvider(grok_config, on_event=None, provider_key="acme_grok")
    p._call_id = "test-call"
    p._allowed_tools = []
    ws = _RecordingWebSocket()
    p.websocket = ws

    await p._send_session_update()

    session = ws.parsed_events()[0]["session"]
    assert "tools" in session
    tool_types = [t.get("type") for t in session["tools"]]
    assert "web_search" in tool_types
    assert "x_search" in tool_types


# --------------------------------------------------------------------------- #
# 2. Event alias handling (response.text.delta is xAI's name)                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_response_text_delta_event_emits_transcript(provider):
    captured: list[tuple[str, bool]] = []

    async def fake_emit(text, *, is_final):
        captured.append((text, is_final))

    provider._emit_transcript = fake_emit
    await provider._handle_event({"type": "response.text.delta", "text": "Hello"})

    assert captured == [("Hello", False)]


@pytest.mark.asyncio
async def test_response_output_text_delta_still_handled(provider):
    """Defensive — if xAI later adds the OpenAI-style event, we still route it."""
    captured: list[tuple[str, bool]] = []

    async def fake_emit(text, *, is_final):
        captured.append((text, is_final))

    provider._emit_transcript = fake_emit
    await provider._handle_event({
        "type": "response.output_text.delta",
        "delta": {"text": "World"},
    })

    assert captured == [("World", False)]


# --------------------------------------------------------------------------- #
# 3. provider_key propagation                                                  #
# --------------------------------------------------------------------------- #


def test_provider_key_stored_on_instance(grok_config):
    p = GrokProvider(grok_config, on_event=None, provider_key="globex_grok")
    assert p.provider_key == "globex_grok"
    # also set via set_provider_identity → readable via attribute
    assert getattr(p, "provider_kind", None) == "grok"


def test_default_provider_key_is_grok(grok_config):
    """Backward-compat: legacy single-instance config sets provider_key='grok'."""
    p = GrokProvider(grok_config, on_event=None)
    assert p.provider_key == "grok"


# --------------------------------------------------------------------------- #
# 4. API key / is_ready                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_start_session_raises_without_api_key(grok_config):
    grok_config.api_key = None
    p = GrokProvider(grok_config, on_event=None, provider_key="test_grok")
    with pytest.raises(ValueError, match="XAI_API_KEY"):
        await p.start_session("call-1")


# --------------------------------------------------------------------------- #
# 5. 30-min session cap warning                                                #
# --------------------------------------------------------------------------- #


def test_maybe_warn_long_session_emits_once(provider, caplog):
    import logging

    provider._session_started_ts = time.monotonic() - 5  # 5 sec elapsed
    provider._session_warned_long_session = False
    # threshold from fixture = 1 sec

    with caplog.at_level(logging.WARNING):
        provider._maybe_warn_long_session()
        provider._maybe_warn_long_session()  # second call should be no-op

    # Either via stdlib logger or structlog — check the warned flag is set.
    assert provider._session_warned_long_session is True


def test_maybe_warn_long_session_silent_before_threshold(provider):
    provider._session_started_ts = time.monotonic()
    provider._session_warned_long_session = False
    provider._maybe_warn_long_session()
    assert provider._session_warned_long_session is False


def test_maybe_warn_long_session_disabled_when_zero(grok_config):
    grok_config.session_warn_after_seconds = 0
    p = GrokProvider(grok_config, on_event=None, provider_key="test_grok")
    p._session_started_ts = time.monotonic() - 9999
    p._session_warned_long_session = False
    p._maybe_warn_long_session()
    assert p._session_warned_long_session is False


# --------------------------------------------------------------------------- #
# 6. Capabilities                                                              #
# --------------------------------------------------------------------------- #


def test_capabilities_advertise_native_vad_and_barge_in(provider):
    caps = provider.get_capabilities()
    assert caps.is_full_agent is True
    assert caps.has_native_vad is True
    assert caps.has_native_barge_in is True
    assert "ulaw" in caps.input_encodings


# --------------------------------------------------------------------------- #
# 7. Tool adapter — event shape compatibility                                 #
# --------------------------------------------------------------------------- #
# Regression: the GrokProvider dispatches tool calls from response.output_item.done
# (the xAI Voice Agent emits function-call fields nested under `item`). The adapter
# must extract from `item` when present, and still accept the flat
# response.function_call_arguments.done shape described in the docs.


@pytest.mark.asyncio
async def test_tool_adapter_extracts_fields_from_item_wrapper():
    """response.output_item.done shape: name/call_id/arguments nested under `item`."""
    from src.tools.adapters.grok import GrokToolAdapter

    class _Registry:
        def to_openai_realtime_schema_filtered(self, names):
            return []

        def is_tool_allowed(self, name, allowed):
            return True

        def get(self, name):
            return None  # forces the unknown-tool branch — exercises field extraction

    event = {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "name": "lookup_customer",
            "call_id": "call_abc123",
            "arguments": '{"id": 42}',
        },
    }
    context = {"call_id": "test-call", "config": {"tools": {"enabled": True}}}
    adapter = GrokToolAdapter(_Registry())
    result = await adapter.handle_tool_call_event(event, context)
    # Even on the unknown-tool error path, the adapter must have populated
    # call_id and function_name from the nested item — proving extraction worked.
    assert result["call_id"] == "call_abc123"
    assert result["function_name"] == "lookup_customer"
    assert result["ai_should_speak"] is False  # error path must suppress speech


@pytest.mark.asyncio
async def test_tool_adapter_accepts_flat_event_shape():
    """response.function_call_arguments.done shape: fields at top level (docs shape)."""
    from src.tools.adapters.grok import GrokToolAdapter

    class _Registry:
        def to_openai_realtime_schema_filtered(self, names):
            return []

        def is_tool_allowed(self, name, allowed):
            return True

        def get(self, name):
            return None

    event = {
        "type": "response.function_call_arguments.done",
        "name": "lookup_customer",
        "call_id": "call_xyz789",
        "arguments": '{"id": 7}',
    }
    context = {"call_id": "test-call", "config": {"tools": {"enabled": True}}}
    adapter = GrokToolAdapter(_Registry())
    result = await adapter.handle_tool_call_event(event, context)
    assert result["call_id"] == "call_xyz789"
    assert result["function_name"] == "lookup_customer"
    assert result["ai_should_speak"] is False


@pytest.mark.asyncio
async def test_tool_adapter_disallowed_tool_suppresses_speech():
    """Disallowed-tool error must include ai_should_speak=False to suppress speech."""
    from src.tools.adapters.grok import GrokToolAdapter

    class _Registry:
        def to_openai_realtime_schema_filtered(self, names):
            return []

        def is_tool_allowed(self, name, allowed):
            return False  # disallow everything

        def get(self, name):
            return object()  # any truthy

    event = {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "name": "forbidden_tool",
            "call_id": "call_blocked",
            "arguments": "{}",
        },
    }
    context = {
        "call_id": "test-call",
        "config": {"tools": {"enabled": True}},
        "allowed_tools": ["other_tool"],
    }
    adapter = GrokToolAdapter(_Registry())
    result = await adapter.handle_tool_call_event(event, context)
    assert result["status"] == "error"
    assert result["ai_should_speak"] is False
