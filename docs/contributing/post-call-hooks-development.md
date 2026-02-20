# Post-Call Hooks Development

Post-call hooks run **after a call ends** to send data to external systems. Examples: Slack notifications, CRM updates, n8n/Zapier webhooks, Discord alerts.

> **Using AVA?** Open this file in Windsurf and say: "Help me build a post-call webhook that sends call summaries to Slack."

## Architecture

```
Call ends → Post-Call Hook (fire-and-forget HTTP) → External system (Slack, CRM, etc.)
```

Post-call hooks extend `PostCallTool` from `src/tools/base.py`. The built-in `GenericWebhookTool` in `src/tools/http/generic_webhook.py` handles most cases via YAML config.

## Option A: YAML-Only (No Code Needed)

For standard webhooks, just add config to `config/ai-agent.yaml`:

```yaml
post_call_tools:
  slack_notification:
    enabled: true
    phase: post_call
    is_global: true
    timeout_ms: 5000
    url: "${SLACK_WEBHOOK_URL}"
    method: POST
    headers:
      Content-Type: "application/json"
    payload_template: |
      {
        "text": "Call ended: {caller_number} → {called_number}",
        "blocks": [
          {
            "type": "section",
            "text": {
              "type": "mrkdwn",
              "text": "*Call Summary*\n• Caller: {caller_number}\n• Duration: {call_duration_seconds}s\n• Outcome: {call_outcome}\n• Summary: {summary}"
            }
          }
        ]
      }
    generate_summary: true
    summary_max_words: 100
```

**Key options:**
- `is_global: true` — runs for every call (not just specific contexts)
- `generate_summary: true` — uses LLM to generate a call summary
- `payload_template` — custom JSON with variable substitution

## Option B: Custom Code

For complex logic:

```python
import structlog
from typing import Optional
from src.tools.base import PostCallTool, ToolDefinition, ToolCategory, ToolPhase
from src.tools.context import PostCallContext

logger = structlog.get_logger(__name__)

class CRMUpdateHook(PostCallTool):
    """Update CRM with call results after each call."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="crm_update",
            description="Update CRM with call outcome and summary",
            category=ToolCategory.BUSINESS,
            phase=ToolPhase.POST_CALL,
            is_global=True,
        )

    async def execute(self, context: PostCallContext) -> None:
        """Execute the post-call webhook.

        Args:
            context: Post-call context with call data, conversation
                     history, tool calls, and summary.

        Returns:
            None — post-call hooks are fire-and-forget.
        """
        try:
            payload = {
                "call_id": context.call_id,
                "caller": context.caller_number,
                "duration": context.call_duration_seconds,
                "outcome": context.call_outcome,
                "summary": context.summary,
                "tool_calls": context.tool_calls,
            }

            # TODO: Send to your CRM API
            logger.info("crm_updated", call_id=context.call_id)

        except Exception as e:
            # Fire-and-forget: log but don't fail
            logger.warning("crm_update_failed",
                           call_id=context.call_id, error=str(e))
```

Register in `src/tools/registry.py` under `initialize_default_tools()`.

## PostCallContext Fields

| Field | Type | Description |
|-------|------|-------------|
| `call_id` | `str` | Unique call identifier |
| `caller_number` | `str` | Caller's phone number |
| `call_duration_seconds` | `float` | Call length in seconds |
| `call_outcome` | `str` | How the call ended |
| `call_start_time` | `str` | ISO timestamp of call start |
| `call_end_time` | `str` | ISO timestamp of call end |
| `conversation_history` | `List[Dict]` | Full conversation (role + content) |
| `summary` | `str` | AI-generated summary (if enabled) |
| `tool_calls` | `List[Dict]` | Tools invoked during the call |
| `pre_call_results` | `Dict` | Results from pre-call hooks |
| `campaign_id` | `str` | Campaign ID (outbound calls) |
| `lead_id` | `str` | Lead ID (outbound calls) |

**Getting a payload dict:**
```python
data = context.to_payload_dict()  # All fields as a flat dict for templating
```

## Error Handling

Post-call hooks must **never throw exceptions**. They are fire-and-forget:

```python
async def execute(self, context: PostCallContext) -> None:
    try:
        # Your webhook logic
        pass
    except Exception as e:
        logger.warning("webhook_failed", error=str(e))
        # Do NOT re-raise — fire-and-forget
```

## Available Variables for Templates

In YAML `payload_template`, use these variables:

| Variable | Example Value |
|----------|--------------|
| `{call_id}` | `abc-123-def` |
| `{caller_number}` | `+15551234567` |
| `{called_number}` | `+15559876543` |
| `{call_duration_seconds}` | `142` |
| `{call_outcome}` | `completed` |
| `{call_start_time}` | `2026-02-12T10:30:00Z` |
| `{call_end_time}` | `2026-02-12T10:32:22Z` |
| `{summary}` | `Caller requested appointment...` |

For JSON values (not quoted), use `_json` suffix: `{tool_calls_json}`.

## Common Integrations

### Discord Webhook

```yaml
post_call_tools:
  discord_alert:
    enabled: true
    phase: post_call
    is_global: true
    url: "${DISCORD_WEBHOOK_URL}"
    method: POST
    payload_template: |
      {"content": "📞 Call from {caller_number} ({call_duration_seconds}s): {summary}"}
    generate_summary: true
```

### n8n / Zapier Webhook

```yaml
post_call_tools:
  n8n_trigger:
    enabled: true
    phase: post_call
    is_global: true
    url: "${N8N_WEBHOOK_URL}"
    method: POST
    payload_template: |
      {
        "caller": "{caller_number}",
        "duration": {call_duration_seconds},
        "outcome": "{call_outcome}",
        "conversation": {conversation_history_json}
      }
```

## Testing

```python
import pytest
from unittest.mock import MagicMock

@pytest.mark.asyncio
async def test_post_call_hook():
    hook = CRMUpdateHook()
    context = MagicMock(spec=PostCallContext)
    context.call_id = "test-123"
    context.caller_number = "+15551234567"
    context.call_duration_seconds = 60
    context.call_outcome = "completed"
    context.summary = "Test call"
    context.tool_calls = []
    # Should not throw
    await hook.execute(context)
```

## Reference

- Canonical implementation: `src/tools/http/generic_webhook.py` (`GenericWebhookTool`)
- Context definition: `src/tools/context.py` (`PostCallContext`)
- Tool base: `src/tools/base.py` (`PostCallTool`)
