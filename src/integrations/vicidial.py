"""VICIdial Remote Agent API adapter and per-call resolution helpers.

VICIdial remains the authoritative owner of campaign state, customer channels,
Remote Agent state, dispositions, callbacks, DNC records, and transfers.  AAVA
uses the public Agent and Non-Agent APIs and never writes production VICIdial
tables directly.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import structlog

logger = structlog.get_logger(__name__)

_STATUS_RE = re.compile(r"^[A-Za-z0-9_-]{1,6}$")
_CALL_ID_RE = re.compile(r"^(?:Y|J|V|M|DC|S|LP|VH|XL)[A-Za-z0-9_.:-]+$")
_ENV_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}$")
_REMOTE_AGENT_CORRELATION_MAX_SECONDS = 10.0
_REMOTE_AGENT_STATUS_CONCURRENCY = 10


class VicidialIntegrationError(ValueError):
    """Raised for invalid configuration or unusable API responses."""


@dataclass
class VicidialApiResult:
    success: bool
    function: str
    message: str
    raw_preview: str = ""
    http_status: Optional[int] = None
    data: Dict[str, Any] = field(default_factory=dict)
    rows: List[Dict[str, str]] = field(default_factory=list)
    error_code: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VicidialSessionInfo:
    external_call_id: str
    mapping_id: str
    agent_user: str
    campaign_id: Optional[str] = None
    lead_id: Optional[str] = None
    list_id: Optional[str] = None
    phone_number: Optional[str] = None
    call_type: Optional[str] = None
    vicidial_status: Optional[str] = None
    direction: str = "outbound"
    resolution_source: str = "callid_info"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def resolve_env_reference(value: Any, env: Optional[Mapping[str, str]] = None) -> str:
    """Resolve an environment variable name or ${NAME} reference."""
    source = env if env is not None else os.environ
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = _ENV_RE.fullmatch(raw)
    if match:
        return str(source.get(match.group(1)) or match.group(2) or "").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
        return str(source.get(raw) or "").strip()
    return ""


def validate_status(status: Any, *, field_name: str = "status") -> str:
    value = str(status or "").strip().upper()
    if not _STATUS_RE.fullmatch(value):
        raise VicidialIntegrationError(
            f"{field_name} must be 1-6 letters, numbers, underscores, or hyphens"
        )
    return value


def validate_call_id(call_id: Any) -> str:
    value = str(call_id or "").strip()
    # Mirrors the installed Non-Agent API's callid_info guard: 18-39
    # characters and a ViciDial caller-code prefix. Rejecting ordinary
    # Asterisk uniqueids prevents an untrusted leg from being misclassified.
    if not (18 <= len(value) <= 39 and _CALL_ID_RE.fullmatch(value)):
        raise VicidialIntegrationError("VICIdial call ID is missing or invalid")
    return value


def _bounded_preview(raw: Any, limit: int = 2000) -> str:
    text = str(raw or "").replace("\x00", "").strip()
    return text[:limit]


def _redact_value(text: str, value: str) -> str:
    return text.replace(value, "[REDACTED]") if value else text


def _parse_delimited_rows(raw: str) -> List[Dict[str, str]]:
    """Parse VICIdial pipe/comma header responses defensively."""
    lines = [line.strip() for line in str(raw or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    for delimiter in ("|", ",", "\t"):
        if delimiter not in lines[0]:
            continue
        headers = [part.strip().lower() for part in lines[0].split(delimiter)]
        if len(headers) < 2:
            continue
        rows: List[Dict[str, str]] = []
        for line in lines[1:]:
            values = [part.strip() for part in line.split(delimiter)]
            rows.append({
                key: values[index] if index < len(values) else ""
                for index, key in enumerate(headers)
                if key
            })
        return rows
    return []


def _parse_delimited(raw: str) -> Dict[str, str]:
    rows = _parse_delimited_rows(raw)
    return rows[0] if rows else {}


def _parse_success(raw: str) -> bool:
    text = str(raw or "").lstrip().upper()
    if text.startswith("SUCCESS:") or text.startswith("VERSION:"):
        return True
    if text.startswith("ERROR:"):
        return False
    if text.startswith("NOTICE:"):
        return " NOT " not in text and " FAILED" not in text
    return bool(_parse_delimited(raw))


def normalize_callback_datetime(value: Any, timezone_name: str) -> str:
    """Return a ViciDial server-local ``YYYY-MM-DD HH:MM:SS`` value.

    Offset-aware input is converted to the configured ViciDial timezone.
    Naive input is deliberately treated as already being in that timezone,
    matching how an operator enters a callback in the ViciDial agent UI.
    """
    raw = str(value or "").strip()
    if not raw:
        raise VicidialIntegrationError("Callback date/time is required")
    try:
        zone = ZoneInfo(str(timezone_name or "UTC").strip() or "UTC")
    except ZoneInfoNotFoundError as exc:
        raise VicidialIntegrationError("VICIdial timezone is invalid") from exc
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VicidialIntegrationError(
            "Callback date/time must be an ISO date/time"
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(zone).replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


class VicidialApiClient:
    """Async, redacting adapter for VICIdial Agent and Non-Agent APIs."""

    def __init__(self, connection: Mapping[str, Any], *, session_factory: Any = None):
        self.connection = dict(connection or {})
        self._session_factory = session_factory or aiohttp.ClientSession

        base_url = str(self.connection.get("base_url") or "").strip().rstrip("/") + "/"
        self.agent_api_url = str(
            self.connection.get("agent_api_url")
            or urljoin(base_url, "agc/api.php")
        ).strip()
        self.non_agent_api_url = str(
            self.connection.get("non_agent_api_url")
            or urljoin(base_url, "vicidial/non_agent_api.php")
        ).strip()
        if not self.agent_api_url.startswith(("http://", "https://")):
            raise VicidialIntegrationError("VICIdial Agent API URL must use http or https")
        if not self.non_agent_api_url.startswith(("http://", "https://")):
            raise VicidialIntegrationError("VICIdial Non-Agent API URL must use http or https")

        self.source = str(self.connection.get("source") or "aava").strip()[:20]
        self.user = resolve_env_reference(self.connection.get("username_env"))
        self.password = resolve_env_reference(self.connection.get("password_env"))
        self.timeout_ms = max(250, min(int(self.connection.get("timeout_ms") or 5000), 30000))
        self.verify_ssl = bool(self.connection.get("verify_ssl", True))

    def credentials_ready(self) -> bool:
        return bool(self.user and self.password and self.source)

    def _auth(self, function: str) -> Dict[str, str]:
        if not self.credentials_ready():
            raise VicidialIntegrationError(
                "VICIdial API credential environment references are not resolved"
            )
        return {
            "source": self.source,
            "user": self.user,
            "pass": self.password,
            "function": function,
        }

    async def _request(
        self,
        *,
        api: str,
        function: str,
        params: Optional[Mapping[str, Any]] = None,
        authenticated: bool = True,
    ) -> VicidialApiResult:
        url = self.agent_api_url if api == "agent" else self.non_agent_api_url
        payload: Dict[str, str] = self._auth(function) if authenticated else {"function": function}
        for key, value in dict(params or {}).items():
            if value is not None and str(value).strip() != "":
                payload[str(key)] = str(value)

        safe_keys = sorted(key for key in payload if key not in {"pass", "password"})
        logger.info(
            "VICIdial API request",
            api=api,
            function=function,
            url=url,
            parameter_keys=safe_keys,
        )
        timeout = aiohttp.ClientTimeout(total=self.timeout_ms / 1000.0)
        try:
            async with self._session_factory(timeout=timeout) as session:
                async with session.post(url, data=payload, ssl=self.verify_ssl) as response:
                    raw = await response.text()
                    success = 200 <= response.status < 300 and _parse_success(raw)
                    preview = _redact_value(_bounded_preview(raw), self.password)
                    rows = _parse_delimited_rows(raw)
                    return VicidialApiResult(
                        success=success,
                        function=function,
                        message=preview or f"HTTP {response.status}",
                        raw_preview=preview,
                        http_status=response.status,
                        data=rows[0] if rows else {},
                        rows=rows,
                        error_code=None if success else "api_error",
                    )
        except asyncio.TimeoutError:
            return VicidialApiResult(
                False, function, "VICIdial API request timed out", error_code="timeout"
            )
        except aiohttp.ClientError as exc:
            return VicidialApiResult(
                False,
                function,
                f"VICIdial API request failed: {type(exc).__name__}",
                error_code="network_error",
            )
        except Exception as exc:
            logger.warning(
                "VICIdial API request failed",
                api=api,
                function=function,
                error_type=type(exc).__name__,
            )
            return VicidialApiResult(
                False,
                function,
                f"VICIdial API request failed: {type(exc).__name__}",
                error_code="request_error",
            )

    async def version(self, *, api: str = "agent") -> VicidialApiResult:
        return await self._request(api=api, function="version", authenticated=False)

    async def campaigns_list(self) -> VicidialApiResult:
        return await self._request(
            api="non_agent",
            function="campaigns_list",
            params={"stage": "pipe", "header": "YES"},
        )

    async def callid_info(self, call_id: str) -> VicidialApiResult:
        return await self._request(
            api="non_agent",
            function="callid_info",
            params={
                "call_id": validate_call_id(call_id),
                "detail": "YES",
                "stage": "pipe",
                "header": "YES",
            },
        )

    async def agent_status(self, agent_user: str) -> VicidialApiResult:
        return await self._request(
            api="non_agent",
            function="agent_status",
            params={"agent_user": str(agent_user).strip(), "stage": "pipe", "header": "YES"},
        )

    async def logged_in_agents(self) -> VicidialApiResult:
        return await self._request(
            api="non_agent",
            function="logged_in_agents",
            params={"stage": "pipe", "header": "YES", "show_sub_status": "YES"},
        )

    async def call_control(
        self,
        session: VicidialSessionInfo,
        *,
        stage: str,
        status: str,
        ingroup_choices: Optional[str] = None,
        phone_number: Optional[str] = None,
    ) -> VicidialApiResult:
        stage_value = str(stage or "").strip().upper()
        if stage_value not in {"HANGUP", "INGROUPTRANSFER", "EXTENSIONTRANSFER"}:
            raise VicidialIntegrationError("Unsupported VICIdial Remote Agent call-control stage")
        params: Dict[str, Any] = {
            "agent_user": session.agent_user,
            "value": validate_call_id(session.external_call_id),
            "stage": stage_value,
            "status": validate_status(status),
        }
        if stage_value == "INGROUPTRANSFER":
            params["ingroup_choices"] = str(ingroup_choices or "").strip()
            if not params["ingroup_choices"]:
                raise VicidialIntegrationError("VICIdial in-group destination is required")
        if stage_value == "EXTENSIONTRANSFER":
            params["phone_number"] = str(phone_number or "").strip()
            if len(params["phone_number"]) < 2:
                raise VicidialIntegrationError("VICIdial extension destination is required")
        return await self._request(api="agent", function="ra_call_control", params=params)

    async def add_dnc_phone(
        self, *, phone_number: str, campaign_id: str
    ) -> VicidialApiResult:
        result = await self._request(
            api="non_agent",
            function="add_dnc_phone",
            params={"phone_number": phone_number, "campaign_id": campaign_id or "SYSTEM_INTERNAL"},
        )
        # The API reports an existing DNC row as ERROR even though the desired
        # compliance state is already satisfied. Normalize that case so retries
        # are idempotent while retaining the original response as evidence.
        if not result.success and "DNC NUMBER ALREADY EXISTS" in result.message.upper():
            result.success = True
            result.error_code = None
            result.data = {**result.data, "already_exists": True}
        return result

    async def update_lead_callback(
        self,
        *,
        lead_id: str,
        campaign_id: str,
        callback_datetime: str,
        callback_status: str,
        callback_type: str = "ANYONE",
        callback_user: Optional[str] = None,
        callback_comments: Optional[str] = None,
    ) -> VicidialApiResult:
        recipient = str(callback_type or "ANYONE").strip().upper()
        if recipient not in {"ANYONE", "USERONLY"}:
            raise VicidialIntegrationError("Callback type must be ANYONE or USERONLY")
        params: Dict[str, Any] = {
            "lead_id": str(lead_id).strip(),
            "campaign_id": str(campaign_id).strip(),
            "callback": "Y",
            "callback_datetime": str(callback_datetime).strip(),
            "callback_status": validate_status(callback_status, field_name="callback_status"),
            "callback_type": recipient,
            "callback_comments": str(callback_comments or "").strip()[:200],
        }
        if recipient == "USERONLY":
            params["callback_user"] = str(callback_user or "").strip()
            if not params["callback_user"]:
                raise VicidialIntegrationError("Callback user is required for USERONLY callbacks")
        return await self._request(api="non_agent", function="update_lead", params=params)

    async def lead_callback_info(self, *, lead_id: str) -> VicidialApiResult:
        return await self._request(
            api="non_agent",
            function="lead_callback_info",
            params={
                "lead_id": str(lead_id).strip(),
                "stage": "pipe",
                "header": "YES",
                "search_location": "CURRENT",
            },
        )

    async def verify_connection(self) -> Dict[str, Any]:
        agent_version = await self.version(api="agent")
        non_agent_version = await self.version(api="non_agent")
        if self.credentials_ready():
            auth = await self.campaigns_list()
            agents = await self.logged_in_agents()
        else:
            auth = VicidialApiResult(
                False,
                "campaigns_list",
                "Credential environment references are unresolved",
                error_code="credentials",
            )
            agents = VicidialApiResult(
                False,
                "logged_in_agents",
                "Credential environment references are unresolved",
                error_code="credentials",
            )
        return {
            "ready": bool(
                agent_version.success
                and non_agent_version.success
                and auth.success
                and agents.success
            ),
            "credentials_resolved": self.credentials_ready(),
            "agent_api": agent_version.to_dict(),
            "non_agent_api": non_agent_version.to_dict(),
            "authentication": auth.to_dict(),
            "agent_visibility": agents.to_dict(),
            "required_functions": [
                "campaigns_list",
                "callid_info",
                "agent_status",
                "logged_in_agents",
                "ra_call_control",
                "add_dnc_phone",
                "update_lead",
                "lead_callback_info",
            ],
            "mutating_functions_probed": False,
        }

    async def resolve_remote_agent_session(
        self,
        *,
        call_id: str,
        mapping: Mapping[str, Any],
        attempts: int = 5,
        delay_seconds: float = 0.5,
    ) -> tuple[Optional[VicidialSessionInfo], list[Dict[str, Any]]]:
        """Correlate one trusted Remote Agent leg within a hard admission deadline.

        ``attempts`` and ``delay_seconds`` are best-effort retry controls. They
        may not all be consumed when VICIdial is slow because customer calls
        must fail closed within ``_REMOTE_AGENT_CORRELATION_MAX_SECONDS``.
        """
        evidence: list[Dict[str, Any]] = []
        start_user = int(str(mapping.get("user_start") or "0"))
        line_count = max(1, int(mapping.get("number_of_lines") or 1))
        allowed_users = {str(start_user + index) for index in range(line_count)}
        attempt_count = max(1, int(attempts))
        retry_delay = max(0.05, float(delay_seconds))
        loop = asyncio.get_running_loop()
        retry_delay_budget = retry_delay * (attempt_count - 1)
        correlation_timeout = max(
            0.5,
            min(
                _REMOTE_AGENT_CORRELATION_MAX_SECONDS,
                max(
                    (self.timeout_ms / 1000.0) * 2,
                    retry_delay_budget + 0.5,
                ),
            ),
        )
        correlation_deadline = loop.time() + correlation_timeout

        async def within_deadline(factory: Any) -> Any:
            remaining = correlation_deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                return await asyncio.wait_for(factory(), timeout=remaining)
            except asyncio.TimeoutError:
                return None

        async def agent_status_batch(users: Iterable[str]) -> Optional[List[VicidialApiResult]]:
            remaining = correlation_deadline - loop.time()
            if remaining <= 0:
                return None
            semaphore = asyncio.Semaphore(_REMOTE_AGENT_STATUS_CONCURRENCY)

            async def fetch(user: str) -> VicidialApiResult:
                async with semaphore:
                    return await self.agent_status(user)

            try:
                return await asyncio.wait_for(
                    asyncio.gather(*(fetch(user) for user in users)),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                return None

        def correlation_timed_out() -> tuple[None, list[Dict[str, Any]]]:
            evidence.append(
                VicidialApiResult(
                    success=False,
                    function="remote_agent_correlation",
                    message="VICIdial Remote Agent correlation deadline exceeded",
                    error_code="correlation_timeout",
                ).to_dict()
            )
            return None, evidence

        async def retry_pause() -> bool:
            remaining = correlation_deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(retry_delay, remaining))
            return correlation_deadline - loop.time() > 0

        def build_session(
            external_call_id: str,
            user: str,
            call_result: VicidialApiResult,
            agent_result: VicidialApiResult,
            *,
            resolution_source: str,
        ) -> Optional[VicidialSessionInfo]:
            call_type = str(call_result.data.get("call_type") or "").strip().upper()
            direction = "inbound" if call_type.startswith("IN") else "outbound"
            configured_direction = str(mapping.get("direction") or "outbound").lower()
            agent_campaign = str(agent_result.data.get("campaign_id") or "").strip()
            call_campaign = str(call_result.data.get("campaign_id") or "").strip()
            call_user = str(call_result.data.get("user") or "").strip()
            agent_phone = str(agent_result.data.get("phone_number") or "").strip()
            call_phone = str(call_result.data.get("phone") or "").strip()
            if agent_phone and call_phone and agent_phone != call_phone:
                return None
            configured_campaign = str(mapping.get("campaign_id") or "").strip()
            closer_campaigns = {
                str(value).strip()
                for value in mapping.get("closer_campaigns") or []
                if str(value).strip()
            }

            if configured_direction != "both" and configured_direction != direction:
                return None
            if direction == "outbound":
                # For outbound calls both APIs describe the dialing campaign.
                # Keep requiring agreement when both values are available.
                if agent_campaign and call_campaign and agent_campaign != call_campaign:
                    return None
                campaign_id = call_campaign or agent_campaign
                if configured_campaign and campaign_id != configured_campaign:
                    return None
            else:
                # In blended/closer mode agent_status reports the campaign the
                # agent logged into, while callid_info reports the inbound
                # group that delivered the call. They are distinct VICIdial
                # concepts and normally differ (for example AVATEST/AVAIN).
                # The closer group and the call-log user are the authoritative
                # inbound mapping checks.
                campaign_id = call_campaign
                if call_user and call_user != user:
                    return None
                if not closer_campaigns or campaign_id not in closer_campaigns:
                    return None

            return VicidialSessionInfo(
                external_call_id=external_call_id,
                mapping_id=str(mapping.get("id") or ""),
                agent_user=user,
                campaign_id=campaign_id or None,
                lead_id=str(agent_result.data.get("lead_id") or "").strip() or None,
                list_id=str(call_result.data.get("list_id") or "").strip() or None,
                phone_number=agent_phone or call_phone or None,
                call_type=call_type or None,
                vicidial_status=str(call_result.data.get("status") or "").strip() or None,
                direction=direction,
                resolution_source=resolution_source,
                metadata={
                    "callid_info": call_result.data,
                    "agent_status": agent_result.data,
                },
            )

        try:
            external_call_id = validate_call_id(call_id)
        except VicidialIntegrationError:
            external_call_id = ""

        # Preferred path: the Remote Agent SIP leg carries the VICIdial call
        # code in CallerID(name) or another dialplan-extracted identifier.
        # This is the cheapest and strongest correlation because both APIs
        # must independently report the exact same call code. The callid_info
        # ``user`` is commonly VDAD for outbound auto calls, so the Remote
        # Agent identity comes from agent_status instead.
        if external_call_id:
            for index in range(attempt_count):
                result = await within_deadline(
                    lambda: self.callid_info(external_call_id)
                )
                if result is None:
                    return correlation_timed_out()
                evidence.append(result.to_dict())
                if result.success:
                    candidates: list[VicidialSessionInfo] = []
                    users = sorted(allowed_users, key=int)
                    agent_results = await agent_status_batch(users)
                    if agent_results is None:
                        return correlation_timed_out()
                    for user, agent_result in zip(users, agent_results):
                        evidence.append(agent_result.to_dict())
                        agent_state = str(
                            agent_result.data.get("status") or ""
                        ).strip().upper()
                        agent_call_id = str(
                            agent_result.data.get("callerid")
                            or agent_result.data.get("call_id")
                            or ""
                        ).strip()
                        if (
                            not agent_result.success
                            or agent_state not in {"QUEUE", "INCALL"}
                            or agent_call_id != external_call_id
                        ):
                            continue
                        resolved = build_session(
                            external_call_id,
                            user,
                            result,
                            agent_result,
                            resolution_source="callid_info",
                        )
                        if resolved is not None:
                            candidates.append(resolved)

                    unique_users = {
                        candidate.agent_user: candidate for candidate in candidates
                    }
                    if len(unique_users) == 1:
                        return next(iter(unique_users.values())), evidence
                    if len(unique_users) > 1:
                        return None, evidence
                if index + 1 < attempt_count and not await retry_pause():
                    return correlation_timed_out()

            fallback = str(mapping.get("static_agent_user") or "").strip()
            if line_count == 1 and fallback and fallback in allowed_users:
                agent_result = await within_deadline(
                    lambda: self.agent_status(fallback)
                )
                if agent_result is None:
                    return correlation_timed_out()
                evidence.append(agent_result.to_dict())
                fallback_call_id = str(
                    agent_result.data.get("callerid")
                    or agent_result.data.get("call_id")
                    or ""
                ).strip()
                if agent_result.success and fallback_call_id == external_call_id:
                    # The preceding exact path already queried callid_info. The
                    # fallback remains deliberately limited to a one-line map
                    # and never admits a different call identifier.
                    result = await within_deadline(
                        lambda: self.callid_info(external_call_id)
                    )
                    if result is None:
                        return correlation_timed_out()
                    evidence.append(result.to_dict())
                    if result.success:
                        resolved = build_session(
                            external_call_id,
                            fallback,
                            result,
                            agent_result,
                            resolution_source="static_one_line_fallback",
                        )
                        if resolved is not None:
                            return resolved, evidence
            return None, evidence

        # Some VICIdial releases replace CallerID(name) with the customer name
        # before dialing the Remote Agent. In that case, discover the call code
        # only through the mapped agent range. Every candidate must be active,
        # carry a valid VICIdial code, be confirmed by callid_info, and match
        # the mapping's campaign/direction. Exactly one candidate is required.
        for index in range(attempt_count):
            candidates: list[VicidialSessionInfo] = []
            users = sorted(allowed_users, key=int)
            agent_results = await agent_status_batch(users)
            if agent_results is None:
                return correlation_timed_out()
            for user, agent_result in zip(users, agent_results):
                evidence.append(agent_result.to_dict())
                agent_state = str(agent_result.data.get("status") or "").strip().upper()
                candidate_call_id = str(
                    agent_result.data.get("callerid")
                    or agent_result.data.get("call_id")
                    or ""
                ).strip()
                if not agent_result.success or agent_state not in {"QUEUE", "INCALL"}:
                    continue
                try:
                    candidate_call_id = validate_call_id(candidate_call_id)
                except VicidialIntegrationError:
                    continue
                call_result = await within_deadline(
                    lambda: self.callid_info(candidate_call_id)
                )
                if call_result is None:
                    return correlation_timed_out()
                evidence.append(call_result.to_dict())
                if not call_result.success:
                    continue
                resolved = build_session(
                    candidate_call_id,
                    user,
                    call_result,
                    agent_result,
                    resolution_source="mapped_agent_status_scan",
                )
                if resolved is not None:
                    candidates.append(resolved)

            unique = {candidate.external_call_id: candidate for candidate in candidates}
            if len(unique) == 1:
                return next(iter(unique.values())), evidence
            if len(unique) > 1:
                return None, evidence
            if index + 1 < attempt_count and not await retry_pause():
                return correlation_timed_out()
        return None, evidence


def status_for(mapping: Mapping[str, Any], semantic: str, default: str) -> str:
    statuses = mapping.get("statuses") if isinstance(mapping.get("statuses"), Mapping) else {}
    return validate_status(statuses.get(semantic) or default, field_name=f"statuses.{semantic}")


def allowed_dispositions(mapping: Mapping[str, Any]) -> Dict[str, str]:
    raw = mapping.get("dispositions")
    if not isinstance(raw, Mapping):
        raw = {}
    result: Dict[str, str] = {}
    for semantic, status in raw.items():
        key = str(semantic or "").strip().lower()
        if key:
            result[key] = validate_status(status, field_name=f"dispositions.{key}")
    return result


def remote_agent_user_range(mapping: Mapping[str, Any]) -> Iterable[str]:
    start = int(str(mapping.get("user_start") or "0"))
    count = max(1, int(mapping.get("number_of_lines") or 1))
    return (str(start + index) for index in range(count))
