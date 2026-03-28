# Fully Local Setup Guide

Run Asterisk AI Voice Agent completely on-premises with no cloud APIs.

This is the **canonical guide** for all local deployment topologies:

- [CPU-Only](#topology-1-cpu-only-single-machine) — everything on one machine, no GPU
- [GPU (same machine)](#topology-2-gpu-same-machine) — Asterisk + AI engine + GPU inference on one box
- [Split-Server (remote GPU)](#topology-3-split-server-remote-gpu) — PBX on one machine, GPU inference on another

## Overview

A fully local deployment uses:
- **STT**: Faster Whisper (recommended with GPU), Vosk, or Sherpa-ONNX
- **LLM**: Phi-3 Mini or other GGUF models via llama.cpp
- **TTS**: Kokoro (recommended) or Piper

## Choose Your Topology

| Topology | Hardware | Latency | Best For |
|----------|----------|---------|----------|
| **CPU-Only** | 8GB+ RAM, modern CPU | 5-15s per turn | Privacy, testing, low-volume |
| **GPU (same box)** | NVIDIA RTX 3060+ | 0.5-2s per turn | Production local, best UX |
| **Split-Server** | PBX box + remote GPU | 1-3s per turn | PBX on VPS, GPU on beefy box |

---

## Prerequisites (All Topologies)

- Docker and Docker Compose v2
- Asterisk 18+ with ARI enabled
- `sudo ./preflight.sh --apply-fixes` run at least once

---

## Topology 1: CPU-Only (Single Machine)

Everything runs on one machine with no GPU.

### Prerequisites

- 8GB+ RAM (16GB recommended)
- Modern CPU (2020+) for reasonable LLM inference speed
- No GPU required

### 1. Environment Variables (.env)

```bash
# STT — Vosk is CPU-friendly; Faster Whisper also works on CPU but is slower without GPU
LOCAL_STT_BACKEND=vosk
LOCAL_STT_MODEL_PATH=/app/models/stt/vosk-model-en-us-0.22

# TTS — Kokoro (premium quality) or Piper (lightweight)
LOCAL_TTS_BACKEND=kokoro
KOKORO_VOICE=af_heart

# LLM — Small model for CPU; TinyLlama for speed, Phi-3 for quality
LOCAL_LLM_MODEL_PATH=/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf
LOCAL_LLM_GPU_LAYERS=0
LOCAL_LLM_CONTEXT=512
LOCAL_LLM_MAX_TOKENS=32

# WebSocket — default host-network loopback
LOCAL_WS_URL=ws://127.0.0.1:8765

# No cloud providers needed
# OPENAI_API_KEY=
# DEEPGRAM_API_KEY=
```

### 2. Start Services

```bash
docker compose -p asterisk-ai-voice-agent up -d --build local_ai_server ai_engine admin_ui
```

### 3. Verify

```bash
docker logs local_ai_server | grep -E "STT|LLM|TTS"
# Expected:
#   ✅ STT model loaded: vosk-model-en-us-0.22
#   ✅ LLM model loaded: phi-3-mini-4k-instruct.Q4_K_M.gguf
#   ✅ TTS backend: Kokoro initialized
```

**Expected latency:** 5-15s per turn on modern CPU, depending on model size.

---

## Topology 2: GPU (Same Machine)

Asterisk, `ai_engine`, and `local_ai_server` all on one box with an NVIDIA GPU.

### Prerequisites

- NVIDIA GPU: RTX 3060 (12GB VRAM) minimum, RTX 4060 Ti+ recommended
- NVIDIA drivers installed (`nvidia-smi` must work)
- [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) installed

**Quick nvidia-container-toolkit install (Debian/Ubuntu):**

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 1. Run Preflight

```bash
sudo ./preflight.sh --apply-fixes
# Preflight detects GPU and sets GPU_AVAILABLE=true in .env
```

> **Tip:** If this machine is a dedicated GPU inference server (no Asterisk), use `--local-server` to skip Asterisk/Admin UI checks:
> ```bash
> sudo ./preflight.sh --apply-fixes --local-server
> ```

### 2. Environment Variables (.env)

```bash
# STT — Faster Whisper is best with GPU (CUDA acceleration)
LOCAL_STT_BACKEND=faster_whisper
LOCAL_STT_MODEL_PATH=base
# Alternatives: tiny, small, medium, large-v3

# TTS — Kokoro HuggingFace mode (auto-downloads, best quality)
LOCAL_TTS_BACKEND=kokoro
KOKORO_MODE=hf
KOKORO_VOICE=af_heart

# LLM — GPU-accelerated inference
LOCAL_LLM_MODEL_PATH=/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf
LOCAL_LLM_GPU_LAYERS=-1          # -1 = offload ALL layers to GPU
LOCAL_LLM_CONTEXT=4096           # larger context with GPU headroom
LOCAL_LLM_MAX_TOKENS=150

# WebSocket — default host-network loopback
LOCAL_WS_URL=ws://127.0.0.1:8765
```

### 3. Build with GPU Compose Overlay

The GPU compose file (`docker-compose.gpu.yml`) builds a CUDA-enabled `local_ai_server` image using `Dockerfile.gpu`:

```bash
docker compose -p asterisk-ai-voice-agent \
  -f docker-compose.yml -f docker-compose.gpu.yml \
  up -d --build local_ai_server

```

Start `ai_engine` and `admin_ui` (no GPU needed for these):

```bash
docker compose -p asterisk-ai-voice-agent up -d --build ai_engine admin_ui
```

### 4. Verify GPU is Accessible

```bash
# Check container sees GPU
docker compose -p asterisk-ai-voice-agent \
  -f docker-compose.yml -f docker-compose.gpu.yml \
  exec local_ai_server nvidia-smi

# Check models loaded
docker logs local_ai_server | grep -E "STT|LLM|TTS|GPU|CUDA"
```

**Expected latency:** ~500ms-2s per turn. Community-validated on RTX 4090: ~665ms E2E with Faster Whisper + Phi-3 + Kokoro.

---

## Topology 3: Split-Server (Remote GPU)

PBX + `ai_engine` on Machine A, `local_ai_server` on Machine B (GPU box).

This is common for production: a small VPS runs Asterisk/ai_engine, and a beefy box (or cloud GPU) handles inference.

### Machine B (GPU Server) — local_ai_server only

```bash
git clone https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk.git
cd Asterisk-AI-Voice-Agent

# --local-server skips Asterisk/Admin UI checks (not needed on GPU-only box)
sudo ./preflight.sh --apply-fixes --local-server
```

**.env on GPU machine:**

```bash
# Bind to all interfaces so the PBX machine can reach us
LOCAL_WS_HOST=0.0.0.0
LOCAL_WS_PORT=8765
LOCAL_WS_AUTH_TOKEN=<generate-a-strong-random-token>

# STT/TTS/LLM config (same as Topology 2)
LOCAL_STT_BACKEND=faster_whisper
LOCAL_STT_MODEL_PATH=base
LOCAL_TTS_BACKEND=kokoro
KOKORO_MODE=hf
KOKORO_VOICE=af_heart
LOCAL_LLM_MODEL_PATH=/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf
LOCAL_LLM_GPU_LAYERS=-1
LOCAL_LLM_CONTEXT=4096
```

**Start local_ai_server with GPU:**

```bash
docker compose -p asterisk-ai-voice-agent \
  -f docker-compose.yml -f docker-compose.gpu.yml \
  up -d --build local_ai_server
```

### Machine A (PBX Server) — ai_engine + admin_ui only

```bash
git clone https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk.git
cd Asterisk-AI-Voice-Agent
sudo ./preflight.sh --apply-fixes
```

**.env on PBX machine:**

```bash
# Point to the remote GPU server
LOCAL_WS_URL=ws://<gpu-machine-ip>:8765
LOCAL_WS_AUTH_TOKEN=<same-token-as-gpu-machine>
LOCAL_WS_CONNECT_TIMEOUT=5.0
LOCAL_WS_RESPONSE_TIMEOUT=10.0
```

**Start only ai_engine + admin_ui (no local_ai_server):**

```bash
docker compose -p asterisk-ai-voice-agent up -d --build ai_engine admin_ui
```

### Network Requirements

- **Port 8765/tcp** open from Machine A → Machine B
- **Auth token** must match on both sides (`LOCAL_WS_AUTH_TOKEN`)
- **Latency**: ideally same LAN (<5ms RTT); works over WAN but adds to E2E

### Security

When `LOCAL_WS_HOST=0.0.0.0`, the inference server is exposed on the network. **Always set `LOCAL_WS_AUTH_TOKEN`** and restrict access via firewall:

```bash
# On GPU machine — only allow PBX machine IP
sudo ufw allow from <pbx-machine-ip> to any port 8765
```

---

## Verify Each Component

### Quick Check (recommended)

One command to verify STT, LLM, and TTS are all working:

```bash
# GPU on same host
agent check --local

# Remote GPU server
agent check --remote <gpu-ip>

# Without the Go CLI binary (standalone script)
python3 scripts/check_local_server.py --local
python3 scripts/check_local_server.py --remote <gpu-ip>
```

See [CLI Tools Guide](CLI_TOOLS_GUIDE.md#agent-check---local--agent-check---remote) for full flag reference.

### Manual Verification (advanced)

The commands below test each component individually. The server uses **WebSocket** (not HTTP), so these commands use Python with the `websockets` library (pre-installed in the container).

> **Remote testing:** Replace `ws://127.0.0.1:8765` with `ws://<gpu-ip>:8765` to test from another machine. If `LOCAL_WS_AUTH_TOKEN` is set, add auth (see [auth example](#with-authentication) below).

### 1. Status Check (are models loaded?)

```bash
docker exec local_ai_server python3 -c "
import asyncio, json, websockets
async def main():
    ws = await websockets.connect('ws://127.0.0.1:8765', open_timeout=5, max_size=None)
    await ws.send(json.dumps({'type': 'status'}))
    r = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    await ws.close()
    m = r.get('models', {}); g = r.get('gpu', {})
    for k in ('stt','llm','tts'):
        info = m.get(k, {})
        print(k.upper() + ':', info.get('backend','?'), '| loaded=' + str(info.get('loaded')), '|', info.get('display','?'))
    print('GPU:', g.get('name','none'), '| usable=' + str(g.get('runtime_usable')))
asyncio.run(main())
"
```

Expected:

```text
STT: faster_whisper | loaded=True | Faster-Whisper (base)
LLM: loaded=True | phi-3-mini-4k-instruct.Q4_K_M.gguf | gpu_layers=50
TTS: kokoro | loaded=True | Kokoro (af_heart, mode=hf)
GPU: NVIDIA GeForce RTX 4090 | usable=True
```

### 2. Test LLM (text generation)

```bash
docker exec local_ai_server python3 -c "
import asyncio, json, websockets, time
async def main():
    ws = await websockets.connect('ws://127.0.0.1:8765', open_timeout=5, max_size=None)
    t0 = time.time()
    await ws.send(json.dumps({'type': 'llm_request', 'text': 'Say hello in one sentence.', 'mode': 'llm'}))
    r = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
    await ws.close()
    print('LLM response:', r.get('text', '')[:200])
    print('Latency: %.2fs' % (time.time() - t0))
asyncio.run(main())
"
```

Expected: a text response in <1s on GPU, 5-15s on CPU.

### 3. Test TTS (speech synthesis)

```bash
docker exec local_ai_server python3 -c "
import asyncio, json, websockets, time
async def main():
    ws = await websockets.connect('ws://127.0.0.1:8765', open_timeout=5, max_size=None)
    t0 = time.time()
    await ws.send(json.dumps({'type': 'tts_request', 'text': 'Hello, this is a test of text to speech.', 'response_format': 'json'}))
    r = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
    await ws.close()
    print('TTS audio:', r.get('byte_length', 0), 'bytes |', r.get('encoding'), r.get('sample_rate_hz'), 'Hz')
    print('Latency: %.2fs' % (time.time() - t0))
asyncio.run(main())
"
```

Expected: audio bytes > 0, encoding=mulaw, sample_rate=8000, latency <0.5s on GPU.

### 4. Test STT (speech recognition — full round-trip)

This generates speech via TTS, then feeds it back into STT to verify the full audio pipeline:

```bash
docker exec local_ai_server python3 -c "
import asyncio, json, websockets, time, base64, audioop
async def main():
    # Generate test audio via TTS
    ws1 = await websockets.connect('ws://127.0.0.1:8765', open_timeout=5, max_size=None)
    await ws1.send(json.dumps({'type': 'tts_request', 'text': 'Hello, this is a test of the speech recognition system.', 'response_format': 'json'}))
    tts = json.loads(await asyncio.wait_for(ws1.recv(), timeout=15))
    audio_mulaw = base64.b64decode(tts.get('audio_data', ''))
    await ws1.close()
    print('TTS generated:', len(audio_mulaw), 'bytes')

    # Convert mulaw 8kHz -> PCM16 16kHz for STT
    pcm16k, _ = audioop.ratecv(audioop.ulaw2lin(audio_mulaw, 2), 2, 1, 8000, 16000, None)

    # Send to STT on a fresh connection
    ws2 = await websockets.connect('ws://127.0.0.1:8765', open_timeout=5, max_size=None)
    await ws2.send(json.dumps({'type': 'set_mode', 'mode': 'stt'}))
    await asyncio.wait_for(ws2.recv(), timeout=5)
    t0 = time.time()
    await ws2.send(json.dumps({'type': 'audio', 'data': base64.b64encode(pcm16k).decode(), 'mode': 'stt', 'rate': 16000}))

    transcript = ''
    try:
        while True:
            raw = await asyncio.wait_for(ws2.recv(), timeout=10)
            if isinstance(raw, str):
                msg = json.loads(raw)
                if msg.get('type') == 'stt_result' and msg.get('is_final') and msg.get('text', '').strip():
                    transcript = msg['text']
                    break
    except asyncio.TimeoutError:
        pass
    await ws2.close()
    print('STT result:', repr(transcript) if transcript else '(no transcript)')
    print('Latency: %.2fs' % (time.time() - t0))
asyncio.run(main())
"
```

Expected: transcript should closely match the TTS input text.

### With Authentication

If `LOCAL_WS_AUTH_TOKEN` is set (required for split-server), add auth before any request:

```bash
# Add this after connecting, before sending any other message:
# await ws.send(json.dumps({'type': 'auth', 'auth_token': 'YOUR_TOKEN'}))
# r = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
# assert r.get('status') == 'ok', f"Auth failed: {r}"

# Example: remote status check with auth
python3 -c "
import asyncio, json, websockets
async def main():
    ws = await websockets.connect('ws://<gpu-ip>:8765', open_timeout=5, max_size=None)
    await ws.send(json.dumps({'type': 'auth', 'auth_token': 'YOUR_TOKEN'}))
    auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert auth.get('status') == 'ok', 'Auth failed: ' + str(auth)
    await ws.send(json.dumps({'type': 'status'}))
    r = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    await ws.close()
    print(json.dumps(r, indent=2))
asyncio.run(main())
"
```

> **Note:** For remote testing without `websockets` installed, use `pip3 install websockets` or run via `docker exec` on the GPU machine.

---

## AI Agent Configuration (config/ai-agent.yaml)

This applies to **all topologies**. The key settings:

```yaml
default_provider: local
active_pipeline: local_only

# IMPORTANT: Use externalmedia for pipelines (not audiosocket)
# AudioSocket + Pipelines has a known Asterisk bridge routing conflict.
# See docs/Transport-Mode-Compatibility.md
audio_transport: externalmedia

providers:
  local:
    type: full
    enabled: true
    capabilities: [stt, llm, tts]
    base_url: ${LOCAL_WS_URL:-ws://127.0.0.1:8765}
    auth_token: ${LOCAL_WS_AUTH_TOKEN:-}

  local_stt:
    type: local
    enabled: true
    capabilities: [stt]
    ws_url: ${LOCAL_WS_URL:-ws://127.0.0.1:8765}
    auth_token: ${LOCAL_WS_AUTH_TOKEN:-}

  local_llm:
    type: local
    enabled: true
    capabilities: [llm]
    ws_url: ${LOCAL_WS_URL:-ws://127.0.0.1:8765}
    auth_token: ${LOCAL_WS_AUTH_TOKEN:-}

  local_tts:
    type: local
    enabled: true
    capabilities: [tts]
    ws_url: ${LOCAL_WS_URL:-ws://127.0.0.1:8765}
    auth_token: ${LOCAL_WS_AUTH_TOKEN:-}

pipelines:
  local_only:
    stt: local_stt
    llm: local_llm
    tts: local_tts
    options:
      stt:
        streaming: true
        chunk_ms: 160
        stream_format: pcm16_16k
        mode: stt
      llm:
        # NOTE: Do NOT include OpenAI model names or URLs here.
        # The local LLM path is configured via LOCAL_LLM_MODEL_PATH in .env
        temperature: 0.7
        max_tokens: 150
      tts:
        format:
          encoding: mulaw
          sample_rate: 8000

contexts:
  default:
    provider: local
    greeting: |
      Hello! I'm your AI assistant running completely locally.
      How can I help you today?
    prompt: |
      You are a helpful AI assistant. Be concise and friendly.
      Keep responses under 2 sentences when possible.
```

> **Transport note:** Pipelines use file-based playback, which requires ExternalMedia RTP transport. AudioSocket + Pipelines causes an Asterisk bridge conflict (greeting only, then silence). See [Transport Compatibility](Transport-Mode-Compatibility.md).

### Important: Do NOT include cloud model names

```yaml
# ❌ WRONG - causes validation errors
pipelines:
  local_only:
    options:
      llm:
        model: gpt-4o-mini
        base_url: https://api.openai.com/v1

# ✅ CORRECT - model path is set via LOCAL_LLM_MODEL_PATH in .env
pipelines:
  local_only:
    options:
      llm:
        temperature: 0.7
        max_tokens: 150
```

---

## Model Downloads

Models are **not bundled** in Docker images. Download them via:

1. **Admin UI → Models Page** (recommended — visual download + progress)
2. **Setup script**: `./scripts/model_setup.sh`
3. **Manual download** into `./models/{stt,tts,llm}/`

### Supported STT Models

**Faster Whisper** (recommended with GPU — CUDA accelerated):
- `tiny`, `base`, `small`, `medium`, `large-v3`
- Set `LOCAL_STT_BACKEND=faster_whisper` and `LOCAL_STT_MODEL_PATH=base`
- GPU builds include Faster Whisper by default (`docker-compose.gpu.yml`)
- CPU builds: set `INCLUDE_FASTER_WHISPER=true` before building

**Vosk** (CPU-friendly, offline, good accuracy):
- `vosk-model-en-us-0.22` (English, recommended)
- `vosk-model-small-en-us-0.15` (English, smaller/faster)
- `vosk-model-nl-0.22` (Dutch)
- See [Vosk Models](https://alphacephei.com/vosk/models)

**Sherpa-ONNX Streaming** (lower latency):
- `sherpa-onnx-streaming-zipformer-en-2023-06-26` (English)
- Use with `SHERPA_MODEL_TYPE=online`
- Best fit for the existing online Sherpa backend

**Sherpa-ONNX Offline Transducer** (VAD-gated):
- `sherpa-onnx-zipformer-en-2023-06-26` (English verification model)
- `sherpa-onnx-zipformer-gigaspeech-2023-12-12` (English fallback)
- `sherpa-onnx-zipformer-ru-2024-09-18` (Russian follow-up after English passes)
- Use with `SHERPA_MODEL_TYPE=offline`
- Offline mode requires a non-streaming transducer model. Do not point offline mode at `sherpa-onnx-streaming-*` models.
- Typical offline tuning knobs:
  - `SHERPA_VAD_MODEL_PATH=/app/models/vad/silero_vad.onnx`
  - `SHERPA_VAD_THRESHOLD=0.35`
  - `SHERPA_VAD_MIN_SILENCE_MS=700`
  - `SHERPA_VAD_MIN_SPEECH_MS=200`
  - `SHERPA_OFFLINE_PREROLL_MS=350`
  - `SHERPA_OFFLINE_DEBUG_SEGMENTS=true` for targeted diagnostics only
- See [Sherpa-ONNX Models](https://github.com/k2-fsa/sherpa-onnx/releases)

**T-one** (Russian telephony streaming CTC):
- Requires rebuild: `docker compose build --build-arg INCLUDE_TONE=true local_ai_server`
- Set `LOCAL_STT_BACKEND=tone`
- Provide:
  - `TONE_MODEL_PATH=/app/models/stt/t-one`
  - `TONE_DECODER_TYPE=beam_search` or `greedy`
  - `TONE_KENLM_PATH=/app/models/stt/t-one/kenlm.bin` when using `beam_search`
- T-one expects 8 kHz audio internally; `local_ai_server` handles 16 kHz to 8 kHz conversion and 300 ms chunk framing.
- Recommended for Russian community validation when you want the upstream T-one path instead of Sherpa/Whisper.

**Kroko Embedded** (optional, requires rebuild):
- `docker compose build --build-arg INCLUDE_KROKO_EMBEDDED=true local_ai_server`
- Models: Download from Admin UI → Models Page

**Whisper.cpp** (optional, GGML-based offline STT):
- Uses `pywhispercpp` with GGML Whisper models (e.g., `ggml-base.bin`, `ggml-small.bin`)
- Set `LOCAL_STT_BACKEND=whisper_cpp` and `LOCAL_WHISPER_CPP_MODEL_PATH=/app/models/stt/ggml-base.bin`
- Requires rebuild: `docker compose build --build-arg INCLUDE_WHISPER_CPP=true local_ai_server`

### Supported LLM Models (GGUF)

- `phi-3-mini-4k-instruct.Q4_K_M.gguf` (recommended — good quality/speed balance)
- `tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf` (fastest on CPU, lower quality)
- `phi-3-mini-128k-instruct.Q4_K_M.gguf` (larger context)
- Any llama.cpp compatible GGUF model

### Supported TTS Models

**Kokoro** (recommended — premium quality):
- **HuggingFace mode** (recommended): `KOKORO_MODE=hf` — auto-downloads from HF Hub
- **Local mode**: `KOKORO_MODE=local` — uses pre-downloaded model files
- **API mode** (not fully local): `KOKORO_MODE=api`
- Voices: `af_heart`, `af_bella`, `am_adam`, `bf_emma`, `bm_george`, etc.

**Piper** (lightweight, many languages):
- `en_US-lessac-medium.onnx` (English, recommended)
- Various voices/languages available

---

## Runtime Mode (LOCAL_AI_MODE)

The `local_ai_server` runtime mode controls which components are preloaded at startup:

| Mode | Components | When to Use |
|------|-----------|-------------|
| `full` | STT + LLM + TTS | GPU hosts, or CPU hosts with ≥16GB RAM |
| `minimal` | STT + TTS only | CPU-only hosts with limited RAM (skips LLM preload) |

**Default behavior** (if `LOCAL_AI_MODE` is unset):
- `GPU_AVAILABLE=true` → defaults to `full`
- `GPU_AVAILABLE=false` → defaults to `minimal`

Set explicitly in `.env` to override: `LOCAL_AI_MODE=full`

---

## Verify and Test

### Health Check

```bash
curl http://localhost:15000/health
# Expected: {"status":"healthy"}
```

### Place a Test Call

See [Installation Guide](INSTALLATION.md#first-successful-call-canonical-checklist) for the full first-call checklist.

### Community Test Report

After a successful call, generate a report for the Community Test Matrix:

```bash
agent rca --local
# Or run directly:
python3 scripts/local_test_report.py --project-root .
```

This outputs hardware, model, latency, and tool-call data for [COMMUNITY_TEST_MATRIX.md](COMMUNITY_TEST_MATRIX.md).

---

## Troubleshooting

### Validation Errors

If you see "Pipeline LLM validation FAILED" but calls still work:
- Confirm `LOCAL_WS_URL` matches your Docker networking mode:
  - Host networking (default): `LOCAL_WS_URL=ws://127.0.0.1:8765`
  - Bridge networking: `LOCAL_WS_URL=ws://local_ai_server:8765`

### Slow LLM Responses

- **Add a GPU** — this is the single biggest improvement (10-30x faster)
- Reduce `LOCAL_LLM_MAX_TOKENS` (24-32 for CPU)
- Use a smaller model (TinyLlama 1.1B for CPU)
- Set `LOCAL_LLM_CONTEXT=512` on CPU
- See [Topology 2](#topology-2-gpu-same-machine) for GPU setup

### Only Greeting Heard, Then Silence

Most likely **wrong transport**. Pipelines require ExternalMedia RTP:

```yaml
# In config/ai-agent.yaml
audio_transport: externalmedia    # NOT audiosocket for pipelines
```

See [Transport Compatibility](Transport-Mode-Compatibility.md) for the full matrix.

### Call Drops After Greeting

1. Local AI server running: `docker ps | grep local`
2. WebSocket accessible from `ai_engine`:
   ```bash
   docker exec -i ai_engine python -c "
   import asyncio, json, websockets
   async def main():
       ws = await websockets.connect('ws://127.0.0.1:8765')
       await ws.send(json.dumps({'type':'status'}))
       print(await ws.recv())
       await ws.close()
   asyncio.run(main())
   "
   ```
3. Models loaded: `docker logs local_ai_server | tail -50`

### Split-Server: Cannot Connect

- Verify `LOCAL_WS_HOST=0.0.0.0` on GPU machine
- Verify `LOCAL_WS_AUTH_TOKEN` matches on both machines
- Test connectivity: `curl -v telnet://<gpu-ip>:8765` (should connect)
- Check firewall: port 8765/tcp must be open

---

## Hardware Recommendations

| Topology | CPU | RAM | GPU | Disk |
|----------|-----|-----|-----|------|
| CPU-Only | 4+ cores (2020+) | 8-16GB | None | 5GB |
| GPU (same box) | 4+ cores | 8-16GB | RTX 3060+ (12GB VRAM) | 10GB |
| Split-Server (PBX) | 2+ cores | 4GB | None | 2GB |
| Split-Server (GPU) | 4+ cores | 8-16GB | RTX 3060+ (12GB VRAM) | 10GB |

For detailed capacity planning, see [Hardware Requirements](HARDWARE_REQUIREMENTS.md).

---

## Related Documentation

- [Hardware Requirements](HARDWARE_REQUIREMENTS.md) — detailed specs and cloud sizing
- [Local Profiles](LOCAL_PROFILES.md) — build profiles (local-core vs local-full)
- [Transport Compatibility](Transport-Mode-Compatibility.md) — validated transport + playback combos
- [Ollama Setup](OLLAMA_SETUP.md) — use Ollama as LLM instead of llama.cpp
- [Configuration Reference](Configuration-Reference.md) — all settings explained
- [Community Test Matrix](COMMUNITY_TEST_MATRIX.md) — validated hardware/model combos
- [Troubleshooting Guide](TROUBLESHOOTING_GUIDE.md) — common issues and solutions
