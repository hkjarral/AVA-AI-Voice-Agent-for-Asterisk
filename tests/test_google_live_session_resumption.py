"""Exercise the real provider lifecycle with synthetic Google websocket peers."""
import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from src.config import GoogleProviderConfig
from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine
from src.providers.google_live import GoogleLiveProvider


class Socket:
    def __init__(self, *, handle="checkpoint", acknowledge=True):
        self.open = True
        self.close_code = None
        self.close_reason = ""
        self.messages = asyncio.Queue()
        self.sent = []
        self.handle = handle
        self.acknowledge = acknowledge

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.messages.get()
        if item is None:
            if self.close_code not in {1000, 1001}:
                close = Close(self.close_code, self.close_reason)
                raise ConnectionClosedError(close, close, True)
            raise StopAsyncIteration
        return json.dumps(item)

    async def send(self, payload):
        assert self.open
        data = json.loads(payload)
        self.sent.append(data)
        if "setup" in data and self.acknowledge:
            await self.messages.put({"setupComplete": {}})
            if self.handle:
                await self.messages.put({"sessionResumptionUpdate": {
                    "resumable": True, "newHandle": self.handle,
                }})

    async def end(self, code=1000, reason=""):
        self.open = False
        self.close_code, self.close_reason = code, reason
        await self.messages.put(None)

    async def close(self):
        if self.open:
            await self.end()


async def spin_until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


async def start(monkeypatch, sockets, **settings):
    config = GoogleProviderConfig(
        api_key="synthetic-test-key", session_resumption_enabled=True,
        greeting="Hello", **settings,
    )
    provider = GoogleLiveProvider(config, AsyncMock())
    provider._send_greeting = AsyncMock()
    opened = []

    async def connect(*args, **kwargs):
        item = sockets[len(opened)]
        opened.append(item)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("src.providers.google_live.websockets.connect", connect)
    await provider.start_session("synthetic-call", {"tools": [], "voice": "Kore"})
    await spin_until(lambda: provider._resume_handle is not None or not sockets[0].handle)
    return provider, opened


async def recover(provider):
    await spin_until(lambda: provider._recovery_task is not None)
    await asyncio.wait_for(provider._recovery_task, timeout=1)


def disconnects(provider):
    return [call.args[0] for call in provider.on_event.await_args_list
            if call.args[0]["type"] == "ProviderDisconnected"]


@pytest.mark.parametrize("vertex", [False, True])
@pytest.mark.parametrize("resumption,compression", [(False, False), (True, False), (False, True), (True, True)])
async def test_setup_controls_are_independent_and_surface_agnostic(vertex, resumption, compression):
    provider = GoogleLiveProvider(GoogleProviderConfig(
        session_resumption_enabled=resumption, context_window_compression_enabled=compression,
        use_vertex_ai=vertex, vertex_project="synthetic-project",
    ), AsyncMock())
    provider._send_message = AsyncMock(return_value=True)
    await provider._send_setup({"tools": [], "voice": "Kore"})
    setup = provider._send_message.call_args.args[0]["setup"]
    assert ("sessionResumption" in setup) is resumption
    assert ("contextWindowCompression" in setup) is compression
    if compression:
        assert setup["contextWindowCompression"] == {"slidingWindow": {}}


async def test_repeated_goaway_preserves_call_setup_and_greeting(monkeypatch):
    sockets = [Socket(handle=f"handle-{i}") for i in range(3)]
    provider, opened = await start(monkeypatch, sockets)
    original = sockets[0].sent[0]["setup"]
    provider._seen_tool_call_ids.add("completed-tool")
    try:
        for index in range(2):
            await provider._handle_go_away({"goAway": {"timeLeft": "60s"}})
            task = provider._recovery_task
            await provider._handle_go_away({"goAway": {"timeLeft": "30s"}})
            assert provider._recovery_task is task
            await recover(provider)
            resumed = sockets[index + 1].sent[0]["setup"]
            assert resumed == {**original, "sessionResumption": {"handle": f"handle-{index}"}}
            await spin_until(lambda: provider._resume_handle == f"handle-{index + 1}")
        assert len(opened) == 3
        assert provider._call_id == "synthetic-call"
        assert provider._seen_tool_call_ids == {"completed-tool"}
        provider._send_greeting.assert_awaited_once()
        assert not disconnects(provider)
    finally:
        await provider.stop_session()


