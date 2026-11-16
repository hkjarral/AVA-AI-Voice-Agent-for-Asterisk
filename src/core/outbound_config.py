"""Shared interpretation of persisted outbound campaign settings."""

from typing import Optional


def voicemail_drop_enabled(value: Optional[int]) -> bool:
    """Decode the persisted 0/1 flag, defaulting only absent/None to enabled.

    Campaign requests supply booleans and SQLite returns integers. Keep the
    legacy enabled default for campaigns without this setting, but preserve 0.
    """
    return True if value is None else bool(int(value))
