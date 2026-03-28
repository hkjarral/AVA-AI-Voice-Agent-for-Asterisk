from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Dict, List, Tuple

from config import LocalAIConfig


_STT_CONFIG_MAP = {
    "model_path": "stt_model_path",
    "sherpa_model_path": "sherpa_model_path",
    "kroko_model_path": "kroko_model_path",
    "whisper_cpp_model_path": "whisper_cpp_model_path",
    "kroko_url": "kroko_url",
    "kroko_language": "kroko_language",
    "kroko_port": "kroko_port",
    "kroko_embedded": "kroko_embedded",
    "url": "kroko_url",
    "language": "kroko_language",
    "port": "kroko_port",
    "embedded": "kroko_embedded",
    "model": "faster_whisper_model",
    "device": "faster_whisper_device",
    "compute_type": "faster_whisper_compute",
    "faster_whisper_language": "faster_whisper_language",
    "whisper_cpp_language": "whisper_cpp_language",
    "sherpa_model_type": "sherpa_model_type",
    "sherpa_vad_model_path": "sherpa_vad_model_path",
    "tone_model_path": "tone_model_path",
    "tone_decoder_type": "tone_decoder_type",
    "tone_kenlm_path": "tone_kenlm_path",
}

_TTS_CONFIG_MAP = {
    "model_path": "tts_model_path",
    "voice": None,
    "lang": "kokoro_lang",
    "mode": "kokoro_mode",
    "api_base_url": "kokoro_api_base_url",
    "api_key": "kokoro_api_key",
    "api_model": "kokoro_api_model",
    "speed": "melotts_speed",
    "device": "melotts_device",
    "silero_language": "silero_language",
    "silero_model_id": "silero_model_id",
    "silero_model_path": "silero_model_path",
    "sample_rate": "silero_sample_rate",
}

_LLM_CONFIG_MAP = {
    "model_path": "llm_model_path",
    "threads": "llm_threads",
    "context": "llm_context",
    "batch": "llm_batch",
    "max_tokens": "llm_max_tokens",
    "temperature": "llm_temperature",
    "top_p": "llm_top_p",
    "repeat_penalty": "llm_repeat_penalty",
    "gpu_layers": "llm_gpu_layers",
    "system_prompt": "llm_system_prompt",
    "use_mlock": "llm_use_mlock",
    "chat_format": "llm_chat_format",
}


def _apply_config_dict(
    config: LocalAIConfig,
    cfg_dict: Dict[str, Any],
    mapping: Dict[str, str],
    changed: List[str],
    backend_name: str,
) -> LocalAIConfig:
    for key, value in cfg_dict.items():
        target = mapping.get(key)
        if target is None:
            if key == "voice" and backend_name == "kokoro":
                config = replace(config, kokoro_voice=str(value))
                changed.append(f"kokoro_voice={value}")
            elif key == "voice" and backend_name == "melotts":
                config = replace(config, melotts_voice=str(value))
                changed.append(f"melotts_voice={value}")
            elif key == "voice" and backend_name == "silero":
                config = replace(config, silero_speaker=str(value))
                changed.append(f"silero_speaker={value}")
            continue
        current = getattr(config, target, None)
        if isinstance(current, bool):
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "yes", "y", "on")
            value = bool(value)
        elif isinstance(current, int):
            value = int(value)
        elif isinstance(current, float):
            value = float(value)
        else:
            value = str(value)
        config = replace(config, **{target: value})
        display = os.path.basename(str(value)) if "path" in target else value
        changed.append(f"{target}={display}")
    return config


