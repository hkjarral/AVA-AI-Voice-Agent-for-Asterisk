"""Phase-aware HTTP template guidance and side-effect-free validation.

Examples are synthetic. Validation never reads credentials, calls an LLM, sends
HTTP, or edits configuration. Runtime and preview use the same serializers.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from .template import TemplateError, _PLACEHOLDER, render_body, render_headers, render_template, render_url
from .debug_trace import redact_headers

BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
PHASES = ("pre_call", "in_call", "post_call")
COMMON = {
    "call_id": ("string", "example-call-123", "AVA call identifier"),
    "caller_number": ("string", "+15551234567", "Caller number"),
    "called_number": ("string", "+18005551234", "Called number"),
    "caller_name": ("string", "Example Caller", "Caller ID name"),
    "context_name": ("string", "example-agent", "Selected Agent"),
}
CAMPAIGN = {
    "campaign_id": ("string", "example-campaign", "Campaign ID; empty for ordinary inbound calls"),
    "lead_id": ("string", "example-lead", "Lead ID; empty for ordinary inbound calls"),
}
TRANSCRIPT = {"transcript_json": ("array", '[{"role":"user","content":"Please connect me to support."}]', "Recorded AVA conversation; insert outside JSON quotes")}
SUMMARY = {
    "summary": ("string", "The caller requested support.", "Generated summary; use inside JSON quotes"),
    "summary_json": ("string (JSON encoded)", '"The caller requested support."', "Generated summary; insert outside JSON quotes"),
    "summary_status": ("string", "ok", "Summary generation status"),
    "summary_error_code": ("string", "", "Safe summary error code, if any"),
}
FINAL = {
    "provider": ("string", "example-provider", "Call provider"),
    "call_direction": ("string", "inbound", "Recorded call direction"),
    "call_duration": ("number", 120, "Final call duration in seconds"),
    "call_outcome": ("string", "transferred", "Recorded final AVA outcome"),
    "call_start_time": ("string", "2026-01-01T12:00:00Z", "Call start time"),
    "call_end_time": ("string", "2026-01-01T12:02:00Z", "Call end time"),
    "tool_calls_json": ("array", "[]", "Recorded in-call tool executions"),
    "pre_call_results_json": ("object", "{}", "Initial enrichment snapshot"),
    "call_metadata_json": ("object", "{}", "Final selected call metadata"),
    "schema_version": ("number", "1", "Webhook payload schema version"),
}
ALL_BUILTINS = {**COMMON, **CAMPAIGN, **TRANSCRIPT, **SUMMARY, **FINAL}
IN_CALL_RESERVED = frozenset({*COMMON, *TRANSCRIPT})


def variable_catalog(phase: str, generate_summary: bool = False) -> list[dict[str, Any]]:
    available = {**COMMON}
    if phase in {"pre_call", "post_call"}:
        available.update(CAMPAIGN)
    if phase in {"in_call", "post_call"}:
        available.update(TRANSCRIPT)
    if phase == "post_call":
        available.update(FINAL)
        # Post-call has always supported an existing/empty summary.
        available.update({key: SUMMARY[key] for key in ("summary", "summary_json")})
    if generate_summary and phase == "in_call":
        available.update(SUMMARY)
    return [dict(name=name, type=kind, example=example, description=description,
                 source="call", status="available")
            for name, (kind, example, description) in available.items()]


def example_body(phase: str, generate_summary: bool = False) -> str:
    fields = ['"schema_version": 1'] if phase == "post_call" else []
    if phase != "pre_call":
        event = "call_briefing" if phase == "in_call" else "call_completed"
        fields.append(f'"event_type": "{event}"')
    fields.extend(['"call_id": "{call_id}"', '"caller_number": "{caller_number}"'])
    if phase == "post_call":
        fields.extend(['"call_duration": {call_duration}', '"call_outcome": "{call_outcome}"'])
    if phase != "pre_call":
        fields.append('"transcript": {transcript_json}')
        if generate_summary:
            fields.append('"summary": {summary_json}')
    return "{\n  " + ",\n  ".join(fields) + "\n}"


def template_fields(config: Mapping[str, Any], phase: str) -> dict[str, str]:
    fields = {"url": str(config.get("url") or "")}
    fields.update({f"headers.{key}": str(value) for key, value in (config.get("headers") or {}).items()})
    if phase != "post_call":
        fields.update({f"query_params.{key}": str(value) for key, value in (config.get("query_params") or {}).items()})
    method = str(config.get("method") or ("GET" if phase == "pre_call" else "POST")).upper()
    if method in BODY_METHODS:
        field = "payload_template" if phase == "post_call" else "body_template"
        if config.get(field):
            fields[field] = str(config[field])
    return fields


def render_request(config: Mapping[str, Any], values: Mapping[str, Any], phase: str,
                   *, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Prepare an HTTP request without sending it. Never return raw values in errors."""
    url = render_url(str(config.get("url") or ""), values, environ=environ)
    headers = render_headers(config.get("headers") or {}, values, environ=environ)
    params = {key: render_template(value, values, environ=environ)
              for key, value in (config.get("query_params") or {}).items()} if phase != "post_call" else {}
    method = str(config.get("method") or ("GET" if phase == "pre_call" else "POST")).upper()
    body = None
    if method in BODY_METHODS:
        field = "payload_template" if phase == "post_call" else "body_template"
        if config.get(field):
            if not any(key.lower() == "content-type" for key in headers):
                headers["Content-Type"] = str(config.get("content_type") or "application/json")
            body = render_body(config[field], values, headers, environ=environ)
    return {"url": url, "headers": headers, "query_params": params, "body": body}


