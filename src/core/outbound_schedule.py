"""Shared normalization helpers for outbound campaign schedules."""

import re
from typing import Any, Optional


def normalize_outbound_daily_window(value: Any, default: str) -> Optional[str]:
    """Normalize legacy H:MM values while rejecting invalid clock times."""
    raw = str(value or default).strip()
    match = re.fullmatch(r"(\d{1,2}):([0-5]\d)", raw)
    if not match:
        return None
    hour = int(match.group(1))
    if hour > 23:
        return None
    return f"{hour:02d}:{match.group(2)}"
