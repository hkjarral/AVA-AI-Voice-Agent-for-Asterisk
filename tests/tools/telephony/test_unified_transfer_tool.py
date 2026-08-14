"""
Unit tests for UnifiedTransferTool destination resolution.
"""

import pytest
from unittest.mock import AsyncMock, Mock

from src.tools.telephony import deferred_transfer as deferred_transfer_mod
from src.tools.telephony.deferred_transfer import DEFERRED_TRANSFER_RESULT_KEY
from src.tools.telephony.unified_transfer import UnifiedTransferTool


class TestUnifiedTransferTool:
    @pytest.fixture
    def tool(self, mock_ari_client):
        mock_ari_client.dialplan_target_exists = AsyncMock(return_value=True)
        mock_ari_client.continue_in_dialplan = AsyncMock(return_value=True)
        return UnifiedTransferTool()

    def test_definition_uses_generic_destination_language(self, tool):
        definition = tool.definition
        assert definition.name == "blind_transfer"
        assert "support_agent" not in definition.description
        assert "sales_agent" not in definition.description

    @pytest.mark.asyncio
    async def test_resolves_destination_by_description_match(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["transfer"] = {
            "defer_until_playback_complete": False,
            "destinations": {
                "tier2_desk": {
                    "type": "extension",
                    "target": "6000",
                    "description": "Support Team",
                }
            }
        }

        result = await tool.execute({"destination": "support"}, tool_context)

        assert result["status"] == "success"
        assert result["type"] == "extension"
        assert result["destination"] == "6000"

        mock_ari_client.continue_in_dialplan.assert_awaited_once_with(
            tool_context.caller_channel_id,
            context="from-internal",
            extension="6000",
            priority=1,
        )

    @pytest.mark.asyncio
    async def test_human_intent_maps_to_single_extension_destination(self, tool, tool_context):
        tool_context.config["tools"]["transfer"] = {
            "defer_until_playback_complete": False,
            "destinations": {
                "frontdesk": {
                    "type": "extension",
                    "target": "6010",
                    "description": "Reception Desk",
                },
                "support_queue": {
                    "type": "queue",
                    "target": "500",
                    "description": "Support Queue",
                },
            }
        }

        result = await tool.execute({"destination": "live person"}, tool_context)

        assert result["status"] == "success"
        assert result["type"] == "extension"
        assert result["destination"] == "6010"

    @pytest.mark.asyncio
    async def test_resolves_destination_by_exact_target_number(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["transfer"] = {
            "defer_until_playback_complete": False,
            "destinations": {
                "support_agent": {
                    "type": "extension",
                    "target": "6000",
                    "description": "Support Agent",
                }
            }
        }

        result = await tool.execute({"destination": "6000"}, tool_context)

        assert result["status"] == "success"
        assert result["type"] == "extension"
        assert result["destination"] == "6000"

        mock_ari_client.continue_in_dialplan.assert_awaited_once_with(
            tool_context.caller_channel_id,
            context="from-internal",
            extension="6000",
            priority=1,
        )

    @pytest.mark.asyncio
    async def test_default_defers_transfer_and_stores_destination_context(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["transfer"] = {
            "extension_context": "from-internal",
            "destinations": {
                "support_agent": {
                    "type": "extension",
                    "target": "6000",
                    "description": "Support Agent",
                    "dialplan_context": "from-support",
                }
            },
        }

        result = await tool.execute({"destination": "support_agent"}, tool_context)

        assert result["status"] == "success"
        assert result["defer_until_playback_complete"] is True
        assert result[DEFERRED_TRANSFER_RESULT_KEY]["dialplan_context"] == "from-support"
        assert tool_context.session_store.get_by_call_id.return_value.pending_deferred_transfer["target"] == "6000"
        mock_ari_client.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_predial_strategy_originates_local_leg_during_deferral(self, tool, tool_context, mock_ari_client):
        engine = Mock()
        engine.register_predial_transfer_channel = Mock()
        mock_ari_client.engine = engine
        tool_context.caller_name = "WIRELESS CALLER"
        tool_context.caller_number = "13164619284"
        tool_context.config["tools"]["transfer"] = {
            "deferred_strategy": "predial_then_bridge",
            "extension_context": "from-internal",
            "predial_timeout_seconds": 12,
            "destinations": {
                "support_agent": {
                    "type": "extension",
                    "target": "6000",
                    "description": "Support Agent",
                    "dialplan_context": "from-support",
                }
            },
        }

        result = await tool.execute({"destination": "support_agent"}, tool_context)

        assert result["status"] == "success"
        action = result[DEFERRED_TRANSFER_RESULT_KEY]
        assert action["payload"]["predial"]["enabled"] is True
        assert action["payload"]["predial"]["endpoint"] == "Local/6000@from-support"
        assert tool_context.session_store.get_by_call_id.return_value.current_action["type"] == "predial_transfer"
        call_args = mock_ari_client.send_command.call_args.kwargs
        assert call_args["resource"] == "channels"
        assert call_args["params"]["endpoint"] == "Local/6000@from-support"
        assert call_args["params"]["callerId"] == '"WIRELESS CALLER" <13164619284>'
        assert call_args["params"]["timeout"] == 12
        assert call_args["params"]["appArgs"].startswith("predial-transfer,test_call_123,support_agent")
        assert "channelVars" not in call_args["params"]
        assert call_args["data"]["variables"]["AGENT_ACTION"] == "predial_transfer"
        assert call_args["data"]["variables"]["AGENT_CALL_ID"] == "test_call_123"
        engine.register_predial_transfer_channel.assert_called_once_with("test_call_123", "SIP/6000-00000001")

    @pytest.mark.asyncio
    async def test_duplicate_deferred_transfer_reuses_pending_action(self, tool, tool_context, mock_ari_client):
        existing_action = {
            "id": "existing-action",
            "kind": "transfer",
            "source_tool": "blind_transfer",
            "commit_tool": "blind_transfer",
            "transfer_type": "extension",
            "target": "6000",
            "description": "Support Agent",
            "dialplan_context": "from-internal",
        }
        tool_context.session_store.get_by_call_id.return_value.pending_deferred_transfer = existing_action
        tool_context.config["tools"]["transfer"] = {
            "deferred_strategy": "predial_then_bridge",
            "extension_context": "from-internal",
            "destinations": {
                "support_agent": {
                    "type": "extension",
                    "target": "6000",
                    "description": "Support Agent",
                }
            },
        }

        result = await tool.execute({"destination": "support_agent"}, tool_context)

        assert result["status"] == "success"
        assert result["duplicate_suppressed"] is True
        assert result[DEFERRED_TRANSFER_RESULT_KEY] == existing_action
        mock_ari_client.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_conflicting_deferred_transfer_request_does_not_reuse_pending_action(self, tool, tool_context, mock_ari_client):
        existing_action = {
            "id": "existing-action",
            "kind": "transfer",
            "source_tool": "blind_transfer",
            "commit_tool": "blind_transfer",
            "transfer_type": "extension",
            "target": "6000",
            "description": "Support Agent",
            "dialplan_context": "from-internal",
        }
        tool_context.session_store.get_by_call_id.return_value.pending_deferred_transfer = existing_action
        tool_context.config["tools"]["transfer"] = {
            "extension_context": "from-internal",
            "destinations": {
                "billing_agent": {
                    "type": "extension",
                    "target": "7000",
                    "description": "Billing Agent",
                }
            },
        }

        result = await tool.execute({"destination": "billing_agent"}, tool_context)

        assert result["status"] == "failed"
        assert "already pending" in result["message"]
        assert tool_context.session_store.get_by_call_id.return_value.pending_deferred_transfer == existing_action
        mock_ari_client.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_pending_deferred_transfer_commit_keeps_action_for_retry(self, tool_context, monkeypatch):
        action = {
            "id": "retry-action",
            "kind": "transfer",
            "source_tool": "blind_transfer",
            "commit_tool": "blind_transfer",
            "transfer_type": "extension",
            "target": "6000",
            "description": "Support Agent",
            "dialplan_context": "from-internal",
        }
        tool_context.session_store.get_by_call_id.return_value.pending_deferred_transfer = dict(action)

        async def fake_commit(action_arg, context_arg):
            return {"status": "error", "message": "temporary failure"}

        monkeypatch.setattr(deferred_transfer_mod, "commit_deferred_transfer_action", fake_commit)

        result = await deferred_transfer_mod.commit_pending_deferred_transfer(tool_context)

        assert result == {"status": "error", "message": "temporary failure"}
        assert tool_context.session_store.get_by_call_id.return_value.pending_deferred_transfer == action

    @pytest.mark.asyncio
    async def test_successful_pending_deferred_transfer_commit_clears_action(self, tool_context, monkeypatch):
        action = {
            "id": "success-action",
            "kind": "transfer",
            "source_tool": "blind_transfer",
            "commit_tool": "blind_transfer",
            "transfer_type": "extension",
            "target": "6000",
            "description": "Support Agent",
            "dialplan_context": "from-internal",
        }
        tool_context.session_store.get_by_call_id.return_value.pending_deferred_transfer = dict(action)

        async def fake_commit(action_arg, context_arg):
            return {"status": "success", "message": "ok"}

        monkeypatch.setattr(deferred_transfer_mod, "commit_deferred_transfer_action", fake_commit)

        result = await deferred_transfer_mod.commit_pending_deferred_transfer(tool_context)

        assert result == {"status": "success", "message": "ok"}
        assert tool_context.session_store.get_by_call_id.return_value.pending_deferred_transfer is None

    @pytest.mark.asyncio
    async def test_successful_pending_deferred_transfer_commit_does_not_resurrect_removed_session(self, tool_context, monkeypatch):
        action = {
            "id": "cleanup-action",
            "kind": "transfer",
            "source_tool": "blind_transfer",
            "commit_tool": "blind_transfer",
            "transfer_type": "extension",
            "target": "6000",
            "description": "Support Agent",
            "dialplan_context": "from-internal",
        }
        session = tool_context.session_store.get_by_call_id.return_value
        session.pending_deferred_transfer = dict(action)
        tool_context.session_store.get_by_call_id.side_effect = [session, None]
        tool_context.session_store.upsert_call.reset_mock()

        async def fake_commit(action_arg, context_arg):
            return {"status": "success", "message": "ok"}

        monkeypatch.setattr(deferred_transfer_mod, "commit_deferred_transfer_action", fake_commit)

        result = await deferred_transfer_mod.commit_pending_deferred_transfer(tool_context)

        assert result == {"status": "success", "message": "ok"}
        tool_context.session_store.upsert_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_pending_deferred_transfer_commit_does_not_resurrect_removed_session(self, tool_context, monkeypatch):
        action = {
            "id": "cleanup-error-action",
            "kind": "transfer",
            "source_tool": "blind_transfer",
            "commit_tool": "blind_transfer",
            "transfer_type": "extension",
            "target": "6000",
            "description": "Support Agent",
            "dialplan_context": "from-internal",
        }
        session = tool_context.session_store.get_by_call_id.return_value
        session.pending_deferred_transfer = dict(action)
        tool_context.session_store.get_by_call_id.side_effect = [session, None]
        tool_context.session_store.upsert_call.reset_mock()

        async def fake_commit(action_arg, context_arg):
            raise RuntimeError("channel gone")

        monkeypatch.setattr(deferred_transfer_mod, "commit_deferred_transfer_action", fake_commit)

        result = await deferred_transfer_mod.commit_pending_deferred_transfer(tool_context)

        assert result == {"status": "error", "message": "channel gone"}
        tool_context.session_store.upsert_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_commit_predial_uses_engine_finalize(self, tool, tool_context, mock_ari_client):
        engine = Mock()
        engine.finalize_predial_transfer = AsyncMock(
            return_value={"status": "success", "strategy": "predial_then_bridge"}
        )
        mock_ari_client.engine = engine
        action = {
            "transfer_type": "extension",
            "target": "6000",
            "description": "Support Agent",
            "dialplan_context": "from-internal",
            "payload": {
                "predial": {
                    "enabled": True,
                    "endpoint": "Local/6000@from-internal",
                    "channel_id": "SIP/6000-00000001",
                }
            },
        }

        result = await tool.commit_deferred_action(action, tool_context)

        assert result == {"status": "success", "strategy": "predial_then_bridge"}
        engine.finalize_predial_transfer.assert_awaited_once_with(tool_context, action)

    @pytest.mark.asyncio
    async def test_commit_deferred_queue_uses_destination_context(self, tool, tool_context, mock_ari_client):
        action = {
            "transfer_type": "queue",
            "target": "301",
            "description": "Support Queue",
            "dialplan_context": "custom-queues",
        }

        result = await tool.commit_deferred_action(action, tool_context)

        assert result["status"] == "success"
        mock_ari_client.dialplan_target_exists.assert_awaited_once_with(
            tool_context.caller_channel_id,
            context="custom-queues",
            extension="301",
            priority=1,
        )
        mock_ari_client.continue_in_dialplan.assert_awaited_once_with(
            tool_context.caller_channel_id,
            context="custom-queues",
            extension="301",
            priority=1,
        )

    @pytest.mark.asyncio
    async def test_direct_queue_transfer_without_context_uses_default_context(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["transfer"] = {
            "queue_context": "custom-queues",
        }

        result = await tool._transfer_to_queue(tool_context, "301", "Support Queue")

        assert result["status"] == "success"
        mock_ari_client.continue_in_dialplan.assert_awaited_once_with(
            tool_context.caller_channel_id,
            context="custom-queues",
            extension="301",
            priority=1,
        )

    @pytest.mark.asyncio
    async def test_direct_ringgroup_transfer_without_context_uses_default_context(self, tool, tool_context, mock_ari_client):
        tool_context.config["tools"]["transfer"] = {
            "ringgroup_context": "custom-groups",
        }

        result = await tool._transfer_to_ringgroup(tool_context, "600", "Support Ring Group")

        assert result["status"] == "success"
        mock_ari_client.continue_in_dialplan.assert_awaited_once_with(
            tool_context.caller_channel_id,
            context="custom-groups",
            extension="600",
            priority=1,
        )

    @pytest.mark.asyncio
    async def test_default_queue_context_is_validated_and_handed_to_ext_queues(
        self, tool, tool_context, mock_ari_client
    ):
        result = await tool._transfer_to_queue(tool_context, "1000", "Technical Support")

        assert result["status"] == "success"
        mock_ari_client.dialplan_target_exists.assert_awaited_once_with(
            tool_context.caller_channel_id,
            context="ext-queues",
            extension="1000",
            priority=1,
        )
        mock_ari_client.continue_in_dialplan.assert_awaited_once_with(
            tool_context.caller_channel_id,
            context="ext-queues",
            extension="1000",
            priority=1,
        )
        assert tool_context.session_store.get_by_call_id.return_value.transfer_active is True

    @pytest.mark.asyncio
    async def test_missing_dialplan_target_fails_before_transfer_ownership_changes(
        self, tool, tool_context, mock_ari_client
    ):
        session = tool_context.session_store.get_by_call_id.return_value
        mock_ari_client.dialplan_target_exists.return_value = False

        result = await tool._transfer_to_queue(tool_context, "1000", "Technical Support")

        assert result["status"] == "failed"
        assert getattr(session, "transfer_active", False) is False
        assert getattr(session, "transfer_state", None) is None
        assert getattr(session, "transfer_target", None) is None
        mock_ari_client.continue_in_dialplan.assert_not_awaited()
        tool_context.session_store.upsert_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unavailable_target_discovery_still_attempts_guarded_handoff(
        self, tool, tool_context, mock_ari_client
    ):
        mock_ari_client.dialplan_target_exists.return_value = None

        result = await tool._transfer_to_queue(tool_context, "1000", "Technical Support")

        assert result["status"] == "success"
        mock_ari_client.continue_in_dialplan.assert_awaited_once_with(
            tool_context.caller_channel_id,
            context="ext-queues",
            extension="1000",
            priority=1,
        )

    @pytest.mark.asyncio
    async def test_rejected_handoff_fails_and_restores_prior_transfer_ownership(
        self, tool, tool_context, mock_ari_client
    ):
        session = tool_context.session_store.get_by_call_id.return_value
        session.transfer_active = False
        session.transfer_state = "ready"
        session.transfer_target = "previous"
        mock_ari_client.continue_in_dialplan.return_value = False

        result = await tool._transfer_to_queue(tool_context, "1000", "Technical Support")

        assert result["status"] == "failed"
        assert session.transfer_active is False
        assert session.transfer_state == "ready"
        assert session.transfer_target == "previous"
        assert tool_context.session_store.upsert_call.await_count == 2
        mock_ari_client.send_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_indeterminate_handoff_result_retains_ownership_without_probe(
        self, tool, tool_context, mock_ari_client
    ):
        session = tool_context.session_store.get_by_call_id.return_value
        session.transfer_state = "ready"
        session.transfer_target = "previous"
        mock_ari_client.continue_in_dialplan.return_value = None
        mock_ari_client.send_command.side_effect = None
        mock_ari_client.send_command.return_value = {
            "id": tool_context.caller_channel_id,
            "state": "Up",
        }

        result = await tool._transfer_to_queue(tool_context, "1000", "Technical Support")

        assert result["status"] == "failed"
        assert result["handoff_indeterminate"] is True
        assert session.transfer_active is True
        assert session.transfer_state == "in_queue"
        assert session.transfer_target == "Technical Support"
        assert tool_context.session_store.upsert_call.await_count == 1
        mock_ari_client.send_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_indeterminate_handoff_result_retains_ownership_when_caller_left_ari(
        self, tool, tool_context, mock_ari_client
    ):
        session = tool_context.session_store.get_by_call_id.return_value
        mock_ari_client.continue_in_dialplan.return_value = None
        mock_ari_client.send_command.side_effect = None
        mock_ari_client.send_command.return_value = {"status": 404}

        result = await tool._transfer_to_queue(tool_context, "1000", "Technical Support")

        assert result["status"] == "failed"
        assert result["handoff_indeterminate"] is True
        assert session.transfer_active is True
        assert session.transfer_state == "in_queue"
        assert session.transfer_target == "Technical Support"
        assert tool_context.session_store.upsert_call.await_count == 1
        mock_ari_client.send_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handoff_exception_retains_ownership_without_presence_probe(
        self, tool, tool_context, mock_ari_client
    ):
        session = tool_context.session_store.get_by_call_id.return_value
        mock_ari_client.continue_in_dialplan.side_effect = RuntimeError("ARI unavailable")
        mock_ari_client.send_command.side_effect = None
        mock_ari_client.send_command.return_value = {
            "id": tool_context.caller_channel_id,
            "state": "Up",
        }

        result = await tool._transfer_to_ringgroup(tool_context, "600", "Support Ring Group")

        assert result["status"] == "failed"
        assert result["handoff_indeterminate"] is True
        assert session.transfer_active is True
        assert session.transfer_state == "in_ringgroup"
        assert session.transfer_target == "Support Ring Group"
        assert tool_context.session_store.upsert_call.await_count == 1
        mock_ari_client.send_command.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("channel_response", [{"status": 404}, {"status": 503}, None])
    async def test_handoff_exception_retains_ownership_when_channel_not_confirmed_present(
        self, tool, tool_context, mock_ari_client, channel_response
    ):
        session = tool_context.session_store.get_by_call_id.return_value
        mock_ari_client.continue_in_dialplan.side_effect = RuntimeError("response lost")
        mock_ari_client.send_command.side_effect = None
        mock_ari_client.send_command.return_value = channel_response

        result = await tool._transfer_to_ringgroup(tool_context, "600", "Support Ring Group")

        assert result["status"] == "failed"
        assert result["handoff_indeterminate"] is True
        assert session.transfer_active is True
        assert session.transfer_state == "in_ringgroup"
        assert session.transfer_target == "Support Ring Group"
        assert tool_context.session_store.upsert_call.await_count == 1
        mock_ari_client.send_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handoff_exception_does_not_run_channel_presence_probe(
        self, tool, tool_context, mock_ari_client
    ):
        session = tool_context.session_store.get_by_call_id.return_value
        mock_ari_client.continue_in_dialplan.side_effect = RuntimeError("response lost")
        mock_ari_client.send_command.side_effect = RuntimeError("ARI still unavailable")

        result = await tool._transfer_to_queue(tool_context, "1000", "Technical Support")

        assert result["status"] == "failed"
        assert result["handoff_indeterminate"] is True
        assert session.transfer_active is True
        assert session.transfer_state == "in_queue"
        assert session.transfer_target == "Technical Support"
        assert tool_context.session_store.upsert_call.await_count == 1
        mock_ari_client.send_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejected_deferred_handoff_keeps_action_for_retry_and_restores_ownership(
        self, tool, tool_context, mock_ari_client
    ):
        action = {
            "id": "queue-retry",
            "kind": "transfer",
            "source_tool": "blind_transfer",
            "commit_tool": "blind_transfer",
            "transfer_type": "queue",
            "target": "1000",
            "description": "Technical Support",
            "dialplan_context": "ext-queues",
        }
        session = tool_context.session_store.get_by_call_id.return_value
        session.pending_deferred_transfer = dict(action)
        registry = Mock()
        registry.get.return_value = tool
        tool_context.tool_registry = registry
        mock_ari_client.continue_in_dialplan.return_value = False

        result = await deferred_transfer_mod.commit_pending_deferred_transfer(tool_context)

        assert result["status"] == "failed"
        assert session.pending_deferred_transfer == action
        assert session.transfer_active is False
        assert session.transfer_state is None
        assert session.transfer_target is None

    @pytest.mark.asyncio
    async def test_human_intent_without_extension_destination_fails(self, tool, tool_context):
        tool_context.config["tools"]["transfer"] = {
            "destinations": {
                "ops_queue": {
                    "type": "queue",
                    "target": "700",
                    "description": "Operations Queue",
                }
            }
        }

        result = await tool.execute({"destination": "live agent"}, tool_context)

        assert result["status"] == "failed"
        assert "Unknown destination" in result["message"]
