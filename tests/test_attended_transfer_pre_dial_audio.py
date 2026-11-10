import asyncio

import pytest

from src.core.models import CallSession
from src.tools.telephony.attended_transfer import AttendedTransferTool
from tests.test_attended_transfer_streaming import _build_engine
from tests.test_attended_transfer_tool import _FakeAriClient, _FakeContext
from tests.test_attended_transfer_tts_provider import _FakeOrchestrator, _install

CFG = {
    "enabled": True,
    "tts_provider": "google_tts",
    "announcement_template": "Transfer for {caller_display}.",
    "agent_accept_prompt_template": "Press 1 to accept.",
}


def _tool_config(attended_cfg):
    return {
        "asterisk": {"app_name": "asterisk-ai-voice-agent"},
        "tools": {
            "attended_transfer": attended_cfg,
            "transfer": {
                "defer_until_playback_complete": False,
                "technology": "SIP",
                "destinations": {
                    "support_agent": {
                        "type": "extension",
                        "target": "6000",
                        "description": "Support agent",
                        "attended_allowed": True,
                    }
                },
            },
        },
    }


class _PrepEngine:
    def __init__(self, events, *, ok=True, error=None):
        self.events = events
        self.ok = ok
        self.error = error
        self.calls = []
        self.recovered = []

    async def prepare_attended_transfer_audio(self, **kwargs):
        self.events.append("prepare")
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.ok

    async def recover_attended_transfer_failure(self, call_id, attempt, *, reason):
        self.recovered.append(reason)


def _tool_fixture(attended_cfg, engine, call_id):
    events = engine.events if engine is not None else []
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    ari = _FakeAriClient(originate_response={"id": "agent-chan"}, engine=engine)
    original_send = ari.send_command

    async def recording_send(**kwargs):
        if kwargs["method"] == "POST" and kwargs["resource"] == "channels":
            events.append("originate")
        elif kwargs["resource"].endswith("/moh"):
            events.append("moh_start" if kwargs["method"] == "POST" else "moh_stop")
        return await original_send(**kwargs)

    ari.send_command = recording_send
    context = _FakeContext(config=_tool_config(attended_cfg), caller_channel_id=call_id, ari_client=ari, session=session)
    return session, ari, context


def _posted(ari, resource_suffix):
    return [c for c in ari.calls if c["method"] == "POST" and c["resource"].endswith(resource_suffix)]


# ---- tool side -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_kwargs", [{"ok": False}, {"error": RuntimeError("tts down")}])
async def test_pre_dial_failure_never_originates(engine_kwargs):
    engine = _PrepEngine([], **engine_kwargs)
    session, ari, context = _tool_fixture(CFG, engine, "1760000000.1001")

    result = await AttendedTransferTool().execute({"destination": "support_agent"}, context)

    assert result == {
        "status": "failed",
        "message": "Support agent could not be reached right now. Offer to take a message instead.",
    }
    # Hold music covers the synthesis wait and is stopped again; no leg is dialled.
    assert engine.events == ["moh_start", "prepare", "moh_stop"]
    assert not _posted(ari, "channels")
    assert session.current_action is None
    assert engine.calls[0]["destination_description"] == "Support agent"
    assert engine.calls[0]["attended_cfg"]["tts_provider"] == "google_tts"


@pytest.mark.asyncio
async def test_pre_dial_success_synthesizes_before_originate():
    engine = _PrepEngine([])
    session, ari, context = _tool_fixture(CFG, engine, "1760000000.1002")

    result = await AttendedTransferTool().execute({"destination": "support_agent"}, context)

    assert result["status"] == "success"
    assert engine.events == ["moh_start", "prepare", "originate"]
    assert len(_posted(ari, "/moh")) == 1
    assert engine.calls[0]["attempt"] is session.current_action


