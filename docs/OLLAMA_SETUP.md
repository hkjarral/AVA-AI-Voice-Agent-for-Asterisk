# Ollama Setup Guide

Run your own local LLM for the Asterisk AI Voice Agent using [Ollama](https://ollama.ai).

## Why Use Ollama?

- **No API Key Required** - Fully self-hosted, no cloud dependencies
- **Privacy** - All data stays on your network
- **No Usage Costs** - Run unlimited calls without API fees
- **Tool Calling Support** - Compatible models can hang up calls, transfer, send emails

## Requirements

- **Hardware**:
  - Mac Mini (M1/M2/M3) - Excellent performance
  - Gaming PC with NVIDIA GPU (8GB+ VRAM recommended)
  - Any machine with 16GB+ RAM for CPU-only inference
- **Software**: Ollama installed ([download](https://ollama.ai))
- **Network**: Ollama must be accessible from Docker containers

## Quick Start

### 1. Install Ollama

```bash
# macOS
curl -fsSL https://ollama.ai/install.sh | sh

# Linux
curl -fsSL https://ollama.ai/install.sh | sh

# Windows
# Download from https://ollama.ai/download
```

### 2. Pull a Model

```bash
# Recommended: Llama 3.2 (supports tool calling)
ollama pull llama3.2

# Smaller model for limited hardware
ollama pull llama3.2:1b

# Alternative: Mistral (also supports tools)
ollama pull mistral
```

### 3. Start Ollama on Network

By default, Ollama only listens on localhost. For Docker to reach it, expose on your network:

```bash
# Start Ollama listening on all interfaces
OLLAMA_HOST=0.0.0.0 ollama serve
```

Or set it permanently:

```bash
# Linux/macOS - add to ~/.bashrc or ~/.zshrc
export OLLAMA_HOST=0.0.0.0

# Then restart Ollama
ollama serve
```

### 4. Find Your IP Address

```bash
# macOS
ipconfig getifaddr en0

# Linux
hostname -I | awk '{print $1}'

# Windows
ipconfig
```

### 5. Configure in ai-agent.yaml

```yaml
pipelines:
  local_ollama:
    stt: local_stt
    llm: ollama_llm
    tts: local_tts
    tools:
      - hangup_call
      - transfer
      - send_email_summary
    options:
      llm:
        base_url: http://192.168.1.100:11434  # Your IP address
        model: llama3.2
        temperature: 0.7
        num_ctx: 8192  # Optional: match your model's context window
        timeout_sec: 60
        tools_enabled: true
```

### 6. Set Active Pipeline

```yaml
active_pipeline: local_ollama
```

## Models with Tool Calling Support

These models can use tools like `hangup_call`, `transfer`, `send_email_summary`:

| Model | Size | Tool Calling | Best For |
|-------|------|--------------|----------|
| `llama3.2` | 2GB | ✅ Yes | General use, good balance |
| `llama3.2:1b` | 1.3GB | ✅ Yes | Limited hardware |
| `llama3.2:3b` | 2GB | ✅ Yes | Better quality |
| `llama3.1` | 4.7GB | ✅ Yes | Higher quality |
| `mistral` | 4.1GB | ✅ Yes | Fast, good quality |
| `mistral-nemo` | 7.1GB | ✅ Yes | Best Mistral quality |
| `qwen2.5` | 4.7GB | ✅ Yes | Multilingual support |
| `qwen2.5:7b` | 4.7GB | ✅ Yes | Good balance |
| `command-r` | 18GB | ✅ Yes | Enterprise quality |

### Models WITHOUT Tool Calling

These models work for conversation but cannot execute actions:

| Model | Size | Notes |
|-------|------|-------|
| `phi3` | 2.2GB | Good for simple conversations |
| `gemma2` | 5.4GB | Google's model |
| `tinyllama` | 637MB | Very small, limited quality |

> **Note**: Models without tool calling will respond but cannot hang up calls, transfer, or send emails. Users must hang up manually.

## Troubleshooting

### "Cannot connect to Ollama"

1. **Check Ollama is running**:
   ```bash
   curl http://localhost:11434/api/tags
   ```

2. **Ensure network access**:
   ```bash
   # Must show 0.0.0.0:11434
   OLLAMA_HOST=0.0.0.0 ollama serve
   ```

3. **Test from another machine**:
   ```bash
   curl http://YOUR_IP:11434/api/tags
   ```

4. **Check firewall**: Ensure port 11434 is open

### "Connection timeout"

- Local models are slower than cloud APIs
- Increase `timeout_sec` in config (try 120 for larger models)
- Use a smaller model (`llama3.2:1b` instead of `llama3.2`)

### "Model not found"

```bash
# List installed models
ollama list

# Pull the missing model
ollama pull llama3.2
```

### Tool calling not working

1. Check model supports tools (see table above)
2. Ensure `tools_enabled: true` in config
3. Check logs for "Ollama tool calls detected"

### Agent hangs up unexpectedly (tool calling)

Some models may over-eagerly emit `hangup_call` tool calls even when the caller did not say goodbye.

Quick mitigations:
- Disable tools for Ollama: set `tools_enabled: false`, or
- Remove `hangup_call` from your context’s `tools:` list.

### Docker networking issues

Docker containers cannot reach `localhost`. Use your host machine's IP:

```yaml
# Wrong - Docker can't reach this
base_url: http://localhost:11434

# Correct - Use your actual IP
base_url: http://192.168.1.100:11434
```

## Performance Tips

### Hardware Recommendations

| Model Size | Minimum RAM | Recommended | GPU |
|------------|-------------|-------------|-----|
| 1B params | 4GB | 8GB | Optional |
| 3B params | 8GB | 16GB | Recommended |
| 7B params | 16GB | 32GB | Recommended |
| 13B+ params | 32GB+ | 64GB+ | Required |

### Optimize Response Time

1. **Use smaller models** for voice (1B-3B is usually sufficient)
2. **Reduce max_tokens** (100-200 for voice responses)
3. **Keep model loaded**: Set `keep_alive: -1` to prevent unloading

### GPU Acceleration

Ollama automatically uses GPU if available:

- **Apple Silicon**: Metal acceleration (automatic)
- **NVIDIA**: CUDA acceleration (requires drivers)
- **AMD**: ROCm support (Linux only)

## Admin UI Configuration

1. Go to **Providers** page
2. Click **Add Provider**
3. Select type: **Ollama**
4. Enter your Ollama server URL
5. Click **Test Connection** to verify and list models
6. Select a model from the dropdown
7. Save and restart AI Engine

## Example Configurations

### Minimal (Limited Hardware)

```yaml
pipelines:
  local_ollama:
    stt: local_stt
    llm: ollama_llm
    tts: local_tts
    options:
      llm:
        base_url: http://192.168.1.100:11434
        model: llama3.2:1b
        max_tokens: 100
        timeout_sec: 120
```

### Production (Good Hardware)

```yaml
pipelines:
  local_ollama:
    stt: local_stt
    llm: ollama_llm
    tts: local_tts
    tools:
      - hangup_call
      - transfer
      - send_email_summary
      - request_transcript
    options:
      llm:
        base_url: http://192.168.1.100:11434
        model: llama3.2
        temperature: 0.7
        max_tokens: 200
        timeout_sec: 60
        tools_enabled: true
```

### Multilingual Support

```yaml
pipelines:
  local_ollama:
    stt: local_stt
    llm: ollama_llm
    tts: local_tts
    options:
      llm:
        base_url: http://192.168.1.100:11434
        model: qwen2.5  # Good multilingual support
        temperature: 0.7
```

## Further Reading

- [Ollama Documentation](https://ollama.ai/docs)
- [Ollama Model Library](https://ollama.ai/library)
- [Tool Calling in Ollama](https://ollama.ai/blog/tool-support)
