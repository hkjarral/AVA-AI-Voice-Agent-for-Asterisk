"""Announcement readiness and admission regressions for #743 and #702."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.transfer_handoff import Announcement
from src.providers.base import AIProviderInterface
from src.providers.handoff import HandoffEvidence
from src.providers.google_live import GoogleLiveProvider
from src.providers.openai_realtime import OpenAIRealtimeProvider
from src.providers.grok import GrokProvider
from src.providers.deepgram import DeepgramProvider
from src.providers.elevenlabs_agent import ElevenLabsAgentProvider
from src.providers.local import LocalProvider
from src.tools.handoff_config import resolve_handoff, validate_handoff_config
from src.tools.telephony.deferred_transfer import build_deferred_transfer_result
from tests.test_deferred_transfer_commit_audio_gate import _build_engine, _armed_session, _agent_audio, _record_stream_starts


MESSAGE = "We are closed. Please leave a message."
CFG = {"message": MESSAGE, "start_timeout_sec": 4, "finish_timeout_sec": 20}
PROVIDERS = [GoogleLiveProvider, OpenAIRealtimeProvider, GrokProvider, DeepgramProvider, ElevenLabsAgentProvider, LocalProvider]


def turn(key, started, *, text="", done=False, interrupted=False):
    return {"id": key, "started_at": started, "text": text, "completed": done, "interrupted": interrupted}


def state(origin=None):
    result = Announcement("a", CFG, "origin", origin or turn("origin", 1), clock=lambda: 10, prepared_at=2)
    result.observe({"type": "HandoffToolResultSent", "success": True, "sent_at": 3})
    return result


def test_filler_completion_does_not_release_but_reply_completion_does():
    result = state()
    result.observe({"handoff_evidence": turn("origin", 1, text="One moment", done=True)})
    assert not result.sealed
    result.observe({"handoff_evidence": turn("reply", 3.5, text=MESSAGE)})
    assert result.selected_id == "reply" and not result.sealed
    result.observe({"handoff_evidence": turn("reply", 3.5, text=MESSAGE, done=True)})
    assert result.sealed and result.reason == "reply_completed"


@pytest.mark.parametrize("text,interrupted,matched", [
    (MESSAGE, False, True), ("WE ARE CLOSED! Please leave a message", False, True),
    (" \n... WE are CLOSED!  Please leave a message.", False, True),
    (MESSAGE + " I will connect you now.", False, True),
    (MESSAGE.rstrip(".") + "box", False, False),
    ("Before that: " + MESSAGE, False, False), ("We are closed", False, False),
    ("Nous sommes fermés", False, False), (MESSAGE, True, False), ("", False, False),
])
def test_conservative_normalized_prefix_matching(text, interrupted, matched):
    result = state(turn("origin", 1, text=text, done=True, interrupted=interrupted))
    assert result.sealed is matched


def test_later_sentence_in_origin_waits_for_post_tool_reply():
    result = state(turn("origin", 1, text="Before that: " + MESSAGE, done=True))
    assert not result.sealed
    result.observe({"handoff_evidence": turn("reply", 3.5, text=MESSAGE)})
    assert not result.sealed and result.selected_id == "reply"
    result.observe({"handoff_evidence": turn("reply", 3.5, text=MESSAGE, done=True)})
    assert result.sealed and result.reason == "reply_completed"


def test_text_without_audio_and_stale_completed_turn_do_not_qualify():
    result = state(turn("origin", None, text=MESSAGE, done=True))
    result.observe({"handoff_evidence": turn("old", 2, text=MESSAGE, done=True)})
    assert not result.sealed and result.selected_id is None


@pytest.mark.parametrize("started,selected", [(6.999, True), (7.001, False)])
def test_start_deadline_boundary(started, selected):
    result = state()
    result.observe({"handoff_evidence": turn("reply", started)})
    assert bool(result.selected_id) is selected


def test_deadlines_are_not_extended_by_duplicate_events():
    result = state()
    assert result.deadline() == 7
    result.observe({"type": "HandoffToolResultSent", "sent_at": 5})
    assert result.deadline() == 7
    result.observe({"handoff_evidence": turn("reply", 4)})
    assert result.deadline() == 24
    result.observe({"handoff_evidence": turn("reply", 6)})
    assert result.deadline() == 24
    result.clock = lambda: 24
    result.expire()
    assert result.reason == "finish_timeout"


async def test_wait_stops_when_action_is_cancelled():
    result = state()
    result.clock = lambda: 3.1
    assert await result.wait(AsyncMock(return_value=False)) is False
    assert result.cancelled


@pytest.mark.parametrize("provider_type", PROVIDERS)
async def test_all_provider_adapters_preserve_default_events_and_correlate_handoff(provider_type):
    # Exercise the real shared callback on every provider implementation.
    provider = provider_type.__new__(provider_type)
    events = []
    async def sink(event):
        events.append(event)
    AIProviderInterface.__init__(provider, sink)
    audio = _agent_audio("call")
    await provider.on_event(audio)
    assert events[-1] == audio  # no event/schema changes with feature disabled
    origin = provider._handoff_evidence.state("call")["current"]
    result = {"deferred_transfer": {"id": "a", "handoff": CFG}}
    await provider.prepare_handoff_result(result, "call", origin)
    await provider.handoff_result_sent(result, "call")
    await provider.handoff_transcript("call", MESSAGE)
    await provider.on_event({"type": "AgentAudioDone", "call_id": "call", "streaming_done": True})
    evidence = events[-1]["handoff_evidence"]
    assert evidence["id"] == origin
    assert evidence["text"] == MESSAGE and evidence["completed"]
    assert events[-1]["handoff_action_id"] == "a"
    provider._handoff_evidence.reset("call")
    assert "call" not in provider._handoff_evidence.calls


@pytest.mark.parametrize("reason", ["audio_idle_fallback", "fallback_no_audio", "duration_estimate", "stop_session", "interruption"])
def test_fallback_and_interrupted_events_are_not_authoritative(reason):
    tracker = HandoffEvidence()
    tracker.event(_agent_audio("c"))
    tracker.prepare("c", {"id": "a"})
    event = tracker.event({"type": "AgentAudioDone", "call_id": "c", "reason": reason})
    assert not event["handoff_evidence"]["completed"]


def test_generation_boundary_does_not_release_and_old_completion_keeps_identity():
    tracker = HandoffEvidence()
    tracker.event({**_agent_audio("c"), "audio_response_id": 1})
    tracker.prepare("c", {"id": "a"}, 1)
    event = tracker.event({"type": "GoogleAudioGenerationComplete", "call_id": "c", "audio_response_id": 1})
    assert not event["handoff_evidence"]["completed"]
    tracker.event({**_agent_audio("c"), "audio_response_id": 2})
    old = tracker.event({"type": "AgentAudioDone", "call_id": "c", "audio_response_id": 1})
    assert old["handoff_evidence"]["id"] == "1"
    assert tracker.state("c")["current"] == "2"


@pytest.mark.parametrize("module,class_name", [("openai", "OpenAIToolAdapter"), ("grok", "GrokToolAdapter"), ("deepgram", "DeepgramToolAdapter")])
@pytest.mark.parametrize("fail_after_output", [False, True])
async def test_native_tool_delivery_boundary_precedes_response_request(module, class_name, fail_after_output):
    import importlib
    adapter = getattr(importlib.import_module("src.tools.adapters." + module), class_name)(None)
    seen = []
    async def send(payload):
        import json
        kind = json.loads(payload)["type"]
        seen.append(kind)
        if kind == "response.create" and fail_after_output:
            raise RuntimeError("response request failed")
    async def sent(result, call_id, *, success):
        assert call_id == "c"
        seen.append("sent" if success else "failed")
    result = {"status": "success", "message": MESSAGE, "deferred_transfer": {"id": "a", "handoff": CFG},
              "call_id": "native-tool", "function_call_id": "native-tool", "function_name": "blind_transfer"}
    await adapter.send_tool_result(result, {"websocket": SimpleNamespace(send=send), "call_id": "c", "handoff_result_sent": sent})
    assert seen[1] == "sent"
    if module != "deepgram":
        assert seen[2] == "response.create"
        assert "failed" not in seen  # response.create failure cannot retract delivery


async def test_engine_delayed_sentence_and_immediate_repeat_race(monkeypatch):
    import src.tools.telephony.deferred_transfer as transfer
    engine = _build_engine()
    session = await _armed_session(engine, "handoff-call")
    action = session.pending_deferred_transfer
    action["handoff"] = CFG
    streams = _record_stream_starts(engine, monkeypatch)
    draining = asyncio.Event()
    release = asyncio.Event()
    async def drain(call_id):
        draining.set()
        await release.wait()
        return True
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", drain)
    monkeypatch.setattr(engine, "_play_deferred_transfer_local_handoff", AsyncMock(return_value=False))
    commit = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(transfer, "commit_pending_deferred_transfer", commit)
    await engine.on_provider_event({"type": "HandoffPrepared", "call_id": session.call_id, "action_id": action["id"],
                                    "origin": turn("origin", 1)})
    await engine.on_provider_event({"type": "HandoffToolResultSent", "call_id": session.call_id, "action_id": action["id"], "sent_at": 3})
    state = engine._transfer_handoffs[session.call_id]
    state.clock = lambda: 3.5
    engine._observe_transfer_handoff(session.call_id, session, {"handoff_action_id": action["id"], "handoff_evidence": turn("origin", 1, text="One moment", done=True)})
    assert not state.sealed and not engine._provider_audio_blocked_by_transfer_commit(session.call_id, session)
    await engine.on_provider_event({**_agent_audio(session.call_id), "handoff_action_id": action["id"], "handoff_evidence": turn("reply", 3.5)})
    assert len(streams) == 1
    # Completion seals synchronously even before the commit task gets CPU time.
    engine._observe_transfer_handoff(session.call_id, session, {"handoff_action_id": action["id"], "handoff_evidence": turn("reply", 3.5, done=True)})
    await engine.on_provider_event(_agent_audio(session.call_id))
    assert len(streams) == 1
    await asyncio.wait_for(draining.wait(), 1)
    assert commit.await_count == 0
    release.set()
    tasks = list(engine._call_bg_tasks.get(session.call_id, ()))
    await asyncio.gather(*tasks)
    assert commit.await_count == 1 and session.call_id not in engine._transfer_handoffs


async def test_replaced_action_unblocks_audio_and_cancels_old_wait(monkeypatch):
    engine = _build_engine()
    session = await _armed_session(engine, "replace")
    session.pending_deferred_transfer["handoff"] = CFG
    await engine.on_provider_event({"type": "HandoffPrepared", "call_id": "replace", "action_id": "action-replace", "origin": turn("old", 1)})
    old = engine._transfer_handoffs["replace"]
    session.pending_deferred_transfer = {"id": "replacement", "kind": "transfer"}
    engine._observe_transfer_handoff("replace", session, {})
    assert old.cancelled and not engine._provider_audio_blocked_by_transfer_commit("replace", session)
    await asyncio.gather(*list(engine._call_bg_tasks.get("replace", ())))


@pytest.mark.parametrize("bad", [{"enabled": "true"}, {"enabled": True}, {"start_timeout_sec": 0}, {"finish_timeout_sec": float("nan")}, {"finish_timeout_sec": True}, {"other": 1}, {"message": "x" * 1001}])
def test_invalid_configuration_rejected(bad):
    with pytest.raises(ValueError):
        validate_handoff_config({"transfer": {"handoff": bad}})


def test_override_precedence_and_provider_result_message():
    global_cfg = {"handoff": {"enabled": True, **CFG}}
    assert resolve_handoff(global_cfg)["message"] == MESSAGE
    assert resolve_handoff(global_cfg, {"handoff": {"mode": "disabled"}}) is None
    assert resolve_handoff({}, {"handoff": {"mode": "custom", "message": "Custom"}})["message"] == "Custom"
    action = {"id": "a", "handoff": CFG}
    result = build_deferred_transfer_result(action=action, message="Generic")
    assert result["message"] == MESSAGE
    action["handoff"] = {**CFG, "owner": "engine"}
    result = build_deferred_transfer_result(action=action, message="Generic")
    assert result["ai_should_speak"] is False


def test_app_config_rejects_invalid_handoff_on_direct_construction():
    engine = _build_engine()
    data = engine.config.model_dump()
    data["tools"]["transfer"]["handoff"] = {"enabled": True, "message": ""}
    with pytest.raises(ValueError, match="message is required"):
        type(engine.config)(**data)


@pytest.mark.parametrize("provider_type,config_type", [(OpenAIRealtimeProvider, "OpenAIRealtimeProviderConfig"), (GrokProvider, "GrokProviderConfig")])
async def test_native_segments_cannot_release_before_whole_response(provider_type, config_type):
    import src.config as config
    provider = provider_type(getattr(config, config_type)(api_key="test"), AsyncMock())
    provider._call_id = "native"
    provider._current_response_id = "r1"
    provider._in_audio_burst = True
    provider._egress_pacer_enabled = False
    await provider.on_event(_agent_audio("native"))
    result = {"deferred_transfer": {"id": "a", "handoff": CFG}}
    await provider.prepare_handoff_result(result, "native", "r1")
    await provider._emit_audio_done()
    assert not provider._handoff_evidence.turn("native", "r1")["completed"]
    await provider.complete_handoff_audio("native", "r1")
    assert provider._handoff_evidence.turn("native", "r1")["completed"]


async def test_elevenlabs_idle_completion_waits_for_authoritative_boundary():
    from src.providers.elevenlabs_config import ElevenLabsAgentConfig
    provider = ElevenLabsAgentProvider(ElevenLabsAgentConfig(agent_id="test"), AsyncMock())
    provider._call_id = "eleven"
    await provider.on_event(_agent_audio("eleven"))
    result = {"deferred_transfer": {"id": "a", "handoff": CFG}}
    await provider.prepare_handoff_result(result, "eleven")
    origin = provider._handoff_evidence.state("eleven")["origin_id"]
    provider._in_audio_burst = True
    await provider._emit_audio_done(reason="audio_idle_fallback")
    assert not provider._handoff_evidence.turn("eleven", origin)["completed"]
    # Audio resumes after an idle gap; it belongs to the still-open response.
    await provider.on_event(_agent_audio("eleven"))
    provider._in_audio_burst = True
    await provider._handle_agent_response_complete({})
    assert provider._handoff_evidence.turn("eleven", origin)["completed"]


async def test_local_blob_completion_is_not_a_duration_timer():
    from src.config import LocalProviderConfig
    provider = LocalProvider(LocalProviderConfig(), AsyncMock())
    provider._active_call_id = "local"
    result = {"deferred_transfer": {"id": "a", "handoff": CFG}}
    await provider.prepare_handoff_result(result, "local")
    await provider._emit_agent_audio("local", b"\x7f" * 160, encoding="mulaw", sample_rate=8000)
    turn_id = provider._handoff_evidence.state("local")["origin_id"]
    completion = next(call.args[0] for call in provider.on_event.await_args_list
                      if call.args[0]["type"] == "HandoffAudioComplete")
    assert completion["handoff_evidence"]["completed"]
    assert completion["handoff_evidence"]["id"] != turn_id
    for task in provider._agent_audio_done_tasks.values():
        task.cancel()
    await asyncio.gather(*provider._agent_audio_done_tasks.values(), return_exceptions=True)


async def test_paced_tail_is_admitted_before_response_readiness():
    from src.config import OpenAIRealtimeProviderConfig
    provider = OpenAIRealtimeProvider(OpenAIRealtimeProviderConfig(api_key="test"), AsyncMock())
    provider._call_id = "paced"
    provider._current_response_id = "r"
    provider._egress_pacer_enabled = True
    provider._outbuf.extend(b"\x7f" * 160)
    await provider.prepare_handoff_result({"deferred_transfer": {"id": "a", "handoff": CFG}}, "paced", "r")
    assert await provider.flush_handoff_pacer()
    assert not provider._outbuf
    audio = [c.args[0] for c in provider.on_event.await_args_list if c.args[0]["type"] == "AgentAudio"]
    assert audio[-1]["data"] == b"\x7f" * 160
    assert not audio[-1]["handoff_evidence"]["completed"]


async def test_store_handoff_preserves_attended_recording_prompt_and_snapshot():
    from src.tools.telephony.deferred_transfer import store_pending_deferred_transfer
    engine = _build_engine()
    session = await _armed_session(engine, "screening")
    config = {"tools": {"transfer": {"handoff": {"enabled": True, **CFG}}}}
    def value(path):
        result = config
        for part in path.split("."):
            result = result.get(part, {})
        return result
    context = SimpleNamespace(get_session=AsyncMock(return_value=session), get_config_value=value,
                              provider_name="google_live", call_id="screening", session_store=engine.session_store)
    action = {"id": "screening-action", "source_tool": "attended_transfer", "payload": {
        "screening_mode": "caller_recording", "caller_screening_prompt": "Please say your name and reason."}}
    await store_pending_deferred_transfer(context, action)
    assert action["handoff"]["message"] == MESSAGE + " Please say your name and reason."
    config["tools"]["transfer"]["handoff"]["message"] = "Changed for future calls"
    assert action["handoff"]["message"].startswith(MESSAGE)


def test_tool_generation_rejects_bad_announcement_before_publish():
    from src.tools.runtime_config import ToolRuntimeGeneration
    with pytest.raises(ValueError, match="message is required"):
        ToolRuntimeGeneration.build(generation_id=2, config={"tools": {"transfer": {"handoff": {"enabled": True}}}})


@pytest.mark.parametrize("long_audio", [False, True])
@pytest.mark.parametrize("origin_said_sentence", [False, True])
async def test_google_native_turn_sequence_preserves_743_and_702(long_audio, origin_said_sentence):
    import base64
    from src.config import GoogleProviderConfig
    coordinator = None
    admitted = []
    async def sink(event):
        nonlocal coordinator
        if event["type"] == "HandoffPrepared":
            origin = event["origin"]
            coordinator = Announcement("a", CFG, origin["id"], dict(origin))
        if coordinator:
            coordinator.observe(event)
        if event["type"] == "AgentAudio" and (not coordinator or not coordinator.sealed):
            admitted.append(event["data"])
    provider = GoogleLiveProvider(GoogleProviderConfig(api_key="test", long_audio_playback_enabled=long_audio), sink)
    provider._call_id = "google-native"
    provider._track_conversation_message = AsyncMock()
    audio = base64.b64encode(b"\x00\x00" * 960).decode()
    await provider._handle_audio_output(audio, mime_type="audio/pcm;rate=24000")
    origin_id = provider._audio_response_id if long_audio else provider._handoff_evidence.state(provider._call_id)["current"]
    result = {"deferred_transfer": {"id": "a", "handoff": CFG}}
    await provider.prepare_handoff_result(result, provider._call_id, origin_id)
    await provider.handoff_result_sent(result, provider._call_id)
    async def complete(text):
        provider._output_transcription_buffer = text
        if long_audio:
            await provider._handle_audio_generation_complete()
            assert not coordinator.sealed
        await provider._handle_server_content({"serverContent": {"turnComplete": True}})
    await complete(MESSAGE if origin_said_sentence else "Let me connect you.")
    assert coordinator.sealed is origin_said_sentence
    before = len(admitted)
    await provider._handle_audio_output(audio, mime_type="audio/pcm;rate=24000")
    if origin_said_sentence:
        assert len(admitted) == before  # #702 repeat never admitted
    else:
        assert len(admitted) == before + 1  # #743 actual announcement is admitted
        await complete(MESSAGE)
        assert coordinator.sealed
        before = len(admitted)
        await provider._handle_audio_output(audio, mime_type="audio/pcm;rate=24000")
        assert len(admitted) == before


def test_engine_owned_handoff_keeps_no_speech_flag_through_sanitizer():
    from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string
    result = {"message": "Engine will announce", "ai_should_speak": False,
              "deferred_transfer": {"id": "a", "handoff": {**CFG, "owner": "engine"}}}
    assert sanitize_tool_result_for_json_string(result)["ai_should_speak"] is False


def test_old_response_cannot_be_adopted_by_replacement_transfer():
    tracker = HandoffEvidence()
    tracker.event({**_agent_audio("c"), "audio_response_id": "old"})
    tracker.prepare("c", {"id": "A"}, "old")
    replacement_origin = tracker.prepare("c", {"id": "B"}, "old")
    assert replacement_origin["id"] != "old"
    late = tracker.event({"type": "AgentAudioDone", "call_id": "c", "audio_response_id": "old"})
    assert late["handoff_action_id"] == "A"
    new = tracker.event({**_agent_audio("c"), "audio_response_id": "new"})
    assert new["handoff_action_id"] == "B"


async def test_cancel_during_local_synthesis_never_starts_old_announcement(monkeypatch):
    engine = _build_engine()
    session = await _armed_session(engine, "cancel-tts")
    session.provider_name = "deepgram"
    session.pending_deferred_transfer["handoff"] = {**CFG, "owner": "engine"}
    entered, release = asyncio.Event(), asyncio.Event()
    async def synth(**kwargs):
        entered.set()
        await release.wait()
        return b"\x7f" * 160
    monkeypatch.setattr(engine, "_local_ai_server_tts", synth)
    playback = AsyncMock()
    monkeypatch.setattr(engine.playback_manager, "play_audio", playback)
    task = asyncio.create_task(engine._play_deferred_transfer_local_handoff(session.call_id, session))
    await entered.wait()
    session.pending_deferred_transfer = None
    release.set()
    assert not await task
    playback.assert_not_awaited()


async def test_disabled_feature_retains_702_gate_before_first_commit_await(monkeypatch):
    engine = _build_engine()
    session = await _armed_session(engine, "legacy-boundary")
    entered, release = asyncio.Event(), asyncio.Event()
    async def save(target):
        entered.set()
        await release.wait()
    monkeypatch.setattr(engine, "_save_session", save)
    monkeypatch.setattr(engine, "_play_deferred_transfer_local_handoff", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_abort_deferred_transfer_after_drain_timeout", AsyncMock())
    task = asyncio.create_task(engine._commit_pending_deferred_transfer_for_call(session.call_id, session))
    await entered.wait()
    assert engine._provider_audio_blocked_by_transfer_commit(session.call_id, session)
    release.set()
    await task


@pytest.mark.parametrize("shape", ["stream", "blob", "failure", "timeout"])
async def test_pipeline_announcement_uses_shared_coordinator_and_exact_sentence(monkeypatch, shape):
    engine = _build_engine()
    session = await _armed_session(engine, "pipeline-handoff")
    action = session.pending_deferred_transfer
    action["handoff"] = {**CFG, "finish_timeout_sec": 0.02}
    seen = []
    async def synth(call_id, text, options):
        seen.append(text)
        if shape == "failure":
            raise RuntimeError("TTS failed")
        if shape == "timeout":
            await asyncio.Event().wait()
        for chunk in ([b"ab", b"cd"] if shape == "stream" else [b"abcd"]):
            yield chunk
    pipeline = SimpleNamespace(tts_adapter=SimpleNamespace(synthesize=synth), tts_options={})
    monkeypatch.setattr(engine, "_pipeline_audio_for_file_playback", lambda options, audio: audio)
    monkeypatch.setattr(engine.playback_manager, "play_audio", AsyncMock(return_value="pid"))
    monkeypatch.setattr(engine.playback_manager, "wait_for_playback_end", AsyncMock(return_value=True))
    result = await engine._speak_pipeline_transfer_announcement(session.call_id, pipeline, MESSAGE, action)
    assert seen == [MESSAGE]
    state = engine._transfer_handoffs[session.call_id]
    assert state.sealed and state.action_id == action["id"]
    assert result is (shape in {"stream", "blob"})
    assert await engine._await_transfer_handoff(session.call_id, action["id"])


async def test_google_watchdog_idle_is_not_announcement_completion():
    import time
    from src.config import GoogleProviderConfig
    provider = GoogleLiveProvider(GoogleProviderConfig(api_key="test"), AsyncMock())
    provider._call_id = "idle-watchdog"
    await provider.on_event(_agent_audio(provider._call_id))
    await provider.prepare_handoff_result({"deferred_transfer": {"id": "a", "handoff": CFG}}, provider._call_id)
    provider._hangup_fallback_armed = True
    provider._hangup_fallback_armed_at = time.monotonic() - 10
    provider._hangup_fallback_audio_started = True
    provider._hangup_fallback_turn_complete_seen = True
    provider._last_audio_out_monotonic = time.monotonic() - 10
    provider._in_audio_burst = True
    provider._flush_pending_user_transcription = AsyncMock()
    await asyncio.wait_for(provider._hangup_fallback_watchdog_loop(), 1)
    done = [c.args[0] for c in provider.on_event.await_args_list if c.args[0]["type"] == "AgentAudioDone"]
    assert len(done) == 1
    assert not done[0]["handoff_evidence"]["completed"]
    assert "_handoff_complete" not in done[0]


@pytest.mark.parametrize("provider_type", PROVIDERS)
async def test_tool_before_any_audio_admits_a_paraphrased_reply(provider_type):
    provider = provider_type.__new__(provider_type)
    events = []
    async def sink(event):
        events.append(event)
    AIProviderInterface.__init__(provider, sink)
    await provider.on_event({"type": "ToolCall", "call_id": "c"})
    result = {"deferred_transfer": {"id": "a", "handoff": CFG}}
    await provider.prepare_handoff_result(result, "c")
    prepared = events[-1]
    announcement = Announcement("a", CFG, prepared["origin"]["id"], prepared["origin"])
    await provider.handoff_result_sent(result, "c")
    announcement.observe(events[-1])
    await provider.on_event(_agent_audio("c"))
    announcement.observe(events[-1])
    await provider.handoff_transcript("c", "Please record your message after the tone.")
    announcement.observe(events[-1])
    await provider.on_event({"type": "AgentAudioDone", "call_id": "c"})
    announcement.observe(events[-1])
    assert announcement.sealed and announcement.reason == "reply_completed"
    assert announcement.selected_id != prepared["origin"]["id"]


@pytest.mark.parametrize("provider_type", PROVIDERS)
@pytest.mark.parametrize("close_fails", [False, True])
async def test_provider_stop_discards_only_its_completed_call_evidence(provider_type, close_fails):
    from src.config import (GoogleProviderConfig, OpenAIRealtimeProviderConfig,
                            GrokProviderConfig, LocalProviderConfig, LLMConfig)
    from src.providers.elevenlabs_config import ElevenLabsAgentConfig
    configs = {GoogleLiveProvider: GoogleProviderConfig(api_key="test"),
               OpenAIRealtimeProvider: OpenAIRealtimeProviderConfig(api_key="test"),
               GrokProvider: GrokProviderConfig(api_key="test"),
               ElevenLabsAgentProvider: ElevenLabsAgentConfig(agent_id="test"),
               LocalProvider: LocalProviderConfig()}
    provider = (DeepgramProvider({}, LLMConfig(), AsyncMock()) if provider_type is DeepgramProvider
                else provider_type(configs[provider_type], AsyncMock()))
    provider._closed = False
    provider._call_id = "ended"
    provider.call_id = "ended"
    provider._active_call_id = "ended"
    provider._handoff_evidence.event(_agent_audio("ended"))
    provider._handoff_evidence.state("other")
    ws = SimpleNamespace(state=SimpleNamespace(name="OPEN"), close=AsyncMock(), send=AsyncMock())
    if close_fails:
        ws.close.side_effect = RuntimeError("test close failure")
        ws.send.side_effect = RuntimeError("test send failure")
    provider.websocket = ws
    provider._ws = ws
    try:
        await provider.stop_session()
    except RuntimeError:
        assert close_fails
    assert "ended" not in provider._handoff_evidence.calls
    assert "other" in provider._handoff_evidence.calls


async def test_grok_native_tool_execution_preserves_private_handoff_until_wire(monkeypatch):
    import json
    from src.config import GrokProviderConfig
    from src.tools.adapters.grok import GrokToolAdapter
    engine = _build_engine()
    session = await _armed_session(engine, "grok-execute")
    action = session.pending_deferred_transfer
    action["handoff"] = CFG
    tool = SimpleNamespace(execute=AsyncMock(return_value={"status": "success", "message": MESSAGE, "deferred_transfer": action}))
    registry = SimpleNamespace(get=lambda name: tool, is_tool_allowed=lambda name, allowed: True)
    events = []
    async def sink(event):
        events.append(event)
    provider = GrokProvider(GrokProviderConfig(api_key="test"), sink)
    provider._call_id = session.call_id
    provider._session_store = engine.session_store
    provider._ari_client = engine.ari_client
    provider._allowed_tools = ["blind_transfer"]
    provider.tool_adapter = GrokToolAdapter(registry)
    payloads = []
    async def send(payload):
        payloads.append(json.loads(payload))
    provider.websocket = SimpleNamespace(state=SimpleNamespace(name="OPEN"), send=send)
    await provider._handle_function_call({"response_id": "origin", "item": {"name": "blind_transfer", "call_id": "tool-id", "arguments": "{}"}})
    prepared = next(e for e in events if e["type"] == "HandoffPrepared")
    sent = next(e for e in events if e["type"] == "HandoffToolResultSent")
    assert prepared["action_id"] == sent["action_id"] == action["id"]
    assert sent["success"] is True
    wire = next(e for e in payloads if e["type"] == "conversation.item.create")
    assert "deferred_transfer" not in json.loads(wire["item"]["output"])
    assert json.loads(wire["item"]["output"])["message"] == MESSAGE
    assert any(e["type"] == "response.create" for e in payloads)


@pytest.mark.parametrize("case,expected", [("missing", False), ("other", False), ("cancelled", False), ("owned", True)])
async def test_handoff_ownership_requires_matching_live_state(case, expected):
    engine = _build_engine()
    session = await _armed_session(engine, "ownership")
    action = session.pending_deferred_transfer
    action["handoff"] = CFG
    if case != "missing":
        state = Announcement("other" if case == "other" else action["id"], CFG)
        if case == "cancelled":
            state.cancel()
        engine._transfer_handoffs[session.call_id] = state
    assert engine._handoff_owns_pending(session.call_id, session) is expected


async def test_missing_preparation_recovers_instead_of_stranding_pending_transfer(monkeypatch):
    engine = _build_engine()
    session = await _armed_session(engine, "missing-prepare")
    action = session.pending_deferred_transfer
    action["handoff"] = {**CFG, "start_timeout_sec": 0.01}
    monkeypatch.setattr(engine, "_stop_deferred_transfer_stream_before_recovery", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_stop_deferred_transfer_playbacks_before_recovery", AsyncMock(return_value=True))
    recovered = AsyncMock(return_value=True)
    monkeypatch.setattr(engine, "_complete_deferred_transfer_timeout_recovery", recovered)
    recorded = AsyncMock()
    monkeypatch.setattr(engine, "_record_deferred_transfer_timeout_tool_result", recorded)
    assert not await asyncio.wait_for(engine._await_transfer_handoff(session.call_id, action["id"]), 1)
    latest = await engine.session_store.get_by_call_id(session.call_id)
    assert latest.pending_deferred_transfer is None
    assert not engine._handoff_owns_pending(session.call_id, latest)
    assert recorded.await_args.kwargs["error_code"] == "deferred_transfer_preparation_failed"
    recovered.assert_awaited_once()


async def test_missing_preparation_wait_accepts_late_event_without_early_recovery(monkeypatch):
    engine = _build_engine()
    session = await _armed_session(engine, "late-prepare")
    action = session.pending_deferred_transfer
    action["handoff"] = {**CFG, "start_timeout_sec": 0.2}
    recover = AsyncMock()
    monkeypatch.setattr(engine, "_recover_failed_deferred_transfer", recover)
    task = asyncio.create_task(engine._await_transfer_handoff(session.call_id, action["id"]))
    await asyncio.sleep(0)
    state = Announcement(action["id"], CFG)
    state.seal("origin_sentence_matched")
    engine._transfer_handoffs[session.call_id] = state
    assert await task
    recover.assert_not_awaited()


async def test_missing_preparation_wait_never_cancels_replacement(monkeypatch):
    engine = _build_engine()
    session = await _armed_session(engine, "missing-replaced")
    action = session.pending_deferred_transfer
    action["handoff"] = {**CFG, "start_timeout_sec": 0.02}
    recover = AsyncMock()
    monkeypatch.setattr(engine, "_recover_failed_deferred_transfer", recover)
    task = asyncio.create_task(engine._await_transfer_handoff(session.call_id, action["id"]))
    await asyncio.sleep(0)
    session.pending_deferred_transfer = {**action, "id": "replacement"}
    assert not await task
    assert session.pending_deferred_transfer["id"] == "replacement"
    recover.assert_not_awaited()


@pytest.mark.parametrize("module,class_name", [("openai", "OpenAIToolAdapter"), ("grok", "GrokToolAdapter"), ("deepgram", "DeepgramToolAdapter")])
async def test_failed_function_output_reports_failure_without_success(module, class_name):
    import importlib
    adapter = getattr(importlib.import_module("src.tools.adapters." + module), class_name)(None)
    notify = AsyncMock()
    ws = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("output failed")))
    result = {"status": "success", "deferred_transfer": {"id": "a", "handoff": CFG},
              "call_id": "native", "function_call_id": "native", "function_name": "blind_transfer"}
    await adapter.send_tool_result(result, {"websocket": ws, "call_id": "c", "handoff_result_sent": notify})
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["success"] is False


@pytest.mark.parametrize("kind", ["pipeline", "engine"])
@pytest.mark.parametrize("cancel,stop_ok", [(False, True), (True, True), (False, False)])
async def test_pipeline_startup_deadline_cleans_exact_playback_or_retains_uncertain_owner(monkeypatch, tmp_path, cancel, stop_ok, kind):
    engine = _build_engine()
    session = await _armed_session(engine, "pipeline-start-stall")
    action = session.pending_deferred_transfer
    action["handoff"] = {**CFG, "finish_timeout_sec": 0.03 if not cancel else 1}
    async def synth(*args):
        yield b"\x7f" * 160
    pipeline = SimpleNamespace(tts_adapter=SimpleNamespace(synthesize=synth), tts_options={})
    monkeypatch.setattr(engine, "_pipeline_audio_for_file_playback", lambda options, audio: audio)
    path = tmp_path / "announcement.ulaw"
    async def create(*args):
        path.write_bytes(b"audio")
        return str(path)
    monkeypatch.setattr(engine.playback_manager, "_create_audio_file", create)
    entered = asyncio.Event()
    started = []
    async def stalled_ari(session, file, playback_id):
        started.append(playback_id)
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(engine.playback_manager, "_play_via_ari", stalled_ari)
    stop = AsyncMock(return_value=True)
    if not stop_ok:
        stop.side_effect = [False, True]
    monkeypatch.setattr(engine.ari_client, "stop_playback", stop)
    if kind == "pipeline":
        operation = engine._speak_pipeline_transfer_announcement(session.call_id, pipeline, MESSAGE, action)
    else:
        session.provider_name = "deepgram"
        monkeypatch.setattr(engine, "_local_ai_server_tts", AsyncMock(return_value=b"audio"))
        operation = engine._play_deferred_transfer_local_handoff(session.call_id, session)
    task = asyncio.create_task(operation)
    await asyncio.wait_for(entered.wait(), 1)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert not await asyncio.wait_for(task, 0.5)
    pid = started[0]
    ref = await engine.session_store.get_playback(pid)
    latest = await engine.session_store.get_by_call_id(session.call_id)
    if stop_ok:
        assert ref is None and pid not in latest.tts_tokens and not path.exists()
    else:
        # An ambiguous remote stop cannot be treated as drained output.
        assert ref is not None and pid in latest.tts_tokens and path.exists()
        session.pending_deferred_transfer = {**action, "id": "replacement"}
        await engine.session_store.set_gating_token(session.call_id, "replacement-token")
        await engine.playback_manager._gating_fallback_task(session.call_id, pid, 0)
        assert await engine.session_store.get_playback(pid) is not None
    await asyncio.gather(*list(engine._call_bg_tasks.get(session.call_id, ())))
    assert await engine.session_store.get_playback(pid) is None
    assert not path.exists()
    if not stop_ok:
        latest = await engine.session_store.get_by_call_id(session.call_id)
        assert latest.pending_deferred_transfer["id"] == "replacement"
        assert "replacement-token" in latest.tts_tokens and pid not in latest.tts_tokens
        await engine.session_store.clear_gating_token(session.call_id, "replacement-token")
    if kind == "pipeline":
        state = engine._transfer_handoffs[session.call_id]
        assert state.cancelled if cancel else state.sealed


@pytest.mark.parametrize("local_setting,local_enabled", [
    (True, True), (False, False), (None, False), (["deepgram"], True),
    ([" DeepGram "], True), ("DeepGram, google_live", True), ([], False), ("", False), (42, True),
])
async def test_deepgram_announcement_selects_one_speech_owner(monkeypatch, local_setting, local_enabled):
    from src.tools.telephony.deferred_transfer import store_pending_deferred_transfer
    engine = _build_engine()
    session = await _armed_session(engine, "deepgram-owner")
    session.provider_name = "deepgram"
    action = session.pending_deferred_transfer
    transfer = {"handoff": {"enabled": True, **CFG}, "local_handoff_audio_providers": local_setting}
    monkeypatch.setattr(engine, "_tool_config_for_session", lambda target: {"tools": {"transfer": transfer}})
    values = {"tools.transfer": transfer, "providers": {"deepgram": {"type": "deepgram"}}}
    context = SimpleNamespace(call_id=session.call_id, provider_name="deepgram", get_session=AsyncMock(return_value=session),
                              get_config_value=lambda key: values.get(key), session_store=engine.session_store)
    await store_pending_deferred_transfer(context, action)
    result = build_deferred_transfer_result(action=action, message="legacy")
    assert action["handoff"]["owner"] == ("engine" if local_enabled else "provider")
    if local_enabled:
        assert result["ai_should_speak"] is False
        # Engine ownership seals before observing any provider speech.
        commit = AsyncMock()
        monkeypatch.setattr(engine, "_commit_pending_deferred_transfer_for_call", commit)
        engine._observe_transfer_handoff(session.call_id, session, {"type": "HandoffPrepared", "action_id": action["id"]})
        assert engine._transfer_handoffs[session.call_id].reason == "engine_announcement"
        await asyncio.gather(*list(engine._call_bg_tasks.get(session.call_id, ())))
    else:
        assert result.get("ai_should_speak", True)
        synth = AsyncMock()
        monkeypatch.setattr(engine, "_local_ai_server_tts", synth)
        assert not await engine._play_deferred_transfer_local_handoff(session.call_id, session)
        synth.assert_not_awaited()


async def test_cancelled_audio_file_write_cleans_up_after_worker_finishes(monkeypatch, tmp_path):
    import src.core.playback_manager as playback_module
    engine = _build_engine()
    manager = engine.playback_manager
    manager.media_dir = str(tmp_path)
    entered, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def worker(write):
        entered.set()
        await release.wait()
        write()
    monkeypatch.setattr(playback_module.asyncio, "to_thread", worker)
    original_cleanup = manager._cleanup_audio_file
    async def cleanup(path):
        await original_cleanup(path)
        cleaned.set()
    monkeypatch.setattr(manager, "_cleanup_audio_file", cleanup)
    task = asyncio.create_task(manager._create_audio_file(b"audio", "cancel-write"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await asyncio.wait_for(cleaned.wait(), 1)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["pipeline", "engine"])
async def test_superseded_action_stops_only_its_late_startup_playback(monkeypatch, kind):
    engine = _build_engine()
    session = await _armed_session(engine, "startup-replaced")
    action = session.pending_deferred_transfer
    action["handoff"] = CFG
    async def synth(*args):
        yield b"audio"
    pipeline = SimpleNamespace(tts_adapter=SimpleNamespace(synthesize=synth), tts_options={})
    monkeypatch.setattr(engine, "_pipeline_audio_for_file_playback", lambda options, audio: audio)
    async def startup(*args):
        session.pending_deferred_transfer = {**action, "id": "replacement"}
        return "old-playback"
    monkeypatch.setattr(engine.playback_manager, "play_audio", startup)
    wait = AsyncMock()
    monkeypatch.setattr(engine.playback_manager, "wait_for_playback_end", wait)
    stop = AsyncMock(return_value=True)
    monkeypatch.setattr(engine, "_stop_transfer_announcement_playback", stop)
    if kind == "pipeline":
        assert not await engine._speak_pipeline_transfer_announcement(session.call_id, pipeline, MESSAGE, action)
    else:
        session.provider_name = "deepgram"
        monkeypatch.setattr(engine, "_local_ai_server_tts", AsyncMock(return_value=b"audio"))
        assert not await engine._play_deferred_transfer_local_handoff(session.call_id, session)
    stop.assert_awaited_once_with(session.call_id, "old-playback")
    wait.assert_not_awaited()
    assert session.pending_deferred_transfer["id"] == "replacement"


@pytest.mark.parametrize("provider_kind", ["google_live", "openai_realtime", "grok", "deepgram", "elevenlabs_agent", "local"])
async def test_action_and_playback_agree_on_legacy_provider_list_normalization(monkeypatch, provider_kind):
    from src.tools.telephony.deferred_transfer import store_pending_deferred_transfer
    engine = _build_engine()
    session = await _armed_session(engine, "normalized-owner")
    session.provider_name = provider_kind
    transfer = {"handoff": {"enabled": True, **CFG}, "local_handoff_audio_providers": " " + provider_kind.upper() + ", other "}
    values = {"tools.transfer": transfer, "providers": {provider_kind: {"type": provider_kind}}}
    monkeypatch.setattr(engine, "_tool_config_for_session", lambda target: {"tools": {"transfer": transfer}})
    context = SimpleNamespace(call_id=session.call_id, provider_name=provider_kind, get_session=AsyncMock(return_value=session),
                              get_config_value=lambda key: values.get(key), session_store=engine.session_store)
    action = session.pending_deferred_transfer
    await store_pending_deferred_transfer(context, action)
    assert provider_kind in engine._deferred_transfer_local_handoff_providers(session)
    assert action["handoff"]["owner"] == "engine"


@pytest.mark.parametrize("raises", [False, True])
async def test_cancelled_playback_retry_exhaustion_uses_terminal_cleanup_without_false_finish(monkeypatch, tmp_path, raises):
    import src.core.playback_manager as playback_module
    from src.core.models import PlaybackRef
    engine = _build_engine()
    session = await _armed_session(engine, "retry-exhausted-" + str(raises))
    session.pending_deferred_transfer = None
    other = await _armed_session(engine, "retry-other-" + str(raises))
    path, other_path = tmp_path / "old.ulaw", tmp_path / "other.ulaw"
    path.write_bytes(b"audio")
    other_path.write_bytes(b"other")
    for target, pid, file in [(session, "old-playback", path), (other, "other-playback", other_path)]:
        await engine.session_store.add_playback(PlaybackRef(pid, target.call_id, target.caller_channel_id, None, "sound:test", str(file)))
        await engine.session_store.set_gating_token(target.call_id, pid)
    monkeypatch.setattr(playback_module, "_CANCEL_RETRY_DELAY", 0.001)
    stop = AsyncMock(side_effect=RuntimeError("ARI failed")) if raises else AsyncMock(return_value=False)
    monkeypatch.setattr(engine.ari_client, "stop_playback", stop)
    hangup = AsyncMock(return_value=True)
    monkeypatch.setattr(engine.ari_client, "hangup_channel", hangup)
    monkeypatch.setattr(engine, "_persist_call_history", AsyncMock())
    monkeypatch.setattr(engine, "_execute_post_call_tools", AsyncMock())
    finish = AsyncMock()
    monkeypatch.setattr(engine.playback_manager, "on_playback_finished", finish)
    cleanup = AsyncMock(wraps=engine._cleanup_call)
    monkeypatch.setattr(engine, "_cleanup_call", cleanup)
    await engine.playback_manager.retain_cancelled_playback(session.call_id, "old-playback")
    await asyncio.wait_for(asyncio.gather(*list(engine._call_bg_tasks.get(session.call_id, ()))), 2)
    assert stop.await_count == playback_module._CANCEL_STOP_ATTEMPTS
    cleanup.assert_awaited_once_with(session.call_id)
    finish.assert_not_awaited()
    assert await engine.session_store.get_by_call_id(session.call_id) is None
    assert await engine.session_store.get_playback("old-playback") is None
    assert not session.tts_tokens and not session.audio_capture_enabled and not path.exists()
    assert await engine.session_store.get_playback("other-playback") is not None
    assert "other-playback" in other.tts_tokens and other_path.exists()
    assert other.pending_deferred_transfer is not None
    hangup.assert_awaited_once_with(session.caller_channel_id)
    await engine.playback_manager.discard_terminal_playbacks(other.call_id)
