"""
Unit tests for PreCallTool and PostCallTool base classes.

Tests the abstract base classes and context dataclasses for
pre-call (enrichment) and post-call (webhook) tool phases.
"""

import pytest
from dataclasses import dataclass
from typing import Dict
from unittest.mock import AsyncMock, Mock

from src.tools.base import (
    PreCallTool, PostCallTool, ToolDefinition, ToolCategory, ToolPhase
)
from src.tools.context import PreCallContext, PostCallContext


# --- Concrete implementations for testing abstract classes ---

class MockPreCallTool(PreCallTool):
    """Concrete PreCallTool for testing."""
    
    def __init__(self, name: str = "mock_precall", output_vars: list = None):
        self._name = name
        self._output_vars = output_vars or ["customer_name", "customer_email"]
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self._name,
            description="Mock pre-call tool for testing",
            category=ToolCategory.BUSINESS,
            phase=ToolPhase.PRE_CALL,
            output_variables=self._output_vars,
            timeout_ms=2000,
        )
    
    async def execute(self, context: PreCallContext) -> Dict[str, str]:
        return {
            "customer_name": "John Doe",
            "customer_email": "john@example.com",
        }


class MockPostCallTool(PostCallTool):
    """Concrete PostCallTool for testing."""
    
    def __init__(self, name: str = "mock_postcall"):
        self._name = name
        self.executed = False
        self.last_context = None
    
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self._name,
            description="Mock post-call tool for testing",
            category=ToolCategory.BUSINESS,
            phase=ToolPhase.POST_CALL,
            is_global=True,
            timeout_ms=5000,
        )
    
    async def execute(self, context: PostCallContext) -> None:
        self.executed = True
        self.last_context = context


# --- PreCallContext Tests ---

class TestPreCallContext:
    """Tests for PreCallContext dataclass."""
    
    def test_basic_creation(self):
        """Test creating a basic PreCallContext."""
        ctx = PreCallContext(
            call_id="call_123",
            caller_number="+1234567890",
        )
        
        assert ctx.call_id == "call_123"
        assert ctx.caller_number == "+1234567890"
        assert ctx.called_number is None
        assert ctx.caller_name is None
        assert ctx.context_name == ""
        assert ctx.call_direction == "inbound"
    
    def test_full_context(self):
        """Test PreCallContext with all fields populated."""
        ctx = PreCallContext(
            call_id="call_456",
            caller_number="+1234567890",
            called_number="+0987654321",
            caller_name="John Doe",
            context_name="support",
            call_direction="inbound",
            campaign_id="camp_001",
            lead_id="lead_001",
            channel_vars={"UNIQUEID": "123.456"},
        )
        
        assert ctx.call_id == "call_456"
        assert ctx.called_number == "+0987654321"
        assert ctx.caller_name == "John Doe"
        assert ctx.context_name == "support"
        assert ctx.campaign_id == "camp_001"
        assert ctx.lead_id == "lead_001"
        assert ctx.channel_vars["UNIQUEID"] == "123.456"
    
    def test_outbound_context(self):
        """Test PreCallContext for outbound calls."""
        ctx = PreCallContext(
            call_id="outbound_789",
            caller_number="+1111111111",
            called_number="+2222222222",
            call_direction="outbound",
            campaign_id="campaign_abc",
            lead_id="lead_xyz",
        )
        
        assert ctx.call_direction == "outbound"
        assert ctx.campaign_id == "campaign_abc"
        assert ctx.lead_id == "lead_xyz"
    
    def test_get_config_value(self):
        """Test config value retrieval with dot notation."""
        ctx = PreCallContext(
            call_id="call_123",
            caller_number="+1234567890",
            config={
                "tools": {
                    "ghl_lookup": {
                        "enabled": True,
                        "timeout_ms": 3000,
                    }
                }
            }
        )
        
        assert ctx.get_config_value("tools.ghl_lookup.enabled") is True
        assert ctx.get_config_value("tools.ghl_lookup.timeout_ms") == 3000
        assert ctx.get_config_value("tools.missing.key", "default") == "default"
    
    def test_get_config_value_no_config(self):
        """Test get_config_value returns default when config is None."""
        ctx = PreCallContext(
            call_id="call_123",
            caller_number="+1234567890",
        )
        
        assert ctx.get_config_value("any.key", "fallback") == "fallback"


# --- PostCallContext Tests ---

