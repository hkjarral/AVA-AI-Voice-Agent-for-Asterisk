# Coding Guidelines

Comprehensive coding standards for all contributions to the Asterisk AI Voice Agent (AAVA).

For high-level project policies (canonical sources, deployment rules, release/merge flow, MCP integration), see [`Agents.md`](../../Agents.md) at the repo root.

---

## Python Style

### General

- Target Python **3.10+**.
- Prefer small, composable functions with clear error handling.
- All I/O operations must be **`async`** — use `asyncio`, `aiohttp`, etc.
- Keep functions under ~50 lines. Extract helpers when logic grows complex.

### Type Hints

- Required on all **public** functions (parameters and return types).
- Use `Optional`, `Dict`, `List`, `Any`, `Union` from `typing`.
- Use `AsyncIterator` for streaming returns (TTS output).
- Forward references: use `from __future__ import annotations` or string literals (`'ToolExecutionContext'`) for circular dependencies.

```python
async def execute(self, parameters: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
    ...
```

### Imports

Order imports with blank-line separators:

1. **Standard library** — `asyncio`, `dataclasses`, `logging`, `os`, etc.
2. **Third-party** — `aiohttp`, `structlog`, `pytest`, etc.
3. **Local** — `from src.tools.base import Tool`, etc.

```python
import asyncio
import logging
from typing import Dict, Any, Optional

import aiohttp
import structlog

from src.tools.base import Tool, ToolDefinition, ToolParameter
from src.tools.context import ToolExecutionContext
```

Use `src.` prefix for all internal imports.

### Logging

- Use **`structlog.get_logger(__name__)`** in application code (providers, pipelines, tools).
- Some foundational modules (`src/tools/base.py`, `src/tools/context.py`, `src/tools/registry.py`) use standard `logging.getLogger(__name__)` — match what the file already uses.
- Never use `print()` for logging.
- Use structured keyword arguments, not f-strings in log messages:

```python
# Good
logger.info("tool_executed", tool_name=name, status=result["status"])

# Bad
logger.info(f"Tool {name} executed with status {result['status']}")
```

### Dataclasses

- Use `@dataclass` for data containers.
- Use `field(default_factory=...)` for mutable defaults (lists, dicts).
- Add docstrings with `Args` sections on public dataclasses.

```python
@dataclass
class ToolDefinition:
    """Schema metadata for a tool.

    Args:
        name: Unique tool identifier (snake_case).
        description: What the tool does (shown to AI).
        category: TELEPHONY, BUSINESS, or HYBRID.
        parameters: List of ToolParameter definitions.
    """
    name: str
    description: str
    category: ToolCategory
    parameters: List[ToolParameter] = field(default_factory=list)
```

### Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Tool names | `snake_case` | `blind_transfer`, `hangup_call` |
| Component keys | `<provider>_<role>` | `openai_llm`, `deepgram_stt` |
| Private methods | `_leading_underscore` | `_build_payload()` |
| Enum values | `UPPER_CASE` | `ToolPhase.PRE_CALL` |
| Config keys | `snake_case` in YAML | `max_execution_time` |
| Classes | `PascalCase` | `GenericHTTPLookupTool` |

---

## Tool Development

> Full tutorial: [`docs/contributing/tool-development.md`](tool-development.md)

### Quick Reference

1. **Extend** the correct ABC from `src/tools/base.py`:
   - `Tool` — in-call tools (AI invokes during conversation)
   - `PreCallTool` — runs after answer, before AI speaks
   - `PostCallTool` — fire-and-forget after call ends

2. **Define** a `ToolDefinition` with category, parameters, and description.

3. **Implement** `execute()`:
   - In-call: `async execute(parameters, context) -> Dict[str, Any]` with `{status, message, ...}`
   - Pre-call: `async execute(context: PreCallContext) -> Dict[str, str]` (output variables)
   - Post-call: `async execute(context: PostCallContext) -> None`

4. **Register** in `src/tools/registry.py` under `initialize_default_tools()`.

5. **Access context** via `ToolExecutionContext` — never bypass for ARI/session access.

### Return Formats

```python
# In-call tool
return {"status": "success", "message": "Transfer initiated", "target": destination}

# Pre-call tool
return {"customer_name": "John Doe", "account_tier": "premium"}

# Post-call tool — returns None (fire-and-forget)
```

### Common Pitfalls

See [`docs/contributing/COMMON_PITFALLS.md`](COMMON_PITFALLS.md) — especially:
- Schema format mismatches between providers (OpenAI Realtime vs Chat Completions)
- Deepgram uses `functions` not `tools`
- Always initialize conversation history from session, never overwrite

---

## Provider & Pipeline Development

> Full tutorial: [`docs/contributing/provider-development.md`](provider-development.md)

### Two Integration Surfaces

| Type | Base Class | Location | When to Use |
|------|-----------|----------|-------------|
| **Full-Agent Provider** | `AIProviderInterface` | `src/providers/` | Provider handles STT+LLM+TTS end-to-end (e.g., OpenAI Realtime) |
| **Pipeline Adapter** | `STTComponent` / `LLMComponent` / `TTSComponent` | `src/pipelines/` | Modular: mix-and-match STT, LLM, TTS from different providers |

