"""
Unit tests for CheckExtensionStatusTool (AAVA-53).
"""

import pytest
from unittest.mock import AsyncMock

from src.tools.context import ToolExecutionContext
from src.tools.telephony.check_extension_status import CheckExtensionStatusTool


@pytest.fixture
def tool_context_factory():
    """
    Factory for building a standalone ToolExecutionContext with custom
    tools.extensions.internal / tools.check_extension_status config, for tests
    that need per-test extension/device_states setups (multi-state resolution).
    Mirrors the shared tool_context/mock_ari_client fixtures in
    tests/tools/conftest.py.
    """

    def _make(extensions=None, tool_cfg=None, transfer_cfg=None):
        client = AsyncMock()
        client.send_command = AsyncMock(return_value={})

        session_store = AsyncMock()
        session_store.get_by_call_id = AsyncMock(return_value=None)

        config = {
            "tools": {
                "extensions": {"internal": extensions or {}},
                "check_extension_status": tool_cfg or {},
                "transfer": transfer_cfg or {},
            }
        }
        return ToolExecutionContext(
            ari_client=client,
            session_store=session_store,
            config=config,
            call_id="test_call_multi_state",
            caller_channel_id="PJSIP/caller-00000001",
        )

    return _make


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
        assert result["message"] == "Extension 6000 is available."

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
        assert result["message"] == "Extension 2765 is on a call."

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
        assert result["message"] == "Extension 2765 is on a call."

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
        assert result["message"] == "Extension 2765 is unavailable."

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
        assert result["message"] == "Extension 6000 is on a call."
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
        assert result["message"] == "Extension 6000 is unavailable."

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


from src.tools.telephony.check_extension_status import (
    resolve_state_mapping, classify_device_state,
)

class TestStateMapping:
    def test_defaults_classify_canonical_values(self):
        m = resolve_state_mapping(None)
        assert classify_device_state("NOT_INUSE", m) == "free"
        assert classify_device_state("INUSE", m) == "busy"
        assert classify_device_state("ONHOLD", m) == "busy"
        assert classify_device_state("UNAVAILABLE", m) == "unavailable"

    def test_unknown_value_is_fail_closed_unavailable(self):
        m = resolve_state_mapping(None)
        assert classify_device_state("WEIRD_CUSTOM", m) == "unavailable"

    def test_operator_override_moves_onhold_to_free(self):
        m = resolve_state_mapping({"free": ["NOT_INUSE", "ONHOLD"]})
        assert classify_device_state("ONHOLD", m) == "free"
        assert classify_device_state("INUSE", m) == "busy"

    def test_case_insensitive(self):
        m = resolve_state_mapping(None)
        assert classify_device_state("not_inuse", m) == "free"

    def test_explicit_empty_bucket_is_authoritative(self):
        # CodeRabbit round 2 (A): a present-but-empty bucket must NOT fall back to the
        # default for that bucket -- only an absent/non-list key should fall back.
        m = resolve_state_mapping({"free": []})
        assert m["free"] == set()
        assert m["unavailable"] == {"UNAVAILABLE", "INVALID", "UNKNOWN"}

    def test_value_only_in_unavailable_bucket_classifies_unavailable(self):
        # CodeRabbit round 2 (C): the "unavailable" bucket must be consulted explicitly,
        # not merely rely on the fail-closed fallback.
        m = resolve_state_mapping({"unavailable": ["CUSTOM_OFFLINE"]})
        assert classify_device_state("CUSTOM_OFFLINE", m) == "unavailable"

    def test_precedence_free_wins_over_busy_when_value_in_both(self):
        # CodeRabbit round 2 (C): precedence is free > busy > unavailable.
        m = resolve_state_mapping({"free": ["CUSTOM_STATE"], "busy": ["CUSTOM_STATE"]})
        assert classify_device_state("CUSTOM_STATE", m) == "free"


from src.tools.telephony.check_extension_status import aggregate_availability

