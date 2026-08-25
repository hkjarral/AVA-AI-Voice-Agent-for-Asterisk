"""Tests for the optional profiles.<name>.transport_in inbound wire leg.

Covers:
- Schema validation of the transport_in encoding/sample-rate pair
  (fixed G.711 rates must not pass with a mismatched rate).
- TransportOrchestrator resolution of wire_in_* on the TransportProfile:
  mirrors transport_out when absent, honors transport_in for RTP, and
  normalizes companded selections onto the AudioSocket slin carrier.
"""

import pytest

from src.core.transport_orchestrator import TransportOrchestrator


BASE_PROFILE = {
    "internal_rate_hz": 8000,
    "chunk_ms": "auto",
    "idle_cutoff_ms": 800,
    "provider_pref": {
        "input_encoding": "mulaw",
        "input_sample_rate_hz": 8000,
        "output_encoding": "mulaw",
        "output_sample_rate_hz": 8000,
    },
    "transport_out": {"encoding": "ulaw", "sample_rate_hz": 8000},
}


def _orchestrator(profile_overrides=None, audio_transport="audiosocket"):
    profile = {**BASE_PROFILE, **(profile_overrides or {})}
    config = {
        "audio_transport": audio_transport,
        "audiosocket": {"format": "slin", "port": 8090},
        "profiles": {"default": "test_profile", "test_profile": profile},
    }
    return TransportOrchestrator(config)


class TestTransportInResolution:
    def test_wire_in_mirrors_wire_out_when_absent(self):
        orchestrator = _orchestrator()
        transport = orchestrator.resolve_transport(
            provider_name="deepgram",
            provider_caps=None,
            channel_vars={},
        )
        assert transport.wire_in_encoding == transport.wire_encoding
        assert transport.wire_in_sample_rate == transport.wire_sample_rate

    def test_rtp_honors_declared_transport_in(self):
        orchestrator = _orchestrator(
            {
                "transport_out": {"encoding": "ulaw", "sample_rate_hz": 8000},
                "transport_in": {"encoding": "alaw", "sample_rate_hz": 8000},
            },
            audio_transport="externalmedia",
        )
        transport = orchestrator.resolve_transport(
            provider_name="deepgram",
            provider_caps=None,
            channel_vars={},
        )
        assert transport.wire_encoding == "ulaw"
        assert transport.wire_in_encoding == "alaw"
        assert transport.wire_in_sample_rate == 8000

    def test_audiosocket_companded_transport_in_rides_slin_carrier(self):
        orchestrator = _orchestrator(
            {
                "transport_out": {"encoding": "slin16", "sample_rate_hz": 16000},
                "transport_in": {"encoding": "alaw", "sample_rate_hz": 8000},
            }
        )
        transport = orchestrator.resolve_transport(
            provider_name="deepgram",
            provider_caps=None,
            channel_vars={},
        )
        assert transport.wire_encoding == "slin16"
        assert transport.wire_sample_rate == 16000
        # AudioSocket frames are signed-linear; companded inbound selections
        # use the 8 kHz compatibility carrier.
        assert transport.wire_in_encoding == "slin"
        assert transport.wire_in_sample_rate == 8000

    def test_audiosocket_slin_transport_in_normalizes(self):
        orchestrator = _orchestrator(
            {
                "transport_out": {"encoding": "ulaw", "sample_rate_hz": 8000},
                "transport_in": {"encoding": "slin16", "sample_rate_hz": 16000},
            }
        )
        transport = orchestrator.resolve_transport(
            provider_name="deepgram",
            provider_caps=None,
            channel_vars={},
        )
        assert transport.wire_in_encoding == "slin16"
        assert transport.wire_in_sample_rate == 16000


class TestTransportInSchemaValidation:
    @pytest.fixture(autouse=True)
    def setup_env(self, monkeypatch):
        monkeypatch.setenv("ASTERISK_ARI_USERNAME", "test_user")
        monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "test_pass")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        monkeypatch.setenv("TELNYX_API_KEY", "tk-test-key")

    def _load_with_profile_block(self, tmp_path, block: str):
        from pathlib import Path

        from src.config import load_config

        source = Path("config/ai-agent.golden-openai.yaml").read_text()
        marker = "  telephony_responsive:"
        assert marker in source
        patched = source.replace(marker, block + marker, 1)
        config_path = tmp_path / "ai-agent.yaml"
        config_path.write_text(patched, encoding="utf-8")
        return load_config(str(config_path))

    def test_valid_transport_in_pair_accepted(self, tmp_path):
        block = (
            "  asymmetric_test:\n"
            "    internal_rate_hz: 8000\n"
            "    transport_out:\n"
            "      encoding: ulaw\n"
            "      sample_rate_hz: 8000\n"
            "    transport_in:\n"
            "      encoding: alaw\n"
            "      sample_rate_hz: 8000\n"
        )
        config = self._load_with_profile_block(tmp_path, block)
        assert config.profiles["asymmetric_test"]["transport_in"] == {
            "encoding": "alaw",
            "sample_rate_hz": 8000,
        }

    def test_invalid_transport_in_rate_rejected(self, tmp_path):
        block = (
            "  asymmetric_test:\n"
            "    internal_rate_hz: 8000\n"
            "    transport_out:\n"
            "      encoding: ulaw\n"
            "      sample_rate_hz: 8000\n"
            "    transport_in:\n"
            "      encoding: alaw\n"
            "      sample_rate_hz: 16000\n"
        )
        with pytest.raises(Exception) as exc_info:
            self._load_with_profile_block(tmp_path, block)
        assert "transport_in" in str(exc_info.value)
        assert "8000" in str(exc_info.value)
