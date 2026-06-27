"""Unit tests for coerce_vad_sensitivity helper (google_live provider).

These tests cover the pure helper function directly and are intentionally
import-light — no Asterisk, no audio, no WebSocket.
"""
import pytest

from src.providers.google_live import (
    VALID_EOS_SENSITIVITY,
    VALID_SOS_SENSITIVITY,
    coerce_vad_sensitivity,
)


# ---------------------------------------------------------------------------
# EOS (end-of-speech) valid values pass through unchanged
# ---------------------------------------------------------------------------

class TestCoerceEosSensitivityValid:
    def test_eos_high_passes(self):
        result = coerce_vad_sensitivity(
            "END_SENSITIVITY_HIGH", VALID_EOS_SENSITIVITY, "END_SENSITIVITY_HIGH"
        )
        assert result == "END_SENSITIVITY_HIGH"

    def test_eos_low_passes(self):
        result = coerce_vad_sensitivity(
            "END_SENSITIVITY_LOW", VALID_EOS_SENSITIVITY, "END_SENSITIVITY_HIGH"
        )
        assert result == "END_SENSITIVITY_LOW"

    def test_eos_unspecified_passes(self):
        result = coerce_vad_sensitivity(
            "END_SENSITIVITY_UNSPECIFIED", VALID_EOS_SENSITIVITY, "END_SENSITIVITY_HIGH"
        )
        assert result == "END_SENSITIVITY_UNSPECIFIED"


# ---------------------------------------------------------------------------
# SOS (start-of-speech) valid values pass through unchanged
# ---------------------------------------------------------------------------

class TestCoerceSosSensitivityValid:
    def test_sos_high_passes(self):
        result = coerce_vad_sensitivity(
            "START_SENSITIVITY_HIGH", VALID_SOS_SENSITIVITY, "START_SENSITIVITY_HIGH"
        )
        assert result == "START_SENSITIVITY_HIGH"

    def test_sos_low_passes(self):
        result = coerce_vad_sensitivity(
            "START_SENSITIVITY_LOW", VALID_SOS_SENSITIVITY, "START_SENSITIVITY_HIGH"
        )
        assert result == "START_SENSITIVITY_LOW"

    def test_sos_unspecified_passes(self):
        result = coerce_vad_sensitivity(
            "START_SENSITIVITY_UNSPECIFIED", VALID_SOS_SENSITIVITY, "START_SENSITIVITY_HIGH"
        )
        assert result == "START_SENSITIVITY_UNSPECIFIED"


# ---------------------------------------------------------------------------
# Invalid values are coerced to the supplied default (HIGH)
# ---------------------------------------------------------------------------

class TestCoerceSensitivityInvalid:
    def test_eos_medium_coerced_to_high(self):
        result = coerce_vad_sensitivity(
            "END_SENSITIVITY_MEDIUM", VALID_EOS_SENSITIVITY, "END_SENSITIVITY_HIGH"
        )
        assert result == "END_SENSITIVITY_HIGH"

    def test_sos_medium_coerced_to_high(self):
        result = coerce_vad_sensitivity(
            "START_SENSITIVITY_MEDIUM", VALID_SOS_SENSITIVITY, "START_SENSITIVITY_HIGH"
        )
        assert result == "START_SENSITIVITY_HIGH"

    def test_garbage_string_coerced_to_eos_high(self):
        result = coerce_vad_sensitivity(
            "TOTALLY_INVALID", VALID_EOS_SENSITIVITY, "END_SENSITIVITY_HIGH"
        )
        assert result == "END_SENSITIVITY_HIGH"

    def test_garbage_string_coerced_to_sos_high(self):
        result = coerce_vad_sensitivity(
            "TOTALLY_INVALID", VALID_SOS_SENSITIVITY, "START_SENSITIVITY_HIGH"
        )
        assert result == "START_SENSITIVITY_HIGH"

    def test_empty_string_coerced(self):
        result = coerce_vad_sensitivity(
            "", VALID_EOS_SENSITIVITY, "END_SENSITIVITY_HIGH"
        )
        assert result == "END_SENSITIVITY_HIGH"
