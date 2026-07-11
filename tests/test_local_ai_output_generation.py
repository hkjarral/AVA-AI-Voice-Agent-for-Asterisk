from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest


LOCAL_AI_DIR = str(Path(__file__).resolve().parents[1] / "local_ai_server")


def _load(name: str):
    if LOCAL_AI_DIR not in sys.path:
        sys.path.insert(0, LOCAL_AI_DIR)
    return importlib.import_module(name)


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_closed_session_drops_llm_and_tts_output():
    server_mod = _load("server")
    session_mod = _load("session")
    instance = object.__new__(server_mod.LocalAIServer)
    session = session_mod.SessionContext(call_id="closed", closed=True, output_generation=3)
    ws = _WebSocket()

    assert await instance._emit_llm_response(
        ws, "late answer", session, "req", source_mode="llm", generation=3
    ) is False
    await instance._emit_tts_audio(
        ws, b"late audio", session, "req", source_mode="full", generation=3
    )
    assert ws.sent == []


@pytest.mark.asyncio
async def test_barge_in_generation_drops_completed_tts_request():
    server_mod = _load("server")
    session_mod = _load("session")
    instance = object.__new__(server_mod.LocalAIServer)
    instance.config = type("Config", (), {})()
    instance.stt_backend = "vosk"
    instance._clear_whisper_stt_suppression = lambda *_args, **_kwargs: None
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_tts(_text):
        started.set()
        await release.wait()
        return b"audio"

    instance.process_tts = slow_tts
    session = session_mod.SessionContext(call_id="call-1", mode="tts")
    ws = _WebSocket()
    task = asyncio.create_task(instance._handle_tts_request(
        ws,
        session,
        {"type": "tts_request", "text": "old answer", "call_id": "call-1"},
    ))
    await started.wait()
    session.output_generation += 1  # same state transition as barge_in
    release.set()
    await task
    assert ws.sent == []


def test_new_generation_invalidates_previous_generation():
    server_mod = _load("server")
    session_mod = _load("session")
    instance = object.__new__(server_mod.LocalAIServer)
    session = session_mod.SessionContext()
    old = instance._start_output_generation(session)
    new = instance._start_output_generation(session)
    assert new > old
    assert instance._output_generation_active(session, old) is False
    assert instance._output_generation_active(session, new) is True


@pytest.mark.asyncio
async def test_session_response_task_can_be_cancelled_without_waiting_for_work():
    server_mod = _load("server")
    session_mod = _load("session")
    instance = object.__new__(server_mod.LocalAIServer)
    session = session_mod.SessionContext(call_id="barge-call")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_response():
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    task = instance._start_session_response_task(
        session, slow_response(), reason="test-response"
    )
    await started.wait()
    instance._cancel_session_response_tasks(session, reason="barge_in")
    await asyncio.wait_for(cancelled.wait(), timeout=0.2)
    assert task.cancelled()
    assert task not in session.response_tasks
