"""
CAMB AI TTS Pipeline Adapter.

Implements the TTSComponent interface for CAMB AI's MARS text-to-speech models.
Supports streaming synthesis with mars-flash (~150ms latency), mars-pro, and
mars-instruct models.

API Reference: https://docs.camb.ai
"""
from __future__ import annotations

import audioop
from ..audio.resampler import resample_audio
import time
import uuid
from typing import Any, AsyncIterator, Callable, Dict, Optional

import aiohttp

from ..config import AppConfig, CambAiProviderConfig
from ..logging_config import get_logger
from .base import TTSComponent

logger = get_logger(__name__)

# CAMB AI streaming TTS returns PCM at 24kHz by default for pcm_s16le
CAMB_AI_PCM_SAMPLE_RATE = 24000


class CambAiTTSAdapter(TTSComponent):
    """
    CAMB AI TTS adapter for pipeline orchestrator.

    Converts text to speech using CAMB AI's MARS models with automatic
    audio format conversion to μ-law 8kHz for telephony.
    """

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: CambAiProviderConfig,
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
        logger.debug(
            "CAMB AI TTS adapter initialized",
            component=self.component_key,
            voice_id=self._provider_config.voice_id,
            speech_model=self._provider_config.speech_model,
            language=self._provider_config.language,
        )

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        await self._ensure_session()

    async def close_call(self, call_id: str) -> None:
        pass

    async def validate_connectivity(self, options: Dict[str, Any]) -> Dict[str, Any]:
        merged = self._compose_options(options or {})
        return await super().validate_connectivity(merged)

    async def synthesize(
        self,
        call_id: str,
        text: str,
        options: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """
        Synthesize text to speech using CAMB AI streaming TTS API.

        Yields audio chunks in μ-law 8kHz format for telephony playback.
        """
        if not text:
            return
            yield  # Makes this an async generator

        await self._ensure_session()
        merged = self._compose_options(options)

        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("CAMB AI TTS requires an API key (CAMB_API_KEY)")

        voice_id = merged.get("voice_id", self._provider_config.voice_id)
        speech_model = merged.get("speech_model", self._provider_config.speech_model)
        language = merged.get("language", self._provider_config.language)
        output_format = merged.get("output_format", self._provider_config.output_format)

        request_id = f"camb-tts-{uuid.uuid4().hex[:12]}"

        base_url = merged.get("base_url", self._provider_config.base_url)
        url = f"{base_url}/tts-stream"

        payload = {
            "text": text,
            "voice_id": voice_id,
            "language": language,
            "speech_model": speech_model,
            "output_configuration": {"format": output_format},
        }

        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }

        logger.info(
            "CAMB AI TTS synthesis started",
            call_id=call_id,
            request_id=request_id,
            text_preview=text[:64],
            voice_id=voice_id,
            speech_model=speech_model,
            language=language,
        )

        started_at = time.perf_counter()

        try:
            async with self._session.post(url, json=payload, headers=headers) as response:
                if response.status >= 400:
                    body = await response.text()
                    logger.error(
                        "CAMB AI TTS synthesis failed",
                        call_id=call_id,
                        request_id=request_id,
                        status=response.status,
                        body=body,
                    )
                    response.raise_for_status()

                raw_audio = await response.read()
                latency_ms = (time.perf_counter() - started_at) * 1000.0

                # Convert PCM16 to μ-law 8kHz for telephony
                converted = self._convert_to_ulaw(raw_audio, output_format)

                logger.info(
                    "CAMB AI TTS synthesis completed",
                    call_id=call_id,
                    request_id=request_id,
                    latency_ms=round(latency_ms, 2),
                    raw_bytes=len(raw_audio),
                    output_bytes=len(converted),
                )

                chunk_ms = int(merged.get("chunk_size_ms", 20))
                for chunk in self._chunk_audio(converted, chunk_ms):
                    if chunk:
                        yield chunk

        except aiohttp.ClientError as exc:
            logger.error(
                "CAMB AI TTS HTTP error",
                call_id=call_id,
                request_id=request_id,
                error=str(exc),
            )
            raise

    def _convert_to_ulaw(self, raw_audio: bytes, output_format: str) -> bytes:
        """Convert CAMB AI audio output to μ-law 8kHz for telephony."""
        if output_format == "pcm_s16le":
            # Raw PCM 16-bit signed little-endian at 24kHz -> resample to 8kHz -> μ-law
            resampled, _ = resample_audio(raw_audio, CAMB_AI_PCM_SAMPLE_RATE, 8000)
            return audioop.lin2ulaw(resampled, 2)
        elif output_format == "wav":
            # WAV has a 44-byte header, skip it to get raw PCM
            pcm_data = raw_audio[44:] if len(raw_audio) > 44 else raw_audio
            resampled, _ = resample_audio(pcm_data, CAMB_AI_PCM_SAMPLE_RATE, 8000)
            return audioop.lin2ulaw(resampled, 2)
        else:
            logger.warning(
                "Unknown CAMB AI output format, passing through raw audio",
                output_format=output_format,
            )
            return raw_audio

    async def _ensure_session(self) -> None:
        if self._session and not self._session.closed:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory()

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge runtime options with defaults."""
        runtime_options = runtime_options or {}

        merged = {
            "api_key": runtime_options.get("api_key",
                self._pipeline_defaults.get("api_key", self._provider_config.api_key)),
            "voice_id": runtime_options.get("voice_id",
                self._pipeline_defaults.get("voice_id", self._provider_config.voice_id)),
            "speech_model": runtime_options.get("speech_model",
                self._pipeline_defaults.get("speech_model", self._provider_config.speech_model)),
            "language": runtime_options.get("language",
                self._pipeline_defaults.get("language", self._provider_config.language)),
            "base_url": runtime_options.get("base_url",
                self._pipeline_defaults.get("base_url", self._provider_config.base_url)),
            "output_format": runtime_options.get("output_format",
                self._pipeline_defaults.get("output_format", self._provider_config.output_format)),
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
