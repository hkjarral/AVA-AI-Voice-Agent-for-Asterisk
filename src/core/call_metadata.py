"""Bounded, non-authoritative metadata carried by one active call.

Call metadata is deliberately separate from caller identity, routing, consent,
transfer, disposition, and external-dialer lifecycle state.  Only explicitly
selected pre-call outputs may enter this namespace.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Mapping


MAX_CALL_METADATA_FIELDS = 32
MAX_CALL_METADATA_KEY_CHARS = 64
MAX_CALL_METADATA_VALUE_CHARS = 1024
MAX_CALL_METADATA_TOTAL_BYTES = 16_384
MAX_CALL_METADATA_UPDATES = 128

_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_CREDENTIAL_RE = re.compile(
    r"(?:^|_)(?:api_?key|access_?token|refresh_?token|token|secret|password|"
    r"authorization|auth|credential|private_?key)(?:$|_)",
    re.IGNORECASE,
)

# These names map to authoritative engine/session state or built-in template
# variables.  Persisting a same-named enrichment value would create ambiguous
# precedence and could accidentally turn a convenience field into call control.
RESERVED_CALL_METADATA_KEYS = frozenset(
    {
        "call_id",
        "caller_id",
        "caller_name",
        "caller_number",
        "called_number",
        "context_name",
        "call_direction",
        "campaign_id",
        "lead_id",
        "provider",
        "provider_name",
        "routing_method",
        "transfer_destination",
        "disposition",
        "external_disposition",
        "consent",
        "dnc",
        "do_not_call",
        "call_outcome",
        "status",
        "current_date",
        "current_weekday",
        "current_time",
        "current_datetime_iso",
        "today",
    }
)


class CallMetadataValidationError(ValueError):
    """Raised when metadata configuration or a value violates its boundary."""


def _tokenize_camel_case(value: str) -> str:
    """Insert separators at camel/Pascal-case boundaries in linear time."""
    tokenized: list[str] = []
    for index, character in enumerate(value):
        previous = value[index - 1] if index else ""
        following = value[index + 1] if index + 1 < len(value) else ""
        starts_word = character.isupper() and (
            previous.islower()
            or previous.isdigit()
            or (previous.isupper() and following.islower())
        )
        if tokenized and starts_word:
            tokenized.append("_")
        tokenized.append(character)
    return "".join(tokenized)


def validate_call_metadata_key(key: Any) -> str:
    normalized = str(key or "").strip()
    if not _KEY_RE.fullmatch(normalized):
        raise CallMetadataValidationError(
            "metadata field names must start with a letter and contain only "
            f"letters, digits, and underscores ({MAX_CALL_METADATA_KEY_CHARS} characters max)"
        )
    if normalized.lower() in RESERVED_CALL_METADATA_KEYS:
        raise CallMetadataValidationError(
            f"'{normalized}' is reserved for authoritative call state"
        )
    # Split camel/Pascal-case boundaries before checking credential tokens so
    # names such as ``customerAccessToken`` and ``crmPassword`` receive the
    # same treatment as their snake_case equivalents.
    credential_key = _tokenize_camel_case(normalized)
    if _CREDENTIAL_RE.search(credential_key):
        raise CallMetadataValidationError(
            f"'{normalized}' looks credential-related and cannot be persisted"
        )
    return normalized


def normalize_call_metadata_policy(
    raw: Any,
    *,
    output_variables: Mapping[str, Any] | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Validate the per-output persistence/correction policy from tool config."""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, Mapping):
        raise CallMetadataValidationError("call_metadata_fields must be an object")
    if len(raw) > MAX_CALL_METADATA_FIELDS:
        raise CallMetadataValidationError(
            f"call_metadata_fields supports at most {MAX_CALL_METADATA_FIELDS} fields"
        )

    known_outputs = set(output_variables or {})
    result: Dict[str, Dict[str, Any]] = {}
    for raw_key, raw_policy in raw.items():
        key = validate_call_metadata_key(raw_key)
        if output_variables is not None and key not in known_outputs:
            raise CallMetadataValidationError(
                f"call metadata field '{key}' is not a configured output variable"
            )
        if not isinstance(raw_policy, Mapping):
            raise CallMetadataValidationError(
                f"call metadata policy for '{key}' must be an object"
            )
        unknown = set(raw_policy) - {"persist", "correctable", "description", "max_length"}
        if unknown:
            raise CallMetadataValidationError(
                f"unsupported policy field(s) for '{key}': {', '.join(sorted(unknown))}"
            )
        for flag_name in ("persist", "correctable"):
            if flag_name in raw_policy and not isinstance(raw_policy[flag_name], bool):
                raise CallMetadataValidationError(
                    f"'{flag_name}' for '{key}' must be a boolean"
                )
        persist = raw_policy.get("persist", False)
        correctable = raw_policy.get("correctable", False)
        if correctable and not persist:
            raise CallMetadataValidationError(
                f"'{key}' cannot be correctable unless persistence is enabled"
            )
        if not persist:
            continue
        description = str(raw_policy.get("description") or "").strip()
        if len(description) > 240:
            raise CallMetadataValidationError(
                f"description for '{key}' must be 240 characters or fewer"
            )
        raw_max = raw_policy.get("max_length", MAX_CALL_METADATA_VALUE_CHARS)
        try:
            max_length = int(raw_max)
        except (TypeError, ValueError) as exc:
            raise CallMetadataValidationError(
                f"max_length for '{key}' must be an integer"
            ) from exc
        if max_length < 1 or max_length > MAX_CALL_METADATA_VALUE_CHARS:
            raise CallMetadataValidationError(
                f"max_length for '{key}' must be between 1 and {MAX_CALL_METADATA_VALUE_CHARS}"
            )
        result[key] = {
            "persist": True,
            "correctable": correctable,
            "description": description,
            "max_length": max_length,
        }
    return result


