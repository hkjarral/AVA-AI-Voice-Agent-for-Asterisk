"""
Low-level Google Calendar API client for the Asterisk AI Voice Agent.

Provides GCalendar class for listing, getting, creating, and deleting events
using service account credentials. Used by the google_calendar tool (gcal_tool).
"""

import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog
from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = structlog.get_logger(__name__)


def _get_timezone(config_tz: str = "") -> str:
    """Resolve timezone: config value first, then GOOGLE_CALENDAR_TZ, TZ, system local, UTC."""
    tz = (config_tz or "").strip()
    if not tz:
        tz = os.environ.get("GOOGLE_CALENDAR_TZ", "").strip()
    if not tz:
        tz = os.environ.get("TZ", "").strip()
    if tz:
        return tz
    try:
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz is not None and getattr(local_tz, "key", None):
            return local_tz.key
    except Exception as exc:
        logger.debug("Could not resolve system timezone", error=str(exc))
    return "UTC"


class GoogleCalendarApiError(Exception):
    """Raised by GCalendar methods when a Google Calendar API call fails at runtime
    (revoked share, expired DWD authorization, transient API outage, network error, etc.).

    Distinct from setup-time misconfiguration, which the /verify endpoint catches.
    Callers in gcal_tool.py MUST map this to a structured error response — never
    silently treat as "no events" / "no availability". Conflating runtime failure
    with business-level emptiness was a real correctness regression introduced by
    the structured get_free_slots response (where empty intervals → reason=
    'no_open_windows' or 'fully_booked'). With this exception, runtime failures
    fail closed instead of looking like business outcomes.
    """

    def __init__(self, message: str, calendar_id: str = "", original: Optional[Exception] = None):
        super().__init__(message)
        self.calendar_id = calendar_id
        self.original = original


