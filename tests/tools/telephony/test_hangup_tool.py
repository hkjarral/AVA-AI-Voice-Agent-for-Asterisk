"""
Unit tests for HangupCallTool.

Tests hangup functionality including:
- Farewell message handling
- will_hangup flag
- Session state updates
- Error handling
"""

import pytest
from unittest.mock import AsyncMock
from src.tools.telephony.hangup import HangupCallTool


class TestHangupCallTool:
    """Test suite for hangup call tool."""
    
    @pytest.fixture
    def hangup_tool(self):
        """Create HangupCallTool instance."""
        return HangupCallTool()

    @pytest.fixture
    def tool_context_no_transcript(self, tool_context):
        """
        Hangup tool unit tests historically assumed hangup_call always succeeds.
        In v5.1.7, hangup_call can be blocked when request_transcript is enabled
        but the transcript offer/decision flow hasn't been completed.

        For the baseline hangup behavior tests, disable request_transcript.
        """
        try:
            tool_context.config.setdefault("tools", {}).setdefault("request_transcript", {})["enabled"] = False
        except Exception:
            pass
        return tool_context
    
    # ==================== Definition Tests ====================
    
    def test_definition(self, hangup_tool):
        """Test tool definition is valid."""
        definition = hangup_tool.definition
        
        assert definition.name == "hangup_call"
        assert definition.category.value == "telephony"
        assert definition.requires_channel is True
        assert definition.max_execution_time == 5
        
        # Check parameters
        assert len(definition.parameters) == 1
        
        farewell_param = definition.parameters[0]
        assert farewell_param.name == "farewell_message"
        assert farewell_param.required is False
        assert farewell_param.type == "string"
    
    def test_definition_description_includes_use_cases(self, hangup_tool):
        """Test that definition describes when to use hangup."""
        description = hangup_tool.definition.description
        
        # Should mention common goodbye scenarios
        assert "goodbye" in description.lower() or "bye" in description.lower()
        assert "thank" in description.lower()
    
    # ==================== Execution Tests ====================
    
    @pytest.mark.asyncio
    async def test_hangup_with_custom_farewell(
        self, hangup_tool, tool_context_no_transcript
    ):
        """Test hangup with custom farewell message."""
        result = await hangup_tool.execute(
            parameters={"farewell_message": "Thank you for calling!"},
            context=tool_context_no_transcript
        )
        
        assert result["status"] == "success"
        assert result["will_hangup"] is True
        assert result["message"] == "Thank you for calling!"
    
    @pytest.mark.asyncio
    async def test_hangup_with_default_farewell(
        self, hangup_tool, tool_context_no_transcript
    ):
        """Test hangup with default farewell when none provided."""
        result = await hangup_tool.execute(
            parameters={},
            context=tool_context_no_transcript
        )
        
        assert result["status"] == "success"
        assert result["will_hangup"] is True
        assert "message" in result
        assert len(result["message"]) > 0
        
        # Default should be polite
        farewell = result["message"].lower()
        assert any(word in farewell for word in ["goodbye", "bye", "thank"])
    
    @pytest.mark.asyncio
    async def test_hangup_with_long_farewell(
        self, hangup_tool, tool_context_no_transcript
    ):
        """Test hangup with longer farewell message."""
        long_farewell = "Thank you so much for calling today. We appreciate your business and hope to serve you again soon. Have a wonderful day!"
        
        result = await hangup_tool.execute(
            parameters={"farewell_message": long_farewell},
            context=tool_context_no_transcript
        )
        
        assert result["status"] == "success"
        assert result["message"] == long_farewell
    
    @pytest.mark.asyncio
    async def test_hangup_with_empty_farewell(
        self, hangup_tool, tool_context_no_transcript
    ):
        """Test hangup with empty farewell string."""
        result = await hangup_tool.execute(
            parameters={"farewell_message": ""},
            context=tool_context_no_transcript
        )
        
        assert result["status"] == "success"
        # Should either use default or accept empty
        assert "message" in result
    
    # ==================== will_hangup Flag Tests ====================
    
    @pytest.mark.asyncio
    async def test_will_hangup_flag_is_true(
        self, hangup_tool, tool_context_no_transcript
    ):
        """Test that will_hangup flag is always True."""
        result = await hangup_tool.execute(
            parameters={"farewell_message": "Goodbye!"},
            context=tool_context_no_transcript
        )
        
        # Critical: This flag tells provider to emit HangupReady event
        assert result["will_hangup"] is True
    
    @pytest.mark.asyncio
    async def test_provider_receives_hangup_signal(
        self, hangup_tool, tool_context_no_transcript
    ):
        """Test that hangup result includes provider signal."""
        result = await hangup_tool.execute(
            parameters={},
            context=tool_context_no_transcript
        )
        
        # Result should contain info for provider to handle hangup
        assert "will_hangup" in result
        assert "message" in result
        assert result["farewell_message"] == result["message"]
        assert result["will_hangup"] is True
    
    # ==================== Session State Tests ====================
    
    @pytest.mark.asyncio
    async def test_hangup_does_not_immediately_terminate(
        self, hangup_tool, tool_context_no_transcript, mock_ari_client
    ):
        """Test that hangup tool doesn't immediately hang up channel."""
        await hangup_tool.execute(
            parameters={"farewell_message": "Goodbye!"},
            context=tool_context_no_transcript
        )
        
        # ARI hangup should NOT be called (provider handles after farewell)
        mock_ari_client.hangup_channel.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_hangup_with_active_session(
        self, hangup_tool, tool_context_no_transcript, sample_call_session
    ):
        """Test hangup during active conversation."""
        sample_call_session.conversation_state = "active"
        sample_call_session.audio_capture_enabled = True
        
        result = await hangup_tool.execute(
            parameters={},
            context=tool_context_no_transcript
        )
        
        assert result["status"] == "success"
        assert result["will_hangup"] is True
    
    # ==================== Error Handling Tests ====================
    
    @pytest.mark.asyncio
    async def test_hangup_with_no_session(
        self, hangup_tool, tool_context, mock_session_store
    ):
        """Test hangup when session doesn't exist."""
        mock_session_store.get_by_call_id.return_value = None
        
        result = await hangup_tool.execute(
            parameters={},
            context=tool_context
        )
        
        # Should handle gracefully
        assert result["status"] in ["success", "error"]
    
    @pytest.mark.asyncio
    async def test_hangup_with_invalid_call_id(
        self, hangup_tool, tool_context
    ):
        """Test hangup with invalid call ID."""
        # Modify context to have invalid call_id
        tool_context.call_id = "invalid_call_id"
        
        result = await hangup_tool.execute(
            parameters={},
            context=tool_context
        )
        
        # Should handle gracefully
        assert "status" in result
    
    # ==================== Integration Tests ====================
    
    @pytest.mark.asyncio
    async def test_hangup_after_transfer_failure(
        self, hangup_tool, tool_context_no_transcript, sample_call_session
    ):
        """Test hangup after failed transfer attempt."""
        # Simulate failed transfer
        sample_call_session.current_action = {
            "type": "transfer",
            "status": "failed"
        }
        
        result = await hangup_tool.execute(
            parameters={"farewell_message": "I apologize for the difficulty. Goodbye!"},
            context=tool_context_no_transcript
        )
        
        assert result["status"] == "success"
    
    @pytest.mark.asyncio
    async def test_hangup_multiple_times(
        self, hangup_tool, tool_context_no_transcript
    ):
        """Test calling hangup tool multiple times (should be idempotent)."""
        # First hangup
        result1 = await hangup_tool.execute(
            parameters={},
            context=tool_context_no_transcript
        )
        
        # Second hangup (edge case, shouldn't happen but handle gracefully)
        result2 = await hangup_tool.execute(
            parameters={},
            context=tool_context_no_transcript
        )
        
        assert result1["status"] == "success"
        # Second call should either succeed or fail gracefully
        assert result2["status"] in ["success", "error"]
    
    # ==================== Message Content Tests ====================
    
    @pytest.mark.asyncio
    async def test_farewell_message_variants(
        self, hangup_tool, tool_context_no_transcript
    ):
        """Test different farewell message styles."""
        farewells = [
            "Goodbye!",
            "Thank you for calling. Have a great day!",
            "Take care!",
            "Bye bye!",
            "We appreciate your call. Goodbye!"
        ]
        
        for farewell in farewells:
            result = await hangup_tool.execute(
                parameters={"farewell_message": farewell},
                context=tool_context_no_transcript
            )
            
            assert result["status"] == "success"
            assert result["message"] == farewell
    
    @pytest.mark.asyncio
    async def test_farewell_with_special_characters(
        self, hangup_tool, tool_context_no_transcript
    ):
        """Test farewell with special characters."""
        farewell = "Thank you! It's been great helping you today. Goodbye! 😊"
        
        result = await hangup_tool.execute(
            parameters={"farewell_message": farewell},
            context=tool_context_no_transcript
        )
        
        assert result["status"] == "success"
        assert result["message"] == farewell
    
    # ==================== Parameter Validation Tests ====================
    
    @pytest.mark.asyncio
    async def test_extra_parameters_ignored(
        self, hangup_tool, tool_context_no_transcript
    ):
        """Test that extra parameters are ignored."""
        result = await hangup_tool.execute(
            parameters={
                "farewell_message": "Goodbye!",
                "extra_param": "should be ignored"
            },
            context=tool_context_no_transcript
        )
        
        assert result["status"] == "success"
        assert result["message"] == "Goodbye!"
    
    @pytest.mark.asyncio
    async def test_farewell_message_none_value(
        self, hangup_tool, tool_context_no_transcript
    ):
        """Test hangup when farewell_message is explicitly None."""
        result = await hangup_tool.execute(
            parameters={"farewell_message": None},
            context=tool_context_no_transcript
        )
        
        assert result["status"] == "success"
        # Should handle None gracefully (use default or empty)
        assert "message" in result

    # ==================== Transcript Interaction Tests ====================
    # v5.0 simplified design: hangup always succeeds; AI manages transcript
    # offers via system prompt, not tool-level guardrails.

    @pytest.mark.asyncio
    async def test_hangup_succeeds_even_when_transcript_enabled(
        self, hangup_tool, tool_context
    ):
        """v5.0: hangup_call always succeeds regardless of transcript config."""
        result = await hangup_tool.execute(parameters={}, context=tool_context)
        assert result["status"] == "success"
        assert result["will_hangup"] is True

    @pytest.mark.asyncio
    async def test_hangup_succeeds_with_transcript_in_history(
        self, hangup_tool, tool_context, sample_call_session
    ):
        """
        Hangup proceeds normally even when transcript offer exists in history.
        """
        sample_call_session.conversation_history.append(
            {"role": "assistant", "content": "Before we hang up, would you like me to email you a transcript of our conversation?"}
        )
        sample_call_session.conversation_history.append(
            {"role": "user", "content": "No, I don't need the transcript."}
        )
        result = await hangup_tool.execute(parameters={"farewell_message": "Goodbye!"}, context=tool_context)
        assert result["status"] == "success"
        assert result["will_hangup"] is True
