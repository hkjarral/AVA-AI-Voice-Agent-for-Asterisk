"""
ElevenLabs TTS Pipeline Adapter.

Implements the TTSComponent interface for ElevenLabs text-to-speech API.

API Reference: https://elevenlabs.io/docs/api-reference/text-to-speech
"""
from __future__ import annotations

import audioop
from ..audio.resampler import resample_audio
import time
import uuid
from typing import Any, AsyncIterator, Callable, Dict, Optional

import aiohttp

from ..config import AppConfig, ElevenLabsProviderConfig
from ..logging_config import get_logger
from .base import TTSComponent

logger = get_logger(__name__)


class ElevenLabsTTSAdapter(TTSComponent):
    """
    ElevenLabs TTS adapter for pipeline orchestrator.
    
    Converts text to speech using ElevenLabs API with automatic
    audio format conversion to μ-law 8kHz for telephony.
    """

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: ElevenLabsProviderConfig,
        options: Optional[Dict[str, Any]] = None,
        *,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_config = provider_config
        self._pipeline_defaults = options or {}
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        """Initialize the adapter."""
        logger.debug(
            "ElevenLabs TTS adapter initialized",
            component=self.component_key,
            voice_id=self._provider_config.voice_id,
            model_id=self._provider_config.model_id,
        )

    async def stop(self) -> None:
        """Cleanup adapter resources."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        """Prepare for a call (ensure HTTP session exists)."""
        await self._ensure_session()

    async def close_call(self, call_id: str) -> None:
        """Cleanup call resources (no per-call state)."""
        pass

    async def validate_connectivity(self, options: Dict[str, Any]) -> Dict[str, Any]:
        # Merge provider config into options so the base validator sees base_url.
        merged = self._compose_options(options or {})
        return await super().validate_connectivity(merged)

    async def synthesize(
        self,
        call_id: str,
        text: str,
        options: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """
        Synthesize text to speech using ElevenLabs API.
        
        Args:
            call_id: Unique call identifier
            text: Text to synthesize
            options: Runtime options (can override defaults)
            
        Yields:
            Audio chunks in μ-law 8kHz format
        """
        if not text:
            return
            yield  # Makes this an async generator
            
        await self._ensure_session()
        merged = self._compose_options(options)
        
        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("ElevenLabs TTS requires an API key (ELEVENLABS_API_KEY)")
        
        voice_id = merged.get("voice_id", self._provider_config.voice_id)
        model_id = merged.get("model_id", self._provider_config.model_id)
        output_format = merged.get("output_format", "ulaw_8000")
        
        request_id = f"11labs-tts-{uuid.uuid4().hex[:12]}"
        
        # Build API URL
        # https://elevenlabs.io/docs/api-reference/text-to-speech
        base_url = merged.get("base_url", self._provider_config.base_url)
        url = f"{base_url}/text-to-speech/{voice_id}"
        
        # Voice settings
        voice_settings = {
            "stability": merged.get("stability", self._provider_config.stability),
            "similarity_boost": merged.get("similarity_boost", self._provider_config.similarity_boost),
            "style": merged.get("style", self._provider_config.style),
            "use_speaker_boost": merged.get("use_speaker_boost", self._provider_config.use_speaker_boost),
        }
        
        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": voice_settings,
        }
        
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/basic",  # μ-law audio
        }
        
        params = {
            "output_format": output_format,
        }
        
        logger.info(
            "ElevenLabs TTS synthesis started",
            call_id=call_id,
            request_id=request_id,
            text_preview=text[:64],
            voice_id=voice_id,
            model_id=model_id,
            output_format=output_format,
        )
        
        started_at = time.perf_counter()
        
        try:
            async with self._session.post(url, json=payload, headers=headers, params=params) as response:
                if response.status >= 400:
                    body = await response.text()
                    logger.error(
                        "ElevenLabs TTS synthesis failed",
                        call_id=call_id,
                        request_id=request_id,
                        status=response.status,
                        body=body,
                    )
                    response.raise_for_status()
                
                # Read the full audio response
                raw_audio = await response.read()
                latency_ms = (time.perf_counter() - started_at) * 1000.0
                
                # Convert if needed (ulaw_8000 is native telephony format)
                if output_format == "ulaw_8000":
                    converted = raw_audio
                elif output_format == "pcm_16000":
                    # Convert PCM16 16kHz to μ-law 8kHz
                    resampled, _ = resample_audio(raw_audio, 16000, 8000)
                    converted = audioop.lin2ulaw(resampled, 2)
                elif output_format == "pcm_24000":
                    # Convert PCM16 24kHz to μ-law 8kHz
                    resampled, _ = resample_audio(raw_audio, 24000, 8000)
                    converted = audioop.lin2ulaw(resampled, 2)
                else:
                    # For other formats, assume it's already usable or skip conversion
                    logger.warning(
                        "Unknown output format, passing through raw audio",
                        output_format=output_format,
                    )
                    converted = raw_audio
                
                logger.info(
                    "ElevenLabs TTS synthesis completed",
                    call_id=call_id,
                    request_id=request_id,
                    latency_ms=round(latency_ms, 2),
                    raw_bytes=len(raw_audio),
                    output_bytes=len(converted),
                )
                
                # Yield in chunks for streaming playback
                chunk_ms = int(merged.get("chunk_size_ms", 20))
                for chunk in self._chunk_audio(converted, chunk_ms):
                    if chunk:
                        yield chunk
                        
        except aiohttp.ClientError as exc:
            logger.error(
                "ElevenLabs TTS HTTP error",
                call_id=call_id,
                request_id=request_id,
                error=str(exc),
            )
            raise

    async def _ensure_session(self) -> None:
        """Ensure HTTP session exists."""
        if self._session and not self._session.closed:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory()

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge runtime options with defaults."""
        runtime_options = runtime_options or {}
        
        # Priority: runtime > pipeline defaults > provider config
        merged = {
            "api_key": runtime_options.get("api_key", 
                self._pipeline_defaults.get("api_key", self._provider_config.api_key)),
            "voice_id": runtime_options.get("voice_id",
                self._pipeline_defaults.get("voice_id", self._provider_config.voice_id)),
            "model_id": runtime_options.get("model_id",
                self._pipeline_defaults.get("model_id", self._provider_config.model_id)),
            "base_url": runtime_options.get("base_url",
                self._pipeline_defaults.get("base_url", self._provider_config.base_url)),
            "output_format": runtime_options.get("output_format",
                self._pipeline_defaults.get("output_format", self._provider_config.output_format)),
            "stability": runtime_options.get("stability",
                self._pipeline_defaults.get("stability", self._provider_config.stability)),
            "similarity_boost": runtime_options.get("similarity_boost",
                self._pipeline_defaults.get("similarity_boost", self._provider_config.similarity_boost)),
            "style": runtime_options.get("style",
                self._pipeline_defaults.get("style", self._provider_config.style)),
            "use_speaker_boost": runtime_options.get("use_speaker_boost",
                self._pipeline_defaults.get("use_speaker_boost", self._provider_config.use_speaker_boost)),
            "chunk_size_ms": runtime_options.get("chunk_size_ms",
                self._pipeline_defaults.get("chunk_size_ms", 20)),
        }
        
        return merged

    def _chunk_audio(self, audio: bytes, chunk_ms: int = 20) -> list:
        """Split μ-law audio into chunks for streaming playback."""
        # μ-law at 8kHz: 8 bytes per ms
        bytes_per_ms = 8
        chunk_size = bytes_per_ms * chunk_ms
        
        chunks = []
        for i in range(0, len(audio), chunk_size):
            chunk = audio[i:i + chunk_size]
            if chunk:
                chunks.append(chunk)
        
        return chunks
