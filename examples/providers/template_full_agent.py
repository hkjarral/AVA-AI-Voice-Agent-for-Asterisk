"""
Template: Full Agent Provider
=============================
A full agent provider handles STT + LLM + TTS in a single connection.

Usage:
    Open this file in Windsurf and tell AVA:
    "Help me build a full agent provider like this"

Guide: docs/contributing/adding-full-agent-provider.md
Reference: src/providers/openai_realtime.py
"""

import asyncio
import structlog
from typing import Dict, Any, List, Callable, Optional
from dataclasses import dataclass

from src.providers.base import (
    AIProviderInterface,
    ProviderCapabilities,
    ProviderCapabilitiesMixin,
)

logger = structlog.get_logger(__name__)


@dataclass
class TemplateProviderConfig:
    """Configuration for the template provider.

    Args:
        api_key: API key for authentication.
        model: Model identifier.
        voice: Voice identifier for TTS output.
    """
    api_key: str = ""
    model: str = "default-model"
    voice: str = "default-voice"


class TemplateProvider(AIProviderInterface, ProviderCapabilitiesMixin):
    """Template full agent provider.

    TODO: Replace 'Template' with your provider name throughout.
    """

    def __init__(
        self,
        config: TemplateProviderConfig,
        on_event: Callable[[Dict[str, Any]], None],
        **kwargs,
    ):
        self.config = config
        self._on_event = on_event
        self._call_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Required: supported_codecs
    # ------------------------------------------------------------------
    @property
    def supported_codecs(self) -> List[str]:
        """List of audio codecs this provider accepts."""
        # TODO: Return codecs your provider supports
        return ["pcm16"]

    # ------------------------------------------------------------------
    # Optional: capabilities
    # ------------------------------------------------------------------
    def get_capabilities(self) -> ProviderCapabilities:
        """Declare provider capabilities for audio negotiation."""
        return ProviderCapabilities(
            input_encodings=["pcm16"],
            input_sample_rates_hz=[16000],
            output_encodings=["pcm16"],
            output_sample_rates_hz=[16000],
            preferred_chunk_ms=20,
            is_full_agent=True,
            has_native_vad=False,  # Set True if provider has VAD
        )

    # ------------------------------------------------------------------
    # Required: start_session
    # ------------------------------------------------------------------
    async def start_session(
        self, call_id: str, on_event: Callable[[Dict[str, Any]], None]
    ):
        """Initialize a session for a new call.

        TODO:
        - Open WebSocket connection to your provider
        - Authenticate with API key
        - Send session configuration (model, voice, system prompt)
        - Start background receive task
        """
        self._call_id = call_id
        self._on_event = on_event
        logger.info("session_started", call_id=call_id, provider="template")

    # ------------------------------------------------------------------
    # Required: send_audio
    # ------------------------------------------------------------------
    async def send_audio(self, audio_chunk: bytes):
        """Send an audio chunk to the provider.

        Called continuously during the call with ~20ms audio chunks.

        TODO:
        - Encode audio if needed (base64, etc.)
        - Send over WebSocket
        """
        pass

    # ------------------------------------------------------------------
    # Required: stop_session
    # ------------------------------------------------------------------
    async def stop_session(self):
        """Clean up when the call ends.

        TODO:
        - Close WebSocket connection
        - Cancel background tasks
        - Release resources
        """
        logger.info("session_stopped", call_id=self._call_id, provider="template")

    # ------------------------------------------------------------------
    # Event emission helpers
    # ------------------------------------------------------------------
    def _emit_audio(self, audio_bytes: bytes):
        """Send audio output back to the caller."""
        self._on_event({"type": "audio", "audio": audio_bytes})

    def _emit_transcript(self, text: str, is_final: bool = True):
        """Send a transcript of what the user said."""
        self._on_event({"type": "transcript", "text": text, "is_final": is_final})

    def _emit_response(self, text: str):
        """Send the AI's text response."""
        self._on_event({"type": "response", "text": text})

    def _emit_tool_call(self, tool_name: str, call_id: str, parameters: Dict):
        """Request a tool call (function calling)."""
        self._on_event({
            "type": "tool_call",
            "tool_name": tool_name,
            "tool_call_id": call_id,
            "parameters": parameters,
        })
