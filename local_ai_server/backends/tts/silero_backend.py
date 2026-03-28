from __future__ import annotations

import os
from typing import Any, Dict, Optional

from backends.interface import TTSBackendInterface


class SileroBackend(TTSBackendInterface):
    def __init__(self):
        self._model = None

    @classmethod
    def name(cls) -> str:
        return "silero"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "speaker": {"type": "string", "required": False, "default": "xenia"},
            "language": {"type": "string", "required": False, "default": "ru"},
            "model_id": {"type": "string", "required": False, "default": "v3_1_ru"},
            "sample_rate": {"type": "integer", "required": False, "default": 8000},
            "model_path": {"type": "string", "required": False},
        }

    @classmethod
    def is_available(cls) -> bool:
        if os.getenv("INCLUDE_SILERO", "").lower() not in ("true", "1"):
            return False
        try:
            import torch  # noqa: F401
            return True
        except ImportError:
            return False

    def initialize(self, config: Dict[str, Any]) -> None:
        pass

    def shutdown(self) -> None:
        self._model = None

    def synthesize(self, text: str) -> Optional[bytes]:
        raise NotImplementedError("SileroBackend is a registry stub; use SileroTTSBackend from tts_backends.py")

    def status(self) -> Dict[str, Any]:
        return {"backend": "silero", "loaded": self._model is not None}
