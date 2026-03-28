"""
Unit tests for InCallHTTPTool (in-call HTTP lookups).

Tests the HTTP lookup tool used for AI-invoked requests during conversation.
"""

import pytest
import os
from unittest.mock import AsyncMock, patch, MagicMock
from dataclasses import dataclass
import aiohttp
import json

from src.tools.http.in_call_lookup import (
    InCallHTTPTool, InCallHTTPConfig, create_in_call_http_tool
)
from src.tools.base import ToolPhase, ToolCategory


# --- InCallHTTPConfig Tests ---

class TestInCallHTTPConfig:
    """Tests for InCallHTTPConfig dataclass."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = InCallHTTPConfig(name="test_tool")
        
        assert config.name == "test_tool"
        assert config.description == ""
        assert config.enabled is True
        assert config.is_global is False
        assert config.timeout_ms == 5000
        assert config.method == "POST"
        assert config.headers == {}
        assert config.query_params == {}
        assert config.output_variables == {}
        assert config.parameters == []
        assert config.return_raw_json is False
        assert config.max_response_size_bytes == 65536
        assert "sorry" in config.error_message.lower()
    
    def test_custom_values(self):
        """Test custom configuration values."""
        config = InCallHTTPConfig(
            name="check_availability",
            description="Check appointment availability",
            enabled=True,
            is_global=True,
            timeout_ms=8000,
            url="https://api.example.com/availability",
            method="POST",
            headers={"Authorization": "Bearer token"},
            query_params={"location": "123"},
            parameters=[
                {"name": "date", "type": "string", "required": True},
                {"name": "time", "type": "string", "required": True},
            ],
            output_variables={"available": "data.available"},
            return_raw_json=False,
            error_message="Could not check availability.",
        )
        
        assert config.name == "check_availability"
        assert config.description == "Check appointment availability"
        assert config.is_global is True
        assert config.timeout_ms == 8000
        assert config.url == "https://api.example.com/availability"
        assert config.method == "POST"
        assert "Authorization" in config.headers
        assert len(config.parameters) == 2
        assert config.error_message == "Could not check availability."


# --- InCallHTTPTool Tests ---

class TestInCallHTTPTool:
    """Tests for InCallHTTPTool."""

    def _make_content(self, chunks):
        class _Content:
            def __init__(self, parts):
                self._parts = list(parts)

            async def iter_chunked(self, _size):
                for part in self._parts:
                    yield part

        return _Content(chunks)
    
    @pytest.fixture
    def tool_config(self):
        """Standard tool configuration for tests."""
        return InCallHTTPConfig(
            name="test_lookup",
            description="Test HTTP lookup tool",
            enabled=True,
            timeout_ms=5000,
            url="https://api.example.com/lookup",
            method="POST",
            headers={"Authorization": "Bearer ${API_KEY}"},
            body_template='{"caller": "{caller_number}", "date": "{date}"}',
            parameters=[
                {"name": "date", "type": "string", "description": "Date", "required": True},
            ],
            output_variables={
                "available": "data.available",
                "next_slot": "data.next_slot",
            },
        )
    
    @pytest.fixture
    def execution_context(self):
        """Standard execution context for tests using MagicMock."""
        context = MagicMock()
        context.call_id = "test_call_123"
        context.caller_number = "+1234567890"
        context.called_number = "+0987654321"
        context.caller_name = "Test Caller"
        context.context_name = "support"
        context.caller_channel_id = "PJSIP/test-001"
        context.session_store = None
        return context
    
    def test_definition_properties(self, tool_config):
        """Test that tool definition has correct properties."""
        tool = InCallHTTPTool(tool_config)
        defn = tool.definition
        
        assert defn.name == "test_lookup"
        assert defn.description == "Test HTTP lookup tool"
        assert defn.phase == ToolPhase.IN_CALL
        assert defn.category == ToolCategory.BUSINESS
        assert defn.timeout_ms == 5000
        assert len(defn.parameters) == 1
        assert defn.parameters[0].name == "date"
    
    def test_definition_with_global(self):
        """Test that is_global is correctly set."""
        config = InCallHTTPConfig(
            name="global_tool",
            is_global=True,
        )
        tool = InCallHTTPTool(config)
        
        assert tool.definition.is_global is True
    
    @pytest.mark.asyncio
    async def test_disabled_tool_returns_failed(self, execution_context):
        """Test that disabled tool returns failed status."""
        config = InCallHTTPConfig(
            name="disabled_tool",
            enabled=False,
            error_message="Tool is disabled",
        )
        tool = InCallHTTPTool(config)
        
        result = await tool.execute({"date": "2026-01-30"}, execution_context)
        
        assert result["status"] == "failed"
        assert result["message"] == "Tool is disabled"
    
    @pytest.mark.asyncio
    async def test_no_url_returns_error(self, execution_context):
        """Test that missing URL returns error status."""
        config = InCallHTTPConfig(
            name="no_url_tool",
            enabled=True,
            url="",
            error_message="No URL configured",
        )
        tool = InCallHTTPTool(config)
        
        result = await tool.execute({"date": "2026-01-30"}, execution_context)
        
        assert result["status"] == "error"
        assert result["message"] == "No URL configured"
    
    @pytest.mark.asyncio
    async def test_successful_lookup(self, tool_config, execution_context):
        """Test successful HTTP lookup with response parsing."""
        tool = InCallHTTPTool(tool_config)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "100"}
        payload = {
            "data": {
                "available": True,
                "next_slot": "2026-01-30 10:00",
            }
        }
        mock_response.charset = "utf-8"
        mock_response.content = self._make_content([json.dumps(payload).encode("utf-8")])
        
        # Create proper async context manager for request
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_request_cm.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_request_cm)
        
        # Create proper async context manager for session
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        
        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result = await tool.execute({"date": "2026-01-30"}, execution_context)
        
        assert result["status"] == "success"
        assert result["data"]["available"] is True
        assert result["data"]["next_slot"] == "2026-01-30 10:00"
    
    @pytest.mark.asyncio
    async def test_return_raw_json(self, execution_context):
        """Test returning raw JSON response."""
        config = InCallHTTPConfig(
            name="raw_json_tool",
            enabled=True,
            url="https://api.example.com/data",
            return_raw_json=True,
        )
        tool = InCallHTTPTool(config)
        
        response_data = {"foo": "bar", "count": 42, "items": [1, 2, 3]}
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "100"}
        mock_response.charset = "utf-8"
        mock_response.content = self._make_content([json.dumps(response_data).encode("utf-8")])
        
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_request_cm.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_request_cm)
        
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        
        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result = await tool.execute({}, execution_context)
        
        assert result["status"] == "success"
        assert result["data"] == response_data
    
    @pytest.mark.asyncio
    async def test_non_200_returns_failed(self, tool_config, execution_context):
        """Test that non-200 response returns failed status."""
        tool = InCallHTTPTool(tool_config)
        
        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.headers = {}
        
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_request_cm.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_request_cm)
        
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        
        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result = await tool.execute({"date": "2026-01-30"}, execution_context)
        
        assert result["status"] == "failed"
    
    @pytest.mark.asyncio
    async def test_request_error_returns_error(self, tool_config, execution_context):
        """Test that request errors return error status."""
        tool = InCallHTTPTool(tool_config)
        
        with patch("aiohttp.ClientSession") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                side_effect=aiohttp.ClientError("Connection failed")
            )
            
            result = await tool.execute({"date": "2026-01-30"}, execution_context)
        
        assert result["status"] == "error"
    
    @pytest.mark.asyncio
    async def test_response_too_large(self, execution_context):
        """Test that oversized responses are rejected."""
        config = InCallHTTPConfig(
            name="size_test",
            enabled=True,
            url="https://api.example.com/data",
            max_response_size_bytes=100,
        )
        tool = InCallHTTPTool(config)
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "1000000"}  # 1MB
        
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_request_cm.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_request_cm)
        
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        
        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result = await tool.execute({}, execution_context)
        
        assert result["status"] == "error"


# --- Variable Substitution Tests ---

class TestInCallVariableSubstitution:
    """Tests for variable substitution in InCallHTTPTool."""
    
    @pytest.fixture
    def tool(self):
        config = InCallHTTPConfig(
            name="sub_test",
            url="https://api.example.com/{context_name}/lookup",
            body_template='{"phone": "{caller_number}", "date": "{date}"}',
            parameters=[{"name": "date", "type": "string"}],
        )
        return InCallHTTPTool(config)
    
    def test_substitute_context_variables(self, tool):
        """Test substitution of context variables."""
        context = {
            "caller_number": "+1555123456",
            "called_number": "+18005551234",
            "caller_name": "Jane Doe",
            "context_name": "sales",
            "call_id": "call_abc123",
        }
        
        result = tool._substitute_variables("{caller_number}", context)
        assert result == "+1555123456"
        
        result = tool._substitute_variables("{context_name}", context)
        assert result == "sales"
    
    def test_substitute_ai_parameters(self, tool):
        """Test substitution of AI-provided parameters."""
        context = {
            "caller_number": "+1555123456",
            "date": "2026-01-30",
            "time": "10:00",
        }
        
        result = tool._substitute_variables("{date}", context)
        assert result == "2026-01-30"
        
        result = tool._substitute_variables("{time}", context)
        assert result == "10:00"
    
    def test_substitute_env_variable(self, tool):
        """Test substitution of environment variables."""
        context = {}
        with patch.dict(os.environ, {"TEST_API_KEY": "secret123"}):
            result = tool._substitute_variables("Bearer ${TEST_API_KEY}", context)
            assert result == "Bearer secret123"
    
    def test_substitute_missing_env_variable(self, tool):
        """Test substitution of missing env variable returns empty."""
        context = {}
        result = tool._substitute_variables("Bearer ${MISSING_KEY}", context)
        assert result == "Bearer "
    
    def test_substitute_multiple_variables(self, tool):
        """Test substitution of multiple variables in one string."""
        context = {
            "caller_number": "+1555123456",
            "date": "2026-01-30",
            "context_name": "support",
        }
        
        result = tool._substitute_variables(
            "Call from {caller_number} on {date} in {context_name}",
            context
        )
        assert result == "Call from +1555123456 on 2026-01-30 in support"


# --- Build Substitution Context Tests ---

class TestBuildSubstitutionContext:
    """Tests for _build_substitution_context method."""
    
    @pytest.fixture
    def tool(self):
        config = InCallHTTPConfig(name="context_test")
        return InCallHTTPTool(config)
    
    @pytest.fixture
    def execution_context(self):
        """Standard execution context for tests."""
        context = MagicMock()
        context.call_id = "call_123"
        context.caller_number = "+15551234567"
        context.called_number = "+18005559999"
        context.caller_name = "Test User"
        context.context_name = "support"
        context.caller_channel_id = "PJSIP/test-001"
        context.session_store = None
        return context
    
    @pytest.mark.asyncio
    async def test_includes_context_variables(self, tool, execution_context):
        """Test that context variables are included."""
        result = await tool._build_substitution_context({}, execution_context)
        
        assert result["caller_number"] == "+15551234567"
        assert result["called_number"] == "+18005559999"
        assert result["caller_name"] == "Test User"
        assert result["context_name"] == "support"
        assert result["call_id"] == "call_123"
    
    @pytest.mark.asyncio
    async def test_includes_ai_parameters(self, tool, execution_context):
        """Test that AI parameters are included."""
        ai_params = {"date": "2026-01-30", "time": "10:00"}
        
        result = await tool._build_substitution_context(ai_params, execution_context)
        
        assert result["date"] == "2026-01-30"
        assert result["time"] == "10:00"
    
    @pytest.mark.asyncio
    async def test_ai_params_override_context(self, tool, execution_context):
        """Test that AI parameters override context variables (except built-ins)."""
        # AI shouldn't override built-in context vars like caller_number
        ai_params = {"custom_var": "custom_value"}
        
        result = await tool._build_substitution_context(ai_params, execution_context)
        
        # Built-in should remain
        assert result["caller_number"] == "+15551234567"
        # Custom param should be added
        assert result["custom_var"] == "custom_value"
    
    @pytest.mark.asyncio
    async def test_includes_pre_call_results(self, tool):
        """Test that pre-call results are included from session."""
        # Create mock session with pre_call_results
        mock_session = MagicMock()
        mock_session.pre_call_results = {
            "customer_id": "cust_12345",
            "customer_name": "John Doe",
        }
        
        mock_session_store = AsyncMock()
        mock_session_store.get_by_call_id = AsyncMock(return_value=mock_session)
        
        context = MagicMock()
        context.call_id = "call_123"
        context.caller_number = "+15551234567"
        context.called_number = "+18005559999"
        context.caller_name = "Test User"
        context.context_name = "support"
        context.caller_channel_id = "PJSIP/test-001"
        context.session_store = mock_session_store
        
        result = await tool._build_substitution_context({}, context)
        
        assert result["customer_id"] == "cust_12345"
        assert result["customer_name"] == "John Doe"


# --- Path Extraction Tests ---

class TestInCallPathExtraction:
    """Tests for JSON path extraction from responses."""
    
    @pytest.fixture
    def tool(self):
        config = InCallHTTPConfig(name="extract_test")
        return InCallHTTPTool(config)
    
    def test_extract_simple_field(self, tool):
        """Test extraction of simple field."""
        data = {"available": True, "slot": "10:00"}
        result = tool._extract_path(data, "available")
        assert result is True
    
    def test_extract_nested_field(self, tool):
        """Test extraction of nested field."""
        data = {"data": {"available": True, "next_slot": "10:30"}}
        result = tool._extract_path(data, "data.available")
        assert result is True
    
    def test_extract_array_element(self, tool):
        """Test extraction of array element."""
        data = {"slots": [{"time": "09:00"}, {"time": "10:00"}]}
        result = tool._extract_path(data, "slots[0].time")
        assert result == "09:00"
    
    def test_extract_second_array_element(self, tool):
        """Test extraction of second array element."""
        data = {"slots": [{"time": "09:00"}, {"time": "10:00"}]}
        result = tool._extract_path(data, "slots[1].time")
        assert result == "10:00"
    
    def test_extract_missing_field_returns_none(self, tool):
        """Test extraction of missing field returns None."""
        data = {"available": True}
        result = tool._extract_path(data, "missing_field")
        assert result is None
    
    def test_extract_missing_nested_returns_none(self, tool):
        """Test extraction of missing nested field returns None."""
        data = {"data": {"available": True}}
        result = tool._extract_path(data, "data.missing")
        assert result is None
    
    def test_extract_out_of_bounds_returns_none(self, tool):
        """Test extraction of out-of-bounds array index returns None."""
        data = {"slots": [{"time": "09:00"}]}
        result = tool._extract_path(data, "slots[5].time")
        assert result is None
    
    def test_extract_empty_path_returns_data(self, tool):
        """Test extraction with empty path returns original data."""
        data = {"test": "value"}
        result = tool._extract_path(data, "")
        assert result == data

    def test_extract_wildcard_all_elements(self, tool):
        """Test [*] wildcard returns all array elements."""
        data = {"slots": [{"time": "09:00"}, {"time": "10:00"}]}
        result = tool._extract_path(data, "slots[*].time")
        assert result == ["09:00", "10:00"]

    def test_extract_wildcard_full_array(self, tool):
        """Test [*] without further path returns full array."""
        data = {"slots": [{"time": "09:00"}, {"time": "10:00"}]}
        result = tool._extract_path(data, "slots[*]")
        assert isinstance(result, list)
        assert len(result) == 2


class TestInCallOutputVariablesSerialization:
    """Tests for JSON serialization of output variables including arrays."""

    def test_array_output_serialized_as_json(self):
        config = InCallHTTPConfig(
            name="serial_test",
            output_variables={"times": "slots[*].time"},
        )
        tool = InCallHTTPTool(config)
        data = {"slots": [{"time": "09:00"}, {"time": "10:00"}]}
        results = tool._extract_output_variables(data)
        assert results["times"] == '["09:00", "10:00"]'

    def test_scalar_output_preserved(self):
        config = InCallHTTPConfig(
            name="serial_test",
            output_variables={"avail": "available"},
        )
        tool = InCallHTTPTool(config)
        results = tool._extract_output_variables({"available": True})
        assert results["avail"] is True


# --- Result Message Tests ---

class TestBuildResultMessage:
    """Tests for _build_result_message method."""
    
    @pytest.fixture
    def tool(self):
        config = InCallHTTPConfig(name="msg_test")
        return InCallHTTPTool(config)
    
    def test_empty_data_returns_default(self, tool):
        """Test that empty data returns default message."""
        result = tool._build_result_message({})
        assert result == "No data retrieved."
    
    def test_builds_readable_message(self, tool):
        """Test that message is human-readable."""
        data = {"available": "yes", "next_slot": "10:00 AM"}
        result = tool._build_result_message(data)
        
        assert "Retrieved:" in result
        assert "Available" in result
        assert "yes" in result
    
    def test_skips_empty_values(self, tool):
        """Test that empty values are skipped."""
        data = {"available": "yes", "empty_field": ""}
        result = tool._build_result_message(data)

        assert "yes" in result
        assert "empty_field" not in result.lower()

    def test_preserves_falsy_zero(self, tool):
        """Test that 0 is not dropped from result message."""
        data = {"count": 0}
        result = tool._build_result_message(data)
        assert "0" in result

    def test_preserves_falsy_false(self, tool):
        """Test that False is not dropped from result message."""
        data = {"available": False}
        result = tool._build_result_message(data)
        assert "False" in result


# --- URL Redaction Tests ---

class TestInCallURLRedaction:
    """Tests for URL redaction in logging."""
    
    @pytest.fixture
    def tool(self):
        config = InCallHTTPConfig(name="redact_test")
        return InCallHTTPTool(config)
    
    def test_redact_api_key(self, tool):
        """Test redaction of api_key parameter."""
        url = "https://api.example.com?api_key=secret123&other=value"
        redacted = tool._redact_url(url)
        assert "secret123" not in redacted
        assert "api_key=***" in redacted
        assert "other=value" in redacted
    
    def test_redact_token(self, tool):
        """Test redaction of token parameter."""
        url = "https://api.example.com?token=mytoken&foo=bar"
        redacted = tool._redact_url(url)
        assert "mytoken" not in redacted
        assert "token=***" in redacted
    
    def test_no_redaction_needed(self, tool):
        """Test URL without sensitive parameters."""
        url = "https://api.example.com?date=2026-01-30&time=10:00"
        redacted = tool._redact_url(url)
        assert redacted == url


# --- Factory Function Tests ---

class TestCreateInCallHTTPTool:
    """Tests for create_in_call_http_tool factory function."""
    
    def test_create_minimal_tool(self):
        """Test creating tool with minimal config."""
        tool = create_in_call_http_tool("simple_tool", {})
        
        assert tool.definition.name == "simple_tool"
        assert tool.config.enabled is True
        assert tool.config.method == "POST"
        assert tool.config.timeout_ms == 5000
    
    def test_create_full_tool(self):
        """Test creating tool with full config."""
        config_dict = {
            "description": "Check appointment availability",
            "enabled": True,
            "is_global": True,
            "timeout_ms": 8000,
            "hold_audio_file": "custom/hold",
            "hold_audio_threshold_ms": 800,
            "url": "https://api.example.com/availability",
            "method": "POST",
            "headers": {"Authorization": "Bearer token"},
            "query_params": {"location": "123"},
            "body_template": '{"date": "{date}"}',
            "parameters": [
                {"name": "date", "type": "string", "required": True},
            ],
            "output_variables": {"available": "data.available"},
            "return_raw_json": False,
            "max_response_size_bytes": 32768,
            "error_message": "Could not check availability.",
        }
        
        tool = create_in_call_http_tool("availability_check", config_dict)
        
        assert tool.definition.name == "availability_check"
        assert tool.definition.description == "Check appointment availability"
        assert tool.definition.is_global is True
        assert tool.config.timeout_ms == 8000
        assert tool.config.url == "https://api.example.com/availability"
        assert tool.config.method == "POST"
        assert tool.config.hold_audio_file == "custom/hold"
        assert tool.config.max_response_size_bytes == 32768
        assert len(tool.config.parameters) == 1
        assert tool.config.error_message == "Could not check availability."
