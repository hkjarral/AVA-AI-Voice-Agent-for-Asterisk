"""
Google Calendar tool for Asterisk AI Voice Agent.

Supports listing events, getting a single event, creating events, deleting events, and finding
free appointment slots (with configurable duration and duration-aligned slot starts).

Datetime handling is DST-aware: when a datetime string has a TZ tail (e.g. Z or +00:00),
the tail is removed and the date/time is interpreted as local time in the calendar timezone
(GOOGLE_CALENDAR_TZ, or TZ env, or UTC)—same as when there is no tail. List/time-range APIs
receive RFC3339 with the correct offset for that zone.

Environment: GOOGLE_CALENDAR_CREDENTIALS (path to service account JSON);
GOOGLE_CALENDAR_TZ for timezone (fallback: TZ).
"""

import asyncio
import structlog
from datetime import datetime, timedelta
from typing import Dict, Any
from zoneinfo import ZoneInfo

from src.tools.base import Tool, ToolDefinition, ToolCategory
from src.tools.context import ToolExecutionContext

from src.tools.business.gcalendar import GCalendar, _get_timezone

logger = structlog.get_logger(__name__)

# Schema for Google Live / Vertex and OpenAI (input_schema is provider-agnostic)
_GOOGLE_CALENDAR_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list_events", "get_event", "create_event", "delete_event", "get_free_slots"],
            "description": "The calendar operation to perform."
        },
        "calendar_key": {
            "type": "string",
            "description": "Optional. Named calendar key (from tools.google_calendar.calendars) to target a single calendar."
        },
        "aggregate_mode": {
            "type": "string",
            "enum": ["all", "any"],
            "description": "For multi-calendar get_free_slots/list_events: 'all' = intersection (default), 'any' = union. Ignored when calendar_key is set."
        },
        "time_min": {
            "type": "string",
            "description": "ISO 8601 start time. Required for list_events and get_free_slots."
        },
        "time_max": {
            "type": "string",
            "description": "ISO 8601 end time. Required for list_events and get_free_slots."
        },
        "free_prefix": {
            "type": "string",
            "description": "The prefix of events that define working hours (e.g., 'Open'). Required for get_free_slots."
        },
        "busy_prefix": {
            "type": "string",
            "description": "The prefix of events that define booked appointments (e.g., 'FOG'). Required for get_free_slots."
        },
        "duration": {
            "type": "integer",
            "description": "Appointment duration in minutes. Used by get_free_slots to return only start times where this many minutes fit. Slot start times are aligned to multiples of this duration (e.g. 15 min -> :00, :15, :30, :45; 30 min -> :00, :30)."
        },
        "event_id": {
            "type": "string",
            "description": "The exact ID of the event. Required for get_event and delete_event."
        },
        "summary": {
            "type": "string",
            "description": "Title of the event. Required for create_event."
        },
        "description": {
            "type": "string",
            "description": "Detailed description of the event. Optional for create_event."
        },
        "start_datetime": {
            "type": "string",
            "description": "ISO 8601 start time for the new event. Required for create_event."
        },
        "end_datetime": {
            "type": "string",
            "description": "ISO 8601 end time for the new event. Required for create_event."
        }
    },
    "required": ["action"]
}


