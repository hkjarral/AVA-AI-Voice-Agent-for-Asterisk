"""
Template: In-Call HTTP Hook
============================
AI-invoked tool that queries external data during conversation.
Examples: check appointments, look up orders, query inventory.

Usage:
    Open this file in Windsurf and tell AVA:
    "Help me build an in-call hook like this for appointment checking"

Guide: docs/contributing/in-call-hooks-development.md
Reference: src/tools/http/in_call_lookup.py
"""

import structlog
from typing import Dict, Any, List

from src.tools.base import Tool, ToolDefinition, ToolParameter, ToolCategory
from src.tools.context import ToolExecutionContext

logger = structlog.get_logger(__name__)


class TemplateInCallHook(Tool):
    """Template in-call HTTP lookup tool.

    TODO: Replace 'Template' with your tool name.
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="template_lookup",
            description="Look up information during a call",
            category=ToolCategory.BUSINESS,
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="What to look up",
                    required=True,
                ),
                # TODO: Add your parameters
            ],
            max_execution_time=10,
        )

    async def execute(
        self, parameters: Dict[str, Any], context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """Execute the in-call lookup.

        Available context fields:
            context.call_id            - Unique call identifier
            context.caller_number      - Caller's phone number
            context.called_number      - Number that was called
            context.user_input         - User's last utterance
            context.session_store      - Session state store
            context.ari_client         - ARI client
            context.config             - Full config from YAML

        Config access:
            context.get_config_value("tools.my_tool.api_url", "default")

        Pre-call results:
            session = await context.get_session()
            pre_call = session.get("pre_call_results", {})

        Returns:
            Dict with {status, message, ...} — AI uses message in response.
        """
        query = parameters.get("query", "")

        try:
            # TODO: Your lookup logic here
            # Example: Call API, query database

            return {
                "status": "success",
                "message": f"Found result for: {query}",
                "data": {"result": "example"},
            }
        except Exception as e:
            logger.error("in_call_lookup_failed", error=str(e))
            return {
                "status": "error",
                "message": "Could not complete the lookup at this time.",
            }


# --- YAML-Only Alternative (no code needed) ---
# Add this to config/ai-agent.yaml instead of writing code:
#
# in_call_tools:
#   appointment_check:
#     enabled: true
#     phase: in_call
#     timeout_ms: 5000
#     url: "https://api.scheduling.com/check"
#     method: POST
#     headers:
#       Authorization: "Bearer ${SCHEDULING_API_KEY}"
#     body_template: '{"date": "{date}", "service": "{service}"}'
#     parameters:
#       - name: date
#         type: string
#         description: "Date to check (YYYY-MM-DD)"
#         required: true
#       - name: service
#         type: string
#         description: "Service type"
#         required: true
#     output_variables:
#       available: "available"
#       next_slot: "nextSlot"
