"""Bounded Calendly transport and conservative v1 appointment classifier."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import aiohttp

ORIGIN = "https://api.calendly.com"
RESOURCE = re.compile(r"^/(users|organizations|event_types|scheduled_events)/[A-Za-z0-9_-]+(?:/invitees/[A-Za-z0-9_-]+)?$")
LOCATIONS = {"physical", "custom", "inbound_call", "google_conference", "microsoft_teams_conference", "zoom_conference"}


class CalendlyError(Exception):
    def __init__(self, code: str, *, uncertain: bool = False, cooldown: int = 0):
        super().__init__(code)
        self.code, self.uncertain, self.cooldown = code, uncertain, cooldown


def public_config(cfg: dict) -> dict:
    """Defensively exclude hand-written credentials even before write validation."""
    allowed = {"uri", "connection_id", "one_to_one_confirmed", "qualified_locations", "name", "duration"}
    if not isinstance(cfg, dict):
        return {}
    result = {key: cfg[key] for key in ("enabled", "selected_appointments") if key in cfg}
    entries = cfg.get("appointments")
    if isinstance(entries, dict):
        result["appointments"] = {key: {k: v for k, v in value.items() if k in allowed} for key, value in entries.items() if isinstance(value, dict)}
    return result


def validate_config(cfg):
    """Reject secrets/paths and unbounded config on every save/load path."""
    if cfg is None:
        return
    if not isinstance(cfg, dict) or set(cfg) - {"enabled", "appointments", "selected_appointments"}:
        raise ValueError("Invalid Calendly configuration; credentials belong in the connection panel")
    if type(cfg.get("enabled", False)) is not bool:
        raise ValueError("Calendly enabled must be boolean")
    entries = cfg.get("appointments", {})
    if not isinstance(entries, dict) or len(entries) > 100:
        raise ValueError("Calendly supports at most 100 selected appointments")
    for key, value in entries.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key) or not isinstance(value, dict):
            raise ValueError("Invalid Calendly appointment key")
        if set(value) - {"uri", "connection_id", "one_to_one_confirmed", "qualified_locations", "name", "duration"}:
            raise ValueError("Unknown Calendly appointment setting")
        try:
            resource_path(value.get("uri", ""), "event_types")
        except CalendlyError:
            raise ValueError("Invalid Calendly appointment resource") from None
        if not isinstance(value.get("connection_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", value["connection_id"]):
            raise ValueError("Invalid Calendly connection identity")
        if type(value.get("one_to_one_confirmed", False)) is not bool:
            raise ValueError("Confirm one-to-one appointment configuration")
        if not isinstance(value.get("name", ""), str) or len(value.get("name", "")) > 200:
            raise ValueError("Invalid Calendly appointment name")
        if not isinstance(value.get("qualified_locations", []), list) or any(v not in LOCATIONS for v in value.get("qualified_locations", [])):
            raise ValueError("Invalid Calendly location qualification")
    selected = cfg.get("selected_appointments")
    if selected is not None and (not isinstance(selected, list) or any(not isinstance(k, str) or k not in entries for k in selected)):
        raise ValueError("Invalid Calendly appointment selection")


def resource_path(uri: str, family: str = "") -> str:
    parsed = urlparse(str(uri))
    if parsed.scheme != "https" or parsed.netloc != "api.calendly.com" or parsed.query or parsed.fragment or not RESOURCE.fullmatch(parsed.path):
        raise CalendlyError("invalid_resource")
    if family and not parsed.path.startswith(f"/{family}/"):
        raise CalendlyError("invalid_resource")
    return parsed.path


def instant(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise CalendlyError("invalid_datetime") from None


def appointment(raw: dict, user: str) -> dict:
    """Return model-safe metadata; an unknown configuration never grants access."""
    reason = ""
    profile = raw.get("profile") or {}
    duration = raw.get("duration")
    if (not raw.get("active") or raw.get("deleted_at") or raw.get("kind") != "solo"
            or raw.get("pooling_type") is not None or raw.get("type") != "StandardEventType"
            or raw.get("booking_method") != "instant" or raw.get("is_paid") is not False
            or profile.get("type") != "User" or profile.get("owner") != user):
        reason = "Requires an active, unpaid, individually owned one-to-one appointment."
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 1 <= duration <= 720 or duration != int(duration):
        reason = "Requires a fixed whole-minute duration."
    if raw.get("duration_options") and raw["duration_options"] != [duration]:
        reason = "Multiple durations are not supported."
    questions = []
    positions: set[int] = set()
    for q in raw.get("custom_questions") or []:
        if not q.get("enabled") or not q.get("required"):
            continue
        name, pos, choices = q.get("name"), q.get("position"), q.get("answer_choices") or []
        if (q.get("type") not in {"string", "text", "phone_number", "single_select"}
                or not isinstance(name, str) or not 1 <= len(name) <= 500
                or type(pos) is not int or pos < 0 or pos in positions
                or not isinstance(choices, list) or len(choices) > 20
                or any(not isinstance(c, str) or not 1 <= len(c) <= 500 for c in choices)
                or (q.get("type") == "single_select" and not choices)):
            reason = "A required question is unsupported or malformed."
        positions.add(pos)
        questions.append({"question": name, "position": pos, "type": q.get("type"), "choices": choices})
    if len(questions) > 10 or len(json.dumps(questions, ensure_ascii=False).encode()) > 6000:
        reason = "More than ten required questions are not supported."
    locations = raw.get("locations") or []
    location: dict[str, Any] | None = None
    summary = "No location configured"
    if len(locations) > 1:
        reason = "Multiple locations are not supported."
    elif locations:
        loc = locations[0]
        kind = loc.get("kind")
        if kind not in LOCATIONS:
            reason = "This location requires Calendly or staff assistance."
        else:
            location = {"kind": kind}
            if kind in {"physical", "custom"}:
                fixed = loc.get("location")
                if not isinstance(fixed, str) or not 1 <= len(fixed) <= 1000:
                    reason = "The location needs a fixed address or description."
                location["location"] = fixed
            if kind == "inbound_call" and not loc.get("phone_number"):
                reason = "The host phone number is missing."
            summary = str(loc.get("location") or loc.get("phone_number") or kind)[:1000]
    uri = raw.get("uri", "")
    resource_path(uri, "event_types")
    return {"uri": uri, "name": str(raw.get("name") or "Appointment")[:200],
            "host": str(profile.get("name") or "")[:200], "duration": duration,
            "location": location, "location_summary": summary, "questions": questions,
            "supported": not reason, "reason": reason, "updated_at": raw.get("updated_at")}


class CalendlyClient:
    def __init__(self, token: str):
        self.token = token

    async def request(self, method: str, path: str, *, params: dict | None = None,
                      body: dict | None = None, timeout: float = 5) -> dict:
        if path not in {"/users/me", "/event_types", "/event_type_available_times", "/scheduled_events", "/invitees"} and not RESOURCE.fullmatch(path.removesuffix("/invitees")):
            raise CalendlyError("invalid_resource")
        if method != "GET" and (method != "POST" or path != "/invitees"):
            raise CalendlyError("unsupported_operation")
        writing = method == "POST"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout, connect=min(3, timeout))) as session:
                async with session.request(method, ORIGIN + path, params=params, json=body,
                                           headers={"Authorization": f"Bearer {self.token}"}, allow_redirects=False) as response:
                    payload = bytearray()
                    # read(n) may return only the currently available fragment.
                    # Drain through EOF under the existing total deadline/cap.
                    async for chunk in response.content.iter_chunked(65536):
                        if len(payload) + len(chunk) > 262144:
                            raise CalendlyError("response_too_large", uncertain=writing)
                        payload.extend(chunk)
                    try:
                        data = json.loads(payload)
                    except (ValueError, UnicodeDecodeError):
                        raise CalendlyError("invalid_response", uncertain=writing) from None
                    if not isinstance(data, dict):
                        raise CalendlyError("invalid_response", uncertain=writing)
                    if response.status != (201 if writing else 200):
                        rejected = response.status in {400, 401, 403, 404, 429} and isinstance(data.get("title"), str)
                        cooldown = 0
                        if response.status == 429:
                            try:
                                cooldown = min(86400, max(1, int(response.headers.get("X-RateLimit-Reset", "60"))))
                            except ValueError:
                                cooldown = 60
                        raise CalendlyError(f"upstream_{response.status}", uncertain=writing and not rejected, cooldown=cooldown)
                    return data
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            # Even connect errors are conservative after admission. Never retry POST.
            raise CalendlyError("network_unavailable", uncertain=writing) from None

    async def pages(self, path: str, params: dict, limit: int = 2) -> list[dict]:
        items: list[dict] = []
        query = {**params, "count": 100}
        seen: set[str] = set()
        for _ in range(limit):
            result = await self.request("GET", path, params=query)
            rows = result.get("collection")
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                raise CalendlyError("invalid_response")
            items.extend(rows)
            page = (result.get("pagination") or {}).get("next_page_token")
            if not page:
                return items
            if not isinstance(page, str) or len(page) > 2000 or page in seen:
                raise CalendlyError("invalid_pagination")
            seen.add(page)
            query["page_token"] = page
        raise CalendlyError("search_incomplete")
