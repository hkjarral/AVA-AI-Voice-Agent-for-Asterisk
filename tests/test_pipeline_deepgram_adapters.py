import asyncio
import json

import pytest

from src.audio.resampler import convert_pcm16le_to_target_format
from src.config import AppConfig, DeepgramProviderConfig
from src.pipelines.deepgram import DeepgramSTTAdapter, DeepgramTTSAdapter
from src.pipelines.orchestrator import PipelineOrchestrator


def _build_app_config() -> AppConfig:
    providers = {
        "deepgram": {
            "api_key": "test-key",
            "model": "nova-2-general",
            "tts_model": "aura-asteria-en",
            "input_encoding": "linear16",
            "input_sample_rate_hz": 8000,
            "base_url": "https://api.deepgram.com",
            "continuous_input": True,
            "stt_language": "en-US",
        }
    }
    pipelines = {
        "deepgram_only": {
            "stt": "deepgram_stt",
            "llm": "ollama_llm",
            "tts": "deepgram_tts",
            "options": {
                "stt": {"language": "en-US"},
                "tts": {"format": {"encoding": "mulaw", "sample_rate": 8000}},
            },
        }
    }
    return AppConfig(
        default_provider="deepgram",
        providers=providers,
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "prompt", "model": "gpt-4o"},
        audio_transport="audiosocket",
        downstream_mode="stream",
        pipelines=pipelines,
        active_pipeline="deepgram_only",
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


class _FakeJsonResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)


class _FakeHttpSession:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self._status = status
        self.closed = False
        self.requests = []

    def post(self, url, headers=None, data=None, timeout=None):
        self.requests.append({"url": url, "headers": headers, "bytes": len(data or b"")})
        return _FakeJsonResponse(self._payload, status=self._status)

    async def close(self):
        self.closed = True


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

    async def json(self):
        return json.loads(self._body.decode("utf-8"))


class _FakeSession:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self._status = status
        self.requests = []
        self.closed = False

    def post(self, url, json=None, params=None, headers=None, data=None, timeout=None):
        self.requests.append(
            {"url": url, "json": json, "params": params, "headers": headers, "data": data, "timeout": timeout}
        )
        return _FakeResponse(self._body, status=self._status)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_deepgram_stt_adapter_transcribes(monkeypatch):
    app_config = _build_app_config()
    provider_config = DeepgramProviderConfig(**app_config.providers["deepgram"])
    deepgram_payload = json.dumps(
        {
            "results": {
                "channels": [
                    {"alternatives": [{"transcript": "hello world", "confidence": 0.92}]}
                ]
            }
        }
    ).encode("utf-8")
    fake_session = _FakeSession(deepgram_payload)
    adapter = DeepgramSTTAdapter(
        "deepgram_stt",
        app_config,
        provider_config,
        {"language": "en-US"},
        session_factory=lambda: fake_session,
    )

    # Prevent real Deepgram REST call by faking aiohttp.ClientSession used in open_call
    fake_rest_payload = {
        "results": {
            "channels": [
                {"alternatives": [{"transcript": "hello world", "confidence": 0.92}]}
            ]
        }
    }
    monkeypatch.setattr(
        "src.pipelines.deepgram.aiohttp.ClientSession",
        lambda: _FakeHttpSession(fake_rest_payload, status=200),
    )

    await adapter.start()
    await adapter.open_call("call-1", {"model": "nova-2-general"})
    audio_buffer = b"\x00\x00" * 160
    transcript = await adapter.transcribe("call-1", audio_buffer, 8000, {})
    assert transcript == "hello world"


@pytest.mark.asyncio
async def test_deepgram_tts_adapter_synthesizes_chunks():
    app_config = _build_app_config()
    provider_config = DeepgramProviderConfig(**app_config.providers["deepgram"])
    pcm_audio = b"\x00\x10" * 160  # 160 samples (20 ms @ 8 kHz)
    fake_session = _FakeSession(pcm_audio)
    adapter = DeepgramTTSAdapter(
        "deepgram_tts",
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
    assert request["json"] == {"text": "Hello caller"}
    assert request["params"]["target_encoding"] == "mulaw"
    assert request["params"]["target_sample_rate"] == 8000


@pytest.mark.asyncio
async def test_pipeline_orchestrator_registers_deepgram_adapters():
    app_config = _build_app_config()
    orchestrator = PipelineOrchestrator(app_config)
    await orchestrator.start()

    resolution = orchestrator.get_pipeline("call-1")
    assert isinstance(resolution.stt_adapter, DeepgramSTTAdapter)
    assert isinstance(resolution.tts_adapter, DeepgramTTSAdapter)
    assert resolution.stt_options["language"] == "en-US"
