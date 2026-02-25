import os
import threading
from datetime import datetime

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


class GCalendar:
    def __init__(self, credentials_path: str = "", calendar_id: str = "", timezone: str = ""):
        """
        Initializes the connection to the Google Calendar API.
        Config params take precedence; falls back to env vars.
        """
        logger.debug("Initializing GCalendar instance")
        self.calendar_id = (calendar_id or "").strip() or os.environ.get("GOOGLE_CALENDAR_ID", "primary")
        self.timezone = timezone
        self.scopes = ["https://www.googleapis.com/auth/calendar"]
        self.service = None
        self.creds = None
        self._lock = threading.Lock()

        key_path = (credentials_path or "").strip() or os.environ.get("GOOGLE_CALENDAR_CREDENTIALS")
        logger.debug("Using credentials path", key_path=key_path)

        if not key_path or not os.path.exists(key_path):
            logger.error(
                "GOOGLE_CALENDAR_CREDENTIALS file not found or env var not set",
                key_path=key_path,
            )
            return

        try:
            logger.debug("Attempting to load service account credentials")
            self.creds = service_account.Credentials.from_service_account_file(
                key_path, scopes=self.scopes
            )
            self.service = build("calendar", "v3", credentials=self.creds)
            logger.info(
                "Successfully connected to Google Calendar",
                calendar_id=self.calendar_id,
            )
        except Exception as e:
            logger.error(
                "Failed to authenticate Google Calendar",
                error=str(e),
                exc_info=True,
            )

    def list_events(self, time_min, time_max):
        """
        Retrieves all events within a specific time range.
        time_min and time_max must be ISO 8601 formatted strings.
        """
        logger.debug("list_events called", time_min=time_min, time_max=time_max)
        if not self.service:
            logger.error("Calendar service is not initialized. Cannot list events.")
            return []

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
        except Exception as e:
            logger.error(
                "Error fetching events from Google API",
                error=str(e),
                exc_info=True,
            )
            return []

    def get_event(self, event_id):
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
            logger.error(
                "Error fetching event from Google API",
                event_id=event_id,
                error=str(e),
                exc_info=True,
            )
            return None

    def create_event(self, summary, description, start_datetime, end_datetime):
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

        logger.debug("Prepared event payload for Google API", start=start_datetime, end=end_datetime)

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
            logger.error(
                "Error creating event via Google API",
                error=str(e),
                exc_info=True,
            )
            return None