class GCalendar:
    def __init__(
        self,
        credentials_path: str = "",
        calendar_id: str = "",
        timezone: str = "",
        subject: str = "",
    ):
        """
        Initializes the connection to the Google Calendar API.
        Config params take precedence; falls back to env vars.

        ``subject`` enables Google Workspace Domain-Wide Delegation: when set,
        the service-account credentials are wrapped with ``with_subject(subject)``
        and the SA impersonates that user for all API calls. This requires the
        SA's OAuth client_id to be authorized at admin.google.com → Security →
        Access and data control → API controls → Domain-wide delegation, with
        scope ``https://www.googleapis.com/auth/calendar``. Without that admin
        consent, with_subject() succeeds at construction but the first token
        refresh / API call returns 401 unauthorized_client.

        DWD is the recommended path when an organization's external-sharing
        policy blocks "share calendar with service-account email" — it lets the
        SA act AS a real user (e.g. user@yourdomain.com) and read/write their
        calendars natively.
        """
        logger.debug("Initializing GCalendar instance")
        self.calendar_id = (calendar_id or "").strip() or os.environ.get("GOOGLE_CALENDAR_ID", "primary")
        self.timezone = timezone
        self.subject = (subject or "").strip()
        self.scopes = ["https://www.googleapis.com/auth/calendar"]
        self.service = None
        self.creds = None
        self._lock = threading.Lock()

        key_path = (credentials_path or "").strip() or os.environ.get("GOOGLE_CALENDAR_CREDENTIALS")
        logger.debug("Using credentials path", key_path=key_path, subject=self.subject or None)

        if not key_path or not os.path.exists(key_path):
            logger.error(
                "GOOGLE_CALENDAR_CREDENTIALS file not found or env var not set",
                key_path=key_path,
            )
            return

        try:
            logger.debug("Attempting to load service account credentials")
            base_creds = service_account.Credentials.from_service_account_file(
                key_path, scopes=self.scopes
            )
            # Apply DWD impersonation if configured. Wrapping happens at
            # construction; the actual unauthorized_client failure (if DWD
            # isn't set up at admin.google.com) only surfaces on the first
            # token mint, which happens when build()/.execute() hit the API.
            self.creds = base_creds.with_subject(self.subject) if self.subject else base_creds
            self.service = build("calendar", "v3", credentials=self.creds)
            logger.info(
                "Successfully connected to Google Calendar",
                calendar_id=self.calendar_id,
                impersonating=self.subject or None,
            )
        except Exception as e:
            logger.error(
                "Failed to authenticate Google Calendar",
                error=str(e),
                subject=self.subject or None,
                exc_info=True,
            )

    def list_events(self, time_min: str, time_max: str) -> List[Dict[str, Any]]:
        """
        Retrieves all events within a specific time range.
        time_min and time_max must be ISO 8601 formatted strings.

        Raises ``GoogleCalendarApiError`` on any failure (uninitialized service,
        revoked share, expired DWD authorization, network/API error). Callers
        in gcal_tool.py catch this and return structured error responses to
        the LLM. We do NOT swallow as ``[]`` — that would conflate runtime
        failure with business-level emptiness in the new structured
        get_free_slots response.
        """
        logger.debug("list_events called", time_min=time_min, time_max=time_max)
        if not self.service:
            logger.error("Calendar service is not initialized. Cannot list events.")
            raise GoogleCalendarApiError(
                "Google Calendar service is not initialized — credentials may be invalid or DWD may be misconfigured.",
                calendar_id=self.calendar_id,
            )

        try:
            logger.debug("Sending request to Google Calendar API (events().list)")
            items = []
            page_token = None
            while True:
                req = self.service.events().list(
                    calendarId=self.calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=2500,
                    pageToken=page_token,
                )
                with self._lock:
                    events_result = req.execute()
                items.extend(events_result.get("items", []))
                page_token = events_result.get("nextPageToken")
                if not page_token:
                    break
            logger.info("Successfully fetched events from Google Calendar", count=len(items))
            return items
        except GoogleCalendarApiError:
            # Already typed; re-raise unchanged
            raise
        except Exception as e:
            logger.error(
                "Error fetching events from Google API",
                error=str(e),
                exc_info=True,
            )
            raise GoogleCalendarApiError(
                f"Google Calendar API error fetching events: {e}",
                calendar_id=self.calendar_id,
                original=e,
            ) from e

    def freebusy_query(self, time_min: str, time_max: str) -> List[tuple]:
        """
        Query Google's native freebusy API for busy intervals on this calendar.

        Returns a list of (start_iso, end_iso) tuples covering busy periods.
        Used by gcal_tool.get_free_slots when no free_prefix is configured —
        operators who don't want to seed "Open" availability events get a
        sensible default by leveraging Google's own busy/free knowledge.

        Raises ``GoogleCalendarApiError`` on failure, mirroring list_events.
        """
        logger.debug("freebusy_query called", time_min=time_min, time_max=time_max)
        if not self.service:
            logger.error("Calendar service is not initialized. Cannot query freebusy.")
            raise GoogleCalendarApiError(
                "Google Calendar service is not initialized — credentials may be invalid or DWD may be misconfigured.",
                calendar_id=self.calendar_id,
            )

        try:
            body = {
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": self.calendar_id}],
            }
            req = self.service.freebusy().query(body=body)
            with self._lock:
                result = req.execute()
            cal_block = (result.get("calendars") or {}).get(self.calendar_id, {})
            errors = cal_block.get("errors")
            if errors:
                # Google returns 200 OK with an `errors` list when access is denied
                # to a calendar — surface explicitly rather than silently returning [].
                raise GoogleCalendarApiError(
                    f"Google Calendar freebusy returned errors: {errors}",
                    calendar_id=self.calendar_id,
                )
            busy = cal_block.get("busy", []) or []
            # Fail closed on malformed intervals — silently dropping a busy
            # entry that's missing start/end would surface bookable slots
            # inside an actually-busy window, which is the same correctness
            # bug we fixed at the gcal_tool layer for parse failures. If
            # Google ever returns a malformed interval, treat it as a
            # signal that the response itself is suspect and refuse to
            # return partial availability.
            out: list[tuple] = []
            for b in busy:
                if not (b.get("start") and b.get("end")):
                    raise GoogleCalendarApiError(
                        f"Google Calendar freebusy returned malformed interval "
                        f"(missing start/end): {b}. Refusing to return partial "
                        f"availability.",
                        calendar_id=self.calendar_id,
                    )
                out.append((b["start"], b["end"]))
            logger.info("Successfully fetched freebusy from Google Calendar", busy_count=len(out))
            return out
        except GoogleCalendarApiError:
            raise
        except Exception as e:
            logger.error(
                "Error fetching freebusy from Google API",
                error=str(e),
                exc_info=True,
            )
            raise GoogleCalendarApiError(
                f"Google Calendar API error fetching freebusy: {e}",
                calendar_id=self.calendar_id,
                original=e,
            ) from e

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns all details of a specific event by its ID.
        """
        logger.debug("get_event called", event_id=event_id)
        if not self.service:
            logger.error("Calendar service is not initialized. Cannot get event.")
            return None

        try:
            logger.debug("Sending request to Google Calendar API (events().get)")
            req = self.service.events().get(
                calendarId=self.calendar_id,
                eventId=event_id,
            )
            with self._lock:
                event = req.execute()

            logger.info("Successfully fetched event details", event_id=event_id)
            return event
        except Exception as e:
            # Mirror delete_event: 404 → return None so the tool can surface
            # "event not found" naturally; anything else (auth, forbidden,
            # 5xx) → raise GoogleCalendarApiError so the gcal_tool wrapper
            # can map to a specific error_code and the SCHEDULING-block
            # recovery rules can fire on real failures. Without this every
            # API failure looked like "event not found" and masked auth
            # errors that need different operator action.
            status = getattr(getattr(e, "resp", None), "status", None)
            if status == 404:
                logger.warning(
                    "Event not found in Google Calendar (404)",
                    event_id=event_id,
                    error=str(e),
                )
                return None
            logger.error(
                "Error fetching event from Google API",
                event_id=event_id,
                error=str(e),
                status=status,
                exc_info=True,
            )
            raise GoogleCalendarApiError(
                f"Google Calendar API error fetching event: {e}",
                calendar_id=self.calendar_id,
                original=e,
            ) from e

    def delete_event(self, event_id: str) -> bool:
        """
        Deletes a calendar event by its ID.
        Returns True on success, False on failure.
        """
        logger.debug("delete_event called", event_id=event_id)
        if not self.service:
            logger.error("Calendar service is not initialized. Cannot delete event.")
            return False

        try:
            logger.debug("Sending request to Google Calendar API (events().delete)")
            req = self.service.events().delete(
                calendarId=self.calendar_id,
                eventId=event_id,
            )
            with self._lock:
                req.execute()

            logger.info("Event successfully deleted from Google Calendar", event_id=event_id)
            return True
        except Exception as e:
            # 404 → return False so the gcal_tool fallback ("delete succeeded
            # via tracked id") path can trigger. Anything else (403, 401,
            # 5xx, network) → raise so the tool surfaces the real error to
            # the LLM. Without this distinction every API failure looked
            # like "not found" and would trigger the fallback even when the
            # real cause was permissions or auth, masking real bugs.
            status = getattr(getattr(e, "resp", None), "status", None)
            if status == 404:
                logger.warning(
                    "Event not found during delete (Google returned 404)",
                    event_id=event_id,
                    error=str(e),
                )
                return False
            logger.error(
                "Error deleting event via Google API",
                event_id=event_id,
                error=str(e),
                status=status,
                exc_info=True,
            )
            raise GoogleCalendarApiError(
                f"Google Calendar API error deleting event: {e}",
                calendar_id=self.calendar_id,
                original=e,
            ) from e

    def create_event(
        self,
        summary: str,
        description: str,
        start_datetime: str,
        end_datetime: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Creates a new calendar event.
        start_datetime and end_datetime must be ISO 8601 formatted strings.
        """
        logger.debug(
            "create_event called",
            has_summary=bool(summary),
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )
        if not self.service:
            logger.error("Calendar service is not initialized. Cannot create event.")
            return None

        timezone = _get_timezone(self.timezone)
        event_body = {
            "summary": summary,
            "description": description,
            "start": {
                "dateTime": start_datetime,
                "timeZone": timezone,
            },
            "end": {
                "dateTime": end_datetime,
                "timeZone": timezone,
            },
        }

        logger.debug(
            "Prepared event payload for Google API",
            summary_present=bool(summary),
            description_len=len(description or ""),
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            timezone=timezone,
        )

        try:
            logger.debug("Sending request to Google Calendar API (events().insert)")
            req = self.service.events().insert(
                calendarId=self.calendar_id,
                body=event_body,
            )
            with self._lock:
                event = req.execute()

            logger.info(
                "Event successfully created in Google Calendar",
                event_id=event.get("id"),
            )
            return event
        except Exception as e:
            # Preserve Google's error detail — caller surfaces it to the LLM
            # so the SCHEDULING-block recovery rules ("forbidden", "403",
            # "not configured", etc.) can actually fire on the real error
            # text. CodeRabbit major finding: previously every API failure
            # collapsed to a generic "Failed to create event." which never
            # matched any of the recovery substrings.
            logger.error(
                "Error creating event via Google API",
                error=str(e),
                exc_info=True,
            )
            raise GoogleCalendarApiError(
                f"Google Calendar API error creating event: {e}",
                calendar_id=self.calendar_id,
                original=e,
            ) from e

