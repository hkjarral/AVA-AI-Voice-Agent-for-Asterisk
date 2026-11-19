import pytest


@pytest.mark.unit
def test_google_live_tool_response_payload_drops_large_fields():
    from src.providers.google_live import GoogleLiveProvider
    from src.config import GoogleProviderConfig

    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)

    huge = {"nested": {"x": "y" * 20000}}
    result = {
        "status": "success",
        "message": "ok",
        "data": huge,
        "mcp": {"server": "s", "tool": "t"},
    }
    payload = provider._build_tool_response_payload("mcp_tool", result)
    assert "data" not in payload
    assert "mcp" not in payload
    assert payload["status"] == "success"


@pytest.mark.unit
def test_google_live_tool_response_payload_includes_extension_availability():
    from src.providers.google_live import GoogleLiveProvider
    from src.config import GoogleProviderConfig

    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)

    result = {
        "status": "success",
        "target": "6000",
        "extension": "6000",
        "device_state_name": "SIP/6000",
        "available": False,
        "device_state": "INUSE",
        "availability_source": "device_state",
        "endpoint_state": "online",
        "tech": "SIP",
    }

    payload = provider._build_tool_response_payload("check_extension_status", result)

    assert payload["status"] == "success"
    assert payload["target"] == "6000"
    assert payload["extension"] == "6000"
    assert payload["device_state_name"] == "SIP/6000"
    assert payload["available"] is False
    assert payload["device_state"] == "INUSE"
    assert payload["availability_source"] == "device_state"
    assert payload["endpoint_state"] == "online"
    assert payload["tech"] == "SIP"
    assert payload["message"] == "Extension 6000 is in use (INUSE)."


@pytest.mark.unit
def test_google_live_tool_response_payload_vertex_hangup_call_non_dict_result():
    from src.providers.google_live import GoogleLiveProvider
    from src.config import GoogleProviderConfig

    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)

    # Non-dict tool output should not crash and should remain payload-safe; we only
    # require this path not to add Vertex-specific instruction text.
    payload = provider._build_tool_response_payload("hangup_call", ["not", "a", "dict"])

    assert payload["status"] == "success"
    assert payload.get("instruction") is None


@pytest.mark.unit
def test_check_extension_new_fields_survive_sanitization():
    from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string
    result = {
        "status": "success", "extension": "102", "available": False,
        "availability_status": "dnd", "availability_reason": "dnd",
        "device_states": [{"id": "Custom:DND102", "role": "custom", "state": "INUSE", "classification": "busy", "status": "dnd"}],
        "message": "Extension 102 is on Do Not Disturb.",
    }
    out = sanitize_tool_result_for_json_string(result, tool_name="check_extension_status")
    assert out["availability_status"] == "dnd"
    assert out["availability_reason"] == "dnd"
    assert out["device_states"][0]["status"] == "dnd"


@pytest.mark.unit
def test_google_live_tool_response_payload_includes_calendar_events():
    """Google Live must receive the event list rather than only a success message."""
    from src.providers.google_live import GoogleLiveProvider
    from src.config import GoogleProviderConfig

    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)
    result = {
        "status": "success",
        "message": "Events listed.",
        "events": [
            {
                "id": "event-1",
                "summary": "Customer appointment",
                "start": "2026-09-20T09:00:00-07:00",
                "end": "2026-09-20T09:30:00-07:00",
                "calendar": "default",
            }
        ],
    }

    payload = provider._build_tool_response_payload("google_calendar", result)

    assert payload["events"] == result["events"]
    assert payload["total_events"] == 1
    assert payload["events_truncated"] is False


@pytest.mark.parametrize("status", ["success", "error", "timeout"])
def test_google_notification_response_excludes_diagnostics_and_continues_silently(status):
    from src.providers.google_live import GoogleLiveProvider
    from src.config import GoogleProviderConfig
    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)
    result = {"status": status, "internal_notification": True, "message": "private acknowledgement", "delivery_status": "uncertain", "summary_status": "timeout", "notification_deliveries": {"private-recipient": {"delivery_status": "uncertain"}}}
    payload = provider._build_tool_response_payload("native_notice", result)
    assert set(payload) == {"status", "instruction"} and payload["status"] == status
    assert "remaining tools" in payload["instruction"] and "Do not read acknowledgements" in payload["instruction"]
    assert "private" not in str(payload) and "notification_deliveries" not in payload
    assert result["notification_deliveries"]["private-recipient"]["delivery_status"] == "uncertain"

