# Asterisk AI Voice Agent - Installation Guide (v5.1.4)

This guide provides detailed instructions for setting up the Asterisk AI Voice Agent v5.1.4 on your server.

## Three Setup Paths

Choose the path that best fits your experience level:

## Upgrade from v4.6.0 → v5.1.4 (Existing Checkout)

This section is for operators upgrading an existing repo checkout (not a fresh install).

### 0) Backup (recommended)

- Backup `.env`
- Backup `config/ai-agent.yaml`
- If you rely on Call History persistence, backup `./data` as well

### 1) Pull the new release

Once `v5.1.4` is published:

```bash
git fetch --tags
git checkout v5.1.4
```

If you track branches instead of tags:

```bash
git checkout main
git pull
```

If you have the CLI installed, this is equivalent (and safer for common operator workflows):

```bash
agent update
```

### 2) Re-run preflight (recommended)

```bash
sudo ./preflight.sh --apply-fixes
```

If preflight reports warnings or failures, resolve them first, then re-run preflight until it returns clean:
- Troubleshooting: `docs/TROUBLESHOOTING_GUIDE.md`
- Re-run: `sudo ./preflight.sh --apply-fixes`
- Verify: `agent check`

### 3) Upgrade checklist (4.5.3 → 4.6.0)

- `.env`:
  - Review ARI settings: `ASTERISK_ARI_PORT`, `ASTERISK_ARI_SCHEME`, `ASTERISK_ARI_SSL_VERIFY`
  - If using rootless Docker/Podman, set a persistent `DOCKER_SOCK=...` in `.env` (not only `export ...`)
- Admin UI “save vs apply”:
  - `.env` edits from the UI may normalize quoting and remove duplicate keys; this is expected in 4.6+
- OpenAI Realtime:
  - Baseline includes a small audio output tweak; validate your call quality if you customized encoding/sample-rate

### 4) Rebuild and recreate containers

```bash
docker compose up -d --build --force-recreate admin_ui ai_engine
```

If your configuration requires local inference:

```bash
docker compose up -d --build --force-recreate local_ai_server
```

### 5) Verify

```bash
curl -sS http://localhost:15000/health
agent check
```

> ⚠️ **Operator note (production hardening):** `ai_engine` exposes a health/metrics server on port `15000`.
> In the default compose, it binds to `0.0.0.0` so `admin_ui` can reach it reliably on best-effort hosts.
> For production, restrict access via firewall/VPN/reverse proxy, or bind it to localhost by setting:
> - `HEALTH_BIND_HOST=127.0.0.1` in `.env` (then `docker compose up -d --force-recreate ai_engine`)
> - Optional: set `HEALTH_API_TOKEN` in `.env` if you need authenticated remote access.

### Path A: Admin UI Setup Wizard (Recommended)

**5-minute visual setup** with the new web-based Admin UI:

```bash
git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent

# Run preflight (REQUIRED - creates .env, generates JWT_SECRET)
sudo ./preflight.sh --apply-fixes

# Start Admin UI first
docker compose up -d admin_ui

# Complete the Setup Wizard in Admin UI, then start ai_engine
docker compose up -d ai_engine
```

If you hit permission/container/health issues during setup, start with:
- `agent check`
- `sudo ./preflight.sh --apply-fixes`
- `docs/TROUBLESHOOTING_GUIDE.md`

**Access the Admin UI:**
- **Local:** `http://localhost:3003`
- **Remote server:** `http://<server-ip>:3003`

> ⚠️ **Security:** The Admin UI is accessible on the network by default.  
> **Change the admin password on first login** and restrict port 3003 (firewall/VPN/reverse proxy) for production.

The Setup Wizard will:
1. ✅ Guide you through provider selection (OpenAI, Deepgram, Google, ElevenLabs, Local)
2. ✅ Validate your API keys with live testing
3. ✅ Test Asterisk ARI connection
4. ✅ Configure contexts and greeting
5. ✅ Start containers automatically

**Default Login:** `admin` / `admin` (must be changed on first login)

**Best for:** First-time users, production deployments, visual configuration

See [Admin UI Setup Guide](../admin_ui/UI_Setup_Guide.md) for detailed instructions.

---