@pytest.mark.parametrize("code,reason", [
    (1000, ""), (1001, ""), (1006, ""), (1011, "Internal error encountered"),
    (1008, "Connection aborted after GoAway signal"),
])
async def test_unexpected_close_resumes_without_engine_disconnect(monkeypatch, code, reason):
    sockets = [Socket(), Socket()]
    provider, opened = await start(monkeypatch, sockets)
    try:
        await sockets[0].end(code, reason)
        await recover(provider)
        assert len(opened) == 2
        assert provider._setup_complete
        assert not disconnects(provider)
    finally:
        await provider.stop_session()


@pytest.mark.parametrize("code,reason", [
    (1008, "Invalid credentials"), (1007, "Unknown setup field"),
    (1011, "Resource has been exhausted (e.g. check quota)"),
])
async def test_permanent_errors_do_not_reconnect(monkeypatch, code, reason):
    sockets = [Socket()]
    provider, opened = await start(monkeypatch, sockets)
    try:
        await sockets[0].end(code, reason)
        await provider._receive_task
        assert len(opened) == 1
        assert len(disconnects(provider)) == 1
    finally:
        await provider.stop_session()


async def test_false_checkpoint_clears_handle_and_refuses_lossy_resume(monkeypatch):
    provider, opened = await start(monkeypatch, [Socket()])
    try:
        await provider._handle_server_message({"sessionResumptionUpdate": {"resumable": False, "newHandle": ""}})
        assert provider._resume_handle is None
        assert "checkpoint" not in provider._redact_close_reason("Rejected checkpoint")
        await opened[0].end(1006)
        await provider._receive_task
        assert len(opened) == 1
        assert len(disconnects(provider)) == 1
    finally:
        await provider.stop_session()


@pytest.mark.parametrize("message", [
    {"sessionResumptionUpdate": {"resumable": False, "newHandle": ""}},
    {"serverContent": {"modelTurn": {"parts": [{"text": "new output"}]}}},
    {"toolCall": {"functionCalls": [{"id": "new-write", "name": "google_calendar", "args": {}}]}},
])
async def test_checkpoint_invalidation_during_recovery_notification_fails_closed(monkeypatch, message):
    provider, opened = await start(monkeypatch, [Socket(), Socket()])
    admitted, release = asyncio.Event(), asyncio.Event()
    provider._handle_server_content = AsyncMock()
    provider._handle_tool_call = AsyncMock()

    async def notify(event):
        if event["type"] == "ProviderSessionRecovery" and event["active"]:
            admitted.set()
            await release.wait()

    provider.on_event = AsyncMock(side_effect=notify)
    try:
        await provider._handle_go_away({"goAway": {"timeLeft": "5s"}})
        await asyncio.wait_for(admitted.wait(), 1)
        # Deliver through the actual old receive loop while admission yields.
        await opened[0].messages.put(message)
        await spin_until(lambda: not provider._session_resumable)
        release.set()
        await recover(provider)
        assert len(opened) == 1  # neither stale resumption nor a fresh session
        assert len(disconnects(provider)) == 1
        provider._handle_tool_call.assert_not_called()
        assert provider.websocket is None
    finally:
        release.set()
        await provider.stop_session()


@pytest.mark.parametrize("missing_handle", [False, True])
async def test_resumed_setup_requires_a_currently_safe_checkpoint(monkeypatch, missing_handle):
    provider, _ = await start(monkeypatch, [Socket()])
    provider._recovering = True
    if missing_handle:
        provider._resume_handle = None
    else:
        provider._session_resumable = False
    provider._send_message = AsyncMock(return_value=True)
    try:
        with pytest.raises(ValueError, match="safe checkpoint"):
            await provider._send_setup(None)
        provider._send_message.assert_not_called()
    finally:
        provider._recovering = False
        await provider.stop_session()


