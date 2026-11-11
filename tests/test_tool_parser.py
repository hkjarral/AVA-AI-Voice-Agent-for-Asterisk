from src.tools.parser import parse_response_with_tools, has_tool_intent_markers


def test_parse_tool_call_primary_wrapper():
    response = (
        "<tool_call>\n"
        '{"name":"hangup_call","arguments":{"farewell_message":"Thank you, goodbye!"}}\n'
        "</tool_call>\n"
        "Thank you, goodbye!"
    )
    clean_text, tool_calls = parse_response_with_tools(response)

    assert clean_text == "Thank you, goodbye!"
    assert tool_calls == [
        {"name": "hangup_call", "parameters": {"farewell_message": "Thank you, goodbye!"}}
    ]


def test_parse_tool_call_named_tag_wrapper_with_name_field():
    response = (
        "<hangup_call>\n"
        '{"name":"hangup_call","arguments":{"farewell_message":"Bye"}}\n'
        "</hangup_call>\n"
        "Bye"
    )
    clean_text, tool_calls = parse_response_with_tools(response)

    assert clean_text == "Bye"
    assert tool_calls == [{"name": "hangup_call", "parameters": {"farewell_message": "Bye"}}]


def test_parse_tool_call_named_tag_wrapper_compact_params():
    response = (
        "<hangup_call>\n"
        '{"farewell_message":"Bye"}\n'
        "</hangup_call>\n"
        "Bye"
    )
    clean_text, tool_calls = parse_response_with_tools(response)

    assert clean_text == "Bye"
    assert tool_calls == [{"name": "hangup_call", "parameters": {"farewell_message": "Bye"}}]


def test_named_tag_without_json_is_not_parsed_as_tool_call():
    response = "<hangup_call>not-json</hangup_call>\nBye"
    clean_text, tool_calls = parse_response_with_tools(response)

    assert clean_text == response
    assert tool_calls is None


def test_parse_tool_call_malformed_closing_tag_prefix():
    response = (
        "</tool_call> "
        '{"name":"hangup_call","arguments":{"farewell_message":"Bye"}} '
        "Bye"
    )
    clean_text, tool_calls = parse_response_with_tools(response)

    assert clean_text == "Bye"
    assert tool_calls == [{"name": "hangup_call", "parameters": {"farewell_message": "Bye"}}]


def test_strip_control_tokens_from_clean_text():
    response = (
        "<tool_call>"
        '{"name":"hangup_call","arguments":{"farewell_message":"Bye"}}'
        "</tool_call>"
        "Bye <|enduser|> <|system|> The call has ended."
    )
    clean_text, tool_calls = parse_response_with_tools(response)
    assert tool_calls and tool_calls[0]["name"] == "hangup_call"
    assert clean_text == "Bye"


def test_parse_tool_call_bare_prefix_json():
    response = (
        'hangup_call {"name":"hangup_call","arguments":{"farewell_message":"Bye"}}\n'
        "Bye"
    )
    clean_text, tool_calls = parse_response_with_tools(response)

    assert clean_text == "Bye"
    assert tool_calls == [{"name": "hangup_call", "parameters": {"farewell_message": "Bye"}}]


def test_parse_tool_call_markdown_prefix_partial_json():
    response = (
        'Thanks for calling. *hangup_call* '
        '{"name":"hangup_call","arguments":{"farewell_message":"Goodbye and take care"'
    )
    clean_text, tool_calls = parse_response_with_tools(response)

    assert clean_text == "Thanks for calling."
    assert tool_calls == [
        {"name": "hangup_call", "parameters": {"farewell_message": "Goodbye and take care"}}
    ]


def test_parse_tool_call_markdown_prefix_no_args_recovers_name():
    response = '*hangup_call* {"name":"hangup_call","arguments":'
    clean_text, tool_calls = parse_response_with_tools(response)

    assert clean_text is None
    assert tool_calls == [{"name": "hangup_call", "parameters": {}}]


def test_has_tool_intent_markers_detects_malformed_tool_output():
    response = 'Thanks. *hangup_call* {"name":"hangup_call","arguments":{"farewell_message":"Bye"'
    assert has_tool_intent_markers(response, ["hangup_call"]) is True


def test_has_tool_intent_markers_ignores_plain_text():
    response = "Thanks for calling. Have a great day."
    assert has_tool_intent_markers(response, ["hangup_call"]) is False


def test_tool_argument_values_never_reach_parser_debug_logs(caplog):
    """All logged wrapper paths retain tool diagnostics without caller PII."""
    import json
    import logging

    parameters = {"caller_name": "Zelda Quartermain", "call_reason": "Invoice 4471 dispute"}
    payload = json.dumps({"name": "attended_transfer", "arguments": parameters})
    wrappers = [
        f"<tool_call>{payload}</tool_call>",
        f"<attended_transfer>{payload}</attended_transfer>",
        f"</tool_call>{payload}",
        f"*attended_transfer* {payload[:-2]}",
    ]
    with caplog.at_level(logging.DEBUG, logger="src.tools.parser"):
        for response in wrappers:
            caplog.clear()
            _, calls = parse_response_with_tools(response)
            assert calls == [{"name": "attended_transfer", "parameters": parameters}]
            records = [r for r in caplog.records if r.name == "src.tools.parser"]
            assert records, "the parser path must exercise a diagnostic log"
            assert "attended_transfer" in caplog.text
            for value in parameters.values():
                assert value not in caplog.text
                assert value not in repr([r.args for r in records])
