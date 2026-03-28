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
            if device_state_id:
                extracted_extension = _extract_extension_from_device_state_id(device_state_id)
                if extracted_extension:
                    extension_for_guardrail = extracted_extension

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
            if device_state_id and not _looks_like_extension_number(extension_for_guardrail):
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
        if isinstance(endpoint_info, dict):
            endpoint_state = str(endpoint_info.get("state", "") or "").strip()
            endpoint_channel_ids = [str(x) for x in (endpoint_info.get("channel_ids") or []) if x is not None]

        warnings: List[str] = []
        availability_source = ""
        if state_norm:
            # Conservative availability mapping:
            # - NOT_INUSE is clearly available.
            # - INUSE/BUSY/RINGING/UNAVAILABLE are not.
            available = state_norm == "NOT_INUSE"
            availability_source = "device_state"
        elif endpoint_state:
            # Endpoint state is weaker (online/offline). We treat "online + no channels" as available.
            available = endpoint_state.lower() == "online" and len(endpoint_channel_ids) == 0
            availability_source = "endpoint_state"
            warnings.append("Device state unavailable; availability inferred from endpoint state (may be less accurate).")
        else:
            return {
                "status": "error",
                "message": "Unable to retrieve device state or endpoint state from ARI",
                "target": target,
                "device_state_id": resolved_id,
                "device_state_error": device_state_error,
            }

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
