"""Validation and plain-text invitation rendering for Microsoft bookings."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.tools.business._calendar_utils import (
    parse_iso_datetime,
    to_utc,
    working_hours_mask,
)

DEFAULT_SUBJECT = "{{meeting_purpose}}"
DEFAULT_BODY = (
    "Hello {{caller_name}},\n\n"
    "Your appointment is scheduled for {{appointment_date}}, "
    "{{start_time}}–{{end_time}} ({{timezone_label}}).\n\n"
    "Purpose: {{meeting_purpose}}\n{{confirmed_notes}}\n"
    "Location: {{location}}\n\n{{rescheduling_instructions}}\n"
    "{{business_name}}\n{{contact_details}}"
)
CALLER_FIELDS = ("caller_name", "meeting_purpose", "confirmed_notes")
OPERATOR_FIELDS = (
    "location",
    "business_name",
    "contact_details",
    "rescheduling_instructions",
)
TEMPLATE_FIELDS = set(
    CALLER_FIELDS
    + OPERATOR_FIELDS
    + (
        "appointment_date",
        "start_time",
        "end_time",
        "timezone_label",
    )
)


class BookingValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def strict_datetime(value: str, timezone_name: str) -> datetime:
    try:
        zone = ZoneInfo(timezone_name)
        if not isinstance(value, str) or not re.match(
            r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value
        ):
            raise ValueError("Use a complete ISO date and time.")
        parsed = parse_iso_datetime(value)
        if parsed.second or parsed.microsecond:
            raise ValueError("Bookings use whole-minute times.")
        if parsed.tzinfo is not None:
            return parsed.astimezone(zone)
        local = parsed.replace(tzinfo=zone)
        if to_utc(local).astimezone(zone).replace(tzinfo=None) != parsed:
            raise ValueError("This local time does not exist; confirm another time.")
        if local.utcoffset() != local.replace(fold=1).utcoffset():
            raise ValueError("This local time is ambiguous; confirm its UTC offset.")
        return local
    except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
        raise BookingValidationError("invalid_datetime", str(exc)) from exc


def booking_limits_enabled(config: dict) -> bool:
    enabled = config.get("enforce_booking_limits", False)
    if type(enabled) is not bool:
        raise BookingValidationError(
            "invalid_configuration", "enforce_booking_limits must be a boolean."
        )
    return enabled


def booking_interval(
    parameters: dict, config: dict, timezone_name: str, *, enforce_future: bool = True
):
    start = strict_datetime(parameters.get("start_datetime"), timezone_name)
    end = strict_datetime(parameters.get("end_datetime"), timezone_name)
    minutes = (to_utc(end) - to_utc(start)).total_seconds() / 60
    if minutes <= 0:
        raise BookingValidationError(
            "invalid_duration", "end_datetime must be after start_datetime."
        )
    try:
        maximum = int(config.get("max_event_duration_minutes", 240))
        horizon = (
            int(config.get("booking_horizon_days", 365))
            if booking_limits_enabled(config)
            else 0
        )
    except (ValueError, TypeError) as exc:
        raise BookingValidationError(
            "invalid_configuration",
            "Invalid booking limits in Microsoft Calendar settings.",
        ) from exc
    if maximum > 0 and minutes > maximum:
        raise BookingValidationError(
            "duration_too_long",
            f"Duration exceeds the allowed maximum of {maximum} minutes.",
        )
    if enforce_future:
        now = utc_now()
        if to_utc(start) <= now:
            raise BookingValidationError(
                "past_booking", "Confirm a future date and time before booking."
            )
        if horizon > 0 and to_utc(end) > now + timedelta(days=horizon):
            raise BookingValidationError(
                "booking_horizon_exceeded",
                "The requested booking is beyond the configured booking horizon.",
            )
    return start, end, int(minutes)


def working_policy(config: dict, *, for_booking: bool = False):
    if for_booking and not booking_limits_enabled(config):
        return 0, 24, set(range(7))
    try:
        start = int(config.get("working_hours_start", 9))
        end = int(config.get("working_hours_end", 17))
        raw_days = config.get("working_days", [0, 1, 2, 3, 4])
        if (
            not isinstance(raw_days, list)
            or not raw_days
            or any(type(day) is not int or not 0 <= day <= 6 for day in raw_days)
        ):
            raise ValueError()
        if not 0 <= start < end <= 24:
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise BookingValidationError(
            "invalid_configuration",
            "Configure valid working hours and at least one working day.",
        ) from exc
    return start, end, set(raw_days)


def within_working_hours(
    start: datetime, end: datetime, timezone_name: str, config: dict
) -> bool:
    if not booking_limits_enabled(config):
        return True
    work_start, work_end, days = working_policy(config, for_booking=True)
    return any(
        a <= start and end <= b
        for a, b in working_hours_mask(
            start, end, timezone_name, work_start, work_end, days
        )
    )


def validated_attendees(parameters: dict, config: dict) -> list[str]:
    raw = parameters.get("attendee_emails", [])
    if not isinstance(raw, list) or len(raw) > 10:
        raise BookingValidationError(
            "invalid_attendee_email",
            "Provide a list of at most ten confirmed email addresses.",
        )
    emails = []
    for email in raw:
        if not isinstance(email, str):
            raise BookingValidationError(
                "invalid_attendee_email", "Each attendee must be an email address."
            )
        email = email.strip()
        if len(email) > 254 or not re.fullmatch(
            r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+",
            email,
        ):
            raise BookingValidationError(
                "invalid_attendee_email",
                "Ask the caller to spell a valid email address and confirm it by reading it back.",
            )
        if len(email.split("@")[0]) > 64 or any(
            len(label) > 63 for label in email.split("@")[1].split(".")
        ):
            raise BookingValidationError(
                "invalid_attendee_email",
                "Email address exceeds supported length limits.",
            )
        if email.casefold() not in {item.casefold() for item in emails}:
            emails.append(email)
    if emails:
        if config.get("invitations_enabled") is not True:
            raise BookingValidationError(
                "invitations_disabled",
                "Invitations are disabled by the operator. Offer an appointment without an invitation.",
            )
        if (
            parameters.get("booking_confirmed") is not True
            or parameters.get("invitation_confirmed") is not True
        ):
            raise BookingValidationError(
                "confirmation_required",
                "Confirm the date, time, duration, attendee addresses, and agreement to create and send invitations first.",
            )
    elif parameters.get("invitation_confirmed") is True:
        raise BookingValidationError(
            "missing_attendee_email",
            "An invitation needs a caller-confirmed attendee email address.",
        )
    if parameters.get("booking_confirmed") is False:
        raise BookingValidationError(
            "confirmation_required", "The caller has not agreed to this booking."
        )
    return emails


def render_template(template: str, values: dict[str, str], *, limit: int) -> str:
    if not isinstance(template, str) or len(template) > limit:
        raise BookingValidationError(
            "invalid_template", "Invitation template exceeds its size limit."
        )

    def replace(match):
        key = match.group(1).strip()
        if key not in TEMPLATE_FIELDS:
            raise BookingValidationError(
                "invalid_template", f"Unsupported invitation placeholder: {key}."
            )
        return values.get(key, "")

    # Replace only the operator template once. Caller text is never reinterpreted.
    rendered = re.sub(r"\{\{([^{}]*)\}\}", replace, template)
    if "{{" in re.sub(r"\{\{([^{}]*)\}\}", "", template) or "}}" in re.sub(
        r"\{\{([^{}]*)\}\}", "", template
    ):
        raise BookingValidationError(
            "invalid_template", "Malformed invitation placeholder."
        )
    if len(rendered) > limit:
        raise BookingValidationError(
            "invalid_template", "Rendered invitation exceeds its size limit."
        )
    return rendered.strip()


def invitation_content(
    parameters: dict, config: dict, start: datetime, end: datetime, timezone_name: str
):
    values = {}
    for key in CALLER_FIELDS + OPERATOR_FIELDS:
        source = parameters if key in CALLER_FIELDS else config
        value = source.get(key, "") or ""
        if not isinstance(value, str) or len(value) > 2000:
            raise BookingValidationError(
                "invalid_invitation_content", f"Invalid invitation field: {key}."
            )
        values[key] = value.strip()
    values["meeting_purpose"] = values["meeting_purpose"] or parameters.get(
        "summary", ""
    )
    values["confirmed_notes"] = values["confirmed_notes"] or parameters.get(
        "description", ""
    )
    values.update(
        appointment_date=start.strftime("%A, %Y-%m-%d"),
        start_time=start.strftime("%H:%M"),
        end_time=end.strftime("%H:%M"),
        timezone_label=f"{timezone_name} (UTC{start.strftime('%z')})",
    )
    subject = render_template(
        config.get("invitation_subject_template", DEFAULT_SUBJECT), values, limit=255
    )
    body = render_template(
        config.get("invitation_body_template", DEFAULT_BODY), values, limit=8000
    )
    if not subject:
        raise BookingValidationError(
            "invalid_template", "Invitation subject must not be empty."
        )
    return subject, body
