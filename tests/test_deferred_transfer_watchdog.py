"""Silent-reply watchdog for deferred transfers.

A deferred transfer commits on the agent's next AgentAudioDone. A provider that
sends no audio after the tool result emits none, so the watchdog commits through
the same path after tools.transfer.deferred_silent_commit_timeout_sec.
"""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

import src.engine as engine_module
import src.tools.telephony.deferred_transfer as deferred_transfer
from src.providers.base import AIProviderInterface
from src.providers.google_live import GoogleLiveProvider
from tests.test_deferred_transfer_commit_audio_gate import (
    _agent_audio,
    _armed_session,
    _build_engine,
    _record_stream_starts,
)

TIMEOUT = 0.05


def _action(action_id, commit_tool):
    return {
        "id": action_id,
        "kind": "transfer",
        "source_tool": commit_tool,
        "commit_tool": commit_tool,
        "transfer_type": "attended_transfer" if commit_tool == "attended_transfer" else "extension",
        "target": "6000",
        "description": "Support agent",
    }


async def _engine_with_pending(call_id, commit_tool="blind_transfer", timeout=TIMEOUT):
    engine = _build_engine()
    if timeout is not None:
        engine.config.tools["transfer"]["deferred_silent_commit_timeout_sec"] = timeout
    session = await _armed_session(engine, call_id)
    session.pending_deferred_transfer = _action(f"action-{call_id}", commit_tool)
    await engine.session_store.upsert_call(session)
    return engine, session


def _fake_commit(engine, monkeypatch, *, drain_delay=0.0):
    """Keep the real commit wrapper and lock; stub only the drain and the tool commit."""
    commits = []

    async def drain(call_id):
        await asyncio.sleep(drain_delay)
        return True

    async def commit(context):
        session = await context.session_store.get_by_call_id(context.call_id)
        commits.append(session.pending_deferred_transfer["id"])
        session.pending_deferred_transfer = None
        await context.session_store.upsert_call(session)
        return {"status": "success"}

    monkeypatch.setattr(engine, "_play_deferred_transfer_local_handoff", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", drain)
    monkeypatch.setattr(deferred_transfer, "commit_pending_deferred_transfer", commit)
    return commits


def _armed(call_id, action_id):
    return {"type": "DeferredTransferArmed", "call_id": call_id, "action_id": action_id}


def _done(call_id):
    return {"type": "AgentAudioDone", "call_id": call_id, "streaming_done": True}


def _watchdog_logs(log):
    return [c for c in log.warning.call_args_list if c.args and c.args[0] == "deferred_transfer_watchdog_commit"]


async def _settle(engine, call_id, seconds=TIMEOUT * 4):
    await asyncio.sleep(seconds)
    pending = [t for t in engine._call_bg_tasks.get(call_id, set()) if not t.done()]
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=2)


@pytest.mark.parametrize("commit_tool", ["attended_transfer", "blind_transfer"])
async def test_silent_reply_commits_after_timeout(monkeypatch, commit_tool):
    call_id = f"call-silent-{commit_tool}"
    engine, session = await _engine_with_pending(call_id, commit_tool)
    commits = _fake_commit(engine, monkeypatch)
    log = Mock()
    monkeypatch.setattr(engine_module, "logger", log)
    action_id = session.pending_deferred_transfer["id"]

    await engine.on_provider_event(_armed(call_id, action_id))
    assert engine._deferred_transfer_watchdogs[call_id]["action_id"] == action_id
    await asyncio.sleep(TIMEOUT / 2)
    assert commits == []
    await _settle(engine, call_id)

    assert commits == [action_id]
    assert call_id not in engine._deferred_transfer_watchdogs
    fired = _watchdog_logs(log)
    assert len(fired) == 1
    assert fired[0].kwargs["call_id"] == call_id
    assert fired[0].kwargs["action_id"] == action_id


