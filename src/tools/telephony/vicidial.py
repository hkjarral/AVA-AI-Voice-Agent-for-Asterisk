"""
ViciDial Remote Agent integration helpers.

This module is intentionally housed with telephony tools because existing
hangup/transfer tools delegate to it when the active call is a ViciDial Remote
Agent call. ViciDial itself is configured as an integration, not exposed as an
AI-callable tool.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any, Dict, Optional, Tuple

import aiohttp
import structlog

logger = structlog.get_logger(__name__)

REMOVED_OUTBOUND_VICIDIAL_MESSAGE = (
    "AAVA_OUTBOUND_PBX_TYPE=vicidial has been removed. "
    "Use the ViciDial Remote Agent integration instead; see "
    "docs/Vicidial-Migration-From-Experimental-Outbound.md."
)
VICIDIAL_UNSUPPORTED_TOOL_NAMES = {"attended_transfer", "transfer_call", "transfer_to_queue"}
VICIDIAL_EXTRA_CHANNEL_VARS = ["VICIDIAL_RA_AGENT_USER", "VICIDIAL_SOURCE", "VICIDIAL_CAMPAIGN_ID", "VICIDIAL_INGROUP"]


class VicidialConfigError(ValueError):
    """Raised when ViciDial integration config is invalid."""


@dataclass
class VicidialSession:
    """Per-call ViciDial Remote Agent metadata."""

    call_id: str
    agent_user: str
    source: str = "channel_vars"


@dataclass
class VicidialApiResult:
    success: bool
    message: str
    raw: str
    status: str = "success"


def _as_dict(config: Any) -> Dict[str, Any]:
    if config is None:
        return {}
    if isinstance(config, dict):
        return config
    if hasattr(config, "dict"):
        try:
            return config.dict()
        except Exception:
            pass
    if hasattr(config, "model_dump"):
        try:
            return config.model_dump()
        except Exception:
            pass
    return getattr(config, "__dict__", {}) or {}


def get_vicidial_config(config: Any) -> Dict[str, Any]:
    cfg = _as_dict(config)
    integrations = cfg.get("integrations") or {}
    if not isinstance(integrations, dict):
        return {}
    vicidial = integrations.get("vicidial") or {}
    return vicidial if isinstance(vicidial, dict) else {}


def is_enabled(config: Any) -> bool:
    return bool(get_vicidial_config(config).get("enabled"))


def filter_tool_names_for_vicidial_session(tool_names: Any, session: Any) -> list[str]:
    """Hide tools that would bypass ViciDial-owned call control."""
    names = list(tool_names or [])
    if not getattr(session, "vicidial_session", None):
        return names
    return [name for name in names if name not in VICIDIAL_UNSUPPORTED_TOOL_NAMES]


async def read_vicidial_channel_vars(ari_client: Any, channel_id: str) -> Dict[str, str]:
    """Read ViciDial channel vars, short-circuiting non-ViciDial calls after one probe."""
    vicidial_vars: Dict[str, str] = {}
    try:
        resp = await ari_client.send_command(
            "GET",
            f"channels/{channel_id}/variable",
            params={"variable": "VICIDIAL_RA_CALL_ID"},
            tolerate_statuses=[404],
        )
        if isinstance(resp, dict):
            call_id = (resp.get("value") or "").strip()
            if call_id:
                vicidial_vars["VICIDIAL_RA_CALL_ID"] = call_id
    except Exception:
        logger.debug("Failed to read ViciDial Remote Agent call ID", call_id=channel_id, exc_info=True)
        return {}

    if not vicidial_vars:
        return {}

    for var_name in VICIDIAL_EXTRA_CHANNEL_VARS:
        try:
            resp = await ari_client.send_command(
                "GET",
                f"channels/{channel_id}/variable",
                params={"variable": var_name},
                tolerate_statuses=[404],
            )
            if isinstance(resp, dict):
                value = (resp.get("value") or "").strip()
                if value:
                    vicidial_vars[var_name] = value
        except Exception:
            logger.debug(
                "Failed to read ViciDial Remote Agent channel var",
                call_id=channel_id,
                variable=var_name,
                exc_info=True,
            )
    return vicidial_vars


def resolve_env_reference(value: Any, env_map: Optional[Dict[str, str]] = None) -> str:
    """Resolve simple env refs used by integration config values."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}", raw)
    if match:
        source = env_map if env_map is not None else os.environ
        env_value = source.get(match.group(1), "")
        return (env_value if env_value else (match.group(2) or "")).strip()
    if env_map is not None:
        out = raw
        for key, env_value in env_map.items():
            out = out.replace(f"${{{key}}}", env_value)
            out = out.replace(f"${key}", env_value)
        return out.strip()
    resolved = os.path.expandvars(raw).strip()
    if "$" in resolved:
        return ""
    return resolved


