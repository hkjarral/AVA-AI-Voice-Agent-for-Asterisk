from __future__ import annotations

import asyncio
import importlib
import struct
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


LOCAL_AI_DIR = str(Path(__file__).resolve().parents[1] / "local_ai_server")


def _load(name: str):
    if LOCAL_AI_DIR not in sys.path:
        sys.path.insert(0, LOCAL_AI_DIR)
    return importlib.import_module(name)


class _Backend:
    def __init__(self):
        self.segments: list[bytes] = []

    def transcribe_pcm16(self, audio: bytes) -> str:
        self.segments.append(audio)
        return "complete utterance"


class _HeldBackend(_Backend):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def transcribe_pcm16(self, audio: bytes) -> str:
        self.started.set()
        assert self.release.wait(timeout=2)
        self.segments.append(audio)
        amplitude = abs(struct.unpack_from("<h", audio)[0])
        return f"complete utterance {amplitude}"


def _frame(amplitude: int) -> bytes:
    return struct.pack("<h", amplitude) * 2560  # 160 ms at PCM16/16 kHz


def _server_and_session(server_mod, session_mod):
    instance = object.__new__(server_mod.LocalAIServer)
    backend = _Backend()
    instance.faster_whisper_backend = backend
    instance._faster_whisper_lock = asyncio.Lock()
    instance.config = SimpleNamespace(
        stt_segment_preroll_ms=200,
        stt_segment_energy_threshold=1200,
        stt_segment_min_ms=250,
        stt_segment_silence_ms=500,
        stt_segment_max_ms=12000,
    )
    return instance, session_mod.SessionContext(call_id="call-segment"), backend


@pytest.mark.asyncio
async def test_whisper_segmenter_does_not_duplicate_first_voice_frame(monkeypatch):
    server_mod = _load("server")
    session_mod = _load("session")
    instance, session, backend = _server_and_session(server_mod, session_mod)
    now = 0.0

    def clock():
        return now

    monkeypatch.setattr(server_mod, "monotonic", clock)

    events = []
    for amplitude in [0, 0, 2000, 2000, 0, 0, 0, 0]:
        now += 0.16
        events = await instance._process_stt_stream_whisper_segmented(
            session,
            _frame(amplitude),
            16000,
            backend_name="faster_whisper",
        )

    assert events[0]["text"] == "complete utterance"
    assert len(backend.segments) == 1
    # 200 ms preroll + 320 ms voice + 640 ms trailing silence. The former
    # implementation duplicated the first 160 ms voice frame (1320 ms total).
    assert len(backend.segments[0]) == int(1.16 * 16000 * 2)


@pytest.mark.asyncio
async def test_whisper_segmenter_uses_per_session_silence_override(monkeypatch):
    server_mod = _load("server")
    session_mod = _load("session")
    instance, session, backend = _server_and_session(server_mod, session_mod)
    instance.config.stt_segment_energy_threshold = 3000
    session.stt_segment_energy_threshold = 1000
    session.stt_segment_silence_ms = 900
    now = 0.0

    monkeypatch.setattr(server_mod, "monotonic", lambda: now)

    for amplitude in [0, 0, 2000, 2000, 0, 0, 0, 0, 0]:
        now += 0.16
        events = await instance._process_stt_stream_whisper_segmented(
            session,
            _frame(amplitude),
            16000,
            backend_name="faster_whisper",
        )
    assert events == []
    assert backend.segments == []

    now += 0.16
    events = await instance._process_stt_stream_whisper_segmented(
        session,
        _frame(0),
        16000,
        backend_name="faster_whisper",
    )
    assert events[0]["text"] == "complete utterance"
    assert len(backend.segments) == 1


