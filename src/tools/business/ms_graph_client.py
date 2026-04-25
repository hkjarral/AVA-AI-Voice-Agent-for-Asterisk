"""Low-level Microsoft Graph Calendar client for the voice-agent tools."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.tools.business._calendar_utils import graph_datetime


# MSAL Python's reserved-scope check rejects ONLY `openid`, `profile`,
# `offline_access` (it auto-adds those itself). `User.Read` is a regular
# Graph delegated permission and must be requested explicitly — without it
# the issued access token cannot call `/me`, which the device-flow worker
# uses to confirm the signed-in identity (Authorization_RequestDenied 403).
MS_CALENDAR_SCOPES = ["User.Read", "Calendars.ReadWrite"]
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class MicrosoftGraphApiError(Exception):
    """Typed runtime error from Microsoft Graph or MSAL token refresh."""

    def __init__(
        self,
        message: str,
        error_code: str = "microsoft_graph_error",
        status: int | None = None,
        payload: Any = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.status = status
        self.payload = payload


@dataclass(frozen=True)
class MicrosoftAccountConfig:
    tenant_id: str
    client_id: str
    token_cache_path: str
    user_principal_name: str
    calendar_id: str
    timezone: str = "UTC"


def _require_msal():
    try:
        import msal  # type: ignore
    except ImportError as exc:
        raise MicrosoftGraphApiError(
            "Microsoft Calendar dependencies are not installed. Install msal.",
            error_code="msal_not_installed",
        ) from exc
    return msal


def _require_portalocker():
    try:
        import portalocker  # type: ignore
    except ImportError as exc:
        raise MicrosoftGraphApiError(
            "Microsoft Calendar token-cache locking dependency is not installed. Install portalocker.",
            error_code="portalocker_not_installed",
        ) from exc
    return portalocker


class MicrosoftGraphClient:
    def __init__(self, account: MicrosoftAccountConfig, timeout: int = 20):
        self.account = account
        self.timeout = timeout
        self.authority = f"https://login.microsoftonline.com/{account.tenant_id}"
        self._msal = _require_msal()
        self._portalocker = _require_portalocker()

    def _load_cache_locked(self):
        cache = self._msal.SerializableTokenCache()
        if self.account.token_cache_path and os.path.exists(self.account.token_cache_path):
            try:
                with open(self.account.token_cache_path, "r", encoding="utf-8") as handle:
                    cache.deserialize(handle.read())
            except Exception as exc:
                raise MicrosoftGraphApiError(
                    f"Could not read Microsoft token cache: {exc}",
                    error_code="token_cache_unreadable",
                ) from exc
        return cache

    def _persist_cache_locked(self, cache) -> None:
        if not cache.has_state_changed:
            return
        path = self.account.token_cache_path
        if not path:
            raise MicrosoftGraphApiError("Missing token cache path.", error_code="missing_token_cache_path")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(cache.serialize())
        # Owner-only (0o600). Both admin_ui and ai_engine run as uid 1000 in
        # the production containers, so they share owner-rw access; no need
        # for group-readable bits.
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def acquire_token(self) -> str:
        cache_path = self.account.token_cache_path
        if not cache_path:
            raise MicrosoftGraphApiError("Missing token cache path.", error_code="missing_token_cache_path")
        lock_path = f"{cache_path}.lock"
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with self._portalocker.Lock(lock_path, timeout=10):
            cache = self._load_cache_locked()
            app = self._msal.PublicClientApplication(
                self.account.client_id,
                authority=self.authority,
                token_cache=cache,
            )
            accounts = app.get_accounts(username=self.account.user_principal_name) or app.get_accounts()
            if not accounts:
                raise MicrosoftGraphApiError(
                    "Microsoft Calendar reconnect required. No signed-in account exists in the token cache.",
                    error_code="auth_expired",
                    status=401,
                )
            result = app.acquire_token_silent(MS_CALENDAR_SCOPES, account=accounts[0])
            self._persist_cache_locked(cache)
        if not result or "access_token" not in result:
            error = (result or {}).get("error") if isinstance(result, dict) else None
            desc = (result or {}).get("error_description") if isinstance(result, dict) else None
            code = "auth_expired" if error in {"invalid_grant", "interaction_required"} else "auth_failed"
            raise MicrosoftGraphApiError(
                desc or "Microsoft Calendar reconnect required.",
                error_code=code,
                status=401,
                payload=result,
            )
        return result["access_token"]

    def _request(
        self,
        method: str,
        path_or_url: str,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> Any:
        token = self.acquire_token()
        if path_or_url.startswith("https://"):
            url = path_or_url
        else:
            url = f"{GRAPH_BASE_URL}{path_or_url}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Prefer": 'outlook.timezone="UTC"',
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw}
            raise self._map_http_error(exc.code, payload) from exc
        except MicrosoftGraphApiError:
            raise
        except Exception as exc:
            raise MicrosoftGraphApiError(
                f"Microsoft Graph request failed: {exc}",
                error_code="graph_unavailable",
            ) from exc

    def _map_http_error(self, status: int, payload: Any) -> MicrosoftGraphApiError:
        graph_error = payload.get("error") if isinstance(payload, dict) else {}
        graph_code = (graph_error or {}).get("code") or ""
        graph_message = (graph_error or {}).get("message") or f"Microsoft Graph HTTP {status}"
        lowered = f"{graph_code} {graph_message}".lower()
        if status == 401:
            code = "auth_expired"
        elif status == 403:
            code = "forbidden_calendar"
        elif status == 404:
            code = "calendar_not_found"
        elif status == 429:
            code = "rate_limited"
        elif status >= 500:
            code = "graph_unavailable"
        elif "mailboxnotenabledforrestapi" in lowered:
            code = "mailbox_not_enabled"
        else:
            code = "microsoft_graph_error"
        return MicrosoftGraphApiError(graph_message, error_code=code, status=status, payload=payload)

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/me")

    def list_calendars(self) -> list[dict[str, Any]]:
        calendars: list[dict[str, Any]] = []
        url: str | None = "/me/calendars"
        while url:
            result = self._request("GET", url)
            calendars.extend(result.get("value") or [])
            url = result.get("@odata.nextLink")
        return calendars

    def list_calendar_view(self, time_min_utc: datetime, time_max_utc: datetime) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        url: str | None = f"/me/calendars/{urllib.parse.quote(self.account.calendar_id, safe='')}/calendarView"
        query = {
            "startDateTime": graph_datetime(time_min_utc),
            "endDateTime": graph_datetime(time_max_utc),
            "$top": "1000",
            "$orderby": "start/dateTime",
        }
        while url:
            result = self._request("GET", url, query=query if url.startswith("/") else None)
            events.extend(result.get("value") or [])
            url = result.get("@odata.nextLink")
            query = None
        return events

    def get_schedule(self, time_min_utc: datetime, time_max_utc: datetime) -> list[tuple[str, str]]:
        body = {
            "schedules": [self.account.user_principal_name],
            "startTime": {"dateTime": graph_datetime(time_min_utc), "timeZone": "UTC"},
            "endTime": {"dateTime": graph_datetime(time_max_utc), "timeZone": "UTC"},
            "availabilityViewInterval": 15,
        }
        result = self._request("POST", "/me/calendar/getSchedule", body=body)
        values = result.get("value") or []
        if not values:
            return []
        schedule = values[0]
        error = schedule.get("error")
        if error:
            raise MicrosoftGraphApiError(
                error.get("message") or "Microsoft getSchedule failed.",
                error_code="get_schedule_failed",
                payload=error,
            )
        busy: list[tuple[str, str]] = []
        for item in schedule.get("scheduleItems") or []:
            status = (item.get("status") or "").lower()
            if status == "free":
                continue
            start = (item.get("start") or {}).get("dateTime")
            end = (item.get("end") or {}).get("dateTime")
            if not start or not end:
                raise MicrosoftGraphApiError(
                    f"Microsoft getSchedule returned malformed interval: {item}",
                    error_code="malformed_schedule_interval",
                )
            busy.append((start, end))
        return busy

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        try:
            return self._request(
                "GET",
                f"/me/calendars/{urllib.parse.quote(self.account.calendar_id, safe='')}/events/{urllib.parse.quote(event_id, safe='')}",
            )
        except MicrosoftGraphApiError as exc:
            if exc.status == 404:
                return None
            raise

    def create_event(
        self,
        summary: str,
        description: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> dict[str, Any]:
        body = {
            "subject": summary,
            "body": {"contentType": "text", "content": description or ""},
            "start": {"dateTime": graph_datetime(start_utc), "timeZone": "UTC"},
            "end": {"dateTime": graph_datetime(end_utc), "timeZone": "UTC"},
        }
        return self._request(
            "POST",
            f"/me/calendars/{urllib.parse.quote(self.account.calendar_id, safe='')}/events",
            body=body,
        )

    def delete_event(self, event_id: str) -> bool:
        try:
            self._request(
                "DELETE",
                f"/me/calendars/{urllib.parse.quote(self.account.calendar_id, safe='')}/events/{urllib.parse.quote(event_id, safe='')}",
            )
            return True
        except MicrosoftGraphApiError as exc:
            if exc.status == 404:
                return False
            raise
