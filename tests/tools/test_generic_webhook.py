"""
Unit tests for GenericWebhookTool (post-call webhooks).

Tests the webhook tool used for sending call data to external systems after call ends.
"""

import pytest
import os
import json
from unittest.mock import AsyncMock, patch, MagicMock
import aiohttp

from src.tools.http.generic_webhook import (
    GenericWebhookTool, WebhookConfig, create_webhook_tool
)
from src.tools.context import PostCallContext
from src.tools.base import ToolPhase, ToolCategory


# --- WebhookConfig Tests ---

class TestWebhookConfig:
    """Tests for WebhookConfig dataclass."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = WebhookConfig(name="test_webhook")
        
        assert config.name == "test_webhook"
        assert config.enabled is True
        assert config.phase == "post_call"
        assert config.is_global is False
        assert config.timeout_ms == 5000
        assert config.method == "POST"
        assert config.headers == {}
        assert config.content_type == "application/json"
        assert config.generate_summary is False
        assert config.summary_max_words == 100
    
    def test_custom_values(self):
        """Test custom configuration values."""
        config = WebhookConfig(
            name="n8n_webhook",
            enabled=True,
            is_global=True,
            timeout_ms=10000,
            url="https://n8n.example.com/webhook/call",
            method="POST",
            headers={"Authorization": "Bearer token"},
            payload_template='{"call_id": "{call_id}"}',
            content_type="application/json",
            generate_summary=True,
            summary_max_words=50,
        )
        
        assert config.name == "n8n_webhook"
        assert config.is_global is True
        assert config.timeout_ms == 10000
        assert config.url == "https://n8n.example.com/webhook/call"
        assert config.generate_summary is True
        assert config.summary_max_words == 50


# --- GenericWebhookTool Tests ---

class TestGenericWebhookTool:
    """Tests for GenericWebhookTool."""
    
    @pytest.fixture
    def webhook_config(self):
        """Standard webhook configuration for tests."""
        return WebhookConfig(
            name="test_webhook",
            enabled=True,
            timeout_ms=5000,
            url="https://webhook.example.com/call-completed",
            method="POST",
            headers={"Content-Type": "application/json"},
            payload_template='{"call_id": "{call_id}", "caller": "{caller_number}"}',
        )
    
    @pytest.fixture
    def postcall_context(self):
        """Standard PostCallContext for tests."""
        return PostCallContext(
            call_id="test_call_123",
            caller_number="+1234567890",
            called_number="+0987654321",
            caller_name="Test Caller",
            context_name="support",
            provider="deepgram",
            call_duration_seconds=120,
            call_outcome="completed",
            call_start_time="2024-01-01T10:00:00Z",
            call_end_time="2024-01-01T10:02:00Z",
            conversation_history=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ],
        )
    
    def test_definition_properties(self, webhook_config):
        """Test that tool definition has correct properties."""
        tool = GenericWebhookTool(webhook_config)
        defn = tool.definition
        
        assert defn.name == "test_webhook"
        assert defn.phase == ToolPhase.POST_CALL
        assert defn.category == ToolCategory.BUSINESS
        assert defn.timeout_ms == 5000
    
    @pytest.mark.asyncio
    async def test_disabled_tool_skips_execution(self, postcall_context):
        """Test that disabled tool does not execute."""
        config = WebhookConfig(
            name="disabled_webhook",
            enabled=False,
            url="https://webhook.example.com/test",
        )
        tool = GenericWebhookTool(config)
        
        with patch("aiohttp.ClientSession") as mock_client:
            await tool.execute(postcall_context)
            mock_client.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_no_url_skips_execution(self, postcall_context):
        """Test that missing URL skips execution."""
        config = WebhookConfig(
            name="no_url_webhook",
            enabled=True,
            url="",
        )
        tool = GenericWebhookTool(config)
        
        with patch("aiohttp.ClientSession") as mock_client:
            await tool.execute(postcall_context)
            mock_client.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_successful_webhook_send(self, webhook_config, postcall_context):
        """Test successful webhook execution."""
        tool = GenericWebhookTool(webhook_config)
        
        mock_response = AsyncMock()
        mock_response.status = 200
        
        mock_session = AsyncMock()
        mock_session.request = AsyncMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))
        
        with patch("aiohttp.ClientSession") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            
            # Should not raise
            await tool.execute(postcall_context)

    @pytest.mark.asyncio
    async def test_get_never_sends_legacy_hidden_payload(self, postcall_context):
        tool = GenericWebhookTool(WebhookConfig(
            name="legacy_get_webhook",
            url="https://webhook.example.com/status",
            method="GET",
            headers={},
            payload_template='{"call_id":"{call_id}"}',
        ))
        response = AsyncMock(status=204)
        response.text = AsyncMock(return_value="")
        request_cm = AsyncMock()
        request_cm.__aenter__ = AsyncMock(return_value=response)
        request_cm.__aexit__ = AsyncMock(return_value=None)
        session = AsyncMock()
        session.request = MagicMock(return_value=request_cm)
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=session)
        session_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=session_cm):
            await tool.execute(postcall_context)

        kwargs = session.request.call_args.kwargs
        assert kwargs["method"] == "GET"
        assert kwargs["data"] is None
        assert "Content-Type" not in kwargs["headers"]

    @pytest.mark.asyncio
    async def test_delete_preserves_configured_payload(self, postcall_context):
        tool = GenericWebhookTool(WebhookConfig(
            name="delete_webhook",
            url="https://webhook.example.com/status",
            method="DELETE",
            headers={},
            payload_template='{"call_id":"{call_id}"}',
        ))
        response = AsyncMock(status=204)
        response.text = AsyncMock(return_value="")
        request_cm = AsyncMock()
        request_cm.__aenter__ = AsyncMock(return_value=response)
        request_cm.__aexit__ = AsyncMock(return_value=None)
        session = AsyncMock()
        session.request = MagicMock(return_value=request_cm)
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=session)
        session_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=session_cm):
            await tool.execute(postcall_context)

        kwargs = session.request.call_args.kwargs
        assert kwargs["method"] == "DELETE"
        assert json.loads(kwargs["data"]) == {"call_id": "test_call_123"}
        assert kwargs["headers"]["Content-Type"] == "application/json"
    
    @pytest.mark.asyncio
    async def test_non_2xx_logs_warning(self, webhook_config, postcall_context):
        """Test that non-2xx response logs warning but doesn't fail."""
        tool = GenericWebhookTool(webhook_config)
        
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")
        
        mock_session = AsyncMock()
        mock_session.request = AsyncMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))
        
        with patch("aiohttp.ClientSession") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            
            # Should not raise (fire-and-forget)
            await tool.execute(postcall_context)
    
    @pytest.mark.asyncio
    async def test_request_error_handled(self, webhook_config, postcall_context):
        """Test that request errors are handled gracefully."""
        tool = GenericWebhookTool(webhook_config)
        
        with patch("aiohttp.ClientSession") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                side_effect=aiohttp.ClientError("Connection failed")
            )
            
            # Should not raise (fire-and-forget)
            await tool.execute(postcall_context)


