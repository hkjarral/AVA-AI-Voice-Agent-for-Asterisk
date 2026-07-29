from __future__ import annotations

from typing import Any, Dict

from backends.interface import TTSBackendInterface


class MatchaBackend(TTSBackendInterface):
    """Matcha-TTS via sherpa-onnx — fast, high-quality CPU TTS."""

    def __init__(self):
        self._tts = None

    @classmethod
    def name(cls) -> str:
        return "matcha"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "matcha_model_path": {
                "type": "string",
                "required": True,
                "description": "Path to Matcha acoustic model ONNX file",
            },
            "matcha_vocoder_path": {
                "type": "string",
                "required": True,
                "description": "Path to Vocos vocoder ONNX file",
            },
            "matcha_speed": {
                "type": "number",
                "required": False,
                "default": 1.0,
                "description": "Speech speed (1.0 = normal)",
            },
            "matcha_sid": {
                "type": "integer",
                "required": False,
                "default": 0,
                "description": "Speaker ID (for multi-speaker models)",
            },
        }

    @classmethod
    def is_available(cls) -> bool:
        # Matcha TTS synthesis is handled directly in server.py via
        # _process_tts_matcha() using sherpa-onnx OfflineTts. This backend
        # class is a registry placeholder — do NOT report as available since
        # initialize()/synthesize() are not wired to the real implementation.
        return False

    def initialize(self, config: Dict[str, Any]) -> None:
        pass

    def shutdown(self) -> None:
        self._tts = None

    def synthesize(self, text: str) -> bytes:
        # Real synthesis is in server.py:_process_tts_matcha()
        return b""

    def status(self) -> Dict[str, Any]:
        return {"backend": "matcha", "loaded": self._tts is not None}
