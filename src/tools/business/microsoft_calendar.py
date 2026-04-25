"""Microsoft Calendar tool using device-code OAuth and Microsoft Graph."""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict

import structlog

from src.tools.base import Tool, ToolCategory, ToolDefinition
from src.tools.business._calendar_utils import (
    build_slot_starts,
    coerce_hour,
    coerce_working_days,
    get_zoneinfo,
    intersect_intervals,
    normalize_to_tz,
    parse_iso_datetime,
    subtract_busy,
    to_utc,
    union_intervals,
    working_hours_mask,
)
from src.tools.business.ms_graph_client import (
    MicrosoftAccountConfig,
    MicrosoftGraphApiError,
    MicrosoftGraphClient,
)
from src.tools.context import ToolExecutionContext

logger = structlog.get_logger(__name__)


_MICROSOFT_CALENDAR_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list_events", "get_event", "create_event", "delete_event", "get_free_slots"],
            "description": "The calendar operation to perform.",
        },
        "account_key": {
            "type": "string",
            "description": "Optional named Microsoft Calendar account key. V1 usually uses 'default'.",
        },
        "aggregate_mode": {
            "type": "string",
            "enum": ["all", "any"],
            "description": "For multi-account get_free_slots: 'all' = intersection, 'any' = union.",
        },
        "time_min": {"type": "string", "description": "ISO 8601 start time."},
        "time_max": {"type": "string", "description": "ISO 8601 end time."},
        "free_prefix": {
            "type": "string",
            "description": (
                "Optional. Omit by default. When configured by the operator, events whose "
                "subjects start with this value define open windows. Blank uses Microsoft "
                "Graph native free/busy plus working hours."
            ),
        },
        "busy_prefix": {"type": "string", "description": "Optional title-prefix busy marker."},
        "duration": {"type": "integer", "description": "Appointment duration in minutes."},
        "event_id": {
            "type": "string",
            "description": (
                "Microsoft Graph event id. Required for get_event. For delete_event you can "
                "OMIT this if you want to delete the event you just created in this same "
                "call (e.g. the typical reschedule flow: create → user changes their mind → "
                "delete → create with new time). The tool will look up the most recently "
                "created event_id automatically. Only pass an event_id explicitly if you "
                "need to delete a specific older event."
            ),
        },
        "summary": {"type": "string", "description": "Event title for create_event."},
        "description": {"type": "string", "description": "Optional event description."},
        "start_datetime": {"type": "string", "description": "ISO 8601 start time for create_event."},
        "end_datetime": {"type": "string", "description": "ISO 8601 end time for create_event."},
    },
    "required": ["action"],
}


