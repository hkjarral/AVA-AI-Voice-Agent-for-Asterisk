# Google Provider Setup Guide

## Overview

Google AI integration provides two modes for the Asterisk AI Voice Agent:

1. **Google Live (Recommended)** - Real-time bidirectional streaming with Gemini 2.5 Flash, native audio processing, ultra-low latency (<1s), and true duplex communication
2. **Modular Pipelines (optional)** - Operator-defined pipelines that use Google adapters (`google_stt`, `google_llm`, `google_tts`) for STT/LLM/TTS

This guide covers setup for both modes.

If you used the Admin UI Setup Wizard, you may not need to follow this guide end-to-end. For first-call onboarding and transport selection, see:
- `INSTALLATION.md`
- `Transport-Mode-Compatibility.md`

For how provider/context selection works (including `AI_CONTEXT` / `AI_PROVIDER`), see:
- `Configuration-Reference.md` → "Call Selection & Precedence (Provider / Pipeline / Context)"

## Quick Start

### 1. Enable Google Cloud APIs

In your Google Cloud Console, enable these APIs:

1. **Cloud Speech-to-Text API**: https://console.cloud.google.com/apis/library/speech.googleapis.com
2. **Cloud Text-to-Speech API**: https://console.cloud.google.com/apis/library/texttospeech.googleapis.com
3. **Generative Language API**: https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com

### 2. Configure API Key

Add your Google API key to `.env`:

```bash
# Google AI (used by google_live and/or Google pipeline adapters)
GOOGLE_API_KEY=your_api_key_here
```

**OR** use a service account (recommended for production):

```bash
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
```

### 3. Configure Asterisk Dialplan

**For Google Live (Recommended):**

```ini
[from-ai-agent]
exten => s,1,NoOp(AI Voice Agent - Google Live)
exten => s,n,Set(AI_CONTEXT=demo_google_live)
exten => s,n,Set(AI_PROVIDER=google_live)
exten => s,n,Stasis(asterisk-ai-voice-agent)
exten => s,n,Hangup()
```

**For Google Cloud Pipeline:**

```ini
[from-ai-agent]
exten => s,1,NoOp(AI Voice Agent - Google Cloud Pipeline)
exten => s,n,Set(AI_CONTEXT=demo_google)
exten => s,n,Set(AI_PROVIDER=google_cloud_full)  ; example pipeline name (you define this in `pipelines:`)
exten => s,n,Stasis(asterisk-ai-voice-agent)
exten => s,n,Hangup()
```

**Recommended**: Set `AI_CONTEXT` and `AI_PROVIDER` when you want an explicit per-extension override:
- `AI_CONTEXT` selects the context (greeting, prompt, profile, tools)
- `AI_PROVIDER` selects the provider (e.g., `google_live`) or a pipeline name you defined under `pipelines:` (e.g., `google_cloud_full`)

If you omit these, the engine will select a context/provider using the precedence rules in `docs/Configuration-Reference.md`.

### 4. Restart Asterisk

```bash
asterisk -rx "dialplan reload"
```

## Pipelines (Optional / Operator-Defined)

Pipeline names are **not shipped/validated by default**. If you want a Google-based pipeline, you define it under `pipelines:` and then select it via `default_provider` or `AI_PROVIDER`.

### 1) Enable Google pipeline adapters

Google pipeline adapters are registered from `providers.google` (this is separate from the full-agent `providers.google_live`).

```yaml
providers:
  google:
    # Credentials are loaded from the environment (env-only):
    # - GOOGLE_API_KEY (API key auth) OR
    # - GOOGLE_APPLICATION_CREDENTIALS (service account JSON path)
    enabled: true
```

### 2) Example pipeline templates

**Example A: all-Google pipeline**

```yaml
pipelines:
  google_cloud_full:        # example pipeline name
    stt: google_stt
    llm: google_llm
    tts: google_tts
    options:
      stt:
        language_code: en-US
      tts:
        voice_name: "en-US-Neural2-A"
```

**Example B: Google STT/TTS + OpenAI LLM**

```yaml
pipelines:
  google_hybrid_openai:     # example pipeline name
    stt: google_stt
    llm: openai_llm
    tts: google_tts
```

## Troubleshooting

### Issue: Greeting plays but no responses

**Cause**: Google Cloud APIs not enabled or API key lacks permissions.

**Solution**: 
1. Enable all three APIs in Google Cloud Console (see Quick Start)
2. Verify API key has permissions for Speech-to-Text, Text-to-Speech, and Generative Language
3. Restart `ai_engine` container

