import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import src.engine as engine_module
from src.core.models import CallSession
from src.pipelines.orchestrator import PipelineSynthesisError
from tests.test_attended_transfer_streaming import _build_engine

ENGINE_SOURCE = Path(__file__).resolve().parents[1] / "src" / "engine.py"
PROVIDER_CFG = {"enabled": True, "tts_provider": "google_tts", "tts_options": {"voice": "Kore"}}


class _LogCapture:
    def __init__(self):
        self.records = []

    def _add(self, level, event, **fields):
        self.records.append({"level": level, "event": event, **fields})

    def debug(self, event, **fields):
        self._add("debug", event, **fields)

    def info(self, event, **fields):
        self._add("info", event, **fields)

    def warning(self, event, **fields):
        self._add("warning", event, **fields)

    def error(self, event, **fields):
        self._add("error", event, **fields)


class _FakeOrchestrator:
    def __init__(self, *, error=None, hang=False):
        self.calls = []
        self._error = error
        self._hang = hang

    async def synthesize_once(self, component_key, *, call_id, text, options=None, timeout_sec):
        self.calls.append(
            {"component_key": component_key, "call_id": call_id, "text": text,
             "options": options, "timeout_sec": timeout_sec}
        )
        if self._hang:
            await asyncio.sleep(10)
        if self._error is not None:
            raise self._error
        return ("AUDIO:" + text).encode()

    @property
    def texts(self):
        return [c["text"] for c in self.calls]


def _install(monkeypatch, engine, orchestrator):
    local_calls = []

    async def fake_local(*, call_id, text, timeout_sec):
        local_calls.append({"call_id": call_id, "text": text, "timeout_sec": timeout_sec})
        return b"local-audio"

    monkeypatch.setattr(engine, "_local_ai_server_tts", fake_local)
    engine.pipeline_orchestrator = orchestrator
    return local_calls


def _functions_calling(attr_name):
    tree = ast.parse(ENGINE_SOURCE.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == attr_name
                ):
                    found.add(node.name)
    return found


@pytest.mark.asyncio
@pytest.mark.parametrize("attended_cfg", [{"enabled": True}, {"enabled": True, "tts_provider": "  "}, None])
async def test_unset_provider_uses_local_ai_server_with_same_arguments(monkeypatch, attended_cfg):
    engine = _build_engine({"enabled": True})
    orchestrator = _FakeOrchestrator()
    local_calls = _install(monkeypatch, engine, orchestrator)

    audio = await engine._attended_transfer_tts(
        call_id="call-1", text="Hello", timeout_sec=8.0, attended_cfg=attended_cfg
    )

    assert audio == b"local-audio"
    assert local_calls == [{"call_id": "call-1", "text": "Hello", "timeout_sec": 8.0}]
    assert orchestrator.calls == []


@pytest.mark.asyncio
async def test_set_provider_uses_synthesize_once_and_never_local(monkeypatch):
    engine = _build_engine(PROVIDER_CFG)
    orchestrator = _FakeOrchestrator()
    local_calls = _install(monkeypatch, engine, orchestrator)

    audio = await engine._attended_transfer_tts(
        call_id="call-2", text="Hello", timeout_sec=8.0, attended_cfg=PROVIDER_CFG
    )

    assert audio == b"AUDIO:Hello"
    assert local_calls == []
    assert orchestrator.calls == [
        {"component_key": "google_tts", "call_id": "attended-transfer:call-2", "text": "Hello",
         "options": {"voice": "Kore"}, "timeout_sec": 8.0}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "orchestrator,expected_type",
    [
        (_FakeOrchestrator(error=RuntimeError("401 for https://key:sk-secret@tts.example.invalid")), "RuntimeError"),
        (_FakeOrchestrator(error=PipelineSynthesisError("google_tts returned no audio")), "PipelineSynthesisError"),
        (_FakeOrchestrator(error=asyncio.TimeoutError()), "TimeoutError"),
        (None, "OrchestratorUnavailable"),
    ],
)
async def test_provider_failure_returns_none_without_local_fallback(monkeypatch, orchestrator, expected_type):
    logs = _LogCapture()
    monkeypatch.setattr(engine_module, "logger", logs)
    engine = _build_engine(PROVIDER_CFG)
    local_calls = _install(monkeypatch, engine, orchestrator)

    audio = await engine._attended_transfer_tts(
        call_id="call-3", text="Hello", timeout_sec=0.05, attended_cfg=PROVIDER_CFG
    )

    assert audio is None
    assert local_calls == []
    failures = [r for r in logs.records if r["event"] == "attended_transfer_tts_failed"]
    assert failures == [
        {"level": "warning", "event": "attended_transfer_tts_failed", "call_id": "call-3",
         "provider": "google_tts", "error_type": expected_type}
    ]
    assert "sk-secret" not in repr(logs.records)


def test_local_ai_server_tts_called_only_by_dispatcher_and_blind_handoff():
    assert _functions_calling("_local_ai_server_tts") == {
        "_attended_transfer_tts",
        "_play_deferred_transfer_local_handoff",
    }
    assert {
        "_handle_attended_transfer_answered",
        "_attended_transfer_abort_and_resume",
        "_attended_transfer_finalize_bridge",
    } <= _functions_calling("_attended_transfer_tts")


