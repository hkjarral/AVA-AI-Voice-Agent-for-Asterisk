# Pre-Call Hooks Development

Pre-call hooks run **after a call is answered but before the AI speaks**. They enrich the call context with external data (CRM lookups, account checks, caller history) so the AI can personalize its greeting.

> **Using AVA?** Open this file in Windsurf and say: "Help me build a pre-call hook that looks up the caller in my CRM."

## Architecture

```
Call Answered → Pre-Call Hook (HTTP lookup) → Inject variables → AI greeting uses enriched context
```

Pre-call hooks extend `PreCallTool` from `src/tools/base.py`. The built-in `GenericHTTPLookupTool` in `src/tools/http/generic_lookup.py` handles most cases via YAML config alone.

## Option A: YAML-Only (No Code Needed)

For simple HTTP lookups, just add config to `config/ai-agent.yaml`:

```yaml
pre_call_tools:
  crm_lookup:
    enabled: true
    phase: pre_call
    timeout_ms: 3000
    url: "https://api.yourcrm.com/contacts?phone={caller_number}"
    method: GET
    headers:
      Authorization: "Bearer ${CRM_API_KEY}"
    output_variables:
      customer_name: "contact.firstName"
      account_tier: "contact.accountTier"
      last_interaction: "contact.lastCall"
```

The output variables are injected into the AI's system prompt, so the AI knows the caller's name and account details before speaking.

**Variable substitution:**
- `{caller_number}`, `{called_number}`, `{call_id}` — call context
- `${CRM_API_KEY}` — environment variables from `.env`

**Path extraction:**
- `contact.firstName` — dot notation into JSON response
- `contacts[0].email` — array index access

## Option B: Custom Code

For complex logic beyond simple HTTP lookups:

```python
import structlog
from typing import Dict
from src.tools.base import PreCallTool, ToolDefinition, ToolCategory, ToolPhase
from src.tools.context import PreCallContext

logger = structlog.get_logger(__name__)

class MyPreCallHook(PreCallTool):
    """Custom pre-call enrichment hook."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="my_pre_call_hook",
            description="Look up caller in custom system",
            category=ToolCategory.BUSINESS,
            phase=ToolPhase.PRE_CALL,
        )

    async def execute(self, context: PreCallContext) -> Dict[str, str]:
        """Execute the pre-call lookup.

        Args:
            context: Pre-call context with caller_number, called_number,
                     caller_name, context_name, campaign_id, lead_id.

        Returns:
            Dict mapping variable names to values. These are injected
            into the AI's system prompt.
        """
        caller = context.caller_number

        # TODO: Your custom lookup logic here
        # Example: query database, call internal API, check cache

        return {
            "customer_name": "John Doe",
            "account_status": "active",
            "vip": "true",
        }
```

Register in `src/tools/registry.py` under `initialize_default_tools()`.

## PreCallContext Fields

| Field | Type | Description |
|-------|------|-------------|
| `call_id` | `str` | Unique call identifier |
| `caller_number` | `str` | Caller's phone number |
| `called_number` | `str` | Number that was called |
| `caller_name` | `str` | Caller ID name (if available) |
| `context_name` | `str` | Asterisk context |
| `call_direction` | `str` | "inbound" or "outbound" |
| `campaign_id` | `str` | Campaign ID (outbound calls) |
| `lead_id` | `str` | Lead ID (outbound calls) |
| `channel_vars` | `Dict` | Asterisk channel variables |
| `config` | `Dict` | Tool configuration from YAML |
| `ari_client` | `object` | ARI client (for Asterisk operations) |

## Error Handling

Pre-call hooks must **never throw exceptions**. On any error, return an empty dict `{}`:

```python
async def execute(self, context: PreCallContext) -> Dict[str, str]:
    try:
        # Your lookup logic
        return {"customer_name": name}
    except Exception as e:
        logger.warning("pre_call_lookup_failed", error=str(e))
        return {}  # Empty = no enrichment, call continues normally
```

## Testing

```python
import pytest
from unittest.mock import MagicMock

@pytest.mark.asyncio
async def test_pre_call_hook():
    hook = MyPreCallHook()
    context = MagicMock(spec=PreCallContext)
    context.caller_number = "+15551234567"
    result = await hook.execute(context)
    assert isinstance(result, dict)
    assert all(isinstance(v, str) for v in result.values())
```

## Reference

- Canonical implementation: `src/tools/http/generic_lookup.py` (`GenericHTTPLookupTool`)
- Context definition: `src/tools/context.py` (`PreCallContext`)
- Tool base: `src/tools/base.py` (`PreCallTool`)
