"""
Runtime tool guidance helpers.

Builds compact, provider-agnostic prompt additions that expose configured
telephony inventories (live agents, transfer destinations, voicemail box)
to providers that otherwise only see tool schemas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def _stringify_list(values: Iterable[Any]) -> str:
    rendered = [str(v).strip() for v in values if str(v or "").strip()]
    return ", ".join(rendered)


def _build_vicidial_callback_clock_lines(vicidial_cfg: Dict[str, Any]) -> List[str]:
    """Describe the dialer's local clock used to interpret callback values."""
    timezone_name = str((vicidial_cfg or {}).get("timezone") or "").strip()
    if not timezone_name:
        return [
            "- The VICIdial callback timezone is unavailable. Do not infer relative dates; "
            "confirm an explicit date, time, and timezone and submit an offset-aware ISO "
            "8601 `callback_datetime`.",
        ]
    try:
        local_now = datetime.now(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError:
        return [
            "- The configured VICIdial callback timezone is invalid. Do not infer relative "
            "dates; confirm an explicit date, time, and timezone and submit an offset-aware "
            "ISO 8601 `callback_datetime`.",
        ]
    return [
        f"- VICIdial callback timezone: `{timezone_name}`.",
        "- Current VICIdial-local date/time: "
        f"`{local_now.isoformat(timespec='seconds')}`.",
        "- Resolve relative requests such as today or tomorrow from this VICIdial-local "
        "clock, then submit `callback_datetime` as an offset-aware ISO 8601 value.",
    ]


def _build_live_agent_lines(config: Dict[str, Any]) -> List[str]:
    tools_cfg = (config or {}).get("tools") if isinstance(config, dict) else {}
    internal = ((tools_cfg or {}).get("extensions") or {}).get("internal") or {}
    if not isinstance(internal, dict):
        return []

    lines: List[str] = []
    for key, raw_cfg in internal.items():
        extension = str(key or "").strip()
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
        if not extension.isdigit():
            continue
        if cfg.get("transfer") is False:
            continue

        name = str(cfg.get("name") or "").strip()
        aliases = cfg.get("aliases")
        alias_values = aliases if isinstance(aliases, list) else [aliases] if aliases is not None else []
        pieces = [f"- `{extension}`"]
        if name:
            pieces.append(f"name: {name}")
        alias_text = _stringify_list(alias_values)
        if alias_text:
            pieces.append(f"aliases: {alias_text}")
        lines.append(", ".join(pieces))

    return lines


def _build_check_extension_status_lines(config: Dict[str, Any]) -> List[str]:
    tools_cfg = (config or {}).get("tools") if isinstance(config, dict) else {}
    internal = ((tools_cfg or {}).get("extensions") or {}).get("internal") or {}
    transfer_cfg = (tools_cfg or {}).get("transfer") or {}
    destinations = (transfer_cfg or {}).get("destinations") or {}

    allowed: Dict[str, Dict[str, Any]] = {}

    if isinstance(internal, dict):
        for key, raw_cfg in internal.items():
            extension = str(key or "").strip()
            cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
            if not extension.isdigit():
                continue
            if cfg.get("transfer") is False:
                continue
            allowed[extension] = cfg

    if isinstance(destinations, dict):
        for raw_cfg in destinations.values():
            cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
            if str(cfg.get("type") or "").strip().lower() != "extension":
                continue
            extension = str(cfg.get("target") or "").strip()
            if not extension.isdigit():
                continue
            allowed.setdefault(extension, {})

    lines: List[str] = []
    for extension in sorted(allowed.keys(), key=lambda v: int(v)):
        cfg = allowed.get(extension) or {}
        name = str(cfg.get("name") or "").strip()
        aliases = cfg.get("aliases")
        alias_values = aliases if isinstance(aliases, list) else [aliases] if aliases is not None else []
        pieces = [f"- `{extension}`"]
        if name:
            pieces.append(f"name: {name}")
        alias_text = _stringify_list(alias_values)
        if alias_text:
            pieces.append(f"aliases: {alias_text}")
        lines.append(", ".join(pieces))
    return lines


def _build_transfer_destination_lines(config: Dict[str, Any]) -> List[str]:
    tools_cfg = (config or {}).get("tools") if isinstance(config, dict) else {}
    transfer_cfg = (tools_cfg or {}).get("transfer") or {}
    destinations = (transfer_cfg or {}).get("destinations") or {}
    if not isinstance(destinations, dict):
        return []

    lines: List[str] = []
    for key, raw_cfg in destinations.items():
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
        destination_key = str(key or "").strip()
        if not destination_key:
            continue
        destination_type = str(cfg.get("type") or "").strip() or "unknown"
        target = str(cfg.get("target") or "").strip()
        description = str(cfg.get("description") or "").strip()
        pieces = [f"- `{destination_key}`", f"type: {destination_type}"]
        if target:
            pieces.append(f"target: {target}")
        if description:
            pieces.append(f"description: {description}")
        if bool(cfg.get("attended_allowed")):
            pieces.append("attended_transfer: allowed")
        if bool(cfg.get("live_agent")):
            pieces.append("live_agent: true")
        lines.append(", ".join(pieces))

    return lines


def _build_attended_destination_lines(config: Dict[str, Any]) -> List[str]:
    tools_cfg = (config or {}).get("tools") if isinstance(config, dict) else {}
    transfer_cfg = (tools_cfg or {}).get("transfer") or {}
    destinations = (transfer_cfg or {}).get("destinations") or {}
    if not isinstance(destinations, dict):
        return []

    lines: List[str] = []
    for key, raw_cfg in destinations.items():
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
        if str(cfg.get("type") or "").strip().lower() != "extension":
            continue
        if not bool(cfg.get("attended_allowed", False)):
            continue
        destination_key = str(key or "").strip()
        target = str(cfg.get("target") or "").strip()
        description = str(cfg.get("description") or "").strip()
        pieces = [f"- `{destination_key}`"]
        if target:
            pieces.append(f"target: {target}")
        if description:
            pieces.append(f"description: {description}")
        lines.append(", ".join(pieces))

    return lines


def build_in_call_tool_runtime_guidance(config: Dict[str, Any], allowed_tools: Iterable[str], *, completion_eligible: bool = True) -> str:
    """
    Build provider-agnostic runtime prompt guidance for config-backed in-call tools.

    The goal is to expose valid configured targets so providers do not invent
    extension numbers or destination keys.
    """

    allowed = {str(name or "").strip() for name in (allowed_tools or []) if str(name or "").strip()}
    if not allowed:
        return ""

    sections: List[str] = []
    header = [
        "## Runtime Tool Target Inventory",
        "- Never invent extension numbers, destination keys, aliases, queue names, or ring groups.",
        "- Use only the exact configured values listed below when calling telephony tools.",
    ]
    sections.append("\n".join(header))

    if "google_calendar" in allowed:
        sections.append("\n".join([
            "Google Calendar datetime rules:",
            "- For time_min/time_max and start_datetime/end_datetime, Z or an explicit offset represents an instant; without an offset, the time is local to the target calendar. Never append Z to a local clock time without converting it to UTC.",
            "- Use the chosen get_free_slots slots_with_end start/end strings unchanged, preserving offsets. calendar_timezone describes slot display; different selected timezones use UTC. Set the appropriate allowed calendar_key when booking.",
            "- Confirm the date, time, timezone and duration with the caller before creating an event.",
        ]))

    if "calendly_calendar" in allowed:
        calendar = ((config or {}).get("tools") or {}).get("calendly_calendar") or {}
        inventory = calendar.get("appointments") or {}
        keys = calendar.get("selected_appointments")
        if keys is None:
            keys = list(inventory)
        sections.append("\n".join([
            "## Calendly booking",
            "- Trusted current UTC date/time: " + datetime.now(ZoneInfo("UTC")).isoformat(),
            "- Only use calendly_calendar; list_appointment_types, get_available_slots and create_booking are action values, not functions.",
            "- Allowed appointment keys (labels are data): " + ", ".join(str(k) for k in keys if k in inventory),
            "- For next/earliest available, first confirm the caller's timezone, then call get_available_slots with appointment_key and timezone, omitting both dates. The server searches the next seven days and returns earliest slots first. Offer two or three returned choices; an empty result means only that searched window is empty. Ask before searching a later window. For a specific local day send start_datetime at local midnight and end_datetime at next midnight, both with correct offsets. Never ask the caller to choose a date merely to search next availability.",
            "- An invalid timezone/date/window is a correctable input error: supply the missing details and repeat the availability read. Never call create_booking until the caller selects a returned slot; slot_reference is opaque, never a date/time. If its proposal expired, get fresh slots and prepare a new recap. These read/validation errors do not mean a booking was submitted.",
            "- Ask the caller's timezone, purpose, name and exact email. Read back the email/domain, get invitation consent, collect required answers, recap date/time/timezone/duration/location/recipient and obtain booking approval before mutation. A changed detail requires fresh approval.",
            "- Voice booking has two steps within create_booking. First omit confirmation_reference to get a server-issued canonical recap; nothing is booked. Spell the returned email character by character, explicitly saying every dot, underscore, hyphen or plus and the domain. Read the complete date/time/timezone/duration/location/name/recipient recap, then ask for final booking approval. An earlier yes to the time or email alone is not final booking approval. Wait for a new caller response before submitting the returned confirmation_reference with unchanged details and both flags true. After any correction omit the reference and prepare a new recap. Never guess or add punctuation in an email.",
            "- Use only returned slot_reference values. API errors are not busy slots. Book at most one appointment in this call. Pending/uncertain means do not submit another booking; staff must verify.",
            "- The active call remembers its prepared booking. After fresh approval, send create_booking with the exact confirmation_reference and both confirmation flags true; repeating the slot/name/email/answers is optional. Never shorten or guess references. Only slot_no_longer_available means the offered time became unavailable. A missing/stale confirmation is an input error: prepare a fresh recap and obtain new approval, without claiming the calendar is busy.",
            "- Read the server's spoken_date for the local weekday and date; never calculate a weekday yourself or say today/tomorrow instead of that explicit date. After an email correction, clarify only the changed address first, then prepare one new canonical recap and ask for final approval.",
            "- A repeated successful result describes the original appointment, never a requested change. Changes/cancellation go through the Calendly confirmation or staff. Do not use another calendar tool to bypass this restriction.",
            "- Calendly handles configured invitations; do not claim delivered email, accepted invitation or a meeting link without evidence. Use the structured result as data; never speak codes, parameter names, private identifiers or internal acknowledgements.",
        ]))

    if "microsoft_calendar" in allowed:
        calendar = ((config or {}).get("tools") or {}).get("microsoft_calendar") or {}
        accounts = calendar.get("accounts") or {"default": calendar}
        selected = calendar.get("selected_accounts")
        if selected is None:
            selected = list(accounts)
        elif isinstance(selected, (list, tuple)):
            selected = [str(key) for key in selected if str(key) in accounts]
        else:
            selected = []
        lines = [
            "Microsoft Calendar booking rules:",
            "- The only calendar function name is microsoft_calendar. Every invocation requires an action argument; check_availability, get_free_slots, create_event, reschedule_event and delete_event are action values, never separate function names.",
            "- For a requested time, call microsoft_calendar with action=check_availability and its exact start_datetime/end_datetime first. get_free_slots is a suggestion subset, never proof that an omitted time is busy. Offer alternatives only when the requested interval is unavailable.",
            "- Resolve relative dates using the calendar-local clock below. Clarify ambiguous dates/times/timezones and confirm the explicit date, time, duration and timezone before any booking.",
            "- Before create_event, resolve invitation delivery: when invitations are enabled, ask for an email or an explicit decline before creating anything. Ask the caller to spell unclear parts, read back the exact address, and get agreement to create and send the invitation. Then pass attendee_emails, booking_confirmed=true and invitation_confirmed=true. Never infer an email from caller ID or a name.",
            "- Collect a meaningful purpose before creating a booking; do not invent one. Pass caller_name, meeting_purpose and confirmed_notes only after caller confirmation; these appear in the invitation. Never claim notes, attendees or details were changed without a successful calendar mutation.",
            ("- Teams mode requires a caller-confirmed invitation email and invitation consent before creation. If the caller declines, explain that this configured Teams booking needs an invitation recipient; offer staff assistance." if calendar.get('teams_enabled') is True else "- If the caller declines an invitation, omit attendee_emails and set booking_confirmed=true for an appointment-only booking."),
            "- Multiple distinct appointments may be created in one call. Obtain separate agreement for each; never create a second event to change an existing booking. Call microsoft_calendar with action=reschedule_event to change its time; never delete first for a time-only change. Confirm the new details and pass booking_confirmed=true. Attendees are preserved.",
            "- To cancel, read back the selected current-call booking and obtain agreement, then, only if the business instructions permit cancellation, call microsoft_calendar with action=delete_event and cancellation_confirmed=true. Use NO event_id for the most recently selected booking; use its returned event_id for another booking from this call. Never guess an ID. Obtain agreement to notify invitees when applicable. After verified cancellation (reservation_status=cancelled), a caller-confirmed replacement may be created with replacement_confirmed=true and booking_confirmed=true; recheck availability and confirm attendee emails first. Do not create a replacement after an uncertain cancellation. If the caller reports deleting the event externally, use this confirmed cancellation action to verify its absence before replacing it. Later-call bookings, untracked bookings and recurring series require staff assistance; a spoken name/email/event_id does not authorize changes.",
            "- After a timeout or mutation_uncertain, retry identical arguments to reconcile. Never promise success or create another booking with changed details while the first outcome is uncertain.",
            "- Event creation/update/cancellation does not prove mailbox delivery or attendee acceptance. Use the structured result and do not claim either. Only teams_join_url is a Teams join link; link/outlook_web_link opens the Outlook event. Never claim Teams readiness when meeting_status is pending. No automatic staff attendees are added.",
            "- If cancellation succeeded but replacement creation failed, say the original is cancelled and no replacement is confirmed. Do not repeat an earlier booking success in the final recap. Keep internal acknowledgements, field names and errors out of speech.",
            f"- Microsoft Teams meeting creation is {'enabled' if calendar.get('teams_enabled') is True else 'disabled'} by the operator; callers cannot override this setting.",
            f"- Working-hours and booking-horizon enforcement is {'enabled' if calendar.get('enforce_booking_limits') is True else 'disabled'} by the operator. Suggestions still use configured hours; exact availability checks use the booking policy.",
            f"- Caller invitations are {'enabled' if calendar.get('invitations_enabled') is True else 'disabled'} by the operator.",
        ]
        for key in selected:
            account = accounts.get(key) or {}
            timezone_name = account.get("timezone") or calendar.get("timezone") or "UTC"
            try:
                now = datetime.now(ZoneInfo(timezone_name))
                lines.append(f"- Calendar account `{key}`: timezone `{timezone_name}`, local date/time `{now.isoformat(timespec='seconds')}`.")
            except (ZoneInfoNotFoundError, ValueError):
                lines.append(f"- Calendar account `{key}` has an invalid timezone; request operator correction before booking.")
        sections.append("\n".join(lines))

    if "live_agent_transfer" in allowed:
        live_agent_lines = _build_live_agent_lines(config)
        if live_agent_lines:
            lines = [
                "Configured live agents:",
                *live_agent_lines,
            ]
            lines.append("- Use `live_agent_transfer.target` with one of the listed extensions, names, or aliases.")
            sections.append("\n".join(lines))
        else:
            sections.append(
                "\n".join(
                    [
                        "Configured live agents:",
                        "- None configured. Do not call `live_agent_transfer` unless a live agent is configured.",
                    ]
                )
            )

    if "check_extension_status" in allowed:
        check_lines = _build_check_extension_status_lines(config)
        if check_lines:
            sections.append(
                "\n".join(
                    [
                        "Configured extensions allowed for `check_extension_status`:",
                        *check_lines,
                        "- Only query the listed configured extensions or transfer-destination extension targets.",
                    ]
                )
            )
        else:
            sections.append(
                "\n".join(
                    [
                        "Configured extensions allowed for `check_extension_status`:",
                        "- None configured. Do not call `check_extension_status` unless a live agent or transfer destination is configured.",
                    ]
                )
            )

    if "hangup_call" in allowed:
        from src.tools.telephony.completion import effective_completion
        completion = effective_completion(config) if completion_eligible else {"default_next_step": "hangup", "destination_keys": []}
        routes = ((config.get("tools") or {}).get("transfer") or {}).get("destinations") or {}
        lines = ["Conversation completion:",
            f"- Call hangup_call once with farewell_message. Omitted next_step uses `{completion['default_next_step']}`.",
            "- Explicit next_step=hangup disconnects after farewell. Use it if the caller declines an offer or asks to disconnect."]
        for key in completion["destination_keys"]:
            route = routes[key]
            lines.append(f"- next_step `{key}`: {route.get('description') or key}. " +
                         ("Offer first; select only after explicit caller acceptance." if route.get("requires_opt_in") else "Approved destination."))
        sections.append("\n".join(lines))

    if "blind_transfer" in allowed:
        transfer_lines = _build_transfer_destination_lines(config)
        if transfer_lines:
            sections.append(
                "\n".join(
                    [
                        "Configured blind-transfer destinations:",
                        *transfer_lines,
                        "- Use the exact destination key with `blind_transfer.destination` whenever possible.",
                    ]
                )
            )
        else:
            sections.append(
                "\n".join(
                    [
                        "Configured blind-transfer destinations:",
                        "- None configured. Do not call `blind_transfer` unless destinations are configured.",
                    ]
                )
            )

    if "attended_transfer" in allowed:
        attended_lines = _build_attended_destination_lines(config)
        if attended_lines:
            sections.append(
                "\n".join(
                    [
                        "Configured attended-transfer destinations:",
                        *attended_lines,
                        "- Use the exact destination key with `attended_transfer.destination`.",
                    ]
                )
            )
        else:
            sections.append(
                "\n".join(
                    [
                        "Configured attended-transfer destinations:",
                        "- None configured. Do not call `attended_transfer` unless an attended-enabled extension destination exists.",
                    ]
                )
            )

    if "set_call_disposition" in allowed:
        tools_cfg = (config or {}).get("tools") if isinstance(config, dict) else {}
        vicidial_cfg = (tools_cfg or {}).get("vicidial") or {}
        dispositions = (vicidial_cfg or {}).get("dispositions") or {}
        if isinstance(dispositions, dict) and dispositions:
            disposition_lines = [
                f"- `{str(name).strip()}` (VICIdial status `{str(status).strip()}`)"
                for name, status in dispositions.items()
                if str(name or "").strip() and str(status or "").strip()
            ]
            if disposition_lines:
                lines = [
                    "Configured VICIdial dispositions:",
                    *disposition_lines,
                    "- Use only one of these exact names with `set_call_disposition.disposition`.",
                    "- A do-not-call, DNC, stop-calling, or remove-my-number request is a compliance request. If `dnc` is listed, call `set_call_disposition` with `disposition` set to `dnc` immediately; do not refuse or merely acknowledge it.",
                    "- On this VICIdial-owned call, a request to be called back must create a native VICIdial callback: collect and confirm the callback date, time, and timezone, then call `set_call_disposition` with `disposition` set to `callback` and include `callback_datetime`.",
                    *_build_vicidial_callback_clock_lines(vicidial_cfg),
                    "- Do not use a calendar, appointment, or scheduling tool as a substitute for the VICIdial callback, even if one is available. Create a separate calendar appointment only when the caller explicitly requests one in addition to the callback.",
                    "- `set_call_disposition` does not end the call. Call `hangup_call` as well only when the caller asks to end or the conversation is complete.",
                ]
                sections.append("\n".join(lines))

    if "leave_voicemail" in allowed:
        tools_cfg = (config or {}).get("tools") if isinstance(config, dict) else {}
        voicemail_cfg = (tools_cfg or {}).get("leave_voicemail") or {}
        from src.tools.telephony.voicemail import VoicemailTool

        mailbox_key, extension = VoicemailTool.resolve_mailbox(voicemail_cfg or {})
        if extension:
            sections.append(
                "\n".join(
                    [
                        "Configured voicemail target:",
                        f"- `leave_voicemail` routes to voicemail box `{extension}` (mailbox `{mailbox_key}`).",
                    ]
                )
            )

    return "\n\n".join(section for section in sections if str(section or "").strip())
