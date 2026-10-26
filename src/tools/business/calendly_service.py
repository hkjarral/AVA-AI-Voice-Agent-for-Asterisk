"""Shared voice/setup booking lifecycle. A journal claim permits one POST only."""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.core.calendly_store import CalendlyStore, digest
from src.tools.business.calendly_client import CalendlyClient, CalendlyError, appointment, instant, resource_path


def email(value: str) -> str:
    value = str(value).strip()
    if len(value) > 254 or not re.fullmatch(r"[^\s@<>\";,]+@[^\s@<>\";,]+\.[^\s@<>\";,]+", value):
        raise CalendlyError("invalid_email")
    local, domain = value.rsplit("@", 1)
    return local + "@" + domain.lower()


def location_label(value: str) -> str:
    return {"google_conference": "Google Meet", "microsoft_teams_conference": "Microsoft Teams",
            "zoom_conference": "Zoom"}.get(value, value)


def view(row: dict, operation: str | None = None) -> dict:
    if operation is not None and row["operation"] != operation:
        return {"status": "error", "code": "existing_request_needs_staff", "message": "Please ask staff to verify the existing request."}
    state = row["state"]
    public = json.loads(row["public"])
    if "location_summary" in public:
        public["location_summary"] = location_label(public["location_summary"])
    return {**public, "status": "success" if state == "confirmed" else "pending" if state in {"submitting", "uncertain", "needs_operator"} else "error",
            "reservation_status": state, "attempt_reference": row["id"],
            "message": {"confirmed": "Appointment booked. Calendly handles its configured confirmation.",
                        "resolved_remote_cancelled": "The appointment was created and has since been cancelled; staff can assist.",
                        "verified_cancelled": "The original appointment was verified cancelled. This request cannot book it again; start a new call for a new appointment.",
                        "rejected": "The booking was rejected. Confirm corrected details before another attempt.",
                        "erased": "The saved scheduling details were erased. This does not cancel an appointment; ask staff to verify in Calendly. Do not book again from this request.",
                        "resolved_externally": "Staff recorded handling this request outside AVA."}.get(state, "Confirmation is pending. Do not book again; ask staff to verify.")}


class ObservedClient:
    """Share observed upstream cooldowns between setup and voice processes."""
    def __init__(self, client, store, connection):
        self.client, self.store, self.connection = client, store, connection
        self.read_retry_used = False

    async def observed(self, method, *args, **kwargs):
        try:
            return await method(*args, **kwargs)
        except CalendlyError as exc:
            if exc.cooldown:
                await asyncio.to_thread(self.store.observe_cooldown, self.connection, exc.cooldown)
            raise

    async def request(self, method, *args, **kwargs):
        try:
            return await self.observed(self.client.request, method, *args, **kwargs)
        except CalendlyError as exc:
            # One safe read retry per operation/client, still inside the native
            # service deadline. Never replay a POST, quota rejection or invalid
            # response, and never reset the shared write admission journal.
            if method != "GET" or exc.code != "network_unavailable" or self.read_retry_used:
                raise
            self.read_retry_used = True
            return await self.observed(self.client.request, method, *args, **kwargs)

    async def pages(self, *args, **kwargs):
        return await self.observed(self.client.pages, *args, **kwargs)


