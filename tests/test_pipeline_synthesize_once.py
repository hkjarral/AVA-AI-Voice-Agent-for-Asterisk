import asyncio

import pytest

import src.pipelines.orchestrator as orchestrator_module
from src.config import AppConfig
from src.pipelines.azure import AzureTTSAdapter
from src.pipelines.base import TTSComponent
from src.pipelines.cambai import CambAiTTSAdapter
from src.pipelines.deepgram import DeepgramTTSAdapter
from src.pipelines.elevenlabs import ElevenLabsTTSAdapter
from src.pipelines.fish_audio import FishAudioTTSAdapter
from src.pipelines.google import GoogleTTSAdapter
from src.pipelines.groq import GroqTTSAdapter
from src.pipelines.local import LocalTTSAdapter
from src.pipelines.openai import OpenAITTSAdapter
from src.pipelines.orchestrator import (
    PipelineOrchestrator,
    PipelineOrchestratorError,
    PipelineSynthesisError,
    _tts_voice_language_options,
)

MULAW_8K = {"encoding": "mulaw", "sample_rate": 8000}


def _app_config(providers=None):
    return AppConfig(
        default_provider="local",
        providers=providers or {},
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "prompt"},
    )


class _RecordingTTS(TTSComponent):
    def __init__(self, component_key, events, chunks=(b"\xff" * 160, b"\x7f" * 160)):
        self.component_key = component_key
        self.events = events
        self._chunks = chunks

    async def start(self):
        self.events.append("start")

    async def open_call(self, call_id, options):
        self.events.append(("open", call_id, dict(options)))

    async def synthesize(self, call_id, text, options):
        self.events.append(("synthesize", text, dict(options)))
        for chunk in self._chunks:
            yield chunk

    async def close_call(self, call_id):
        self.events.append(("close", call_id))

    async def stop(self):
        self.events.append("stop")


class _HangingTTS(_RecordingTTS):
    async def synthesize(self, call_id, text, options):
        self.events.append(("synthesize", text, dict(options)))
        await asyncio.sleep(10)
        yield b"late"


class _FailingTTS(_RecordingTTS):
    async def synthesize(self, call_id, text, options):
        self.events.append(("synthesize", text, dict(options)))
        raise RuntimeError("provider rejected token sk-secret-123")
        yield b""  # pragma: no cover - makes this an async generator


class _CloseFailsTTS(_RecordingTTS):
    async def close_call(self, call_id):
        self.events.append(("close", call_id))
        raise RuntimeError("close failed for https://user:pw@example.invalid")


def _orchestrator(component_cls, events, providers=None, **kwargs):
    return PipelineOrchestrator(
        _app_config(providers),
        registry={"chosen_tts": lambda key, options: component_cls(key, events, **kwargs)},
    )


def _synth_options(events):
    return next(e for e in events if isinstance(e, tuple) and e[0] == "synthesize")[2]


@pytest.mark.asyncio
async def test_synthesize_once_joins_chunks_and_closes_component():
    events = []
    orchestrator = _orchestrator(_RecordingTTS, events)

    audio = await orchestrator.synthesize_once(
        "chosen_tts", call_id="attx:1", text="Hello there", timeout_sec=5
    )

    assert audio == b"\xff" * 160 + b"\x7f" * 160
    kinds = [e if isinstance(e, str) else e[0] for e in events]
    assert kinds == ["start", "open", "synthesize", "close", "stop"]
    assert events[-2:] == [("close", "attx:1"), "stop"]


@pytest.mark.asyncio
async def test_synthesize_once_forces_mulaw_8k_and_timeouts():
    events = []
    orchestrator = _orchestrator(_RecordingTTS, events)

    await orchestrator.synthesize_once(
        "chosen_tts",
        call_id="attx:2",
        text="Hello",
        options={"format": {"encoding": "slin16", "sample_rate": 16000}, "target_encoding": "slin16"},
        timeout_sec=3,
    )

    for seen in (_synth_options(events), next(e for e in events if isinstance(e, tuple) and e[0] == "open")[2]):
        assert seen["format"] == MULAW_8K
        assert seen["target_format"] == MULAW_8K
        assert seen["target_encoding"] == "mulaw"
        assert seen["target_sample_rate_hz"] == 8000
        assert seen["timeout_sec"] == 3.0
        assert seen["request_timeout_sec"] == 3.0
        assert seen["response_timeout_sec"] == 3.0