@pytest.mark.asyncio
async def test_whisper_segmenter_finalizes_speech_burst_without_followup_frames(monkeypatch):
    """A complete caller burst must not require the transport to send silence."""
    server_mod = _load("server")
    session_mod = _load("session")
    instance, session, backend = _server_and_session(server_mod, session_mod)
    now = 0.0
    original_sleep = asyncio.sleep

    monkeypatch.setattr(server_mod, "monotonic", lambda: now)
    instance.stt_backend = "faster_whisper"
    instance.mock_models = False
    instance._ingress_rms_count = 0
    instance.buffer_timeout_ms = 500
    instance._send_json = AsyncMock(return_value=True)

    async def advance_clock(delay: float):
        nonlocal now
        now += delay

    monkeypatch.setattr(server_mod.asyncio, "sleep", advance_clock)

    # Two PCM16/16 kHz frames are 320 ms, above the configured 250 ms minimum;
    # amplitude 2000 is above the configured RMS threshold of 1200.
    for _ in range(2):
        now += 0.16
        await instance._handle_audio_payload(
            object(),
            session,
            {"mode": "stt", "rate": 16000},
            incoming_bytes=_frame(2000),
        )

    # Model the observed transport lifecycle: time passes, but no more audio
    # payload arrives. The per-session idle task must finish the buffered burst.
    idle_task = session.idle_task
    assert idle_task is not None
    await original_sleep(0)
    await idle_task

    assert len(backend.segments) == 1
    assert session.stt_segment_in_speech is False
    assert session.stt_segment_buffer == b""
    instance._send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_whisper_segmenter_finalizes_when_transport_continues_silence(monkeypatch):
    server_mod = _load("server")
    session_mod = _load("session")
    instance, session, backend = _server_and_session(server_mod, session_mod)
    now = 0.0

    monkeypatch.setattr(server_mod, "monotonic", lambda: now)

    events = []
    for amplitude in [2000, 2000, 0, 0, 0, 0]:
        now += 0.16
        events = await instance._process_stt_stream_whisper_segmented(
            session,
            _frame(amplitude),
            16000,
            backend_name="faster_whisper",
        )

    assert events[0]["text"] == "complete utterance"
    assert len(backend.segments) == 1
    assert session.stt_segment_in_speech is False


@pytest.mark.asyncio
async def test_whisper_suppression_discards_speech_instead_of_queueing_it(monkeypatch):
    """Keep explicit TTS echo suppression distinct from missing idle finalization."""
    server_mod = _load("server")
    session_mod = _load("session")
    instance, session, backend = _server_and_session(server_mod, session_mod)
    now = 1.0

    monkeypatch.setattr(server_mod, "monotonic", lambda: now)
    instance.stt_backend = "faster_whisper"
    instance.mock_models = False
    instance._ingress_rms_count = 0
    session.stt_suppress_until = 2.0

    # Both above-threshold speech frames arrive while TTS echo suppression is
    # active. _handle_audio_payload returns before the segmenter sees them.
    for _ in range(2):
        await instance._handle_audio_payload(
            None,
            session,
            {"mode": "stt", "rate": 16000},
            incoming_bytes=_frame(2000),
        )

    assert session.stt_segment_in_speech is False
    assert session.stt_segment_buffer == b""

    # Silence after suppression expiry cannot reconstruct discarded speech.
    now = 2.1
    for _ in range(4):
        now += 0.16
        await instance._handle_audio_payload(
            None,
            session,
            {"mode": "stt", "rate": 16000},
            incoming_bytes=_frame(0),
        )

    assert backend.segments == []


@pytest.mark.asyncio
async def test_whisper_idle_finalizer_is_per_session_and_idempotent(monkeypatch):
    server_mod = _load("server")
    session_mod = _load("session")
    instance, first, backend = _server_and_session(server_mod, session_mod)
    second = session_mod.SessionContext(call_id="call-other")
    now = 1.0

    monkeypatch.setattr(server_mod, "monotonic", lambda: now)
    await instance._process_stt_stream_whisper_segmented(
        first, _frame(2000) + _frame(2000), 16000, backend_name="faster_whisper"
    )
    now += 0.5

    events = await instance._finalize_whisper_segment_if_due(
        first, backend_name="faster_whisper"
    )
    repeated = await instance._finalize_whisper_segment_if_due(
        first, backend_name="faster_whisper"
    )

    assert events[0]["text"] == "complete utterance"
    assert repeated == []
    assert len(backend.segments) == 1
    assert second.stt_segment_buffer == b""
    assert second.stt_segment_in_speech is False


@pytest.mark.asyncio
async def test_whisper_idle_finalizer_does_not_emit_after_teardown(monkeypatch):
    server_mod = _load("server")
    session_mod = _load("session")
    instance, session, backend = _server_and_session(server_mod, session_mod)
    now = 1.0

    monkeypatch.setattr(server_mod, "monotonic", lambda: now)
    instance.stt_backend = "faster_whisper"
    instance.buffer_timeout_ms = 500
    instance._send_json = AsyncMock(return_value=True)

    async def close_during_wait(delay: float):
        nonlocal now
        now += delay
        session.closed = True

    monkeypatch.setattr(server_mod.asyncio, "sleep", close_during_wait)
    await instance._process_stt_stream_whisper_segmented(
        session, _frame(2000) + _frame(2000), 16000, backend_name="faster_whisper"
    )
    instance._schedule_idle_finalizer(object(), session, None, "stt")
    idle_task = session.idle_task
    assert idle_task is not None
    await idle_task
    instance._reset_stt_session(session)

    assert backend.segments == []
    instance._send_json.assert_not_awaited()
    assert session.stt_segment_buffer == b""


