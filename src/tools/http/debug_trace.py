"""
Debug trace helpers for HTTP tools.

These utilities are intentionally lightweight and provider-agnostic: they only
help HTTP tools emit useful request/response context when LOG_LEVEL=debug.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, Mapping, Optional


_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")
_BRACE_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_SENSITIVE_HEADER_PATTERN = re.compile(
    r"(?:authorization|proxy-authorization|api[-_]?key|token|secret|cookie|password|credential)",
    re.IGNORECASE,
)


def debug_enabled(logger: logging.Logger) -> bool:
    """Return True when DEBUG logging is enabled for this logger."""
    return bool(logger and logger.isEnabledFor(logging.DEBUG))


def preview(value: Any, *, limit: int = 4096) -> str:
    """Render a bounded string preview of `value` for logs."""
    if value is None:
        return ""
    try:
        if isinstance(value, bytes):
            s = value.decode("utf-8", errors="replace")
        else:
            s = str(value)
    except Exception:
        s = "<unprintable>"
    if limit and len(s) > limit:
        return s[:limit] + "…"
    return s


def redact_headers(headers: Mapping[str, Any]) -> Dict[str, Any]:
    """Return log-safe request headers without changing the real request."""
    return {
        str(name): "<redacted>" if _SENSITIVE_HEADER_PATTERN.search(str(name)) else value
        for name, value in (headers or {}).items()
    }


def extract_used_env_vars(*templates: Optional[str]) -> list[str]:
    """Return sorted `${ENV_VAR}` names referenced across templates."""
    names: set[str] = set()
    for t in templates:
        if not t:
            continue
        for m in _ENV_PATTERN.finditer(t):
            names.add(m.group(1))
    return sorted(names)


def extract_used_brace_vars(*templates: Optional[str]) -> list[str]:
    """Return sorted `{var}` names referenced across templates."""
    names: set[str] = set()
    for t in templates:
        if not t:
            continue
        for m in _BRACE_PATTERN.finditer(t):
            names.add(m.group(1))
    return sorted(names)


def build_var_snapshot(
    *,
    used_brace_vars: Iterable[str],
    used_env_vars: Iterable[str],
    values: Mapping[str, Any],
    env: Mapping[str, str],
) -> Dict[str, Any]:
    """Build a snapshot of referenced variables and env vars for debug logs."""
    brace: Dict[str, Any] = {}
    for name in used_brace_vars:
        brace[name] = values.get(name)

    envs: Dict[str, Any] = {}
    for name in used_env_vars:
        # Environment substitutions overwhelmingly represent credentials.  A
        # trace only needs to show whether the variable resolved, never its
        # plaintext value.
        value = env.get(name)
        envs[name] = "<set>" if value else None

    return {"vars": brace, "env": envs}