@pytest.mark.asyncio
async def test_synthesize_once_closes_on_timeout():
    events = []
    orchestrator = _orchestrator(_HangingTTS, events)

    with pytest.raises(asyncio.TimeoutError):
        await orchestrator.synthesize_once("chosen_tts", call_id="attx:3", text="Hi", timeout_sec=0.05)

    assert events[-2:] == [("close", "attx:3"), "stop"]


@pytest.mark.asyncio
async def test_synthesize_once_closes_on_adapter_exception():
    events = []
    orchestrator = _orchestrator(_FailingTTS, events)

    with pytest.raises(RuntimeError):
        await orchestrator.synthesize_once("chosen_tts", call_id="attx:4", text="Hi", timeout_sec=5)

    assert events[-2:] == [("close", "attx:4"), "stop"]


@pytest.mark.asyncio
async def test_synthesize_once_empty_audio_is_an_error():
    events = []
    orchestrator = _orchestrator(_RecordingTTS, events, chunks=())

    with pytest.raises(PipelineSynthesisError, match="no audio"):
        await orchestrator.synthesize_once("chosen_tts", call_id="attx:5", text="Hi", timeout_sec=5)

    assert events[-2:] == [("close", "attx:5"), "stop"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "component_key,providers,match",
    [
        ("missing_tts", None, "unavailable or not configured"),
        ("chosen_tts", {"chosen_tts": {"type": "unsupported", "enabled": False}}, "disabled"),
        ("chosen_llm", None, "not a TTS component"),
        ("bogus", None, "not a TTS component"),
    ],
)
async def test_synthesize_once_rejects_without_building(component_key, providers, match):
    events = []
    orchestrator = _orchestrator(_RecordingTTS, events, providers=providers)

    with pytest.raises(PipelineSynthesisError, match=match):
        await orchestrator.synthesize_once(component_key, call_id="attx:6", text="Hi", timeout_sec=1)

    assert events == []
    assert issubclass(PipelineSynthesisError, PipelineOrchestratorError)


@pytest.mark.asyncio
async def test_synthesize_once_rejects_blank_text():
    events = []
    orchestrator = _orchestrator(_RecordingTTS, events)

    with pytest.raises(PipelineSynthesisError, match="Nothing to synthesize"):
        await orchestrator.synthesize_once("chosen_tts", call_id="attx:7", text="   ", timeout_sec=1)

    assert events == []


@pytest.mark.parametrize(
    "adapter_cls,expected",
    [
        (GoogleTTSAdapter, {"voice": "v1", "language_code": "en-GB"}),
        (OpenAITTSAdapter, {"voice": "v1"}),
        (GroqTTSAdapter, {"voice": "v1"}),
        (ElevenLabsTTSAdapter, {"voice_id": "v1"}),
        (CambAiTTSAdapter, {"voice_id": "v1", "language": "en-GB"}),
        (AzureTTSAdapter, {"voice_name": "v1", "language": "en-GB"}),
        (FishAudioTTSAdapter, {"reference_id": "v1"}),
        (DeepgramTTSAdapter, {"model": "v1", "language": "en-GB"}),
        (LocalTTSAdapter, {"voice": "v1", "language": "en-GB"}),
    ],
)
def test_voice_language_mapping_table(adapter_cls, expected):
    component = adapter_cls.__new__(adapter_cls)
    assert _tts_voice_language_options(
        component, {"voice": "v1", "language": "en-GB", "speaking_rate": 2.0}
    ) == expected


def test_voice_language_mapping_skips_blank_values():
    component = GoogleTTSAdapter.__new__(GoogleTTSAdapter)
    assert _tts_voice_language_options(component, {"voice": "", "language": None}) == {}
    assert _tts_voice_language_options(component, None) == {}


@pytest.mark.asyncio
async def test_synthesize_once_passes_mapped_voice_and_drops_other_keys():
    events = []
    orchestrator = _orchestrator(_RecordingTTS, events)

    await orchestrator.synthesize_once(
        "chosen_tts",
        call_id="attx:8",
        text="Hi",
        options={"voice": "Kore", "language": "en-US", "speaking_rate": 3.0},
        timeout_sec=5,
    )

    seen = _synth_options(events)
    assert seen["voice"] == "Kore"
    assert seen["language"] == "en-US"
    assert "speaking_rate" not in seen


