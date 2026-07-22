import asyncio
from unittest.mock import AsyncMock

import pytest

from src.config import AppConfig
from src.engine import Engine, _PipelinePlaybackInterrupted
from src.pipelines.base import STTComponent, LLMComponent, TTSComponent
from src.tools.telephony.hangup_policy import normalize_hangup_policy


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


class _BlockingLLM(LLMComponent):
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def generate(self, call_id, transcript, context, options):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return "late response after caller hangup"


class _CancellationResistantLLM(LLMComponent):
    """Model a provider request that completes after task cancellation."""

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def generate(self, call_id, transcript, context, options):
        self.started.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                continue
        return "late response after caller hangup"


class _StubTTS(TTSComponent):
    async def synthesize(self, call_id, text, options):
        yield b"ulaw-bytes"


class _RecordingTTS(TTSComponent):
    def __init__(self):
        self.started = asyncio.Event()

    async def synthesize(self, call_id, text, options):
        self.started.set()
        yield b"ulaw-bytes"


class _HangingTTS(TTSComponent):
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def synthesize(self, call_id, text, options):
        """Hold synthesis open without yielding caller-facing audio."""
        self.started.set()
        await self.release.wait()
        if False:
            yield b""


class _StreamOwnershipStub:
    def __init__(self, stream_id="stream-1"):
        self.stream_id = stream_id
        self.active = True

    def is_stream_active(self, call_id, stream_id=None):
        return self.active and stream_id == self.stream_id


@pytest.mark.asyncio
async def test_pipeline_stream_put_exits_when_barge_in_stops_full_queue():
    engine = Engine.__new__(Engine)
    manager = _StreamOwnershipStub()
    engine.streaming_playback_manager = manager
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait(b"already-full")

    async def stop_stream():
        await asyncio.sleep(0.05)
        manager.active = False

    stopper = asyncio.create_task(stop_stream())
    with pytest.raises(_PipelinePlaybackInterrupted):
        await asyncio.wait_for(
            engine._put_pipeline_stream_chunk(
                "call-deadlock", "stream-1", queue, b"blocked", wait_slice_sec=0.02
            ),
            timeout=0.5,
        )
    await stopper


@pytest.mark.asyncio
async def test_pipeline_stream_put_rejects_replaced_stream_owner():
    engine = Engine.__new__(Engine)
    engine.streaming_playback_manager = _StreamOwnershipStub(stream_id="new-stream")
    queue = asyncio.Queue(maxsize=1)

    with pytest.raises(_PipelinePlaybackInterrupted):
        await engine._put_pipeline_stream_chunk(
            "call-replaced", "old-stream", queue, b"stale"
        )
    assert queue.empty()


@pytest.mark.asyncio
async def test_pipeline_stream_put_allows_healthy_consumer():
    engine = Engine.__new__(Engine)
    engine.streaming_playback_manager = _StreamOwnershipStub()
    queue = asyncio.Queue(maxsize=1)

    await engine._put_pipeline_stream_chunk(
        "call-healthy", "stream-1", queue, b"audio"
    )
    assert await queue.get() == b"audio"


def test_pipeline_terminal_fallback_matches_first_live_farewell():
    assert Engine._is_pipeline_farewell_without_tool(
        "Okay, never mind. That's all. Thank you.",
        "Thanks for calling! If you're in the US or Canada, you'll get a text with helpful links in just a moment. Have a great day!",
        normalize_hangup_policy({}),
    )


def test_pipeline_terminal_fallback_rejects_casual_mid_call_thanks():
    assert not Engine._is_pipeline_farewell_without_tool(
        "Thanks, now explain Local Hybrid pricing.",
        "You're welcome. Local Hybrid costs about two tenths of a cent per minute.",
        normalize_hangup_policy({}),
    )


def test_pipeline_terminal_fallback_requires_assistant_farewell():
    assert not Engine._is_pipeline_farewell_without_tool(
        "That's all.",
        "Is there anything else you'd like to know?",
        normalize_hangup_policy({}),
    )