@pytest.mark.asyncio
async def test_unset_provider_skips_pre_dial_synthesis():
    engine = _PrepEngine([])
    session, ari, context = _tool_fixture({"enabled": True}, engine, "1760000000.1003")

    result = await AttendedTransferTool().execute({"destination": "support_agent"}, context)

    assert result["status"] == "success"
    assert engine.calls == []
    assert engine.events == ["moh_start", "originate"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ok", [True, False])
async def test_caller_recording_prepares_before_originate(ok):
    engine = _PrepEngine([], ok=ok)
    cfg = dict(CFG, screening_mode="caller_recording")
    session, ari, context = _tool_fixture(cfg, engine, "1760000000.1004")
    attempt = {"id": "attempt", "type": "attended_transfer", "screening_mode": "caller_recording"}
    session.current_action = attempt

    await AttendedTransferTool()._complete_caller_recording_transfer(
        context=context, attempt=attempt, destination="support_agent", extension="6000",
        description="Support agent", dial_endpoint="SIP/6000", dial_timeout_sec=30, moh_class="default",
        screening_max_seconds=6, screening_silence_ms=1200,
    )

    assert engine.calls[0]["attempt"] is attempt
    if ok:
        assert engine.events == ["moh_start", "prepare", "originate"]
        assert len(_posted(ari, "/moh")) == 1
        assert engine.recovered == []
    else:
        # The engine's recovery path stops hold music; no leg is dialled.
        assert engine.events == ["moh_start", "prepare"]
        assert engine.recovered == ["tts-unavailable"]


# ---- engine side -----------------------------------------------------------


async def _engine_session(cfg, call_id="call-pre", action=None):
    engine = _build_engine(cfg)
    session = CallSession(call_id=call_id, caller_channel_id=f"caller-{call_id}", caller_name="Bob",
                          caller_number="15551230000", context_name="support")
    session.current_action = action if action is not None else {"type": "attended_transfer"}
    await engine.session_store.upsert_call(session)
    return engine, session


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra,payload,expected",
    [
        ({}, None, {"announcement": "Transfer for Bob.", "prompt": "Press 1 to accept."}),
        (
            {"screening_mode": "caller_recording", "announcement_template": ""},
            {"kind": "caller_recording", "audio_ulaw": b"\xff" * 80, "duration_ms": 10},
            {"announcement": "Hi, this is Ava. Here is the caller's screening.", "prompt": "Press 1 to accept."},
        ),
        ({"screening_mode": "ai_briefing"}, None, {"prompt": "Press 1 to accept."}),
    ],
)
async def test_prepare_stores_expected_audio(monkeypatch, extra, payload, expected):
    cfg = dict(CFG, **extra)
    action = {"type": "attended_transfer"}
    if payload:
        action["screening_payload"] = payload
    engine, session = await _engine_session(cfg, action=action)
    orchestrator = _FakeOrchestrator()
    local_calls = _install(monkeypatch, engine, orchestrator)

    ok = await engine.prepare_attended_transfer_audio(
        call_id="call-pre", destination_description="Support agent", attended_cfg=cfg
    )

    assert ok is True
    assert local_calls == []
    assert orchestrator.texts == list(expected.values())
    stored = session.current_action["_pre_audio"]
    assert {k: v["text"] for k, v in stored.items()} == expected
    assert all(v["provider"] == "google_tts" for v in stored.values())
    assert stored["prompt"]["audio"] == b"AUDIO:Press 1 to accept."


@pytest.mark.asyncio
async def test_prepare_failure_stores_nothing(monkeypatch):
    engine, session = await _engine_session(CFG)
    _install(monkeypatch, engine, _FakeOrchestrator(error=RuntimeError("down")))

    ok = await engine.prepare_attended_transfer_audio(
        call_id="call-pre", destination_description="Support agent", attended_cfg=CFG
    )

    assert ok is False
    assert "_pre_audio" not in session.current_action


@pytest.mark.asyncio
async def test_prepare_unset_provider_is_a_no_op(monkeypatch):
    engine, session = await _engine_session({"enabled": True})
    orchestrator = _FakeOrchestrator()
    local_calls = _install(monkeypatch, engine, orchestrator)

    ok = await engine.prepare_attended_transfer_audio(
        call_id="call-pre", destination_description="Support agent", attended_cfg={"enabled": True}
    )

    assert ok is True
    assert orchestrator.calls == [] and local_calls == []
    assert "_pre_audio" not in session.current_action


@pytest.mark.asyncio
async def test_prepare_rejects_stale_attempt(monkeypatch):
    engine, session = await _engine_session(CFG)
    orchestrator = _FakeOrchestrator()
    _install(monkeypatch, engine, orchestrator)

    ok = await engine.prepare_attended_transfer_audio(
        call_id="call-pre", destination_description="Support agent", attended_cfg=CFG,
        attempt={"type": "attended_transfer", "id": "older"},
    )

    assert ok is False
    assert orchestrator.calls == []


def _patch_answer_path(monkeypatch, engine, played):
    async def fake_play(*, channel_id, audio_bytes, playback_id_prefix, timeout_sec):
        played.append((playback_id_prefix, audio_bytes))
        return f"{playback_id_prefix}-ok"

    async def fake_wait_dtmf(agent_channel_id, *, timeout_sec):
        return "1"

    async def fake_finalize(session_obj, **kwargs):
        return None

    monkeypatch.setattr(engine, "_play_ulaw_bytes_on_channel_and_wait", fake_play)
    monkeypatch.setattr(engine, "_wait_for_attended_transfer_dtmf", fake_wait_dtmf)
    monkeypatch.setattr(engine, "_attended_transfer_finalize_bridge", fake_finalize)