@pytest.mark.asyncio
async def test_shutdown_failure_is_logged_by_class_only(monkeypatch):
    records = []

    class _Capture:
        def _add(self, level, event, **fields):
            records.append({"level": level, "event": event, **fields})

        def debug(self, event, **fields):
            self._add("debug", event, **fields)

        def info(self, event, **fields):
            self._add("info", event, **fields)

        def warning(self, event, **fields):
            self._add("warning", event, **fields)

        def error(self, event, **fields):
            self._add("error", event, **fields)

    monkeypatch.setattr(orchestrator_module, "logger", _Capture())
    events = []
    orchestrator = _orchestrator(_CloseFailsTTS, events)

    audio = await orchestrator.synthesize_once("chosen_tts", call_id="attx:9", text="Hi", timeout_sec=5)

    assert audio
    assert events[-1] == "stop"
    failures = [r for r in records if r.get("error_type") == "RuntimeError"]
    assert failures and failures[0]["step"] == "close_call"
    assert "example.invalid" not in repr(records)
    assert all("exc_info" not in r for r in failures)


@pytest.mark.asyncio
async def test_engine_keeps_audio_when_cleanup_crosses_synthesis_deadline():
    from tests.test_attended_transfer_streaming import _build_engine

    events = []
    closing = asyncio.Event()
    release = asyncio.Event()

    class SlowClose(_RecordingTTS):
        async def close_call(self, call_id):
            closing.set()
            await release.wait()
            await super().close_call(call_id)

    engine = _build_engine({"enabled": True})
    engine.pipeline_orchestrator = _orchestrator(SlowClose, events)
    task = asyncio.create_task(engine._attended_transfer_tts(
        call_id="cleanup-deadline", text="Hello", timeout_sec=.1,
        attended_cfg={"tts_provider": "chosen_tts"},
    ))
    try:
        await asyncio.wait_for(closing.wait(), timeout=1)
        await asyncio.sleep(.15)  # Past the synthesis budget, still in cleanup.
        assert not task.done()
        release.set()
        assert await asyncio.wait_for(task, timeout=1)
        assert events[-1] == "stop"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("hanging_step", ["close_call", "stop"])
async def test_cleanup_deadlines_do_not_skip_stop_or_discard_audio(monkeypatch, hanging_step):
    monkeypatch.setattr(orchestrator_module, "_ONE_SHOT_CLEANUP_STEP_TIMEOUT_SEC", .02)
    events = []

    class HangingCleanup(_RecordingTTS):
        async def close_call(self, call_id):
            await super().close_call(call_id)
            if hanging_step == "close_call":
                await asyncio.Event().wait()

        async def stop(self):
            await super().stop()
            if hanging_step == "stop":
                await asyncio.Event().wait()

    orchestrator = _orchestrator(HangingCleanup, events)
    audio = await asyncio.wait_for(orchestrator.synthesize_once(
        "chosen_tts", call_id="cleanup-timeout", text="Hi", timeout_sec=.1,
    ), timeout=1)
    assert audio
    assert events[-2:] == [("close", "cleanup-timeout"), "stop"]


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_owned_cleanup():
    events = []
    closing = asyncio.Event()
    release = asyncio.Event()

    class SlowClose(_RecordingTTS):
        async def close_call(self, call_id):
            closing.set()
            await release.wait()
            await super().close_call(call_id)

    orchestrator = _orchestrator(SlowClose, events)
    task = asyncio.create_task(orchestrator.synthesize_once(
        "chosen_tts", call_id="cancel-cleanup", text="Hi", timeout_sec=1,
    ))
    try:
        await asyncio.wait_for(closing.wait(), timeout=1)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert events[-2:] == [("close", "cancel-cleanup"), "stop"]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_engine_times_out_real_synthesis_and_closes_adapter():
    from tests.test_attended_transfer_streaming import _build_engine

    events = []
    engine = _build_engine({"enabled": True})
    engine.pipeline_orchestrator = _orchestrator(_HangingTTS, events)
    audio = await asyncio.wait_for(engine._attended_transfer_tts(
        call_id="real-timeout", text="Hi", timeout_sec=.1,
        attended_cfg={"tts_provider": "chosen_tts"},
    ), timeout=1)
    assert audio is None
    assert events[-2:] == [("close", "attended-transfer:real-timeout"), "stop"]
