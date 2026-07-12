from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _load_local_ai_config_module():
    local_ai_dir = Path(__file__).resolve().parents[1] / "local_ai_server"
    sys.path.insert(0, str(local_ai_dir))
    try:
        return importlib.import_module("config")
    finally:
        # Avoid leaking path changes into other tests.
        if sys.path and sys.path[0] == str(local_ai_dir):
            sys.path.pop(0)


def test_llm_context_defaults_to_2048_on_gpu(monkeypatch):
    monkeypatch.delenv("LOCAL_LLM_CONTEXT", raising=False)
    monkeypatch.setenv("GPU_AVAILABLE", "1")
    config_mod = _load_local_ai_config_module()
    cfg = config_mod.LocalAIConfig.from_env()
    assert cfg.llm_context == 2048


def test_llm_context_defaults_to_768_on_cpu(monkeypatch):
    monkeypatch.delenv("LOCAL_LLM_CONTEXT", raising=False)
    monkeypatch.setenv("GPU_AVAILABLE", "0")
    config_mod = _load_local_ai_config_module()
    cfg = config_mod.LocalAIConfig.from_env()
    assert cfg.llm_context == 768


def test_llm_context_respects_env_override(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_CONTEXT", "1536")
    monkeypatch.setenv("GPU_AVAILABLE", "0")
    config_mod = _load_local_ai_config_module()
    cfg = config_mod.LocalAIConfig.from_env()
    assert cfg.llm_context == 1536


def test_llama_chat_format_auto_uses_embedded_gguf_template():
    config_mod = _load_local_ai_config_module()
    assert config_mod.llama_chat_format_override("auto") is None
    assert config_mod.llama_chat_format_override(" AUTO ") is None


def test_llama_chat_format_preserves_explicit_and_legacy_modes():
    config_mod = _load_local_ai_config_module()
    assert config_mod.llama_chat_format_override(" chatml ") == "chatml"
    assert config_mod.llama_chat_format_override("") is None


def test_documented_tts_phrase_cache_key_is_respected(monkeypatch):
    monkeypatch.setenv("LOCAL_TTS_PHRASE_CACHE_ENABLED", "true")
    monkeypatch.delenv("LOCAL_TTS_PHRASE_CACHE", raising=False)
    config_mod = _load_local_ai_config_module()
    assert config_mod.LocalAIConfig.from_env().tts_phrase_cache_enabled is True


def test_documented_tts_phrase_cache_key_wins_over_legacy_alias(monkeypatch):
    monkeypatch.setenv("LOCAL_TTS_PHRASE_CACHE_ENABLED", "false")
    monkeypatch.setenv("LOCAL_TTS_PHRASE_CACHE", "true")
    config_mod = _load_local_ai_config_module()
    assert config_mod.LocalAIConfig.from_env().tts_phrase_cache_enabled is False


def test_legacy_tts_phrase_cache_key_remains_compatible(monkeypatch):
    monkeypatch.delenv("LOCAL_TTS_PHRASE_CACHE_ENABLED", raising=False)
    monkeypatch.setenv("LOCAL_TTS_PHRASE_CACHE", "true")
    config_mod = _load_local_ai_config_module()
    assert config_mod.LocalAIConfig.from_env().tts_phrase_cache_enabled is True


def test_tool_gateway_enabled_defaults_true(monkeypatch):
    monkeypatch.delenv("LOCAL_TOOL_GATEWAY_ENABLED", raising=False)
    config_mod = _load_local_ai_config_module()
    cfg = config_mod.LocalAIConfig.from_env()
    assert cfg.tool_gateway_enabled is True


def test_tool_gateway_enabled_respects_env_false(monkeypatch):
    monkeypatch.setenv("LOCAL_TOOL_GATEWAY_ENABLED", "0")
    config_mod = _load_local_ai_config_module()
    cfg = config_mod.LocalAIConfig.from_env()
    assert cfg.tool_gateway_enabled is False


def test_default_voice_preamble_denies_unprovided_cross_call_memory(monkeypatch):
    monkeypatch.delenv("LOCAL_LLM_VOICE_PREAMBLE", raising=False)
    config_mod = _load_local_ai_config_module()
    cfg = config_mod.LocalAIConfig.from_env()
    assert "Treat each call as a new session" in cfg.llm_voice_preamble
    assert "Do not claim to remember previous calls" in cfg.llm_voice_preamble


def test_silero_config_defaults(monkeypatch):
    monkeypatch.delenv("SILERO_SPEAKER", raising=False)
    monkeypatch.delenv("SILERO_LANGUAGE", raising=False)
    monkeypatch.delenv("SILERO_MODEL_ID", raising=False)
    monkeypatch.delenv("SILERO_SAMPLE_RATE", raising=False)
    monkeypatch.delenv("SILERO_MODEL_PATH", raising=False)
    config_mod = _load_local_ai_config_module()
    cfg = config_mod.LocalAIConfig.from_env()
    assert cfg.silero_speaker == "xenia"
    assert cfg.silero_language == "ru"
    assert cfg.silero_model_id == "v3_1_ru"
    assert cfg.silero_sample_rate == 8000
    assert cfg.silero_model_path == "/app/models/tts/silero"


def test_silero_config_from_env(monkeypatch):
    monkeypatch.setenv("SILERO_SPEAKER", "aidar")
    monkeypatch.setenv("SILERO_LANGUAGE", "ru")
    monkeypatch.setenv("SILERO_MODEL_ID", "v3_1_ru")
    monkeypatch.setenv("SILERO_SAMPLE_RATE", "24000")
    monkeypatch.setenv("SILERO_MODEL_PATH", "/custom/silero")
    config_mod = _load_local_ai_config_module()
    cfg = config_mod.LocalAIConfig.from_env()
    assert cfg.silero_speaker == "aidar"
    assert cfg.silero_language == "ru"
    assert cfg.silero_model_id == "v3_1_ru"
    assert cfg.silero_sample_rate == 24000
    assert cfg.silero_model_path == "/custom/silero"
