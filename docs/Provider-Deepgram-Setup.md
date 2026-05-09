# Deepgram Provider Setup Guide

## Overview

Deepgram Voice Agent is a monolithic real-time conversational AI provider that combines speech-to-text, LLM reasoning, and text-to-speech in a single streaming API. Ideal for low-latency telephony applications with built-in function calling support.

**Performance**: 1-2 second response latency | Full duplex | Native tool execution

If you used the Admin UI Setup Wizard, you may not need to follow this guide end-to-end. For first-call onboarding and transport selection, see:
- `INSTALLATION.md`
- `Transport-Mode-Compatibility.md`

For how provider/context selection works (including `AI_CONTEXT` / `AI_PROVIDER`), see:
- `Configuration-Reference.md` → "Call Selection & Precedence (Provider / Pipeline / Context)"

## Quick Start

### 1. Get Deepgram API Key

1. Sign up at [Deepgram Console](https://console.deepgram.com/)
2. Navigate to **API Keys**
3. Create a new API key with Voice Agent access
4. Copy your API key

### 2. Configure API Key

Add your Deepgram API key to `.env`:

```bash
# Deepgram Voice Agent (required for deepgram provider)
DEEPGRAM_API_KEY=your_api_key_here
```

**Test API Key**:
```bash
curl -X GET "https://api.deepgram.com/v1/projects" \
  -H "Authorization: Token ${DEEPGRAM_API_KEY}"
```

### 3. Configure Provider

The Deepgram provider is configured in `config/ai-agent.yaml`:

```yaml
providers:
  deepgram:
    # API key is injected from `DEEPGRAM_API_KEY` in `.env` (env-only; do not commit keys to YAML)
    enabled: true
    type: full
    capabilities: ["stt", "llm", "tts"]
    greeting: "Hi {caller_name}, I'm Ava. How can I help you today?"
    
    # Models — see "Choosing models" below
    model: nova-3                        # Deepgram's current recommended default
    tts_model: aura-2-thalia-en
    
    # Audio (telephony defaults)
    input_encoding: mulaw
    input_sample_rate_hz: 8000
    output_encoding: mulaw
    output_sample_rate_hz: 8000
    
    # Optional behavior overrides (otherwise inherits from context / llm prompt)
    instructions: "Voice assistant. Be concise."
    continuous_input: true
```

**Key Settings**:
- `model`: Deepgram listen (STT) model — see **Choosing models** below
- `tts_model`: Aura speak (TTS) voice (example: `aura-2-thalia-en`)
- `input_encoding`/`input_sample_rate_hz`: what the engine receives from Asterisk (telephony defaults are μ-law @ 8 kHz)

#### Choosing models

| Model | Type | Status | When to use |
|-------|------|--------|-------------|
| `nova-3` | Listen (STT) | **GA — Deepgram's recommended default** | New deployments. Higher accuracy than nova-2, multilingual conversation, customizable vocabulary. |
| `nova-2` | Listen (STT) | GA, still supported | Languages not yet supported by nova-3, or workloads that depend on filler-word identification. |
| `flux-general-en` | Listen (STT) + turn detection | GA, conversational | **Recommended for voice agents.** English-only conversational STT with built-in EndOfTurn / EagerEndOfTurn detection. Selectable from the Admin UI's Deepgram STT Model dropdown. End-to-end verified on the Voice Agent path (call ID `1778364955.475`, 2026-05-09). |
| `flux-general-multi` | Listen (STT) + turn detection | GA, conversational | Multilingual variant of Flux for non-English voice-agent deployments. |
| `aura-2-thalia-en` | Speak (TTS) | GA | Default voice. Browse the full Aura-2 voice catalog at [developers.deepgram.com/docs/tts-models](https://developers.deepgram.com/docs/tts-models). |

> **Repo state (2026-05-09):** the shipped `config/ai-agent.yaml` still defaults `model: nova-2` for backward compatibility with existing deployments. New deployments selecting Flux from the Admin UI dropdown will route through the Deepgram Voice Agent path with `listen.provider.model: flux-general-en` automatically — no manual yaml edit required. Prior to v6.5.0, the listen model was hardcoded to `nova-3` in `src/providers/deepgram.py`, so existing yaml overrides for `model:` were silently ignored; this is fixed in v6.5.0.

> **Advanced Flux tuning** (`eot_threshold`, `eager_eot_threshold`, prefix padding) is not yet exposed through the Admin UI's Deepgram form. The standalone Flux pipeline adapter at `src/pipelines/deepgram_flux.py` (registered as `deepgram_flux_stt`) supports these knobs for hybrid pipelines (Flux STT + non-Deepgram LLM/TTS). Wiring them through to the Voice Agent Settings JSON is a planned enhancement.

### 4. Configure Asterisk Dialplan

Add to `/etc/asterisk/extensions_custom.conf`:

```ini
[from-ai-agent-deepgram]
exten => s,1,NoOp(AI Voice Agent - Deepgram)
exten => s,n,Set(AI_CONTEXT=demo_deepgram)
exten => s,n,Set(AI_PROVIDER=deepgram)
exten => s,n,Stasis(asterisk-ai-voice-agent)
exten => s,n,Hangup()
```

**Recommended**: Set `AI_CONTEXT` and `AI_PROVIDER` when you want an explicit per-extension override:
- `AI_CONTEXT` selects the context (greeting, prompt, profile, tools)
- `AI_PROVIDER=deepgram` forces this provider for the call

If you omit these, the engine will select a context/provider using the precedence rules in `docs/Configuration-Reference.md`.

### 5. Reload Asterisk

```bash
asterisk -rx "dialplan reload"
```

### 6. Create FreePBX Custom Destination

1. Navigate to **Admin → Custom Destinations**
2. Click **Add Custom Destination**
3. Set:
   - **Target**: `from-ai-agent-deepgram,s,1`
   - **Description**: `Deepgram AI Agent`
4. Save and Apply Config

### 7. Test Call

Route a test call to the custom destination and verify:
- ✅ Greeting plays within 1-2 seconds
- ✅ AI responds to your questions naturally
- ✅ Duplex communication (can interrupt AI)
- ✅ Tools execute if configured (transfer, email, etc.)

## Context Configuration

Define your AI's behavior in `config/ai-agent.yaml`:

```yaml
contexts:
  demo_deepgram:
    greeting: "Hi {caller_name}, I'm Ava. How can I help you today?"
    profile: telephony_ulaw_8k
    prompt: |
      You are Ava, a helpful AI assistant for {company_name}.
      
      Your role is to assist callers with inquiries and route calls as needed.
      
      CONVERSATION STYLE:
      - Be friendly, professional, and concise
      - Speak naturally without filler words
      - Answer questions directly and clearly
      - Confirm user requests before executing tools
      
      CALL ENDING PROTOCOL:
      1. When user indicates they're done → ask "Is there anything else?"
      2. If user confirms done → say brief farewell + IMMEDIATELY call hangup_call
      3. NEVER leave call hanging in silence
      
      TOOL USAGE:
      - Use transfer tool to send callers to appropriate departments
      - Use email tools when caller requests transcript or summary
      - Always confirm actions with user before executing
```

**Template Variables**:
- `{caller_name}` - Caller ID name
- `{caller_number}` - Caller phone number
- `{company_name}` - Your company name (set in config)

## Tool Configuration

Enable tools for Deepgram in `config/ai-agent.yaml`:

```yaml
providers:
  deepgram:
    tools:
      - transfer              # Transfer calls to extensions/queues
      - cancel_transfer       # Cancel an active transfer
      - hangup_call           # End call with farewell
      - leave_voicemail       # Send caller to voicemail
      - send_email_summary    # Auto-send call summary
      - request_transcript    # Email transcript on request
```

**Tool Execution**: Deepgram natively supports function calling. Tools are executed automatically when the AI decides to use them based on conversation context.

## Troubleshooting

### Issue: "No Audio" or "Silence"

**Cause**: Sample rate or encoding mismatch

**Fix**:
```yaml
providers:
  deepgram:
    input_encoding: mulaw        # Must match Asterisk/transport
    input_sample_rate_hz: 8000   # Must match Asterisk/transport
```

### Issue: "High Latency" (>3 seconds)

**Cause**: Network latency or model selection

**Fix**:
1. Check network: `ping api.deepgram.com`
2. Confirm you're on `model: nova-3` (faster and more accurate than nova-2 for most use cases)
3. Verify API key not rate-limited

### Issue: "Tools Not Working"

**Cause**: Incorrect function calling format

**Fix**: Verify tools are in provider config (not pipeline-level). Deepgram uses its own function calling format - check logs for `FunctionCallRequest` events.

**See**: `docs/contributing/COMMON_PITFALLS.md#deepgram-function-calling`

### Issue: "AI Cuts Off Mid-Sentence"

**Cause**: Barge-in / gating too aggressive (telephony noise can trigger interruptions)

**Fix**:
```yaml
barge_in:
  enabled: true
  # Raise thresholds if the agent gets interrupted by line noise
  energy_threshold: 800
  min_ms: 200
```

## Production Considerations

### API Key Management
- Rotate keys every 90 days
- Use separate keys for dev/staging/production
- Monitor usage in Deepgram Console

### Cost Optimization
- Deepgram charges per minute of audio processed
- Monitor concurrent calls to manage costs
- Consider usage-based pricing tier for high volume

### Monitoring
- Track response latency in logs
- Monitor Deepgram API status: https://status.deepgram.com/
- Set up alerts for API errors or high latency

## See Also

- **Implementation & API Reference**: `docs/contributing/references/Provider-Deepgram-Implementation.md`
- **Golden Baseline**: `docs/case-studies/Deepgram-Agent-Golden-Baseline.md`
- **Common Pitfalls**: `docs/contributing/COMMON_PITFALLS.md`
- **Tool Calling Guide**: `docs/TOOL_CALLING_GUIDE.md`

---

**Deepgram Provider Setup - Complete** ✅

For questions or issues, see the [GitHub repository](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk).
