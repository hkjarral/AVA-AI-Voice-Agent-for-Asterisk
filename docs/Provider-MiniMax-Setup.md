# MiniMax LLM Provider Setup Guide

## Overview

MiniMax provides OpenAI-compatible Chat Completions for its M2.7 family of models with enhanced reasoning and coding capabilities. Suitable for AI voice agents needing large-context LLM capabilities with tool calling support.

**Performance**: 204K context window | OpenAI-compatible API | Tool calling supported

**Why MiniMax?**
- **Large context**: 204K token context window for complex conversations
- **OpenAI-compatible**: Standard Chat Completions API format
- **Speed option**: `MiniMax-M2.7-highspeed` variant for latency-sensitive calls
- **Tool calling**: Native function calling via OpenAI-style `tools` parameter

If you used the Admin UI Setup Wizard, you may not need to follow this guide end-to-end. For first-call onboarding and transport selection, see:
- `INSTALLATION.md`
- `Transport-Mode-Compatibility.md`

For how provider/context selection works (including `AI_CONTEXT` / `AI_PROVIDER`), see:
- `Configuration-Reference.md` -> "Call Selection & Precedence (Provider / Pipeline / Context)"

## Quick Start

### 1. Get MiniMax API Key

1. Sign up at [MiniMax Platform](https://platform.minimax.io/)
2. Navigate to your account API keys
3. Create a new API key
4. Copy your API key

### 2. Configure API Key

Add your MiniMax API key to `.env`:

```bash
# MiniMax LLM (OpenAI-compatible Chat Completions)
# Docs: https://platform.minimax.io/docs/api-reference/text-openai-api
MINIMAX_API_KEY=your_api_key_here
```

### 3. Configure Provider

Add or enable the MiniMax provider in `config/ai-agent.yaml`:

```yaml
providers:
  minimax_llm:
    enabled: true
    type: minimax
    capabilities: [llm]
    chat_base_url: "https://api.minimax.io/v1"
    # For China domestic access: https://api.minimaxi.com/v1
    chat_model: MiniMax-M2.7
    temperature: 1.0
    response_timeout_sec: 30

pipelines:
  minimax_hybrid:
    stt: local_stt
    llm: minimax_llm
    tts: local_tts
    options:
      llm:
        model: MiniMax-M2.7
        temperature: 1.0
        max_tokens: 150
      stt:
        chunk_ms: 160
        mode: stt
        stream_format: pcm16_16k
        streaming: true
      tts:
        format:
          encoding: mulaw
          sample_rate: 8000

active_pipeline: minimax_hybrid
```

### 4. Model Selection

| Model | Description | Best For |
|-------|-------------|----------|
| `MiniMax-M2.7` | Default, latest flagship with enhanced reasoning | General-purpose voice workflows |
| `MiniMax-M2.7-highspeed` | High-speed version of M2.7 | Latency-sensitive calls |
| `MiniMax-M2.5` | Previous generation, 204K context | Legacy compatibility |
| `MiniMax-M2.5-highspeed` | Previous generation, lower latency | Legacy latency-sensitive calls |

### 5. Tool Calling

MiniMax supports OpenAI-style tool calling. Tools are automatically wired from `src/tools/registry.py` when configured in the pipeline. The adapter sends `tool_choice: "auto"` and handles tool call responses natively.

If a tool call request fails (HTTP 400/422), the adapter automatically retries without tools to ensure the call continues.

### 6. MiniMax-Specific Constraints

- **Temperature**: Must be in the range (0.0, 1.0]. A value of 0 is rejected by the API; the adapter clamps it to 0.01 automatically.
- **response_format**: Not supported by MiniMax. The adapter omits it entirely.
- **Thinking tags**: The adapter automatically strips `<think>` blocks from responses so they are not spoken aloud.

### 7. Configure Asterisk Dialplan

Add to `/etc/asterisk/extensions_custom.conf`:

```ini
[from-ai-agent-minimax]
exten => s,1,NoOp(AI Voice Agent - MiniMax LLM)
exten => s,n,Set(AI_CONTEXT=demo_minimax)
exten => s,n,Set(AI_PROVIDER=minimax_hybrid)
exten => s,n,Stasis(asterisk-ai-voice-agent)
exten => s,n,Hangup()
```

Reload:
```bash
asterisk -rx "dialplan reload"
```

### 8. Admin UI Setup

1. Navigate to the Admin UI provider configuration page
2. Add a new LLM provider with type `minimax`
3. Set the API key (or reference `MINIMAX_API_KEY` from `.env`)
4. Select model (`MiniMax-M2.7`, `MiniMax-M2.7-highspeed`, `MiniMax-M2.5`, or `MiniMax-M2.5-highspeed`)
5. Create or update a pipeline to use `minimax_llm` as the LLM component

### 9. Test Call

Route a test call to the custom destination and verify:
- Greeting plays within expected latency
- AI responds naturally to questions
- Tool execution works if configured
- Check logs for any API errors

## Troubleshooting

### Issue: "MINIMAX_API_KEY not set"

**Fix**: Ensure `MINIMAX_API_KEY` is set in `.env` and the container has been restarted to pick up the change.

### Issue: "Auth failed (HTTP 401/403)"

**Fix**: Verify your API key is valid at [platform.minimax.io](https://platform.minimax.io/). Ensure no extra whitespace in the key.

### Issue: Temperature errors (HTTP 400)

**Fix**: MiniMax rejects `temperature=0`. Use a value between 0.01 and 1.0. The adapter handles this automatically, but pipeline-level overrides may bypass clamping.

### Issue: Tool calling returns 400/422

**Fix**: The adapter retries without tools automatically. If tools consistently fail, verify your tool schemas are compatible with MiniMax's OpenAI-style function calling format.

### Issue: High latency

**Fix**: Switch to `MiniMax-M2.7-highspeed` for faster responses. Reduce `max_tokens` if responses are longer than needed.

## See Also

- **MiniMax API Docs**: https://platform.minimax.io/docs/api-reference/text-openai-api
- **Tool Calling Guide**: `docs/TOOL_CALLING_GUIDE.md`
- **Configuration Reference**: `docs/Configuration-Reference.md`

---

**MiniMax LLM Provider Setup - Complete**

For questions or issues, see the [GitHub repository](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk).
