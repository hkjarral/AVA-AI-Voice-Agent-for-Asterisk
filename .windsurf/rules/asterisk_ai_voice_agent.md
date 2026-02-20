---
trigger: always_on
description: Development rules and guidelines for the Asterisk AI Voice Agent v4.x project
globs: src/**/*.py, *.py, docker-compose.yml, Dockerfile, config/ai-agent.yaml, docs/contributing/architecture-*.md, docs/ROADMAP.md
---

# Asterisk AI Voice Agent v4.x — Windsurf Rules

> For high-level planning, onboarding, and “what to build next”, defer to the AVA project manager persona defined in `AVA.mdc`. Use this file to constrain *how* code, configs, and docs are changed.

## GA Scope & Architecture
- Two-container stack (`ai-engine`, `local-ai-server`). Upstream transport is dual-path: ExternalMedia RTP is the production default out of the box; AudioSocket remains supported via `audio_transport=audiosocket` and must stay parity-tested. Downstream playback is streaming-first, with file playback as fallback/debug.
- Hybrid ARI flow (`_handle_caller_stasis_start_hybrid`) remains authoritative for call lifecycle; extend handlers rather than bypassing bridge/originate logic.
- `SessionStore`, `ConversationCoordinator`, `PlaybackManager`, and `AudioGatingManager` manage state, gating, and metrics; prefer these interfaces over legacy dictionaries when adding logic.
- `TransportOrchestrator` and YAML `profiles` own codec/sample and transport negotiation; new transport/format work should extend these layers, not hard-code codecs.

## Workflow Essentials
- For **public contributors**:
  - Run Docker locally following `README.md`, `docs/INSTALLATION.md`, and `cli/README.md` (e.g., `./install.sh`, `docker compose up`, and `agent init/doctor/demo/troubleshoot`).
  - Prefer small, well-scoped branches from `develop` and keep changes aligned with the documented architecture.
- For **maintainers using the shared lab server**:
  - Develop on `develop`, push, then deploy using the workflow in `.agent/workflows/deploy_to_server.md` (host is configurable via `SERVER_HOST`, default `mypbx.server.com`).
- Use `scripts/rca_collect.sh` for RCA evidence during regressions.
- When `vad.use_provider_vad` is enabled, keep local WebRTC/Enhanced VAD disabled and document any follow-on tuning in the config + milestone notes.
- For OpenAI Realtime, keep `providers.openai_realtime.provider_input_sample_rate_hz=24000` and ensure session formats advertise PCM16 @ 24 kHz.
- Makefile targets (`make deploy`, `make server-logs`, `make test-local`, etc.) are recommended but optional; if invoking compose directly, include `--build` for code changes.
- Never rely on `docker compose restart` for new code, and do not bypass git by copying files into containers.

## Streaming Transport Guardrails
- Treat `config/ai-agent.yaml` as the source of truth for streaming defaults: `streaming.min_start_ms`, `low_watermark_ms`, `fallback_timeout_ms`, `provider_grace_ms`, `jitter_buffer_ms`, and `barge_in.post_tts_end_protection_ms`. Prefer tuning via audio `profiles` when possible.
- Keep `StreamingPlaybackManager` pacing provider frames in ~20 ms slices, sustaining jitter buffer depth before playback and pausing when depth drops below the low watermark.
- Preserve post‑TTS guard windows so the agent does not hear its own playback; `ConversationCoordinator`/`AudioGatingManager` must stay responsible for gating toggles and metrics.

## Pipelines & Providers
- Register new STT/LLM/TTS adapters via `src/pipelines/orchestrator.py`, extend the YAML schema, update examples, and refresh milestone docs under `docs/contributing/milestones/`.
- Providers must honor negotiated profiles, emit events compatible with Prometheus metrics, and expose readiness through `/health`.
- Archive regression details (call IDs, tuning outcomes) in `docs/resilience.md` (and `docs/regressions/` if/when restored) and compare to golden baselines under `docs/baselines/golden/`.
- Local provider: keep STT idle-finalize around ~1.2 s, async LLM execution, and transcript aggregation (≥ 3 words or ≥ 12 chars) so slow local runs never starve audio ingest.

## Testing & Observability
- Regression loop: place a streaming call, watch transport depth/fallback logs, scrape `/metrics`, then archive findings with `scripts/rca_collect.sh`.
- Remote ai-engine logs: use the `server-logs` Makefile target or follow `.agent/workflows/deploy_to_server.md` for current commands.
- Compare results to golden baselines under `docs/baselines/golden/` and record deviations in `docs/resilience.md`.

## Documentation Hygiene
- Do not create new documentation files unless explicitly asked. Never add docs in the project root. When asked to create canonical docs, place them under `docs/` or `docs/contributing/`; use `archived/` for drafts/RCAs.
- Architectural shifts must land in `docs/contributing/architecture-deep-dive.md` first, followed by roadmap and milestone updates; rule files (`Agents.md`, `Gemini.md`, Windsurf, Cursor) stay in lockstep.
- Replace references to `call-framework.md` with golden baselines (`docs/baselines/golden/`) and regression evidence in `docs/resilience.md`.

## Provider/Pipeline Resolution Precedence
- Provider precedence: `AI_PROVIDER` (Asterisk channel var) > `contexts.*.provider` > `default_provider`.
- Per-call overrides read from: `AI_PROVIDER`, `AI_AUDIO_PROFILE`, `AI_CONTEXT`.

## MCP Tools
- If MCP servers are available, prefer MCP resources over raw web search; discover via `list_mcp_resources` / `list_mcp_resource_templates`, read via `read_mcp_resource`.

## Change Safety & Review
- Validate against golden baselines in `docs/baselines/golden/` and capture RCA with `scripts/rca_collect.sh` where appropriate.

## Contributor Support

When someone says "I want to contribute" or asks how to help:
- Walk them through `docs/contributing/OPERATOR_CONTRIBUTOR_GUIDE.md`.
- Suggest tasks from `docs/ROADMAP.md` Good First Issues based on their interest.
- All code must follow `docs/contributing/CODING_GUIDELINES.md`.
- New features of milestone scope need a spec using `docs/contributing/milestones/TEMPLATE.md`.
- Use `gh` CLI for PR submission: `gh repo fork`, create branch from `develop`, `gh pr create --base staging`.
- Run tests: `PYTHONPATH=$(pwd) pytest tests/ -v`
- PRs must reference a ROADMAP item or GitHub Issue and include testing summary.