class _StubResolution:
    def __init__(self, stt_adapter=None, stt_options=None, llm_adapter=None, tts_adapter=None):
        self.pipeline_name = "stub"
        self.stt_key = "stub_stt"
        self.stt_adapter = stt_adapter or _StubSTT()
        self.llm_adapter = llm_adapter or _StubLLM()
        self.tts_adapter = tts_adapter or _StubTTS()
        self.stt_options = stt_options or {}
        self.llm_options = {}
        self.tts_options = {}
        self.prepared = True

    def component_summary(self):
        return {"stt": "stub", "llm": "stub", "tts": "stub"}


@pytest.mark.asyncio
async def test_pipeline_hanging_greeting_stops_connection_audio_after_timeout(monkeypatch):
    """A pipeline TTS generator that never yields cannot ring indefinitely."""
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {"initial_greeting": "hi", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"hanging": {}},
        "active_pipeline": "hanging",
        "audio_transport": "externalmedia",
        "downstream_mode": "file",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    engine._connection_audio_handoff_timeout_seconds = 0.05
    engine.ari_client.stop_playback = AsyncMock(return_value=True)
    hanging_tts = _HangingTTS()
    resolution = _StubResolution(tts_adapter=hanging_tts)
    monkeypatch.setattr(
        engine.pipeline_orchestrator,
        "get_pipeline",
        lambda *args, **kwargs: resolution,
    )

    from src.core.models import CallSession

    call_id = "call-hanging-greeting"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "hanging"
    session.connection_audio_playback_id = "connection-audio-hanging"
    session.connection_audio_media_uri = "tone:ring"
    await engine.session_store.upsert_call(session)

    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(hanging_tts.started.wait(), timeout=2)
    for _ in range(20):
        if session.connection_audio_playback_id is None:
            break
        await asyncio.sleep(0.01)

    engine.ari_client.stop_playback.assert_awaited_once_with(
        "connection-audio-hanging"
    )
    assert session.connection_audio_playback_id is None
    await engine._cleanup_call(call_id)


@pytest.mark.asyncio
async def test_pipeline_greeting_is_not_synthesized_after_cleanup_gate(monkeypatch):
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {
            "initial_greeting": "hello",
            "prompt": "You are helpful",
            "model": "gpt-4o",
        },
        "pipelines": {"gated": {}},
        "active_pipeline": "gated",
        "audio_transport": "audiosocket",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    tts = _RecordingTTS()
    resolution = _StubResolution(tts_adapter=tts)
    monkeypatch.setattr(
        engine.pipeline_orchestrator,
        "get_pipeline",
        lambda *args, **kwargs: resolution,
    )
    original_gate = engine._pipeline_output_allowed

    def gate(call_id, session, *, stage):
        if stage == "greeting-start":
            session.cleanup_in_progress = True
        return original_gate(call_id, session, stage=stage)

    monkeypatch.setattr(engine, "_pipeline_output_allowed", gate)

    from src.core.models import CallSession

    call_id = "call-greeting-cleanup-gate"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "gated"
    await engine.session_store.upsert_call(session)

    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.sleep(0.05)

    assert not tts.started.is_set()
    await engine._cleanup_call(call_id)


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
    resolution = _StubResolution()

    def fake_get_pipeline(call_id, pipeline_name=None):
        return resolution

    monkeypatch.setattr(engine.pipeline_orchestrator, "get_pipeline", fake_get_pipeline)

    # Register a fake session
    from src.core.models import CallSession
    call_id = "call-abc"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "local_only"
    captured_registry = object()
    session.tool_runtime_registry = captured_registry
    await engine.session_store.upsert_call(session)

    # Start pipeline runner explicitly
    await engine._ensure_pipeline_runner(session, forced=True)

    assert call_id in engine._pipeline_tasks
    assert call_id in engine._pipeline_queues
    for _ in range(20):
        if getattr(resolution.llm_adapter, "_call_tool_registry", None) is not None:
            break
        await asyncio.sleep(0.01)
    assert resolution.llm_adapter.tool_registry_or(None) is captured_registry

    # Feed some audio and then cleanup
    q = engine._pipeline_queues[call_id]
    await q.put(b"\x00\x00" * 512)  # short chunk; runner will batch and continue

    await engine._cleanup_call(call_id)

    # Runner should be cancelled and queues/flags cleared
    assert call_id not in engine._pipeline_tasks
    assert call_id not in engine._pipeline_queues
    assert call_id not in engine._pipeline_forced


