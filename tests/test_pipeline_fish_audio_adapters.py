import asyncio
import io
import os
import struct
import wave

import aiohttp
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
        self.requests.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
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
async def test_fish_audio_request_is_bounded_by_a_timeout():
    session = _FakeSession([_pcm16_tone(160)])
    adapter = await _adapter(session)

    [chunk async for chunk in adapter.synthesize("call-1", "Bonjour", {})]
    assert session.requests[0]["timeout"].total == 15.0

    session = _FakeSession([_pcm16_tone(160)])
    adapter = await _adapter(session)
    [chunk async for chunk in adapter.synthesize("call-1", "Bonjour", {"request_timeout_sec": 4})]
    assert session.requests[0]["timeout"].total == 4.0


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



# ─── Realtime transport (websocket) ─────────────────────────────────────


class _FakeWebSocket:
    """Minimal aiohttp-like websocket speaking the Fish Audio realtime protocol."""

    def __init__(self, pack, audio_chunks, finish_reason="stop"):
        self._pack = pack
        self._audio_chunks = list(audio_chunks)
        self._finish_reason = finish_reason
        self.sent = []
        self.closed = False
        self._stopped = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    async def send_bytes(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self._messages()

    async def _messages(self):
        # Let the sender task deliver "start" and the first fragments.
        await asyncio.sleep(0)
        for chunk in self._audio_chunks:
            yield _FakeWSMessage(self._pack({"event": "audio", "audio": chunk}))
            await asyncio.sleep(0)
        # The provider only finishes once the client closed the text stream.
        for _ in range(100):
            if any(b"stop" in message for message in self.sent):
                break
            await asyncio.sleep(0.01)
        yield _FakeWSMessage(self._pack({"event": "finish", "reason": self._finish_reason}))


class _FakeWSMessage:
    def __init__(self, data):
        self.type = aiohttp.WSMsgType.BINARY
        self.data = data


class _WebSocketSession(_FakeSession):
    """HTTP session whose ws_connect returns the fake websocket."""

    def __init__(self, websocket):
        super().__init__([b""])
        self.websocket = websocket
        self.ws_calls = []

    def ws_connect(self, url, headers=None, timeout=None, heartbeat=None, max_msg_size=None):
        self.ws_calls.append({"url": url, "headers": headers, "timeout": timeout})
        return self.websocket


async def _fragments(*texts):
    for text in texts:
        yield text


@pytest.mark.asyncio
async def test_fish_audio_transport_declares_text_streaming():
    session = _FakeSession([b""])
    http_adapter = await _adapter(session)
    assert http_adapter.supports_text_stream is False

    ws_adapter = await _adapter(session, {"transport": "websocket"})
    assert ws_adapter.supports_text_stream is True


@pytest.mark.asyncio
async def test_fish_audio_realtime_session_follows_the_protocol():
    msgpack = pytest.importorskip("msgpack")

    def pack(obj):
        return msgpack.packb(obj, use_bin_type=True)

    def unpack(data):
        return msgpack.unpackb(data, raw=False)

    websocket = _FakeWebSocket(pack, [_pcm16_tone(160), _pcm16_tone(160)])
    session = _WebSocketSession(websocket)
    adapter = await _adapter(session, {"transport": "websocket"})

    chunks = []
    async for chunk in adapter.synthesize_stream(
        "call-1", _fragments("Bonjour,", " vous etes bien chez Rea PC."), {}
    ):
        chunks.append(chunk)

    # The realtime endpoint is derived from the configured base URL.
    assert session.ws_calls[0]["url"] == "wss://api.fish.audio/v1/tts/live"
    assert session.ws_calls[0]["headers"]["model"] == "s2.1-pro"

    events = [unpack(message) for message in websocket.sent]
    assert [event["event"] for event in events] == [
        "start", "text", "flush", "text", "flush", "stop",
    ]
    start_request = events[0]["request"]
    assert start_request["format"] == "pcm"
    assert start_request["sample_rate"] == 8000
    assert start_request["latency"] == "low"
    assert start_request["reference_id"] == "voice-model-id"
    assert start_request["text"] == ""
    assert [event["text"] for event in events if event["event"] == "text"] == [
        "Bonjour,", " vous etes bien chez Rea PC.",
    ]

    # Audio comes back as 20 ms mu-law chunks, as on the HTTP path.
    assert [len(chunk) for chunk in chunks] == [160, 160]


@pytest.mark.asyncio
async def test_fish_audio_realtime_error_is_raised():
    msgpack = pytest.importorskip("msgpack")
    pack = lambda obj: msgpack.packb(obj, use_bin_type=True)  # noqa: E731

    websocket = _FakeWebSocket(pack, [], finish_reason="error")
    session = _WebSocketSession(websocket)
    adapter = await _adapter(session, {"transport": "websocket"})

    with pytest.raises(RuntimeError, match="realtime session finished with an error"):
        async for _ in adapter.synthesize_stream("call-1", _fragments("Bonjour"), {}):
            pass


@pytest.mark.asyncio
async def test_fish_audio_stream_falls_back_to_http_requests():
    session = _FakeSession([_pcm16_tone(160)])
    adapter = await _adapter(session)  # transport http

    chunks = []
    async for chunk in adapter.synthesize_stream(
        "call-1", _fragments("Bonjour,", " bienvenue."), {}
    ):
        chunks.append(chunk)

    # One request per fragment, and audio for both.
    assert len(session.requests) == 2
    assert sum(len(chunk) for chunk in chunks) == 320


@pytest.mark.asyncio
async def test_fish_audio_realtime_url_can_be_overridden():
    session = _FakeSession([b""])
    adapter = await _adapter(
        session, {"transport": "websocket", "ws_base_url": "http://127.0.0.1:8789/v1"}
    )
    assert adapter._websocket_url(adapter._compose_options({})) == (
        "ws://127.0.0.1:8789/v1/tts/live"
    )

# ─── Integration Test (live API) ────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fish_audio_live_api():
    """Integration test against a real endpoint.

    Point it at the service with FISH_AUDIO_API_KEY (and optionally
    FISH_AUDIO_REFERENCE_ID), or at the bundled mock with:

        python scripts/fish_audio_mock.py &
        FISH_AUDIO_API_KEY=mock-key FISH_AUDIO_BASE_URL=http://127.0.0.1:8788/v1 \
            pytest -m integration tests/test_pipeline_fish_audio_adapters.py
    """
    api_key = os.getenv("FISH_AUDIO_API_KEY")
    if not api_key:
        pytest.skip("FISH_AUDIO_API_KEY not set - skipping live API test")

    app_config = _build_app_config(api_key=api_key)
    payload = dict(app_config.providers["fishaudio_tts"])
    payload["base_url"] = os.getenv("FISH_AUDIO_BASE_URL", payload["base_url"])
    payload["model"] = os.getenv("FISH_AUDIO_MODEL", payload["model"])
    payload["reference_id"] = os.getenv("FISH_AUDIO_REFERENCE_ID") or None
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
        # Audio must arrive progressively, not as a single blob.
        assert len(chunks) > 1
    finally:
        await adapter.stop()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fish_audio_live_realtime():
    """Realtime integration test, against the service or the bundled mock.

        python scripts/fish_audio_mock.py &
        FISH_AUDIO_API_KEY=mock-key FISH_AUDIO_WS_BASE_URL=ws://127.0.0.1:8789/v1 \
            pytest -m integration tests/test_pipeline_fish_audio_adapters.py
    """
    api_key = os.getenv("FISH_AUDIO_API_KEY")
    if not api_key:
        pytest.skip("FISH_AUDIO_API_KEY not set - skipping live API test")
    pytest.importorskip("msgpack")

    app_config = _build_app_config(api_key=api_key)
    payload = dict(app_config.providers["fishaudio_tts"])
    payload["transport"] = "websocket"
    payload["ws_base_url"] = os.getenv("FISH_AUDIO_WS_BASE_URL") or None
    payload["base_url"] = os.getenv("FISH_AUDIO_BASE_URL", payload["base_url"])
    payload["model"] = os.getenv("FISH_AUDIO_MODEL", payload["model"])
    payload["reference_id"] = os.getenv("FISH_AUDIO_REFERENCE_ID") or None
    provider_config = FishAudioProviderConfig(**payload)

    adapter = FishAudioTTSAdapter("fishaudio_tts", app_config, provider_config, {})
    await adapter.start()
    await adapter.open_call("call-live", {})

    try:
        chunks = []
        async for chunk in adapter.synthesize_stream(
            "call-live", _fragments("Bonjour,", " ici le test temps reel."), {}
        ):
            chunks.append(chunk)
        synthesized = b"".join(chunks)
        assert len(synthesized) > 100, "expected substantial audio, got %d bytes" % len(synthesized)
        for chunk in chunks:
            assert len(chunk) <= 160
        assert len(chunks) > 1
    finally:
        await adapter.stop()