class TestAggregateAvailability:
    def test_all_free_is_available(self):
        out = aggregate_availability([
            {"id": "PJSIP/102", "role": "native", "state": "NOT_INUSE", "classification": "free"},
            {"id": "Custom:DND102", "role": "custom", "state": "NOT_INUSE", "classification": "free", "status": "dnd"},
        ])
        assert out["available"] is True
        assert out["availability_status"] == "available"

    def test_native_inuse_is_in_call(self):
        out = aggregate_availability([
            {"id": "PJSIP/102", "role": "native", "state": "INUSE", "classification": "busy"},
        ])
        assert out["available"] is False
        assert out["availability_status"] == "in_call"

    def test_custom_dnd_busy_labels_dnd(self):
        out = aggregate_availability([
            {"id": "PJSIP/102", "role": "native", "state": "NOT_INUSE", "classification": "free"},
            {"id": "Custom:DND102", "role": "custom", "state": "INUSE", "classification": "busy", "status": "dnd"},
        ])
        assert out["available"] is False
        assert out["availability_status"] == "dnd"

    def test_in_call_wins_over_dnd_by_priority(self):
        out = aggregate_availability([
            {"id": "PJSIP/102", "role": "native", "state": "INUSE", "classification": "busy"},
            {"id": "Custom:DND102", "role": "custom", "state": "INUSE", "classification": "busy", "status": "dnd"},
        ])
        assert out["availability_status"] == "in_call"

    def test_unavailable_when_only_unavailable(self):
        out = aggregate_availability([
            {"id": "PJSIP/102", "role": "native", "state": "UNAVAILABLE", "classification": "unavailable"},
        ])
        assert out["available"] is False
        assert out["availability_status"] == "unavailable"


class TestMultiStateExecute:
    @pytest.fixture
    def tool(self):
        return CheckExtensionStatusTool()

    @pytest.mark.asyncio
    async def test_native_free_custom_dnd_busy_reports_dnd(self, tool_context_factory):
        ctx = tool_context_factory(extensions={"102": {
            "dial_string": "PJSIP/102", "transfer": True,
            "device_states": [{"id": "Custom:DND102", "status": "dnd"}],
        }})
        async def sc(method, resource, data=None, params=None):
            if "PJSIP" in resource and "channels" not in resource:
                return {"name": "PJSIP/102", "state": "NOT_INUSE"}
            if "Custom" in resource:
                return {"name": "Custom:DND102", "state": "INUSE"}
            if resource == "channels":
                return []
            return {}
        ctx.ari_client.send_command = AsyncMock(side_effect=sc)
        out = await CheckExtensionStatusTool().execute({"extension": "102"}, ctx)
        assert out["available"] is False
        assert out["availability_status"] == "dnd"
        ids = {s["id"] for s in out["device_states"]}
        assert "PJSIP/102" in ids and "Custom:DND102" in ids

    @pytest.mark.asyncio
    async def test_backcompat_single_state_unchanged(self, tool, tool_context, mock_ari_client):
        mock_ari_client.send_command = AsyncMock(return_value={"name": "SIP/6000", "state": "NOT_INUSE"})
        out = await tool.execute({"extension": "6000", "tech": "SIP"}, tool_context)
        assert out["available"] is True
        assert out["availability_status"] == "available"


class TestGuardrailCustomStates:
    @pytest.mark.asyncio
    async def test_configured_custom_state_is_allowed(self, tool_context_factory):
        ctx = tool_context_factory(
            extensions={"102": {"dial_string": "PJSIP/102", "transfer": True,
                                "device_states": [{"id": "Custom:DND102", "status": "dnd"}]}},
            tool_cfg={"restrict_to_configured_extensions": True})
        async def sc(method, resource, data=None, params=None):
            if "Custom" in resource:
                return {"name": "Custom:DND102", "state": "NOT_INUSE"}
            if resource == "channels":
                return []
            return {"name": "PJSIP/102", "state": "NOT_INUSE"}
        ctx.ari_client.send_command = AsyncMock(side_effect=sc)
        out = await CheckExtensionStatusTool().execute({"extension": "102"}, ctx)
        assert out.get("guardrail_blocked") is not True
        assert out["available"] is True

    @pytest.mark.asyncio
    async def test_unconfigured_state_id_param_still_blocked(self, tool_context_factory):
        ctx = tool_context_factory(
            extensions={"102": {"dial_string": "PJSIP/102", "transfer": True}},
            tool_cfg={"restrict_to_configured_extensions": True})
        out = await CheckExtensionStatusTool().execute({"extension": "102", "device_state_id": "Custom:HACK"}, ctx)
        assert out["guardrail_blocked"] is True

    @pytest.mark.asyncio
    async def test_configured_custom_state_id_param_dedups_and_labels_dnd(self, tool_context_factory):
        # A device_state_id param that matches a configured custom device_states entry
        # (#577 guardrail carve-out) must be represented exactly once, as role="custom"
        # with the entry's configured status -- not also as a "native" record that
        # mislabels a BUSY custom state as an active call (#600 dedup fix).
        ctx = tool_context_factory(
            extensions={"102": {"dial_string": "PJSIP/102", "transfer": True,
                                "device_states": [{"id": "Custom:DND102", "status": "dnd"}]}},
            tool_cfg={"restrict_to_configured_extensions": True})

        async def sc(method, resource, data=None, params=None):
            if "Custom" in resource:
                return {"name": "Custom:DND102", "state": "BUSY"}
            if resource == "channels":
                return []
            return {"name": "PJSIP/102", "state": "NOT_INUSE"}

        ctx.ari_client.send_command = AsyncMock(side_effect=sc)
        out = await CheckExtensionStatusTool().execute(
            {"extension": "102", "device_state_id": "Custom:DND102"}, ctx)
        assert out["availability_status"] == "dnd"
        assert out["available"] is False
        matching = [s for s in out["device_states"] if s["id"] == "Custom:DND102"]
        assert len(matching) == 1
        assert matching[0]["role"] == "custom"


