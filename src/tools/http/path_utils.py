"""Shared JSON path extraction utilities for HTTP tools.

Supports dot-notation paths with numeric indices and [*] wildcards:
  - "name"                -> data["name"]
  - "contact.email"       -> data["contact"]["email"]
  - "items[0].name"       -> data["items"][0]["name"]
  - "items[*].name"       -> [item["name"] for each item in data["items"]]
  - "[0].name"            -> data[0]["name"] (root list, numeric index)
  - "[*].name"            -> [item["name"] for each item in data] (root list)
"""

from __future__ import annotations

import re
from typing import Any

# Sentinel to distinguish "field missing" from "field present but None/null".
_MISSING = object()


def extract_path(data: Any, path: str) -> Any:
    """Extract a value from nested data using a dot-notation path.

    Wildcard semantics:
      - ``field[*]`` fans out over every element in the array.
      - Each ``[*]`` adds one nesting level (nested wildcards produce nested lists).
      - Missing keys are excluded from wildcard results; JSON ``null`` is preserved.

    Returns ``None`` when the path cannot be resolved (missing key, wrong type, etc.).
    """
    if not path:
        return data

    current = data
    segments = re.split(r'\.(?![^\[]*\])', path)

    for i, segment in enumerate(segments):
        if current is None:
            return None

        # --- bare [*] (current value is already a list) ---
        if segment == '[*]':
            if not isinstance(current, list):
                return None
            remaining = '.'.join(segments[i + 1:])
            if remaining:
                results = []
                for item in current:
                    val = _extract_single(item, remaining)
                    if val is not _MISSING:
                        results.append(val)
                return results
            return current

        # --- bare [N] (current value is already a list) ---
        bare_index = re.match(r'^\[(\d+)\]$', segment)
        if bare_index:
            index = int(bare_index.group(1))
            if not isinstance(current, list) or index >= len(current):
                return None
            current = current[index]
            continue

        # --- field[*] wildcard ---
        wildcard_match = re.match(r'^(\w+)\[\*\]$', segment)
        if wildcard_match:
            field_name = wildcard_match.group(1)
            arr = _resolve_field(current, field_name)
            if arr is _MISSING or not isinstance(arr, list):
                return None
            remaining = '.'.join(segments[i + 1:])
            if remaining:
                results = []
                for item in arr:
                    val = _extract_single(item, remaining)
                    if val is not _MISSING:
                        results.append(val)
                return results
            return arr

        # --- field[N] numeric index ---
        index_match = re.match(r'^(\w+)\[(\d+)\]$', segment)
        if index_match:
            field_name = index_match.group(1)
            index = int(index_match.group(2))
            arr = _resolve_field(current, field_name)
            if arr is _MISSING or not isinstance(arr, list) or index >= len(arr):
                return None
            current = arr[index]
            continue

        # --- simple field access ---
        val = _resolve_field(current, segment)
        if val is _MISSING:
            return None
        current = val

    return current


# ---- internal helpers -------------------------------------------------------

def _resolve_field(data: Any, key: str) -> Any:
    """Look up *key* in *data* (must be a dict). Returns ``_MISSING`` if absent."""
    if isinstance(data, dict) and key in data:
        return data[key]
    return _MISSING


def _extract_single(data: Any, path: str) -> Any:
    """Like ``extract_path`` but returns ``_MISSING`` instead of ``None`` for
    truly absent paths so callers can distinguish missing from JSON null."""
    if not path:
        return data

    segments = re.split(r'\.(?![^\[]*\])', path)
    current = data

    for i, segment in enumerate(segments):
        if current is None:
            # path continues but we hit null — field exists as null
            return None

        # bare [*]
        if segment == '[*]':
            if not isinstance(current, list):
                return _MISSING
            remaining = '.'.join(segments[i + 1:])
            if remaining:
                results = []
                for item in current:
                    val = _extract_single(item, remaining)
                    if val is not _MISSING:
                        results.append(val)
                return results
            return current

        # bare [N]
        bare_index = re.match(r'^\[(\d+)\]$', segment)
        if bare_index:
            index = int(bare_index.group(1))
            if not isinstance(current, list) or index >= len(current):
                return _MISSING
            current = current[index]
            continue

        # field[*]
        wildcard_match = re.match(r'^(\w+)\[\*\]$', segment)
        if wildcard_match:
            field_name = wildcard_match.group(1)
            arr = _resolve_field(current, field_name)
            if arr is _MISSING or not isinstance(arr, list):
                return _MISSING
            remaining = '.'.join(segments[i + 1:])
            if remaining:
                results = []
                for item in arr:
                    val = _extract_single(item, remaining)
                    if val is not _MISSING:
                        results.append(val)
                return results
            return arr

        # field[N]
        index_match = re.match(r'^(\w+)\[(\d+)\]$', segment)
        if index_match:
            field_name = index_match.group(1)
            index = int(index_match.group(2))
            arr = _resolve_field(current, field_name)
            if arr is _MISSING or not isinstance(arr, list) or index >= len(arr):
                return _MISSING
            current = arr[index]
            continue

        # simple key
        val = _resolve_field(current, segment)
        if val is _MISSING:
            return _MISSING
        current = val

    return current
