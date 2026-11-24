"""Model-dependent settings reject mismatches without rewriting legacy pins."""
import pytest
from pydantic import ValidationError

from src.config import DeepgramProviderConfig, OpenAIProviderConfig, OpenAIRealtimeProviderConfig, GroqTTSProviderConfig


def test_new_openai_defaults_and_explicit_legacy_models_are_distinct():
    current = OpenAIProviderConfig()
    assert (current.tts_model, current.tts_transport) == ("gpt-realtime-2.1-mini", "auto")
    legacy = OpenAIProviderConfig(tts_model="tts-1", stt_model="whisper-1", chat_model="gpt-4o-mini")
    assert (legacy.tts_model, legacy.stt_model, legacy.chat_model) == ("tts-1", "whisper-1", "gpt-4o-mini")
    assert legacy.reasoning_effort is None
    assert OpenAIRealtimeProviderConfig(model="gpt-realtime").model == "gpt-realtime"


def test_openai_specific_reasoning_default_does_not_leak_to_compatible_gateway():
    custom = OpenAIProviderConfig(chat_base_url="https://api.deepseek.com", chat_model="deepseek-flash")
    assert custom.reasoning_effort is None
    assert OpenAIProviderConfig(**custom.model_dump()).reasoning_effort is None
    explicit = OpenAIProviderConfig(chat_base_url="http://127.0.0.1:8080/v1", reasoning_effort="low")
    assert explicit.reasoning_effort == "low"


@pytest.mark.parametrize("settings", [
    {"tts_transport": "speech", "tts_model": "gpt-realtime-2.1-mini"},
    {"tts_transport": "realtime", "tts_model": "tts-1"},
    {"tts_transport": "unknown"},
])
def test_openai_tts_transport_and_model_must_match(settings):
    with pytest.raises(ValidationError):
        OpenAIProviderConfig(**settings)


@pytest.mark.parametrize("settings", [
    {"tts_model": "flux-kit-en", "tts_version": "v1"},
    {"tts_model": "aura-2-thalia-en", "tts_version": "v2"},
    {"tts_expressivity": 1},
    {"tts_speed": 0.65},
    {"tts_model": "flux-kit-en", "tts_version": "v2", "tts_speed": 1.03},
    {"tts_model": "flux-kit-en", "tts_version": "v2", "tts_expressivity": True},
    {"language_hints": [""]},
])
def test_deepgram_rejects_incompatible_tts_controls(settings):
    with pytest.raises(ValidationError):
        DeepgramProviderConfig(**settings)


def test_deepgram_flux_controls_and_normalized_hints_are_preserved():
    config = DeepgramProviderConfig(
        model="flux-general-multi", language_hints=["en", " es ", "en"],
        tts_model="flux-kit-en", tts_version="v2", tts_speed=1.05, tts_expressivity=-1,
    )
    assert config.language_hints == ["en", "es"]
    assert config.tts_speed == 1.05
    assert config.tts_expressivity == -1


def test_openai_realtime_reasoning_rejects_chat_only_efforts():
    for effort in ("none", "max"):
        with pytest.raises(ValidationError):
            OpenAIRealtimeProviderConfig(reasoning_effort=effort)


def test_arabic_groq_uses_arabic_default_and_rejects_explicit_english_voice():
    model = "canopylabs/orpheus-arabic-saudi"
    assert GroqTTSProviderConfig(tts_model=model).voice == "abdullah"
    with pytest.raises(ValidationError, match="voice"):
        GroqTTSProviderConfig(tts_model=model, voice="hannah")