class TestPostCallContext:
    """Tests for PostCallContext dataclass."""
    
    def test_basic_creation(self):
        """Test creating a basic PostCallContext."""
        ctx = PostCallContext(
            call_id="call_123",
            caller_number="+1234567890",
        )
        
        assert ctx.call_id == "call_123"
        assert ctx.caller_number == "+1234567890"
        assert ctx.call_duration_seconds == 0
        assert ctx.conversation_history == []
        assert ctx.tool_calls == []
    
    def test_full_context(self):
        """Test PostCallContext with all fields populated."""
        ctx = PostCallContext(
            call_id="call_456",
            caller_number="+1234567890",
            called_number="+0987654321",
            caller_name="John Doe",
            context_name="support",
            provider="deepgram",
            call_direction="inbound",
            call_duration_seconds=120,
            call_outcome="answered_human",
            call_start_time="2024-01-01T10:00:00Z",
            call_end_time="2024-01-01T10:02:00Z",
            conversation_history=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ],
            summary="Customer called for support.",
            tool_calls=[{"name": "transfer", "status": "success"}],
            pre_call_results={"customer_name": "John"},
            campaign_id="camp_001",
            lead_id="lead_001",
        )
        
        assert ctx.call_duration_seconds == 120
        assert ctx.call_outcome == "answered_human"
        assert len(ctx.conversation_history) == 2
        assert ctx.summary == "Customer called for support."
        assert len(ctx.tool_calls) == 1
        assert ctx.pre_call_results["customer_name"] == "John"
    
    def test_to_payload_dict(self):
        """Test conversion to webhook payload dictionary."""
        ctx = PostCallContext(
            call_id="call_789",
            caller_number="+1234567890",
            called_number="+0987654321",
            caller_name="Jane Doe",
            context_name="sales",
            provider="openai_realtime",
            call_direction="inbound",
            call_duration_seconds=60,
            call_outcome="completed",
            call_start_time="2024-01-01T12:00:00Z",
            call_end_time="2024-01-01T12:01:00Z",
            conversation_history=[{"role": "user", "content": "Test"}],
            summary="Test call summary",
            campaign_id="camp_xyz",
            lead_id="lead_abc",
        )
        
        payload = ctx.to_payload_dict()
        
        assert payload["call_id"] == "call_789"
        assert payload["caller_number"] == "+1234567890"
        assert payload["called_number"] == "+0987654321"
        assert payload["caller_name"] == "Jane Doe"
        assert payload["context_name"] == "sales"
        assert payload["provider"] == "openai_realtime"
        assert payload["call_duration"] == 60
        assert payload["call_outcome"] == "completed"
        assert payload["summary"] == "Test call summary"
        assert payload["campaign_id"] == "camp_xyz"
        assert payload["lead_id"] == "lead_abc"
        # JSON fields should be strings
        assert '"role": "user"' in payload["transcript_json"]
    
    def test_to_payload_dict_handles_none(self):
        """Test that to_payload_dict handles None values gracefully."""
        ctx = PostCallContext(
            call_id="call_123",
            caller_number="+1234567890",
        )
        
        payload = ctx.to_payload_dict()
        
        assert payload["called_number"] == ""
        assert payload["caller_name"] == ""
        assert payload["call_start_time"] == ""
        assert payload["call_end_time"] == ""
        assert payload["summary"] == ""
        assert payload["campaign_id"] == ""
        assert payload["lead_id"] == ""

    def test_to_payload_dict_flattens_pre_call_results(self):
        """Pre-call output variables are exposed as individual placeholders."""
        ctx = PostCallContext(
            call_id="call_123",
            caller_number="+1234567890",
            pre_call_results={"customer_name": "John Smith", "va": "Acme Corp"},
        )

        payload = ctx.to_payload_dict()

        # Individual pre-call variables usable directly in webhook templates
        assert payload["customer_name"] == "John Smith"
        assert payload["va"] == "Acme Corp"
        # Blob still present for backward compatibility
        assert '"customer_name": "John Smith"' in payload["pre_call_results_json"]

    def test_to_payload_dict_pre_call_does_not_override_builtins(self):
        """A pre-call variable must not clobber a built-in payload key."""
        ctx = PostCallContext(
            call_id="call_123",
            caller_number="+1234567890",
            caller_name="Jane Doe",
            pre_call_results={"caller_number": "999", "caller_name": "SPOOF"},
        )

        payload = ctx.to_payload_dict()

        # Built-ins win over same-named pre-call variables
        assert payload["caller_number"] == "+1234567890"
        assert payload["caller_name"] == "Jane Doe"


