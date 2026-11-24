"""Structural credential locations in templates, command arguments and connection strings."""
import json
import re

from services.secret_keys import connection_password_spans, is_secret_key

_REFERENCE = re.compile(r"(?:Bearer |Basic )?\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*(?::[-=])?\})\Z")
_PREFIXED = re.compile(r"(?<![A-Za-z0-9])(?:sk[-_]|xai-|gsk_|AIza|AKIA|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_.-]{12,}")
_JWT = re.compile(r"(?<![\w-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_AUTH = re.compile(r"""\b(?:Bearer|Token|ApiKey)[ \t]+([^\s"'<>,;}{]{20,})""")
_ASSIGN = re.compile(r"""(?:^|[\s?&;,"\'])(?:--?)?([\w.-]+)=(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)'|([^\s,;&"\']+))""")
_JSON_KEY = re.compile(r'"(?:\\.|[^"\\])*"\s*:\s*')


def is_reference(value):
    return isinstance(value, str) and bool(_REFERENCE.fullmatch(value))


def private_name(name):
    return is_secret_key(name.replace("-", "_")) or name.lower() in {"apikey", "authorization", "x-auth"}


def credential_spans(value, *, template=False, provider_url=False):
    if not isinstance(value, str) or is_reference(value):
        return []
    found = connection_password_spans(value)
    if provider_url:
        query_end = value.find("#")
        query_end = len(value) if query_end < 0 else query_end
        query = value.find("?", 0, query_end)
        if 0 <= query < query_end - 1:
            found.append((query + 1, query_end))
    if template:
        found.extend(m.span() for m in _PREFIXED.finditer(value))
        found.extend(m.span() for m in _JWT.finditer(value))
        found.extend(m.span(1) for m in _AUTH.finditer(value))
        for m in _ASSIGN.finditer(value):
            if private_name(m[1]):
                group = next(n for n in (2, 3, 4) if m[n] is not None)
                if not is_reference(m[group]):
                    found.append(m.span(group))
        for m in _JSON_KEY.finditer(value):
            try:
                key, _ = json.JSONDecoder().raw_decode(m.group())
            except ValueError:
                continue
            if not private_name(key):
                continue
            start = m.end()
            try:
                item, end = json.JSONDecoder().raw_decode(value, start)
            except ValueError:
                # A malformed private value has no reliable end.
                end = len(value)
                item = None
            if is_reference(item):
                continue
            if isinstance(item, str):
                start += 1
                end -= 1
            found.append((start, end))
    merged = []
    for start, end in sorted(found):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
