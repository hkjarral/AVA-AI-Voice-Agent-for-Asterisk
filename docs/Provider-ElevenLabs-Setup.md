# ElevenLabs Provider Setup Guide

## Overview

ElevenLabs Conversational AI is a full-agent provider that combines speech-to-text, LLM reasoning, and high-quality text-to-speech in a single streaming API. Ideal for applications requiring premium voice quality with natural conversation flow.

**Performance**: 1-2 second response latency | Full duplex | Client-side tool execution

> **Note**: This guide covers **both** the **full-agent** provider (`elevenlabs_agent`) and the **TTS-only pipeline adapter** (`elevenlabs_tts`) for modular pipelines. See [ElevenLabs TTS Pipeline Adapter](#elevenlabs-tts-pipeline-adapter) for modular setup.

If you used the Admin UI Setup Wizard, you may not need to follow this guide end-to-end. For first-call onboarding and transport selection, see:
- `INSTALLATION.md`
- `Transport-Mode-Compatibility.md`

For how provider/context selection works (including `AI_CONTEXT` / `AI_PROVIDER`), see:
- `Configuration-Reference.md` → "Call Selection & Precedence (Provider / Pipeline / Context)"

## Quick Start

### 1. Create ElevenLabs Agent

1. Sign up at [ElevenLabs](https://elevenlabs.io/)
2. Navigate to [Agents Dashboard](https://elevenlabs.io/app/agents)
3. Click **"Create Agent"**
4. Configure your agent:
   - **Name**: Your agent name (e.g., "Customer Support Agent")
   - **Voice**: Select from the voice library
   - **First Message**: Initial greeting
   - **System Prompt**: Define behavior and personality
   - **LLM Model**: Select model (GPT-4o, Claude, etc.)

### 2. Enable Agent Security (Required)

**CRITICAL**: Authentication must be enabled for API access.

1. In agent settings, go to **"Security"** tab
2. Enable **"Require authentication"**
3. This allows secure signed URL connections

Without authentication, the agent cannot be accessed via API.

### 3. Get Credentials

1. Get your **API Key** from [API Keys](https://elevenlabs.io/app/settings/api-keys)
2. Get your **Agent ID** from the agent dashboard URL (format: `agent_xxxxxxxxxxxxxxxxxxxxxxxxxxxx`)

### 4. Configure Environment Variables

Add to your `.env` file:

```bash
# ElevenLabs Conversational AI (required)
ELEVENLABS_API_KEY=xi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ELEVENLABS_AGENT_ID=agent_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**Test API Key**:
```bash
# Test API key and Agent ID together (from project root)
curl -X GET "https://api.elevenlabs.io/v1/convai/conversation/get_signed_url?agent_id=$(grep ELEVENLABS_AGENT_ID .env | cut -d'=' -f2)" \
  -H "xi-api-key: $(grep ELEVENLABS_API_KEY .env | cut -d'=' -f2)"
```

### 5. Configure Provider

The ElevenLabs provider is configured in `config/ai-agent.yaml`:

```yaml
providers:
  elevenlabs_agent:
    enabled: true
    type: full
    capabilities: ["stt", "llm", "tts"]

    # Credentials are loaded from environment variables (env-only):
    # - ELEVENLABS_API_KEY
    # - ELEVENLABS_AGENT_ID
    
    # Transport/provider audio formats
    input_encoding: ulaw
    input_sample_rate_hz: 8000
    provider_input_encoding: pcm16
    provider_input_sample_rate_hz: 16000
    output_encoding: pcm16
    output_sample_rate_hz: 16000
    target_encoding: ulaw
    target_sample_rate_hz: 8000

    # Voice/model (examples; use values from your ElevenLabs account)
    voice_id: uDsPstFWFBUXjIBimV7s
    model_id: eleven_flash_v2_5
```

**Key Settings**:
- Telephony ingress is typically μ-law @ 8 kHz; the engine handles resampling to ElevenLabs’ native PCM16
- `ELEVENLABS_AGENT_ID` is required (used to fetch a signed URL)
- Voice/model can be configured in YAML (voice/model IDs) and in the ElevenLabs dashboard (agent behavior)
- Greeting and prompt can be overridden from context YAML (see Dynamic Variables section)

### 6. Configure Asterisk Dialplan

Add to `/etc/asterisk/extensions_custom.conf`:

```ini
[from-ai-agent-elevenlabs]
exten => s,1,NoOp(AI Voice Agent - ElevenLabs)
exten => s,n,Set(AI_CONTEXT=demo_elevenlabs)
exten => s,n,Set(AI_PROVIDER=elevenlabs_agent)
exten => s,n,Stasis(asterisk-ai-voice-agent)
exten => s,n,Hangup()
```

**Recommended**: Set `AI_CONTEXT` and `AI_PROVIDER` when you want an explicit per-extension override:
- `AI_CONTEXT` selects the context (profile, tools)
- `AI_PROVIDER=elevenlabs_agent` forces this provider for the call

If you omit these, the engine will select a context/provider using the precedence rules in `docs/Configuration-Reference.md`.

### 7. Reload Asterisk

```bash
asterisk -rx "dialplan reload"
```

### 8. Create FreePBX Custom Destination

1. Navigate to **Admin → Custom Destinations**
2. Click **Add Custom Destination**
3. Set:
   - **Target**: `from-ai-agent-elevenlabs,s,1`
   - **Description**: `ElevenLabs AI Agent`
4. Save and Apply Config

### 9. Test Call

Route a test call to the custom destination and verify:
- ✅ Greeting plays within 1-2 seconds
- ✅ AI responds with high-quality voice
- ✅ Duplex communication works (can interrupt AI)
- ✅ Tools execute if configured (hangup, transfer, etc.)

## Tool Configuration

ElevenLabs uses **Client Tools** - tools defined in the dashboard but executed by this system.

### Add Tools to ElevenLabs Dashboard

1. In agent settings, go to **"Tools"** tab
2. Click **"Add Tool"** → **"Client Tool"**
3. Add each tool schema below
4. Ensure tools show your agent in "Dependent agents"

### hangup_call

```json
{
  "type": "client",
  "name": "hangup_call",
  "description": "You MUST call this tool to properly end the conversation when the user says goodbye, thanks for your help, that's all I need, or any farewell phrase. Without calling this tool, the call will not end.",
  "disable_interruptions": false,
  "force_pre_tool_speech": "auto",
  "assignments": [],
  "tool_call_sound": null,
  "tool_call_sound_behavior": "auto",
  "execution_mode": "immediate",
  "expects_response": true,
  "response_timeout_secs": 5,
  "parameters": [
    {
      "id": "farewell_message",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "A warm farewell message to say before ending the call",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": false
    }
  ],
  "dynamic_variables": {
    "dynamic_variable_placeholders": {}
  }
}
```

> **Important**: The description must be imperative - simply saying "goodbye" does NOT end the call. The LLM must invoke this tool.

### transfer

```json
{
  "type": "client",
  "name": "transfer",
  "description": "Transfer the caller to another extension or department. Use when the caller asks to speak with a live person, agent, or specific department like sales or support.",
  "disable_interruptions": false,
  "force_pre_tool_speech": "auto",
  "assignments": [],
  "tool_call_sound": null,
  "tool_call_sound_behavior": "auto",
  "execution_mode": "immediate",
  "expects_response": true,
  "response_timeout_secs": 45,
  "parameters": [
    {
      "id": "target",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "Extension number or department name (e.g., '2765', 'sales', 'support', 'live agent')",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": true
    }
  ],
  "dynamic_variables": {
    "dynamic_variable_placeholders": {}
  }
}
```

### leave_voicemail

```json
{
  "type": "client",
  "name": "leave_voicemail",
  "description": "Send the caller to voicemail so they can leave a message. Use when caller wants to leave a message or when transfer fails.",
  "disable_interruptions": false,
  "force_pre_tool_speech": "auto",
  "assignments": [],
  "tool_call_sound": null,
  "tool_call_sound_behavior": "auto",
  "execution_mode": "immediate",
  "expects_response": true,
  "response_timeout_secs": 15,
  "parameters": [],
  "dynamic_variables": {
    "dynamic_variable_placeholders": {}
  }
}
```

### cancel_transfer

```json
{
  "type": "client",
  "name": "cancel_transfer",
  "description": "Cancel the current transfer if it hasn't been answered yet. Use when caller changes their mind during a transfer.",
  "disable_interruptions": false,
  "force_pre_tool_speech": "auto",
  "assignments": [],
  "tool_call_sound": null,
  "tool_call_sound_behavior": "auto",
  "execution_mode": "immediate",
  "expects_response": true,
  "response_timeout_secs": 5,
  "parameters": [],
  "dynamic_variables": {
    "dynamic_variable_placeholders": {}
  }
}
```

### send_email_summary (Optional)

```json
{
  "type": "client",
  "name": "send_email_summary",
  "description": "Send an email summary of the call to a specified email address.",
  "disable_interruptions": false,
  "force_pre_tool_speech": "auto",
  "assignments": [],
  "tool_call_sound": null,
  "tool_call_sound_behavior": "auto",
  "execution_mode": "immediate",
  "expects_response": true,
  "response_timeout_secs": 10,
  "parameters": [
    {
      "id": "recipient_email",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "Email address to send the call summary to",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": true
    }
  ],
  "dynamic_variables": {
    "dynamic_variable_placeholders": {}
  }
}
```

> **Note**: This tool is typically triggered automatically at call end. Only add if you want the AI to explicitly offer summaries.

### request_transcript

```json
{
  "type": "client",
  "name": "request_transcript",
  "description": "Send call transcript to caller's email address. Use this when caller says yes to the transcript offer, or when they explicitly request a transcript. IMPORTANT: Before calling this tool, you MUST ask for the email, read it back clearly (spell it out), and get confirmation that it's correct.",
  "disable_interruptions": false,
  "force_pre_tool_speech": "auto",
  "assignments": [],
  "tool_call_sound": null,
  "tool_call_sound_behavior": "auto",
  "execution_mode": "immediate",
  "expects_response": true,
  "response_timeout_secs": 10,
  "parameters": [
    {
      "id": "caller_email",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "Caller's email address. Parse from speech: 'john dot smith at gmail dot com' becomes 'john.smith@gmail.com'",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": true
    }
  ],
  "dynamic_variables": {
    "dynamic_variable_placeholders": {}
  }
}
```

## Context Configuration

Define your context in `config/ai-agent.yaml`:

```yaml
contexts:
  demo_elevenlabs:
    provider: elevenlabs_agent
    profile: telephony_ulaw_8k
    greeting: "Hi {caller_name}, I'm your AI assistant. How can I help you today?"
    prompt: |
      You are a helpful voice assistant.
      Keep responses short (1-3 sentences).
      Always offer to email a transcript before ending the call.
    tools:
      - hangup_call
      - transfer
      - leave_voicemail
      - cancel_transfer
      - request_transcript
```

**Context fields override ElevenLabs dashboard settings** when the corresponding toggles are enabled in Security → Overrides.

> **Tool Names**: Use standard names (`transfer`, `hangup_call`, etc.) in both the context `tools:` list and the ElevenLabs dashboard. Note: `transfer` is an alias of canonical `blind_transfer`.

## Dynamic Variables & Overrides

ElevenLabs supports runtime personalization through dynamic variables and configuration overrides. **This aligns ElevenLabs with other full providers** - your context's `greeting` and `prompt` control the agent behavior, not the dashboard settings.

> **Important**: Unlike other providers where tools are sent via API, **ElevenLabs tools must be configured in the dashboard**. Only greeting and system prompt can be overridden from context YAML.

### Enabling Overrides (Required)

You **MUST** enable these toggles in ElevenLabs Dashboard → Agent → **Security** tab → **Overrides**:

| Dashboard Toggle | Context Field | Effect |
|------------------|---------------|--------|
| **First message** | `greeting` | Context greeting overrides dashboard first message |
| **System prompt** | `prompt` | Context prompt overrides dashboard system prompt |

> **Without enabling these toggles**, the dashboard values will be used and your context settings will be ignored.

### Available Dynamic Variables

The following variables are automatically passed and can be used in your greeting or prompt:

| Variable | Description | Example Value |
|----------|-------------|---------------|
| `{caller_name}` | Caller's name from CID | "JOHN SMITH" |
| `{caller_id}` | Caller's phone number | "13165551234" |

### Usage Example

```yaml
contexts:
  personalized_support:
    provider: elevenlabs_agent
    greeting: "Hi {caller_name}, thank you for calling! How can I help?"
    prompt: |
      You are speaking with {caller_name} (phone: {caller_id}).
      Personalize responses using their name.
```

### How It Works

1. Engine extracts `caller_name` and `caller_id` from the call session
2. Variables are substituted into `greeting` and `prompt` before sending
3. Substituted values are sent via `conversation_config_override`
4. ElevenLabs uses these instead of dashboard defaults

**Note**: Tools are NOT overridable - they must be configured in ElevenLabs dashboard.

### Architecture Alignment

With overrides enabled, ElevenLabs now works like other full providers in this project:

| Component | Deepgram/OpenAI Realtime | ElevenLabs |
|-----------|--------------------------|------------|
| **Greeting** | Context YAML → API | Context YAML → Override |
| **System Prompt** | Context YAML → API | Context YAML → Override |
| **Tools** | Context YAML → API | **Dashboard only** |
| **Voice/Model** | API or Dashboard | Dashboard only |

This means you can use the same context configuration across providers - just switch the `provider:` field and your greeting/prompt will work consistently.

### System Prompt Best Practice

Add a **CALL ENDING PROTOCOL** at the TOP of your system prompt to ensure transcript is offered before hangup:

```text
CALL ENDING PROTOCOL (MUST FOLLOW EXACTLY):
When the caller indicates they're done (goodbye, thanks, that's all, etc.):
1. FIRST ask: "Before you go, would you like me to email you a transcript of our conversation?"
2. If they say YES:
   - Ask for their email address
   - Read it back and spell it out for confirmation
   - Use request_transcript tool
   - THEN use hangup_call with a warm farewell
3. If they say NO:
   - Use hangup_call tool with a warm farewell
4. NEVER skip the transcript offer - always ask before hanging up
```

**Tip**: Place important behavioral instructions at the TOP of the system prompt for highest priority.

## Troubleshooting

### Issue: "No Audio" or "Silence"

**Cause**: Environment variables not set or agent security not enabled

**Fix**:
1. Verify `.env` has both `ELEVENLABS_API_KEY` and `ELEVENLABS_AGENT_ID`
2. Ensure agent has "Require authentication" enabled in dashboard
3. Check logs: `docker logs ai_engine 2>&1 | grep -i elevenlabs`

### Issue: "Connection Timeout"

**Cause**: Invalid API key or agent ID

**Fix**:
1. Test API key: `curl -H "xi-api-key: $ELEVENLABS_API_KEY" https://api.elevenlabs.io/v1/user`
2. Verify agent ID matches dashboard URL
3. Check network connectivity to elevenlabs.io

### Issue: "Greeting/Prompt Override Not Working"

**Cause**: Override toggles not enabled in ElevenLabs dashboard

**Fix**:
1. Go to ElevenLabs Dashboard → Agent → **Security** tab
2. Enable **First message** toggle (for greeting override)
3. Enable **System prompt** toggle (for prompt override)
4. Save and wait 30 seconds for changes to propagate
5. Check logs for `Override first_message` and `Override system_prompt` entries

### Issue: "Tools Not Working"

**Cause**: Tools not configured in ElevenLabs dashboard or names don't match

**Fix**:
1. Verify tool schemas are added in ElevenLabs Agent → Tools tab
2. Ensure tool names match exactly: `hangup_call`, `transfer`, `leave_voicemail`, `cancel_transfer`, `request_transcript`
3. Check tools are linked to your agent ("Dependent agents")
4. Check logs for `client_tool_call` events

### Issue: "AI Doesn't Hang Up"

**Cause**: `hangup_call` tool not configured in ElevenLabs dashboard

**Fix**:
1. Add `hangup_call` tool schema to agent's Tools tab
2. Update agent's system prompt to use the tool when user says goodbye
3. Example prompt addition: "When the user says goodbye or indicates they want to end the call, use the hangup_call tool."

### Issue: "Second Call Fails"

**Cause**: Provider state not reset between calls (fixed in v4.4.1)

**Fix**: Update to latest version - this was fixed in commit e123a45.

## Production Considerations

### API Key Management
- API keys are loaded from environment variables only (for security)
- Rotate keys periodically
- Use separate keys for dev/staging/production

### Cost Optimization
- ElevenLabs charges per character of generated speech
- Monitor usage in ElevenLabs dashboard
- Consider voice selection (some voices cost more)

### Monitoring
- Track response latency in logs
- Monitor ElevenLabs API status
- Set up alerts for connection failures

### Voice Quality
- ElevenLabs offers premium voice quality
- Test different voices for your use case
- Adjust voice settings (stability, similarity) in dashboard

## ElevenLabs TTS Pipeline Adapter

In addition to the full-agent provider, ElevenLabs can be used as a **TTS-only component** in modular pipelines (e.g., `local_stt` → `openai_llm` → `elevenlabs_tts`). This gives you premium ElevenLabs voice quality while choosing your own STT and LLM providers.

### Prerequisites

- `ELEVENLABS_API_KEY` in your `.env` file (same key used for the full agent)
- No `ELEVENLABS_AGENT_ID` needed — this uses the TTS API directly

### Provider Configuration

A default `elevenlabs_tts` provider is included in `config/ai-agent.golden-elevenlabs.yaml`. To use it, add the provider to your `config/ai-agent.yaml` under `providers:`:

```yaml
providers:
  elevenlabs_tts:
    type: elevenlabs
    enabled: true
    capabilities:
      - tts
    voice_id: "21m00Tcm4TlvDq8ikWAM"   # Rachel (warm, professional)
    model_id: "eleven_turbo_v2_5"        # Fast, high-quality
    output_format: "ulaw_8000"           # Telephony-optimized
    stability: 0.5
    similarity_boost: 0.75
    style: 0.0
    use_speaker_boost: true
```

**Key settings**:
- **`voice_id`**: Choose from the [ElevenLabs Voice Library](https://elevenlabs.io/voice-library). Default is Rachel (`21m00Tcm4TlvDq8ikWAM`).
- **`model_id`**: `eleven_turbo_v2_5` offers the best balance of speed and quality for telephony.
- **`output_format`**: Must be `ulaw_8000` for telephony. ElevenLabs returns μ-law encoded audio at 8 kHz.

### Pipeline Configuration

Reference `elevenlabs_tts` as the TTS component in your pipeline:

```yaml
pipelines:
  local_hybrid:
    stt: local_stt
    llm: openai_llm
    tts: elevenlabs_tts    # ElevenLabs for premium voice quality
    options:
      stt:
        mode: stt
        streaming: true
        stream_format: pcm16_16k
      llm:
        model: gpt-4o-mini
        temperature: 0.7
        max_tokens: 150
      tts:
        format:
          encoding: mulaw
          sample_rate: 8000
```

### Audio Profile

The recommended audio profile for ElevenLabs TTS pipelines is **`telephony_ulaw_8k`**:

```yaml
contexts:
  demo_hybrid:
    pipeline: local_hybrid
    profile: telephony_ulaw_8k    # Required for ElevenLabs TTS
    greeting: "Hi, I'm Ava. How can I help you today?"
    prompt: "You are a helpful AI assistant."
```

### Transport Compatibility

| Transport | Status | Notes |
|-----------|--------|-------|
| **AudioSocket** | ✅ Validated | Streaming playback via AudioSocket |
| **ExternalMedia RTP** | ✅ Validated | Streaming playback via RTP |

Both transports are supported. The engine automatically handles format conversion between ElevenLabs μ-law output and the transport wire format.

### Barge-In Tuning for Speakerphone

When callers use **speakerphone**, the agent's own TTS audio can feed back through the caller's microphone, causing false barge-in interruptions that cut off the agent mid-sentence.

**Recommended barge-in settings for ElevenLabs TTS pipelines:**

```yaml
barge_in:
  enabled: true
  energy_threshold: 1000         # Higher than default (700) to ignore echo
  min_ms: 250                    # Minimum speech duration to trigger
  cooldown_ms: 500               # Cooldown between barge-in events
  post_tts_end_protection_ms: 600  # Prevents false triggers after TTS ends
  provider_output_suppress_ms: 1200
```

The critical setting is **`post_tts_end_protection_ms: 600`** (default is 250). This prevents the system from interpreting speakerphone echo as a barge-in attempt immediately after each TTS segment finishes. You can configure this in the Admin UI under **Barge-in → Post-TTS Protection (ms)**.

> **Tip**: If you still experience greeting cutoff on speakerphone, try increasing `initial_protection_ms` to 500-1000ms. This protects the beginning of each TTS utterance.

### Troubleshooting (Pipeline TTS)

**Issue: "Pipeline TTS validation FAILED — No base_url/ws_url configured"**

Cause: The `elevenlabs_tts` provider was created through the Admin UI with empty fields.

Fix: Use the YAML configuration above instead, or ensure the Admin UI provider has `type: elevenlabs` and `capabilities: [tts]` set correctly. The engine auto-discovers the ElevenLabs API base URL.

**Issue: "Pipeline is invalid — cannot resolve tts component 'elevenlabs_tts'"**

Cause: Provider name typo or missing provider configuration.

Fix: Verify `elevenlabs_tts` is defined under `providers:` in your config and `ELEVENLABS_API_KEY` is set in `.env`.

**Issue: Audio sounds garbled or too fast**

Cause: Audio profile mismatch.

Fix: Ensure your context uses `profile: telephony_ulaw_8k` and the provider has `output_format: ulaw_8000`.

## See Also

- **Implementation & API Reference**: `docs/contributing/references/Provider-ElevenLabs-Implementation.md`
- **Configuration Reference**: `docs/Configuration-Reference.md#elevenlabs-agent-monolithic-agent`
- **Common Pitfalls**: `docs/contributing/COMMON_PITFALLS.md`
- **Tool Calling Guide**: `docs/TOOL_CALLING_GUIDE.md`

---

**ElevenLabs Provider Setup - Complete** ✅

For questions or issues, see the [GitHub repository](https://github.com/hkjarral/Asterisk-AI-Voice-Agent).
