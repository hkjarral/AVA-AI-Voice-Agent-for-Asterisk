# Code Style

Practical conventions used across `src/`. Match the surrounding code; this guide just makes the existing patterns explicit so new contributors don't have to reverse-engineer them.

There is **no automated linter / formatter enforced** in CI today (no `ruff.toml`, no `pyproject.toml`, no `pre-commit`), so these conventions are upheld by review. Keep diffs minimal and don't reformat unrelated lines.

## Python — general

- **Line length**: pragmatic, not strict. Match adjacent code; ~100–120 chars is fine when the alternative is awkward wrapping.
- **Indentation**: 4 spaces, no tabs.
- **String quotes**: double quotes (`"..."`) by default; single quotes are fine inside f-strings.
- **f-strings** preferred over `.format()` and `%`-formatting.
- **Type hints**: required on public function signatures; optional on internal helpers. Use `Optional[X]` rather than `X | None` for consistency with the existing codebase.

## Imports

Three groups, separated by a blank line:

1. Stdlib (`asyncio`, `os`, `json`, …)
2. Third-party (`structlog`, `prometheus_client`, `websockets`, …)
3. Local — both relative (`from ..audio.resampler import ...`) and absolute (`from src.tools.registry import ...`) styles exist; either is acceptable but stay consistent within a module.

**Optional dependencies** are imported inside a `try / except` and gated at use-site:

```python
try:
    import resend  # type: ignore
except Exception:
    resend = None
```

## Logging

- Use `structlog`, never `print()` or stdlib `logging` directly:

  ```python
  import structlog
  logger = structlog.get_logger(__name__)
  ```

  (Both `import structlog; structlog.get_logger(...)` and `from structlog import get_logger` are used in the codebase — pick whichever the surrounding module uses.)

- **Pass structured fields, don't interpolate into the message**:

  ```python
  # good
  logger.info("Provider connected", provider="deepgram", session_id=sid)

  # bad
  logger.info(f"Provider {provider} connected with session {sid}")
  ```

- Reserve `logger.error(..., exc_info=exc)` for actual failures — not control flow.

## Async

- The engine is async-first. Provider/tool entry points are `async def`.
- Spawn fire-and-forget tasks with `asyncio.create_task(...)` and **always attach a done-callback** that logs exceptions (see `_log_provider_task_exception` in `src/providers/deepgram.py` for the canonical pattern).
- Don't block the event loop with `time.sleep()` — use `await asyncio.sleep(...)`.

## Tools (`src/tools/`)

- Each tool is a class extending `Tool` (`src/tools/base.py`).
- The `definition` property returns a `ToolDefinition` with `name`, `description`, `category` (one of `ToolCategory.{TELEPHONY, BUSINESS, HYBRID}` — see [src/tools/base.py](../../src/tools/base.py)), and `parameters` (list of `ToolParameter`).
- `execute(parameters, context)` is `async`, takes a `ToolExecutionContext`, and returns the standard return shape:

  ```python
  return {"status": "success" | "error", "message": "...", "data": {...}}
  ```

- Tool names are lowercase snake_case. The canonical name is what's registered in `src/tools/registry.py`; aliases live in `TOOL_ALIASES`. Lead with the canonical name in docs (e.g. `blind_transfer`, not `transfer`).

## Providers & pipelines

- **Monolithic providers** (`src/providers/`): one file per provider, class extends `AIProviderInterface` from `src/providers/base.py`. Use existing providers (`deepgram.py`, `openai_realtime.py`, `google_live.py`) as references — see `docs/contributing/provider-development.md`.
- **Pipeline adapters** (`src/pipelines/`): one component per adapter (`google_stt`, `elevenlabs_tts`, etc.). See `docs/contributing/pipeline-development.md`.

## Prometheus metrics

- Define module-level constants with a leading underscore (`_STREAMING_ACTIVE_GAUGE`, `_DEEPGRAM_INPUT_RATE`).
- Metric names are `ai_agent_<area>_<what>_<unit>` (e.g. `ai_agent_streaming_active`, `ai_agent_call_duration_seconds`).
- Avoid high-cardinality labels — **never** add `call_id` as a label. Use Admin UI → Call History for per-call debugging instead.
- Document every new metric in `docs/MONITORING_GUIDE.md`.

## Comments

Default to writing no comments. Only add one when the **WHY** is non-obvious — a hidden constraint, a workaround for a specific bug, behavior that would surprise a reader. Don't explain WHAT the code does (well-named identifiers handle that), and don't reference the current task / fix / callers ("used by X", "added for the Y flow", "handles the case from issue #123") — those belong in the PR description and rot as the codebase evolves.

## Tests

- `tests/` is a flat directory — one `test_<topic>.py` per area (e.g. `test_audio_resampler.py`, `test_attended_transfer_tool.py`). Don't mirror `src/` subdirectories; pick a descriptive topic name instead.
- Use `pytest`. Async tests use `pytest-asyncio` with `@pytest.mark.asyncio`.
- Prefer real fixtures (file paths, config dicts) over mocks where it's cheap. When mocking, mock at the boundary (HTTP/WebSocket), not internal helpers.
- See `docs/contributing/testing-guide.md` for run commands.

## Documentation

- New env vars → add to `docs/ENVIRONMENT_VARIABLES.md` AND `docs/Configuration-Reference.md`.
- New config keys → add to `docs/Configuration-Reference.md` AND `config/ai-agent.example.yaml`.
- New tools → add to `docs/TOOL_CALLING_GUIDE.md` AND the relevant tool-specific guide if one exists.
- New metrics → add to `docs/MONITORING_GUIDE.md`.

## When in doubt

- Match the surrounding file's conventions over this guide.
- Keep diffs minimal; don't reformat unrelated lines.
- If a convention is unclear or contradictory, raise it on Discord or in your PR rather than guessing.
