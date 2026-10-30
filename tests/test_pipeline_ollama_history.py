import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.pipelines.ollama import OllamaLLMAdapter


def _history(turns, system=True):
    """Build synthetic completed conversation turns with an optional system prompt."""
    messages = [{"role": "system", "content": "You are a phone agent."}] if system else []
    for i in range(turns):
        messages.append({"role": "user", "content": f"u{i}"})
        messages.append({"role": "assistant", "content": f"a{i}"})
    return messages


def test_short_history_is_sent_unchanged():
    messages = _history(3)

    assert OllamaLLMAdapter._recent_messages(messages) == messages


def test_long_history_keeps_system_prompt_and_latest_messages():
    messages = _history(8)

    recent = OllamaLLMAdapter._recent_messages(messages)

    assert len(recent) == 10
    assert recent[0] == {"role": "system", "content": "You are a phone agent."}
    assert recent[1:] == messages[-9:]


def test_long_history_without_system_prompt_keeps_latest_messages():
    messages = _history(8, system=False)

    assert OllamaLLMAdapter._recent_messages(messages) == messages[-10:]


@pytest.mark.asyncio
async def test_generate_preserves_system_prompt_in_long_history_payload():
    """Capture the actual HTTP request so bypassing the trim helper fails this test."""
    adapter = OllamaLLMAdapter(SimpleNamespace())
    await adapter.open_call("long-history", {})
    history = _history(8)
    adapter._sessions["long-history"]["messages"] = copy.deepcopy(history)
    expected_messages = [*history, {"role": "user", "content": "Latest question"}]
    captured = {}
    response = SimpleNamespace(
        status=200,
        json=AsyncMock(return_value={"message": {"content": "Latest answer"}}),
    )

    def post(url, **kwargs):
        # Snapshot before generate appends the assistant reply to session history.
        captured["url"] = url
        captured["payload"] = copy.deepcopy(kwargs["json"])
        return MagicMock(
            __aenter__=AsyncMock(return_value=response),
            __aexit__=AsyncMock(return_value=None),
        )

    adapter._session = SimpleNamespace(closed=False, post=post, close=AsyncMock())
    try:
        result = await adapter.generate("long-history", "Latest question", {}, {})

        assert captured["url"].endswith("/api/chat")
        sent = captured["payload"]["messages"]
        assert len(sent) == 10
        assert sent[0] == history[0]
        assert sent[1:] == expected_messages[-9:]
        assert result.text == "Latest answer"
    finally:
        await adapter.stop()
