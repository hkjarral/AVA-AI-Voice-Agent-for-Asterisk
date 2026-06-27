"""Unit tests for coerce_vad_sensitivity helper (google_live provider).

These tests cover the pure helper function directly and are intentionally
import-light — no Asterisk, no audio, no WebSocket.

Also includes config-layer tests that verify YAML-null values are accepted by
GoogleProviderConfig before reaching the provider coercion path (Finding 1 from
PR #472 review).
"""
import pytest

from src.config import GoogleProviderConfig
from src.providers.google_live import (
    VALID_EOS_SENSITIVITY,
    VALID_SOS_SENSITIVITY,
    coerce_vad_sensitivity,
)


# ---------------------------------------------------------------------------
# Config-layer: null VAD values must not raise at GoogleProviderConfig init
# ---------------------------------------------------------------------------

class TestGoogleProviderConfigNullVad:
    def test_null_eos_accepted(self):
        # YAML `vad_end_of_speech_sensitivity: null` must not raise ValidationError.
        cfg = GoogleProviderConfig(vad_end_of_speech_sensitivity=None)
        assert cfg.vad_end_of_speech_sensitivity is None

    def test_null_sos_accepted(self):
        cfg = GoogleProviderConfig(vad_start_of_speech_sensitivity=None)
        assert cfg.vad_start_of_speech_sensitivity is None

    def test_both_null_accepted(self):
        # Both fields null simultaneously (e.g. full reset via YAML).
        cfg = GoogleProviderConfig(
            vad_end_of_speech_sensitivity=None,
            vad_start_of_speech_sensitivity=None,
        )
        assert cfg.vad_end_of_speech_sensitivity is None
        assert cfg.vad_start_of_speech_sensitivity is None

    def test_null_eos_coerces_to_high_via_helper(self):
        # End-to-end: None from config → coerce helper → HIGH default.
        cfg = GoogleProviderConfig(vad_end_of_speech_sensitivity=None)
        result = coerce_vad_sensitivity(
            cfg.vad_end_of_speech_sensitivity, VALID_EOS_SENSITIVITY, "END_SENSITIVITY_HIGH"
        )
        assert result == "END_SENSITIVITY_HIGH"

    def test_null_sos_coerces_to_high_via_helper(self):
        cfg = GoogleProviderConfig(vad_start_of_speech_sensitivity=None)
        result = coerce_vad_sensitivity(
            cfg.vad_start_of_speech_sensitivity, VALID_SOS_SENSITIVITY, "START_SENSITIVITY_HIGH"
        )
        assert result == "START_SENSITIVITY_HIGH"


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

    def test_eos_none_coerced_to_high(self):
        # Pydantic can pass None for a YAML-null value; must coerce, not raise.
        result = coerce_vad_sensitivity(
            None, VALID_EOS_SENSITIVITY, "END_SENSITIVITY_HIGH"
        )
        assert result == "END_SENSITIVITY_HIGH"

    def test_sos_none_coerced_to_high(self):
        result = coerce_vad_sensitivity(
            None, VALID_SOS_SENSITIVITY, "START_SENSITIVITY_HIGH"
        )
        assert result == "START_SENSITIVITY_HIGH"
