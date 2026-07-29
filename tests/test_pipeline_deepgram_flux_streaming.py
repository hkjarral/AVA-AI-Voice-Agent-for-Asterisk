import asyncio

import pytest

from src.config import AppConfig, DeepgramProviderConfig
from src.pipelines import deepgram_flux as flux_module
from src.pipelines.deepgram_flux import DeepgramFluxSTTAdapter


class _MockFluxWebSocket:
    def __init__(self):
        self.closed = False
        self._queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self._queue.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def send(self, data):
        return None

    async def close(self):
        self.closed = True
        await self._queue.put(None)


def _app_config():
    return AppConfig(
        default_provider="deepgram",
        providers={
            "deepgram": {
                "api_key": "test-key",
                "base_url": "https://api.deepgram.com",
                "input_encoding": "linear16",
                "input_sample_rate_hz": 8000,
            }
        },
        asterisk={"host": "127.0.0.1", "username": "u", "password": "p"},
        llm={"initial_greeting": "", "prompt": "prompt", "model": "gpt-4o"},
    )


@pytest.mark.asyncio
async def test_flux_connection_and_start_use_the_same_engine_audio_format(monkeypatch):
    config = _app_config()
    provider = DeepgramProviderConfig(**config.providers["deepgram"])
    adapter = DeepgramFluxSTTAdapter(
        "deepgram_flux_stt",
        config,
        provider,
        {"sample_rate": 8000, "encoding": "mulaw"},
    )
    captured = {}

    async def fake_connect(url, **kwargs):
        captured["url"] = url
        return _MockFluxWebSocket()

    monkeypatch.setattr(flux_module.websockets, "connect", fake_connect)
    runtime_options = {
        "sample_rate": 16000,
        "encoding": "linear16",
        "stream_format": "pcm16_16k",
    }
    await adapter.open_call("call-flux", runtime_options)
    await adapter.start_stream(
        "call-flux",
        runtime_options,
        sample_rate_hz=16000,
        fmt="pcm16_16k",
    )

    assert "/v2/listen" in captured["url"]
    assert "encoding=linear16" in captured["url"]
    assert "sample_rate=16000" in captured["url"]
    await adapter.close_call("call-flux")
