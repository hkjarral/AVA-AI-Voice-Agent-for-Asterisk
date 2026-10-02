"""Microsoft Calendar tool using device-code OAuth and Microsoft Graph."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import threading
from collections import OrderedDict
from contextlib import contextmanager
from time import monotonic
from datetime import datetime, timedelta
from typing import Any, Dict

import structlog

from src.tools.base import Tool, ToolCategory, ToolDefinition
from src.tools.business._calendar_utils import (
    graph_datetime,
    build_slot_starts,
    intersect_intervals,
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
from src.tools.business.microsoft_booking import (
    BookingValidationError,
    booking_interval,
    invitation_content,
    strict_datetime,
    validated_attendees,
    within_working_hours,
    working_policy,
    CALLER_FIELDS,
    OPERATOR_FIELDS,
    utc_now,
    booking_limits_enabled,
)
from src.tools.context import ToolExecutionContext, resolve_scoped_tool_config

logger = structlog.get_logger(__name__)


_MICROSOFT_CALENDAR_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "list_events",
                "get_event",
                "create_event",
                "delete_event",
                "get_free_slots",
                "check_availability",
                "reschedule_event",
            ],
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
                "Legacy argument accepted for compatibility; operator configuration is authoritative. "
                "Omit this argument. The configured prefix defines open windows; blank uses "
                "selected-calendar event availability plus working hours."
            ),
        },
        "busy_prefix": {
            "type": "string",
            "description": "Legacy argument accepted for compatibility; omit it. The operator configures busy markers.",
        },
        "duration": {
            "type": "integer",
            "description": "Appointment duration in minutes.",
        },
        "event_id": {
            "type": "string",
            "description": "For reads, the Graph id. For delete_event/reschedule_event omit it for the most recently selected booking, or supply an ID returned for another booking created in this same call. Older/untracked bookings require staff.",
        },
        "attendee_emails": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 10,
            "description": "Email addresses spelled out and confirmed by the caller. Omit for an appointment without invitations.",
        },
        "booking_confirmed": {
            "type": "boolean",
            "description": "True only after the caller agrees to the exact date, time, timezone and duration. Required for invitation bookings and rescheduling.",
        },
        "invitation_confirmed": {
            "type": "boolean",
            "description": "True only after the caller confirms every attendee address and agrees to send invitations. Required when attendees are supplied.",
        },
        "cancellation_confirmed": {
            "type": "boolean",
            "description": "True only after reading back the current-call booking and obtaining agreement to cancel it and its invitations.",
        },
        "caller_name": {
            "type": "string",
            "description": "Caller-confirmed name for the invitation body.",
        },
        "meeting_purpose": {
            "type": "string",
            "description": "Caller-confirmed reason for the appointment.",
        },
        "confirmed_notes": {
            "type": "string",
            "description": "Relevant notes confirmed by the caller for inclusion in the invitation.",
        },
        "summary": {"type": "string", "description": "Event title for create_event."},
        "description": {"type": "string", "description": "Optional event description."},
        "start_datetime": {
            "type": "string",
            "description": "ISO 8601 start time for create_event.",
        },
        "end_datetime": {
            "type": "string",
            "description": "ISO 8601 end time for create_event.",
        },
    },
    "required": ["action"],
}


class MicrosoftCalendarTool(Tool):
    _LAST_EVENT_CACHE_CAP = 1024
    # Held inside the worker through read/check/write/cache, even if its awaiting
    # coroutine is cancelled. Shared by registry generations in this process.
    _MUTATION_LOCK = threading.Lock()
    _MUTATION_LOCK_WAIT_SECONDS = 10

    def __init__(self):
        super().__init__()
        self._clients: dict[tuple[str, str, str, str, str], MicrosoftGraphClient] = {}
        self._clients_lock = threading.Lock()
        self._last_event_per_call: "OrderedDict[str, dict]" = OrderedDict()
        self._last_event_lock = threading.Lock()
        self._owned_bookings: "OrderedDict[tuple[str, str], dict]" = OrderedDict()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="microsoft_calendar",
            description=(
                "Interact with a Microsoft 365 Outlook calendar. Use this to list events, "
                "check_availability for an exact requested interval before suggesting free slots. "
                "Confirm bookings and attendee emails before creating invitations. Cancel or "
                "reschedule only a booking created in this call; older bookings require staff."
            ),
            category=ToolCategory.BUSINESS,
            requires_channel=False,
            max_execution_time=30,
            input_schema=_MICROSOFT_CALENDAR_INPUT_SCHEMA,
        )

    def _get_config(self, context: ToolExecutionContext) -> Dict[str, Any]:
        return resolve_scoped_tool_config(
            context, "microsoft_calendar", self._load_config
        )

    def _resolve_accounts(self, config: Dict[str, Any]) -> dict[str, dict[str, str]]:
        accounts: dict[str, dict[str, str]] = {}
        account_map = config.get("accounts") or {}
        if isinstance(account_map, dict) and account_map:
            for key, value in account_map.items():
                if not isinstance(value, dict):
                    continue
                accounts[str(key)] = {
                    "tenant_id": value.get("tenant_id", "")
                    or config.get("tenant_id", ""),
                    "client_id": value.get("client_id", "")
                    or config.get("client_id", ""),
                    "token_cache_path": value.get("token_cache_path", "")
                    or config.get("token_cache_path", ""),
                    "user_principal_name": value.get("user_principal_name", "")
                    or config.get("user_principal_name", ""),
                    "calendar_id": value.get("calendar_id", "")
                    or config.get("calendar_id", ""),
                    "timezone": value.get("timezone", "")
                    or config.get("timezone", "")
                    or "UTC",
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

    def _client_for_config(
        self, account: MicrosoftAccountConfig
    ) -> MicrosoftGraphClient:
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
        for attr in (
            "tenant_id",
            "client_id",
            "token_cache_path",
            "user_principal_name",
            "calendar_id",
        ):
            if not getattr(account, attr):
                missing.append(attr)
        if missing:
            return f"Microsoft Calendar account is missing: {', '.join(missing)}."
        try:
            ZoneInfo(account.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return "Microsoft Calendar has an invalid timezone; ask an operator to correct it."
        return None

    def _map_api_error(
        self, exc: MicrosoftGraphApiError, prefix: str
    ) -> dict[str, Any]:
        messages = {
            "auth_expired": "Microsoft Calendar is not configured for runtime use: reconnect required.",
            "auth_failed": "Microsoft Calendar authorization failed; ask an operator to reconnect.",
            "account_identity_mismatch": "The configured Microsoft identity does not match the signed-in cache. Ask an operator to verify and correct the identity; no other cached account will be used.",
            "forbidden_calendar": "Microsoft Calendar access is forbidden (403).",
            "calendar_not_found": "Microsoft Calendar is not configured correctly: calendar not found.",
            "graph_unavailable": "Microsoft Graph is currently unavailable.",
            "rate_limited": "Microsoft Graph is rate limited; wait before trying again.",
            "booking_changed": "The booking changed in Outlook; ask staff to verify it before making changes.",
        }
        return {
            "status": "error",
            "error_code": exc.error_code,
            "message": f"{prefix}: {messages.get(exc.error_code, 'Microsoft Calendar request failed; ask an operator to investigate.')}",
            "http_status": exc.status,
        }

    def _parse_event_dt(
        self, value: dict[str, Any], fallback_tz: str
    ) -> datetime | None:
        dt_raw = (value or {}).get("dateTime")
        if not dt_raw:
            return None
        # Graph requests use Prefer UTC, but old events can carry timezone names.
        tz_name = (value or {}).get("timeZone") or fallback_tz or "UTC"
        # Respect both positive and negative offsets. Prefer UTC governs naive
        # response values; an unrecognised event timezone fails closed.
        if not isinstance(dt_raw, str):
            raise BookingValidationError(
                "malformed_calendar_event", "Calendar returned an invalid event time."
            )
        # Graph can return seven fractional digits; Python stores microseconds.
        # Normalize only the seconds fraction, leaving timezone offsets intact.
        dt_raw = re.sub(r"([Tt ]\d{2}:\d{2}:\d{2}\.\d{6})\d+", r"\1", dt_raw)
        parsed = parse_iso_datetime(dt_raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
        return parsed.astimezone(ZoneInfo(fallback_tz))

    async def execute(
        self, parameters: Dict[str, Any], context: ToolExecutionContext
    ) -> Dict[str, Any]:
        if not isinstance(parameters, dict) or set(parameters) - set(
            _MICROSOFT_CALENDAR_INPUT_SCHEMA["properties"]
        ):
            return {
                "status": "error",
                "error_code": "invalid_parameters",
                "message": "Use only the documented Microsoft Calendar arguments.",
            }
        call_id = getattr(context, "call_id", None) or ""
        action = parameters.get("action")
        logger.info(
            "MicrosoftCalendarTool execution triggered", call_id=call_id, action=action
        )

        config = self._get_config(context)
        if config.get("enabled") is False:
            return {"status": "error", "message": "Microsoft Calendar is disabled."}
        if not action:
            return {
                "status": "error",
                "message": "Error: 'action' parameter is missing.",
            }

        accounts = self._resolve_accounts(config)
        selected_keys = self._selected_account_keys(config)
        if not selected_keys:
            return {
                "status": "error",
                "message": "No Microsoft Calendar accounts are selected or configured.",
            }

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
            if len(selected_keys) > 1 and action_name in {
                "create_event",
                "delete_event",
                "get_event",
                "reschedule_event",
            }:
                return None, {
                    "status": "error",
                    "message": "account_key is required when multiple Microsoft Calendar accounts are selected.",
                }
            return selected_keys[0], None

        try:
            if action == "check_availability":
                return await self._handle_get_free_slots(
                    parameters, config, accounts, selected_keys, call_id, exact=True
                )
            if action == "get_free_slots":
                return await self._handle_get_free_slots(
                    parameters, config, accounts, selected_keys, call_id
                )
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
                return await self._handle_create_event(
                    parameters, config, accounts[key], key, call_id
                )
            if action == "delete_event":
                key, err = _target_key("delete_event")
                if err:
                    return err
                return await self._run_mutation(
                    self._change_booking,
                    parameters,
                    config,
                    accounts[key],
                    key,
                    call_id,
                    "delete_event",
                )
            if action == "reschedule_event":
                key, err = _target_key("reschedule_event")
                if err:
                    return err
                return await self._run_mutation(
                    self._change_booking,
                    parameters,
                    config,
                    accounts[key],
                    key,
                    call_id,
                    action,
                )
            return {"status": "error", "message": f"Error: Unknown action '{action}'."}
        except MicrosoftGraphApiError as exc:
            return self._map_api_error(exc, "Microsoft Calendar request failed")
        except BookingValidationError as exc:
            return {"status": "error", "error_code": exc.code, "message": str(exc)}
        except Exception:
            logger.error("MicrosoftCalendarTool failed", call_id=call_id, action=action)
            return {
                "status": "error",
                "message": "An unexpected Microsoft Calendar error occurred.",
            }

    async def _handle_get_free_slots(
        self,
        parameters: dict[str, Any],
        config: dict[str, Any],
        accounts: dict[str, dict[str, str]],
        selected_keys: list[str],
        call_id: str,
        exact: bool = False,
    ) -> dict[str, Any]:
        time_min = (
            parameters.get("start_datetime") if exact else parameters.get("time_min")
        )
        time_max = (
            parameters.get("end_datetime") if exact else parameters.get("time_max")
        )
        if not time_min or not time_max:
            return {
                "status": "error",
                "error_code": "missing_parameters",
                "message": "Provide start_datetime/end_datetime for check_availability, or time_min/time_max for get_free_slots.",
            }
        aggregate_mode = (parameters.get("aggregate_mode") or "all").lower()
        if aggregate_mode not in {"all", "any"}:
            aggregate_mode = "all"
        account_key = parameters.get("account_key")
        keys_to_use = [str(account_key)] if account_key else list(selected_keys)
        for key in keys_to_use:
            if key not in accounts or key not in selected_keys:
                return {
                    "status": "error",
                    "message": f"Microsoft Calendar account '{key}' is not available for this context.",
                }

        # Reads and mutations must apply the same operator booking policy.
        free_prefix = (config.get("free_prefix") or "").strip()
        busy_prefix = (config.get("busy_prefix") or "Busy").strip() or "Busy"
        availability_mode = "title_prefix" if free_prefix else "freebusy"

        try:
            duration_minutes = parameters.get(
                "duration", config.get("min_slot_duration_minutes", 30)
            )
            if isinstance(duration_minutes, float) and duration_minutes.is_integer():
                duration_minutes = int(duration_minutes)
            if type(duration_minutes) is not int or not 1 <= duration_minutes <= 1440:
                raise ValueError()
        except (TypeError, ValueError):
            raise BookingValidationError(
                "invalid_duration",
                "duration must be a positive integer number of minutes, at most 1440.",
            )
        work_start, work_end, work_days = working_policy(config, for_booking=exact)
        exact_results = []
        per_account_intervals: list[list[tuple[datetime, datetime]]] = []
        failed_keys: list[str] = []
        failures: dict[str, dict[str, Any]] = {}
        total_free_blocks = 0
        total_busy_blocks = 0
        per_account_free_counts: dict[str, int] = {}

        for key in keys_to_use:
            account = self._account_config(accounts[key])
            validation_error = self._validate_account(account)
            if validation_error:
                failed_keys.append(key)
                failures[key] = {
                    "status": "error",
                    "error_code": "invalid_configuration",
                    "message": validation_error,
                }
                logger.warning(
                    "Invalid Microsoft Calendar account config",
                    call_id=call_id,
                    account_key=key,
                    error=validation_error,
                )
                continue
            client = self._client_for_config(account)
            tz_name = account.timezone or "UTC"
            range_start = strict_datetime(time_min, tz_name)
            range_end = strict_datetime(time_max, tz_name)
            if to_utc(range_end) <= to_utc(range_start) or to_utc(range_end) - to_utc(
                range_start
            ) > timedelta(days=93):
                raise BookingValidationError(
                    "invalid_range",
                    "Use an increasing calendar range of at most 93 days.",
                )
            if exact:
                booking_interval(
                    {"start_datetime": time_min, "end_datetime": time_max},
                    config,
                    tz_name,
                )
            else:
                range_start = max(range_start, utc_now().astimezone(ZoneInfo(tz_name)))
                try:
                    horizon = (
                        int(config.get("booking_horizon_days", 365))
                        if booking_limits_enabled(config)
                        else 0
                    )
                except (TypeError, ValueError):
                    raise BookingValidationError(
                        "invalid_configuration", "Invalid booking horizon."
                    )
                if horizon > 0:
                    range_end = min(
                        range_end,
                        (utc_now() + timedelta(days=horizon)).astimezone(
                            ZoneInfo(tz_name)
                        ),
                    )
            try:
                if range_end <= range_start:
                    per_account_intervals.append([])
                    per_account_free_counts[key] = 0
                    continue
                intervals, free_count, busy_count = await asyncio.to_thread(
                    self._available_intervals,
                    client,
                    range_start,
                    range_end,
                    tz_name,
                    config,
                    free_prefix,
                    busy_prefix,
                    for_booking=exact,
                )
                if exact:
                    exact_results.append(
                        any(a <= range_start and range_end <= b for a, b in intervals)
                    )
            except MicrosoftGraphApiError as exc:
                failed_keys.append(key)
                failures[key] = self._map_api_error(
                    exc, "Could not check Microsoft Calendar availability"
                )
                logger.warning(
                    "Microsoft Calendar API failed during get_free_slots",
                    call_id=call_id,
                    account_key=key,
                    error_code=exc.error_code,
                )
                continue
            per_account_intervals.append(intervals)
            per_account_free_counts[key] = free_count
            total_free_blocks += free_count
            total_busy_blocks += busy_count

        if not per_account_intervals:
            if len(keys_to_use) == 1:
                return failures[keys_to_use[0]]
            return {
                "error_code": "calendars_unavailable",
                "status": "error",
                "message": f"All selected Microsoft Calendar accounts are unavailable: {', '.join(failed_keys)}.",
            }
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
                available_intervals = intersect_intervals(
                    available_intervals, intervals
                )

        if exact:
            available = (
                any(exact_results) if aggregate_mode == "any" else all(exact_results)
            )
            outside_hours = any(
                not within_working_hours(
                    strict_datetime(time_min, accounts[k]["timezone"]),
                    strict_datetime(time_max, accounts[k]["timezone"]),
                    accounts[k]["timezone"],
                    config,
                )
                for k in keys_to_use
                if k not in failed_keys
            )
            return {
                "status": "success",
                "available": available,
                "reason": (
                    "available"
                    if available
                    else "outside_working_hours" if outside_hours else "busy"
                ),
                "message": (
                    "The requested interval is available."
                    if available
                    else "The requested interval is unavailable; offer alternatives."
                ),
                "start": time_min,
                "end": time_max,
                "calendar_timezone": accounts[keys_to_use[0]]["timezone"],
                "unavailable_accounts": failed_keys,
                "agent_hint": "This checks the exact interval. Confirm it with the caller before creation; creation rechecks availability.",
            }

        slot_starts = build_slot_starts(available_intervals, duration_minutes)
        tz_set = {
            self._account_config(accounts[k]).timezone or "UTC"
            for k in keys_to_use
            if k not in failed_keys
        }
        output_tz_name = next(iter(tz_set)) if len(tz_set) == 1 else "UTC"
        output_tz = ZoneInfo(output_tz_name)
        tz_disagreement = len(tz_set) > 1
        slot_starts = sorted([slot.astimezone(output_tz) for slot in slot_starts])

        try:
            max_slots = int(config.get("max_slots_returned", 3))
        except (TypeError, ValueError):
            max_slots = 3
        total_slots = len(slot_starts)
        if max_slots and max_slots > 0 and total_slots > max_slots:
            slot_starts = slot_starts[:max_slots]
        slot_pairs = [
            (slot, slot + timedelta(minutes=duration_minutes)) for slot in slot_starts
        ]
        slots_with_end = [
            {"start": start.isoformat(), "end": end.isoformat()}
            for start, end in slot_pairs
        ]
        slots = [start.isoformat() for start, _end in slot_pairs]
        readable = [
            f"{start.strftime('%Y-%m-%d %H:%M')}-{end.strftime('%H:%M')}"
            for start, end in slot_pairs
        ]
        cals_without_open = [
            key
            for key in keys_to_use
            if key not in failed_keys and per_account_free_counts.get(key, 0) == 0
        ]

        if slots:
            reason = "available"
            starts_only = [
                start.strftime("%Y-%m-%d %H:%M") for start, _end in slot_pairs
            ]
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
            "agent_hint": "These are suggestions, not the complete availability map. Never infer that an omitted time is busy, even when slots_truncated is false: suggestions use a slot grid. Check the caller's requested start/end with check_availability first; offer alternatives only if it is busy or outside permitted hours.",
            "unavailable_accounts": failed_keys,
            "slots_returned": len(slots),
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

    def _available_intervals(
        self,
        client,
        start,
        end,
        tz_name,
        config,
        free_prefix=None,
        busy_prefix=None,
        exclude_id=None,
        for_booking=False,
    ):
        # calendarView uses the SAME selected calendar as create/update/delete.
        events = client.list_calendar_view(to_utc(start), to_utc(end))
        prefix = (
            (config.get("free_prefix") or "").strip()
            if free_prefix is None
            else free_prefix
        )
        busy = []
        opened = []
        for event in events:
            if event.get("isCancelled") or event.get("id") == exclude_id:
                continue
            a = self._parse_event_dt(event.get("start") or {}, tz_name)
            b = self._parse_event_dt(event.get("end") or {}, tz_name)
            if a is None or b is None or to_utc(b) <= to_utc(a):
                raise BookingValidationError(
                    "malformed_calendar_event",
                    "Calendar returned an invalid event interval; availability cannot be confirmed.",
                )
            subject = event.get("subject") or ""
            if prefix and subject.startswith(prefix) and not event.get("transactionId"):
                opened.append((a, b))
            elif (event.get("showAs") or "busy").lower() != "free" or (
                prefix
                and subject.startswith(
                    busy_prefix or config.get("busy_prefix") or "Busy"
                )
            ):
                busy.append((a, b))
        work_start, work_end, days = working_policy(config, for_booking=for_booking)
        windows = (
            [(start, end)]
            if for_booking and not booking_limits_enabled(config)
            else working_hours_mask(start, end, tz_name, work_start, work_end, days)
        )
        if prefix:
            windows = intersect_intervals(union_intervals([opened]), windows)
        return subtract_busy(windows, busy), len(windows), len(busy)

    async def _handle_list_events(
        self, parameters: dict[str, Any], cfg: dict[str, str], key: str
    ) -> dict[str, Any]:
        time_min = parameters.get("time_min")
        time_max = parameters.get("time_max")
        if not time_min or not time_max:
            return {
                "status": "error",
                "message": "Error: 'time_min' and 'time_max' are required for list_events.",
            }
        account = self._account_config(cfg)
        validation_error = self._validate_account(account)
        if validation_error:
            return {"status": "error", "message": validation_error}
        start = strict_datetime(time_min, account.timezone)
        end = strict_datetime(time_max, account.timezone)
        try:
            events = await asyncio.to_thread(
                self._client_for_config(account).list_calendar_view,
                to_utc(start),
                to_utc(end),
            )
        except MicrosoftGraphApiError as exc:
            return self._map_api_error(exc, "Could not list Microsoft Calendar events")
        simplified = []
        for event in events:
            simplified.append(
                {
                    "id": event.get("id"),
                    "summary": event.get("subject", "No Title"),
                    "start": event.get("start", {}).get("dateTime"),
                    "end": event.get("end", {}).get("dateTime"),
                    "calendar": key,
                }
            )
        return {"status": "success", "message": "Events listed.", "events": simplified}

    async def _handle_get_event(
        self, parameters: dict[str, Any], cfg: dict[str, str], key: str
    ) -> dict[str, Any]:
        event_id = parameters.get("event_id")
        if not event_id:
            return {
                "status": "error",
                "message": "Error: 'event_id' is required for get_event.",
            }
        account = self._account_config(cfg)
        validation_error = self._validate_account(account)
        if validation_error:
            return {"status": "error", "message": validation_error}
        try:
            event = await asyncio.to_thread(
                self._client_for_config(account).get_event, event_id
            )
        except MicrosoftGraphApiError as exc:
            return self._map_api_error(exc, "Could not get Microsoft Calendar event")
        if not event:
            return {
                "status": "error",
                "error_code": "event_not_found",
                "message": "Event not found.",
            }
        return {
            "status": "success",
            "message": "Event retrieved.",
            "id": event.get("id"),
            "summary": event.get("subject"),
            "start": (event.get("start") or {}).get("dateTime"),
            "end": (event.get("end") or {}).get("dateTime"),
            "calendar": key,
        }

    def _binding(self, account):
        return hashlib.sha256(
            json.dumps(
                [
                    account.tenant_id,
                    account.client_id,
                    account.token_cache_path,
                    account.user_principal_name,
                    account.calendar_id,
                ]
            ).encode()
        ).hexdigest()

    def _call_bookings(self, call_id):
        with self._last_event_lock:
            return [
                dict(record)
                for (owner, _), record in self._owned_bookings.items()
                if owner == call_id
            ]

    def _track(self, call_id, record):
        with self._last_event_lock:
            identity = (call_id, record["transaction"])
            self._owned_bookings[identity] = record
            self._owned_bookings.move_to_end(identity)
            self._last_event_per_call[call_id] = record
            self._last_event_per_call.move_to_end(call_id)
            # Bound records, not merely calls. Evicted ownership cannot authorize a mutation.
            while len(self._owned_bookings) > self._LAST_EVENT_CACHE_CAP:
                (owner, transaction), _ = self._owned_bookings.popitem(last=False)
                if (
                    self._last_event_per_call.get(owner, {}).get("transaction")
                    == transaction
                ):
                    self._last_event_per_call.pop(owner, None)

    def _mutation_result(
        self,
        event,
        key,
        account,
        start,
        end,
        attendees,
        *,
        operation="created",
        reconciled=False,
    ):
        invited = bool(attendees)
        return {
            "status": "success",
            "message": (
                "Event created." if operation == "created" else "Event rescheduled."
            ),
            "id": event["id"],
            "event_id": event["id"],
            "link": event.get("webLink"),
            "calendar": key,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "calendar_timezone": account.timezone,
            "reservation_status": operation,
            "invitation_status": "request_accepted" if invited else "not_requested",
            "delivery_status": "unknown" if invited else "not_applicable",
            "attendee_acceptance_status": "unknown" if invited else "not_applicable",
            "reconciled": reconciled,
            "agent_hint": "Report the booking result. With attendees, Microsoft accepted the invitation/update request; do not claim delivery or acceptance. To cancel or reschedule this booking, obtain caller agreement and use delete_event or reschedule_event with NO event_id for the most recently selected booking; for another current-call booking use its returned event_id. Read back the selected booking before obtaining agreement. Later-call changes require staff. Never delete first to reschedule.",
        }

    def _uncertain(self, operation):
        return {
            "status": "error",
            "error_code": "mutation_uncertain",
            "reservation_status": "unknown",
            "invitation_status": "unknown",
            "delivery_status": "unknown",
            "attendee_acceptance_status": "unknown",
            "message": f"Calendar {operation} outcome is uncertain.",
            "agent_hint": "Do not promise success or send another booking with changed arguments. Retry the identical operation to reconcile; if uncertainty remains, ask staff to check Outlook.",
        }

    async def _handle_create_event(self, parameters, config, cfg, key, call_id):
        return await self._run_mutation(
            self._create_booking, parameters, config, cfg, key, call_id
        )

    async def _run_mutation(self, worker, *args):
        cancelled = threading.Event()
        deadline = monotonic() + self.definition.max_execution_time
        try:
            return await asyncio.to_thread(worker, *args, cancelled, deadline)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def _check_mutation_active(self, cancelled, deadline):
        if cancelled.is_set() or monotonic() >= deadline:
            raise BookingValidationError(
                "calendar_busy",
                "This calendar attempt expired or was cancelled before requesting a change. Retry identical arguments shortly.",
            )

    @contextmanager
    def _mutation_lock(self, cancelled, deadline):
        self._check_mutation_active(cancelled, deadline)
        timeout = min(self._MUTATION_LOCK_WAIT_SECONDS, max(0, deadline - monotonic()))
        if not self._MUTATION_LOCK.acquire(timeout=timeout):
            raise BookingValidationError(
                "calendar_busy",
                "Another calendar change is in progress; this attempt made no change. Retry shortly.",
            )
        try:
            self._check_mutation_active(cancelled, deadline)
            yield
        finally:
            self._MUTATION_LOCK.release()

    def _create_booking(
        self, parameters, config, cfg, key, call_id, cancelled, deadline
    ):
        if not call_id:
            raise BookingValidationError(
                "missing_call_context", "Booking requires an active call context."
            )
        summary = parameters.get("summary")
        description = parameters.get("description", "") or ""
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary) > 255
            or not isinstance(description, str)
            or len(description) > 8000
        ):
            raise BookingValidationError(
                "invalid_parameters",
                "Provide an event summary (at most 255 characters) and description (at most 8000).",
            )
        account = self._account_config(cfg)
        error = self._validate_account(account)
        if error:
            return {"status": "error", "message": error}
        start, end, _ = booking_interval(
            parameters, config, account.timezone, enforce_future=False
        )
        attendees = validated_attendees(parameters, config)
        if (
            config.get("invitations_enabled") is True
            and parameters.get("booking_confirmed") is not True
        ):
            raise BookingValidationError(
                "confirmation_required",
                "Obtain caller agreement to the exact booking details first.",
            )
        if attendees:
            summary, description = invitation_content(
                parameters, config, start, end, account.timezone
            )
        binding = self._binding(account)
        identity = json.dumps(
            [
                binding,
                call_id,
                summary,
                description,
                to_utc(start).isoformat(),
                to_utc(end).isoformat(),
                sorted(email.casefold() for email in attendees),
            ],
            sort_keys=True,
        )
        transaction = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
        with self._mutation_lock(cancelled, deadline):
            records = self._call_bookings(call_id)
            tracked = next((r for r in records if r["transaction"] == transaction), {})
            for record in records:
                if record.get("pending_operation"):
                    return self._uncertain(record["pending_operation"])
                if (
                    record.get("state") == "uncertain"
                    and record["transaction"] != transaction
                ):
                    raise BookingValidationError(
                        "existing_booking",
                        "A booking outcome is uncertain. Reconcile the identical operation before creating another appointment.",
                    )
            if (
                tracked.get("state") == "cancelled"
                and tracked.get("transaction") == transaction
            ):
                raise BookingValidationError(
                    "cancelled_booking",
                    "This booking was cancelled. Ask staff to rebook the identical appointment.",
                )
            client = self._client_for_config(account)
            try:
                events = client.list_calendar_view(to_utc(start), to_utc(end))
                existing = [
                    e
                    for e in events
                    if e.get("transactionId") == transaction
                    and not e.get("isCancelled")
                ]
                if len(existing) > 1:
                    return self._uncertain("creation")
                if existing:
                    event = existing[0]
                    if not event.get("id"):
                        return self._uncertain("creation")
                    if (
                        self._parse_event_dt(event.get("start") or {}, account.timezone)
                        != start
                        or self._parse_event_dt(
                            event.get("end") or {}, account.timezone
                        )
                        != end
                    ):
                        raise BookingValidationError(
                            "booking_changed",
                            "The booking changed in Outlook; ask staff to verify it.",
                        )
                    # A retry reconciles the same event, never creates a duplicate.
                    self._track(
                        call_id,
                        {
                            "event_id": event["id"],
                            "account_key": key,
                            "binding": binding,
                            "transaction": transaction,
                            "state": "active",
                            "attendees": attendees,
                            "template_parameters": {
                                f: parameters.get(f, "")
                                for f in CALLER_FIELDS + ("summary", "description")
                            },
                            "template_config": {
                                f: config[f]
                                for f in OPERATOR_FIELDS
                                + (
                                    "invitation_subject_template",
                                    "invitation_body_template",
                                )
                                if f in config
                            },
                            "rendered_body": description,
                            "start_utc": to_utc(start).isoformat(),
                            "end_utc": to_utc(end).isoformat(),
                            "subject": summary,
                        },
                    )
                    return self._mutation_result(
                        event, key, account, start, end, attendees, reconciled=True
                    )
                if tracked.get("state") in {"active", "uncertain"}:
                    return self._uncertain("creation")
                booking_interval(parameters, config, account.timezone)
                if not within_working_hours(start, end, account.timezone, config):
                    raise BookingValidationError(
                        "outside_working_hours",
                        "The requested interval is outside configured working hours; offer alternatives.",
                    )
                intervals, _, _ = self._available_intervals(
                    client, start, end, account.timezone, config, for_booking=True
                )
                if not any(a <= start and end <= b for a, b in intervals):
                    raise BookingValidationError(
                        "slot_busy",
                        "The chosen interval is now busy. Check alternatives and obtain caller agreement again.",
                    )
            except MicrosoftGraphApiError as exc:
                return self._map_api_error(exc, "Could not verify the booking")
            record = {
                "account_key": key,
                "binding": binding,
                "transaction": transaction,
                "state": "uncertain",
                "attendees": attendees,
                "template_parameters": {
                    f: parameters.get(f, "")
                    for f in CALLER_FIELDS + ("summary", "description")
                },
                "template_config": {
                    f: config[f]
                    for f in OPERATOR_FIELDS
                    + ("invitation_subject_template", "invitation_body_template")
                    if f in config
                },
                "rendered_body": description,
                "start_utc": to_utc(start).isoformat(),
                "end_utc": to_utc(end).isoformat(),
                "subject": summary,
            }
            self._check_mutation_active(cancelled, deadline)
            self._track(call_id, record)
            try:
                event = client.create_event(
                    summary,
                    description,
                    to_utc(start),
                    to_utc(end),
                    attendee_emails=attendees,
                    transaction_id=transaction,
                )
            except MicrosoftGraphApiError as exc:
                if exc.error_code == "graph_unavailable" or exc.status is None:
                    return self._uncertain("creation")
                record["state"] = "failed"
                self._track(call_id, record)
                return self._map_api_error(
                    exc, "Failed to create Microsoft Calendar event"
                )
            if not isinstance(event, dict) or not event.get("id"):
                return self._uncertain("creation")
            record.update(event_id=event["id"], state="active")
            self._track(call_id, record)
            return self._mutation_result(event, key, account, start, end, attendees)

    def _change_booking(
        self, parameters, config, cfg, key, call_id, operation, cancelled, deadline
    ):
        account = self._account_config(cfg)
        error = self._validate_account(account)
        if error:
            return {"status": "error", "message": error}
        if (
            parameters.get(
                "cancellation_confirmed"
                if operation == "delete_event"
                else "booking_confirmed"
            )
            is not True
        ):
            raise BookingValidationError(
                "confirmation_required",
                "Read back the booking and obtain caller agreement to cancel or reschedule it first.",
            )
        with self._mutation_lock(cancelled, deadline):
            records = self._call_bookings(call_id)
            with self._last_event_lock:
                latest = self._last_event_per_call.get(call_id, {}).get("transaction")
            requested_id = parameters.get("event_id")
            tracked = next(
                (
                    r
                    for r in records
                    if (
                        (
                            r.get("event_id") == requested_id
                            and r.get("binding") == self._binding(account)
                            and r.get("account_key") == key
                        )
                        if requested_id
                        else r["transaction"] == latest
                    )
                ),
                {},
            )
            if (
                requested_id
                and not tracked
                and any(r.get("binding") == self._binding(account) for r in records)
            ):
                raise BookingValidationError(
                    "booking_mismatch",
                    "event_id does not match a booking owned by this call. Omit it for the most recently selected booking.",
                )
            for record in records:
                if record["transaction"] != tracked.get("transaction") and (
                    record.get("pending_operation")
                    or record.get("state") == "uncertain"
                ):
                    return self._uncertain(
                        record.get("pending_operation") or "creation"
                    )
            if (
                not call_id
                or not tracked.get("event_id")
                or tracked.get("binding") != self._binding(account)
                or tracked.get("account_key") != key
            ):
                raise BookingValidationError(
                    "staff_assistance_required",
                    "No booking from this call is available for this calendar. An event_id does not authorize changes; ask staff to handle earlier bookings.",
                )
            event_id = tracked["event_id"]
            if parameters.get("event_id") and parameters["event_id"] != event_id:
                raise BookingValidationError(
                    "booking_mismatch",
                    "event_id does not match this call's booking. Omit it and confirm the current-call booking.",
                )
            if operation == "delete_event" and tracked.get("state") == "cancelled":
                return {
                    "status": "success",
                    "message": "Event already cancelled.",
                    "reservation_status": "cancelled",
                    "calendar": key,
                    "event_id": event_id,
                    "invitation_status": tracked.get("cancellation_status", "unknown"),
                    "delivery_status": (
                        "unknown" if tracked.get("attendees") else "not_applicable"
                    ),
                }
            client = self._client_for_config(account)
            try:
                event = client.get_event(event_id)
                if event is None or event.get("isCancelled"):
                    if operation == "delete_event":
                        tracked.update(
                            state="cancelled",
                            pending_operation=None,
                            pending_target=None,
                            pending_body=None,
                            pending_subject=None,
                            cancellation_status="unknown",
                        )
                        self._track(call_id, tracked)
                        return {
                            "status": "success",
                            "message": "Event is no longer present.",
                            "reservation_status": "cancelled",
                            "calendar": key,
                            "invitation_status": "unknown",
                            "delivery_status": "unknown",
                            "event_id": event_id,
                        }
                    raise BookingValidationError(
                        "event_not_found",
                        "Booking no longer exists; ask staff to investigate.",
                    )
                if event.get("type", "singleInstance") != "singleInstance":
                    raise BookingValidationError(
                        "unsupported_event",
                        "Recurring bookings require staff assistance.",
                    )
                actual_start = self._parse_event_dt(
                    event.get("start") or {}, account.timezone
                )
                actual_end = self._parse_event_dt(
                    event.get("end") or {}, account.timezone
                )
                observed = (
                    [to_utc(actual_start).isoformat(), to_utc(actual_end).isoformat()]
                    if actual_start and actual_end
                    else None
                )
                expected = [tracked.get("start_utc"), tracked.get("end_utc")]
                matches_expected = observed == expected and event.get(
                    "subject"
                ) == tracked.get("subject")
                matches_pending = (
                    tracked.get("pending_operation") == "reschedule_event"
                    and observed == tracked.get("pending_target")
                    and event.get("subject") == tracked.get("pending_subject")
                )
                if observed is None or not (matches_expected or matches_pending):
                    raise BookingValidationError(
                        "booking_changed",
                        "The booking time changed in Outlook; ask staff to verify it before making changes.",
                    )
                attendees = event.get("attendees") or []
                current_emails = sorted(
                    str(
                        (item.get("emailAddress") or {}).get("address") or ""
                    ).casefold()
                    for item in attendees
                )
                if current_emails != sorted(
                    email.casefold() for email in tracked.get("attendees", [])
                ):
                    raise BookingValidationError(
                        "booking_changed",
                        "Attendees changed in Outlook; ask staff to verify consent before making changes.",
                    )
                if attendees and event.get("isOrganizer") is not True:
                    raise BookingValidationError(
                        "not_organizer",
                        "This calendar is not the meeting organizer; ask staff to handle the change.",
                    )
                if attendees or operation == "delete_event":
                    expected_body = (
                        tracked.get("pending_body", "")
                        if matches_pending
                        else tracked.get("rendered_body", "")
                    )
                    current_body = event.get("body") or {}
                    if (
                        event.get("isOnlineMeeting")
                        or current_body.get("contentType", "text").lower() != "text"
                        or current_body.get("content", "")
                        .replace("\r\n", "\n")
                        .replace("\r", "\n")
                        .strip()
                        != expected_body.replace("\r\n", "\n")
                        .replace("\r", "\n")
                        .strip()
                    ):
                        raise BookingValidationError(
                            "booking_changed",
                            "The invitation body changed in Outlook; ask staff to handle it safely.",
                        )
                if operation == "delete_event":
                    if tracked.get("pending_operation") not in {None, "delete_event"}:
                        return self._uncertain("rescheduling")
                    etag = event.get("@odata.etag")
                    if (
                        not isinstance(etag, str)
                        or not etag.strip()
                        or etag.strip() == "*"
                    ):
                        raise BookingValidationError(
                            "booking_changed",
                            "Cannot verify the booking version; ask staff to handle cancellation.",
                        )
                    self._check_mutation_active(cancelled, deadline)
                    tracked["pending_operation"] = operation
                    self._track(call_id, tracked)
                    # DELETE on an organizer meeting generates cancellation notices.
                    deleted = client.delete_event(event_id, etag=etag)
                    tracked.update(
                        state="cancelled",
                        pending_operation=None,
                        pending_target=None,
                        pending_body=None,
                        pending_subject=None,
                        cancellation_status=(
                            (
                                "cancellation_request_accepted"
                                if attendees
                                else "not_requested"
                            )
                            if deleted
                            else "unknown"
                        ),
                    )
                    self._track(call_id, tracked)
                    return {
                        "status": "success",
                        "message": "Event cancelled.",
                        "id": event_id,
                        "event_id": event_id,
                        "calendar": key,
                        "reservation_status": "cancelled",
                        "invitation_status": tracked["cancellation_status"],
                        "delivery_status": "unknown" if attendees else "not_applicable",
                        "agent_hint": "Report cancellation. Do not claim cancellation-message delivery. Later-call booking changes require staff.",
                    }
                if tracked.get("state") == "cancelled":
                    raise BookingValidationError(
                        "event_not_found",
                        "This booking was cancelled; ask staff to rebook it.",
                    )
                if any(
                    field in parameters
                    for field in (
                        "attendee_emails",
                        "summary",
                        "description",
                        "caller_name",
                        "meeting_purpose",
                        "confirmed_notes",
                    )
                ):
                    raise BookingValidationError(
                        "unsupported_change",
                        "reschedule_event changes only the time and template date fields; attendees and other details are preserved.",
                    )
                start, end, _ = booking_interval(
                    parameters, config, account.timezone, enforce_future=False
                )
                if not within_working_hours(start, end, account.timezone, config):
                    raise BookingValidationError(
                        "outside_working_hours",
                        "The new interval is outside working hours; the original booking is retained.",
                    )
                target = [to_utc(start).isoformat(), to_utc(end).isoformat()]
                if tracked.get("pending_operation") and (
                    tracked.get("pending_operation") != operation
                    or tracked.get("pending_target") != target
                ):
                    return self._uncertain("rescheduling")
                current_start = self._parse_event_dt(
                    event.get("start") or {}, account.timezone
                )
                current_end = self._parse_event_dt(
                    event.get("end") or {}, account.timezone
                )
                if current_start is None or current_end is None:
                    raise BookingValidationError(
                        "malformed_calendar_event",
                        "Cannot verify the existing booking time; ask staff.",
                    )
                if to_utc(current_start) == to_utc(start) and to_utc(
                    current_end
                ) == to_utc(end):
                    tracked.update(
                        pending_operation=None,
                        pending_target=None,
                        rendered_body=tracked.get("pending_body")
                        or tracked.get("rendered_body", ""),
                        pending_body=None,
                        subject=tracked.get("pending_subject")
                        or tracked.get("subject"),
                        pending_subject=None,
                        start_utc=target[0],
                        end_utc=target[1],
                    )
                    self._track(call_id, tracked)
                    return self._mutation_result(
                        event,
                        key,
                        account,
                        start,
                        end,
                        attendees,
                        operation="rescheduled",
                        reconciled=True,
                    )
                if tracked.get("pending_operation"):
                    return self._uncertain("rescheduling")
                booking_interval(parameters, config, account.timezone)
                intervals, _, _ = self._available_intervals(
                    client,
                    start,
                    end,
                    account.timezone,
                    config,
                    exclude_id=event_id,
                    for_booking=True,
                )
                if not any(a <= start and end <= b for a, b in intervals):
                    raise BookingValidationError(
                        "slot_busy",
                        "The new interval is busy; the original booking is retained.",
                    )
                update_body = {
                    "start": {"dateTime": graph_datetime(start), "timeZone": "UTC"},
                    "end": {"dateTime": graph_datetime(end), "timeZone": "UTC"},
                }
                rendered_body = tracked.get("rendered_body", "")
                rendered_subject = tracked.get("subject")
                if attendees:
                    rendered_subject, rendered_body = invitation_content(
                        tracked["template_parameters"],
                        tracked["template_config"],
                        start,
                        end,
                        account.timezone,
                    )
                    update_body["subject"] = rendered_subject
                    update_body["body"] = {
                        "contentType": "text",
                        "content": rendered_body,
                    }
                etag = event.get("@odata.etag")
                if not isinstance(etag, str) or not etag.strip() or etag.strip() == "*":
                    raise BookingValidationError(
                        "booking_changed",
                        "Cannot verify the booking version; ask staff to handle rescheduling.",
                    )
                self._check_mutation_active(cancelled, deadline)
                tracked.update(
                    pending_operation=operation,
                    pending_target=target,
                    pending_body=rendered_body,
                    pending_subject=rendered_subject,
                )
                self._track(call_id, tracked)
                updated = client.update_event(event_id, update_body, etag)
                if not isinstance(updated, dict) or updated.get("id") != event_id:
                    return self._uncertain("rescheduling")
                tracked.update(
                    pending_operation=None,
                    pending_target=None,
                    rendered_body=rendered_body,
                    pending_body=None,
                    subject=rendered_subject,
                    pending_subject=None,
                    start_utc=target[0],
                    end_utc=target[1],
                )
                self._track(call_id, tracked)
                return self._mutation_result(
                    updated,
                    key,
                    account,
                    start,
                    end,
                    attendees,
                    operation="rescheduled",
                )
            except MicrosoftGraphApiError as exc:
                if tracked.get("pending_operation"):
                    if exc.error_code == "graph_unavailable" or exc.status is None:
                        return self._uncertain(operation)
                    tracked.update(
                        pending_operation=None,
                        pending_target=None,
                        pending_body=None,
                        pending_subject=None,
                    )
                    self._track(call_id, tracked)
                return self._map_api_error(
                    exc, "Could not change the current-call booking"
                )