# --- PreCallTool Base Class Tests ---

class TestPreCallToolBase:
    """Tests for PreCallTool abstract base class."""
    
    def test_definition_has_precall_phase(self):
        """Test that PreCallTool definitions have PRE_CALL phase."""
        tool = MockPreCallTool()
        assert tool.definition.phase == ToolPhase.PRE_CALL
    
    def test_definition_has_output_variables(self):
        """Test that PreCallTool definitions declare output variables."""
        tool = MockPreCallTool(output_vars=["name", "email", "phone"])
        assert tool.definition.output_variables == ["name", "email", "phone"]
    
    @pytest.mark.asyncio
    async def test_execute_returns_dict(self):
        """Test that execute returns a dictionary of string values."""
        tool = MockPreCallTool()
        ctx = PreCallContext(
            call_id="test_123",
            caller_number="+1234567890",
        )
        
        result = await tool.execute(ctx)
        
        assert isinstance(result, dict)
        assert "customer_name" in result
        assert "customer_email" in result
        assert all(isinstance(v, str) for v in result.values())


# --- PostCallTool Base Class Tests ---

class TestPostCallToolBase:
    """Tests for PostCallTool abstract base class."""
    
    def test_definition_has_postcall_phase(self):
        """Test that PostCallTool definitions have POST_CALL phase."""
        tool = MockPostCallTool()
        assert tool.definition.phase == ToolPhase.POST_CALL
    
    def test_definition_can_be_global(self):
        """Test that PostCallTool definitions support is_global flag."""
        tool = MockPostCallTool()
        assert tool.definition.is_global is True
    
    @pytest.mark.asyncio
    async def test_execute_fire_and_forget(self):
        """Test that execute is fire-and-forget (returns None)."""
        tool = MockPostCallTool()
        ctx = PostCallContext(
            call_id="test_123",
            caller_number="+1234567890",
            conversation_history=[{"role": "user", "content": "Hello"}],
        )
        
        result = await tool.execute(ctx)
        
        assert result is None
        assert tool.executed is True
        assert tool.last_context == ctx
    
    @pytest.mark.asyncio
    async def test_execute_receives_full_context(self):
        """Test that execute receives the full PostCallContext."""
        tool = MockPostCallTool()
        ctx = PostCallContext(
            call_id="test_456",
            caller_number="+1234567890",
            call_duration_seconds=120,
            conversation_history=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ],
            summary="Test summary",
        )
        
        await tool.execute(ctx)
        
        assert tool.last_context.call_id == "test_456"
        assert tool.last_context.call_duration_seconds == 120
        assert len(tool.last_context.conversation_history) == 2
        assert tool.last_context.summary == "Test summary"


# --- ToolDefinition Phase System Tests ---

class TestToolDefinitionPhaseSystem:
    """Tests for ToolDefinition phase-related fields."""
    
    def test_default_phase_is_in_call(self):
        """Test that default phase is IN_CALL for backward compatibility."""
        defn = ToolDefinition(
            name="test_tool",
            description="Test",
            category=ToolCategory.BUSINESS,
        )
        assert defn.phase == ToolPhase.IN_CALL
    
    def test_output_variables_default_empty(self):
        """Test that output_variables defaults to empty list."""
        defn = ToolDefinition(
            name="test_tool",
            description="Test",
            category=ToolCategory.BUSINESS,
        )
        assert defn.output_variables == []
    
    def test_timeout_ms_optional(self):
        """Test that timeout_ms is optional."""
        defn = ToolDefinition(
            name="test_tool",
            description="Test",
            category=ToolCategory.BUSINESS,
        )
        assert defn.timeout_ms is None
    
    def test_hold_audio_fields(self):
        """Test hold audio configuration fields."""
        defn = ToolDefinition(
            name="test_tool",
            description="Test",
            category=ToolCategory.BUSINESS,
            phase=ToolPhase.PRE_CALL,
            hold_audio_file="custom/please-wait",
            hold_audio_threshold_ms=1000,
        )
        assert defn.hold_audio_file == "custom/please-wait"
        assert defn.hold_audio_threshold_ms == 1000
    
    def test_is_global_default_false(self):
        """Test that is_global defaults to False."""
        defn = ToolDefinition(
            name="test_tool",
            description="Test",
            category=ToolCategory.BUSINESS,
        )
        assert defn.is_global is False
