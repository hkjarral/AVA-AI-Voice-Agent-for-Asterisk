# In-Call HTTP Hooks Development

In-call hooks are tools the AI can invoke **during a conversation** to look up data from external systems. Examples: check appointment availability, look up order status, query inventory.

> **Using AVA?** Open this file in Windsurf and say: "Help me build an in-call hook that checks appointment availability."

## Architecture

```
Caller speaks → AI decides to call tool → In-Call Hook (HTTP request) → AI uses result in response
```

In-call hooks extend `Tool` from `src/tools/base.py`. The built-in `InCallHTTPTool` in `src/tools/http/in_call_lookup.py` handles most cases via YAML config.

## Option A: YAML-Only (No Code Needed)

For simple HTTP lookups that the AI triggers during conversation:

```yaml
in_call_tools:
  check_appointment:
    enabled: true
    phase: in_call
    timeout_ms: 5000
    url: "https://api.scheduling.com/availability"
    method: POST
    headers:
      Authorization: "Bearer ${SCHEDULING_API_KEY}"
      Content-Type: "application/json"
    body_template: |
      {"date": "{date}", "service": "{service_type}"}
    parameters:
      - name: date
        type: string
        description: "The date to check (YYYY-MM-DD format)"
        required: true
      - name: service_type
        type: string
        description: "Type of service requested"
        required: true
    output_variables:
      available: "available"
      next_slot: "nextAvailableSlot"
      duration: "estimatedDuration"
    error_message: "I'm sorry, I couldn't check availability right now."
```

The AI sees the tool definition (name, description, parameters) and decides when to call it based on conversation context.

## Option B: Custom Code

For complex logic:

```python
import structlog
from typing import Dict, Any, List
from src.tools.base import Tool, ToolDefinition, ToolParameter, ToolCategory
from src.tools.context import ToolExecutionContext

logger = structlog.get_logger(__name__)

class AppointmentCheckerTool(Tool):
    """Check appointment availability during a call."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="check_appointment",
            description="Check if an appointment slot is available for a given date and service",
            category=ToolCategory.BUSINESS,
            parameters=[
                ToolParameter(
                    name="date",
                    type="string",
                    description="Date to check (YYYY-MM-DD)",
                    required=True,
                ),
                ToolParameter(
                    name="service_type",
                    type="string",
                    description="Type of service",
                    required=True,
                    enum=["consultation", "checkup", "follow-up"],
                ),
            ],
            max_execution_time=10,
        )

    async def execute(self, parameters: Dict[str, Any],
                      context: ToolExecutionContext) -> Dict[str, Any]:
        """Execute the appointment check.

        Args:
            parameters: AI-provided parameters (date, service_type).
            context: Execution context with call_id, session, ARI access.

        Returns:
            Dict with status, message, and any data for the AI.
        """
        date = parameters.get("date")
        service = parameters.get("service_type")

        try:
            # TODO: Call your scheduling API
            available = True
            next_slot = "2:00 PM"

            return {
                "status": "success",
                "message": f"Appointment available on {date} at {next_slot}",
                "available": available,
                "next_slot": next_slot,
            }
        except Exception as e:
            logger.error("appointment_check_failed", error=str(e))
            return {
                "status": "error",
                "message": "Could not check availability at this time.",
            }
```

Register in `src/tools/registry.py` under `initialize_default_tools()`.

## ToolExecutionContext Fields

| Field | Type | Description |
|-------|------|-------------|
| `call_id` | `str` | Unique call identifier |
| `caller_channel_id` | `str` | Asterisk channel ID |
| `bridge_id` | `str` | ARI bridge ID |
| `caller_number` | `str` | Caller's phone number |
| `called_number` | `str` | Number that was called |
| `caller_name` | `str` | Caller ID name |
| `context_name` | `str` | Asterisk context |
| `session_store` | `object` | Session state store |
| `ari_client` | `object` | ARI client for Asterisk operations |
| `config` | `Dict` | Full config from YAML |
| `provider_name` | `str` | Active provider name |
| `user_input` | `str` | User's last utterance |
| `detected_intent` | `str` | Detected intent (if available) |

**Accessing config values:**
```python
value = context.get_config_value("tools.appointment.api_url", "https://default.com")
```

**Accessing pre-call results:**
```python
session = await context.get_session()
pre_call_data = session.get("pre_call_results", {})
customer_name = pre_call_data.get("customer_name", "")
```

## Return Format

```python
# Success
return {"status": "success", "message": "Found 3 open slots", "data": {...}}

# Failure (expected — e.g., no results)
return {"status": "failed", "message": "No appointments available on that date"}

# Error (unexpected — e.g., API down)
return {"status": "error", "message": "Scheduling system is temporarily unavailable"}
```

The AI uses the `message` field to form its spoken response to the caller.

## Variable Substitution (YAML Mode)

In-call HTTP tools can substitute from three sources:

1. **AI parameters:** `{date}`, `{service_type}` — provided by the AI
2. **Call context:** `{caller_number}`, `{call_id}` — from the call
3. **Pre-call results:** `{customer_name}` — from pre-call hooks
4. **Environment:** `${API_KEY}` — from `.env`

## Testing

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_appointment_checker():
    tool = AppointmentCheckerTool()
    context = MagicMock(spec=ToolExecutionContext)
    result = await tool.execute(
        {"date": "2026-03-15", "service_type": "consultation"},
        context,
    )
    assert result["status"] in ("success", "failed", "error")
    assert "message" in result
```

## Reference

- Canonical implementation: `src/tools/http/in_call_lookup.py` (`InCallHTTPTool`)
- Context definition: `src/tools/context.py` (`ToolExecutionContext`)
- Tool base: `src/tools/base.py` (`Tool`, `ToolDefinition`, `ToolParameter`)
- Tool registry: `src/tools/registry.py`
