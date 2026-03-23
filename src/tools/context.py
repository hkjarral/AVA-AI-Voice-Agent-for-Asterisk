"""
Tool execution context - provides access to system resources during tool execution.

Includes:
- ToolExecutionContext: For in-call tool execution (existing)
- PreCallContext: For pre-call tools (CRM lookup, enrichment)
- PostCallContext: For post-call tools (webhooks, CRM updates)
"""

from dataclasses import dataclass, field
from typing import Optional, Any, Dict, List
import logging

logger = logging.getLogger(__name__)


@dataclass
class ToolExecutionContext:
    """
    Context provided to tools during execution.
    
    Contains all information and system access needed for a tool to execute,
    including call metadata, session state, and system clients (ARI, etc.).
    """
    
    # Call information
    call_id: str
    caller_channel_id: Optional[str] = None
    bridge_id: Optional[str] = None
    caller_number: Optional[str] = None   # CALLERID(num) - caller's phone number
    called_number: Optional[str] = None   # DIALED_NUMBER or __FROM_DID - the number that was dialed
    caller_name: Optional[str] = None     # CALLERID(name) for personalization
    context_name: Optional[str] = None    # AI_CONTEXT from dialplan
    
    # System access (injected by provider)
    session_store: Any = None  # SessionStore instance
    ari_client: Any = None      # ARIClient instance
    config: Any = None           # Config dict
    
    # Provider information
    provider_name: str = None  # "deepgram", "openai_realtime", "custom_pipeline"
    provider_session: Any = None
    
    # Request metadata
    user_input: Optional[str] = None  # Original user utterance
    detected_intent: Optional[str] = None
    confidence: Optional[float] = None

    @staticmethod
    def is_pending_attended_transfer(session: Any) -> bool:
        """Return True when an attended transfer is awaiting a callee decision."""
        current_action = getattr(session, "current_action", None) or {}
        if not isinstance(current_action, dict):
            return False

        if current_action.get("type") != "attended_transfer":
            return False

        decision = str(current_action.get("decision") or "").strip().lower()
        return decision not in {"accepted", "declined"}
    
    async def get_session(self):
        """
        Get current call session from session store.
        
        Returns:
            Session object with call state
        
        Raises:
            RuntimeError: If session not found
        """
        if not self.session_store:
            raise RuntimeError("SessionStore not available in context")
        
        session = await self.session_store.get_by_call_id(self.call_id)
        if not session:
            raise RuntimeError(f"Session not found for call_id: {self.call_id}")
        
        return session
    
    async def update_session(self, **kwargs):
        """
        Update call session with new attributes.
        
        Args:
            **kwargs: Attributes to update on session
        
        Example:
            await context.update_session(
                transfer_active=True,
                transfer_target="2765"
            )
        """
        if not self.session_store:
            raise RuntimeError("SessionStore not available in context")
        
        session = await self.get_session()
        
        for key, value in kwargs.items():
            setattr(session, key, value)
        
        await self.session_store.upsert_call(session)
        logger.debug(f"Updated session {self.call_id}: {kwargs}")

    async def get_tool_block_response(self, tool_name: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Return a standardized error result when a pending attended transfer should
        block tool execution for the active call.
        """
        if tool_name == "cancel_transfer":
            return None

        try:
            session = await self.get_session()
        except Exception:
            logger.debug(
                "Unable to load session while checking pending attended transfer guard for call_id=%s tool=%s",
                self.call_id,
                tool_name,
                exc_info=True,
            )
            return None

        if not self.is_pending_attended_transfer(session):
            return None

        logger.warning(
            "Blocking tool call during pending attended transfer for call_id=%s tool=%s provider=%s",
            self.call_id,
            tool_name,
            self.provider_name,
        )
        return {
            "status": "error",
            "message": "Tool calls are blocked while an attended transfer is pending. Wait for the transfer to complete or use cancel_transfer.",
        }
    
    def get_config_value(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value.
        
        Args:
            key: Config key (supports dot notation, e.g., "tools.transfer.destinations.support_agent.target")
            default: Default value if key not found
        
        Returns:
            Config value or default
        """
        if not self.config:
            return default
        
        # Support dot notation for nested keys
        keys = key.split('.')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value


@dataclass
class PreCallContext:
    """
    Context provided to pre-call tools.
    
    Pre-call tools run after the call is answered but before the AI speaks.
    They fetch enrichment data (e.g., CRM lookup) to inject into prompts.
    """
    
    # Call identifiers
    call_id: str
    caller_number: str  # ANI (caller's phone number)
    called_number: Optional[str] = None  # DID that was called
    caller_name: Optional[str] = None  # Caller ID name if available
    
    # Context information
    context_name: str = ""  # AI_CONTEXT from dialplan
    call_direction: str = "inbound"  # "inbound" or "outbound"
    
    # Outbound-specific (from campaign dialer)
    campaign_id: Optional[str] = None
    lead_id: Optional[str] = None
    
    # Channel variables from Asterisk
    channel_vars: Dict[str, str] = field(default_factory=dict)
    
    # System access
    config: Any = None  # Config dict
    ari_client: Any = None  # ARIClient instance (for hold audio playback)
    
    def get_config_value(self, key: str, default: Any = None) -> Any:
        """Get configuration value with dot notation support."""
        if not self.config:
            return default
        
        keys = key.split('.')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value


@dataclass
class PostCallContext:
    """
    Context provided to post-call tools.
    
    Post-call tools run after the call ends (fire-and-forget).
    They receive comprehensive session data for webhooks, CRM updates, etc.
    """
    
    # Call identifiers
    call_id: str
    caller_number: str  # ANI
    called_number: Optional[str] = None  # DID
    caller_name: Optional[str] = None
    
    # Context and provider
    context_name: str = ""
    provider: str = ""  # Provider used for the call
    call_direction: str = "inbound"
    
    # Call metrics
    call_duration_seconds: int = 0
    call_outcome: str = ""  # e.g., "answered_human", "voicemail", "no_answer"
    call_start_time: Optional[str] = None  # ISO timestamp
    call_end_time: Optional[str] = None  # ISO timestamp
    
    # Conversation data
    conversation_history: List[Dict[str, str]] = field(default_factory=list)  # Transcript
    summary: Optional[str] = None  # AI-generated summary if available
    
    # Tool execution data
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)  # In-call tool executions
    pre_call_results: Dict[str, str] = field(default_factory=dict)  # Data from pre-call tools
    
    # Outbound-specific
    campaign_id: Optional[str] = None
    lead_id: Optional[str] = None
    
    # System access
    config: Any = None
    
    def to_payload_dict(self) -> Dict[str, Any]:
        """
        Convert context to a dictionary suitable for webhook payloads.
        
        Returns:
            Dictionary with all call data for templating.
        """
        import json
        return {
            "call_id": self.call_id,
            "caller_number": self.caller_number,
            "called_number": self.called_number or "",
            "caller_name": self.caller_name or "",
            "context_name": self.context_name,
            "provider": self.provider,
            "call_direction": self.call_direction,
            "call_duration": self.call_duration_seconds,
            "call_outcome": self.call_outcome,
            "call_start_time": self.call_start_time or "",
            "call_end_time": self.call_end_time or "",
            "transcript_json": json.dumps(self.conversation_history),
            "summary": self.summary or "",
            "tool_calls_json": json.dumps(self.tool_calls),
            "pre_call_results_json": json.dumps(self.pre_call_results),
            "campaign_id": self.campaign_id or "",
            "lead_id": self.lead_id or "",
        }
