"""Synthetic pipeline notification continuation; no model/network calls."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.adapters.sanitize import prepare_pipeline_tool_result
from src.pipelines.openai import OpenAILLMAdapter
from src.pipelines.google import GoogleLLMAdapter
from src.pipelines.ollama import OllamaLLMAdapter


def continuation(status="success", internal=True):
    result = {
        "status": status,
        "internal_notification": internal,
        "message": "PRIVATE ACKNOWLEDGEMENT",
        "notification_deliveries": {
            "PRIVATE RECIPIENT": {"delivery_status": "accepted"}
        },
    }
    options = {
        "system_prompt": "Follow the configured business workflow.",
        "tools": ["blind_transfer"],
    }
    original = copy.deepcopy(options)
    message, extra, effective = prepare_pipeline_tool_result("notify", result, options)
    assert options == original
    context = {
        "prior_messages": [
            {"role": "user", "content": "Please transfer me"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "synthetic",
                        "type": "function",
                        "function": {"name": "notify", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "content": message, "tool_call_id": "synthetic"},
        ],
        **extra,
    }
    return result, context, effective


@pytest.mark.parametrize("status", ["success", "error", "timeout"])
def test_openai_and_google_pipeline_requests_exclude_notification_details(status):
    result, context, options = continuation(status)
    openai = object.__new__(OpenAILLMAdapter)
    payload = openai._build_chat_payload(
        "", context, {"chat_model": "synthetic", **options}
    )
    assert json.loads(payload["messages"][-1]["content"]) == {
        "status": status,
        "internal_notification": True,
    }
    google = object.__new__(GoogleLLMAdapter)
    google_payload = google._build_payload("", context, {"temperature": 0, **options})
    for value in (payload, google_payload):
        encoded = json.dumps(value)
        assert "PRIVATE" not in encoded and "notification_deliveries" not in encoded
        assert (
            "Do not read acknowledgements" in encoded and "remaining tools" in encoded
        )
        assert "configured business workflow" in encoded
        assert f"outcome: {status}" in encoded
    assert options["tools"] == ["blind_transfer"]
    assert "Do not read acknowledgements" in options["instructions"]
    assert "configured business workflow" in options["instructions"]
    assert "PRIVATE RECIPIENT" in result["notification_deliveries"]


@pytest.mark.asyncio
@pytest.mark.parametrize("internal", [True, False])
async def test_ollama_notification_skips_readout_and_preserves_followup_tools(internal):
    _, context, options = continuation(internal=internal)
    response = SimpleNamespace(
        status=200,
        json=AsyncMock(
            return_value={
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "followup",
                            "function": {
                                "name": "blind_transfer",
                                "arguments": {"destination": "support"},
                            },
                        }
                    ],
                }
            }
        ),
    )
    cm = MagicMock(
        __aenter__=AsyncMock(return_value=response),
        __aexit__=AsyncMock(return_value=None),
    )
    client = SimpleNamespace(closed=False, post=MagicMock(return_value=cm))
    adapter = OllamaLLMAdapter(SimpleNamespace())
    adapter._session = client
    adapter._build_tools_schema = lambda names: [
        {"type": "function", "function": {"name": names[0]}}
    ]
    # Existing calls already have a cached system prompt; the silent branch must
    # work without relying on replacement of that first message.
    adapter._sessions["synthetic"] = {
        "messages": [{"role": "system", "content": "Cached business prompt"}]
    }
    result = await adapter.generate("synthetic", "", context, options)
    payload = client.post.call_args.kwargs["json"]
    text = json.dumps(payload["messages"])
    if internal:
        assert "Do not read acknowledgements" in text and "PRIVATE" not in text
        assert "Now say this information" not in text
    else:
        assert "PRIVATE ACKNOWLEDGEMENT" in text and "Now say this information" in text
    assert result.text == "" and result.tool_calls[0]["name"] == "blind_transfer"
    assert payload["tools"][0]["function"]["name"] == "blind_transfer"
