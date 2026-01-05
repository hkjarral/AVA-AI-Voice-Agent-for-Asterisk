from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


def _parse_bool(raw: Optional[str], default: bool = False) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "y", "on"):
        return True
    if value in ("0", "false", "no", "n", "off"):
        return False
    return default


@dataclass(frozen=True)
class LocalAIConfig:
    runtime_mode: str = "full"
    ws_host: str = "127.0.0.1"
    ws_port: int = 8765
    ws_auth_token: str = ""

    mock_models: bool = False
    fail_fast: bool = False

    stt_backend: str = "vosk"
    stt_model_path: str = "/app/models/stt/vosk-model-en-us-0.22"

    sherpa_model_path: str = "/app/models/stt/sherpa"
    faster_whisper_model: str = "base"
    faster_whisper_device: str = "cpu"
    faster_whisper_compute: str = "int8"
    faster_whisper_language: str = "en"

    whisper_cpp_model_path: str = "/app/models/stt/ggml-base.en.bin"
    whisper_cpp_language: str = "en"

    kroko_url: str = "wss://app.kroko.ai/api/v1/transcripts/streaming"
    kroko_api_key: str = ""
    kroko_language: str = "en-US"
    kroko_model_path: str = "/app/models/kroko/kroko-en-v1.0.onnx"
    kroko_embedded: bool = False
    kroko_port: int = 6006

    llm_model_path: str = "/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf"
    llm_threads: int = 4
    llm_context: int = 768
    llm_batch: int = 128
    llm_max_tokens: int = 64
    llm_temperature: float = 0.4
    llm_top_p: float = 0.85
    llm_repeat_penalty: float = 1.05
    llm_gpu_layers: int = 0
    llm_system_prompt: str = (
        "You are a helpful AI voice assistant. Respond naturally and conversationally to the caller."
    )
    llm_stop_tokens: List[str] = field(
        default_factory=lambda: ["<|user|>", "<|assistant|>", "<|end|>"]
    )
    llm_use_mlock: bool = False
    llm_infer_timeout_sec: float = 20.0

    tts_backend: str = "piper"
    tts_model_path: str = "/app/models/tts/en_US-lessac-medium.onnx"

    melotts_voice: str = "EN-US"
    melotts_device: str = "cpu"
    melotts_speed: float = 1.0

    kokoro_voice: str = "af_heart"
    kokoro_mode: str = "local"
    kokoro_lang: str = "a"
    kokoro_model_path: str = "/app/models/tts/kokoro"
    kokoro_api_base_url: str = ""
    kokoro_api_key: str = ""
    kokoro_api_model: str = "model"

    stt_idle_ms: int = 5000

    @classmethod
    def from_env(cls) -> "LocalAIConfig":
        default_threads = max(1, min(16, os.cpu_count() or 1))

        stop_tokens = [
            token.strip()
            for token in (
                os.getenv("LOCAL_LLM_STOP_TOKENS", "<|user|>,<|assistant|>,<|end|>") or ""
            ).split(",")
            if token.strip()
        ] or ["<|user|>", "<|assistant|>", "<|end|>"]

        return cls(
            runtime_mode=(os.getenv("LOCAL_AI_MODE", "full") or "full").strip().lower(),
            ws_host=os.getenv("LOCAL_WS_HOST", "127.0.0.1"),
            ws_port=int(os.getenv("LOCAL_WS_PORT", "8765")),
            ws_auth_token=(os.getenv("LOCAL_WS_AUTH_TOKEN", "") or "").strip(),
            mock_models=_parse_bool(os.getenv("LOCAL_AI_MOCK_MODELS", "0")),
            fail_fast=_parse_bool(os.getenv("LOCAL_AI_FAIL_FAST", "0")),
            stt_backend=(os.getenv("LOCAL_STT_BACKEND", "vosk") or "vosk").strip().lower(),
            stt_model_path=os.getenv(
                "LOCAL_STT_MODEL_PATH", "/app/models/stt/vosk-model-en-us-0.22"
            ),
            sherpa_model_path=os.getenv("SHERPA_MODEL_PATH", "/app/models/stt/sherpa"),
            faster_whisper_model=os.getenv("FASTER_WHISPER_MODEL", "base"),
            faster_whisper_device=os.getenv("FASTER_WHISPER_DEVICE", "cpu"),
            faster_whisper_compute=os.getenv("FASTER_WHISPER_COMPUTE_TYPE", "int8"),
            faster_whisper_language=os.getenv("FASTER_WHISPER_LANGUAGE", "en"),
            whisper_cpp_model_path=os.getenv(
                "WHISPER_CPP_MODEL_PATH", "/app/models/stt/ggml-base.en.bin"
            ),
            whisper_cpp_language=os.getenv("WHISPER_CPP_LANGUAGE", "en"),
            kroko_url=os.getenv(
                "KROKO_URL",
                "wss://app.kroko.ai/api/v1/transcripts/streaming",
            ),
            kroko_api_key=os.getenv("KROKO_API_KEY", ""),
            kroko_language=os.getenv("KROKO_LANGUAGE", "en-US"),
            kroko_model_path=os.getenv(
                "KROKO_MODEL_PATH", "/app/models/kroko/kroko-en-v1.0.onnx"
            ),
            kroko_embedded=_parse_bool(os.getenv("KROKO_EMBEDDED", "0")),
            kroko_port=int(os.getenv("KROKO_PORT", "6006")),
            llm_model_path=os.getenv(
                "LOCAL_LLM_MODEL_PATH",
                "/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf",
            ),
            llm_threads=int(os.getenv("LOCAL_LLM_THREADS", str(default_threads))),
            llm_context=int(os.getenv("LOCAL_LLM_CONTEXT", "768")),
            llm_batch=int(os.getenv("LOCAL_LLM_BATCH", "128")),
            llm_max_tokens=int(os.getenv("LOCAL_LLM_MAX_TOKENS", "64")),
            llm_temperature=float(os.getenv("LOCAL_LLM_TEMPERATURE", "0.4")),
            llm_top_p=float(os.getenv("LOCAL_LLM_TOP_P", "0.85")),
            llm_repeat_penalty=float(os.getenv("LOCAL_LLM_REPEAT_PENALTY", "1.05")),
            llm_gpu_layers=int(os.getenv("LOCAL_LLM_GPU_LAYERS", "0")),
            llm_system_prompt=os.getenv(
                "LOCAL_LLM_SYSTEM_PROMPT",
                "You are a helpful AI voice assistant. Respond naturally and conversationally to the caller.",
            ),
            llm_stop_tokens=stop_tokens,
            llm_use_mlock=_parse_bool(os.getenv("LOCAL_LLM_USE_MLOCK", "0")),
            llm_infer_timeout_sec=float(os.getenv("LOCAL_LLM_INFER_TIMEOUT_SEC", "20.0")),
            tts_backend=(os.getenv("LOCAL_TTS_BACKEND", "piper") or "piper").strip().lower(),
            tts_model_path=os.getenv(
                "LOCAL_TTS_MODEL_PATH", "/app/models/tts/en_US-lessac-medium.onnx"
            ),
            melotts_voice=os.getenv("MELOTTS_VOICE", "EN-US"),
            melotts_device=os.getenv("MELOTTS_DEVICE", "cpu"),
            melotts_speed=float(os.getenv("MELOTTS_SPEED", "1.0")),
            kokoro_voice=os.getenv("KOKORO_VOICE") or os.getenv("LOCAL_TTS_VOICE", "af_heart"),
            kokoro_mode=(os.getenv("KOKORO_MODE", "local") or "local").strip().lower(),
            kokoro_lang=os.getenv("KOKORO_LANG", "a"),
            kokoro_model_path=os.getenv("KOKORO_MODEL_PATH", "/app/models/tts/kokoro"),
            kokoro_api_base_url=(os.getenv("KOKORO_API_BASE_URL", "") or "").strip(),
            kokoro_api_key=(os.getenv("KOKORO_API_KEY", "") or "").strip(),
            kokoro_api_model=(os.getenv("KOKORO_API_MODEL", "model") or "model").strip(),
            stt_idle_ms=int(os.getenv("LOCAL_STT_IDLE_MS", "5000")),
        )

