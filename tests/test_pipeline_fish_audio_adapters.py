import io
import os
import struct
import wave

import pytest

from src.config import AppConfig, FishAudioProviderConfig
from src.pipelines.fish_audio import FishAudioTTSAdapter
from src.pipelines.orchestrator import PipelineOrchestrator, PipelineOrchestratorError


def _build_app_config(api_key: str = "test-key") -> AppConfig:
    providers = {
        "fishaudio_tts": {
            "api_key": api_key,
            "model": "s2.1-pro",
            "reference_id": "voice-model-id",
            "audio_format": "pcm",
            "latency": "low",
            "base_url": "https://api.fish.audio/v1",
        },
        "local": {
            "ws_url": "ws://127.0.0.1:8765",
        },
    }
    pipelines = {
        "fishaudio_pipeline": {
            "stt": "local_stt",
            "llm": "local_llm",
            "tts": "fishaudio_tts",
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
        active_pipeline="fishaudio_pipeline",
    )


class _FakeContent:
    """Mimics aiohttp's StreamReader for the chunks we care about."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.iterated = False

    def iter_chunked(self, size):
        self.iterated = True
        chunks = self._chunks

        async def iterator():
            for chunk in chunks:
                yield chunk

        return iterator()


class _FakeResponse:
    def __init__(self, chunks, status: int = 200):
        self._chunks = list(chunks)
        self.status = status
        self.content = _FakeContent(self._chunks)
        self.read_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self):
        self.read_called = True
        return b"".join(self._chunks)

    async def text(self):
        return b"".join(self._chunks).decode("utf-8", errors="ignore")

    def raise_for_status(self):
        if self.status >= 400:
            raise Exception("HTTP %d" % self.status)


class _FakeSession:
    def __init__(self, chunks, status: int = 200):
        self._chunks = list(chunks)
        self._status = status
        self.requests = []
        self.responses = []
        self.closed = False

    def post(self, url, json=None, params=None, headers=None, data=None, timeout=None):
        self.requests.append({"url": url, "json": json, "headers": headers})
        response = _FakeResponse(self._chunks, status=self._status)
        self.responses.append(response)
        return response

    async def close(self):
        self.closed = True


def _pcm16_tone(num_samples: int, amplitude: int = 1000) -> bytes:
    """Non-silent PCM16 so format conversion produces observable output."""
    return struct.pack("<" + "h" * num_samples, *([amplitude, -amplitude] * (num_samples // 2)))


def _wav_container(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


async def _adapter(session, options=None, api_key: str = "test-key"):
    app_config = _build_app_config(api_key=api_key)
    provider_config = FishAudioProviderConfig(**app_config.providers["fishaudio_tts"])
    adapter = FishAudioTTSAdapter(
        "fishaudio_tts",
        app_config,
        provider_config,
        options or {},
        session_factory=lambda: session,
    )
    await adapter.start()
    await adapter.open_call("call-1", {})
    return adapter


# ─── Unit Tests (mocked HTTP) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_fish_audio_sends_expected_request():
    session = _FakeSession([_pcm16_tone(160)])
    adapter = await _adapter(session)

    [chunk async for chunk in adapter.synthesize("call-1", "Bonjour", {})]

    assert len(session.requests) == 1
    request = session.requests[0]
    assert request["url"] == "https://api.fish.audio/v1/tts"

    payload = request["json"]
    assert payload["text"] == "Bonjour"
    assert payload["format"] == "pcm"
    # Telephony call: ask the provider for 8 kHz so nothing is resampled.
    assert payload["sample_rate"] == 8000
    assert payload["latency"] == "low"
    assert payload["reference_id"] == "voice-model-id"
    assert payload["chunk_length"] == 200

    headers = request["headers"]
    assert headers["Authorization"] == "Bearer test-key"
    # Fish Audio selects the speech model with a header.
    assert headers["model"] == "s2.1-pro"


@pytest.mark.asyncio
async def test_fish_audio_streams_chunks_as_they_arrive():
    # Three HTTP chunks of 20 ms each at 8 kHz (160 samples, 320 bytes).
    session = _FakeSession([_pcm16_tone(160), _pcm16_tone(160), _pcm16_tone(160)])
    adapter = await _adapter(session)

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "Bonjour", {})]

    # One 20 ms mu-law chunk (160 bytes) per 20 ms of PCM, streamed not buffered.
    assert [len(chunk) for chunk in chunks] == [160, 160, 160]
    assert session.responses[0].content.iterated is True
    assert session.responses[0].read_called is False


@pytest.mark.asyncio
async def test_fish_audio_realigns_samples_split_across_http_chunks():
    tone = _pcm16_tone(160)
    # Split in the middle of a 16-bit sample: the adapter must carry the odd byte.
    session = _FakeSession([tone[:161], tone[161:]])
    adapter = await _adapter(session)

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "Bonjour", {})]

    assert b"".join(chunks) != b""
    assert sum(len(chunk) for chunk in chunks) == 160


@pytest.mark.asyncio
async def test_fish_audio_wideband_transport_requests_16k_pcm():
    session = _FakeSession([_pcm16_tone(320)])
    options = {"format": {"encoding": "linear16", "sample_rate": 16000}}
    adapter = await _adapter(session, options)

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "Bonjour", {})]

    assert session.requests[0]["json"]["sample_rate"] == 16000
    # 20 ms of PCM16 at 16 kHz is 640 bytes.
    assert [len(chunk) for chunk in chunks] == [640]


@pytest.mark.asyncio
async def test_fish_audio_unsupported_rate_falls_back_and_resamples():
    session = _FakeSession([_pcm16_tone(320)])
    options = {"format": {"encoding": "linear16", "sample_rate": 22050}}
    adapter = await _adapter(session, options)

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "Bonjour", {})]

    # 22050 Hz is not a Fish Audio output rate: request 16 kHz and resample here.
    assert session.requests[0]["json"]["sample_rate"] == 16000
    assert session.responses[0].read_called is True
    assert b"".join(chunks) != b""


@pytest.mark.asyncio
async def test_fish_audio_decodes_wav_container():
    pcm = _pcm16_tone(160)
    session = _FakeSession([_wav_container(pcm, 8000)])
    adapter = await _adapter(session, {"audio_format": "wav"})

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "Bonjour", {})]

    assert session.requests[0]["json"]["format"] == "wav"
    assert sum(len(chunk) for chunk in chunks) == 160


@pytest.mark.asyncio
async def test_fish_audio_runtime_options_override_defaults():
    session = _FakeSession([_pcm16_tone(160)])
    adapter = await _adapter(session)

    runtime = {
        "model": "s1",
        "reference_id": "other-voice",
        "latency": "balanced",
        "speed": 1.1,
        "volume": 0.5,
    }
    [chunk async for chunk in adapter.synthesize("call-1", "Bonjour", runtime)]

    request = session.requests[0]
    assert request["headers"]["model"] == "s1"
    payload = request["json"]
    assert payload["reference_id"] == "other-voice"
    assert payload["latency"] == "balanced"
    assert payload["prosody"] == {"speed": 1.1, "volume": 0.5}


@pytest.mark.asyncio
async def test_fish_audio_empty_text_yields_nothing():
    session = _FakeSession([_pcm16_tone(160)])
    adapter = await _adapter(session)

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "", {})]

    assert chunks == []
    assert session.requests == []


@pytest.mark.asyncio
async def test_fish_audio_missing_api_key_raises():
    session = _FakeSession([_pcm16_tone(160)])
    adapter = await _adapter(session, api_key="unused")
    adapter._provider_config = FishAudioProviderConfig(api_key=None)

    with pytest.raises(RuntimeError, match="FISH_AUDIO_API_KEY"):
        [chunk async for chunk in adapter.synthesize("call-1", "Bonjour", {})]


@pytest.mark.asyncio
async def test_fish_audio_unsupported_format_raises():
    session = _FakeSession([b""])
    adapter = await _adapter(session, {"audio_format": "mp3"})

    with pytest.raises(RuntimeError, match="Unsupported Fish Audio TTS output format"):
        [chunk async for chunk in adapter.synthesize("call-1", "Bonjour", {})]


@pytest.mark.asyncio
async def test_fish_audio_api_error_raises():
    session = _FakeSession([b'{"detail": "unauthorized"}'], status=401)
    adapter = await _adapter(session)

    with pytest.raises(Exception):
        [chunk async for chunk in adapter.synthesize("call-1", "Bonjour", {})]


@pytest.mark.asyncio
async def test_pipeline_orchestrator_registers_fish_audio_tts():
    app_config = _build_app_config()
    orchestrator = PipelineOrchestrator(app_config)
    await orchestrator.start()

    resolution = orchestrator.get_pipeline("call-1")
    assert isinstance(resolution.tts_adapter, FishAudioTTSAdapter)


@pytest.mark.asyncio
async def test_pipeline_orchestrator_skips_disabled_provider():
    # A disabled provider is not registered, so a pipeline that references it is
    # reported as invalid rather than silently answering with a placeholder.
    app_config = _build_app_config()
    app_config.providers["fishaudio_tts"]["enabled"] = False
    orchestrator = PipelineOrchestrator(app_config)

    with pytest.raises(PipelineOrchestratorError, match="fishaudio_tts"):
        await orchestrator.start()


# ─── Integration Test (live API) ────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fish_audio_live_api():
    """Integration test: call the real Fish Audio API and verify telephone audio."""
    api_key = os.getenv("FISH_AUDIO_API_KEY")
    if not api_key:
        pytest.skip("FISH_AUDIO_API_KEY not set - skipping live API test")

    app_config = _build_app_config(api_key=api_key)
    payload = dict(app_config.providers["fishaudio_tts"])
    reference_id = os.getenv("FISH_AUDIO_REFERENCE_ID")
    payload["reference_id"] = reference_id or None
    provider_config = FishAudioProviderConfig(**payload)

    adapter = FishAudioTTSAdapter("fishaudio_tts", app_config, provider_config, {})
    await adapter.start()
    await adapter.open_call("call-live", {})

    try:
        chunks = [
            chunk
            async for chunk in adapter.synthesize(
                "call-live", "Bonjour, ici le test Fish Audio.", {}
            )
        ]
        synthesized = b"".join(chunks)
        assert len(synthesized) > 100, "expected substantial audio, got %d bytes" % len(synthesized)
        # mu-law 8 kHz in 20 ms chunks is 160 bytes per chunk.
        for chunk in chunks:
            assert len(chunk) <= 160
    finally:
        await adapter.stop()
