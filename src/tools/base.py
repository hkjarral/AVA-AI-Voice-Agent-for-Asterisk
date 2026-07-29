"""
Base classes for unified tool calling system.

This module defines the core abstractions that all tools must implement,
regardless of which AI provider they're used with.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ToolPhase(Enum):
    """Phase of tool execution in the call lifecycle."""
    PRE_CALL = "pre_call"    # Runs after answer, before AI speaks (CRM lookup, enrichment)
    IN_CALL = "in_call"      # Runs during AI conversation (existing tools)
    POST_CALL = "post_call"  # Runs after call ends (webhooks, CRM updates)


class ToolCategory(Enum):
    """Category of tool for execution routing."""
    TELEPHONY = "telephony"  # Executes via ARI (transfers, voicemail, etc.)
    BUSINESS = "business"     # Executes via provider-native or external APIs
    HYBRID = "hybrid"         # May use both telephony and business logic


@dataclass
class ToolParameter:
    """Definition of a tool parameter."""
    name: str
    type: str  # "string", "integer", "boolean", "number", "array", "object"
    description: str
    required: bool = False
    enum: Optional[List[str]] = None
    default: Optional[Any] = None
    
    def to_dict(self, include_default: bool = True) -> Dict[str, Any]:
        """
        Convert to dictionary for schema generation.
        
        Args:
            include_default: Whether to include the default field (some providers don't support it)
        """
        result = {
            "type": self.type,
            "description": self.description
        }
        if self.enum:
            result["enum"] = self.enum
        # Only include default if requested (Deepgram doesn't support it)
        if include_default and self.default is not None:
            result["default"] = self.default
        return result


@dataclass
class ToolDefinition:
    """
    Provider-agnostic tool definition.
    
    Contains all metadata needed to expose a tool to any AI provider.
    """
    name: str
    description: str
    category: ToolCategory
    parameters: List[ToolParameter] = field(default_factory=list)
    # Optional raw JSON schema (e.g., MCP tool inputSchema). If present, it is used
    # for provider schema generation instead of `parameters`.
    input_schema: Optional[Dict[str, Any]] = None
    requires_channel: bool = False  # Needs active call channel
    max_execution_time: int = 30    # Timeout in seconds
    
    # Phase system fields (Milestone 24)
    phase: ToolPhase = ToolPhase.IN_CALL  # Default to in-call for backward compatibility
    is_global: bool = False  # If True, available in all contexts by default
    output_variables: List[str] = field(default_factory=list)  # Pre-call: variables to inject into prompt
    timeout_ms: Optional[int] = None  # Per-tool timeout in milliseconds (phase tools)
    
    # Pre-call hold audio (played via ARI if tool exceeds threshold)
    hold_audio_file: Optional[str] = None  # Asterisk sound filename (e.g., "custom/please-wait")
    hold_audio_threshold_ms: int = 500  # Play audio if tool takes longer than this (ms)

    def _strip_defaults(self, schema: Any) -> Any:
        """Deepgram does not support 'default' fields in parameter schema."""
        if isinstance(schema, dict):
            return {k: self._strip_defaults(v) for k, v in schema.items() if k != "default"}
        if isinstance(schema, list):
            return [self._strip_defaults(v) for v in schema]
        return schema

    def _json_schema_object(self) -> Dict[str, Any]:
        if isinstance(self.input_schema, dict) and self.input_schema:
            # Ensure schema is object-shaped for function calling.
            if self.input_schema.get("type") is None:
                return {"type": "object", **self.input_schema}
            return dict(self.input_schema)
        return {
            "type": "object",
            "properties": {
                p.name: p.to_dict()
                for p in self.parameters
            },
            "required": [p.name for p in self.parameters if p.required],
        }
    
    def to_deepgram_schema(self) -> Dict[str, Any]:
        """
        Convert to Deepgram Voice Agent function calling format.
        
        Deepgram format:
        {
            "name": "tool_name",
            "description": "Tool description",
            "parameters": {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        }
        
        Note: Deepgram doesn't support 'default' field in parameters,
        so we exclude it with include_default=False.
        """
        if isinstance(self.input_schema, dict) and self.input_schema:
            return {
                "name": self.name,
                "description": self.description,
                "parameters": self._strip_defaults(self._json_schema_object()),
            }
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    p.name: p.to_dict(include_default=False)
                    for p in self.parameters
                },
                "required": [p.name for p in self.parameters if p.required]
            }
        }
    
    def to_openai_schema(self) -> Dict[str, Any]:
        """
        Convert to OpenAI API function calling format (Chat Completions).
        
        OpenAI format:
        {
            "type": "function",
            "function": {
                "name": "tool_name",
                "description": "Tool description",
                "parameters": {
                    "type": "object",
                    "properties": {...},
                    "required": [...]
                }
            }
        }
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._json_schema_object(),
            }
        }
    
    def to_openai_realtime_schema(self) -> Dict[str, Any]:
        """
        Convert to OpenAI Realtime API function calling format.
        
        OpenAI Realtime format (flatter structure, different from Chat Completions):
        {
            "type": "function",
            "name": "tool_name",
            "description": "Tool description",
            "parameters": {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        }
        
        Note: Realtime API has name/description at top level, not nested under "function"
        """
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self._json_schema_object(),
        }
    
    def to_elevenlabs_schema(self) -> Dict[str, Any]:
        """
        Convert to ElevenLabs Conversational AI tool format.
        
        ElevenLabs format:
        {
            "type": "client",  # client-side tool execution
            "name": "tool_name",
            "description": "Tool description",
            "parameters": {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        }
        """
        return {
            "type": "client",
            "name": self.name,
            "description": self.description,
            "parameters": self._json_schema_object(),
        }
    
    def to_prompt_text(self) -> str:
        """
        Convert to text format for custom pipeline system prompts.
        
        Used when provider doesn't have native function calling (custom pipelines).
        LLM learns to output structured text that we can parse.
        """
        params_desc = []
        for p in self.parameters:
            req_str = "required" if p.required else "optional"
            param_str = f"{p.name} ({p.type}, {req_str}): {p.description}"
            if p.enum:
                param_str += f" [options: {', '.join(p.enum)}]"
            params_desc.append(param_str)
        
        params_text = "\n  ".join(params_desc) if params_desc else "  (no parameters)"
        
        return f"{self.name}: {self.description}\n  Parameters:\n  {params_text}"
    
    def to_local_llm_schema(self) -> Dict[str, Any]:
        """
        Convert to JSON schema format for local LLM prompts.
        
        Returns a dictionary that can be serialized to JSON and embedded
        in system prompts for local LLMs (Phi-3, Llama, etc.) that don't
        have native function calling but can output structured JSON.
        """
        import json
        if isinstance(self.input_schema, dict) and self.input_schema:
            schema_obj = self._json_schema_object()
            params = schema_obj.get("properties") if isinstance(schema_obj.get("properties"), dict) else {}
            required = schema_obj.get("required") if isinstance(schema_obj.get("required"), list) else []
            # Keep existing local prompt format, but preserve as much as possible.
            return {
                "name": self.name,
                "description": self.description,
                "parameters": params,
                "required": required,
            }
        params = {}
        required = []
        for p in self.parameters:
            param_def = {"type": p.type, "description": p.description}
            if p.enum:
                param_def["enum"] = p.enum
            params[p.name] = param_def
            if p.required:
                required.append(p.name)
        
        return {
            "name": self.name,
            "description": self.description,
            "parameters": params,
            "required": required
        }