def validate_template(config: Mapping[str, Any], phase: str) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError("Invalid HTTP tool phase")
    catalog = variable_catalog(phase, bool(config.get("generate_summary")))
    values = {item["name"]: item["example"] for item in catalog}
    reserved = set(values)
    issues: list[dict[str, Any]] = []
    if config.get("generate_summary") and phase == "pre_call":
        issues.append(dict(severity="error", error_code="summary_unavailable", field="generate_summary",
                           message="AI summaries are unavailable before the conversation starts."))
    if config.get("generate_summary") and phase == "in_call" and not config.get("summary_provider"):
        issues.append(dict(severity="error", error_code="summary_provider_required", field="summary_provider",
                           message="Select an explicit provider for the in-call summary."))
    references: list[dict[str, Any]] = []
    parameters = config.get("parameters") or []
    model_names: set[str] = set()
    if phase == "in_call":
        for param in parameters:
            name = param.get("name", "")
            if name in reserved:
                issues.append(dict(severity="error", error_code="reserved_template_variable", field="parameters", variable=name,
                                   message=f"{name} is supplied by AVA and cannot be a model parameter."))
            elif name:
                model_names.add(name)
                kind = param.get("type", "string")
                value = param.get("default")
                if value is None:
                    value = {"integer": 1, "number": 1.5, "boolean": True, "array": [], "object": {}}.get(kind, "Example value")
                values[name] = value
                catalog.append(dict(name=name, type=kind, example=value, source="model_parameter", status="conditional",
                                    description="Model parameter; its runtime value must be supplied."))
    environments: dict[str, str] = {}
    for field, template in template_fields(config, phase).items():
        for match in _PLACEHOLDER.finditer(template):
            env, name = match.groups()
            severity, message, code = "available", "Available in this phase.", ""
            if env:
                name = env
                environments[env] = ("https://example.invalid" if field == "url" and match.start() == 0 else
                                     "application/json" if field.lower() == "headers.content-type" else "null")
                severity, message = "warning", "Environment-backed value; preview uses a synthetic replacement."
            elif name not in reserved and name not in model_names:
                if name in ALL_BUILTINS:
                    severity, code = "error", "unavailable_template_variable"
                    message = f"{name} is unavailable for these {phase.replace('_', '-')} settings."
                else:
                    severity = "warning"
                    message = "Custom enrichment/metadata variable; availability and type must be supplied at runtime."
                # A neutral JSON value keeps unknown dynamic slots testable,
                # without claiming they will exist when the call executes.
                if name not in values and name not in ALL_BUILTINS:
                    catalog.append(dict(name=name, type="runtime-defined", example="null", source="enrichment/metadata",
                                        status="conditional", description="Availability and JSON type depend on completed enrichment or call metadata."))
                values.setdefault(name, "null")
            elif name in model_names:
                severity, message = "warning", "Model parameter; runtime value is not guaranteed by this preview."
            if name and name.endswith("_json") and field in {"body_template", "payload_template"}:
                quoted, escaped = False, False
                for character in template[:match.start()]:
                    if escaped:
                        escaped = False
                    elif quoted and character == "\\":
                        escaped = True
                    elif character == '"':
                        quoted = not quoted
                if quoted and severity != "error":
                    severity, message = "warning", "JSON-encoded values inside quotes become strings; insert outside quotes to preserve their JSON type."
            reference = dict(field=field, name=name, start=len(template[:match.start()].encode("utf-16-le")) // 2, end=len(template[:match.end()].encode("utf-16-le")) // 2, status=severity, message=message)
            references.append(reference)
            if severity in {"warning", "error"}:
                issues.append(dict(severity=severity, error_code=code or "runtime_value_required", field=field, variable=name, message=message))
    try:
        rendered = render_request(config, values, phase, environ=environments)
        rendered["headers"] = redact_headers(rendered["headers"])
        if rendered["body"]:
            def mask(value):
                if isinstance(value, dict):
                    return {key: mask(item) for key, item in redact_headers(value).items()}
                if isinstance(value, list):
                    return [mask(item) for item in value]
                return value
            try:
                rendered["body"] = json.dumps(mask(json.loads(rendered["body"])), ensure_ascii=False)
            except ValueError:
                pass  # Explicit text/form templates retain their serializer.
    except TemplateError as exc:
        rendered = None
        issues.append(dict(severity="error", field="request", message=str(exc), **exc.diagnostics()))
    return dict(valid=not any(item["severity"] == "error" for item in issues), issues=issues,
                references=references, variables=catalog, example_body=example_body(phase, bool(config.get("generate_summary"))),
                preview=rendered)


def assert_valid_template(config: Mapping[str, Any], phase: str) -> None:
    """Reject definite errors on activation, while allowing uncertain enrichment."""
    if not config.get("enabled", True):
        return
    result = validate_template(config, phase)
    error = next((item for item in result["issues"] if item["severity"] == "error"), None)
    if error:
        raise TemplateError(error["message"], code=error["error_code"], variable=error.get("variable"))
