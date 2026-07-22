import pytest
from pydantic import ValidationError

from src.config import (
    AppConfig,
    AzureTTSProviderConfig,
    CambAiProviderConfig,
    DeepgramProviderConfig,
    ElevenLabsProviderConfig,
    GoogleProviderConfig,
    GroqTTSProviderConfig,
    OpenAIProviderConfig,
)
from src.pipelines.azure import AzureTTSAdapter
from src.pipelines.cambai import CambAiTTSAdapter
from src.pipelines.deepgram import DeepgramTTSAdapter
from src.pipelines.elevenlabs import ElevenLabsTTSAdapter
from src.pipelines.google import GoogleTTSAdapter
from src.pipelines.groq import GroqTTSAdapter
from src.pipelines.openai import OpenAITTSAdapter


def _app_config() -> AppConfig:
    return AppConfig(
        default_provider="local",
        providers={"local": {"enabled": True}},
        asterisk={"host": "127.0.0.1", "username": "u", "password": "p"},
        llm={"initial_greeting": "", "prompt": "prompt", "model": "gpt-4o"},
    )


def test_audio_profile_accepts_ga_and_experimental_contracts():
    config = _app_config()
    data = config.model_dump()
    data["profiles"] = {
        "default": "telephony_ulaw_8k",
        "telephony_ulaw_8k": {
            "provider_pref": {
                "input_encoding": "mulaw",
                "input_sample_rate_hz": 8000,
                "output_encoding": "mulaw",
                "output_sample_rate_hz": 8000,
            },
            "transport_out": {"encoding": "ulaw", "sample_rate_hz": 8000},
        },
        "wideband_pcm_16k": {
            "provider_pref": {
                "input_encoding": "linear16",
                "input_sample_rate_hz": 16000,
                "output_encoding": "linear16",
                "output_sample_rate_hz": 16000,
            },
            "transport_out": {"encoding": "slin16", "sample_rate_hz": 16000},
        },
    }
    assert AppConfig(**data).profiles["default"] == "telephony_ulaw_8k"


def test_audio_profile_rejects_impossible_telephony_encoding_rate_pair():
    config = _app_config()
    data = config.model_dump()
    data["profiles"] = {
        "default": "broken",
        "broken": {
            "transport_out": {"encoding": "ulaw", "sample_rate_hz": 16000}
        },
    }
    with pytest.raises(ValidationError, match="ulaw requires 8000 Hz"):
        AppConfig(**data)


def test_audio_profile_rejects_missing_default_target():
    config = _app_config()
    data = config.model_dump()
    data["profiles"] = {"default": "missing"}
    with pytest.raises(ValidationError, match="references missing profile"):
        AppConfig(**data)


@pytest.mark.parametrize("invalid_mode", ["unknown", None])
def test_app_config_rejects_unknown_provider_output_resampler(invalid_mode):
    config = _app_config()
    data = config.model_dump()
    data["providers"]["local"]["output_resampler"] = invalid_mode
    with pytest.raises(
        ValidationError, match=r"providers\.local\.output_resampler"
    ):
        AppConfig(**data)


def test_app_config_rejects_unknown_pipeline_output_resampler():
    config = _app_config()
    data = config.model_dump()
    data["pipelines"] = {
        "canary": {
            "stt": "deepgram_stt",
            "llm": "openai_llm",
            "tts": "openai_tts",
            "options": {"tts": {"output_resampler": "unknown"}},
        }
    }
    with pytest.raises(
        ValidationError,
        match=r"pipelines\.canary\.options\.tts\.output_resampler",
    ):
        AppConfig(**data)


@pytest.mark.parametrize(
    "config_type",
    [
        OpenAIProviderConfig,
        GoogleProviderConfig,
        DeepgramProviderConfig,
        GroqTTSProviderConfig,
        ElevenLabsProviderConfig,
        CambAiProviderConfig,
        AzureTTSProviderConfig,
    ],
)
def test_modular_tts_config_rejects_unknown_output_resampler(config_type):
    with pytest.raises(ValidationError, match="output_resampler"):
        config_type(output_resampler="unknown")


