"""OpenAI-compatible speech-to-text facade for the local Ava runtime."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any

import websockets
import imageio_ffmpeg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer
from starlette.datastructures import UploadFile

from settings import get_setting


router = APIRouter(prefix="/v1", tags=["speech"])

_basic = HTTPBasic(auto_error=False)
_bearer = HTTPBearer(auto_error=False)
_MAX_INPUT_BYTES = 25 * 1024 * 1024
_MAX_BODY_BYTES = _MAX_INPUT_BYTES + 64 * 1024
_MAX_DURATION_SECONDS = 120
_MAX_PCM_BYTES = _MAX_DURATION_SECONDS * 16000 * 2
_DECODE_TIMEOUT_SECONDS = 90
_SUPPORTED_RESPONSE_FORMATS = {"json", "text", "verbose_json", "srt", "vtt"}
_transcription_busy = False


def _configured_secret(name: str) -> str:
    return (get_setting(name, os.getenv(name, "")) or "").strip()


async def require_speech_auth(
    request: Request,
) -> None:
    """Authenticate solely from headers, before multipart parsing or body reads."""
    username = _configured_secret("AAVA_SPEECH_API_USERNAME")
    password = _configured_secret("AAVA_SPEECH_API_PASSWORD")
    api_key = _configured_secret("AAVA_SPEECH_API_KEY")
    if not ((username and password) or api_key):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="speech_api_not_configured")

    basic: HTTPBasicCredentials | None = await _basic(request)
    bearer: HTTPAuthorizationCredentials | None = await _bearer(request)
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


async def _read_request_body(request: Request) -> bytes:
    """Bound the complete multipart body during receipt, including chunked uploads."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_content_length") from exc
        if declared < 0:
            raise HTTPException(status_code=400, detail="invalid_content_length")
        if declared > _MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request_body_too_large")

    chunks: list[bytes] = []
    total = 0
    try:
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                total += len(chunk)
                if total > _MAX_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="request_body_too_large")
                chunks.append(chunk)
    except TimeoutError as exc:
        raise HTTPException(status_code=408, detail="upload_timeout") from exc
    return b"".join(chunks)


async def _parse_upload(request: Request) -> tuple[bytes, str, str | None, str]:
    """Parse only an authenticated, bounded multipart body and close every upload."""
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise HTTPException(status_code=415, detail="multipart_required")
    body = await _read_request_body(request)
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    bounded_request = Request(request.scope, receive)
    async with bounded_request.form(max_files=1, max_fields=3, max_part_size=8192) as form:
        files = form.getlist("file")
        if len(files) != 1 or not isinstance(files[0], UploadFile):
            raise HTTPException(status_code=400, detail="one_audio_file_required")
        for field in ("model", "language", "response_format"):
            if len(form.getlist(field)) > 1:
                raise HTTPException(status_code=400, detail="duplicate_form_field")
        model = form.get("model", "whisper-1")
        language = form.get("language")
        response_format = form.get("response_format", "json")
        if not isinstance(model, str) or model not in {"whisper-1", "default"}:
            raise HTTPException(status_code=400, detail="unsupported_model")
        if not isinstance(response_format, str) or response_format not in _SUPPORTED_RESPONSE_FORMATS:
            raise HTTPException(status_code=400, detail="unsupported_response_format")
        if language is not None:
            if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}|auto|detect_language", language):
                raise HTTPException(status_code=400, detail="invalid_language")
            if language == "detect_language":
                language = "auto"
        return await _read_bounded(files[0]), model, language, response_format


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
    """Decode with an output ceiling enforced while FFmpeg is still running."""
    # Some M4A/MP4 files keep their index at the end and cannot be read from a pipe.
    # The private temporary directory and its contents are removed on every exit path.
    with tempfile.TemporaryDirectory(prefix="ava-stt-") as temp_dir:
        source = Path(temp_dir) / "audio"
        write = asyncio.create_task(asyncio.to_thread(source.write_bytes, audio))
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            await write
            raise
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "wav,mp3,mov,ogg,matroska,flac,aac",
            "-i",
            str(source),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            "16000",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        ))
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            process = await spawn
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
            raise
        assert process.stdout is not None
        output = bytearray()
        try:
            async with asyncio.timeout(_DECODE_TIMEOUT_SECONDS):
                while chunk := await process.stdout.read(64 * 1024):
                    if len(output) + len(chunk) > _MAX_PCM_BYTES:
                        raise HTTPException(status_code=413, detail="decoded_audio_too_large")
                    output.extend(chunk)
                await process.wait()
        except TimeoutError as exc:
            raise HTTPException(status_code=422, detail="audio_decode_timeout") from exc
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        if process.returncode != 0 or not output:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported_or_invalid_audio")
        return bytes(output)