@pytest.mark.parametrize(
    "commit_tool,transfer_type",
    [("attended_transfer", "attended_transfer"), ("blind_transfer", "extension")],
)
async def test_provider_tool_result_arms_the_watchdog(monkeypatch, commit_tool, transfer_type):
    call_id = f"call-provider-{commit_tool}"
    engine, session = await _engine_with_pending(call_id, commit_tool)
    action = deferred_transfer.build_deferred_transfer_action(
        source_tool=commit_tool, commit_tool=commit_tool, transfer_type=transfer_type,
        target="6000", description="Support agent",
    )
    session.pending_deferred_transfer = dict(action)
    await engine.session_store.upsert_call(session)
    commits = _fake_commit(engine, monkeypatch)
    provider = GoogleLiveProvider.__new__(GoogleLiveProvider)
    AIProviderInterface.__init__(provider, engine.on_provider_event)
    result = deferred_transfer.build_deferred_transfer_result(action=action, message="Please hold.")

    await provider.prepare_handoff_result(result, call_id)
    assert call_id not in engine._deferred_transfer_watchdogs  # Not sent yet.
    await provider.handoff_result_sent(result, call_id)
    await _settle(engine, call_id)

    assert commits == [action["id"]]


async def test_only_the_sent_result_reports_unannounced_transfers():
    events = []

    async def sink(event):
        events.append(event)

    provider = GoogleLiveProvider.__new__(GoogleLiveProvider)
    AIProviderInterface.__init__(provider, sink)
    transfer = {"deferred_transfer": {"id": "a", "kind": "transfer"}}
    for hook in (provider.prepare_handoff_result, provider.handoff_result_sent):
        await hook({"status": "success", "message": "ok"}, "c")
        await hook("not a dict", "c")
    await provider.prepare_handoff_result(transfer, "c")
    assert events == []
    await provider.handoff_result_sent(transfer, "c")
    assert events == [{"type": "DeferredTransferArmed", "call_id": "c", "action_id": "a", "success": True}]
    await provider.handoff_result_sent(transfer, "d", success=False)
    assert events[-1] == {"type": "DeferredTransferArmed", "call_id": "d", "action_id": "a", "success": False}


async def test_reply_audio_holds_the_watchdog(monkeypatch):
    call_id = "call-audio"
    engine, session = await _engine_with_pending(call_id)
    commits = _fake_commit(engine, monkeypatch)
    _record_stream_starts(engine, monkeypatch)
    action_id = session.pending_deferred_transfer["id"]

    await engine.on_provider_event(_armed(call_id, action_id))
    await engine.on_provider_event(_agent_audio(call_id))
    await _settle(engine, call_id)

    assert commits == []
    entry = engine._deferred_transfer_watchdogs[call_id]
    assert entry["state"] == "agent_speaking"
    assert entry["task"] is None


async def test_agent_audio_done_first_commits_once(monkeypatch):
    call_id = "call-done-first"
    engine, session = await _engine_with_pending(call_id)
    commits = _fake_commit(engine, monkeypatch)
    log = Mock()
    monkeypatch.setattr(engine_module, "logger", log)
    action_id = session.pending_deferred_transfer["id"]

    await engine.on_provider_event(_armed(call_id, action_id))
    await engine.on_provider_event(_done(call_id))
    await _settle(engine, call_id)

    assert commits == [action_id]
    assert _watchdog_logs(log) == []
    assert call_id not in engine._deferred_transfer_watchdogs


async def test_late_agent_audio_done_during_watchdog_commit_is_a_no_op(monkeypatch):
    call_id = "call-late-done"
    engine, session = await _engine_with_pending(call_id)
    commits = _fake_commit(engine, monkeypatch, drain_delay=TIMEOUT * 4)
    action_id = session.pending_deferred_transfer["id"]

    await engine.on_provider_event(_armed(call_id, action_id))
    await asyncio.sleep(TIMEOUT * 2)  # watchdog fired; its commit is draining
    assert call_id not in engine._deferred_transfer_watchdogs
    await engine.on_provider_event(_done(call_id))
    await _settle(engine, call_id, seconds=TIMEOUT * 8)

    assert commits == [action_id]


