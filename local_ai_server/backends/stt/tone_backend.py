from __future__ import annotations

from typing import Any, Dict, Optional

from backends.interface import STTBackendInterface


class ToneBackend(STTBackendInterface):
    def __init__(self):
        self._model = None

    @classmethod
    def name(cls) -> str:
        return "tone"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "model_path": {"type": "string", "required": True, "description": "Path to T-one model directory"},
            "decoder_type": {
                "type": "string",
                "required": False,
                "enum": ["beam_search", "greedy"],
                "description": "T-one decoder mode",
            },
            "kenlm_path": {"type": "string", "required": False, "description": "Path to kenlm.bin for beam search"},
        }

    @classmethod
    def is_available(cls) -> bool:
        try:
            from tone.pipeline import StreamingCTCPipeline  # noqa: F401

            return True
        except ImportError:
            return False

    def initialize(self, config: Dict[str, Any]) -> None:
        pass

    def shutdown(self) -> None:
        self._model = None

    def process_audio(self, audio_bytes: bytes) -> Optional[str]:
        raise NotImplementedError("ToneBackend is a registry stub; use ToneSTTBackend from stt_backends.py")

    def status(self) -> Dict[str, Any]:
        return {"backend": "tone", "loaded": self._model is not None}
