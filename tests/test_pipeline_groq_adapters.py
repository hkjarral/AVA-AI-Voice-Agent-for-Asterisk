import asyncio
import json
import wave
from io import BytesIO

import pytest

from src.audio.resampler import convert_pcm16le_to_target_format
from src.config import AppConfig, GroqSTTProviderConfig, GroqTTSProviderConfig
from src.pipelines.groq import GroqSTTAdapter, GroqTTSAdapter
from src.pipelines.openai import OpenAILLMAdapter
from src.pipelines.orchestrator import PipelineOrchestrator


def _build_wav_bytes(pcm16: bytes, sample_rate: int = 8000) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16)
    return buf.getvalue()


def _build_app_config() -> AppConfig:
    providers = {
        "groq_stt": {
            "type": "groq",
            "enabled": True,
            "api_key": "test-key",
            "stt_base_url": "https://api.groq.com/openai/v1/audio/transcriptions",
            "stt_model": "whisper-large-v3-turbo",
            "response_format": "json",
            "temperature": 0,
            "request_timeout_sec": 2.0,
        },
        "groq_tts": {
            "type": "groq",
            "enabled": True,
            "api_key": "test-key",
            "tts_base_url": "https://api.groq.com/openai/v1/audio/speech",
            "tts_model": "canopylabs/orpheus-v1-english",
            "voice": "hannah",
            "response_format": "wav",
            "max_input_chars": 200,
            "target_encoding": "mulaw",
            "target_sample_rate_hz": 8000,
            "chunk_size_ms": 20,
            "request_timeout_sec": 2.0,
        },
        # Groq LLM already uses OpenAI-compatible adapter registration.
        "groq_llm": {
            "type": "openai",
            "enabled": True,
            "api_key": "test-key",
            "chat_base_url": "https://api.groq.com/openai/v1",
            "chat_model": "llama-3.3-70b-versatile",
            "tools_enabled": False,
        },
    }
    pipelines = {
        "groq_cloud": {
            "stt": "groq_stt",
            "llm": "groq_llm",
            "tts": "groq_tts",
            "options": {
                "stt": {},
                "llm": {"use_realtime": False},
                "tts": {"format": {"encoding": "mulaw", "sample_rate": 8000}},
            },
        }
    }
    return AppConfig(
        default_provider="openai",
        providers=providers,
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "prompt", "model": "gpt-4o"},
        audio_transport="audiosocket",
        downstream_mode="stream",
        pipelines=pipelines,
        active_pipeline="groq_cloud",
    )


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self):
        return self._body


class _FakeSession:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self._status = status
        self.requests = []
        self.closed = False

    def post(self, url, *, json=None, data=None, headers=None, timeout=None):
        self.requests.append(
            {"url": url, "json": json, "data": data, "headers": headers, "timeout": timeout}
        )
        return _FakeResponse(self._body, status=self._status)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_groq_stt_adapter_transcribes_json():
    app_config = _build_app_config()
    provider_config = GroqSTTProviderConfig(**app_config.providers["groq_stt"])
    body = json.dumps({"text": "hello"}).encode("utf-8")
    fake_session = _FakeSession(body)

    adapter = GroqSTTAdapter(
        "groq_stt",
        app_config,
        provider_config,
        {},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    audio_buffer = b"\x00\x10" * 160  # 10 ms @ 16 kHz
    transcript = await adapter.transcribe("call-1", audio_buffer, 16000, {})
    assert transcript == "hello"

    request = fake_session.requests[0]
    assert request["url"].endswith("/audio/transcriptions")
    assert request["headers"]["Authorization"] == "Bearer test-key"
    assert request["data"] is not None


@pytest.mark.asyncio
async def test_groq_tts_adapter_synthesizes_wav_to_mulaw():
    app_config = _build_app_config()
    provider_config = GroqTTSProviderConfig(**app_config.providers["groq_tts"])

    pcm_audio = b"\x00\x10" * 160  # 20 ms @ 8 kHz
    wav_bytes = _build_wav_bytes(pcm_audio, sample_rate=8000)
    fake_session = _FakeSession(wav_bytes)

    adapter = GroqTTSAdapter(
        "groq_tts",
        app_config,
        provider_config,
        {"format": {"encoding": "mulaw", "sample_rate": 8000}},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "Hello caller", {})]
    synthesized = b"".join(chunks)
    expected = convert_pcm16le_to_target_format(pcm_audio, "mulaw")
    assert synthesized == expected

    request = fake_session.requests[0]
    assert request["url"].endswith("/audio/speech")
    assert request["json"]["model"] == "canopylabs/orpheus-v1-english"
    assert request["json"]["voice"] == "hannah"
    assert request["json"]["response_format"] == "wav"


@pytest.mark.asyncio
async def test_groq_tts_adapter_splits_long_text():
    app_config = _build_app_config()
    provider_config = GroqTTSProviderConfig(**app_config.providers["groq_tts"])

    pcm_audio = b"\x00\x10" * 160  # 20 ms @ 8 kHz
    wav_bytes = _build_wav_bytes(pcm_audio, sample_rate=8000)
    fake_session = _FakeSession(wav_bytes)

    adapter = GroqTTSAdapter(
        "groq_tts",
        app_config,
        provider_config,
        {"format": {"encoding": "mulaw", "sample_rate": 8000}, "max_input_chars": 10},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    long_text = "hello world this is a long sentence"
    chunks = [chunk async for chunk in adapter.synthesize("call-1", long_text, {})]
    assert b"".join(chunks)
    assert len(fake_session.requests) >= 2


@pytest.mark.asyncio
async def test_pipeline_orchestrator_registers_groq_adapters():
    app_config = _build_app_config()
    orchestrator = PipelineOrchestrator(app_config)
    await orchestrator.start()

    resolution = orchestrator.get_pipeline("call-1")
    assert isinstance(resolution.stt_adapter, GroqSTTAdapter)
    assert isinstance(resolution.tts_adapter, GroqTTSAdapter)
    assert isinstance(resolution.llm_adapter, OpenAILLMAdapter)