class Tool(ABC):
    """
    Abstract base class for all tools.
    
    All tools must inherit from this class and implement:
    - definition property: Returns ToolDefinition with metadata
    - execute method: Performs the actual tool action
    """
    
    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Return tool definition with metadata."""
        pass
    
    @abstractmethod
    async def execute(
        self,
        parameters: Dict[str, Any],
        context: 'ToolExecutionContext'
    ) -> Dict[str, Any]:
        """
        Execute the tool with given parameters and context.
        
        Args:
            parameters: Tool parameters from AI provider
            context: Execution context with call info and system access
        
        Returns:
            Result dictionary with:
            - status: "success" | "failed" | "error"
            - message: Human-readable message for AI to speak
            - Additional tool-specific fields
        
        Raises:
            ValueError: If parameters are invalid
            RuntimeError: If execution fails
        """
        pass
    
    async def validate_parameters(self, parameters: Dict[str, Any]) -> bool:
        """
        Validate parameters before execution.
        
        Args:
            parameters: Parameters to validate
        
        Returns:
            True if valid
        
        Raises:
            ValueError: If validation fails with specific error message
        """
        # Check required parameters
        for param in self.definition.parameters:
            if param.required and param.name not in parameters:
                raise ValueError(f"Missing required parameter: {param.name}")
            
            # Check enum values
            if param.enum and param.name in parameters:
                if parameters[param.name] not in param.enum:
                    raise ValueError(
                        f"Invalid value for {param.name}. "
                        f"Must be one of: {', '.join(param.enum)}"
                    )
        
        return True
    
    def _load_config(self) -> Dict[str, Any]:
        """
        Load tool-specific configuration from ai-agent.yaml.
        
        Returns empty dict if no config found.
        """
        from src.config import load_config
        
        try:
            config = load_config()
            tools_config = config.get('tools', {})
            return tools_config.get(self.definition.name, {})
        except Exception as e:
            logger.warning(f"Failed to load config for {self.definition.name}: {e}")
            return {}


class PreCallTool(ABC):
    """
    Abstract base class for pre-call tools.
    
    Pre-call tools run after the call is answered but before the AI speaks.
    They fetch enrichment data (e.g., CRM lookup) and return output variables
    that are injected into the system prompt.
    
    All pre-call tools must inherit from this class and implement:
    - definition property: Returns ToolDefinition with phase=PRE_CALL
    - execute method: Fetches data and returns output variables
    """
    
    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """
        Return tool definition with metadata.
        
        Must have phase=ToolPhase.PRE_CALL and define output_variables.
        """
        pass
    
    @abstractmethod
    async def execute(
        self,
        context: 'PreCallContext'
    ) -> Dict[str, str]:
        """
        Execute the pre-call tool to fetch enrichment data.

        Args:
            context: PreCallContext with caller info and system access

        Returns:
            Dictionary mapping output_variable names to string values.
            Missing/null values should be returned as empty string "".

        Example:
            return {
                "customer_name": "John Smith",
                "customer_email": "john@example.com",
                "last_call_notes": "",  # Not found
            }

        Raises:
            Exception: On failure (will be caught; empty values used)
        """
        pass

    def get_last_result(self, call_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Return execution metadata from the last ``execute()`` call, or None.

        Optional hook used by call-history tracking. Concrete tools may override to
        surface diagnostics like HTTP status, response body preview, or upstream
        error message. Default implementation returns None — the engine still
        records ``name``/``status``/``duration_ms`` from its own measurements.

        ``call_id`` is provided so concrete tools can isolate per-execution state
        (tools are registered as singletons; concurrent executions across calls
        must not share mutable diagnostics fields).
        """
        return None


