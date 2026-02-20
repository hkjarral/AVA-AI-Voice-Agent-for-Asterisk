# Asterisk AI Voice Agent – Codex/CLI Rules (Agents)

This file provides high-level guidance for Codex/CLI-style assistants working in this repository.

## Role of AVA

- AVA (defined in `AVA.mdc`) is the **project manager and senior engineer** for this project.
- When users have questions about:
  - What feature to work on next
  - How to onboard as a new developer
  - How to add providers/tools/pipelines
  - How to debug call behavior and prepare PRs
- Prefer to:
  - Load and follow `AVA.mdc` for high-level planning and guidance.
  - Use this file and the IDE rule files (Cursor/Windsurf/Claude/Gemini) for technical guardrails and context.

## Canonical Sources

Codex/CLI assistants should treat the following as primary sources of truth:

- Overview and quick start:
  - `README.md`
  - `docs/README.md`
- Architecture and roadmap:
  - `docs/contributing/architecture-deep-dive.md`
  - `docs/contributing/architecture-quickstart.md`
  - `docs/ROADMAP.md`
  - `docs/baselines/golden/`
  - `docs/contributing/milestones/`
  - Regression evidence in `docs/resilience.md` (and `docs/regressions/` if/when restored)
- Operation and tools:
  - `docs/INSTALLATION.md`
  - `docs/FreePBX-Integration-Guide.md`
  - `docs/Configuration-Reference.md`
  - `docs/MONITORING_GUIDE.md`
- Tools and integrations:
  - `docs/TOOL_CALLING_GUIDE.md`
- Feature backlog / community features:
  - `docs/contributing/README.md`
  - `docs/contributing/quickstart.md`

When there is a conflict, prefer these repository docs over external sources.

## Coordination with IDE Rules

- Cursor rules:
  - `.cursor/rules/asterisk_ai_voice_agent.mdc`
  - `.cursor/rules/architecture-overview.mdc`
  - `.cursor/rules/project-roadmap.mdc`
- Windsurf rules:
  - `.windsurf/rules/asterisk_ai_voice_agent.md`
  - `.windsurf/rules/architecture-overview.md`
  - `.windsurf/rules/project-roadmap.md`
  - `.windsurf/rules/document-creation-rule.md`
- Claude Code rules:
  - `CLAUDE.md`
- Gemini rules:
  - `Gemini.md`

These files define **how** to safely change code and docs:

- Two-container architecture (ai-engine, local-ai-server).
- Dual upstream transport (ExternalMedia RTP default, AudioSocket supported) and streaming-first downstream with file fallback.
- Use of `SessionStore`, `PlaybackManager`, `ConversationCoordinator`, `AudioGatingManager`, `TransportOrchestrator`, and audio profiles.
- Streaming defaults, profiles, and gating rules.
- Roadmap structure and regression documentation practices.

Codex/CLI assistants should not contradict these rules; instead, use them as constraints when proposing edits.

## MCP Tools (Linear and Others)

If the environment exposes MCP servers (for example via `linear-mcp-server`, `perplexity-ask`, etc.):

- Prefer:
  - MCP access to Linear for reading/updating AAVA issues (AAVA‑63..AAVA‑66 and future).
  - MCP access to curated resources over raw web search.
- However:
  - Do **not** request or expose secrets (API keys, tokens) in chat.
  - Assume some contributors will not have MCP configured; in that case rely on local docs (especially `docs/linear-issues-community-features.md`).

## Typical Flows

- New contributor (non-developer / Asterisk operator):
  - Point to `docs/contributing/OPERATOR_CONTRIBUTOR_GUIDE.md`.
  - Suggest running `scripts/setup-contributor.sh` on their local machine.
  - Recommend Windsurf (or Cursor/Claude Code) with AVA handling the technical work.
  - AVA's contributor playbooks (in `AVA.mdc`) automate fork → branch → commit → PR via `gh` CLI.
  - Read this file alongside `AVA.mdc` for full project context.
- New developer:
  - Point to `docs/DEVELOPER_ONBOARDING.md`.
  - Encourage use of an AI-enabled IDE (e.g., Windsurf via the referral link in that doc) and AVA for guidance.
- New provider/tool/pipeline:
  - Ask AVA for a high-level plan and mapping to AAVA issues.
  - Use IDE rules and architecture docs when editing code.
  - Review `docs/contributing/CODING_GUIDELINES.md` for code standards.
- Debugging calls:
  - Suggest `agent troubleshoot`, `scripts/rca_collect.sh`, and relevant docs.
  - Use golden baselines and regression docs for comparison.

Keep interactions concise and focused, and let AVA orchestrate the larger workflow.

## Development Server Notes

- SSH: `root@voiprnd.nemtclouddispatch.com`
- Project directory: `/root/Asterisk-AI-Voice-Agent`

## GitHub PR Descriptions / Comments

- When drafting PR descriptions or GitHub comments for copy/paste into the GitHub web UI, always return the text inside a fenced Markdown code block using ```md ... ``` so links and lists render correctly after pasting.

## Release / Merge Policy (Required)

- **Never merge to `main` directly.** Always open a PR first.
- When a change is ready:
  - Push a feature/fix branch to `origin`
  - Open a PR targeting `main`
  - Wait for required checks and human review/approval
  - Merge via GitHub (squash/merge/rebase per repo settings)
- If asked to “merge to main” in chat, treat it as “prepare a PR” unless the user explicitly overrides this policy.

## Deployment Policy (Required)

- **Never deploy via ad-hoc file copy** (e.g., `scp`, manual edits on the server, or hotfixing a running container) unless the user explicitly asks.
- All deployments must be **git-tracked**:
  - Commit changes locally
  - Push to the appropriate branch on `origin`
  - On the target server, deploy via `git pull` (or equivalent) and restart services as needed
- If an emergency hotfix is explicitly requested:
  - Make a backup on the server
  - Apply the minimal change
  - **Immediately backport** the same change into git (commit + push) and redeploy via git so the server matches a tracked revision.