def apply_switch_model_request(
    config: LocalAIConfig, data: Dict[str, Any]
) -> Tuple[LocalAIConfig, List[str]]:
    changed: List[str] = []
    new_config = config

    if "stt_config" in data and isinstance(data["stt_config"], dict):
        backend = (data.get("stt_backend") or new_config.stt_backend or "").strip().lower()
        new_config = _apply_config_dict(new_config, data["stt_config"], _STT_CONFIG_MAP, changed, backend)

    if "tts_config" in data and isinstance(data["tts_config"], dict):
        backend = (data.get("tts_backend") or new_config.tts_backend or "").strip().lower()
        new_config = _apply_config_dict(new_config, data["tts_config"], _TTS_CONFIG_MAP, changed, backend)

    if "llm_config" in data and isinstance(data["llm_config"], dict):
        new_config = _apply_config_dict(new_config, data["llm_config"], _LLM_CONFIG_MAP, changed, "llama_cpp")

    if "stt_backend" in data:
        backend = (data["stt_backend"] or "").strip().lower()
        if backend in ("vosk", "sherpa", "kroko", "faster_whisper", "whisper_cpp", "tone"):
            new_config = replace(new_config, stt_backend=backend)
            changed.append(f"stt_backend={backend}")

    if "stt_model_path" in data:
        stt_path = data["stt_model_path"]
        if new_config.stt_backend == "sherpa":
            new_config = replace(new_config, sherpa_model_path=stt_path)
            changed.append(f"sherpa_model_path={os.path.basename(stt_path)}")
        elif new_config.stt_backend == "kroko":
            new_config = replace(new_config, kroko_model_path=stt_path)
            changed.append(f"kroko_model_path={os.path.basename(stt_path)}")
        elif new_config.stt_backend == "whisper_cpp":
            new_config = replace(new_config, whisper_cpp_model_path=stt_path)
            changed.append(f"whisper_cpp_model_path={os.path.basename(stt_path)}")
        elif new_config.stt_backend == "tone":
            new_config = replace(new_config, tone_model_path=stt_path)
            changed.append(f"tone_model_path={os.path.basename(stt_path)}")
        else:
            new_config = replace(new_config, stt_model_path=stt_path)
            changed.append(f"stt_model_path={os.path.basename(stt_path)}")

    if "sherpa_model_path" in data:
        value = data["sherpa_model_path"]
        new_config = replace(new_config, sherpa_model_path=value)
        changed.append(f"sherpa_model_path={os.path.basename(value)}")

    if "kroko_model_path" in data:
        value = data["kroko_model_path"]
        new_config = replace(new_config, kroko_model_path=value)
        changed.append(f"kroko_model_path={os.path.basename(value)}")

    if "whisper_cpp_model_path" in data:
        value = data["whisper_cpp_model_path"]
        new_config = replace(new_config, whisper_cpp_model_path=value)
        changed.append(f"whisper_cpp_model_path={os.path.basename(value)}")

    if "tone_model_path" in data:
        value = data["tone_model_path"]
        new_config = replace(new_config, tone_model_path=value)
        changed.append(f"tone_model_path={os.path.basename(value)}")

    if "kroko_language" in data:
        value = data["kroko_language"]
        new_config = replace(new_config, kroko_language=value)
        changed.append(f"kroko_language={value}")

    if "faster_whisper_language" in data:
        value = str(data["faster_whisper_language"]).strip().lower()
        new_config = replace(new_config, faster_whisper_language=value)
        changed.append(f"faster_whisper_language={value}")

    if "whisper_cpp_language" in data:
        value = str(data["whisper_cpp_language"]).strip().lower()
        new_config = replace(new_config, whisper_cpp_language=value)
        changed.append(f"whisper_cpp_language={value}")

    if "sherpa_model_type" in data:
        value = str(data["sherpa_model_type"]).strip().lower()
        if value in ("online", "offline"):
            new_config = replace(new_config, sherpa_model_type=value)
            changed.append(f"sherpa_model_type={value}")

    if "sherpa_vad_model_path" in data:
        value = str(data["sherpa_vad_model_path"])
        new_config = replace(new_config, sherpa_vad_model_path=value)
        changed.append(f"sherpa_vad_model_path={os.path.basename(value)}")

    if "tone_decoder_type" in data:
        value = str(data["tone_decoder_type"]).strip().lower()
        if value in ("beam_search", "greedy"):
            new_config = replace(new_config, tone_decoder_type=value)
            changed.append(f"tone_decoder_type={value}")

    if "tone_kenlm_path" in data:
        value = str(data["tone_kenlm_path"])
        new_config = replace(new_config, tone_kenlm_path=value)
        changed.append(f"tone_kenlm_path={os.path.basename(value)}")

    if "kroko_url" in data:
        new_config = replace(new_config, kroko_url=data["kroko_url"])
        changed.append("kroko_url=updated")

    if "kroko_port" in data:
        try:
            port = int(data["kroko_port"])
            new_config = replace(new_config, kroko_port=port)
            changed.append(f"kroko_port={port}")
        except Exception:
            pass

    if "kroko_embedded" in data:
        raw = data["kroko_embedded"]
        if isinstance(raw, str):
            raw = raw.strip().lower() in ("1", "true", "yes", "y", "on")
        embedded = bool(raw)
        new_config = replace(new_config, kroko_embedded=embedded)
        changed.append(f"kroko_embedded={'1' if embedded else '0'}")

    if "llm_model_path" in data:
        value = data["llm_model_path"]
        new_config = replace(new_config, llm_model_path=value)
        changed.append(f"llm_model_path={os.path.basename(value)}")

    if "tts_backend" in data:
        backend = (data["tts_backend"] or "").strip().lower()
        if backend in ("piper", "kokoro", "melotts", "silero"):
            new_config = replace(new_config, tts_backend=backend)
            changed.append(f"tts_backend={backend}")

    if "tts_model_path" in data:
        value = data["tts_model_path"]
        if new_config.tts_backend == "piper":
            new_config = replace(new_config, tts_model_path=value)
            changed.append(f"tts_model_path={os.path.basename(value)}")
        else:
            new_config = replace(new_config, kokoro_model_path=value)
            changed.append(f"kokoro_model_path={os.path.basename(value)}")

    if "kokoro_voice" in data:
        value = data["kokoro_voice"]
        new_config = replace(new_config, kokoro_voice=value)
        changed.append(f"kokoro_voice={value}")

    if "kokoro_mode" in data:
        value = (data["kokoro_mode"] or "local").strip().lower()
        new_config = replace(new_config, kokoro_mode=value)
        changed.append(f"kokoro_mode={value}")

    if "kokoro_model_path" in data:
        value = data["kokoro_model_path"]
        new_config = replace(new_config, kokoro_model_path=value)
        changed.append(f"kokoro_model_path={os.path.basename(value)}")

    if "kokoro_api_base_url" in data:
        new_config = replace(new_config, kokoro_api_base_url=(data["kokoro_api_base_url"] or "").strip())
        changed.append("kokoro_api_base_url=updated")

    if "kokoro_api_key" in data:
        new_config = replace(new_config, kokoro_api_key=(data["kokoro_api_key"] or "").strip())
        changed.append("kokoro_api_key=updated")

    if "kokoro_api_model" in data:
        value = (data["kokoro_api_model"] or "model").strip()
        new_config = replace(new_config, kokoro_api_model=value)
        changed.append(f"kokoro_api_model={value}")

    if "silero_speaker" in data:
        value = data["silero_speaker"]
        new_config = replace(new_config, silero_speaker=value)
        changed.append(f"silero_speaker={value}")

    if "silero_language" in data:
        value = data["silero_language"]
        new_config = replace(new_config, silero_language=value)
        changed.append(f"silero_language={value}")

    if "silero_model_id" in data:
        value = data["silero_model_id"]
        new_config = replace(new_config, silero_model_id=value)
        changed.append(f"silero_model_id={value}")

    if "silero_model_path" in data:
        value = data["silero_model_path"]
        new_config = replace(new_config, silero_model_path=value)
        changed.append(f"silero_model_path={value}")

    return new_config, changed
