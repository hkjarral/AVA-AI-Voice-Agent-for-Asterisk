"""Authenticated Calendly setup/recovery. Never expose the PAT through config."""
import asyncio
import json
import logging
import sqlite3
import sys
import uuid
import portalocker
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Depends
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
import settings
import auth

if settings.PROJECT_ROOT not in sys.path:
    sys.path.insert(0, settings.PROJECT_ROOT)

from src.core.calendly_store import CalendlyStore
from src.tools.business.calendly_client import CalendlyError
from src.tools.business.calendly_service import CalendlyService, view

class PrivateInputRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def private_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, detail={"code": "invalid_request", "message": "Check the submitted Calendly fields."}) from None
        return private_handler


router = APIRouter(route_class=PrivateInputRoute)


async def service():
    # No credential or arbitrary filesystem path is accepted from a request.
    try:
        return CalendlyService(await asyncio.to_thread(CalendlyStore))
    except portalocker.exceptions.LockException:
        raise HTTPException(503, detail={"code": "journal_busy", "message": "Scheduling storage is busy. Wait a moment and try this setup action again. No booking was submitted."}) from None
    except (OSError, sqlite3.Error, CalendlyError):
        raise HTTPException(503, detail={"code": "journal_unavailable", "message": "Scheduling storage is unavailable; no booking was submitted."}) from None


async def safe(awaitable):
    try:
        return await awaitable
    except CalendlyError as exc:
        # Codes are integration-owned constants; never log the candidate token,
        # upstream response body, request arguments, or exception repr.
        logging.getLogger(__name__).warning("Calendly action failed (%s)", exc.code)
        messages = {
            "verification_token_required": "Enter a personal access token in the field above, then verify the connection. Booking can remain paused.",
            "invalid_token": "Paste only the personal access token, with no surrounding spaces or line breaks.",
            "network_unavailable": "The server could not reach Calendly. Wait briefly and verify the connection again.",
            "invalid_response": "Calendly returned an unreadable response. Check the saved request status before repeating a booking.",
            "identity_access_denied": "Calendly denied the account identity check. Confirm this PAT includes users:read. If that scope is present, check account or organization API restrictions. This is not a booking eligibility check.",
            "invalid_host_timezone": "Calendly returned an unsupported account timezone. Check the account timezone in Calendly.",
            "upstream_401": "Calendly rejected the token. Create or replace the saved token and verify again.",
            "upstream_403": "Calendly denied access. Check the token scopes and the account's paid booking eligibility.",
            "upstream_404": "The Calendly resource is unavailable. Refresh appointment selection; staff must check any pending booking.",
            "upstream_429": "Calendly's request limit was reached. Wait for the limit to reset; do not repeat a pending booking.",
            "rate_limited": "Calendly's shared request cooldown is active. Wait before checking again; do not repeat a pending booking.",
            "connection_disabled": "Scheduling is paused. Connect and explicitly enable booking before testing.",
            "connection_changed": "The connection changed. Refresh this panel before continuing.",
            "credential_permissions": "The saved credential needs private file permissions. Ask the server administrator to repair access.",
            "credential_unavailable": "The saved credential is unavailable. Verify or replace the connection.",
            "check_not_due": "A status check is in progress or its cooldown has not expired. Wait briefly and refresh.",
            "conference_not_ready": "The booking exists, but Calendly has not provided ready conference details. Check Calendly and try readback later; do not book again.",
            "conference_not_qualified": "Run and qualify a disposable setup test for this conference appointment before using it in calls.",
            "proposal_expired": "The offered time has expired. Find times again and reconfirm the details.",
            "proposal_changed_reconfirm": "The appointment settings changed. Find times again and obtain fresh confirmation.",
            "slot_no_longer_available": "That time is no longer available. Choose and confirm a new offered time.",
        }
        raise HTTPException(409 if exc.code in {"connection_changed", "check_not_due"} else 422,
                            detail={"code": exc.code, "message": messages.get(exc.code, "Calendly could not complete this action. Check setup or ask staff to verify a pending request.")}) from None
    except (OSError, ValueError, TimeoutError, sqlite3.Error):
        raise HTTPException(503, detail={"code": "calendly_unavailable", "message": "Calendly is unavailable. Do not repeat an uncertain booking."}) from None
    except portalocker.exceptions.LockException:
        raise HTTPException(503, detail={"code": "credential_busy", "message": "The connection is being updated. Wait a moment and try this setup action again."}) from None


class Candidate(BaseModel):
    token: str | None = Field(None, max_length=8192)


class Connection(Candidate):
    revision: int = Field(ge=0)


class Revision(BaseModel):
    revision: int = Field(ge=0)


class Proposal(BaseModel):
    appointment_key: str = Field(max_length=100)
    start_datetime: str = Field(max_length=100)
    end_datetime: str = Field(max_length=100)
    timezone: str = Field(max_length=100)
    operation: str | None = Field(None, max_length=50)