@pytest.mark.parametrize("cause", ["cancel_transfer", "overflow_recovery"])
async def test_cleared_action_is_never_committed(monkeypatch, cause):
    # cancel_transfer (cancel_transfer.py:137) and _recover_failed_deferred_transfer
    # (engine.py 22808, reached by #811's overflow path) both clear the action.
    call_id = f"call-cleared-{cause}"
    engine, session = await _engine_with_pending(call_id)
    commits = _fake_commit(engine, monkeypatch)
    log = Mock()
    monkeypatch.setattr(engine_module, "logger", log)

    await engine.on_provider_event(_armed(call_id, session.pending_deferred_transfer["id"]))
    session.pending_deferred_transfer = None
    await engine.session_store.upsert_call(session)
    await _settle(engine, call_id)

    assert commits == []
    assert _watchdog_logs(log) == []
    assert call_id not in engine._deferred_transfer_watchdogs


async def test_call_cleanup_cancels_the_watchdog(monkeypatch):
    call_id = "call-hangup"
    engine, session = await _engine_with_pending(call_id, timeout=1.0)
    commits = _fake_commit(engine, monkeypatch)
    engine.ari_client.hangup_channel = AsyncMock(return_value=True)
    engine.ari_client.destroy_bridge = AsyncMock(return_value=True)

    await engine.on_provider_event(_armed(call_id, session.pending_deferred_transfer["id"]))
    task = engine._deferred_transfer_watchdogs[call_id]["task"]
    assert task is not None
    await engine._cleanup_call(call_id)
    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert call_id not in engine._deferred_transfer_watchdogs
    assert commits == []


async def test_zero_timeout_disables_the_watchdog(monkeypatch):
    call_id = "call-zero"
    engine, session = await _engine_with_pending(call_id, timeout=0)
    commits = _fake_commit(engine, monkeypatch)

    await engine.on_provider_event(_armed(call_id, session.pending_deferred_transfer["id"]))

    assert call_id not in engine._deferred_transfer_watchdogs
    assert not any(t.get_name().startswith("deferred-transfer-watchdog-")
                   for t in engine._call_bg_tasks.get(call_id, set()))
    await _settle(engine, call_id)
    assert commits == []


async def test_handoff_action_keeps_its_own_timer(monkeypatch):
    call_id = "call-handoff"
    engine, session = await _engine_with_pending(call_id)
    session.pending_deferred_transfer["handoff"] = {"message": "Connecting you.", "start_timeout_sec": 4}
    await engine.session_store.upsert_call(session)

    await engine.on_provider_event(_armed(call_id, session.pending_deferred_transfer["id"]))

    assert call_id not in engine._deferred_transfer_watchdogs


async def test_rearm_same_action_keeps_one_watchdog_and_new_action_replaces_it(monkeypatch):
    call_id = "call-rearm"
    engine, session = await _engine_with_pending(call_id, timeout=1.0)
    first_id = session.pending_deferred_transfer["id"]

    await engine.on_provider_event(_armed(call_id, first_id))
    first = engine._deferred_transfer_watchdogs[call_id]["task"]
    await engine.on_provider_event(_armed(call_id, first_id))  # suppressed duplicate request
    assert engine._deferred_transfer_watchdogs[call_id]["task"] is first

    session.pending_deferred_transfer = _action("action-replacement", "blind_transfer")
    await engine.session_store.upsert_call(session)
    await engine.on_provider_event(_armed(call_id, "action-replacement"))
    await asyncio.gather(first, return_exceptions=True)

    assert first.cancelled()
    assert engine._deferred_transfer_watchdogs[call_id]["action_id"] == "action-replacement"
    engine._deferred_transfer_watchdogs[call_id]["task"].cancel()


async def test_stale_result_preserves_current_watchdog(monkeypatch):
    call_id = "call-stale-result"
    engine, session = await _engine_with_pending(call_id)
    commits = _fake_commit(engine, monkeypatch)
    action_id = session.pending_deferred_transfer["id"]
    await engine.on_provider_event(_armed(call_id, action_id))
    task = engine._deferred_transfer_watchdogs[call_id]["task"]

    await engine.on_provider_event(_armed(call_id, "old-replaced-action"))

    assert engine._deferred_transfer_watchdogs[call_id]["task"] is task
    await asyncio.wait_for(task, timeout=2)
    assert commits == [action_id]