@pytest.mark.asyncio
async def test_cancelled_whisper_idle_task_cannot_clear_its_replacement(monkeypatch):
    server_mod = _load("server")
    session_mod = _load("session")
    instance, session, _backend = _server_and_session(server_mod, session_mod)
    original_sleep = asyncio.sleep

    instance.stt_backend = "faster_whisper"
    instance.buffer_timeout_ms = 500
    session.stt_segment_in_speech = True

    async def remain_pending(_delay: float):
        await original_sleep(3600)

    monkeypatch.setattr(server_mod.asyncio, "sleep", remain_pending)

    instance._schedule_idle_finalizer(object(), session, None, "stt")
    replaced = session.idle_task
    assert replaced is not None
    await original_sleep(0)

    # A new voice payload follows this same cancel-and-rearm path.
    instance._schedule_idle_finalizer(object(), session, None, "stt")
    replacement = session.idle_task
    assert replacement is not None and replacement is not replaced
    await original_sleep(0)
    await asyncio.gather(replaced, return_exceptions=True)

    assert session.idle_task is replacement
    assert not replacement.done()

    # TTS suppression and teardown both use this reset owner.
    instance._reset_stt_session(session)
    await asyncio.gather(replacement, return_exceptions=True)
    assert session.idle_task is None


@pytest.mark.asyncio
async def test_new_voice_during_idle_decode_preserves_and_finalizes_both_segments(monkeypatch):
    server_mod = _load("server")
    session_mod = _load("session")
    instance, session, _backend = _server_and_session(server_mod, session_mod)
    backend = _HeldBackend()
    instance.faster_whisper_backend = backend
    instance.stt_backend = "faster_whisper"
    instance.mock_models = False
    instance._ingress_rms_count = 0
    instance.buffer_timeout_ms = 500
    instance._send_json = AsyncMock(return_value=True)
    original_sleep = asyncio.sleep
    second_timer = asyncio.Event()
    sleeps = 0
    now = 1.0

    monkeypatch.setattr(server_mod, "monotonic", lambda: now)

    async def controlled_sleep(delay: float):
        nonlocal now, sleeps
        sleeps += 1
        if sleeps > 1:
            await second_timer.wait()
        now += delay

    monkeypatch.setattr(server_mod.asyncio, "sleep", controlled_sleep)

    await instance._handle_audio_payload(
        object(), session, {"mode": "stt", "rate": 16000},
        incoming_bytes=_frame(2000) + _frame(2000),
    )
    first_idle = session.idle_task
    assert first_idle is not None
    await original_sleep(0)
    assert await asyncio.to_thread(backend.started.wait, 2)

    # The first buffer has been claimed while its decoder is held. New voice
    # starts a distinct segment and owns a replacement idle timer.
    await instance._handle_audio_payload(
        object(), session, {"mode": "stt", "rate": 16000},
        incoming_bytes=_frame(3000) + _frame(3000),
    )
    replacement = session.idle_task
    assert replacement is not None and replacement is not first_idle
    backend.release.set()
    await first_idle

    assert len(backend.segments) == 1
    assert session.stt_segment_in_speech is True
    assert session.stt_segment_buffer == _frame(3000) + _frame(3000)

    second_timer.set()
    await replacement
    assert len(backend.segments) == 2
    assert instance._send_json.await_count == 2
    assert session.stt_segment_in_speech is False


