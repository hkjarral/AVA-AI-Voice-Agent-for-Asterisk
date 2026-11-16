"""Shared interpretation of persisted outbound campaign settings."""

from typing import Any, Mapping, Optional


VOICEMAIL_RECORDING_REQUIRED = (
    "Voicemail drop is enabled but no voicemail recording is set. "
    "Upload/generate voicemail before starting."
)


def voicemail_drop_enabled(value: Optional[int]) -> bool:
    """Decode the persisted 0/1 flag, defaulting only absent/None to enabled.

    Campaign requests supply booleans and SQLite returns integers. Keep the
    legacy enabled default for campaigns without this setting, but preserve 0.
    """
    return True if value is None else bool(int(value))


def validate_voicemail_recording(campaign: Mapping[str, Any]) -> None:
    """Require a configured recording when a campaign is entering/staying running."""
    if voicemail_drop_enabled(campaign.get("voicemail_drop_enabled")) and not str(
        campaign.get("voicemail_drop_media_uri") or ""
    ).strip():
        raise ValueError(VOICEMAIL_RECORDING_REQUIRED)
