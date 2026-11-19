"""Tests for the MiniMax LLM pipeline adapter."""

import json
from unittest.mock import patch, MagicMock

import pytest

from src.config import AppConfig, MiniMaxLLMProviderConfig
from src.pipelines.minimax import MiniMaxLLMAdapter, _clamp_temperature
from src.tools.base import ToolPhase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_app_config() -> AppConfig:
    providers = {
        "minimax_llm": {
            "api_key": "test-minimax-key",
            "chat_base_url": "https://api.minimax.io/v1",
            "chat_model": "MiniMax-M3",
            "temperature": 1.0,
            "response_timeout_sec": 10.0,
            "type": "minimax",
        }
    }
    pipelines = {
        "minimax_stack": {
            "stt": "openai_stt",
            "llm": "minimax_llm",
            "tts": "openai_tts",
            "options": {
                "llm": {"temperature": 0.8},
            },
        }
    }
    return AppConfig(
        default_provider="minimax",
        providers=providers,
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "You are a helpful assistant.", "model": "MiniMax-M3"},
        audio_transport="audiosocket",
        downstream_mode="stream",
        pipelines=pipelines,
        active_pipeline="minimax_stack",
    )


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self):
        return self._body

    async def text(self):
        return self._body.decode("utf-8", errors="ignore")

    def raise_for_status(self):
        if self.status >= 400:
            raise Exception(f"HTTP {self.status}")


class _FakeSession:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self._status = status
        self.requests = []
        self.closed = False

    def post(self, url, json=None, data=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json, "data": data, "headers": headers, "timeout": timeout})
        return _FakeResponse(self._body, status=self._status)

    def get(self, url, headers=None, timeout=None):
        return _FakeResponse(self._body, status=self._status)

    async def close(self):
        self.closed = True