async def test_goaway_waits_for_safe_checkpoint(monkeypatch):
    provider, opened = await start(monkeypatch, [Socket(), Socket()])
    try:
        provider._session_resumable = False
        await provider._handle_go_away({"goAway": {"timeLeft": "5s"}})
        await asyncio.sleep(0)
        assert len(opened) == 1
        await provider._handle_server_message({"sessionResumptionUpdate": {"resumable": True, "newHandle": "new-safe"}})
        await recover(provider)
        assert opened[1].sent[0]["setup"]["sessionResumption"]["handle"] == "new-safe"
    finally:
        await provider.stop_session()


async def test_goaway_without_checkpoint_has_bounded_failure(monkeypatch):
    provider, opened = await start(monkeypatch, [Socket(handle=None)])
    try:
        await provider._handle_go_away({"goAway": {"timeLeft": "0.01s"}})
        await recover(provider)
        assert len(opened) == 1
        assert len(disconnects(provider)) == 1
    finally:
        await provider.stop_session()


async def test_connect_retries_are_bounded_and_fail_once(monkeypatch):
    provider, opened = await start(monkeypatch, [Socket(), ConnectionError(), ConnectionError()])
    try:
        await opened[0].end(1006)
        await recover(provider)
        assert len(opened) == 3
        assert len(disconnects(provider)) == 1
        assert provider.websocket is None
        assert not provider._recovery_audio
    finally:
        await provider.stop_session()


async def test_setup_timeout_does_not_leave_tasks_or_a_live_socket(monkeypatch):
    sockets = [Socket(), Socket(acknowledge=False)]
    provider, opened = await start(monkeypatch, sockets, session_recovery_timeout_sec=0.03)
    try:
        await sockets[0].end(1006)
        await recover(provider)
        assert len(opened) == 2
        assert len(disconnects(provider)) == 1
        assert not sockets[1].open
        assert provider._receive_task.done()
    finally:
        await provider.stop_session()


async def test_stop_cancels_inflight_recovery_and_clears_handles(monkeypatch):
    sockets = [Socket(), Socket(acknowledge=False)]
    provider, opened = await start(monkeypatch, sockets)
    await sockets[0].end(1006)
    await spin_until(lambda: len(opened) == 2)
    await provider.stop_session()
    assert provider._closed
    assert provider._resume_handle is None
    assert provider._redaction_resume_handle is None
    assert not sockets[1].open
    assert not disconnects(provider)
    assert not any(not task.done() and task.get_name().startswith("google-live-")
                   for task in asyncio.all_tasks())


@pytest.mark.parametrize("state", ["hangup", "transfer", "transfer_during_setup"])
async def test_terminal_owner_prevents_resumption(monkeypatch, state):
    sockets = [Socket(), Socket()]
    provider, opened = await start(monkeypatch, sockets)
    allowed = True

    async def guard():
        nonlocal allowed
        if state == "transfer_during_setup" and len(opened) == 2:
            allowed = False
        return allowed

    provider._recovery_guard = guard
    try:
        if state == "hangup":
            provider._hangup_after_response = True
        elif state == "transfer":
            allowed = False
        await sockets[0].end(1006)
        if state == "hangup":
            await provider._receive_task
            assert not disconnects(provider)
        else:
            await recover(provider)
        assert len(opened) == (2 if state == "transfer_during_setup" else 1)
        assert not provider._setup_complete
    finally:
        await provider.stop_session()


