from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.tools.execution_history import (
    build_in_call_tool_record,
    normalize_call_history_tool_redaction_mode,
    normalize_tool_terminal_status,
    record_in_call_tool_result,
    stable_tool_call_id,
)
from src.core.models import CallSession
from src.core.session_store import SessionStore


def test_calendar_create_record_has_stable_id_action_and_target():
    record = build_in_call_tool_record(
        call_id="call-1",
        tool_call_id="provider-tool-7",
        tool_name="google_calendar",
        parameters={"action": "create_event", "summary": "Consultation"},
        result={"status": "success", "event_id": "event-42", "message": "Created"},
        duration_ms=12.345,
    )

    assert record == {
        "type": "tool_result",
        "call_id": "call-1",
        "tool_call_id": "provider-tool-7",
        "name": "google_calendar",
        "action": "create_event",
        "status": "success",
        "target_id": "event-42",
        "params": {"action": "create_event", "summary": "***REDACTED***"},
        "result": "success",
        "message": "Created",
        "redaction_mode": "strict",
        "redacted_fields": ["params.summary"],
        "timestamp": record["timestamp"],
        "duration_ms": 12.35,
    }
    assert record["timestamp"].endswith("+00:00")


@pytest.mark.parametrize(
    "raw_status",
    ["error", "failed", "timeout", "blocked", "disabled", "skipped", "no_transfer", "queue_fallback"],
)
def test_failure_statuses_are_normalized(raw_status):
    record = build_in_call_tool_record(
        call_id="call-1",
        tool_call_id="tool-1",
        tool_name="lookup",
        parameters={},
        result={"status": raw_status, "message": "No result"},
    )

    assert record["status"] == "failure"
    assert record["result"] == raw_status


@pytest.mark.parametrize(
    "result",
    [
        {"status": "pending"},
        {"status": "partial"},
        {"message": "No explicit outcome"},
        "unstructured result",
        None,
    ],
)
def test_unknown_or_malformed_statuses_fail_closed(result):
    assert normalize_tool_terminal_status(result) == "failure"


def test_delete_uses_parameter_target_for_compensation():
    record = build_in_call_tool_record(
        call_id="call-1",
        tool_call_id="tool-delete",
        tool_name="google_calendar",
        parameters={"action": "delete_event", "event_id": "event-42"},
        result={"status": "success", "message": "Deleted"},
    )

    assert record["action"] == "delete_event"
    assert record["target_id"] == "event-42"


def test_persisted_params_redact_pii_secrets_and_nested_values_without_mutating_execution_input():
    parameters = {
        "action": "request",
        "caller_email": "caller@example.com",
        "comments": "Call me after my medical appointment",
        "summary": "Consultation with Jane Doe",
        "metadata": {
            "authorization": "Bearer secret-token",
            "attendees": [
                {
                    "email": "guest@example.com",
                    "role": "guest",
                    "comments": "Use the side entrance",
                }
            ],
        },
    }

    record = build_in_call_tool_record(
        call_id="call-private",
        tool_call_id="tool-private",
        tool_name="request_transcript",
        parameters=parameters,
        result={"status": "success"},
    )

    assert record["params"] == {
        "action": "request",
        "caller_email": "***REDACTED***",
        "comments": "***REDACTED***",
        "summary": "***REDACTED***",
        "metadata": {
            "authorization": "***REDACTED***",
            "attendees": [
                {
                    "email": "***REDACTED***",
                    "role": "guest",
                    "comments": "***REDACTED***",
                }
            ],
        },
    }
    assert record["action"] == "request"
    assert parameters["caller_email"] == "caller@example.com"
    assert parameters["metadata"]["authorization"] == "Bearer secret-token"


def test_parameter_redaction_covers_common_pii_and_credential_shapes_without_suffix_false_positives():
    parameters = {
        "customerName": "Jane Doe",
        "contact_phone": "+1-555-0100",
        "date-of-birth": "1990-01-01",
        "account_number": "123456789",
        "follow_up_notes": "Prefers text messages",
        "requestBody": "contains caller details",
        "headers": {"X-API-Key": "secret", "Content-Type": "application/json"},
        "bypass": True,
        "compass": "north",
    }

    record = build_in_call_tool_record(
        call_id="call-pii",
        tool_call_id="tool-pii",
        tool_name="lookup",
        parameters=parameters,
        result={"status": "success"},
    )

    assert record["params"] == {
        "customerName": "***REDACTED***",
        "contact_phone": "***REDACTED***",
        "date-of-birth": "***REDACTED***",
        "account_number": "***REDACTED***",
        "follow_up_notes": "***REDACTED***",
        "requestBody": "***REDACTED***",
        "headers": "***REDACTED***",
        "bypass": True,
        "compass": "north",
    }


