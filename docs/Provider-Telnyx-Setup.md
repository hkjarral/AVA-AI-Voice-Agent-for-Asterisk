# Telnyx AI Inference Provider Setup Guide

## Overview

Telnyx AI Inference provides an OpenAI-compatible API for LLM access to 53+ models including GPT-4o, Claude, Llama, and Mistral. Ideal for cost-effective AI voice agents with flexible model selection and competitive pricing.

**Performance**: Same as the underlying model (GPT-4o, Claude, Llama, etc.) | OpenAI-compatible API | Competitive pricing

**Why Telnyx AI Inference?**
- **OpenAI-compatible API**: Drop-in replacement for OpenAI with just a `base_url` change
- **53+ models**: Access to GPT-4o, GPT-4o-mini, Claude, Llama, Mistral, and more
- **Competitive pricing**: Often cheaper than direct provider pricing
- **Single API key**: Access multiple model providers through one interface

If you used the Admin UI Setup Wizard, you may not need to follow this guide end-to-end. For first-call onboarding and transport selection, see:
- `INSTALLATION.md`
- `Transport-Mode-Compatibility.md`

For how provider/context selection works (including `AI_CONTEXT` / `AI_PROVIDER`), see:
- `Configuration-Reference.md` -> "Call Selection & Precedence (Provider / Pipeline / Context)"

## Quick Start

### 1. Get Telnyx API Key

