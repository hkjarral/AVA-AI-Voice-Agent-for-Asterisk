"""Read-only endpoint discovery and validation for assisted destination setup.

ARI lists devices, not a PBX directory. Nothing in this module changes config,
authorizes a transfer, or calls a call-control endpoint.
"""

import asyncio
import json
import re
import ssl
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr


DISCOVERY_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_ENDPOINTS = 5000
MAX_ADDITIONS = 100
KEY_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
TARGET_PATTERN = r"[0-9]{1,32}"
CONTEXT_PATTERN = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}"

ERRORS = {
    "not_configured": "Configure the Asterisk connection before discovering endpoints.",
    "authentication": "Asterisk authentication failed. Check the configured ARI credentials.",
    "forbidden": "The configured ARI user cannot list endpoints.",
    "unsupported": "Endpoint discovery is unavailable. Check the Asterisk ARI endpoint module.",
    "timeout": "Endpoint discovery timed out. Retry when Asterisk is reachable.",
    "connection": "Could not connect to the configured Asterisk server.",
    "tls": "The configured Asterisk TLS connection could not be verified.",
    "invalid_response": "Asterisk returned an invalid endpoint inventory. No candidates were accepted.",
    "too_large": "The endpoint inventory exceeds the discovery limit. Use manual destination setup.",
    "upstream": "Asterisk could not return the endpoint inventory. Try again.",
}


def discovery_error(category: str, status_code: int = 502) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"category": category, "message": ERRORS[category]})


class EndpointCandidate(BaseModel):
    technology: str
    resource: str
    state: str


class EndpointDiscoveryResult(BaseModel):
    endpoints: list[EndpointCandidate]
    discovered_at: str
    source: str = "configured_ari"


def _parse_inventory(raw: bytes) -> list[EndpointCandidate]:
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise discovery_error("invalid_response") from None
    if not isinstance(data, list):
        raise discovery_error("invalid_response")
    if len(data) > MAX_ENDPOINTS:
        raise discovery_error("too_large")
    candidates = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            raise discovery_error("invalid_response")
        tech, resource, state = item.get("technology"), item.get("resource"), item.get("state", "unknown")
        if not isinstance(tech, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+-]{0,31}", tech):
            raise discovery_error("invalid_response")
        if (
            not isinstance(resource, str) or not 1 <= len(resource) <= 256
            or resource != resource.strip() or any(ord(char) < 32 or ord(char) == 127 for char in resource)
            or not isinstance(state, str) or state not in {"online", "offline", "unknown"}
        ):
            raise discovery_error("invalid_response")
        identity = (tech, resource)
        if identity in seen:
            raise discovery_error("invalid_response")
        seen.add(identity)
        # Explicit projection: never expose channel IDs or extra upstream fields.
        candidates.append(EndpointCandidate(technology=tech, resource=resource, state=state))
    return sorted(candidates, key=lambda endpoint: (endpoint.technology, endpoint.resource))


async def discover_endpoints(settings: dict) -> EndpointDiscoveryResult:
    if not settings.get("username") or not settings.get("password"):
        raise discovery_error("not_configured", 409)
    try:
        url = httpx.URL(
            scheme=settings["scheme"], host=settings["host"], port=settings["port"], path="/ari/endpoints",
        )
        if url.scheme not in {"http", "https"} or not url.host:
            raise ValueError("Invalid configured connection")
    except (KeyError, TypeError, ValueError, httpx.InvalidURL):
        raise discovery_error("not_configured", 409) from None

    async def fetch() -> bytes:
        # No redirects, retries, user-provided URLs, or call-control requests.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(DISCOVERY_TIMEOUT_SECONDS, connect=3.0),
            verify=settings.get("ssl_verify", True), follow_redirects=False,
        ) as client:
            async with client.stream(
                "GET", url, headers={"Accept-Encoding": "identity"},
                auth=(settings["username"], settings["password"]),
            ) as response:
                if response.status_code != 200:
                    category = {401: "authentication", 403: "forbidden", 404: "unsupported"}.get(response.status_code, "upstream")
                    raise discovery_error(category)
                # Never invoke HTTPX decompression before enforcing the byte limit.
                if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                    raise discovery_error("invalid_response")
                body = bytearray()
                async for chunk in response.aiter_raw(chunk_size=65536):
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise discovery_error("too_large")
                    body.extend(chunk)
                return bytes(body)

    try:
        raw = await asyncio.wait_for(fetch(), timeout=DISCOVERY_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        raise discovery_error("timeout", 504) from None
    except httpx.ConnectError as exc:
        cause = exc
        while cause is not None:
            if isinstance(cause, ssl.SSLError):
                raise discovery_error("tls") from None
            cause = cause.__cause__
        raise discovery_error("connection") from None
    except ssl.SSLError:
        raise discovery_error("tls") from None
    except httpx.HTTPError:
        raise discovery_error("connection") from None

    return EndpointDiscoveryResult(
        endpoints=_parse_inventory(raw),
        discovered_at=datetime.now(timezone.utc).isoformat(),
    )


class DestinationAddition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: StrictStr = Field(pattern=f"^{KEY_PATTERN}$")
    target: StrictStr = Field(pattern=f"^{TARGET_PATTERN}$")
    description: StrictStr = Field(min_length=1, max_length=200)
    dialplan_context: StrictStr = Field(pattern=f"^{CONTEXT_PATTERN}$")


class DestinationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    additions: list[DestinationAddition] = Field(min_length=1, max_length=MAX_ADDITIONS)
    # Includes current unsaved edits, not just persisted YAML.
    existing_destinations: dict[str, Any] = Field(default_factory=dict, max_length=MAX_ENDPOINTS)
    extension_context: StrictStr = Field(default="from-internal", max_length=80)


def validate_additions(review: DestinationReview) -> dict[str, dict]:
    """Validate a whole batch without writing anything or altering old entries."""
    destinations = review.existing_destinations
    context = review.extension_context.strip() or "from-internal"
    keys = set(destinations)
    routes = {
        (
            str(dest.get("target") or "").strip(),
            str(dest.get("dialplan_context") or dest.get("context") or context).strip() or context,
        )
        for dest in destinations.values()
        if isinstance(dest, dict) and (dest.get("type") or "extension") == "extension"
    }
    additions = {}
    for row in review.additions:
        route = (row.target, row.dialplan_context)
        if row.key in keys:
            raise HTTPException(status_code=409, detail={"category": "duplicate_key", "message": "A destination key already exists. Choose a different key."})
        if route in routes:
            raise HTTPException(status_code=409, detail={"category": "duplicate_route", "message": "An extension destination already uses that number and context."})
        if not row.description.strip() or any(ord(char) < 32 or ord(char) == 127 for char in row.description):
            raise HTTPException(status_code=422, detail={"category": "invalid_description", "message": "Enter a description without control characters."})
        keys.add(row.key)
        routes.add(route)
        additions[row.key] = {
            "type": "extension", "target": row.target, "description": row.description.strip(),
            "dialplan_context": row.dialplan_context, "attended_allowed": False,
        }
    return {"destinations": additions}
