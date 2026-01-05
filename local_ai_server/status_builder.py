from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from constants import DEBUG_AUDIO_FLOW, _level_name

def _stt_status(server) -> Tuple[bool, Optional[str], Optional[str]]:
    if server.stt_backend == "vosk":
        loaded = server.mock_models or server.stt_model is not None
        path = server.stt_model_path
        display = os.path.basename(server.stt_model_path)
        return loaded, path, display
    if server.stt_backend == "kroko":
        loaded = server.mock_models or server.kroko_backend is not None
        path = server.kroko_model_path if server.kroko_embedded else server.kroko_url
        display = (
            f"Kroko (embedded, port {server.kroko_port})"
            if server.kroko_embedded
            else f"Kroko ({server.kroko_language})"
        )
        return loaded, path, display
    if server.stt_backend == "sherpa":
        loaded = server.mock_models or server.sherpa_backend is not None
        path = server.sherpa_model_path
        display = os.path.basename(server.sherpa_model_path)
        return loaded, path, display
    if server.stt_backend == "faster_whisper":
        loaded = server.mock_models or server.faster_whisper_backend is not None
        path = server.faster_whisper_model
        display = f"Faster-Whisper ({server.faster_whisper_model})"
        return loaded, path, display
    if server.stt_backend == "whisper_cpp":
        loaded = server.mock_models or server.whisper_cpp_backend is not None
        path = getattr(server, "whisper_cpp_model_path", None)
        display = "Whisper.cpp" if loaded else "Whisper.cpp (not loaded)"
        return loaded, path, display
    return False, None, None


def _tts_status(server) -> Tuple[bool, Optional[str], Optional[str]]:
    if server.tts_backend == "piper":
        loaded = server.mock_models or server.tts_model is not None
        path = server.tts_model_path
        display = os.path.basename(server.tts_model_path)
        return loaded, path, display
    if server.tts_backend == "kokoro":
        if server.kokoro_mode == "api":
            loaded = server.mock_models or bool(server.kokoro_api_base_url)
            path = server.kokoro_api_base_url
            display = f"Kokoro Web API ({server.kokoro_voice})"
            return loaded, path, display

        loaded = server.mock_models or server.kokoro_backend is not None
        path = (
            server.kokoro_model_path
            if server.kokoro_mode != "hf"
            else "hf://hexgrad/Kokoro-82M"
        )
        display = f"Kokoro ({server.kokoro_voice})"
        return loaded, path, display
    if server.tts_backend == "melotts":
        loaded = server.mock_models or server.melotts_backend is not None
        path = server.melotts_voice
        display = f"MeloTTS ({server.melotts_voice})"
        return loaded, path, display
    return False, None, None


def build_status_response(server) -> Dict[str, Any]:
    stt_loaded, stt_path, stt_display = _stt_status(server)
    tts_loaded, tts_path, tts_display = _tts_status(server)
    llm_display = os.path.basename(server.llm_model_path)
    runtime_mode = (getattr(server, "runtime_mode", None) or "full").strip().lower()
    llm_loaded = server.mock_models or server.llm_model is not None
    if runtime_mode == "minimal":
        llm_loaded = False

    return {
        "type": "status_response",
        "status": "ok",
        "stt_backend": server.stt_backend,
        "tts_backend": server.tts_backend,
        "models": {
            "stt": {
                "backend": server.stt_backend,
                "loaded": stt_loaded,
                "path": stt_path,
                "display": stt_display,
            },
            "llm": {
                "loaded": llm_loaded,
                "path": server.llm_model_path,
                "display": llm_display,
                "config": {
                    "context": server.llm_context,
                    "threads": server.llm_threads,
                    "batch": server.llm_batch,
                },
            },
            "tts": {
                "backend": server.tts_backend,
                "loaded": tts_loaded,
                "path": tts_path,
                "display": tts_display,
            },
        },
        "kroko": {
            "embedded": server.kroko_embedded,
            "port": server.kroko_port,
            "language": server.kroko_language,
            "url": server.kroko_url,
            "model_path": server.kroko_model_path,
        },
        "kokoro": {
            "mode": server.kokoro_mode,
            "voice": server.kokoro_voice,
            "model_path": server.kokoro_model_path,
            "api_base_url": server.kokoro_api_base_url,
            "api_key_set": bool(server.kokoro_api_key),
        },
        "config": {
            "log_level": _level_name,
            "debug_audio": DEBUG_AUDIO_FLOW,
            "mock_models": server.mock_models,
            "runtime_mode": runtime_mode,
            "degraded": bool(server.startup_errors),
            "startup_errors": dict(server.startup_errors) if server.startup_errors else {},
        },
    }
