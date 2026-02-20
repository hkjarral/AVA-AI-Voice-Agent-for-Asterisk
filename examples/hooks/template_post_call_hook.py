"""
Template: Post-Call Hook
=========================
Runs after a call ends. Fire-and-forget webhook to external systems.
Examples: Slack notifications, CRM updates, Discord alerts, n8n triggers.

Usage:
    Open this file in Windsurf and tell AVA:
    "Help me build a post-call webhook for Slack"

Guide: docs/contributing/post-call-hooks-development.md
Reference: src/tools/http/generic_webhook.py
"""

import structlog
from typing import Optional

from src.tools.base import PostCallTool, ToolDefinition, ToolCategory, ToolPhase
from src.tools.context import PostCallContext

logger = structlog.get_logger(__name__)


class TemplatePostCallHook(PostCallTool):
    """Template post-call webhook.

    TODO: Replace 'Template' with your hook name.
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="template_webhook",
            description="Send call data to external system after call ends",
            category=ToolCategory.BUSINESS,
            phase=ToolPhase.POST_CALL,
            is_global=True,  # Set True to run for every call
        )

    async def execute(self, context: PostCallContext) -> None:
        """Execute the post-call webhook.

        Available context fields:
            context.call_id                 - Unique call identifier
            context.caller_number           - Caller's phone number
            context.call_duration_seconds   - Call length in seconds
            context.call_outcome            - How the call ended
            context.call_start_time         - ISO timestamp
            context.call_end_time           - ISO timestamp
            context.conversation_history    - Full conversation
            context.summary                 - AI-generated summary
            context.tool_calls              - Tools invoked during call
            context.pre_call_results        - Pre-call hook results
            context.campaign_id             - Campaign ID (outbound)
            context.lead_id                 - Lead ID (outbound)

        Payload dict:
            context.to_payload_dict()  - All fields as flat dict

        Returns:
            None — post-call hooks are fire-and-forget.
            NEVER throw exceptions.
        """
        try:
            payload = {
                "call_id": context.call_id,
                "caller": context.caller_number,
                "duration": context.call_duration_seconds,
                "outcome": context.call_outcome,
                "summary": context.summary,
            }

            # TODO: Send to your external system
            # Example: aiohttp POST to webhook URL

            logger.info("webhook_sent", call_id=context.call_id)

        except Exception as e:
            # Fire-and-forget: log but NEVER re-raise
            logger.warning("webhook_failed",
                           call_id=context.call_id, error=str(e))


# --- YAML-Only Alternative (no code needed) ---
# Add this to config/ai-agent.yaml instead of writing code:
#
# post_call_tools:
#   slack_notification:
#     enabled: true
#     phase: post_call
#     is_global: true
#     url: "${SLACK_WEBHOOK_URL}"
#     method: POST
#     payload_template: |
#       {
#         "text": "Call from {caller_number} ({call_duration_seconds}s): {summary}"
#       }
#     generate_summary: true
#     summary_max_words: 100
