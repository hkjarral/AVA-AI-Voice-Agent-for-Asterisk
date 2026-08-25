"""Regression tests for provider capability rate negotiation.

An operator who configures ElevenLabs for pcm_8000 (dashboard agent output +
``output_sample_rate_hz: 8000`` in YAML) must get 8000 through negotiation.
Before this fix the static capability list omitted 8000, so the orchestrator
silently renegotiated the configured value back to 16000 and the provider then
misinterpreted the returned audio's rate.
"""

from src.core.transport_orchestrator import TransportOrchestrator
from src.providers.elevenlabs_agent import ElevenLabsAgentProvider
from src.providers.elevenlabs_config import ElevenLabsAgentConfig


def _elevenlabs_capabilities():
    provider = ElevenLabsAgentProvider.__new__(ElevenLabsAgentProvider)
    return provider.get_capabilities()


def test_elevenlabs_capabilities_include_8k_output():
    caps = _elevenlabs_capabilities()
    assert 8000 in caps.output_sample_rates_hz
    assert 16000 in caps.output_sample_rates_hz
    assert "alaw" in caps.input_encodings


def test_orchestrator_honors_configured_8k_elevenlabs_output():
    config = {
        "audio_transport": "audiosocket",
        "audiosocket": {"format": "slin", "port": 8090},
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {
                "internal_rate_hz": 8000,
                "provider_pref": {
                    "input_encoding": "mulaw",
                    "input_sample_rate_hz": 8000,
                    "output_encoding": "mulaw",
                    "output_sample_rate_hz": 8000,
                },
                "transport_out": {"encoding": "ulaw", "sample_rate_hz": 8000},
            },
        },
    }
    orchestrator = TransportOrchestrator(config)
    provider_config = ElevenLabsAgentConfig.from_dict(
        {
            "agent_id": "agent_test",
            "provider_input_encoding": "pcm16",
            "provider_input_sample_rate_hz": 8000,
            "output_encoding": "pcm16",
            "output_sample_rate_hz": 8000,
        }
    )

    transport = orchestrator.resolve_transport(
        provider_name="elevenlabs_agent",
        provider_caps=_elevenlabs_capabilities(),
        channel_vars={},
        provider_config=provider_config,
    )

    assert transport.provider_output_sample_rate == 8000
    assert transport.provider_input_sample_rate == 8000
