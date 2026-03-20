from __future__ import annotations

from typing import Any, Dict, List
import numpy as np

from backends.interface import TTSBackendInterface


class KittenTTSBackend(TTSBackendInterface):
    """KittenTTS backend (ONNX, CPU-friendly, 24kHz).

    Models: https://huggingface.co/KittenML
    Voices: ['Bella', 'Jasper', 'Luna', 'Bruno', 'Rosie', 'Hugo', 'Kiki', 'Leo']
    """

    def __init__(self):
        self._model = None
        self._sample_rate = 24000
        self._available_voices: List[str] = []
        self._model_name: str | None = None

    @classmethod
    def name(cls) -> str:
        return "kittentts"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "model": {
                "type": "string",
                "required": False,
                "default": "KittenML/kitten-tts-micro-0.8",
                "description": "HF repo id for the KittenTTS model (nano/micro/mini)",
            },
            "voice": {
                "type": "string",
                "required": False,
                "default": "Jasper",
                "description": "Voice name (Bella, Jasper, Luna, Bruno, Rosie, Hugo, Kiki, Leo)",
            },
            "speed": {
                "type": "number",
                "required": False,
                "default": 1.0,
                "description": "Speech speed multiplier",
            },
            "cache_dir": {"type": "string", "required": False},
        }

    @classmethod
    def is_available(cls) -> bool:
        try:
            import kittentts  # noqa: F401
            return True
        except Exception:
            return False

    def initialize(self, config: Dict[str, Any]) -> None:
        model_name = config.get("model") or "KittenML/kitten-tts-micro-0.8"
        cache_dir = config.get("cache_dir")
        try:
            from kittentts import KittenTTS  # type: ignore
            self._model = KittenTTS(model_name, cache_dir=cache_dir)
            self._available_voices = list(getattr(self._model, "available_voices", []))
            self._model_name = model_name
        except Exception as exc:
            # Defer error to status/synthesize calls
            self._model = None
            self._model_name = model_name

    def shutdown(self) -> None:
        self._model = None
        self._available_voices = []
        self._model_name = None

    def synthesize(self, text: str) -> bytes:
        if not self._model:
            return b""
        # Defaults align with config_schema
        voice = getattr(self._model, "default_voice", None)
        speed = 1.0
        try:
            # Allow caller to override via attributes set during initialize
            # but typically the server passes the merged config into initialize only.
            # So we stick to Jasper/1.0 unless the upstream sets them in self.
            voice = getattr(self, "_voice", "Jasper")
            speed = getattr(self, "_speed", 1.0)
        except Exception:
            voice = "Jasper"
            speed = 1.0
        try:
            audio = self._model.generate(str(text), voice=str(voice), speed=float(speed))
            if audio is None:
                return b""
            # audio is a NumPy float32 array in [-1, 1] at 24kHz
            if isinstance(audio, np.ndarray):
                if audio.dtype != np.int16:
                    audio = (audio * 32767.0).clip(-32768, 32767).astype(np.int16)
                return audio.tobytes()
            return b""
        except Exception:
            return b""

    def status(self) -> Dict[str, Any]:
        return {
            "backend": "kittentts",
            "loaded": self._model is not None,
            "model": self._model_name,
            "sample_rate": self._sample_rate,
            "voices": self._available_voices,
        }
