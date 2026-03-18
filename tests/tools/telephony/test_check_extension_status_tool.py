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

        # Ensure URL-encoded slash
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["method"] == "GET"
        assert call_args["resource"] == "deviceStates/SIP%2F6000"

    @pytest.mark.asyncio
    async def test_queries_device_state_with_param_tech(self, tool, tool_context, mock_ari_client):
        # Remove internal config entry to force param-based resolution
        tool_context.config["tools"]["extensions"]["internal"].pop("6000", None)
        mock_ari_client.send_command = AsyncMock(return_value={"name": "PJSIP/2765", "state": "INUSE"})

        result = await tool.execute({"extension": "2765", "tech": "PJSIP"}, tool_context)
        assert result["status"] == "success"
        assert result["device_state_id"] == "PJSIP/2765"
        assert result["available"] is False

    @pytest.mark.asyncio
    async def test_device_state_id_override(self, tool, tool_context, mock_ari_client):
        mock_ari_client.send_command = AsyncMock(return_value={"name": "Custom/agentA", "state": "NOT_INUSE"})

        result = await tool.execute({"extension": "ignored", "device_state_id": "Custom/agentA"}, tool_context)
        assert result["status"] == "success"
        assert result["device_state_id"] == "Custom/agentA"
        assert result["available"] is True

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
        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_falls_back_to_endpoint_state_when_device_state_fails(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"]["internal"] = {}

        async def send_command_side_effect(method, resource, data=None, params=None):
            if method == "GET" and resource == "endpoints/PJSIP/2765":
                return {"technology": "PJSIP", "resource": "2765", "state": "online", "channel_ids": []}
            if method == "GET" and resource == "deviceStates/PJSIP%2F2765":
                raise Exception("404 Not Found")
            raise Exception(f"Unexpected ARI call: {method} {resource}")

        mock_ari_client.send_command = AsyncMock(side_effect=send_command_side_effect)

        result = await tool.execute({"extension": "2765"}, tool_context)
        assert result["status"] == "success"
        assert result["availability_source"] == "endpoint_state"
        assert result["available"] is True

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
            raise Exception(f"Unexpected ARI call: {method} {resource}")

        mock_ari_client.send_command = AsyncMock(side_effect=send_command_side_effect)

        result = await tool.execute({"extension": "2765"}, tool_context)
        assert result["status"] == "success"
        assert result["device_state_id"] == "PJSIP/2765"
        assert result["available"] is True