### Full-Agent Providers

Extend `AIProviderInterface` from `src/providers/base.py`:

- Implement: `supported_codecs`, `start_session()`, `send_audio()`, `stop_session()`
- Optionally implement `ProviderCapabilitiesMixin` for capability hints
- Event-driven: push events via `on_event` callback

### Pipeline Adapters

Extend the appropriate component from `src/pipelines/base.py`:

- `STTComponent.transcribe(call_id, audio_pcm16, sample_rate_hz, options) -> str`
- `LLMComponent.generate(call_id, transcript, context, options) -> Union[str, LLMResponse]`
- `TTSComponent.synthesize(call_id, text, options) -> AsyncIterator[bytes]`

Register factory functions in `src/pipelines/orchestrator.py`.

### Key Rules

- Honor negotiated audio profiles — never hard-code codecs or sample rates.
- Emit Prometheus metrics and expose readiness via `/health`.
- Use `_auto_detect_credentials()` from `Component` base for env var fallback.
- Internal audio format is **PCM16 @ 16kHz** — match this for zero transcoding.

---

## Configuration

- **`config/ai-agent.yaml`** is the source of truth for runtime configuration.
- **Secrets** (API keys, tokens) go in `.env`, never in YAML or code.
- **New config fields** must be documented in [`docs/Configuration-Reference.md`](../Configuration-Reference.md).
- Use dot-notation for config access in context: `context.get_config_value("tools.transfer.destinations.support_agent.target", default)`.
- Variable substitution in HTTP tools:
  - Context variables: `{caller_number}`, `{call_id}`
  - Environment variables: `${API_KEY}`

---

## Documentation

- All new features must update relevant docs.
- New features of milestone scope must have a milestone spec — use [`docs/contributing/milestones/TEMPLATE.md`](milestones/TEMPLATE.md).
- Documentation lives under `docs/` or `docs/contributing/`, never in the project root.
- Architecture changes must update [`docs/contributing/architecture-deep-dive.md`](architecture-deep-dive.md).
- New tools: update [`docs/TOOL_CALLING_GUIDE.md`](../TOOL_CALLING_GUIDE.md).
- New providers: create `docs/Provider-<Name>-Setup.md`.
- New config fields: update [`docs/Configuration-Reference.md`](../Configuration-Reference.md).

### File Naming

- Operator-facing docs: `UPPER_SNAKE.md` (e.g., `ADMIN_UI_GUIDE.md`)
- Reference/setup docs: `Title-Case-Hyphens.md` (e.g., `Provider-Azure-Setup.md`)
- Don't rename existing files — breaks external links.

---

## Testing

- Unit tests required for all new tools, adapters, and providers.
- Use **`pytest`** and **`pytest-asyncio`**.
- Mock ARI and provider APIs — don't require live services.
- Run tests: `PYTHONPATH=$(pwd) pytest tests/ -v`

### Test Structure

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_tool_execute_success():
    """Tool returns success status for valid input."""
    tool = MyTool()
    context = MagicMock(spec=ToolExecutionContext)
    result = await tool.execute({"param": "value"}, context)
    assert result["status"] == "success"
```

### What to Test

- Valid usage (happy path)
- Missing/invalid parameters
- Error handling (network failures, malformed responses)
- Disabled/misconfigured state
- For telephony tools: at least one real test call with call ID documented in PR

---

## Git & Contribution Workflow

- Small, focused commits with descriptive messages.
- Branch from **`develop`**, PR to **`staging`**.
- Reference GitHub Issue or ROADMAP item in PR description.
- Conventional Commits encouraged but not required.
- Never merge to `main` directly — always open a PR.

### PR Checklist

- [ ] Code follows these guidelines
- [ ] Tests added/updated and passing
- [ ] Documentation updated for behavior changes
- [ ] ROADMAP item or GitHub Issue referenced
- [ ] For telephony changes: test call ID documented

---

## Error Handling Philosophy

| Phase | Behavior on Error |
|-------|-------------------|
| Pre-call | Return empty dict `{}` — never throw |
| In-call | Return `{status: "error", message: ...}` — AI sees tool failure |
| Post-call | Log and continue — fire-and-forget |
| Providers | Graceful degradation — placeholder adapters for unimplemented components |

- Defensive chunking and size limits for HTTP responses.
- URL redaction in logs for sensitive parameters (api_key, token).
- Conditional debug logging via `debug_enabled(logger)` check.

---

## References

- [Agents.md](../../Agents.md) — high-level project policies
- [Tool Development Guide](tool-development.md) — full tool tutorial
- [Provider Development Guide](provider-development.md) — full provider tutorial
- [Common Pitfalls](COMMON_PITFALLS.md) — real issues from production
- [Architecture Deep Dive](architecture-deep-dive.md) — system architecture
- [Architecture Quickstart](architecture-quickstart.md) — 10-minute overview
- [Configuration Reference](../Configuration-Reference.md) — YAML and env var reference
