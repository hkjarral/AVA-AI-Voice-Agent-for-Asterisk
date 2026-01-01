"""
# Milestone7: OpenAI cloud component adapters for configurable pipelines.

This module provides concrete STT, LLM, and TTS adapters that integrate with
OpenAI's Realtime WebSocket API, Chat Completions REST API, and audio.speech
endpoint. The adapters mirror the contract defined in `base.py` so that
`PipelineOrchestrator` can wire them into call flows alongside other providers.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
import wave
from io import BytesIO
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Iterable, Optional

import aiohttp
import websockets

from ..audio import convert_pcm16le_to_target_format, resample_audio
from ..config import AppConfig, OpenAIProviderConfig
from ..logging_config import get_logger
from .base import LLMComponent, STTComponent, TTSComponent, LLMResponse
from ..tools.registry import tool_registry

logger = get_logger(__name__)


# Shared helpers -----------------------------------------------------------------


def _merge_dicts(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(base or {})
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
        return
    bytes_per_sample = _bytes_per_sample(encoding)
    frame_size = max(bytes_per_sample, int(sample_rate * (chunk_ms / 1000.0) * bytes_per_sample))
    for idx in range(0, len(audio_bytes), frame_size):
        yield audio_bytes[idx : idx + frame_size]


def _make_ws_headers(options: Dict[str, Any]) -> Iterable[tuple[str, str]]:
    headers = [
        ("Authorization", f"Bearer {options['api_key']}"),
        ("OpenAI-Beta", "realtime=v1"),
        ("User-Agent", "Asterisk-AI-Voice-Agent/1.0"),
    ]
    if options.get("organization"):
        headers.append(("OpenAI-Organization", options["organization"]))
    if options.get("project"):
        headers.append(("OpenAI-Project", options["project"]))
    return headers


def _make_http_headers(options: Dict[str, Any]) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {options['api_key']}",
        "Content-Type": "application/json",
        "User-Agent": "Asterisk-AI-Voice-Agent/1.0",
    }
    if options.get("organization"):
        headers["OpenAI-Organization"] = options["organization"]
    if options.get("project"):
        headers["OpenAI-Project"] = options["project"]
    return headers


def _decode_audio_payload(raw_bytes: bytes) -> bytes:
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw_bytes

    audio_b64 = payload.get("data") or payload.get("audio")
    if not audio_b64:
        return raw_bytes
    try:
        return base64.b64decode(audio_b64)
    except (base64.binascii.Error, TypeError):
        logger.warning("Failed to base64 decode OpenAI audio payload")
        return raw_bytes


# OpenAI Speech-to-Text Adapter --------------------------------------------------


class OpenAISTTAdapter(STTComponent):
    """OpenAI Speech-to-Text adapter using /v1/audio/transcriptions (Whisper-style REST)."""

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: OpenAIProviderConfig,
        options: Optional[Dict[str, Any]] = None,
        *,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_defaults = provider_config
        self._pipeline_defaults = options or {}
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        self._default_timeout = float(self._pipeline_defaults.get("request_timeout_sec", provider_config.response_timeout_sec))

    async def start(self) -> None:
        logger.debug(
            "OpenAI STT adapter initialized",
            component=self.component_key,
            default_model=getattr(self._provider_defaults, "stt_model", "whisper-1"),
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
        if not audio_pcm16:
            return ""

        await self._ensure_session()
        assert self._session

        merged = self._compose_options(options)
        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("OpenAI STT requires an API key")

        wav_bytes = _pcm16le_to_wav(audio_pcm16, sample_rate_hz)
        form = aiohttp.FormData()
        form.add_field("file", wav_bytes, filename="audio.wav", content_type="audio/wav")
        form.add_field("model", str(merged["model"]))
        if merged.get("language"):
            form.add_field("language", str(merged["language"]))
        if merged.get("prompt"):
            form.add_field("prompt", str(merged["prompt"]))
        if merged.get("temperature") is not None:
            form.add_field("temperature", str(merged["temperature"]))
        if merged.get("response_format"):
            form.add_field("response_format", str(merged["response_format"]))
        timestamp_granularities = merged.get("timestamp_granularities")
        if timestamp_granularities:
            for val in list(timestamp_granularities):
                form.add_field("timestamp_granularities[]", str(val))

        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Asterisk-AI-Voice-Agent/1.0",
        }

        url = merged["stt_base_url"]
        timeout_sec = float(merged.get("request_timeout_sec", self._default_timeout))
        request_id = f"openai-stt-{uuid.uuid4().hex[:12]}"

        started_at = time.perf_counter()
        async with self._session.post(
            url,
            data=form,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout_sec),
        ) as resp:
            body = await resp.read()
            if resp.status >= 400:
                body_preview = body.decode("utf-8", errors="ignore")[:200]
                logger.error(
                    "OpenAI STT request failed",
                    call_id=call_id,
                    request_id=request_id,
                    status=resp.status,
                    body_preview=body_preview,
                )
                resp.raise_for_status()

        latency_ms = (time.perf_counter() - started_at) * 1000.0
        transcript = self._parse_transcript(body, response_format=merged.get("response_format") or "json")
        logger.info(
            "OpenAI STT transcript received",
            call_id=call_id,
            request_id=request_id,
            latency_ms=round(latency_ms, 2),
            transcript_preview=(transcript or "")[:80],
        )
        return transcript or ""

    async def _ensure_session(self) -> None:
        if self._session and not self._session.closed:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory()

    @staticmethod
    def _parse_transcript(payload: bytes, *, response_format: str) -> str:
        fmt = (response_format or "json").lower()
        if fmt == "text":
            return payload.decode("utf-8", errors="ignore").strip()

        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            return payload.decode("utf-8", errors="ignore").strip()

        text = data.get("text")
        if isinstance(text, str):
            return text.strip()
        return ""

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        runtime_options = runtime_options or {}
        model = runtime_options.get(
            "model",
            runtime_options.get(
                "stt_model",
                self._pipeline_defaults.get("model", self._pipeline_defaults.get("stt_model", getattr(self._provider_defaults, "stt_model", "whisper-1"))),
            ),
        )

        return {
            "api_key": runtime_options.get("api_key", self._pipeline_defaults.get("api_key", self._provider_defaults.api_key)),
            "stt_base_url": runtime_options.get(
                "stt_base_url",
                runtime_options.get("base_url", self._pipeline_defaults.get("stt_base_url", getattr(self._provider_defaults, "stt_base_url", "https://api.openai.com/v1/audio/transcriptions"))),
            ),
            "model": model,
            "language": runtime_options.get("language", self._pipeline_defaults.get("language", None)),
            "prompt": runtime_options.get("prompt", self._pipeline_defaults.get("prompt", None)),
            "response_format": runtime_options.get("response_format", self._pipeline_defaults.get("response_format", "json")),
            "temperature": runtime_options.get("temperature", self._pipeline_defaults.get("temperature")),
            "timestamp_granularities": runtime_options.get("timestamp_granularities", self._pipeline_defaults.get("timestamp_granularities")),
            "request_timeout_sec": float(runtime_options.get("request_timeout_sec", self._pipeline_defaults.get("request_timeout_sec", self._default_timeout))),
        }


def _pcm16le_to_wav(audio_pcm16: bytes, sample_rate_hz: int) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate_hz))
        wf.writeframes(audio_pcm16)
    return buf.getvalue()


# Milestone7: OpenAI Chat/Reatime LLM Adapter ------------------------------------


class OpenAILLMAdapter(LLMComponent):
    """# Milestone7: OpenAI LLM adapter supporting Chat Completions and Realtime."""

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: OpenAIProviderConfig,
        options: Optional[Dict[str, Any]] = None,
        *,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_defaults = provider_config
        self._pipeline_defaults = options or {}
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        self._default_timeout = float(self._pipeline_defaults.get("response_timeout_sec", provider_config.response_timeout_sec))

    async def start(self) -> None:
        logger.debug(
            "OpenAI LLM adapter initialized",
            component=self.component_key,
            default_model=self._provider_defaults.chat_model,
        )

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def validate_connectivity(self, options: Dict[str, Any]) -> Dict[str, Any]:
        """Override to merge provider defaults with options for validation."""
        merged = {
            "chat_base_url": self._provider_defaults.chat_base_url,
            "api_key": self._provider_defaults.api_key,
        }
        merged.update(options)
        return await super().validate_connectivity(merged)

    async def generate(
        self,
        call_id: str,
        transcript: str,
        context: Dict[str, Any],
        options: Dict[str, Any],
    ) -> str | LLMResponse:
        merged = self._compose_options(options)
        if not merged["api_key"]:
            raise RuntimeError("OpenAI LLM requires an API key")

        use_realtime = bool(merged.get("use_realtime"))
        if use_realtime:
            return await self._generate_realtime(call_id, transcript, context, merged)

        await self._ensure_session()
        assert self._session
        payload = self._build_chat_payload(transcript, context, merged)
        
        # Milestone7: Tool support
        tools_enabled = bool(merged.get("tools_enabled", True))
        tools_list = merged.get("tools")
        if tools_enabled and tools_list and isinstance(tools_list, list):
            tool_schemas = []
            for tool_name in tools_list:
                tool = tool_registry.get(tool_name)
                if tool:
                    tool_schemas.append(tool.definition.to_openai_schema())
                else:
                    logger.warning("Tool not found in registry", tool=tool_name)
            
            if tool_schemas:
                payload["tools"] = tool_schemas
                payload["tool_choice"] = "auto"

        headers = _make_http_headers(merged)
        url = merged["chat_base_url"].rstrip("/") + "/chat/completions"

        logger.debug(
            "OpenAI chat completion request",
            call_id=call_id,
            model=payload.get("model"),
            temperature=payload.get("temperature"),
            tools_count=len(payload.get("tools", [])),
        )

        retries = 1
        for attempt in range(retries + 1):
            try:
                async with self._session.post(url, json=payload, headers=headers, timeout=merged["timeout_sec"]) as response:
                    body = await response.text()
                    if response.status >= 400:
                        logger.error(
                            "OpenAI chat completion failed",
                            call_id=call_id,
                            status=response.status,
                            body_preview=body[:128],
                        )
                        response.raise_for_status()

                    data = json.loads(body)
                    choices = data.get("choices") or []
                    if not choices:
                        logger.warning("OpenAI chat completion returned no choices", call_id=call_id)
                        return ""

                    message = choices[0].get("message") or {}
                    content = message.get("content", "")
                    tool_calls = message.get("tool_calls") or []
                    
                    # Log response
                    log_ctx = {
                        "call_id": call_id,
                        "model": payload.get("model"),
                        "preview": (content or "")[:80],
                    }
                    if tool_calls:
                        log_ctx["tool_calls"] = len(tool_calls)
                        # Parse tool calls into our standard dict format
                        parsed_tool_calls = []
                        for tc in tool_calls:
                            try:
                                func = tc.get("function", {})
                                name = func.get("name")
                                args = func.get("arguments", "{}")
                                parsed_tool_calls.append({
                                    "id": tc.get("id"),
                                    "name": name,
                                    "parameters": json.loads(args),
                                    "type": tc.get("type", "function")
                                })
                            except Exception as e:
                                logger.warning("Failed to parse tool call", error=str(e))
                        
                        logger.info("OpenAI chat completion received with tools", **log_ctx)
                        return LLMResponse(
                            text=content or "",
                            tool_calls=parsed_tool_calls,
                            metadata=data.get("usage", {})
                        )
                    
                    logger.info("OpenAI chat completion received", **log_ctx)
                    return LLMResponse(text=content or "", tool_calls=[], metadata=data.get("usage", {}))
            except aiohttp.ClientError as e:
                if self._session is None or self._session.closed:
                    logger.info("OpenAI LLM generation cancelled (session closed)", call_id=call_id)
                    return ""

                if attempt == retries:
                    logger.error("OpenAI LLM connection error", call_id=call_id, error=str(e))
                    raise

                logger.warning("OpenAI LLM connection error, retrying", call_id=call_id, error=str(e))
    
    async def _generate_realtime(
        self,
        call_id: str,
        transcript: str,
        context: Dict[str, Any],
        merged: Dict[str, Any],
    ) -> str:
        headers = list(_make_ws_headers(merged))
        websocket = await websockets.connect(
            merged["realtime_base_url"],
            additional_headers=headers,
            max_size=8 * 1024 * 1024,
        )

        session_payload = {
            "type": "session.create",
            "session": {
                "model": merged["realtime_model"],
                "modalities": merged.get("modalities"),
                "instructions": merged.get("system_prompt") or context.get("system_prompt"),
            },
        }
        await websocket.send(json.dumps(session_payload))

        messages = self._coalesce_messages(transcript, context, merged)
        request_payload = {
            "type": "response.create",
            "response": {
                "modalities": ["text"],
                "instructions": merged.get("instructions"),
                "metadata": {"component": self.component_key, "call_id": call_id},
                "conversation": {"messages": messages},
            },
        }
        await websocket.send(json.dumps(request_payload))

        buffer: list[str] = []
        try:
            while True:
                message = await asyncio.wait_for(websocket.recv(), timeout=merged["timeout_sec"])
                if isinstance(message, bytes):
                    continue
                payload = json.loads(message)
                event_type = payload.get("type")
                if event_type == "response.output_text.delta":
                    buffer.append(payload.get("delta") or "")
                elif event_type in ("response.output_text.done", "response.completed"):
                    response_text = "".join(buffer).strip()
                    logger.info("OpenAI realtime LLM response", call_id=call_id, preview=response_text[:80])
                    return response_text
                elif event_type == "response.error":
                    logger.error("OpenAI realtime LLM error", call_id=call_id, error=payload.get("error"))
                    break
        finally:
            await websocket.close()
        return ""

    async def _ensure_session(self) -> None:
        if self._session and not self._session.closed:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory()

    def _compose_options(self, runtime_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        runtime_options = runtime_options or {}
        merged = {
            "api_key": runtime_options.get("api_key", self._pipeline_defaults.get("api_key", self._provider_defaults.api_key)),
            "organization": runtime_options.get("organization", self._pipeline_defaults.get("organization", self._provider_defaults.organization)),
            "project": runtime_options.get("project", self._pipeline_defaults.get("project", self._provider_defaults.project)),
            "tools_enabled": runtime_options.get(
                "tools_enabled",
                self._pipeline_defaults.get("tools_enabled", self._provider_defaults.tools_enabled),
            ),
            "chat_base_url": runtime_options.get(
                "chat_base_url",
                self._pipeline_defaults.get("chat_base_url", self._provider_defaults.chat_base_url),
            ),
            "realtime_base_url": runtime_options.get(
                "realtime_base_url",
                self._pipeline_defaults.get("realtime_base_url", self._provider_defaults.realtime_base_url),
            ),
            "chat_model": runtime_options.get(
                "chat_model",
                self._pipeline_defaults.get("chat_model", self._provider_defaults.chat_model),
            ),
            "realtime_model": runtime_options.get(
                "realtime_model",
                self._pipeline_defaults.get("realtime_model", self._provider_defaults.realtime_model),
            ),
            "modalities": runtime_options.get(
                "modalities",
                self._pipeline_defaults.get("modalities", self._provider_defaults.default_modalities or ["text"]),
            ),
            "system_prompt": runtime_options.get("system_prompt", self._pipeline_defaults.get("system_prompt")),
            "instructions": runtime_options.get("instructions", self._pipeline_defaults.get("instructions")),
            "temperature": runtime_options.get("temperature", self._pipeline_defaults.get("temperature", 0.7)),
            "max_tokens": runtime_options.get("max_tokens", self._pipeline_defaults.get("max_tokens")),
            "timeout_sec": float(runtime_options.get("timeout_sec", self._pipeline_defaults.get("timeout_sec", self._default_timeout))),
            "use_realtime": runtime_options.get("use_realtime", self._pipeline_defaults.get("use_realtime", False)),
            "tools": runtime_options.get("tools", self._pipeline_defaults.get("tools", [])),
        }

        # Fallback persona when missing
        try:
            sys_p = (merged.get("system_prompt") or "").strip()
        except Exception:
            sys_p = ""
        if not sys_p:
            try:
                merged["system_prompt"] = getattr(self._app_config.llm, "prompt", None)
            except Exception:
                merged["system_prompt"] = None
        try:
            instr = (merged.get("instructions") or "").strip()
        except Exception:
            instr = ""
        if not instr:
            try:
                merged["instructions"] = getattr(self._app_config.llm, "prompt", None)
            except Exception:
                merged["instructions"] = None
        return merged

    def _build_chat_payload(self, transcript: str, context: Dict[str, Any], merged: Dict[str, Any]) -> Dict[str, Any]:
        messages = self._coalesce_messages(transcript, context, merged)
        payload: Dict[str, Any] = {
            "model": merged["chat_model"],
            "messages": messages,
        }
        if merged.get("temperature") is not None:
            payload["temperature"] = merged["temperature"]
        if merged.get("max_tokens") is not None:
            payload["max_tokens"] = merged["max_tokens"]
        return payload

    def _coalesce_messages(self, transcript: str, context: Dict[str, Any], merged: Dict[str, Any]) -> list[Dict[str, str]]:
        messages = context.get("messages")
        if messages:
            return messages

        conversation = []
        system_prompt = merged.get("system_prompt") or context.get("system_prompt")
        if system_prompt:
            conversation.append({"role": "system", "content": system_prompt})

        prior = context.get("prior_messages") or []
        conversation.extend(prior)

        if transcript:
            conversation.append({"role": "user", "content": transcript})
        return conversation


# Milestone7: OpenAI audio.speech TTS Adapter ------------------------------------


class OpenAITTSAdapter(TTSComponent):
    """# Milestone7: OpenAI TTS adapter calling the audio.speech REST API."""

    def __init__(
        self,
        component_key: str,
        app_config: AppConfig,
        provider_config: OpenAIProviderConfig,
        options: Optional[Dict[str, Any]] = None,
        *,
        session_factory: Optional[Callable[[], aiohttp.ClientSession]] = None,
    ):
        self.component_key = component_key
        self._app_config = app_config
        self._provider_defaults = provider_config
        self._pipeline_defaults = options or {}
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        self._chunk_size_ms = int(self._pipeline_defaults.get("chunk_size_ms", provider_config.chunk_size_ms))

    async def start(self) -> None:
        logger.debug(
            "OpenAI TTS adapter initialized",
            component=self.component_key,
            default_model=self._provider_defaults.tts_model,
        )

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def open_call(self, call_id: str, options: Dict[str, Any]) -> None:
        await self._ensure_session()

    async def close_call(self, call_id: str) -> None:
        return

    async def validate_connectivity(self, options: Dict[str, Any]) -> Dict[str, Any]:
        # The base validator expects URLs/credentials in the options dict. For OpenAI modular providers
        # those values typically live in provider defaults, so we validate using composed options.
        merged = self._compose_options(options or {})
        return await super().validate_connectivity(merged)

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
        assert self._session

        merged = self._compose_options(options)
        api_key = merged.get("api_key")
        if not api_key:
            raise RuntimeError("OpenAI TTS requires an API key")

        # OpenAI audio.speech voice must be one of a known enum. When pipelines are edited and the
        # TTS provider is swapped, legacy pipeline-level options may still include a voice from
        # a different provider (e.g., Groq "hannah"). Fall back to a safe OpenAI voice to avoid
        # silent-call failures (greeting synthesis).
        allowed_voices = {
            "alloy",
            "ash",
            "coral",
            "echo",
            "fable",
            "nova",
            "onyx",
            "sage",
            "shimmer",
        }
        voice = (merged.get("voice") or "").strip().lower()
        if voice not in allowed_voices:
            fallback = (getattr(self._provider_defaults, "voice", None) or "alloy").strip().lower()
            if fallback not in allowed_voices:
                fallback = "alloy"
            logger.warning(
                "OpenAI TTS voice invalid; falling back to supported voice",
                call_id=call_id,
                requested_voice=merged.get("voice"),
                fallback_voice=fallback,
            )
            merged["voice"] = fallback

        headers = _make_http_headers(merged)
        url = merged["tts_base_url"]

        payload = {
            "model": merged["tts_model"],
            "input": text,
            "voice": merged["voice"],
            "format": merged["response_format"],
        }

        logger.info(
            "OpenAI TTS synthesis started",
            call_id=call_id,
            model=payload["model"],
            voice=payload["voice"],
            text_preview=text[:64],
        )

        async def _post_tts(req_payload: Dict[str, Any]) -> tuple[int, bytes, str]:
            async with self._session.post(url, json=req_payload, headers=headers, timeout=merged["timeout_sec"]) as resp:
                raw = await resp.read()
                body_text = raw.decode("utf-8", errors="ignore")
                return resp.status, raw, body_text

        status, data, body = await _post_tts(payload)
        if status >= 400:
            body_lower = (body or "").lower()
            # Some OpenAI accounts do not have access to all TTS models. If we hit an invalid model error,
            # retry with the broadly-available `tts-1` to avoid silent-call failures (e.g., greeting).
            if status == 400 and "invalid model" in body_lower and payload.get("model") != "tts-1":
                logger.warning(
                    "OpenAI TTS model rejected; retrying with tts-1",
                    call_id=call_id,
                    requested_model=payload.get("model"),
                    status=status,
                    body_preview=body[:128],
                )
                retry_payload = {**payload, "model": "tts-1"}
                status, data, body = await _post_tts(retry_payload)

            if status >= 400:
                logger.error(
                    "OpenAI TTS synthesis failed",
                    call_id=call_id,
                    status=status,
                    body_preview=(body or "")[:128],
                )
                raise RuntimeError(
                    f"OpenAI TTS request failed (status {status}): {(body or '')[:256]}"
                )

        audio_bytes = _decode_audio_payload(data)
        pcm_bytes, source_rate = self._decode_to_pcm16le(audio_bytes, merged)
        converted = self._convert_pcm(
            pcm_bytes,
            source_rate,
            merged["target_format"]["encoding"],
            merged["target_format"]["sample_rate"],
        )

        logger.info(
            "OpenAI TTS synthesis completed",
            call_id=call_id,
            output_bytes=len(converted),
            target_encoding=merged["target_format"]["encoding"],
            target_sample_rate=merged["target_format"]["sample_rate"],
        )

        chunk_ms = int(merged.get("chunk_size_ms", self._chunk_size_ms))
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
        runtime_options = runtime_options or {}
        # Backward compatible: if older configs provide "source_format", allow it to override response_format
        # (only meaningful for "wav" or "pcm").
        source_defaults = self._pipeline_defaults.get("source_format", {})
        merged_source = {
            "encoding": runtime_options.get("source_format", {}).get("encoding", source_defaults.get("encoding")),
            "sample_rate": runtime_options.get("source_format", {}).get("sample_rate", source_defaults.get("sample_rate")),
        }

        format_defaults = self._pipeline_defaults.get("format", {})
        merged_target = {
            "encoding": runtime_options.get("format", {}).get(
                "encoding",
                format_defaults.get("encoding", self._provider_defaults.target_encoding),
            ),
            "sample_rate": int(
                runtime_options.get("format", {}).get(
                    "sample_rate",
                    format_defaults.get("sample_rate", self._provider_defaults.target_sample_rate_hz),
                )
            ),
        }

        merged = {
            "api_key": runtime_options.get("api_key", self._pipeline_defaults.get("api_key", self._provider_defaults.api_key)),
            "organization": runtime_options.get("organization", self._pipeline_defaults.get("organization", self._provider_defaults.organization)),
            "project": runtime_options.get("project", self._pipeline_defaults.get("project", self._provider_defaults.project)),
            "tts_base_url": runtime_options.get(
                "tts_base_url",
                runtime_options.get("base_url", self._pipeline_defaults.get("tts_base_url", self._pipeline_defaults.get("base_url", self._provider_defaults.tts_base_url))),
            ),
            "tts_model": runtime_options.get(
                "model",
                self._pipeline_defaults.get("model", self._provider_defaults.tts_model),
            ),
            "voice": runtime_options.get("voice", self._pipeline_defaults.get("voice", self._provider_defaults.voice)),
            "chunk_size_ms": runtime_options.get("chunk_size_ms", self._pipeline_defaults.get("chunk_size_ms", self._provider_defaults.chunk_size_ms)),
            "timeout_sec": float(runtime_options.get("timeout_sec", self._pipeline_defaults.get("timeout_sec", self._provider_defaults.response_timeout_sec))),
            # OpenAI speech supports multiple formats; we only decode "wav" and "pcm" in the engine.
            "response_format": runtime_options.get(
                "response_format",
                self._pipeline_defaults.get(
                    "response_format",
                    merged_source.get("encoding") or getattr(self._provider_defaults, "tts_response_format", "wav"),
                ),
            ),
            "source_format": merged_source,
            "target_format": merged_target,
        }
        return merged

    @staticmethod
    def _decode_to_pcm16le(audio_bytes: bytes, merged: Dict[str, Any]) -> tuple[bytes, int]:
        if not audio_bytes:
            return b"", int(merged["target_format"]["sample_rate"])

        fmt = (merged.get("response_format") or "wav").lower()
        if fmt == "wav" or (audio_bytes[:4] == b"RIFF" and b"WAVE" in audio_bytes[:32]):
            try:
                with wave.open(BytesIO(audio_bytes), "rb") as wf:
                    if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                        raise ValueError("Only mono PCM16 WAV is supported")
                    frames = wf.readframes(wf.getnframes())
                    return frames, int(wf.getframerate())
            except Exception as exc:
                raise RuntimeError(f"Failed to decode OpenAI WAV payload: {exc}") from exc

        if fmt == "pcm":
            sample_rate = (
                merged.get("source_format", {}).get("sample_rate")
                or merged.get("source_sample_rate_hz")
                or merged.get("pcm_sample_rate_hz")
            )
            if not sample_rate:
                sample_rate = 24000
            return audio_bytes, int(sample_rate)

        raise RuntimeError(f"OpenAI TTS response_format '{fmt}' is not supported (use 'wav' or 'pcm').")

    @staticmethod
    def _convert_pcm(
        pcm_bytes: bytes,
        source_rate: int,
        target_encoding: str,
        target_rate: int,
    ) -> bytes:
        if not pcm_bytes:
            return b""
        if int(source_rate) != int(target_rate):
            pcm_bytes, _ = resample_audio(pcm_bytes, int(source_rate), int(target_rate))
        return convert_pcm16le_to_target_format(pcm_bytes, target_encoding)


__all__ = [
    "OpenAISTTAdapter",
    "OpenAILLMAdapter",
    "OpenAITTSAdapter",
]
