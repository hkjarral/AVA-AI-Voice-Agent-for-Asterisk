from __future__ import annotations

import json
from typing import Any, Dict, Tuple


TOOL_CHECK_EXTENSION_KEYS: Tuple[str, ...] = (
    "status",
    "message",
    "target",
    "extension",
    "device_state_name",
    "device_state",
    "available",
    "availability_source",
    "endpoint_state",
    "tech",
    "availability_status",
    "availability_reason",
    "device_states",
)

CALENDAR_TOOL_NAMES = frozenset({"google_calendar", "microsoft_calendar"})

# Calendar tools intentionally return structured data that the model needs to
# answer the caller.  Keep this allowlist shared by every provider adapter so
# OpenAI, Google Live, Grok, and Deepgram do not diverge at the final response
# boundary.  Unknown/internal fields remain filtered out.
TOOL_CALENDAR_KEYS: Tuple[str, ...] = (
    "status",
    "message",
    "error",
    "error_code",
    "events",
    "slots",
    "slots_with_end",
    "slot_duration_minutes",
    "calendar_timezone",
    "tz_disagreement",
    "open_windows_found",
    "busy_blocks_found",
    "reason",
    "availability_mode",
    "total_slots_available",
    "slots_truncated",
    "calendars_without_open_windows",
    "id",
    "event_id",
    "summary",
    "description",
    "start",
    "end",
    "calendar",
    "link",
    "agent_hint",
)


def _safe_jsonable(obj: Any, *, depth: int = 0, max_depth: int = 5, max_items: int = 50) -> Any:
    """Convert arbitrary tool output to a bounded JSON-compatible value."""
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
    tool_name: str | None = None,
) -> Dict[str, Any]:
    """Return a JSON-serializable, size-capped tool result dict for providers that require JSON-string payloads."""
    selected_keep_keys = keep_keys
    normalized_tool_name = str(tool_name or "").strip()
    if normalized_tool_name == "check_extension_status":
        selected_keep_keys = TOOL_CHECK_EXTENSION_KEYS
    elif normalized_tool_name in CALENDAR_TOOL_NAMES:
        selected_keep_keys = TOOL_CALENDAR_KEYS

    if not isinstance(result, dict):
        payload: Dict[str, Any] = {"status": "success", "message": str(result)}
    else:
        payload = {}
        for k in selected_keep_keys:
            if k in result:
                payload[k] = _safe_jsonable(result.get(k))
        if "message" not in payload:
            payload["message"] = str(result.get("message") or "")
        # Keep a compact structured payload when available (helps follow-up reasoning).
        if "result" in result and "result" not in payload:
            payload["result"] = _safe_jsonable(result.get("result"), max_depth=3, max_items=20)

        if normalized_tool_name in CALENDAR_TOOL_NAMES and isinstance(result.get("events"), (list, tuple)):
            total_events = len(result["events"])
            returned_events = len(payload.get("events") or [])
            payload["total_events"] = total_events
            payload["events_returned"] = returned_events
            payload["events_truncated"] = returned_events < total_events

    # Cap size; drop structured keys progressively, then truncate message.
    def _fits() -> bool:
        """Return whether the current payload fits the provider byte budget."""
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

    # Calendar result arrays can be large even though each event is compact.
    # Retain the earliest entries and explicit truncation metadata instead of
    # dropping the complete structured result (which made a successful
    # list_events call indistinguishable from an empty calendar to the model).
    if normalized_tool_name in CALENDAR_TOOL_NAMES:
        while not _fits():
            groups = []
            events = payload.get("events")
            if isinstance(events, list) and events:
                groups.append((len(json.dumps(events, ensure_ascii=False).encode("utf-8")), "events"))

            slots = payload.get("slots")
            slots_with_end = payload.get("slots_with_end")
            slot_count = max(
                len(slots) if isinstance(slots, list) else 0,
                len(slots_with_end) if isinstance(slots_with_end, list) else 0,
            )
            if slot_count:
                slot_bytes = len(json.dumps(slots or [], ensure_ascii=False).encode("utf-8"))
                slot_bytes += len(json.dumps(slots_with_end or [], ensure_ascii=False).encode("utf-8"))
                groups.append((slot_bytes, "slots"))

            calendars = payload.get("calendars_without_open_windows")
            if isinstance(calendars, list) and calendars:
                groups.append((len(json.dumps(calendars, ensure_ascii=False).encode("utf-8")), "calendars"))

            if not groups:
                break

            _, largest_group = max(groups)
            if largest_group == "events":
                payload["events"].pop()
                payload["events_returned"] = len(payload["events"])
                payload["events_truncated"] = True
            elif largest_group == "slots":
                if isinstance(slots, list) and slots:
                    slots.pop()
                if isinstance(slots_with_end, list) and slots_with_end:
                    slots_with_end.pop()
                payload["slots_truncated"] = True
            else:
                payload["calendars_without_open_windows"].pop()

        if _fits():
            return payload

        # Long descriptions and model hints are useful but secondary to the
        # event times, status, and primary message.
        for key in ("description", "agent_hint", "link", "summary"):
            if key in payload:
                payload.pop(key, None)
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
    if _fits():
        return payload

    # A non-message scalar may still be unexpectedly large. Preserve the
    # provider contract and hard byte cap with a minimal final payload.
    minimal = {
        "status": _safe_jsonable(payload.get("status")),
        "message": best,
    }
    while len(json.dumps(minimal, ensure_ascii=False).encode("utf-8")) > max_bytes and minimal["message"]:
        minimal["message"] = minimal["message"][:-1]
    if len(json.dumps(minimal, ensure_ascii=False).encode("utf-8")) <= max_bytes:
        return minimal
    return {} if max_bytes >= 2 else payload
