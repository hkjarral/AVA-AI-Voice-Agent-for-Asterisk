<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner_dark_mode.png?v=9">
  <source media="(prefers-color-scheme: light)" srcset="assets/banner_light_mode.png?v=9">
  <img alt="Asterisk AI Voice Agent" src="assets/banner_light_mode.png?v=9" width="100%">
</picture>

![Version](https://img.shields.io/badge/version-6.4.2-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Docker](https://img.shields.io/badge/docker-compose-blue.svg)
![Asterisk](https://img.shields.io/badge/asterisk-18+-orange.svg)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/hkjarral/Asterisk-AI-Voice-Agent)
[![Discord](https://dcbadge.limes.pink/api/server/ysg8fphxUe?style=plastic)](https://discord.gg/ysg8fphxUe)
<br>
<a href="https://www.producthunt.com/products/ava-ai-voice-agent-for-asterisk?embed=true&amp;utm_source=badge-featured&amp;utm_medium=badge&amp;utm_campaign=badge-ava-ai-voice-agent-for-asterisk" target="_blank" rel="noopener noreferrer"><img alt="AVA - AI Voice Agent for Asterisk - Open-source AI voice agent for any phone system | Product Hunt" width="250" height="54" src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1120145&amp;theme=light&amp;t=1775845744279"></a>

The most powerful, flexible open-source AI voice agent for Asterisk/FreePBX. Featuring a **modular pipeline architecture** that lets you mix and match STT, LLM, and TTS providers, plus **6 production-ready golden baselines** validated for enterprise deployment.

[Quick Start](#-quick-start) • [Features](#-features) • [Roadmap](docs/ROADMAP.md) • [Demo](#-demo) • [Docs](docs/README.md) • [Community](#-community)

</div>

---

## 📖 Table of Contents

- [🚀 Quick Start](#-quick-start)
- [🎉 What's New](#-whats-new-in-v642)
- [🌟 Why Asterisk AI Voice Agent?](#-why-asterisk-ai-voice-agent)
- [✨ Features](#-features)
- [🎥 Demo](#-demo)
- [🛠️ AI-Powered Actions](#-ai-powered-actions)
- [🩺 Agent CLI Tools](#-agent-cli-tools)
- [⚙️ Configuration](#-configuration)
- [🏗️ Project Architecture](#-project-architecture)
- [📊 Requirements](#-requirements)
- [🗺️ Documentation](#-documentation)
- [🤝 Contributing](#-contributing)
- [💬 Community](#-community)
- [📝 License](#-license)

---

## 🚀 Quick Start

Get the **Admin UI running in 2 minutes**.

For a complete **first successful call** walkthrough (dialplan + transport selection + verification), see:
- **[Installation Guide](docs/INSTALLATION.md)**
- **[Transport Compatibility](docs/Transport-Mode-Compatibility.md)**

### 1. Run Pre-flight Check (Required)

```bash
# Clone repository
git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent

# Run preflight with auto-fix (creates .env, generates JWT_SECRET)
sudo ./preflight.sh --apply-fixes
```

> **Important:** Preflight creates your `.env` file and generates a secure `JWT_SECRET`. Always run this first!

### 2. Start the Admin UI

```bash
# Start the Admin UI container
docker compose -p asterisk-ai-voice-agent up -d --build --force-recreate admin_ui
```

### 3. Access the Dashboard

Open in your browser:
- **Local:** `http://localhost:3003`
- **Remote server:** `http://<server-ip>:3003`

**Default Login:** `admin` / `admin`

Follow the **Setup Wizard** to configure your providers and make a test call.

> ⚠️ **Security:** The Admin UI is accessible on the network. **Change the default password immediately** and restrict port 3003 via firewall, VPN, or reverse proxy for production use.

### 4. Verify Installation

> **GPU users:** If you have an NVIDIA GPU for local AI inference, see **[docs/LOCAL_ONLY_SETUP.md](docs/LOCAL_ONLY_SETUP.md)** for the GPU compose overlay (`docker-compose.gpu.yml`) before building.

```bash
# Start ai_engine (required for health checks)
docker compose -p asterisk-ai-voice-agent up -d --build ai_engine

# Check ai_engine health
curl http://localhost:15000/health
# Expected: {"status":"healthy"}

# View logs for any errors
docker compose -p asterisk-ai-voice-agent logs ai_engine | tail -20
```

### 5. Connect Asterisk

The wizard will generate the necessary dialplan configuration for your Asterisk server.

Transport selection is configuration-dependent (not strictly “pipelines vs full agents”). Use the validated matrix in:
- **[docs/Transport-Mode-Compatibility.md](docs/Transport-Mode-Compatibility.md)**

---

## 🔧 Advanced Setup (CLI)

For users who prefer the command line or need headless setup.

### Option A: Interactive CLI
```bash
./install.sh
agent setup
```

> Note: Legacy commands `agent init`, `agent doctor`, and `agent troubleshoot` remain available as hidden aliases in CLI v6.4.0.

### Option B: Manual Setup
```bash
# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Start services
docker compose -p asterisk-ai-voice-agent up -d
```

### Configure Asterisk Dialplan
Add this to your FreePBX (`extensions_custom.conf`):
```asterisk
[from-ai-agent]
exten => s,1,NoOp(Asterisk AI Voice Agent)
 ; Optional per-call overrides:
 ; - AI_PROVIDER selects a provider/pipeline (otherwise uses default_provider from ai-agent.yaml)
 ; - AI_CONTEXT selects a context/persona (otherwise uses default context)
 same => n,Set(AI_PROVIDER=google_live)
 same => n,Set(AI_CONTEXT=sales-agent)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```
Notes:
- `AI_PROVIDER` is optional. If unset, the engine follows normal precedence (context provider → default_provider).
- `AI_CONTEXT` is optional. Use it to change greeting/persona without changing your default provider/pipeline.
- See `docs/FreePBX-Integration-Guide.md` for channel variable precedence and examples.

### Test Your Agent
**Health check:**
```bash
agent check
```

**View logs:**
```bash
docker compose -p asterisk-ai-voice-agent logs -f ai_engine
```

---

## 🎉 What's New in v6.4.2

<details open>
<summary><b>Latest Updates</b></summary>

### 🗓️ Microsoft Calendar — Outlook / Microsoft 365 integration (NEW, v6.4.2)
- **Device-code OAuth** for V1 (one work/school account per deployment) — no public redirect URL required, runs entirely from the Tools UI **Connect** button
- **Native Microsoft Graph free/busy** by default with a working-hours mask (Mon–Fri 09:00–17:00, configurable) — operators don't need to seed "Open" availability events
- **Per-context account binding** via `contexts.<name>.tool_overrides.microsoft_calendar.selected_accounts`
- **Server-side delete fallback** absorbs LLMs that hallucinate or omit event_ids on reschedule (validated across Google Live, Deepgram, OpenAI Realtime, ElevenLabs)
- Personal Outlook.com and tenant-wide application permissions are intentionally out of scope for V1
- Setup guide: [docs/Microsoft-calendar-tool.md](docs/Microsoft-calendar-tool.md)

### 📅 Google Calendar — major overhaul (v6.4.2)
- **Multi-account / per-context binding** (#338): single deployments can serve multiple separate calendars (one per business line / agent persona); each Admin UI Context binds to exactly one calendar; cross-calendar `aggregate_mode` for shared availability use cases
- **JSON upload + auto-discover**: per-row "📁 Upload JSON" button writes the SA file to `secrets/`, authenticates, lists shared calendars, auto-fills credentials path / calendar id / timezone — eliminates the SCP-and-paste workflow for the 90% case
- **Domain-Wide Delegation** support: optional `subject` per calendar enables Workspace impersonation; UI exposes the SA `client_id` (the #1 setup pitfall) with copy-to-clipboard for paste-into-admin-console
- **Tools UI polish**: `free_prefix` / `busy_prefix` / `min_slot_duration_minutes` now exposed as form fields (were YAML-only); per-calendar 🩺 **Verify access** button with distinct, actionable error codes (`forbidden_calendar`, `calendar_not_found`, `auth_failed`, `dwd_not_configured`, etc.)
- **Native free/busy mode**: blank/absent `free_prefix` switches `get_free_slots` to Google's `freebusy.query()` API intersected with a working-hours mask
- **Slot-count cap** (default 3, configurable) so the agent stops reading 20-slot lists; **event duration cap** (default 240 min) prevents 7-hour bookings from LLM math errors

### 🎯 Reschedule reliability across all providers (v6.4.2)
- Server-side `event_id` tracking + 400/404 fallback eliminates duplicate bookings caused by LLMs hallucinating opaque IDs across conversation turns
- Date/time prompt placeholders (`{today}`, `{current_date}`, `{current_weekday}`, `{current_time}`, `{current_datetime_iso}`) injected per-call so models stop reasoning with stale years (real bug observed: local_hybrid model thinking it's 2023, ElevenLabs/Claude saying "Tuesday April 27" when April 27 2026 is a Monday)

### 🔧 Reliability fixes (v6.4.2)
- **OpenAI Realtime — duplicate events (3x) on fast tools**: race condition between fast tool execution (~300–500ms) and `response.done` commit caused the LLM to retry the booking. Fixed by gating `function_call_output` on a per-`response_id` async event
- **Per-context `tool_overrides` now actually take effect** on OpenAI Realtime, Deepgram, and Google Live (was silently ignored — only ElevenLabs honored it). Affected `selected_calendars`, custom transfer destinations, webhook URLs, etc.
- **Google Live — 30-voice catalog** (#349): voice picker expanded from 8 hardcoded voices to all 30 native-audio voices with Google's official tone descriptors

### ⚡ CPU Latency Optimization — Streaming LLM→TTS Overlap (v6.4.1)
- **Sentence-by-sentence streaming**: LLM tokens are streamed and split at sentence boundaries — each sentence is synthesized and played immediately instead of waiting for the full response. Reduces perceived latency from 3-10s to sub-2s on pipeline configurations.
- **Pipeline filler audio**: Instant acknowledgment phrase (e.g. "One moment please.") plays in the agent's own voice before LLM starts thinking. Configurable phrases via Admin UI.
- **Qwen 2.5-1.5B Instruct recommended for CPU**: New 940MB model achieves ~15-30 tok/s on CPU (vs Phi-3's ~0.8 tok/s). Setup Wizard auto-recommends with "⚡ CPU Recommended" badge.
- **Direct PCM→µ-law conversion**: Eliminates temp WAV file roundtrip in all 5 TTS backends (10-50ms saved per response).
- **Preflight hardened**: Buildx detection, RAM/disk/network checks, GPU install gated behind `--apply-fixes`, all runtime ports validated.

### 📞 Attended Transfer Streaming & Screening (v6.4.0)
- **Three screening modes**: `basic_tts` (caller ID announcement), `ai_briefing` (experimental AI conversation summary), `caller_recording` (records caller stating name/reason)
- **Streaming delivery**: ExternalMedia RTP helper eliminates shared storage dependency for transfer announcements
- **Provider-agnostic tool guidance**: Dynamically exposes configured transfer targets to LLM providers, preventing hallucinated extensions
- **Live Agents UI**: Redesigned compact layout with auto-polling for agent availability

### 🗣️ Russian Speech Backends (v6.4.0)
- **Sherpa Offline STT**: VAD-gated offline transducer mode with Silero VAD, configurable thresholds, preroll padding, and debug diagnostics. Set `SHERPA_MODEL_TYPE=offline` to enable.
- **T-one STT**: Native Russian telephony ASR using streaming CTC pipeline with beam search/greedy decoding. Requires `--build-arg INCLUDE_TONE=true`.
- **Silero TTS**: Multi-language TTS (ru, en, de, es, fr, ua) with native 8kHz telephony output and multiple speakers. Requires `--build-arg INCLUDE_SILERO=true`.

### 🎧 Admin UI (v6.4.0)
- **Fullscreen panels**: Maximize/minimize toggle for System Topology, Call Statistics, and Call History with Escape key support
- **Conversation timestamps**: Per-message timestamps in Call Log UI with LLM payload sanitization
- **HTTP tool wildcards**: JSONPath `[*]` array extraction now works correctly in output variables

### Previously in v6.3.2
- Microsoft Azure Speech Service STT & TTS pipeline adapters
- MiniMax LLM M2.7 via OpenAI-compatible API
- Call Recording Playback in Admin UI
- Azure SSRF prevention, PII logging discipline

For full release notes, see [CHANGELOG.md](CHANGELOG.md).

</details>

<details>
<summary><b>Previous Versions</b></summary>

#### v6.3.1 - Local AI Server & Guardrails
- Backend enable/rebuild flow, model lifecycle UX, GPU ergonomics, CPU-first onboarding
- Structured local tool gateway, hangup guardrails, tool-call parsing robustness
- `agent check --local` / `--remote` CLI verification

#### v6.1.1 - Operator Config & Live Agent Transfer
- Operator config overrides (`ai-agent.local.yaml`), live agent transfer tool
- ViciDial compatibility, Asterisk config discovery in Admin UI
- OpenAI Realtime GA API, Email system overhaul, NAT/GPU support

#### v5.3.1 - Phase Tools & Stability
- Pre-call HTTP lookups, in-call HTTP tools, and post-call webhooks (Milestone 24)
- Deepgram Voice Agent language configuration
- ExternalMedia RTP greeting cutoff fix

#### v4.4.3 - Cross-Platform Support
- **🌍 Pre-flight Script**: System compatibility checker with auto-fix mode.
- **🔧 Admin UI Fixes**: Models page, providers page, dashboard improvements.
- **🛠️ Developer Experience**: Code splitting, ESLint + Prettier.

#### v4.4.2 - Local AI Enhancements
- **🎤 New STT Backends**: Kroko ASR, Sherpa-ONNX.
- **🔊 Kokoro TTS**: High-quality neural TTS.
- **🔄 Model Management**: Dynamic backend switching from Dashboard.
- **📚 Documentation**: LOCAL_ONLY_SETUP.md guide.

#### v4.4.1 - Admin UI
- **🖥️ Admin UI**: Modern web interface (http://localhost:3003).
- **🎙️ ElevenLabs Conversational AI**: Premium voice quality provider.
- **🎵 Background Music**: Ambient music during AI calls.

#### v4.3 - Complete Tool Support & Documentation
- **🔧 Complete Tool Support**: Works across ALL pipeline types.
- **📚 Documentation Overhaul**: Reorganized structure.
- **💬 Discord Community**: Official server integration.

#### v4.2 - Google Live API & Enhanced Setup
- **🤖 Google Live API**: Gemini 2.0 Flash integration.
- **🚀 Interactive Setup**: `agent init` wizard (`agent quickstart` remains available for backward compatibility).

#### v4.1 - Tool Calling & Agent CLI
- **🔧 Tool Calling System**: Transfer calls, send emails.
- **🩺 Agent CLI Tools**: `doctor`, `troubleshoot`, `demo`.

</details>

---

## 🌟 Why Asterisk AI Voice Agent?

| Feature | Benefit |
|---------|---------|
| **Asterisk-Native** | Works directly with your existing Asterisk/FreePBX - no external telephony providers required. |
| **Truly Open Source** | MIT licensed with complete transparency and control. |
| **Modular Architecture** | Choose cloud, local, or hybrid - mix providers as needed. |
| **Production-Ready** | Battle-tested baselines with Call History-first debugging. |
| **Cost-Effective** | Local Hybrid costs ~$0.001-0.003/minute (LLM only). |
| **Privacy-First** | Keep audio local while using cloud intelligence. |

---

## ✨ Features

### 6 Golden Baseline Configurations

1. **OpenAI Realtime** (Recommended for Quick Start)
   - Modern cloud AI with natural conversations (<2s response).
   - Config: `config/ai-agent.golden-openai.yaml`
   - *Best for: Enterprise deployments, quick setup.*

2. **Deepgram Voice Agent** (Enterprise Cloud)
   - Advanced Think stage for complex reasoning (<3s response).
   - Config: `config/ai-agent.golden-deepgram.yaml`
   - *Best for: Deepgram ecosystem, advanced features.*

3. **Google Live API** (Multimodal AI)
   - Gemini Live (Flash) with multimodal capabilities (<2s response).
   - Config: `config/ai-agent.golden-google-live.yaml`
   - *Best for: Google ecosystem, advanced AI features.*

4. **ElevenLabs Agent** (Premium Voice Quality)
   - ElevenLabs Conversational AI with premium voices (<2s response).
   - Config: `config/ai-agent.golden-elevenlabs.yaml`
   - *Best for: Voice quality priority, natural conversations.*

5. **Local Hybrid** (Privacy-Focused)
   - Local STT/TTS + Cloud LLM (OpenAI). Audio stays on-premises.
   - Config: `config/ai-agent.golden-local-hybrid.yaml`
   - *Best for: Audio privacy, cost control, compliance.*

6. **Telnyx AI Inference** (Cost-Effective Multi-Model)
   - Local STT/TTS + Telnyx LLM with 53+ models (GPT-4o, Claude, Llama).
   - OpenAI-compatible API with competitive pricing.
   - Config: `config/ai-agent.golden-telnyx.yaml`
   - *Best for: Model flexibility, cost optimization, multi-provider access.*

### Additional LLM Providers

- **MiniMax LLM** (High-Performance Cost-Effective)
   - Local STT/TTS + MiniMax M2.7 LLM with enhanced reasoning and coding.
   - OpenAI-compatible API with tool-calling support.
   - Models: `MiniMax-M2.7` (default, latest flagship), `MiniMax-M2.7-highspeed` (low-latency), `MiniMax-M2.5`, `MiniMax-M2.5-highspeed`.
   - Activate: set `MINIMAX_API_KEY` in `.env`, then configure `providers.minimax_llm` in `config/ai-agent.yaml` (see the `minimax_llm` section with `enabled: true`).
   - *Best for: Long-context conversations, cost-effective high-performance LLM.*

### Fully Local (Optional)

AVA also supports a **Fully Local** mode (100% on-premises, no cloud APIs). Three topologies are supported:

| Topology | Latency | Best For |
|----------|---------|----------|
| **CPU-Only** | 5-15s/turn | Privacy, testing |
| **GPU (same box)** | 0.5-2s/turn | Production local |
| **Split-Server** (remote GPU) | 1-3s/turn | PBX on VPS + GPU box |

GPU setup uses `docker-compose.gpu.yml` overlay with CUDA-enabled llama.cpp. Community-validated: RTX 4090 achieves ~1.0s E2E.

- See: **[docs/LOCAL_ONLY_SETUP.md](docs/LOCAL_ONLY_SETUP.md)** (canonical guide for all local topologies)
- Hardware guidance: **[docs/HARDWARE_REQUIREMENTS.md](docs/HARDWARE_REQUIREMENTS.md)**

### 🏠 Self-Hosted LLM with Ollama (No API Key Required)

Run your own local LLM using [Ollama](https://ollama.ai) - perfect for privacy-focused deployments:

```yaml
# In ai-agent.yaml
active_pipeline: local_hybrid
pipelines:
  local_hybrid:
    stt: local_stt
    llm: ollama_llm
    tts: local_tts
```

**Features:**

- **No API key required** - fully self-hosted on your network
- **Tool calling support** with compatible models (Llama 3.2, Mistral, Qwen)
- Local Vosk STT + Your Ollama LLM + Local Piper TTS
- Complete privacy - all processing stays on-premises

**Requirements:**

- Mac Mini, gaming PC, or server with Ollama installed
- 8GB+ RAM (16GB+ recommended for larger models)
- See [docs/OLLAMA_SETUP.md](docs/OLLAMA_SETUP.md) for setup guide

**Recommended Models:**

| Model | Size | Tool Calling |
|-------|------|--------------|
| `llama3.2` | 2GB | ✅ Yes |
| `mistral` | 4GB | ✅ Yes |
| `qwen2.5` | 4.7GB | ✅ Yes |

### Technical Features

- **Tool Calling System**: AI-powered actions (transfers, emails) work with any provider.
- **Agent CLI Tools**: `setup`, `check`, `rca`, `update`, `version` commands (legacy aliases: `init`, `doctor`, `troubleshoot`).
- **Modular Pipeline System**: Independent STT, LLM, and TTS provider selection.
- **Dual Transport Support**: AudioSocket (default in `config/ai-agent.yaml`) and ExternalMedia RTP (both supported — see the transport matrix).
- **Streaming-First Downstream**: Streaming playback when possible, with automatic fallback to file playback for robustness.
- **High-Performance Architecture**: Separate `ai_engine` and `local_ai_server` containers.
- **Observability**: Built-in **Call History** for per-call debugging + optional `/metrics` scraping.
- **State Management**: SessionStore for centralized, typed call state.
- **Barge-In Support**: Interrupt handling with configurable gating.

### 🖥️ Admin UI

Modern web interface for configuration and system management.

**Quick Start:**
```bash
docker compose -p asterisk-ai-voice-agent up -d --build --force-recreate admin_ui
# Access at: http://localhost:3003
# Login: admin / admin (change immediately!)
```

**Key Features:**
- **Setup Wizard**: Visual provider configuration.
- **Dashboard**: Real-time system metrics, container status, and Asterisk connection indicator.
- **Asterisk Setup**: Live ARI status, module checklist, config audit with guided fix commands.
- **Live Logs**: WebSocket-based log streaming.
- **YAML Editor**: Monaco-based editor with validation.

---

## 🎥 Demo

[![Watch the demo](https://img.youtube.com/vi/fDZ_yMNenJc/hqdefault.jpg)](https://youtu.be/fDZ_yMNenJc "Asterisk AI Voice Agent v6.1 Deep Dive")

### 📞 Try it Live! (US Only)

Experience our production-ready configurations with a single phone call:

**Dial: (925) 736-6718**

- **Press 5** → Google Live API (Multimodal AI with Gemini 2.0)
- **Press 6** → Deepgram Voice Agent (Enterprise cloud with Think stage)
- **Press 7** → OpenAI Realtime API (Modern cloud AI, most natural)
- **Press 8** → Local Hybrid Pipeline (Privacy-focused, audio stays local)
- **Press 9** → ElevenLabs Agent (Santa voice with background music)
- **Press 10** → Fully Local Pipeline (100% on-premises, CPU-based)

---

## 🛠️ AI-Powered Actions

Your AI agent can perform real-world telephony actions through tool calling.

### Unified Call Transfers

```text
Caller: "Transfer me to the sales team"
Agent: "I'll connect you to our sales team right away."
[Transfer to sales queue with queue music]
```

**Supported Destinations:**
- **Extensions**: Direct SIP/PJSIP endpoint transfers.
- **Queues**: ACD queue transfers with position announcements.
- **Ring Groups**: Multiple agents ring simultaneously.

### Call Control & Voicemail

- **Cancel Transfer**: "Actually, cancel that" (during ring).
- **Hangup Call**: Ends call gracefully with farewell.
- **Voicemail**: Routes to voicemail box.

### Email Integration

- **Automatic Call Summaries**: Admins receive full transcripts and metadata.
- **Caller-Requested Transcripts**: "Email me a transcript of this call."

| Tool | Description | Status |
|------|-------------|--------|
| `transfer` | Transfer to extensions, queues, or ring groups | ✅ |
| `cancel_transfer` | Cancel in-progress transfer (during ring) | ✅ |
| `hangup_call` | End call gracefully with farewell message | ✅ |
| `leave_voicemail` | Route caller to voicemail extension | ✅ |
| `send_email_summary` | Auto-send call summaries to admins | ⚙️ Disabled by default |
| `request_transcript` | Caller-initiated email transcripts | ⚙️ Disabled by default |

### HTTP Tools (Pre/In/Post-Call) Example

```yaml
# In ai-agent.yaml
tools:
  pre_call_lookup:
    kind: generic_http_lookup
    phase: pre_call
    enabled: true
    is_global: false
  post_call_webhook:
    kind: generic_webhook
    phase: post_call
    enabled: true
    is_global: false

in_call_tools:
  intent_router:
    kind: in_call_http_lookup
    enabled: true
    is_global: false

contexts:
  default:
    pre_call_tools:
      - pre_call_lookup
    tools:
      - intent_router
      - hangup_call
    post_call_tools:
      - post_call_webhook
```

---

## 🩺 Agent CLI Tools

Production-ready CLI for operations and setup.

**Installation:**
```bash
curl -sSL https://raw.githubusercontent.com/hkjarral/Asterisk-AI-Voice-Agent/main/scripts/install-cli.sh | bash
```

**Commands:**
```bash
agent setup               # Interactive setup wizard (recommended)
agent check               # Standard diagnostics report (share this output when asking for help)
agent check --local       # Verify local AI server (STT, LLM, TTS) on this host
agent check --remote <ip> # Verify local AI server on a remote GPU machine
agent update              # Pull latest code + rebuild/restart as needed
agent rca --call <call_id> # Post-call RCA (use Call History to find call_id)
agent version             # Version information
```

---

## ⚙ Configuration

### Three-File Configuration
- **[`config/ai-agent.yaml`](config/ai-agent.yaml)** - Golden baseline configs (git-tracked, upstream-managed).
- **`config/ai-agent.local.yaml`** - Operator overrides (git-ignored). Any keys here are deep-merged on top of the base file at startup; all Admin UI and CLI writes go here so upstream updates never conflict.
- **[`.env`](.env.example)** - Secrets and API keys (git-ignored).

**Example `.env`:**
```bash
OPENAI_API_KEY=sk-your-key-here
DEEPGRAM_API_KEY=your-key-here
ASTERISK_ARI_USERNAME=asterisk
ASTERISK_ARI_PASSWORD=your-password
```

### Optional: Metrics (Bring Your Own Prometheus)
The engine exposes Prometheus-format metrics at `http://<engine-host>:15000/metrics`.
Per-call debugging is handled via **Admin UI → Call History**.

---

## 🏗 Project Architecture

Two-container architecture for performance and scalability:

1. **`ai_engine`** (Lightweight orchestrator): Connects to Asterisk via ARI, manages call lifecycle.
2. **`local_ai_server`** (Optional): Runs local STT/LLM/TTS models (Vosk, Faster Whisper, Whisper.cpp, Sherpa, Kroko, Piper, Kokoro, MeloTTS, llama.cpp).

```mermaid
graph LR
    A[Asterisk Server] <-->|ARI, RTP| B[ai_engine]
    B <-->|API| C[AI Provider]
    B <-->|WS| D[local_ai_server]
    
    style A fill:#f9f,stroke:#333,stroke-width:2px
    style B fill:#bbf,stroke:#333,stroke-width:2px
    style C fill:#bfb,stroke:#333,stroke-width:2px
    style D fill:#fbf,stroke:#333,stroke-width:2px
```

---

## 📊 Requirements

### Platform Requirements

| Requirement | Details |
|-------------|---------|
| **Architecture** | x86_64 (AMD64) only |
| **OS** | Linux with systemd |
| **Supported Distros** | Ubuntu 20.04+, Debian 11+, RHEL/Rocky/Alma 8+, Fedora 38+, Sangoma Linux |

> **Note:** ARM64 (Apple Silicon, Raspberry Pi) is not currently supported. See [Supported Platforms](docs/SUPPORTED_PLATFORMS.md) for the full compatibility matrix.

### Minimum System Requirements

| Type | CPU | RAM | GPU | Disk |
|------|-----|-----|-----|------|
| **Cloud** (OpenAI/Deepgram) | 2+ cores | 4GB | None | 1GB |
| **Local Hybrid** (cloud LLM) | 4+ cores | 8GB+ | None | 2GB |
| **Fully Local** (CPU) | 4+ cores (2020+) | 8-16GB | None | 5GB |
| **Fully Local** (GPU) | 4+ cores | 8-16GB | RTX 3060+ | 10GB |

### Software Requirements

- Docker + Docker Compose v2
- Asterisk 18+ with ARI enabled
- FreePBX (recommended) or vanilla Asterisk

### Preflight Automation

The `preflight.sh` script handles initial setup:
- Seeds `.env` from `.env.example` with your settings
- Prompts for Asterisk config directory location
- Sets `ASTERISK_UID`/`ASTERISK_GID` to match host permissions (fixes media access issues)
- Re-running preflight often resolves permission problems

---

## 🗺 Documentation

### Getting Started
- **[Docs Index](docs/README.md)**
- **[FreePBX Integration Guide](docs/FreePBX-Integration-Guide.md)**
- **[Installation Guide](docs/INSTALLATION.md)**

### Configuration & Operations
- **[Configuration Reference](docs/Configuration-Reference.md)**
- **[Transport Compatibility](docs/Transport-Mode-Compatibility.md)**
- **[Tuning Recipes](docs/Tuning-Recipes.md)**
- **[Supported Platforms](docs/SUPPORTED_PLATFORMS.md)**
- **[Local Profiles](docs/LOCAL_PROFILES.md)**
- **[Monitoring Guide](docs/MONITORING_GUIDE.md)**

### Development & Community
- **[Roadmap](docs/ROADMAP.md)** - What's next, planned milestones, and how to get involved
- **[Developer Documentation](docs/contributing/README.md)**
- **[Architecture Deep Dive](docs/contributing/architecture-deep-dive.md)**
- **[Contributing Guide](CONTRIBUTING.md)**
- **[Milestone History](docs/MILESTONE_HISTORY.md)** - Completed milestones 1-24

---

## 🤝 Contributing

**You don't need to know how to code.** Our AI assistant AVA writes the code for you — just describe what you want to build.

<!-- TODO: Add YouTube video link once recorded -->
<!-- **Watch the 5-minute walkthrough:** [YouTube Video](https://youtube.com/...) -->

### 🚀 Get Started in 3 Steps

```bash
git clone -b develop https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent
./scripts/setup-contributor.sh
```

Then open in [Windsurf](https://codeium.com/windsurf) and type: **"I want to contribute"**

### 📖 Guides

| Guide | For |
|-------|-----|
| **[Operator Contributor Guide](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/docs/contributing/OPERATOR_CONTRIBUTOR_GUIDE.md)** | First-time contributors (no GitHub experience needed) |
| **[Contributing Guide](CONTRIBUTING.md)** | Full contribution guidelines and workflow |
| **[Coding Guidelines](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/docs/contributing/CODING_GUIDELINES.md)** | Code standards for all contributions |
| **[Roadmap](docs/ROADMAP.md)** | What to work on next (13+ beginner-friendly tasks) |

### 🔧 Build Something New

| Area | Guide | Template |
|------|-------|----------|
| Full Agent Provider | [Guide](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/docs/contributing/adding-full-agent-provider.md) | [Template](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/examples/providers/template_full_agent.py) |
| Pipeline Adapter (STT/LLM/TTS) | [Guide](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/docs/contributing/adding-pipeline-adapter.md) | [Templates](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/tree/develop/examples/pipelines/) |
| Pre-Call Hook | [Guide](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/docs/contributing/pre-call-hooks-development.md) | [Template](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/examples/hooks/template_pre_call_hook.py) |
| In-Call Hook | [Guide](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/docs/contributing/in-call-hooks-development.md) | [Template](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/examples/hooks/template_in_call_hook.py) |
| Post-Call Hook | [Guide](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/docs/contributing/post-call-hooks-development.md) | [Template](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/examples/hooks/template_post_call_hook.py) |

### 👩‍💻 For Developers
- [Developer Onboarding](docs/DEVELOPER_ONBOARDING.md) - Project overview and first tasks
- [Developer Quickstart](docs/contributing/quickstart.md) - Set up your dev environment
- [Developer Documentation](docs/contributing/README.md) - Full contributor docs

### Contributors

<table>
<tr>
<td align="center"><a href="https://github.com/hkjarral"><img src="https://github.com/hkjarral.png" width="60" alt="hkjarral"><br><sub><b>hkjarral</b></sub></a><br>Architecture, Code</td>
<td align="center"><a href="https://github.com/a692570"><img src="https://github.com/a692570.png" width="60" alt="a692570"><br><sub><b>Abhishek</b></sub></a><br>Telnyx LLM Provider</td>
<td align="center"><a href="https://github.com/turgutguvercin"><img src="https://github.com/turgutguvercin.png" width="60" alt="turgutguvercin"><br><sub><b>turgutguvercin</b></sub></a><br>NumPy Resampler</td>
<td align="center"><a href="https://github.com/Scarjit"><img src="https://github.com/Scarjit.png" width="60" alt="Scarjit"><br><sub><b>Scarjit</b></sub></a><br>Code</td>
<td align="center"><a href="https://github.com/egorky"><img src="https://github.com/egorky.png" width="60" alt="egorky"><br><sub><b>egorky</b></sub></a><br>Azure STT/TTS Provider</td>
</tr>
<tr>
<td align="center"><a href="https://github.com/alemstrom"><img src="https://github.com/alemstrom.png" width="60" alt="alemstrom"><br><sub><b>alemstrom</b></sub></a><br>Docs — PBX Setup</td>
<td align="center"><a href="https://github.com/gcsuri"><img src="https://github.com/gcsuri.png" width="60" alt="gcsuri"><br><sub><b>gcsuri</b></sub></a><br>Code — Google Calendar</td>
<td align="center"><a href="https://github.com/octo-patch"><img src="https://github.com/octo-patch.png" width="60" alt="octo-patch"><br><sub><b>octo-patch</b></sub></a><br>MiniMax LLM Provider</td>
<td align="center"><a href="https://github.com/neilruaro-camb"><img src="https://github.com/neilruaro-camb.png" width="60" alt="neilruaro-camb"><br><sub><b>neilruaro-camb</b></sub></a><br>CAMB AI TTS Provider</td>
<td align="center"><a href="https://github.com/aoi-dev-0411"><img src="https://github.com/aoi-dev-0411.png" width="60" alt="aoi-dev-0411"><br><sub><b>aoi-dev-0411</b></sub></a><br>Transcript Search, Health Badges</td>
</tr>
</table>

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for the full list and [Recognition Program](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/develop/docs/contributing/RECOGNITION.md) for how we recognize contributions.

---

## 💬 Community

- **[Discord Server](https://discord.gg/ysg8fphxUe)** - Support and discussions
- [GitHub Issues](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues) - Bug reports
- [GitHub Discussions](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/discussions) - General chat

---

## 📝 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## 💖 Support This Project

Asterisk AI Voice Agent is **free and open source**. If it's saving you money, consider supporting development:

<p align="center">
  <a href="https://github.com/sponsors/hkjarral">
    <img src="https://img.shields.io/badge/Sponsor_on_GitHub-❤️-pink?logo=github&style=for-the-badge" alt="GitHub Sponsors">
  </a>
  <a href="https://ko-fi.com/asteriskaivoiceagent">
    <img src="https://img.shields.io/badge/Buy_me_a_coffee-☕-yellow?logo=ko-fi&style=for-the-badge" alt="Ko-fi">
  </a>
  <a href="https://meetify.com/aava1">
    <img src="https://img.shields.io/badge/Book_Consultation-📅-blue?style=for-the-badge" alt="Book Consultation">
  </a>
</p>

Your support funds:
- 🐛 Faster bug fixes and issue responses  
- ✨ New provider integrations and features  
- 📚 Better documentation and tutorials

If you find this project useful, please also give it a ⭐️!

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=hkjarral/Asterisk-AI-Voice-Agent&type=date&legend=top-left)](https://www.star-history.com/#hkjarral/Asterisk-AI-Voice-Agent&type=date&legend=top-left)
