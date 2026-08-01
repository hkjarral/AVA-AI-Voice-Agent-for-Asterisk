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
