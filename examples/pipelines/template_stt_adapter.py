"""
Template: STT Pipeline Adapter
===============================
Transcribes audio to text using an external STT service.

Usage:
    Open this file in Windsurf and tell AVA:
    "Help me build an STT adapter like this for [provider name]"

Guide: docs/contributing/adding-pipeline-adapter.md
Reference: src/pipelines/deepgram.py (Deepgram STT)
"""

import structlog
from typing import Dict, Any, Optional

from src.pipelines.base import STTComponent

logger = structlog.get_logger(__name__)


class TemplateSTT(STTComponent):
    """STT adapter for TemplateProvider.

    TODO: Replace 'Template' with your provider name.
    """

    def __init__(self, call_id: str, options: Dict[str, Any]):
        self.call_id = call_id
        self.options = options
        self._api_key = options.get("api_key") or self._auto_detect_credentials()

    async def transcribe(
        self,
        call_id: str,
        audio_pcm16: bytes,
        sample_rate_hz: int = 16000,
        options: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Transcribe PCM16 audio to text.

        Args:
            call_id: Unique call identifier.
            audio_pcm16: Raw PCM16 audio bytes (16kHz mono).
            sample_rate_hz: Audio sample rate (default 16000).
            options: Additional provider-specific options.

        Returns:
            Transcribed text string.

        TODO:
        - Send audio to your STT API
        - Parse the response
        - Return the transcribed text
        """
        # Example: POST audio to REST API
        # async with aiohttp.ClientSession() as session:
        #     resp = await session.post(
        #         "https://api.provider.com/v1/transcribe",
        #         headers={"Authorization": f"Bearer {self._api_key}"},
        #         data=audio_pcm16,
        #     )
        #     result = await resp.json()
        #     return result.get("text", "")
        return ""

    async def start(self):
        """Initialize resources (e.g., open persistent connection)."""
        logger.info("stt_adapter_started", provider="template", call_id=self.call_id)

    async def stop(self):
        """Clean up resources."""
        logger.info("stt_adapter_stopped", provider="template", call_id=self.call_id)
