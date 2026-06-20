# Admin UI Guide

The Admin UI is a web-based interface for configuring, monitoring, and managing your Asterisk AI Voice Agent deployment.

## Quick Start

```bash
# Start the Admin UI container
docker compose -p asterisk-ai-voice-agent up -d --build --force-recreate admin_ui

# Access at:
#   Local:  http://localhost:3003
#   Remote: http://<server-ip>:3003
```

**First login**: a one-time password is printed to the admin_ui logs on first start (`docker compose -p asterisk-ai-voice-agent logs admin_ui | grep -i password`); you must change it at first login. `admin`/`admin` no longer works.

## Pages Overview

### Dashboard

The dashboard shows a live system topology with:
- Container status (ai_engine, local_ai_server, admin_ui)
- Asterisk ARI connection status (green/red pill)
- Active provider and pipeline information
- CPU, memory, and disk usage

Clickable cards navigate directly to the relevant settings pages.

### Setup Wizard

The wizard walks you through initial configuration:
1. **Provider selection** — Choose your AI provider (OpenAI, Deepgram, Google, ElevenLabs, Local Hybrid)
2. **API key entry** — Enter and validate your provider credentials
3. **Transport selection** — AudioSocket (default) or ExternalMedia RTP
4. **Test** — Verify the configuration produces a healthy engine

The wizard writes to `config/ai-agent.local.yaml` (operator overrides), so upstream updates to the base config never conflict.

### Configuration

#### Providers
Configure full-agent providers and their settings (model, voice, API version, etc.). Each provider card shows its current status.

#### Pipelines
Configure modular STT/LLM/TTS pipelines for mix-and-match provider combinations.

#### Contexts
Named personas with custom greetings, system prompts, and audio profiles. Use contexts to create different agent personalities for different phone numbers or departments.

#### Audio Profiles
Transport and codec settings per context. Profiles like `telephony_ulaw_8k`, `openai_realtime_24k`, and `wideband_pcm_16k` control how audio is encoded and transmitted.

#### Tools
Enable/disable AI-powered actions (transfers, hangup, email, voicemail) and configure tool-specific settings.

### Call History

Per-call debugging and analytics:
- Searchable list of all calls with timestamps, duration, and provider
- Full conversation transcripts
- Tool call history with parameters and results
- Call quality metrics

Use Call History as the primary debugging tool — it provides more context than raw logs.

### Live Logs

WebSocket-based real-time log streaming from `ai_engine`. Filter by log level or search for specific call IDs.

### YAML Editor

Monaco-based editor with syntax highlighting and validation for direct editing of `config/ai-agent.yaml` and `config/ai-agent.local.yaml`.

### Environment Variables

Visual editor for `.env` variables. Changes require a container restart to take effect.

### System

#### Asterisk
Live ARI connection status, required Asterisk module checklist, configuration audit with guided fix commands. Supports both local and remote Asterisk deployments.

#### Containers
Start, stop, restart, and rebuild Docker containers directly from the UI.

#### Models
Browse, download, and manage local STT, TTS, and LLM models. Tabs split the catalog by type (`STT`, `TTS`, `LLM`) plus an `Installed` view that lists what's already on disk. The catalog itself lives in `admin_ui/backend/api/models_catalog.py` and ships with ~250 entries across the three types; new entries land via PRs and CI verifies every download URL is reachable on each PR plus weekly on `main` (see `scripts/check_catalog_urls.py`).

The top of the page exposes the **Local AI Server** card with three pickers — STT, LLM, TTS — that show the currently-loaded model and let you switch the engine to any installed file. Switching applies the change to `.env` and reloads the engine in place; the running engine recovers from the swap without dropping calls already in progress.

Downloaded models live under `models/<type>/` on the host (mounted into the container at `/app/models/<type>/`). The Installed tab counts files matching the catalog; deleting via the trash icon removes the file plus any sidecars (`.json` config for Piper, `.sha256` integrity sidecar).

##### Community Models (best-effort)
Operators can add LLM, TTS, or STT models that aren't in the curated catalog by pasting a HuggingFace download URL. Useful for trying new GGUF releases (Qwen3-4B, SmolLM2 variants, etc.) before they land in the official catalog.

**Off by default.** Toggle on via the "Community Models" card at the bottom of the Models page; the choice persists to `.env` as `ENABLE_CUSTOM_MODELS=true`. When disabled, the panel collapses to a one-line note and no community entries appear in the main lists.

When enabled:
1. Click **+ Add custom model**. The form requires a Type (LLM/TTS/STT), Display name, and HTTPS Download URL; optional fields are Config URL (for Piper TTS), Chat format hint (for LLM, e.g. `chatml` / `llama-3` / `mistral-instruct`), Expected SHA256 (verified after download), and free-form Notes.
2. The entry appears immediately in the relevant tab (STT/TTS/LLM) with a yellow **Community** badge alongside curated entries.
3. Click **Download** on the entry as you would a curated model. Progress polls in the existing download UI.
4. After download, expand the entry's chevron in the Community Models panel to **Inspect GGUF header** (LLM only): architecture, parameter count, quantization, context length, layer count, file size, and an estimated RAM requirement are surfaced. Architectures not in the verified-supported list (`llama`, `qwen2`, `qwen3`, `phi3`, `phi4`, `gemma`, `gemma2`, `mistral`, `mixtral`, `tinyllama`, etc.) get a yellow warning rather than blocking the download.
5. Switch the engine to the new model via the LLM picker at the top of the page (a page reload may be needed for the picker to see the new file).
6. The trash icon removes both the JSON entry and the on-disk file (plus all sidecars).

