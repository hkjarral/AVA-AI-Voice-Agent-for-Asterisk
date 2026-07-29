import asyncio
import json
import os
import struct

import pytest

from src.config import AppConfig, CambAiProviderConfig
from src.pipelines.cambai import CambAiTTSAdapter
from src.pipelines.orchestrator import PipelineOrchestrator


def _build_app_config(api_key: str = "test-key") -> AppConfig:
    providers = {
        "cambai": {
            "api_key": api_key,
            "voice_id": 147320,
            "speech_model": "mars-flash",
            "language": "en-us",
            "base_url": "https://client.camb.ai/apis",
            "output_format": "pcm_s16le",
        },
        "local": {
            "ws_url": "ws://127.0.0.1:8765",
        },
    }
    pipelines = {
        "cambai_pipeline": {
            "stt": "local_stt",
            "llm": "local_llm",
            "tts": "cambai_tts",
        }
    }
    return AppConfig(
        default_provider="local",
        providers=providers,
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "prompt", "model": "gpt-4o"},
        audio_transport="audiosocket",
        downstream_mode="stream",
        pipelines=pipelines,
        active_pipeline="cambai_pipeline",
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

    async def text(self):
        return self._body.decode("utf-8", errors="ignore")

    def raise_for_status(self):
        if self.status >= 400:
            raise Exception(f"HTTP {self.status}")


class _FakeSession:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self._status = status
        self.requests = []
        self.closed = False

    def post(self, url, json=None, params=None, headers=None, data=None, timeout=None):
        self.requests.append(
            {"url": url, "json": json, "params": params, "headers": headers}
        )
        return _FakeResponse(self._body, status=self._status)

    async def close(self):
        self.closed = True


def _make_pcm16_silence(num_samples: int = 320) -> bytes:
    """Generate silent PCM16 audio (320 samples = 20ms at 16kHz)."""
    return b"\x00\x00" * num_samples


# ─── Unit Tests (mocked HTTP) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_cambai_tts_adapter_sends_correct_payload():
    """Verify the adapter sends the correct payload to CAMB AI API."""
    app_config = _build_app_config()
    provider_config = CambAiProviderConfig(**app_config.providers["cambai"])

    # Fake PCM16 audio response (simulate 24kHz, will be resampled to 8kHz)
    pcm_audio = _make_pcm16_silence(480)  # 20ms at 24kHz
    fake_session = _FakeSession(pcm_audio)

    adapter = CambAiTTSAdapter(
        "cambai_tts",
        app_config,
        provider_config,
        {},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "Hello from CAMB AI", {})]

    # Verify request was made
    assert len(fake_session.requests) == 1
    request = fake_session.requests[0]

    # Verify URL
    assert request["url"] == "https://client.camb.ai/apis/tts-stream"

    # Verify payload
    payload = request["json"]
    assert payload["text"] == "Hello from CAMB AI"
    assert payload["voice_id"] == 147320
    assert payload["language"] == "en-us"
    assert payload["speech_model"] == "mars-flash"
    assert payload["output_configuration"] == {"format": "pcm_s16le"}

    # Verify auth header
    assert request["headers"]["x-api-key"] == "test-key"
    assert request["headers"]["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_cambai_tts_adapter_converts_pcm_to_ulaw():
    """Verify PCM16 audio is converted to μ-law for telephony."""
    app_config = _build_app_config()
    provider_config = CambAiProviderConfig(**app_config.providers["cambai"])

    # Create non-silent PCM16 audio so conversion produces non-empty output
    # 960 samples at 24kHz = 40ms, resampled to 8kHz = 320 samples = 640 bytes μ-law
    pcm_audio = struct.pack("<" + "h" * 960, *([1000] * 960))
    fake_session = _FakeSession(pcm_audio)

    adapter = CambAiTTSAdapter(
        "cambai_tts",
        app_config,
        provider_config,
        {},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "Test audio", {})]
    synthesized = b"".join(chunks)

    # Output should be non-empty μ-law audio
    assert len(synthesized) > 0
    # μ-law bytes should differ from the PCM input
    assert synthesized != pcm_audio


