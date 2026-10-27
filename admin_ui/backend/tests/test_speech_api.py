import io
import sys
import wave
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


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
