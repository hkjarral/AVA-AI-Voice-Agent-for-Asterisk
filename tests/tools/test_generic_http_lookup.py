"""
Unit tests for GenericHTTPLookupTool (pre-call HTTP lookups).

Tests the HTTP lookup tool used for CRM enrichment during pre-call phase.
"""

import pytest
import os
import json
from unittest.mock import AsyncMock, patch, MagicMock
import aiohttp

from src.tools.http.generic_lookup import (
    GenericHTTPLookupTool, HTTPLookupConfig, create_http_lookup_tool
)
from src.tools.context import PreCallContext
from src.tools.base import ToolPhase, ToolCategory


# --- HTTPLookupConfig Tests ---

class TestHTTPLookupConfig:
    """Tests for HTTPLookupConfig dataclass."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = HTTPLookupConfig(name="test_lookup")
        
        assert config.name == "test_lookup"
        assert config.enabled is True
        assert config.phase == "pre_call"
        assert config.is_global is False
        assert config.timeout_ms == 2000
        assert config.method == "GET"
        assert config.headers == {}
        assert config.query_params == {}
        assert config.output_variables == {}
        assert config.max_response_size_bytes == 65536
    
    def test_custom_values(self):
        """Test custom configuration values."""
        config = HTTPLookupConfig(
            name="ghl_lookup",
            enabled=True,
            timeout_ms=3000,
            url="https://api.example.com/contacts",
            method="POST",
            headers={"Authorization": "Bearer token"},
            query_params={"phone": "{caller_number}"},
            output_variables={"name": "contact.name"},
            hold_audio_file="custom/please-wait",
            hold_audio_threshold_ms=1000,
        )
        
        assert config.name == "ghl_lookup"
        assert config.timeout_ms == 3000
        assert config.url == "https://api.example.com/contacts"
        assert config.method == "POST"
        assert "Authorization" in config.headers
        assert config.hold_audio_file == "custom/please-wait"


# --- GenericHTTPLookupTool Tests ---

class TestGenericHTTPLookupTool:
    """Tests for GenericHTTPLookupTool."""

    @staticmethod
    def _make_content(chunks):
        class _Content:
            def __init__(self, parts):
                self._parts = list(parts)

            async def iter_chunked(self, _size):
                for part in self._parts:
                    yield part

        return _Content(chunks)
    
    @pytest.fixture
    def lookup_config(self):
        """Standard lookup configuration for tests."""
        return HTTPLookupConfig(
            name="test_crm_lookup",
            enabled=True,
            timeout_ms=2000,
            url="https://api.example.com/contacts",
            method="GET",
            headers={"Authorization": "Bearer ${API_KEY}"},
            query_params={"phone": "{caller_number}"},
            output_variables={
                "customer_name": "firstName",
                "customer_email": "email",
            },
        )
    
    @pytest.fixture
    def precall_context(self):
        """Standard PreCallContext for tests."""
        return PreCallContext(
            call_id="test_call_123",
            caller_number="+1234567890",
            called_number="+0987654321",
            caller_name="Test Caller",
            context_name="support",
        )
    
    def test_definition_properties(self, lookup_config):
        """Test that tool definition has correct properties."""
        tool = GenericHTTPLookupTool(lookup_config)
        defn = tool.definition
        
        assert defn.name == "test_crm_lookup"
        assert defn.phase == ToolPhase.PRE_CALL
        assert defn.category == ToolCategory.BUSINESS
        assert "customer_name" in defn.output_variables
        assert "customer_email" in defn.output_variables
        assert defn.timeout_ms == 2000
    
    @pytest.mark.asyncio
    async def test_disabled_tool_returns_empty(self, precall_context):
        """Test that disabled tool returns empty values."""
        config = HTTPLookupConfig(
            name="disabled_lookup",
            enabled=False,
            output_variables={"name": "firstName"},
        )
        tool = GenericHTTPLookupTool(config)
        
        result = await tool.execute(precall_context)
        
        assert result == {"name": ""}
    
    @pytest.mark.asyncio
    async def test_no_url_returns_empty(self, precall_context):
        """Test that missing URL returns empty values."""
        config = HTTPLookupConfig(
            name="no_url_lookup",
            enabled=True,
            url="",
            output_variables={"name": "firstName"},
        )
        tool = GenericHTTPLookupTool(config)
        
        result = await tool.execute(precall_context)
        
        assert result == {"name": ""}
    
    @pytest.mark.asyncio
    async def test_successful_lookup(self, lookup_config, precall_context):
        """Test successful HTTP lookup with response parsing."""
        tool = GenericHTTPLookupTool(lookup_config)
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "100"}
        payload = {
            "firstName": "John",
            "lastName": "Doe",
            "email": "john@example.com",
        }
        mock_response.content = self._make_content([json.dumps(payload).encode("utf-8")])
        mock_response.charset = "utf-8"
        
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
            result = await tool.execute(precall_context)
        
        assert result["customer_name"] == "John"
        assert result["customer_email"] == "john@example.com"

    @pytest.mark.asyncio
    async def test_missing_content_length_enforces_max_size(self, lookup_config, precall_context):
        """Missing Content-Length must still enforce max_response_size_bytes."""
        lookup_config.max_response_size_bytes = 10  # smaller than payload below
        tool = GenericHTTPLookupTool(lookup_config)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.content = self._make_content([b'{"firstName":"John","email":"john@example.com"}'])
        mock_response.charset = "utf-8"

        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_request_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_request_cm)

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result = await tool.execute(precall_context)

        assert result == {"customer_name": "", "customer_email": ""}

    @pytest.mark.asyncio
    async def test_content_length_smaller_than_body_still_enforces_max_size(self, lookup_config, precall_context):
        """Lying Content-Length must not bypass max_response_size_bytes."""
        lookup_config.max_response_size_bytes = 10  # smaller than payload below
        tool = GenericHTTPLookupTool(lookup_config)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "1"}  # misleading/smaller than actual
        mock_response.content = self._make_content([b'{"firstName":"John","email":"john@example.com"}'])
        mock_response.charset = "utf-8"

        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_request_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_request_cm)

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result = await tool.execute(precall_context)

        assert result == {"customer_name": "", "customer_email": ""}
    
    @pytest.mark.asyncio
    async def test_non_200_returns_empty(self, lookup_config, precall_context):
        """Test that non-200 response returns empty values."""
        tool = GenericHTTPLookupTool(lookup_config)
        
        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.headers = {}
        
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
            result = await tool.execute(precall_context)
        
        assert result == {"customer_name": "", "customer_email": ""}
    
    @pytest.mark.asyncio
    async def test_request_error_returns_empty(self, lookup_config, precall_context):
        """Test that request errors return empty values."""
        tool = GenericHTTPLookupTool(lookup_config)
        
        with patch("aiohttp.ClientSession") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                side_effect=aiohttp.ClientError("Connection failed")
            )
            
            result = await tool.execute(precall_context)
        
        assert result == {"customer_name": "", "customer_email": ""}


# --- Variable Substitution Tests ---

class TestVariableSubstitution:
    """Tests for variable substitution in URLs and parameters."""
    
    @pytest.fixture
    def tool(self):
        config = HTTPLookupConfig(
            name="sub_test",
            url="https://api.example.com/{context_name}/lookup",
            query_params={"phone": "{caller_number}", "id": "{call_id}"},
            output_variables={"result": "data"},
        )
        return GenericHTTPLookupTool(config)
    
    @pytest.fixture
    def context(self):
        return PreCallContext(
            call_id="call_abc123",
            caller_number="+1555123456",
            called_number="+18005551234",
            caller_name="Jane Doe",
            context_name="sales",
            campaign_id="camp_001",
            lead_id="lead_xyz",
        )
    
    def test_substitute_caller_number(self, tool, context):
        """Test substitution of caller_number."""
        result = tool._substitute_variables("{caller_number}", context)
        assert result == "+1555123456"
    
    def test_substitute_called_number(self, tool, context):
        """Test substitution of called_number."""
        result = tool._substitute_variables("{called_number}", context)
        assert result == "+18005551234"
    
    def test_substitute_caller_name(self, tool, context):
        """Test substitution of caller_name."""
        result = tool._substitute_variables("{caller_name}", context)
        assert result == "Jane Doe"
    
    def test_substitute_context_name(self, tool, context):
        """Test substitution of context_name."""
        result = tool._substitute_variables("{context_name}", context)
        assert result == "sales"
    
    def test_substitute_call_id(self, tool, context):
        """Test substitution of call_id."""
        result = tool._substitute_variables("{call_id}", context)
        assert result == "call_abc123"
    
    def test_substitute_campaign_id(self, tool, context):
        """Test substitution of campaign_id."""
        result = tool._substitute_variables("{campaign_id}", context)
        assert result == "camp_001"
    
    def test_substitute_lead_id(self, tool, context):
        """Test substitution of lead_id."""
        result = tool._substitute_variables("{lead_id}", context)
        assert result == "lead_xyz"
    
    def test_substitute_env_variable(self, tool, context):
        """Test substitution of environment variables."""
        with patch.dict(os.environ, {"TEST_API_KEY": "secret123"}):
            result = tool._substitute_variables("Bearer ${TEST_API_KEY}", context)
            assert result == "Bearer secret123"
    
    def test_substitute_missing_env_variable(self, tool, context):
        """Test substitution of missing env variable returns empty."""
        result = tool._substitute_variables("Bearer ${MISSING_KEY}", context)
        assert result == "Bearer "
    
    def test_substitute_multiple_variables(self, tool, context):
        """Test substitution of multiple variables in one string."""
        result = tool._substitute_variables(
            "Call {call_id} from {caller_number} to {context_name}",
            context
        )
        assert result == "Call call_abc123 from +1555123456 to sales"


# --- Path Extraction Tests ---

class TestPathExtraction:
    """Tests for JSON path extraction from responses."""
    
    @pytest.fixture
    def tool(self):
        config = HTTPLookupConfig(
            name="extract_test",
            output_variables={"result": "data"},
        )
        return GenericHTTPLookupTool(config)
    
    def test_extract_simple_field(self, tool):
        """Test extraction of simple field."""
        data = {"firstName": "John", "lastName": "Doe"}
        result = tool._extract_path(data, "firstName")
        assert result == "John"
    
    def test_extract_nested_field(self, tool):
        """Test extraction of nested field."""
        data = {"contact": {"name": "John", "email": "john@test.com"}}
        result = tool._extract_path(data, "contact.name")
        assert result == "John"
    
    def test_extract_array_element(self, tool):
        """Test extraction of array element."""
        data = {"contacts": [{"name": "First"}, {"name": "Second"}]}
        result = tool._extract_path(data, "contacts[0].name")
        assert result == "First"
    
    def test_extract_array_second_element(self, tool):
        """Test extraction of second array element."""
        data = {"contacts": [{"name": "First"}, {"name": "Second"}]}
        result = tool._extract_path(data, "contacts[1].name")
        assert result == "Second"
    
    def test_extract_missing_field_returns_none(self, tool):
        """Test extraction of missing field returns None."""
        data = {"firstName": "John"}
        result = tool._extract_path(data, "lastName")
        assert result is None
    
    def test_extract_missing_nested_returns_none(self, tool):
        """Test extraction of missing nested field returns None."""
        data = {"contact": {"name": "John"}}
        result = tool._extract_path(data, "contact.email")
        assert result is None
    
    def test_extract_out_of_bounds_array_returns_none(self, tool):
        """Test extraction of out-of-bounds array index returns None."""
        data = {"contacts": [{"name": "Only"}]}
        result = tool._extract_path(data, "contacts[5].name")
        assert result is None
    
    def test_extract_empty_path_returns_data(self, tool):
        """Test extraction with empty path returns original data."""
        data = {"test": "value"}
        result = tool._extract_path(data, "")
        assert result == data

    def test_extract_wildcard_all_elements(self, tool):
        """Test [*] wildcard returns all array elements."""
        data = {"items": [{"name": "A"}, {"name": "B"}]}
        result = tool._extract_path(data, "items[*].name")
        assert result == ["A", "B"]

    def test_extract_wildcard_full_array(self, tool):
        """Test [*] without further path returns full array."""
        data = {"items": [1, 2, 3]}
        result = tool._extract_path(data, "items[*]")
        assert result == [1, 2, 3]


class TestOutputVariablesSerialization:
    """Tests for JSON serialization of output variables including arrays."""

    def test_array_output_serialized_as_json(self):
        """Wildcard output variables should be JSON-serialized, not Python repr."""
        config = HTTPLookupConfig(
            name="serial_test",
            output_variables={"names": "users[*].name"},
        )
        tool = GenericHTTPLookupTool(config)
        data = {"users": [{"name": "Alice"}, {"name": "Bob"}]}
        results = tool._extract_output_variables(data)
        assert results["names"] == '["Alice", "Bob"]'

    def test_dict_output_serialized_as_json(self):
        config = HTTPLookupConfig(
            name="serial_test",
            output_variables={"info": "contact"},
        )
        tool = GenericHTTPLookupTool(config)
        data = {"contact": {"name": "Alice", "email": "a@b.com"}}
        results = tool._extract_output_variables(data)
        parsed = json.loads(results["info"])
        assert parsed == {"name": "Alice", "email": "a@b.com"}

    def test_scalar_output_not_json_wrapped(self):
        config = HTTPLookupConfig(
            name="serial_test",
            output_variables={"name": "name", "count": "count"},
        )
        tool = GenericHTTPLookupTool(config)
        data = {"name": "Alice", "count": 42}
        results = tool._extract_output_variables(data)
        assert results["name"] == "Alice"
        assert results["count"] == "42"

    def test_missing_output_returns_empty_string(self):
        config = HTTPLookupConfig(
            name="serial_test",
            output_variables={"missing": "no_such_field"},
        )
        tool = GenericHTTPLookupTool(config)
        results = tool._extract_output_variables({"other": 1})
        assert results["missing"] == ""


# --- Factory Function Tests ---

class TestCreateHTTPLookupTool:
    """Tests for create_http_lookup_tool factory function."""
    
    def test_create_minimal_tool(self):
        """Test creating tool with minimal config."""
        tool = create_http_lookup_tool("simple_lookup", {})
        
        assert tool.definition.name == "simple_lookup"
        assert tool.config.enabled is True
        assert tool.config.method == "GET"
    
    def test_create_full_tool(self):
        """Test creating tool with full config."""
        config_dict = {
            "enabled": True,
            "phase": "pre_call",
            "is_global": True,
            "timeout_ms": 3000,
            "hold_audio_file": "custom/hold",
            "hold_audio_threshold_ms": 800,
            "url": "https://api.example.com/lookup",
            "method": "POST",
            "headers": {"Authorization": "Bearer token"},
            "query_params": {"phone": "{caller_number}"},
            "body_template": '{"phone": "{caller_number}"}',
            "output_variables": {"name": "contact.name"},
            "max_response_size_bytes": 32768,
        }
        
        tool = create_http_lookup_tool("full_lookup", config_dict)
        
        assert tool.definition.name == "full_lookup"
        assert tool.definition.is_global is True
        assert tool.config.timeout_ms == 3000
        assert tool.config.url == "https://api.example.com/lookup"
        assert tool.config.method == "POST"
        assert tool.config.hold_audio_file == "custom/hold"
        assert tool.config.max_response_size_bytes == 32768


# --- URL Redaction Tests ---

class TestURLRedaction:
    """Tests for URL redaction in logging."""
    
    @pytest.fixture
    def tool(self):
        config = HTTPLookupConfig(name="redact_test")
        return GenericHTTPLookupTool(config)
    
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
    
    def test_redact_multiple_sensitive(self, tool):
        """Test redaction of multiple sensitive parameters."""
        url = "https://api.example.com?api_key=key1&auth=auth2&normal=ok"
        redacted = tool._redact_url(url)
        assert "key1" not in redacted
        assert "auth2" not in redacted
        assert "normal=ok" in redacted
    
    def test_no_redaction_needed(self, tool):
        """Test URL without sensitive parameters."""
        url = "https://api.example.com?phone=123&name=test"
        redacted = tool._redact_url(url)
        assert redacted == url