@pytest.mark.asyncio
async def test_answer_plays_pre_dial_audio_without_second_synthesis(monkeypatch):
    engine, session = await _engine_session(CFG)
    orchestrator = _FakeOrchestrator()
    local_calls = _install(monkeypatch, engine, orchestrator)
    assert await engine.prepare_attended_transfer_audio(
        call_id="call-pre", destination_description="Support agent", attended_cfg=CFG
    )
    played = []
    _patch_answer_path(monkeypatch, engine, played)

    await engine._handle_attended_transfer_answered("agent-pre", ["attended-transfer", "call-pre", "support_agent"])

    assert len(orchestrator.calls) == 2
    assert local_calls == []
    assert played == [("attx-ann", b"AUDIO:Transfer for Bob."), ("attx-prompt", b"AUDIO:Press 1 to accept.")]


@pytest.mark.asyncio
async def test_answer_ignores_pre_dial_audio_for_different_text(monkeypatch):
    engine, session = await _engine_session(CFG)
    orchestrator = _FakeOrchestrator()
    _install(monkeypatch, engine, orchestrator)
    session.current_action["_pre_audio"] = {
        "announcement": {"provider": "google_tts", "text": "Old text.", "audio": b"stale"},
    }
    played = []
    _patch_answer_path(monkeypatch, engine, played)

    await engine._handle_attended_transfer_answered("agent-pre", ["attended-transfer", "call-pre", "support_agent"])

    assert orchestrator.texts == ["Transfer for Bob.", "Press 1 to accept."]
    assert ("attx-ann", b"stale") not in played


class _BarrierOrchestrator(_FakeOrchestrator):
    """Each call waits until every expected call has started."""

    def __init__(self, expected):
        super().__init__()
        self._expected = expected
        self._arrived = 0
        self._all_arrived = asyncio.Event()

    async def synthesize_once(self, component_key, *, call_id, text, options=None, timeout_sec):
        self._arrived += 1
        if self._arrived >= self._expected:
            self._all_arrived.set()
        await self._all_arrived.wait()
        return await super().synthesize_once(
            component_key, call_id=call_id, text=text, options=options, timeout_sec=timeout_sec
        )


@pytest.mark.asyncio
async def test_prepare_synthesizes_texts_concurrently(monkeypatch):
    # With a 1 s timeout, sequential synthesis would time out on the first text.
    cfg = dict(CFG, tts_timeout_seconds=1)
    engine, session = await _engine_session(cfg)
    orchestrator = _BarrierOrchestrator(expected=2)
    _install(monkeypatch, engine, orchestrator)

    ok = await engine.prepare_attended_transfer_audio(
        call_id="call-pre", destination_description="Support agent", attended_cfg=cfg
    )

    assert ok is True
    assert sorted(orchestrator.texts) == ["Press 1 to accept.", "Transfer for Bob."]
    assert set(session.current_action["_pre_audio"]) == {"announcement", "prompt"}


@pytest.mark.asyncio
async def test_ai_briefing_fallback_reuses_prompt_and_synthesizes_announcement(monkeypatch):
    cfg = dict(CFG, screening_mode="ai_briefing")
    engine, session = await _engine_session(cfg)
    orchestrator = _FakeOrchestrator()
    _install(monkeypatch, engine, orchestrator)
    assert await engine.prepare_attended_transfer_audio(
        call_id="call-pre", destination_description="Support agent", attended_cfg=cfg
    )
    assert orchestrator.texts == ["Press 1 to accept."]

    async def no_briefing(**kwargs):
        return None

    monkeypatch.setattr(engine, "_generate_attended_transfer_briefing_text", no_briefing)
    played = []
    _patch_answer_path(monkeypatch, engine, played)

    await engine._handle_attended_transfer_answered("agent-pre", ["attended-transfer", "call-pre", "support_agent"])

    # Briefing unavailable: basic_tts announcement synthesized now, prompt reused.
    assert orchestrator.texts == ["Press 1 to accept.", "Transfer for Bob."]
    assert played == [("attx-ann", b"AUDIO:Transfer for Bob."), ("attx-prompt", b"AUDIO:Press 1 to accept.")]


