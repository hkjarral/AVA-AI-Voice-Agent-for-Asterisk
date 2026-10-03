"""Bounded, credential-safe probes for operator-configured modular providers.

These authenticated Admin UI endpoints intentionally support LAN inference
servers. They are not an outbound network isolation boundary: hostname checks
resolve DNS separately from the HTTP transport, as the HTTP tool tester does.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)
MODULAR_HTTP_KINDS = frozenset({"openai", "groq", "telnyx", "telenyx", "minimax"})
DEFAULT_BASES = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "telnyx": "https://api.telnyx.com/v2/ai",
    "telenyx": "https://api.telnyx.com/v2/ai",
    "minimax": "https://api.minimax.io/v1",
}
# Official service names do not need custom-target DNS discovery. Unlike the
# old allowlist, this never replaces a configured scheme, port or path.
OFFICIAL_HOSTS = frozenset({
    "api.openai.com", "api.groq.com", "api.telnyx.com", "api.minimax.io",
    "api.minimaxi.com", "openrouter.ai", "api.deepseek.com",
})


class ProviderValidationError(ValueError):
    """An endpoint cannot be safely tested; never fall back to another host."""


@dataclass(frozen=True)
class ProbeTarget:
    url: str
    base_url: str
    role: str
    kind: str


def provider_role(name: str, config: Mapping[str, Any]) -> str:
    caps = config.get("capabilities") or []
    if isinstance(caps, str):
        caps = [caps]
    roles = [role for role in caps if role in {"stt", "llm", "tts"}]
    if len(roles) == 1:
        return roles[0]
    match = re.search(r"_(stt|llm|tts)$", name.lower())
    if match:
        return match.group(1)
    fields = [role for role in ("stt", "tts") if config.get(f"{role}_base_url")]
    if len(fields) == 1 and not config.get("chat_base_url"):
        return fields[0]
    return "llm"


def probe_target(name: str, config: Mapping[str, Any]) -> ProbeTarget:
    kind = str(config.get("type") or "").strip().lower()
    if kind not in MODULAR_HTTP_KINDS:
        raise ProviderValidationError("Unsupported modular HTTP provider type")
    role = provider_role(name, config)
    if (
        name.lower() == "groq_llm" and kind == "openai" and role == "llm"
        and not (config.get("chat_base_url") or config.get("base_url"))
    ):
        raise ProviderValidationError("groq_llm with type 'openai' requires an explicit chat_base_url or base_url")
    default = DEFAULT_BASES[kind]
    if role == "llm":
        value = config.get("chat_base_url") or config.get("base_url") or default
        base = str(value).strip().rstrip("/")
        url = f"{base}/models"
    else:
        resource = "transcriptions" if role == "stt" else "speech"
        # Speech adapter URLs name the complete resource, not an API root.
        value = config.get(f"{role}_base_url") or config.get("base_url") or f"{default}/audio/{resource}"
        base = url = str(value).strip().rstrip("/")
    validate_url_shape(base)
    return ProbeTarget(url=url, base_url=base, role=role, kind=kind)


def validate_url_shape(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or "?" in url or "#" in url
            or "\\" in url or any(ord(c) <= 32 or ord(c) == 127 for c in url)
            or "%" in parsed.netloc or port == 0
        ):
            raise ValueError()
        # Match HTTPX's interpretation before resolving a destination.
        if httpx.URL(url).host.lower() != parsed.hostname.lower():
            raise ValueError()
    except (ValueError, httpx.InvalidURL):
        raise ProviderValidationError(
            "Provider URL must be an absolute HTTP(S) URL with a valid host/port, "
            "without embedded credentials, whitespace, query or fragment"
        ) from None


def _check_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or (ip.is_reserved and not ip.is_loopback):
        raise ProviderValidationError("Provider URL targets a blocked metadata or special-use address")
    # RFC1918, loopback and IPv6 ULA are intended local deployment targets.
    local = ip.is_loopback or any(ip in network for network in (
        ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("fc00::/7"),
    ) if network.version == ip.version)
    if not local and not ip.is_global:
        raise ProviderValidationError("Provider URL targets a blocked special-use address")
    return local


async def validate_target(target: ProbeTarget) -> None:
    parsed = urlsplit(target.url)
    host = parsed.hostname or ""
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        if host.lower().rstrip(".") in {"metadata.google.internal", "instance-data.ec2.internal"}:
            raise ProviderValidationError("Provider URL targets a blocked metadata hostname")
        if host.lower() in OFFICIAL_HOSTS and parsed.scheme == "https":
            return
        try:
            infos = await asyncio.wait_for(
                asyncio.to_thread(socket.getaddrinfo, host, parsed.port or (443 if parsed.scheme == "https" else 80), 0, socket.SOCK_STREAM),
                timeout=3.0,
            )
            addresses = list({info[4][0] for info in infos})
        except (OSError, asyncio.TimeoutError):
            raise ProviderValidationError("Cannot resolve the configured provider hostname") from None
    if not addresses:
        raise ProviderValidationError("Cannot resolve the configured provider hostname")
    local = [_check_address(address) for address in addresses]
    if parsed.scheme == "http" and not all(local):
        raise ProviderValidationError("Public provider endpoints require HTTPS; HTTP is supported for LAN/loopback endpoints")


def safe_destination(target: ProbeTarget, key: str = "") -> str:
    # Paths may include arbitrary operator identifiers. Diagnostics only show
    # the origin; the browser already holds the configured resource path.
    parsed = urlsplit(target.url)
    destination = f"{parsed.scheme}://{parsed.netloc}"
    return destination.replace(key, "[redacted]") if key else destination


def _log_field(value: str, limit: int = 256) -> str:
    """Bound diagnostic fields and remove line breaks at the logging boundary."""
    return value[:limit].replace("\r", "").replace("\n", "")


async def test_modular_provider(
    name: str, config: Mapping[str, Any], key: str, *, exercise_chat: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    target = None
    status = None
    outcome = "rejected"
    level = "none"
    try:
        target = probe_target(name, config)
        await validate_target(target)
        no_auth = key.strip().lower() == "not-needed"
        if not key or (no_auth and target.kind != "openai"):
            raise ProviderValidationError("API key is not configured for this provider")
        if any(ord(c) < 32 or ord(c) == 127 for c in key):
            raise ProviderValidationError("API key contains invalid control characters")
        try:
            key.encode("ascii")
        except UnicodeError:
            raise ProviderValidationError("API key must contain ASCII characters") from None
        if no_auth and (urlsplit(target.url).hostname or "").lower() in OFFICIAL_HOSTS:
            raise ProviderValidationError("The no-auth sentinel is only supported for custom OpenAI-compatible endpoints")
        headers = {} if no_auth else {"Authorization": f"Bearer {key}"}
        destination = safe_destination(target, key)
        model = str(config.get("chat_model") or config.get("model") or "Qwen/Qwen3-235B-A22B").strip()
        api_key_ref = str(config.get("api_key_ref") or "").strip()
        exercise_chat = exercise_chat and target.role == "llm"
        if exercise_chat and model.startswith("openai/") and not api_key_ref:
            raise ProviderValidationError("Telnyx external openai/* models require api_key_ref (Integration Secret identifier)")
        # The total budget also bounds a server that continually dribbles data
        # inside HTTPX's per-read timeout. DNS has its own three-second budget.
        async with asyncio.timeout(20.0), httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(target.url, headers=headers)
            status = response.status_code
            if 300 <= status < 400:
                raise ProviderValidationError(f"Provider returned HTTP {status}; redirects are disabled")
            if status in {401, 403}:
                raise ProviderValidationError(f"Provider rejected authentication (HTTP {status})")
            if target.role != "llm":
                if status not in {200, 405}:
                    raise ProviderValidationError(f"Speech endpoint probe failed (HTTP {status})")
                level = "reachability"
                message = "Speech endpoint reachable; authentication and transcription/synthesis were not verified"
            else:
                if status != 200:
                    raise ProviderValidationError(f"Models probe failed (HTTP {status}); no alternate endpoint was tried")
                try:
                    data = response.json()
                    models = data.get("data") if isinstance(data, dict) else None
                    if not isinstance(models, list):
                        raise ValueError()
                except (ValueError, AttributeError):
                    raise ProviderValidationError("Models endpoint returned an invalid OpenAI-compatible model list") from None
                level = "connectivity" if no_auth else "authentication"
                message = f"Connected. Found {len(models)} models; inference was not tested"
                if exercise_chat:
                    payload = {
                        "model": model, "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                        "temperature": 0.0, "max_tokens": 16,
                    }
                    if api_key_ref:
                        payload["api_key_ref"] = api_key_ref
                    response = await client.post(f"{target.base_url}/chat/completions", headers=headers, json=payload)
                    status = response.status_code
                    if status != 200:
                        raise ProviderValidationError(f"Telnyx chat completion failed (HTTP {status})")
                    try:
                        body = response.json()
                        choices = body.get("choices") if isinstance(body, dict) else None
                        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                            raise ValueError()
                    except (ValueError, AttributeError):
                        raise ProviderValidationError("Telnyx returned an invalid chat completion") from None
                    level = "inference"
                    message = "Telnyx chat completion verified"
        outcome = "success"
        return {"success": True, "message": f"{message} at {destination}", "endpoint": destination, "validation_level": level}
    except ProviderValidationError as exc:
        message = str(exc)
    except (httpx.TimeoutException, TimeoutError):
        outcome = "timeout"
        message = "Configured provider connection timed out; no alternate endpoint was tried"
    except httpx.RequestError:
        outcome = "connection_error"
        message = "Cannot connect to the configured provider; no alternate endpoint was tried"
    finally:
        # Never include exceptions, response bodies, URLs containing credentials,
        # model IDs, headers, or raw config in this diagnostic event.
        logger.info(
            "Provider validation provider=%s kind=%s role=%s destination=%s outcome=%s status=%s level=%s elapsed_ms=%d",
            _log_field(re.sub(r"[^A-Za-z0-9_.-]", "?", name.replace(key, "redacted") if key else name), 64),
            _log_field(target.kind if target else "unknown"), _log_field(target.role if target else "unknown"),
            _log_field(safe_destination(target, key) if target else "invalid"), outcome, status, level,
            int((time.monotonic() - started) * 1000),
        )
    destination = safe_destination(target, key) if target else ""
    return {"success": False, "message": f"{message}{f' at {destination}' if destination else ''}", "endpoint": destination, "validation_level": "none"}