class PostCallTool(ABC):
    """
    Abstract base class for post-call tools.
    
    Post-call tools run after the call ends (fire-and-forget).
    They send data to external systems (webhooks, CRM updates).
    
    All post-call tools must inherit from this class and implement:
    - definition property: Returns ToolDefinition with phase=POST_CALL
    - execute method: Sends data to external system (fire-and-forget)
    """
    
    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """
        Return tool definition with metadata.
        
        Must have phase=ToolPhase.POST_CALL.
        """
        pass
    
    @abstractmethod
    async def execute(
        self,
        context: 'PostCallContext'
    ) -> None:
        """
        Execute the post-call tool (fire-and-forget).

        Args:
            context: PostCallContext with comprehensive session data

        Returns:
            None (fire-and-forget; return value ignored)

        Note:
            - Exceptions are logged but do not affect call cleanup
            - No retry mechanism (receiving systems handle retries)
            - Should complete quickly; long operations should be async
        """
        pass

    def get_last_result(self, call_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Return execution metadata from the last ``execute()`` call, or None.

        Optional hook used by call-history tracking. Concrete tools may override
        to surface diagnostics like HTTP status, response body preview, or
        upstream error message. Default implementation returns None — the engine
        still records ``name``/``status``/``duration_ms`` from its own
        measurements.

        ``call_id`` is provided so concrete tools can isolate per-execution
        state (tools are registered as singletons; concurrent post-call
        executions across calls must not share mutable diagnostics fields).
        """
        return None