# --- Payload Building Tests ---

class TestPayloadBuilding:
    """Tests for webhook payload construction."""
    
    @pytest.fixture
    def context(self):
        return PostCallContext(
            call_id="call_xyz",
            caller_number="+1555123456",
            called_number="+18005551234",
            caller_name="John Doe",
            context_name="sales",
            provider="openai_realtime",
            call_direction="inbound",
            call_duration_seconds=90,
            call_outcome="completed",
            call_start_time="2024-01-15T14:30:00Z",
            call_end_time="2024-01-15T14:31:30Z",
            conversation_history=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ],
            campaign_id="camp_abc",
            lead_id="lead_123",
        )
    
    def test_simple_variable_substitution(self, context):
        """Test simple variable substitution in payload."""
        config = WebhookConfig(
            name="test",
            payload_template='{"id": "{call_id}", "phone": "{caller_number}"}',
        )
        tool = GenericWebhookTool(config)
        
        payload = tool._build_payload(context)
        data = json.loads(payload)
        
        assert data["id"] == "call_xyz"
        assert data["phone"] == "+1555123456"
    
    def test_json_field_not_quoted(self, context):
        """Test that _json fields are not quoted."""
        config = WebhookConfig(
            name="test",
            payload_template='{"transcript": {transcript_json}}',
        )
        tool = GenericWebhookTool(config)
        
        payload = tool._build_payload(context)
        data = json.loads(payload)
        
        assert isinstance(data["transcript"], list)
        assert data["transcript"][0]["role"] == "user"
    
    def test_special_characters_escaped(self, context):
        """Test that special characters in values are JSON-escaped."""
        context.caller_name = 'John "The Man" Doe'
        config = WebhookConfig(
            name="test",
            payload_template='{"name": "{caller_name}"}',
        )
        tool = GenericWebhookTool(config)
        
        payload = tool._build_payload(context)
        # Should be valid JSON
        data = json.loads(payload)
        assert data["name"] == 'John "The Man" Doe'
    
    def test_env_variable_substitution(self, context):
        """Test environment variable substitution."""
        config = WebhookConfig(
            name="test",
            payload_template='{"key": "${TEST_WEBHOOK_KEY}"}',
        )
        tool = GenericWebhookTool(config)
        
        with patch.dict(os.environ, {"TEST_WEBHOOK_KEY": "secret123"}):
            payload = tool._build_payload(context)
            data = json.loads(payload)
            assert data["key"] == "secret123"
    
    def test_summary_json_available_and_transcript_preserved(self, context):
        """Test that summary_json is available and transcript_json remains the transcript."""
        context.summary = "Customer called about billing question."
        config = WebhookConfig(
            name="test",
            payload_template='{"transcript": {transcript_json}, "summary": "{summary}", "summary_json": {summary_json}}',
            generate_summary=True,
        )
        tool = GenericWebhookTool(config)
        
        payload = tool._build_payload(context)
        data = json.loads(payload)
        
        assert data["transcript"] == context.conversation_history
        assert data["summary"] == "Customer called about billing question."
        assert data["summary_json"] == "Customer called about billing question."
    
    def test_all_context_fields_available(self, context):
        """Test that all PostCallContext fields are available for substitution."""
        config = WebhookConfig(
            name="test",
            payload_template='''{
                "call_id": "{call_id}",
                "caller_number": "{caller_number}",
                "called_number": "{called_number}",
                "caller_name": "{caller_name}",
                "context_name": "{context_name}",
                "provider": "{provider}",
                "call_direction": "{call_direction}",
                "call_duration": {call_duration},
                "call_outcome": "{call_outcome}",
                "campaign_id": "{campaign_id}",
                "lead_id": "{lead_id}"
            }''',
        )
        tool = GenericWebhookTool(config)
        
        payload = tool._build_payload(context)
        data = json.loads(payload)
        
        assert data["call_id"] == "call_xyz"
        assert data["caller_number"] == "+1555123456"
        assert data["called_number"] == "+18005551234"
        assert data["caller_name"] == "John Doe"
        assert data["context_name"] == "sales"
        assert data["provider"] == "openai_realtime"
        assert data["call_direction"] == "inbound"
        assert data["call_duration"] == 90
        assert data["call_outcome"] == "completed"
        assert data["campaign_id"] == "camp_abc"
        assert data["lead_id"] == "lead_123"