class Booking(BaseModel):
    operation: str = Field(max_length=50)
    slot_reference: str = Field(max_length=100)
    name: str = Field(max_length=200)
    email: str = Field(max_length=254)
    booking_confirmed: bool = False
    email_confirmed: bool = False
    answers: list[dict] = Field(default_factory=list, max_length=10)


class Resolution(BaseModel):
    note: str = Field(min_length=1, max_length=500)


class Observations(BaseModel):
    invitation_received: bool
    calendar_verified: bool
    cleanup_reported: bool


def config():
    from api.config import _read_merged_config_dict
    return (_read_merged_config_dict().get("tools") or {}).get("calendly_calendar") or {}


@router.get("/connection")
async def connection():
    s = await service()
    return await asyncio.to_thread(s.store.connection)


@router.post("/connection/verify")
async def verify(req: Candidate):
    s = await service()
    return await safe(s.verify(req.token))


@router.put("/connection")
async def save_connection(req: Connection):
    s = await service()
    if not req.token:
        raise HTTPException(422, detail="Enter a new token to save a connection")
    identity = await safe(s.verify(req.token))
    return await safe(asyncio.to_thread(s.store.save_connection, identity, req.token, req.revision))


@router.post("/connection/enable")
async def enable(req: Revision):
    s = await service()
    await safe(s.verify())
    return await safe(asyncio.to_thread(s.store.update_connection, req.revision, enabled=True))


@router.post("/connection/disable")
async def disable(req: Revision):
    s = await service()
    return await safe(asyncio.to_thread(s.store.update_connection, req.revision, enabled=False))


@router.delete("/connection")
async def disconnect(req: Revision):
    s = await service()
    return await safe(asyncio.to_thread(s.store.disconnect, req.revision))


@router.get("/appointment-types")
async def appointment_types():
    return await safe((await service()).discovery())


def test_operation(raw):
    try:
        if not isinstance(raw, str) or not raw.startswith("setup:") or str(uuid.UUID(raw[6:])) != raw[6:]:
            raise ValueError()
        return raw
    except (ValueError, AttributeError):
        raise HTTPException(422, detail="Invalid setup operation") from None


@router.post("/test-proposals")
async def test_proposals(req: Proposal):
    s = await service()
    operation = test_operation(req.operation) if req.operation else "setup:" + str(uuid.uuid4())
    result = await safe(s.slots(config(), operation, req.appointment_key, req.start_datetime, req.end_datetime, req.timezone, setup=True))
    return {**result, "operation": operation}


@router.post("/test-bookings")
async def test_booking(req: Booking):
    s = await service()
    return await safe(s.book(config(), test_operation(req.operation), req.dict()))


@router.get("/attempts")
async def attempts():
    s = await service()
    rows = await asyncio.to_thread(s.store.list_attempts)
    return [{"id": r["id"], "operation": r["operation"], "state": r["state"], "updated": r["updated"], **json.loads(r["public"])} for r in rows]


@router.get("/attempts/{key}")
async def detail(key: str):
    s = await service()
    row = await safe(asyncio.to_thread(s.store.attempt, key))
    private = json.loads(row["private"])
    links = {}
    for name in ("cancel_url", "reschedule_url"):
        raw = private.get(name)
        parsed = urlparse(raw or "")
        if parsed.scheme == "https" and parsed.netloc == "calendly.com" and parsed.path.startswith(("/cancellations/", "/reschedulings/")):
            links[name] = raw
    return {**view(row), **links}


@router.post("/attempts/{key}/reconcile")
async def reconcile(key: str):
    return await safe((await service()).reconcile(key, manual=True))


@router.post("/attempts/{key}/qualify")
async def qualify(key: str):
    s = await service()
    return await safe(s.reconcile(key, qualification=True))


@router.post("/attempts/{key}/observations")
async def observations(key: str, req: Observations, user=Depends(auth.get_current_user)):
    import time
    s = await service()
    row = await safe(asyncio.to_thread(s.store.attempt, key))
    conn = await asyncio.to_thread(s.store.connection)
    if row["state"] != "confirmed" or not row["operation"].startswith("setup:") or row["connection_id"] != conn.get("id"):
        raise HTTPException(422, detail="Observations require this connection's confirmed setup booking")
    await safe(asyncio.to_thread(s.store.update_connection, conn["revision"], observations={
        **req.dict(), "source": "operator", "recorded_at": time.time(), "actor": user.username,
    }))
    return {"status": "recorded", "evidence": "operator"}


@router.post("/attempts/{key}/external-resolution")
async def resolve(key: str, req: Resolution, user=Depends(auth.get_current_user)):
    s = await service()
    await safe(asyncio.to_thread(s.store.resolve, key, user.username, req.note))
    return {"status": "resolved_externally"}