async def _transcribe_pcm16(pcm16: bytes, language: str | None) -> dict[str, Any]:
    """Send one bounded batch request and reject malformed or uncorrelated replies."""
    ws_url = get_setting("HEALTH_CHECK_LOCAL_AI_URL", "ws://127.0.0.1:8765")
    token = _configured_secret("LOCAL_WS_AUTH_TOKEN")
    try:
        timeout = min(max(float(_configured_secret("AAVA_SPEECH_STT_TIMEOUT_SECONDS") or "240"), 10.0), 600.0)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="invalid_transcription_timeout_config") from exc
    request_id = secrets.token_hex(16)
    payload: dict[str, Any] = {
        "type": "transcribe_request",
        "request_id": request_id,
        "encoding": "pcm_s16le",
        "sample_rate_hz": 16000,
        "data": (await asyncio.to_thread(base64.b64encode, pcm16)).decode("ascii"),
    }
    if language:
        payload["language"] = language

    try:
        async with websockets.connect(ws_url, open_timeout=5, close_timeout=5, max_size=64 * 1024, proxy=None) as ws:
            if token:
                await ws.send(json.dumps({"type": "auth", "auth_token": token}))
                auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if not isinstance(auth, dict) or auth.get("type") != "auth_response" or auth.get("status") != "ok":
                    raise RuntimeError("local_ai_auth_failed")
            await ws.send(await asyncio.to_thread(json.dumps, payload))
            response = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="transcription_timeout") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="local_ai_transcription_unavailable") from exc

    if not isinstance(response, dict) or response.get("type") != "transcribe_response" or response.get("request_id") != request_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="invalid_local_ai_response")
    if response.get("status") == "error":
        reason = response.get("error")
        if reason in {"invalid_language", "unsupported_language"}:
            raise HTTPException(status_code=400, detail=reason)
        if reason in {"batch_busy", "live_call_active"}:
            raise HTTPException(status_code=429, detail=reason)
        raise HTTPException(status_code=503, detail="transcription_failed")
    if response.get("status") != "ok" or not isinstance(response.get("text"), str):
        raise HTTPException(status_code=502, detail="invalid_local_ai_response")
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
async def create_transcription(request: Request):
    """Authenticate first, then admit at most one bounded Admin batch request."""
    await require_speech_auth(request)
    global _transcription_busy
    if _transcription_busy:
        raise HTTPException(status_code=429, detail="batch_busy")
    _transcription_busy = True
    operation = None
    disconnected = None
    try:
        audio, _model, language, response_format = await _parse_upload(request)

        async def transcribe():
            pcm16 = await _decode_to_pcm16(audio)
            result = await _transcribe_pcm16(pcm16, language)
            duration = len(pcm16) / float(16000 * 2)
            return _format_response(result["text"], duration, language, response_format)

        async def wait_for_disconnect():
            # The upload is fully consumed before this task becomes the sole
            # reader. ASGI doesn't cancel a normal endpoint on HTTP disconnect.
            while True:
                if (await request.receive())["type"] == "http.disconnect":
                    return
                await asyncio.sleep(0.05)

        operation = asyncio.create_task(transcribe())
        disconnected = asyncio.create_task(wait_for_disconnect())
        done, _ = await asyncio.wait(
            {operation, disconnected}, return_when=asyncio.FIRST_COMPLETED
        )
        if disconnected in done:
            await disconnected
            raise HTTPException(status_code=499, detail="client_disconnected")
        return await operation
    finally:
        try:
            for task in (operation, disconnected):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (operation, disconnected) if task),
                return_exceptions=True,
            )
        finally:
            # Keep admission reserved until decoder/WS cancellation has finished.
            _transcription_busy = False
