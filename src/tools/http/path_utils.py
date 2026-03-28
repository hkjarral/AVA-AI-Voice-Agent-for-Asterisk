"""Shared JSON path extraction utilities for HTTP tools.

Supports dot-notation paths with numeric indices and [*] wildcards:
  - "name"                -> data["name"]
  - "contact.email"       -> data["contact"]["email"]
  - "items[0].name"       -> data["items"][0]["name"]
  - "items[*].name"       -> [item["name"] for each item in data["items"]]
  - "line-items[*].sku"   -> works with non-word chars in field names
  - "[0].name"            -> data[0]["name"] (root list, numeric index)
  - "[*].name"            -> [item["name"] for each item in data] (root list)
"""

from __future__ import annotations

import re
from typing import Any

# Sentinel to distinguish "field missing" from "field present but None/null".
_MISSING = object()

# Field names may contain word chars, hyphens, or spaces (common in third-party
# JSON payloads).  The bracket expression is always at the end: field[*] or
# field[0].
_RE_FIELD_WILDCARD = re.compile(r'^(.+)\[\*\]$')
_RE_FIELD_INDEX = re.compile(r'^(.+)\[(\d+)\]$')
_RE_BARE_INDEX = re.compile(r'^\[(\d+)\]$')


def extract_path(data: Any, path: str) -> Any:
    """Extract a value from nested data using a dot-notation path.

    Wildcard semantics:
      - ``field[*]`` fans out over every element in the array.
      - Each ``[*]`` adds one nesting level (nested wildcards produce nested lists).
      - Missing keys are excluded from wildcard results; JSON ``null`` is preserved.
      - When a nested wildcard field is absent on a parent item, the parent
        contributes an empty list ``[]`` (preserving positional alignment).

    Returns ``None`` when the path cannot be resolved (missing key, wrong type, etc.).
    """
    result = _extract_impl(data, path, missing=None)
    return result


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------

def _extract_impl(data: Any, path: str, *, missing: Any) -> Any:
    """Core extraction logic shared by the public API and recursive fan-out.

    *missing* is the sentinel returned for structurally absent paths:
      - ``None``     when called from ``extract_path`` (public boundary)
      - ``_MISSING`` when called recursively during wildcard fan-out so that
        callers can distinguish "absent field" from "field is JSON null".
    """
    if not path:
        return data

    current = data
    segments = re.split(r'\.(?![^\[]*\])', path)

    for i, segment in enumerate(segments):
        if current is None:
            # Path continues but we hit null — field exists as null.
            # Return None (not *missing*) because null is a present value.
            return None

        # --- bare [*] (current value is already a list) ---
        if segment == '[*]':
            if not isinstance(current, list):
                return missing
            return _fanout(current, segments, i, missing)

        # --- bare [N] (current value is already a list) ---
        m = _RE_BARE_INDEX.match(segment)
        if m:
            index = int(m.group(1))
            if not isinstance(current, list) or index >= len(current):
                return missing
            current = current[index]
            continue

        # --- field[*] wildcard ---
        m = _RE_FIELD_WILDCARD.match(segment)
        if m:
            field_name = m.group(1)
            arr = _resolve_field(current, field_name)
            if arr is _MISSING or not isinstance(arr, list):
                return missing
            return _fanout(arr, segments, i, missing)

        # --- field[N] numeric index ---
        m = _RE_FIELD_INDEX.match(segment)
        if m:
            field_name = m.group(1)
            index = int(m.group(2))
            arr = _resolve_field(current, field_name)
            if arr is _MISSING or not isinstance(arr, list) or index >= len(arr):
                return missing
            current = arr[index]
            continue

        # --- simple field access ---
        val = _resolve_field(current, segment)
        if val is _MISSING:
            return missing
        current = val

    return current


def _fanout(arr: list, segments: list, i: int, missing: Any) -> Any:
    """Fan out a wildcard across *arr*, recursing into remaining segments."""
    remaining = '.'.join(segments[i + 1:])
    if not remaining:
        return arr
    results = []
    for item in arr:
        val = _extract_impl(item, remaining, missing=_MISSING)
        if val is _MISSING:
            # The remaining path starts with another wildcard — preserve the
            # slot as [] so positional alignment is maintained.
            if remaining.startswith('[*]') or _RE_FIELD_WILDCARD.match(remaining.split('.')[0]):
                results.append([])
            # Otherwise the item simply doesn't have the field — skip it.
        else:
            results.append(val)
    return results


def _resolve_field(data: Any, key: str) -> Any:
    """Look up *key* in *data* (must be a dict). Returns ``_MISSING`` if absent."""
    if isinstance(data, dict) and key in data:
        return data[key]
    return _MISSING
