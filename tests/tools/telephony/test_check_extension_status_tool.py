"""
Unit tests for CheckExtensionStatusTool (AAVA-53).
"""

import pytest
from unittest.mock import AsyncMock

from src.tools.telephony.check_extension_status import CheckExtensionStatusTool


class TestCheckExtensionStatusTool:
    @pytest.fixture
    def tool(self):
        return CheckExtensionStatusTool()

    def test_definition(self, tool):
        d = tool.definition
        assert d.name == "check_extension_status"
        assert d.category.value == "telephony"
        assert d.requires_channel is False
        assert d.is_global is False

        params = {p.name: p for p in d.parameters}
        assert params["extension"].required is True
        assert params["tech"].required is False
        assert params["device_state_id"].required is False

    @pytest.mark.asyncio
    async def test_queries_device_state_with_config_tech(self, tool, tool_context, mock_ari_client):
        # Add explicit tech to config to avoid relying on dial_string parsing
        tool_context.config["tools"]["extensions"]["internal"]["6000"]["device_state_tech"] = "SIP"

        mock_ari_client.send_command = AsyncMock(return_value={"name": "SIP/6000", "state": "NOT_INUSE"})

        result = await tool.execute({"extension": "6000"}, tool_context)
        assert result["status"] == "success"
        assert result["device_state_id"] == "SIP/6000"
        assert result["available"] is True
        assert result["message"] == "Extension 6000 is available (NOT_INUSE)."

        # Ensure URL-encoded slash
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["method"] == "GET"
        assert call_args["resource"] == "deviceStates/SIP%2F6000"

    @pytest.mark.asyncio
    async def test_queries_device_state_with_param_tech(self, tool, tool_context, mock_ari_client):
        # Remove internal config entry to force param-based resolution
        tool_context.config["tools"]["extensions"]["internal"].pop("6000", None)
        tool_context.config["tools"]["check_extension_status"] = {"restrict_to_configured_extensions": False}
        mock_ari_client.send_command = AsyncMock(return_value={"name": "PJSIP/2765", "state": "INUSE"})

        result = await tool.execute({"extension": "2765", "tech": "PJSIP"}, tool_context)
        assert result["status"] == "success"
        assert result["device_state_id"] == "PJSIP/2765"
        assert result["available"] is False
        assert result["message"] == "Extension 2765 is in use (INUSE)."

    @pytest.mark.asyncio
    async def test_device_state_id_override(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["check_extension_status"] = {"restrict_to_configured_extensions": False}
        mock_ari_client.send_command = AsyncMock(return_value={"name": "Custom/agentA", "state": "NOT_INUSE"})

        result = await tool.execute({"extension": "ignored", "device_state_id": "Custom/agentA"}, tool_context)
        assert result["status"] == "success"
        assert result["device_state_id"] == "Custom/agentA"
        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_blocks_device_state_id_override_when_guardrail_enabled(self, tool, tool_context, mock_ari_client):
        result = await tool.execute({"extension": "ignored", "device_state_id": "Custom/agentA"}, tool_context)

        assert result["status"] == "failed"
        assert result["guardrail_blocked"] is True
        mock_ari_client.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolves_extension_by_alias(self, tool, tool_context, mock_ari_client):
        # 6000 has alias "support" in the shared tool_config fixture
        mock_ari_client.send_command = AsyncMock(return_value={"name": "SIP/6000", "state": "NOT_INUSE"})

        result = await tool.execute({"extension": "support"}, tool_context)
        assert result["status"] == "success"
        assert result["extension"] == "6000"
        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_auto_detects_tech_via_ari_endpoints_when_not_configured(self, tool, tool_context, mock_ari_client):
        # Remove internal config so the tool must auto-detect using endpoints API.
        tool_context.config["tools"]["extensions"]["internal"] = {}
        tool_context.config["tools"]["check_extension_status"] = {"restrict_to_configured_extensions": False}

        async def send_command_side_effect(method, resource, data=None, params=None):
            if method == "GET" and resource == "endpoints/PJSIP/2765":
                return {"technology": "PJSIP", "resource": "2765", "state": "online", "channel_ids": []}
            if method == "GET" and resource == "deviceStates/PJSIP%2F2765":
                return {"name": "PJSIP/2765", "state": "NOT_INUSE"}
            if method == "GET" and resource == "channels":
                return []
            raise Exception(f"Unexpected ARI call: {method} {resource}")

        mock_ari_client.send_command = AsyncMock(side_effect=send_command_side_effect)

        result = await tool.execute({"extension": "2765"}, tool_context)
        assert result["status"] == "success"
        assert result["device_state_id"] == "PJSIP/2765"
        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_falls_back_to_endpoint_state_when_device_state_fails(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"]["internal"] = {}
        tool_context.config["tools"]["check_extension_status"] = {"restrict_to_configured_extensions": False}

        async def send_command_side_effect(method, resource, data=None, params=None):
            if method == "GET" and resource == "endpoints/PJSIP/2765":
                return {"technology": "PJSIP", "resource": "2765", "state": "online", "channel_ids": []}
            if method == "GET" and resource == "deviceStates/PJSIP%2F2765":
                raise Exception("404 Not Found")
            if method == "GET" and resource == "channels":
                return []
            raise Exception(f"Unexpected ARI call: {method} {resource}")

        mock_ari_client.send_command = AsyncMock(side_effect=send_command_side_effect)

        result = await tool.execute({"extension": "2765"}, tool_context)
        assert result["status"] == "success"
        assert result["availability_source"] == "endpoint_state"
        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_marks_extension_busy_when_endpoint_state_is_online_and_active_channel_found(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"]["internal"] = {}
        tool_context.config["tools"]["check_extension_status"] = {"restrict_to_configured_extensions": False}

        async def send_command_side_effect(method, resource, data=None, params=None):
            if method == "GET" and resource == "endpoints/PJSIP/2765":
                return {"technology": "PJSIP", "resource": "2765", "state": "online", "channel_ids": []}
            if method == "GET" and resource == "deviceStates/PJSIP%2F2765":
                raise Exception("404 Not Found")
            if method == "GET" and resource == "channels":
                return [
                    {"id": "PJSIP/2765-00000001", "name": "PJSIP/2765-00000001", "state": "Up"},
                ]
            raise Exception(f"Unexpected ARI call: {method} {resource}")

        mock_ari_client.send_command = AsyncMock(side_effect=send_command_side_effect)

        result = await tool.execute({"extension": "2765"}, tool_context)
        assert result["status"] == "success"
        assert result["availability_source"] == "endpoint_state+endpoint_channels"
        assert result["available"] is False
        assert result["endpoint_channel_ids"] == ["PJSIP/2765-00000001"]
        assert result["message"] == "Extension 2765 is in use (active_endpoint_channels_detected)."

    @pytest.mark.asyncio
    async def test_marks_extension_unavailable_when_endpoint_state_fallback_channel_lookup_fails(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"]["internal"] = {}
        tool_context.config["tools"]["check_extension_status"] = {"restrict_to_configured_extensions": False}

        async def send_command_side_effect(method, resource, data=None, params=None):
            if method == "GET" and resource == "endpoints/PJSIP/2765":
                return {"technology": "PJSIP", "resource": "2765", "state": "online", "channel_ids": []}
            if method == "GET" and resource == "deviceStates/PJSIP%2F2765":
                raise Exception("404 Not Found")
            if method == "GET" and resource == "channels":
                raise Exception("channels unavailable")
            raise Exception(f"Unexpected ARI call: {method} {resource}")

        mock_ari_client.send_command = AsyncMock(side_effect=send_command_side_effect)

        result = await tool.execute({"extension": "2765"}, tool_context)
        assert result["status"] == "success"
        assert result["availability_source"] == "endpoint_state+endpoint_channels_lookup_failed"
        assert result["available"] is False
        assert result["message"] == "Extension 2765 is in use (channel_lookup_failed)."

    @pytest.mark.asyncio
    async def test_marks_extension_busy_when_active_channel_is_detected(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"]["internal"]["6000"]["device_state_tech"] = "SIP"

        async def send_command_side_effect(method, resource, data=None, params=None):
            if method == "GET" and resource == "endpoints/SIP/6000":
                return {"technology": "SIP", "resource": "6000", "state": "online", "channel_ids": []}
            if method == "GET" and resource == "deviceStates/SIP%2F6000":
                return {"name": "SIP/6000", "state": "NOT_INUSE"}
            if method == "GET" and resource == "channels":
                return [
                    {"id": "SIP/6000-00000001", "name": "SIP/6000-00000001", "state": "Up"},
                ]
            raise Exception(f"Unexpected ARI call: {method} {resource}")

        mock_ari_client.send_command = AsyncMock(side_effect=send_command_side_effect)

        result = await tool.execute({"extension": "6000"}, tool_context)
        assert result["status"] == "success"
        assert result["available"] is False
        assert result["availability_source"] == "device_state+endpoint_channels"
        assert result["endpoint_channel_ids"] == ["SIP/6000-00000001"]
        assert result["message"] == "Extension 6000 is in use (active_endpoint_channels_detected)."
        assert "active endpoint channel(s)" in str(result.get("warnings", []))

    @pytest.mark.asyncio
    async def test_does_not_match_prefixed_extension_in_active_channels(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"]["internal"].setdefault("600", {})["device_state_tech"] = "SIP"

        async def send_command_side_effect(method, resource, data=None, params=None):
            if method == "GET" and resource == "endpoints/SIP/600":
                return {"technology": "SIP", "resource": "600", "state": "online", "channel_ids": []}
            if method == "GET" and resource == "deviceStates/SIP%2F600":
                return {"name": "SIP/600", "state": "NOT_INUSE"}
            if method == "GET" and resource == "channels":
                return [
                    {"id": "SIP/6000-00000001", "name": "SIP/6000-00000001", "state": "Up"},
                ]
            raise Exception(f"Unexpected ARI call: {method} {resource}")

        mock_ari_client.send_command = AsyncMock(side_effect=send_command_side_effect)

        result = await tool.execute({"extension": "600"}, tool_context)
        assert result["status"] == "success"
        assert result["available"] is True
        assert result["availability_source"] == "device_state"
        assert result["endpoint_channel_ids"] == []

    @pytest.mark.asyncio
    async def test_marks_extension_unavailable_if_active_channels_check_fails(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"]["internal"]["6000"]["device_state_tech"] = "SIP"

        async def send_command_side_effect(method, resource, data=None, params=None):
            if method == "GET" and resource == "endpoints/SIP/6000":
                return {"technology": "SIP", "resource": "6000", "state": "online", "channel_ids": []}
            if method == "GET" and resource == "deviceStates/SIP%2F6000":
                return {"name": "SIP/6000", "state": "NOT_INUSE"}
            if method == "GET" and resource == "channels":
                raise Exception("channels unavailable")
            raise Exception(f"Unexpected ARI call: {method} {resource}")

        mock_ari_client.send_command = AsyncMock(side_effect=send_command_side_effect)

        result = await tool.execute({"extension": "6000"}, tool_context)
        assert result["status"] == "success"
        assert result["available"] is False
        assert result["availability_source"] == "device_state+endpoint_channels_lookup_failed"
        assert result["endpoint_channel_ids"] == []
        assert result["message"] == "Extension 6000 is in use (channel_lookup_failed)."

    @pytest.mark.asyncio
    async def test_falls_back_to_list_endpoints_and_device_states(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"]["internal"] = {
            "6000": {
                "name": "Live Agent",
                "dial_string": "SIP/6000",
                "device_state_tech": "auto",
            }
        }

        async def send_command_side_effect(method, resource, data=None, params=None):
            if method == "GET" and resource == "endpoints/SIP/6000":
                raise Exception("404 Not Found")
            if method == "GET" and resource == "endpoints":
                return [{"technology": "SIP", "resource": "6000", "state": "online", "channel_ids": []}]
            if method == "GET" and resource == "deviceStates/SIP%2F6000":
                raise Exception("404 Not Found")
            if method == "GET" and resource == "deviceStates":
                return [{"name": "SIP/6000", "state": "NOT_INUSE"}]
            if method == "GET" and resource == "channels":
                return []
            raise Exception(f"Unexpected ARI call: {method} {resource}")

        mock_ari_client.send_command = AsyncMock(side_effect=send_command_side_effect)

        result = await tool.execute({"extension": "6000"}, tool_context)
        assert result["status"] == "success"
        assert result["device_state_id"] == "SIP/6000"
        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_resolves_transfer_destination_key_to_extension(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["transfer"] = {
            "destinations": {
                "support_agent": {"type": "extension", "target": "6000", "description": "Support"},
            }
        }

        mock_ari_client.send_command = AsyncMock(return_value={"name": "SIP/6000", "state": "NOT_INUSE"})

        result = await tool.execute({"extension": "support_agent"}, tool_context)
        assert result["status"] == "success"
        assert result["extension"] == "6000"
        assert result["device_state_id"] == "SIP/6000"
        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_recovers_from_invalid_device_state_by_trying_other_tech(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"]["internal"]["2765"] = {
            "name": "Agent",
            "dial_string": "SIP/2765",  # wrong tech
            "device_state_tech": "auto",
        }

        async def send_command_side_effect(method, resource, data=None, params=None):
            # First attempt uses dial_string tech (SIP) -> INVALID.
            if method == "GET" and resource == "deviceStates/SIP%2F2765":
                return {"name": "SIP/2765", "state": "INVALID"}
            # Tool then probes endpoints and retries deviceStates with PJSIP -> NOT_INUSE.
            if method == "GET" and resource == "endpoints/PJSIP/2765":
                return {"technology": "PJSIP", "resource": "2765", "state": "online", "channel_ids": []}
            if method == "GET" and resource == "deviceStates/PJSIP%2F2765":
                return {"name": "PJSIP/2765", "state": "NOT_INUSE"}
            if method == "GET" and resource == "channels":
                return []
            raise Exception(f"Unexpected ARI call: {method} {resource}")

        mock_ari_client.send_command = AsyncMock(side_effect=send_command_side_effect)

        result = await tool.execute({"extension": "2765"}, tool_context)
        assert result["status"] == "success"
        assert result["device_state_id"] == "PJSIP/2765"
        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_blocks_unconfigured_numeric_extension_by_default(self, tool, tool_context, mock_ari_client):
        result = await tool.execute({"extension": "2766"}, tool_context)

        assert result["status"] == "failed"
        assert result["guardrail_blocked"] is True
        assert result["extension"] == "2766"
        assert "Allowed extensions:" in result["message"]
        mock_ari_client.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocks_when_no_configured_extensions_exist(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"]["internal"] = {}
        tool_context.config["tools"]["transfer"] = {"destinations": {}}

        result = await tool.execute({"extension": "2765"}, tool_context)

        assert result["status"] == "failed"
        assert result["guardrail_blocked"] is True
        assert "No configured extensions" in result["message"]
        mock_ari_client.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocks_unresolved_non_numeric_target_when_guardrail_enabled(self, tool, tool_context, mock_ari_client):
        result = await tool.execute({"extension": "supportx"}, tool_context)

        assert result["status"] == "failed"
        assert result["guardrail_blocked"] is True
        mock_ari_client.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_numeric_extension_when_present_in_transfer_destinations(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"]["internal"] = {}
        tool_context.config["tools"]["transfer"] = {
            "technology": "SIP",
            "destinations": {
                "sales_agent": {"type": "extension", "target": "6000", "description": "Sales"},
            }
        }
        mock_ari_client.send_command = AsyncMock(return_value={"name": "SIP/6000", "state": "NOT_INUSE"})

        result = await tool.execute({"extension": "6000"}, tool_context)

        assert result["status"] == "success"
        assert result["extension"] == "6000"
        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_can_disable_guardrail_for_legacy_unconfigured_checks(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["check_extension_status"] = {"restrict_to_configured_extensions": False}
        tool_context.config["tools"]["extensions"]["internal"] = {}

        async def send_command_side_effect(method, resource, data=None, params=None):
            if method == "GET" and resource == "endpoints/PJSIP/2765":
                return {"technology": "PJSIP", "resource": "2765", "state": "online", "channel_ids": []}
            if method == "GET" and resource == "deviceStates/PJSIP%2F2765":
                return {"name": "PJSIP/2765", "state": "NOT_INUSE"}
            raise Exception(f"Unexpected ARI call: {method} {resource}")

        mock_ari_client.send_command = AsyncMock(side_effect=send_command_side_effect)

        result = await tool.execute({"extension": "2765"}, tool_context)

        assert result["status"] == "success"
        assert result["device_state_id"] == "PJSIP/2765"