@pytest.mark.asyncio
async def test_cleanup_cancels_inflight_pipeline_turn_before_bridge_teardown(monkeypatch):
    """A late LLM result must not create playback after call cleanup starts."""
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {"initial_greeting": "", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"streaming": {}},
        "active_pipeline": "streaming",
        "audio_transport": "audiosocket",
        "downstream_mode": "stream",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    stt = _ResultStreamingStubSTT()
    llm = _BlockingLLM()
    tts = _RecordingTTS()
    resolution = _StubResolution(
        stt_adapter=stt,
        stt_options={"streaming": True, "chunk_ms": 80},
        llm_adapter=llm,
        tts_adapter=tts,
    )
    monkeypatch.setattr(
        engine.pipeline_orchestrator,
        "get_pipeline",
        lambda *args, **kwargs: resolution,
    )

    from src.core.models import CallSession

    call_id = "call-cleanup-inflight-llm"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "streaming"
    session.bridge_id = "bridge-cleanup-race"
    await engine.session_store.upsert_call(session)
    engine.ari_client.set_channel_var = AsyncMock(return_value=True)
    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(stt.started.wait(), timeout=2)
    await stt.results.put("goodbye this is final")
    await asyncio.wait_for(llm.started.wait(), timeout=2)

    async def release_llm_during_bridge_teardown(_bridge_id):
        llm.release.set()
        await asyncio.sleep(0.05)
        return True

    engine.ari_client.destroy_bridge = AsyncMock(side_effect=release_llm_during_bridge_teardown)
    engine.streaming_playback_manager.start_streaming_playback = AsyncMock(
        return_value="late-stream"
    )

    await engine._cleanup_call(call_id)

    assert llm.cancelled.is_set()
    assert not tts.started.is_set()
    engine.streaming_playback_manager.start_streaming_playback.assert_not_awaited()
    assert call_id not in engine._pipeline_tasks


@pytest.mark.asyncio
async def test_cleanup_suppresses_output_from_cancellation_resistant_llm(monkeypatch):
    """A provider result that survives cancellation must still fail closed."""
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {"initial_greeting": "", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"streaming": {}},
        "active_pipeline": "streaming",
        "audio_transport": "audiosocket",
        "downstream_mode": "stream",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    stt = _ResultStreamingStubSTT()
    llm = _CancellationResistantLLM()
    tts = _RecordingTTS()
    resolution = _StubResolution(
        stt_adapter=stt,
        stt_options={"streaming": True, "chunk_ms": 80},
        llm_adapter=llm,
        tts_adapter=tts,
    )
    monkeypatch.setattr(
        engine.pipeline_orchestrator,
        "get_pipeline",
        lambda *args, **kwargs: resolution,
    )

    from src.core.models import CallSession

    call_id = "call-cleanup-resistant-llm"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "streaming"
    await engine.session_store.upsert_call(session)
    engine.ari_client.set_channel_var = AsyncMock(return_value=True)
    engine.ari_client.hangup_channel = AsyncMock(return_value=True)
    engine.streaming_playback_manager.start_streaming_playback = AsyncMock(
        return_value="late-stream"
    )

    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(stt.started.wait(), timeout=2)
    await stt.results.put("explain the project in detail")
    await asyncio.wait_for(llm.started.wait(), timeout=2)

    cleanup_task = asyncio.create_task(engine._cleanup_call(call_id))
    for _ in range(100):
        if session.cleanup_in_progress:
            break
        await asyncio.sleep(0.01)
    assert session.cleanup_in_progress is True
    # Return the provider result only after cleanup has acquired ownership.
    # Whether cancellation has propagated yet is intentionally irrelevant.
    llm.release.set()
    await asyncio.wait_for(cleanup_task, timeout=3)

    assert not tts.started.is_set()
    engine.streaming_playback_manager.start_streaming_playback.assert_not_awaited()
    assert call_id not in engine._pipeline_tasks


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
