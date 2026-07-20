"""Admin API for VICIdial Remote Agent connections and mappings."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

project_root = os.environ.get("PROJECT_ROOT") or str(Path(__file__).resolve().parents[3])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agents_store import AgentsStore
from src.core.vicidial_store import get_vicidial_store
from src.integrations.vicidial import VicidialApiClient, remote_agent_user_range

router = APIRouter(prefix="/outbound/vicidial", tags=["outbound"])
logger = logging.getLogger(__name__)

ACTIVITY_SUMMARY_MAX_ROWS = 5000
CONNECTION_VERIFICATION_MAX_SECONDS = 30.0
MAPPING_VERIFICATION_MAX_SECONDS = 30.0


class VicidialConnectionRequest(BaseModel):
    name: str
    enabled: bool = True
    base_url: str
    agent_api_url: Optional[str] = None
    non_agent_api_url: Optional[str] = None
    source: str = "aava"
    username_env: str = "VICIDIAL_API_USER"
    password_env: str = "VICIDIAL_API_PASS"
    verify_ssl: bool = True
    timeout_ms: int = 5000
    topology: str = "lan_vpn"
    vicidial_host: Optional[str] = None
    sip_port: int = 5060
    rtp_start: int = 10000
    rtp_end: int = 20000
    timezone: str = "UTC"


class VicidialMappingRequest(BaseModel):
    connection_id: str
    name: str
    enabled: bool = True
    direction: str = "both"
    campaign_id: Optional[str] = None
    closer_campaigns: List[str] = Field(default_factory=list)
    user_start: str
    number_of_lines: int = 1
    conf_exten: str
    static_agent_user: Optional[str] = None
    ai_agent: str
    trusted_context: str = "from-vicidial-ra"
    trusted_endpoint: Optional[str] = None
    pbx_setup_mode: str = "generated_registration"
    pbx_technology: str = "PJSIP"
    pbx_trunk_name: Optional[str] = None
    sip_username: Optional[str] = None
    sip_auth_username: Optional[str] = None
    sip_contact_user: Optional[str] = None
    sip_transport: str = "udp"
    dispositions: Dict[str, str] = Field(default_factory=dict)
    statuses: Dict[str, str] = Field(default_factory=dict)
    destinations: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    dnc_scope: str = "campaign"
    callback_type: str = "ANYONE"


def _dump(model: BaseModel) -> Dict[str, Any]:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _store():
    try:
        return get_vicidial_store()
    except Exception as exc:
        logger.exception("VICIdial store unavailable")
        raise HTTPException(status_code=500, detail="VICIdial store unavailable") from exc


def _active_agent(slug: str) -> Optional[Dict[str, Any]]:
    store = AgentsStore()
    try:
        agent = store.get_by_slug(slug)
        if agent and bool(agent.get("is_active")):
            return agent
        return None
    finally:
        store.close()


def _mapping_with_connection(mapping: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(mapping)
    result["connection"] = _store().get_connection(str(mapping.get("connection_id") or ""))
    result["agent_available"] = bool(_active_agent(str(mapping.get("ai_agent") or "")))
    return result


def _call_history_store():
    try:
        from src.core.call_history import get_call_history_store

        return get_call_history_store()
    except Exception as exc:
        logger.exception("Call History unavailable for VICIdial activity")
        raise HTTPException(status_code=500, detail="Call History unavailable") from exc


async def _engine_health_ari_connected_compat() -> Optional[bool]:
    from api import system as system_api

    return await system_api._engine_health_ari_connected()


async def _asterisk_endpoints(technology: str, resource: Optional[str] = None) -> Dict[str, Any]:
    """Return a sanitized ARI endpoint snapshot without exposing ARI credentials."""
    import httpx
    from api import system as system_api

    tech = str(technology or "PJSIP").strip().upper()
    if tech not in {"PJSIP", "SIP"}:
        raise ValueError("PBX technology must be PJSIP or SIP")
    endpoint = str(resource or "").strip() or None
    settings = await asyncio.to_thread(system_api._ari_env_settings)
    engine_ari = await system_api._engine_health_ari_connected()
    result: Dict[str, Any] = {
        "ari_connected": engine_ari,
        "probe_available": False,
        "technology": tech,
        "resource": endpoint,
        "found": False,
        "state": None,
        "channel_count": 0,
        "ready": False,
    }
    if engine_ari is False:
        result["note"] = "Asterisk ARI is disconnected"
        return result
    if not settings.get("username") or not settings.get("password"):
        result["note"] = "ARI is connected, but endpoint detail credentials are unavailable to Admin UI"
        return result

    base_url = f"{settings['scheme']}://{settings['host']}:{settings['port']}"
    verify = settings["ssl_verify"] if settings["scheme"] == "https" else True
    path = f"/ari/endpoints/{quote(tech, safe='')}"
    if endpoint:
        path += f"/{quote(endpoint, safe='')}"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=3.0), verify=verify
        ) as client:
            response = await client.get(
                base_url + path,
                auth=(settings["username"], settings["password"]),
            )
    except Exception:
        result["note"] = "ARI endpoint query failed"
        return result

    result["probe_available"] = True
    if response.status_code == 404:
        result["note"] = "Asterisk endpoint was not found"
        return result
    if response.status_code != 200:
        result["note"] = f"ARI endpoint query returned HTTP {response.status_code}"
        return result

    payload = response.json()
    rows = payload if isinstance(payload, list) else [payload]
    endpoints = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_tech = str(row.get("technology") or tech).strip().upper()
        row_resource = str(row.get("resource") or "").strip()
        if not row_resource:
            continue
        state = str(row.get("state") or "unknown").strip().lower()
        if state not in {"online", "offline", "unknown"}:
            state = "unknown"
        endpoints.append(
            {
                "technology": row_tech,
                "resource": row_resource,
                "state": state,
                "channel_count": len(row.get("channel_ids") or []),
            }
        )
    result["endpoints"] = sorted(endpoints, key=lambda row: row["resource"].lower())
    if endpoint:
        match = next((row for row in endpoints if row["resource"] == endpoint), None)
        if match:
            result.update(match)
            result["found"] = True
            result["ready"] = match["state"] == "online"
            result["note"] = (
                "Endpoint is reachable through Asterisk"
                if result["ready"]
                else "Endpoint exists but is not currently online"
            )
    else:
        result["found"] = bool(endpoints)
        result["ready"] = engine_ari is not False
        result["note"] = "Asterisk endpoints loaded"
    return result


def _activity_start(range_name: str, now: Optional[datetime] = None) -> datetime:
    """Return a UTC lower bound for one of the deliberately small UI windows."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    if range_name == "today":
        local_now = current.astimezone()
        return local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(
            timezone.utc
        )
    if range_name == "30d":
        return current - timedelta(days=30)
    return current - timedelta(days=7)


