"""Model-dependent settings must fail before config persistence or restart."""
import copy

import pytest
import yaml

from src.config import load_config, validate_provider_settings
from src.config.normalization import ConfigValidationError


INVALID_SETTINGS = [
    ("customer_tts", {"type": "deepgram", "tts_version": "v2", "tts_model": "aura-2-thalia-en"}),
    ("deepgram", {"tts_version": "v1", "tts_model": "flux-kit-en"}),
    ("customer_tts", {"type": "groq", "tts_model": "canopylabs/orpheus-arabic-saudi", "voice": "hannah"}),
    ("customer_tts", {"type": "openai", "tts_transport": "speech", "tts_model": "gpt-realtime-2.1-mini"}),
    ("customer_tts", {"type": "elevenlabs", "model_id": "eleven_v3", "stability": 0.3}),
]


@pytest.mark.parametrize("name,settings", INVALID_SETTINGS)
def test_loader_keeps_unused_settings_but_selected_validation_rejects_them(tmp_path, monkeypatch, name, settings):
    monkeypatch.setenv("ASTERISK_ARI_USERNAME", "test")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "test")
    content = {"default_provider": "local", "providers": {"local": {}, name: settings}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(content))
    config = load_config(str(path))
    validate_provider_settings(config.providers, provider_names={"local"})
    with pytest.raises(ConfigValidationError, match=f"providers.{name}."):
        validate_provider_settings(config.providers, provider_names={name})


def test_validation_preserves_saved_custom_models_endpoints_and_credentials():
    providers = {
        "custom_tts": {"type": "openai", "tts_model": "operator-model", "tts_base_url": "http://localhost:9000/speech", "api_key_file": "/unavailable/key"},
        "arabic_tts": {"type": "groq", "tts_model": "canopylabs/orpheus-arabic-saudi"},
        "staged_tts": {"type": "deepgram", "enabled": False, "tts_version": "v2", "tts_model": "aura-2-thalia-en"},
    }
    before = copy.deepcopy(providers)
    validate_provider_settings(providers)
    assert providers == before


def test_canonical_openai_role_validation_uses_legacy_base_settings():
    validate_provider_settings({
        "openai": {"tts_model": "tts-1", "tts_transport": "speech"},
        "openai_tts": {"voice": "alloy"},
    })


def test_provider_error_never_contains_credentials_or_private_values():
    with pytest.raises(ConfigValidationError) as exc:
        validate_provider_settings({"customer_tts": {
            "type": "elevenlabs", "api_key": "private-key",
            "stability": "private-invalid-value",
        }})
    assert "private-key" not in str(exc.value)
    assert "private-invalid-value" not in str(exc.value)
