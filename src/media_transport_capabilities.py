"""Dependency-free capability gates shared by media transport surfaces.

This module intentionally imports only the Python standard library.  It is
used by the Admin UI's configuration process as well as the ai_engine, whose
WebSocket protocol dependency versions differ.
"""

from __future__ import annotations

import re
from typing import Optional


def supports_media_websocket(version: Optional[str]) -> bool:
    """Whether *version* supports Asterisk's JSON Media WebSocket controls.

    Unknown versions, Asterisk 21, and un-certified future branches fail
    closed. This is a feature floor, not a certification claim for vendor
    builds or module availability.
    """
    # Parse the first version-like token only. Searching ahead for a supported
    # token could accidentally approve an unsupported server version mentioned
    # later in a package/build string.
    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.\d+)?", str(version or ""))
    if not match:
        return False
    major, minor = (int(part) for part in match.groups())
    return (major == 20 and minor >= 18) or (major == 22 and minor >= 8) or (
        major == 23 and minor >= 2
    )


def resolve_media_websocket_control(version: Optional[str], requested: str = "json") -> Optional[str]:
    """Select before admission, never as a retry/downgrade after a failure.

    Plain is experimental and restricted to the exact upstream release proved
    on the development PBX. A matching version still requires working modules and live
    qualification of the vendor build. Existing JSON floors remain unchanged.
    """
    if requested not in {"json", "plain", "auto"}:
        return None
    if requested in {"json", "auto"} and supports_media_websocket(version):
        return "json"
    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?", str(version or ""))
    if requested in {"plain", "auto"} and match and match.groups() == ("20", "17", "0"):
        return "plain"
    return None