async def test_audio_is_bounded_and_only_sent_after_resumed_setup_ack(monkeypatch):
    sockets = [Socket(), Socket(acknowledge=False)]
    provider, opened = await start(monkeypatch, sockets, session_recovery_audio_buffer_ms=40)
    try:
        await sockets[0].end(1006)
        await spin_until(lambda: len(opened) == 2)
        await provider.send_audio(b"\x01\x00" * 1600, 16000, "linear16")
        assert len(provider._recovery_audio) == 1280
        assert provider._recovery_audio_dropped == 1920
        assert len(sockets[1].sent) == 1
        await sockets[1].messages.put({"setupComplete": {}})
        await recover(provider)
        sent = [base64.b64decode(item["realtimeInput"]["audio"]["data"])
                for item in sockets[1].sent[1:]]
        assert b"".join(sent) == b"\x01\x00" * 640
        assert not disconnects(provider)
    finally:
        await provider.stop_session()


@pytest.mark.parametrize("model", ["gemini-3.8-live", "gemini-2.5-flash-native-audio-latest"])
async def test_duplicate_completed_tool_reuses_result_without_execution(monkeypatch, model):
    provider, opened = await start(monkeypatch, [Socket()], llm_model=model)
    provider._handle_tool_call = AsyncMock()
    result = {"id": "booking-1", "name": "google_calendar", "response": {"status": "success"}}
    try:
        await provider._send_message({"toolResponse": {"functionResponses": [result]}})
        await provider._handle_server_message({"toolCall": {"functionCalls": [
            {"id": "booking-1", "name": "google_calendar", "args": {}},
        ]}})
        assert opened[0].sent[-1] == {"toolResponse": {"functionResponses": [result]}}
        # A legacy handler may see an empty list, but never the booking.
        assert all(not call.args[0]["toolCall"]["functionCalls"]
                   for call in provider._handle_tool_call.await_args_list)
    finally:
        await provider.stop_session()


@pytest.mark.parametrize("field,value", [
    ("cleanup_in_progress", True), ("cleanup_after_tts", True), ("transfer_active", True),
    ("pending_deferred_transfer", {}), ("current_action", {"type": "attended_transfer"}),
    ("current_action", {"type": "attended_transfer", "decision": "accepted"}),
    ("current_action", {"type": "attended_transfer", "decision": "declined"}),
    ("current_action", {"type": "predial_transfer"}),
])
async def test_engine_recovery_guard_respects_routing_and_termination(field, value):
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    session = CallSession(call_id="call-guard", caller_channel_id="call-guard", provider_name="google_live")
    await engine.session_store.upsert_call(session)
    assert await engine._can_resume_google_session("call-guard")
    setattr(session, field, value)
    assert not await engine._can_resume_google_session("call-guard")
    assert not await engine._can_resume_google_session("missing")


async def test_disabled_recovery_still_reports_unexpected_clean_close(monkeypatch):
    socket = Socket()
    provider, opened = await start(monkeypatch, [socket])
    provider.config.session_resumption_enabled = False
    try:
        await socket.end(1000)
        await provider._receive_task
        assert len(opened) == 1
        assert len(disconnects(provider)) == 1
    finally:
        await provider.stop_session()


