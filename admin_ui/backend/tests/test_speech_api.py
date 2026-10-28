import asyncio
import base64
import io
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest
import websockets
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import speech  # noqa: E402


def _wav_bytes() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\0\0" * 16000)
    return out.getvalue()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(speech.router)
    return TestClient(app)


def _set_auth(monkeypatch) -> None:
    monkeypatch.setenv("AAVA_SPEECH_API_USERNAME", "nextcloud")
    monkeypatch.setenv("AAVA_SPEECH_API_PASSWORD", "test-password")


def test_models_requires_auth(monkeypatch):
    _set_auth(monkeypatch)
    response = _client().get("/v1/models")
    assert response.status_code == 401


def test_models_advertises_whisper(monkeypatch):
    _set_auth(monkeypatch)
    response = _client().get("/v1/models", auth=("nextcloud", "test-password"))
    assert response.status_code == 200
    assert response.json()["data"] == [
        {"id": "whisper-1", "object": "model", "owned_by": "ava"}
    ]


def test_verbose_json_transcription_contract(monkeypatch):
    _set_auth(monkeypatch)

    async def fake_decode(_audio: bytes) -> bytes:
        return b"\0\0" * 32000

    async def fake_transcribe(_pcm16: bytes, language: str | None):
        assert language == "en"
        return {"type": "transcribe_response", "status": "ok", "text": "hello world"}

    monkeypatch.setattr(speech, "_decode_to_pcm16", fake_decode)
    monkeypatch.setattr(speech, "_transcribe_pcm16", fake_transcribe)
    response = _client().post(
        "/v1/audio/transcriptions",
        auth=("nextcloud", "test-password"),
        files={"file": ("sample.wav", _wav_bytes(), "audio/wav")},
        data={"model": "whisper-1", "language": "en", "response_format": "verbose_json"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "task": "transcribe",
        "language": "en",
        "duration": 2.0,
        "text": "hello world",
        "segments": [{"id": 0, "start": 0.0, "end": 2.0, "text": "hello world"}],
    }


def test_rejects_unsupported_model_before_decode(monkeypatch):
    _set_auth(monkeypatch)
    response = _client().post(
        "/v1/audio/transcriptions",
        auth=("nextcloud", "test-password"),
        files={"file": ("sample.wav", _wav_bytes(), "audio/wav")},
        data={"model": "not-installed"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "unsupported_model"


def test_fails_closed_without_configured_credentials(monkeypatch):
    for key in ("AAVA_SPEECH_API_USERNAME", "AAVA_SPEECH_API_PASSWORD", "AAVA_SPEECH_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    response = _client().get("/v1/models")
    assert response.status_code == 503
    assert response.json()["detail"] == "speech_api_not_configured"


def test_bearer_auth_and_bad_credentials(monkeypatch):
    monkeypatch.setenv("AAVA_SPEECH_API_KEY", "test-key")
    monkeypatch.delenv("AAVA_SPEECH_API_USERNAME", raising=False)
    monkeypatch.delenv("AAVA_SPEECH_API_PASSWORD", raising=False)
    client = _client()
    assert client.get("/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/models", headers={"Authorization": "Bearer test-key"}).status_code == 200


async def _asgi_post(app, headers, body):
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/v1/audio/transcriptions",
        "raw_path": b"/v1/audio/transcriptions", "query_string": b"",
        "root_path": "", "headers": headers,
        "client": ("test", 1234), "server": ("test", 80),
    }
    received = 0
    sent = []

    async def receive():
        nonlocal received
        received += 1
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent[0]["status"], received


@pytest.mark.asyncio
async def test_unauthorized_upload_rejected_without_body_read(monkeypatch):
    _set_auth(monkeypatch)
    app = FastAPI()
    app.include_router(speech.router)
    status_code, reads = await _asgi_post(
        app, [(b"content-type", b"multipart/form-data; boundary=x")], b"payload"
    )
    assert (status_code, reads) == (401, 0)


@pytest.mark.asyncio
async def test_chunked_total_body_limit_is_enforced_before_parse(monkeypatch):
    _set_auth(monkeypatch)
    monkeypatch.setattr(speech, "_MAX_BODY_BYTES", 8)
    app = FastAPI()
    app.include_router(speech.router)
    auth = base64.b64encode(b"nextcloud:test-password")
    status_code, reads = await _asgi_post(
        app,
        [
            (b"content-type", b"multipart/form-data; boundary=x"),
            (b"authorization", b"Basic " + auth),
        ],
        b"123456789",
    )
    assert (status_code, reads) == (413, 1)


def test_per_file_limit_and_cleanup(monkeypatch):
    _set_auth(monkeypatch)
    monkeypatch.setattr(speech, "_MAX_INPUT_BYTES", 4)
    response = _client().post(
        "/v1/audio/transcriptions",
        auth=("nextcloud", "test-password"),
        files={"file": ("sample.wav", b"12345", "audio/wav")},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "audio_file_too_large"


@pytest.mark.asyncio
async def test_real_decoder_and_expansion_ceiling(monkeypatch):
    pcm16 = await speech._decode_to_pcm16(_wav_bytes())
    assert len(pcm16) == 32000
    with pytest.raises(HTTPException) as invalid:
        await speech._decode_to_pcm16(b"not audio")
    assert invalid.value.status_code == 422
    monkeypatch.setattr(speech, "_MAX_PCM_BYTES", 1024)
    with pytest.raises(HTTPException) as oversized:
        await speech._decode_to_pcm16(_wav_bytes())
    assert oversized.value.status_code == 413


@pytest.mark.asyncio
async def test_m4a_with_index_at_end_is_seekable(tmp_path):
    source = tmp_path / "source.m4a"
    subprocess.run(
        [
            speech.imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", "pipe:0", "-c:a", "aac", str(source),
        ],
        input=_wav_bytes(),
        capture_output=True,
        check=True,
    )
    encoded = source.read_bytes()
    assert encoded.index(b"mdat") < encoded.index(b"moov")
    pcm16 = await speech._decode_to_pcm16(encoded)
    assert len(pcm16) >= 30000


@pytest.mark.asyncio
async def test_decoder_timeout_reaps_child(monkeypatch, tmp_path):
    pid_file = tmp_path / "child.pid"
    decoder = tmp_path / "slow-decoder"
    decoder.write_text(
        "#!/usr/bin/env python3\nimport os,time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    decoder.chmod(0o700)
    monkeypatch.setattr(speech.imageio_ffmpeg, "get_ffmpeg_exe", lambda: str(decoder))
    monkeypatch.setattr(speech, "_DECODE_TIMEOUT_SECONDS", 0.5)
    with pytest.raises(HTTPException) as timed_out:
        await speech._decode_to_pcm16(b"short")
    assert timed_out.value.detail == "audio_decode_timeout"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


class _WebSocketReply:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def send(self, _message):
        return None

    async def recv(self):
        return json.dumps(self.response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"type": "transcribe_response", "request_id": "wrong", "status": "ok", "text": "hello"}, 502),
        ({"type": "transcribe_response", "request_id": "req", "status": "ok", "text": 123}, 502),
        ({"type": "transcribe_response", "request_id": "req", "status": "error", "error": "internal /secret/path"}, 503),
    ],
)
async def test_bridge_rejects_bad_response_shape_and_sanitizes_errors(monkeypatch, response, expected):
    monkeypatch.setattr(speech, "_configured_secret", lambda _name: "")
    monkeypatch.setattr(speech.secrets, "token_hex", lambda _bytes: "req")
    monkeypatch.setattr(speech.websockets, "connect", lambda *_args, **_kwargs: _WebSocketReply(response))
    with pytest.raises(HTTPException) as failed:
        await speech._transcribe_pcm16(b"\0\0", None)
    assert failed.value.status_code == expected
    assert "/secret/path" not in str(failed.value.detail)


@pytest.mark.parametrize("response_format", ["json", "text", "verbose_json", "srt", "vtt"])
def test_all_advertised_formats(monkeypatch, response_format):
    _set_auth(monkeypatch)

    async def fake_decode(_audio):
        return b"\0\0" * 16000

    async def fake_transcribe(_pcm16, _language):
        return {"text": "hello"}

    monkeypatch.setattr(speech, "_decode_to_pcm16", fake_decode)
    monkeypatch.setattr(speech, "_transcribe_pcm16", fake_transcribe)
    response = _client().post(
        "/v1/audio/transcriptions",
        auth=("nextcloud", "test-password"),
        files={"file": ("sample.wav", _wav_bytes(), "audio/wav")},
        data={"response_format": response_format},
    )
    assert response.status_code == 200
    assert "hello" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["decode", "transcribe"])
async def test_http_disconnect_cancels_work_before_releasing_admission(monkeypatch, stage):
    started = asyncio.Event()
    cleaning = asyncio.Event()
    release_cleanup = asyncio.Event()
    disconnected = asyncio.Event()

    async def parse(_request):
        return b"audio", "whisper-1", None, "json"

    async def block():
        try:
            started.set()
            await asyncio.Future()
        finally:
            cleaning.set()
            await release_cleanup.wait()

    async def decode(_audio):
        if stage == "decode":
            await block()
        return b"\0\0"

    async def transcribe(_pcm16, _language):
        await block()

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def auth(_request):
        pass

    monkeypatch.setattr(speech, "require_speech_auth", auth)
    monkeypatch.setattr(speech, "_parse_upload", parse)
    monkeypatch.setattr(speech, "_decode_to_pcm16", decode)
    monkeypatch.setattr(speech, "_transcribe_pcm16", transcribe)
    request = Request({"type": "http"}, receive)
    task = asyncio.create_task(speech.create_transcription(request))
    await asyncio.wait_for(started.wait(), 2)
    disconnected.set()
    await asyncio.wait_for(cleaning.wait(), 2)
    assert speech._transcription_busy
    with pytest.raises(HTTPException) as busy:
        await speech.create_transcription(request)
    assert busy.value.status_code == 429
    release_cleanup.set()
    with pytest.raises(HTTPException) as closed:
        await asyncio.wait_for(task, 2)
    assert closed.value.status_code == 499
    assert not speech._transcription_busy


@pytest.mark.asyncio
async def test_decoder_cancellation_during_spawn_reaps_child_and_removes_upload(monkeypatch):
    spawning = asyncio.Event()
    finish_spawn = asyncio.Event()
    source_paths = []

    class Process:
        returncode = None
        reaped = False

        def kill(self):
            self.returncode = -9

        async def wait(self):
            self.reaped = True

    process = Process()

    async def spawn(*args, **_kwargs):
        source_paths.append(Path(args[args.index("-i") + 1]))
        spawning.set()
        await finish_spawn.wait()
        return process

    monkeypatch.setattr(speech.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(speech._decode_to_pcm16(b"audio"))
    await asyncio.wait_for(spawning.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish_spawn.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert process.returncode == -9
    assert process.reaped
    assert not source_paths[0].exists()


@pytest.mark.asyncio
async def test_http_disconnect_closes_upstream_websocket(monkeypatch):
    started = asyncio.Event()
    upstream_closed = asyncio.Event()
    disconnected = asyncio.Event()

    async def backend(websocket):
        request = json.loads(await websocket.recv())
        assert request["type"] == "transcribe_request"
        started.set()
        await websocket.wait_closed()
        upstream_closed.set()

    async def parse(_request):
        return b"audio", "whisper-1", None, "json"

    async def decode(_audio):
        return b"\0\0"

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def auth(_request):
        pass

    monkeypatch.setattr(speech, "require_speech_auth", auth)
    monkeypatch.setattr(speech, "_configured_secret", lambda _name: "")
    monkeypatch.setattr(speech, "_parse_upload", parse)
    monkeypatch.setattr(speech, "_decode_to_pcm16", decode)
    async with websockets.serve(backend, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(speech, "get_setting", lambda *_args: f"ws://127.0.0.1:{port}")
        task = asyncio.create_task(speech.create_transcription(Request({"type": "http"}, receive)))
        await asyncio.wait_for(started.wait(), 2)
        disconnected.set()
        with pytest.raises(HTTPException) as closed:
            await asyncio.wait_for(task, 2)
        assert closed.value.status_code == 499
        await asyncio.wait_for(upstream_closed.wait(), 2)
        assert not speech._transcription_busy