async def test_timeout_default_and_bounds():
    engine = _build_engine()
    session = await _armed_session(engine, "call-bounds")
    assert engine._deferred_transfer_silent_commit_timeout(session) == 4.0
    for raw, expected in ((99, 30.0), (-1, 0.0), ("bad", 4.0), (float("nan"), 4.0), (2.5, 2.5)):
        engine.config.tools["transfer"]["deferred_silent_commit_timeout_sec"] = raw
        assert engine._deferred_transfer_silent_commit_timeout(session) == expected


def _speech(call_id, etype, **extra):
    return {"type": etype, "call_id": call_id, **extra}


async def test_caller_speech_holds_the_timer_until_it_ends(monkeypatch):
    call_id = "call-caller-vad"
    engine, session = await _engine_with_pending(call_id)
    commits = _fake_commit(engine, monkeypatch)
    action_id = session.pending_deferred_transfer["id"]

    await engine.on_provider_event(_armed(call_id, action_id))
    await engine.on_provider_event(_speech(call_id, "CallerSpeechStarted", vad=True))
    await asyncio.sleep(TIMEOUT * 4)
    assert commits == []  # Provider VAD holds the reply; the caller is talking.

    await engine.on_provider_event(_speech(call_id, "CallerSpeechStopped", vad=True))
    await asyncio.sleep(TIMEOUT / 2)
    assert commits == []  # The full timeout counts from the end of speech.
    await _settle(engine, call_id)

    assert commits == [action_id]


async def test_caller_speech_without_an_end_is_bounded_by_the_backstop(monkeypatch):
    call_id = "call-caller-backstop"
    engine, session = await _engine_with_pending(call_id)
    commits = _fake_commit(engine, monkeypatch)
    monkeypatch.setattr(type(engine), "_DEFERRED_TRANSFER_CALLER_SPEECH_BACKSTOP_SEC", TIMEOUT * 4)
    action_id = session.pending_deferred_transfer["id"]

    await engine.on_provider_event(_armed(call_id, action_id))
    await engine.on_provider_event(_speech(call_id, "CallerSpeechStarted", vad=True))
    await asyncio.sleep(TIMEOUT * 2)
    assert commits == []
    await _settle(engine, call_id, seconds=TIMEOUT * 4)

    assert commits == [action_id]


async def test_caller_activity_without_edges_restarts_the_timer(monkeypatch):
    # Google Live reports input transcription fragments, not VAD edges.
    call_id = "call-caller-fragments"
    engine, session = await _engine_with_pending(call_id)
    commits = _fake_commit(engine, monkeypatch)
    action_id = session.pending_deferred_transfer["id"]

    await engine.on_provider_event(_armed(call_id, action_id))
    for _ in range(6):
        await asyncio.sleep(TIMEOUT / 2)
        await engine.on_provider_event(_speech(call_id, "CallerSpeechStarted"))
    assert commits == []
    await _settle(engine, call_id)

    assert commits == [action_id]


@pytest.mark.parametrize("caller_vad", [False, True])
async def test_barge_in_that_cuts_the_reply_resumes_the_timer(monkeypatch, caller_vad):
    call_id = f"call-barge-in-{caller_vad}"
    engine, session = await _engine_with_pending(call_id)
    commits = _fake_commit(engine, monkeypatch)
    _record_stream_starts(engine, monkeypatch)
    action_id = session.pending_deferred_transfer["id"]

    await engine.on_provider_event(_armed(call_id, action_id))
    await engine.on_provider_event(_agent_audio(call_id))
    if caller_vad:
        await engine.on_provider_event(_speech(call_id, "CallerSpeechStarted", vad=True))
    await engine.on_provider_event(_speech(call_id, "ProviderBargeIn", event="interrupted"))
    if caller_vad:
        await asyncio.sleep(TIMEOUT * 3)
        assert commits == []
        await engine.on_provider_event(_speech(call_id, "CallerSpeechStopped", vad=True))
    await _settle(engine, call_id)

    assert commits == [action_id]


