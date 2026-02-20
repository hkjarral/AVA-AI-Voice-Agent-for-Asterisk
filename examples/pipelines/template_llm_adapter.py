"""
Template: LLM Pipeline Adapter
===============================
Generates AI responses from transcribed text.

Usage:
    Open this file in Windsurf and tell AVA:
    "Help me build an LLM adapter like this for [provider name]"

Guide: docs/contributing/adding-pipeline-adapter.md
Reference: src/pipelines/openai_chat.py (OpenAI Chat)
"""

import structlog
from typing import Dict, Any, Optional, Union

from src.pipelines.base import LLMComponent, LLMResponse

logger = structlog.get_logger(__name__)


class TemplateLLM(LLMComponent):
    """LLM adapter for TemplateProvider.

    TODO: Replace 'Template' with your provider name.
    """

    def __init__(self, call_id: str, options: Dict[str, Any]):
        self.call_id = call_id
        self.options = options
        self._api_key = options.get("api_key") or self._auto_detect_credentials()

    async def generate(
        self,
        call_id: str,
        transcript: str,
        context: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Union[str, LLMResponse]:
        """Generate an AI response.

        Args:
            call_id: Unique call identifier.
            transcript: User's transcribed speech.
            context: Conversation context with:
                - system_prompt: str
                - conversation_history: List[Dict]
                - tools: List[Dict] (tool schemas)
            options: Additional provider-specific options.

        Returns:
            Plain text response, or LLMResponse with tool_calls.

        TODO:
        - Build messages array from context + transcript
        - Call your LLM API
        - Parse response (text and/or tool calls)
        - Return text or LLMResponse
        """
        # For simple text response:
        # return "Hello! How can I help you?"

        # For response with tool calls:
        # return LLMResponse(
        #     text="Let me check that for you.",
        #     tool_calls=[{
        #         "name": "check_appointment",
        #         "arguments": {"date": "2026-03-15"},
        #     }],
        # )
        return ""

    async def start(self):
        """Initialize resources."""
        logger.info("llm_adapter_started", provider="template", call_id=self.call_id)

    async def stop(self):
        """Clean up resources."""
        logger.info("llm_adapter_stopped", provider="template", call_id=self.call_id)
