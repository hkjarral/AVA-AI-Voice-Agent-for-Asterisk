"""Privacy-safe support packages for calls and system diagnostics."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents_store import AgentsStore
from api.log_events import parse_log_line
from api.logs import _compute_related_ids, _event_matches_call, _read_container_logs_sync

router = APIRouter()

MAX_CONTAINER_BYTES = 5 * 1024 * 1024
CORRELATION_MAX_BYTES = 64 * 1024 * 1024
MAX_SYSTEM_HOURS = 24
CALL_WINDOW_PAD_SECONDS = 15


class CallBundleRequest(BaseModel):
    call_id: str = Field(min_length=1, max_length=160)
    include_local_ai_server: bool = True
    include_admin_ui: bool = True
    include_transcript: bool = True
    include_tools: bool = True
    include_settings: bool = True


class SystemBundleRequest(BaseModel):
    hours: int = Field(default=1, ge=1, le=MAX_SYSTEM_HOURS)
    include_ai_engine: bool = True
    include_local_ai_server: bool = True
    include_admin_ui: bool = True
    include_config: bool = True


def _store() -> AgentsStore:
    return AgentsStore()


def _call_store():
    from src.core.call_history import get_call_history_store

    return get_call_history_store()


KEEP = {
    "id", "slug", "provider", "voice", "audio_profile", "is_operator_managed",
    "is_active", "is_default", "source_file", "created_at", "updated_at",
}
REDACT_LABEL = {
    "display_name": "name", "extension": "ext", "role_label": "role",
    "greeting": "greeting", "prompt": "prompt", "notes": "notes",
}


def _redact(value: Any, label: str):
    if value is None:
        return None
    text = str(value)
    sha = hashlib.sha256(text.encode()).hexdigest()[:12]
    return f"[{label} len={len(text)} sha={sha}]"


def _structure_only(raw: Any):
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return ["<unparseable>"]
    if isinstance(data, list):
        return [
            item.get("name", "<tool>") if isinstance(item, dict)
            else f"<tool len={len(item)}>" if isinstance(item, str)
            else "<tool>"
            for item in data
        ]
    if isinstance(data, dict):
        return sorted(data.keys())
    return ["<unknown>"]


def redact_agent(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if key in KEEP:
            out[key] = value
        elif key in REDACT_LABEL:
            out[key] = _redact(value, REDACT_LABEL[key])
        elif key in ("tools_json", "mcp_json", "extra_json"):
            out[key] = _structure_only(value)
    return out


_SENSITIVE_KEY_PARTS = {
    "api_key", "apikey", "password", "passwd", "secret", "authorization",
    "auth_token", "access_token", "refresh_token", "private_key", "credential",
    "cookie", "client_secret", "prompt", "greeting", "instructions",
    "system_message", "notes",
}
_IDENTITY_KEY_PARTS = {
    "caller_number", "caller_name", "called_number", "phone", "telephone",
    "contact_number", "customer_name",
}
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+")
_URL_SECRET_RE = re.compile(r"(?i)([?&](?:api[_-]?key|token|secret|password|signature)=)[^&\s]+")
_KV_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|secret|authorization)"
    r"(\s*[=:]\s*)(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,}\]]+)"
)
_KV_PRIVATE_TEXT_RE = re.compile(
    r"(?i)\b(prompt|greeting|instructions|system_message|notes)"
    r"(\s*=\s*)(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
)
_KV_IDENTITY_RE = re.compile(
    r"(?i)\b(caller_number|caller_name|called_number|phone_number)"
    r"(\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,}\]]+)"
)
_CALL_ID_RE = re.compile(r"\b\d{9,12}\.\d+\b")
_DATETIME_RE = re.compile(
    r"\b(?:"
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|\d{8}-\d{6}"
    r")\b"
)
_IPV4_RE = re.compile(
    r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b"
)
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\w.])\+?\d[\d() .-]{7,}\d(?![\w.])")


def _sensitive_key(key: str) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _identity_key(key: str) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(part in normalized for part in _IDENTITY_KEY_PARTS)


def sanitize_text(value: str) -> str:
    raw_text = str(value or "")
    if "\n" in raw_text:
        trailing_newline = raw_text.endswith("\n")
        sanitized_lines = "\n".join(sanitize_text(line) for line in raw_text.splitlines())
        return sanitized_lines + ("\n" if trailing_newline else "")

    stripped = raw_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
            return json.dumps(sanitize_value(parsed), sort_keys=True, default=str)
        except (json.JSONDecodeError, TypeError):
            pass

    protected: Dict[str, str] = {}

    def protect_diagnostic_value(match: re.Match) -> str:
        token = f"__AVA_SAFE_VALUE_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    def redact_phone(match: re.Match) -> str:
        candidate = match.group(0)
        return "[PHONE_REDACTED]" if sum(char.isdigit() for char in candidate) >= 10 else candidate

    # Preserve diagnostic identifiers and time anchors before applying the broad
    # phone-number fallback. ISO timestamps, calendar slots, and IPv4 addresses
    # contain enough digits to otherwise look like telephone numbers.
    text = _CALL_ID_RE.sub(protect_diagnostic_value, raw_text)
    text = _DATETIME_RE.sub(protect_diagnostic_value, text)
    text = _IPV4_RE.sub(protect_diagnostic_value, text)
    text = _EMAIL_RE.sub("[EMAIL_REDACTED]", text)
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _URL_SECRET_RE.sub(r"\1[REDACTED]", text)
    text = _KV_SECRET_RE.sub(r"\1\2[REDACTED]", text)
    text = _KV_PRIVATE_TEXT_RE.sub(r"\1\2[REDACTED]", text)
    text = _KV_IDENTITY_RE.sub(r"\1\2[IDENTITY_REDACTED]", text)
    text = _PHONE_CANDIDATE_RE.sub(redact_phone, text)
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


def sanitize_value(value: Any, key: str = "") -> Any:
    if _sensitive_key(key):
        return "[REDACTED]" if value not in (None, "") else value
    if _identity_key(key):
        return "[IDENTITY_REDACTED]" if value not in (None, "") else value
    if isinstance(value, dict):
        return {str(k): sanitize_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def _call_number_values(call: Any) -> list[str]:
    values = []
    for field in ("caller_number", "called_number"):
        value = str(getattr(call, field, "") or "").strip()
        if value and value.lower() not in {"unknown", "anonymous", "unavailable"}:
            values.append(value)
    return values


def _sanitize_call_text(value: str, call: Any) -> str:
    text = sanitize_text(value)
    for number in _call_number_values(call):
        text = re.sub(
            rf"(?<!\w){re.escape(number)}(?!\w)",
            "[CALL_NUMBER_REDACTED]",
            text,
        )
    caller_name = str(getattr(call, "caller_name", "") or "").strip()
    if len(caller_name) >= 3 and caller_name.lower() not in {"unknown", "anonymous", "unavailable"}:
        text = re.sub(re.escape(caller_name), "[CALLER_NAME_REDACTED]", text, flags=re.IGNORECASE)
    return text


def _sanitize_for_call(value: Any, call: Any, key: str = "") -> Any:
    if _sensitive_key(key):
        return "[REDACTED]" if value not in (None, "") else value
    if _identity_key(key):
        return "[IDENTITY_REDACTED]" if value not in (None, "") else value
    if isinstance(value, dict):
        return {str(k): _sanitize_for_call(v, call, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_call(item, call) for item in value]
    if isinstance(value, str):
        return _sanitize_call_text(value, call)
    if value is not None and str(value) in _call_number_values(call):
        return "[CALL_NUMBER_REDACTED]"
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True, default=str).encode("utf-8")


def _bounded_text(raw: bytes, limit: int = MAX_CONTAINER_BYTES) -> Tuple[str, bool, int]:
    original_size = len(raw or b"")
    if original_size <= limit:
        return (raw or b"").decode("utf-8", errors="replace"), False, original_size
    half = limit // 2
    clipped = (raw or b"")[:half] + b"\n... [TRUNCATED BY SUPPORT EXPORT] ...\n" + (raw or b"")[-half:]
    return clipped.decode("utf-8", errors="replace"), True, original_size


async def _read_window(container: str, since: int, until: int, *, limit: int | None = MAX_CONTAINER_BYTES):
    try:
        raw, container_id, resolved_name = await asyncio.to_thread(
            _read_container_logs_sync, container, tail=None, since=since, until=until
        )
        if limit is None:
            text = (raw or b"").decode("utf-8", errors="replace")
            truncated = False
            original_size = len(raw or b"")
        else:
            text, truncated, original_size = _bounded_text(raw, limit=limit)
        return text, {
            "container": resolved_name,
            "container_id": container_id[:12],
            "available": True,
            "truncated": truncated,
            "original_bytes": original_size,
            "exported_bytes": len(text.encode("utf-8")),
        }
    except Exception:
        return "", {
            "container": container,
            "available": False,
            "truncated": False,
            "reason": "Container logs were unavailable.",
        }


def _parsed(text: str):
    parsed = []
    for line in text.splitlines():
        item = parse_log_line(line)
        if item:
            parsed.append(item)
    return parsed


def _filter_call_lines(text: str, call_id: str):
    parsed = _parsed(text)
    related_ids, bridge_ids = _compute_related_ids(parsed, call_id)
    wanted_ids = set(related_ids or [call_id])
    wanted_bridges = set(bridge_ids)
    matching = []
    events = []
    for event, fields in parsed:
        if _event_matches_call(event, fields, wanted_ids, wanted_bridges):
            matching.append(sanitize_text(event.raw))
            events.append(event)
    known_raw = {event.raw for event in events}
    for line in text.splitlines():
        if call_id in line and line not in known_raw:
            matching.append(sanitize_text(line))
    return "\n".join(dict.fromkeys(matching)), events, related_ids, bridge_ids


def _filter_secondary_source(text: str, call_id: str) -> str:
    """Keep optional container evidence scoped to the selected call."""
    return "\n".join(
        sanitize_text(line)
        for line in text.splitlines()
        if call_id in line
    )


def _event_format_and_levels(text: str):
    json_lines = 0
    text_lines = 0
    levels = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("{"):
            json_lines += 1
        else:
            text_lines += 1
        parsed = parse_log_line(line)
        if parsed:
            levels.add(parsed[0].level)
    fmt = (
        "none" if not (json_lines or text_lines)
        else "mixed" if json_lines and text_lines
        else "json" if json_lines
        else "console"
    )
    return fmt, sorted(levels)


_LIFECYCLE_STAGES = (
    ("Call started", ("stasisstart", "caller channel entered stasis", "rca_call_start")),
    ("Provider connected", ("provider session started", "session started", "websocket connected", "setup complete")),
    ("Audio transport ready", ("audiosocket connection", "externalmedia channel created", "transportcard", "media websocket")),
    ("Tools captured", ("executing pre-call", "tool executed", "executing post-call tools", "post-call tool")),
    ("Call ended", ("rca_call_end", "call cleanup completed", "stasis ended")),
)


def _analysis(call: Any, events: list, log_text: str) -> Dict[str, Any]:
    warnings = [event for event in events if event.level == "warning"]
    errors = [event for event in events if event.level in ("error", "critical")]
    lower = log_text.lower()
    tools_used = any(
        getattr(call, field, []) or []
        for field in ("pre_call_tool_calls", "tool_calls", "post_call_tool_calls")
    )
    lifecycle = []
    for label, patterns in _LIFECYCLE_STAGES:
        captured = any(pattern in lower for pattern in patterns)
        if label == "Tools captured" and not tools_used:
            captured = True
        lifecycle.append({"name": label, "captured": captured})
    findings = [
        {
            "severity": "error" if event.level in ("error", "critical") else "warning",
            "message": _sanitize_call_text(event.msg, call),
            "component": event.component,
        }
        for event in [*errors, *warnings][:10]
    ]
    outcome = str(getattr(call, "outcome", "") or "").lower()
    call_error = _sanitize_call_text(getattr(call, "error_message", "") or "", call)
    failed_outcome = outcome in {"error", "abandoned", "no_input_timeout", "failed"}
    if failed_outcome or call_error:
        findings.insert(0, {
            "severity": "error",
            "message": call_error or f"Call History recorded outcome: {outcome}",
            "component": "call_history",
        })
    missing = [stage["name"] for stage in lifecycle if not stage["captured"]]
    if failed_outcome or call_error:
        status, headline = "issues_found", f"Call ended with {outcome or 'an error'}"
    elif errors:
        status, headline = "issues_found", f"{len(errors)} error event{'s' if len(errors) != 1 else ''} found"
    elif warnings:
        status, headline = "review", f"{len(warnings)} warning event{'s' if len(warnings) != 1 else ''} to review"
    elif missing:
        status, headline = "incomplete", "Some lifecycle evidence is unavailable"
    else:
        status, headline = "healthy", "No obvious call failure found"
    return {
        "status": status,
        "headline": headline,
        "event_count": len(events),
        "error_count": len(errors) + int(bool(failed_outcome or call_error)),
        "warning_count": len(warnings),
        "lifecycle": lifecycle,
        "findings": findings,
        "missing_evidence": missing,
        "recommendation": (
            "Review the Call History failure and captured error evidence first." if (failed_outcome or call_error)
            else "Review the captured error evidence first." if errors
            else "Review warnings in context; they may describe normal cleanup." if warnings
            else "The captured lifecycle appears complete." if not missing
            else "Use INFO or DEBUG logging on a new call for richer evidence."
        ),
        "outcome": getattr(call, "outcome", None),
    }


def _call_summary(call: Any) -> Dict[str, Any]:
    return sanitize_value({
        "call_id": call.call_id,
        "start_time": call.start_time.isoformat() if call.start_time else None,
        "end_time": call.end_time.isoformat() if call.end_time else None,
        "duration_seconds": call.duration_seconds,
        "provider_name": call.provider_name,
        "pipeline_name": call.pipeline_name,
        "pipeline_components": call.pipeline_components or {},
        "agent": call.context_name,
        "routing_method": call.routing_method,
        "voice": getattr(call, "voice", None),
        "voice_source": getattr(call, "voice_source", None),
        "outcome": call.outcome,
        "error_message": sanitize_text(call.error_message or "") or None,
        "transfer_occurred": bool(call.transfer_destination),
        "external_platform": getattr(call, "external_platform", None),
        "external_direction": getattr(call, "external_direction", None),
        "external_disposition": getattr(call, "external_disposition", None),
        "avg_turn_latency_ms": call.avg_turn_latency_ms,
        "max_turn_latency_ms": call.max_turn_latency_ms,
        "total_turns": call.total_turns,
        "caller_audio_format": call.caller_audio_format,
        "codec_alignment_ok": call.codec_alignment_ok,
        "barge_in_count": call.barge_in_count,
    })


def _tool_summary(call: Any) -> Dict[str, Any]:
    return _sanitize_for_call({
        "pre_call": getattr(call, "pre_call_tool_calls", []) or [],
        "in_call": getattr(call, "tool_calls", []) or [],
        "post_call": getattr(call, "post_call_tool_calls", []) or [],
    }, call)


def _source_recommendations(call: Any) -> Dict[str, Any]:
    snapshot = getattr(call, "diagnostics_snapshot", {}) or {}
    resolved = snapshot.get("resolved") or {}
    provider_kind = str(resolved.get("provider_kind") or call.provider_name or "").lower()
    components = resolved.get("pipeline_components") or call.pipeline_components or {}
    local_used = provider_kind == "local" or any("local" in str(value).lower() for value in components.values())
    return {
        "ai_engine": {"selected": True, "required": True, "reason": "Core lifecycle and call-correlated evidence."},
        "local_ai_server": {
            "selected": True,
            "required": False,
            "recommended": local_used,
            "reason": "Recommended because this call used local components." if local_used else "Probably not needed; no local components were resolved.",
        },
        "admin_ui": {
            "selected": True,
            "required": False,
            "recommended": False,
            "reason": "Optional configuration and management context.",
        },
    }


async def _call_evidence(call_id: str):
    call = await _call_store().get_by_call_id(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call record not found")
    start = call.start_time or datetime.now(timezone.utc)
    end = call.end_time or datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    since = int((start - timedelta(seconds=CALL_WINDOW_PAD_SECONDS)).timestamp())
    until = int((end + timedelta(seconds=CALL_WINDOW_PAD_SECONDS)).timestamp())
    # Use a larger bounded window for correlation before applying the smaller
    # per-file export cap. This keeps DEBUG-heavy calls useful without allowing
    # preview and bundle requests to decode an unbounded log window in memory.
    raw, meta = await _read_window(
        "ai_engine", since, until, limit=CORRELATION_MAX_BYTES
    )
    filtered, events, related_ids, bridge_ids = _filter_call_lines(raw, call_id)
    filtered = _sanitize_call_text(filtered, call)
    filtered, call_truncated, matched_original_size = _bounded_text(filtered.encode("utf-8"))
    fmt, levels = _event_format_and_levels(filtered)
    meta.update({
        "truncated": call_truncated,
        "matched_original_bytes": matched_original_size,
        "exported_bytes": len(filtered.encode("utf-8")),
        "format": fmt,
        "observed_levels": levels,
        "matching_events": len(events),
        "related_channel_count": max(0, len(related_ids) - 1),
        "related_bridge_count": len(bridge_ids),
    })
    return call, since, until, filtered, events, meta


@router.get("/support/call-preview")
async def call_preview(call_id: str = Query(min_length=1, max_length=160)):
    call, _since, _until, log_text, events, log_meta = await _call_evidence(call_id)
    return {
        "call": _call_summary(call),
        "analysis": _analysis(call, events, log_text),
        "settings": sanitize_value(getattr(call, "diagnostics_snapshot", {}) or {}),
        "tool_counts": {
            "pre_call": len(getattr(call, "pre_call_tool_calls", []) or []),
            "in_call": len(getattr(call, "tool_calls", []) or []),
            "post_call": len(getattr(call, "post_call_tool_calls", []) or []),
        },
        "log_evidence": log_meta,
        "sources": _source_recommendations(call),
    }


def _zip_response(buffer: io.BytesIO, filename: str, *, deprecated: bool = False):
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if deprecated:
        headers["Deprecation"] = "true"
        headers["Link"] = '</api/support/system-bundle>; rel="successor-version"'
        headers["X-AVA-Replacement"] = "POST /api/support/system-bundle"
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


@router.post("/support/call-bundle")
async def call_bundle(request: CallBundleRequest):
    call, since, until, ai_logs, events, ai_meta = await _call_evidence(request.call_id)
    generated_at = datetime.now(timezone.utc)
    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "bundle_type": "call_support",
        "generated_at": generated_at.isoformat(),
        "call_id": call.call_id,
        "window": {
            "since": datetime.fromtimestamp(since, tz=timezone.utc).isoformat(),
            "until": datetime.fromtimestamp(until, tz=timezone.utc).isoformat(),
            "padding_seconds": CALL_WINDOW_PAD_SECONDS,
        },
        "privacy": {
            "recordings_included": False,
            "phone_numbers_included": False,
            "credentials_included": False,
            "sanitized": True,
        },
        "sources": {"ai_engine": ai_meta},
        "omissions": [],
    }
    analysis = _analysis(call, events, ai_logs)
    fmt, levels = _event_format_and_levels(ai_logs)
    manifest["log_format"] = fmt
    manifest["observed_log_levels"] = levels

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.txt",
            "AVA call support package\n\n"
            "Attach this ZIP to a GitHub issue or share it with AVA support on Discord.\n"
            "It contains sanitized call lifecycle evidence, settings, and selected logs.\n"
            "It never contains recordings, phone numbers, API keys, passwords, or secrets.\n",
        )
        archive.writestr("call/summary.json", _json_bytes(_call_summary(call)))
        archive.writestr("analysis/summary.json", _json_bytes(analysis))
        archive.writestr("analysis/summary.txt", f"{analysis['headline']}\n\n{analysis['recommendation']}\n")
        archive.writestr("logs/ai_engine.log", sanitize_text(ai_logs))

        if request.include_settings:
            settings = getattr(call, "diagnostics_snapshot", {}) or {}
            if settings:
                archive.writestr("call/effective_settings.json", _json_bytes(_sanitize_for_call(settings, call)))
            else:
                manifest["omissions"].append("Call-time settings snapshot unavailable for this historical call.")
        if request.include_transcript:
            archive.writestr("call/conversation.json", _json_bytes(_sanitize_for_call(getattr(call, "conversation_history", []) or [], call)))
        if request.include_tools:
            archive.writestr("call/tool_executions.json", _json_bytes(_tool_summary(call)))

        for container, enabled in (
            ("local_ai_server", request.include_local_ai_server),
            ("admin_ui", request.include_admin_ui),
        ):
            if not enabled:
                manifest["sources"][container] = {"selected": False}
                continue
            text, meta = await _read_window(container, since, until)
            text = _filter_secondary_source(text, call.call_id)
            text = _sanitize_call_text(text, call)
            meta["matching_lines"] = len(text.splitlines()) if text else 0
            manifest["sources"][container] = meta
            if text:
                archive.writestr(f"logs/{container}.log", text)
            elif meta.get("available"):
                manifest["omissions"].append(f"No {container} entries referenced this call ID.")

        try:
            from api.system import get_basic_system_info

            archive.writestr("system/system_info.json", _json_bytes(get_basic_system_info()))
        except Exception:
            manifest["omissions"].append("Basic system information was unavailable.")
        archive.writestr("manifest.json", _json_bytes(manifest))

    safe_call_id = re.sub(r"[^A-Za-z0-9_.-]", "_", call.call_id)
    filename = f"ava-call-support-{safe_call_id}-{generated_at.strftime('%Y%m%d-%H%M%S')}.zip"
    return _zip_response(buf, filename)


def _system_bundle_sync(request: SystemBundleRequest, *, deprecated: bool = False):
    generated_at = datetime.now(timezone.utc)
    since = int((generated_at - timedelta(hours=request.hours)).timestamp())
    until = int(generated_at.timestamp())
    selected = {
        "ai_engine": request.include_ai_engine,
        "local_ai_server": request.include_local_ai_server,
        "admin_ui": request.include_admin_ui,
    }
    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "bundle_type": "system_diagnostics",
        "generated_at": generated_at.isoformat(),
        "window_hours": request.hours,
        "privacy": {
            "recordings_included": False,
            "phone_numbers_included": False,
            "credentials_included": False,
            "sanitized": True,
        },
        "sources": {},
        "omissions": [],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.txt",
            "AVA system diagnostics package\n\n"
            "This advanced export can include events from multiple calls. Logs and configuration are sanitized.\n"
            "Recordings, .env values, API keys, passwords, and secrets are never included.\n",
        )
        for container, enabled in selected.items():
            if not enabled:
                manifest["sources"][container] = {"selected": False}
                continue
            try:
                raw, container_id, resolved = _read_container_logs_sync(container, tail=None, since=since, until=until)
                text, truncated, original_size = _bounded_text(raw)
                archive.writestr(f"logs/{container}.log", sanitize_text(text))
                manifest["sources"][container] = {
                    "available": True,
                    "container": resolved,
                    "container_id": container_id[:12],
                    "truncated": truncated,
                    "original_bytes": original_size,
                }
            except Exception:
                manifest["sources"][container] = {"available": False, "reason": "Container logs were unavailable."}

        if request.include_config:
            try:
                from api.config import _read_merged_config_dict

                archive.writestr("config/current_config.sanitized.json", _json_bytes(sanitize_value(_read_merged_config_dict())))
            except Exception:
                manifest["omissions"].append("Sanitized configuration was unavailable and was omitted.")
        try:
            from api.system import get_basic_system_info

            archive.writestr("system/system_info.json", _json_bytes(get_basic_system_info()))
        except Exception:
            manifest["omissions"].append("Basic system information was unavailable.")
        archive.writestr("manifest.json", _json_bytes(manifest))
    filename = f"ava-system-diagnostics-{generated_at.strftime('%Y%m%d-%H%M%S')}.zip"
    return _zip_response(buf, filename, deprecated=deprecated)


@router.post("/support/system-bundle")
async def system_bundle(request: SystemBundleRequest):
    return await asyncio.to_thread(_system_bundle_sync, request)


@router.get("/support-bundle")
def support_bundle():
    """Legacy secret-safe agent/system bundle retained for compatibility."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        agents = [redact_agent(agent) for agent in _store().list_all()]
        archive.writestr("agents_redacted.json", _json_bytes(agents))
        archive.writestr(
            "BUNDLE_README.txt",
            "AVA sanitized support bundle. Contains: redacted agent metadata, system info. "
            "Never contains: .env, prompts, recordings, transcripts.",
        )
        try:
            from api.system import get_basic_system_info

            archive.writestr("system_info.json", _json_bytes(get_basic_system_info()))
        except Exception:
            archive.writestr("system_info.json", _json_bytes({"error": "unavailable"}))
    return _zip_response(buf, "ava-support-bundle.zip")
