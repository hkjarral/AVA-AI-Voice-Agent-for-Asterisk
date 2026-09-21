"""Shared Fish Audio endpoint validation without pipeline runtime imports."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


FISH_AUDIO_OFFICIAL_VERIFICATION_URL = "https://api.fish.audio/model"
FISH_AUDIO_MOCK_VERIFICATION_URL = "http://127.0.0.1:8788/model"
FISH_AUDIO_OFFICIAL_TTS_URL = "https://api.fish.audio/v1/tts"
FISH_AUDIO_MOCK_TTS_URL = "http://127.0.0.1:8788/v1/tts"


def _is_loopback_hostname(hostname: str) -> bool:
    """Return whether a parsed endpoint host is explicitly loopback."""
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_fish_audio_base_url(base_url: str) -> str:
    """Normalize a Fish Audio endpoint and reject unsafe clear-text URLs.

    Bearer credentials may be sent to HTTPS endpoints. Plain HTTP is accepted
    only for an explicit loopback host so the bundled local mock remains usable
    without allowing API keys to cross the network in clear text.
    """
    normalized = str(base_url or "").strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("Fish Audio base_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError(
            "Fish Audio base_url must not contain credentials, query, or fragment"
        )
    if parsed.scheme == "https":
        return normalized

    if not _is_loopback_hostname(parsed.hostname):
        raise RuntimeError(
            "Fish Audio base_url must use HTTPS; HTTP is allowed only for a loopback mock"
        )
    return normalized


def validate_fish_audio_ws_url(ws_url: str) -> str:
    """Normalize a realtime endpoint and reject clear-text remote WebSockets."""
    normalized = str(ws_url or "").strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise RuntimeError("Fish Audio ws_base_url must be an absolute WS(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError(
            "Fish Audio ws_base_url must not contain credentials, query, or fragment"
        )
    if parsed.scheme == "ws" and not _is_loopback_hostname(parsed.hostname):
        raise RuntimeError(
            "Fish Audio ws_base_url must use WSS; WS is allowed only for a loopback mock"
        )
    return normalized


def fish_audio_verification_url(base_url: str) -> str:
    """Return a fixed allowlisted endpoint for credential verification.

    Verification deliberately does not interpolate any user-controlled URL
    component. The only local exception is the bundled mock's fixed loopback
    address and port; synthesis itself may still use another validated
    loopback URL when exercised directly.
    """
    normalized = validate_fish_audio_base_url(base_url)
    parsed = urlparse(normalized)
    hostname = (parsed.hostname or "").lower()

    if (
        parsed.scheme == "https"
        and hostname == "api.fish.audio"
        and parsed.port in {None, 443}
    ):
        return FISH_AUDIO_OFFICIAL_VERIFICATION_URL
    if (
        parsed.scheme == "http"
        and hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed.port == 8788
    ):
        return FISH_AUDIO_MOCK_VERIFICATION_URL
    raise RuntimeError(
        "Credential verification supports only api.fish.audio or the bundled "
        "loopback mock on port 8788"
    )


def fish_audio_synthesis_test_url(base_url: str) -> str:
    """Return a fixed allowlisted endpoint for provider connection testing.

    A provider connection test sends the bearer credential in a real synthesis
    request so it can detect model entitlement and billing failures. Keep the
    destination fixed rather than interpolating a user-controlled path.
    """
    normalized = validate_fish_audio_base_url(base_url)
    parsed = urlparse(normalized)
    hostname = (parsed.hostname or "").lower()

    if (
        parsed.scheme == "https"
        and hostname == "api.fish.audio"
        and parsed.port in {None, 443}
    ):
        return FISH_AUDIO_OFFICIAL_TTS_URL
    if (
        parsed.scheme == "http"
        and hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed.port == 8788
    ):
        return FISH_AUDIO_MOCK_TTS_URL
    raise RuntimeError(
        "Synthesis testing supports only api.fish.audio or the bundled "
        "loopback mock on port 8788"
    )