# --- Variable Substitution in URL/Headers Tests ---

class TestURLHeaderSubstitution:
    """Tests for variable substitution in URLs and headers."""
    
    @pytest.fixture
    def tool(self):
        config = WebhookConfig(name="sub_test")
        return GenericWebhookTool(config)
    
    @pytest.fixture
    def context(self):
        return PostCallContext(
            call_id="call_abc",
            caller_number="+1555999888",
            called_number="+18001234567",
            caller_name="Test User",
            context_name="billing",
            provider="deepgram",
            call_direction="outbound",
            campaign_id="camp_test",
            lead_id="lead_test",
        )
    
    def test_substitute_call_id(self, tool, context):
        """Test substitution of call_id in URL."""
        result = tool._substitute_variables("https://api.test.com/{call_id}", context)
        assert result == "https://api.test.com/call_abc"
    
    def test_substitute_caller_number(self, tool, context):
        """Test substitution of caller_number."""
        result = tool._substitute_variables("{caller_number}", context)
        assert result == "+1555999888"
    
    def test_substitute_context_name(self, tool, context):
        """Test substitution of context_name."""
        result = tool._substitute_variables("context={context_name}", context)
        assert result == "context=billing"
    
    def test_substitute_env_var(self, tool, context):
        """Test substitution of environment variable."""
        with patch.dict(os.environ, {"WEBHOOK_TOKEN": "abc123"}):
            result = tool._substitute_variables("Bearer ${WEBHOOK_TOKEN}", context)
            assert result == "Bearer abc123"
    
    def test_substitute_campaign_fields(self, tool, context):
        """Test substitution of campaign/lead IDs."""
        result = tool._substitute_variables(
            "campaign={campaign_id}&lead={lead_id}", context
        )
        assert result == "campaign=camp_test&lead=lead_test"


# --- URL Redaction Tests ---

class TestWebhookURLRedaction:
    """Tests for URL redaction in logging."""
    
    @pytest.fixture
    def tool(self):
        config = WebhookConfig(name="redact_test")
        return GenericWebhookTool(config)
    
    def test_redact_api_key(self, tool):
        """Test redaction of api_key parameter."""
        url = "https://webhook.test.com?api_key=secret&data=ok"
        redacted = tool._redact_url(url)
        assert "secret" not in redacted
        assert "api_key=***" in redacted
    
    def test_redact_auth_token(self, tool):
        """Test redaction of auth parameter."""
        url = "https://webhook.test.com?auth=mytoken123"
        redacted = tool._redact_url(url)
        assert "mytoken123" not in redacted
        assert "auth=***" in redacted


