from __future__ import annotations

import logging
import os

# Configure logging level from environment (default INFO)
_level_name = os.getenv("LOCAL_LOG_LEVEL", "INFO").upper()
_level = getattr(logging, _level_name, logging.INFO)
logging.basicConfig(
    level=_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S %z",
)

# Suppress noisy websockets connection logs unless DEBUG level
# These "connection open/close" messages spam the logs during health checks
if _level > logging.DEBUG:
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("websockets.server").setLevel(logging.WARNING)

# Debug mode for verbose audio processing logs
# Set LOCAL_DEBUG=1 in .env to enable detailed audio flow logging
DEBUG_AUDIO_FLOW = os.getenv("LOCAL_DEBUG", "0") == "1"

# WebSocket message protocol version. Single source of truth shared by the
# server (local_ai_server) and the engine client (src/providers/local.py).
# Bump only on a breaking change to the JSON message contract.
PROTOCOL_VERSION = 2

SUPPORTED_MODES = {"full", "stt", "llm", "tts"}
DEFAULT_MODE = "full"
ULAW_SAMPLE_RATE = 8000
PCM16_TARGET_RATE = 16000


def _normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


# SenseVoice language hints accepted by sherpa-onnx's OfflineRecognizer.from_sense_voice().
SENSE_VOICE_LANGUAGES = ("auto", "zh", "en", "ja", "ko", "yue")

# Kokoro's language code is the first character of its built-in voice ID.
# Mirrors the publisher-verified catalog; checked by provider catalog CI.
KOKORO_LANGUAGES = ("a", "b", "e", "f", "h", "i", "j", "p", "z")


def kokoro_voice_language(voice: str) -> str | None:
    """Infer the phonemizer for a single stock voice; leave custom values alone."""
    import re
    value = str(voice or "").strip()
    if re.fullmatch(r"[abefhijpz][fm]_[a-z]+", value):
        return value[0]
    return None