### Issue: Falls back to local_hybrid pipeline

**Cause**: Missing `AI_PROVIDER` channel variable in dialplan.

**Solution**: Add both variables to dialplan:
```ini
exten => s,n,Set(AI_CONTEXT=demo_google)
exten => s,n,Set(AI_PROVIDER=google_cloud_full)
```

### Issue: "Pipeline not found" error

**Cause**: Typo in pipeline name or pipelines not validating on startup.

**Solution**: 
1. Check `docker logs ai_engine` for pipeline validation results
2. Verify the pipeline name exists under `pipelines:` (names are operator-defined)
3. Fix any validation errors printed during engine startup

### Verify Configuration

Check engine logs:
```bash
docker logs ai_engine 2>&1 | grep -E "google|Pipeline validation|Engine started"
```

Expected output:
```
Engine started and listening for calls
```

## Advanced Configuration

### Custom Voices

Edit `config/ai-agent.yaml` to change TTS voices:

```yaml
pipelines:
  google_cloud_full:
    options:
      tts:
        voice_name: "en-US-Neural2-C"  # Male voice
        # Other options: Neural2-A (female), Neural2-D (male), Neural2-F (female)
```

### Multi-Language Support

Change STT language in pipeline options:

```yaml
pipelines:
  google_cloud_full:
    options:
      stt:
        language_code: "es-ES"  # Spanish
        # Chirp 3 supports 100+ languages
```

### Adjust Response Speed

Modify speaking rate:

```yaml
pipelines:
  google_cloud_full:
    options:
      tts:
        speaking_rate: 1.1  # 10% faster (range: 0.25 to 4.0)
```

## IAM Roles Required

If using service account authentication, grant these roles:

- **Cloud Speech-to-Text User** (`roles/speech.user`)
- **Cloud Text-to-Speech User** (`roles/texttospeech.user`)
- **Generative AI User** (`roles/aiplatform.user`)

Google Live / Generative Language access is authenticated with API key or OAuth credentials. There is no separate `roles/generativelanguage.liveapi.user` IAM role.

---

# Google Gemini Live API (Real-Time Agent)

## Overview

**Google Live** (`AI_PROVIDER=google_live`) is a **real-time bidirectional streaming** voice agent similar to OpenAI Realtime. It provides:

- ✅ **Native audio processing** (no separate STT/TTS)
- ✅ **Built-in Voice Activity Detection (VAD)**
- ✅ **True barge-in support** (interrupt naturally)
- ✅ **Ultra-low latency** (<1s)
- ✅ **Function calling in streaming mode**
- ✅ **Session management for context**

### When to Use Google Live vs Pipeline Mode

| Feature | Google Live | Pipeline Mode |
|---------|------------|---------------|
| **Architecture** | Native audio end-to-end | Sequential STT→LLM→TTS |
| **Latency** | <1s | 1.5-2.5s |
| **Barge-in** | ✅ Yes (automatic) | ❌ No |
| **Turn-taking** | Duplex (simultaneous) | Sequential (wait for TTS) |
| **Best for** | Conversations, support | Debugging, batch processing |

## Setup

### 1. Enable Gemini Live API

Enable the **Gemini Live API** in Google Cloud Console:
https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com

### 2. Configure API Key

Use the same `GOOGLE_API_KEY` from pipeline setup:

```bash
GOOGLE_API_KEY=your_api_key_here
```

The setup wizard validates the key with Google's `models.list` endpoint. If that endpoint succeeds but does not advertise any `bidiGenerateContent` models, the wizard now treats Live model discovery as inconclusive instead of blocking setup. It will warn and continue with `gemini-2.5-flash-native-audio-latest`; if runtime calls fail, verify Live API access, billing/quota, and the currently available models in AI Studio.

### 3. Update Dialplan

```asterisk
[from-ai-agent]
exten => s,1,NoOp(Google Live API Demo)
same => n,Set(AI_CONTEXT=demo_google_live)
same => n,Set(AI_PROVIDER=google_live)  ; Use real-time agent
same => n,Stasis(asterisk-ai-voice-agent)
same => n,Hangup()
```

### 4. Test the Agent

Call your extension and:
- **Say something** → Agent responds in real-time
- **Interrupt the agent mid-sentence** → It stops and listens
- **Have a natural conversation** → Context is maintained

## Architecture

### Audio Flow

