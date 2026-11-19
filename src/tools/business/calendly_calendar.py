"""Optional Calendly v1 native in-call adapter."""
import asyncio
import logging
import copy
import time
from datetime import datetime, timedelta, timezone

from src.core.calendly_store import CalendlyStore
from src.tools.base import Tool, ToolCategory, ToolDefinition
from src.tools.context import resolve_scoped_tool_config
from src.tools.business.calendly_client import CalendlyError, instant
from src.tools.business.calendly_service import CalendlyService

logger = logging.getLogger(__name__)


class CalendlyCalendarTool(Tool):
    @property
    def definition(self):
        props = {
            "action": {"type": "string", "enum": ["list_appointment_types", "get_available_slots", "create_booking"]},
            "appointment_key": {"type": "string", "description": "Exact key returned by list_appointment_types."},
            "start_datetime": {"type": "string", "description": "Availability window start, ISO 8601 with UTC offset. For next available, omit both dates to search the next seven days using the server clock."},
            "end_datetime": {"type": "string", "description": "Exclusive availability window end, ISO 8601 with UTC offset. Defaults to seven days after the start. For a specific local day, send local midnight to next midnight with the correct offsets."},
            "timezone": {"type": "string", "description": "Required caller-confirmed IANA timezone, e.g. America/Los_Angeles."},
            "slot_reference": {"type": "string", "description": "Exact opaque reference returned by get_available_slots. Never a date/time or invented value. Select a returned slot before create_booking."}, "name": {"type": "string"}, "email": {"type": "string"},
            "confirmation_reference": {"type": "string", "description": "Exact reference returned by the recap. After fresh approval, send this and both flags true; the active call retains the prepared details. Never invent or shorten a reference."},
            "booking_confirmed": {"type": "boolean"}, "email_confirmed": {"type": "boolean"},
            "answers": {"type": "array", "items": {"type": "object", "properties": {"position": {"type": "integer"}, "answer": {"type": "string"}}, "required": ["position", "answer"], "additionalProperties": False}},
        }
        return ToolDefinition(name="calendly_calendar", category=ToolCategory.BUSINESS, requires_channel=False,
                              max_execution_time=20,
                              description="Book one Calendly appointment in the current call. Use action values, never separate function names. List allowed appointment types, offer returned slots, collect required questions. First call create_booking without confirmation_reference to prepare a canonical recap; this does not book. Spell the returned email including every dot and symbol, read date/time/timezone/duration/location/name/recipient, and ask for final approval. After fresh approval, call create_booking with the exact returned confirmation_reference and email_confirmed=true, booking_confirmed=true. The active call retains the unchanged prepared details; repeating them is optional. Corrections require a new recap. Validation errors do not mean a slot is busy. Never retry an uncertain booking or create another appointment to change a booking; use the Calendly confirmation or staff. Booking success does not prove invitation delivery.",
                              input_schema={"type": "object", "properties": props, "required": ["action"], "additionalProperties": False})

    async def execute(self, parameters, context):
        try:
            async with asyncio.timeout(15):
                return await self._execute(parameters, context)
        except TimeoutError:
            return {"status": "pending", "code": "scheduling_timeout", "message": "Scheduling could not be confirmed. Ask staff to verify before booking again."}

    async def _execute(self, parameters, context):
        try:
            cfg = resolve_scoped_tool_config(context, "calendly_calendar", lambda: {})
            if not cfg.get("enabled") or not context.call_id:
                raise CalendlyError("tool_not_enabled")
            allowed = cfg.get("selected_appointments")
            if allowed is None:
                allowed = list((cfg.get("appointments") or {}).keys())
            operation = "call:" + context.call_id
            store = await asyncio.to_thread(CalendlyStore)
            service = CalendlyService(store)
            if parameters.get("action") == "list_appointment_types":
                conn, _ = await service.credentials(admission=True)
                entries = []
                for key in allowed:
                    try:
                        value = service.binding(cfg, key, conn)
                    except CalendlyError:
                        continue  # Incomplete/denied entries grant no access to this agent.
                    entries.append({"key": key, "name": str(value.get("name") or key)[:200], "duration_minutes": value.get("duration")})
                return {"status": "success", "appointment_types": entries}
            if parameters.get("action") == "get_available_slots":
                if parameters.get("appointment_key") not in allowed:
                    raise CalendlyError("appointment_not_allowed")
                start, end = parameters.get("start_datetime", ""), parameters.get("end_datetime", "")
                if not start and end:
                    raise CalendlyError("search_start_required")
                if not start:
                    start = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
                if not end:
                    end = (instant(start) + timedelta(days=7)).isoformat()
                return await service.slots(cfg, operation, parameters["appointment_key"], start,
                                           end, parameters.get("timezone", ""))
            if parameters.get("action") == "create_booking":
                async def active():
                    session = await context.get_session()
                    if not session or getattr(session, "cleanup_in_progress", False) or getattr(session, "cleanup_completed", False):
                        raise CalendlyError("call_not_active")
                    if await context.get_tool_block_response("calendly_calendar"):
                        raise CalendlyError("call_not_active")
                session = await context.get_session() if getattr(context, "session_store", None) is not None else None
                request = dict(parameters)
                reference = request.get("confirmation_reference")
                if reference and not await asyncio.to_thread(store.prior, operation):
                    saved = getattr(session, "_calendly_confirmation", {}) if session else {}
                    if saved and (saved["expires"] < time.monotonic() or saved["reference"] != reference):
                        raise CalendlyError("confirmation_changed_reconfirm")
                    if saved:
                        # Retain only validated preparation details in the active
                        # call's memory. Never infer fresh approval flags or put
                        # this cache in the journal/history. Explicitly supplied
                        # changes still reach the service's digest comparison.
                        request = {**copy.deepcopy(saved["parameters"]), **request}
                    elif not all(request.get(k) for k in ("slot_reference", "name", "email")):
                        raise CalendlyError("confirmation_details_required")
                result = await service.book(cfg, operation, request, active)
                if session is not None:
                    if result.get("status") == "confirmation_required":
                        prepared = {"action": "create_booking", "slot_reference": request["slot_reference"],
                                    "name": result["confirmation_recap"]["name"], "email": result["confirmation_recap"]["email"],
                                    "answers": [{"position": a["position"], "answer": a["answer"]}
                                                for a in (request.get("answers") or [])]}
                        # Private runtime attribute: excluded from dataclass
                        # serialization and repr, discarded with the call.
                        session._calendly_confirmation = {"reference": result["confirmation_reference"],
                                                         "parameters": prepared, "expires": time.monotonic() + 300}
                    elif result.get("reservation_status"):
                        session._calendly_confirmation = {}
                return result
            raise CalendlyError("unsupported_action")
        except CalendlyError as exc:
            messages = {
                "invalid_timezone": "Ask for the caller's timezone, then check availability with its IANA name. For next available, omit both dates to search the next seven days. No booking was submitted.",
                "invalid_datetime": "Correct the availability dates using ISO 8601 with UTC offsets. For next available, omit both dates to search the next seven days. No booking was submitted.",
                "search_start_required": "Supply a search start along with the end, or omit both dates for the next seven days. No booking was submitted.",
                "invalid_search_window": "Search a future window of at most 31 days. For next available, omit both dates for the next seven days. For a specific day use midnight to next midnight in the caller's timezone. No booking was submitted.",
                "proposal_expired": "Check availability again and use an exact returned slot_reference; never use a date/time as a reference. Then prepare a new recap and obtain approval. No booking was submitted.",
                "confirmation_details_required": "The prepared details are unavailable in this call. Prepare a new recap with the selected slot, name and exact email, then obtain fresh approval. No booking was submitted; this does not mean the time is busy.",
                "confirmation_changed_reconfirm": "The confirmation reference is stale, changed or incorrect. Prepare a new recap without a confirmation_reference and obtain fresh approval. No booking was submitted; do not say the time is unavailable.",
            }
            return {"status": "pending" if exc.uncertain else "error", "code": exc.code,
                    "message": messages.get(exc.code, "Scheduling could not be confirmed. Clarify missing details or ask staff; never repeat an uncertain booking.")}
        except (TimeoutError, OSError, ValueError):
            return {"status": "pending", "code": "scheduling_unavailable", "message": "Scheduling confirmation is unavailable. Ask staff to check before booking again."}
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Calendly execution unavailable")
            return {"status": "pending", "code": "scheduling_unavailable", "message": "Ask staff to verify this request before booking again."}