### Path B: CLI Quickstart (Alternative)

**Command-line wizard** for terminal-based setup:

```bash
git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent

	# Run installer
	./install.sh

	# Run CLI wizard
	agent setup
	```

**Best for:** Headless servers, scripted deployments, CLI preference

	> Note: `agent quickstart` and `agent init` are still available for backward compatibility, but `agent setup` is the recommended CLI wizard for v5.1.4.

---

### Path C: Manual Setup (Advanced Users)

**Traditional installer** with manual configuration:

```bash
git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent
./install.sh
```

The installer will:
1. Guide you through **3 baseline choices** (a fast-path subset):
   - **OpenAI Realtime** - Fastest (0.5-1.5s), requires OPENAI_API_KEY
   - **Deepgram Voice Agent** - Enterprise (1-2s), requires DEEPGRAM_API_KEY + OPENAI_API_KEY
   - **Local Hybrid** - Privacy-focused (3-7s), requires OPENAI_API_KEY + 8GB RAM
2. Validate ARI connection with your Asterisk server
3. Prompt for required API keys
4. Offer CLI tool installation
5. Start Docker containers automatically

**Best for:** Advanced users, custom configurations, specific requirements

If you want to use additional providers (e.g., Google Live, ElevenLabs) or switch between multiple golden configs, use the Admin UI Setup Wizard (Path A) or edit `config/ai-agent.yaml` directly.

Notes:
- The project ships **5 golden baseline configs** under `config/ai-agent.golden-*.yaml`.
- A **Fully Local** mode is also supported (100% on-premises), but requires stronger hardware for local LLM inference; see `docs/LOCAL_ONLY_SETUP.md` and `docs/HARDWARE_REQUIREMENTS.md`.

**Local note:** This project does **not** bundle models in images. For recommended local build/run profiles (including a smaller `local-core` build), see `docs/LOCAL_PROFILES.md`.

**Kroko note:** `INCLUDE_KROKO_EMBEDDED` is off by default to keep the local-ai-server image lighter. Enable it only if you need embedded Kroko (see `docs/LOCAL_PROFILES.md`).

**Container OS note:** `admin_ui` and `ai_engine` ship on Debian `bookworm` (Python `3.11`). `local_ai_server` ships on Debian `trixie` intentionally (for embedded Kroko glibc compatibility).

---

## Detailed Installation

For manual installation, custom configurations, or troubleshooting, continue below.

## 1. Prerequisites

Before you begin, ensure your system meets the following requirements:

- **Operating System**: A modern Linux distribution (e.g., Ubuntu 20.04+, CentOS 7+).
- **Asterisk**: Version 18 or newer. FreePBX 15+ is also supported.
- **ARI (Asterisk REST Interface)**: Enabled and configured on your Asterisk server.
- **Docker**: Latest stable version of Docker and Docker Compose. Podman is community-supported (aliased as `docker`) but not officially tested.
- **Git**: Required to clone the project repository.
- **Network Access**: Your server must be able to make outbound connections to the internet for Docker image downloads and API access to AI providers.

### Prerequisite checks

- Verify required Asterisk modules are loaded:

  ```bash
  asterisk -rx "module show like res_ari_applications"
  asterisk -rx "module show like app_audiosocket"
  ```

  Expected example output:

  ```
  Module                         Description                               Use Count  Status   Support Level
  res_ari_applications.so        RESTful API module - Stasis application   0          Running  core
  1 modules loaded

  Module                         Description                               Use Count  Status   Support Level
  app_audiosocket.so             AudioSocket Application                    20         Running  extended
  1 modules loaded
  ```

  If Asterisk < 18, on FreePBX Distro run:

  ```bash
  asterisk-switch-version   # aka asterisk-version-switch
  ```

  and select Asterisk 18+.

- Quick install Docker
  - Ubuntu:

    ```bash
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker $USER && newgrp docker
    docker --version && docker compose version
    ```

- Debian (11/12):

    ```bash
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg

    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    sudo usermod -aG docker $USER && newgrp docker
    docker --version && docker compose version
    ```

- CentOS/Rocky/Alma:

    ```bash
    sudo dnf -y install dnf-plugins-core
    sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    sudo dnf install -y docker-ce docker-ce-cli containerd.io
    sudo systemctl enable --now docker
    docker --version && docker compose version
    ```

