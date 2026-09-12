"""Unit tests for activityHandling support (google_live provider).

Covers the pure coercion helper, the config field that carries the value, and
the setup message that puts it on the wire. Import-light — no Asterisk, no
audio, no real WebSocket.

The setup-message tests matter more than they look: the feature's contract is
that a provider which has not opted in sends exactly what it sends today, so
"the key is absent unless configured" is the property worth pinning down.
"""
import asyncio

import pytest

from src.config import GoogleProviderConfig
from src.providers.google_live import (
    VALID_ACTIVITY_HANDLING,
    GoogleLiveProvider,
    coerce_activity_handling,
)


# ---------------------------------------------------------------------------
# Config layer: the field exists, defaults to None, and accepts YAML values
# ---------------------------------------------------------------------------

class TestGoogleProviderConfigActivityHandling:
    def test_defaults_to_none(self):
        # Absent from YAML must stay absent, not become a substituted default.
        assert GoogleProviderConfig().activity_handling is None

    def test_null_accepted(self):
        # YAML `activity_handling: null` must not raise ValidationError.
        cfg = GoogleProviderConfig(activity_handling=None)
        assert cfg.activity_handling is None

    def test_no_interruption_accepted(self):
        cfg = GoogleProviderConfig(activity_handling="NO_INTERRUPTION")
        assert cfg.activity_handling == "NO_INTERRUPTION"

    def test_start_of_activity_interrupts_accepted(self):
        cfg = GoogleProviderConfig(activity_handling="START_OF_ACTIVITY_INTERRUPTS")
        assert cfg.activity_handling == "START_OF_ACTIVITY_INTERRUPTS"

    def test_invalid_value_survives_config_load(self):
        # Validation belongs to the provider, not the model: a typo must not
        # fail config load for every agent in the file.
        cfg = GoogleProviderConfig(activity_handling="NOPE")
        assert cfg.activity_handling == "NOPE"
        assert coerce_activity_handling(cfg.activity_handling) is None


# ---------------------------------------------------------------------------
# Valid values pass through unchanged
# ---------------------------------------------------------------------------

class TestCoerceActivityHandlingValid:
    @pytest.mark.parametrize("value", sorted(VALID_ACTIVITY_HANDLING))
    def test_api_accepted_values_pass(self, value):
        assert coerce_activity_handling(value) == value


# ---------------------------------------------------------------------------
# Everything else coerces to None, meaning "omit the key"
# ---------------------------------------------------------------------------

class TestCoerceActivityHandlingInvalid:
    def test_unspecified_is_not_accepted(self):
        # A documented member, deliberately rejected: sending it is equivalent
        # to omitting the key, and omission is the cheaper path.
        assert coerce_activity_handling("ACTIVITY_HANDLING_UNSPECIFIED") is None

    def test_garbage_string_coerced_to_none(self):
        assert coerce_activity_handling("TOTALLY_INVALID") is None

    def test_empty_string_coerced_to_none(self):
        assert coerce_activity_handling("") is None

    def test_none_coerced_to_none(self):
        # Pydantic passes None for a YAML-null value; must coerce, not raise.
        assert coerce_activity_handling(None) is None

    def test_lowercase_is_rejected(self):
        # The API enum is case-sensitive; a near-miss must not reach the wire.
        assert coerce_activity_handling("no_interruption") is None

    @pytest.mark.parametrize("value", [True, 1, 0, [], {}, object()])
    def test_non_string_types_coerced_to_none(self, value):
        assert coerce_activity_handling(value) is None


# ---------------------------------------------------------------------------
# Setup message: the key reaches the wire only when configured
# ---------------------------------------------------------------------------

def _capture_setup(config: GoogleProviderConfig) -> dict:
    """Run _send_setup with the transport stubbed and return the setup message."""
    provider = GoogleLiveProvider(config=config, on_event=lambda *a, **kw: None)
    captured = {}

    async def fake_send(message):
        captured.update(message)
        return True

    provider._send_message = fake_send
    asyncio.run(provider._send_setup(None))
    return captured


class TestSetupMessageActivityHandling:
    def test_key_absent_when_unconfigured(self):
        # The compatibility guarantee: an existing deployment that never sets
        # this sends the same realtimeInputConfig it sends today.
        realtime = _capture_setup(GoogleProviderConfig())["setup"]["realtimeInputConfig"]
        assert "activityHandling" not in realtime
        assert "automaticActivityDetection" in realtime

    def test_key_present_when_configured(self):
        realtime = _capture_setup(
            GoogleProviderConfig(activity_handling="NO_INTERRUPTION")
        )["setup"]["realtimeInputConfig"]
        assert realtime["activityHandling"] == "NO_INTERRUPTION"

    def test_key_absent_when_value_is_invalid(self):
        # A typo degrades to today's behaviour rather than closing the socket
        # at 1007 mid-call.
        realtime = _capture_setup(
            GoogleProviderConfig(activity_handling="NOPE")
        )["setup"]["realtimeInputConfig"]
        assert "activityHandling" not in realtime

    def test_vad_block_is_unchanged_by_the_feature(self):
        # The new key is a sibling of automaticActivityDetection, so setting it
        # must not disturb the VAD members alongside it.
        without = _capture_setup(GoogleProviderConfig())["setup"]["realtimeInputConfig"]
        with_ah = _capture_setup(
            GoogleProviderConfig(activity_handling="NO_INTERRUPTION")
        )["setup"]["realtimeInputConfig"]
        assert without["automaticActivityDetection"] == with_ah["automaticActivityDetection"]
