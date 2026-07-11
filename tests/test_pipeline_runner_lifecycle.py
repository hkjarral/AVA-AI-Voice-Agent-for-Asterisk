import asyncio
import pytest

from src.config import AppConfig
from src.engine import Engine
from src.pipelines.base import STTComponent, LLMComponent, TTSComponent


class _StubSTT(STTComponent):
    async def transcribe(self, call_id, audio_pcm16, sample_rate_hz, options):
        return "hi"


class _StreamingStubSTT(STTComponent):
    supports_streaming = True

    def __init__(self):
        self.open_options = None
        self.start_options = None
        self.start_format = None
        self.sent = []
        self.started = asyncio.Event()
        self.audio_sent = asyncio.Event()
        self._keep_receiving = asyncio.Event()

    async def open_call(self, call_id, options):
        self.open_options = dict(options)

    async def transcribe(self, call_id, audio_pcm16, sample_rate_hz, options):
        raise AssertionError("streaming adapter should not use buffered transcription")

    async def start_stream(self, call_id, options, *, sample_rate_hz, fmt):
        self.start_options = dict(options)
        self.start_format = (sample_rate_hz, fmt)
        self.started.set()

    async def send_audio(self, call_id, audio, *, fmt="pcm16_16k"):
        self.sent.append((bytes(audio), fmt))
        self.audio_sent.set()

    async def iter_results(self, call_id):
        await self._keep_receiving.wait()
        if False:
            yield ""

    async def stop_stream(self, call_id):
        self._keep_receiving.set()


class _ResultStreamingStubSTT(_StreamingStubSTT):
    def __init__(self):
        super().__init__()
        self.results = asyncio.Queue()

    async def iter_results(self, call_id):
        while True:
            value = await self.results.get()
            if value is None:
                return
            yield value

    async def stop_stream(self, call_id):
        self.results.put_nowait(None)


class _StubLLM(LLMComponent):
    async def generate(self, call_id, transcript, context, options):
        return "hello"


class _RecordingLLM(LLMComponent):
    def __init__(self):
        self.transcripts = []
        self.called = asyncio.Event()

    async def generate(self, call_id, transcript, context, options):
        self.transcripts.append(transcript)
        self.called.set()
        return ""


class _StubTTS(TTSComponent):
    async def synthesize(self, call_id, text, options):
        yield b"ulaw-bytes"


class _StubResolution:
    def __init__(self, stt_adapter=None, stt_options=None, llm_adapter=None):
        self.pipeline_name = "stub"
        self.stt_key = "stub_stt"
        self.stt_adapter = stt_adapter or _StubSTT()
        self.llm_adapter = llm_adapter or _StubLLM()
        self.tts_adapter = _StubTTS()
        self.stt_options = stt_options or {}
        self.llm_options = {}
        self.tts_options = {}
        self.prepared = True

    def component_summary(self):
        return {"stt": "stub", "llm": "stub", "tts": "stub"}


@pytest.mark.asyncio
async def test_pipeline_runner_lifecycle(monkeypatch):
    # Minimal AppConfig, orchestrator presence is enough; we will stub its output
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {"host": "127.0.0.1", "port": 8088, "username": "u", "password": "p", "app_name": "ai-voice-agent"},
        "llm": {"initial_greeting": "hi", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"local_only": {}},
        "active_pipeline": "local_only",
        "audio_transport": "externalmedia",
    }
    app_config = AppConfig(**config_data)

    engine = Engine(app_config)
    engine.pipeline_orchestrator._started = True

    # Stub orchestrator to return a fake resolution with in-memory adapters
    def fake_get_pipeline(call_id, pipeline_name=None):
        return _StubResolution()

    monkeypatch.setattr(engine.pipeline_orchestrator, "get_pipeline", fake_get_pipeline)

    # Register a fake session
    from src.core.models import CallSession
    call_id = "call-abc"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "local_only"
    await engine.session_store.upsert_call(session)

    # Start pipeline runner explicitly
    await engine._ensure_pipeline_runner(session, forced=True)

    assert call_id in engine._pipeline_tasks
    assert call_id in engine._pipeline_queues

    # Feed some audio and then cleanup
    q = engine._pipeline_queues[call_id]
    await q.put(b"\x00\x00" * 512)  # short chunk; runner will batch and continue

    await engine._cleanup_call(call_id)

    # Runner should be cancelled and queues/flags cleared
    assert call_id not in engine._pipeline_tasks
    assert call_id not in engine._pipeline_queues
    assert call_id not in engine._pipeline_forced