async def test_late_caller_transcript_does_not_resume_the_timer_over_a_reply(monkeypatch):
    call_id = "call-late-transcript"
    engine, session = await _engine_with_pending(call_id)
    commits = _fake_commit(engine, monkeypatch)
    _record_stream_starts(engine, monkeypatch)
    action_id = session.pending_deferred_transfer["id"]

    await engine.on_provider_event(_armed(call_id, action_id))
    await engine.on_provider_event(_agent_audio(call_id))
    await engine.on_provider_event(
        _speech(call_id, "ConversationText", role="user", text="sales please")
    )
    await engine.on_provider_event(_speech(call_id, "CallerSpeechStarted"))
    await _settle(engine, call_id)

    assert commits == []
    assert engine._deferred_transfer_watchdogs[call_id]["state"] == "agent_speaking"


async def test_rearm_during_the_watchdog_commit_starts_no_second_watchdog(monkeypatch):
    call_id = "call-rearm-in-flight"
    engine, session = await _engine_with_pending(call_id)
    commits = _fake_commit(engine, monkeypatch, drain_delay=TIMEOUT * 4)
    action_id = session.pending_deferred_transfer["id"]

    await engine.on_provider_event(_armed(call_id, action_id))
    await asyncio.sleep(TIMEOUT * 2)  # Fired; its commit is draining.
    assert engine._deferred_transfer_commit_in_flight(call_id, action_id)
    await engine.on_provider_event(_armed(call_id, action_id))

    assert call_id not in engine._deferred_transfer_watchdogs
    await _settle(engine, call_id, seconds=TIMEOUT * 8)
    assert commits == [action_id]
    assert not engine._deferred_transfer_commit_in_flight(call_id, action_id)


async def test_rearm_during_an_agent_audio_done_commit_starts_no_watchdog(monkeypatch):
    call_id = "call-rearm-done-commit"
    engine, session = await _engine_with_pending(call_id)
    commits = _fake_commit(engine, monkeypatch, drain_delay=TIMEOUT * 4)
    action_id = session.pending_deferred_transfer["id"]

    commit = asyncio.create_task(
        engine._commit_pending_deferred_transfer_for_call(call_id, session, expected_action_id=action_id)
    )
    await asyncio.sleep(TIMEOUT)
    await engine.on_provider_event(_armed(call_id, action_id))
    assert call_id not in engine._deferred_transfer_watchdogs
    await commit

    assert commits == [action_id]


class _OrderedWebSocket:
    state = type("State", (), {"name": "OPEN"})()

    def __init__(self, order):
        self.order = order

    async def send(self, payload):
        import json

        data = json.loads(payload)
        item = data.get("item") or {}
        self.order.append(("ws", item.get("type") or data.get("type")))


class _DeferredResultAdapter:
    def __init__(self, real, action):
        self.real = real
        self.action = action

    async def handle_tool_call_event(self, event, _context):
        return {
            "call_id": event["item"]["call_id"],
            "function_name": event["item"]["name"],
            "status": "success",
            "message": "Transferring you now.",
            "deferred_transfer": dict(self.action),
        }

    async def send_tool_result(self, result, context):
        await self.real.send_tool_result(result, context)


def _realtime_provider(kind, on_event):
    from src.config import GrokProviderConfig, OpenAIRealtimeProviderConfig
    from src.providers.grok import GrokProvider
    from src.providers.openai_realtime import OpenAIRealtimeProvider
    from src.tools.adapters.grok import GrokToolAdapter
    from src.tools.adapters.openai import OpenAIToolAdapter
    from src.tools.registry import ToolRegistry

    if kind == "openai":
        provider = OpenAIRealtimeProvider(
            OpenAIRealtimeProviderConfig(api_key="k", model="gpt-test", voice="alloy"), on_event=on_event,
        )
        return provider, OpenAIToolAdapter(ToolRegistry())
    provider = GrokProvider(GrokProviderConfig(api_key="k", model="grok", voice="eve"), on_event=on_event)
    return provider, GrokToolAdapter(ToolRegistry())


