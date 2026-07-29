"""Stable, queryable history records for terminal in-call tool results."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

import structlog


logger = structlog.get_logger(__name__)

_FAILURE_STATUSES = {
    "blocked",
    "cancelled",
    "canceled",
    "denied",
    "disabled",
    "error",
    "failed",
    "failure",
    "no_transfer",
    "queue_fallback",
    "skipped",
    "timeout",
}
_SUCCESS_STATUSES = {"completed", "ok", "queued", "success", "transferred"}
_TARGET_ID_KEYS = (
    "target_id",
    "event_id",
    "resource_id",
    "id",
    "destination",
    "target",
    "extension",
    "queue",
    "mailbox",
)
_REDACTED_PARAMETER_VALUE = "***REDACTED***"
CALL_HISTORY_TOOL_REDACTION_MODES = frozenset({"strict", "show_routing", "off"})
_DEFAULT_TOOL_REDACTION_MODE = "strict"
_warned_invalid_redaction_modes: set[str] = set()

_SECRET_PARAMETER_KEYS = {
    # Credentials and authorization material. This follows the repository-wide
    # logging and HTTP-diagnostic redaction posture without substring matches
    # that would turn ordinary keys such as ``bypass`` into false positives.
    "api_key",
    "apikey",
    "api_keys",
    "token",
    "access_token",
    "refresh_token",
    "auth_token",
    "bearer",
    "password",
    "passwd",
    "pwd",
    "pass",
    "authorization",
    "auth",
    "credential",
    "credentials",
    "secret",
    "secrets",
    "private_key",
    "client_secret",
    "cookie",
    "cookies",
    "header",
    "headers",
}
_PII_PARAMETER_KEYS = {
    # Caller-supplied PII and free text accepted by built-in in-call tools.
    "name",
    "first_name",
    "last_name",
    "full_name",
    "customer_name",
    "caller_name",
    "contact_name",
    "email",
    "email_address",
    "caller_email",
    "recipient_email",
    "phone",
    "phone_number",
    "mobile",
    "telephone",
    "address",
    "street_address",
    "ssn",
    "social_security_number",
    "dob",
    "date_of_birth",
    "account_number",
    "comment",
    "comments",
    "callback_comments",
    "note",
    "notes",
    "summary",
    "description",
    "farewell_message",
    "message",
    "body",
    "query",
    "prompt",
}
_ROUTING_PARAMETER_KEYS = {
    # Telephony routing targets. Operators may expose these while retaining
    # credential and caller-PII redaction via ``show_routing`` mode.
    "destination",
    "target",
    "extension",
    "device_state_id",
    "queue",
    "mailbox",
}
_SECRET_PARAMETER_SUFFIXES = {
    "api_key",
    "token",
    "secret",
    "password",
    "passwd",
    "pwd",
    "pass",
    "authorization",
    "auth",
    "credential",
    "credentials",
    "private_key",
    "client_secret",
    "cookie",
}
_PII_PARAMETER_SUFFIXES = {
    "name",
    "email",
    "phone",
    "mobile",
    "telephone",
    "address",
    "ssn",
    "dob",
    "account_number",
    "comment",
    "comments",
    "note",
    "notes",
    "message",
    "body",
    "query",
    "prompt",
}
_ROUTING_PARAMETER_SUFFIXES = {
    "destination",
    "target",
    "extension",
    "queue",
    "mailbox",
}
_ROUTING_TARGET_ID_KEYS = frozenset(_ROUTING_PARAMETER_KEYS.intersection(_TARGET_ID_KEYS))


def stable_tool_call_id(value: Any = None) -> str:
    """Return an upstream tool-call id or create a unique per-invocation fallback."""
    candidate = str(value or "").strip()
    return candidate or f"generated-{uuid4().hex}"


def normalize_tool_terminal_status(result: Any) -> str:
    """Reduce provider/tool-specific outcomes to the terminal reporting contract.

    Unknown or malformed outcomes fail closed.  A completed invocation must not
    be reported as successful unless its result matches an explicit success
    signal; the persisted contract intentionally remains binary so downstream
    reducers never have to interpret provider-specific intermediate states.
    """
    if isinstance(result, dict):
        raw_status = str(result.get("status") or "").strip().lower()
        if raw_status in _FAILURE_STATUSES:
            return "failure"
        if raw_status in _SUCCESS_STATUSES:
            return "success"
        if isinstance(result.get("success"), bool):
            return "success" if result["success"] else "failure"
        if result.get("error"):
            return "failure"
    return "failure"


def _scalar_identifier(value: Any) -> Optional[str]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int, float)):
        candidate = str(value).strip()
        if candidate:
            return candidate[:512]
    return None


def normalize_call_history_tool_redaction_mode(value: Any, *, warn: bool = False) -> str:
    """Return a supported mode, failing closed to ``strict`` when invalid."""
    candidate = str(value or "").strip().lower()
    if not candidate:
        return _DEFAULT_TOOL_REDACTION_MODE
    if candidate in CALL_HISTORY_TOOL_REDACTION_MODES:
        return candidate
    if warn and candidate not in _warned_invalid_redaction_modes:
        _warned_invalid_redaction_modes.add(candidate)
        logger.warning(
            "Invalid call-history tool redaction mode; using strict",
            configured_mode=candidate,
            supported_modes=sorted(CALL_HISTORY_TOOL_REDACTION_MODES),
        )
    return _DEFAULT_TOOL_REDACTION_MODE


def resolve_call_history_tool_redaction_mode() -> str:
    """Resolve the effective runtime policy from the AI Engine environment."""
    return normalize_call_history_tool_redaction_mode(
        os.getenv("CALL_HISTORY_TOOL_REDACTION_MODE", _DEFAULT_TOOL_REDACTION_MODE),
        warn=True,
    )


def _normalized_parameter_key(key: Any) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key)).lower().replace("-", "_")


def _matches_key_family(normalized: str, keys: set[str], suffixes: set[str]) -> bool:
    if normalized in keys:
        return True
    return any(normalized.endswith(f"_{suffix}") for suffix in suffixes)


def _parameter_key_family(key: Any) -> Optional[str]:
    normalized = _normalized_parameter_key(key)
    if _matches_key_family(normalized, _SECRET_PARAMETER_KEYS, _SECRET_PARAMETER_SUFFIXES):
        return "secret"
    if _matches_key_family(normalized, _PII_PARAMETER_KEYS, _PII_PARAMETER_SUFFIXES):
        return "pii"
    if _matches_key_family(normalized, _ROUTING_PARAMETER_KEYS, _ROUTING_PARAMETER_SUFFIXES):
        return "routing"
    return None


def _is_sensitive_parameter_key(key: Any, mode: str = _DEFAULT_TOOL_REDACTION_MODE) -> bool:
    if mode == "off":
        return False
    family = _parameter_key_family(key)
    if family in {"secret", "pii"}:
        return True
    return family == "routing" and mode == "strict"


def _sensitive_scalar_values(value: Any) -> list[str]:
    """Collect scalar strings from a redacted value for message echo removal."""
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_sensitive_scalar_values(item))
        return values
    if isinstance(value, (list, tuple)):
        values = []
        for item in value:
            values.extend(_sensitive_scalar_values(item))
        return values
    if isinstance(value, bool) or value is None:
        return []
    candidate = str(value)
    return [candidate] if candidate else []


def _redact_message_echoes(message: Any, sensitive_values: list[str]) -> tuple[str, bool]:
    rendered = str(message or "")
    if not rendered or not sensitive_values:
        return rendered, False

    changed = False
    for candidate in sorted(set(sensitive_values), key=len, reverse=True):
        # Replace exact token echoes without corrupting words or larger numbers
        # that merely contain the sensitive value as a substring.
        rendered, replacements = re.subn(
            rf"(?<!\w){re.escape(candidate)}(?!\w)",
            _REDACTED_PARAMETER_VALUE,
            rendered,
        )
        changed = changed or replacements > 0
    return rendered, changed


def _redact_parameter_value(value: Any) -> Any:
    if value is None:
        return None
    if value == "":
        return ""
    return _REDACTED_PARAMETER_VALUE


def _sanitize_persisted_parameters(
    value: Any,
    *,
    mode: str = _DEFAULT_TOOL_REDACTION_MODE,
    path: str = "params",
) -> tuple[Any, list[str], list[str]]:
    """Copy parameters and return sanitized data, redacted paths, and echo values."""
    if isinstance(value, dict):
        sanitized: Dict[Any, Any] = {}
        redacted_fields: list[str] = []
        sensitive_values: list[str] = []
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if _is_sensitive_parameter_key(key, mode):
                sanitized[key] = _redact_parameter_value(item)
                if item not in (None, ""):
                    redacted_fields.append(item_path)
                    sensitive_values.extend(_sensitive_scalar_values(item))
                continue
            copied, nested_fields, nested_values = _sanitize_persisted_parameters(
                item,
                mode=mode,
                path=item_path,
            )
            sanitized[key] = copied
            redacted_fields.extend(nested_fields)
            sensitive_values.extend(nested_values)
        return sanitized, redacted_fields, sensitive_values
    if isinstance(value, list):
        sanitized_list = []
        redacted_fields = []
        sensitive_values = []
        for index, item in enumerate(value):
            copied, nested_fields, nested_values = _sanitize_persisted_parameters(
                item,
                mode=mode,
                path=f"{path}[{index}]",
            )
            sanitized_list.append(copied)
            redacted_fields.extend(nested_fields)
            sensitive_values.extend(nested_values)
        return sanitized_list, redacted_fields, sensitive_values
    if isinstance(value, tuple):
        copied, redacted_fields, sensitive_values = _sanitize_persisted_parameters(
            list(value),
            mode=mode,
            path=path,
        )
        return tuple(copied), redacted_fields, sensitive_values
    return value, [], []


def _target_id(parameters: Any, result: Any) -> tuple[Optional[str], Optional[str]]:
    # A created resource id in the result is authoritative. For compensating
    # operations (for example calendar/delete_event), fall back to the same id
    # supplied as a parameter so reducers can reconcile create/delete pairs.
    for source in (result, parameters):
        if not isinstance(source, dict):
            continue
        for key in _TARGET_ID_KEYS:
            candidate = _scalar_identifier(source.get(key))
            if candidate:
                return candidate, key
    return None, None


def build_in_call_tool_record(
    *,
    call_id: str,
    tool_call_id: Any,
    tool_name: str,
    parameters: Any,
    result: Any,
    duration_ms: float = 0.0,
    canonical_name: Optional[str] = None,
    redaction_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the additive v7.5.3 in-call tool-result contract.

    Existing ``name``, ``params``, ``result``, ``message``, ``timestamp`` and
    ``duration_ms`` fields remain intact for API/UI compatibility. New fields
    make the append-only stream reducible without mixing telemetry into the
    transcript-only ``conversation_history``.
    """
    mode = (
        resolve_call_history_tool_redaction_mode()
        if redaction_mode is None
        else normalize_call_history_tool_redaction_mode(redaction_mode)
    )
    execution_params = parameters if isinstance(parameters, dict) else {}
    params, redacted_fields, sensitive_values = _sanitize_persisted_parameters(
        execution_params,
        mode=mode,
    )
    result_dict = result if isinstance(result, dict) else {}
    raw_status = str(result_dict.get("status") or "").strip()
    terminal_status = normalize_tool_terminal_status(result)
    name = str(canonical_name or tool_name or "unknown").strip() or "unknown"
    action = _scalar_identifier(execution_params.get("action"))
    if not action:
        action = _scalar_identifier(result_dict.get("action"))
    if not action:
        action = name

    target_id, target_key = _target_id(execution_params, result_dict)
    if target_id and target_key in _ROUTING_TARGET_ID_KEYS and mode == "strict":
        sensitive_values.append(target_id)
        target_id = _REDACTED_PARAMETER_VALUE
        redacted_fields.append("target_id")

    message = result_dict.get("message")
    if message is None and not isinstance(result, dict):
        message = str(result)
    message, message_redacted = _redact_message_echoes(message, sensitive_values)
    if message_redacted:
        redacted_fields.append("message")

    return {
        "type": "tool_result",
        "call_id": str(call_id or ""),
        "tool_call_id": stable_tool_call_id(tool_call_id),
        "name": name,
        "action": action,
        "status": terminal_status,
        "target_id": target_id,
        "params": params,
        "result": raw_status or terminal_status,
        "message": message,
        "redaction_mode": mode,
        "redacted_fields": list(dict.fromkeys(redacted_fields)),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round(max(0.0, float(duration_ms or 0.0)), 2),
    }


