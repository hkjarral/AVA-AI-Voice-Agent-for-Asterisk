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

@pytest.mark.asyncio
async def test_google_transfer_failure_is_a_single_history_event():
    from unittest.mock import AsyncMock
    from src.providers.google_live import GoogleLiveProvider
    from src.config import GoogleProviderConfig
    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)
    provider._call_id = 'active'
    provider._ws_is_open = lambda: True
    provider._send_message = AsyncMock(return_value=True)
    assert not await provider.notify_transfer_failed(call_id='stale')
    provider._send_message.assert_not_awaited()
    assert await provider.notify_transfer_failed(call_id='active')
    msg = provider._send_message.call_args.args[0]
    assert msg['clientContent']['turnComplete'] is True
    assert 'toolResponse' not in msg
    text = msg['clientContent']['turns'][0]['parts'][0]['text']
    assert 'No transfer is in progress' in text
    assert 'Do not retry' in text
    provider._closed = True
    assert not await provider.notify_transfer_failed(call_id='active')
    assert provider._send_message.await_count == 1