```
Asterisk (8kHz µ-law) 
    ↓ Transcode to 16kHz PCM
    ↓ Stream to Gemini Live API (WebSocket)
    ↓ Receive 24kHz PCM audio
    ↓ Resample to 8kHz µ-law
    ↓ Back to Asterisk
```

### Key Components

1. **GoogleLiveProvider** (`src/providers/google_live.py`)
   - WebSocket management
   - Audio transcoding (µ-law ↔ PCM, 8kHz ↔ 16kHz/24kHz)
   - Session setup and lifecycle

2. **GoogleToolAdapter** (`src/tools/adapters/google.py`)
   - Function calling support
   - Tool execution integration

3. **Audio Resampling**
   - Input: 8kHz µ-law → 16kHz PCM (Gemini input)
   - Output: 24kHz PCM → 8kHz µ-law (Asterisk output)

## Configuration

Google Live is configured under `providers.google_live` in `config/ai-agent.yaml`:

```yaml
providers:
  google_live:
    # Credentials are loaded from the environment (env-only):
    # - GOOGLE_API_KEY (API key auth) OR
    # - GOOGLE_APPLICATION_CREDENTIALS (service account JSON path)
    enabled: true
    type: full
    capabilities: ["stt", "llm", "tts"]

    # Transport/provider audio formats
    input_encoding: ulaw
    input_sample_rate_hz: 8000
    provider_input_encoding: linear16
    provider_input_sample_rate_hz: 16000
    output_encoding: linear16
    output_sample_rate_hz: 24000
    target_encoding: ulaw
    target_sample_rate_hz: 8000

    # Model/voice — see "Choosing a model" below
    llm_model: gemini-2.5-flash-native-audio-latest
    tts_voice_name: Aoede

    # Session behavior
    greeting: "Hi! I'm powered by Google Gemini Live API."
    instructions: "You are a helpful voice assistant. Be concise."
    response_modalities: audio

    # Hangup fallback watchdog (recommended defaults)
    hangup_fallback_audio_idle_sec: 1.25
    hangup_fallback_min_armed_sec: 0.8
    hangup_fallback_no_audio_timeout_sec: 4.0
    hangup_fallback_turn_complete_timeout_sec: 2.5
```

#### Choosing a model

Google publishes Gemini Live models on two surfaces with different lifecycles. Pick based on which auth mode you're using (see [Provider-Vertex-Setup.md](Provider-Vertex-Setup.md) for Vertex AI auth):

| Model ID | Surface | Status (2026-04) | Notes |
|----------|---------|------------------|-------|
| `gemini-2.5-flash-native-audio-latest` | Developer API | Preview alias | **Shipped default.** Tracks Google's newest 2.5 native-audio snapshot automatically. |
| `gemini-2.5-flash-native-audio-preview-12-2025` | Developer API | Preview, dated | Pin this for reproducibility — guarantees a fixed snapshot. |
| `gemini-3.1-flash-live-preview` | Developer API | Preview, newest generation | Gemini 3.1 generation Live model. Evaluate before flipping the default; tool-calling parity not yet validated for AAVA. |
| `gemini-live-2.5-flash-native-audio` | **Vertex AI** | **GA** | Use via `use_vertex_ai: true`. SLA, VPC-SC, fewer function-calling bugs (see [Provider-Vertex-Setup.md](Provider-Vertex-Setup.md)). |

**Recommendation:**
- **Developer API users** → keep the shipped `gemini-2.5-flash-native-audio-latest` unless you need pinned reproducibility (then use the dated string).
- **Production / enterprise** → switch to Vertex AI mode and use `gemini-live-2.5-flash-native-audio` for GA-grade reliability and the function-calling fix.
- **Evaluating Gemini 3.1** → swap to `gemini-3.1-flash-live-preview` in a non-prod context first; report back via Discord/issues if function-calling and barge-in behave well end-to-end.

> Older Live models (`gemini-2.0-flash-live-001-preview-*`) are no longer listed on Google's models page and should not be used for new deployments.

### Hangup Fallback Tuning

Google Live may occasionally miss or delay `turnComplete` near the end of a farewell.  
The fallback watchdog protects against stuck calls:

- `hangup_fallback_audio_idle_sec`: hang up after this much trailing silence once audio has started.
- `hangup_fallback_min_armed_sec`: minimum time the fallback must stay armed before it can fire.
- `hangup_fallback_no_audio_timeout_sec`: fail-safe when no farewell audio arrives at all.
- `hangup_fallback_turn_complete_timeout_sec`: grace window to wait for `turnComplete` before fallback.

## Features

### 1. Barge-In (Interruption)

