"""
Unit tests for LiveAgentTransferTool.
"""

import pytest

from src.tools.telephony.live_agent_transfer import LiveAgentTransferTool


class TestLiveAgentTransferTool:
    @pytest.fixture
    def tool(self):
        return LiveAgentTransferTool()

    def test_definition(self, tool):
        d = tool.definition
        assert d.name == "live_agent_transfer"
        assert d.category.value == "telephony"
        assert d.requires_channel is True
        assert len(d.parameters) == 1
        assert d.parameters[0].name == "target"
        assert d.parameters[0].required is False

    @pytest.mark.asyncio
    async def test_uses_explicit_live_agent_destination_key(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["transfer"] = {
            "enabled": True,
            "live_agent_destination_key": "tier2_live",
            "destinations": {
                "sales_agent": {"type": "extension", "target": "2765", "description": "Sales"},
                "tier2_live": {"type": "extension", "target": "6000", "description": "Live Agent", "live_agent": True},
            },
        }

        result = await tool.execute({}, tool_context)

        assert result["status"] == "success"
        assert result["destination"] == "6000"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["resource"] == f"channels/{tool_context.caller_channel_id}/continue"
        assert call_args["params"]["extension"] == "6000"

    @pytest.mark.asyncio
    async def test_explicit_target_extension_overrides_default_resolution(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                "2765": {
                    "name": "Live Agent 2",
                    "aliases": ["haider"],
                    "dial_string": "PJSIP/2765",
                    "transfer": True,
                },
                "6000": {
                    "name": "Live Agent",
                    "aliases": ["support"],
                    "dial_string": "SIP/6000",
                    "transfer": True,
                },
            }
        }

        result = await tool.execute({"target": "2765"}, tool_context)

        assert result["status"] == "success"
        assert result["destination"] == "2765"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["resource"] == f"channels/{tool_context.caller_channel_id}/continue"
        assert call_args["params"]["extension"] == "2765"

    @pytest.mark.asyncio
    async def test_explicit_target_numeric_string_matches_integer_extension_key(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                6000: {
                    "name": "Live Agent",
                    "aliases": ["support"],
                    "dial_string": "SIP/6000",
                    "transfer": True,
                },
            }
        }

        result = await tool.execute({"target": "6000"}, tool_context)

        assert result["status"] == "success"
        assert result["destination"] == "6000"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["params"]["extension"] == "6000"

    @pytest.mark.asyncio
    async def test_destination_override_takes_precedence_over_explicit_target(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["transfer"] = {
            "enabled": True,
            "live_agent_destination_key": "tier2_live",
            "destinations": {
                "tier2_live": {"type": "extension", "target": "6000", "description": "Tier 2", "live_agent": True},
            },
        }
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                "2765": {
                    "name": "Live Agent 2",
                    "dial_string": "PJSIP/2765",
                    "transfer": True,
                },
                "6000": {
                    "name": "Tier 2",
                    "dial_string": "SIP/6000",
                    "transfer": True,
                },
            }
        }

        result = await tool.execute({"target": "2765"}, tool_context)

        assert result["status"] == "success"
        assert result["destination"] == "6000"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["params"]["extension"] == "6000"

    @pytest.mark.asyncio
    async def test_explicit_target_alias_routes_to_matching_extension(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                "2765": {
                    "name": "Live Agent 2",
                    "aliases": ["haider"],
                    "dial_string": "PJSIP/2765",
                    "transfer": True,
                },
                "6000": {
                    "name": "Support Team",
                    "aliases": ["support"],
                    "dial_string": "SIP/6000",
                    "transfer": True,
                },
            }
        }

        result = await tool.execute({"target": "support"}, tool_context)

        assert result["status"] == "success"
        assert result["destination"] == "6000"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["params"]["extension"] == "6000"

    @pytest.mark.asyncio
    async def test_explicit_target_fails_when_not_configured(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                "6000": {"name": "Live Agent", "dial_string": "SIP/6000", "transfer": True},
            }
        }

        result = await tool.execute({"target": "2765"}, tool_context)

        assert result["status"] == "failed"
        assert "not configured" in result["message"]
        mock_ari_client.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_explicit_target_fails_when_transfer_disabled(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                "6000": {"name": "Live Agent", "dial_string": "SIP/6000", "transfer": False},
            }
        }

        result = await tool.execute({"target": "6000"}, tool_context)

        assert result["status"] == "failed"
        assert "not enabled for transfers" in result["message"]
        mock_ari_client.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_live_agent_key_when_config_not_set(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["transfer"] = {
            "enabled": True,
            "destinations": {
                "live_agent": {"type": "extension", "target": "6001", "description": "Live Agent"},
            },
        }
        # Ensure no Live Agents are configured so destination fallback can be exercised.
        tool_context.config["tools"]["extensions"] = {"internal": {}}

        result = await tool.execute({}, tool_context)

        assert result["status"] == "success"
        assert result["destination"] == "6001"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["resource"] == f"channels/{tool_context.caller_channel_id}/continue"
        assert call_args["params"]["extension"] == "6001"

    @pytest.mark.asyncio
    async def test_fails_when_live_agent_destination_not_configured(self, tool, tool_context):
        tool_context.config["tools"]["transfer"] = {
            "enabled": True,
            "destinations": {
                "sales_agent": {"type": "extension", "target": "2765", "description": "Sales"},
            },
        }
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                "2765": {"name": "Sales Agent", "aliases": ["sales"], "dial_string": "SIP/2765"},
            }
        }

        result = await tool.execute({}, tool_context)
        assert result["status"] == "failed"
        assert "Live agent transfer is not configured" in result["message"]

    @pytest.mark.asyncio
    async def test_falls_back_to_internal_live_agent_extension_and_maps_to_transfer_destination(
        self, tool, tool_context, mock_ari_client
    ):
        tool_context.config["tools"]["transfer"] = {
            "enabled": True,
            "destinations": {
                "support_agent": {"type": "extension", "target": "6000", "description": "Support agent"},
                "live_agent": {"type": "extension", "target": "6000", "description": "Live Agent"},
            },
        }
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                "6000": {
                    "name": "Live Agent",
                    "aliases": ["agent", "human"],
                    "dial_string": "SIP/6000",
                    "description": "Live customer service representative",
                },
            }
        }

        result = await tool.execute({}, tool_context)

        assert result["status"] == "success"
        assert result["destination"] == "6000"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["resource"] == f"channels/{tool_context.caller_channel_id}/continue"
        assert call_args["params"]["extension"] == "6000"

    @pytest.mark.asyncio
    async def test_falls_back_to_internal_live_agent_extension_without_transfer_destinations(
        self, tool, tool_context, mock_ari_client
    ):
        tool_context.config["tools"]["transfer"] = {
            "enabled": True,
            "destinations": {},
        }
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                "7007": {
                    "name": "Live Agent",
                    "aliases": ["live agent"],
                    "dial_string": "PJSIP/7007",
                    "description": "Escalation desk",
                },
            }
        }

        result = await tool.execute({}, tool_context)

        assert result["status"] == "success"
        assert result["destination"] == "7007"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["resource"] == f"channels/{tool_context.caller_channel_id}/continue"
        assert call_args["params"]["extension"] == "7007"

    @pytest.mark.asyncio
    async def test_ignores_misconfigured_live_agent_destination_key_and_uses_internal_extension(
        self, tool, tool_context, mock_ari_client
    ):
        tool_context.config["tools"]["transfer"] = {
            "enabled": True,
            # Misconfigured: points at a normal destination (not marked live_agent).
            "live_agent_destination_key": "support_agent",
            "destinations": {
                "support_agent": {"type": "extension", "target": "2765", "description": "Support agent"},
            },
        }
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                "6000": {"name": "Live Agent", "description": "Live customer service rep", "dial_string": "SIP/6000", "transfer": True},
            }
        }

        result = await tool.execute({}, tool_context)

        assert result["status"] == "success"
        assert result["destination"] == "6000"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["resource"] == f"channels/{tool_context.caller_channel_id}/continue"
        assert call_args["params"]["extension"] == "6000"

    @pytest.mark.asyncio
    async def test_prefers_internal_live_agents_over_destination_live_agent_flag_when_no_explicit_override(
        self, tool, tool_context, mock_ari_client
    ):
        tool_context.config["tools"]["transfer"] = {
            "enabled": True,
            "destinations": {
                "tier2_live": {"type": "extension", "target": "6000", "description": "Tier 2", "live_agent": True},
            },
        }
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                "7007": {"name": "Live Agent", "dial_string": "PJSIP/7007", "description": "Escalation desk", "transfer": True},
            }
        }

        result = await tool.execute({}, tool_context)

        assert result["status"] == "success"
        assert result["destination"] == "7007"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["resource"] == f"channels/{tool_context.caller_channel_id}/continue"
        assert call_args["params"]["extension"] == "7007"

    @pytest.mark.asyncio
    async def test_explicit_destination_override_wins_over_internal_live_agents(
        self, tool, tool_context, mock_ari_client
    ):
        tool_context.config["tools"]["transfer"] = {
            "enabled": True,
            "live_agent_destination_key": "tier2_live",
            "destinations": {
                "tier2_live": {"type": "extension", "target": "6000", "description": "Tier 2", "live_agent": True},
            },
        }
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                "7007": {"name": "Live Agent", "dial_string": "PJSIP/7007", "description": "Escalation desk", "transfer": True},
            }
        }

        result = await tool.execute({}, tool_context)

        assert result["status"] == "success"
        assert result["destination"] == "6000"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["resource"] == f"channels/{tool_context.caller_channel_id}/continue"
        assert call_args["params"]["extension"] == "6000"

    @pytest.mark.asyncio
    async def test_misconfigured_destination_override_does_not_prevent_destination_fallback_scan(
        self, tool, tool_context, mock_ari_client
    ):
        tool_context.config["tools"]["transfer"] = {
            "enabled": True,
            # Misconfigured: points to non-live destination.
            "live_agent_destination_key": "support_agent",
            "destinations": {
                "support_agent": {"type": "extension", "target": "2765", "description": "Support agent"},
                "tier2_live": {"type": "extension", "target": "6000", "description": "Tier 2", "live_agent": True},
            },
        }
        # Ensure no Live Agents are configured so destination fallback can be exercised.
        tool_context.config["tools"]["extensions"] = {"internal": {}}

        result = await tool.execute({}, tool_context)

        assert result["status"] == "success"
        assert result["destination"] == "6000"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["resource"] == f"channels/{tool_context.caller_channel_id}/continue"
        assert call_args["params"]["extension"] == "6000"

    @pytest.mark.asyncio
    async def test_fails_when_internal_live_agents_are_ambiguous_even_if_destination_live_agent_exists(
        self, tool, tool_context, mock_ari_client
    ):
        tool_context.config["tools"]["transfer"] = {
            "enabled": True,
            "destinations": {
                "tier2_live": {"type": "extension", "target": "6000", "description": "Tier 2", "live_agent": True},
            },
        }
        tool_context.config["tools"]["extensions"] = {
            "internal": {
                "7007": {"name": "Live Agent", "dial_string": "PJSIP/7007", "description": "Escalation desk", "transfer": True},
                "7008": {"name": "Live Agent", "dial_string": "PJSIP/7008", "description": "Backup desk", "transfer": True},
            }
        }

        result = await tool.execute({}, tool_context)

        assert result["status"] == "failed"
        assert "Multiple Live Agents" in result["message"]
        mock_ari_client.send_command.assert_not_called()