@pytest.mark.asyncio
async def test_pipeline_runner_uses_canonical_streaming_stt_audio_contract(monkeypatch):
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {"host": "127.0.0.1", "port": 8088, "username": "u", "password": "p", "app_name": "ai-voice-agent"},
        "llm": {"initial_greeting": "", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"streaming": {}},
        "active_pipeline": "streaming",
        "audio_transport": "externalmedia",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    stt = _StreamingStubSTT()
    configured_options = {
        "streaming": True,
        "chunk_ms": 80,
        "stream_format": "pcm16_8k",
        "sample_rate": 8000,
        "encoding": "mulaw",
    }
    resolution = _StubResolution(stt_adapter=stt, stt_options=configured_options)
    monkeypatch.setattr(engine.pipeline_orchestrator, "get_pipeline", lambda *args, **kwargs: resolution)

    from src.core.models import CallSession
    call_id = "call-streaming-format"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "streaming"
    await engine.session_store.upsert_call(session)
    await engine._ensure_pipeline_runner(session, forced=True)

    await asyncio.wait_for(stt.started.wait(), timeout=2)
    assert stt.open_options["stream_format"] == "pcm16_16k"
    assert stt.open_options["sample_rate"] == 16000
    assert stt.open_options["encoding"] == "linear16"
    assert stt.start_options == stt.open_options
    assert stt.start_format == (16000, "pcm16_16k")
    assert configured_options["stream_format"] == "pcm16_8k"  # Stored config was not mutated.

    await engine._pipeline_queues[call_id].put(b"\x00\x00" * 1280)  # 80 ms at 16 kHz PCM16.
    await asyncio.wait_for(stt.audio_sent.wait(), timeout=2)
    assert stt.sent[0][1] == "pcm16_16k"
    assert len(stt.sent[0][0]) == 2560

    await engine._cleanup_call(call_id)


@pytest.mark.asyncio
async def test_pipeline_dialog_consumer_restarts_after_unexpected_exit(monkeypatch):
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {"host": "127.0.0.1", "port": 8088, "username": "u", "password": "p", "app_name": "ai-voice-agent"},
        "llm": {"initial_greeting": "", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"streaming": {}},
        "active_pipeline": "streaming",
        "audio_transport": "externalmedia",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    stt = _ResultStreamingStubSTT()
    llm = _RecordingLLM()
    resolution = _StubResolution(
        stt_adapter=stt,
        stt_options={"streaming": True, "chunk_ms": 80},
        llm_adapter=llm,
    )
    monkeypatch.setattr(engine.pipeline_orchestrator, "get_pipeline", lambda *args, **kwargs: resolution)

    activity_calls = 0

    async def fail_first_activity(*_args, **_kwargs):
        nonlocal activity_calls
        activity_calls += 1
        if activity_calls == 1:
            raise RuntimeError("transient dialog failure")

    monkeypatch.setattr(engine, "_no_input_note_activity", fail_first_activity)

    from src.core.models import CallSession
    call_id = "call-dialog-restart"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "streaming"
    await engine.session_store.upsert_call(session)
    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(stt.started.wait(), timeout=2)

    await stt.results.put("first turn crashes consumer")
    await stt.results.put("second turn survives")
    await asyncio.wait_for(llm.called.wait(), timeout=2)

    assert llm.transcripts == ["second turn survives"]
    await engine._cleanup_call(call_id)
