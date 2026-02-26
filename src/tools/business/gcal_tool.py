"""
Google Calendar tool for Asterisk AI Voice Agent.

Supports listing events, getting a single event, creating events, deleting events,
and finding free appointment slots (with configurable duration and duration-aligned slot starts).

Environment: GOOGLE_CALENDAR_CREDENTIALS (path to service account JSON);
GOOGLE_CALENDAR_TZ for timezone (fallback: TZ).
"""

import os
import re
import structlog
from datetime import datetime, timedelta
from typing import Dict, Any

from src.tools.base import Tool, ToolDefinition, ToolCategory
from src.tools.context import ToolExecutionContext

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

try:
    from src.tools.business.gcalendar import GCalendar
except ImportError:
    from gcalendar import GCalendar

logger = structlog.get_logger(__name__)


def _get_calendar_tz_name() -> str:
    """Resolve timezone name: GOOGLE_CALENDAR_TZ, else TZ, else UTC."""
    tz = os.environ.get("GOOGLE_CALENDAR_TZ", "").strip()
    if not tz:
        tz = os.environ.get("TZ", "").strip()
    return tz or "UTC"


def _get_calendar_zone():
    """Return ZoneInfo for GOOGLE_CALENDAR_TZ (DST-aware). Falls back to UTC if unavailable."""
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(_get_calendar_tz_name())
    except Exception:
        return ZoneInfo("UTC")


def _format_offset(dt: datetime) -> str:
    """Format utcoffset as +HH:MM or -HH:MM (DST-aware for the given datetime)."""
    off = dt.utcoffset()
    if off is None:
        return "+00:00"
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    h, r = divmod(abs(total), 3600)
    m = r // 60
    return f"{sign}{h:02d}:{m:02d}"


def _naive_to_rfc3339_for_calendar(naive_iso: str) -> str:
    """
    Strip any trailing timezone from the input, interpret the naive datetime in
    GOOGLE_CALENDAR_TZ (DST-aware), then return RFC3339 with the offset that applies
    on that date. E.g. "2026-02-02T00:00:00+02:00" → strip "+02:00", treat as
    Europe/Budapest local time; on 2026-02-02 Budapest is in standard time, so
    output "2026-02-02T00:00:00+01:00".
    """
    s = _strip_tz_from_iso(naive_iso)
    if not s:
        return naive_iso
    try:
        dt_naive = datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        return naive_iso
    zone = _get_calendar_zone()
    if zone is None:
        return naive_iso
    dt_local = dt_naive.replace(tzinfo=zone)
    off = _format_offset(dt_local)
    return dt_naive.strftime("%Y-%m-%dT%H:%M:%S") + off


def _strip_tz_from_iso(s: str) -> str:
    """Remove timezone part from an ISO datetime string (Z or ±HH:MM)."""
    if not s or not isinstance(s, str):
        return s
    s = s.strip()
    if s.upper().endswith("Z"):
        return s[:-1].strip()
    return re.sub(r"[-+]\d{2}:?\d{2}(:\d{2})?$", "", s).strip()


