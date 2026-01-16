# Developer Onboarding

This guide is for contributors who want to run the repo locally, make changes, and validate behavior.

## Choose Your Goal

- **Operator / make calls**: start with `docs/INSTALLATION.md` and `docs/FreePBX-Integration-Guide.md`
- **Contribute code**: start with `docs/contributing/quickstart.md` and `docs/contributing/README.md`

## Recommended Dev Flow

1. Read architecture overview: `docs/contributing/architecture-quickstart.md`
2. Set up a dev environment: `docs/contributing/quickstart.md`
3. Pick a golden baseline to test against:
   - Configs: `config/ai-agent.golden-*.yaml`
   - Quick references: `docs/baselines/golden/`
4. Validate and troubleshoot calls:
   - `agent check`, `agent rca` (v5.1.4)
   - Legacy aliases (v5.1.4): `agent doctor`, `agent troubleshoot`
   - RCA bundle capture: `scripts/rca_collect.sh`

## Where To Make Changes

- Engine (Python): `src/`
- Local AI Server (Python): `local_ai_server/`
- Admin UI: `admin_ui/frontend/` and `admin_ui/backend/`
- CLI (Go): `cli/`

## Contributing

- Process: `CONTRIBUTING.md`
- Code style: `docs/contributing/code-style.md`
