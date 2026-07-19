"""Admin API for VICIdial Remote Agent connections and mappings."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

project_root = os.environ.get("PROJECT_ROOT") or str(Path(__file__).resolve().parents[3])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agents_store import AgentsStore
from src.core.vicidial_store import get_vicidial_store
from src.integrations.vicidial import VicidialApiClient, remote_agent_user_range

router = APIRouter(prefix="/outbound/vicidial", tags=["outbound"])


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
        raise HTTPException(status_code=500, detail=f"VICIdial store unavailable: {exc}")


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
        result = await VicidialApiClient(connection).verify_connection()
    except ValueError as exc:
        result = {"ready": False, "error": str(exc)}
    _store().record_verification(kind="connection", record_id=connection_id, result=result)
    return result


@router.get("/mappings")
async def list_mappings():
    return [_mapping_with_connection(mapping) for mapping in _store().list_mappings()]


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
    connection_result = await client.verify_connection()
    campaigns = connection_result.get("authentication") or {}
    campaign_rows = list(campaigns.get("rows") or [])
    campaign_id = str(mapping.get("campaign_id") or "").strip()
    campaign_found = not campaign_id or any(
        str(row.get("campaign_id") or "").strip() == campaign_id
        for row in campaign_rows
    )
    agent_available = bool(_active_agent(str(mapping.get("ai_agent") or "")))

    users = list(remote_agent_user_range(mapping))
    status_results = []
    # agent_status is authoritative for whether each configured VICIdial user
    # actually exists. logged_in_agents alone can include a stale or manually
    # inserted live-agent row whose user is rejected by call-control APIs.
    # Bound each burst so a large Remote Agent range does not overwhelm the
    # dialer's legacy Non-Agent API.
    for offset in range(0, len(users), 10):
        status_results.extend(await asyncio.gather(*(
            client.agent_status(user)
            for user in users[offset:offset + 10]
        )))
    status_checks: Dict[str, Dict[str, Any]] = {}
    api_users: List[str] = []
    for user, status_result in zip(users, status_results):
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
    result: Dict[str, Any] = {
        # API and configuration checks cannot prove SIP registration, stable
        # READY state, media, or disposition. Only a real acceptance call may
        # promote a mapping to ready.
        "ready": bool(configuration_ready and live_call_ready),
        "configuration_ready": configuration_ready,
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
            "note": "Confirm pjsip show registrations and sip show peer on the PBX hosts",
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
    _store().record_verification(kind="mapping", record_id=mapping_id, result=result)
    return result


def _dialplan(mapping: Dict[str, Any]) -> str:
    context = str(mapping.get("trusted_context") or "from-vicidial-ra")
    mapping_id = str(mapping.get("id") or "")
    agent = str(mapping.get("ai_agent") or "")
    conf_exten = str(mapping.get("conf_exten") or "s")
    trusted_endpoint = str(mapping.get("trusted_endpoint") or "").strip()
    lines = [
        f"[{context}]",
        f"exten => {conf_exten},1,NoOp(VICIdial Remote Agent call: ${{CALLERID(all)}})",
    ]
    if trusted_endpoint:
        lines.extend([
            f' same => n,GotoIf($["${{CHANNEL(endpoint)}}"="{trusted_endpoint}"]?trusted:reject)',
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
        "vicidial_steps": [
            f"Create/verify Phone {mapping.get('conf_exten')} with protocol SIP and a unique conf_secret.",
            f"Create every VICIdial user in the contiguous range {user_range}; agent_status must recognize each user before enabling {mapping.get('number_of_lines')} line(s).",
            f"All {mapping.get('number_of_lines')} Remote Agent line(s) share Phone/conf_exten {mapping.get('conf_exten')}; do not create incremented SIP Phones for the additional users.",
            f"Create/verify Remote Agent with conf_exten {mapping.get('conf_exten')}, On-Hook Agent=N for classic outbound mode, and campaign {mapping.get('campaign_id') or 'CLOSER (inbound only)' }.",
            "For a both-direction mapping, set the outbound campaign Allow Inbound and Blended=Y and select the same inbound groups on the campaign and Remote Agent. Inbound-only Remote Agents use campaign CLOSER plus their inbound groups.",
            "For the first outbound acceptance test, use a measured Drop Call Seconds window (30 seconds in the validated lab); 5 seconds can expire before Remote Agent delivery. Tune it for production policy after measuring.",
            "For SQL-created lab records, keep closer_campaigns as an empty string rather than NULL.",
            "Grant the dedicated API user only the functions required by the readiness report.",
        ],
        "freepbx_trunk": {
            "name": "vicidial-ra",
            "username": mapping.get("conf_exten"),
            "auth_username": mapping.get("conf_exten"),
            "secret": "Use the VICIdial Phone conf_secret; never the Phone pass field",
            "authentication": "Outbound",
            "registration": "Send",
            "sip_server": connection.get("vicidial_host") or "<VICIDIAL_HOST>",
            "sip_server_port": connection.get("sip_port") or 5060,
            "context": mapping.get("trusted_context"),
            "contact_user": mapping.get("conf_exten"),
            "match_permit": connection.get("vicidial_host") or "<VICIDIAL_HOST>",
            "codec": "ulaw",
            "dtmf": "RFC4733/RFC2833",
            "direct_media": False,
            "qualify": "Keep enabled when OPTIONS succeeds; disable only the failing direction after verification",
        },
        "dialplan": _dialplan(mapping),
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