1. Sign up at [Telnyx Portal](https://portal.telnyx.com/)
2. Navigate to **AI -> API Keys**
3. Create a new API key
4. Copy your API key

### 2. Configure API Key

Add your Telnyx API key to `.env`:

```bash
# Telnyx AI Inference (OpenAI-compatible)
TELNYX_API_KEY=your_api_key_here
```

**Test API Key**:
```bash
curl -X GET "https://api.telnyx.com/v2/ai/models" \
  -H "Authorization: Bearer ${TELNYX_API_KEY}"
```

### 3. Configure Provider

Telnyx uses the OpenAI-compatible API, making it a drop-in replacement. Configure it in `config/ai-agent.yaml`:

**Option A: Use Telnyx as an LLM in a Pipeline**

```yaml
providers:
  openai:
    enabled: true
    api_key: "${OPENAI_API_KEY}"
  # Add Telnyx as a separate provider entry
  telnyx:
    enabled: true
    api_key: "${TELNYX_API_KEY}"

pipelines:
  # Use Telnyx for LLM with local STT/TTS
  telnyx_hybrid:
    stt: local_stt
    llm: telnyx_llm
    tts: local_tts
    options:
      llm:
        base_url: "https://api.telnyx.com/v2/ai"
        model: "gpt-4o-mini"  # or claude-3-5-sonnet, llama-3.1-70b, etc.
        temperature: 0.7
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

active_pipeline: telnyx_hybrid
```

**Option B: Override OpenAI base_url for LLM**

```yaml
pipelines:
  local_hybrid:
    stt: local_stt
    llm: openai_llm
    tts: local_tts
    options:
      llm:
        # Change base_url to Telnyx
        base_url: "https://api.telnyx.com/v2/ai"
        model: "gpt-4o-mini"
        temperature: 0.7
        max_tokens: 150
```

### 4. Available Models

Telnyx AI Inference provides access to 53+ models. Popular options:

| Model | Description | Best For |
|-------|-------------|----------|
| `gpt-4o` | OpenAI's flagship multimodal model | Complex reasoning, high quality |
| `gpt-4o-mini` | Fast, cost-effective GPT-4 class | Cost-sensitive deployments |
| `claude-3-5-sonnet` | Anthropic's Claude via API | Nuanced conversations |
| `llama-3.1-70b` | Meta's Llama 3.1 70B | Open model, self-hosted feel |
| `mistral-large` | Mistral's flagship model | European data residency |

Check available models:
```bash
curl -s "https://api.telnyx.com/v2/ai/models" \
  -H "Authorization: Bearer ${TELNYX_API_KEY}" | jq '.data[].id'
```

### 5. Configure Asterisk Dialplan

Add to `/etc/asterisk/extensions_custom.conf`:

```ini
[from-ai-agent-telnyx]
exten => s,1,NoOp(AI Voice Agent - Telnyx AI Inference)
exten => s,n,Set(AI_CONTEXT=demo_telnyx)
exten => s,n,Set(AI_PROVIDER=local)
exten => s,n,Stasis(asterisk-ai-voice-agent)
exten => s,n,Hangup()
```

### 6. Reload Asterisk

```bash
asterisk -rx "dialplan reload"
```

### 7. Create FreePBX Custom Destination

1. Navigate to **Admin -> Custom Destinations**
2. Click **Add Custom Destination**
3. Set:
   - **Target**: `from-ai-agent-telnyx,s,1`
   - **Description**: `Telnyx AI Inference Agent`
4. Save and Apply Config

### 8. Test Call

Route a test call to the custom destination and verify:
- Greeting plays within expected latency
- AI responds naturally to questions
- Tool execution works if configured
- Check logs for any API errors

## Context Configuration

Define your AI's behavior in `config/ai-agent.yaml`:

```yaml
contexts:
  demo_telnyx:
    greeting: "Hi {caller_name}, I'm your AI assistant powered by Telnyx. How can I help you today?"
    profile: telephony_ulaw_8k
    prompt: |
      You are a helpful AI assistant for {company_name}.
      
      Your role is to assist callers professionally and efficiently.
      
      CONVERSATION STYLE:
      - Be warm, professional, and concise
      - Use natural language without robotic phrases
      - Answer questions directly and clearly
      - Confirm important actions before executing
      
      CALL ENDING PROTOCOL:
      1. When user says goodbye -> ask "Is there anything else I can help with?"
      2. If user confirms done -> give brief farewell + IMMEDIATELY call hangup_call tool
      3. NEVER leave silence - always explicitly end the call
```

**Template Variables**:
- `{caller_name}` - Caller ID name
- `{caller_number}` - Caller phone number
- `{company_name}` - Your company name (set in config)

## Pricing Comparison

Telnyx AI Inference offers competitive pricing compared to direct provider access:

| Model | Telnyx (per 1M tokens) | Direct Provider |
|-------|------------------------|-----------------|
| gpt-4o-mini | ~$0.15 input / $0.60 output | $0.15 / $0.60 (OpenAI) |
| gpt-4o | ~$2.50 input / $10.00 output | $2.50 / $10.00 (OpenAI) |
| claude-3-5-sonnet | Competitive | Via Anthropic direct |
| llama-3.1-70b | Competitive | Via various providers |

**Note**: Check Telnyx portal for current pricing. Rates may be lower than direct providers due to volume agreements.

## Why Telnyx for AI Inference?

### Cost Benefits
- **Volume pricing**: Telnyx negotiates volume rates with model providers
- **Single bill**: Consolidate multiple AI providers on one invoice
- **No minimums**: Pay only for what you use

### Technical Benefits
- **OpenAI-compatible**: No code changes, just update `base_url`
- **Model flexibility**: Switch between GPT-4o, Claude, Llama without changing providers
- **Low latency**: Global edge infrastructure for fast inference

### Operational Benefits
- **One API key**: Access multiple model families
- **Unified monitoring**: Single dashboard for all AI usage
- **24/7 support**: Enterprise-grade support included

## Note on SIP Trunking

SIP trunk configuration is handled natively by Asterisk/FreePBX and is separate from AI inference. Telnyx also offers SIP trunking services, but this guide focuses specifically on AI inference capabilities. For SIP trunk setup, refer to your Asterisk/FreePBX documentation.

## Troubleshooting

### Issue: "Authentication Failed"

**Cause**: Invalid or missing API key

**Fix**: 
1. Verify `TELNYX_API_KEY` is set in `.env`
2. Test the key directly with curl (see step 2)
3. Ensure no extra whitespace in the key

### Issue: "Model Not Found"

**Cause**: Invalid model name

**Fix**:
1. List available models with the curl command in step 4
2. Use the exact model ID from the response
3. Some models may have different naming conventions

### Issue: "High Latency"

**Cause**: Network latency or model selection

**Fix**:
1. Check network connectivity to `api.telnyx.com`
2. Consider using faster models like `gpt-4o-mini` for latency-sensitive calls
3. Monitor response times in logs

### Issue: "Rate Limited"

**Cause**: Exceeded API rate limits

**Fix**:
1. Check your Telnyx portal for rate limit status
2. Implement request queuing for high-volume deployments
3. Contact Telnyx support for rate limit increases

## Production Considerations

### API Key Management
- Rotate keys periodically
- Use separate keys for dev/staging/production
- Monitor usage in Telnyx Portal
- Set spending alerts

### Cost Optimization
- Choose the right model for each use case
- Use `gpt-4o-mini` for simple queries, `gpt-4o` for complex reasoning
- Monitor token usage per call
- Set budget alerts in Telnyx Portal

### Monitoring
- Track response latency in logs
- Monitor Telnyx API status
- Set up alerts for API errors
- Review usage analytics in portal

## See Also

- **Telnyx AI Inference Docs**: https://developers.telnyx.com/docs/inference/overview
- **Golden Baseline**: `config/ai-agent.golden-telnyx.yaml`
- **Tool Calling Guide**: `docs/TOOL_CALLING_GUIDE.md`
- **Configuration Reference**: `docs/Configuration-Reference.md`

---

**Telnyx AI Inference Provider Setup - Complete**

For questions or issues, see the [GitHub repository](https://github.com/hkjarral/Asterisk-AI-Voice-Agent).