def _resolve_secret(value: Any) -> str:
    return resolve_env_reference(value)


def _validate_status_code(value: Any, field: str) -> None:
    raw = str(value or "").strip()
    if not raw:
        raise VicidialConfigError(f"integrations.vicidial.status_codes.{field} is required")
    if len(raw) > 6:
        raise VicidialConfigError(
            f"integrations.vicidial.status_codes.{field} must be at most 6 characters"
        )


def validate_vicidial_config(config_data: Dict[str, Any]) -> None:
    """Validate integrations.vicidial when enabled."""
    vicidial = get_vicidial_config(config_data)
    if not bool(vicidial.get("enabled")):
        return

    api_url = str(vicidial.get("api_url") or "").strip()
    if not api_url:
        raise VicidialConfigError("integrations.vicidial.api_url is required when enabled")

    source = str(vicidial.get("source") or "").strip()
    if not source:
        raise VicidialConfigError("integrations.vicidial.source is required when enabled")

    if not _resolve_secret(vicidial.get("user")):
        raise VicidialConfigError(
            "integrations.vicidial.user must resolve to a non-empty value "
            "(set VICIDIAL_API_USER in .env)"
        )
    if not _resolve_secret(vicidial.get("pass")):
        raise VicidialConfigError(
            "integrations.vicidial.pass must resolve to a non-empty value "
            "(set VICIDIAL_API_PASS in .env)"
        )

    status_codes = vicidial.get("status_codes") or {}
    if not isinstance(status_codes, dict):
        raise VicidialConfigError("integrations.vicidial.status_codes must be a mapping")
    for key in ("ai_hangup", "ai_ingroup_transfer", "ai_extension_transfer"):
        _validate_status_code(status_codes.get(key), key)

    destinations = vicidial.get("destinations") or {}
    if not isinstance(destinations, dict):
        raise VicidialConfigError("integrations.vicidial.destinations must be a mapping")
    for key, dest in destinations.items():
        if not isinstance(dest, dict):
            raise VicidialConfigError(f"integrations.vicidial.destinations.{key} must be a mapping")
        dest_type = str(dest.get("type") or "").strip().lower()
        if dest_type not in {"ingroup", "extension"}:
            raise VicidialConfigError(
                f"integrations.vicidial.destinations.{key}.type must be ingroup or extension"
            )
        if dest_type == "ingroup" and not str(dest.get("ingroup_choices") or "").strip():
            raise VicidialConfigError(
                f"integrations.vicidial.destinations.{key}.ingroup_choices is required"
            )
        if dest_type == "extension" and not str(dest.get("phone_number") or "").strip():
            raise VicidialConfigError(
                f"integrations.vicidial.destinations.{key}.phone_number is required"
            )

    default_destination = str(vicidial.get("default_live_agent_destination") or "").strip()
    if default_destination and default_destination not in destinations:
        raise VicidialConfigError(
            "integrations.vicidial.default_live_agent_destination must match a configured destination"
        )


def build_session_from_channel_vars(
    channel_vars: Dict[str, Any],
    config: Any,
) -> Optional[VicidialSession]:
    call_id = str(channel_vars.get("VICIDIAL_RA_CALL_ID") or "").strip()
    if not call_id:
        return None

    agent_user = str(channel_vars.get("VICIDIAL_RA_AGENT_USER") or "").strip()
    source = "channel_vars"
    if not agent_user:
        fallback = str(get_vicidial_config(config).get("default_agent_user") or "").strip()
        if fallback:
            agent_user = fallback
            source = "default_agent_user"
            logger.warning(
                "Using integrations.vicidial.default_agent_user for ViciDial call; "
                "set VICIDIAL_RA_AGENT_USER per call in dialplan for production",
                vicidial_call_id=call_id,
                docs="docs/Vicidial-Setup.md",
            )

    if not agent_user:
        logger.error(
            "ViciDial call detected but VICIDIAL_RA_AGENT_USER is missing",
            vicidial_call_id=call_id,
        )
        return None

    return VicidialSession(call_id=call_id, agent_user=agent_user, source=source)


