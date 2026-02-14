# Contributing to Asterisk AI Voice Agent

Thank you for your interest in contributing! All contributions are welcome — code, documentation, bug reports, tests, ideas, and feedback.

By participating, you agree to our [Code of Conduct](CODE_OF_CONDUCT.md).

<!-- TODO: Add YouTube video link once recorded -->
<!-- **Watch the 5-minute walkthrough:** [YouTube Video](https://youtube.com/...) -->

## Why Contribute?

**You don't need to know how to code.** Our AI assistant AVA writes the code for you.

- **You know telephony better than most developers** — your Asterisk experience is rare and valuable
- **AI does the coding** — Windsurf + AVA handle Python, React, Go — you just describe what you want
- **Your server IS your test lab** — test features on your actual phone system with real calls
- **One command to contribute** — run `scripts/setup-contributor.sh` once, then tell AVA to "submit a PR"
- **Get recognized** — your name in our Contributors list, release notes, and Discord
- **Shape YOUR tool** — contribute features YOU actually need in your day-to-day operations

**New to open source?** See the [Operator Contributor Guide](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/docs/contributing/OPERATOR_CONTRIBUTOR_GUIDE.md) — a step-by-step walkthrough for people who have never used GitHub before.

## What We're Looking For

| Contribution Type | Examples | Good Starting Point? |
|-------------------|----------|---------------------|
| **Documentation** | Fix typos, improve guides, add examples | Yes |
| **Tests** | Add unit tests, increase coverage (currently ~28%) | Yes |
| **Bug fixes** | Fix reported issues | Yes |
| **Tools** | New telephony or business tools (SMS, calendar, etc.) | Intermediate |
| **Providers** | New STT/LLM/TTS pipeline adapters (Azure, Claude, etc.) | Intermediate |
| **Admin UI** | Frontend features, accessibility, UX improvements | Intermediate |
| **CLI** | New commands, help text improvements | Intermediate |
| **CI/CD** | Workflow improvements, automated checks | Intermediate |

## Finding Work

- **Good first issues**: [GitHub Issues labeled `good first issue`](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues?q=label%3A%22good+first+issue%22)
- **Help wanted**: [GitHub Issues labeled `help wanted`](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues?q=label%3A%22help+wanted%22)
- **Roadmap**: See [docs/ROADMAP.md](docs/ROADMAP.md) for planned milestones with difficulty levels
- **Discord**: Join [our server](https://discord.gg/ysg8fphxUe) and ask in #contributing

## Getting Started

**New to the project?** Start here:

1. **[Developer Onboarding](docs/DEVELOPER_ONBOARDING.md)** - Project overview, directory map, first tasks
2. **[Quick Start Guide](docs/contributing/quickstart.md)** - Set up your dev environment (15 min)
3. **[Architecture Overview](docs/contributing/architecture-quickstart.md)** - Understand the system (10 min)
4. **[Common Pitfalls](docs/contributing/COMMON_PITFALLS.md)** - Avoid these mistakes

**For complete developer documentation**, see [docs/contributing/](docs/contributing/README.md).

## Branches and Workflow

Active branches:
- `develop`: Feature work and ongoing development
- `staging`: Release prep and GA readiness (PRs typically target here)
- `main`: Stable releases

Recommended flow:

1. Fork the repository and create a feature branch from `develop`
2. Make your changes in small, focused commits
3. Open a Pull Request (PR) against `staging` (preferred) or `develop`
4. Include a clear description and testing notes
5. A maintainer will review, run CI/manual checks, and merge

Releases are promoted from `staging` to `main` after golden baseline validation.

## Development Setup

### Option A: AI-Assisted Setup (Recommended for Operators)

```bash
git clone -b develop https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent
./scripts/setup-contributor.sh
```

Then open the folder in [Windsurf](https://codeium.com/windsurf) and type "I want to contribute" in the chat. See the [Operator Contributor Guide](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/docs/contributing/OPERATOR_CONTRIBUTOR_GUIDE.md) for details.

### Option B: Traditional Setup (For Developers)

```bash
git clone -b develop https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent
./install.sh   # guided setup; or follow README for manual steps
```

For Local/Hybrid profiles, run `make model-setup` when prompted to download models.

## Code Style & Quality

- Python: target 3.10+. Keep code readable and well-logged.
- Prefer small, composable functions and clear error handling.
- Add or update documentation where behavior changes.
- See [code-style.md](docs/contributing/code-style.md) for details.

## Tests & Verification

```bash
# Run Python tests
pytest tests/ -v

# Verify health
curl http://127.0.0.1:15000/health

# Full diagnostics
agent check
```

See [testing-guide.md](docs/contributing/testing-guide.md) for more detail.

## Commit Messages

- Use clear, descriptive messages (Conventional Commits encouraged but not required)
- Reference related issues where applicable (e.g., `Fixes #123`)

## Proposing Features

For new features or significant changes:

1. Open a [GitHub Discussion](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/discussions) in "Ideas"
2. If accepted, create a milestone spec using the [template](docs/contributing/milestones/TEMPLATE.md)
3. Submit as a Draft PR for review

See [GOVERNANCE.md](GOVERNANCE.md) for the full decision-making process.

## Review Expectations

- PRs are typically reviewed within a few days
- The maintainer may request changes or suggest a different approach
- CI must pass before merge
- Documentation updates are expected for behavior changes

## Reporting Issues

Use [GitHub Issues](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues) with:
- Steps to reproduce
- Relevant logs (`agent check`, `agent rca`)
- Environment details (OS, Docker version, Asterisk version)
- Redacted config if relevant

## Issue Labels

We use labels to categorize issues:

| Label | Description |
|-------|-------------|
| `good first issue` | Good for newcomers |
| `help wanted` | Extra attention needed |
| `difficulty: beginner` | No Asterisk experience needed |
| `difficulty: intermediate` | Some domain knowledge needed |
| `difficulty: advanced` | Deep telephony/provider knowledge |
| `area: docs` | Documentation |
| `area: admin-ui` | Admin UI (React/TypeScript) |
| `area: engine` | Core Python engine |
| `area: cli` | Go CLI tool |
| `area: providers` | Provider integrations |
| `area: tools` | Tool calling system |
| `area: local-ai` | Local AI server |

## File Naming Conventions

- Operator-facing docs: `UPPER_SNAKE.md` (e.g., `ADMIN_UI_GUIDE.md`)
- Reference/setup docs: `Title-Case-Hyphens.md` (e.g., `Provider-Azure-Setup.md`)
- Don't rename existing files (breaks external links)

---

Thanks again for helping improve the project!
