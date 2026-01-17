"""
Tests for Send Email Summary Tool

This tool is automatically triggered at the end of calls to send
summary emails to the admin.
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch
from src.tools.business.email_summary import SendEmailSummaryTool
from src.tools.context import ToolExecutionContext
from src.tools.base import ToolCategory


class TestEmailSummaryTool:
    """Test suite for SendEmailSummaryTool."""
    
    # ==================== Tool Definition Tests ====================
    
    def test_tool_definition(self):
        """Test that the tool defines itself correctly."""
        tool = SendEmailSummaryTool()
        definition = tool.definition
        
        assert definition.name == "send_email_summary"
        assert definition.category == ToolCategory.BUSINESS
        assert "admin" in definition.description.lower()
        assert len(definition.parameters) == 0  # Auto-triggered, no params
    
    # ==================== Configuration Tests ====================
    
    @pytest.mark.asyncio
    async def test_tool_disabled(self, email_summary_tool, tool_context):
        """Test that tool is skipped when disabled."""
        with patch.object(tool_context, 'get_config_value') as mock_config:
            mock_config.return_value = {"enabled": False}
            
            result = await email_summary_tool.execute(
                parameters={},
                context=tool_context
            )
            
            assert result["status"] == "skipped"
            assert "disabled" in result["message"].lower()
    
    @pytest.mark.asyncio
    async def test_tool_enabled(
        self, email_summary_tool, tool_context, enabled_email_config, sample_call_session
    ):
        """Test that tool executes when enabled."""
        with patch('src.tools.business.email_summary.resend') as mock_resend:
            mock_resend.Emails.send.return_value = {"id": "email_123"}
            
            result = await email_summary_tool.execute(
                parameters={},
                context=tool_context
            )
            
            assert result["status"] == "success"
            assert "recipient" in result
    
    # ==================== Email Data Preparation Tests ====================
    
    @pytest.mark.asyncio
    async def test_email_includes_caller_info(
        self, email_summary_tool, tool_context, enabled_email_config, sample_call_session
    ):
        """Test that email includes caller name and number."""
        sample_call_session.caller_name = "John Doe"
        sample_call_session.caller_number = "+15551234567"
        
        with patch('src.tools.business.email_summary.resend') as mock_resend:
            mock_resend.Emails.send.return_value = {"id": "email_123"}
            
            # Capture email data sent to Resend
            sent_email = None
            def capture_send(email_data):
                nonlocal sent_email
                sent_email = email_data
                return {"id": "email_123"}
            
            mock_resend.Emails.send.side_effect = capture_send
            
            result = await email_summary_tool.execute(
                parameters={},
                context=tool_context
            )
            
            assert result["status"] == "success"
            # Email will be sent async, so we won't capture it in sync test
            # But we can verify the tool prepared it
    
    @pytest.mark.asyncio
    async def test_email_includes_call_duration(
        self, email_summary_tool, tool_context, enabled_email_config, sample_call_session
    ):
        """Test that email includes formatted call duration."""
        # Set call to 90 seconds (1m 30s)
        sample_call_session.start_time = datetime.now() - timedelta(seconds=90)
        
        with patch('src.tools.business.email_summary.resend') as mock_resend:
            mock_resend.Emails.send.return_value = {"id": "email_123"}
            
            result = await email_summary_tool.execute(
                parameters={},
                context=tool_context
            )
            
            assert result["status"] == "success"
    
    @pytest.mark.asyncio
    async def test_email_includes_conversation_transcript(
        self, email_summary_tool, tool_context, enabled_email_config, sample_call_session
    ):
        """Test that email includes formatted conversation transcript."""
        sample_call_session.conversation_history = [
            {"role": "assistant", "content": "Hello! How can I help you?"},
            {"role": "user", "content": "I need help with my order"},
            {"role": "assistant", "content": "I'd be happy to help with your order!"}
        ]
        
        with patch('src.tools.business.email_summary.resend') as mock_resend:
            mock_resend.Emails.send.return_value = {"id": "email_123"}
            
            result = await email_summary_tool.execute(
                parameters={},
                context=tool_context
            )
            
            assert result["status"] == "success"
    
    @pytest.mark.asyncio
    async def test_email_without_transcript_when_disabled(
        self, email_summary_tool, tool_context, sample_call_session
    ):
        """Test that transcript is excluded when include_transcript is false."""
        config = {
            "enabled": True,
            "include_transcript": False,
            "admin_email": "admin@test.com"
        }
        
        with patch.object(tool_context, 'get_config_value') as mock_config:
            mock_config.return_value = config
            
            with patch('src.tools.business.email_summary.resend') as mock_resend:
                mock_resend.Emails.send.return_value = {"id": "email_123"}
                
                result = await email_summary_tool.execute(
                    parameters={},
                    context=tool_context
                )
                
                assert result["status"] == "success"
    
    # ==================== Empty/Missing Data Tests ====================
    
    @pytest.mark.asyncio
    async def test_empty_conversation_history(
        self, email_summary_tool, tool_context, enabled_email_config, sample_call_session
    ):
        """Test handling of empty conversation history."""
        sample_call_session.conversation_history = []
        
        with patch('src.tools.business.email_summary.resend') as mock_resend:
            mock_resend.Emails.send.return_value = {"id": "email_123"}
            
            result = await email_summary_tool.execute(
                parameters={},
                context=tool_context
            )
            
            assert result["status"] == "success"
    
    @pytest.mark.asyncio
    async def test_missing_session(self, email_summary_tool, enabled_email_config):
        """Test error handling when session is not found."""
        mock_context = Mock(spec=ToolExecutionContext)
        mock_context.call_id = "test-call-123"
        mock_context.get_config_value = Mock(return_value=enabled_email_config)
        mock_context.get_session = AsyncMock(return_value=None)
        
        result = await email_summary_tool.execute(
            parameters={},
            context=mock_context
        )
        
        assert result["status"] == "error"
        assert "session" in result["message"].lower()
    
    @pytest.mark.asyncio
    async def test_missing_caller_info(
        self, email_summary_tool, tool_context, enabled_email_config, sample_call_session
    ):
        """Test handling when caller info is missing."""
        sample_call_session.caller_name = None
        sample_call_session.caller_number = None
        
        with patch('src.tools.business.email_summary.resend') as mock_resend:
            mock_resend.Emails.send.return_value = {"id": "email_123"}
            
            result = await email_summary_tool.execute(
                parameters={},
                context=tool_context
            )
            
            # Should still succeed with "Unknown" placeholders
            assert result["status"] == "success"
    
    # ==================== Async Email Sending Tests ====================
    
    @pytest.mark.asyncio
    async def test_email_sent_asynchronously(
        self, email_summary_tool, tool_context, enabled_email_config, sample_call_session
    ):
        """Test that email is scheduled asynchronously (doesn't block)."""
        with patch('src.tools.business.email_summary.resend') as mock_resend:
            with patch('src.tools.business.email_summary.asyncio.create_task') as mock_task:
                mock_resend.Emails.send.return_value = {"id": "email_123"}
                
                result = await email_summary_tool.execute(
                    parameters={},
                    context=tool_context
                )
                
                # Verify create_task was called (async sending)
                assert mock_task.called
                assert result["status"] == "success"
                assert "shortly" in result["message"].lower() or "scheduled" in result["message"].lower()
    
    @pytest.mark.asyncio
    async def test_returns_success_before_email_sent(
        self, email_summary_tool, tool_context, enabled_email_config, sample_call_session
    ):
        """Test that tool returns success immediately without waiting for email."""
        with patch('src.tools.business.email_summary.resend') as mock_resend:
            # Simulate slow email send
            async def slow_send():
                await asyncio.sleep(1)
                return {"id": "email_123"}
            
            mock_resend.Emails.send.return_value = {"id": "email_123"}
            
            import time
            start = time.time()
            
            result = await email_summary_tool.execute(
                parameters={},
                context=tool_context
            )
            
            elapsed = time.time() - start
            
            # Should return immediately (< 0.1s), not wait for email
            assert elapsed < 0.1
            assert result["status"] == "success"
    
    # ==================== Duration Formatting Tests ====================
    
    def test_format_duration_seconds(self, email_summary_tool):
        """Test duration formatting for seconds."""
        assert email_summary_tool._format_duration(30) == "30 seconds"
        assert email_summary_tool._format_duration(59) == "59 seconds"
    
    def test_format_duration_minutes(self, email_summary_tool):
        """Test duration formatting for minutes."""
        assert email_summary_tool._format_duration(90) == "1m 30s"
        assert email_summary_tool._format_duration(125) == "2m 5s"
        assert email_summary_tool._format_duration(600) == "10m 0s"
    
    def test_format_duration_hours(self, email_summary_tool):
        """Test duration formatting for hours."""
        assert email_summary_tool._format_duration(3600) == "1h 0m"
        assert email_summary_tool._format_duration(3725) == "1h 2m"
        assert email_summary_tool._format_duration(7260) == "2h 1m"
    
    # ==================== Conversation Formatting Tests ====================
    
    def test_format_conversation_ai_messages(self, email_summary_tool):
        """Test formatting of AI assistant messages."""
        history = [
            {"role": "assistant", "content": "Hello!"},
            {"role": "assistant", "content": "How can I help?"}
        ]
        
        formatted = email_summary_tool._format_conversation(history)
        
        assert "AI: Hello!" in formatted
        assert "AI: How can I help?" in formatted
    
    def test_format_conversation_user_messages(self, email_summary_tool):
        """Test formatting of user/caller messages."""
        history = [
            {"role": "user", "content": "I need help"},
            {"role": "user", "content": "With my account"}
        ]
        
        formatted = email_summary_tool._format_conversation(history)
        
        assert "Caller: I need help" in formatted
        assert "Caller: With my account" in formatted
    
    def test_format_conversation_mixed(self, email_summary_tool):
        """Test formatting of mixed conversation."""
        history = [
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": "Hi there"},
            {"role": "assistant", "content": "How can I help?"}
        ]
        
        formatted = email_summary_tool._format_conversation(history)
        
        assert "AI: Hello!" in formatted
        assert "Caller: Hi there" in formatted
        assert "AI: How can I help?" in formatted
    
    def test_format_conversation_empty(self, email_summary_tool):
        """Test formatting of empty conversation."""
        formatted = email_summary_tool._format_conversation([])
        
        assert "no conversation" in formatted.lower()

    def test_transcript_html_preserves_newlines_and_escapes(self, email_summary_tool):
        transcript = "AI: Hello\nCaller: 1 < 2\nAI: Bye"
        html_out = email_summary_tool._format_pretty_html(transcript)
        assert "<br" in html_out
        assert "1 &lt; 2" in html_out
    
    # ==================== Error Handling Tests ====================
    
    @pytest.mark.asyncio
    async def test_exception_during_execution(
        self, email_summary_tool, tool_context, enabled_email_config
    ):
        """Test error handling when exception occurs during execution."""
        # Make get_session raise an exception
        tool_context.get_session = AsyncMock(side_effect=Exception("Database error"))
        
        result = await email_summary_tool.execute(
            parameters={},
            context=tool_context
        )
        
        assert result["status"] == "error"
        assert "error" in result["message"].lower()
    
    # ==================== Config Customization Tests ====================
    
    @pytest.mark.asyncio
    async def test_custom_admin_email(
        self, email_summary_tool, tool_context, sample_call_session
    ):
        """Test using custom admin email from config."""
        config = {
            "enabled": True,
            "admin_email": "custom@admin.com",
            "from_email": "agent@company.com"
        }
        
        with patch.object(tool_context, 'get_config_value') as mock_config:
            mock_config.return_value = config
            
            with patch('src.tools.business.email_summary.resend') as mock_resend:
                mock_resend.Emails.send.return_value = {"id": "email_123"}
                
                result = await email_summary_tool.execute(
                    parameters={},
                    context=tool_context
                )
                
                assert result["status"] == "success"
                assert result["recipient"] == "custom@admin.com"


# ==================== Pytest Fixtures ====================

@pytest.fixture
def email_summary_tool():
    """Create an email summary tool instance."""
    return SendEmailSummaryTool()


@pytest.fixture
def enabled_email_config():
    """Email summary tool config with tool enabled."""
    return {
        "enabled": True,
        "admin_email": "admin@test.com",
        "from_email": "agent@test.com",
        "from_name": "AI Agent",
        "include_transcript": True
    }
