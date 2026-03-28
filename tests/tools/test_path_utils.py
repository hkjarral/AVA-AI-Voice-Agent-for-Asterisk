"""Tests for shared JSON path extraction utility (src/tools/http/path_utils.py).

Covers simple paths, numeric indices, [*] wildcards, null/missing semantics,
nested wildcards, the exact failing cases from GitHub issue #281, and
adapter-level sanitization to prove extracted data reaches providers.
"""

import json

import pytest

from src.tools.http.path_utils import extract_path
from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string


# ---------------------------------------------------------------------------
# Issue #281 — exact failing cases from the bug report
# ---------------------------------------------------------------------------

PIZZA_RESPONSE = {
    "records": [
        {
            "id": "rec2CWYUI42xjmlbT",
            "createdTime": "2026-03-25T17:27:49.000Z",
            "fields": {"name": "Margherita", "price": 8.5, "size": "30cm", "category": "classic"},
        },
        {
            "id": "rec56duESZz10xSJ9",
            "createdTime": "2026-03-25T17:27:49.000Z",
            "fields": {"name": "Pepperoni", "price": 9.9, "size": "30cm", "category": "classic"},
        },
        {
            "id": "recKmcmV3thQCpKqp",
            "createdTime": "2026-03-25T17:27:49.000Z",
            "fields": {"name": "Hawaiian", "price": 10.5, "size": "30cm", "category": "special"},
        },
        {
            "id": "recRt865ANp8OrYSH",
            "createdTime": "2026-03-25T17:27:49.000Z",
            "fields": {"name": "BBQ Chicken", "price": 11.2, "size": "30cm", "category": "special"},
        },
        {
            "id": "recfdO0XSmuEsaK7x",
            "createdTime": "2026-03-25T17:27:49.000Z",
            "fields": {"name": "Veggie", "price": 9.5, "size": "30cm", "category": "vegetarian"},
        },
    ]
}


class TestIssue281:
    """Exact cases from issue #281 that previously returned empty strings."""

    def test_records_wildcard_returns_full_array(self):
        result = extract_path(PIZZA_RESPONSE, "records[*]")
        assert isinstance(result, list)
        assert len(result) == 5
        assert result[0]["id"] == "rec2CWYUI42xjmlbT"

    def test_records_wildcard_fields(self):
        result = extract_path(PIZZA_RESPONSE, "records[*].fields")
        assert isinstance(result, list)
        assert len(result) == 5
        assert result[0] == {"name": "Margherita", "price": 8.5, "size": "30cm", "category": "classic"}

    def test_records_wildcard_fields_name(self):
        result = extract_path(PIZZA_RESPONSE, "records[*].fields.name")
        assert result == ["Margherita", "Pepperoni", "Hawaiian", "BBQ Chicken", "Veggie"]

    def test_records_wildcard_fields_price(self):
        result = extract_path(PIZZA_RESPONSE, "records[*].fields.price")
        assert result == [8.5, 9.9, 10.5, 11.2, 9.5]


# ---------------------------------------------------------------------------
# Backward compatibility — simple paths and numeric indices
# ---------------------------------------------------------------------------

