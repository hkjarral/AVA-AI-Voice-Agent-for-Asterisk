"""Single-pass, context-aware rendering of operator-authored HTTP templates.

Only placeholders in the original template are interpreted. Replacement text,
including environment values, is always data and is never scanned again.
"""

import json
import os
import re
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit


_PLACEHOLDER = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}|(?<!\$)\{([A-Za-z0-9_.-]+)\}")


class TemplateError(ValueError):
    """A safe, value-free template error suitable for existing diagnostics."""


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def json_escape(value: str) -> str:
    return json.dumps(value)[1:-1]


def render_template(
    template: str,
    values: Mapping[str, Any],
    *,
    escape: Optional[Callable[[str, str], str]] = None,
    env_escape: Optional[Callable[[str], str]] = None,
) -> str:
    """Resolve original tokens; leave unknown data tokens, empty missing envs."""
    def replace(match: re.Match[str]) -> str:
        env, key = match.groups()
        if env is not None:
            value = os.environ.get(env, "")
            return env_escape(value) if env_escape else value
        if key not in values:
            return match.group(0)
        value = _text(values[key])
        return escape(key, value) if escape else value

    return _PLACEHOLDER.sub(replace, template or "")


def render_url(template: str, values: Mapping[str, Any]) -> str:
    """Keep the authority operator-owned and encode data as URL components."""
    # A noncharacter cannot be a legitimate configured DNS name.
    marker = "\uffff"
    shape = render_template(template, values, escape=lambda _key, _value: marker)
    try:
        parsed = urlsplit(shape)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise TemplateError("HTTP URL requires an operator-configured origin")
        if marker in parsed.netloc or marker in parsed.scheme:
            raise TemplateError("Call data is not allowed in the HTTP URL authority")
        result = render_template(template, values, escape=lambda _key, value: quote(value, safe=""))
        # quote() leaves dots intact; HTTP clients can normalize dot segments.
        if any(unquote(part) in {".", ".."} for part in urlsplit(result).path.split("/")):
            raise TemplateError("HTTP URL dot segments are not supported")
        return result
    except ValueError:
        raise TemplateError("Invalid HTTP URL template") from None


def render_headers(templates: Mapping[str, str], values: Mapping[str, Any]) -> dict[str, str]:
    def clean(value: str) -> str:
        return value.replace("\r", " ").replace("\n", " ")

    headers = {}
    for key, value in templates.items():
        # Call data must not select a less restrictive body serializer.
        if key.lower() == "content-type" and any(
            match.group(2) is not None for match in _PLACEHOLDER.finditer(value)
        ):
            raise TemplateError("HTTP Content-Type must be operator-configured")
        headers[key] = clean(render_template(value, values))
    return headers


def _loads(value: str) -> Any:
    def invalid_constant(_value: str) -> None:
        raise ValueError("Non-finite JSON number")

    return json.loads(value, parse_constant=invalid_constant)


def render_json(template: str, values: Mapping[str, Any]) -> str:
    """Escape string slots and serialize each unquoted slot as one JSON value.

    Native JSON types retain their types. Strings in unquoted slots must contain
    exactly one valid JSON value (for legacy serialized enrichment and *_json).
    No suffix grants raw insertion privileges. Malformed JSON never falls back
    to a raw request. The scanner only inspects original template characters.
    """
    parts: list[str] = []
    quoted = False
    escaped = False
    cursor = 0
    try:
        for match in _PLACEHOLDER.finditer(template):
            literal = template[cursor:match.start()]
            parts.append(literal)
            for character in literal:
                if escaped:
                    escaped = False
                elif quoted and character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = not quoted
            if escaped:
                raise TemplateError("Placeholder cannot follow a JSON escape")
            env, key = match.groups()
            if env is not None:
                value = os.environ.get(env, "")
            elif key in values:
                value = values[key]
            else:
                raise TemplateError("Missing JSON template variable")
            if quoted:
                parts.append(json_escape(_text(value)))
            else:
                if isinstance(value, str):
                    value = _loads(value)
                parts.append(json.dumps(value, allow_nan=False))
            cursor = match.end()
        parts.append(template[cursor:])
        rendered = "".join(parts)
        _loads(rendered)
        return rendered
    except (ValueError, TypeError):
        # Parser errors can include substituted secrets. Never propagate them.
        raise TemplateError("Invalid JSON HTTP template or value") from None


def body_media_type(headers: Mapping[str, str]) -> str:
    """Use the configured Content-Type; absent headers mean JSON."""
    content_type = next(
        (value for key, value in headers.items() if key.lower() == "content-type"),
        "application/json",
    )
    return content_type.split(";", 1)[0].strip().lower()


def render_body(template: str, values: Mapping[str, Any], headers: Mapping[str, str]) -> str:
    """JSON by default; explicit plain text and form bodies remain supported."""
    media_type = body_media_type(headers)
    if media_type == "application/json" or media_type.endswith("+json"):
        return render_json(template, values)
    if media_type == "text/plain":
        return render_template(template, values)
    if media_type == "application/x-www-form-urlencoded":
        return urlencode([
            (render_template(key, values), render_template(value, values))
            for key, value in parse_qsl(template, keep_blank_values=True)
        ])
    raise TemplateError("Unsupported HTTP template Content-Type")
