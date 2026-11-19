"""Contract tests for the Telnyx text-generation adapter."""

import json

import pytest

from src.config import AppConfig, TelnyxLLMProviderConfig
from src.pipelines.telnyx import TelnyxLLMAdapter


def test_telnyx_summary_job_prompt_overrides_agent_persona():
    app_config = AppConfig(
        default_provider="local",
        providers={},
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "VOICE AGENT PERSONA"},
    )
    provider_config = TelnyxLLMProviderConfig(api_key="test-key")
    adapter = TelnyxLLMAdapter("telnyx_llm", app_config, provider_config, {})
    summary_prompt = "Summarize the call. Do not use the agent persona."

    options = adapter._compose_options(
        {
            "system_prompt": summary_prompt,
            "instructions": summary_prompt,
            "temperature": 0.3,
            "max_tokens": 300,
            "tools": [],
        }
    )
    payload = adapter._build_chat_payload(
        "user: What does it cost?\nassistant: It costs one dollar.",
        {"system_prompt": summary_prompt, "prior_messages": []},
        options,
    )

    assert payload["messages"][0] == {"role": "system", "content": summary_prompt}
    assert payload["messages"][-1]["role"] == "user"
    assert "VOICE AGENT PERSONA" not in str(payload["messages"])
    assert "tools" not in payload


class _FakeResponse:
    def __init__(self, body):
        self.status = 200
        self._body = body

    async def text(self):
        return json.dumps(self._body)

    def raise_for_status(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    closed = False

    def __init__(self, body):
        self._body = body

    def post(self, *args, **kwargs):
        return _FakeResponse(self._body)


@pytest.mark.parametrize("arguments", ["", None, "null", {}, "  "])
async def test_telnyx_generate_treats_empty_tool_arguments_as_no_parameters(arguments):
    app_config = AppConfig(
        default_provider="local",
        providers={},
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "persona"},
    )
    adapter = TelnyxLLMAdapter("telnyx_llm", app_config, TelnyxLLMProviderConfig(api_key="test-key"), {})
    body = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "hangup_call", "arguments": arguments}}
                    ],
                }
            }
        ]
    }
    adapter._session = _FakeSession(body)

    async def _no_session():
        return None

    async def _same_model(model, **kwargs):
        return model

    adapter._ensure_session = _no_session
    adapter._maybe_resolve_model_id = _same_model

    response = await adapter.generate("call-1", "bye", {}, {})

    assert response.tool_calls == [
        {"id": "call_1", "name": "hangup_call", "parameters": {}, "type": "function"}
    ]
