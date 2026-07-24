"""# Milestone7: Google Cloud component adapters for configurable pipelines."""

from __future__ import annotations

import asyncio
import base64
import struct
import json
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Callable, Dict, Iterable, Optional, Sequence, Tuple

import aiohttp

from ..audio import (
    convert_pcm16le_to_target_format,
    mulaw_to_pcm16le,
    resample_audio,
    resolve_output_resampler_policy,
)
from ..config import AppConfig, GoogleProviderConfig
from ..logging_config import get_logger
from .base import LLMComponent, LLMResponse, STTComponent, TTSComponent

logger = get_logger(__name__)

try:  # pragma: no cover - covered indirectly via credential tests
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleAuthRequest
except ImportError:  # pragma: no cover - surfaced via RuntimeError when needed
    service_account = None
    GoogleAuthRequest = None


_GOOGLE_DEFAULT_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_GENERATIVE_SCOPE = "https://www.googleapis.com/auth/generative-language"


def _merge_dicts(base: Optional[Dict[str, Any]], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base or {})
    if override:
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = _merge_dicts(merged[key], value)
            elif value is not None:
                merged[key] = value
    return merged


def _bytes_per_sample(encoding: str) -> int:
    fmt = (encoding or "").lower()
    if fmt in ("ulaw", "mulaw", "mu-law", "g711_ulaw"):
        return 1
    return 2


def _chunk_audio(audio_bytes: bytes, encoding: str, sample_rate: int, chunk_ms: int) -> Iterable[bytes]:
    if not audio_bytes:
        return []
    bytes_per_sample = _bytes_per_sample(encoding)
    frame_size = max(bytes_per_sample, int(sample_rate * (chunk_ms / 1000.0) * bytes_per_sample))
    for idx in range(0, len(audio_bytes), frame_size):
        yield audio_bytes[idx : idx + frame_size]


def _extract_stt_transcript(payload: Dict[str, Any]) -> Optional[str]:
    results = payload.get("results") or []
    for entry in results:
        alternatives = entry.get("alternatives") or []
        for alt in alternatives:
            transcript = alt.get("transcript")
            if transcript:
                return transcript
    return None