class TestSimplePaths:
    def test_simple_field(self):
        assert extract_path({"name": "Alice"}, "name") == "Alice"

    def test_nested_field(self):
        assert extract_path({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_empty_path_returns_data(self):
        data = {"x": 1}
        assert extract_path(data, "") is data

    def test_missing_field_returns_none(self):
        assert extract_path({"a": 1}, "b") is None

    def test_missing_nested_field_returns_none(self):
        assert extract_path({"a": {"b": 1}}, "a.c") is None

    def test_none_data(self):
        assert extract_path(None, "a") is None


class TestNumericIndex:
    def test_first_element(self):
        data = {"items": [{"v": "a"}, {"v": "b"}]}
        assert extract_path(data, "items[0].v") == "a"

    def test_second_element(self):
        data = {"items": [{"v": "a"}, {"v": "b"}]}
        assert extract_path(data, "items[1].v") == "b"

    def test_out_of_bounds(self):
        data = {"items": [{"v": "a"}]}
        assert extract_path(data, "items[5].v") is None

    def test_index_on_non_list(self):
        data = {"items": "not a list"}
        assert extract_path(data, "items[0]") is None


# ---------------------------------------------------------------------------
# Wildcard [*] — core behavior
# ---------------------------------------------------------------------------

class TestWildcard:
    def test_wildcard_returns_full_array(self):
        data = {"items": [1, 2, 3]}
        assert extract_path(data, "items[*]") == [1, 2, 3]

    def test_wildcard_with_nested_path(self):
        data = {"users": [{"name": "A"}, {"name": "B"}]}
        assert extract_path(data, "users[*].name") == ["A", "B"]

    def test_wildcard_on_empty_array(self):
        data = {"items": []}
        assert extract_path(data, "items[*]") == []

    def test_wildcard_on_empty_array_with_path(self):
        data = {"items": []}
        assert extract_path(data, "items[*].name") == []

    def test_wildcard_on_missing_field(self):
        data = {"other": [1]}
        assert extract_path(data, "items[*]") is None

    def test_wildcard_on_non_array(self):
        data = {"items": "string"}
        assert extract_path(data, "items[*]") is None

    def test_wildcard_on_dict_value(self):
        data = {"items": {"a": 1}}
        assert extract_path(data, "items[*]") is None

    def test_wildcard_deep_nested(self):
        data = {"a": {"b": [{"c": {"d": 1}}, {"c": {"d": 2}}]}}
        assert extract_path(data, "a.b[*].c.d") == [1, 2]


# ---------------------------------------------------------------------------
# Bare [*] — root-level list
# ---------------------------------------------------------------------------

class TestBareWildcard:
    def test_bare_wildcard_on_list(self):
        data = [{"name": "A"}, {"name": "B"}]
        assert extract_path(data, "[*].name") == ["A", "B"]

    def test_bare_wildcard_returns_list(self):
        data = [1, 2, 3]
        assert extract_path(data, "[*]") == [1, 2, 3]

    def test_bare_wildcard_on_non_list(self):
        data = {"key": "value"}
        assert extract_path(data, "[*].name") is None


# ---------------------------------------------------------------------------
# Bare [N] — root-level numeric index (Admin UI suggests these)
# ---------------------------------------------------------------------------

class TestBareNumericIndex:
    def test_bare_index_on_root_list(self):
        data = [{"name": "A"}, {"name": "B"}]
        assert extract_path(data, "[0].name") == "A"

    def test_bare_index_second_element(self):
        data = [{"name": "A"}, {"name": "B"}]
        assert extract_path(data, "[1].name") == "B"

    def test_bare_index_out_of_bounds(self):
        data = [{"name": "A"}]
        assert extract_path(data, "[5].name") is None

    def test_bare_index_on_non_list(self):
        data = {"key": "value"}
        assert extract_path(data, "[0].name") is None

    def test_bare_index_no_remaining_path(self):
        data = ["a", "b", "c"]
        assert extract_path(data, "[1]") == "b"


# ---------------------------------------------------------------------------
# Null vs missing semantics
# ---------------------------------------------------------------------------

class TestNullMissing:
    def test_null_preserved_in_wildcard(self):
        """JSON null is preserved; missing keys are excluded."""
        data = {"items": [{"foo": None}, {}]}
        result = extract_path(data, "items[*].foo")
        assert result == [None]

    def test_null_and_value_preserved(self):
        data = {"items": [{"foo": "a"}, {"foo": None}]}
        result = extract_path(data, "items[*].foo")
        assert result == ["a", None]

    def test_all_missing_returns_empty_list(self):
        data = {"items": [{}, {}, {}]}
        result = extract_path(data, "items[*].foo")
        assert result == []

    def test_scalar_null_field(self):
        """Direct access to a null field returns None."""
        data = {"value": None}
        assert extract_path(data, "value") is None

    def test_path_through_null_field(self):
        """Path continues through a null value — should return None."""
        data = {"a": None}
        assert extract_path(data, "a.b") is None


# ---------------------------------------------------------------------------
# Mixed numeric index + wildcard
# ---------------------------------------------------------------------------

class TestMixedPaths:
    def test_numeric_then_wildcard(self):
        data = {"data": {"items": [{"tags": ["x", "y"]}, {"tags": ["z"]}]}}
        assert extract_path(data, "data.items[0].tags[*]") == ["x", "y"]

    def test_wildcard_then_numeric(self):
        data = {"rows": [{"cells": ["a", "b"]}, {"cells": ["c", "d"]}]}
        assert extract_path(data, "rows[*].cells[0]") == ["a", "c"]


# ---------------------------------------------------------------------------
# Nested wildcards — each [*] adds one nesting level
# ---------------------------------------------------------------------------

class TestNestedWildcards:
    def test_double_wildcard_nested_lists(self):
        data = {
            "groups": [
                {"members": [{"name": "A"}, {"name": "B"}]},
                {"members": [{"name": "C"}]},
            ]
        }
        result = extract_path(data, "groups[*].members[*].name")
        assert result == [["A", "B"], ["C"]]

    def test_double_wildcard_empty_inner(self):
        data = {
            "groups": [
                {"members": []},
                {"members": [{"name": "X"}]},
            ]
        }
        result = extract_path(data, "groups[*].members[*].name")
        assert result == [[], ["X"]]

    def test_double_wildcard_missing_inner_preserves_slot(self):
        """Missing nested wildcard field produces [] to preserve positional alignment."""
        data = {
            "groups": [
                {"members": [{"name": "A"}]},
                {},
                {"members": [{"name": "C"}]},
            ]
        }
        result = extract_path(data, "groups[*].members[*].name")
        assert result == [["A"], [], ["C"]]


# ---------------------------------------------------------------------------
# Non-word JSON keys (hyphens, spaces — common in third-party APIs)
# ---------------------------------------------------------------------------

class TestNonWordKeys:
    def test_hyphenated_field_wildcard(self):
        data = {"line-items": [{"sku": "A"}, {"sku": "B"}]}
        assert extract_path(data, "line-items[*].sku") == ["A", "B"]

    def test_hyphenated_field_index(self):
        data = {"line-items": [{"sku": "A"}, {"sku": "B"}]}
        assert extract_path(data, "line-items[0].sku") == "A"

    def test_simple_hyphenated_field(self):
        data = {"first-name": "Alice"}
        assert extract_path(data, "first-name") == "Alice"


# ---------------------------------------------------------------------------
# Traversal through null in wildcard context
# ---------------------------------------------------------------------------

class TestNullTraversalInWildcard:
    def test_intermediate_null_included_as_none(self):
        """When an intermediate field is null (not missing), the item contributes None."""
        data = {"items": [{"a": None}, {"a": {"b": "x"}}]}
        result = extract_path(data, "items[*].a.b")
        assert result == [None, "x"]

    def test_intermediate_null_vs_missing(self):
        """null intermediate → None in result; missing intermediate → excluded."""
        data = {"items": [{"a": None}, {}, {"a": {"b": "y"}}]}
        result = extract_path(data, "items[*].a.b")
        assert result == [None, "y"]


# ---------------------------------------------------------------------------
# Adapter-level sanitization — prove "data" key survives to providers
# ---------------------------------------------------------------------------

class TestSanitizerPreservesData:
    """Verify extracted output variables survive sanitize_tool_result_for_json_string."""

    def test_data_key_preserved_in_sanitized_output(self):
        """Simulates the in-call tool result flowing through the OpenAI/Deepgram path."""
        tool_result = {
            "status": "success",
            "message": "Retrieved data successfully.",
            "data": {
                "menu": json.dumps(["Margherita", "Pepperoni", "Hawaiian"]),
                "count": "5",
            },
        }
        sanitized = sanitize_tool_result_for_json_string(tool_result)
        assert "data" in sanitized
        assert sanitized["data"]["menu"] == json.dumps(["Margherita", "Pepperoni", "Hawaiian"])

    def test_data_with_array_survives_json_encoding(self):
        """The final JSON string sent to providers should contain the data."""
        tool_result = {
            "status": "success",
            "message": "OK",
            "data": {"names": ["Alice", "Bob"]},
        }
        sanitized = sanitize_tool_result_for_json_string(tool_result)
        encoded = json.dumps(sanitized)
        assert "Alice" in encoded
        assert "Bob" in encoded

    def test_large_data_capped_by_safe_jsonable(self):
        """_safe_jsonable caps lists at 50 items, preventing unbounded growth."""
        big_list = [{"name": f"item_{i}"} for i in range(100)]
        tool_result = {
            "status": "success",
            "message": "OK",
            "data": {"items": big_list},
        }
        sanitized = sanitize_tool_result_for_json_string(tool_result)
        # _safe_jsonable caps at max_items=50
        assert len(sanitized["data"]["items"]) == 50

    def test_oversized_data_dropped_to_meet_byte_cap(self):
        """When data alone exceeds max_bytes, it must be dropped to stay within budget."""
        big_list = [{"name": f"item_{i}", "desc": "x" * 200} for i in range(50)]
        tool_result = {
            "status": "success",
            "message": "Summary of results",
            "data": {"items": big_list},
        }
        max_bytes = 2000
        sanitized = sanitize_tool_result_for_json_string(tool_result, max_bytes=max_bytes)
        encoded = json.dumps(sanitized, ensure_ascii=False)
        assert len(encoded.encode("utf-8")) <= max_bytes
        # data should have been dropped to fit
        assert "data" not in sanitized
        # message should still be present
        assert "message" in sanitized

    def test_multibyte_message_truncated_within_budget(self):
        """Multibyte text in message must not exceed max_bytes after truncation."""
        # Each emoji is 4 bytes in UTF-8
        tool_result = {
            "status": "success",
            "message": "\U0001f600" * 500,
        }
        max_bytes = 200
        sanitized = sanitize_tool_result_for_json_string(tool_result, max_bytes=max_bytes)
        encoded = json.dumps(sanitized, ensure_ascii=False)
        assert len(encoded.encode("utf-8")) <= max_bytes
