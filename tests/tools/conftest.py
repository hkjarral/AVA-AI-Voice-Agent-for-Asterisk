"""
Shared pytest fixtures for tool testing.

Provides mocks for ARI client, SessionStore, and ToolExecutionContext.
"""

import pytest
from unittest.mock import AsyncMock, Mock, MagicMock
from datetime import datetime
from src.tools.context import ToolExecutionContext
from src.core.models import CallSession


@pytest.fixture
def mock_ari_client():
    """
    Mock ARI client with common telephony operations.
    
    Pre-configured with:
    - send_command: For channel origination
    - hangup_channel: For call termination
    - remove_channel_from_bridge: For bridge manipulation
    - add_channel_to_bridge: For bridge manipulation
    """
    client = AsyncMock()
    
    # Channel origination (returns channel dict)
    # Also handles blind transfer redirect (returns status 204)
    async def send_command_mock(method, resource, data=None, params=None):
        if method == "POST" and "redirect" in resource:
            # Blind transfer redirect
            return {"status": 204}
        else:
            # Channel origination
            return {
                "id": "SIP/6000-00000001",
                "state": "Ring",
                "caller": {"number": "6789", "name": "AI Agent"},
                "connected": {"number": "6000"}
            }
    
    client.send_command = AsyncMock(side_effect=send_command_mock)
    
    # Channel hangup
    client.hangup_channel = AsyncMock()
    
    # Bridge manipulation
    client.remove_channel_from_bridge = AsyncMock()
    client.add_channel_to_bridge = AsyncMock()
    client.get_bridge_channels = AsyncMock(return_value=[
        "PJSIP/caller-00000001",
        "UnicastRTP/ai-agent-00000002"
    ])
    
    return client


@pytest.fixture
def mock_session_store():
    """
    Mock SessionStore with async operations.
    
    Pre-configured with:
    - get_by_call_id: Returns None by default (set in tests)
    - get_by_channel_id: Returns None by default
    - upsert_call: No-op async function
    """
    store = AsyncMock()
    store.get_by_call_id = AsyncMock(return_value=None)
    store.get_by_channel_id = AsyncMock(return_value=None)
    store.upsert_call = AsyncMock()
    store.remove_call = AsyncMock()
    return store


@pytest.fixture
def sample_call_session():
    """
    Sample CallSession for testing tool execution.
    
    Represents an active call with:
    - Caller connected
    - Bridge established
    - AI provider active
    - No ongoing actions
    """
    return CallSession(
        call_id="test_call_123",
        caller_channel_id="PJSIP/caller-00000001",
        caller_name="John Doe",
        caller_number="+1234567890",
        external_media_id="UnicastRTP/ai-agent-00000002",
        bridge_id="test_bridge_123",
        provider_name="deepgram",
        conversation_state="active",
        status="connected",
        start_time=datetime.now(),
        conversation_history=[
            {
                "role": "user",
                "content": "Hello, I need help",
                "timestamp": datetime.now().timestamp()
            },
            {
                "role": "assistant",
                "content": "Hi! I'm happy to help you today.",
                "timestamp": datetime.now().timestamp()
            }
        ],
        current_action=None,  # No ongoing action
        audio_capture_enabled=True,
        provider_session_active=True
    )


@pytest.fixture
def tool_config():
    """
    Tool configuration dict matching config/ai-agent.yaml structure.
    
    Includes:
    - Extension definitions (internal)
    - AI identity for caller ID
    - Tool-specific settings
    """
    return {
        "tools": {
            "extensions": {
                "internal": {
                    "6000": {
                        "name": "Live Agent",
                        "aliases": ["support", "agent", "human", "representative"],
                        "dial_string": "SIP/6000",
                        "action_type": "transfer",
                        "mode": "warm",
                        "timeout": 30
                    },
                    "7000": {
                        "name": "Blind Transfer Test",
                        "aliases": ["blind"],
                        "dial_string": "SIP/7000",
                        "action_type": "transfer",
                        "mode": "blind",
                        "timeout": 30
                    },
                    "6001": {
                        "name": "Sales Department",
                        "aliases": ["sales"],
                        "dial_string": "SIP/6001",
                        "action_type": "transfer",
                        "mode": "warm",
                        "timeout": 30
                    },
                    "6002": {
                        "name": "Technical Support",
                        "aliases": ["tech", "technical", "it"],
                        "dial_string": "SIP/6002",
                        "action_type": "transfer",
                        "mode": "warm",
                        "timeout": 45
                    }
                }
            },
            "transfer": {
                "defer_until_playback_complete": False,
            },
            "ai_identity": {
                "name": "AI Agent",
                "number": "6789"
            },
            "send_email_summary": {
                "enabled": True,
                "admin_email": "admin@example.com",
                "from_email": "ai-agent@example.com"
            },
            "request_transcript": {
                "enabled": True,
                "from_email": "transcripts@example.com",
                "admin_bcc": "admin@example.com"
            }
        }
    }


@pytest.fixture
def tool_context(mock_ari_client, mock_session_store, sample_call_session, tool_config):
    """
    Complete ToolExecutionContext for testing.
    
    Pre-configured with:
    - Mock ARI client
    - Mock session store (returns sample_call_session by default)
    - Tool configuration
    - Active call context
    """
    context = ToolExecutionContext(
        ari_client=mock_ari_client,
        session_store=mock_session_store,
        config=tool_config,
        call_id="test_call_123",
        caller_channel_id="PJSIP/caller-00000001"
    )
    
    # Configure session store to return our sample session
    mock_session_store.get_by_call_id.return_value = sample_call_session
    
    return context


@pytest.fixture
def mock_resend_client():
    """
    Mock Resend email client for email tool testing.
    
    Pre-configured to return successful send response.
    """
    client = Mock()
    client.emails = Mock()
    client.emails.send = Mock(return_value={
        "id": "email_test_123",
        "from": "ai-agent@example.com",
        "to": "test@example.com",
        "subject": "Test Email",
        "created_at": datetime.now().isoformat()
    })
    return client


@pytest.fixture
def mock_dns_resolver():
    """
    Mock DNS resolver for MX record validation.
    
    Pre-configured to return valid MX records for common domains.
    """
    resolver = Mock()
    
    # Mock MX records for common domains
    def resolve_mx(domain, record_type):
        if record_type != 'MX':
            raise ValueError("Only MX records supported in mock")
        
        # Valid domains
        if domain in ['gmail.com', 'example.com', 'company.com']:
            mx_record = Mock()
            mx_record.priority = 10
            mx_record.exchange = f"mx.{domain}"
            return [mx_record]
        
        # Invalid domains
        raise Exception(f"No MX records found for {domain}")
    
    resolver.resolve = resolve_mx
    return resolver
