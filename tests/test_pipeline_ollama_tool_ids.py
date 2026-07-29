from src.pipelines.ollama import OllamaLLMAdapter


def _tool_call(tool_call_id=None):
    call = {
        "function": {
            "name": "google_calendar",
            "arguments": {"action": "create_event"},
        }
    }
    if tool_call_id is not None:
        call["id"] = tool_call_id
    return call


def test_ollama_missing_ids_are_distinct_across_turns():
    first_turn = OllamaLLMAdapter._parse_tool_calls([_tool_call()])
    second_turn = OllamaLLMAdapter._parse_tool_calls([_tool_call()])

    assert first_turn[0]["id"].startswith("generated-")
    assert second_turn[0]["id"].startswith("generated-")
    assert first_turn[0]["id"] != second_turn[0]["id"]


def test_ollama_preserves_provider_native_id():
    parsed = OllamaLLMAdapter._parse_tool_calls([_tool_call("ollama-native-1")])

    assert parsed[0]["id"] == "ollama-native-1"