class _SequencedFakeSession:
    """Fake session that returns different responses for successive POST calls."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self._call_index = 0
        self.requests = []
        self.closed = False

    def post(self, url, json=None, data=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json, "data": data, "headers": headers, "timeout": timeout})
        body, status = self._responses[min(self._call_index, len(self._responses) - 1)]
        self._call_index += 1
        return _FakeResponse(body, status=status)

    def get(self, url, headers=None, timeout=None):
        return _FakeResponse(b"{}", status=200)

    async def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Unit Tests
# ---------------------------------------------------------------------------

class TestClampTemperature:
    def test_zero_clamped(self):
        assert _clamp_temperature(0) == 0.01

    def test_negative_clamped(self):
        assert _clamp_temperature(-1.0) == 0.01

    def test_over_one_clamped(self):
        assert _clamp_temperature(1.5) == 1.0

    def test_valid_value_passthrough(self):
        assert _clamp_temperature(0.7) == 0.7

    def test_one_passthrough(self):
        assert _clamp_temperature(1.0) == 1.0

    def test_none_defaults(self):
        assert _clamp_temperature(None) == 1.0


@pytest.mark.asyncio
async def test_minimax_llm_chat_completion():
    """MiniMax adapter returns correct text from chat completion response."""
    app_config = _build_app_config()
    provider_config = MiniMaxLLMProviderConfig(**app_config.providers["minimax_llm"])
    body = json.dumps({
        "choices": [{"message": {"content": "Hello! How can I help?"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8},
    }).encode("utf-8")
    fake_session = _FakeSession(body)

    adapter = MiniMaxLLMAdapter(
        "minimax_llm",
        app_config,
        provider_config,
        {},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    summary_prompt = "Summarize the call. Do not use the agent persona."
    response = await adapter.generate(
        "call-1",
        "hello",
        {"system_prompt": summary_prompt},
        {"system_prompt": summary_prompt, "instructions": summary_prompt},
    )
    assert response.text == "Hello! How can I help?"
    assert response.tool_calls == []

    request = fake_session.requests[0]
    assert request["json"]["model"] == "MiniMax-M3"
    assert request["json"]["messages"][0] == {"role": "system", "content": summary_prompt}
    assert request["url"] == "https://api.minimax.io/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer test-minimax-key"


@pytest.mark.asyncio
async def test_minimax_llm_temperature_clamped():
    """Temperature is clamped to (0, 1] for MiniMax API."""
    app_config = _build_app_config()
    provider_config = MiniMaxLLMProviderConfig(**app_config.providers["minimax_llm"])
    body = json.dumps({
        "choices": [{"message": {"content": "ok"}}],
    }).encode("utf-8")
    fake_session = _FakeSession(body)

    adapter = MiniMaxLLMAdapter(
        "minimax_llm",
        app_config,
        provider_config,
        {"temperature": 0},  # Should be clamped to 0.01
        session_factory=lambda: fake_session,
    )
    await adapter.start()
    await adapter.generate("call-1", "test", {}, {})
    payload = fake_session.requests[0]["json"]
    assert payload["temperature"] == 0.01


@pytest.mark.asyncio
async def test_minimax_llm_tool_calls():
    """Adapter parses tool calls from MiniMax response."""
    app_config = _build_app_config()
    provider_config = MiniMaxLLMProviderConfig(**app_config.providers["minimax_llm"])
    body = json.dumps({
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city": "Tokyo"}',
                    },
                }],
            },
        }],
    }).encode("utf-8")
    fake_session = _FakeSession(body)

    adapter = MiniMaxLLMAdapter(
        "minimax_llm",
        app_config,
        provider_config,
        {},
        session_factory=lambda: fake_session,
    )
    await adapter.start()
    response = await adapter.generate("call-1", "weather in Tokyo", {}, {})
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0]["name"] == "get_weather"
    assert response.tool_calls[0]["parameters"] == {"city": "Tokyo"}


@pytest.mark.asyncio
async def test_minimax_llm_strips_thinking():
    """Adapter strips <think> blocks from response content."""
    app_config = _build_app_config()
    provider_config = MiniMaxLLMProviderConfig(**app_config.providers["minimax_llm"])
    body = json.dumps({
        "choices": [{
            "message": {"content": "<think>some reasoning</think>The answer is 42."},
        }],
    }).encode("utf-8")
    fake_session = _FakeSession(body)

    adapter = MiniMaxLLMAdapter(
        "minimax_llm",
        app_config,
        provider_config,
        {},
        session_factory=lambda: fake_session,
    )
    await adapter.start()
    response = await adapter.generate("call-1", "what is 6*7?", {}, {})
    assert response.text == "The answer is 42."


@pytest.mark.asyncio
async def test_minimax_llm_highspeed_model():
    """Adapter works with the MiniMax-M2.7-highspeed model."""
    app_config = _build_app_config()
    config_data = dict(app_config.providers["minimax_llm"])
    config_data["chat_model"] = "MiniMax-M2.7-highspeed"
    provider_config = MiniMaxLLMProviderConfig(**config_data)
    body = json.dumps({
        "choices": [{"message": {"content": "fast response"}}],
    }).encode("utf-8")
    fake_session = _FakeSession(body)

    adapter = MiniMaxLLMAdapter(
        "minimax_llm",
        app_config,
        provider_config,
        {},
        session_factory=lambda: fake_session,
    )
    await adapter.start()
    response = await adapter.generate("call-1", "hi", {}, {})
    assert response.text == "fast response"
    assert fake_session.requests[0]["json"]["model"] == "MiniMax-M2.7-highspeed"


@pytest.mark.asyncio
async def test_minimax_llm_no_api_key_raises():
    """Adapter raises when MINIMAX_API_KEY is not set."""
    app_config = _build_app_config()
    config_data = dict(app_config.providers["minimax_llm"])
    config_data["api_key"] = None
    provider_config = MiniMaxLLMProviderConfig(**config_data)
    fake_session = _FakeSession(b"{}")

    adapter = MiniMaxLLMAdapter(
        "minimax_llm",
        app_config,
        provider_config,
        {},
        session_factory=lambda: fake_session,
    )
    await adapter.start()
    with pytest.raises(RuntimeError, match="MINIMAX_API_KEY"):
        await adapter.generate("call-1", "hi", {}, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("error_status", [400, 422])
async def test_minimax_llm_retry_without_tools(error_status):
    """Adapter retries without tools when first attempt returns 400 or 422."""
    app_config = _build_app_config()
    provider_config = MiniMaxLLMProviderConfig(**app_config.providers["minimax_llm"])

    error_body = json.dumps({"error": {"message": "tool format unsupported"}}).encode("utf-8")
    success_body = json.dumps({
        "choices": [{"message": {"content": "fallback answer"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }).encode("utf-8")

    fake_session = _SequencedFakeSession([
        (error_body, error_status),  # First call with tools -> error
        (success_body, 200),         # Retry without tools -> success
    ])

    # Create a fake tool so the adapter can build tool schemas.
    fake_tool_def = MagicMock()
    fake_tool_def.phase = ToolPhase.IN_CALL
    fake_tool_def.to_openai_schema.return_value = {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }
    fake_tool = MagicMock()
    fake_tool.definition = fake_tool_def

    adapter = MiniMaxLLMAdapter(
        "minimax_llm",
        app_config,
        provider_config,
        {"tools": ["get_weather"]},
        session_factory=lambda: fake_session,
    )
    await adapter.start()

    with patch("src.pipelines.minimax.tool_registry") as mock_registry:
        mock_registry.get.return_value = fake_tool
        response = await adapter.generate("call-1", "weather?", {"system_prompt": "Be helpful."}, {})

    assert response.text == "fallback answer"
    # First request should have had tools, second should not.
    assert len(fake_session.requests) == 2
    assert "tools" in fake_session.requests[0]["json"]
    assert "tools" not in fake_session.requests[1]["json"]
    assert "tool_choice" not in fake_session.requests[1]["json"]


@pytest.mark.asyncio
async def test_minimax_llm_metadata_contains_assistant_message():
    """Metadata includes the full assistant message for multi-turn compliance."""
    app_config = _build_app_config()
    provider_config = MiniMaxLLMProviderConfig(**app_config.providers["minimax_llm"])
    original_content = "<think>reasoning</think>The result."
    body = json.dumps({
        "choices": [{"message": {"content": original_content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }).encode("utf-8")
    fake_session = _FakeSession(body)

    adapter = MiniMaxLLMAdapter(
        "minimax_llm",
        app_config,
        provider_config,
        {},
        session_factory=lambda: fake_session,
    )
    await adapter.start()
    response = await adapter.generate("call-1", "test", {}, {})

    # Spoken text should have thinking stripped.
    assert response.text == "The result."
    # Metadata should preserve the original content for history replay.
    assert "assistant_message" in response.metadata
    assert response.metadata["assistant_message"]["content"] == original_content
    assert "usage" in response.metadata


async def _minimax_tool_calls(arguments):
    app_config = _build_app_config()
    provider_config = MiniMaxLLMProviderConfig(**app_config.providers["minimax_llm"])
    body = json.dumps({
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "hangup_call", "arguments": arguments},
                }],
            },
        }],
    }).encode("utf-8")
    adapter = MiniMaxLLMAdapter(
        "minimax_llm",
        app_config,
        provider_config,
        {},
        session_factory=lambda: _FakeSession(body),
    )
    await adapter.start()
    response = await adapter.generate("call-1", "bye", {}, {})
    return response.tool_calls


@pytest.mark.asyncio
async def test_minimax_llm_tool_call_with_empty_arguments_keeps_the_call():
    """A parameterless call arrives with arguments "" and must not be dropped."""
    tool_calls = await _minimax_tool_calls("")

    assert [(tc["name"], tc["parameters"]) for tc in tool_calls] == [("hangup_call", {})]


@pytest.mark.asyncio
async def test_minimax_llm_tool_call_with_object_arguments_keeps_the_call():
    tool_calls = await _minimax_tool_calls({"reason": "done"})

    assert [(tc["name"], tc["parameters"]) for tc in tool_calls] == [("hangup_call", {"reason": "done"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", ["", None, {"reason": "done"}, "null"])
async def test_minimax_llm_replays_normalized_tool_arguments_with_original_reasoning(arguments):
    """Accepted argument shapes must remain valid on the following API request."""
    app_config = _build_app_config()
    provider_config = MiniMaxLLMProviderConfig(**app_config.providers["minimax_llm"])
    raw_content = "<think>Original reasoning.</think>"
    first_body = json.dumps({"choices": [{"message": {
        "role": "assistant", "content": raw_content, "reasoning_content": "Original reasoning.",
        "tool_calls": [{"id": "call_1", "type": "function", "function": {
            "name": "hangup_call", "arguments": arguments,
        }}],
    }}]}).encode("utf-8")
    next_body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "Done."}}]}).encode("utf-8")
    fake_session = _SequencedFakeSession([(first_body, 200), (next_body, 200)])
    adapter = MiniMaxLLMAdapter("minimax_llm", app_config, provider_config, {}, session_factory=lambda: fake_session)
    await adapter.start()
    try:
        response = await adapter.generate("call-1", "bye", {}, {})
        assistant_message = response.metadata["assistant_message"]
        messages = [assistant_message, {"role": "tool", "tool_call_id": "call_1", "content": "Done."}]
        await adapter.generate("call-1", "", {"messages": messages}, {})

        replayed = fake_session.requests[1]["json"]["messages"][0]
        assert replayed["content"] == raw_content
        assert replayed["reasoning_content"] == "Original reasoning."
        replayed_arguments = replayed["tool_calls"][0]["function"]["arguments"]
        assert isinstance(replayed_arguments, str)
        expected = arguments if isinstance(arguments, dict) else {}
        assert json.loads(replayed_arguments) == expected
        assert response.tool_calls[0]["parameters"] == expected
    finally:
        await adapter.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", ['[1]', '"text"', 'true'])
async def test_minimax_llm_drops_non_object_tool_arguments(arguments):
    """Malformed parameter types must not reach a tool execution."""
    assert await _minimax_tool_calls(arguments) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_arguments", ['{not json', '[1]'])
async def test_minimax_llm_replay_contains_only_accepted_tool_calls(invalid_arguments):
    """Rejected calls must not create unmatched tool IDs in replayed history."""
    app_config = _build_app_config()
    provider_config = MiniMaxLLMProviderConfig(**app_config.providers["minimax_llm"])
    body = json.dumps({"choices": [{"message": {
        "role": "assistant", "content": "<think>Keep the reasoning.</think>",
        "tool_calls": [
            {"id": "invalid", "type": "function", "function": {"name": "hangup_call", "arguments": invalid_arguments}},
            {"id": "valid", "type": "function", "function": {"name": "hangup_call", "arguments": ""}},
        ],
    }}]}).encode("utf-8")
    adapter = MiniMaxLLMAdapter("minimax_llm", app_config, provider_config, {}, session_factory=lambda: _FakeSession(body))
    await adapter.start()
    try:
        response = await adapter.generate("call-1", "bye", {}, {})
        assert [call["id"] for call in response.tool_calls] == ["valid"]
        retained = response.metadata["assistant_message"]
        assert retained["content"] == "<think>Keep the reasoning.</think>"
        assert retained["tool_calls"] == [{
            "id": "valid", "type": "function", "function": {"name": "hangup_call", "arguments": "{}"},
        }]
    finally:
        await adapter.stop()
