# Developer Onboarding Guide

This guide is for developers who want to contribute features (providers, tools, pipelines) to the Asterisk AI Voice Agent.
It assumes basic familiarity with Git, Docker, and Asterisk/FreePBX.

The goal is to get you from **zero** to **running the project against your own Asterisk server** with an AI-powered IDE, so you can start building confidently.

---

## 1. Prerequisites

Before you start, you should have:

- A development machine (Linux or macOS recommended) with:
  - `git`
  - Docker and Docker Compose
  - Python 3.10+ (for some scripts/tests)
- Access to an Asterisk or FreePBX server where you can:
  - Enable ARI (Asterisk REST Interface)
  - Configure dialplan contexts/extensions
  - Optionally create queues, ring groups, and voicemail boxes
- API keys for at least one AI provider (you can start with OpenAI or Deepgram; see `README.md` and `docs/INSTALLATION.md` for details).

Keep these Asterisk details handy (you will need them during setup):

- Asterisk/FreePBX host (IP or hostname)
- ARI port (default: `8088`)
- ARI username and password

---

## 2. Choose and Configure an AI-Powered IDE

You can develop with any editor, but the project is optimized for AI-enabled IDEs that understand the repo and AVA (the project manager persona defined in `AVA.mdc`).

### Recommended: Windsurf (with referral link)

1. Sign up for Windsurf using this referral link:

   - https://windsurf.com/refer?referral_code=lrjifcvyc5cntgur

2. Install Windsurf and open this repository in it.
3. Ensure Windsurf is allowed to:
   - Read local files in the repo.
   - Use its AI assistant on the project.

Once the repo is open, you can address the project manager persona (AVA) directly in Windsurf chats to ask for onboarding help, architecture summaries, or feature guidance.

### Other IDEs (Cursor, VS Code, etc.)

If you prefer another editor:

- Cursor:
  - Open the repo.
  - Make sure `.cursor/rules/` is loaded so the architecture and development rules apply.
- VS Code / other:
  - Use an AI assistant that can read the repo and follow local rules.
  - Refer to `AVA.mdc` as the high-level project manager instructions.

You can still follow this guide and use AVA conceptually even if your IDE doesn’t support AVA directly.

---

## 3. Clone the Repository

On your development machine:

```bash
git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent
```

Make sure you are on the `develop` branch for active development:

```bash
git checkout develop
```

---

## 4. Run the Installer

For most developers, the fastest path is the guided installer:

```bash
./install.sh
```

The installer will:

- Check Docker/Docker Compose.
- Ask you to choose a **golden baseline**:
  - OpenAI Realtime (recommended for quick start)
  - Deepgram Voice Agent
  - Local Hybrid
- Prompt for required API keys (only for the provider you choose).
- Generate `.env` and `config/ai-agent.yaml`.
- Build and start the Docker services (`ai_engine`, `local_ai_server`).

If you prefer a more manual setup, see `docs/INSTALLATION.md`, but for most contributors `./install.sh` is the right choice.

---

## 5. Configure Asterisk / FreePBX

Next, wire your Asterisk/FreePBX server to the AI engine.

### 5.1. Enable ARI

On your Asterisk/FreePBX server:

- Make sure ARI is enabled and reachable from the machine running this project.
- Note the host, port, username, and password (you will plug these into the CLI).

### 5.2. Dialplan Integration

Follow the dialplan instructions in:

- `docs/INSTALLATION.md` (minimal example)
- `docs/FreePBX-Integration-Guide.md` (detailed FreePBX instructions)

At minimum, you will add a context like:

```asterisk
[from-ai-agent]
exten => s,1,NoOp(Asterisk AI Voice Agent v5.1.4)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

Then route a test extension or DID to that context.

---

## 6. Use the `agent` CLI for Setup and Health Checks

The project ships with a Go-based CLI (`agent`) to make setup and diagnostics easier. See `cli/README.md` for details.

Typical flow:

```bash
# Verify CLI is installed (via install script or manual build)
agent version

# Run interactive setup (configures ARI, provider, transport)
agent setup

# Run system health checks
agent check
```

Use `agent setup` to plug in your Asterisk ARI host/port/user/pass and provider settings if the installer didn’t already capture them.

---

## 7. Make Your First Test Call

Once Docker services are up and ARI is configured:

1. Check container status:

   ```bash
   docker compose ps
   ```

2. Check health:

   ```bash
   curl http://127.0.0.1:15000/health
   ```

3. From your phone or softphone, call the extension or DID that routes to the AI context you configured.

   - Say hello, ask the agent’s name, ask a simple question, then say goodbye.
   - Watch logs:

   ```bash
   docker compose logs -f ai_engine
   ```

If the call behaves as expected, you are ready to start developing.

---

## 8. Start Developing Features

Now that the project is running against your server, you can begin contributing.

### 8.1. Talk to AVA (Project Manager)

In Windsurf or your AI-enabled IDE:

- Open a chat and explain what you want to work on, for example:
  - “I want to add a new provider.”
  - “I want to add a calendar appointment tool.”
  - “I want to improve queue transfers.”
- AVA (defined in `AVA.mdc`) will:
  - Map your request to the roadmap and Linear specs (or [Discord discussions](https://discord.gg/ysg8fphxUe)).
  - Tell you which files to touch, which configs to update, and which docs to read.
  - Propose a short plan and a checklist (tests, docs, PR requirements).

### 8.2. Branch and Code

Typical Git workflow:

```bash
git checkout develop
git pull
git checkout -b feature/<short-description>
```

Then:

- Edit code under `src/` and configs under `config/`.
- Use the IDE rules (Cursor/Windsurf) for architecture guardrails.
- Run tests or targeted checks as needed (see `CONTRIBUTING.md`).

### 8.3. Test Changes with Real Calls

For telephony-facing changes (providers, pipelines, tools):

- Make at least one real call that exercises your change.
- Use:

  ```bash
  agent rca
  ```

  and, if needed:

  ```bash
  ./scripts/rca_collect.sh
  ```

- Save notes on:
  - Call IDs
  - Observed behavior
  - Any tuning you applied

These details are great material for your PR description.

---

## 9. Preparing a Pull Request

Before you open a PR:

- Ensure:
  - Code builds and basic tests pass.
  - At least one test call has been made for telephony changes.
  - Relevant docs are updated (Architecture, Roadmap, Tool Calling, provider/tool-specific guides).
- In your PR description:
  - Describe the feature or fix.
  - Reference any Linear issue IDs (e.g., `AAVA-63`) if applicable.
  - Include a brief test summary:
    - Calls placed (IDs).
    - `agent check` / `agent rca` results.

For branching and PR details, also see `CONTRIBUTING.md`.

---

## 10. Where to Go Next

- For architecture details:
  - `docs/contributing/architecture-deep-dive.md`
- For roadmap and milestones:
  - `docs/ROADMAP.md`
  - `docs/contributing/milestones/`
- For tools and integrations:
  - `docs/TOOL_CALLING_GUIDE.md`
  - `docs/ASTERISK_QUEUE_SETUP.md`
- For community-requested 4.2 features:
  - Join our [Discord server](https://discord.gg/ysg8fphxUe) for discussions

If you get stuck at any point, ask AVA in your AI IDE to:

- Explain the relevant part of the system.
- Suggest next steps.
- Help you prepare tests and a PR.
