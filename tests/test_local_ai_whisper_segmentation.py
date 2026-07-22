from __future__ import annotations

import asyncio
import importlib
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

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
