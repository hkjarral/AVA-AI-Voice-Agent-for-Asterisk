import asyncio
import base64
import json
import wave
from io import BytesIO

import pytest

from src.audio.resampler import convert_pcm16le_to_target_format
from src.config import AppConfig, OpenAIProviderConfig
from src.pipelines.openai import OpenAISTTAdapter, OpenAILLMAdapter, OpenAITTSAdapter
from src.pipelines.orchestrator import PipelineOrchestrator


def _build_app_config() -> AppConfig:
    providers = {
        "openai": {
            "api_key": "test-key",
            "organization": "test-org",
            "project": "test-project",
            "realtime_base_url": "wss://api.openai.com/v1/realtime",
            "chat_base_url": "https://api.openai.com/v1",
            "tts_base_url": "https://api.openai.com/v1/audio/speech",
            "realtime_model": "gpt-4o-realtime-preview-2024-12-17",
            "chat_model": "gpt-4o-mini",
            "tts_model": "gpt-4o-mini-tts",
            "voice": "alloy",
            "default_modalities": ["text"],
            "input_encoding": "linear16",
            "input_sample_rate_hz": 8000,
            "target_encoding": "mulaw",
            "target_sample_rate_hz": 8000,
            "chunk_size_ms": 20,
            "response_timeout_sec": 2.0,
        }
    }
    pipelines = {
        "openai_stack": {
            "stt": "openai_stt",
            "llm": "openai_llm",
            "tts": "openai_tts",
            "options": {
                "stt": {},
                "llm": {"use_realtime": False, "temperature": 0.5},
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
        active_pipeline="openai_stack",
    )


class _MockWebSocket:
    def __init__(self):
        self.sent = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        return await self._queue.get()

    async def close(self):
        self.closed = True

    def push(self, message):
        self._queue.put_nowait(message)


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

    async def text(self):
        return self._body.decode("utf-8", errors="ignore")


class _FakeSession:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self._status = status
        self.requests = []
        self.closed = False

    def post(self, url, json=None, data=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json, "data": data, "headers": headers, "timeout": timeout})
        return _FakeResponse(self._body, status=self._status)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_openai_stt_adapter_transcribes(monkeypatch):
    app_config = _build_app_config()
    provider_config = OpenAIProviderConfig(**app_config.providers["openai"])
    body = json.dumps({"text": "hello"}).encode("utf-8")
    fake_session = _FakeSession(body)
    adapter = OpenAISTTAdapter(
        "openai_stt",
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
async def test_openai_llm_adapter_chat_completion(monkeypatch):
    app_config = _build_app_config()
    provider_config = OpenAIProviderConfig(**app_config.providers["openai"])
    body = json.dumps({"choices": [{"message": {"content": "hi there"}}]}).encode("utf-8")
    fake_session = _FakeSession(body)

    adapter = OpenAILLMAdapter(
        "openai_llm",
        app_config,
        provider_config,
        {"use_realtime": False},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    response = await adapter.generate("call-1", "hello", {"system_prompt": "You are helpful."}, {})
    assert response.text == "hi there"

    request = fake_session.requests[0]
    assert request["json"]["model"] == "gpt-4o-mini"
    assert request["json"]["messages"][-1] == {"role": "user", "content": "hello"}


@pytest.mark.asyncio
async def test_openai_llm_adapter_realtime(monkeypatch):
    app_config = _build_app_config()
    provider_config = OpenAIProviderConfig(**app_config.providers["openai"])
    adapter = OpenAILLMAdapter("openai_llm", app_config, provider_config, {"use_realtime": True})

    mock_ws = _MockWebSocket()

    async def fake_connect(*args, **kwargs):
        return mock_ws

    monkeypatch.setattr("src.pipelines.openai.websockets.connect", fake_connect)

    await adapter.start()
    task = asyncio.create_task(
        adapter.generate(
            "call-1",
            "hello listener",
            {"system_prompt": "You are concise."},
            {"use_realtime": True},
        )
    )

    await asyncio.sleep(0)
    mock_ws.push(json.dumps({"type": "response.output_text.delta", "delta": "response"}))
    mock_ws.push(json.dumps({"type": "response.output_text.done"}))

    result = await task
    assert result == "response"

    session_event = json.loads(mock_ws.sent[0])
    assert session_event["type"] == "session.create"
    response_event = json.loads(mock_ws.sent[1])
    assert response_event["type"] == "response.create"


@pytest.mark.asyncio
async def test_openai_tts_adapter_synthesizes_chunks():
    app_config = _build_app_config()
    provider_config = OpenAIProviderConfig(**app_config.providers["openai"])

    pcm_audio = b"\x00\x10" * 160  # 20 ms @ 8 kHz
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(pcm_audio)
    wav_bytes = buf.getvalue()
    fake_session = _FakeSession(wav_bytes)

    adapter = OpenAITTSAdapter(
        "openai_tts",
        app_config,
        provider_config,
        {"response_format": "wav", "format": {"encoding": "mulaw", "sample_rate": 8000}},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "Hello caller", {})]
    synthesized = b"".join(chunks)
    expected = convert_pcm16le_to_target_format(pcm_audio, "mulaw")

    assert synthesized == expected
    request = fake_session.requests[0]
    assert request["json"]["model"] == "gpt-4o-mini-tts"
    assert request["json"]["voice"] == "alloy"
    assert request["json"]["response_format"] == "wav"


@pytest.mark.asyncio
async def test_pipeline_orchestrator_registers_openai_adapters():
    app_config = _build_app_config()
    orchestrator = PipelineOrchestrator(app_config)
    await orchestrator.start()

    resolution = orchestrator.get_pipeline("call-1")
    assert isinstance(resolution.stt_adapter, OpenAISTTAdapter)
    assert isinstance(resolution.llm_adapter, OpenAILLMAdapter)
    assert isinstance(resolution.tts_adapter, OpenAITTSAdapter)
    assert resolution.tts_options["format"]["encoding"] == "mulaw"