@pytest.mark.asyncio
async def test_tts_suppression_quarantines_claimed_idle_decode(monkeypatch):
    server_mod = _load("server")
    session_mod = _load("session")
    instance, session, _backend = _server_and_session(server_mod, session_mod)
    backend = _HeldBackend()
    instance.faster_whisper_backend = backend
    instance.stt_backend = "faster_whisper"
    instance.buffer_timeout_ms = 500
    instance._send_json = AsyncMock(return_value=True)
    original_sleep = asyncio.sleep
    now = 1.0

    monkeypatch.setattr(server_mod, "monotonic", lambda: now)

    async def advance_clock(delay: float):
        nonlocal now
        now += delay

    monkeypatch.setattr(server_mod.asyncio, "sleep", advance_clock)
    await instance._process_stt_stream_whisper_segmented(
        session, _frame(2000) + _frame(2000), 16000, backend_name="faster_whisper"
    )
    instance._schedule_idle_finalizer(object(), session, None, "stt")
    idle_task = session.idle_task
    assert idle_task is not None
    await original_sleep(0)
    assert await asyncio.to_thread(backend.started.wait, 2)

    instance._arm_whisper_stt_suppression(
        session, b"\x00" * 8000, source="tts"
    )
    backend.release.set()
    await idle_task

    assert len(backend.segments) == 1
    instance._send_json.assert_not_awaited()
    assert session.stt_segment_buffer == b""
    assert session.stt_suppress_until > now


@pytest.mark.asyncio
async def test_new_voice_while_idle_waits_for_cold_backend_lock_preserves_both_segments(
    monkeypatch,
):
    server_mod = _load("server")
    session_mod = _load("session")
    instance, session, _backend = _server_and_session(server_mod, session_mod)
    backend = _HeldBackend()
    backend.release.set()
    instance.faster_whisper_backend = backend
    instance.stt_backend = "faster_whisper"
    instance.mock_models = False
    instance._ingress_rms_count = 0
    instance.buffer_timeout_ms = 500
    instance._send_json = AsyncMock(return_value=True)
    original_sleep = asyncio.sleep
    second_timer = asyncio.Event()
    sleeps = 0
    now = 1.0

    monkeypatch.setattr(server_mod, "monotonic", lambda: now)

    async def controlled_sleep(delay: float):
        nonlocal now, sleeps
        sleeps += 1
        if sleeps > 1:
            await second_timer.wait()
        now += delay

    monkeypatch.setattr(server_mod.asyncio, "sleep", controlled_sleep)
    await instance._faster_whisper_lock.acquire()

    await instance._handle_audio_payload(
        object(), session, {"mode": "stt", "rate": 16000},
        incoming_bytes=_frame(2000) + _frame(2000),
    )
    first_idle = session.idle_task
    assert first_idle is not None
    await original_sleep(0)
    assert session.idle_task is None
    assert session.stt_segment_in_speech is False
    assert not backend.started.is_set()

    await instance._handle_audio_payload(
        object(), session, {"mode": "stt", "rate": 16000},
        incoming_bytes=_frame(3000) + _frame(3000),
    )
    replacement = session.idle_task
    assert replacement is not None and replacement is not first_idle

    instance._faster_whisper_lock.release()
    await first_idle
    assert len(backend.segments) == 1
    assert session.stt_segment_buffer == _frame(3000) + _frame(3000)

    second_timer.set()
    await replacement
    assert len(backend.segments) == 2
    assert instance._send_json.await_count == 2
    assert session.stt_segment_in_speech is False


@pytest.mark.asyncio
async def test_teardown_quarantines_claimed_idle_decode_and_lets_decoder_finish(monkeypatch):
    server_mod = _load("server")
    session_mod = _load("session")
    instance, session, _backend = _server_and_session(server_mod, session_mod)
    backend = _HeldBackend()
    instance.faster_whisper_backend = backend
    instance.stt_backend = "faster_whisper"
    instance.buffer_timeout_ms = 500
    instance._send_json = AsyncMock(return_value=True)
    original_sleep = asyncio.sleep
    now = 1.0

    monkeypatch.setattr(server_mod, "monotonic", lambda: now)

    async def advance_clock(delay: float):
        nonlocal now
        now += delay

    monkeypatch.setattr(server_mod.asyncio, "sleep", advance_clock)
    await instance._process_stt_stream_whisper_segmented(
        session, _frame(2000) + _frame(2000), 16000, backend_name="faster_whisper"
    )
    instance._schedule_idle_finalizer(object(), session, None, "stt")
    idle_task = session.idle_task
    assert idle_task is not None
    await original_sleep(0)
    assert await asyncio.to_thread(backend.started.wait, 2)

    session.closed = True
    instance._reset_stt_session(session)
    backend.release.set()
    await idle_task

    assert len(backend.segments) == 1
    instance._send_json.assert_not_awaited()
    assert session.stt_segment_buffer == b""
    assert session.idle_task is None