@pytest.mark.parametrize("setting,value", [
    ("session_recovery_timeout_sec", float("inf")), ("session_recovery_timeout_sec", 0),
    ("session_recovery_timeout_sec", 31), ("session_recovery_timeout_sec", True),
    ("session_recovery_max_attempts", 0), ("session_recovery_max_attempts", 4),
    ("session_recovery_max_attempts", True), ("session_recovery_audio_buffer_ms", -1),
    ("session_recovery_audio_buffer_ms", 2001), ("session_recovery_audio_buffer_ms", True),
])
def test_recovery_settings_reject_unbounded_or_boolean_limits(setting, value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        GoogleProviderConfig(**{setting: value})


async def test_recovery_settings_survive_configuration_round_trip():
    config = GoogleProviderConfig(
        session_resumption_enabled=True, context_window_compression_enabled=True,
        session_recovery_timeout_sec=8, session_recovery_max_attempts=3,
        session_recovery_audio_buffer_ms=500,
    )
    restored = GoogleProviderConfig.model_validate(config.model_dump())
    assert restored == config


async def test_rejected_resume_is_not_retried_or_replaced_with_fresh_chat(monkeypatch):
    sockets = [Socket(), Socket(acknowledge=False)]
    provider, opened = await start(monkeypatch, sockets)
    try:
        await sockets[0].end(1006)
        await spin_until(lambda: len(opened) == 2)
        await sockets[1].end(1008, "Invalid session handle")
        await recover(provider)
        assert len(opened) == 2
        assert sockets[1].sent[0]["setup"]["sessionResumption"] == {"handle": "checkpoint"}
        assert len(disconnects(provider)) == 1
    finally:
        await provider.stop_session()


async def test_pending_tool_prevents_planned_recovery_until_finished(monkeypatch):
    provider, opened = await start(monkeypatch, [Socket(), Socket()])
    finished = asyncio.Event()
    tool = asyncio.create_task(finished.wait())
    provider._tool_call_tasks["booking"] = tool
    try:
        await provider._handle_go_away({"goAway": {"timeLeft": "5s"}})
        await asyncio.sleep(0)
        assert len(opened) == 1
        assert not tool.cancelled()
        finished.set()
        await tool
        await recover(provider)
        assert len(opened) == 2
        assert not disconnects(provider)
    finally:
        finished.set()
        await provider.stop_session()


async def test_vertex_auth_failure_during_recovery_never_falls_back_to_api_key(monkeypatch):
    import src.providers.google_live as google_live

    provider = GoogleLiveProvider(GoogleProviderConfig(
        api_key="synthetic-key", use_vertex_ai=True, vertex_project="test-project",
        session_resumption_enabled=True,
    ), AsyncMock())
    provider._call_id = "vertex-recovery"
    provider._recovering = True
    provider._vertex_active = True
    monkeypatch.setattr(google_live, "_GOOGLE_AUTH_AVAILABLE", True)
    monkeypatch.setattr(google_live.google.auth, "default", lambda **kwargs: (_ for _ in ()).throw(ValueError("auth rejected")))
    connect = AsyncMock()
    monkeypatch.setattr(google_live.websockets, "connect", connect)
    with pytest.raises(ValueError, match="auth rejected"):
        await provider._connect_session()
    connect.assert_not_called()
    assert provider._vertex_active


async def test_socket_turnover_with_real_websocket_client():
    from websockets.asyncio.server import serve

    rotate = asyncio.Event()
    setups = []

    async def peer(socket):
        setups.append(json.loads(await socket.recv())["setup"])
        number = len(setups)
        await socket.send(json.dumps({"setupComplete": {}}))
        await socket.send(json.dumps({"sessionResumptionUpdate": {"resumable": True, "newHandle": f"wire-handle-{number}"}}))
        if number == 1:
            await rotate.wait()
            await socket.send(json.dumps({"goAway": {"timeLeft": "60s"}}))
        await socket.wait_closed()

    async with serve(peer, "127.0.0.1", 0, subprotocols=["gemini-live"]) as server:
        port = server.sockets[0].getsockname()[1]
        provider = GoogleLiveProvider(GoogleProviderConfig(
            api_key="synthetic-key", session_resumption_enabled=True,
            websocket_endpoint=f"ws://127.0.0.1:{port}",
        ), AsyncMock())
        try:
            await provider.start_session("wire-call", {"tools": []})
            await spin_until(lambda: provider._resume_handle == "wire-handle-1")
            rotate.set()
            await recover(provider)
            assert len(setups) == 2
            assert setups[1]["sessionResumption"] == {"handle": "wire-handle-1"}
            assert provider._setup_complete
            assert not disconnects(provider)
        finally:
            await provider.stop_session()


async def test_legacy_entered_write_survives_goaway_deadline_and_reader_cancellation(monkeypatch):
    provider, opened = await start(monkeypatch, [Socket()], llm_model="gemini-2.5-flash-native-audio-latest")
    provider.TOOL_DRAIN_GRACE_SEC = 0.01
    entered, finish = asyncio.Event(), asyncio.Event()

    async def write(data):
        provider._active_tool_call_ids.add("booking")
        entered.set()
        await finish.wait()

    provider._handle_tool_call = write
    await opened[0].messages.put({"toolCall": {"functionCalls": [
        {"id": "booking", "name": "google_calendar", "args": {}},
    ]}})
    await entered.wait()
    task = provider._tool_call_tasks["booking"]
    try:
        # The legacy reader is busy awaiting this entered write. Turnover may
        # exhaust its deadline, but cancellation must not strand that mutation.
        await provider._handle_go_away({"goAway": {"timeLeft": "0.01s"}})
        await recover(provider)
        assert not task.cancelled()
        assert not task.done()
        assert len(disconnects(provider)) == 1
        finish.set()
        await task
        assert len(opened) == 1
    finally:
        finish.set()
        await provider.stop_session()


@pytest.mark.parametrize("vertex", [False, True])
@pytest.mark.parametrize("id_fields", [{}, {"id": ""}])
async def test_legacy_idless_tools_execute_and_reply_without_local_ids(monkeypatch, vertex, id_fields):
    provider, opened = await start(monkeypatch, [Socket()], llm_model="gemini-2.5-flash-native-audio-latest")
    provider._vertex_active = vertex
    registry = Mock()
    registry.is_tool_allowed.return_value = True
    registry.get.return_value = None
    provider._tool_adapter = SimpleNamespace(registry=registry, execute_tool=AsyncMock(return_value={"status": "success"}))
    provider._allowed_tools = ["google_calendar"]
    recorder = AsyncMock()
    monkeypatch.setattr("src.providers.google_live.record_in_call_tool_result", recorder)
    call = {"name": "google_calendar", "args": {}, **id_fields}
    try:
        # No server id means these are distinct requests, not duplicate ids.
        for _ in range(2):
            await provider._handle_server_message({"toolCall": {"functionCalls": [call]}})
        assert provider._tool_adapter.execute_tool.await_count == 2
        replies = [m["toolResponse"]["functionResponses"][0] for m in opened[0].sent if "toolResponse" in m]
        assert len(replies) == 2
        assert all(r["name"] == "google_calendar" and r["response"]["status"] == "success" for r in replies)
        assert all("id" not in r if vertex else r["id"] == id_fields.get("id") for r in replies)
        assert all(c.kwargs["tool_call_id"] == id_fields.get("id") for c in recorder.await_args_list)
        assert not provider._seen_tool_call_ids
        assert not provider._tool_call_tasks
        assert not provider._active_tool_call_ids
    finally:
        await provider.stop_session()


@pytest.mark.parametrize("entered", [False, True])
async def test_idless_tool_teardown_protects_only_entered_writes(monkeypatch, entered):
    provider, _ = await start(monkeypatch, [Socket()], llm_model="gemini-2.5-flash-native-audio-latest")
    provider.TOOL_DRAIN_GRACE_SEC = 0.01
    paused, release = asyncio.Event(), asyncio.Event()

    async def pause(*args, **kwargs):
        paused.set()
        await release.wait()
        return {"status": "success"} if entered else None

    registry = Mock()
    registry.is_tool_allowed.return_value = True
    registry.get.return_value = None
    execute = AsyncMock(side_effect=pause) if entered else AsyncMock()
    provider._tool_adapter = SimpleNamespace(registry=registry, execute_tool=execute)
    provider._allowed_tools = ["google_calendar"]
    monkeypatch.setattr("src.providers.google_live.record_in_call_tool_result", AsyncMock())
    monkeypatch.setattr("src.tools.context.ToolExecutionContext.get_tool_block_response", AsyncMock(return_value=None) if entered else pause)
    reader = asyncio.create_task(provider._handle_server_message({"toolCall": {"functionCalls": [{"name": "google_calendar", "args": {}}]}}))
    await asyncio.wait_for(paused.wait(), 1)
    task = next(iter(provider._tool_call_tasks.values()))
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)
    try:
        await provider.stop_session()
        assert task.cancelled() is (not entered)
        if entered:
            assert not task.done()
        else:
            execute.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await provider.stop_session()