@pytest.mark.asyncio
async def test_cambai_tts_adapter_empty_text_yields_nothing():
    """Empty text should yield no audio chunks."""
    app_config = _build_app_config()
    provider_config = CambAiProviderConfig(**app_config.providers["cambai"])

    fake_session = _FakeSession(b"")
    adapter = CambAiTTSAdapter(
        "cambai_tts",
        app_config,
        provider_config,
        {},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "", {})]
    assert chunks == []
    # No HTTP request should have been made
    assert len(fake_session.requests) == 0


@pytest.mark.asyncio
async def test_cambai_tts_adapter_api_error_raises():
    """API errors should propagate as exceptions."""
    app_config = _build_app_config()
    provider_config = CambAiProviderConfig(**app_config.providers["cambai"])

    fake_session = _FakeSession(b'{"error": "unauthorized"}', status=401)
    adapter = CambAiTTSAdapter(
        "cambai_tts",
        app_config,
        provider_config,
        {},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    with pytest.raises(Exception):
        chunks = [chunk async for chunk in adapter.synthesize("call-1", "Error test", {})]


@pytest.mark.asyncio
async def test_cambai_tts_adapter_runtime_option_overrides():
    """Runtime options should override provider config defaults."""
    app_config = _build_app_config()
    provider_config = CambAiProviderConfig(**app_config.providers["cambai"])

    pcm_audio = _make_pcm16_silence(480)
    fake_session = _FakeSession(pcm_audio)

    adapter = CambAiTTSAdapter(
        "cambai_tts",
        app_config,
        provider_config,
        {},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    # Override voice_id and language at runtime
    runtime_opts = {"voice_id": 999999, "language": "es-es", "speech_model": "mars-pro"}
    chunks = [chunk async for chunk in adapter.synthesize("call-1", "Hola", runtime_opts)]

    payload = fake_session.requests[0]["json"]
    assert payload["voice_id"] == 999999
    assert payload["language"] == "es-es"
    assert payload["speech_model"] == "mars-pro"


@pytest.mark.asyncio
async def test_pipeline_orchestrator_registers_cambai_tts():
    """Verify the orchestrator registers the cambai_tts factory."""
    app_config = _build_app_config()
    orchestrator = PipelineOrchestrator(app_config)
    await orchestrator.start()

    resolution = orchestrator.get_pipeline("call-1")
    assert isinstance(resolution.tts_adapter, CambAiTTSAdapter)


# ─── Integration Tests (live API) ───────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cambai_tts_live_api():
    """Integration test: call the real CAMB AI TTS API and verify audio output."""
    api_key = os.getenv("CAMB_API_KEY")
    if not api_key:
        pytest.skip("CAMB_API_KEY not set — skipping live API test")

    app_config = _build_app_config(api_key=api_key)
    provider_config = CambAiProviderConfig(**app_config.providers["cambai"])

    adapter = CambAiTTSAdapter(
        "cambai_tts",
        app_config,
        provider_config,
        {},
    )

    await adapter.start()
    await adapter.open_call("call-live", {})

    try:
        chunks = []
        async for chunk in adapter.synthesize("call-live", "Hello from CAMB AI test.", {}):
            chunks.append(chunk)

        synthesized = b"".join(chunks)

        # Should produce non-empty μ-law audio
        assert len(synthesized) > 100, f"Expected substantial audio, got {len(synthesized)} bytes"

        # μ-law 8kHz at 20ms chunks = 160 bytes per chunk
        for chunk in chunks:
            assert len(chunk) <= 160, f"Chunk too large: {len(chunk)} bytes (expected ≤160 for 20ms μ-law)"
    finally:
        await adapter.stop()