class MicrosoftCalendarTool(Tool):
    _LAST_EVENT_CACHE_CAP = 1024

    def __init__(self):
        super().__init__()
        self._clients: dict[tuple[str, str, str, str, str], MicrosoftGraphClient] = {}
        self._clients_lock = threading.Lock()
        self._last_event_per_call: "OrderedDict[str, dict]" = OrderedDict()
        self._last_event_lock = threading.Lock()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="microsoft_calendar",
            description=(
                "Interact with a Microsoft 365 Outlook calendar. Use this to list events, "
                "get a specific event, create events, delete events, or find free slots."
            ),
            category=ToolCategory.BUSINESS,
            requires_channel=False,
            max_execution_time=30,
            input_schema=_MICROSOFT_CALENDAR_INPUT_SCHEMA,
        )

    def _get_config(self, context: ToolExecutionContext) -> Dict[str, Any]:
        base: Dict[str, Any] = {}
        overlay: Dict[str, Any] = {}
        if context and getattr(context, "get_config_value", None):
            base = context.get_config_value("tools.microsoft_calendar", {}) or {}
            ctx_name = getattr(context, "context_name", None)
            if ctx_name:
                try:
                    overlay = context.get_config_value(
                        f"contexts.{ctx_name}.tool_overrides.microsoft_calendar", {}
                    ) or {}
                except (KeyError, TypeError, AttributeError):
                    overlay = {}
        merged = dict(base or {})
        for key, value in (overlay or {}).items():
            merged[key] = value
        return merged or self._load_config()

    def _resolve_accounts(self, config: Dict[str, Any]) -> dict[str, dict[str, str]]:
        accounts: dict[str, dict[str, str]] = {}
        account_map = config.get("accounts") or {}
        if isinstance(account_map, dict) and account_map:
            for key, value in account_map.items():
                if not isinstance(value, dict):
                    continue
                accounts[str(key)] = {
                    "tenant_id": value.get("tenant_id", "") or config.get("tenant_id", ""),
                    "client_id": value.get("client_id", "") or config.get("client_id", ""),
                    "token_cache_path": value.get("token_cache_path", "") or config.get("token_cache_path", ""),
                    "user_principal_name": value.get("user_principal_name", "") or config.get("user_principal_name", ""),
                    "calendar_id": value.get("calendar_id", "") or config.get("calendar_id", ""),
                    "timezone": value.get("timezone", "") or config.get("timezone", "") or "UTC",
                }
        else:
            accounts["default"] = {
                "tenant_id": config.get("tenant_id", ""),
                "client_id": config.get("client_id", ""),
                "token_cache_path": config.get("token_cache_path", ""),
                "user_principal_name": config.get("user_principal_name", ""),
                "calendar_id": config.get("calendar_id", ""),
                "timezone": config.get("timezone", "") or "UTC",
            }
        return accounts

    def _selected_account_keys(self, config: Dict[str, Any]) -> list[str]:
        accounts = self._resolve_accounts(config)
        raw = config.get("selected_accounts")
        if raw is None:
            return list(accounts.keys())
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(key) for key in raw if str(key) in accounts]

    def _account_config(self, cfg: dict[str, str]) -> MicrosoftAccountConfig:
        return MicrosoftAccountConfig(
            tenant_id=(cfg.get("tenant_id") or "").strip(),
            client_id=(cfg.get("client_id") or "").strip(),
            token_cache_path=(cfg.get("token_cache_path") or "").strip(),
            user_principal_name=(cfg.get("user_principal_name") or "").strip(),
            calendar_id=(cfg.get("calendar_id") or "").strip(),
            timezone=(cfg.get("timezone") or "UTC").strip() or "UTC",
        )

    def _client_for_config(self, account: MicrosoftAccountConfig) -> MicrosoftGraphClient:
        key = (
            account.tenant_id,
            account.client_id,
            account.token_cache_path,
            account.user_principal_name,
            account.calendar_id,
        )
        with self._clients_lock:
            client = self._clients.get(key)
            if client is None:
                client = MicrosoftGraphClient(account)
                self._clients[key] = client
            return client

    def _validate_account(self, account: MicrosoftAccountConfig) -> str | None:
        missing = []
        for attr in ("tenant_id", "client_id", "token_cache_path", "user_principal_name", "calendar_id"):
            if not getattr(account, attr):
                missing.append(attr)
        if missing:
            return f"Microsoft Calendar account is missing: {', '.join(missing)}."
        return None

    def _map_api_error(self, exc: MicrosoftGraphApiError, prefix: str) -> dict[str, Any]:
        message = str(exc)
        if exc.error_code == "auth_expired":
            message = (
                "Microsoft Calendar is not configured for runtime use because credentials "
                "expired or no credentials are available. Ask an operator to reconnect "
                "the account in Tools before booking."
            )
        elif exc.error_code == "forbidden_calendar":
            message = f"Microsoft Calendar access is forbidden (403): {message}"
        elif exc.error_code == "calendar_not_found":
            message = f"Microsoft Calendar is not configured correctly: calendar not found. {message}"
        elif exc.error_code in {"graph_unavailable", "rate_limited"}:
            message = f"Microsoft Graph is currently unavailable: {message}"
        elif exc.error_code in {"msal_not_installed", "portalocker_not_installed", "missing_token_cache_path", "token_cache_unreadable"}:
            message = f"Microsoft Calendar is not configured correctly: {message}"
        return {
            "status": "error",
            "error_code": exc.error_code,
            "message": f"{prefix}: {message}",
            "http_status": exc.status,
        }

    def _parse_event_dt(self, value: dict[str, Any], fallback_tz: str) -> datetime | None:
        dt_raw = (value or {}).get("dateTime")
        if not dt_raw:
            return None
        # Graph requests use Prefer UTC, but old events can carry timezone names.
        tz_name = (value or {}).get("timeZone") or fallback_tz or "UTC"
        if dt_raw.endswith("Z") or "+" in dt_raw[10:]:
            return parse_iso_datetime(dt_raw).astimezone(get_zoneinfo(fallback_tz))
        return parse_iso_datetime(dt_raw).replace(tzinfo=get_zoneinfo(tz_name)).astimezone(get_zoneinfo(fallback_tz))

    async def execute(self, parameters: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
        call_id = getattr(context, "call_id", None) or ""
        action = parameters.get("action")
        logger.info("MicrosoftCalendarTool execution triggered", call_id=call_id, action=action)

        config = self._get_config(context)
        if config.get("enabled") is False:
            return {"status": "error", "message": "Microsoft Calendar is disabled."}
        if not action:
            return {"status": "error", "message": "Error: 'action' parameter is missing."}

        accounts = self._resolve_accounts(config)
        selected_keys = self._selected_account_keys(config)
        if not selected_keys:
            return {"status": "error", "message": "No Microsoft Calendar accounts are selected or configured."}

        account_key = parameters.get("account_key")

        def _target_key(action_name: str) -> tuple[str | None, dict[str, Any] | None]:
            if account_key:
                key = str(account_key)
                if key not in accounts:
                    return None, {
                        "status": "error",
                        "message": f"Unknown account_key '{key}'. Available: {', '.join(accounts.keys())}.",
                    }
                if key not in selected_keys:
                    return None, {
                        "status": "error",
                        "message": f"Account '{key}' is not selected for this context.",
                    }
                return key, None
            if len(selected_keys) > 1 and action_name in {"create_event", "delete_event", "get_event"}:
                return None, {
                    "status": "error",
                    "message": "account_key is required when multiple Microsoft Calendar accounts are selected.",
                }
            return selected_keys[0], None

        try:
            if action == "get_free_slots":
                return await self._handle_get_free_slots(parameters, config, accounts, selected_keys, call_id)
            if action == "list_events":
                key, err = _target_key("list_events")
                if err:
                    return err
                return await self._handle_list_events(parameters, accounts[key], key)
            if action == "get_event":
                key, err = _target_key("get_event")
                if err:
                    return err
                return await self._handle_get_event(parameters, accounts[key], key)
            if action == "create_event":
                key, err = _target_key("create_event")
                if err:
                    return err
                return await self._handle_create_event(parameters, config, accounts[key], key, call_id)
            if action == "delete_event":
                key, err = _target_key("delete_event")
                if err:
                    return err
                return await self._handle_delete_event(parameters, accounts[key], key, call_id)
            return {"status": "error", "message": f"Error: Unknown action '{action}'."}
        except Exception as exc:
            logger.error("MicrosoftCalendarTool failed", call_id=call_id, action=action, error=str(exc), exc_info=True)
            return {"status": "error", "message": "An unexpected Microsoft Calendar error occurred."}

    async def _handle_get_free_slots(
        self,
        parameters: dict[str, Any],
        config: dict[str, Any],
        accounts: dict[str, dict[str, str]],
        selected_keys: list[str],
        call_id: str,
    ) -> dict[str, Any]:
        time_min = parameters.get("time_min")
        time_max = parameters.get("time_max")
        if not time_min or not time_max:
            return {
                "status": "error",
                "error_code": "missing_parameters",
                "message": "Missing required parameters: 'time_min' and 'time_max' are required for get_free_slots.",
            }
        aggregate_mode = (parameters.get("aggregate_mode") or "all").lower()
        if aggregate_mode not in {"all", "any"}:
            aggregate_mode = "all"
        account_key = parameters.get("account_key")
        keys_to_use = [str(account_key)] if account_key else list(selected_keys)
        for key in keys_to_use:
            if key not in accounts or key not in selected_keys:
                return {"status": "error", "message": f"Microsoft Calendar account '{key}' is not available for this context."}

        config_free = (config.get("free_prefix") or "").strip()
        free_prefix = (parameters.get("free_prefix") or config_free).strip() if config_free else ""
        busy_prefix = (parameters.get("busy_prefix") or config.get("busy_prefix") or "Busy").strip() or "Busy"
        availability_mode = "title_prefix" if free_prefix else "freebusy"

        duration_raw = parameters.get("duration") or config.get("min_slot_duration_minutes", 30)
        try:
            duration_minutes = max(1, int(duration_raw))
        except (TypeError, ValueError):
            duration_minutes = 30
        work_start = coerce_hour(config.get("working_hours_start"), 9)
        work_end = coerce_hour(config.get("working_hours_end"), 17)
        if work_end <= work_start:
            work_start, work_end = 9, 17
        work_days = coerce_working_days(config.get("working_days"))

        per_account_intervals: list[list[tuple[datetime, datetime]]] = []
        failed_keys: list[str] = []
        total_free_blocks = 0
        total_busy_blocks = 0
        per_account_free_counts: dict[str, int] = {}

        for key in keys_to_use:
            account = self._account_config(accounts[key])
            validation_error = self._validate_account(account)
            if validation_error:
                failed_keys.append(key)
                logger.warning("Invalid Microsoft Calendar account config", call_id=call_id, account_key=key, error=validation_error)
                continue
            client = self._client_for_config(account)
            tz_name = account.timezone or "UTC"
            range_start = normalize_to_tz(time_min, tz_name, respect_offset=True)
            range_end = normalize_to_tz(time_max, tz_name, respect_offset=True)
            try:
                if availability_mode == "freebusy":
                    busy_blocks = await asyncio.to_thread(
                        self._get_schedule_busy_blocks_chunked,
                        client,
                        range_start,
                        range_end,
                        tz_name,
                    )
                    free_blocks = working_hours_mask(range_start, range_end, tz_name, work_start, work_end, work_days)
                    intervals = subtract_busy(free_blocks, busy_blocks)
                    free_count = len(free_blocks)
                    busy_count = len(busy_blocks)
                else:
                    events = await asyncio.to_thread(
                        client.list_calendar_view,
                        to_utc(range_start),
                        to_utc(range_end),
                    )
                    intervals, free_count, busy_count = self._intervals_from_prefix_events(
                        events,
                        free_prefix,
                        busy_prefix,
                        tz_name,
                    )
            except MicrosoftGraphApiError as exc:
                failed_keys.append(key)
                logger.warning(
                    "Microsoft Calendar API failed during get_free_slots",
                    call_id=call_id,
                    account_key=key,
                    error_code=exc.error_code,
                    error=str(exc),
                )
                continue
            per_account_intervals.append(intervals)
            per_account_free_counts[key] = free_count
            total_free_blocks += free_count
            total_busy_blocks += busy_count

        if not per_account_intervals:
            return {"status": "error", "message": f"All selected Microsoft Calendar accounts are unavailable: {', '.join(failed_keys)}."}
        if failed_keys and aggregate_mode != "any" and len(keys_to_use) > 1:
            return {
                "status": "error",
                "message": (
                    "Cannot compute shared Microsoft Calendar availability while these "
                    f"accounts are unavailable: {', '.join(failed_keys)}."
                ),
            }

        if len(per_account_intervals) == 1 or aggregate_mode == "any":
            available_intervals = union_intervals(per_account_intervals)
        else:
            available_intervals = per_account_intervals[0]
            for intervals in per_account_intervals[1:]:
                available_intervals = intersect_intervals(available_intervals, intervals)

        slot_starts = build_slot_starts(available_intervals, duration_minutes)
        tz_set = {self._account_config(accounts[k]).timezone or "UTC" for k in keys_to_use if k not in failed_keys}
        output_tz_name = next(iter(tz_set)) if len(tz_set) == 1 else "UTC"
        output_tz = get_zoneinfo(output_tz_name)
        tz_disagreement = len(tz_set) > 1
        slot_starts = sorted([slot.astimezone(output_tz) for slot in slot_starts])

        try:
            max_slots = int(config.get("max_slots_returned", 3))
        except (TypeError, ValueError):
            max_slots = 3
        total_slots = len(slot_starts)
        if max_slots and max_slots > 0 and total_slots > max_slots:
            slot_starts = slot_starts[:max_slots]
        slot_pairs = [(slot, slot + timedelta(minutes=duration_minutes)) for slot in slot_starts]
        slots_with_end = [{"start": start.isoformat(), "end": end.isoformat()} for start, end in slot_pairs]
        slots = [start.isoformat() for start, _end in slot_pairs]
        readable = [f"{start.strftime('%Y-%m-%d %H:%M')}-{end.strftime('%H:%M')}" for start, end in slot_pairs]
        cals_without_open = [
            key for key in keys_to_use
            if key not in failed_keys and per_account_free_counts.get(key, 0) == 0
        ]

        if slots:
            reason = "available"
            starts_only = [start.strftime("%Y-%m-%d %H:%M") for start, _end in slot_pairs]
            message = (
                "Free slot starts: "
                + ", ".join(starts_only)
                + f". Each slot is {duration_minutes} minutes long in {output_tz_name}: "
                + ", ".join(readable)
                + f". Set end_datetime = start_datetime + {duration_minutes} minutes."
            )
            if tz_disagreement:
                message += " Selected Microsoft calendars use different timezones; pass UTC datetimes with a Z suffix when booking."
            if total_slots > len(slot_starts):
                message += f" (showing {len(slot_starts)} of {total_slots} available; propose 2-3 of these to the caller)."
        elif aggregate_mode == "all" and len(keys_to_use) > 1 and cals_without_open:
            reason = "no_open_windows"
            message = "No working-hours overlap across the selected Microsoft calendars for this range."
        elif total_free_blocks > 0:
            reason = "fully_booked"
            message = "I checked the Microsoft calendar - the open availability windows for this range are fully booked."
        else:
            reason = "no_open_windows"
            if availability_mode == "freebusy":
                message = (
                    f"No working-hours availability in the requested time range. Working hours are "
                    f"configured as {work_start:02d}:00-{work_end:02d}:00 on weekdays."
                )
            else:
                message = f"I don't see any '{free_prefix}' availability blocks on the Microsoft calendar for this range."

        return {
            "status": "success",
            "message": message,
            "slots": slots,
            "slots_with_end": slots_with_end,
            "slot_duration_minutes": duration_minutes,
            "calendar_timezone": output_tz_name,
            "tz_disagreement": tz_disagreement,
            "open_windows_found": total_free_blocks > 0,
            "busy_blocks_found": total_busy_blocks > 0,
            "reason": reason,
            "availability_mode": availability_mode,
            "total_slots_available": total_slots,
            "slots_truncated": total_slots > len(slot_starts),
            "calendars_without_open_windows": cals_without_open,
        }

    def _get_schedule_busy_blocks_chunked(
        self,
        client: MicrosoftGraphClient,
        range_start: datetime,
        range_end: datetime,
        tz_name: str,
    ) -> list[tuple[datetime, datetime]]:
        busy: list[tuple[datetime, datetime]] = []
        cursor = range_start
        max_span = timedelta(days=31)
        while cursor < range_end:
            chunk_end = min(cursor + max_span, range_end)
            for start_iso, end_iso in client.get_schedule(to_utc(cursor), to_utc(chunk_end)):
                start = parse_iso_datetime(start_iso).replace(tzinfo=get_zoneinfo("UTC")).astimezone(get_zoneinfo(tz_name))
                end = parse_iso_datetime(end_iso).replace(tzinfo=get_zoneinfo("UTC")).astimezone(get_zoneinfo(tz_name))
                busy.append((start, end))
            cursor = chunk_end
        return busy

    def _intervals_from_prefix_events(
        self,
        events: list[dict[str, Any]],
        free_prefix: str,
        busy_prefix: str,
        tz_name: str,
    ) -> tuple[list[tuple[datetime, datetime]], int, int]:
        free_blocks: list[tuple[datetime, datetime]] = []
        busy_blocks: list[tuple[datetime, datetime]] = []
        for event in events:
            subject = (event.get("subject") or "").strip()
            start = self._parse_event_dt(event.get("start") or {}, tz_name)
            end = self._parse_event_dt(event.get("end") or {}, tz_name)
            if not start or not end:
                continue
            if subject.startswith(free_prefix):
                free_blocks.append((start, end))
            elif subject.startswith(busy_prefix):
                busy_blocks.append((start, end))
        return subtract_busy(free_blocks, busy_blocks), len(free_blocks), len(busy_blocks)

    async def _handle_list_events(self, parameters: dict[str, Any], cfg: dict[str, str], key: str) -> dict[str, Any]:
        time_min = parameters.get("time_min")
        time_max = parameters.get("time_max")
        if not time_min or not time_max:
            return {"status": "error", "message": "Error: 'time_min' and 'time_max' are required for list_events."}
        account = self._account_config(cfg)
        validation_error = self._validate_account(account)
        if validation_error:
            return {"status": "error", "message": validation_error}
        start = normalize_to_tz(time_min, account.timezone, respect_offset=True)
        end = normalize_to_tz(time_max, account.timezone, respect_offset=True)
        try:
            events = await asyncio.to_thread(self._client_for_config(account).list_calendar_view, to_utc(start), to_utc(end))
        except MicrosoftGraphApiError as exc:
            return self._map_api_error(exc, "Could not list Microsoft Calendar events")
        simplified = []
        for event in events:
            simplified.append({
                "id": event.get("id"),
                "summary": event.get("subject", "No Title"),
                "start": event.get("start", {}).get("dateTime"),
                "end": event.get("end", {}).get("dateTime"),
                "calendar": key,
            })
        return {"status": "success", "message": "Events listed.", "events": simplified}

    async def _handle_get_event(self, parameters: dict[str, Any], cfg: dict[str, str], key: str) -> dict[str, Any]:
        event_id = parameters.get("event_id")
        if not event_id:
            return {"status": "error", "message": "Error: 'event_id' is required for get_event."}
        account = self._account_config(cfg)
        validation_error = self._validate_account(account)
        if validation_error:
            return {"status": "error", "message": validation_error}
        try:
            event = await asyncio.to_thread(self._client_for_config(account).get_event, event_id)
        except MicrosoftGraphApiError as exc:
            return self._map_api_error(exc, "Could not get Microsoft Calendar event")
        if not event:
            return {"status": "error", "error_code": "event_not_found", "message": "Event not found."}
        return {
            "status": "success",
            "message": "Event retrieved.",
            "id": event.get("id"),
            "summary": event.get("subject"),
            "description": ((event.get("body") or {}).get("content") or ""),
            "start": (event.get("start") or {}).get("dateTime"),
            "end": (event.get("end") or {}).get("dateTime"),
            "calendar": key,
        }

    async def _handle_create_event(
        self,
        parameters: dict[str, Any],
        config: dict[str, Any],
        cfg: dict[str, str],
        key: str,
        call_id: str,
    ) -> dict[str, Any]:
        summary = parameters.get("summary")
        start_raw = parameters.get("start_datetime")
        end_raw = parameters.get("end_datetime")
        if not summary or not start_raw or not end_raw:
            return {"status": "error", "message": "Error: 'summary', 'start_datetime', and 'end_datetime' are required for create_event."}
        account = self._account_config(cfg)
        validation_error = self._validate_account(account)
        if validation_error:
            return {"status": "error", "message": validation_error}
        try:
            start_local = normalize_to_tz(start_raw, account.timezone, respect_offset=True)
            end_local = normalize_to_tz(end_raw, account.timezone, respect_offset=True)
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        actual_minutes = (end_local - start_local).total_seconds() / 60.0
        try:
            max_duration = int(config.get("max_event_duration_minutes", 240))
        except (TypeError, ValueError):
            max_duration = 240
        if actual_minutes <= 0:
            return {
                "status": "error",
                "error_code": "invalid_duration",
                "message": "Invalid event duration: end_datetime must be after start_datetime.",
            }
        if max_duration > 0 and actual_minutes > max_duration:
            return {
                "status": "error",
                "error_code": "duration_too_long",
                "message": (
                    f"Event duration {int(actual_minutes)} minutes exceeds the allowed "
                    f"maximum of {max_duration} minutes. Retry with end_datetime equal "
                    "to start_datetime plus the meeting length."
                ),
            }
        try:
            event = await asyncio.to_thread(
                self._client_for_config(account).create_event,
                summary,
                parameters.get("description", "") or "",
                to_utc(start_local),
                to_utc(end_local),
            )
        except MicrosoftGraphApiError as exc:
            return self._map_api_error(exc, "Failed to create Microsoft Calendar event")
        event_id = event.get("id")
        if call_id and event_id:
            with self._last_event_lock:
                if call_id in self._last_event_per_call:
                    self._last_event_per_call.move_to_end(call_id)
                self._last_event_per_call[call_id] = {"event_id": event_id, "account_key": key}
                while len(self._last_event_per_call) > self._LAST_EVENT_CACHE_CAP:
                    self._last_event_per_call.popitem(last=False)
        return {
            "status": "success",
            "message": "Event created.",
            "agent_hint": (
                "If the caller later corrects the date or time, call delete_event with NO "
                "event_id parameter (the tool will delete the event you just created), "
                "then call create_event with the corrected time. Do NOT try to echo back "
                "the event_id — the tool tracks it server-side for this call."
            ),
            "id": event_id,
            "event_id": event_id,
            "link": event.get("webLink"),
            "calendar": key,
        }

    async def _handle_delete_event(self, parameters: dict[str, Any], cfg: dict[str, str], key: str, call_id: str) -> dict[str, Any]:
        event_id = parameters.get("event_id")
        # Real-time speech-to-speech models can't reliably echo opaque Graph
        # event ids back across conversation turns (we observed Gemini hallucinating
        # base64, Deepgram passing the literal "event_id_here", and OpenAI passing
        # "1"). For the common single-call reschedule flow — where create_event
        # ran a few turns earlier in the same call — the authoritative id lives
        # in `_last_event_per_call[call_id]`. If the model omits event_id, use
        # that. Belt-and-suspenders: if it passes a wrong id, the malformed-id
        # fallback below still rescues the call.
        if not event_id and call_id:
            with self._last_event_lock:
                tracked = self._last_event_per_call.get(call_id)
            if tracked and tracked.get("event_id"):
                event_id = tracked["event_id"]
        if not event_id:
            return {
                "status": "error",
                "message": (
                    "Error: 'event_id' is required for delete_event when no event was "
                    "created in this call to fall back on."
                ),
            }
        account = self._account_config(cfg)
        validation_error = self._validate_account(account)
        if validation_error:
            return {"status": "error", "message": validation_error}
        client = self._client_for_config(account)

        def _tracked_fallback_id() -> str | None:
            if not call_id:
                return None
            with self._last_event_lock:
                tracked = self._last_event_per_call.get(call_id)
            if tracked and tracked.get("event_id") and tracked["event_id"] != event_id:
                return tracked["event_id"]
            return None

        # Two failure modes need the tracked-id fallback:
        #   * 404 — `client.delete_event` returns False (the model gave us an id
        #     that's well-formed but not present in this mailbox).
        #   * 400 "The Id is invalid." — `client.delete_event` raises
        #     MicrosoftGraphApiError because the id is structurally malformed
        #     (e.g. an LLM hallucination with a different mailbox prefix).
        # In both cases, if we already created an event for this call we know
        # the authoritative id; retry with it before surfacing an error.
        initial_exc: MicrosoftGraphApiError | None = None
        success = False
        try:
            success = await asyncio.to_thread(client.delete_event, event_id)
        except MicrosoftGraphApiError as exc:
            if exc.status == 400:
                initial_exc = exc
            else:
                return self._map_api_error(exc, "Failed to delete Microsoft Calendar event")
        if not success:
            fallback_id = _tracked_fallback_id()
            if fallback_id:
                try:
                    retry_success = await asyncio.to_thread(client.delete_event, fallback_id)
                except MicrosoftGraphApiError as exc:
                    return self._map_api_error(exc, "Failed to delete Microsoft Calendar event during fallback retry")
                if retry_success:
                    with self._last_event_lock:
                        self._last_event_per_call.pop(call_id, None)
                    return {
                        "status": "success",
                        "message": "Event deleted.",
                        "id": fallback_id,
                        "event_id": fallback_id,
                        "calendar": key,
                    }
            if initial_exc is not None:
                return self._map_api_error(initial_exc, "Failed to delete Microsoft Calendar event")
            return {"status": "error", "error_code": "event_not_found", "message": "Failed to delete event (not found)."}
        if call_id:
            with self._last_event_lock:
                tracked = self._last_event_per_call.get(call_id)
                if tracked and tracked.get("event_id") == event_id:
                    self._last_event_per_call.pop(call_id, None)
        # Match the fallback retry's response shape: surface both `id` and
        # `event_id` for callers that key by either name.
        return {
            "status": "success",
            "message": "Event deleted.",
            "id": event_id,
            "event_id": event_id,
            "calendar": key,
        }
