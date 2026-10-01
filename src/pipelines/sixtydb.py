"""60db REST TTS adapted to mono PCM16/16 kHz or telephony mu-law/8 kHz."""
from __future__ import annotations

import base64
import io
import json
import wave
from typing import Any, AsyncIterator, Callable, Dict, Optional

import aiohttp

from ..audio import convert_pcm16le_to_target_format, resample_audio, resolve_output_resampler_policy
from ..config import AppConfig, SixtyDBProviderConfig
from .base import TTSComponent


MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class SixtyDBTTSAdapter(TTSComponent):
    wideband_output_format = {"encoding": "linear16", "sample_rate": 16000, "options": {}}

    def __init__(
        self, component_key: str, app_config: AppConfig,
        provider_config: SixtyDBProviderConfig, options: Optional[Dict[str, Any]] = None,
        *, session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
    ):
        self.component_key = component_key
        self._provider_config = provider_config
        self._pipeline_defaults = options or {}
        self._session_factory = session_factory or aiohttp.ClientSession
        self._session: Optional[aiohttp.ClientSession] = None

    async def stop(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        await self._ensure_session()

    async def _ensure_session(self) -> None:
        if self._session is None or self._session.closed:
            self._session = self._session_factory()

    def _compose_options(self, options: Dict[str, Any]) -> Dict[str, Any]:
        merged = {**self._provider_config.model_dump(), **self._pipeline_defaults, **(options or {})}
        validated = SixtyDBProviderConfig(**merged)
        if not isinstance(validated.api_key, str) or not validated.api_key.strip():
            raise RuntimeError("60db TTS requires SIXTYDB_API_KEY")
        if not isinstance(validated.voice_id, str) or not validated.voice_id.strip():
            raise RuntimeError("60db TTS requires an explicit workspace voice_id")
        merged.update(validated.model_dump())
        target = merged.get("format") or merged.get("target_format") or {}
        merged["format"] = {
            "encoding": str(target.get("encoding", "mulaw")).lower(),
            "sample_rate": int(target.get("sample_rate", 8000)),
        }
        if merged["format"]["encoding"] not in {"mulaw", "ulaw", "mu-law", "linear16", "pcm16", "slin16"}:
            raise RuntimeError("60db TTS target encoding must be PCM16 or mu-law")
        if merged["format"]["sample_rate"] not in {8000, 16000}:
            raise RuntimeError("60db TTS target sample rate must be 8000 or 16000 Hz")
        return merged

    async def validate_connectivity(self, options: Dict[str, Any]) -> Dict[str, Any]:
        try:
            merged = self._compose_options(options)
        except (ValueError, RuntimeError):
            return {"healthy": False, "error": "Invalid 60db key, workspace voice, or audio configuration", "details": {}}
        merged["base_url"] = "https://api.60db.ai"
        return await super().validate_connectivity(merged)

    @staticmethod
    def _validate_metadata(record: Dict[str, Any]) -> None:
        if not isinstance(record, dict):
            raise RuntimeError("60db returned an invalid response object")
        if record.get("success") is False or record.get("type") == "error" or record.get("error"):
            raise RuntimeError("60db reported a synthesis failure")
        for field in ("encoding", "audio_encoding", "output_format"):
            if record.get(field) is not None and str(record[field]).lower() not in {"linear16", "pcm", "pcm16", "wav"}:
                raise RuntimeError("60db returned incompatible audio encoding")
        for field, expected in (("sample_rate", 16000), ("sample_rate_hertz", 16000), ("channels", 1), ("bit_depth", 16)):
            if field in record and record[field] != expected:
                raise RuntimeError("60db returned incompatible audio metadata")
        if "audio_config" in record:
            SixtyDBTTSAdapter._validate_metadata(record["audio_config"])

    @staticmethod
    def _decode_record(record: Dict[str, Any], depth: int = 0) -> bytes:
        if depth > 4:
            raise RuntimeError("60db audio envelope is too deeply nested")
        SixtyDBTTSAdapter._validate_metadata(record)
        result = record.get("result", record.get("backendResponse", record))
        SixtyDBTTSAdapter._validate_metadata(result)
        value = result.get("audioContent", result.get("audio_base64"))
        if value is None:
            return b""
        if not isinstance(value, str):
            raise RuntimeError("60db audio must be base64 text")
        audio = base64.b64decode(value, validate=True)
        if audio.startswith(b"{"):
            try:
                inner = json.loads(audio)
            except (ValueError, UnicodeDecodeError):
                return audio
            audio = SixtyDBTTSAdapter._decode_record(inner, depth + 1)
            if not audio:
                raise RuntimeError("60db encoded wrapper contains no audio")
        return audio

    @staticmethod
    def _pcm(audio: bytes) -> bytes:
        if audio.startswith(b"RIFF"):
            with wave.open(io.BytesIO(audio), "rb") as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, "NONE"):
                    raise RuntimeError("60db WAV must be mono PCM16 at 16000 Hz")
                frames = wav.getnframes()
                audio = wav.readframes(frames)
                if len(audio) != frames * 2:
                    raise RuntimeError("60db returned a truncated WAV")
        elif audio.startswith((b"ID3", b"OggS", b"fLaC")):
            raise RuntimeError("60db returned compressed audio instead of LINEAR16")
        return audio

    async def _audio_chunks(self, response: aiohttp.ClientResponse) -> AsyncIterator[bytes]:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        self._validate_metadata({
            field: int(response.headers[header])
            for field, header in (("sample_rate", "X-Sample-Rate"), ("channels", "X-Channels"), ("bit_depth", "X-Bit-Depth"))
            if header in response.headers
        })
        if content_type in {"application/x-ndjson", "application/ndjson", "text/plain"}:
            total = 0
            async for line in response.content:
                total += len(line)
                if total > MAX_RESPONSE_BYTES:
                    raise RuntimeError("60db response exceeds 32 MiB")
                if line.strip():
                    yield self._pcm(self._decode_record(json.loads(line)))
        elif content_type == "application/json":
            yield self._pcm(self._decode_record(json.loads(await self._read_bounded(response))))
        elif content_type in {"audio/wav", "audio/x-wav", "audio/pcm", "audio/octet-stream", "application/octet-stream"}:
            # WAV containers must be complete before stdlib wave can validate
            # their metadata and strip the header. NDJSON raw PCM stays streamed.
            yield self._pcm(await self._read_bounded(response))
        else:
            raise RuntimeError("60db returned an unsupported response content type")

    @staticmethod
    async def _read_bounded(response: aiohttp.ClientResponse) -> bytes:
        data = bytearray()
        async for part in response.content.iter_chunked(65536):
            data.extend(part)
            if len(data) > MAX_RESPONSE_BYTES:
                raise RuntimeError("60db response exceeds 32 MiB")
        return bytes(data)

    async def synthesize(self, call_id: str, text: str, options: Dict[str, Any]) -> AsyncIterator[bytes]:
        if not isinstance(text, str) or not text.strip() or len(text) > 5000:
            raise RuntimeError("60db text must contain 1 to 5000 characters")
        merged = self._compose_options(options)
        await self._ensure_session()
        payload = {
            "text": text, "voice_id": merged["voice_id"], "speed": merged["speed"],
            "audio_config": {"audio_encoding": "LINEAR16", "sample_rate_hertz": 16000},
            "output_format": "wav", "timestamp_type": "NONE",
        }
        if merged.get("model_id"):
            payload["model_id"] = merged["model_id"]
        target = merged["format"]
        mode = resolve_output_resampler_policy(provider_mode=merged["output_resampler"])[0]
        timeout = aiohttp.ClientTimeout(
            total=None, connect=merged["connect_timeout_sec"],
            sock_connect=merged["connect_timeout_sec"], sock_read=merged["read_timeout_sec"],
        )
        pending = bytearray()
        resample_state = None
        produced = False
        async with self._session.post(
            "https://api.60db.ai/tts-synthesize", json=payload,
            headers={"Authorization": "Bearer " + merged["api_key"], "Content-Type": "application/json"},
            timeout=timeout, allow_redirects=False,
        ) as response:
            if response.status >= 300:
                raise RuntimeError("60db TTS request failed (HTTP %d)" % response.status)
            async for audio in self._audio_chunks(response):
                pending.extend(audio)
                # Fixed 20 ms PCM windows preserve 2:1 resampling alignment
                # independently of provider chunk boundaries (including odd bytes).
                while len(pending) >= 640:
                    pcm = bytes(pending[:640])
                    del pending[:640]
                    pcm, resample_state = resample_audio(pcm, 16000, target["sample_rate"], mode=mode, state=resample_state)
                    converted = convert_pcm16le_to_target_format(pcm, target["encoding"])
                    if converted:
                        produced = True
                        yield converted
            if len(pending) % 2:
                raise RuntimeError("60db returned an incomplete PCM16 sample")
            if pending:
                pcm, _ = resample_audio(bytes(pending), 16000, target["sample_rate"], mode=mode, state=resample_state)
                converted = convert_pcm16le_to_target_format(pcm, target["encoding"])
                if converted:
                    produced = True
                    yield converted
            if not produced:
                raise RuntimeError("60db TTS returned no audio")
