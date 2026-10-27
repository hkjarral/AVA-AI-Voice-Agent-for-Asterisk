"""OpenAI-compatible speech-to-text facade for the local Ava runtime."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import secrets
import subprocess
from typing import Annotated, Any

import websockets
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer

from settings import get_setting


router = APIRouter(prefix="/v1", tags=["speech"])

_basic = HTTPBasic(auto_error=False)
_bearer = HTTPBearer(auto_error=False)
_MAX_INPUT_BYTES = 25 * 1024 * 1024
_MAX_PCM_BYTES = 128 * 1024 * 1024
_SUPPORTED_RESPONSE_FORMATS = {"json", "text", "verbose_json", "srt", "vtt"}


def _configured_secret(name: str) -> str:
    return (get_setting(name, os.getenv(name, "")) or "").strip()


async def require_speech_auth(
    basic: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    username = _configured_secret("AAVA_SPEECH_API_USERNAME")
    password = _configured_secret("AAVA_SPEECH_API_PASSWORD")
    api_key = _configured_secret("AAVA_SPEECH_API_KEY")
    if not ((username and password) or api_key):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="speech_api_not_configured")

    basic_ok = bool(
        username
        and password
        and basic
        and hmac.compare_digest(basic.username.encode(), username.encode())
        and hmac.compare_digest(basic.password.encode(), password.encode())
    )
    bearer_ok = bool(
        api_key
        and bearer
        and bearer.scheme.lower() == "bearer"
        and hmac.compare_digest(bearer.credentials.encode(), api_key.encode())
    )
    if not (basic_ok or bearer_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_speech_api_credentials",
            headers={"WWW-Authenticate": "Basic, Bearer"},
        )


async def _read_bounded(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(min(1024 * 1024, _MAX_INPUT_BYTES + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_INPUT_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="audio_file_too_large")
        chunks.append(chunk)
    if not chunks:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty_audio_file")
    return b"".join(chunks)


async def _decode_to_pcm16(audio: bytes) -> bytes:
    def _run() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                "pipe:0",
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                "16000",
                "pipe:1",
            ],
            input=audio,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=90,
            check=False,
        )

    try:
        result = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="audio_decode_timeout") from exc
    if result.returncode != 0 or not result.stdout:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported_or_invalid_audio")
    if len(result.stdout) > _MAX_PCM_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="decoded_audio_too_large")
    return result.stdout


async def _transcribe_pcm16(pcm16: bytes, language: str | None) -> dict[str, Any]:
    ws_url = get_setting("HEALTH_CHECK_LOCAL_AI_URL", "ws://127.0.0.1:8765")
    token = _configured_secret("LOCAL_WS_AUTH_TOKEN")
    timeout = min(max(float(_configured_secret("AAVA_SPEECH_STT_TIMEOUT_SECONDS") or "240"), 10.0), 600.0)
    request_id = secrets.token_hex(16)
    payload: dict[str, Any] = {
        "type": "transcribe_request",
        "request_id": request_id,
        "encoding": "pcm_s16le",
        "sample_rate_hz": 16000,
        "data": base64.b64encode(pcm16).decode("ascii"),
    }
    if language and language not in {"default", "detect_language"}:
        payload["language"] = language

    try:
        async with websockets.connect(ws_url, open_timeout=5, close_timeout=5, max_size=None, proxy=None) as ws:
            if token:
                await ws.send(json.dumps({"type": "auth", "auth_token": token}))
                auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if auth.get("type") != "auth_response" or auth.get("status") != "ok":
                    raise RuntimeError("local_ai_auth_failed")
            await ws.send(json.dumps(payload))
            response = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="transcription_timeout") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="local_ai_transcription_unavailable") from exc

    if response.get("type") != "transcribe_response" or response.get("request_id") != request_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="invalid_local_ai_response")
    if response.get("status") != "ok":
        detail = str(response.get("error") or "transcription_failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    return response


def _timestamp(seconds: float, *, separator: str) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def _format_response(text: str, duration: float, language: str | None, response_format: str):
    if response_format == "text":
        return PlainTextResponse(text)
    if response_format in {"srt", "vtt"}:
        separator = "," if response_format == "srt" else "."
        cue = f"{_timestamp(0, separator=separator)} --> {_timestamp(duration, separator=separator)}\n{text}\n"
        if response_format == "vtt":
            cue = "WEBVTT\n\n" + cue
        else:
            cue = "1\n" + cue
        return PlainTextResponse(cue)
    if response_format == "verbose_json":
        return JSONResponse(
            {
                "task": "transcribe",
                "language": language or "unknown",
                "duration": duration,
                "text": text,
                "segments": [{"id": 0, "start": 0.0, "end": duration, "text": text}],
            }
        )
    return JSONResponse({"text": text})


@router.get("/models")
async def list_speech_models(_: None = Depends(require_speech_auth)):
    return {"object": "list", "data": [{"id": "whisper-1", "object": "model", "owned_by": "ava"}]}


@router.post("/audio/transcriptions")
async def create_transcription(
    file: Annotated[UploadFile, File(...)],
    model: Annotated[str, Form()] = "whisper-1",
    language: Annotated[str | None, Form()] = None,
    response_format: Annotated[str, Form()] = "json",
    _: None = Depends(require_speech_auth),
):
    if model not in {"whisper-1", "default"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported_model")
    if response_format not in _SUPPORTED_RESPONSE_FORMATS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported_response_format")
    audio = await _read_bounded(file)
    pcm16 = await _decode_to_pcm16(audio)
    result = await _transcribe_pcm16(pcm16, language)
    text = str(result.get("text") or "")
    duration = len(pcm16) / float(16000 * 2)
    return _format_response(text, duration, language, response_format)
