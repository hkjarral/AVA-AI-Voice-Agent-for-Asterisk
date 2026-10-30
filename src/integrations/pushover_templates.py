"""Pure notification-template review; no secrets, recipients, LLM or network access.

Pushover's plain-text contract and 250/1024 character limits were checked against
https://pushover.net/api on 2026-10-30. Render previews with the runtime renderer.
"""
from __future__ import annotations

from string import Formatter
from types import SimpleNamespace
import re

from .pushover import VARIABLES, PHASES, DEFAULT_TITLES, default_body, render_message
from src.tools.summary import summary_prompt


def preview_values(phase, generate_summary=True):
    return dict(agent="Example Agent", context_name="Example Agent", caller_name="Example Caller",
                called_number="+18005550100", caller_number="+15550102030", name="Example Caller",
                call_id="synthetic-preview", company="Example Company", callback_number="unknown",
                crm_status="unknown", identity_source="caller-confirmed", destination="support",
                outcome="caller_hangup" if phase == "post_call" else "pending",
                call_outcome="caller_hangup" if phase == "post_call" else "pending",
                call_duration="120" if phase == "post_call" else "0", language="unknown", meeting="unknown",
                stage={"pre_call": "arrival", "in_call": "in-call update", "post_call": "completion"}[phase],
                summary="The caller requested support." if generate_summary and phase != "pre_call" else "",
                summary_status="preview" if generate_summary and phase != "pre_call" else "disabled")


def review_templates(config, phase):
    if phase not in PHASES:
        raise ValueError("Invalid notification phase")
    enabled_summary = bool(config.get("generate_summary"))
    values = preview_values(phase, enabled_summary)
    issues, references = [], []
    def issue(field, code, message, severity="error", variable=None):
        row = dict(field=field, error_code=code, message=message, severity=severity)
        if variable:
            row["variable"] = variable
        issues.append(row)
    if enabled_summary and phase == "pre_call":
        issue("generate_summary", "summary_unavailable", "Pre-call notifications cannot generate a conversation summary.")
    if enabled_summary and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}_llm", str(config.get("summary_provider") or "")):
        issue("summary_provider", "summary_provider_required", "Select an explicit configured summary provider.")
    try:
        summary_prompt(config.get("summary_prompt"), config.get("summary_max_words") or 100)
    except (ValueError, TypeError):
        issue("summary_prompt", "invalid_summary_prompt", "Summary prompts support only the plain {max_words} placeholder.")
    conditional = {"company", "crm_status", "callback_number", "destination", "language", "meeting"}
    descriptions = {
        "agent": "Selected Agent", "context_name": "Selected Agent name", "name": "Resolved caller name",
        "caller_name": "Resolved caller name", "identity_source": "Source of the resolved caller name",
        "call_id": "AVA call identifier", "caller_number": "Caller number", "called_number": "Called number",
        "stage": "Notification phase", "summary": "Generated plain-text summary", "summary_status": "Summary generation status",
        "outcome": "Observed outcome after the call; pending before completion",
        "call_outcome": "Observed outcome after the call; pending before completion",
        "call_duration": "Final duration in seconds after the call; 0 before completion",
    }
    catalog = []
    for name in sorted(VARIABLES):
        source = "enrichment/metadata" if name in conditional else "call"
        description = descriptions.get(name, "Optional trusted call value; unknown when unavailable")
        if name == "summary" and not enabled_summary:
            description += "; generation is disabled, so the renderer uses unavailable"
        catalog.append(dict(name=name, type="string", source=source, description=description, example=values[name]))
    templates = {}
    for field, fallback, limit in [("title", DEFAULT_TITLES[phase], 2000), ("body", default_body(phase, generate_summary=enabled_summary), 8000)]:
        template = config.get(field) or ""
        if not isinstance(template, str):
            issue(field, "invalid_template_type", "Enter a plain-text template.")
            continue
        if len(template) > limit:
            issue(field, "template_too_long", f"Template source must be at most {limit} characters.")
        templates[field] = template or fallback
        try:
            list(Formatter().parse(template))
        except ValueError:
            issue(field, "malformed_template", "Unmatched or malformed braces. Use {{ and }} for literal braces.")
            continue
        index = 0
        while index < len(template):
            if template[index:index+2] in {"{{", "}}"}:
                index += 2
                continue
            if template[index] != "{":
                index += 1
                continue
            end = template.find("}", index + 1) + 1
            if not end:
                break  # Formatter already diagnosed malformed templates.
            name = template[index+1:end-1]
            status, message = "available", "Available trusted notification value."
            if name not in VARIABLES:
                status, message = "error", "Unsupported notification variable. Choose a plain-text variable from the catalog; format operations are not supported."
                safe_name = name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) else None
                issue(field, "unsupported_template_variable", message, variable=safe_name)
            elif name in conditional:
                status, message = "warning", "Depends on trusted call enrichment or metadata; unknown when unavailable."
            elif name == "summary" and not enabled_summary:
                status, message = "warning", "Summary generation is disabled; this placeholder renders as unavailable."
            elif name in {"outcome", "call_outcome", "call_duration"} and phase != "post_call":
                status, message = "warning", "The call has not completed: outcome is pending and duration is 0."
            if status == "warning":
                issue(field, "conditional_template_value", message, severity="warning", variable=name)
            references.append(dict(field=field, name=name if name in VARIABLES else "unsupported variable", start=len(template[:index].encode("utf-16-le")) // 2, end=len(template[:end].encode("utf-16-le")) // 2, status=status, message=message))
            index = end
        if len(template) > (250 if field == "title" else 1024):
            issue(field, "notification_length_limit", "Rendered notifications are limited to 250 title and 1024 message characters; long values are truncated by the runtime renderer.", severity="warning")
    valid = not any(row["severity"] == "error" for row in issues)
    preview = None
    if valid:
        preview = render_message(SimpleNamespace(**templates, generate_summary=enabled_summary,
                                 priority=config.get("priority", 0), sound=config.get("sound", "")), phase, values)
    return dict(valid=valid, issues=issues, references=references, variables=catalog,
                example_title=DEFAULT_TITLES[phase], example_body=default_body(phase, generate_summary=enabled_summary), preview=preview)