@pytest.mark.parametrize("kind", ["openai", "grok"])
async def test_realtime_providers_arm_only_after_the_result_is_sent(kind):
    from src.core.models import CallSession
    from src.core.session_store import SessionStore

    order = []

    async def sink(event):
        order.append(("event", event["type"]))

    provider, real_adapter = _realtime_provider(kind, sink)
    store = SessionStore()
    await store.upsert_call(CallSession(call_id=f"call-{kind}", caller_channel_id=f"call-{kind}"))
    provider._call_id = f"call-{kind}"
    provider._session_store = store
    provider.websocket = _OrderedWebSocket(order)
    provider.tool_adapter = _DeferredResultAdapter(real_adapter, _action("action-rt", "blind_transfer"))
    parent_done = asyncio.Event()
    waiting = asyncio.Event()

    async def await_parent(*_args, **_kwargs):
        waiting.set()
        await parent_done.wait()

    provider._await_parent_response_done = await_parent
    task = asyncio.create_task(provider._handle_function_call({
        "response_id": "resp-parent",
        "item": {"type": "function_call", "call_id": "fc-1", "name": "blind_transfer", "arguments": "{}"},
    }))
    await asyncio.wait_for(waiting.wait(), timeout=2)
    await asyncio.sleep(0)
    assert ("event", "DeferredTransferArmed") not in order  # Still waiting for response.done.

    parent_done.set()
    await asyncio.wait_for(task, timeout=2)

    sent = order.index(("ws", "function_call_output"))
    armed = order.index(("event", "DeferredTransferArmed"))
    assert sent < armed
    assert order.count(("event", "DeferredTransferArmed")) == 1


@pytest.mark.parametrize("model", ["gemini-3.8-live", "gemini-live-2.5-flash-native-audio"])
@pytest.mark.parametrize("with_non_blocking", [False, True])
async def test_google_live_arms_once_after_the_result_is_sent(monkeypatch, model, with_non_blocking):
    """3.8, and any toolCall with a non-blocking tool, runs through scheduled tasks."""
    from src.config import GoogleProviderConfig
    from src.tools.context import ToolExecutionContext
    from src.tools.registry import ToolRegistry

    order = []

    async def sink(event):
        order.append(("event", event["type"]))

    provider = GoogleLiveProvider(config=GoogleProviderConfig(llm_model=model), on_event=sink)
    provider._call_id = "call-google"
    provider._allowed_tools = ["blind_transfer", "check_extension_status"]
    registry = ToolRegistry.isolated()
    monkeypatch.setattr(registry, "is_tool_allowed", lambda _name, _allowed: True)
    monkeypatch.setattr(provider._tool_adapter, "registry", registry)
    action = _action("action-google", "blind_transfer")

    async def execute(name, _args, _context):
        if name == "blind_transfer":
            return {"status": "success", "message": "Transferring you now.", "deferred_transfer": dict(action)}
        return {"status": "success", "message": "Extension 601 is available."}

    async def send(payload):
        response = (payload.get("toolResponse") or {}).get("functionResponses") or [{}]
        order.append(("ws", response[0].get("name")))
        return True

    scheduled = []
    schedule = provider._schedule_tool_calls

    def record_schedule(data):
        scheduled.append(data)
        schedule(data)

    monkeypatch.setattr(provider, "_send_message", send)
    monkeypatch.setattr(provider, "_schedule_tool_calls", record_schedule)
    monkeypatch.setattr(provider._tool_adapter, "execute_tool", execute)
    monkeypatch.setattr(provider, "_track_conversation_message", AsyncMock())
    monkeypatch.setattr(ToolExecutionContext, "get_tool_block_response", AsyncMock(return_value=None))
    monkeypatch.setattr("src.providers.google_live.record_in_call_tool_result", AsyncMock())
    calls = [{"id": "fc-transfer", "name": "blind_transfer", "args": {"target": "6000"}}]
    if with_non_blocking:
        calls.insert(0, {"id": "fc-status", "name": "check_extension_status", "args": {"extension": "601"}})

    await provider._handle_server_message({"toolCall": {"functionCalls": calls}})
    tasks = list(provider._tool_call_tasks.values())
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)

    assert bool(scheduled) is (with_non_blocking or model == "gemini-3.8-live")
    assert order.count(("event", "DeferredTransferArmed")) == 1
    assert order.index(("ws", "blind_transfer")) < order.index(("event", "DeferredTransferArmed"))
    if with_non_blocking:
        assert ("ws", "check_extension_status") in order