def _mask_phone_number(value: Optional[str]) -> Optional[str]:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if not digits:
        return None
    visible = 4 if len(digits) > 4 else 2
    return f"•••{digits[-visible:]}"


def _record_activity(record: Any, ai_failure_status: Optional[str] = None) -> Dict[str, Any]:
    metadata = record.external_metadata if isinstance(record.external_metadata, dict) else {}
    session = metadata.get("session") if isinstance(metadata.get("session"), dict) else {}
    disposition = str(record.external_disposition or "").strip() or None
    requested = str(metadata.get("requested_disposition") or "").strip() or None
    finalized = bool(metadata.get("finalized") and disposition)
    outcome = str(record.outcome or "unknown").strip().lower()
    semantic = str(metadata.get("disposition_label") or "").strip()
    unconfirmed_error = bool(not finalized or outcome in {"error", "failed"})
    confirmed_failure = bool(
        finalized
        and (
            semantic == "ai_failure"
            or (ai_failure_status and disposition == ai_failure_status)
        )
    )
    needs_attention = bool(unconfirmed_error or confirmed_failure)
    return {
        "id": record.id,
        "started_at": record.start_time.isoformat() if record.start_time else None,
        "direction": str(record.external_direction or "unknown").strip().lower(),
        "masked_number": _mask_phone_number(record.caller_number or record.called_number),
        "remote_agent": str(session.get("agent_user") or "").strip() or None,
        "ai_agent": str(record.context_name or "").strip() or None,
        "duration_seconds": round(float(record.duration_seconds or 0), 1),
        "outcome": outcome,
        "disposition": disposition or requested,
        "disposition_confirmed": bool(disposition),
        "finalized": finalized,
        "unconfirmed_error": unconfirmed_error,
        "confirmed_failure": confirmed_failure,
        "needs_attention": needs_attention,
        "mapping_id": str(metadata.get("mapping_id") or "").strip() or None,
        "mapping_name": str(metadata.get("mapping_name") or "").strip() or None,
    }