class GCalendarTool(Tool):
    """
    Generic tool for interacting with Google Calendar, extended with
    a custom slot availability calculator.
    Compatible with Google Live/Vertex and OpenAI via Asterisk-AI-Voice-Agent.
    """

    def __init__(self):
        super().__init__()
        logger.debug("Initializing GCalendarTool instance")
        self._cal = None
        self._cal_config_key = None
        self._cals: dict[tuple[str, str, str], GCalendar] = {}

    def _get_cal(self, config: Dict[str, Any]) -> GCalendar:
        """Return a GCalendar instance, (re)creating if config changed or service is None."""
        creds_path = config.get("credentials_path", "")
        cal_id = config.get("calendar_id", "")
        tz = config.get("timezone", "")
        config_key = (creds_path, cal_id, tz)
        if self._cal is None or self._cal.service is None or self._cal_config_key != config_key:
            self._cal = GCalendar(credentials_path=creds_path, calendar_id=cal_id, timezone=tz)
            self._cal_config_key = config_key
        return self._cal

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="google_calendar",
            description=(
                "A general tool to interact with Google Calendar. Use this to list events, "
                "get a specific event, create a new event, delete an event, or find free slots."
            ),
            category=ToolCategory.BUSINESS,
            requires_channel=False,
            max_execution_time=30,
            input_schema=_GOOGLE_CALENDAR_INPUT_SCHEMA,
        )

    def _parse_iso(self, iso_str: str) -> datetime:
        """Helper to parse ISO strings, handling the 'Z' suffix if present."""
        if iso_str.endswith('Z'):
            iso_str = iso_str[:-1] + '+00:00'
        return datetime.fromisoformat(iso_str)

    def _get_calendar_tz_name(self, config: Dict[str, Any]) -> str:
        """Resolve calendar timezone: config timezone, then GOOGLE_CALENDAR_TZ, TZ, UTC."""
        return _get_timezone(config.get("timezone", ""))

    def _get_or_create_cal(self, creds_path: str, cal_id: str, tz: str) -> GCalendar:
        key = (creds_path or "", cal_id or "", tz or "")
        inst = self._cals.get(key)
        if inst is None or inst.service is None:
            inst = GCalendar(credentials_path=creds_path, calendar_id=cal_id, timezone=tz)
            self._cals[key] = inst
        return inst

    def _resolve_calendars(self, config: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
        """
        Return dict of calendar_key -> {credentials_path, calendar_id, timezone}.
        Falls back to single-calendar legacy keys if calendars{} missing.
        """
        cals = {}
        cal_map = config.get("calendars") or {}
        if isinstance(cal_map, dict) and cal_map:
            for k, v in cal_map.items():
                if not isinstance(v, dict):
                    continue
                cals[str(k)] = {
                    "credentials_path": v.get("credentials_path", "") or config.get("credentials_path", ""),
                    "calendar_id": v.get("calendar_id", "") or config.get("calendar_id", ""),
                    "timezone": v.get("timezone", "") or config.get("timezone", ""),
                }
        else:
            # Legacy: single calendar from tool config/env
            cals["default"] = {
                "credentials_path": config.get("credentials_path", ""),
                "calendar_id": config.get("calendar_id", ""),
                "timezone": config.get("timezone", ""),
            }
        return cals

    def _selected_calendar_keys(self, config: Dict[str, Any]) -> list[str]:
        sel = []
        # Per-context nested override can set tools.google_calendar.selected_calendars
        # When missing, default to all configured calendars
        try:
            sel = list(config.get("selected_calendars", []) or [])
        except Exception:
            sel = []
        if sel:
            return [str(s) for s in sel if str(s) in self._resolve_calendars(config).keys()]
        return list(self._resolve_calendars(config).keys())

    def _normalize_datetime_to_calendar_tz(
        self, dt_str: str, calendar_tz_name: str
    ) -> datetime:
        """
        Parse datetime string as local time in the calendar timezone (DST-aware).

        If dt_str has a TZ tail (Z or ±HH:MM): the tail is removed and the date/time
        is interpreted as local time in the calendar zone (same as when there is no tail).
        So "2025-03-15T19:00:00Z" is treated as 19:00 in the calendar zone, not as 19:00 UTC.

        Uses GOOGLE_CALENDAR_TZ / TZ for the calendar zone; falls back to UTC if invalid.
        """
        dt_str = (dt_str or "").strip()
        if not dt_str:
            raise ValueError("Empty datetime string")
        # Normalize Z for parsing, then parse
        if dt_str.upper().endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(dt_str)
        except ValueError as e:
            raise ValueError(f"Invalid datetime string: {dt_str}") from e

        try:
            cal_tz = ZoneInfo(calendar_tz_name)
        except Exception:
            cal_tz = ZoneInfo("UTC")

        # If there was a TZ tail, remove it: use only the wall-clock time (naive)
        # and interpret that as local time in the calendar zone (same as no tail).
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return parsed.replace(tzinfo=cal_tz)

    def _get_config(self, context: ToolExecutionContext) -> Dict[str, Any]:
        """
        Get google_calendar config: from context when available, else from ai-agent.yaml.
        """
        if context and getattr(context, "get_config_value", None):
            return context.get_config_value("tools.google_calendar", {}) or {}
        return self._load_config()

    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext,
    ) -> Dict[str, Any]:
        """
        Routes the request to the underlying GCalendar module or executes custom logic based on the action.

        Args:
            parameters: Tool parameters from the AI; must include "action" and action-specific fields
                (e.g. event_id for get_event/delete_event, time_min/time_max for list_events).
            context: Tool execution context with call_id and config access.

        Returns:
            Dict with "status" ("success" | "error") and "message"; may include "events", "id",
            "link", or other action-specific keys. On error, message describes the failure.
        """
        call_id = getattr(context, "call_id", None) or ""
        logger.info("GCalendarTool execution triggered by LLM", call_id=call_id)
        safe_parameters = {
            "action": parameters.get("action"),
            "event_id": parameters.get("event_id"),
            "has_summary": bool(parameters.get("summary")),
            "has_description": bool(parameters.get("description")),
            "time_min": parameters.get("time_min"),
            "time_max": parameters.get("time_max"),
        }
        logger.debug("Raw arguments received from LLM", call_id=call_id, parameters=safe_parameters)

        config = self._get_config(context)
        if config.get("enabled") is False:
            logger.info("Google Calendar tool disabled by config", call_id=call_id)
            out = {"status": "error", "message": "Google Calendar is disabled."}
            return out

        action = parameters.get("action")
        if not action:
            error_msg = "Error: 'action' parameter is missing."
            logger.warning("Missing action parameter", call_id=call_id)
            out = {"status": "error", "message": error_msg}
            logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
            return out

        # Resolve configured calendars (multi or legacy single)
        calendars = self._resolve_calendars(config)
        selected_keys = self._selected_calendar_keys(config)

        # If legacy single-calendar, keep existing behavior for backward-compat
        legacy_single = len(calendars) == 1 and "default" in calendars

        # Helper: get timezone name for a specific calendar
        def _tz_for_key(k: str) -> str:
            tz = calendars[k].get("timezone", "")
            return _get_timezone(tz)

        # Helper: build or reuse GCalendar for a key
        def _cal_for_key(k: str) -> GCalendar:
            cfg = calendars[k]
            return self._get_or_create_cal(cfg.get("credentials_path", ""), cfg.get("calendar_id", ""), cfg.get("timezone", ""))

        # Backward-compat single-calendar guard
        if legacy_single:
            cal = self._get_cal(config)
            calendar_tz_name = self._get_calendar_tz_name(config)
            if not getattr(cal, "service", None):
                logger.error("Google Calendar service unavailable", call_id=call_id)
                return {"status": "error", "message": "Google Calendar is not configured or unavailable."}
        else:
            # For multi-cal, ensure at least one selected calendar resolves
            if not selected_keys:
                logger.error("No calendars selected or configured", call_id=call_id)
                return {"status": "error", "message": "No Google Calendars are selected or configured for this context."}
            # Validate services exist (best-effort; skip broken ones at runtime)
            at_least_one_ready = False
            for k in selected_keys:
                c = _cal_for_key(k)
                if getattr(c, "service", None):
                    at_least_one_ready = True
                    break
            if not at_least_one_ready:
                logger.error("No Google Calendar services available (multi-cal)", call_id=call_id)
                return {"status": "error", "message": "Google Calendar is not configured or unavailable (multi-account)."}

        try:
            if action == "get_free_slots":
                # Prefixes: config (YAML/UI) takes precedence as defaults; LLM can override via parameters
                free_prefix = parameters.get("free_prefix") or config.get("free_prefix")
                busy_prefix = parameters.get("busy_prefix") or config.get("busy_prefix")
                time_min = parameters.get("time_min")
                time_max = parameters.get("time_max")
                calendar_key = parameters.get("calendar_key")
                aggregate_mode = (parameters.get("aggregate_mode") or "all").lower()
                if aggregate_mode not in ("all", "any"):
                    aggregate_mode = "all"

                if not all([time_min, time_max, free_prefix, busy_prefix]):
                    error_msg = (
                        "Error: 'time_min' and 'time_max' are required. "
                        "'free_prefix' and 'busy_prefix' are required unless set in tool config (YAML/UI)."
                    )
                    logger.warning("Missing required parameters for get_free_slots", call_id=call_id)
                    out = {"status": "error", "message": error_msg}
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out

                def _list_events_for_key(k: str) -> list[dict]:
                    tz_name = _tz_for_key(k)
                    try:
                        time_min_dt = self._normalize_datetime_to_calendar_tz(time_min, tz_name)
                        time_max_dt = self._normalize_datetime_to_calendar_tz(time_max, tz_name)
                    except ValueError as e:
                        logger.warning("Invalid datetime for get_free_slots (key=%s)" % k, call_id=call_id, error=str(e))
                        return []
                    cal_i = _cal_for_key(k)
                    if not getattr(cal_i, "service", None):
                        return []
                    return cal_i.list_events(time_min_dt.isoformat(), time_max_dt.isoformat())

                def _available_intervals_from_events(evts: list[dict]) -> list[tuple[datetime, datetime]]:
                    free_blocks = []
                    busy_blocks = []
                    for e in evts:
                        summary = e.get("summary", "").strip()
                        start_str = e.get("start", {}).get("dateTime")
                        end_str = e.get("end", {}).get("dateTime")
                        if not start_str or not end_str:
                            continue
                        start_dt = self._parse_iso(start_str)
                        end_dt = self._parse_iso(end_str)
                        if summary.startswith(free_prefix):
                            free_blocks.append((start_dt, end_dt))
                        elif summary.startswith(busy_prefix):
                            busy_blocks.append((start_dt, end_dt))
                    free_blocks.sort(key=lambda x: x[0])
                    busy_blocks.sort(key=lambda x: x[0])
                    available: list[tuple[datetime, datetime]] = []
                    for f_start, f_end in free_blocks:
                        current_start = f_start
                        for b_start, b_end in busy_blocks:
                            if b_end <= current_start or b_start >= f_end:
                                continue
                            if current_start < b_start:
                                available.append((current_start, b_start))
                            current_start = max(current_start, b_end)
                        if current_start < f_end:
                            available.append((current_start, f_end))
                    return available

                def _union(intervals: list[list[tuple[datetime, datetime]]]) -> list[tuple[datetime, datetime]]:
                    merged: list[tuple[datetime, datetime]] = []
                    for lst in intervals:
                        for s, e in lst:
                            merged.append((s, e))
                    if not merged:
                        return []
                    merged.sort(key=lambda x: x[0])
                    out_iv: list[tuple[datetime, datetime]] = []
                    cur_s, cur_e = merged[0]
                    for s, e in merged[1:]:
                        if s <= cur_e:
                            cur_e = max(cur_e, e)
                        else:
                            out_iv.append((cur_s, cur_e))
                            cur_s, cur_e = s, e
                    out_iv.append((cur_s, cur_e))
                    return out_iv

                def _intersect(a: list[tuple[datetime, datetime]], b: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
                    i, j = 0, 0
                    res: list[tuple[datetime, datetime]] = []
                    a_sorted = sorted(a, key=lambda x: x[0])
                    b_sorted = sorted(b, key=lambda x: x[0])
                    while i < len(a_sorted) and j < len(b_sorted):
                        s = max(a_sorted[i][0], b_sorted[j][0])
                        e = min(a_sorted[i][1], b_sorted[j][1])
                        if s < e:
                            res.append((s, e))
                        if a_sorted[i][1] < b_sorted[j][1]:
                            i += 1
                        else:
                            j += 1
                    return res

                # Gather intervals per selected calendar (or single target)
                keys_to_use: list[str]
                if legacy_single:
                    keys_to_use = ["default"]
                elif calendar_key and calendar_key in calendars:
                    keys_to_use = [calendar_key]
                else:
                    keys_to_use = list(selected_keys)

                per_cal_intervals: list[list[tuple[datetime, datetime]]] = []
                for k in keys_to_use:
                    evts = await asyncio.to_thread(_list_events_for_key, k)
                    per_cal_intervals.append(_available_intervals_from_events(evts))

                # Aggregate
                if not per_cal_intervals:
                    out = {"status": "success", "message": "Free slot starts: (none)"}
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out

                if len(per_cal_intervals) == 1 or aggregate_mode == "any":
                    available_intervals = _union(per_cal_intervals)
                else:
                    # intersect across all calendars (default)
                    cur = per_cal_intervals[0]
                    for nxt in per_cal_intervals[1:]:
                        cur = _intersect(cur, nxt)
                    available_intervals = cur

                # Duration: from parameter "duration" (minutes), fallback to config
                duration_minutes = parameters.get("duration") or config.get("min_slot_duration_minutes", 15)
                try:
                    duration_minutes = max(1, int(duration_minutes))
                except (TypeError, ValueError):
                    duration_minutes = 15

                duration_td = timedelta(minutes=duration_minutes)

                def round_up_to_next_slot(dt: datetime, step_minutes: int) -> datetime:
                    """Round dt up to next time that is a multiple of step_minutes from midnight (same tz)."""
                    total_minutes = dt.hour * 60 + dt.minute
                    if dt.second or dt.microsecond or total_minutes % step_minutes != 0:
                        q = (total_minutes + step_minutes - 1) // step_minutes
                        new_total = q * step_minutes
                        if new_total >= 24 * 60:
                            days_add = new_total // (24 * 60)
                            new_total = new_total % (24 * 60)
                            base = dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_add)
                            return base.replace(hour=new_total // 60, minute=new_total % 60)
                        return dt.replace(hour=new_total // 60, minute=new_total % 60, second=0, microsecond=0)
                    return dt

                slot_starts: list[datetime] = []
                for s, end_t in available_intervals:
                    if end_t <= s:
                        continue
                    if s + duration_td <= end_t:
                        slot_starts.append(s)
                    start = round_up_to_next_slot(s, duration_minutes)
                    while start + duration_td <= end_t:
                        if start > s:
                            slot_starts.append(start)
                        start += timedelta(minutes=duration_minutes)

                slot_starts.sort()
                results = [t.strftime("%Y-%m-%d %H:%M") for t in slot_starts]
                out = {"status": "success", "message": "Free slot starts: " + ", ".join(results)}
                logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                return out

            if action == "list_events":
                time_min = parameters.get("time_min")
                time_max = parameters.get("time_max")
                calendar_key = parameters.get("calendar_key")
                aggregate_mode = (parameters.get("aggregate_mode") or "all").lower()
                if aggregate_mode not in ("all", "any"):
                    aggregate_mode = "all"
                if not time_min or not time_max:
                    error_msg = "Error: 'time_min' and 'time_max' parameters are required for list_events."
                    logger.warning("Missing time range for list_events", call_id=call_id)
                    out = {"status": "error", "message": error_msg}
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out

                def _list_for_key(k: str) -> list[dict]:
                    tz_name = _tz_for_key(k)
                    try:
                        tmin = self._normalize_datetime_to_calendar_tz(time_min, tz_name).isoformat()
                        tmax = self._normalize_datetime_to_calendar_tz(time_max, tz_name).isoformat()
                    except ValueError as e:
                        logger.warning("Invalid datetime for list_events (key=%s)" % k, call_id=call_id, error=str(e))
                        return []
                    ci = _cal_for_key(k)
                    if not getattr(ci, "service", None):
                        return []
                    return ci.list_events(tmin, tmax)

                if legacy_single:
                    events = await asyncio.to_thread(cal.list_events, *[
                        self._normalize_datetime_to_calendar_tz(time_min, self._get_calendar_tz_name(config)).isoformat(),
                        self._normalize_datetime_to_calendar_tz(time_max, self._get_calendar_tz_name(config)).isoformat(),
                    ])
                    pools = {"default": events}
                else:
                    if calendar_key and calendar_key in calendars:
                        pools = {calendar_key: await asyncio.to_thread(_list_for_key, calendar_key)}
                    else:
                        pools = {}
                        for k in selected_keys:
                            pools[k] = await asyncio.to_thread(_list_for_key, k)

                simplified = []
                for k, evs in pools.items():
                    for e in evs:
                        simplified.append({
                            "id": e.get("id"),
                            "summary": e.get("summary", "No Title"),
                            "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
                            "end": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"),
                            "calendar": k,
                        })
                # No need to aggregate here; return merged list with calendar labels
                out = {"status": "success", "message": "Events listed.", "events": simplified}
                logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                return out

            if action == "get_event":
                event_id = parameters.get("event_id")
                calendar_key = parameters.get("calendar_key")
                if not event_id:
                    error_msg = "Error: 'event_id' parameter is required for get_event."
                    logger.warning("Missing event_id for get_event", call_id=call_id)
                    out = {"status": "error", "message": error_msg}
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out
                # Resolve target cal
                target_key = None
                if legacy_single:
                    target_key = "default"
                    cal_i = cal
                else:
                    if calendar_key and calendar_key in calendars:
                        target_key = calendar_key
                    else:
                        target_key = (selected_keys[0] if selected_keys else None)
                    if not target_key:
                        out = {"status": "error", "message": "No calendar_key provided and no selected calendars configured."}
                        logger.warning("No target calendar for get_event", call_id=call_id)
                        logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                        return out
                    cal_i = _cal_for_key(target_key)
                event = await asyncio.to_thread(cal_i.get_event, event_id)
                if not event:
                    out = {"status": "error", "message": "Event not found."}
                    logger.warning("Event not found", call_id=call_id, event_id=event_id)
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out
                out = {
                    "status": "success",
                    "message": "Event retrieved.",
                    "id": event.get("id"),
                    "summary": event.get("summary"),
                    "description": event.get("description", ""),
                    "start": event.get("start", {}).get("dateTime") or event.get("start", {}).get("date"),
                    "end": event.get("end", {}).get("dateTime") or event.get("end", {}).get("date"),
                    "calendar": target_key,
                }
                logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                return out

            if action == "create_event":
                summary = parameters.get("summary")
                desc = parameters.get("description", "")
                start_dt = parameters.get("start_datetime")
                end_dt = parameters.get("end_datetime")
                calendar_key = parameters.get("calendar_key")
                if not summary or not start_dt or not end_dt:
                    error_msg = (
                        "Error: 'summary', 'start_datetime', and 'end_datetime' are required for create_event."
                    )
                    logger.warning("Missing required parameters for create_event", call_id=call_id)
                    out = {"status": "error", "message": error_msg}
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out
                # Resolve target cal
                if legacy_single:
                    tz_name = self._get_calendar_tz_name(config)
                    cal_i = cal
                    target_key = "default"
                else:
                    target_key = calendar_key or (selected_keys[0] if selected_keys else None)
                    if not target_key or target_key not in calendars:
                        out = {"status": "error", "message": "calendar_key is required (or configure selected_calendars)."}
                        logger.warning("Missing target calendar for create_event", call_id=call_id)
                        logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                        return out
                    tz_name = _tz_for_key(target_key)
                    cal_i = _cal_for_key(target_key)
                # DST-aware: if input has TZ tail, convert to calendar TZ and send local time (no tail)
                try:
                    start_dt_local = self._normalize_datetime_to_calendar_tz(start_dt, tz_name)
                    end_dt_local = self._normalize_datetime_to_calendar_tz(end_dt, tz_name)
                    start_dt_str = start_dt_local.strftime("%Y-%m-%dT%H:%M:%S")
                    end_dt_str = end_dt_local.strftime("%Y-%m-%dT%H:%M:%S")
                except ValueError as e:
                    out = {"status": "error", "message": str(e)}
                    logger.warning("Invalid datetime for create_event", call_id=call_id, error=str(e))
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out
                event = await asyncio.to_thread(cal_i.create_event, summary, desc, start_dt_str, end_dt_str)
                if not event:
                    out = {"status": "error", "message": "Failed to create event."}
                    logger.error("Failed to create event", call_id=call_id)
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out
                out = {
                    "status": "success",
                    "message": "Event created.",
                    "id": event.get("id"),
                    "link": event.get("htmlLink"),
                    "calendar": target_key,
                }
                logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                return out

            if action == "delete_event":
                event_id = parameters.get("event_id")
                calendar_key = parameters.get("calendar_key")
                if not event_id:
                    error_msg = "Error: 'event_id' parameter is required for delete_event."
                    logger.warning("Missing event_id for delete_event", call_id=call_id)
                    out = {"status": "error", "message": error_msg}
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out
                # Resolve target cal
                if legacy_single:
                    cal_i = cal
                    target_key = "default"
                else:
                    target_key = calendar_key or (selected_keys[0] if selected_keys else None)
                    if not target_key or target_key not in calendars:
                        out = {"status": "error", "message": "calendar_key is required (or configure selected_calendars)."}
                        logger.warning("Missing target calendar for delete_event", call_id=call_id)
                        logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                        return out
                    cal_i = _cal_for_key(target_key)
                success = await asyncio.to_thread(cal_i.delete_event, event_id)
                if not success:
                    out = {"status": "error", "message": "Failed to delete event (not found or calendar error)."}
                    logger.warning("Failed to delete event", call_id=call_id, event_id=event_id)
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out
                out = {"status": "success", "message": "Event deleted.", "id": event_id, "calendar": target_key}
                logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                return out

            error_msg = f"Error: Unknown action '{action}'."
            logger.warning("Unknown action", call_id=call_id, action=action)
            out = {"status": "error", "message": error_msg}
            logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
            return out

        except Exception as e:
            logger.error(
                "GCalendarTool failed",
                call_id=call_id,
                action=action,
                error=str(e),
                exc_info=True,
            )
            out = {"status": "error", "message": "An unexpected calendar error occurred."}
            logger.info("Tool response to AI", call_id=call_id, action=action or "?", status=out.get("status"))
            return out

