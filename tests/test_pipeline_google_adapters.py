import asyncio
import base64
import io
import json
import wave

import pytest

from src.audio.resampler import convert_pcm16le_to_target_format
from src.config import AppConfig, GoogleProviderConfig
from src.pipelines.google import GoogleLLMAdapter, GoogleSTTAdapter, GoogleTTSAdapter
from src.pipelines.orchestrator import PipelineOrchestrator, PipelineOrchestratorError


def _build_app_config() -> AppConfig:
    providers = {
        "google": {
            "api_key": "test-google-key",
            "project_id": "demo-project",
            "stt_base_url": "https://speech.googleapis.com/v1",
            "tts_base_url": "https://texttospeech.googleapis.com/v1",
            "llm_base_url": "https://generativelanguage.googleapis.com/v1",
            "stt_language_code": "en-US",
            "tts_voice_name": "en-US-Neural2-C",
            "tts_audio_encoding": "MULAW",
            "tts_sample_rate_hz": 8000,
            "llm_model": "models/gemini-1.5-pro-latest",
        }
    }
    pipelines = {
        "google_stack": {
            "stt": "google_stt",
            "llm": "google_llm",
            "tts": "google_tts",
            "options": {
                "stt": {"language_code": "en-US"},
                "llm": {"temperature": 0.3},
                "tts": {"format": {"encoding": "mulaw", "sample_rate": 8000}},
            },
        }
    }
    return AppConfig(
        default_provider="google",
        providers=providers,
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "prompt", "model": "gpt-4o"},
        audio_transport="audiosocket",
        downstream_mode="stream",
        pipelines=pipelines,
        active_pipeline="google_stack",
    )


class _FakeResponse:
    def __init__(self, body: str, status: int = 200):
        self._body = body
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._body


class _FakeSession:
    def __init__(self, body: str, status: int = 200):
        self._body = body
        self._status = status
        self.requests = []
        self.closed = False

    def post(self, url, json=None, params=None, headers=None, timeout=None):
        self.requests.append(
            {
                "url": url,
                "json": json,
                "params": params,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return _FakeResponse(self._body, status=self._status)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_google_stt_adapter_transcribes(monkeypatch):
    app_config = _build_app_config()
    provider_config = GoogleProviderConfig(**app_config.providers["google"])
    payload = json.dumps(
        {"results": [{"alternatives": [{"transcript": "hello google"}]}]}
    )
    fake_session = _FakeSession(payload)

    adapter = GoogleSTTAdapter(
        "google_stt",
        app_config,
        provider_config,
        {"encoding": "LINEAR16"},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    audio = b"\x00\x10" * 160  # 20 ms of PCM16 @ 8 kHz
    transcript = await adapter.transcribe("call-1", audio, 8000, {})
    assert transcript == "hello google"

    request = fake_session.requests[0]
    assert request["params"]["key"] == "test-google-key"
    assert request["json"]["config"]["languageCode"] == "en-US"


@pytest.mark.asyncio
async def test_google_llm_adapter_generate(monkeypatch):
    app_config = _build_app_config()
    provider_config = GoogleProviderConfig(**app_config.providers["google"])
    payload = json.dumps(
        {
            "candidates": [
                {"content": {"parts": [{"text": "response from gemini"}]}}
            ]
        }
    )
    fake_session = _FakeSession(payload)

    adapter = GoogleLLMAdapter(
        "google_llm",
        app_config,
        provider_config,
        {"temperature": 0.1},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    reply = await adapter.generate(
        "call-1",
        "hello there",
        {"system_prompt": "You are friendly."},
        {"temperature": 0.2},
    )
    assert reply.text == "response from gemini"

    request = fake_session.requests[0]
    assert request["params"]["key"] == "test-google-key"
    assert request["json"]["generationConfig"]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_google_tts_adapter_synthesizes_chunks():
    app_config = _build_app_config()
    provider_config = GoogleProviderConfig(**app_config.providers["google"])

    pcm_audio = b"\x00\x10" * 160
    mulaw_audio = convert_pcm16le_to_target_format(pcm_audio, "mulaw")
    payload = json.dumps({"audioContent": base64.b64encode(mulaw_audio).decode("ascii")})
    fake_session = _FakeSession(payload)

    adapter = GoogleTTSAdapter(
        "google_tts",
        app_config,
        provider_config,
        {"format": {"encoding": "mulaw", "sample_rate": 8000}},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "Hello caller", {})]
    synthesized = b"".join(chunks)
    assert synthesized == mulaw_audio

    request = fake_session.requests[0]
    assert request["params"]["key"] == "test-google-key"
    assert request["json"]["voice"]["name"] == "en-US-Neural2-C"


def test_google_tts_strips_linear16_wav_container_before_playback():
    pcm_audio = b"\x00\x10" * 320
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm_audio)

    converted = GoogleTTSAdapter._convert_audio(
        wav_buffer.getvalue(),
        "LINEAR16",
        16000,
        "linear16",
        16000,
    )

    assert converted == pcm_audio


@pytest.mark.asyncio
async def test_google_orchestrator_falls_back_without_credentials(monkeypatch):
    app_config = _build_app_config()
    app_config.providers["google"].pop("api_key", None)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    orchestrator = PipelineOrchestrator(app_config)
    with pytest.raises(PipelineOrchestratorError) as exc_info:
        await orchestrator.start()
    assert "google_stack" in str(exc_info.value)
    assert "cannot resolve" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_pipeline_orchestrator_registers_google_adapters():
    app_config = _build_app_config()
    orchestrator = PipelineOrchestrator(app_config)
    await orchestrator.start()

    resolution = orchestrator.get_pipeline("call-1")
    assert isinstance(resolution.stt_adapter, GoogleSTTAdapter)
    assert isinstance(resolution.llm_adapter, GoogleLLMAdapter)
    assert isinstance(resolution.tts_adapter, GoogleTTSAdapter)
    assert resolution.stt_options["language_code"] == "en-US"
