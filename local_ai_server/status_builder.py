from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from constants import DEBUG_AUDIO_FLOW, _level_name

def _stt_language(server) -> Optional[str]:
    backend = server.stt_backend
    if backend == "kroko":
        return getattr(server, "kroko_language", None)
    if backend == "tone":
        return "ru"
    if backend == "faster_whisper":
        return getattr(server, "faster_whisper_language", None)
    if backend == "whisper_cpp":
        return getattr(server, "whisper_cpp_language", None)
    return None


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
        model_type = getattr(server, "sherpa_model_type", "online")
        display = f"Sherpa ({os.path.basename(server.sherpa_model_path)}, {model_type})"
        return loaded, path, display
    if server.stt_backend == "tone":
        loaded = server.mock_models or getattr(server, "tone_backend", None) is not None
        path = getattr(server, "tone_model_path", None)
        decoder = getattr(server, "tone_decoder_type", "beam_search")
        display = f"T-one ({os.path.basename(path or 't-one')}, {decoder})"
        return loaded, path, display
    if server.stt_backend == "faster_whisper":
        loaded = server.mock_models or server.faster_whisper_backend is not None
        path = server.faster_whisper_model
        lang = getattr(server, "faster_whisper_language", "en")
        display = f"Faster-Whisper ({server.faster_whisper_model}, {lang})"
        return loaded, path, display
    if server.stt_backend == "whisper_cpp":
        loaded = server.mock_models or server.whisper_cpp_backend is not None
        path = getattr(server, "whisper_cpp_model_path", None)
        lang = getattr(server, "whisper_cpp_language", "en")
        if loaded:
            display = f"Whisper.cpp ({lang})"
        else:
            display = "Whisper.cpp (not loaded)"
        return loaded, path, display
    return False, None, None


def _tts_status(server) -> Tuple[bool, Optional[str], Optional[str]]:
    if server.tts_backend == "piper":
        loaded = server.mock_models or server.tts_model is not None
        path = server.tts_model_path
        display = os.path.basename(server.tts_model_path)
        return loaded, path, display
    if server.tts_backend == "kokoro":
        # For UI consistency, keep `models.tts.path` stable as the configured Kokoro model path
        # (matches the value returned by /api/local-ai/models) regardless of kokoro_mode.
        #
        # kokoro_mode and API base_url are still exposed under the top-level "kokoro" object.
        stable_path = server.kokoro_model_path
        if server.kokoro_mode == "api":
            loaded = server.mock_models or bool(server.kokoro_api_base_url)
            display = f"Kokoro Web API ({server.kokoro_voice})"
            return loaded, stable_path, display

        loaded = server.mock_models or server.kokoro_backend is not None
        suffix = f", mode={server.kokoro_mode}" if server.kokoro_mode else ""
        display = f"Kokoro ({server.kokoro_voice}{suffix})"
        return loaded, stable_path, display
    if server.tts_backend == "melotts":
        loaded = server.mock_models or server.melotts_backend is not None
        path = server.melotts_voice
        display = f"MeloTTS ({server.melotts_voice})"
        return loaded, path, display
    if server.tts_backend == "silero":
        loaded = server.mock_models or server.silero_backend is not None
        # Path must match the dropdown option format: "speaker:model_id"
        # (e.g. "es_0:v3_es") so the UI can select the correct entry.
        speaker = getattr(server, "silero_speaker", "xenia")
        model_id = getattr(server, "silero_model_id", "v3_1_ru")
        path = f"{speaker}:{model_id}"
        display = f"Silero ({server.silero_language}/{speaker})"
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

    # Prompt-fit diagnostics (best-effort; used for UI guidance).
    system_prompt = (getattr(server, "llm_system_prompt", "") or "").strip()
    system_prompt_chars = len(system_prompt)
    system_prompt_tokens = None
    safe_max_tokens = None
    try:
        # Estimate tokens using the same wrapper the model sees.
        if hasattr(server, "_build_phi_prompt_with_system"):
            estimate_prompt = server._build_phi_prompt_with_system("Hello", system_prompt)
        else:
            estimate_prompt = server._build_phi_prompt("Hello")
        system_prompt_tokens = int(server._count_prompt_tokens(estimate_prompt))
        margin = 8
        safe_max_tokens = max(1, int(server.llm_context) - system_prompt_tokens - margin)
    except Exception:
        system_prompt_tokens = None
        safe_max_tokens = None

    gpu_status: Dict[str, Any]
    try:
        gpu_status = server.get_gpu_runtime_status()
    except Exception as exc:  # pragma: no cover - defensive status path
        gpu_status = {
            "host_preflight_detected": None,
            "host_preflight_raw": None,
            "runtime_detected": False,
            "runtime_usable": False,
            "source": "none",
            "name": None,
            "memory_gb": None,
            "error": str(exc),
            "checked_at_epoch_ms": None,
        }

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
                "language": _stt_language(server),
                "sherpa_model_type": getattr(server, "sherpa_model_type", None) if server.stt_backend == "sherpa" else None,
                "tone_decoder_type": getattr(server, "tone_decoder_type", None) if server.stt_backend == "tone" else None,
            },
            "llm": {
                "loaded": llm_loaded,
                "path": server.llm_model_path,
                "display": llm_display,
                "config": {
                    "context": server.llm_context,
                    "threads": server.llm_threads,
                    "batch": server.llm_batch,
                    "max_tokens": getattr(server, "llm_max_tokens", None),
                    "temperature": getattr(server, "llm_temperature", None),
                    "top_p": getattr(server, "llm_top_p", None),
                    "repeat_penalty": getattr(server, "llm_repeat_penalty", None),
                    "gpu_layers": getattr(server, "_llm_gpu_layers_effective", None),
                },
                "prompt_fit": {
                    "system_prompt_chars": system_prompt_chars,
                    "system_prompt_tokens": system_prompt_tokens,
                    "safe_max_tokens": safe_max_tokens,
                },
                "auto_context": dict(getattr(server, "_llm_auto_ctx_meta", {}) or {}),
                "tool_capability": dict(getattr(server, "_llm_tool_capability_meta", {}) or {}),
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
        "silero": {
            "language": getattr(server, "silero_language", "ru"),
            "speaker": getattr(server, "silero_speaker", "xenia"),
            "model_id": getattr(server, "silero_model_id", "v3_1_ru"),
            "model_path": getattr(server, "silero_model_path", "/app/models/tts/silero"),
            "sample_rate": getattr(server, "silero_sample_rate", 8000),
        },
        "gpu": gpu_status,
        "config": {
            "log_level": _level_name,
            "debug_audio": DEBUG_AUDIO_FLOW,
            "mock_models": server.mock_models,
            "runtime_mode": runtime_mode,
            "tool_gateway_enabled": bool(getattr(server, "tool_gateway_enabled", True)),
            "degraded": bool(server.startup_errors),
            "startup_errors": dict(server.startup_errors) if server.startup_errors else {},
            "runtime_fallbacks": dict(getattr(server, "runtime_fallbacks", {}) or {}),
        },
    }
