# Asterisk AI Voice Agent – Gemini Rules

This file provides rules and context for Gemini-based IDE assistants working in this repository.

> For high-level planning, onboarding, and "what to build next", defer to AVA (`AVA.mdc`). Use this file for technical guardrails.

## Architecture Overview

- **Two-container stack**: `ai-engine` (Python) + `local-ai-server` (Vosk/Piper)
- **Upstream transport**: ExternalMedia RTP (default) or AudioSocket (legacy)
- **Downstream playback**: Streaming-first with file fallback
- **Call lifecycle**: Hybrid ARI flow via `_handle_caller_stasis_start_hybrid()`
- **State management**: `SessionStore`, `ConversationCoordinator`, `PlaybackManager`, `AudioGatingManager`

## Canonical Documentation

Primary sources of truth (prefer these over external sources):

- **Architecture**: `docs/contributing/architecture-deep-dive.md`, `docs/contributing/architecture-quickstart.md`
- **Roadmap**: `docs/ROADMAP.md`
- **Installation**: `docs/INSTALLATION.md`, `docs/FreePBX-Integration-Guide.md`
- **Contributing**: `docs/contributing/README.md`, `docs/contributing/CODING_GUIDELINES.md`
- **Baselines**: `docs/baselines/golden/`

## Development Workflow

### For Contributors

1. Run Docker locally: `./install.sh && docker compose up`
2. Branch from `develop`, keep changes small and aligned with architecture
3. Test with real calls, use `scripts/rca_collect.sh` for diagnostics
4. Submit PRs via `gh pr create --base develop`

### For Maintainers

- Deploy via `.agent/workflows/deploy_to_server.md`
- Use `make deploy`, `make server-logs` Makefile targets
- Never bypass git with `scp` or manual container edits

## Code Change Guidelines

- Register new STT/LLM/TTS adapters in `src/pipelines/orchestrator.py`
- Extend YAML schema for new providers, update `config/ai-agent.yaml` examples
- Providers must emit Prometheus metrics and expose `/health`
- Archive regressions in `docs/resilience.md`

## Documentation Rules

- Do not create new docs without explicit request
- Never add docs in project root
- Canonical docs go in `docs/` or `docs/contributing/`
- Architectural changes land in `docs/contributing/architecture-deep-dive.md` first

## Release & Deployment Policy

- **Never merge to `main` directly** — always open a PR
- **Never deploy via ad-hoc file copy** — all deployments must be git-tracked
- Push to branch → PR → review → merge via GitHub

## Contributor Support

When someone says "I want to contribute":

1. Point to `docs/contributing/OPERATOR_CONTRIBUTOR_GUIDE.md`
2. Suggest `scripts/setup-contributor.sh` for environment setup
3. Recommend tasks from `docs/ROADMAP.md` based on interest
4. All code must follow `docs/contributing/CODING_GUIDELINES.md`

## MCP Tools

If MCP servers are available, prefer MCP access to Linear (AAVA issues) and curated resources over raw web search. Do not expose API keys or tokens in chat.

## Related Rule Files

- `AVA.mdc` — Project manager persona with contributor playbooks
- `Agents.md` — Codex/CLI rules
- `CLAUDE.md` — Claude Code rules
- `.windsurf/rules/asterisk_ai_voice_agent.md` — Windsurf rules
- `.cursor/rules/asterisk_ai_voice_agent.mdc` — Cursor rules
