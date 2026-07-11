from __future__ import annotations

import os
from typing import Any, Dict

from config import LocalAIConfig


def detect_capabilities(config: LocalAIConfig) -> Dict[str, Any]:
    capabilities: Dict[str, Any] = {
        "vosk": False,
        "sherpa": False,
        "kroko_embedded": False,
        "faster_whisper": False,
        "whisper_cpp": False,
        "tone": False,
        "piper": False,
        "kokoro": False,
        "melotts": False,
        "silero": False,
        "matcha": False,
        "llama": False,
    }

    try:
        import vosk  # noqa: F401
        capabilities["vosk"] = True
    except ImportError:
        pass

    try:
        import sherpa_onnx  # noqa: F401
        capabilities["sherpa"] = True
    except ImportError:
        pass

    kroko_binary = "/usr/local/bin/kroko-server"
    if os.path.exists(kroko_binary):
        capabilities["kroko_embedded"] = True

    try:
        from faster_whisper import WhisperModel  # noqa: F401
        capabilities["faster_whisper"] = True
    except ImportError:
        pass

    try:
        from pywhispercpp.model import Model  # noqa: F401
        capabilities["whisper_cpp"] = True
    except ImportError:
        pass

    try:
        from tone.pipeline import StreamingCTCPipeline  # noqa: F401
        capabilities["tone"] = True
    except ImportError:
        pass

    try:
        from piper import PiperVoice  # noqa: F401
        capabilities["piper"] = True
    except ImportError:
        pass

    try:
        import kokoro  # noqa: F401
        capabilities["kokoro"] = True
    except ImportError:
        if config.kokoro_mode == "api" and config.kokoro_api_base_url:
            capabilities["kokoro"] = True
        elif os.path.exists(config.kokoro_model_path):
            capabilities["kokoro"] = True

    try:
        from melo.api import TTS  # noqa: F401
        capabilities["melotts"] = True
    except ImportError:
        pass

    # Silero requires both torch and a prepared torch.hub cache.  Reporting it
    # from the torch import alone makes minimal images look ready even though
    # initialization would immediately fail offline.
    try:
        import torch  # noqa: F401
        silero_available = os.path.isdir(config.silero_model_path) and bool(
            os.listdir(config.silero_model_path)
        )
    except ImportError:
        silero_available = False
    except OSError:
        silero_available = False
    if silero_available:
        capabilities["silero"] = True

    # Matcha uses sherpa-onnx at runtime and requires both the acoustic model
    # and vocoder.  Keep this separate from generic Sherpa STT availability.
    try:
        import sherpa_onnx  # noqa: F401

        capabilities["matcha"] = os.path.isfile(config.matcha_model_path) and os.path.isfile(
            config.matcha_vocoder_path
        )
    except ImportError:
        pass

    try:
        from llama_cpp import Llama  # noqa: F401
        capabilities["llama"] = True
    except ImportError:
        pass

    return capabilities
