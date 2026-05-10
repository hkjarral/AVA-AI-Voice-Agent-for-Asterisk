"""JSON-schema validation tests for the Local AI Server WebSocket protocol.

Audit follow-up (re-review): the v6.5.0 #368 fix added two new message types
(`tool_context` and `tool_result`) but they were initially missing from the
top-level `oneOf` in `local_ai_server/protocol_contract.py`. Without them in
the oneOf, environments running with `jsonschema` installed reject valid
tool-gateway messages even though the server handles them correctly.

These tests pin two properties:
  1. The new message types are in the protocol's top-level `oneOf` so they
     pass validation alongside the legacy `llm_tool_request` /
     `llm_tool_response` shapes.
  2. The published JSON schema artifact at
     `docs/local-ai-server/protocol.schema.json` is in sync with the
     in-code schema in `protocol_contract.py` (regeneration is part of
     the release process).
"""

import json
import os

import pytest

from local_ai_server.protocol_contract import (
    PROTOCOL_SCHEMA,
    validate_payload,
)


# Skip cleanly if `jsonschema` isn't installed in the test env. The
# `validate_payload` helper falls back to minimal checks in that case,
# so we still exercise it but the fine-grained shape validation requires
# the library.
jsonschema = pytest.importorskip(
    "jsonschema",
    reason="jsonschema not installed; install via pip to run schema tests",
)


# --- ToolContext ---


def test_tool_context_minimal_payload_validates():
    """Most permissive valid ToolContext: type + call_id only."""
    validate_payload({
        "type": "tool_context",
        "call_id": "1234-5678",
    })


def test_tool_context_full_payload_validates():
    validate_payload({
        "type": "tool_context",
        "call_id": "1234-5678",
        "allowed_tools": ["hangup_call", "request_transcript"],
        "tools": [
            {
                "name": "hangup_call",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        "tool_policy": "auto",
        "protocol_version": 2,
    })


def test_tool_context_missing_type_rejected():
    """`type` is required by the contract. `validate_payload` enforces this
    via a manual pre-check (raising ``ValueError``) before delegating to
    jsonschema, so missing-type rejection works even when jsonschema is
    not installed. Either exception type is acceptable here — the
    semantic guarantee is that the payload is rejected."""
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        validate_payload({"call_id": "1234"})


def test_tool_context_with_invalid_tool_policy_rejected():
    """`tool_policy` is constrained to a known enum by the schema. The
    pre-check in ``validate_payload`` does not catch this, so the
    rejection genuinely comes from jsonschema and the specific
    ``jsonschema.ValidationError`` type is meaningful."""
    with pytest.raises(jsonschema.ValidationError):
        validate_payload({
            "type": "tool_context",
            "tool_policy": "not-a-real-policy",
        })


# --- ToolResult ---


def test_tool_result_success_shape_validates():
    validate_payload({
        "type": "tool_result",
        "call_id": "1234-5678",
        "tool_name": "microsoft_calendar",
        "result": {"events": []},
    })


def test_tool_result_error_shape_validates():
    validate_payload({
        "type": "tool_result",
        "call_id": "1234-5678",
        "tool_name": "microsoft_calendar",
        "result": {"error": "Calendar API unavailable"},
        "is_error": True,
    })


def test_tool_result_with_function_call_id_validates():
    """Some callers pass `function_call_id` instead of `tool_name`; both
    should validate per the schema (the server's handler accepts either)."""
    validate_payload({
        "type": "tool_result",
        "function_call_id": "fc_12345",
        "tool_name": "microsoft_calendar",
        "result": {"value": 42},
    })


def test_tool_result_arbitrary_result_value_validates():
    """`result` may be any JSON value (object preferred); list/string/null
    must all validate so server-side stringification works."""
    for result_value in (
        {"k": "v"},
        ["a", "b"],
        "plain string",
        42,
        None,
    ):
        validate_payload({
            "type": "tool_result",
            "tool_name": "tool",
            "result": result_value,
        })


def test_tool_result_missing_tool_name_rejected():
    """`tool_name` is the only schema-required field besides `type`. This
    rejection comes from jsonschema (the pre-check only validates the
    `type` key), so the specific ``jsonschema.ValidationError`` type is
    meaningful here."""
    with pytest.raises(jsonschema.ValidationError):
        validate_payload({
            "type": "tool_result",
            "result": {"k": "v"},
        })


# --- Schema regeneration sync check ---


def test_published_schema_json_matches_in_code_schema():
    """`docs/local-ai-server/protocol.schema.json` must be in sync with
    `PROTOCOL_SCHEMA` defined in `local_ai_server/protocol_contract.py`.

    If this fails, regenerate via:
        python3 local_ai_server/protocol_contract.py \
            --write-schema docs/local-ai-server/protocol.schema.json
    """
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir)
    )
    artifact_path = os.path.join(
        repo_root, "docs", "local-ai-server", "protocol.schema.json"
    )
    assert os.path.exists(artifact_path), (
        "docs/local-ai-server/protocol.schema.json missing — run "
        "`python3 local_ai_server/protocol_contract.py --write-schema "
        "docs/local-ai-server/protocol.schema.json` to regenerate."
    )
    with open(artifact_path) as fh:
        artifact = json.load(fh)
    assert artifact == PROTOCOL_SCHEMA, (
        "Published schema artifact is out of sync with PROTOCOL_SCHEMA. "
        "Regenerate via:\n"
        "  python3 local_ai_server/protocol_contract.py "
        "--write-schema docs/local-ai-server/protocol.schema.json"
    )


def test_oneOf_contains_tool_context_and_tool_result():
    """Pin the audit-required property: the v6.5.0 tool-gateway message
    types are in the top-level oneOf so a strict validator accepts them."""
    refs = {
        item.get("$ref", "")
        for item in PROTOCOL_SCHEMA.get("oneOf", [])
    }
    assert "#/$defs/ToolContext" in refs, (
        "ToolContext must be in the protocol's top-level oneOf so a strict "
        "jsonschema validator accepts it."
    )
    assert "#/$defs/ToolResult" in refs, (
        "ToolResult must be in the protocol's top-level oneOf so a strict "
        "jsonschema validator accepts it."
    )