def resolve_destination(config: Any, requested: Optional[str]) -> Tuple[Optional[str], Optional[Dict[str, Any]], str]:
    vicidial = get_vicidial_config(config)
    destinations = vicidial.get("destinations") or {}
    if not isinstance(destinations, dict) or not destinations:
        return None, None, "no_destinations"

    key = str(requested or "").strip()
    if not key:
        key = str(vicidial.get("default_live_agent_destination") or "").strip()
    if key and key in destinations and isinstance(destinations[key], dict):
        return key, dict(destinations[key]), "exact_key"

    normalized = " ".join(key.lower().replace("_", " ").replace("-", " ").split())
    if normalized:
        for candidate, dest in destinations.items():
            if not isinstance(dest, dict):
                continue
            cand_norm = " ".join(str(candidate).lower().replace("_", " ").replace("-", " ").split())
            desc_norm = " ".join(str(dest.get("description") or "").lower().split())
            if cand_norm == normalized or (desc_norm and desc_norm == normalized):
                return str(candidate), dict(dest), "normalized"

    return None, None, "not_found"


def status_code(config: Any, action: str) -> str:
    defaults = {
        "ai_hangup": "AIHU",
        "ai_ingroup_transfer": "AIXFR",
        "ai_extension_transfer": "AIEXT",
    }
    codes = get_vicidial_config(config).get("status_codes") or {}
    if not isinstance(codes, dict):
        codes = {}
    return str(codes.get(action) or defaults[action]).strip()


class VicidialAgentApiClient:
    """Small async client for ViciDial Agent API ra_call_control."""

    def __init__(self, config: Any, *, session_factory: Any = None):
        self._config = get_vicidial_config(config)
        self._session_factory = session_factory or aiohttp.ClientSession

    def _base_params(self, session: VicidialSession) -> Dict[str, str]:
        return {
            "source": str(self._config.get("source") or "aava").strip(),
            "user": _resolve_secret(self._config.get("user")),
            "pass": _resolve_secret(self._config.get("pass")),
            "agent_user": session.agent_user,
            "function": "ra_call_control",
            "value": session.call_id,
        }

    async def call_control(
        self,
        session: VicidialSession,
        *,
        stage: str,
        status: str,
        ingroup_choices: Optional[str] = None,
        phone_number: Optional[str] = None,
    ) -> VicidialApiResult:
        params = self._base_params(session)
        params["stage"] = stage
        if status:
            params["status"] = status
        if ingroup_choices:
            params["ingroup_choices"] = ingroup_choices
        if phone_number:
            params["phone_number"] = phone_number

        timeout_ms = int(self._config.get("timeout_ms") or 5000)
        verify_ssl = bool(self._config.get("verify_ssl", True))
        api_url = str(self._config.get("api_url") or "").strip()

        safe_params = dict(params)
        safe_params["pass"] = "***"
        logger.info(
            "Calling ViciDial ra_call_control",
            stage=stage,
            params=safe_params,
            url=api_url,
        )

        timeout = aiohttp.ClientTimeout(total=max(timeout_ms, 1) / 1000)
        try:
            async with self._session_factory() as http_session:
                async with http_session.get(
                    api_url,
                    params=params,
                    timeout=timeout,
                    ssl=verify_ssl,
                ) as response:
                    raw = (await response.text()).strip()
        except Exception as exc:
            logger.warning("ViciDial ra_call_control request failed", stage=stage, error=str(exc))
            return VicidialApiResult(
                success=False,
                status="error",
                message=f"ViciDial API request failed: {exc}",
                raw=str(exc),
            )

        success = raw.upper().startswith("SUCCESS:")
        return VicidialApiResult(
            success=success,
            status="success" if success else "error",
            message=raw,
            raw=raw,
        )

    async def test_connection(self) -> VicidialApiResult:
        session = VicidialSession(call_id="AAVA_INVALID_TEST_CALL_ID", agent_user=str(self._config.get("default_agent_user") or "AAVA_TEST"))
        result = await self.call_control(
            session,
            stage="HANGUP",
            status=status_code({"integrations": {"vicidial": self._config}}, "ai_hangup"),
        )
        if is_expected_test_connection_response(result.raw):
            return VicidialApiResult(
                success=True,
                status="success",
                message="ViciDial API reachable and credentials/source were accepted",
                raw=result.raw,
            )
        return result


def is_expected_test_connection_response(raw: str) -> bool:
    """Return True for the expected invalid-call response from the connection probe."""
    raw_upper = str(raw or "").strip().upper()
    if not raw_upper.startswith("ERROR:"):
        return False
    expected_markers = (
        "NO ACTIVE CALL",
        "NO LIVE CALL",
        "DOES NOT HAVE A LIVE CALL",
        "INVALID CALL ID",
        "CALL ID NOT FOUND",
        "CALL NOT FOUND",
    )
    return any(marker in raw_upper for marker in expected_markers)