async def _active_transfer_provider():
    """Build a real provider send path with an in-memory call and mocked socket."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from src.providers.google_live import GoogleLiveProvider
    from src.config import GoogleProviderConfig
    from src.core.models import CallSession
    from src.core.session_store import SessionStore
    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)
    provider._call_id = "active"
    provider._ws_is_open = lambda: True
    provider.websocket = SimpleNamespace(send=AsyncMock())
    provider._session_store = SessionStore()
    session = CallSession(call_id="active", caller_channel_id="caller")
    attempt = {"type": "attended_transfer", "id": "old", "recovery_started": True}
    session.current_action = attempt
    await provider._session_store.upsert_call(session)
    return provider, session, attempt


@pytest.mark.asyncio
async def test_google_transfer_failure_is_a_single_history_event():
    import json
    provider, session, attempt = await _active_transfer_provider()
    assert not await provider.notify_transfer_failed(call_id="stale", attempt=attempt)
    provider.websocket.send.assert_not_awaited()
    assert await provider.notify_transfer_failed(call_id="active", attempt=attempt)
    msg = json.loads(provider.websocket.send.call_args.args[0])
    assert msg["clientContent"]["turnComplete"] is True
    assert "toolResponse" not in msg
    text = msg["clientContent"]["turns"][0]["parts"][0]["text"]
    assert "No transfer is in progress" in text
    assert "Do not retry" in text
    provider._closed = True
    assert not await provider.notify_transfer_failed(call_id="active", attempt=attempt)
    assert provider.websocket.send.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["new-attempt", "hangup", "closing"])
async def test_transfer_failure_rechecks_ownership_after_waiting_for_send_lock(transition):
    """An obsolete failure turn must never interrupt a newer call-state owner."""
    import asyncio
    provider, session, attempt = await _active_transfer_provider()
    await provider._send_lock.acquire()
    pending = asyncio.create_task(provider.notify_transfer_failed(call_id="active", attempt=attempt))
    await asyncio.sleep(0)
    assert not pending.done()
    if transition == "new-attempt":
        session.current_action = {"type": "attended_transfer", "id": "new"}
    elif transition == "hangup":
        await provider._session_store.remove_call("active")
    else:
        provider._closing = True
    provider._send_lock.release()
    assert await pending is False
    provider.websocket.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('transition', ['sent', 'send-failed', 'new-attempt', 'hangup'])
async def test_transfer_failure_waits_until_originating_tool_response_is_sent(transition):
    import asyncio
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    provider, session, attempt = await _active_transfer_provider()
    attempt['_tool_history_origin'] = {'tool_call_id': 'origin'}
    session.current_action = None
    entered = asyncio.Event()
    release = asyncio.Event()
    response_entered = asyncio.Event()
    response_release = asyncio.Event()
    notify_tasks = []
    sent = []

    async def execute(*_args):
        session.current_action = attempt
        notify_tasks.append(asyncio.create_task(provider.notify_transfer_failed(call_id='active', attempt=attempt)))
        entered.set()
        await release.wait()
        return {'status': 'success', 'type': 'attended_transfer'}

    async def send(payload):
        message = json.loads(payload)
        if 'toolResponse' in message:
            response_entered.set()
            await response_release.wait()
            if transition == 'send-failed':
                raise RuntimeError('send failed')
        sent.append(message)

    provider.websocket.send = send
    provider._allowed_tools = ['attended_transfer']
    provider._tool_adapter = SimpleNamespace(
        registry=SimpleNamespace(is_tool_allowed=lambda *_: True), execute_tool=execute)
    provider.prepare_handoff_result = AsyncMock()
    provider.handoff_result_sent = AsyncMock()
    tool = asyncio.create_task(provider._handle_tool_call({'toolCall': {'functionCalls': [
        {'id': 'origin', 'name': 'attended_transfer', 'args': {}}]}}))
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.sleep(0)
    assert sent == []
    release.set()
    await asyncio.wait_for(response_entered.wait(), 1)
    assert not notify_tasks[0].done()
    assert sent == []
    if transition == 'new-attempt':
        session.current_action = {'type': 'attended_transfer', 'id': 'new'}
    elif transition == 'hangup':
        await provider._session_store.remove_call('active')
    response_release.set()
    await asyncio.wait_for(tool, 1)
    delivered = await asyncio.wait_for(notify_tasks[0], 1)
    assert delivered is (transition == 'sent')
    assert ['toolResponse' if 'toolResponse' in m else 'clientContent' for m in sent] == (
        ['toolResponse', 'clientContent'] if transition == 'sent' else
        [] if transition == 'send-failed' else ['toolResponse'])


@pytest.mark.asyncio
async def test_transfer_response_waiter_is_released_on_session_reset_and_survives_recovery_timeout():
    import asyncio
    provider, session, attempt = await _active_transfer_provider()
    attempt['_tool_history_origin'] = {'tool_call_id': 'origin'}
    response = asyncio.get_running_loop().create_future()
    provider._transfer_tool_responses[('active', 'origin')] = response
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(provider.notify_transfer_failed(call_id='active', attempt=attempt), .01)
    assert not response.cancelled()
    pending = asyncio.create_task(provider.notify_transfer_failed(call_id='active', attempt=attempt))
    await asyncio.sleep(0)
    provider._release_transfer_tool_responses()
    assert not await pending
    provider.websocket.send.assert_not_awaited()
