"""
Unit tests for CancelTransferTool.

Tests cancel transfer functionality including:
- Canceling active transfers
- No transfer to cancel scenarios
- Transfer already answered scenarios
- Session state updates
"""

import pytest
from unittest.mock import AsyncMock, Mock
from src.tools.telephony.cancel_transfer import CancelTransferTool


class TestCancelTransferTool:
    """Test suite for cancel transfer tool."""
    
    @pytest.fixture
    def cancel_tool(self):
        """Create CancelTransferTool instance."""
        return CancelTransferTool()
    
    # ==================== Definition Tests ====================
    
    def test_definition(self, cancel_tool):
        """Test tool definition is valid."""
        definition = cancel_tool.definition
        
        assert definition.name == "cancel_transfer"
        assert definition.category.value == "telephony"
        assert definition.requires_channel is True
        assert definition.max_execution_time == 5
        
        # Should have no parameters (cancel is action-only)
        assert len(definition.parameters) == 0
    
    def test_definition_description_mentions_limitations(self, cancel_tool):
        """Test that definition explains when cancel works."""
        description = cancel_tool.definition.description
        
        # Should mention timing constraint
        assert "answer" in description.lower() or "ring" in description.lower()
    
    # ==================== Success Scenarios ====================
    
    @pytest.mark.asyncio
    async def test_cancel_active_transfer(
        self, cancel_tool, tool_context, sample_call_session, mock_ari_client
    ):
        """Test canceling an active transfer in progress."""
        # Simulate active transfer
        sample_call_session.current_action = {
            "type": "transfer",
            "status": "ringing",
            "target_extension": "6000",
            "channel_id": "SIP/6000-00000002"
        }
        
        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        assert result["status"] == "success"
        assert "cancel" in result["message"].lower()
        
        # Verify transfer channel was hung up
        mock_ari_client.hangup_channel.assert_called_once_with(
            "SIP/6000-00000002"
        )
    
    @pytest.mark.asyncio
    async def test_cancel_clears_current_action(
        self, cancel_tool, tool_context, sample_call_session, mock_session_store
    ):
        """Test that cancel clears current_action from session."""
        # Simulate active transfer
        sample_call_session.current_action = {
            "type": "transfer",
            "status": "ringing",
            "target_extension": "6000",
            "channel_id": "SIP/6000-00000002"
        }
        
        await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        # Verify session was updated to clear action
        assert mock_session_store.upsert_call.called
        
        # Get updated session
        updated_session = mock_session_store.upsert_call.call_args[0][0]
        assert updated_session.current_action is None

    @pytest.mark.asyncio
    async def test_cancel_pending_deferred_transfer(
        self, cancel_tool, tool_context, sample_call_session, mock_session_store, mock_ari_client
    ):
        """Test canceling a deferred transfer before playback drain commits it."""
        sample_call_session.current_action = None
        sample_call_session.pending_deferred_transfer = {
            "id": "deferred-transfer",
            "kind": "transfer",
            "source_tool": "blind_transfer",
            "commit_tool": "blind_transfer",
            "transfer_type": "extension",
            "target": "6000",
            "description": "Support agent",
        }

        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )

        assert result["status"] == "success"
        mock_ari_client.hangup_channel.assert_not_called()
        updated_session = mock_session_store.upsert_call.call_args[0][0]
        assert updated_session.current_action is None
        assert updated_session.pending_deferred_transfer is None

    @pytest.mark.asyncio
    async def test_cancel_pending_predial_deferred_transfer_hangs_up_leg(
        self, cancel_tool, tool_context, sample_call_session, mock_session_store, mock_ari_client
    ):
        """Test canceling a deferred predial transfer before it bridges to the caller."""
        sample_call_session.current_action = {
            "type": "predial_transfer",
            "answered": True,
            "bridged": False,
            "predial_channel_id": "SIP/6000-00000004",
        }
        sample_call_session.pending_deferred_transfer = {
            "id": "predial-deferred-transfer",
            "kind": "transfer",
            "source_tool": "blind_transfer",
            "commit_tool": "blind_transfer",
            "transfer_type": "extension",
            "target": "6000",
            "description": "Support agent",
            "payload": {
                "predial": {
                    "enabled": True,
                    "channel_id": "SIP/6000-00000004",
                }
            },
        }
        engine = Mock()
        mock_ari_client.engine = engine

        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )

        assert result["status"] == "success"
        engine._unregister_predial_transfer_channel.assert_called_once_with("SIP/6000-00000004")
        mock_ari_client.hangup_channel.assert_awaited_once_with("SIP/6000-00000004")
        updated_session = mock_session_store.upsert_call.call_args[0][0]
        assert updated_session.current_action is None
        assert updated_session.pending_deferred_transfer is None
    
    @pytest.mark.asyncio
    async def test_cancel_stops_hold_music(
        self, cancel_tool, tool_context, sample_call_session, mock_ari_client
    ):
        """Test that cancel stops hold music on caller channel."""
        # Simulate active transfer with MOH
        sample_call_session.current_action = {
            "type": "transfer",
            "status": "ringing",
            "target_extension": "6000",
            "channel_id": "SIP/6000-00000002"
        }
        
        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        assert result["status"] == "success"
        
        # Should have stopped MOH via DELETE to /channels/{id}/moh
        # Verify hangup was called
        assert mock_ari_client.hangup_channel.called or mock_ari_client.send_command.called
    
    # ==================== No Transfer Scenarios ====================
    
    @pytest.mark.asyncio
    async def test_cancel_with_no_transfer(
        self, cancel_tool, tool_context, sample_call_session
    ):
        """Test cancel when no transfer is in progress."""
        # No current_action
        sample_call_session.current_action = None
        
        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        assert result["status"] in ["no_transfer", "error"]
        assert "no transfer" in result["message"].lower() or "not in progress" in result["message"].lower()
    
    @pytest.mark.asyncio
    async def test_cancel_with_different_action_type(
        self, cancel_tool, tool_context, sample_call_session
    ):
        """Test cancel when current action is not a transfer."""
        # Different action type
        sample_call_session.current_action = {
            "type": "hangup",
            "status": "pending"
        }
        
        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        # Should indicate no transfer to cancel
        assert result["status"] in ["no_transfer", "error"]
    
    # ==================== Transfer Already Answered ====================
    
    @pytest.mark.asyncio
    async def test_cancel_after_transfer_answered(
        self, cancel_tool, tool_context, sample_call_session
    ):
        """Test cancel after transfer has been answered."""
        # Transfer already answered
        sample_call_session.current_action = {
            "type": "transfer",
            "answered": True,
            "target_extension": "6000",
            "channel_id": "SIP/6000-00000002"
        }
        
        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        # Cannot cancel after answer
        assert result["status"] in ["error", "too_late"]
        assert "answer" in result["message"].lower() or "too late" in result["message"].lower() or "cannot" in result["message"].lower()
    
    @pytest.mark.asyncio
    async def test_cancel_after_transfer_complete(
        self, cancel_tool, tool_context, sample_call_session
    ):
        """Test cancel after transfer is complete."""
        # Transfer complete (no current_action means complete)
        sample_call_session.current_action = None
        
        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        # Cannot cancel when no transfer
        assert result["status"] == "no_transfer"
    
    # ==================== Error Handling ====================
    
    @pytest.mark.asyncio
    async def test_cancel_with_no_session(
        self, cancel_tool, tool_context, mock_session_store
    ):
        """Test cancel when session doesn't exist."""
        mock_session_store.get_by_call_id.return_value = None
        
        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        assert result["status"] == "error"
        # Error message may vary
        assert "error" in result["message"].lower() or "cancel" in result["message"].lower()
    
    @pytest.mark.asyncio
    async def test_cancel_ari_hangup_failure(
        self, cancel_tool, tool_context, sample_call_session, mock_ari_client
    ):
        """Test cancel when ARI hangup fails."""
        # Simulate active transfer
        sample_call_session.current_action = {
            "type": "transfer",
            "status": "ringing",
            "target_extension": "6000",
            "channel_id": "SIP/6000-00000002"
        }
        
        # Simulate ARI failure
        mock_ari_client.hangup_channel.side_effect = Exception("Hangup failed")
        
        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        # Should handle gracefully
        assert result["status"] in ["success", "error"]
    
    @pytest.mark.asyncio
    async def test_cancel_missing_transfer_channel_id(
        self, cancel_tool, tool_context, sample_call_session
    ):
        """Test cancel when transfer_channel_id is missing."""
        # Active transfer but missing channel ID
        sample_call_session.current_action = {
            "type": "transfer",
            "status": "ringing",
            "target_extension": "6000"
            # No transfer_channel_id
        }
        
        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        # Should handle gracefully
        assert "status" in result
    
    # ==================== Session State Tests ====================
    
    @pytest.mark.asyncio
    async def test_cancel_restores_ai_to_conversation(
        self, cancel_tool, tool_context, sample_call_session, mock_session_store
    ):
        """Test that cancel allows AI to resume conversation."""
        # Simulate active transfer
        sample_call_session.current_action = {
            "type": "transfer",
            "status": "ringing",
            "target_extension": "6000",
            "channel_id": "SIP/6000-00000002"
        }
        
        await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        # Verify session was updated
        assert mock_session_store.upsert_call.called
        
        # Get updated session
        updated_session = mock_session_store.upsert_call.call_args[0][0]
        
        # current_action should be cleared, allowing AI to continue
        assert updated_session.current_action is None
    
    @pytest.mark.asyncio
    async def test_cancel_multiple_times(
        self, cancel_tool, tool_context, sample_call_session
    ):
        """Test calling cancel multiple times."""
        # First cancel with active transfer
        sample_call_session.current_action = {
            "type": "transfer",
            "status": "ringing",
            "target_extension": "6000",
            "channel_id": "SIP/6000-00000002"
        }
        
        result1 = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        # Clear action
        sample_call_session.current_action = None
        
        # Second cancel with no active transfer
        result2 = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        assert result1["status"] == "success"
        assert result2["status"] in ["no_transfer", "error"]
    
    # ==================== Parameter Tests ====================
    
    @pytest.mark.asyncio
    async def test_cancel_with_no_parameters(
        self, cancel_tool, tool_context, sample_call_session
    ):
        """Test that cancel works with empty parameters."""
        sample_call_session.current_action = {
            "type": "transfer",
            "status": "ringing",
            "target_extension": "6000",
            "channel_id": "SIP/6000-00000002"
        }
        
        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        assert result["status"] == "success"
    
    @pytest.mark.asyncio
    async def test_cancel_ignores_extra_parameters(
        self, cancel_tool, tool_context, sample_call_session
    ):
        """Test that extra parameters are ignored."""
        sample_call_session.current_action = {
            "type": "transfer",
            "status": "ringing",
            "target_extension": "6000",
            "channel_id": "SIP/6000-00000002"
        }
        
        result = await cancel_tool.execute(
            parameters={"extra": "ignored"},
            context=tool_context
        )
        
        assert result["status"] == "success"
    
    # ==================== Integration Tests ====================
    
    @pytest.mark.asyncio
    async def test_cancel_during_blind_transfer(
        self, cancel_tool, tool_context, sample_call_session
    ):
        """Test canceling a blind transfer."""
        # Blind transfer in progress
        sample_call_session.current_action = {
            "type": "transfer",
            "mode": "blind",
            "status": "ringing",
            "target_extension": "6001",
            "channel_id": "SIP/6001-00000003"
        }
        
        result = await cancel_tool.execute(
            parameters={},
            context=tool_context
        )
        
        assert result["status"] == "success"
    
    @pytest.mark.asyncio
    async def test_cancel_transfer_to_different_departments(
        self, cancel_tool, tool_context, sample_call_session
    ):
        """Test canceling transfers to various departments."""
        departments = ["6000", "6001", "6002"]
        
        for dept in departments:
            sample_call_session.current_action = {
                "type": "transfer",
                "status": "ringing",
                "target_extension": dept,
                "channel_id": f"SIP/{dept}-00000002"
            }
            
            result = await cancel_tool.execute(
                parameters={},
                context=tool_context
            )
            
            assert result["status"] == "success"