async def record_in_call_tool_result(
    *,
    session_store: Any,
    call_id: str,
    tool_call_id: Any,
    tool_name: str,
    parameters: Any,
    result: Any,
    duration_ms: float = 0.0,
    canonical_name: Optional[str] = None,
    redaction_mode: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Append one terminal result to ``CallSession.tool_calls`` best-effort.

    Telemetry failure must never alter call flow. The tool result is recorded
    before provider delivery, so a failed provider response and retry retain
    the original execution fact under the upstream ``tool_call_id``.
    """
    record: Optional[Dict[str, Any]] = None
    try:
        record = build_in_call_tool_record(
            call_id=call_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            parameters=parameters,
            result=result,
            duration_ms=duration_ms,
            canonical_name=canonical_name,
            redaction_mode=redaction_mode,
        )
        if session_store is None:
            return None
        atomic_append = getattr(session_store, "append_tool_call_if_active", None)
        if callable(atomic_append):
            if not await atomic_append(call_id, record):
                logger.debug(
                    "Tool result history skipped; session no longer active",
                    call_id=call_id,
                    tool=record["name"],
                    tool_call_id=record["tool_call_id"],
                )
                return None
            return record

        # Lightweight test/custom stores may not expose the atomic helper.
        # They still never receive the retained CallSession fallback that
        # previously resurrected completed production sessions.
        current = await session_store.get_by_call_id(call_id)
        if current is None:
            logger.debug(
                "Tool result history skipped; session not found",
                call_id=call_id,
                tool=record["name"],
                tool_call_id=record["tool_call_id"],
            )
            return None
        if getattr(current, "tool_calls", None) is None:
            current.tool_calls = []
        current.tool_calls.append(record)
        await session_store.upsert_call(current)
        logger.debug(
            "Tool result recorded in call history",
            call_id=call_id,
            tool=record["name"],
            tool_call_id=record["tool_call_id"],
            status=record["status"],
            target_id=record["target_id"],
        )
        return record
    except Exception:
        logger.debug(
            "Failed to record tool result in call history",
            call_id=call_id,
            tool=record["name"] if record else str(canonical_name or tool_name or ""),
            tool_call_id=record["tool_call_id"] if record else str(tool_call_id or ""),
            exc_info=True,
        )
        return None
