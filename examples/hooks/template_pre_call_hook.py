"""
Template: Pre-Call Hook
========================
Runs after call answer, before AI speaks. Enriches call context
with external data (CRM lookup, account check, etc.).

Usage:
    Open this file in Windsurf and tell AVA:
    "Help me build a pre-call hook like this for my CRM"

Guide: docs/contributing/pre-call-hooks-development.md
Reference: src/tools/http/generic_lookup.py
"""

import structlog
from typing import Dict

from src.tools.base import PreCallTool, ToolDefinition, ToolCategory, ToolPhase
from src.tools.context import PreCallContext

logger = structlog.get_logger(__name__)


class TemplatePreCallHook(PreCallTool):
    """Template pre-call enrichment hook.

    TODO: Replace 'Template' with your hook name.
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="template_pre_call",
            description="Look up caller information before AI greeting",
            category=ToolCategory.BUSINESS,
            phase=ToolPhase.PRE_CALL,
        )

    async def execute(self, context: PreCallContext) -> Dict[str, str]:
        """Execute the pre-call lookup.

        Available context fields:
            context.caller_number   - Caller's phone number
            context.called_number   - Number that was called
            context.caller_name     - Caller ID name
            context.context_name    - Asterisk context
            context.call_direction  - "inbound" or "outbound"
            context.campaign_id     - Campaign ID (outbound)
            context.lead_id         - Lead ID (outbound)
            context.config          - Tool config from YAML

        Returns:
            Dict[str, str] mapping variable names to values.
            These are injected into the AI's system prompt.
            Return {} on any error (never throw).
        """
        try:
            caller = context.caller_number

            # TODO: Your lookup logic here
            # Example: Query CRM, check database, call internal API

            return {
                "customer_name": "John Doe",
                "account_status": "active",
            }
        except Exception as e:
            logger.warning("pre_call_lookup_failed", error=str(e))
            return {}  # Empty = no enrichment, call continues normally


# --- YAML-Only Alternative (no code needed) ---
# Add this to config/ai-agent.yaml instead of writing code:
#
# pre_call_tools:
#   crm_lookup:
#     enabled: true
#     phase: pre_call
#     timeout_ms: 3000
#     url: "https://api.yourcrm.com/contacts?phone={caller_number}"
#     method: GET
#     headers:
#       Authorization: "Bearer ${CRM_API_KEY}"
#     output_variables:
#       customer_name: "contact.firstName"
#       account_status: "contact.status"