def normalize_call_metadata_value(value: Any, *, max_length: int) -> str:
    """Return a bounded scalar string; reject containers and oversized values."""
    if isinstance(value, (dict, list, tuple, set)):
        raise CallMetadataValidationError("call metadata values must be scalar")
    if value is None:
        normalized = ""
    elif isinstance(value, (str, int, float, bool)):
        normalized = str(value)
    else:
        raise CallMetadataValidationError("call metadata values must be scalar")
    if len(normalized) > max_length:
        raise CallMetadataValidationError(
            f"call metadata value exceeds the {max_length}-character limit"
        )
    return normalized


def validate_call_metadata_document(values: Mapping[str, Any]) -> Dict[str, str]:
    """Validate the complete persisted object and enforce its aggregate bound."""
    if not isinstance(values, Mapping):
        raise CallMetadataValidationError("call metadata must be an object")
    if len(values) > MAX_CALL_METADATA_FIELDS:
        raise CallMetadataValidationError(
            f"call metadata supports at most {MAX_CALL_METADATA_FIELDS} fields"
        )
    normalized: Dict[str, str] = {}
    for raw_key, value in values.items():
        key = validate_call_metadata_key(raw_key)
        normalized[key] = normalize_call_metadata_value(
            value,
            max_length=MAX_CALL_METADATA_VALUE_CHARS,
        )
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_CALL_METADATA_TOTAL_BYTES:
        raise CallMetadataValidationError(
            f"call metadata exceeds the {MAX_CALL_METADATA_TOTAL_BYTES}-byte total limit"
        )
    return normalized


def normalize_call_metadata_updates(raw: Any) -> list[Dict[str, str]]:
    """Validate the value-free provenance audit stored with Call History."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise CallMetadataValidationError("call metadata updates must be an array")
    normalized: list[Dict[str, str]] = []
    for entry in raw[-MAX_CALL_METADATA_UPDATES:]:
        if not isinstance(entry, Mapping):
            raise CallMetadataValidationError("call metadata update entries must be objects")
        if set(entry) - {"field", "source", "updated_at"}:
            raise CallMetadataValidationError("call metadata update entries contain unsupported fields")
        field = validate_call_metadata_key(entry.get("field"))
        source = str(entry.get("source") or "").strip()
        if source != "agent_correction":
            raise CallMetadataValidationError("unsupported call metadata update source")
        updated_at = str(entry.get("updated_at") or "").strip()
        if len(updated_at) > 64:
            raise CallMetadataValidationError("call metadata update timestamp is too long")
        item = {"field": field, "source": source}
        if updated_at:
            item["updated_at"] = updated_at
        normalized.append(item)
    return normalized


def call_metadata_json_path(key: Any) -> str:
    """Return a safe SQLite JSON path for a previously validated field name."""
    return f"$.{validate_call_metadata_key(key)}"