# --- Summary Generation Tests ---

class TestSummaryGeneration:
    """Tests for AI-powered summary generation."""
    
    @pytest.fixture
    def context_with_history(self):
        return PostCallContext(
            call_id="call_summary_test",
            caller_number="+1234567890",
            conversation_history=[
                {"role": "user", "content": "Hi, I need help with my bill"},
                {"role": "assistant", "content": "I'd be happy to help with your billing question."},
                {"role": "user", "content": "My bill is too high this month"},
                {"role": "assistant", "content": "Let me look into that for you."},
            ],
        )
    
    @pytest.mark.asyncio
    async def test_summary_generation_with_openai(self, context_with_history):
        """Test summary generation using OpenAI."""
        pytest.importorskip("openai")  # Skip if openai not installed
        
        config = WebhookConfig(
            name="test_summary",
            generate_summary=True,
            summary_max_words=50,
        )
        tool = GenericWebhookTool(config)
        
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Customer called about high bill."
        
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            with patch("src.tools.http.generic_webhook.openai") as mock_openai_module:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                mock_openai_module.AsyncOpenAI.return_value = mock_client
                
                summary = await tool._generate_summary(context_with_history)
                
                assert summary == "Customer called about high bill."
    
    @pytest.mark.asyncio
    async def test_summary_empty_history_returns_empty(self):
        """Test that empty conversation history returns empty summary."""
        config = WebhookConfig(
            name="test_summary",
            generate_summary=True,
        )
        tool = GenericWebhookTool(config)
        
        context = PostCallContext(
            call_id="empty_call",
            caller_number="+1234567890",
            conversation_history=[],
        )
        
        summary = await tool._generate_summary(context)
        assert summary == ""
    
    @pytest.mark.asyncio
    async def test_summary_no_api_key_returns_empty(self, context_with_history):
        """Test that missing API key returns empty summary."""
        config = WebhookConfig(
            name="test_summary",
            generate_summary=True,
        )
        tool = GenericWebhookTool(config)
        
        with patch.dict(os.environ, {}, clear=True):
            # Remove OPENAI_API_KEY if present
            os.environ.pop("OPENAI_API_KEY", None)
            summary = await tool._generate_summary(context_with_history)
            assert summary == ""


# --- Factory Function Tests ---

class TestCreateWebhookTool:
    """Tests for create_webhook_tool factory function."""
    
    def test_create_minimal_tool(self):
        """Test creating tool with minimal config."""
        tool = create_webhook_tool("simple_webhook", {})
        
        assert tool.definition.name == "simple_webhook"
        assert tool.config.enabled is True
        assert tool.config.method == "POST"
    
    def test_create_full_tool(self):
        """Test creating tool with full config."""
        config_dict = {
            "enabled": True,
            "phase": "post_call",
            "is_global": True,
            "timeout_ms": 10000,
            "url": "https://webhook.example.com/call",
            "method": "POST",
            "headers": {"Authorization": "Bearer token"},
            "payload_template": '{"id": "{call_id}"}',
            "content_type": "application/json",
            "generate_summary": True,
            "summary_max_words": 75,
        }
        
        tool = create_webhook_tool("full_webhook", config_dict)
        
        assert tool.definition.name == "full_webhook"
        assert tool.definition.is_global is True
        assert tool.config.timeout_ms == 10000
        assert tool.config.url == "https://webhook.example.com/call"
        assert tool.config.generate_summary is True
        assert tool.config.summary_max_words == 75


# --- Default Payload Tests ---

class TestDefaultPayload:
    """Tests for default payload when no template is provided."""
    
    def test_default_payload_structure(self):
        """Test that default payload (no template) uses context.to_payload_dict()."""
        context = PostCallContext(
            call_id="call_default",
            caller_number="+1234567890",
            context_name="test",
            provider="deepgram",
            call_duration_seconds=60,
        )
        
        # Verify to_payload_dict() returns expected structure
        payload = context.to_payload_dict()
        
        assert payload["call_id"] == "call_default"
        assert payload["caller_number"] == "+1234567890"
        assert payload["context_name"] == "test"
        assert payload["provider"] == "deepgram"
        assert payload["call_duration"] == 60
        assert "transcript_json" in payload
        assert "tool_calls_json" in payload
