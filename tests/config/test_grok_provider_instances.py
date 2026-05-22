"""Multi-instance validation tests for the ``grok`` full-agent provider kind."""

import pytest

from src.config.normalization import ConfigValidationError, validate_providers
from src.config.provider_instances import (
    API_KEY_COMPATIBLE_KINDS,
    FULL_AGENT_KINDS,
    FULL_AGENT_KINDS_WITH_NATIVE_TTS_GATING,
    provider_kind,
)


def test_grok_is_registered_in_full_agent_kinds():
    assert "grok" in FULL_AGENT_KINDS


def test_grok_is_api_key_compatible():
    assert "grok" in API_KEY_COMPATIBLE_KINDS


def test_grok_emits_native_tts_chunks_for_gating():
    assert "grok" in FULL_AGENT_KINDS_WITH_NATIVE_TTS_GATING


def test_provider_kind_resolves_grok_from_type_field():
    cfg = {"type": "grok", "enabled": True}
    assert provider_kind("acme_grok", cfg) == "grok"


def test_provider_kind_resolves_grok_from_legacy_canonical_key():
    cfg = {"enabled": True}
    assert provider_kind("grok", cfg) == "grok"


def test_legacy_full_type_on_canonical_grok_key_resolves():
    cfg = {"type": "full", "enabled": True}
    assert provider_kind("grok", cfg) == "grok"


def test_two_grok_instances_with_distinct_keys_validate():
    config_data = {
        "default_provider": "acme_grok",
        "providers": {
            "acme_grok": {"type": "grok", "enabled": True, "voice": "eve"},
            "globex_grok": {"type": "grok", "enabled": True, "voice": "rex"},
        },
    }
    # Should not raise
    validate_providers(config_data)


def test_grok_default_provider_with_legacy_canonical_key():
    config_data = {
        "default_provider": "grok",
        "providers": {
            "grok": {"enabled": True},
        },
    }
    validate_providers(config_data)


def test_grok_instance_key_cannot_collide_with_pipeline_name():
    config_data = {
        "default_provider": "acme_grok",
        "providers": {
            "acme_grok": {"type": "grok", "enabled": True},
        },
        "pipelines": {
            "acme_grok": {
                "stt": "local_stt",
                "llm": "openai_llm",
                "tts": "local_tts",
            }
        },
    }
    with pytest.raises(ConfigValidationError):
        validate_providers(config_data)


def test_grok_full_agent_in_modular_pipeline_slot_fails():
    config_data = {
        "default_provider": "acme_grok",
        "providers": {
            "acme_grok": {"type": "grok", "enabled": True},
        },
        "pipelines": {
            "hybrid": {
                "stt": "acme_grok",  # full-agent in modular slot — invalid
                "llm": "openai_llm",
                "tts": "local_tts",
            }
        },
    }
    with pytest.raises(ConfigValidationError):
        validate_providers(config_data)


def test_context_routing_to_grok_instance():
    config_data = {
        "default_provider": "acme_grok",
        "providers": {
            "acme_grok": {"type": "grok", "enabled": True},
            "globex_grok": {"type": "grok", "enabled": True},
        },
        "contexts": {
            "acme_support": {"provider": "acme_grok"},
            "globex_sales": {"provider": "globex_grok"},
        },
    }
    validate_providers(config_data)


def test_context_routing_to_unknown_grok_key_fails():
    config_data = {
        "default_provider": "acme_grok",
        "providers": {
            "acme_grok": {"type": "grok", "enabled": True},
        },
        "contexts": {
            "acme_support": {"provider": "nonexistent_grok"},
        },
    }
    with pytest.raises(ConfigValidationError):
        validate_providers(config_data)
