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

**Default login**: `admin` / `admin` — change this immediately in production.

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

## Security

The Admin UI has Docker socket access for container management. Treat it as a control plane with elevated privileges.

**Production requirements**:
- Change the default `admin` / `admin` credentials immediately
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