@pytest.mark.parametrize(
    ("adapter_type", "config"),
    [
        (OpenAITTSAdapter, OpenAIProviderConfig(output_resampler="bandlimited")),
        (GoogleTTSAdapter, GoogleProviderConfig(output_resampler="bandlimited")),
        (DeepgramTTSAdapter, DeepgramProviderConfig(output_resampler="bandlimited")),
        (GroqTTSAdapter, GroqTTSProviderConfig(output_resampler="bandlimited")),
        (
            ElevenLabsTTSAdapter,
            ElevenLabsProviderConfig(output_resampler="bandlimited"),
        ),
        (CambAiTTSAdapter, CambAiProviderConfig(output_resampler="bandlimited")),
        (AzureTTSAdapter, AzureTTSProviderConfig(output_resampler="bandlimited")),
    ],
)
def test_modular_tts_adapter_inherits_provider_output_resampler(
    adapter_type, config
):
    adapter = adapter_type("tts", _app_config(), config)
    assert adapter._compose_options({})["output_resampler"] == "bandlimited"


@pytest.mark.parametrize(
    ("adapter_type", "config"),
    [
        (OpenAITTSAdapter, OpenAIProviderConfig(output_resampler="bandlimited")),
        (GoogleTTSAdapter, GoogleProviderConfig(output_resampler="bandlimited")),
        (DeepgramTTSAdapter, DeepgramProviderConfig(output_resampler="bandlimited")),
        (GroqTTSAdapter, GroqTTSProviderConfig(output_resampler="bandlimited")),
        (
            ElevenLabsTTSAdapter,
            ElevenLabsProviderConfig(output_resampler="bandlimited"),
        ),
        (CambAiTTSAdapter, CambAiProviderConfig(output_resampler="bandlimited")),
        (AzureTTSAdapter, AzureTTSProviderConfig(output_resampler="bandlimited")),
    ],
)
def test_pipeline_override_can_roll_back_provider_output_resampler(
    adapter_type, config
):
    adapter = adapter_type(
        "tts", _app_config(), config, {"output_resampler": "linear"}
    )
    assert adapter._compose_options({})["output_resampler"] == "linear"


def test_openai_modular_conversion_passes_explicit_policy(monkeypatch):
    import src.pipelines.openai as module

    calls = []

    def fake_resample(data, source_rate, target_rate, **kwargs):
        calls.append((source_rate, target_rate, kwargs))
        return b"\x00\x00" * 160, None

    monkeypatch.setattr(module, "resample_audio", fake_resample)
    OpenAITTSAdapter._convert_pcm(
        b"\x00\x00" * 480, 24000, "mulaw", 8000, "bandlimited"
    )
    assert calls == [(24000, 8000, {"mode": "bandlimited"})]


def test_google_modular_conversion_passes_explicit_policy(monkeypatch):
    import src.pipelines.google as module

    calls = []

    def fake_resample(data, source_rate, target_rate, **kwargs):
        calls.append((source_rate, target_rate, kwargs))
        return b"\x00\x00" * 160, None

    monkeypatch.setattr(module, "resample_audio", fake_resample)
    GoogleTTSAdapter._convert_audio(
        b"\x00\x00" * 480,
        "linear16",
        24000,
        "mulaw",
        8000,
        "bandlimited",
    )
    assert calls == [(24000, 8000, {"mode": "bandlimited"})]


def test_deepgram_modular_conversion_passes_explicit_policy(monkeypatch):
    import src.pipelines.deepgram as module

    calls = []

    def fake_resample(data, source_rate, target_rate, **kwargs):
        calls.append((source_rate, target_rate, kwargs))
        return b"\x00\x00" * 160, None

    monkeypatch.setattr(module, "resample_audio", fake_resample)
    DeepgramTTSAdapter._convert_audio(
        b"\x00\x00" * 480,
        "linear16",
        24000,
        "mulaw",
        8000,
        "bandlimited",
    )
    assert calls == [(24000, 8000, {"mode": "bandlimited"})]
