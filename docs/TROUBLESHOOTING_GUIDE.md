# Troubleshooting Guide

Complete guide to diagnosing and fixing issues with Asterisk AI Voice Agent.

## Table of Contents

- [Installation](#installation)
- [Quick Diagnostics](#quick-diagnostics)
- [Common Issues](#common-issues)
- [Troubleshooting Tools](#troubleshooting-tools)
- [Symptom-Based Diagnosis](#symptom-based-diagnosis)
- [Log Analysis](#log-analysis)
- [Provider-Specific Issues](#provider-specific-issues)
- [Performance Issues](#performance-issues)
- [Network Issues](#network-issues)
- [IPv6 (GA policy)](#ipv6-ga-policy)
- [Getting Help](#getting-help)

---

## Installation

The `agent` CLI tools are available as pre-built binaries for easy installation (v4.1+).

### Quick Install (Linux/macOS)

```bash
curl -sSL https://raw.githubusercontent.com/hkjarral/Asterisk-AI-Voice-Agent/main/scripts/install-cli.sh | bash
```

This will:
- Auto-detect your platform
- Download the latest binary
- Verify checksums
- Install to `/usr/local/bin`

### Manual Installation

Download the appropriate binary for your platform from [GitHub Releases](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest):

**Linux:**
```bash
# Most servers (x86_64)
curl -L -o agent https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest/download/agent-linux-amd64
chmod +x agent
sudo mv agent /usr/local/bin/

# ARM64 (Raspberry Pi, AWS Graviton)
curl -L -o agent https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest/download/agent-linux-arm64
chmod +x agent
sudo mv agent /usr/local/bin/
```

**macOS:**
```bash
# Intel Macs
curl -L -o agent https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest/download/agent-darwin-amd64

# Apple Silicon (M1/M2/M3)
curl -L -o agent https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest/download/agent-darwin-arm64

chmod +x agent
sudo mv agent /usr/local/bin/
```

**Windows:**
Download `agent-windows-amd64.exe` from [releases](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest) and add to your PATH.

### Verify Installation

```bash
agent version
```

You should see:
```
Asterisk AI Voice Agent CLI
Version:    vX.Y.Z
Built:      YYYY-MM-DDTHH:MM:SSZ
Repository: https://github.com/hkjarral/Asterisk-AI-Voice-Agent
```

Note: The CLI binary and the Python engine may have different version strings depending on how the release was built.

### Available Tools

- **`agent setup`** - Interactive setup wizard (v5.2.5)
- **`agent check`** - Standard diagnostics report (v5.2.5)
- **`agent rca`** - Post-call root cause analysis (v5.2.5)
- **`agent update`** - Pull latest code + rebuild/restart as needed (v5.1+)

Legacy aliases (v5.2.5; hidden from `--help`):
- `agent init` → `agent setup`
- `agent doctor` → `agent check`
- `agent troubleshoot` → `agent rca`

---

## Quick Diagnostics

### Step 1: Run Health Check

```bash
agent check
```

This performs comprehensive system checks:
- ✅ Docker containers running
- ✅ Asterisk ARI connectivity
- ✅ AudioSocket/RTP availability
- ✅ Configuration validation
- ✅ Provider API connectivity
- ✅ Recent call history

**Exit codes:**
- `0` - All checks passed
- `1` - Warnings (non-critical issues)
- `2` - Failures (critical issues)

### Call History DB (if missing or empty)

Call History is stored in a SQLite DB under `./data` on the host (mounted into `ai_engine` as `/app/data`).

Quick checks:
```bash
ls -la ./data
docker compose logs ai_engine | grep -i \"call history\" | tail -n 20
```

Common fixes:
- Run `sudo ./preflight.sh --apply-fixes` (creates `./data` and `./models/*`, fixes permissions, applies SELinux contexts where applicable).
- Avoid non-local filesystems for `./data` (some NFS setups can break SQLite locking).

### Admin UI Shows “AI Engine/Local AI Server Error” But Containers Are Running (Tier 3 / Best-effort)

This is most common on Tier 3 hosts (Docker Desktop, Podman, unsupported distros) where:
- the Admin UI can’t reach the Docker API socket, and/or
- the Admin UI health probes are using URLs that aren’t reachable from inside the `admin_ui` container.

Quick checks:
```bash
docker compose ps
```

If container control (start/stop/restart) fails from the UI:
- Ensure the Docker socket is mounted and set in `.env` (varies by host):
  - Docker Desktop: `DOCKER_SOCK=/var/run/docker.sock`
  - Rootless Docker/Podman: often `DOCKER_SOCK=/run/user/<uid>/docker.sock`
- Then recreate the Admin UI container so the mount updates:
```bash
docker compose up -d --force-recreate admin_ui
```

If the UI shows a 500 error and `admin_ui` logs contain `PermissionError: [Errno 13] Permission denied` for `/var/run/docker.sock`:
- Your host’s `docker` group GID may not be `999` (Debian often differs), so the Admin UI user (UID 1000) can’t open the socket.
- Fix by setting `DOCKER_GID` to the socket’s group ID and recreating `admin_ui`:
```bash
ls -ln /var/run/docker.sock
DOCKER_GID=$(ls -ln /var/run/docker.sock | awk '{print $4}')
grep -qE '^[# ]*DOCKER_GID=' .env && sed -i.bak -E "s/^[# ]*DOCKER_GID=.*/DOCKER_GID=$DOCKER_GID/" .env || echo "DOCKER_GID=$DOCKER_GID" >> .env
docker compose up -d --force-recreate admin_ui
```

If containers are running but the UI shows “unreachable”:
- Set explicit health probe URLs in `.env` (values must be reachable from `admin_ui`):
```bash
HEALTH_CHECK_AI_ENGINE_URL=http://ai_engine:15000/health
HEALTH_CHECK_LOCAL_AI_URL=ws://local_ai_server:8765
```

Notes:
- The **Local AI Server is optional** unless you plan to use local STT/TTS models.
- If you run **bridge networking** and want Local AI Server reachable across containers, set:
  - `LOCAL_WS_HOST=0.0.0.0`
  - `LOCAL_WS_AUTH_TOKEN=...` (required; server refuses to start if exposed without auth)

### Step 2: Analyze Recent Call

```bash
agent rca
```

Automatically analyzes your most recent call with:
- Log collection and parsing (from Docker logs)
- Metrics extraction
- Format alignment check
- Baseline comparison
- AI-powered diagnosis

**How it works:**
- Reads logs directly from Docker: `docker logs ai_engine`
- Analyzes calls from last 24 hours
- No file logging required (LOG_TO_FILE not needed)
- Requires `ai_engine` container to be running
- Works with both console and JSON log formats

**Log Format Recommendation:**
For best troubleshooting results, use JSON format in `.env`:
```bash
LOG_FORMAT=json  # Recommended for structured analysis
```

Console format works too, but JSON provides:
- More reliable parsing (no ANSI color codes)
- Structured data for better analysis
- Easier field extraction

**Analyze most recent call:**
```bash
agent rca
```

**Advanced (legacy alias): list recent calls:**
```bash
agent troubleshoot --list
```

---

## Common Issues

### 0. Docker Build Fails (apt-get / DNS)

**Symptoms:** `docker compose up -d --build ai_engine` fails with errors like:
- `Temporary failure resolving 'deb.debian.org'`
- `E: Unable to locate package build-essential`

**Cause:** Docker/BuildKit can’t resolve DNS or doesn’t have outbound internet during image build. This is not related to your host Debian version (Debian inside the image can differ).

**Fix (recommended):**
```bash
# Pull latest fixes (pins the base image to Debian 12/bookworm)
git pull

# Rebuild the engine image
docker compose build --no-cache --pull ai_engine
docker compose up -d ai_engine
```

**If DNS is still failing inside Docker:**
```bash
# Quick DNS probe inside a container
docker run --rm busybox:1.36.1 nslookup deb.debian.org
```

If the DNS probe fails, set explicit DNS servers for Docker and restart it:
```bash
sudo mkdir -p /etc/docker
printf '{\"dns\":[\"1.1.1.1\",\"8.8.8.8\"]}\n' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
```

If you’re on a Debian/Ubuntu host using `systemd-resolved`, also confirm Docker isn’t inheriting a loopback resolver (e.g. `127.0.0.53`):
```bash
readlink -f /etc/resolv.conf
cat /etc/resolv.conf
```

### 1. No Audio (Complete Silence)

**Symptoms:** Neither caller nor agent can hear anything.

**Quick Check:**
```bash
agent rca
```

**Common Causes:**

#### Transport Configuration Issue
```bash
# Check transport mode
grep audio_transport config/ai-agent.yaml

# Check container logs for transport startup
docker logs ai_engine | grep -iE "transport|audiosocket|externalmedia"
```

**Fix:** Verify your transport matches your provider:
```yaml
# For full agents (Deepgram, OpenAI Realtime)
audio_transport: audiosocket
audiosocket:
  host: "0.0.0.0"
  port: 8090
  format: "ulaw"  # or "slin16"

# For pipelines (hybrid, local_only)
audio_transport: externalmedia
external_media:
  rtp_host: "0.0.0.0"
  rtp_port: 18080
  # Optional: allocate per-call RTP ports
  # port_range: "18080:18099"
```

#### Dialplan Not Passing to Stasis
**Check** your dialplan in `/etc/asterisk/extensions_custom.conf`:
```
[from-ai-agent]
exten => s,1,NoOp(AI Voice Agent)
 same => n,Answer()
 same => n,Stasis(asterisk-ai-voice-agent)  ; ← Must pass to Stasis app
 same => n,Hangup()
```

**Fix:** Ensure you're calling `Stasis(asterisk-ai-voice-agent)`, not `AudioSocket()`.  
The ai-engine creates AudioSocket/RTP channels automatically via ARI.

#### Container Not Running
```bash
docker ps | grep ai_engine
```

**Fix:** Start container:
```bash
docker compose up -d ai_engine
```

---

### 2. Garbled/Distorted Audio

**Symptoms:** Audio is fast, slow, choppy, robotic, or distorted.

**Quick Check:**
```bash
agent rca
```

**Common Causes:**

#### Audio Format Configuration
Check your transport format configuration.

**Check logs:**
```bash
docker logs ai_engine | grep -i "format\|transport"
```

**For AudioSocket transport (full agents):**
```yaml
audiosocket:
  format: "slin"  # PCM16 format
```

**For ExternalMedia RTP (pipelines):**  
Format is automatically managed based on provider configuration.

#### Jitter Buffer Underflows
**Symptoms:** Choppy, stuttering audio.

**Check logs:**
```bash
docker logs ai_engine | grep -i underflow
```

**Fix:** Increase buffer size in `config/ai-agent.yaml`:
```yaml
streaming:
  jitter_buffer_ms: 100  # Increase if underflows occur (default: 50)
```

#### Provider Bytes Pacing Bug
**Check with troubleshoot:**
```bash
agent rca
```

Look for: "Provider bytes ratio" should be `~1.0`.
- ❌ Ratio `<0.95` or `>1.05` = CRITICAL pacing bug

**Fix:** This usually indicates a code bug. Check:
- Provider output format matches expected
- No duplicate byte counting
- Streaming manager receiving correct byte counts

#### Sample Rate Mismatch
**Expected flow:**
- Asterisk → AudioSocket: 8kHz PCM16 (slin)
- ai-engine ↔ Provider: Provider's native rate
- ai-engine → Asterisk: 8kHz PCM16 (slin)

**Check config:**
```yaml
streaming:
  sample_rate: 8000  # Must be 8kHz for telephony
```

---

### 3. Echo (Agent Hears Itself)

**Symptoms:** Agent responds to its own output, creating confusion or loops.

**Quick Check:**
```bash
agent rca
```

**Common Causes:**

#### VAD Too Sensitive (OpenAI Realtime)
**CRITICAL SETTING** for OpenAI Realtime API:

```yaml
vad:
  webrtc_aggressiveness: 1  # NOT 0!
```

**Why:** Level 0 detects echo as "speech", causing gate flutter.

**Verify:**
```bash
docker logs ai_engine | grep "webrtc_aggressiveness"
```

**Expected:** `webrtc_aggressiveness=1`

#### Audio Gate Flutter
**Symptoms:** Gate opening/closing rapidly (50+ times per call).

**Check:**
```bash
agent rca
```

Look for: "Gate closures: XX"
- ✅ `<5` closures = Normal
- ⚠️ `5-20` closures = Elevated
- ❌ `>20` closures = Flutter (echo leakage)

**Fix:**
```yaml
vad:
  webrtc_aggressiveness: 1
  confidence_threshold: 0.6
  post_tts_end_protection_ms: 250  # Prevents premature reopening
```

#### Provider Echo Cancellation Not Working
**For OpenAI Realtime:** Has built-in server-side echo cancellation.
**Solution:** Let OpenAI handle it, keep local VAD at level 1.

**For Deepgram:** May need to adjust settings or use barge-in config.

---

### 4. Self-Interruption Loop

**Symptoms:** Agent cuts itself off mid-sentence repeatedly.

**Quick Check:**
```bash
agent rca
```

**Cause:** This is a variant of echo issue - agent hearing its own audio.

**Fix:** Same as Echo troubleshooting above:
1. Set `webrtc_aggressiveness: 1`
2. Increase `post_tts_end_protection_ms`
3. Check for gate flutter

---

### 5. One-Way Audio

**Symptoms:** Only caller OR only agent can be heard.

**Quick Check:**
```bash
agent rca
```

**Diagnose Direction:**

#### Caller Can't Hear Agent (TTS Issue)
**Check:**
```bash
docker logs ai_engine | grep -i "playback\|tts\|playing"
```

**No playback logs?**
- Provider API key invalid or missing
- TTS provider down or unreachable
- Format encoding issue (check transport mode)

**Fix:**
```bash
# Verify API keys in .env
grep -E "OPENAI_API_KEY|DEEPGRAM_API_KEY" .env

# Test provider connectivity
curl https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"
```

#### Agent Can't Hear Caller (STT Issue)
**Check:**
```bash
docker logs ai_engine | grep -i "transcript\|stt\|speech"
```

**No transcription logs?**
- Provider API key invalid
- AudioSocket not receiving audio
- Format mismatch preventing STT

**Fix:**
1. Verify API keys
2. Check AudioSocket connectivity
3. Verify format: `slin` at 8kHz

---

## Troubleshooting Tools

### agent check

**System health check and diagnostics.**

```bash
# Basic health check
agent check

# JSON output (for scripts)
agent check --json

# Verbose output
agent check -v
```

**What it checks:**
- Docker containers (ai-engine, local-ai-server, monitoring)
- Asterisk ARI (connectivity, authentication)
- AudioSocket (port 8090 availability)
- Configuration (YAML validation, required fields)
- Provider APIs (key validation, connectivity)
- Recent calls (last 24 hours)

**Exit Codes:**
- `0` = All checks passed
- `1` = Warnings detected
- `2` = Critical failures

**Use Cases:**
- Pre-flight checks before deployment
- CI/CD validation
- Post-deployment verification
- Scheduled monitoring

---

### agent rca

**Post-call analysis and root cause analysis.**

```bash
# Analyze most recent call
agent rca

# Analyze specific call
agent rca --call 1761424308.2043

# JSON output (JSON only)
agent rca --json

# Verbose output
agent rca -v
```

**What it analyzes:**
- **Call Logs:** Filters logs for specific call ID
- **Metrics:** Provider bytes, drift, underflows, SNR
- **Format Alignment:** AudioSocket, provider, frame sizes
- **VAD Settings:** Aggressiveness, thresholds
- **Audio Gating:** Gate closures, flutter detection
- **Baseline Comparison:** vs golden configs
- **Quality Score:** 0-100 based on metrics
- **LLM Diagnosis:** AI-powered root cause analysis

**Symptoms Supported:**
- `no-audio` - Complete silence
- `garbled` - Distorted/fast/slow audio
- `echo` - Agent hears itself
- `interruption` - Self-interruption loop
- `one-way` - Only one direction works

**Output Sections:**
1. Pipeline Status (AudioSocket, Transcription, Playback)
2. Audio Issues (underflows, format mismatches)
3. Errors & Warnings
4. Symptom Analysis (if specified)
5. Detailed Metrics (RCA-level)
6. Call Quality Verdict (0-100 score)
7. AI Diagnosis (if enabled)

Note: Advanced `agent troubleshoot` flags (list/symptoms/collect-only/etc.) still exist as a hidden legacy alias in v5.0, but `agent rca` is the recommended surface.

---

### agent demo

**Audio pipeline validation without making real calls.**

```bash
# Run basic validation
agent demo

# Use custom audio file
agent demo --wav /path/to/test.wav

# Run multiple iterations
agent demo --loop 5

# Save generated audio files
agent demo --save

# Verbose output
agent demo -v
```

**What it tests:**
- AudioSocket server connectivity
- Container health
- Configuration validation
- Provider API connectivity
- Audio processing pipeline

**Use Cases:**
- Pre-production validation
- CI/CD testing
- Configuration verification
- Provider API testing

---

### agent setup

**Interactive setup wizard.**

```bash
# Run setup wizard
agent setup

# Flags below are planned; they may exist but are not implemented in v5.2.5:
# agent setup --non-interactive
# agent setup --template <name>
```

**What it configures:**
- Asterisk ARI credentials
- Audio transport (AudioSocket/ExternalMedia)
- AI provider selection
- Pipeline configuration
- Configuration validation

---

## Debugging Tool Execution Issues

**Tool execution** allows AI agents to perform actions like call transfers, hangups, and sending emails. When tools don't work, follow this debugging workflow.

### Quick Diagnostics for Tool Issues

```bash
# 1. Collect standard diagnostics
agent check

# 2. Review most recent call for tool execution
agent rca

# 3. Look for tool-specific errors
docker logs ai_engine 2>&1 | grep -i "tool\|function"
```

### Common Tool Execution Problems

#### Tools Not Executing (Generic)

**Symptom**: AI mentions action ("I'll transfer you") but nothing happens.

**Diagnostic Steps**:

1. **Verify tools are configured:**
   ```bash
   grep -A 10 "tools:" config/ai-agent.yaml
   ```
   Should list enabled tools for your pipeline/provider.

2. **Check tool registration in logs:**
   ```bash
   docker logs ai_engine 2>&1 | grep "tools configured"
   ```
   Expected pattern:
   ```
   ✅ "OpenAI session configured with 6 tools"
   ✅ "Added tools to provider context: ['transfer', 'hangup_call', ...]"
   ```

3. **Look for tool invocation:**
   ```bash
   docker logs ai_engine 2>&1 | grep "function call\|tool call"
   ```
   Expected patterns:
   ```
   ✅ "OpenAI function call detected: hangup_call"
   ✅ "Tool hangup_call executed: success"
   ```

**If No Tool Registration**:
- Check `config/ai-agent.yaml` under your pipeline's `tools:` section
- Ensure tools are listed (e.g., `- hangup_call`, `- transfer`)
- Restart containers after config changes

#### OpenAI Realtime: Schema Format Error

**Symptom**: Error `Missing required parameter: 'session.tools[0].name'`

**Root Cause**: Using Chat Completions schema format instead of Realtime API format.

**Diagnostic**:
```bash
# Look for schema error
docker logs ai_engine 2>&1 | grep "missing_required_parameter"
```

**Fix**: This is a code issue (should be fixed in v4.2+). If you see this error:
- Verify you're on latest version: `agent update` (or `git pull origin main`)
- Check that `src/tools/adapters/openai.py` uses `to_openai_realtime_schema()`
- See [Common Pitfalls](contributing/COMMON_PITFALLS.md#pitfall-1-tool-schema-format-mismatch-openai-realtime) for details

#### Deepgram: Functions Not Being Called

**Symptom**: Deepgram configured but never calls functions.

**Diagnostic**:
```bash
# Check for FunctionCallRequest events
docker logs ai_engine 2>&1 | grep "FunctionCallRequest"
```

**Common Issues**:
- Using `agent.think.tools` instead of `agent.think.functions` (wrong field name)
- Event handler checking `"function_call"` instead of `"FunctionCallRequest"`

**Fix**: Verify code uses Deepgram-specific naming (fixed in v4.1+).

#### Hangup Tool: Call Doesn't Disconnect

**Symptom**: AI says "goodbye" but call stays connected.

**Diagnostic Steps**:

1. **Check if tool was invoked:**
   ```bash
   docker logs ai_engine 2>&1 | grep -i "hangup"
   ```
   Expected patterns:
   ```
   ✅ "Hangup requested"
   ✅ "Hangup tool executed"
   ✅ "Call will hangup after farewell"
   ```

2. **Check for execution errors:**
   ```bash
   docker logs ai_engine 2>&1 | grep -i "AttributeError\|hangup.*error"
   ```

**Common Issues**:
- Wrong ARI method (using `delete_channel` instead of `hangup_channel`)
- Missing farewell playback (call hangs up too quickly)
- Tool not registered with provider

**Workaround**: If urgent, caller can hang up manually.

#### Transfer Tool: Not Transferring

**Symptom**: AI says "transferring you" but call doesn't transfer.

**Diagnostic**:
```bash
# Check transfer execution
docker logs ai_engine 2>&1 | grep -i "transfer"
```

Expected patterns:
```
✅ "Transfer tool invoked"
✅ "Resolved destination: ringgroup 600"
✅ "Transfer initiated"
```

**Common Issues**:
- Destination not found (extension/queue/ringgroup doesn't exist in Asterisk)
- ARI redirect/continue failure
- Transfer active flag not set correctly

**Verification**:
```bash
# Check Asterisk for destination
asterisk -rx "core show hints" | grep <extension>
asterisk -rx "queue show <queue-name>"
```

### Using agent rca for Tool Issues

Use `agent rca` as the first stop for tool execution issues:

```bash
# Analyze most recent call (includes tool execution sections)
agent rca

# Look for these sections in output:
# 1. Tool Registration: "Tools configured: 6"
# 2. Tool Invocations: "function_call detected"
# 3. Tool Results: "executed: success" or "executed: failure"
# 4. Errors: AttributeError, missing methods, schema errors
```

### Expected Log Patterns for Successful Tool Execution

**OpenAI Realtime**:
```
[info] Added tools to provider context: ['transfer', 'cancel_transfer', 'hangup_call', ...]
[info] Generated OpenAI Realtime schemas for 6 tools
[info] OpenAI session configured with 6 tools
[info] OpenAI function call detected: hangup_call (call_id_...)
[info] Hangup requested: farewell="Thank you for calling!"
[info] Hangup tool executed - next response will trigger hangup
[info] HangupReady event received - executing hangup
```

**Deepgram Voice Agent**:
```
[info] Configured agent.think.functions for Deepgram
[info] FunctionCallRequest event received
[info] Function: transfer_call, parameters: {destination: 'sales'}
[info] Transfer tool executed: success
```

**Pipelines (OpenAI Chat Completions)**:
```
[info] LLM response contains tool_calls
[info] Tool call: hangup_call
[info] Executing tool via tool_registry
[info] Tool hangup_call executed: success
```

### Warning Patterns (Tool Issues)

```
⚠️ "AI used farewell phrase without invoking hangup_call tool"
   → Tool not being called by LLM

❌ "Missing required parameter: 'session.tools[0].name'"
   → Schema format mismatch (OpenAI Realtime)

❌ "AttributeError: 'Engine' object has no attribute 'app_config'"
   → Code bug in tool context creation

❌ "AttributeError: 'ARIClient' object has no attribute 'delete_channel'"
   → Wrong ARI method name
```

### Further Help

For detailed explanations of tool execution issues and fixes:
- **[Common Pitfalls - Tool Execution](contributing/COMMON_PITFALLS.md#tool-execution-issues)**
- **[Tool Development Guide](contributing/tool-development.md)**
- **[Tool Calling Guide](TOOL_CALLING_GUIDE.md)** (user perspective)

---

## Advanced (Legacy) Symptom Flags

`agent rca` is the recommended v5.0 surface. If you need symptom-focused heuristics, the hidden legacy alias `agent troubleshoot` supports:

```bash
agent troubleshoot --last --symptom <no-audio|garbled|echo|interruption|one-way>
```

---

## Log Analysis

### Manual Log Review

```bash
# Recent logs (last hour)
docker logs --since 1h ai_engine

# Follow logs in real-time
docker logs -f ai_engine

# Search for specific call
docker logs ai_engine | grep "1761424308.2043"

# Filter by level
docker logs ai_engine | grep ERROR
docker logs ai_engine | grep WARNING

# Search for specific issues
docker logs ai_engine | grep -i "underflow"
docker logs ai_engine | grep -i "format"
docker logs ai_engine | grep -i "error"
```

### Key Log Patterns

#### Successful Call Indicators
```
✅ "AudioSocket connection accepted"
✅ "Transcription:" or "transcript:"
✅ "Playback started" or "playing audio"
✅ "Provider bytes" ratio ~1.0
✅ Drift <10%
```

#### Problem Indicators
```
❌ "Connection refused" or "Connection failed"
❌ "Format mismatch" or "format error"
❌ "Underflow" (especially >50 per call)
❌ "Provider bytes" ratio <0.95 or >1.05
❌ Drift >10%
❌ Gate closures >20
```

### Log Levels

Adjust logging in `.env`:
```bash
LOG_LEVEL=debug    # Most verbose (use for troubleshooting)
LOG_LEVEL=info     # Default (recommended)
LOG_LEVEL=warning  # Quiet (only warnings and errors)
LOG_LEVEL=error    # Very quiet (only errors)

# Streaming-specific logging
STREAMING_LOG_LEVEL=debug  # Detailed streaming logs
```

---

## Provider-Specific Issues

### OpenAI Realtime

#### Common Issues

**1. WebRTC VAD Sample Rate Error**
```
ERROR: WebRTC VAD error - sample rate must be 8000, 16000, or 32000
```

**Cause:** OpenAI outputs 24kHz, incompatible with WebRTC VAD.

**Fix:** Not yet fixed - tracked in AAVA-27.

**2. Model Not Found**
```
ERROR: received 4000 (private use) invalid_request_error.missing_model
```

**Cause:** Wrong model specified for Realtime API.

**Fix:** Use correct model:
```yaml
providers:
  openai_realtime:
    model: "gpt-4o-realtime-preview-2024-12-17"  # NOT gpt-4o!
```

**3. Authentication Failed**
```
ERROR: 401 Unauthorized
```

**Fix:** Verify API key in `.env`:
```bash
OPENAI_API_KEY=sk-proj-...
```

### Deepgram Voice Agent

#### Common Issues

**1. Low RMS Warnings (Spam)**
```
WARNING: Low RMS level detected in audio
```

**Cause:** Deepgram API sensitivity - not actually a problem.

**Fix:** These warnings are suppressed by default. If seeing many:
- Check actual audio quality with test call
- Ignore if audio sounds good

**2. Connection Timeout**
```
ERROR: Deepgram connection timeout
```

**Fix:**
- Check API key: `grep DEEPGRAM_API_KEY .env`
- Verify network connectivity
- Check Deepgram service status

**3. Format Encoding Issues**
```
ERROR: Unsupported audio format
```

**Fix:** Verify config:
```yaml
providers:
  deepgram:
    encoding: "mulaw"  # or "linear16"
    sample_rate: 8000
```

### Local AI (Vosk + Phi-3 + Piper)

#### Common Issues

**1. Models Not Loading**
```
ERROR: Model file not found
```

**Fix:** Run model setup:
```bash
make model-setup
```

If you see permission errors (for example `PermissionError: [Errno 13] Permission denied` when the UI tries to download models), fix host mounts/permissions first:
```bash
sudo ./preflight.sh --apply-fixes
docker compose up -d --force-recreate local_ai_server
```

Or check specific paths in `.env`:
```bash
LOCAL_STT_MODEL_PATH=/app/models/stt/vosk-model-en-us-0.22
LOCAL_LLM_MODEL_PATH=/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf
LOCAL_TTS_MODEL_PATH=/app/models/tts/en_US-lessac-medium.onnx
```

**2. Slow LLM Responses (>10 seconds)**

**Cause:** CPU performance - Phi-3 needs modern hardware.

**Hardware Requirements:**
- CPU: 2020+ (Ryzen 9 5950X / i9-11900K or newer)
- RAM: 8GB+
- GPU: Optional (RTX 3060+) for faster inference

**Fix:**
- Reduce context: `LOCAL_LLM_CONTEXT=2048`
- Reduce max tokens: `LOCAL_LLM_MAX_TOKENS=32`
- Or switch to local_hybrid (local STT/TTS, cloud LLM)

**3. Container Restart Loop**
```
docker ps  # local_ai_server keeps restarting
```

**Check logs:**
```bash
docker logs local_ai_server
```

Common causes:
- Insufficient RAM (needs 8GB+)
- Missing model files
- Port conflict (8765)

---

## Performance Issues

### High Latency

**Symptoms:** >2 second delay between speech and response.

**Diagnose:**
```bash
agent rca
```

Look for:
- Provider API response times
- Network latency
- LLM generation time

**Fixes:**

#### Cloud Providers (OpenAI, Deepgram)
- Check network connectivity
- Verify API endpoints accessible
- Use geographically closer regions if available

#### Local AI
- Reduce LLM context size
- Reduce max_tokens
- Enable GPU acceleration (if available)
- Consider hybrid mode (cloud LLM only)

### High CPU/Memory Usage

**Check resource usage:**
```bash
docker stats ai_engine local_ai_server
```

**Normal Usage:**
- ai-engine: <20% CPU, <512MB RAM
- local_ai_server: 50-100% CPU (during inference), 4-8GB RAM

**High usage causes:**
- Multiple concurrent calls
- Large LLM models
- Debug logging enabled

**Fixes:**
- Scale horizontally (multiple containers)
- Use smaller models
- Reduce logging: `LOG_LEVEL=warning`
- Enable GPU acceleration

### Audio Quality Degradation

**Check metrics:**
```bash
agent rca
```

**Key Metrics:**
- **Drift:** Should be <10%
- **Underflows:** <1% of frames
- **Provider bytes ratio:** 0.99-1.01
- **Quality Score:** >70

**If score <70:**
1. Check format alignment
2. Increase jitter buffer
3. Verify network stability
4. Check provider API health

---

## Network Issues

### Connectivity Problems

#### Can't Reach Asterisk ARI
```bash
# Test ARI connectivity
curl -u asterisk:asterisk http://127.0.0.1:8088/ari/asterisk/info

# Container-side ARI probe (recommended in v5.0; avoids requiring curl/ping in ai_engine)
agent check
```

**Fix:** Update `.env`:
```bash
ASTERISK_HOST=127.0.0.1  # or remote IP/hostname
```

#### AudioSocket Port Not Accessible
```bash
# Check if port 8090 is listening
netstat -tuln | grep 8090

# Check firewall
sudo ufw status | grep 8090

# Test from Asterisk
telnet ai-engine-host 8090
```

**Fix:** Open firewall port:
```bash
sudo ufw allow 8090/tcp
```

#### Provider API Unreachable
```bash
# Test OpenAI
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"

# Test Deepgram
curl https://api.deepgram.com/v1/listen \
  -H "Authorization: Token $DEEPGRAM_API_KEY"
```

**Fix:**
- Check API keys
- Verify internet connectivity
- Check corporate firewall/proxy

### Docker Networking

#### Host Network (Default in `docker-compose.yml`)

Most deployments use host networking for telephony/low-latency behavior.

**Verify:**
```bash
docker compose ps
```

#### Bridge Network (Advanced / Optional)

If you run a custom bridge-network compose (not the default), port mappings are required:
```yaml
ports:
  - "8090:8090"      # AudioSocket
  - "18080:18080/udp"  # RTP
  - "15000:15000"    # Health
```

**Verify:**
```bash
docker ps | grep ai_engine
# Should show port mappings
```

**Security:** Bridge mode still requires strict firewall rules and allowlisting as appropriate for your deployment.

See: [docs/PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)

### IPv6 (GA policy)

For GA stability, AAVA treats IPv6 as **best-effort**.

If IPv6 is enabled but not fully functional in your environment, you may see intermittent DNS/connectivity issues (especially in host-network Docker setups).

**Recommendation (host-level):** disable IPv6 on the host running AAVA.

Temporary (until reboot):
```bash
sudo sysctl -w net.ipv6.conf.all.disable_ipv6=1
sudo sysctl -w net.ipv6.conf.default.disable_ipv6=1
```

Persistent:
```bash
cat <<'EOF' | sudo tee /etc/sysctl.d/99-disable-ipv6.conf
net.ipv6.conf.all.disable_ipv6=1
net.ipv6.conf.default.disable_ipv6=1
EOF
sudo sysctl --system
```

---

## Docker Build Issues

### DNS Resolution Failure During Build

**Symptom:** Docker build fails with DNS resolution errors:
```
Failed to establish a new connection: [Errno -3] Temporary failure in name resolution
ERROR: Could not find a version that satisfies the requirement websockets
```

**Root Cause:** Docker BuildKit networking can't resolve DNS on some networks/systems.

**Solutions:**

#### Solution 1: Disable BuildKit (Simplest)
```bash
DOCKER_BUILDKIT=0 docker compose build
```

#### Solution 2: Use Host Network for Build
```bash
docker build --network=host -t asterisk-ai-voice-agent-ai-engine ./
docker build --network=host -t asterisk-ai-voice-agent-local-ai-server ./local_ai_server
```

#### Solution 3: Configure Docker DNS
```bash
# Edit Docker daemon config
sudo nano /etc/docker/daemon.json
```

Add:
```json
{
  "dns": ["8.8.8.8", "8.8.4.4"]
}
```

Then restart Docker:
```bash
sudo systemctl restart docker
```

#### Solution 4: Check System DNS
```bash
# Verify DNS resolution works
nslookup pypi.org

# If not, fix system DNS
sudo nano /etc/resolv.conf
# Add: nameserver 8.8.8.8
```

### Build Timeout or Slow Download

**Symptom:** Build hangs or times out downloading packages.

**Solutions:**

1. **Use pip mirror:**
   ```bash
   # In Dockerfile, change pip install to:
   RUN pip install --no-cache-dir -i https://pypi.org/simple/ -r requirements.txt
   ```

2. **Increase Docker timeout:**
   ```bash
   COMPOSE_HTTP_TIMEOUT=200 docker compose build
   ```

3. **Build with verbose output:**
   ```bash
   docker compose build --progress=plain
   ```

### docker-compose vs docker compose

**Symptom:** `docker: 'compose' is not a docker command`

**Root Cause:** Older Docker installations (Debian/Ubuntu packages) use `docker-compose` (v1) not `docker compose` (v2).

**Solution:**
```bash
# Use docker-compose instead
docker compose up -d ai_engine admin_ui

# Or install Docker Compose v2
sudo apt-get update
sudo apt-get install docker-compose-plugin
```

---

## Getting Help

### 1. Collect Diagnostics

```bash
# Run standard diagnostics report (recommended)
agent check > agent-check.txt

# Analyze most recent call
agent rca > agent-rca.txt

# Collect logs
docker logs --since 1h ai_engine > ai-engine.log 2>&1
```

### 2. Check Documentation

- **[CLI Tools Guide](../cli/README.md)** - Complete CLI reference
- **[Production Deployment](PRODUCTION_DEPLOYMENT.md)** - Security & networking
- **[Configuration Reference](Configuration-Reference.md)** - All settings explained
- **[Golden Baselines](baselines/golden/)** - Validated configurations

### 3. Search GitHub Issues

https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues

Search for:
- Error messages
- Symptoms
- Provider names

### 4. Create GitHub Issue

**Include:**
1. Symptom description
2. Output from `agent check`
3. Output from `agent rca`
4. Relevant log excerpts (redact API keys!)
5. Configuration (redact credentials!)
6. Environment details (OS, Docker version, Asterisk version)

**Template:**
```
**Symptom:** 
Garbled audio - sounds robotic and fast

**Environment:**
- OS: Ubuntu 22.04
- Docker: 24.0.7
- Asterisk: 18.20.0
- ai-engine version: v4.0.0

**Configuration:**
Provider: OpenAI Realtime
Transport: AudioSocket
Network: Bridge mode

**Diagnostics:**
[Attach doctor-report.txt]
[Attach troubleshoot-report.txt]

**Logs:**
[Attach relevant log excerpts]
```

### 5. Community Support

- **Discussions:** https://github.com/hkjarral/Asterisk-AI-Voice-Agent/discussions
- **Discord:** (coming soon)

---

## Quick Reference

### Essential Commands

```bash
# Standard diagnostics report (share this output when asking for help)
agent check

# Post-call RCA (most recent call)
agent rca

# Advanced (legacy alias): list recent calls / symptom heuristics
# agent troubleshoot --list
# agent troubleshoot --last --symptom garbled

# View logs
docker logs -f ai_engine

# Restart services
docker compose restart ai_engine
```

### Essential Configs

```yaml
# Correct AudioSocket format
audiosocket:
  host: "0.0.0.0"
  port: 8090
  format: "slin"  # CRITICAL

# Optimal VAD for OpenAI
vad:
  webrtc_aggressiveness: 1  # NOT 0
  confidence_threshold: 0.6

# Buffer for stability
streaming:
  jitter_buffer_ms: 100
  sample_rate: 8000
```

### Essential Asterisk Dialplan

**The dialplan is the same regardless of transport mode.** Just pass the call to the Stasis application:

```
[from-ai-agent]
exten => s,1,NoOp(AI Voice Agent)
 same => n,Answer()
 same => n,Set(AI_CONTEXT=demo_openai)  ; Optional: select context
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

**Transport is controlled in config, not dialplan:**
- Set `audio_transport: externalmedia` for **pipelines** (hybrid, local_only)
- Set `audio_transport: audiosocket` for **full agents** (Deepgram, OpenAI Realtime)

The ai-engine automatically creates the AudioSocket server or RTP endpoint based on your config. You don't need to add `AudioSocket()` to the dialplan.

**Context Selection:**
Use `AI_CONTEXT` to select different agent personalities/configurations from `config/ai-agent.yaml`.

See [docs/Transport-Mode-Compatibility.md](Transport-Mode-Compatibility.md) for transport mode details.

---

## Appendix: Metric Thresholds

### Quality Metrics (from agent rca)

| Metric | Excellent | Acceptable | Poor | Critical |
|--------|-----------|------------|------|----------|
| **Provider Bytes Ratio** | 0.99-1.01 | 0.95-1.05 | 0.90-1.10 | <0.90 or >1.10 |
| **Drift** | <5% | 5-10% | 10-20% | >20% |
| **Underflow Rate** | 0% | <1% | 1-5% | >5% |
| **Gate Closures** | <5 | 5-20 | 20-50 | >50 |
| **Quality Score** | >90 | 70-90 | 50-70 | <50 |

### Performance Metrics

| Metric | Target | Warning | Critical |
|--------|--------|---------|----------|
| **Response Latency** | <1s | 1-2s | >2s |
| **CPU Usage** | <20% | 20-50% | >50% |
| **Memory Usage** | <512MB | 512MB-1GB | >1GB |
| **Network Latency** | <50ms | 50-200ms | >200ms |

---

**Last Updated:** January 26, 2026  
**Version:** 5.2.4
