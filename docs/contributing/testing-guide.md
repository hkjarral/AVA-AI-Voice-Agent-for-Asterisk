# Testing Guide

## Quick Commands

```bash
# Run all Python tests
pytest tests/ -v

# Run a specific test file
pytest tests/tools/test_generic_http_lookup.py -v

# Run tests matching a keyword
pytest tests/ -k "test_hangup" -v

# Run with coverage report
pytest tests/ --cov=src --cov-report=term-missing

# CLI tests (Go)
cd cli && go test ./...

# Admin UI build check
cd admin_ui/frontend && npm ci && npm run build
```

## CI Integration

CI runs on `staging` and `main` branches:

| Workflow | File | What It Does |
|----------|------|-------------|
| CI | `.github/workflows/ci.yml` | Python tests, coverage gate (~28% baseline), Docker image size checks, Trivy scan |
| Regression Hardening | `.github/workflows/regression-hardening.yml` | CLI build, Admin UI build, Docker compose validation |

CI must pass before PRs can be merged.

## Test Structure

```
tests/
├── tools/
│   ├── conftest.py                    # Shared fixtures (mock contexts, configs)
│   ├── test_generic_http_lookup.py    # HTTP lookup tool tests
│   ├── test_generic_webhook.py        # Webhook tool tests
│   ├── test_in_call_http_lookup.py    # In-call HTTP tool tests
│   ├── test_phase_tools_base.py       # Phase tool base class tests
│   └── test_phase_tool_integration.py # Phase tool integration tests
├── test_*.py                          # Other test files
└── conftest.py                        # Global fixtures
```

## Writing Tests

### Test Pattern

Tests use `pytest` with `pytest-asyncio` for async tool execution:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_context():
    """Create a mock ToolExecutionContext."""
    ctx = MagicMock()
    ctx.call_id = "test-call-123"
    ctx.session = MagicMock()
    ctx.get_config_value = MagicMock(return_value="default")
    ctx.update_session = AsyncMock()
    return ctx


class TestMyFeature:
    def test_definition_is_correct(self):
        """Test tool definition schema."""
        tool = MyTool()
        assert tool.definition.name == "expected_name"
        assert tool.definition.category == ToolCategory.BUSINESS

    @pytest.mark.asyncio
    async def test_execute_success(self, mock_context):
        """Test successful execution."""
        tool = MyTool()
        result = await tool.execute({"param": "value"}, mock_context)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_execute_error_handling(self, mock_context):
        """Test graceful error handling."""
        tool = MyTool()
        mock_context.update_session = AsyncMock(side_effect=Exception("test error"))
        result = await tool.execute({}, mock_context)
        assert result["status"] == "error"
```

### Mocking Patterns

Common mocks used in the test suite:

```python
# Mock ARI client
mock_ari = AsyncMock()
mock_ari.channels.originate = AsyncMock(return_value=MagicMock(id="chan-123"))

# Mock config values
mock_context.get_config_value = MagicMock(side_effect=lambda key, default=None: {
    'tools.my_tool.setting': 'value',
}.get(key, default))

# Mock HTTP calls
with patch('aiohttp.ClientSession') as mock_session:
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"data": "value"})
    mock_session.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
```

### What to Test

When adding a new feature or tool:

1. **Definition correctness** — name, category, parameters, required fields
2. **Happy path** — successful execution with valid parameters
3. **Missing parameters** — graceful handling of optional/missing params
4. **Error handling** — exceptions produce error status, not crashes
5. **Edge cases** — empty strings, special characters, timeout behavior

## Coverage

Current coverage is ~28%. The CI gate enforces a baseline threshold. Help us increase it!

**High-value areas for new tests:**
- `src/tools/telephony/` — transfer, voicemail, hangup tools
- `src/tools/business/` — email dispatcher, transcript request
- `src/core/` — session store, audio gating, transport orchestrator

## Manual Testing

For telephony features, you need a live Asterisk setup. The test flow is:

1. Make a call to the agent
2. Trigger the feature you're testing (e.g., "transfer me to sales")
3. Check `agent rca` output for the call
4. Check Admin UI Call History for transcripts and tool calls

## References

- Test overview: [tests/README.md](../../tests/README.md)
- CI workflow: [.github/workflows/ci.yml](../../.github/workflows/ci.yml)
- Regression hardening: [.github/workflows/regression-hardening.yml](../../.github/workflows/regression-hardening.yml)