async def test_engine_watchdog_is_paused_and_resumed_for_bounded_recovery():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine._no_input_note_processing = AsyncMock()
    session = CallSession(call_id="watchdog-call", caller_channel_id="watchdog-call", provider_name="google_live")
    await engine.session_store.upsert_call(session)
    for active in (True, False):
        await engine.on_provider_event({"type": "ProviderSessionRecovery", "call_id": session.call_id, "active": active})
    assert [call.args for call in engine._no_input_note_processing.await_args_list] == [
        (session.call_id, True), (session.call_id, False),
    ]


async def test_concurrent_calls_keep_checkpoints_and_recovery_isolated(monkeypatch):
    first = [Socket(handle="call-a"), Socket()]
    provider, opened = await start(monkeypatch, first)
    other = GoogleLiveProvider(provider.config.model_copy(), AsyncMock())
    other._call_id = "call-b"
    other._resume_handle = "call-b-handle"
    other._session_resumable = True
    try:
        await first[0].end(1006)
        await recover(provider)
        assert opened[1].sent[0]["setup"]["sessionResumption"]["handle"] == "call-a"
        assert other._resume_handle == "call-b-handle"
        assert other._recovery_task is None
        assert not other._recovery_audio
    finally:
        await provider.stop_session()


async def test_recovery_error_reporting_cannot_leak_handle_into_close_logs(monkeypatch):
    provider, _ = await start(monkeypatch, [Socket()])
    try:
        reason = provider._redact_close_reason("Rejected checkpoint for synthetic-test-key")
        assert "checkpoint" not in reason
        assert "synthetic-test-key" not in reason
    finally:
        await provider.stop_session()


