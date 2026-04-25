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
import threading
import structlog
from datetime import datetime, timedelta
from typing import Dict, Any
from zoneinfo import ZoneInfo

from src.tools.base import Tool, ToolDefinition, ToolCategory
from src.tools.context import ToolExecutionContext

from src.tools.business.gcalendar import GCalendar, GoogleCalendarApiError, _get_timezone

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
            "description": "For multi-calendar get_free_slots: 'all' = intersection (default), 'any' = union. Ignored when calendar_key is set."
        },
        "time_min": {
            "type": "string",
            "description": (
                "ISO 8601 start time. Required for list_events and get_free_slots. "
                "Tip: any TZ tail (Z or ±HH:MM) is stripped and the wall-clock is "
                "interpreted as the calendar's local timezone. Pass a naive string "
                "like '2026-04-25T09:00:00' to be unambiguous."
            )
        },
        "time_max": {
            "type": "string",
            "description": (
                "ISO 8601 end time. Required for list_events and get_free_slots. "
                "Same timezone behavior as time_min: TZ tail is stripped, wall-clock "
                "treated as calendar-local. Pass naive '2026-04-28T17:00:00' to be "
                "unambiguous."
            )
        },
        "free_prefix": {
            "type": "string",
            "description": (
                "OPTIONAL — DO NOT PASS unless the operator explicitly told you to use a "
                "title-based availability scheme. By default the operator's calendar uses "
                "Google's native free/busy API and you should OMIT this parameter entirely. "
                "Only pass a value (e.g. 'Open') when the operator's prompt explicitly says "
                "'open windows are events titled with X' — that's the only correct use case."
            )
        },
        "busy_prefix": {
            "type": "string",
            "description": (
                "OPTIONAL — DO NOT PASS unless the operator explicitly told you to use a "
                "title-based busy scheme. Default behavior uses Google's native free/busy. "
                "Only pass a value when the operator's prompt explicitly says so."
            )
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
            "description": (
                "ISO 8601 start time for the new event. Required for create_event. "
                "Use the calendar's local time (the slot strings returned by "
                "get_free_slots are already in calendar-local time; pass them as-is)."
            )
        },
        "end_datetime": {
            "type": "string",
            "description": (
                "ISO 8601 end time for the new event. Required for create_event. "
                "MUST equal start_datetime PLUS the meeting duration (typically 30 "
                "minutes — same as the slot grid returned by get_free_slots). DO NOT "
                "extend end_datetime to end-of-day, end-of-availability, or the start "
                "of the next slot. Example: if start_datetime is "
                "'2026-04-27T11:00:00' for a 30-minute meeting, end_datetime must be "
                "'2026-04-27T11:30:00' — not '2026-04-27T18:00:00' or any later time."
            )
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

    # Cap on the per-call_id event-tracking map. Every successful create_event
    # adds an entry; delete_event removes its own, but most calls book then
    # hang up without ever issuing a delete, so entries accumulate over the
    # engine's lifetime if unbounded. 1024 is generous — at typical voice-agent
    # call rates that's many hours of distinct concurrent calls — and LRU
    # eviction keeps the working set focused on calls that are likely still
    # in progress. (Codex P2 feedback.)
    _LAST_EVENT_CACHE_CAP = 1024

    def __init__(self):
        super().__init__()
        logger.debug("Initializing GCalendarTool instance")
        self._cal = None
        self._cal_config_key = None
        self._cals: dict[tuple[str, str, str], GCalendar] = {}
        self._cals_lock = threading.Lock()
        # Per-call_id tracking of the most-recent successful create_event so
        # delete_event can recover from LLM-hallucinated event_ids. Live test
        # found Gemini calling delete_event with a fabricated id (e.g.
        # 'f0l1q7d4j1t5n0b4h3c2a1m0') instead of the real id from the prior
        # create_event success — Google returned 404, and the model then
        # called create_event anyway, leaving duplicate events on the calendar.
        # OrderedDict + LRU eviction at _LAST_EVENT_CACHE_CAP entries keeps
        # this bounded — without the cap the typical flow (book → end call,
        # never delete) would leak an entry per call indefinitely.
        from collections import OrderedDict
        self._last_event_per_call: "OrderedDict[str, dict]" = OrderedDict()
        self._last_event_lock = threading.Lock()

    def _get_cal(self, config: Dict[str, Any]) -> GCalendar:
        """Return a GCalendar instance, (re)creating if config changed or service is None.

        Legacy single-calendar path. Includes ``subject`` in the cache key so DWD
        works consistently here too — Codex feedback #7. The cache key tuple
        mirrors _get_or_create_cal's tuple shape for the same correctness reason
        (different impersonation targets must not reuse the wrong client).
        """
        creds_path = config.get("credentials_path", "")
        cal_id = config.get("calendar_id", "")
        tz = config.get("timezone", "")
        subject = config.get("subject", "")
        config_key = (creds_path, cal_id, tz, subject)
        if self._cal is None or self._cal.service is None or self._cal_config_key != config_key:
            self._cal = GCalendar(
                credentials_path=creds_path,
                calendar_id=cal_id,
                timezone=tz,
                subject=subject,
            )
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

    def _get_or_create_cal(
        self,
        creds_path: str,
        cal_id: str,
        tz: str,
        subject: str = "",
    ) -> GCalendar:
        # CRITICAL: subject MUST be part of the cache key.
        # With DWD, the same (creds_path, cal_id, tz) tuple can resolve to
        # DIFFERENT authenticated identities depending on whether/who we're
        # impersonating. If subject weren't part of the key, switching the
        # impersonation target would silently reuse the wrong cached client
        # and act as the previous subject. Codex flagged this explicitly.
        key = (creds_path or "", cal_id or "", tz or "", subject or "")
        with self._cals_lock:
            inst = self._cals.get(key)
            if inst is None or inst.service is None:
                inst = GCalendar(
                    credentials_path=creds_path,
                    calendar_id=cal_id,
                    timezone=tz,
                    subject=subject,
                )
                self._cals[key] = inst
            return inst

    def _resolve_calendars(self, config: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
        """
        Return dict of calendar_key -> {credentials_path, calendar_id, timezone, subject}.
        Falls back to single-calendar legacy keys if calendars{} missing.

        ``subject`` (when present) enables Domain-Wide Delegation — the SA
        impersonates that user for API calls. Per-calendar so different
        calendars in the same tenant can impersonate different users.
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
                    # Subject is opt-in per entry; falls back to a tool-level
                    # default if specified there (rare but cheap to support).
                    "subject": v.get("subject", "") or config.get("subject", ""),
                }
        else:
            # Legacy: single calendar from tool config/env
            cals["default"] = {
                "credentials_path": config.get("credentials_path", ""),
                "calendar_id": config.get("calendar_id", ""),
                "timezone": config.get("timezone", ""),
                "subject": config.get("subject", ""),
            }
        return cals

    def _selected_calendar_keys(self, config: Dict[str, Any]) -> list[str]:
        """Return selected calendar keys for this context. Missing = all; invalid/empty = none (fail closed)."""
        calendars = self._resolve_calendars(config)
        raw = config.get("selected_calendars")
        # Missing key means "use all configured calendars"
        if raw is None:
            return list(calendars.keys())
        # Present but not a list (e.g. typo, scalar) — fail closed
        if not isinstance(raw, (list, tuple)):
            return []
        # Filter to valid keys only; empty list = none selected (fail closed)
        return [str(s) for s in raw if str(s) in calendars]

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
        Get google_calendar config: base from tools.google_calendar, with per-context overlay if present.
        """
        base: Dict[str, Any] = {}
        overlay: Dict[str, Any] = {}
        if context and getattr(context, "get_config_value", None):
            base = context.get_config_value("tools.google_calendar", {}) or {}
            ctx_name = getattr(context, "context_name", None)
            if ctx_name:
                # Per-context override under contexts.<name>.tool_overrides.google_calendar
                # Only catch KeyError/TypeError (path not found); other errors must surface
                try:
                    overlay = context.get_config_value(f"contexts.{ctx_name}.tool_overrides.google_calendar", {}) or {}
                except (KeyError, TypeError, AttributeError):
                    overlay = {}
        # Merge (overlay wins)
        out = dict(base or {})
        for k, v in (overlay or {}).items():
            out[k] = v
        return out or self._load_config()

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

        # If legacy single-calendar (env-var-only; no explicit `calendars` map in
        # config), keep the backward-compat code path that reads root-level
        # credentials_path / calendar_id / timezone via self._get_cal(config).
        #
        # CRITICAL: must NOT match when the user has an explicit nested
        # tools.google_calendar.calendars.default entry — that's a real
        # single-calendar multi-account config and its credentials live in the
        # nested entry, not at the root.
        #
        # Match the truthiness semantics of `_resolve_calendars`: it treats any
        # non-dict OR empty-dict `calendars` value as legacy (and falls through
        # to the root-level env-var path to materialize `calendars.default`), so
        # `calendars: {}` in YAML must also count as legacy here for consistency.
        _raw_calendars = config.get("calendars")
        legacy_single = (
            (not isinstance(_raw_calendars, dict) or not _raw_calendars)
            and len(calendars) == 1
            and "default" in calendars
        )

        # Helper: get timezone name for a specific calendar
        def _tz_for_key(k: str) -> str:
            tz = calendars[k].get("timezone", "")
            return _get_timezone(tz)

        # Helper: build or reuse GCalendar for a key
        def _cal_for_key(k: str) -> GCalendar:
            cfg = calendars[k]
            return self._get_or_create_cal(
                cfg.get("credentials_path", ""),
                cfg.get("calendar_id", ""),
                cfg.get("timezone", ""),
                cfg.get("subject", ""),
            )

        # Helper: validate calendar_key is in selected_keys (not just global calendars)
        def _validate_calendar_key(calendar_key: str, action_name: str) -> dict | None:
            """Return error dict if calendar_key is invalid/unauthorized, else None."""
            if calendar_key not in calendars:
                msg = f"Unknown calendar_key '{calendar_key}'. Available: {', '.join(calendars.keys())}."
                logger.warning("Unknown calendar_key", call_id=call_id, action=action_name, calendar_key=calendar_key)
                return {"status": "error", "message": msg}
            if calendar_key not in selected_keys:
                msg = f"Calendar '{calendar_key}' is not selected for this context. Selected: {', '.join(selected_keys)}."
                logger.warning("calendar_key not in selected_calendars", call_id=call_id, action=action_name, calendar_key=calendar_key)
                return {"status": "error", "message": msg}
            return None

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
                # Availability resolution: two modes, switched by whether free_prefix is
                # configured.
                #   - "title_prefix" (default when free_prefix is set): scan event titles
                #     for events starting with free_prefix (open windows) and busy_prefix
                #     (blocked time inside open windows). Operators define their working
                #     hours by creating "Open 9-12" / "Open 14-17" events.
                #   - "freebusy" (default when free_prefix is empty/blank): use Google's
                #     native freebusy API for busy intervals, intersected with a default
                #     working-hours mask (Mon-Fri 09:00–17:00 in the calendar's timezone).
                #     No need to seed Open events — sensible default for operators who
                #     just want "during business hours, when am I not in another meeting".
                # The mode switch is implicit on free_prefix being blank so existing
                # configs (which set free_prefix='Open') keep their behavior.
                # Single canonical rule for free_prefix → mode selection
                # (CodeRabbit major: previously diverged across CHANGELOG/docs):
                #
                #   config has free_prefix as a non-empty string  → title-prefix mode (use that prefix)
                #   config has free_prefix='' (explicit blank)    → freebusy mode (operator's deliberate choice)
                #   config has NO free_prefix key                 → freebusy mode (defaults match the explicit-blank case)
                #
                # In ALL cases, the operator's choice wins over LLM-supplied
                # values. The LLM still gets to *narrow* an active title-prefix
                # mode by passing a different prefix, but it cannot escape
                # freebusy mode by passing 'Open' when the operator has chosen
                # freebusy. Background: Gemini auto-fills free_prefix='Open'
                # from the schema example even when the operator has cleared
                # it in the UI; without operator-wins precedence, that LLM
                # noise would override deliberate UI configuration.
                config_free_raw = config.get("free_prefix")
                config_free = (config_free_raw or "").strip() if config_free_raw is not None else ""
                if config_free:
                    # Non-empty config value — title-prefix mode. LLM can
                    # override the prefix string (e.g. for cross-team setups
                    # where different demo contexts use different prefix
                    # conventions on the same calendar) but stays in
                    # title-prefix mode.
                    _free_pref_explicit = parameters.get("free_prefix")
                    free_prefix = (
                        _free_pref_explicit.strip() if _free_pref_explicit
                        else config_free
                    )
                else:
                    # Config blank or absent — freebusy mode regardless of
                    # what the LLM passes. Operator-wins.
                    free_prefix = ""
                _busy_pref_raw = parameters.get("busy_prefix") or config.get("busy_prefix") or ""
                busy_prefix = (_busy_pref_raw or "").strip() or "Busy"
                availability_mode = "title_prefix" if free_prefix else "freebusy"
                time_min = parameters.get("time_min")
                time_max = parameters.get("time_max")
                calendar_key = parameters.get("calendar_key")
                aggregate_mode = (parameters.get("aggregate_mode") or "all").lower()
                if aggregate_mode not in ("all", "any"):
                    aggregate_mode = "all"

                if not all([time_min, time_max]):
                    # IMPORTANT: tell the LLM exactly how to recover. Without an explicit
                    # "retry with..." instruction, gpt-4o-mini (deepgram think stage) and
                    # similar models conflate this with a configuration error and tell
                    # the caller "the calendar isn't configured" — which then trips the
                    # SCHEDULING-fallback prompt rule. Be precise: this is recoverable.
                    error_msg = (
                        "Missing required parameters: 'time_min' and 'time_max' are required for "
                        "get_free_slots. Retry the same call with ISO 8601 timestamps for both, "
                        "e.g. time_min='2026-04-25T09:00:00Z' and time_max='2026-04-28T17:00:00Z'. "
                        "Do NOT tell the caller the calendar is not configured — this is a parameter "
                        "error you can retry."
                    )
                    logger.warning("Missing required parameters for get_free_slots", call_id=call_id)
                    out = {"status": "error", "error_code": "missing_parameters", "message": error_msg}
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out

                def _list_events_for_key(k: str) -> list[dict]:
                    tz_name = _tz_for_key(k)
                    time_min_dt = self._normalize_datetime_to_calendar_tz(time_min, tz_name)
                    time_max_dt = self._normalize_datetime_to_calendar_tz(time_max, tz_name)
                    cal_i = _cal_for_key(k)
                    if not getattr(cal_i, "service", None):
                        return []
                    return cal_i.list_events(time_min_dt.isoformat(), time_max_dt.isoformat())

                def _freebusy_for_key(k: str) -> tuple[list[tuple[str, str]], str]:
                    """Query Google freebusy for one calendar, returns (busy_intervals, tz_name).

                    Used in 'freebusy' availability mode (when free_prefix is blank).
                    Busy intervals are returned as raw ISO strings; the caller normalizes.
                    """
                    tz_name = _tz_for_key(k)
                    time_min_dt = self._normalize_datetime_to_calendar_tz(time_min, tz_name)
                    time_max_dt = self._normalize_datetime_to_calendar_tz(time_max, tz_name)
                    cal_i = _cal_for_key(k)
                    if not getattr(cal_i, "service", None):
                        return [], tz_name
                    return cal_i.freebusy_query(time_min_dt.isoformat(), time_max_dt.isoformat()), tz_name

                def _working_hours_mask(
                    range_start: datetime, range_end: datetime, tz_name: str,
                    work_start_hour: int, work_end_hour: int, work_days: set,
                ) -> list[tuple[datetime, datetime]]:
                    """Generate working-hour intervals within [range_start, range_end].

                    work_days uses Python's weekday() convention (Mon=0..Sun=6). Both
                    bounds are inclusive on the day, exclusive on the hour. Crossing
                    DST is handled by ZoneInfo via .astimezone().
                    """
                    try:
                        tz = ZoneInfo(tz_name)
                    except (KeyError, TypeError):
                        tz = ZoneInfo("UTC")
                    rs = range_start.astimezone(tz)
                    re_ = range_end.astimezone(tz)
                    out: list[tuple[datetime, datetime]] = []
                    cur_day = rs.replace(hour=0, minute=0, second=0, microsecond=0)
                    while cur_day <= re_:
                        if cur_day.weekday() in work_days:
                            day_open = cur_day.replace(hour=work_start_hour)
                            day_close = cur_day.replace(hour=work_end_hour) if work_end_hour < 24 else (cur_day + timedelta(days=1))
                            seg_s = max(day_open, rs)
                            seg_e = min(day_close, re_)
                            if seg_s < seg_e:
                                out.append((seg_s, seg_e))
                        cur_day = cur_day + timedelta(days=1)
                    return out

                # Working hours config (only used in freebusy mode). Conservative
                # business-hours default. Operators can override via tool config.
                # Future PR: surface in UI; for now accept YAML overrides.
                #
                # Validate ranges defensively — without this, an operator typo
                # like working_hours_start=25 would crash at the `cur_day.replace
                # (hour=work_start_hour)` call deep in _working_hours_mask with
                # an unhelpful ValueError. Range checks here keep the failure
                # mode predictable: invalid value → fall back to default + log
                # warning so the operator notices.
                def _coerce_hour(raw_value, default: int, key: str) -> int:
                    try:
                        v = int(raw_value)
                    except (TypeError, ValueError):
                        if raw_value is not None:
                            logger.warning(
                                "Invalid working-hours value; falling back to default",
                                key=key, raw_value=raw_value, default=default,
                            )
                        return default
                    if v < 0 or v > 24:
                        logger.warning(
                            "working-hours value out of range [0..24]; falling back to default",
                            key=key, value=v, default=default,
                        )
                        return default
                    return v

                work_start_hour = _coerce_hour(
                    config.get("working_hours_start"), 9, "working_hours_start",
                )
                work_end_hour = _coerce_hour(
                    config.get("working_hours_end"), 17, "working_hours_end",
                )
                if work_end_hour <= work_start_hour:
                    logger.warning(
                        "working_hours_end must be greater than working_hours_start; reverting to defaults",
                        work_start_hour=work_start_hour, work_end_hour=work_end_hour,
                    )
                    work_start_hour, work_end_hour = 9, 17
                _wd_raw = config.get("working_days")
                if isinstance(_wd_raw, list) and _wd_raw:
                    try:
                        # Constrain to Python weekday() range (0=Mon..6=Sun);
                        # ignore out-of-range entries (e.g., a typo of 7).
                        work_days_set = {int(d) for d in _wd_raw if 0 <= int(d) <= 6}
                        if not work_days_set:
                            raise ValueError("no valid weekday values")
                    except (TypeError, ValueError) as wd_err:
                        logger.warning(
                            "Invalid working_days; falling back to Mon-Fri",
                            raw_value=_wd_raw, error=str(wd_err),
                        )
                        work_days_set = {0, 1, 2, 3, 4}
                else:
                    work_days_set = {0, 1, 2, 3, 4}  # Mon-Fri

                def _available_intervals_from_events(
                    evts: list[dict],
                ) -> tuple[list[tuple[datetime, datetime]], int, int]:
                    """Compute available intervals from events.

                    Returns (intervals, free_block_count, busy_block_count). The block counts
                    are surfaced so the caller can distinguish "no Open windows configured"
                    from "all Open windows are fully booked" — two failure modes that look
                    identical from the empty-intervals shape alone.
                    """
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
                    return available, len(free_blocks), len(busy_blocks)

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
                elif calendar_key:
                    err = _validate_calendar_key(calendar_key, "get_free_slots")
                    if err:
                        return err
                    keys_to_use = [calendar_key]
                else:
                    keys_to_use = list(selected_keys)

                # Validate time_min/time_max once before per-calendar loop
                _validation_tz = _tz_for_key(keys_to_use[0]) if not legacy_single else calendar_tz_name
                try:
                    self._normalize_datetime_to_calendar_tz(time_min, _validation_tz)
                    self._normalize_datetime_to_calendar_tz(time_max, _validation_tz)
                except ValueError as e:
                    out = {"status": "error", "message": str(e)}
                    logger.warning("Invalid datetime for get_free_slots", call_id=call_id, error=str(e))
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out

                per_cal_intervals: list[list[tuple[datetime, datetime]]] = []
                failed_keys: list[str] = []
                # Track per-calendar block counts so the response builder can
                # distinguish "calendar X has no Open windows" (intersection
                # impossibility) from "all calendars are fully booked".
                # Codex feedback #6.
                per_cal_free_counts: dict[str, int] = {}
                per_cal_busy_counts: dict[str, int] = {}
                total_free_blocks = 0
                total_busy_blocks = 0
                for k in keys_to_use:
                    cal_i = _cal_for_key(k)
                    if not getattr(cal_i, "service", None):
                        failed_keys.append(k)
                        continue
                    try:
                        if availability_mode == "freebusy":
                            # Mode B: native Google free/busy + working-hours mask.
                            # Busy intervals come from freebusy.query(); open windows
                            # are synthesized from the configured working hours within
                            # [time_min, time_max]. This is the default when no
                            # free_prefix is configured — operators don't have to seed
                            # "Open" events.
                            busy_isos, tz_name_k = await asyncio.to_thread(_freebusy_for_key, k)
                            tn_min = self._normalize_datetime_to_calendar_tz(time_min, tz_name_k)
                            tn_max = self._normalize_datetime_to_calendar_tz(time_max, tz_name_k)
                            free_blocks_dt = _working_hours_mask(
                                tn_min, tn_max, tz_name_k,
                                work_start_hour, work_end_hour, work_days_set,
                            )
                            busy_blocks_dt: list[tuple[datetime, datetime]] = []
                            parse_failed = False
                            for s_iso, e_iso in busy_isos:
                                try:
                                    busy_blocks_dt.append((self._parse_iso(s_iso), self._parse_iso(e_iso)))
                                except Exception as parse_err:
                                    # FAIL CLOSED — silently dropping a busy
                                    # interval would surface bookable slots
                                    # inside an actually-busy window, exactly
                                    # the kind of correctness bug the rest of
                                    # the freebusy flow is designed to prevent.
                                    # CodeRabbit major finding. Mark the whole
                                    # calendar as failed for this query.
                                    logger.warning(
                                        "Failed to parse freebusy interval; treating calendar as unavailable",
                                        call_id=call_id,
                                        calendar_key=k,
                                        bad_start=s_iso,
                                        bad_end=e_iso,
                                        error=str(parse_err),
                                    )
                                    parse_failed = True
                                    break
                            if parse_failed:
                                failed_keys.append(k)
                                continue
                            # Subtract busy from each working-hours block (mirrors the
                            # title-prefix free-vs-busy intersection logic).
                            free_blocks_dt.sort(key=lambda x: x[0])
                            busy_blocks_dt.sort(key=lambda x: x[0])
                            intervals: list[tuple[datetime, datetime]] = []
                            for f_start, f_end in free_blocks_dt:
                                current_start = f_start
                                for b_start, b_end in busy_blocks_dt:
                                    if b_end <= current_start or b_start >= f_end:
                                        continue
                                    if current_start < b_start:
                                        intervals.append((current_start, b_start))
                                    current_start = max(current_start, b_end)
                                if current_start < f_end:
                                    intervals.append((current_start, f_end))
                            fb_count = len(free_blocks_dt)
                            bb_count = len(busy_blocks_dt)
                        else:
                            evts = await asyncio.to_thread(_list_events_for_key, k)
                            intervals, fb_count, bb_count = _available_intervals_from_events(evts)
                    except GoogleCalendarApiError as api_err:
                        # Runtime failure (revoked share, expired DWD, network
                        # error, etc.) — fail per-calendar and let the existing
                        # failed_keys logic decide whether to fail the whole
                        # operation in 'all' mode. Without this catch, we'd
                        # propagate up and the LLM would see a generic error.
                        # Codex feedback #1.
                        logger.warning(
                            "Calendar API failed during get_free_slots; treating as unavailable",
                            call_id=call_id,
                            calendar_key=k,
                            calendar_id=api_err.calendar_id,
                            availability_mode=availability_mode,
                            error=str(api_err),
                        )
                        failed_keys.append(k)
                        continue
                    per_cal_intervals.append(intervals)
                    per_cal_free_counts[k] = fb_count
                    per_cal_busy_counts[k] = bb_count
                    total_free_blocks += fb_count
                    total_busy_blocks += bb_count

                # Log skipped calendars so operators can diagnose
                if failed_keys:
                    logger.warning("Skipped unavailable calendars during aggregation", call_id=call_id, failed_keys=failed_keys)

                # Aggregate
                if not per_cal_intervals:
                    out = {"status": "error", "message": f"All selected calendars are unavailable: {', '.join(failed_keys)}."}
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out

                # Intersection ("all" mode) requires every selected calendar to be
                # reachable. If any were skipped, silently intersecting across the
                # reachable subset would widen the result ("free on every reachable
                # calendar" ≠ "free on every selected calendar"), potentially
                # surfacing slots that are actually busy on the unavailable one.
                # Fail closed in that case. "any" mode (union) can still proceed
                # since a union over a subset is still a valid subset of the full
                # union — the LLM just gets fewer candidates.
                if failed_keys and aggregate_mode != "any" and len(keys_to_use) > 1:
                    out = {
                        "status": "error",
                        "message": (
                            f"Cannot compute shared availability (aggregate_mode='all') while "
                            f"these calendars are unavailable: {', '.join(failed_keys)}."
                        ),
                    }
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

                # Duration: from parameter "duration" (minutes), fallback to config,
                # then to backend default of 30. Backend default matches the UI's
                # placeholder so a freshly-configured UI calendar with blank duration
                # uses 30-minute slots, not 15. (Codex feedback #2.) Was 15 in the
                # original implementation; bumped here for consistency. YAML configs
                # that explicitly set min_slot_duration_minutes keep their value.
                duration_minutes = parameters.get("duration") or config.get("min_slot_duration_minutes", 30)
                try:
                    duration_minutes = max(1, int(duration_minutes))
                except (TypeError, ValueError):
                    duration_minutes = 30

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
                    # Always align to duration multiples from midnight
                    start = round_up_to_next_slot(s, duration_minutes)
                    while start + duration_td <= end_t:
                        slot_starts.append(start)
                        start += timedelta(minutes=duration_minutes)

                # Pick the output timezone for the slot list. Priority:
                # 1. Root tool config `timezone` (operator-pinned override)
                # 2. The TARGET calendar's timezone (single-cal mode)
                # 3. UTC fallback
                # CodeRabbit critical finding: in multi-calendar configs where
                # selected calendars have DIFFERENT timezones, formatting slots
                # in `keys_to_use[0]`'s TZ misleads the LLM — it picks a slot,
                # then create_event lands on a different calendar with a
                # different TZ, and the same naive timestamp resolves to a
                # different wall-clock time. We avoid that footgun by emitting
                # slots in UTC when calendars disagree on TZ. Single-cal and
                # all-same-TZ multi-cal cases (the 99% case) keep their
                # original behavior.
                tz_set: set[str] = set()
                for k in keys_to_use:
                    if k in failed_keys:
                        continue
                    tz_name = _tz_for_key(k) if not legacy_single else calendar_tz_name
                    if tz_name:
                        tz_set.add(tz_name)
                # Selection rules — corrected per CodeRabbit critical follow-up:
                # the previous version had `config["timezone"]` (root override)
                # winning over per-calendar timezones, which is a footgun in
                # multi-calendar mode. create_event ALWAYS uses the target
                # calendar's TZ to reparse naive datetime strings, so if the
                # slot list was formatted in a different TZ the model will
                # book at the wrong wall-clock time.
                #
                # Legacy single-cal mode: root config["timezone"] IS the
                #   calendar's TZ (no per-calendar overrides exist) — use it.
                # Multi-calendar mode: per-calendar TZ is authoritative.
                #   Root config["timezone"] is ignored to avoid the create_event
                #   reparse mismatch. If the selected calendars agree on a TZ,
                #   use it. If they disagree, fall back to UTC + warning.
                if legacy_single:
                    output_tz_name = (
                        (config.get("timezone") or "").strip()
                        or calendar_tz_name
                        or "UTC"
                    )
                elif len(tz_set) == 1:
                    output_tz_name = next(iter(tz_set))
                elif len(tz_set) > 1:
                    # Multi-cal disagreement — fall back to UTC + warn so the
                    # LLM doesn't book in the wrong wall-clock zone.
                    output_tz_name = "UTC"
                    logger.warning(
                        "Selected calendars have differing timezones; emitting slots in UTC",
                        call_id=call_id,
                        timezones=sorted(tz_set),
                    )
                else:
                    output_tz_name = "UTC"
                try:
                    output_tz = ZoneInfo(output_tz_name)
                except (KeyError, TypeError):
                    output_tz = ZoneInfo("UTC")
                    output_tz_name = "UTC"
                # Surface to caller whether we had to fall back due to multi-cal
                # TZ disagreement, so consumers (and tests) can distinguish.
                # Multi-cal mode + selected calendars don't agree on a TZ.
                # In this case we fall back to UTC display and warn the model
                # to pass UTC datetimes (with 'Z') to create_event. Doesn't
                # apply in legacy_single mode (only one calendar exists).
                multi_cal_tz_disagreement = len(tz_set) > 1 and not legacy_single
                slot_starts = [t.astimezone(output_tz) for t in slot_starts]
                slot_starts.sort()

                # Cap how many slots are surfaced to the LLM. Without a cap, get_free_slots
                # over a multi-day range can return 20-50+ slots; the LLM then dutifully
                # reads the entire list to the caller (≈16 syllables per ISO timestamp →
                # multiple minutes of monologue). Operators can tune this in the UI under
                # Tools → google_calendar → Slot-finding defaults. Default: 3. Set to 0
                # (or any value ≤0) to disable the cap and return everything.
                _max_slots_raw = config.get("max_slots_returned", 3)
                try:
                    max_slots = int(_max_slots_raw) if _max_slots_raw is not None else 3
                except (TypeError, ValueError):
                    max_slots = 3
                total_slot_starts = len(slot_starts)
                if max_slots and max_slots > 0 and total_slot_starts > max_slots:
                    slot_starts = slot_starts[:max_slots]
                # Build slot pairs (start → end) so the LLM can see exact end times.
                # Without explicit end times in the message, models infer from context
                # and sometimes fill the entire post-start availability window — a real
                # bug seen on ElevenLabs/Claude where the agent picked 11 AM correctly
                # but booked end_datetime at 18:00 (end of working hours) instead of
                # 11:30. Showing "09:00–09:30" makes the duration unambiguous.
                slot_pairs = [(t, t + timedelta(minutes=duration_minutes)) for t in slot_starts]
                results = [
                    f"{s.strftime('%Y-%m-%d %H:%M')}–{e.strftime('%H:%M')}"
                    for (s, e) in slot_pairs
                ]
                slots_iso = [s.isoformat() for s in slot_starts]
                slots_iso_pairs = [
                    {"start": s.isoformat(), "end": e.isoformat()} for (s, e) in slot_pairs
                ]
                slots_truncated = total_slot_starts > len(slot_starts)

                # Distinguish empty-result failure modes so the LLM can react appropriately.
                # For multi-cal aggregations (Codex feedback #6), aggregate counts alone
                # mislead — e.g. in 'all' (intersection) mode, if calendar A has Open
                # blocks and B has none, the intersection is empty but it's because B
                # has no Open windows, not because everything is booked. Track per-cal
                # counts and check accordingly.
                open_windows_found = total_free_blocks > 0
                busy_blocks_found = total_busy_blocks > 0

                # Per-calendar empty diagnosis (only meaningful for multi-cal)
                cals_with_no_open: list[str] = [
                    k for k in keys_to_use
                    if k not in failed_keys and per_cal_free_counts.get(k, 0) == 0
                ]

                if results:
                    reason = "available"
                    # Message uses explicit START–END pairs AND timezone name so
                    # the LLM books the right duration in the right zone. Legacy
                    # "Free slot starts:" prefix is preserved as the literal
                    # opening token for back-compat with user prompt templates
                    # that pattern-match on it (this string predates the PR and
                    # at least one customer was known to grep for it). When the
                    # list is capped (see max_slots_returned above), nudge the
                    # LLM to summarize rather than read all slots verbatim —
                    # pre-PR behavior was for the model to read every ISO
                    # timestamp aloud, producing minutes of robotic monologue.
                    starts_only = [s.split("–")[0] for s in results]
                    if multi_cal_tz_disagreement:
                        # When the selected calendars don't agree on a single TZ
                        # we surface slots in UTC. The model needs to know to
                        # pass UTC-suffixed datetimes (`...Z`) to create_event
                        # — NOT naive local-time strings, since each calendar's
                        # interpretation of "local" would differ.
                        booking_hint = (
                            "When booking via create_event, pass start_datetime "
                            "and end_datetime in UTC with the 'Z' suffix (e.g. "
                            "'2026-04-27T17:00:00Z') and set the appropriate "
                            "calendar_key so the booking lands on the calendar "
                            "you intended. The selected calendars use different "
                            "timezones, which is why slots are returned in UTC."
                        )
                    else:
                        booking_hint = (
                            "When booking via create_event, pass start_datetime "
                            "and end_datetime as the SAME local-time strings "
                            "(no Z, no offset — calendar timezone is implicit)"
                        )
                    message = (
                        "Free slot starts: " + ", ".join(starts_only) + ". "
                        f"Each slot is {duration_minutes} minutes long in "
                        f"{output_tz_name}: {', '.join(results)}. " + booking_hint +
                        f" and set end_datetime = start_datetime + {duration_minutes} minutes."
                    )
                    if slots_truncated:
                        message += (
                            f" (showing {len(results)} of {total_slot_starts} available; "
                            f"propose 2-3 of these to the caller — do not read the full list)."
                        )
                elif aggregate_mode == "all" and len(keys_to_use) > 1 and cals_with_no_open:
                    # Intersection mode + at least one selected calendar has no Open
                    # blocks → intersection is empty BECAUSE that calendar lacks
                    # availability, not because everything is booked. Surface this
                    # specifically so the LLM can name which calendar.
                    reason = "no_open_windows"
                    if availability_mode == "freebusy":
                        message = (
                            "No working-hours overlap across the selected calendars for this range. "
                            "Try a different date or expand the time range."
                        )
                    elif len(cals_with_no_open) == len(keys_to_use):
                        message = (
                            f"I don't see any '{free_prefix}' availability blocks on any of the "
                            f"selected calendars for this range. Open windows are defined by "
                            f"events titled with '{free_prefix}'."
                        )
                    else:
                        message = (
                            f"I can't find a shared open window because calendar(s) "
                            f"{', '.join(cals_with_no_open)} have no '{free_prefix}' availability "
                            f"blocks for this range. Open windows are defined by events titled "
                            f"with '{free_prefix}'."
                        )
                elif open_windows_found:
                    # Some Open windows existed across the (intersected/unioned) set
                    # but they're entirely blocked by Busy events.
                    reason = "fully_booked"
                    message = (
                        "I checked the calendar — your open availability windows for this range "
                        "are fully booked. Want to try a different day?"
                    )
                else:
                    # No Open blocks anywhere across the set (or, in freebusy mode,
                    # the requested time range falls entirely outside working hours).
                    reason = "no_open_windows"
                    if availability_mode == "freebusy":
                        message = (
                            f"No working-hours availability in the requested time range. "
                            f"Working hours are configured as {work_start_hour:02d}:00–{work_end_hour:02d}:00 "
                            f"on weekdays. Try a different date or expand the time range."
                        )
                    else:
                        message = (
                            f"I don't see any '{free_prefix}' availability blocks on the calendar for this range. "
                            f"Open windows are defined by events titled with '{free_prefix}'."
                        )

                out = {
                    "status": "success",
                    "message": message,
                    "slots": slots_iso,
                    # Explicit (start, end) pairs so models that read structured
                    # responses (rather than the message string) book the right
                    # duration. Companion to the message's "set end_datetime =
                    # start_datetime + N minutes" hint.
                    "slots_with_end": slots_iso_pairs if results else [],
                    "slot_duration_minutes": duration_minutes,
                    # Calendar timezone — surfaced so the model knows what TZ to use
                    # for create_event without guessing. The lenient parser in
                    # _normalize_datetime_to_calendar_tz strips any TZ tail and
                    # treats wall-clock as local; this field tells the model what
                    # "local" means.
                    "calendar_timezone": output_tz_name,
                    # True when the selected calendars don't agree on a single
                    # timezone and we fell back to UTC for the slot list.
                    # Consumers should know to pass UTC datetimes (with 'Z') to
                    # create_event in this case, not naive local-time strings.
                    "tz_disagreement": multi_cal_tz_disagreement,
                    "open_windows_found": open_windows_found,
                    "busy_blocks_found": busy_blocks_found,
                    "reason": reason,
                    "availability_mode": availability_mode,
                    "total_slots_available": total_slot_starts,
                    "slots_truncated": slots_truncated,
                    # Per-calendar diagnostics — useful for downstream tooling that
                    # wants to identify which calendar lacks availability without
                    # parsing the message string.
                    "calendars_without_open_windows": cals_with_no_open,
                }
                logger.info(
                    "Tool response to AI",
                    call_id=call_id, action=action, status=out.get("status"),
                    reason=reason, slot_count=len(slots_iso),
                    total_slots_available=total_slot_starts, slots_truncated=slots_truncated,
                    availability_mode=availability_mode,
                )
                return out

            if action == "list_events":
                time_min = parameters.get("time_min")
                time_max = parameters.get("time_max")
                calendar_key = parameters.get("calendar_key")
                if not time_min or not time_max:
                    error_msg = "Error: 'time_min' and 'time_max' parameters are required for list_events."
                    logger.warning("Missing time range for list_events", call_id=call_id)
                    out = {"status": "error", "message": error_msg}
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out

                # Validate time_min/time_max once before per-calendar loop
                _val_tz = self._get_calendar_tz_name(config) if legacy_single else _tz_for_key(selected_keys[0] if selected_keys else "default")
                try:
                    self._normalize_datetime_to_calendar_tz(time_min, _val_tz)
                    self._normalize_datetime_to_calendar_tz(time_max, _val_tz)
                except ValueError as e:
                    out = {"status": "error", "message": str(e)}
                    logger.warning("Invalid datetime for list_events", call_id=call_id, error=str(e))
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out

                def _list_for_key(k: str) -> list[dict]:
                    tz_name = _tz_for_key(k)
                    tmin = self._normalize_datetime_to_calendar_tz(time_min, tz_name).isoformat()
                    tmax = self._normalize_datetime_to_calendar_tz(time_max, tz_name).isoformat()
                    ci = _cal_for_key(k)
                    if not getattr(ci, "service", None):
                        return []
                    return ci.list_events(tmin, tmax)

                if legacy_single:
                    try:
                        events = await asyncio.to_thread(cal.list_events, *[
                            self._normalize_datetime_to_calendar_tz(time_min, self._get_calendar_tz_name(config)).isoformat(),
                            self._normalize_datetime_to_calendar_tz(time_max, self._get_calendar_tz_name(config)).isoformat(),
                        ])
                    except GoogleCalendarApiError as api_err:
                        out = {
                            "status": "error",
                            "message": f"Could not list events: {api_err}",
                        }
                        logger.warning("list_events: API error (legacy single)", call_id=call_id, error=str(api_err))
                        logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                        return out
                    pools = {"default": events}
                elif calendar_key:
                    err = _validate_calendar_key(calendar_key, "list_events")
                    if err:
                        return err
                    # Refuse to silently return empty when the targeted calendar
                    # is unavailable — the caller asked for a specific calendar.
                    ci_one = _cal_for_key(calendar_key)
                    if not getattr(ci_one, "service", None):
                        out = {
                            "status": "error",
                            "message": f"Calendar '{calendar_key}' is configured but currently unavailable.",
                        }
                        logger.warning("list_events: targeted calendar unavailable", call_id=call_id, calendar_key=calendar_key)
                        logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                        return out
                    try:
                        pools = {calendar_key: await asyncio.to_thread(_list_for_key, calendar_key)}
                    except GoogleCalendarApiError as api_err:
                        out = {
                            "status": "error",
                            "message": f"Could not list events on calendar '{calendar_key}': {api_err}",
                        }
                        logger.warning("list_events: API error (targeted)", call_id=call_id, calendar_key=calendar_key, error=str(api_err))
                        logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                        return out
                else:
                    # Aggregate across selected calendars. If any are unavailable
                    # OR fail at runtime (revoked share, expired DWD, network
                    # error), return an error — silently merging the reachable
                    # subset would hide missing data and make the merged list
                    # look complete when it isn't. Codex feedback #1.
                    pools = {}
                    list_failed_keys: list[str] = []
                    for k in selected_keys:
                        ci_k = _cal_for_key(k)
                        if not getattr(ci_k, "service", None):
                            list_failed_keys.append(k)
                            continue
                        try:
                            pools[k] = await asyncio.to_thread(_list_for_key, k)
                        except GoogleCalendarApiError as api_err:
                            logger.warning(
                                "list_events: API error during multi-calendar aggregation",
                                call_id=call_id,
                                calendar_key=k,
                                error=str(api_err),
                            )
                            list_failed_keys.append(k)
                            continue
                    if list_failed_keys:
                        out = {
                            "status": "error",
                            "message": (
                                f"Cannot list events because these selected calendars are "
                                f"currently unavailable: {', '.join(list_failed_keys)}."
                            ),
                        }
                        logger.warning("list_events: selected calendars unavailable", call_id=call_id, failed_keys=list_failed_keys)
                        logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                        return out

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
                    if calendar_key:
                        err = _validate_calendar_key(calendar_key, "get_event")
                        if err:
                            return err
                        target_key = calendar_key
                    else:
                        target_key = (selected_keys[0] if selected_keys else None)
                    if not target_key:
                        out = {"status": "error", "message": "No calendar_key provided and no selected calendars configured."}
                        logger.warning("No target calendar for get_event", call_id=call_id)
                        logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                        return out
                    cal_i = _cal_for_key(target_key)
                # get_event raises GoogleCalendarApiError on auth/forbidden/
                # 5xx and returns None on 404. Treat the typed error the
                # same way as create_event/delete_event so the SCHEDULING-
                # block recovery substrings can match real auth failures
                # rather than collapsing them into "event not found".
                try:
                    event = await asyncio.to_thread(cal_i.get_event, event_id)
                except GoogleCalendarApiError as api_err:
                    raw_msg = str(api_err)
                    status = getattr(getattr(api_err.original, "resp", None), "status", None)
                    error_code = "get_event_failed"
                    if status == 403 or "forbidden" in raw_msg.lower():
                        error_code = "forbidden_calendar"
                    elif status == 401 or "unauthorized" in raw_msg.lower():
                        error_code = "auth_failed"
                    elif status and 500 <= status < 600:
                        error_code = "google_api_unavailable"
                    out = {
                        "status": "error",
                        "error_code": error_code,
                        "message": f"Failed to retrieve event: {raw_msg}. (http_status={status})",
                    }
                    logger.error(
                        "get_event raised GoogleCalendarApiError",
                        call_id=call_id, event_id=event_id,
                        error_code=error_code, status=status,
                    )
                    logger.info(
                        "Tool response to AI", call_id=call_id, action=action,
                        status=out.get("status"), error_code=error_code,
                    )
                    return out
                if not event:
                    out = {"status": "error", "error_code": "event_not_found", "message": "Event not found."}
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
                    if calendar_key:
                        err = _validate_calendar_key(calendar_key, "create_event")
                        if err:
                            return err
                        target_key = calendar_key
                    else:
                        target_key = (selected_keys[0] if selected_keys else None)
                    if not target_key:
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

                # Guard against the "model fills the entire availability window"
                # bug. Real example from voiprnd test calls: ElevenLabs/Claude got
                # picked the right start (11:00) but passed end_datetime=18:00 —
                # booked a 7-hour meeting instead of 30 minutes. Without an
                # explicit cap, every provider is one prompt-misinterpretation
                # away from the same failure. Threshold is generous (4h) so
                # legitimate long meetings still pass; smaller schema cap is
                # configurable via tools.google_calendar.max_event_duration_minutes.
                try:
                    _max_dur_raw = config.get("max_event_duration_minutes", 240)
                    max_dur = int(_max_dur_raw) if _max_dur_raw is not None else 240
                except (TypeError, ValueError):
                    max_dur = 240
                actual_minutes = (end_dt_local - start_dt_local).total_seconds() / 60.0
                if actual_minutes <= 0:
                    out = {
                        "status": "error",
                        "error_code": "invalid_duration",
                        "message": (
                            f"Invalid event duration: end_datetime ({end_dt}) must be after "
                            f"start_datetime ({start_dt}). Retry with end_datetime equal to "
                            f"start_datetime plus the meeting length (typically 30 minutes)."
                        ),
                    }
                    logger.warning("Non-positive duration for create_event", call_id=call_id, duration_min=actual_minutes)
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out
                if max_dur > 0 and actual_minutes > max_dur:
                    out = {
                        "status": "error",
                        "error_code": "duration_too_long",
                        "message": (
                            f"Event duration {int(actual_minutes)} minutes exceeds the "
                            f"allowed maximum of {max_dur} minutes. The most common cause "
                            f"is end_datetime being set to the end of the available window "
                            f"instead of start_datetime + slot duration. Retry with "
                            f"end_datetime equal to start_datetime plus the meeting length "
                            f"(typically 30 minutes)."
                        ),
                    }
                    logger.warning(
                        "Event duration exceeds cap; refusing create_event",
                        call_id=call_id,
                        duration_min=actual_minutes,
                        max_dur=max_dur,
                    )
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out

                # Surface Google API errors with their original detail so the
                # SCHEDULING-block recovery substrings ('forbidden', '403',
                # 'not configured', 'unavailable') can match the real failure
                # cause. Previously every API failure collapsed to a generic
                # "Failed to create event." which never matched any recovery
                # substring — a permissions failure on booking would hit that
                # generic path and either bail silently or send the caller
                # the wrong fallback message. CodeRabbit major finding.
                try:
                    event = await asyncio.to_thread(
                        cal_i.create_event, summary, desc, start_dt_str, end_dt_str
                    )
                except GoogleCalendarApiError as api_err:
                    raw_msg = str(api_err)
                    status = getattr(getattr(api_err.original, "resp", None), "status", None)
                    error_code = "create_event_failed"
                    if status == 403 or "forbidden" in raw_msg.lower():
                        error_code = "forbidden_calendar"
                    elif status == 401 or "unauthorized" in raw_msg.lower():
                        error_code = "auth_failed"
                    elif status == 404 or "not found" in raw_msg.lower():
                        error_code = "calendar_not_found"
                    elif status and 500 <= status < 600:
                        error_code = "google_api_unavailable"
                    out = {
                        "status": "error",
                        "error_code": error_code,
                        "message": (
                            f"Failed to create event: {raw_msg}. "
                            f"(http_status={status})"
                        ),
                    }
                    logger.error(
                        "create_event raised GoogleCalendarApiError",
                        call_id=call_id, error_code=error_code, status=status,
                    )
                    logger.info(
                        "Tool response to AI", call_id=call_id, action=action,
                        status=out.get("status"), error_code=error_code,
                    )
                    return out
                if not event:
                    out = {
                        "status": "error",
                        "error_code": "create_event_failed",
                        "message": "Failed to create event (service returned no event object).",
                    }
                    logger.error("Failed to create event (None returned)", call_id=call_id)
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out
                created_id = event.get("id")
                # Track the last successful create for this call so delete_event
                # can fall back when the model hallucinates the event_id (real
                # bug seen on Gemini in voiprnd round-4 test). LRU eviction
                # bounds the cache at _LAST_EVENT_CACHE_CAP — see __init__.
                if call_id and created_id:
                    with self._last_event_lock:
                        # move_to_end so updating an existing call_id refreshes
                        # its LRU position rather than leaving it stale at the
                        # cold end. Then evict the oldest if over cap.
                        if call_id in self._last_event_per_call:
                            self._last_event_per_call.move_to_end(call_id)
                        self._last_event_per_call[call_id] = {
                            "event_id": created_id,
                            "calendar_key": target_key,
                        }
                        while len(self._last_event_per_call) > self._LAST_EVENT_CACHE_CAP:
                            evicted_call_id, _evicted_entry = self._last_event_per_call.popitem(last=False)
                            logger.debug(
                                "Evicted oldest entry from _last_event_per_call (LRU cap reached)",
                                evicted_call_id=evicted_call_id,
                                cap=self._LAST_EVENT_CACHE_CAP,
                            )
                # Round-5 fix carried the deletion guidance in the `message`
                # field, but live testing showed providers (especially OpenAI
                # Realtime and ElevenLabs) read the message verbatim to the
                # caller — including the raw event_id and the developer-facing
                # text "do not invent or guess one". That's a UX regression:
                # the caller hears robotic developer scaffolding.
                #
                # Fix: keep `message` short and human-friendly (the prior
                # "Event created." wording, which prompt templates still grep
                # for), and put the deletion-guidance + event_id in a separate
                # `agent_hint` field. Models that consume the structured
                # response (most do) get the hint; models that only read the
                # message field get a clean confirmation. The `event_id` /
                # `id` fields remain available for tool args.
                out = {
                    "status": "success",
                    "message": "Event created.",
                    "agent_hint": (
                        f"event_id='{created_id}'. If the caller later corrects "
                        f"the date or time, call delete_event with THIS EXACT "
                        f"event_id (do not invent or guess one) and then call "
                        f"create_event with the corrected time."
                    ),
                    "id": created_id,
                    "event_id": created_id,
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
                    if calendar_key:
                        err = _validate_calendar_key(calendar_key, "delete_event")
                        if err:
                            return err
                        target_key = calendar_key
                    else:
                        target_key = (selected_keys[0] if selected_keys else None)
                    if not target_key:
                        out = {"status": "error", "message": "calendar_key is required (or configure selected_calendars)."}
                        logger.warning("Missing target calendar for delete_event", call_id=call_id)
                        logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                        return out
                    cal_i = _cal_for_key(target_key)
                # delete_event raises GoogleCalendarApiError on non-404 errors
                # (auth, forbidden, server, etc.) and returns False on 404.
                # Surface non-404 errors with specific error_codes so the
                # SCHEDULING-block recovery substrings can match. The False/
                # 404 path falls through to the LLM-hallucinated-id fallback
                # below, which is the common case (the bug we already
                # documented in round-5 testing).
                try:
                    success = await asyncio.to_thread(cal_i.delete_event, event_id)
                except GoogleCalendarApiError as api_err:
                    raw_msg = str(api_err)
                    status = getattr(getattr(api_err.original, "resp", None), "status", None)
                    error_code = "delete_event_failed"
                    if status == 403 or "forbidden" in raw_msg.lower():
                        error_code = "forbidden_calendar"
                    elif status == 401 or "unauthorized" in raw_msg.lower():
                        error_code = "auth_failed"
                    elif status and 500 <= status < 600:
                        error_code = "google_api_unavailable"
                    out = {
                        "status": "error",
                        "error_code": error_code,
                        "message": f"Failed to delete event: {raw_msg}. (http_status={status})",
                    }
                    logger.error(
                        "delete_event raised GoogleCalendarApiError",
                        call_id=call_id, event_id=event_id,
                        error_code=error_code, status=status,
                    )
                    logger.info(
                        "Tool response to AI", call_id=call_id, action=action,
                        status=out.get("status"), error_code=error_code,
                    )
                    return out
                if not success:
                    # Fallback: when the model hallucinates an event_id (Gemini
                    # bug seen in voiprnd round-4 test), try the last
                    # successful create_event from this same call. The
                    # delete-then-recreate prompt rule depends on the model
                    # passing the right id, but models can confabulate ids
                    # that don't exist. If we have a real id from this call,
                    # use it — that's almost certainly what the model meant.
                    fallback_id = None
                    fallback_target = None
                    if call_id:
                        with self._last_event_lock:
                            tracked = self._last_event_per_call.get(call_id)
                        if tracked and tracked.get("event_id") and tracked["event_id"] != event_id:
                            fallback_id = tracked["event_id"]
                            fallback_target = tracked.get("calendar_key") or target_key
                    if fallback_id:
                        # Resolve cal_i for the tracked target if different
                        if not legacy_single and fallback_target and fallback_target != target_key:
                            err = _validate_calendar_key(fallback_target, "delete_event")
                            if err is None:
                                cal_i = _cal_for_key(fallback_target)
                                target_key = fallback_target
                        # Retry can also raise GoogleCalendarApiError (auth /
                        # forbidden / 5xx). Without this catch the exception
                        # would bubble out of the tool as a generic 500-style
                        # failure with no error_code, and the SCHEDULING-block
                        # recovery rules wouldn't fire.
                        try:
                            retry_success = await asyncio.to_thread(cal_i.delete_event, fallback_id)
                        except GoogleCalendarApiError as retry_err:
                            raw_msg = str(retry_err)
                            status = getattr(getattr(retry_err.original, "resp", None), "status", None)
                            error_code = "delete_event_failed"
                            if status == 403 or "forbidden" in raw_msg.lower():
                                error_code = "forbidden_calendar"
                            elif status == 401 or "unauthorized" in raw_msg.lower():
                                error_code = "auth_failed"
                            elif status and 500 <= status < 600:
                                error_code = "google_api_unavailable"
                            out = {
                                "status": "error",
                                "error_code": error_code,
                                "message": (
                                    f"Failed to delete event during fallback retry: "
                                    f"{raw_msg}. (http_status={status})"
                                ),
                            }
                            logger.error(
                                "Fallback delete_event raised GoogleCalendarApiError",
                                call_id=call_id, requested_id=event_id,
                                fallback_id=fallback_id,
                                error_code=error_code, status=status,
                            )
                            logger.info(
                                "Tool response to AI", call_id=call_id, action=action,
                                status=out.get("status"), error_code=error_code,
                            )
                            return out
                        if retry_success:
                            logger.warning(
                                "delete_event recovered from hallucinated id via call-tracked fallback",
                                call_id=call_id,
                                requested_id=event_id,
                                deleted_id=fallback_id,
                            )
                            with self._last_event_lock:
                                # Clear so we don't double-fallback on a
                                # subsequent delete in the same call.
                                self._last_event_per_call.pop(call_id, None)
                            out = {
                                "status": "success",
                                "message": (
                                    f"Event deleted (note: the event_id you supplied "
                                    f"('{event_id}') did not exist; the most-recent "
                                    f"booking from this call ('{fallback_id}') was "
                                    f"deleted instead. Always copy the event_id "
                                    f"verbatim from the create_event success message)."
                                ),
                                "id": fallback_id,
                                "event_id": fallback_id,
                                "calendar": target_key,
                            }
                            logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"), fallback=True)
                            return out
                    # No fallback available, or fallback also failed
                    out = {"status": "error", "message": "Failed to delete event (not found or calendar error)."}
                    logger.warning("Failed to delete event", call_id=call_id, event_id=event_id)
                    logger.info("Tool response to AI", call_id=call_id, action=action, status=out.get("status"))
                    return out
                # Successful delete — clear the tracking so a stale id can't
                # accidentally be matched in a future fallback.
                if call_id:
                    with self._last_event_lock:
                        tracked = self._last_event_per_call.get(call_id)
                        if tracked and tracked.get("event_id") == event_id:
                            self._last_event_per_call.pop(call_id, None)
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

