"""Effective model/transport selection preserves operator endpoint choices."""
import pytest
from pydantic import ValidationError

from src.config import (GoogleProviderConfig, LocalProviderConfig, OpenAIProviderConfig,
                        provider_settings_payload, resolve_openai_tts_transport)
from src.pipelines.openai import OpenAILLMAdapter, OpenAITTSAdapter


@pytest.mark.parametrize("value", [None, "", "   "])
def test_canonical_blank_models_share_default_in_schema_and_adapter(value):
    cfg = OpenAIProviderConfig(api_key="synthetic", tts_model=value)
    assert cfg.tts_model == "gpt-realtime-2.1-mini"
    adapter = OpenAITTSAdapter("openai_tts", None, cfg)
    assert adapter._compose_options({})["tts_model"] == cfg.tts_model


@pytest.mark.parametrize("value", [None, "", "   "])
def test_custom_speech_endpoint_with_unspecified_model_preserves_endpoint_voice(value):
    cfg = OpenAIProviderConfig(api_key="synthetic", tts_base_url="http://127.0.0.1:9000/speech", tts_model=value, voice="operator-voice")
    adapter = OpenAITTSAdapter("openai_tts", None, cfg)
    effective = adapter._compose_options({})
    assert effective["tts_base_url"] == "http://127.0.0.1:9000/speech"
    assert effective["voice"] == "operator-voice"
    assert effective["tts_model"] == "tts-1"
    assert resolve_openai_tts_transport(effective["tts_model"], effective["tts_transport"]) == "speech"


def test_pipeline_custom_endpoint_requires_explicit_realtime_choice():
    cfg = OpenAIProviderConfig(api_key="synthetic")
    adapter = OpenAITTSAdapter("openai_tts", None, cfg, {"tts_base_url": "https://operator.example/speech"})
    assert adapter._compose_options({})["tts_model"] == "tts-1"
    assert adapter._compose_options({"tts_transport": "realtime"})["tts_model"] == "gpt-realtime-2.1-mini"
    assert OpenAIProviderConfig(tts_transport="speech").tts_model == "tts-1"


@pytest.mark.parametrize("model", ["tts-1", "tts-1-hd", "gpt-4o-mini-tts", "gpt-4o-mini-tts-2025-12-15"])
def test_schema_and_adapter_share_speech_snapshot_transport_rules(model):
    for transport in ("auto", "speech"):
        cfg = OpenAIProviderConfig(tts_model=model, tts_transport=transport)
        assert resolve_openai_tts_transport(cfg.tts_model, cfg.tts_transport) == "speech"
    with pytest.raises(ValidationError):
        OpenAIProviderConfig(tts_model=model, tts_transport="realtime")
    adapter = OpenAITTSAdapter("openai_tts", None, OpenAIProviderConfig())
    with pytest.raises(ValidationError):
        adapter._compose_options({"model": model, "tts_transport": "realtime"})


def test_optional_null_strings_retain_null_semantics():
    assert LocalProviderConfig(ws_url=None).ws_url is None
    assert GoogleProviderConfig(vad_end_of_speech_sensitivity=None).vad_end_of_speech_sensitivity is None
    assert provider_settings_payload(LocalProviderConfig, {"ws_url": None})["ws_url"] is None


@pytest.mark.parametrize("explicit", [False, True])
def test_pipeline_model_override_resolves_reasoning_after_effective_model(explicit):
    cfg = OpenAIProviderConfig(api_key="synthetic", **({"reasoning_effort": "low"} if explicit else {}))
    adapter = OpenAILLMAdapter("openai_llm", None, cfg, {"model": "gpt-6.2-sol"})
    options = adapter._compose_options({})
    assert options["reasoning_effort"] == ("low" if explicit else None)
    payload = adapter._build_chat_payload("synthetic", {}, options)
    assert payload.get("reasoning_effort") == ("low" if explicit else None)
    assert adapter._compose_options({"reasoning_effort": "medium"})["reasoning_effort"] == "medium"
    assert adapter._compose_options({"reasoning_effort": None})["reasoning_effort"] is None