@pytest.mark.asyncio
async def test_answer_handler_uses_provider_for_announcement_and_prompt(monkeypatch):
    cfg = dict(PROVIDER_CFG, announcement_template="Transfer for {caller_display}.",
               agent_accept_prompt_template="Press 1 to accept.")
    engine = _build_engine(cfg)
    orchestrator = _FakeOrchestrator()
    local_calls = _install(monkeypatch, engine, orchestrator)
    session = CallSession(call_id="call-ans", caller_channel_id="caller-ans", caller_name="Bob",
                          caller_number="15551230000", context_name="support")
    session.current_action = {"type": "attended_transfer"}
    await engine.session_store.upsert_call(session)
    played, finalize_calls = [], []

    async def fake_play(*, channel_id, audio_bytes, playback_id_prefix, timeout_sec):
        played.append((playback_id_prefix, audio_bytes))
        return f"{playback_id_prefix}-ok"

    async def fake_wait_dtmf(agent_channel_id, *, timeout_sec):
        return "1"

    async def fake_finalize(session_obj, **kwargs):
        finalize_calls.append(kwargs)

    monkeypatch.setattr(engine, "_play_ulaw_bytes_on_channel_and_wait", fake_play)
    monkeypatch.setattr(engine, "_wait_for_attended_transfer_dtmf", fake_wait_dtmf)
    monkeypatch.setattr(engine, "_attended_transfer_finalize_bridge", fake_finalize)

    await engine._handle_attended_transfer_answered("agent-ans", ["attended-transfer", "call-ans", "support_agent"])

    assert local_calls == []
    assert orchestrator.texts == ["Transfer for Bob.", "Press 1 to accept."]
    assert played == [("attx-ann", b"AUDIO:Transfer for Bob."), ("attx-prompt", b"AUDIO:Press 1 to accept.")]
    assert finalize_calls and finalize_calls[0]["attended_cfg"]["tts_provider"] == "google_tts"


@pytest.mark.asyncio
async def test_announcement_failure_with_provider_aborts_without_local(monkeypatch):
    engine = _build_engine(PROVIDER_CFG)
    local_calls = _install(monkeypatch, engine, _FakeOrchestrator(error=RuntimeError("boom")))
    session = CallSession(call_id="call-fail", caller_channel_id="caller-fail")
    session.current_action = {"type": "attended_transfer"}
    await engine.session_store.upsert_call(session)
    aborts = []

    async def fake_abort(session_obj, agent_channel_id, *, reason):
        aborts.append(reason)

    monkeypatch.setattr(engine, "_attended_transfer_abort_and_resume", fake_abort)

    await engine._handle_attended_transfer_answered("agent-fail", ["attended-transfer", "call-fail", "support_agent"])

    assert aborts == ["tts-unavailable"]
    assert local_calls == []


@pytest.mark.asyncio
async def test_declined_prompt_uses_provider(monkeypatch):
    cfg = dict(PROVIDER_CFG, caller_declined_prompt="Not available right now.")
    engine = _build_engine(cfg)
    orchestrator = _FakeOrchestrator()
    local_calls = _install(monkeypatch, engine, orchestrator)
    session = CallSession(call_id="call-dec", caller_channel_id="caller-dec")
    session.current_action = {"type": "attended_transfer", "agent_channel_id": "agent-dec"}
    await engine.session_store.upsert_call(session)
    engine.register_attended_transfer_agent_channel(session.call_id, "agent-dec")
    played = []

    async def fake_play(*, channel_id, audio_bytes, playback_id_prefix, timeout_sec):
        played.append((channel_id, playback_id_prefix, audio_bytes))
        return "ok"

    monkeypatch.setattr(engine, "_play_ulaw_bytes_on_channel_and_wait", fake_play)
    monkeypatch.setattr(engine.ari_client, "hangup_channel", AsyncMock())
    monkeypatch.setattr(engine.ari_client, "send_command", AsyncMock())

    await engine._attended_transfer_abort_and_resume(session, "agent-dec", reason="declined")

    assert local_calls == []
    assert orchestrator.texts == ["Not available right now."]
    assert played == [("caller-dec", "attx-decline", b"AUDIO:Not available right now.")]


@pytest.mark.asyncio
async def test_connected_prompt_uses_provider(monkeypatch):
    engine = _build_engine(PROVIDER_CFG)
    orchestrator = _FakeOrchestrator()
    local_calls = _install(monkeypatch, engine, orchestrator)
    session = CallSession(call_id="call-con", caller_channel_id="caller-con", bridge_id="bridge-con")
    session.current_action = {"type": "attended_transfer", "agent_channel_id": "agent-con"}
    await engine.session_store.upsert_call(session)
    engine.register_attended_transfer_agent_channel(session.call_id, "agent-con")
    played = []

    async def fake_play(*, channel_id, audio_bytes, playback_id_prefix, timeout_sec):
        played.append((channel_id, playback_id_prefix, audio_bytes))
        return "ok"

    monkeypatch.setattr(engine, "_play_ulaw_bytes_on_channel_and_wait", fake_play)
    monkeypatch.setattr(engine, "_cleanup_attended_transfer_helper_media", AsyncMock())
    monkeypatch.setattr(engine, "_remove_primary_media_from_bridge", AsyncMock())
    monkeypatch.setattr(engine.ari_client, "send_command", AsyncMock())
    monkeypatch.setattr(engine.ari_client, "add_channel_to_bridge", AsyncMock())

    await engine._attended_transfer_finalize_bridge(
        session,
        agent_channel_id="agent-con",
        destination_description="Support agent",
        caller_connected_prompt="Connecting you now.",
        tts_timeout=8.0,
        attended_cfg=PROVIDER_CFG,
    )

    assert local_calls == []
    assert orchestrator.texts == ["Connecting you now."]
    assert played == [("caller-con", "attx-caller", b"AUDIO:Connecting you now.")]