### Rootless Docker (best-effort)

If your host uses **rootless Docker**, the Admin UI needs the rootless socket mounted. Set `DOCKER_SOCK` before starting `admin_ui`:

```bash
export DOCKER_SOCK=/run/user/$(id -u)/docker.sock
docker compose up -d --force-recreate admin_ui
```

`./preflight.sh` prints the exact command for your system when it detects rootless Docker.

## 2. Installation Steps

The installation is handled by an interactive script that will guide you through the process.

### Step 2.1: Clone the Repository

First, clone the project repository to a directory on your server.

```bash
git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent
```

### Step 2.2: Run the Installation Script

Execute the `install.sh` script. You will need to run it with `sudo` if your user does not have permissions to run Docker.

```bash
./install.sh
```

The script will perform the following actions:

1. **System Checks**: Verify that Docker is installed and running.
2. **Interactive Setup**: Launch a wizard to collect configuration details.

### Step 2.3: Interactive Setup Wizard

The wizard will prompt you for the following information.

#### AI Provider Selection

You will be asked to choose an AI provider.

- **[1] OpenAI Realtime**: Out-of-the-box realtime voice path (cloud).
- **[2] Deepgram Voice Agent**: Cloud STT/TTS with strong latency/quality.
- **[3] Local Hybrid**: Local STT/TTS + cloud LLM (audio stays local).

#### Provider Configuration

Based on your selection, you will need to provide API keys.

- **Deepgram API Key**: Required if you select the Deepgram provider.
- **OpenAI API Key**: Required if you select any OpenAI-based pipeline.

#### Asterisk ARI Configuration

You will need to provide the connection details for your Asterisk server's ARI.

- **Asterisk Host**: The hostname or IP address of your Asterisk server.
- **ARI Username**: The username for an ARI user.
- **ARI Password**: The password for the ARI user.

### What You'll Need (at a glance)

- A Linux server with Docker + Docker Compose
- Asterisk 18+ or FreePBX 15+ with ARI enabled
- API keys for your chosen provider (optional): `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`

### Step 2.4: Configuration File Generation

After you complete the wizard, the script will create a `.env` file in the project root with all your settings. You can manually edit this file later if you need to make changes.

### Step 2.5: Start the Service

Once the configuration is complete, the script will prompt you to build and start the Docker container. You can also do this manually.

```bash
docker compose up --build -d
```

> IMPORTANT: First startup time (local models)
>
> If you selected a Local or Hybrid workflow, the `local-ai-server` may take 15–20 minutes on first startup to load LLM/TTS models depending on your CPU, RAM, and disk speed. This is expected and readiness may show degraded until models have fully loaded. Monitor with:
>
> ```bash
> docker compose logs -f local_ai_server
> ```
>
> Subsequent restarts are typically much faster due to OS page cache. If startup is too slow for your hardware, consider using MEDIUM or LIGHT tier models and update the `.env` model paths accordingly.

## 3. Verifying the Installation

After starting the service, you can check that it is running correctly.

### Check Docker Container Status

```bash
docker compose ps
```

You should see the `ai_engine` container running, and `local_ai_server` if your selected configuration requires local STT/LLM/TTS.

## First Successful Call (Canonical Checklist)

This section is designed to remove ambiguity between “containers started” and a **working phone call**.

### 1) Confirm engine health

```bash
curl http://localhost:15000/health
```

Expected: `{"status":"healthy"}`

### 2) Confirm ARI connectivity

In `ai_engine` logs, look for indicators that ARI is reachable and authenticated.

```bash
docker compose logs -f ai_engine
```

If ARI is not reachable, verify `.env` values and that Asterisk ARI is enabled:
- `ASTERISK_HOST`
- `ASTERISK_ARI_USERNAME`
- `ASTERISK_ARI_PASSWORD`

### 3) Choose transport using the validated compatibility matrix

Transport selection depends on your chosen provider mode and playback method.

Use the validated combinations in:
- **[Transport & Playback Mode Compatibility Guide](Transport-Mode-Compatibility.md)**

### 4) Configure Asterisk dialplan and reload