@pytest.mark.parametrize("kind", ["openai", "grok"])
async def test_realtime_providers_report_vad_edges_only_after_a_deferred_result(kind):
    events = []

    async def sink(event):
        events.append(event)

    provider, _ = _realtime_provider(kind, sink)
    call_id = f"call-edges-{kind}"
    provider._call_id = call_id
    provider._greeting_completed = True

    await provider._handle_event({"type": "input_audio_buffer.speech_started"})
    assert not [e for e in events if e["type"].startswith("CallerSpeech")]

    # Caller is still talking when the result goes out: the start follows the arm.
    await provider.handoff_result_sent({"deferred_transfer": {"id": "a", "kind": "transfer"}}, call_id)
    assert [e["type"] for e in events][-2:] == ["DeferredTransferArmed", "CallerSpeechStarted"]
    assert events[-1]["vad"] is True

    await provider._handle_event({"type": "input_audio_buffer.speech_stopped"})
    assert events[-1]["type"] == "CallerSpeechStopped"


@pytest.mark.parametrize("kind", ["openai", "grok"])
async def test_stop_session_forgets_the_calls_deferred_flags(kind):
    events = []

    async def sink(event):
        events.append(event)

    provider, _ = _realtime_provider(kind, sink)
    call_id = f"call-forget-{kind}"
    provider._call_id = call_id
    provider._greeting_completed = True
    await provider._handle_event({"type": "input_audio_buffer.speech_started"})
    await provider.handoff_result_sent({"deferred_transfer": {"id": "a", "kind": "transfer"}}, call_id)
    assert call_id in provider._handoff_evidence.deferred

    await provider.stop_session()

    assert call_id not in provider._handoff_evidence.deferred
    events.clear()
    await provider.emit_caller_speech(call_id, started=False)
    assert events == []
    assert call_id not in provider._handoff_evidence.deferred


@pytest.mark.parametrize(
    "adapter_kind,result,context",
    [
        ("openai", {"call_id": "fc-1", "function_name": "blind_transfer"}, {"websocket": None}),
        ("openai", {"function_name": "blind_transfer"}, {"websocket": object()}),
        ("grok", {"call_id": "fc-1", "function_name": "blind_transfer"}, {"websocket": None}),
        ("grok", {"function_name": "blind_transfer"}, {"websocket": object()}),
        ("deepgram", {"function_call_id": "fc-1", "function_name": "blind_transfer"}, {"websocket": None}),
        ("deepgram", {"function_name": "blind_transfer"}, {"websocket": object()}),
        ("deepgram", {"function_call_id": "fc-1"}, {"websocket": object()}),
    ],
)
async def test_adapter_early_returns_report_a_failed_send(adapter_kind, result, context):
    from src.tools.adapters.deepgram import DeepgramToolAdapter
    from src.tools.adapters.grok import GrokToolAdapter
    from src.tools.adapters.openai import OpenAIToolAdapter
    from src.tools.registry import ToolRegistry

    adapter = {
        "openai": OpenAIToolAdapter, "grok": GrokToolAdapter, "deepgram": DeepgramToolAdapter,
    }[adapter_kind](ToolRegistry())
    events = []

    async def sink(event):
        events.append(event)

    provider = GoogleLiveProvider.__new__(GoogleLiveProvider)
    AIProviderInterface.__init__(provider, sink)
    action = {"id": "action-early", "kind": "transfer"}
    payload = {**result, "status": "success", "deferred_transfer": action}

    await adapter.send_tool_result(
        payload, {**context, "call_id": "call-early", "handoff_result_sent": provider.handoff_result_sent},
    )

    assert events == [{"type": "DeferredTransferArmed", "call_id": "call-early",
                       "action_id": "action-early", "success": False}]
