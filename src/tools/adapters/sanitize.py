from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Tuple


def _safe_jsonable(obj: Any, *, depth: int = 0, max_depth: int = 5, max_items: int = 50) -> Any:
    if depth >= max_depth:
        return str(obj)
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for idx, (k, v) in enumerate(obj.items()):
            if idx >= max_items:
                break
            out[str(k)] = _safe_jsonable(v, depth=depth + 1, max_depth=max_depth, max_items=max_items)
        return out
    if isinstance(obj, (list, tuple)):
        return [_safe_jsonable(v, depth=depth + 1, max_depth=max_depth, max_items=max_items) for v in list(obj)[:max_items]]
    return str(obj)


def sanitize_tool_result_for_json_string(
    result: Any,
    *,
    max_bytes: int = 12000,
    keep_keys: Tuple[str, ...] = ("status", "message", "data", "will_hangup", "transferred", "transfer_mode", "extension", "destination", "error"),
) -> Dict[str, Any]:
    """Return a JSON-serializable, size-capped tool result dict for providers that require JSON-string payloads."""
    if not isinstance(result, dict):
        payload: Dict[str, Any] = {"status": "success", "message": str(result)}
    else:
        payload = {}
        for k in keep_keys:
            if k in result:
                payload[k] = _safe_jsonable(result.get(k))
        if "message" not in payload:
            payload["message"] = str(result.get("message") or "")
        # Keep a compact structured payload when available (helps follow-up reasoning).
        if "result" in result and "result" not in payload:
            payload["result"] = _safe_jsonable(result.get("result"), max_depth=3, max_items=20)

    # Cap size; drop structured keys progressively, then truncate message.
    def _fits() -> bool:
        try:
            return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= max_bytes
        except Exception:
            return False

    if _fits():
        return payload

    # Drop "result" first (secondary structured payload).
    if "result" in payload:
        payload.pop("result", None)
        if _fits():
            return payload

    # Drop "data" next (extracted output variables — message still carries a summary).
    if "data" in payload:
        payload.pop("data", None)
        if _fits():
            return payload

    # Last resort: binary-search trim message to fit within the byte budget.
    msg = str(payload.get("message") or "")
    low, high, best = 0, min(len(msg), 800), ""
    while low <= high:
        mid = (low + high) // 2
        payload["message"] = msg[:mid]
        if _fits():
            best = payload["message"]
            low = mid + 1
        else:
            high = mid - 1
    payload["message"] = best
    return payload