Add the minimal Stasis dialplan in **[5. Configure Asterisk Dialplan](#5-configure-asterisk-dialplan)** below, then reload your dialplan:

```bash
asterisk -rx "dialplan reload"
```

### 5) Place a test call and verify expected outcomes

Expected outcomes:
- You hear a greeting.
- The call appears in **Admin UI → Call History** (if enabled in your config/release).
- `ai_engine` logs show the call entering Stasis and starting the configured transport.

If you get “greeting only” or “no audio”, jump to:
- **[Transport Compatibility](Transport-Mode-Compatibility.md)**
- **[Troubleshooting Guide](TROUBLESHOOTING_GUIDE.md)**

### Check Container Logs

```bash
docker compose logs -f ai_engine
```

Look for a message indicating a successful connection to Asterisk ARI and that the engine is ready to start the selected transport.

For transport-specific expectations (AudioSocket vs ExternalMedia RTP), see:
- **[Transport & Playback Mode Compatibility Guide](Transport-Mode-Compatibility.md)**

### 5. Configure Asterisk Dialplan

The engine uses **ARI-based architecture** - the dialplan just hands calls to Stasis. The engine manages audio transport internally.

**Minimal Dialplan** (works for all supported modes):

Add to `/etc/asterisk/extensions_custom.conf`:

```asterisk
[from-ai-agent]
exten => s,1,NoOp(Asterisk AI Voice Agent v5.1.4)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

**Optional: Provider Override via Channel Variables**:

```asterisk
[from-ai-agent-support]
exten => s,1,NoOp(AI Agent - Customer Support)
 same => n,Set(AI_PROVIDER=deepgram)
 same => n,Set(AI_CONTEXT=support)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

[from-ai-agent-openai]
exten => s,1,NoOp(AI Agent - OpenAI Realtime)
 same => n,Set(AI_PROVIDER=openai_realtime)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

**Important:** Do NOT use `AudioSocket()` in the dialplan. The engine originates AudioSocket channels via ARI automatically.

**How It Works:**
1. Call enters `Stasis(asterisk-ai-voice-agent)`
2. Engine receives StasisStart event via ARI
3. Engine starts the configured transport and playback mode for that call
4. Engine bridges the transport channel with the caller
5. Two-way audio flows automatically

For validated transport/playback combinations, see:
- **[Transport & Playback Mode Compatibility Guide](Transport-Mode-Compatibility.md)**

After adding the dialplan, reload Asterisk configuration:

```bash
asterisk -rx "dialplan reload"
```

## 4. Troubleshooting

- **Cannot connect to ARI**:
  - Verify that your Asterisk `host`, `username`, and `password` are correct in the `.env` file.
  - Ensure that the ARI port (usually 8088) is accessible from the Docker container.
  - Check your `ari.conf` and `http.conf` in Asterisk.
- **AI does not respond**:
  - Check that your API keys in the `.env` file are correct.
- **Audio Quality Issues**:
  - Confirm AudioSocket is connected (see Asterisk CLI and `ai_engine` logs).
  - Use a tmpfs/SSD for the media volume (default: `./asterisk_media` on host, mounted as `/mnt/asterisk_media` in `ai_engine`) to minimize I/O latency for file-based playback.
  - Verify you are not appending file extensions to ARI `sound:` URIs (Asterisk will add them automatically).

- **No host Python 3 installed (scripts/Makefile)**:
  - The Makefile auto-falls back to running helper scripts inside the `ai_engine` container. You’ll see a hint when it does.
  - Check your environment:

        ```bash
        make check-python
        ```

  - Run helpers directly in the container if desired:

        ```bash
        docker compose exec -T ai_engine python /app/scripts/validate_externalmedia_config.py
        docker compose exec -T ai_engine python /app/scripts/test_externalmedia_call.py
        docker compose exec -T ai_engine python /app/scripts/monitor_externalmedia.py
        docker compose exec -T ai_engine python /app/scripts/capture_test_logs.py --duration 40
        docker compose exec -T ai_engine python /app/scripts/analyze_logs.py /app/logs/latest.json
        ```

For more advanced troubleshooting, refer to the project's main `README.md` or open an issue in the repository.
