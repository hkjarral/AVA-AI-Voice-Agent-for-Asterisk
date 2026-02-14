# Tool Development

This guide covers how to implement and wire new tools that the AI agent can call at runtime.

## Overview

Tools are actions the AI can perform during a call — transferring, hanging up, sending emails, looking up data, etc. The tool system is provider-agnostic: you write a tool once, and it works with OpenAI, Deepgram, Google, ElevenLabs, and pipeline providers automatically.

## Architecture

```
ToolDefinition (schema)    →  Provider adapters translate to provider-specific format
Tool.execute(params, ctx)  →  Engine calls when AI invokes the tool
ToolRegistry               →  Singleton that holds all available tools
ToolExecutionContext        →  Per-call context with ARI access, session, config
```

## Key Files

| File | Purpose |
|------|---------|
| `src/tools/base.py` | `Tool`, `ToolDefinition`, `ToolParameter`, `ToolCategory` base classes |
| `src/tools/context.py` | `ToolExecutionContext` — per-call ARI/session access |
| `src/tools/registry.py` | `ToolRegistry` singleton and `tool_registry` global |
| `src/tools/telephony/` | Telephony tools (transfer, hangup, voicemail, etc.) |
| `src/tools/business/` | Business tools (email, transcripts) |
| `src/tools/http/` | HTTP-based tools (webhooks, lookups) |
| `src/tools/adapters/` | Provider-specific format translation (OpenAI, Deepgram) |

## Tutorial: Build a Tool Step-by-Step

This walks through the pattern used by existing tools like `hangup_call`. Use this as a reference when creating new tools.

### Step 1: Create the Tool Class

Create a new file in `src/tools/telephony/` or `src/tools/business/` depending on category.

A tool needs two things:
- A `definition` property returning a `ToolDefinition` (the schema the AI sees)
- An `execute` method that performs the action

```python
# src/tools/business/my_tool.py

from typing import Dict, Any
from src.tools.base import Tool, ToolDefinition, ToolParameter, ToolCategory
from src.tools.context import ToolExecutionContext
import structlog

logger = structlog.get_logger(__name__)


class MyNewTool(Tool):
    """Description of what this tool does."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="my_tool_name",           # Name the AI uses to call this tool
            description=(
                "Clear description of when and how the AI should use this tool. "
                "Be specific about trigger conditions."
            ),
            category=ToolCategory.BUSINESS,  # TELEPHONY, BUSINESS, or HYBRID
            requires_channel=False,          # True if tool needs ARI channel access
            max_execution_time=10,           # Timeout in seconds
            parameters=[
                ToolParameter(
                    name="param_name",
                    type="string",           # string, integer, boolean, number
                    description="What this parameter is for",
                    required=True
                ),
            ]
        )

    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """Execute the tool action."""
        param_value = parameters.get('param_name', '')

        logger.info("Tool executing",
                    call_id=context.call_id,
                    param=param_value)

        try:
            # Your tool logic here
            result = f"Processed: {param_value}"

            return {
                "status": "success",
                "message": result
            }
        except Exception as e:
            logger.error(f"Tool error: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e)
            }
```

### Step 2: Register the Tool

Add your tool to `initialize_default_tools()` in `src/tools/registry.py`:

```python
# In ToolRegistry.initialize_default_tools():
try:
    from src.tools.business.my_tool import MyNewTool
    self.register(MyNewTool)
except ImportError as e:
    logger.warning(f"Could not import MyNewTool: {e}")
```

### Step 3: Add to Config

Add the tool to the allowlist in `config/ai-agent.yaml` so it's available in contexts:

```yaml
contexts:
  default:
    tools:
      - hangup_call
      - blind_transfer
      - my_tool_name    # Your new tool
```

### Step 4: Write Tests

Create a test file following the patterns in `tests/tools/`:

```python
# tests/tools/test_my_tool.py

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.tools.business.my_tool import MyNewTool


@pytest.fixture
def tool():
    return MyNewTool()


@pytest.fixture
def mock_context():
    ctx = MagicMock()
    ctx.call_id = "test-call-123"
    ctx.get_config_value = MagicMock(return_value="default")
    ctx.update_session = AsyncMock()
    return ctx


class TestMyNewToolDefinition:
    def test_name(self, tool):
        assert tool.definition.name == "my_tool_name"

    def test_has_required_param(self, tool):
        params = tool.definition.parameters
        assert any(p.name == "param_name" and p.required for p in params)


class TestMyNewToolExecution:
    @pytest.mark.asyncio
    async def test_success(self, tool, mock_context):
        result = await tool.execute(
            {"param_name": "test_value"},
            mock_context
        )
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_missing_param(self, tool, mock_context):
        result = await tool.execute({}, mock_context)
        # Verify your tool handles missing params gracefully
        assert "status" in result
```

Run with: `pytest tests/tools/test_my_tool.py -v`

## Reference: Existing Tool Implementations

Study these as examples:

| Tool | File | Complexity | Good Example For |
|------|------|-----------|-----------------|
| `hangup_call` | `src/tools/telephony/hangup.py` | Simple | Basic tool pattern, config defaults |
| `leave_voicemail` | `src/tools/telephony/voicemail.py` | Simple | ARI integration |
| `blind_transfer` | `src/tools/telephony/unified_transfer.py` | Complex | ARI origination, error handling |
| `send_email_summary` | `src/tools/business/email_summary.py` | Medium | External service integration |
| `request_transcript` | `src/tools/business/transcript_request.py` | Medium | Multi-step conversation flow |

## Provider Adapter Wiring

Provider adapters automatically translate `ToolDefinition` schemas into provider-specific formats. You generally don't need to modify adapters when adding a new tool — the base class handles schema generation.

Adapters are in:
- `src/tools/adapters/openai.py` — OpenAI Realtime format
- `src/tools/adapters/deepgram.py` — Deepgram format

## Further Reading

- User-facing tool guide: [docs/TOOL_CALLING_GUIDE.md](../TOOL_CALLING_GUIDE.md)
- Tool calling milestone (historical): [milestone-16-tool-calling-system.md](milestones/milestone-16-tool-calling-system.md)
- Tests: [tests/README.md](../../tests/README.md)
