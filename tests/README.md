# Tests Overview

This document explains the test layout and how to run tests locally and on a server.

## Test Locations

- `tests/`: Python unit/integration tests for the engine and pipelines
  - `tests/test_audio_resampler.py`
  - `tests/test_pipeline_*.py` (adapters and runner lifecycle)
  - `tests/test_playback_manager.py`
  - `tests/test_session_store.py`
  - `tests/tools/` - Tool system tests (NEW - v4.1)
    - `tests/tools/telephony/` - Transfer, hangup, cancel transfer (58 tests)
    - `tests/tools/business/` - Email transcript + summary (53 tests)
- `scripts/test_externalmedia_call.py`: Health-driven end-to-end call flow check
- `scripts/test_externalmedia_deployment.py`: ARI + RTP deployment sanity
- `local_ai_server/test_local_ai_server.py`: Local AI server smoke test (optional)

## Prerequisites

- Engine running via `docker-compose up -d` (or `make up`)
- Health endpoint available at `http://127.0.0.1:15000/health`
- Python 3.11+ and dependencies (inside the ai_engine container or host venv)

## Running Unit Tests

Run inside the ai_engine container (recommended):

```bash
# From repo root
docker compose exec ai_engine pytest -q
```

Or locally (ensure venv matches requirements):

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
pytest -q
```

## Running End-to-End ExternalMedia Tests

- Call flow test:

```bash
python3 scripts/test_externalmedia_call.py --url http://127.0.0.1:15000/health
```

- Deployment sanity:

```bash
python3 scripts/test_externalmedia_deployment.py
```

## Troubleshooting

- Ensure containers are healthy: `make ps` and `make logs`
- Clear logs between runs to improve signal: `make server-clear-logs` (localhost-aware)
- Validate configuration: `python3 scripts/validate_externalmedia_config.py`

## CI/CD Integration

Test coverage is enforced via GitHub Actions (`.github/workflows/ci.yml`):

- **Current Coverage**: ~28-29% (111 tool tests)
- **Enforced Threshold**: 26.5% (current baseline)
- **Next Target**: 30% (need more tests)
- **Ultimate Target**: 40%+
- **Coverage Reports**: HTML, XML, and JSON reports uploaded as GitHub Actions artifacts

**Test Stats**:

- 58 telephony tool tests ✅
- 28 email transcript tool tests ✅
- 25 email summary tool tests ✅
- **Total Tool Tests: 111**
- **Overall Tests Passing: ~276**

## Coverage Targets

| Module | Current | Target | Status |
|--------|---------|--------|--------|
| `src/tools/` | 80%+ | 80%+ | ✅ |
| `src/core/session_store.py` | ~60% | 80% | 🟡 |
| `src/core/models.py` | ~40% | 60% | 🟡 |
| `src/engine.py` | ~15% | 30% | 🟢 |
| `src/providers/` | ~20% | 35% | 🟢 |

**Overall**: 27% baseline → 30% next → 40% ultimate target

## Test Quality Standards

New code must meet these standards:

- **Unit tests**: >80% coverage for new functions/classes
- **Integration tests**: For multi-component workflows
- **Mocking**: Use fixtures from `conftest.py`
- **Assertions**: Clear, specific, testing one thing
- **Documentation**: Docstrings explaining what's tested