def _format_dt_in_calendar_tz(dt: datetime) -> str:
    """
    Format datetime in GOOGLE_CALENDAR_TZ with the DST-aware offset for that moment.
    Returns "YYYY-MM-DD HH:MM +HH:MM" (e.g. 2026-02-02 00:00 +01:00 for Budapest in winter).
    Naive dt is treated as local time in calendar TZ.
    """
    zone = _get_calendar_zone()
    if zone is None:
        if dt.tzinfo is None:
            return dt.strftime("%Y-%m-%d %H:%M +00:00")
        return dt.strftime("%Y-%m-%d %H:%M") + " " + _format_offset(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
    dt_in_zone = dt.astimezone(zone)
    off = _format_offset(dt_in_zone)
    return dt_in_zone.strftime("%Y-%m-%d %H:%M") + " " + off

# Schema for Google Live / Vertex and OpenAI (input_schema is provider-agnostic)
_GOOGLE_CALENDAR_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list_events", "get_event", "create_event", "delete_event", "get_free_slots"],
            "description": "The calendar operation to perform."
        },
        "time_min": {
            "type": "string",
            "description": "Start time (ISO 8601 without timezone, e.g. 2025-02-24T09:00:00). Interpreted in GOOGLE_CALENDAR_TZ (DST-aware). Required for list_events and get_free_slots."
        },
        "time_max": {
            "type": "string",
            "description": "End time (ISO 8601 without timezone). Interpreted in GOOGLE_CALENDAR_TZ (DST-aware). Required for list_events and get_free_slots."
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
            "description": "Start time for the new event (ISO 8601 without timezone). Interpreted in GOOGLE_CALENDAR_TZ (DST-aware). Required for create_event."
        },
        "end_datetime": {
            "type": "string",
            "description": "End time for the new event (ISO 8601 without timezone). Interpreted in GOOGLE_CALENDAR_TZ (DST-aware). Required for create_event."
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
        self.cal = GCalendar()

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

    @staticmethod
    def _strip_tz_from_datetime_str(s: str) -> str:
        """Remove timezone part from an ISO datetime string (Z or ±HH:MM)."""
        if not s or not isinstance(s, str):
            return s
        s = s.strip()
        if s.upper().endswith('Z'):
            return s[:-1]
        return re.sub(r'[-+]\d{2}:?\d{2}(:\d{2})?$', '', s)

    def _get_config(self, context: ToolExecutionContext) -> Dict[str, Any]:
        """
        Get google_calendar config: from context when available, else from ai-agent.yaml.
        """
        if context and getattr(context, "get_config_value", None):
            return context.get_config_value("tools.google_calendar", {}) or {}
        return self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """
        Load google_calendar config from ai-agent.yaml (fallback when context has no config).
        """
        try:
            from src.config import load_config
            config = load_config()
            tools_config = getattr(config, "tools", None)
            if not isinstance(tools_config, dict):
                return {}
            return tools_config.get("google_calendar", {})
        except Exception as e:
            logger.warning("Failed to load config for google_calendar", error=str(e))
            return {}

    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext,
    ) -> Dict[str, Any]:
        """
        Routes the request to the underlying GCalendar module or executes custom logic based on the action.
        """
        call_id = getattr(context, "call_id", None) or ""
        logger.info("GCalendarTool execution triggered by LLM", call_id=call_id)
        logger.debug("Raw arguments received from LLM", call_id=call_id, parameters=parameters)

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
            logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
            return out

        try:
            if action == "get_free_slots":
                # Prefixes: config (YAML/UI) takes precedence as defaults; LLM can override via parameters
                free_prefix = parameters.get("free_prefix") or config.get("free_prefix")
                busy_prefix = parameters.get("busy_prefix") or config.get("busy_prefix")
                time_min = parameters.get("time_min")
                time_max = parameters.get("time_max")

                if not all([time_min, time_max, free_prefix, busy_prefix]):
                    error_msg = (
                        "Error: 'time_min' and 'time_max' are required. "
                        "'free_prefix' and 'busy_prefix' are required unless set in tool config (YAML/UI)."
                    )
                    logger.warning("Missing required parameters for get_free_slots", call_id=call_id)
                    out = {"status": "error", "message": error_msg}
                    logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                    return out

                logger.debug(
                    "Calculating free slots",
                    call_id=call_id,
                    free_prefix=free_prefix,
                    busy_prefix=busy_prefix,
                )
                time_min_rfc = _naive_to_rfc3339_for_calendar(time_min)
                time_max_rfc = _naive_to_rfc3339_for_calendar(time_max)
                events = self.cal.list_events(time_min_rfc, time_max_rfc)

                free_blocks = []
                busy_blocks = []

                # 1. Categorize events based on prefixes
                for e in events:
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

                # 2. Sort both lists chronologically
                free_blocks.sort(key=lambda x: x[0])
                busy_blocks.sort(key=lambda x: x[0])

                available_intervals = []

                # 3. Subtraction logic
                for f_start, f_end in free_blocks:
                    current_start = f_start

                    for b_start, b_end in busy_blocks:
                        if b_end <= current_start or b_start >= f_end:
                            continue
                        if current_start < b_start:
                            available_intervals.append((current_start, b_start))
                        current_start = max(current_start, b_end)

                    if current_start < f_end:
                        available_intervals.append((current_start, f_end))

                # 4. Duration: from parameter "duration" (minutes), fallback to config
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
                    # Include the actual start of the free slot first to fill the calendar better
                    if s + duration_td <= end_t:
                        slot_starts.append(s)
                    # Then all duration-aligned starts after s
                    start = round_up_to_next_slot(s, duration_minutes)
                    while start + duration_td <= end_t:
                        if start > s:  # avoid duplicate when s is already aligned
                            slot_starts.append(start)
                        start += timedelta(minutes=duration_minutes)

                slot_starts.sort()
                results = [_format_dt_in_calendar_tz(t) for t in slot_starts]
                out = {"status": "success", "message": "Free slot starts: " + ", ".join(results)}
                logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                return out

            if action == "list_events":
                time_min = parameters.get("time_min")
                time_max = parameters.get("time_max")
                if not time_min or not time_max:
                    error_msg = "Error: 'time_min' and 'time_max' parameters are required for list_events."
                    logger.warning("Missing time range for list_events", call_id=call_id)
                    out = {"status": "error", "message": error_msg}
                    logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                    return out
                time_min_rfc = _naive_to_rfc3339_for_calendar(time_min)
                time_max_rfc = _naive_to_rfc3339_for_calendar(time_max)
                events = self.cal.list_events(time_min_rfc, time_max_rfc)
                simplified_events = []
                for e in events:
                    start_str = e.get("start", {}).get("dateTime")
                    end_str = e.get("end", {}).get("dateTime")
                    simplified_events.append({
                        "id": e.get("id"),
                        "summary": e.get("summary", "No Title"),
                        "start": _format_dt_in_calendar_tz(self._parse_iso(start_str)) if start_str else start_str,
                        "end": _format_dt_in_calendar_tz(self._parse_iso(end_str)) if end_str else end_str,
                    })
                out = {"status": "success", "message": "Events listed.", "events": simplified_events}
                logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                return out

            if action == "get_event":
                event_id = parameters.get("event_id")
                if not event_id:
                    error_msg = "Error: 'event_id' parameter is required for get_event."
                    logger.warning("Missing event_id for get_event", call_id=call_id)
                    out = {"status": "error", "message": error_msg}
                    logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                    return out
                event = self.cal.get_event(event_id)
                if not event:
                    out = {"status": "error", "message": "Event not found."}
                    logger.warning("Event not found", call_id=call_id, event_id=event_id)
                    logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                    return out
                start_str = event.get("start", {}).get("dateTime")
                end_str = event.get("end", {}).get("dateTime")
                out = {
                    "status": "success",
                    "message": "Event retrieved.",
                    "id": event.get("id"),
                    "summary": event.get("summary"),
                    "description": event.get("description", ""),
                    "start": _format_dt_in_calendar_tz(self._parse_iso(start_str)) if start_str else start_str,
                    "end": _format_dt_in_calendar_tz(self._parse_iso(end_str)) if end_str else end_str,
                }
                logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                return out

            if action == "delete_event":
                event_id = parameters.get("event_id")
                if not event_id:
                    error_msg = "Error: 'event_id' parameter is required for delete_event."
                    logger.warning("Missing event_id for delete_event", call_id=call_id)
                    out = {"status": "error", "message": error_msg}
                    logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                    return out
                deleted = self.cal.delete_event(event_id)
                if not deleted:
                    out = {"status": "error", "message": "Failed to delete event (event not found or no permission)."}
                    logger.warning("Failed to delete event", call_id=call_id, event_id=event_id)
                    logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                    return out
                out = {"status": "success", "message": "Event deleted."}
                logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                return out

            if action == "create_event":
                summary = parameters.get("summary")
                desc = parameters.get("description", "")
                start_dt = parameters.get("start_datetime")
                end_dt = parameters.get("end_datetime")
                if start_dt:
                    start_dt = self._strip_tz_from_datetime_str(start_dt)
                if end_dt:
                    end_dt = self._strip_tz_from_datetime_str(end_dt)
                if not summary or not start_dt or not end_dt:
                    error_msg = (
                        "Error: 'summary', 'start_datetime', and 'end_datetime' are required for create_event."
                    )
                    logger.warning("Missing required parameters for create_event", call_id=call_id)
                    out = {"status": "error", "message": error_msg}
                    logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                    return out
                event = self.cal.create_event(summary, desc, start_dt, end_dt)
                if not event:
                    out = {"status": "error", "message": "Failed to create event."}
                    logger.error("Failed to create event", call_id=call_id)
                    logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                    return out
                out = {
                    "status": "success",
                    "message": "Event created.",
                    "id": event.get("id"),
                    "link": event.get("htmlLink"),
                }
                logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
                return out

            error_msg = f"Error: Unknown action '{action}'."
            logger.warning("Unknown action", call_id=call_id, action=action)
            out = {"status": "error", "message": error_msg}
            logger.info("Tool response to AI", call_id=call_id, action=action, response=out)
            return out

        except Exception as e:
            logger.error(
                "GCalendarTool failed",
                call_id=call_id,
                action=action,
                error=str(e),
                exc_info=True,
            )
            out = {"status": "error", "message": f"Calendar error: {str(e)}"}
            logger.info("Tool response to AI", call_id=call_id, action=action or "?", response=out)
            return out