@pytest.mark.parametrize("target_key", ["destination", "target", "extension", "queue", "mailbox"])
def test_strict_mode_redacts_transfer_targets_consistently(target_key):
    record = build_in_call_tool_record(
        call_id="call-transfer",
        tool_call_id="tool-transfer",
        tool_name="transfer_call",
        parameters={"action": "transfer", target_key: "private-destination"},
        result={"status": "success", "message": "Using private-destination"},
        redaction_mode="strict",
    )

    assert record["params"][target_key] == "***REDACTED***"
    assert record["target_id"] == "***REDACTED***"
    assert record["message"] == "Using ***REDACTED***"
    assert record["redacted_fields"] == [f"params.{target_key}", "target_id", "message"]
    assert record["action"] == "transfer"


@pytest.mark.parametrize("target_key", ["destination", "target", "extension", "queue", "mailbox"])
def test_show_routing_preserves_targets_but_redacts_pii_and_secrets(target_key):
    parameters = {
        "action": "transfer",
        target_key: "support-6000",
        "caller_email": "caller@example.com",
        "authorization": "Bearer secret-token",
    }
    record = build_in_call_tool_record(
        call_id="call-routing-visible",
        tool_call_id="tool-routing-visible",
        tool_name="blind_transfer",
        parameters=parameters,
        result={"status": "success", "message": "Routing support-6000 for caller@example.com"},
        redaction_mode="show_routing",
    )

    assert record["params"][target_key] == "support-6000"
    assert record["params"]["caller_email"] == "***REDACTED***"
    assert record["params"]["authorization"] == "***REDACTED***"
    assert record["target_id"] == "support-6000"
    assert record["message"] == "Routing support-6000 for ***REDACTED***"
    assert record["redaction_mode"] == "show_routing"
    assert record["redacted_fields"] == ["params.caller_email", "params.authorization", "message"]
    assert parameters["caller_email"] == "caller@example.com"


def test_off_mode_persists_diagnostics_verbatim_without_mutating_input():
    parameters = {
        "destination": "Support",
        "caller_email": "caller@example.com",
        "metadata": {"authorization": "Bearer secret-token"},
    }
    record = build_in_call_tool_record(
        call_id="call-off",
        tool_call_id="tool-off",
        tool_name="blind_transfer",
        parameters=parameters,
        result={"status": "success", "message": "Transfer Support for caller@example.com"},
        redaction_mode="off",
    )

    assert record["params"] == parameters
    assert record["params"] is not parameters
    assert record["params"]["metadata"] is not parameters["metadata"]
    assert record["target_id"] == "Support"
    assert record["message"] == "Transfer Support for caller@example.com"
    assert record["redaction_mode"] == "off"
    assert record["redacted_fields"] == []


def test_strict_mode_sanitizes_a_short_routing_value_only_as_a_token():
    record = build_in_call_tool_record(
        call_id="call-short-route",
        tool_call_id="tool-short-route",
        tool_name="blind_transfer",
        parameters={"destination": "1"},
        result={"status": "success", "message": "Transfer to 1; attempt 12 remains."},
        redaction_mode="strict",
    )

    assert record["message"] == "Transfer to ***REDACTED***; attempt 12 remains."


@pytest.mark.parametrize(
    ("sensitive_value", "message", "expected"),
    [
        (
            "Sam",
            "Samantha requested a callback; Sam confirmed.",
            "Samantha requested a callback; ***REDACTED*** confirmed.",
        ),
        (
            "6000",
            "Transfer to 6000; reference 16000 remains.",
            "Transfer to ***REDACTED***; reference 16000 remains.",
        ),
    ],
)
def test_strict_mode_sanitizes_longer_routing_values_only_as_tokens(
    sensitive_value,
    message,
    expected,
):
    record = build_in_call_tool_record(
        call_id="call-token-route",
        tool_call_id="tool-token-route",
        tool_name="blind_transfer",
        parameters={"destination": sensitive_value},
        result={"status": "success", "message": message},
        redaction_mode="strict",
    )

    assert record["message"] == expected