class CalendlyService:
    def __init__(self, store: CalendlyStore, client_factory=CalendlyClient):
        self.store, self.client_factory = store, client_factory

    async def credentials(self, admission=False):
        conn, token = await asyncio.to_thread(self.store.credentials, admission=admission)
        return conn, ObservedClient(self.client_factory(token), self.store, conn)

    async def identity(self, client) -> dict:
        raw = (await client.request("GET", "/users/me")).get("resource") or {}
        resource_path(raw.get("uri", ""), "users")
        resource_path(raw.get("current_organization", ""), "organizations")
        try:
            ZoneInfo(raw.get("timezone", ""))
        except (ValueError, ZoneInfoNotFoundError):
            raise CalendlyError("invalid_host_timezone") from None
        return raw

    async def verify(self, token: str | None = None) -> dict:
        if token:
            if len(token) > 8192 or any(c.isspace() for c in token):
                raise CalendlyError("invalid_token")
            client = self.client_factory(token)
        else:
            try:
                _, client = await self.credentials()
            except CalendlyError as exc:
                if exc.code in {"connection_disabled", "credential_unavailable"}:
                    raise CalendlyError("verification_token_required") from None
                raise
        try:
            person = await self.identity(client)
        except CalendlyError as exc:
            if exc.code == "upstream_403":
                raise CalendlyError("identity_access_denied") from None
            raise
        # Identity success is not proof of appointment or paid write access.
        return {k: person[k] for k in ("uri", "current_organization", "name", "timezone") if k in person}

    async def discovery(self) -> list[dict]:
        conn, client = await self.credentials()
        raw = await client.pages("/event_types", {"user": conn["user"]})
        entries = [appointment(row, conn["user"]) for row in raw]
        return [{**entry, "location_summary": location_label(entry["location_summary"])} for entry in entries]

    def binding(self, cfg: dict, key: str, conn: dict) -> dict:
        entries = cfg.get("appointments") or {}
        selected = cfg.get("selected_appointments")
        if (not cfg.get("enabled") or key not in entries or selected is not None and key not in selected):
            raise CalendlyError("appointment_not_allowed")
        value = entries[key]
        if value.get("connection_id") != conn["id"] or not value.get("one_to_one_confirmed"):
            raise CalendlyError("appointment_setup_incomplete")
        return value

    async def current(self, cfg: dict, key: str, conn: dict, client, *, setup=False) -> dict:
        binding = self.binding(cfg, key, conn)
        data = (await client.request("GET", resource_path(binding.get("uri", ""), "event_types"))).get("resource") or {}
        entry = appointment(data, conn["user"])
        if entry["uri"] != binding["uri"] or not entry["supported"]:
            raise CalendlyError("appointment_unsupported")
        kind = (entry.get("location") or {}).get("kind", "")
        if kind.endswith("_conference") and not setup:
            qualified = (conn.get("qualified_types") or {}).get(entry["uri"])
            if qualified != digest(entry):
                raise CalendlyError("conference_not_qualified")
        return entry

    async def slots(self, cfg: dict, operation: str, key: str, start: str, end: str, tz: str, *, setup=False) -> dict:
        # Authenticated setup can preview times before the operator enables
        # submissions. Voice offers and every booking still require admission.
        conn, client = await self.credentials(admission=not setup)
        try:
            ZoneInfo(tz)
        except (ValueError, ZoneInfoNotFoundError):
            raise CalendlyError("invalid_timezone") from None
        a, b = instant(start), instant(end)
        if a < datetime.now(timezone.utc) or not 0 < (b - a).total_seconds() <= 31 * 86400:
            raise CalendlyError("invalid_search_window")
        entry = await self.current(cfg, key, conn, client, setup=setup)
        result = await client.request("GET", "/event_type_available_times", params={"event_type": entry["uri"], "start_time": a.isoformat(), "end_time": b.isoformat()})
        rows = result.get("collection")
        if not isinstance(rows, list):
            raise CalendlyError("invalid_response")
        output = []
        for row in sorted(rows, key=lambda row: instant(row.get("start_time", ""))):
            when = instant(row.get("start_time", ""))
            if row.get("status") != "available" or row.get("invitees_remaining") != 1 or not a <= when < b:
                continue
            if len(output) >= 10:
                break
            ref = await asyncio.to_thread(self.store.proposal, operation, conn["id"],
                                          {"appointment": entry, "key": key, "start": when.isoformat(), "timezone": tz, "revision": conn["revision"], "setup": setup})
            output.append({"slot_reference": ref, "start_datetime": when.astimezone(ZoneInfo(tz)).isoformat(),
                           "spoken_date": when.astimezone(ZoneInfo(tz)).strftime("%A, %B %d, %Y"),
                           "end_datetime": (when + timedelta(minutes=entry["duration"])).astimezone(ZoneInfo(tz)).isoformat()})
        return {"status": "success", "appointment_key": key, "appointment_name": entry["name"], "duration_minutes": entry["duration"],
                "timezone": tz, "location_summary": location_label(entry["location_summary"]), "questions": entry["questions"],
                "slots": output, "search_start": a.isoformat(), "search_end": b.isoformat(), "slots_truncated": len(rows) > len(output)}

    async def book(self, cfg: dict, operation: str, params: dict, admission_check=None) -> dict:
        async with asyncio.timeout(15):
            return await self._book(cfg, operation, params, admission_check, time.monotonic() + 15)

    async def _book(self, cfg, operation, params, admission_check, deadline):
        conn, client = await self.credentials(admission=True)
        prior = await asyncio.to_thread(self.store.prior, operation)
        if prior:
            return view(prior, operation)
        proposal = await asyncio.to_thread(self.store.read_proposal, params.get("slot_reference", ""), operation, conn["id"])
        if not isinstance(proposal, dict) or not {"key", "appointment", "start", "timezone", "revision"} <= proposal.keys():
            raise CalendlyError("proposal_expired")
        key = proposal["key"]
        entry = await self.current(cfg, key, conn, client, setup=bool(proposal.get("setup")))
        if proposal["revision"] != conn["revision"] or digest(entry) != digest(proposal["appointment"]):
            raise CalendlyError("proposal_changed_reconfirm")
        if proposal.get("setup") and (params.get("booking_confirmed") is not True or params.get("email_confirmed") is not True):
            raise CalendlyError("confirmation_required")
        name = params.get("name", "")
        if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(ord(c) < 32 for c in name):
            raise CalendlyError("invalid_name")
        recipient = email(params.get("email", ""))
        answers = params.get("answers") or []
        if not isinstance(answers, list) or len(answers) != len(entry["questions"]):
            raise CalendlyError("required_answers_missing")
        by_position = {}
        for answer in answers:
            if not isinstance(answer, dict) or type(answer.get("position")) is not int or answer["position"] in by_position:
                raise CalendlyError("invalid_answer")
            by_position[answer["position"]] = answer.get("answer")
        questions = []
        for q in entry["questions"]:
            answer = by_position.get(q["position"])
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 1000:
                raise CalendlyError("invalid_answer")
            if q["type"] == "single_select" and answer not in q["choices"]:
                raise CalendlyError("invalid_answer")
            if q["type"] == "phone_number" and not re.fullmatch(r"\+?[0-9 ()-]{5,30}", answer):
                raise CalendlyError("invalid_answer")
            questions.append({"question": q["question"], "position": q["position"], "answer": answer})
        when = instant(proposal["start"])
        if when <= datetime.now(timezone.utc):
            raise CalendlyError("proposal_expired")
        public = {"appointment_name": entry["name"], "start_datetime": when.astimezone(ZoneInfo(proposal["timezone"])).isoformat(),
                  "end_datetime": (when + timedelta(minutes=entry["duration"])).astimezone(ZoneInfo(proposal["timezone"])).isoformat(),
                  "timezone": proposal["timezone"], "duration_minutes": entry["duration"],
                  "location_summary": location_label(entry["location_summary"])}
        local_start = when.astimezone(ZoneInfo(proposal["timezone"]))
        public["spoken_date"] = local_start.strftime("%A, %B %d, %Y")
        if not proposal.get("setup"):
            # Booleans from the model alone must not skip a canonical readback.
            # Reuse expiring, call/connection-bound proposals; persist only a
            # digest, never another copy of the recipient or spoken spelling.
            fingerprint = digest([params.get("slot_reference"), conn["revision"], entry, name.strip(), recipient, questions])
            reference = params.get("confirmation_reference")
            if not reference:
                reference = await asyncio.to_thread(self.store.proposal, operation, conn["id"], {"confirmation_digest": fingerprint})
                spelling = " ".join({".": "dot", "_": "underscore", "-": "hyphen", "+": "plus", "@": "at"}.get(c, c) for c in recipient)
                return {**public, "status": "confirmation_required", "confirmation_reference": reference,
                        "confirmation_recap": {"name": name.strip(), "email": recipient, "email_spelling": spelling},
                        "message": "Nothing is booked. Use spoken_date for the local weekday/date, never calculate it yourself. Spell the exact email including every dot and symbol, read the complete appointment recap, then ask for approval. Only after the caller approves this recap, call create_booking with the same details, this confirmation_reference and both confirmation flags true. A correction requires a new recap without a confirmation_reference."}
            confirmation = await asyncio.to_thread(self.store.read_proposal, reference, operation, conn["id"])
            if confirmation.get("confirmation_digest") != fingerprint:
                raise CalendlyError("confirmation_changed_reconfirm")
            if params.get("booking_confirmed") is not True or params.get("email_confirmed") is not True:
                raise CalendlyError("confirmation_required")
        payload = {"event_type": entry["uri"], "start_time": when.isoformat(),
                   "invitee": {"name": name.strip(), "email": recipient, "timezone": proposal["timezone"]},
                   "questions_and_answers": questions}
        if entry["location"]:
            payload["location"] = entry["location"]
        # This is still read-only preflight. Never release an uncertain request,
        # infer cancellation from availability/404, or replay the original call.
        # At most two historical duplicates/four exact GETs fit this operation.
        for _ in range(2):
            duplicate = await asyncio.to_thread(self.store.duplicate, operation, conn, payload)
            if not duplicate or not await self.verify_cancelled_duplicate(duplicate, conn, client):
                break
        slots = await client.request("GET", "/event_type_available_times", params={"event_type": entry["uri"], "start_time": when.isoformat(), "end_time": (when + timedelta(minutes=entry["duration"])).isoformat()})
        if not any(instant(s.get("start_time", "")) == when and s.get("status") == "available" and s.get("invitees_remaining") == 1 for s in slots.get("collection", [])):
            raise CalendlyError("slot_no_longer_available")
        if admission_check:
            await admission_check()
        if deadline - time.monotonic() < 7:
            raise CalendlyError("insufficient_submission_budget")
        row, admitted = await asyncio.to_thread(self.store.admit, operation, conn, payload, public)
        if not admitted:
            return view(row, operation)
        # From this point cancellation and every unexpected exception are uncertain.
        payload["tracking"] = {"utm_source": "ava", "utm_medium": "setup_test" if proposal.get("setup") else "voice",
                               "utm_content": row["marker"], "utm_campaign": None, "utm_term": None, "salesforce_uuid": None}
        private = json.loads(row["private"])
        private["lookup"] = {"event_type": entry["uri"], "start": when.isoformat(), "email": recipient, "name": name.strip(),
                             "duration": entry["duration"], "answers_digest": digest(questions), "location_kind": (entry["location"] or {}).get("kind"), "appointment_digest": digest(entry)}
        try:
            # Reserve journal persistence time inside the enclosing deadline.
            async with asyncio.timeout(min(7, deadline - time.monotonic() - 2)):
                result = (await client.request("POST", "/invitees", body=payload, timeout=min(6, deadline-time.monotonic()-2))).get("resource") or {}
            self.validate_invitee(result, private["lookup"], row["marker"])
            try:
                resource_path(result.get("uri", ""), "scheduled_events")
                resource_path(result.get("event", ""), "scheduled_events")
            except CalendlyError:
                raise CalendlyError("booking_response_unverified", uncertain=True) from None
            if result.get("event") + "/invitees/" not in result.get("uri", "") or result.get("status") != "active":
                raise CalendlyError("booking_response_unverified", uncertain=True)
            private.update(event=result["event"], invitee=result["uri"], cancel_url=result.get("cancel_url"), reschedule_url=result.get("reschedule_url"))
            await asyncio.to_thread(self.store.finish, row["id"], "confirmed", public, private)
        except CalendlyError as exc:
            state = "uncertain" if exc.uncertain else "rejected"
            await asyncio.to_thread(self.store.finish, row["id"], state, {**public, "code": exc.code}, private)
        except BaseException:
            await asyncio.shield(asyncio.to_thread(self.store.finish, row["id"], "uncertain", public, private))
            raise
        return view(await asyncio.to_thread(self.store.attempt, row["id"]), operation)

    async def verify_cancelled_duplicate(self, row, conn, client) -> bool:
        if row["state"] not in {"confirmed", "resolved_remote_cancelled"} or row["connection_id"] != conn["id"]:
            return False
        private = json.loads(row["private"])
        lookup = private.get("lookup") or {}
        if (private.get("user") != conn["user"] or private.get("organization") != conn["organization"]
                or not private.get("event") or not private.get("invitee") or not lookup):
            return False
        try:
            event = (await client.request("GET", resource_path(private["event"], "scheduled_events"))).get("resource") or {}
            invitee = (await client.request("GET", resource_path(private["invitee"], "scheduled_events"))).get("resource") or {}
            self.validate_invitee(invitee, lookup, row["marker"])
            if (event.get("uri") != private["event"] or invitee.get("uri") != private["invitee"]
                    or invitee.get("event") != event.get("uri") or invitee.get("rescheduled")
                    or not invitee["uri"].startswith(event["uri"] + "/invitees/")
                    or event.get("status") != "canceled" or invitee.get("status") != "canceled"
                    or event.get("event_type") != lookup["event_type"]
                    or instant(event.get("start_time", "")) != instant(lookup["start"])
                    or (instant(event.get("end_time", "")) - instant(event["start_time"])).total_seconds() != lookup["duration"] * 60
                    or not any(m.get("user") == conn["user"] for m in event.get("event_memberships", []))):
                return False
        except (CalendlyError, KeyError, TypeError, AttributeError):
            return False
        return await asyncio.to_thread(self.store.release_cancelled_duplicate, row)

    @staticmethod
    def validate_invitee(invitee, lookup, marker):
        try:
            if (email(invitee["email"]) != lookup["email"] or invitee.get("name") != lookup["name"]
                    or invitee.get("status") not in {"active", "canceled"}
                    or (invitee.get("tracking") or {}).get("utm_content") != marker
                    or digest(invitee.get("questions_and_answers") or []) != lookup["answers_digest"]):
                raise ValueError()
        except (ValueError, KeyError, TypeError, CalendlyError):
            raise CalendlyError("booking_response_unverified", uncertain=True) from None

    async def reconcile(self, key: str, manual=False, qualification=False):
        row = await asyncio.to_thread(self.store.attempt, key)
        if qualification:
            if row["state"] != "confirmed" or not row["operation"].startswith("setup:"):
                raise CalendlyError("setup_booking_required")
        else:
            row = await asyncio.to_thread(self.store.lease_check, key, manual)
        private = json.loads(row["private"])
        public = json.loads(row["public"])
        try:
            async with asyncio.timeout(20):
                conn, client = await self.credentials()
                if conn["id"] != row["connection_id"] or conn["user"] != private.get("user") or conn["organization"] != private.get("organization"):
                    raise CalendlyError("connection_changed")
                lookup = private.get("lookup")
                # Reconstruct lookup after a crash before local response processing.
                if not lookup and private.get("payload"):
                    p = private["payload"]
                    lookup = {"event_type": p["event_type"], "start": p["start_time"], "email": p["invitee"]["email"],
                              "name": p["invitee"]["name"], "duration": (instant(public["end_datetime"]) - instant(public["start_datetime"])).total_seconds() / 60,
                              "answers_digest": digest(p.get("questions_and_answers") or [])}
                if not lookup:
                    raise CalendlyError("lookup_details_expired")
                calls = 0
                async def read(path, params=None):
                    nonlocal calls
                    calls += 1
                    if calls > 8:
                        raise CalendlyError("search_incomplete")
                    return await client.request("GET", path, params=params)
                async def pages(path, params):
                    values = []
                    for _ in range(2):
                        data = await read(path, {**params, "count": 100})
                        if not isinstance(data.get("collection"), list):
                            raise CalendlyError("invalid_response")
                        values += data["collection"]
                        token = (data.get("pagination") or {}).get("next_page_token")
                        if not token:
                            return values
                        if not isinstance(token, str) or len(token) > 2000:
                            raise CalendlyError("invalid_pagination")
                        params = {**params, "page_token": token}
                    raise CalendlyError("search_incomplete")
                if private.get("event") and not qualification:
                    events = [(await read(resource_path(private["event"], "scheduled_events"))).get("resource") or {}]
                else:
                    if not qualification and not conn.get("qualified"):
                        raise CalendlyError("discovery_not_qualified")
                    t = instant(lookup["start"])
                    events = []
                    for status in ("active", "canceled"):
                        events += await pages("/scheduled_events", {"user": conn["user"], "invitee_email": lookup["email"], "min_start_time": (t-timedelta(seconds=1)).isoformat(), "max_start_time": (t+timedelta(seconds=1)).isoformat(), "sort": "start_time:asc", "status": status})
                matches = {}
                for event in events:
                    if event.get("event_type") != lookup["event_type"] or instant(event.get("start_time", "")) != instant(lookup["start"]):
                        continue
                    if not any(m.get("user") == conn["user"] for m in event.get("event_memberships", [])) or (instant(event["end_time"]) - instant(event["start_time"])).total_seconds() != lookup["duration"]*60:
                        raise CalendlyError("booking_identity_mismatch")
                    event_path = resource_path(event.get("uri", ""), "scheduled_events")
                    if private.get("invitee") and not qualification:
                        invitees = [(await read(resource_path(private["invitee"], "scheduled_events"))).get("resource") or {}]
                    else:
                        invitees = []
                        for status in ("active", "canceled"):
                            invitees += await pages(event_path + "/invitees", {"email": lookup["email"], "status": status})
                    for invitee in invitees:
                        if (invitee.get("tracking") or {}).get("utm_content") != row["marker"]:
                            continue
                        self.validate_invitee(invitee, lookup, row["marker"])
                        if invitee.get("event") != event["uri"] or invitee.get("rescheduled"):
                            raise CalendlyError("booking_changed_needs_staff")
                        resource_path(invitee.get("uri", ""), "scheduled_events")
                        matches[invitee["uri"]] = (event, invitee)
                if len(matches) != 1:
                    raise CalendlyError("no_unique_match")
                event, invitee = next(iter(matches.values()))
                if event.get("status") not in {"active", "canceled"}:
                    raise CalendlyError("booking_changed_needs_staff")
                private.update(event=event["uri"], invitee=invitee["uri"], lookup=lookup, cancel_url=invitee.get("cancel_url"), reschedule_url=invitee.get("reschedule_url"))
                if qualification:
                    exact = (await read(resource_path(invitee["uri"], "scheduled_events"))).get("resource") or {}
                    self.validate_invitee(exact, lookup, row["marker"])
                    if event.get("status") != "active" or invitee.get("status") != "active":
                        raise CalendlyError("setup_booking_not_active")
                    kind = lookup.get("location_kind") or ""
                    if kind.endswith("_conference"):
                        location = event.get("location") or {}
                        join = location.get("join_url") or ""
                        if location.get("type") != kind or location.get("status") != "pushed" or not str(join).startswith("https://"):
                            raise CalendlyError("conference_not_ready")
                    qualified = {**(conn.get("qualified_types") or {}), lookup["event_type"]: lookup.get("appointment_digest")}
                    await asyncio.to_thread(self.store.update_connection, conn["revision"], qualified=True, qualified_types=qualified)
                else:
                    state = "confirmed" if event.get("status") == invitee.get("status") == "active" else "resolved_remote_cancelled"
                    await asyncio.to_thread(self.store.finish, key, state, public, private)
        except (CalendlyError, TimeoutError, KeyError, TypeError, AttributeError, ValueError) as exc:
            if qualification:
                if isinstance(exc, (CalendlyError, TimeoutError)):
                    raise
                raise CalendlyError("invalid_response") from None
            code = (exc.code if isinstance(exc, CalendlyError)
                    else "check_timeout" if isinstance(exc, TimeoutError) else "invalid_response")
            state = "needs_operator" if row["passes"] >= 3 or code in {"discovery_not_qualified", "lookup_details_expired", "connection_changed"} else "uncertain"
            await asyncio.to_thread(self.store.finish, key, state, {**public, "code": code})
        return view(await asyncio.to_thread(self.store.attempt, key))


