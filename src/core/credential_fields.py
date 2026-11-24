"""Credential names and URL userinfo shared by the engine and operator interfaces."""

import re

_CONNECTION = re.compile(r'''\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/@?:#"\s]*:([^/@?#"\s]+)@''')

_PATH_SETTINGS = {"GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CALENDAR_CREDENTIALS", "CALENDLY_TOKEN_PATH"}
_SECRET_SEGMENTS = frozenset({"KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "PASS", "PWD", "APIKEY", "CREDENTIAL", "CREDENTIALS"})
EXTRA_SECRET_NAMES = frozenset({"VICIDIAL_API_PASS"})


def is_secret_key(name: str) -> bool:
    name = name.upper()
    if name in _PATH_SETTINGS:
        return False
    return (
        name in EXTRA_SECRET_NAMES
        or name.endswith("_WEBHOOK_URL")
        or not _SECRET_SEGMENTS.isdisjoint(name.split("_"))
    )


def connection_password_spans(value):
    """Find password userinfo without parsing or connecting to the destination."""
    return [match.span(1) for match in _CONNECTION.finditer(value)] if isinstance(value, str) else []


_CREDENTIAL_RE = re.compile(
    r"(?:^|_)(?:api_?key|access_?token|refresh_?token|token|secret|password|"
    r"authorization|auth|credential|private_?key)(?:$|_)",
    re.IGNORECASE,
)

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


def rejected_call_metadata_key(key):
    """Keep the established validation contract for operator-defined business fields."""
    return bool(_CREDENTIAL_RE.search(_tokenize_camel_case(key)))


def is_private_metadata_key(key):
    normalized = _tokenize_camel_case(str(key).replace("-", "_"))
    return is_secret_key(normalized) or bool(_CREDENTIAL_RE.search(normalized))


def mask_business_data(value):
    """Project stored business data for display, preserving ordinary field values."""
    if isinstance(value, dict):
        return {key: "<redacted>" if is_private_metadata_key(key) else mask_business_data(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [mask_business_data(item) for item in value]
    if isinstance(value, str):
        for start, end in reversed(connection_password_spans(value)):
            value = value[:start] + "<redacted>" + value[end:]
    return value
