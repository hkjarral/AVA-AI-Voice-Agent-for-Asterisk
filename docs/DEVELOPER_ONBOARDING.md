# Developer Onboarding

This guide is for contributors who want to run the repo locally, make changes, and validate behavior.

## Project at a Glance

Asterisk AI Voice Agent (AAVA) is an open-source AI voice agent platform for Asterisk/FreePBX telephone systems. It connects to Asterisk via ARI, routes audio through configurable AI providers (OpenAI, Deepgram, Google, ElevenLabs, or local models), and enables the AI to take real-world actions like transferring calls and sending emails. The project ships as Docker containers with a web-based Admin UI.

## Choose Your Goal

- **Operator / make calls**: Start with [INSTALLATION.md](INSTALLATION.md) and [FreePBX Integration Guide](FreePBX-Integration-Guide.md)
- **Contribute code**: Start with [Contributing Quickstart](contributing/quickstart.md) and [Contributing README](contributing/README.md)

## Directory Map

```
Asterisk-AI-Voice-Agent/
├── src/                    # Core Python engine
│   ├── core/               #   Session management, audio gating, transport orchestrator
│   ├── providers/          #   Full-agent providers (OpenAI, Deepgram, Google, ElevenLabs)
│   ├── pipelines/          #   Modular STT/LLM/TTS pipeline adapters
│   └── tools/              #   Tool calling system (telephony + business tools)
├── local_ai_server/        # Optional local AI server (Vosk, Piper, Kokoro, llama.cpp)
├── admin_ui/
│   ├── backend/            #   FastAPI backend (Python)
│   └── frontend/           #   React frontend (TypeScript)
├── cli/                    # Agent CLI tool (Go)
├── config/                 # YAML configuration files + golden baselines
├── scripts/                # Utility scripts (install, preflight, model setup)
├── tests/                  # Python test suite (pytest)
├── docs/                   # All documentation
│   └── contributing/       #   Developer guides and milestone specs
└── .github/                # CI/CD workflows, issue templates
```

## Recommended Dev Flow

1. Read the architecture overview: [Architecture Quickstart](contributing/architecture-quickstart.md) (10-minute read)
2. Set up a dev environment: [Contributing Quickstart](contributing/quickstart.md)
3. Pick a golden baseline to test against:
   - Configs: `config/ai-agent.golden-*.yaml`
   - Quick references: `docs/baselines/golden/`
4. Validate and troubleshoot calls:
   - `agent check` — health diagnostics
   - `agent rca` — post-call root cause analysis
   - Legacy aliases: `agent doctor`, `agent troubleshoot`

## Running Tests Locally

```bash
# Python tests (from repo root)
pip install -r requirements.txt -r requirements-test.txt
pytest tests/ -v

# Run a specific test file
pytest tests/tools/test_hangup.py -v

# CLI tests (Go)
cd cli && go test ./... && cd ..

# Admin UI build check
cd admin_ui/frontend && npm ci && npm run build && cd ../..
```

## Making a Test Call

To make an actual test call, you need:
1. An Asterisk 18+ server with ARI enabled
2. A SIP phone or softphone registered to Asterisk
3. A dialplan route pointing to the `asterisk-ai-voice-agent` Stasis app
4. At least one provider API key configured in `.env`

See [FreePBX Integration Guide](FreePBX-Integration-Guide.md) for the full setup. You can also call the live demo at **(925) 736-6718** to hear the agent in action.

## Suggested First Tasks

New to the project? Here are some great starting points:

- **Fix a doc typo or broken link** — Browse `docs/` and submit a quick PR
- **Add a unit test** — Look at `tests/` for patterns; coverage is at ~28%
- **Improve CLI help text** — `cli/` is Go; help strings are in `cmd/` files
- **Browse `good first issue`** — Check [GitHub Issues](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues?q=label%3A%22good+first+issue%22)

## Where To Make Changes

| Area | Directory | Language |
|------|-----------|----------|
| Core engine | `src/` | Python |
| Local AI server | `local_ai_server/` | Python |
| Admin UI backend | `admin_ui/backend/` | Python (FastAPI) |
| Admin UI frontend | `admin_ui/frontend/` | TypeScript (React) |
| CLI tools | `cli/` | Go |
| Documentation | `docs/` | Markdown |
| CI/CD | `.github/workflows/` | YAML |

## Contributing

- Process and PR workflow: [CONTRIBUTING.md](../CONTRIBUTING.md)
- Code style: [code-style.md](contributing/code-style.md)
- Architecture deep dive: [architecture-deep-dive.md](contributing/architecture-deep-dive.md)
- Roadmap and planned work: [ROADMAP.md](ROADMAP.md)
