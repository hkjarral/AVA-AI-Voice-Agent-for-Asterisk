from __future__ import annotations

import asyncio
import importlib
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


def test_barge_in_rolls_back_only_latest_interrupted_exchange():
    server_mod = _load("server")
    session_mod = _load("session")
    instance = object.__new__(server_mod.LocalAIServer)
    session = session_mod.SessionContext(
        call_id="rollback-call",
        llm_messages=[
            {"role": "user", "content": "What is Ava?"},
            {"role": "assistant", "content": "Ava is a voice agent."},
            {"role": "user", "content": "Explain every provider."},
            {"role": "assistant", "content": "The first provider is..."},
        ],
        llm_user_turns=["What is Ava?", "Explain every provider."],
    )

    instance._rollback_interrupted_exchange(session)

    assert session.llm_messages == [
        {"role": "user", "content": "What is Ava?"},
        {"role": "assistant", "content": "Ava is a voice agent."},
    ]
    assert session.llm_user_turns == ["What is Ava?"]
    assert session.interruption_pending is True


def test_barge_in_rollback_is_noop_without_latest_assistant():
    server_mod = _load("server")
    session_mod = _load("session")
    instance = object.__new__(server_mod.LocalAIServer)
    session = session_mod.SessionContext(
        call_id="no-assistant",
        llm_messages=[{"role": "user", "content": "new question"}],
        llm_user_turns=["new question"],
    )

    instance._rollback_interrupted_exchange(session)

    assert session.llm_messages == [{"role": "user", "content": "new question"}]
    assert session.llm_user_turns == ["new question"]
    assert session.interruption_pending is True


def test_interrupted_turn_adds_one_shot_latest_utterance_focus():
    server_mod = _load("server")
    session_mod = _load("session")
    instance = object.__new__(server_mod.LocalAIServer)
    instance.llm_system_prompt = "Base prompt"
    instance.llm_voice_preamble = "Voice rules"
    session = session_mod.SessionContext(interruption_pending=True)

    effective = instance._get_effective_system_prompt(session)

    assert "Do not resume" in effective
    assert "newest utterance" in effective


def test_normal_turn_does_not_add_interruption_focus():
    server_mod = _load("server")
    session_mod = _load("session")
    instance = object.__new__(server_mod.LocalAIServer)
    instance.llm_system_prompt = "Base prompt"
    instance.llm_voice_preamble = "Voice rules"
    session = session_mod.SessionContext(interruption_pending=False)

    assert "Do not resume" not in instance._get_effective_system_prompt(session)


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


@pytest.mark.asyncio
async def test_streaming_llm_lock_is_held_until_cancelled_worker_exits():
    server_mod = _load("server")
    instance = object.__new__(server_mod.LocalAIServer)
    instance._llm_lock = asyncio.Lock()
    instance.llm_max_tokens = 32
    instance.llm_stop_tokens = []
    instance.llm_temperature = 0.2
    instance.llm_top_p = 0.9
    instance.llm_repeat_penalty = 1.0
    instance.config = SimpleNamespace(llm_infer_timeout_sec=2.0)
    worker_blocked = threading.Event()
    release_worker = threading.Event()

    class _Model:
        def create_chat_completion(self, **_kwargs):
            yield {"choices": [{"delta": {"content": "first"}}]}
            worker_blocked.set()
            release_worker.wait(timeout=2.0)
            yield {"choices": [{"delta": {"content": "late"}}]}

    instance.llm_model = _Model()
    stream = instance.process_llm_chat_streaming([{"role": "user", "content": "hello"}])
    assert await anext(stream) == "first"
    assert await asyncio.to_thread(worker_blocked.wait, 1.0)

    close_task = asyncio.create_task(stream.aclose())
    await asyncio.sleep(0.05)
    assert instance._llm_lock.locked()
    assert not close_task.done()

    release_worker.set()
    await asyncio.wait_for(close_task, timeout=1.0)
    assert not instance._llm_lock.locked()


@pytest.mark.asyncio
async def test_raw_llm_request_preserves_assistant_turn_in_session_history():
    server_mod = _load("server")
    session_mod = _load("session")
    instance = object.__new__(server_mod.LocalAIServer)
    instance.config = SimpleNamespace(llm_infer_timeout_sec=1.0)
    instance.llm_chat_format = ""
    instance._emit_llm_response = AsyncMock(return_value=True)

    def _prepare(session, text):
        session.llm_messages.append({"role": "user", "content": text})
        session.llm_user_turns = [text]
        return "prompt", 1, False, 1, []

    instance._prepare_llm_prompt = _prepare
    instance.process_llm = AsyncMock(return_value="The answer. <tool>ignored</tool>")
    session = session_mod.SessionContext(call_id="raw-history")

    await instance._handle_llm_request(
        websocket=object(),
        session=session,
        data={"type": "llm_request", "call_id": "raw-history", "text": "Question"},
    )

    assert session.llm_messages[0] == {"role": "user", "content": "Question"}
    assert session.llm_messages[-1]["role"] == "assistant"
    assert "The answer" in session.llm_messages[-1]["content"]
