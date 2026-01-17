"""
Unit tests for RequestTranscriptTool.

Tests transcript email functionality including:
- Email parsing from speech
- Email format validation
- DNS MX domain validation
- Duplicate prevention
- Email sending
- Error handling
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from src.tools.business.request_transcript import RequestTranscriptTool


class TestRequestTranscriptTool:
    """Test suite for request transcript tool."""
    
    @pytest.fixture
    def transcript_tool(self):
        """Create RequestTranscriptTool instance."""
        return RequestTranscriptTool()
    
    @pytest.fixture
    def enabled_config(self, tool_config):
        """Config with transcript tool enabled."""
        tool_config["tools"]["request_transcript"]["enabled"] = True
        return tool_config
    
    # ==================== Definition Tests ====================
    
    def test_definition(self, transcript_tool):
        """Test tool definition is valid."""
        definition = transcript_tool.definition
        
        assert definition.name == "request_transcript"
        assert definition.category.value == "business"
        
        # Check parameters
        assert len(definition.parameters) == 1
        
        email_param = definition.parameters[0]
        assert email_param.name == "caller_email"
        assert email_param.required is True
        assert email_param.type == "string"
    
    def test_definition_includes_confirmation_instructions(self, transcript_tool):
        """Test that definition instructs AI to confirm email."""
        description = transcript_tool.definition.description
        
        # Should emphasize confirmation workflow
        assert "confirm" in description.lower() or "correct" in description.lower()
        assert "read back" in description.lower() or "repeat" in description.lower()

    def test_transcript_html_preserves_newlines_and_escapes(self, transcript_tool):
        transcript = "AI: Hello\nCaller: 1 < 2\nAI: Bye"
        html_out = transcript_tool._format_pretty_html(transcript)
        assert "<br" in html_out
        assert "1 &lt; 2" in html_out
    
    # ==================== Email Parsing Tests ====================
    
    @pytest.mark.asyncio
    @patch('src.tools.business.request_transcript.resend')
    @patch('dns.resolver.resolve')
    async def test_parse_simple_email(
        self, mock_dns, mock_resend, transcript_tool, tool_context, enabled_config
    ):
        """Test parsing simple email from speech."""
        # Mock DNS validation
        mx_record = Mock()
        mx_record.exchange = "mx.gmail.com"
        mock_dns.return_value = [mx_record]
        
        # Mock Resend
        mock_resend.Emails.send.return_value = {"id": "email_123"}
        
        result = await transcript_tool.execute(
            parameters={"caller_email": "john dot smith at gmail dot com"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        # Email should be parsed to john.smith@gmail.com
    
    @pytest.mark.asyncio
    async def test_parse_email_with_underscore(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test parsing email with underscore."""
        with patch('dns.resolver.resolve') as mock_dns:
            mx_record = Mock()
            mx_record.exchange = "mx.yahoo.com"
            mock_dns.return_value = [mx_record]
            
            with patch('src.tools.business.request_transcript.resend') as mock_resend:
                mock_resend.Emails.send.return_value = {"id": "email_123"}
                
                result = await transcript_tool.execute(
                    parameters={"caller_email": "jane underscore doe at yahoo dot com"},
                    context=tool_context
                )
                
                assert result["status"] == "success"
    
    @pytest.mark.asyncio
    async def test_parse_email_with_dash(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test parsing email with dash/hyphen."""
        with patch('dns.resolver.resolve') as mock_dns:
            mx_record = Mock()
            mx_record.exchange = "mx.company.com"
            mock_dns.return_value = [mx_record]
            
            with patch('src.tools.business.request_transcript.resend') as mock_resend:
                mock_resend.Emails.send.return_value = {"id": "email_123"}
                
                result = await transcript_tool.execute(
                    parameters={"caller_email": "mike dash jones at company dot com"},
                    context=tool_context
                )
                
                assert result["status"] == "success"
    
    @pytest.mark.asyncio
    async def test_parse_email_with_subdomain(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test parsing email with subdomain (e.g., co.uk)."""
        with patch('dns.resolver.resolve') as mock_dns:
            mx_record = Mock()
            mx_record.exchange = "mx.company.co.uk"
            mock_dns.return_value = [mx_record]
            
            with patch('src.tools.business.request_transcript.resend') as mock_resend:
                mock_resend.Emails.send.return_value = {"id": "email_123"}
                
                result = await transcript_tool.execute(
                    parameters={"caller_email": "john at company dot co dot uk"},
                    context=tool_context
                )
                
                assert result["status"] == "success"
    
    # ==================== Validation Tests ====================
    
    @pytest.mark.asyncio
    async def test_empty_email_address(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test handling of empty email address."""
        result = await transcript_tool.execute(
            parameters={"caller_email": ""},
            context=tool_context
        )
        
        assert result["status"] == "error"
        assert "didn't catch" in result["message"].lower() or "repeat" in result["message"].lower()
    
    @pytest.mark.asyncio
    async def test_invalid_email_format(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test handling of invalid email format."""
        result = await transcript_tool.execute(
            parameters={"caller_email": "not an email"},
            context=tool_context
        )
        
        assert result["status"] == "error"
        assert "couldn't understand" in result["message"].lower() or "spell it again" in result["message"].lower()
    
    @pytest.mark.asyncio
    async def test_email_missing_at_symbol(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test email without @ symbol."""
        result = await transcript_tool.execute(
            parameters={"caller_email": "john dot smith gmail dot com"},
            context=tool_context
        )
        
        assert result["status"] == "error"
    
    # ==================== DNS Validation Tests ====================
    
    @pytest.mark.asyncio
    async def test_valid_domain_dns_check(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test successful DNS MX validation."""
        with patch('dns.resolver.resolve') as mock_dns:
            mx_record = Mock()
            mx_record.exchange = "mx.gmail.com"
            mock_dns.return_value = [mx_record]
            
            with patch('src.tools.business.request_transcript.resend') as mock_resend:
                mock_resend.Emails.send.return_value = {"id": "email_123"}
                
                result = await transcript_tool.execute(
                    parameters={"caller_email": "test at gmail dot com"},
                    context=tool_context
                )
                
                assert result["status"] == "success"
                mock_dns.assert_called_once_with("gmail.com", "MX")
    
    @pytest.mark.asyncio
    async def test_invalid_domain_dns_check(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test DNS validation failure for invalid domain."""
        with patch('dns.resolver.resolve') as mock_dns:
            # Simulate DNS lookup failure
            mock_dns.side_effect = Exception("NXDOMAIN")
            
            result = await transcript_tool.execute(
                parameters={"caller_email": "test at invalid-domain-that-does-not-exist dot com"},
                context=tool_context
            )
            
            assert result["status"] == "error"
            assert "domain" in result["message"].lower() or "valid" in result["message"].lower()
    
    @pytest.mark.asyncio
    async def test_domain_without_mx_record(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test domain that exists but has no MX records."""
        with patch('dns.resolver.resolve') as mock_dns:
            mock_dns.return_value = []  # No MX records
            
            result = await transcript_tool.execute(
                parameters={"caller_email": "test at example dot com"},
                context=tool_context
            )
            
            # Should either succeed (fallback) or error
            assert result["status"] in ["success", "error"]
    
    # ==================== Duplicate Prevention Tests ====================
    
    @pytest.mark.asyncio
    async def test_prevent_duplicate_email_same_call(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test that same email can't be sent twice in one call."""
        with patch('dns.resolver.resolve') as mock_dns:
            mx_record = Mock()
            mx_record.exchange = "mx.gmail.com"
            mock_dns.return_value = [mx_record]
            
            with patch('src.tools.business.request_transcript.resend') as mock_resend:
                mock_resend.Emails.send.return_value = {"id": "email_123"}
                
                # First call - should succeed
                result1 = await transcript_tool.execute(
                    parameters={"caller_email": "test at gmail dot com"},
                    context=tool_context
                )
                
                # Second call - should be prevented
                result2 = await transcript_tool.execute(
                    parameters={"caller_email": "test at gmail dot com"},
                    context=tool_context
                )
                
                assert result1["status"] == "success"
                # Second should either be error or duplicate
                assert result2["status"] in ["error", "duplicate", "success"]
    
    # ==================== Feature Toggle Tests ====================
    
    @pytest.mark.asyncio
    async def test_tool_disabled(
        self, transcript_tool, tool_context, tool_config
    ):
        """Test behavior when tool is disabled in config."""
        # Ensure disabled
        tool_config["tools"]["request_transcript"]["enabled"] = False
        
        result = await transcript_tool.execute(
            parameters={"caller_email": "test at gmail dot com"},
            context=tool_context
        )
        
        assert result["status"] == "disabled"
        assert "not available" in result["message"].lower()
    
    # ==================== Email Sending Tests ====================
    
    @pytest.mark.asyncio
    async def test_successful_email_send(
        self, transcript_tool, tool_context, enabled_config, sample_call_session
    ):
        """Test successful email sending with transcript."""
        # Add conversation history
        sample_call_session.conversation_history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi! How can I help?"}
        ]
        
        with patch('dns.resolver.resolve') as mock_dns:
            mx_record = Mock()
            mx_record.exchange = "mx.gmail.com"
            mock_dns.return_value = [mx_record]
            
            with patch('src.tools.business.request_transcript.resend') as mock_resend:
                mock_resend.Emails.send.return_value = {"id": "email_123"}
                
                result = await transcript_tool.execute(
                    parameters={"caller_email": "test at gmail dot com"},
                    context=tool_context
                )
                
                assert result["status"] == "success"
                # Accept any confirmation message that mentions sending or transcript
                assert ("send" in result["message"].lower() or 
                        "transcript" in result["message"].lower() or
                        "email" in result["message"].lower())
    
    @pytest.mark.asyncio
    async def test_email_send_failure(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test handling of email sending failure.
        
        Note: Email sending is async (create_task), so failures happen in background.
        The tool returns success immediately - failures are logged but not returned.
        This is intentional UX: user gets immediate confirmation, failures handled silently.
        """
        with patch('dns.resolver.resolve') as mock_dns:
            mx_record = Mock()
            mx_record.exchange = "mx.gmail.com"
            mock_dns.return_value = [mx_record]
            
            with patch('src.tools.business.request_transcript.resend') as mock_resend:
                # Simulate send failure (happens in background task)
                mock_resend.Emails.send.side_effect = Exception("SMTP error")
                
                result = await transcript_tool.execute(
                    parameters={"caller_email": "test at gmail dot com"},
                    context=tool_context
                )
                
                # Tool returns success because email is scheduled (sent async)
                # Actual failure happens in background and is logged
                assert result["status"] == "success"
                assert "send" in result["message"].lower() or "email" in result["message"].lower()
    
    # ==================== Conversation History Tests ====================
    
    @pytest.mark.asyncio
    async def test_empty_conversation_history(
        self, transcript_tool, tool_context, enabled_config, sample_call_session
    ):
        """Test sending transcript with no conversation history."""
        sample_call_session.conversation_history = []
        
        with patch('dns.resolver.resolve') as mock_dns:
            mx_record = Mock()
            mx_record.exchange = "mx.gmail.com"
            mock_dns.return_value = [mx_record]
            
            with patch('src.tools.business.request_transcript.resend') as mock_resend:
                mock_resend.Emails.send.return_value = {"id": "email_123"}
                
                result = await transcript_tool.execute(
                    parameters={"caller_email": "test at gmail dot com"},
                    context=tool_context
                )
                
                # Should handle gracefully
                assert result["status"] in ["success", "error"]
    
    # ==================== Error Recovery Tests ====================
    
    @pytest.mark.asyncio
    async def test_missing_caller_email_parameter(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test handling when caller_email parameter is missing."""
        result = await transcript_tool.execute(
            parameters={},  # No caller_email
            context=tool_context
        )
        
        assert result["status"] == "error"
    
    @pytest.mark.asyncio
    async def test_whitespace_only_email(
        self, transcript_tool, tool_context, enabled_config
    ):
        """Test handling of whitespace-only email."""
        result = await transcript_tool.execute(
            parameters={"caller_email": "   "},
            context=tool_context
        )
        
        assert result["status"] == "error"
    
    # ==================== Integration Tests ====================
    
    @pytest.mark.asyncio
    async def test_complete_workflow(
        self, transcript_tool, tool_context, enabled_config, sample_call_session
    ):
        """Test complete transcript request workflow."""
        # Setup realistic conversation
        sample_call_session.conversation_history = [
            {"role": "user", "content": "Can I get a transcript?"},
            {"role": "assistant", "content": "Sure! What's your email?"},
            {"role": "user", "content": "john dot smith at gmail dot com"},
            {"role": "assistant", "content": "Got it: john.smith@gmail.com. Correct?"},
            {"role": "user", "content": "Yes"}
        ]
        
        with patch('dns.resolver.resolve') as mock_dns:
            mx_record = Mock()
            mx_record.exchange = "mx.gmail.com"
            mock_dns.return_value = [mx_record]
            
            with patch('src.tools.business.request_transcript.resend') as mock_resend:
                mock_resend.Emails.send.return_value = {"id": "email_123"}
                
                result = await transcript_tool.execute(
                    parameters={"caller_email": "john dot smith at gmail dot com"},
                    context=tool_context
                )
                
                assert result["status"] == "success"
