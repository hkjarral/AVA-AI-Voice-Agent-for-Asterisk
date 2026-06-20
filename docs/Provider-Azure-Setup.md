# Microsoft Azure Speech Service Provider Setup Guide

## Overview

Azure Speech Service provides STT (Speech-to-Text) and TTS (Text-to-Speech) capabilities for the AI voice agent pipeline. Use Azure STT/TTS alongside any LLM provider (OpenAI, Telnyx, local, etc.) in a modular pipeline.

**Performance**: Low-latency neural voices | WebSocket streaming STT | Global Azure regions

If you used the Admin UI Setup Wizard, you may not need to follow this guide end-to-end. For transport selection and first-call onboarding, see:
- `INSTALLATION.md`
- `Transport-Mode-Compatibility.md`

For provider/context selection details, see:
- `Configuration-Reference.md` -> "Call Selection & Precedence (Provider / Pipeline / Context)"

## Quick Start

### 1. Create Azure Speech Resource

1. Sign in to the [Azure Portal](https://portal.azure.com/)
2. Navigate to **Create a resource** -> search **Speech**
3. Click **Create** on **Speech** (under Azure AI services)
4. Select your subscription, resource group, region (e.g. `eastus`), and pricing tier (Free F0 or Standard S0)
5. After deployment, go to the resource and copy **Key 1** and **Region** from **Keys and Endpoint**

### 2. Configure Environment Variables

Add to your `.env` file:

```bash
# Microsoft Azure Speech Service
AZURE_SPEECH_KEY=your_speech_key_here
```

> The Azure **region** is not an environment variable -- set it via the `region`
> field on each Azure provider in `config/ai-agent.yaml` (see below).

**Test your key**:
```bash
curl -v "https://eastus.api.cognitive.microsoft.com/sts/v1.0/issuetoken" \
  -H "Ocp-Apim-Subscription-Key: ${AZURE_SPEECH_KEY}" \
  -H "Content-Length: 0" -X POST
```

A successful response returns a JWT token.

### 3. Configure Providers

In `config/ai-agent.yaml`, enable the Azure STT and TTS providers:

```yaml
providers:
  azure_stt:
    type: azure
    capabilities: [stt]
    enabled: true
    region: eastus            # Azure region for this resource (set here, not in .env)
    language: en-US           # BCP-47 locale
    variant: realtime         # "realtime" (REST one-shot) or "fast" (Fast Transcription API)
    request_timeout_sec: 15.0

  azure_tts:
    type: azure
    capabilities: [tts]
    enabled: true
    region: eastus
    voice_name: en-US-JennyNeural
    output_format: riff-8khz-16bit-mono-pcm
    target_encoding: mulaw
    target_sample_rate_hz: 8000
    chunk_size_ms: 20
    request_timeout_sec: 15.0
```

### 4. Wire into a Pipeline

Pair Azure STT/TTS with any LLM provider:

```yaml
pipelines:
  azure_hybrid:
    stt: azure_stt
    llm: openai_llm          # or telnyx_llm, local_llm, etc.
    tts: azure_tts
    options:
      stt:
        chunk_ms: 160
        mode: stt
        stream_format: pcm16_16k
        streaming: true
      tts:
        format:
          encoding: mulaw
          sample_rate: 8000

active_pipeline: azure_hybrid
```

### 5. Configure Asterisk Dialplan

Add to `/etc/asterisk/extensions_custom.conf`:

```ini
[from-ai-agent-azure]
exten => s,1,NoOp(AI Voice Agent - Azure Speech)
exten => s,n,Set(AI_PROVIDER=azure_hybrid)
exten => s,n,Stasis(asterisk-ai-voice-agent)
exten => s,n,Hangup()
```

Then reload: `asterisk -rx "dialplan reload"`

## STT Variants

Azure STT supports two variants controlled by the `variant` field:

| Variant | Method | Best For |
|---------|--------|----------|
| `realtime` (default) | REST POST with binary WAV | General telephony STT, low-latency single utterances |
| `fast` | Fast Transcription API (multipart POST) | Batch-style transcription, longer audio segments |

Switch variants in YAML:
```yaml
providers:
  azure_stt:
    variant: fast   # or "realtime"
```

You can also override endpoint URLs if needed:
- `fast_stt_base_url` -- overrides the Fast Transcription endpoint
- `realtime_stt_base_url` -- overrides the Real-Time STT endpoint

## TTS Voice and SSML Options

### Voice Selection

Azure offers 400+ neural voices. Set via `voice_name`:

```yaml
providers:
  azure_tts:
    voice_name: en-US-JennyNeural    # Default, natural female
```

Popular telephony voices:
- `en-US-JennyNeural` -- Natural female (default)
- `en-US-GuyNeural` -- Natural male
- `en-US-AriaNeural` -- Expressive female
- `es-ES-AlvaroNeural` -- Spanish male

Browse all voices at: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts

### SSML Prosody Controls

Fine-tune speech output with prosody settings:

```yaml
providers:
  azure_tts:
    prosody_pitch: "+5%"     # Pitch shift: "+10%", "-5%", "high", "low"
    prosody_rate: "medium"   # Speed: "slow", "medium", "fast", "+20%", "0.8"
```

### Multilingual Voices

For multilingual voices speaking a different language than their base:

```yaml
providers:
  azure_tts:
    voice_name: en-US-JennyMultilingualNeural
    lang_tag: es-MX          # Spoken language override
```

### Downstream Mode Override

Override the global `downstream_mode` per-provider:

```yaml
providers:
  azure_tts:
    downstream_mode_override: stream   # "auto" | "stream" | "file"
```

## Admin UI Quick-Add

1. Open the Admin UI and navigate to **Provider Settings**
2. Click **Add Provider**
3. Select **Azure Speech STT** or **Azure Speech TTS**
4. Enter your `AZURE_SPEECH_KEY` and select the Azure region
5. Choose your voice (TTS) or variant (STT)
6. Save -- the provider is auto-wired into your active pipeline

## Troubleshooting

### Issue: "401 Unauthorized"

**Cause**: Invalid or expired speech key.

**Fix**:
1. Verify `AZURE_SPEECH_KEY` in `.env` matches the Azure Portal key
2. Re-run the token test curl command above
3. Ensure no trailing whitespace in the key

### Issue: "No audio / empty transcription"

**Cause**: Region mismatch or wrong audio format.

**Fix**:
1. Confirm `region` in YAML matches the region of your Azure Speech resource
2. Verify upstream audio is PCM 16kHz 16-bit mono (expected by Azure STT)
3. Check `variant` setting -- try switching between `realtime` and `fast`

### Issue: "High TTS latency"

**Cause**: Region distance or output format overhead.

**Fix**:
1. Use an Azure region close to your server (e.g. `eastus` for US East Coast)
2. Enable streaming: `streaming: true` (default)
3. Use telephony-optimised format: `riff-8khz-16bit-mono-pcm`

### Issue: "Voice not found"

**Cause**: Invalid `voice_name` or voice not available in your region.

**Fix**:
1. Check available voices for your region: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts
2. Ensure the voice name matches exactly (e.g. `en-US-JennyNeural`, not `Jenny`)
3. Neural voices require Standard S0 tier (not Free F0 for some voices)

## See Also

- **Azure Speech Service docs**: https://learn.microsoft.com/azure/ai-services/speech-service/
- **Voice gallery**: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts
- **Configuration Reference**: `docs/Configuration-Reference.md`
- **Tool Calling Guide**: `docs/TOOL_CALLING_GUIDE.md`

---

**Microsoft Azure Speech Service Provider Setup - Complete**

For questions or issues, see the [GitHub repository](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk).
