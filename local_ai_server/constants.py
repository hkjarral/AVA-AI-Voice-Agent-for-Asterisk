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
KOKORO_STOCK_VOICES = frozenset("""
af_alloy af_aoede af_bella af_heart af_jessica af_kore af_nicole af_nova af_river af_sarah af_sky
am_adam am_echo am_eric am_fenrir am_liam am_michael am_onyx am_puck am_santa
bf_alice bf_emma bf_isabella bf_lily bm_daniel bm_fable bm_george bm_lewis
ef_dora em_alex em_santa ff_siwis hf_alpha hf_beta hm_omega hm_psi if_sara im_nicola
jf_alpha jf_gongitsune jf_nezumi jf_tebukuro jm_kumo pf_dora pm_alex pm_santa
zf_xiaobei zf_xiaoni zf_xiaoxiao zf_xiaoyi zm_yunjian zm_yunxi zm_yunxia zm_yunyang
""".split())


def kokoro_voice_language(voice: str) -> str | None:
    """Infer the phonemizer for a single stock voice; leave custom values alone."""
    value = str(voice or "").strip()
    if value in KOKORO_STOCK_VOICES:
        return value[0]
    return None
