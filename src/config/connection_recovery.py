"""Opt-in initial connection policy for cloud full-agent providers."""
from typing import Optional

from pydantic import BaseModel, Field, field_validator

CLOUD_CONNECTION_KINDS = frozenset({
    "google_live", "openai_realtime", "grok", "deepgram", "elevenlabs_agent",
})


class CloudConnectionConfig(BaseModel):
    # Missing fields preserve the previous single attempt / 10s opening deadline.
    connect_timeout_sec: float = Field(default=10.0, gt=0, le=60, allow_inf_nan=False)
    connect_max_retries: int = Field(default=0, ge=0, le=3)
    # Includes retry backoff and provider-owned pre-connect authentication;
    # excludes session setup after the socket opens.
    connect_total_timeout_sec: Optional[float] = Field(
        default=None, gt=0, le=180, allow_inf_nan=False,
    )

    @field_validator("connect_max_retries", mode="before")
    @classmethod
    def reject_boolean_retries(cls, value):
        if isinstance(value, bool):
            raise ValueError("connect_max_retries must be an integer, not a boolean")
        return value

    @field_validator("connect_timeout_sec", "connect_total_timeout_sec", mode="before")
    @classmethod
    def reject_boolean_timeouts(cls, value):
        if isinstance(value, bool):
            raise ValueError("connection timeouts must be numbers, not booleans")
        return value