class TestCustomStateFailOpen:
    """CodeRabbit round 2 (D): a custom device state is an opt-in "unavailable" signal.
    It must only make the extension unavailable when its raw value classifies as busy;
    an unset/UNKNOWN/empty/invalid custom state must NOT block."""

    @pytest.mark.asyncio
    async def test_unset_custom_dnd_does_not_block_availability(self, tool_context_factory):
        ctx = tool_context_factory(extensions={"102": {
            "dial_string": "PJSIP/102", "transfer": True,
            "device_states": [{"id": "Custom:DND102", "status": "dnd"}],
        }})

        async def sc(method, resource, data=None, params=None):
            if "PJSIP" in resource and "channels" not in resource:
                return {"name": "PJSIP/102", "state": "NOT_INUSE"}
            if "Custom" in resource:
                return {"name": "Custom:DND102", "state": "UNKNOWN"}
            if resource == "channels":
                return []
            return {}

        ctx.ari_client.send_command = AsyncMock(side_effect=sc)
        out = await CheckExtensionStatusTool().execute({"extension": "102"}, ctx)
        assert out["available"] is True
        assert out["availability_status"] == "available"

    @pytest.mark.asyncio
    async def test_custom_dnd_busy_still_blocks_availability(self, tool_context_factory):
        # Guard the happy path: an actively-set custom BUSY state still yields dnd.
        ctx = tool_context_factory(extensions={"102": {
            "dial_string": "PJSIP/102", "transfer": True,
            "device_states": [{"id": "Custom:DND102", "status": "dnd"}],
        }})

        async def sc(method, resource, data=None, params=None):
            if "PJSIP" in resource and "channels" not in resource:
                return {"name": "PJSIP/102", "state": "NOT_INUSE"}
            if "Custom" in resource:
                return {"name": "Custom:DND102", "state": "BUSY"}
            if resource == "channels":
                return []
            return {}

        ctx.ari_client.send_command = AsyncMock(side_effect=sc)
        out = await CheckExtensionStatusTool().execute({"extension": "102"}, ctx)
        assert out["available"] is False
        assert out["availability_status"] == "dnd"


class TestMessageMatchesAggregatedStatus:
    """CodeRabbit round 2 (E): availability_message must always reflect the FINAL
    availability_status, even with no custom device_states (e.g., a state_mapping
    override on native state alone)."""

    @pytest.mark.asyncio
    async def test_message_matches_status_when_native_state_remapped_to_busy(self, tool_context_factory):
        ctx = tool_context_factory(
            extensions={"102": {"dial_string": "PJSIP/102", "transfer": True}},
            tool_cfg={"state_mapping": {"free": ["IDLE_STATE_NOT_USED"], "busy": ["NOT_INUSE"]}},
        )
        ctx.ari_client.send_command = AsyncMock(return_value={"name": "PJSIP/102", "state": "NOT_INUSE"})
        out = await CheckExtensionStatusTool().execute({"extension": "102"}, ctx)
        assert out["available"] is False
        assert out["availability_status"] == "busy"
        assert "available" not in out["message"].lower()
        assert "busy" in out["message"].lower()