The "best-effort" framing is genuine: catalog URL liveness, GGUF magic verification, file size and RAM estimation are checked, but the model isn't actually loaded as part of validation. If a community model fails to load or behaves badly at runtime, please open a GitHub issue.

**Storage**:
- Entries: `data/custom_models.json` (gitignored, persists across container rebuilds and upgrades)
- Files: `models/<type>/custom_<type>_<slug>__<basename>` — the `custom_…__` prefix is namespaced with the unique entry id so two community entries with the same upstream filename can never collide on disk.

**Security**:
- HTTPS-only download URLs (rejects `http://` to prevent SSRF via redirect)
- Path-traversal blocked on every endpoint that touches a model file
- The `/delete-file` endpoint refuses to act on community-model files (community models must go through `DELETE /api/custom-models/{id}` so JSON and disk stay in sync)

## Per-Instance Provider Credentials (v6.5.2)

Full-agent provider forms (Grok, OpenAI Realtime, Deepgram, Google Live, ElevenLabs Agent) include a uniform **Provider Credentials** card. Paste the API key (or upload service-account JSON for Google Vertex) directly into the card; the Admin UI writes it to a per-instance file under `/app/project/secrets/providers/<provider_key>/` on the server (chmod 0600). The provider's `api_key_file` / `agent_id_file` / `credentials_path` field in YAML is updated automatically — no manual editing required, no secrets stored in `.env` or YAML.

This is the canonical credential storage path for multi-instance deployments (e.g. `acme_grok` and `globex_grok` each with isolated keys). Legacy single-instance configs that set `XAI_API_KEY` / `OPENAI_API_KEY` / etc. in `.env` continue to work as a fallback.

The **System → Environment** page includes a "Per-Instance Provider Credentials" status section listing every configured provider's credential file presence. Lets operators audit which instances have credentials on disk without SSH access.

## System Topology (v6.5.2)

The dashboard System Topology card uses tri-state health indicators (`Checking…` / healthy / error) with a 2-strike debounce: a single failed probe does not immediately flip a dot red. This eliminates the false-positive red flashes that previously appeared during engine warmup or transient localhost probe timeouts.

- ARI, AI Engine, and Local AI Server each get the tri-state + debounce treatment
- Per-provider readiness uses the same 2-strike pattern so providers start at `Checking…` instead of red on first paint
- Backend probe timeouts raised (`ai_engine` `/health` connect 1.5s → 5s; `local_ai_server` WebSocket open_timeout 2.5s → 5s)
- Provider cards are grouped by provider type with multi-instance sub-rows, so two `*_grok` or `*_google_live` instances render as the same provider kind with separate readiness dots
- Asterisk + AI Engine cards stretch to match the Providers column height; Models live AI Server output in a 3-column responsive grid

## Help tooltips (v6.5.2)

~260 inline help tooltips were backfilled across the admin UI in v6.5.2 — provider forms (Grok 17, OpenAI Realtime 24, Deepgram 22, ElevenLabs 10, Local 30, Google Live 29, Azure 21, OpenAI 17, Telnyx 7, Ollama 6), Setup Wizard (26 fields), LLM/MCP/Profiles/Models pages. The `HelpTooltip` component is viewport-aware: it measures the trigger and flips the popover from above to below when the icon is near the top of a scrolled modal, so the content stays visible.

## Security

The Admin UI has Docker socket access for container management. Treat it as a control plane with elevated privileges.

**Production requirements**:
- `admin`/`admin` is removed: retrieve the one-time password from the admin_ui logs (`docker compose -p asterisk-ai-voice-agent logs admin_ui | grep -i password`) and set a new one at first login
- Set `JWT_SECRET` in `.env` (preflight generates this automatically)
- Restrict port 3003 via firewall, VPN, or reverse proxy
- Never expose directly to the internet without authentication

See [SECURITY.md](../SECURITY.md) section 2.1 for detailed Admin UI security guidance including nginx reverse proxy configuration.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Can't access UI | Verify container is running: `docker compose ps admin_ui` |
| Login fails | Check `JWT_SECRET` is set in `.env`. Reset by deleting `data/users.json` and restarting. |
| Config changes not taking effect | Click "Restart" on the affected container, or run `docker compose restart ai_engine` |
| Dashboard shows disconnected | Check that `ai_engine` container is running and ARI credentials in `.env` are correct |
| Stale data after update | Hard refresh (Ctrl+Shift+R) to clear cached frontend assets |
