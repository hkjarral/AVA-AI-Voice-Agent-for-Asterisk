"""Structural credential locations in templates, command arguments and connection strings."""
import json
import re

from services.secret_keys import connection_password_spans, is_secret_key

_REFERENCE = re.compile(r"(?:Bearer |Basic )?\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*(?::[-=])?\})\Z")
_PREFIXED = re.compile(r"(?<![A-Za-z0-9])(?:sk[-_]|xai-|gsk_|AIza|AKIA|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_.-]{12,}")
_TOKEN_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")
_JWT_TAIL = re.compile(r"\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_AUTH = re.compile(r"""\b(?:Token|ApiKey)[ \t]+([^\s"'<>,;}{]{20,})""")
_BEARER = re.compile(r"""\bBearer[ \t]+(\$\{[^}\s]*\}|[^\s"'<>,;}{]+)""", re.I)
_BASIC_HEADER = re.compile(r"\b(?:Proxy-)?Authorization:[ \t]*Basic[ \t]+([A-Za-z0-9+/]+=*)(?=[\s\"\'<>;,]|$)", re.I)
_ASSIGN = re.compile(r"""(?:^|[\s?&;,"\'])(?:--?)?([\w.-]+)=(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)'|([^\s,;&"\']+))""")
_JSON_QUOTES = re.compile(r'\\.|"')


def is_reference(value):
    return isinstance(value, str) and bool(_REFERENCE.fullmatch(value))


def private_name(name):
    return is_secret_key(name.replace("-", "_")) or name.lower() in {"apikey", "authorization", "x-auth"}


def _jwt_spans(text):
    last_end = 0
    for segment in _TOKEN_SEGMENT.finditer(text):
        start, end = segment.span()
        if start < last_end or end - start <= 3 or not text.startswith("eyJ", start):
            continue
        if start and text[start - 1].isalnum():
            continue
        tail = _JWT_TAIL.match(text, end)
        if tail:
            last_end = tail.end()
            yield start, last_end


def _json_private_spans(text):
    # Consider each pair of consecutive unescaped quotes, including in a JSON
    # fragment surrounded by free text. Never rescan a private compound value.
    decoder = json.JSONDecoder()
    previous_quote = None
    protected_until = 0
    for quote in _JSON_QUOTES.finditer(text):
        if quote.group() != '"':
            continue
        opening, previous_quote = previous_quote, quote.start()
        if opening is None or opening < protected_until:
            continue
        start = quote.end()
        while start < len(text) and text[start].isspace():
            start += 1
        if start == len(text) or text[start] != ":":
            continue
        try:
            key = json.loads(text[opening:quote.end()])
        except ValueError:
            continue
        if not private_name(key):
            continue
        start += 1
        while start < len(text) and text[start].isspace():
            start += 1
        try:
            item, end = decoder.raw_decode(text, start)
        except (ValueError, RecursionError):
            yield start, len(text)
            return
        protected_until = end
        if not is_reference(item):
            yield (start + 1, end - 1) if isinstance(item, str) else (start, end)


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
        found.extend(_jwt_spans(value))
        for pattern in (_BEARER, _AUTH):
            found.extend(m.span(1) for m in pattern.finditer(value) if not is_reference(m[1]))
        found.extend(m.span(1) for m in _BASIC_HEADER.finditer(value))
        for m in _ASSIGN.finditer(value):
            if private_name(m[1]):
                group = next(n for n in (2, 3, 4) if m[n] is not None)
                if not is_reference(m[group]):
                    found.append(m.span(group))
        found.extend(_json_private_spans(value))
    merged = []
    for start, end in sorted(found):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