def _summarize_activity(
    records: List[Any],
    mapping_id: Optional[str],
    mappings: List[Dict[str, Any]],
):
    failure_statuses = {
        str(mapping.get("id")): str(
            (mapping.get("statuses") or {}).get("ai_failure") or "AIFAIL"
        ).strip()
        for mapping in mappings
        if mapping.get("id")
    }
    rows = []
    for record in records:
        metadata = record.external_metadata if isinstance(record.external_metadata, dict) else {}
        record_mapping_id = str(metadata.get("mapping_id") or "").strip()
        rows.append(_record_activity(record, failure_statuses.get(record_mapping_id)))
    mapping_summaries: Dict[str, Dict[str, Any]] = {
        str(mapping.get("id")): {
            "mapping_id": str(mapping.get("id")),
            "mapping_name": str(mapping.get("name") or "Unnamed mapping"),
            "handled": 0,
            "finalized": 0,
            "unconfirmed_errors": 0,
            "confirmed_failures": 0,
            "needs_attention": 0,
            "last_call_at": None,
        }
        for mapping in mappings
        if mapping.get("id")
    }
    for row in rows:
        key = row["mapping_id"] or "unknown"
        if key not in mapping_summaries:
            mapping_summaries[key] = {
                "mapping_id": row["mapping_id"],
                "mapping_name": row["mapping_name"] or "Unknown/deleted mapping",
                "handled": 0,
                "finalized": 0,
                "unconfirmed_errors": 0,
                "confirmed_failures": 0,
                "needs_attention": 0,
                "last_call_at": None,
            }
        summary = mapping_summaries[key]
        summary["handled"] += 1
        summary["finalized"] += int(row["finalized"])
        summary["unconfirmed_errors"] += int(row["unconfirmed_error"])
        summary["confirmed_failures"] += int(row["confirmed_failure"])
        summary["needs_attention"] += int(row["needs_attention"])
        if summary["last_call_at"] is None:
            summary["last_call_at"] = row["started_at"]

    selected = [row for row in rows if not mapping_id or row["mapping_id"] == mapping_id]
    handled = len(selected)
    duration = sum(float(row["duration_seconds"] or 0) for row in selected)
    dispositions = Counter(
        str(row["disposition"])
        for row in selected
        if row["disposition"] and row["disposition_confirmed"]
    )
    return {
        "summary": {
            "handled": handled,
            "finalized": sum(int(row["finalized"]) for row in selected),
            "unconfirmed_errors": sum(int(row["unconfirmed_error"]) for row in selected),
            "confirmed_failures": sum(int(row["confirmed_failure"]) for row in selected),
            "needs_attention": sum(int(row["needs_attention"]) for row in selected),
            "average_duration_seconds": round(duration / handled, 1) if handled else 0,
            "last_call_at": selected[0]["started_at"] if selected else None,
        },
        "dispositions": [
            {"status": status, "count": count}
            for status, count in sorted(
                dispositions.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "by_mapping": list(mapping_summaries.values()),
        "recent_calls": selected,
    }


@router.get("/connections")
async def list_connections():
    return _store().list_connections()


@router.post("/connections")
async def create_connection(req: VicidialConnectionRequest):
    try:
        return _store().save_connection(_dump(req))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.put("/connections/{connection_id}")
async def update_connection(connection_id: str, req: VicidialConnectionRequest):
    if not _store().get_connection(connection_id):
        raise HTTPException(status_code=404, detail="VICIdial connection not found")
    try:
        return _store().save_connection(_dump(req), connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/connections/{connection_id}")
async def delete_connection(connection_id: str):
    if not _store().delete_connection(connection_id):
        raise HTTPException(status_code=404, detail="VICIdial connection not found")
    return {"deleted": True, "id": connection_id}


@router.post("/connections/{connection_id}/verify")
async def verify_connection(connection_id: str):
    connection = _store().get_connection(connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="VICIdial connection not found")
    try:
        async with asyncio.timeout(CONNECTION_VERIFICATION_MAX_SECONDS):
            result = await VicidialApiClient(connection).verify_connection()
    except TimeoutError:
        result = {
            "ready": False,
            "error": "VICIdial connection verification exceeded the overall deadline",
            "error_code": "verification_timeout",
            "verification": {
                "timed_out": True,
                "timeout_seconds": CONNECTION_VERIFICATION_MAX_SECONDS,
            },
        }
    except ValueError:
        logger.warning(
            "Invalid VICIdial connection configuration",
            exc_info=True,
        )
        result = {
            "ready": False,
            "error": "VICIdial connection configuration is invalid",
        }
    _store().record_verification(kind="connection", record_id=connection_id, result=result)
    return result


@router.get("/mappings")
async def list_mappings():
    return [_mapping_with_connection(mapping) for mapping in _store().list_mappings()]


@router.get("/asterisk/endpoints")
async def list_asterisk_endpoints(
    technology: str = Query("PJSIP", pattern="^(?i:PJSIP|SIP)$"),
):
    try:
        return await _asterisk_endpoints(technology)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/activity")
async def vicidial_activity(
    range_name: str = Query("7d", alias="range", pattern="^(today|7d|30d)$"),
    mapping_id: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=25),
):
    """Summarize only VICIdial calls that reached AAVA and entered Call History."""
    now = datetime.now(timezone.utc)
    records = await _call_history_store().list_external_activity(
        "vicidial",
        start_date=_activity_start(range_name, now),
        end_date=now,
        mapping_id=mapping_id,
        max_rows=ACTIVITY_SUMMARY_MAX_ROWS + 1,
    )
    truncated = len(records) > ACTIVITY_SUMMARY_MAX_ROWS
    if truncated:
        records = records[:ACTIVITY_SUMMARY_MAX_ROWS]
    mappings = _store().list_mappings()
    result = _summarize_activity(records, mapping_id, mappings)
    result["recent_calls"] = result["recent_calls"][:limit]
    result.update(
        {
            "range": range_name,
            "mapping_id": mapping_id,
            "generated_at": now.isoformat(),
            "truncated": truncated,
            "scope_note": (
                "Only calls delivered to AAVA are counted. VICIdial dial attempts that never "
                "reached an AAVA Remote Agent are not included."
                + (
                    f" This range exceeded {ACTIVITY_SUMMARY_MAX_ROWS:,} calls; metrics use the most recent records."
                    if truncated
                    else ""
                )
            ),
        }
    )
    return result


@router.post("/mappings")
async def create_mapping(req: VicidialMappingRequest):
    if not _active_agent(req.ai_agent):
        raise HTTPException(status_code=422, detail="Selected AAVA Agent is missing or inactive")
    try:
        return _mapping_with_connection(_store().save_mapping(_dump(req)))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.put("/mappings/{mapping_id}")
async def update_mapping(mapping_id: str, req: VicidialMappingRequest):
    if not _store().get_mapping(mapping_id):
        raise HTTPException(status_code=404, detail="VICIdial mapping not found")
    if not _active_agent(req.ai_agent):
        raise HTTPException(status_code=422, detail="Selected AAVA Agent is missing or inactive")
    try:
        return _mapping_with_connection(_store().save_mapping(_dump(req), mapping_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/mappings/{mapping_id}")
async def delete_mapping(mapping_id: str):
    if not _store().delete_mapping(mapping_id):
        raise HTTPException(status_code=404, detail="VICIdial mapping not found")
    return {"deleted": True, "id": mapping_id}


@router.post("/mappings/{mapping_id}/verify")
async def verify_mapping(mapping_id: str):
    mapping = _store().get_mapping(mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="VICIdial mapping not found")
    connection = _store().get_connection(str(mapping.get("connection_id") or ""))
    if not connection:
        raise HTTPException(status_code=422, detail="VICIdial connection is missing")

    client = VicidialApiClient(connection)
    users = list(remote_agent_user_range(mapping))
    connection_result: Dict[str, Any] = {}
    status_results: Dict[str, Any] = {}
    verification_timed_out = False
    try:
        async with asyncio.timeout(MAPPING_VERIFICATION_MAX_SECONDS):
            connection_result = await client.verify_connection()
            # agent_status is authoritative for whether each configured VICIdial user
            # actually exists. logged_in_agents alone can include a stale or manually
            # inserted live-agent row whose user is rejected by call-control APIs.
            # Bound each burst so a large Remote Agent range does not overwhelm the
            # dialer's legacy Non-Agent API; the surrounding deadline bounds all bursts.
            for offset in range(0, len(users), 10):
                batch_users = users[offset:offset + 10]
                batch_results = await asyncio.gather(*(
                    client.agent_status(user) for user in batch_users
                ))
                status_results.update(zip(batch_users, batch_results))
    except TimeoutError:
        verification_timed_out = True
        connection_result = {
            **connection_result,
            "ready": False,
            "error": "VICIdial mapping verification exceeded the overall deadline",
            "error_code": "verification_timeout",
        }

    campaigns = connection_result.get("authentication") or {}
    campaign_rows = list(campaigns.get("rows") or [])
    campaign_id = str(mapping.get("campaign_id") or "").strip()
    campaign_found = any(
        str(row.get("campaign_id") or "").strip() == campaign_id
        for row in campaign_rows
    )
    agent_available = bool(_active_agent(str(mapping.get("ai_agent") or "")))

    status_checks: Dict[str, Dict[str, Any]] = {}
    api_users: List[str] = []
    for user in users:
        status_result = status_results.get(user)
        if status_result is None:
            status_checks[user] = {
                "success": False,
                "status": None,
                "error_code": (
                    "verification_timeout" if verification_timed_out else "not_checked"
                ),
            }
            continue
        status_data = dict(status_result.data or {})
        status = str(status_data.get("status") or "").strip().upper()
        status_checks[user] = {
            "success": bool(status_result.success),
            "status": status or None,
            "error_code": status_result.error_code,
        }
        if status_result.success:
            api_users.append(user)
    unverified_users = [user for user in users if user not in api_users]
    logged_agents = dict(connection_result.get("agent_visibility") or {})
    logged_rows = list(logged_agents.get("rows") or [])
    agent_rows = {
        str(row.get("user") or "").strip(): row
        for row in logged_rows
        if str(row.get("user") or "").strip()
    }
    visible_users = [user for user in users if user in agent_rows]
    ready_users = [
        user for user in visible_users
        if status_checks.get(user, {}).get("status") == "READY"
    ]
    configuration_ready = bool(
        connection_result.get("ready")
        and campaign_found
        and agent_available
        and len(api_users) == len(users)
        and len(visible_users) == len(users)
    )
    previous_verification = mapping.get("last_verification")
    if not isinstance(previous_verification, dict):
        previous_verification = {}
    real_calls = previous_verification.get("real_calls")
    if not isinstance(real_calls, dict):
        real_calls = {}
    configured_direction = str(mapping.get("direction") or "both")
    required_directions = (
        ["inbound", "outbound"]
        if configured_direction == "both"
        else [configured_direction]
    )
    live_call_ready = all(
        bool(dict(real_calls.get(direction) or {}).get("verified"))
        for direction in required_directions
    )
    trusted_endpoint = str(mapping.get("trusted_endpoint") or "").strip()
    if trusted_endpoint:
        pbx_endpoint = await _asterisk_endpoints(
            str(mapping.get("pbx_technology") or "PJSIP"), trusted_endpoint
        )
    else:
        pbx_endpoint = {
            "ari_connected": await _engine_health_ari_connected_compat(),
            "probe_available": False,
            "technology": str(mapping.get("pbx_technology") or "PJSIP").upper(),
            "resource": None,
            "found": False,
            "state": None,
            "channel_count": 0,
            "ready": False,
            "note": "Select the exact Asterisk endpoint ID to enable ARI verification",
        }
    result: Dict[str, Any] = {
        # API and configuration checks cannot prove SIP registration, stable
        # READY state, media, or disposition. Only a real acceptance call may
        # promote a mapping to ready.
        "ready": bool(configuration_ready and pbx_endpoint.get("ready") and live_call_ready),
        "verification": {
            "timed_out": verification_timed_out,
            "timeout_seconds": MAPPING_VERIFICATION_MAX_SECONDS,
        },
        "configuration_ready": configuration_ready,
        "pbx_ready": bool(pbx_endpoint.get("ready")),
        "pbx_endpoint": pbx_endpoint,
        "connection": connection_result,
        "campaign": {
            "id": campaign_id or None,
            "found": campaign_found,
        },
        "remote_agent": {
            "users": users,
            "api_users": api_users,
            "unverified_users": unverified_users,
            "visible_users": visible_users,
            "ready_users": ready_users,
            "status_checks": status_checks,
            "conf_exten": mapping.get("conf_exten"),
            "note": "READY is confirmed by a real call test; API visibility alone is not sufficient",
        },
        "aava_agent": {
            "slug": mapping.get("ai_agent"),
            "available": agent_available,
        },
        "registration": {
            "verified": False,
            "note": (
                "ARI confirms endpoint reachability, not outbound registration renewal; "
                "confirm registration on the PBX or with a real call"
            ),
        },
        "real_call": {
            "verified": live_call_ready,
            "required_directions": required_directions,
            "note": "Each configured direction requires a correlated call with confirmed VICIdial terminal control",
        },
        "real_calls": real_calls,
    }
    if logged_agents:
        result["remote_agent"]["api"] = logged_agents
    return _store().record_mapping_verification(mapping_id=mapping_id, result=result)


def _dialplan(mapping: Dict[str, Any]) -> str:
    context = str(mapping.get("trusted_context") or "from-vicidial-ra")
    mapping_id = str(mapping.get("id") or "")
    agent = str(mapping.get("ai_agent") or "")
    conf_exten = str(mapping.get("conf_exten") or "s")
    trusted_endpoint = str(mapping.get("trusted_endpoint") or "").strip()
    pbx_technology = str(mapping.get("pbx_technology") or "PJSIP").strip().upper()
    endpoint_channel_item = "peername" if pbx_technology == "SIP" else "endpoint"
    lines = [
        f"[{context}]",
        f"exten => {conf_exten},1,NoOp(VICIdial Remote Agent call: ${{CALLERID(all)}})",
    ]
    if trusted_endpoint:
        lines.extend([
            f' same => n,GotoIf($["${{CHANNEL({endpoint_channel_item})}}"="{trusted_endpoint}"]?trusted:reject)',
            " same => n(reject),Hangup(21)",
            " same => n(trusted),NoOp(Trusted VICIdial endpoint accepted)",
        ])
    lines.extend([
        " same => n,Set(__AAVA_CALL_OWNER=vicidial)",
        " same => n,Set(__VICIDIAL_RA_CALL_ID=${CALLERID(name)})",
        f" same => n,Set(__VICIDIAL_MAPPING_ID={mapping_id})",
        f" same => n,Set(__AI_AGENT={agent})",
        " same => n,Answer()",
        " same => n,Stasis(asterisk-ai-voice-agent)",
        " same => n,Hangup()",
    ])
    return "\n".join(lines)


@router.get("/mappings/{mapping_id}/guidance")
async def mapping_guidance(mapping_id: str):
    mapping = _store().get_mapping(mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="VICIdial mapping not found")
    connection = _store().get_connection(str(mapping.get("connection_id") or ""))
    if not connection:
        raise HTTPException(status_code=422, detail="VICIdial connection is missing")

    topology = str(connection.get("topology") or "lan_vpn")
    remote_agent_users = list(remote_agent_user_range(mapping))
    user_range = (
        remote_agent_users[0]
        if len(remote_agent_users) == 1
        else f"{remote_agent_users[0]} through {remote_agent_users[-1]}"
    )
    setup_mode = str(mapping.get("pbx_setup_mode") or "generated_registration")
    endpoint_id = str(mapping.get("trusted_endpoint") or "").strip()
    trunk_name = str(mapping.get("pbx_trunk_name") or endpoint_id or "").strip()
    sip_username = str(mapping.get("sip_username") or mapping.get("conf_exten") or "").strip()
    sip_auth_username = str(mapping.get("sip_auth_username") or sip_username).strip()
    sip_contact_user = str(
        mapping.get("sip_contact_user") or mapping.get("conf_exten") or ""
    ).strip()
    technology = str(mapping.get("pbx_technology") or "PJSIP").upper()
    direction = str(mapping.get("direction") or "both")
    remote_agent_campaign = (
        "CLOSER"
        if direction == "inbound"
        else str(mapping.get("campaign_id") or "<REQUIRED_REAL_CAMPAIGN>")
    )
    if setup_mode == "existing_endpoint":
        pbx_guidance: Dict[str, Any] = {
            "setup_mode": setup_mode,
            "technology": technology,
            "name": trunk_name or "<EXISTING_TRUNK_LABEL>",
            "endpoint_id": endpoint_id or "<SELECT_ENDPOINT_ID>",
            "configuration": "Keep the existing endpoint; AAVA performs no PBX mutation",
            "context": mapping.get("trusted_context"),
        }
    else:
        pbx_guidance = {
            "setup_mode": setup_mode,
            "technology": technology,
            "name": trunk_name or "<CHOOSE_TRUNK_NAME>",
            "endpoint_id": endpoint_id or "<SELECT_ENDPOINT_ID>",
            "username": sip_username,
            "auth_username": sip_auth_username,
            "secret": "<VICIDIAL_PHONE_CONF_SECRET>",
            "authentication": "Outbound",
            "registration": "Send",
            "sip_server": connection.get("vicidial_host") or "<VICIDIAL_HOST>",
            "sip_server_port": connection.get("sip_port") or 5060,
            "context": mapping.get("trusted_context"),
            "contact_user": sip_contact_user,
            "transport": str(mapping.get("sip_transport") or "udp").upper(),
            "match_permit": connection.get("vicidial_host") or "<VICIDIAL_HOST>",
            "codec": "ulaw",
            "dtmf": "RFC4733/RFC2833",
            "direct_media": False,
            "qualify": "Keep enabled when OPTIONS succeeds; disable only the failing direction after verification",
        }
    nat_notes = {
        "lan_vpn": [
            "Prefer private routed addresses or a site-to-site VPN.",
            "Include both PBX subnets in local_net and keep direct media disabled.",
            "Allow SIP and RTP only between the two PBX addresses.",
        ],
        "ava_behind_nat": [
            "Use outbound PJSIP registration so VICIdial learns AAVA's current contact.",
            "Set external_signaling_address, external_media_address, local_net, rtp_symmetric, force_rport, and rewrite_contact.",
            "Forward the configured SIP port and RTP range; disable SIP ALG and verify SDP addresses.",
        ],
        "public_sbc": [
            "Prefer a VPN or SBC with TLS/SRTP; do not expose unauthenticated SIP or the VICIdial API.",
            "Restrict signaling and RTP to known peer addresses and verify symmetric routing.",
        ],
    }
    return {
        "mapping": mapping,
        "connection": connection,
        "artifact_inputs": {
            "setup_mode": setup_mode,
            "technology": technology,
            "remote_agent_extension": str(mapping.get("conf_exten") or ""),
            "trunk_name": trunk_name,
            "endpoint_id": endpoint_id,
            "username": sip_username,
            "auth_username": sip_auth_username,
            "contact_user": sip_contact_user,
        },
        "vicidial_steps": [
            f"Create/verify Phone {mapping.get('conf_exten')} with protocol SIP and a unique conf_secret.",
            f"Create every VICIdial user in the contiguous range {user_range}; agent_status must recognize each user before enabling {mapping.get('number_of_lines')} line(s).",
            f"All {mapping.get('number_of_lines')} Remote Agent line(s) share Phone/conf_exten {mapping.get('conf_exten')}; do not create incremented SIP Phones for the additional users.",
            f"Create/verify Remote Agent with conf_exten {mapping.get('conf_exten')}, On-Hook Agent=N for classic outbound mode, and campaign {remote_agent_campaign}.",
            f"For a both-direction mapping, set the outbound campaign Allow Inbound and Blended=Y and select the same inbound groups on the campaign and Remote Agent. Inbound-only Remote Agents use campaign CLOSER plus their inbound groups; the mapping's action campaign {mapping.get('campaign_id') or '<REQUIRED_REAL_CAMPAIGN>'} remains the real campaign used for DNC and native callbacks.",
            "For the first outbound acceptance test, use a measured Drop Call Seconds window (30 seconds in the validated lab); 5 seconds can expire before Remote Agent delivery. Tune it for production policy after measuring.",
            "For SQL-created lab records, keep closer_campaigns as an empty string rather than NULL.",
            "Grant the dedicated API user only the functions required by the readiness report.",
            (
                f"Reuse existing Asterisk endpoint {endpoint_id or '<SELECT_ENDPOINT_ID>'}; do not create or overwrite a trunk from this guide."
                if setup_mode == "existing_endpoint"
                else f"Create a dedicated AAVA-side trunk/endpoint named {trunk_name or '<CHOOSE_TRUNK_NAME>'}."
            ),
        ],
        "freepbx_trunk": pbx_guidance,
        "dialplan": _dialplan(mapping),
        "dialplan_install": {
            "path": "/etc/asterisk/extensions_custom.conf",
            "freepbx_apply": "Use FreePBX Apply Config or run fwconsole reload",
            "asterisk_apply": "For vanilla Asterisk, run asterisk -rx 'dialplan reload'",
            "note": "Do not edit FreePBX-generated dialplan files; add this exact context to extensions_custom.conf",
        },
        "network": {
            "topology": topology,
            "sip_port": connection.get("sip_port"),
            "rtp_range": f"{connection.get('rtp_start')}-{connection.get('rtp_end')}",
            "notes": nat_notes[topology],
        },
        "lab_customer_leg": [
            "Create a separate test-only SIP customer endpoint/context on voiprnd; do not reuse the Remote Agent extension.",
            "Change the LOOPTEST carrier to Dial() that SIP endpoint so VICIdial observes a non-Local channel.",
            "Never copy this lab carrier into production; production deployments retain their existing carrier.",
        ],
        "verification_order": [
            "Agent and Non-Agent APIs",
            "campaign/mapping and active AAVA Agent",
            "registration stable through qualification cycles",
            "Remote Agent READY",
            "real outbound customer correlation and AAVA delivery",
            "real inbound/closer delivery",
            "two-way audio and DTMF",
            "hangup, both cold transfers, DNC, callback, final reporting",
        ],
    }
