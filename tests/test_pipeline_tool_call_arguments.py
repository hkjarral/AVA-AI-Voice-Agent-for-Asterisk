import json

import pytest

from src.pipelines.base import parse_tool_call_arguments


def test_json_object_string_is_decoded():
    assert parse_tool_call_arguments('{"city": "Tokyo"}') == {"city": "Tokyo"}


@pytest.mark.parametrize("empty", ["", "  ", None, "null"])
def test_empty_arguments_mean_no_parameters(empty):
    assert parse_tool_call_arguments(empty) == {}


def test_object_arguments_pass_through():
    assert parse_tool_call_arguments({"reason": "done"}) == {"reason": "done"}


def test_invalid_json_still_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_tool_call_arguments("{not json")


@pytest.mark.parametrize("value", [[], [1], "text", 1, True, False])
def test_non_object_json_is_rejected(value):
    with pytest.raises(TypeError, match="JSON object"):
        parse_tool_call_arguments(json.dumps(value))