@pytest.mark.parametrize("configured", [None, "", "invalid", " STRICT "])
def test_redaction_mode_normalization_fails_closed(configured):
    expected = "strict"
    assert normalize_call_history_tool_redaction_mode(configured) == expected


def test_runtime_mode_is_resolved_from_environment(monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_TOOL_REDACTION_MODE", "show_routing")

    record = build_in_call_tool_record(
        call_id="call-env-policy",
        tool_call_id="tool-env-policy",
        tool_name="blind_transfer",
        parameters={"destination": "support", "caller_email": "caller@example.com"},
        result={"status": "success", "destination": "6000"},
    )

    assert record["redaction_mode"] == "show_routing"
    assert record["params"]["destination"] == "support"
    assert record["params"]["caller_email"] == "***REDACTED***"
    assert record["target_id"] == "6000"


def test_missing_ids_are_unique_per_invocation():
    first = stable_tool_call_id()
    second = stable_tool_call_id("")

    assert first.startswith("generated-")
    assert second.startswith("generated-")
    assert first != second


@pytest.mark.asyncio
async def test_retries_append_with_same_stable_tool_call_id_and_leave_transcript_unchanged():
    session = SimpleNamespace(tool_calls=[], conversation_history=[{"role": "user", "content": "book it"}])
    store = SimpleNamespace(
        get_by_call_id=AsyncMock(return_value=session),
        upsert_call=AsyncMock(),
    )

    for status in ("failed", "success"):
        await record_in_call_tool_result(
            session_store=store,
            call_id="call-1",
            tool_call_id="provider-retry-1",
            tool_name="google_calendar",
            parameters={"action": "create_event"},
            result={"status": status, "event_id": "event-42" if status == "success" else None},
        )

    assert [entry["tool_call_id"] for entry in session.tool_calls] == [
        "provider-retry-1",
        "provider-retry-1",
    ]
    assert [entry["status"] for entry in session.tool_calls] == ["failure", "success"]
    assert session.conversation_history == [{"role": "user", "content": "book it"}]
    assert store.upsert_call.await_count == 2


@pytest.mark.asyncio
async def test_record_construction_failure_is_best_effort():
    store = SimpleNamespace(
        get_by_call_id=AsyncMock(),
        upsert_call=AsyncMock(),
    )

    recorded = await record_in_call_tool_result(
        session_store=store,
        call_id="call-invalid-duration",
        tool_call_id="tool-invalid-duration",
        tool_name="lookup",
        parameters={},
        result={"status": "success"},
        duration_ms="not-a-number",
    )

    assert recorded is None
    store.get_by_call_id.assert_not_awaited()
    store.upsert_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_call_persists_enriched_tool_result(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    from src.core.call_history import CallHistoryStore, CallRecord

    tool_result = build_in_call_tool_record(
        call_id="completed-call",
        tool_call_id="provider-tool-9",
        tool_name="google_calendar",
        parameters={"action": "create_event"},
        result={"status": "success", "event_id": "event-9"},
    )
    store = CallHistoryStore(db_path=str(tmp_path / "tool-results.db"))
    now = datetime.now(timezone.utc)
    record = CallRecord(
        call_id="completed-call",
        start_time=now,
        end_time=now,
        outcome="completed",
        conversation_history=[{"role": "user", "content": "book it"}],
        tool_calls=[tool_result],
    )

    assert await store.save(record) is True
    persisted = await store.get_by_call_id("completed-call")

    assert persisted is not None
    assert persisted.outcome == "completed"
    assert persisted.tool_calls == [tool_result]
    assert persisted.conversation_history == [{"role": "user", "content": "book it"}]


@pytest.mark.asyncio
async def test_late_tool_result_does_not_resurrect_removed_session():
    store = SessionStore()
    session = CallSession(call_id="ended", caller_channel_id="ended")
    await store.upsert_call(session)
    await store.remove_call("ended")

    recorded = await record_in_call_tool_result(
        session_store=store,
        call_id="ended",
        tool_call_id="late-tool",
        tool_name="lookup",
        parameters={},
        result={"status": "success"},
    )

    assert recorded is None
    assert await store.get_by_call_id("ended") is None
    assert session.tool_calls == []
