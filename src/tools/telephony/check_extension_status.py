"""
Check Extension Status Tool - Query Asterisk device state for an extension.

Purpose (AAVA-53):
- Allow the AI agent to check whether an internal extension is available (e.g., NOT_INUSE)
  during a call, and then decide whether to transfer or continue the conversation.

Notes:
- Uses ARI deviceStates API (GET /ari/deviceStates/{deviceStateName}).
- Device state name is usually "<TECH>/<EXT>" (e.g., "PJSIP/2765" or "SIP/6000").
- Tech selection should be configurable per extension via Admin UI (stored under tools.extensions.internal).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, List
from urllib.parse import quote

import structlog

from src.tools.base import Tool, ToolDefinition, ToolParameter, ToolCategory, ToolPhase
from src.tools.context import ToolExecutionContext

logger = structlog.get_logger(__name__)


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, tuple):
        return [str(v) for v in value if v is not None]
    return [str(value)]

def _looks_like_extension_number(value: str) -> bool:
    value = (value or "").strip()
    return bool(value) and value.isdigit()


def _parse_dial_string_tech(dial_string: str) -> Optional[str]:
    dial_string = (dial_string or "").strip()
    if not dial_string:
        return None
    # Common patterns: "PJSIP/2765", "SIP/6000", "PJSIP/2765@from-internal"
    if "/" not in dial_string:
        return None
    tech = dial_string.split("/", 1)[0].strip()
    if not tech:
        return None
    return tech


DEFAULT_STATE_MAPPING: Dict[str, Tuple[str, ...]] = {
    "free": ("NOT_INUSE",),
    "busy": ("INUSE", "BUSY", "RINGING", "RINGINUSE", "ONHOLD"),
    "unavailable": ("UNAVAILABLE", "INVALID", "UNKNOWN"),
}


def resolve_state_mapping(cfg: Optional[dict]) -> Dict[str, set]:
    """Merge operator config over defaults; each bucket falls back to its default when absent."""
    cfg = cfg if isinstance(cfg, dict) else {}
    resolved: Dict[str, set] = {}
    for bucket, default in DEFAULT_STATE_MAPPING.items():
        values = cfg.get(bucket)
        if isinstance(values, (list, tuple)):
            resolved[bucket] = {str(v).strip().upper() for v in values if str(v).strip()}
        else:
            resolved[bucket] = {v.upper() for v in default}
    return resolved


def classify_device_state(state: str, mapping: Dict[str, set]) -> str:
    """Return 'free' | 'busy' | 'unavailable'. Precedence is free -> busy -> unavailable;
    unlisted values fail closed to 'unavailable'."""
    s = str(state or "").strip().upper()
    if s in mapping.get("free", set()):
        return "free"
    if s in mapping.get("busy", set()):
        return "busy"
    if s in mapping.get("unavailable", set()):
        return "unavailable"
    return "unavailable"


def _classify_custom_device_state(state: str, mapping: Dict[str, set]) -> str:
    """A CUSTOM device state is an opt-in "unavailable" signal, not a fail-closed one:
    it only makes the extension unavailable when its raw value classifies as busy.
    Free/unavailable/unknown/empty/invalid raw values are not actively blocking, so they
    classify as 'free' here (unlike native states, which stay fail-closed)."""
    return "busy" if classify_device_state(state, mapping) == "busy" else "free"


_STATUS_PRIORITY = ["in_call", "busy", "dnd", "ringing", "away", "on_hold", "unavailable", "unknown"]
_NATIVE_BUSY_LABEL = {"INUSE": "in_call", "BUSY": "in_call", "RINGINUSE": "in_call",
                      "RINGING": "ringing", "ONHOLD": "on_hold", "ACTIVE_CHANNELS": "in_call"}


def _label_for(state_rec: dict) -> str:
    cls = state_rec.get("classification")
    if cls == "free":
        return ""
    if cls == "unavailable":
        return "unavailable"
    if state_rec.get("role") == "native":
        return _NATIVE_BUSY_LABEL.get(str(state_rec.get("state", "")).strip().upper(), "busy")
    return str(state_rec.get("status") or "").strip().lower() or "busy"


def aggregate_availability(states: List[dict]) -> dict:
    labels = [lbl for lbl in (_label_for(s) for s in states) if lbl]
    if not labels:
        return {"available": True, "availability_status": "available", "availability_reason": "available"}
    top = min(labels, key=lambda label: _STATUS_PRIORITY.index(label) if label in _STATUS_PRIORITY else len(_STATUS_PRIORITY))
    return {"available": False, "availability_status": top, "availability_reason": top}


def _resolve_extension_entry(
    *,
    target: str,
    extensions_config: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], str]:
    """
    Resolve a user-supplied target (e.g., "2765", "support", "sales_agent") to a configured extension entry.

    Returns:
        (extension_number, config_entry, resolution_source)
    """
    target = (target or "").strip()
    if not target or not isinstance(extensions_config, dict):
        return "", {}, ""

    if target in extensions_config and isinstance(extensions_config.get(target), dict):
        return target, dict(extensions_config[target]), "config.key"

    target_lower = target.lower()
    for ext_num, ext_cfg in extensions_config.items():
        if not isinstance(ext_num, str):
            ext_num = str(ext_num)
        if not isinstance(ext_cfg, dict):
            continue
        name = str(ext_cfg.get("name", "") or "").strip().lower()
        if name and name == target_lower:
            return ext_num, dict(ext_cfg), "config.name"

        aliases = [a.strip().lower() for a in _as_str_list(ext_cfg.get("aliases")) if a.strip()]
        if target_lower in aliases:
            return ext_num, dict(ext_cfg), "config.alias"

    return "", {}, ""

def _resolve_transfer_destination_extension(
    *,
    target: str,
    destinations: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], str]:
    """
    Resolve a transfer destination key (tools.transfer.destinations.<key>) to an extension number.

    Returns:
        (extension_number, destination_config, resolution_source)
    """
    target = (target or "").strip()
    if not target or not isinstance(destinations, dict):
        return "", {}, ""

    dest = destinations.get(target)
    if not isinstance(dest, dict):
        return "", {}, ""

    if str(dest.get("type", "") or "").strip().lower() != "extension":
        return "", {}, ""

    ext = str(dest.get("target", "") or "").strip()
    if not _looks_like_extension_number(ext):
        return "", {}, ""

    return ext, dict(dest), "config.transfer.destinations"


def _allowed_configured_extensions(
    *,
    extensions_config: Dict[str, Any],
    destinations: Dict[str, Any],
) -> List[str]:
    allowed: set[str] = set()

    if isinstance(extensions_config, dict):
        for key, cfg in extensions_config.items():
            extension = str(key or "").strip()
            if not _looks_like_extension_number(extension):
                continue
            if isinstance(cfg, dict) and cfg.get("transfer") is False:
                continue
            allowed.add(extension)

    if isinstance(destinations, dict):
        for cfg in destinations.values():
            if not isinstance(cfg, dict):
                continue
            if str(cfg.get("type", "") or "").strip().lower() != "extension":
                continue
            extension = str(cfg.get("target", "") or "").strip()
            if _looks_like_extension_number(extension):
                allowed.add(extension)

    return sorted(allowed)


def _extract_extension_from_device_state_id(device_state_id: str) -> str:
    raw = str(device_state_id or "").strip()
    if "/" not in raw:
        return ""
    return raw.split("/", 1)[1].strip()


def _resolve_device_state_id(
    *,
    extension: str,
    extensions_config: Dict[str, Any],
    tech: str = "",
    device_state_id: str = "",
    default_technology: str = "",
) -> Tuple[str, str]:
    """
    Resolve the ARI deviceStateName to query.

    Returns:
        (device_state_id, resolution_source)
    """
    extension = (extension or "").strip()
    if not extension:
        return "", ""

    if device_state_id:
        return device_state_id.strip(), "parameter.device_state_id"

    entry = None
    if isinstance(extensions_config, dict):
        entry = extensions_config.get(extension)

    # Best-effort: if the extension isn't keyed by its number, try to match by dial_string suffix.
    if entry is None and isinstance(extensions_config, dict):
        for _, cfg in extensions_config.items():
            if not isinstance(cfg, dict):
                continue
            dial_string = str(cfg.get("dial_string", "") or "")
            if dial_string.endswith(f"/{extension}") or f"/{extension}@" in dial_string:
                entry = cfg
                break

    if isinstance(entry, dict):
        cfg_state_id = str(entry.get("device_state_id", "") or "").strip()
        if cfg_state_id:
            return cfg_state_id, "config.device_state_id"

        cfg_tech = str(entry.get("device_state_tech", "") or "").strip()
        if cfg_tech and cfg_tech.lower() != "auto":
            tech = cfg_tech
            return f"{tech.upper()}/{extension}", "config.device_state_tech"

        dial_string = str(entry.get("dial_string", "") or "")
        dial_tech = _parse_dial_string_tech(dial_string)
        if dial_tech:
            tech = dial_tech
            return f"{tech.upper()}/{extension}", "config.dial_string"

    if tech:
        return f"{tech.upper()}/{extension}", "parameter.tech"

    if default_technology and _looks_like_extension_number(extension):
        return f"{str(default_technology).upper()}/{extension}", "config.transfer.technology"

    return "", ""


async def _probe_endpoint(
    *,
    context: ToolExecutionContext,
    tech: str,
    extension: str,
) -> Optional[Dict[str, Any]]:
    """
    Best-effort ARI endpoint probe.

    Returns an Endpoint dict on success, otherwise None.
    """
    if not context.ari_client:
        return None
    tech = (tech or "").strip().upper()
    extension = (extension or "").strip()
    if not tech or not extension:
        return None
    try:
        resp = await context.ari_client.send_command(
            method="GET",
            resource=f"endpoints/{tech}/{quote(extension, safe='')}",
        )
    except Exception:
        logger.debug(
            "ARI endpoint probe failed",
            tech=tech,
            extension=extension,
            exc_info=True,
        )
        return None
    if not isinstance(resp, dict):
        return None

    # ARI error payloads are often JSON objects too (e.g., {"message": "..."}).
    # Only treat this as a valid endpoint if it resembles the Endpoint model.
    if "technology" not in resp or "resource" not in resp:
        return None
    return resp


async def _list_device_states(
    *,
    context: ToolExecutionContext,
) -> List[Dict[str, Any]]:
    if not context.ari_client:
        return []
    try:
        resp = await context.ari_client.send_command(
            method="GET",
            resource="deviceStates",
        )
    except Exception:
        logger.debug("ARI device states list failed", exc_info=True)
        return []
    if not isinstance(resp, list):
        return []
    return [item for item in resp if isinstance(item, dict)]


async def _query_custom_device_state(
    *,
    context: ToolExecutionContext,
    device_state_id: str,
) -> str:
    """Query a single (typically custom/tracking) device state id and return its raw state."""
    encoded_id = quote(device_state_id, safe="")
    resp: Optional[Dict[str, Any]] = None
    try:
        resp = await context.ari_client.send_command(
            method="GET",
            resource=f"deviceStates/{encoded_id}",
        )
    except Exception:
        logger.debug(
            "ARI custom device state query failed",
            device_state_id=device_state_id,
            exc_info=True,
        )
        resp = None
    if not isinstance(resp, dict) or "state" not in resp:
        for item in await _list_device_states(context=context):
            if str(item.get("name", "") or "") == device_state_id:
                resp = item
                break
    if isinstance(resp, dict):
        return str(resp.get("state", "") or "")
    return ""


async def _list_endpoints(
    *,
    context: ToolExecutionContext,
) -> List[Dict[str, Any]]:
    if not context.ari_client:
        return []
    try:
        resp = await context.ari_client.send_command(
            method="GET",
            resource="endpoints",
        )
    except Exception:
        logger.debug("ARI endpoints list failed", exc_info=True)
        return []
    if not isinstance(resp, list):
        return []
    return [item for item in resp if isinstance(item, dict)]


async def _list_channels(
    *,
    context: ToolExecutionContext,
) -> Optional[List[Dict[str, Any]]]:
    if not context.ari_client:
        return None
    try:
        resp = await context.ari_client.send_command(
            method="GET",
            resource="channels",
        )
    except Exception:
        logger.debug("ARI channels list failed", exc_info=True)
        return None
    if not isinstance(resp, list):
        return None
    return [item for item in resp if isinstance(item, dict)]


def _extension_matches_channel(
    *,
    channel: Dict[str, Any],
    tech: str,
    extension: str,
) -> bool:
    if not channel:
        return False
    if not tech or not extension:
        return False
    tech = str(tech).strip().upper()
    channel_name = str(channel.get("name", "") or "")
    endpoint_name = f"{tech}/{extension}"
    if channel_name == endpoint_name or channel_name.startswith(f"{endpoint_name}-"):
        return True

    # Fall back to connected/number when available; this covers some
    # Local/forwarded channel name variations.
    connected = channel.get("connected")
    if isinstance(connected, dict):
        connected_number = str(connected.get("number", "") or "").strip()
        if connected_number and connected_number == extension:
            return True
    return False


def _has_active_channels(
    *,
    channels: List[Dict[str, Any]],
    tech: str,
    extension: str,
) -> List[str]:
    if not channels:
        return []
    active: List[str] = []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        if not _extension_matches_channel(channel=channel, tech=tech, extension=extension):
            continue
        channel_id = str(channel.get("id", "") or "").strip()
        if not channel_id:
            continue
        state = str(channel.get("state", "") or "").strip().upper()
        if state and state not in {"DOWN"}:
            active.append(channel_id)
            continue
        if not state:
            active.append(channel_id)
    return active


class CheckExtensionStatusTool(Tool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="check_extension_status",
            description=(
                "Check if an internal extension is available by querying Asterisk device state. "
                "Use this before attempting a transfer to a live agent. "
                "Only check extension numbers configured under Tools unless the guardrail is explicitly disabled."
            ),
            category=ToolCategory.TELEPHONY,
            phase=ToolPhase.IN_CALL,
            is_global=False,
            requires_channel=False,
            max_execution_time=10,
            parameters=[
                ToolParameter(
                    name="extension",
                    type="string",
                    description="Extension number to check (e.g., '2765').",
                    required=True,
                ),
                ToolParameter(
                    name="tech",
                    type="string",
                    description="Channel technology for device state (e.g., 'PJSIP', 'SIP'). Defaults to auto via config/dial_string.",
                    required=False,
                ),
                ToolParameter(
                    name="device_state_id",
                    type="string",
                    description="Optional explicit device state id to query (e.g., 'PJSIP/2765'). Overrides tech/extension resolution.",
                    required=False,
                ),
            ],
        )

    async def execute(self, parameters: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
        await self.validate_parameters(parameters)

        if not context.ari_client:
            return {"status": "error", "message": "ARI client not available in tool context"}

        target = str(parameters.get("extension", "") or "").strip()
        tech = str(parameters.get("tech", "") or "").strip()
        device_state_id = str(parameters.get("device_state_id", "") or "").strip()

        tool_cfg = context.get_config_value("tools.check_extension_status", {}) or {}
        extensions_cfg = context.get_config_value("tools.extensions.internal", {}) or {}
        transfer_destinations = context.get_config_value("tools.transfer.destinations", {}) or {}
        transfer_cfg = context.get_config_value("tools.transfer", {}) or {}
        restrict_to_configured = bool(tool_cfg.get("restrict_to_configured_extensions", True))
        allowed_extensions = _allowed_configured_extensions(
            extensions_config=extensions_cfg,
            destinations=transfer_destinations,
        )

        # Resolve what the caller/model provided into a real extension number.
        resolved_ext = ""
        ext_entry: Dict[str, Any] = {}
        ext_source = ""
        destination_cfg: Dict[str, Any] = {}
        destination_source = ""

        if _looks_like_extension_number(target):
            resolved_ext = target
            ext_entry = dict(extensions_cfg.get(target) or {}) if isinstance(extensions_cfg, dict) else {}
            ext_source = "parameter.extension"
        else:
            # First, resolve via internal extension config (key/name/aliases).
            resolved_ext, ext_entry, ext_source = _resolve_extension_entry(target=target, extensions_config=extensions_cfg)
            # Next, allow passing a transfer destination key like "support_agent" / "sales_agent".
            if not resolved_ext:
                resolved_ext, destination_cfg, destination_source = _resolve_transfer_destination_extension(
                    target=target, destinations=transfer_destinations
                )
                if resolved_ext and not ext_entry and isinstance(extensions_cfg, dict):
                    ext_entry = dict(extensions_cfg.get(resolved_ext) or {})

        extension = resolved_ext or target

        if restrict_to_configured:
            normalized_extension = str(extension or "").strip()
            extension_for_guardrail = normalized_extension
            # A device_state_id param that matches one of the resolved extension's own
            # configured custom device_states (#577) is treated as part of that extension's
            # config, not an arbitrary caller-supplied state id.
            configured_state_ids = {
                str(ds.get("id", "") or "").strip()
                for ds in (ext_entry.get("device_states") or [] if isinstance(ext_entry, dict) else [])
                if isinstance(ds, dict)
            } - {""}
            is_configured_custom_state = bool(device_state_id) and device_state_id in configured_state_ids
            if device_state_id and not is_configured_custom_state:
                # An explicit device_state_id param must resolve to a configured extension
                # via its "<TECH>/<extension>" shape (checked below); it must not silently
                # fall back to the (unrelated) `extension` param's value, or an arbitrary
                # device_state_id could ride along with a valid `extension` param.
                extension_for_guardrail = _extract_extension_from_device_state_id(device_state_id)

            if not allowed_extensions:
                logger.warning(
                    "Blocked status check because no configured extensions are available",
                    call_id=context.call_id,
                    requested_extension=target,
                    device_state_id=device_state_id,
                )
                return {
                    "status": "failed",
                    "message": "No configured extensions are available for status checks.",
                    "extension": extension_for_guardrail or normalized_extension or target,
                    "available": False,
                    "guardrail_blocked": True,
                    "allowed_extensions": allowed_extensions,
                }

            if _looks_like_extension_number(extension_for_guardrail) and extension_for_guardrail not in allowed_extensions:
                logger.warning(
                    "Blocked status check for unconfigured extension",
                    call_id=context.call_id,
                    requested_extension=target,
                    resolved_extension=extension_for_guardrail,
                    device_state_id=device_state_id or None,
                    allowed_extensions=allowed_extensions,
                )
                return {
                    "status": "failed",
                    "message": (
                        f"Extension {extension_for_guardrail} is not configured. "
                        f"Allowed extensions: {', '.join(allowed_extensions)}."
                    ),
                    "extension": extension_for_guardrail,
                    "available": False,
                    "guardrail_blocked": True,
                    "allowed_extensions": allowed_extensions,
                }
            if device_state_id and not is_configured_custom_state and not _looks_like_extension_number(extension_for_guardrail):
                logger.warning(
                    "Blocked status check for non-numeric device_state_id while configured-only guardrail is enabled",
                    call_id=context.call_id,
                    requested_extension=target,
                    device_state_id=device_state_id,
                    allowed_extensions=allowed_extensions,
                )
                return {
                    "status": "failed",
                    "message": (
                        f"Device state id '{device_state_id}' is not allowed. "
                        f"Allowed extensions: {', '.join(allowed_extensions)}."
                    ),
                    "extension": extension_for_guardrail or normalized_extension or target,
                    "available": False,
                    "guardrail_blocked": True,
                    "allowed_extensions": allowed_extensions,
                }
            if not device_state_id and not resolved_ext and not _looks_like_extension_number(normalized_extension):
                logger.warning(
                    "Blocked status check for unresolved configured-only target",
                    call_id=context.call_id,
                    requested_extension=target,
                    resolved_extension=normalized_extension,
                    allowed_extensions=allowed_extensions,
                )
                return {
                    "status": "failed",
                    "message": (
                        f"Extension target '{normalized_extension or target}' is not configured. "
                        f"Allowed extensions: {', '.join(allowed_extensions)}."
                    ),
                    "extension": normalized_extension or target,
                    "available": False,
                    "guardrail_blocked": True,
                    "allowed_extensions": allowed_extensions,
                }

        # Resolve device_state_id/tech using config + parameters.
        resolved_id, source = _resolve_device_state_id(
            extension=extension,
            extensions_config=extensions_cfg,
            tech=tech,
            device_state_id=device_state_id,
            default_technology=str((transfer_cfg or {}).get("technology", "") or "").strip(),
        )

        # If not resolvable via config/params, attempt auto-tech detection via ARI endpoints.
        endpoint_info: Dict[str, Any] = {}
        used_tech = ""
        if not resolved_id and not device_state_id and not tech:
            # Only auto-detect tech for numeric extensions. If the target is a logical key, we
            # should have resolved it via config first; otherwise we'd hit endpoints/PJSIP/<key>.
            if not _looks_like_extension_number(extension):
                resolved_id = ""
            else:
                # Try common techs in an opinionated order (most FreePBX installs are PJSIP-first).
                for candidate in ("PJSIP", "SIP"):
                    endpoint = await _probe_endpoint(context=context, tech=candidate, extension=extension)
                    if endpoint:
                        endpoint_info = endpoint
                        used_tech = candidate
                        source = "ari.endpoints.detected"
                        resolved_id = f"{candidate}/{extension}"
                        break

        # If we did resolve a tech (via config/param), also try to fetch endpoint state for extra context.
        if not endpoint_info:
            inferred_tech = ""
            if device_state_id and "/" in device_state_id:
                inferred_tech = device_state_id.split("/", 1)[0]
            elif resolved_id and "/" in resolved_id:
                inferred_tech = resolved_id.split("/", 1)[0]
            elif tech:
                inferred_tech = tech
            if inferred_tech:
                endpoint = await _probe_endpoint(context=context, tech=inferred_tech, extension=extension)
                if endpoint:
                    endpoint_info = endpoint
                    used_tech = inferred_tech
                else:
                    for item in await _list_endpoints(context=context):
                        if (
                            str(item.get("technology", "") or "").upper() == inferred_tech.upper()
                            and str(item.get("resource", "") or "") == extension
                        ):
                            endpoint_info = item
                            used_tech = inferred_tech
                            if not source:
                                source = "ari.endpoints.list"
                            break

        if not resolved_id and not endpoint_info:
            logger.warning(
                "Unable to resolve extension tech/device state id",
                call_id=context.call_id,
                target=target,
                resolved_extension=extension,
                has_extensions_config=bool(extensions_cfg),
            )
            return {
                "status": "error",
                "message": (
                    "Unable to resolve extension tech/device state id. Provide tech/device_state_id, "
                    "or configure tools.extensions.internal, or use a numeric extension (e.g., '2765')."
                ),
                "target": target,
            }

        # ARI expects deviceStateName in the URL path, so URL-encode slashes.
        encoded = quote(resolved_id, safe="") if resolved_id else ""

        device_state_error: Optional[str] = None
        device_state_resp: Optional[Dict[str, Any]] = None
        try:
            if encoded:
                device_state_resp = await context.ari_client.send_command(
                    method="GET",
                    resource=f"deviceStates/{encoded}",
                )
        except Exception as exc:
            device_state_error = str(exc)
            logger.warning(
                "ARI device state query failed; will fall back to list lookup if available",
                call_id=context.call_id,
                target=target,
                extension=extension,
                device_state_id=resolved_id,
                error=device_state_error,
            )

        if encoded and (not isinstance(device_state_resp, dict) or "state" not in device_state_resp):
            for item in await _list_device_states(context=context):
                if str(item.get("name", "") or "") == resolved_id:
                    device_state_resp = item
                    if not source:
                        source = "ari.deviceStates.list"
                    break

        # Common ARI response: {"name":"PJSIP/2765","state":"NOT_INUSE"}
        state = ""
        name = ""
        if isinstance(device_state_resp, dict):
            name = str(device_state_resp.get("name", "") or "")
            state = str(device_state_resp.get("state", "") or "")
        state_norm = state.strip().upper()

        # If we got INVALID, try to recover (common when tech is wrong, e.g. SIP vs PJSIP).
        if state_norm == "INVALID" and not device_state_id:
            if _looks_like_extension_number(extension):
                for candidate in ("PJSIP", "SIP"):
                    if resolved_id and resolved_id.startswith(f"{candidate}/"):
                        continue
                    endpoint = await _probe_endpoint(context=context, tech=candidate, extension=extension)
                    if not endpoint:
                        continue
                    try:
                        candidate_id = f"{candidate}/{extension}"
                        encoded_candidate = quote(candidate_id, safe="")
                        candidate_resp = await context.ari_client.send_command(
                            method="GET",
                            resource=f"deviceStates/{encoded_candidate}",
                        )
                    except Exception:
                        logger.debug(
                            "ARI fallback probe failed",
                            tech=candidate,
                            extension=extension,
                            exc_info=True,
                        )
                        continue
                    if not isinstance(candidate_resp, dict) or "state" not in candidate_resp:
                        for item in await _list_device_states(context=context):
                            if str(item.get("name", "") or "") == candidate_id:
                                candidate_resp = item
                                break
                    if isinstance(candidate_resp, dict):
                        cstate = str(candidate_resp.get("state", "") or "").strip().upper()
                        if cstate and cstate != "INVALID":
                            device_state_resp = candidate_resp
                            resolved_id = candidate_id
                            state_norm = cstate
                            name = str(candidate_resp.get("name", "") or "")
                            source = "ari.deviceStates.fallback"
                            endpoint_info = endpoint_info or endpoint
                            used_tech = candidate
                            break

        endpoint_state = ""
        endpoint_channel_ids: List[str] = []
        warnings: List[str] = []
        channel_lookup_failed = False
        if isinstance(endpoint_info, dict):
            endpoint_state = str(endpoint_info.get("state", "") or "").strip()
            endpoint_channel_ids = [str(x) for x in (endpoint_info.get("channel_ids") or []) if x is not None]
            # If the endpoint is online but doesn't list channel IDs, cross-check
            # active channels directly as a fallback.
            if state_norm == "NOT_INUSE" and not endpoint_channel_ids and endpoint_state.lower() == "online":
                endpoint_tech = used_tech
                if not endpoint_tech and resolved_id and "/" in resolved_id:
                    endpoint_tech = resolved_id.split("/", 1)[0]
                channels = await _list_channels(context=context)
                if channels is None:
                    channel_lookup_failed = True
                    warnings.append(
                        "Failed to verify active endpoint channels due to ARI lookup failure; treating extension as unavailable."
                    )
                else:
                    active_channels = _has_active_channels(
                        channels=channels,
                        tech=endpoint_tech,
                        extension=extension,
                    )
                    if active_channels:
                        endpoint_channel_ids = active_channels
                        warnings.append(
                            "Device state reported as available, but active endpoint channel(s) were detected. "
                            "Treating extension as busy."
                        )

        availability_source = ""
        availability_reason = ""
        if state_norm:
            # Conservative availability mapping:
            # - NOT_INUSE is clearly available.
            # - INUSE/BUSY/RINGING/UNAVAILABLE are not.
            available = state_norm == "NOT_INUSE"
            availability_reason = str(state_norm or state)
            if state_norm == "NOT_INUSE" and endpoint_channel_ids:
                available = False
                availability_source = "device_state+endpoint_channels"
                availability_reason = "active_endpoint_channels_detected"
                if not any("Treating extension as busy" in w for w in warnings):
                    warnings.append(
                        "Device state reports NOT_INUSE, but active endpoint channel(s) were detected. "
                        "Treating extension as busy."
                    )
            elif state_norm == "NOT_INUSE" and channel_lookup_failed:
                available = False
                availability_source = "device_state+endpoint_channels_lookup_failed"
                availability_reason = "channel_lookup_failed"
            else:
                availability_source = "device_state"
        elif endpoint_state:
            # Endpoint state is weaker (online/offline). We treat "online + no channels" as available.
            available = endpoint_state.lower() == "online" and len(endpoint_channel_ids) == 0
            availability_source = "endpoint_state"
            availability_reason = endpoint_state.lower() or "offline"
            if available and used_tech:
                channels = await _list_channels(context=context)
                if channels is None:
                    available = False
                    availability_source = "endpoint_state+endpoint_channels_lookup_failed"
                    availability_reason = "channel_lookup_failed"
                    warnings.append(
                        "Failed to verify active endpoint channels due to ARI lookup failure; "
                        "treating extension as unavailable."
                    )
                else:
                    active_channels = _has_active_channels(
                        channels=channels,
                        tech=used_tech,
                        extension=extension,
                    )
                    if active_channels:
                        endpoint_channel_ids = active_channels
                        available = False
                        availability_source = "endpoint_state+endpoint_channels"
                        availability_reason = "active_endpoint_channels_detected"
                        warnings.append(
                            "Endpoint state reported as available, but active endpoint "
                            "channel(s) were detected. Treating extension as busy."
                        )
            warnings.append("Device state unavailable; availability inferred from endpoint state (may be less accurate).")
        else:
            return {
                "status": "error",
                "message": "Unable to retrieve device state or endpoint state from ARI",
                "target": target,
                "device_state_id": resolved_id,
                "device_state_error": device_state_error,
            }

        # Always return an explicit availability message so downstream sanitizers
        # still preserve a clear model-readable signal.
        resolved_extension = extension or target
        availability_text = "available" if available else "in use"
        availability_detail = str(availability_reason)
        availability_message = (
            f"Extension {resolved_extension} is {availability_text} "
            f"({availability_detail})."
        )

        # Resolve any additional (e.g., custom DND/away tracking) device states configured
        # for this extension and aggregate them together with the native device state into
        # a single, labeled availability decision (#577).
        mapping = resolve_state_mapping(
            context.get_config_value("tools.check_extension_status.state_mapping", {}) or {}
        )

        active_channels_sources = {"device_state+endpoint_channels", "endpoint_state+endpoint_channels"}
        lookup_failed_sources = {
            "device_state+endpoint_channels_lookup_failed",
            "endpoint_state+endpoint_channels_lookup_failed",
        }
        if availability_source in active_channels_sources:
            # The #595 active-channel cross-check flipped an apparently-free native state to
            # busy; represent that explicitly so aggregation labels it "in_call".
            native_state, native_classification = "ACTIVE_CHANNELS", "busy"
        elif availability_source in lookup_failed_sources:
            native_state, native_classification = (state_norm or "UNAVAILABLE"), "unavailable"
        elif state_norm:
            native_state, native_classification = state_norm, classify_device_state(state_norm, mapping)
        else:
            native_state = endpoint_state.strip().upper() if endpoint_state else ""
            native_classification = "free" if available else "unavailable"

        configured_device_states = ext_entry.get("device_states") if isinstance(ext_entry, dict) else None
        configured_custom_states = [
            ds for ds in configured_device_states if isinstance(ds, dict)
        ] if isinstance(configured_device_states, list) else []

        # If resolved_id matches one of the extension's own configured custom device_states
        # (the #577 guardrail carve-out), it is the SAME state as that entry -- represent it
        # exactly once, as role="custom" with the entry's configured status, instead of also
        # emitting a "native" record (which would mislabel a busy custom state, e.g. treat a
        # DND state as an active call) and re-querying/duplicating it in the loop below (#600).
        matching_configured = next(
            (ds for ds in configured_custom_states if str(ds.get("id", "") or "").strip() == resolved_id),
            None,
        )
        if matching_configured is not None:
            device_state_records: List[Dict[str, Any]] = [{
                "id": resolved_id,
                "role": "custom",
                "state": native_state,
                "classification": _classify_custom_device_state(native_state, mapping),
                "status": str(matching_configured.get("status", "") or "").strip(),
            }]
        else:
            device_state_records = [{
                "id": resolved_id,
                "role": "native",
                "state": native_state,
                "classification": native_classification,
            }]

        for ds in configured_custom_states:
            ds_id = str(ds.get("id", "") or "").strip()
            if not ds_id or ds_id == resolved_id:
                continue
            ds_status = str(ds.get("status", "") or "").strip()
            ds_state_norm = (await _query_custom_device_state(context=context, device_state_id=ds_id)).strip().upper()
            device_state_records.append({
                "id": ds_id,
                "role": "custom",
                "state": ds_state_norm,
                "classification": _classify_custom_device_state(ds_state_norm, mapping),
                "status": ds_status,
            })

        aggregated = aggregate_availability(device_state_records)
        has_custom_states = any(r.get("role") == "custom" for r in device_state_records)

        available = aggregated["available"]
        availability_status = aggregated["availability_status"]
        availability_reason_field = aggregated["availability_reason"]

        # Always rebuild the message from the FINAL availability_status so it never
        # contradicts `available`, whether or not custom device_states are configured.
        status_messages = {
            "available": f"Extension {resolved_extension} is available.",
            "in_call": f"Extension {resolved_extension} is on a call.",
            "dnd": f"Extension {resolved_extension} is on Do Not Disturb.",
            "away": f"Extension {resolved_extension} is away.",
            "on_hold": f"Extension {resolved_extension} is on hold.",
            "ringing": f"Extension {resolved_extension} is ringing.",
            "unavailable": f"Extension {resolved_extension} is unavailable.",
            "busy": f"Extension {resolved_extension} is busy.",
        }
        availability_message = status_messages.get(
            availability_status, f"Extension {resolved_extension} is unavailable."
        )

        if has_custom_states:
            availability_source = "device_states_aggregate"

        result = {
            "status": "success",
            "target": target,
            "extension": extension,
            "device_state_id": resolved_id,
            "resolution_source": source or ext_source,
            "extension_resolution_source": ext_source,
            "device_state_name": name or resolved_id,
            "device_state": state_norm or state,
            "available": available,
            "availability_source": availability_source,
            "availability_status": availability_status,
            "availability_reason": availability_reason_field,
            "device_states": device_state_records,
            "message": availability_message,
        }

        if destination_source:
            result["destination_resolution_source"] = destination_source
            result["destination_key"] = target
            result["destination_target"] = resolved_ext
            # Keep a small subset of destination config to avoid leaking large blobs.
            result["destination_type"] = str(destination_cfg.get("type", "") or "")

        if endpoint_state:
            result["endpoint_state"] = endpoint_state
            result["endpoint_channel_ids"] = endpoint_channel_ids
            if used_tech:
                result["tech"] = used_tech

        if warnings:
            result["warnings"] = warnings

        logger.info(
            "Extension device state",
            call_id=context.call_id,
            target=target,
            extension=extension,
            device_state_id=resolved_id,
            state=state_norm or state,
            available=available,
            source=source or ext_source,
            endpoint_state=endpoint_state or None,
        )
        return result