async def recovery_loop():
    """Only touch an already-created journal; disabled installations stay inert."""
    import logging
    import os
    from pathlib import Path
    log = logging.getLogger(__name__)
    last_cleanup = None
    while True:
        try:
            path = Path(os.getenv("CALENDLY_DB_PATH") or "/app/data/operator/calendly.db")
            if path.exists():
                store = await asyncio.to_thread(CalendlyStore)
                if last_cleanup is None or time.monotonic() - last_cleanup >= 60:
                    try:
                        await asyncio.to_thread(store.cleanup)
                        last_cleanup = time.monotonic()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log.warning("Calendly cleanup failed (%s); reconciliation continues", type(exc).__name__)
                if Path(os.getenv("CALL_HISTORY_DB_PATH", "/app/data/call_history.db")).exists():
                    try:
                        from src.core.call_history import get_call_history_store
                        await get_call_history_store().retry_pending_calendly_purges()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log.warning("Calendly purge retry failed (%s); reconciliation continues", type(exc).__name__)
                service = CalendlyService(store)
                try:
                    keys = await asyncio.to_thread(store.due)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("Calendly due lookup failed (%s)", type(exc).__name__)
                    keys = []
                for key in keys:
                    try:
                        await service.reconcile(key)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log.warning("Calendly reconciliation failed (%s); other attempts continue", type(exc).__name__)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Journaling failures must not take down the telephony runtime.
            log.warning("Calendly recovery unavailable; scheduling remains fail-closed")
        await asyncio.sleep(5)