**Automatic** - No configuration needed:
- User speaks → Gemini detects via built-in VAD
- Agent stops talking immediately
- User's speech is processed

### 2. Function Calling

Tools work seamlessly in streaming mode:

```python
# Example: Transfer tool
"What's your name?" → User responds
Agent calls transfer_tool(extension="6000")
"Transferring you now..."
```

### 3. Session Management

Conversation context is maintained automatically:
- History tracked per WebSocket session
- Context carries across turns
- No need to re-introduce agent

## Limitations (AAVA-75 Findings)

### Google Pipeline Mode Limitations

| Issue | Impact | Workaround |
|-------|--------|-----------|
| **No barge-in** | Must wait for TTS to finish | Use `google_live` instead |
| **Sequential turns** | Higher latency (1.5-2.5s) | Use `google_live` for <1s |
| **System prompt repetition** | Fixed in v4.0 | Update to latest version |
| **Thinking tokens** | Need 256+ max_output_tokens | Config already updated |

### Google Live API Considerations

| Consideration | Details |
|---------------|---------|
| **Audio format** | Requires 16kHz PCM input (resampling needed for 8kHz) |
| **WebSocket** | Persistent connection (manage reconnection) |
| **API availability** | Live API is still preview-labeled. Google's model-discovery metadata can lag or omit Live-capable models for some keys, so setup warns rather than treating missing `bidiGenerateContent` listings as an invalid key. |
| **Latency** | Network-dependent (test in your environment) |

## Cost Comparison

| Mode | Cost per Minute | Components |
|------|----------------|------------|
| **Pipeline** | Varies | STT + LLM + TTS billed separately |
| **Live API** | Varies | All-in-one native audio session |

Pricing changes frequently; verify current rates and quotas in your Google Cloud console before production rollout.

## Troubleshooting

### Issue: WebSocket Connection Fails

**Symptoms**: `Failed to start Google Live session`

**Solutions**:
1. Verify API key: `echo $GOOGLE_API_KEY`
2. Check API enabled: https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com
3. Check quota limits in Cloud Console

### Issue: No Audio Received

**Symptoms**: Agent doesn't respond, silence

**Solutions**:
1. Check logs for `Google Live audio output` messages
2. Verify audio transcoding in logs: `docker logs ai_engine 2>&1 | grep -i resample`
3. Test with pipeline mode first to isolate issue

### Issue: Google Live mis-hears English as other languages (CRITICAL)

**Context**:

- Golden Baseline commit `d4affe8` used the default Gemini Live setup payload (no `realtimeInputConfig`).
- A later change added an explicit `realtimeInputConfig.automaticActivityDetection.disabled=false` block to the Google Live setup message.

**Symptoms**:

- Caller speaks clear English over telephony (8 kHz µ-law) but input transcriptions from Google Live show Arabic/Thai/Vietnamese tokens.
- RCA captures (`caller_to_provider.wav`) show clean English audio with high offline STT confidence.

**Root Cause & Fix**:

- For the ExternalMedia RTP telephony profile, explicitly sending `realtimeInputConfig` caused Gemini Live to mis-classify language on otherwise clean audio.
- Removing `realtimeInputConfig` and reverting to the Golden Baseline setup (commit `2597f63`) restores stable English recognition.

**Guidance**:

- **Do NOT set `realtimeInputConfig` for `google_live`** in this configuration.
- Rely on Gemini Live's default activity detection and constrain language via the system prompt in `demo_google_live`.
- If multilingual drift appears again, compare the setup payload against commit `d4affe8` and ensure `realtimeInputConfig` has not been reintroduced.

### Issue: Barge-In Not Working

**Note**: Barge-in is **automatic** with Google Live. If not working:
1. Confirm using `AI_PROVIDER=google_live` (not pipeline)
2. Check VAD is active: `docker logs ai_engine 2>&1 | grep -i vad`
3. Verify WebSocket messages: `docker logs ai_engine 2>&1 | grep -i inputtranscription`

## Migration from Pipeline to Live

### Before (Pipeline Mode)
```asterisk
Set(AI_CONTEXT=demo_google)
; AI_PROVIDER defaults to your configured `default_provider`
Stasis(asterisk-ai-voice-agent)
```

### After (Live API Mode)
```asterisk
Set(AI_CONTEXT=demo_google_live)
Set(AI_PROVIDER=google_live)  ; Enable real-time agent
Stasis(asterisk-ai-voice-agent)
```

## Support

- **Issues**: https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues
- **Docs**: https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/tree/main/docs