async def test_generation_invalidates_an_older_safe_checkpoint(monkeypatch):
    provider, opened = await start(monkeypatch, [Socket()])
    provider._handle_server_content = AsyncMock()
    try:
        await provider._handle_server_message({"serverContent": {"modelTurn": {"parts": [{"text": "new turn"}]}}})
        assert not provider._safe_checkpoint()
        await opened[0].end(1006)
        await provider._receive_task
        assert len(opened) == 1
        assert len(disconnects(provider)) == 1
    finally:
        await provider.stop_session()


async def test_audio_during_recovery_end_notification_is_not_replayed_next_time(monkeypatch):
    sockets = [Socket(), Socket(), Socket()]
    provider, _ = await start(monkeypatch, sockets)

    async def notify(event):
        if event.get("type") == "ProviderSessionRecovery" and event.get("active") is False:
            await provider.send_audio(b"\x01\x00" * 320, 16000, "linear16")

    provider.on_event = AsyncMock(side_effect=notify)
    try:
        for index in range(2):
            await provider._handle_go_away({"goAway": {"timeLeft": "60s"}})
            await recover(provider)
            assert not provider._recovery_audio
            audio = [item for item in sockets[index + 1].sent if "realtimeInput" in item]
            assert len(audio) == 1  # Current notification input only; no previous turnover's frames.
    finally:
        await provider.stop_session()


async def test_failed_replay_discards_socket_before_recovery_end_notification(monkeypatch):
    provider, _ = await start(monkeypatch, [Socket()])
    resumed = Socket()
    end_states = []

    async def resume():
        provider.websocket = resumed
        provider._setup_complete = True
        provider._recovery_audio.extend(b"\x01\x00" * 320)

    async def notify(event):
        if event.get("type") == "ProviderSessionRecovery" and event.get("active") is False:
            end_states.append((provider._recovering, provider.websocket))
            await provider.send_audio(b"\x02\x00" * 320, 16000, "linear16")

    provider._resume_transport = resume
    provider._send_message = AsyncMock(return_value=False)
    provider.on_event = AsyncMock(side_effect=notify)
    try:
        await provider._handle_go_away({"goAway": {"timeLeft": "60s"}})
        await recover(provider)
        assert end_states == [(False, None)]
        assert not resumed.open
        assert not provider._recovery_audio
        assert len(disconnects(provider)) == 1
    finally:
        await provider.stop_session()