def _extract_candidate_text(payload: Dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    for candidate in candidates:
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            text = part.get("text")
            if text:
                return text
    return ""


def _decode_google_audio(audio_content: str) -> bytes:
    try:
        return base64.b64decode(audio_content)
    except (base64.binascii.Error, TypeError):
        logger.error("Failed to decode Google TTS audioContent payload")
        return b""


def _unwrap_google_wav(audio_bytes: bytes) -> Tuple[bytes, Optional[int]]:
    """Return the WAV data chunk and declared rate from Google TTS output.

    Google wraps both LINEAR16 and MULAW results in RIFF/WAV.  AudioSocket
    consumes headerless media, so treating the container bytes as samples
    causes a click/static burst at the start of each utterance.
    """
    if not (
        len(audio_bytes) >= 12
        and audio_bytes[:4] == b"RIFF"
        and audio_bytes[8:12] == b"WAVE"
    ):
        return audio_bytes, None

    offset = 12
    sample_rate: Optional[int] = None
    while offset + 8 <= len(audio_bytes):
        chunk_id = audio_bytes[offset : offset + 4]
        chunk_size = int.from_bytes(audio_bytes[offset + 4 : offset + 8], "little")
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        if payload_end > len(audio_bytes):
            raise RuntimeError("Google TTS returned a truncated WAV container")
        if chunk_id == b"fmt " and chunk_size >= 8:
            sample_rate = struct.unpack_from("<I", audio_bytes, payload_start + 4)[0]
        elif chunk_id == b"data":
            return audio_bytes[payload_start:payload_end], sample_rate
        offset = payload_end + (chunk_size % 2)

    raise RuntimeError("Google TTS WAV response did not contain an audio data chunk")


class _GoogleCredentialManager:
    """# Milestone7: Resolve Google Cloud credentials (API key or service account)."""

    def __init__(self, provider_config: GoogleProviderConfig):
        self._api_key = provider_config.api_key or os.getenv("GOOGLE_API_KEY")
        self._service_account_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        self._token_cache: Dict[Tuple[str, ...], Tuple[str, Optional[datetime]]] = {}
        self._lock = asyncio.Lock()

    async def build_auth(self, scopes: Sequence[str]) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Return (headers, query_params) for the request."""
        if self._api_key:
            return {}, {"key": self._api_key}

        if self._service_account_path:
            token = await self._get_service_account_token(scopes)
            return {"Authorization": f"Bearer {token}"}, {}

        return {}, {}

    async def _get_service_account_token(self, scopes: Sequence[str]) -> str:
        if not self._service_account_path:
            raise RuntimeError("Service account path not configured for Google Cloud requests")
        if service_account is None or GoogleAuthRequest is None:
            raise RuntimeError("google-auth is required for service account authentication")

        scope_key = tuple(sorted(scopes or (_GOOGLE_DEFAULT_SCOPE,)))

        async with self._lock:
            cached_token, expiry = self._token_cache.get(scope_key, (None, None))
            if cached_token and expiry and expiry - timedelta(minutes=5) > datetime.utcnow():
                return cached_token

            loop = asyncio.get_running_loop()
            token, new_expiry = await loop.run_in_executor(None, self._refresh_token_sync, scope_key)
            self._token_cache[scope_key] = (token, new_expiry)
            return token

    def _refresh_token_sync(self, scope_key: Tuple[str, ...]) -> Tuple[str, Optional[datetime]]:
        credentials = service_account.Credentials.from_service_account_file(
            self._service_account_path,
            scopes=list(scope_key) or [_GOOGLE_DEFAULT_SCOPE],
        )
        credentials.refresh(GoogleAuthRequest())
        return credentials.token, credentials.expiry


class GoogleSTTAdapter(STTComponent):
    """# Milestone7: Google Cloud Speech-to-Text adapter for pipeline orchestrator."""

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: GoogleProviderConfig,
        options: Optional[Dict[str, Any]] = None,
        *,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
        auth_scopes: Optional[Sequence[str]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_defaults = provider_config
        self._pipeline_defaults = dict(options or {})
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        self._credential_manager = _GoogleCredentialManager(provider_config)
        self._auth_scopes = tuple(auth_scopes or (_GOOGLE_DEFAULT_SCOPE,))

    async def start(self) -> None:
        logger.debug(
            "Google STT adapter initialized",
            component=self.component_key,
            default_language=self._provider_defaults.stt_language_code,
        )

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        await self._ensure_session()

    async def close_call(self, call_id: str) -> None:
        return

    async def transcribe(
        self,
        call_id: str,
        audio_pcm16: bytes,
        sample_rate_hz: int,
        options: Dict[str, Any],
    ) -> str:
        await self._ensure_session()
        assert self._session is not None

        merged = self._compose_options(options)
        headers, params = await self._credential_manager.build_auth(self._auth_scopes)

        target_rate = int(merged.get("sample_rate_hz") or sample_rate_hz)
        audio_bytes = audio_pcm16
        if target_rate != sample_rate_hz:
            audio_bytes, _ = resample_audio(audio_pcm16, sample_rate_hz, target_rate)

        encoding = (merged.get("encoding") or "LINEAR16").upper()
        if encoding in ("MULAW", "MU-LAW", "ULAW", "G711_ULAW"):
            audio_bytes = convert_pcm16le_to_target_format(audio_bytes, "mulaw")
        elif encoding not in ("LINEAR16", "PCM16"):
            logger.warning(
                "Google STT using unsupported encoding override; sending raw bytes",
                encoding=encoding,
                call_id=call_id,
            )

        config_payload = {
            "encoding": encoding,
            "languageCode": merged["language_code"],
            "sampleRateHertz": target_rate,
        }
        if merged.get("model"):
            config_payload["model"] = merged["model"]

        config_payload = _merge_dicts(config_payload, merged.get("config_overrides"))
        config_payload["sampleRateHertz"] = target_rate  # Ensure overrides can't remove sample rate.

        audio_content = base64.b64encode(audio_bytes).decode("ascii")
        request_payload: Dict[str, Any] = {
            "config": config_payload,
            "audio": {"content": audio_content},
        }
        request_payload = _merge_dicts(request_payload, merged.get("request_overrides"))

        request_id = f"google-stt-{uuid.uuid4().hex[:10]}"
        url = self._provider_defaults.stt_base_url.rstrip("/") + "/speech:recognize"

        started_at = time.perf_counter()
        async with self._session.post(
            url,
            json=request_payload,
            params=params or None,
            headers=headers or None,
            timeout=merged["timeout_sec"],
        ) as response:
            body = await response.text()
            if response.status >= 400:
                logger.error(
                    "Google STT recognition failed",
                    call_id=call_id,
                    request_id=request_id,
                    status=response.status,
                    body_preview=body[:128],
                )
                response.raise_for_status()
            data = json.loads(body)

        transcript = _extract_stt_transcript(data) or ""
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        logger.info(
            "Google STT transcript received",
            call_id=call_id,
            request_id=request_id,
            latency_ms=round(latency_ms, 2),
            transcript_preview=transcript[:80],
        )
        return transcript

    async def _ensure_session(self) -> None:
        if self._session and not self._session.closed:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory()

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged = _merge_dicts(self._pipeline_defaults, runtime_options)
        options = {
            "encoding": merged.get("encoding", "LINEAR16"),
            "language_code": merged.get("language_code", self._provider_defaults.stt_language_code),
            "model": merged.get("model"),
            "sample_rate_hz": merged.get("sample_rate_hz"),
            "timeout_sec": float(merged.get("timeout_sec", 12.0)),
            "config_overrides": dict(merged.get("config_overrides") or merged.get("config") or {}),
            "request_overrides": dict(merged.get("request_overrides") or merged.get("request") or {}),
        }
        return options


class GoogleLLMAdapter(LLMComponent):
    """# Milestone7: Google Generative Language adapter (Gemini/PaLM)."""

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: GoogleProviderConfig,
        options: Optional[Dict[str, Any]] = None,
        *,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
        auth_scopes: Optional[Sequence[str]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_defaults = provider_config
        self._pipeline_defaults = dict(options or {})
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        self._credential_manager = _GoogleCredentialManager(provider_config)
        self._auth_scopes = tuple(auth_scopes or (_GENERATIVE_SCOPE, _GOOGLE_DEFAULT_SCOPE))

    async def start(self) -> None:
        logger.debug(
            "Google LLM adapter initialized",
            component=self.component_key,
            default_model=self._provider_defaults.llm_model,
        )

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def generate(
        self,
        call_id: str,
        transcript: str,
        context: Dict[str, Any],
        options: Dict[str, Any],
    ) -> LLMResponse:
        await self._ensure_session()
        assert self._session is not None

        merged = self._compose_options(options)
        headers, params = await self._credential_manager.build_auth(self._auth_scopes)

        payload = self._build_payload(transcript, context, merged)
        payload = _merge_dicts(payload, merged.get("request_overrides"))

        request_id = f"google-llm-{uuid.uuid4().hex[:10]}"
        model_path = merged["model"]
        if not model_path.startswith("models/"):
            model_path = f"models/{model_path}"
        url = f"{self._provider_defaults.llm_base_url.rstrip('/')}/{model_path}:generateContent"

        async with self._session.post(
            url,
            json=payload,
            params=params or None,
            headers=headers or None,
            timeout=merged["timeout_sec"],
        ) as response:
            body = await response.text()
            if response.status >= 400:
                logger.error(
                    "Google LLM generateContent failed",
                    call_id=call_id,
                    request_id=request_id,
                    status=response.status,
                    body_preview=body[:128],
                )
                response.raise_for_status()
            data = json.loads(body)

        # Log raw response for debugging empty responses
        text = _extract_candidate_text(data)
        if not text:
            logger.warning(
                "Google LLM returned empty response",
                call_id=call_id,
                request_id=request_id,
                response_keys=list(data.keys()),
                candidates_count=len(data.get("candidates", [])),
                raw_response=body[:500] if len(body) <= 500 else body[:500] + "...",
            )
        logger.info(
            "Google LLM response received",
            call_id=call_id,
            request_id=request_id,
            preview=text[:80] if text else "(empty)",
        )
        return LLMResponse(text=text or "")

    async def _ensure_session(self) -> None:
        if self._session and not self._session.closed:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory()

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged = _merge_dicts(self._pipeline_defaults, runtime_options)
        # Determine system instruction precedence: runtime > pipeline > app_config.llm.prompt
        try:
            sys_instr = (merged.get("system_instruction") or merged.get("system_prompt") or "").strip()
        except Exception:
            sys_instr = ""
        if not sys_instr:
            try:
                sys_instr = getattr(self._app_config.llm, "prompt", None) or None
            except Exception:
                sys_instr = None
        options = {
            "model": merged.get("model", self._provider_defaults.llm_model),
            "temperature": merged.get("temperature", 0.7),
            "top_p": merged.get("top_p"),
            "candidate_count": int(merged.get("candidate_count", 1)),
            "max_output_tokens": merged.get("max_output_tokens"),
            "system_instruction": sys_instr,
            "safety_settings": merged.get("safety_settings"),
            "request_overrides": dict(merged.get("request_overrides") or merged.get("request") or {}),
            "timeout_sec": float(merged.get("timeout_sec", 10.0)),
        }
        return options

    def _build_payload(self, transcript: str, context: Dict[str, Any], merged: Dict[str, Any]) -> Dict[str, Any]:
        contents = context.get("google_contents")
        if not contents:
            # Get system instruction to prepend to first message
            system_instruction = merged.get("system_instruction") or context.get("system_prompt")
            contents = self._coalesce_contents(transcript, context, system_instruction)

        generation_config = {
            "temperature": merged["temperature"],
        }
        if merged.get("top_p") is not None:
            generation_config["topP"] = merged["top_p"]
        if merged.get("max_output_tokens") is not None:
            generation_config["maxOutputTokens"] = merged["max_output_tokens"]
        if merged.get("candidate_count") not in (None, 1):
            generation_config["candidateCount"] = merged["candidate_count"]

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }

        if merged.get("safety_settings"):
            payload["safetySettings"] = merged["safety_settings"]

        return payload

    def _coalesce_contents(self, transcript: str, context: Dict[str, Any], system_instruction: Optional[str] = None) -> list[Dict[str, Any]]:
        contents: list[Dict[str, Any]] = []
        prior_messages = context.get("prior_messages") or []

        for msg in prior_messages:
            role = msg.get("role") if isinstance(msg, dict) else None
            text = msg.get("content") if isinstance(msg, dict) else str(msg)
            if role not in ("user", "model", "assistant"):
                continue
            normalized_role = "model" if role in ("assistant", "model") else "user"
            contents.append({"role": normalized_role, "parts": [{"text": text or ""}]})

        # Build the user message, prepending system instruction if provided
        user_text = transcript or ""
        if system_instruction and not prior_messages:
            # Only prepend system instruction to first user message in conversation
            user_text = f"{system_instruction}\n\n{user_text}" if user_text else system_instruction
        
        if user_text or not contents:
            contents.append({"role": "user", "parts": [{"text": user_text}]})

        return contents


class GoogleTTSAdapter(TTSComponent):
    """# Milestone7: Google Cloud Text-to-Speech adapter with μ-law/PCM chunking."""

    wideband_output_format = {
        "encoding": "linear16",
        "sample_rate": 16000,
        "options": {"audio_encoding": "LINEAR16", "audio_sample_rate": 16000},
    }

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: GoogleProviderConfig,
        options: Optional[Dict[str, Any]] = None,
        *,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
        auth_scopes: Optional[Sequence[str]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_defaults = provider_config
        self._pipeline_defaults = dict(options or {})
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        self._credential_manager = _GoogleCredentialManager(provider_config)
        self._auth_scopes = tuple(auth_scopes or (_GOOGLE_DEFAULT_SCOPE,))
        self._default_chunk_ms = int(self._pipeline_defaults.get("chunk_size_ms", 20))

    async def start(self) -> None:
        logger.debug(
            "Google TTS adapter initialized",
            component=self.component_key,
            default_voice=self._provider_defaults.tts_voice_name,
        )

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        await self._ensure_session()

    async def close_call(self, call_id: str) -> None:
        return

    async def synthesize(
        self,
        call_id: str,
        text: str,
        options: Dict[str, Any],
    ) -> AsyncIterator[bytes]:
        if not text:
            return  # Exit early - yields nothing (async generator)
            yield  # Unreachable but makes this an async generator
        await self._ensure_session()
        assert self._session is not None

        merged = self._compose_options(options)
        headers, params = await self._credential_manager.build_auth(self._auth_scopes)
        url = self._provider_defaults.tts_base_url.rstrip("/") + "/text:synthesize"

        payload = {
            "input": {"text": text},
            "voice": {
                "languageCode": merged["language_code"],
                "name": merged["voice_name"],
            },
            "audioConfig": {
                "audioEncoding": merged["audio_encoding"],
                "sampleRateHertz": merged["audio_sample_rate"],
            },
        }

        if merged.get("speaking_rate") is not None:
            payload["audioConfig"]["speakingRate"] = merged["speaking_rate"]
        if merged.get("pitch") is not None:
            payload["audioConfig"]["pitch"] = merged["pitch"]
        if merged.get("volume_gain_db") is not None:
            payload["audioConfig"]["volumeGainDb"] = merged["volume_gain_db"]
        if merged.get("effects_profile_id"):
            payload["audioConfig"]["effectsProfileId"] = merged["effects_profile_id"]

        payload = _merge_dicts(payload, merged.get("request_overrides"))

        request_id = f"google-tts-{uuid.uuid4().hex[:10]}"
        started_at = time.perf_counter()

        async with self._session.post(
            url,
            json=payload,
            params=params or None,
            headers=headers or None,
            timeout=merged["timeout_sec"],
        ) as response:
            body = await response.text()
            if response.status >= 400:
                logger.error(
                    "Google TTS synthesis failed",
                    call_id=call_id,
                    request_id=request_id,
                    status=response.status,
                    body_preview=body[:128],
                )
                response.raise_for_status()
            data = json.loads(body)

        audio_content = data.get("audioContent")
        if not audio_content:
            logger.warning("Google TTS response missing audioContent", call_id=call_id, request_id=request_id)
            return

        raw_audio = _decode_google_audio(audio_content)
        converted = self._convert_audio(
            raw_audio,
            merged["audio_encoding"],
            merged["audio_sample_rate"],
            merged["target_format"]["encoding"],
            merged["target_format"]["sample_rate"],
            merged["output_resampler"],
        )

        latency_ms = (time.perf_counter() - started_at) * 1000.0
        logger.info(
            "Google TTS synthesis completed",
            call_id=call_id,
            request_id=request_id,
            latency_ms=round(latency_ms, 2),
            text_preview=text[:64],
        )

        chunk_value = merged.get("chunk_size_ms")
        chunk_ms = int(chunk_value if chunk_value is not None else self._default_chunk_ms)
        for chunk in _chunk_audio(
            converted,
            merged["target_format"]["encoding"],
            merged["target_format"]["sample_rate"],
            chunk_ms,
        ):
            if chunk:
                yield chunk

    async def _ensure_session(self) -> None:
        if self._session and not self._session.closed:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory()

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged = _merge_dicts(self._pipeline_defaults, runtime_options)
        target_format = merged.get("format") or merged.get("target_format") or {}
        source_format = merged.get("source_format") or {}
        options = {
            "voice_name": merged.get("voice", self._provider_defaults.tts_voice_name),
            "language_code": merged.get("language_code", self._provider_defaults.stt_language_code),
            "audio_encoding": (merged.get("audio_encoding") or source_format.get("encoding") or self._provider_defaults.tts_audio_encoding).upper(),
            "audio_sample_rate": int(
                merged.get("audio_sample_rate")
                or source_format.get("sample_rate")
                or self._provider_defaults.tts_sample_rate_hz
            ),
            "chunk_size_ms": merged.get("chunk_size_ms"),
            "timeout_sec": float(merged.get("timeout_sec", 15.0)),
            "speaking_rate": merged.get("speaking_rate"),
            "pitch": merged.get("pitch"),
            "volume_gain_db": merged.get("volume_gain_db"),
            "effects_profile_id": merged.get("effects_profile_id"),
            "request_overrides": dict(merged.get("request_overrides") or merged.get("request") or {}),
            "target_format": {
                "encoding": (target_format.get("encoding") or "mulaw").lower(),
                "sample_rate": int(target_format.get("sample_rate") or 8000),
            },
            "output_resampler": merged.get(
                "output_resampler", self._provider_defaults.output_resampler
            ),
        }
        options["output_resampler"] = resolve_output_resampler_policy(
            provider_mode=options.get("output_resampler")
        )[0]
        return options

    @staticmethod
    def _convert_audio(
        audio_bytes: bytes,
        source_encoding: str,
        source_rate: int,
        target_encoding: str,
        target_rate: int,
        output_resampler: str = "linear",
    ) -> bytes:
        if not audio_bytes:
            return b""

        audio_bytes, container_rate = _unwrap_google_wav(audio_bytes)
        if container_rate:
            source_rate = int(container_rate)

        fmt = (source_encoding or "").lower()
        if fmt in ("ulaw", "mulaw", "mu-law", "g711_ulaw"):
            pcm_bytes = mulaw_to_pcm16le(audio_bytes)
        else:
            pcm_bytes = audio_bytes

        if source_rate != target_rate:
            pcm_bytes, _ = resample_audio(
                pcm_bytes,
                source_rate,
                target_rate,
                mode=output_resampler,
            )

        return convert_pcm16le_to_target_format(pcm_bytes, target_encoding)


__all__ = [
    "GoogleLLMAdapter",
    "GoogleSTTAdapter",
    "GoogleTTSAdapter",
]