@pytest.mark.asyncio
async def test_answer_ignores_pre_dial_audio_from_another_provider(monkeypatch):
    engine, session = await _engine_session(CFG)
    orchestrator = _FakeOrchestrator()
    _install(monkeypatch, engine, orchestrator)
    session.current_action["_pre_audio"] = {
        "announcement": {"provider": "other_tts", "text": "Transfer for Bob.", "audio": b"other-ann"},
        "prompt": {"provider": "other_tts", "text": "Press 1 to accept.", "audio": b"other-prompt"},
    }
    played = []
    _patch_answer_path(monkeypatch, engine, played)

    await engine._handle_attended_transfer_answered("agent-pre", ["attended-transfer", "call-pre", "support_agent"])

    assert orchestrator.texts == ["Transfer for Bob.", "Press 1 to accept."]
    assert played == [("attx-ann", b"AUDIO:Transfer for Bob."), ("attx-prompt", b"AUDIO:Press 1 to accept.")]


@pytest.mark.asyncio
async def test_answer_uses_per_call_provider_when_global_differs(monkeypatch):
    # The agent's tool_configs set the provider; the global YAML has none.
    global_cfg = {k: v for k, v in CFG.items() if k != "tts_provider"}
    engine, session = await _engine_session(global_cfg)
    session.tool_runtime_config = {"tools": {"attended_transfer": dict(CFG)}}
    orchestrator = _FakeOrchestrator()
    local_calls = _install(monkeypatch, engine, orchestrator)
    assert await engine.prepare_attended_transfer_audio(
        call_id="call-pre", destination_description="Support agent", attended_cfg=dict(CFG)
    )
    played = []
    _patch_answer_path(monkeypatch, engine, played)

    await engine._handle_attended_transfer_answered("agent-pre", ["attended-transfer", "call-pre", "support_agent"])

    # Pre-dial audio is played as prepared: no second synthesis, no Local AI Server.
    assert len(orchestrator.calls) == 2
    assert local_calls == []
    assert played == [("attx-ann", b"AUDIO:Transfer for Bob."), ("attx-prompt", b"AUDIO:Press 1 to accept.")]


class _EndingOrchestrator(_FakeOrchestrator):
    """The call ends while its audio is being synthesized."""

    def __init__(self, engine, call_id):
        super().__init__()
        self._engine = engine
        self._call_id = call_id

    async def synthesize_once(self, component_key, *, call_id, text, options=None, timeout_sec):
        await self._engine.session_store.remove_call(self._call_id)
        return await super().synthesize_once(
            component_key, call_id=call_id, text=text, options=options, timeout_sec=timeout_sec
        )


@pytest.mark.asyncio
async def test_prepare_does_not_reinsert_a_finished_call(monkeypatch):
    engine, session = await _engine_session(CFG)
    _install(monkeypatch, engine, _EndingOrchestrator(engine, "call-pre"))

    ok = await engine.prepare_attended_transfer_audio(
        call_id="call-pre", destination_description="Support agent", attended_cfg=CFG
    )

    assert ok is False
    assert await engine.session_store.get_by_call_id("call-pre") is None


@pytest.mark.asyncio
async def test_pre_dial_disables_caller_capture_before_hold_music():
    events = []
    session_holder = {}

    class _CaptureEngine(_PrepEngine):
        async def prepare_attended_transfer_audio(self, **kwargs):
            events.append(("prepare", session_holder["s"].audio_capture_enabled))
            return await super().prepare_attended_transfer_audio(**kwargs)

    engine = _CaptureEngine(events)
    session, ari, context = _tool_fixture(CFG, engine, "1760000000.1005")
    session_holder["s"] = session
    session.audio_capture_enabled = True
    original_send = ari.send_command

    async def capture_send(**kwargs):
        if kwargs["resource"].endswith("/moh") and kwargs["method"] == "POST":
            events.append(("moh_capture", session.audio_capture_enabled))
        return await original_send(**kwargs)

    ari.send_command = capture_send

    result = await AttendedTransferTool().execute({"destination": "support_agent"}, context)

    assert result["status"] == "success"
    # Capture is already off when hold music starts and during synthesis.
    observed = [e for e in events if isinstance(e, tuple)]
    assert observed == [("moh_capture", False), ("prepare", False)]
    assert session.audio_capture_enabled is False
    assert session.current_action["pre_transfer_audio_capture_enabled"] is True


@pytest.mark.asyncio
async def test_pre_dial_failure_restores_caller_capture():
    engine = _PrepEngine([], ok=False)
    session, ari, context = _tool_fixture(CFG, engine, "1760000000.1006")
    session.audio_capture_enabled = True

    result = await AttendedTransferTool().execute({"destination": "support_agent"}, context)

    assert result["status"] == "failed"
    assert session.current_action is None
    assert session.audio_capture_enabled is True
