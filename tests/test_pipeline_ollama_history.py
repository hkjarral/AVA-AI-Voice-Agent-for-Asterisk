from src.pipelines.ollama import OllamaLLMAdapter


def _history(turns, system=True):
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
