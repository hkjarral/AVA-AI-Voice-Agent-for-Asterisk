# Google Provider Setup Guide

## Overview

Google AI integration provides two modes for the Asterisk AI Voice Agent:

1. **Google Live (Recommended)** - Real-time bidirectional streaming with Gemini 2.5 Flash, native audio processing, ultra-low latency (<1s), and true duplex communication
2. **Modular Pipelines (optional)** - Operator-defined pipelines that use Google adapters (`google_stt`, `google_llm`, `google_tts`) for STT/LLM/TTS

This guide covers setup for both modes.

If you used the Admin UI Setup Wizard, you may not need to follow this guide end-to-end. For first-call onboarding and transport selection, see:
- `INSTALLATION.md`
- `Transport-Mode-Compatibility.md`

For how provider/Agent selection works (including `AI_AGENT` / `AI_PROVIDER`), see:
- `Configuration-Reference.md` → "Call Selection & Precedence (Provider / Pipeline / Agent)"

## Long calls and session recovery

Google limits the lifetime of each Live WebSocket connection to approximately ten
minutes, on both Developer API and Vertex AI. Moving to Vertex does not remove
the need for connection turnover. Audio sessions also have a separate context
limit, approximately fifteen minutes without compression.

In **Providers → Google Live → API Mode → Long calls and connection recovery**:

- Enable **Keep long calls connected** to request resumable session checkpoints
  and replace the connection after `GoAway` or an eligible unexpected close.
- Enable **Compress long conversation history** independently to let Google
  discard older turns when approaching the context limit. Keep critical business
  state in AVA's tools/records rather than relying on model recall.
- Save, then restart the AI Engine. Both options default to **off**; existing
  configurations remain opted out. Leave resumption off when zero server-side
  data retention is required. Google caches conversation data for resumption.

Equivalent per-provider YAML (including named `type: google_live` instances):

```yaml
session_resumption_enabled: true
context_window_compression_enabled: true
session_recovery_timeout_sec: 10
session_recovery_max_attempts: 2
session_recovery_audio_buffer_ms: 1000
```

Recovery preserves the setup configuration, greeting state, tool permissions,
completed tool responses and deduplication state. It never starts a fresh
conversation or falls back between Vertex and Developer API during resumption.
Planned turnover waits for a resumable checkpoint and completed tools, bounded by
`goAway.timeLeft`. An unexpected disconnect without a currently safe checkpoint
(for example during generation or a tool call) follows terminal disconnect
handling; retaining an older handle does not prove it is safe to replay.
Non-resumable updates revoke the saved checkpoint. Recovery rechecks safety
after notifying the engine and before sending resumed setup; invalidation follows
terminal handling instead of resuming stale context or starting a fresh chat.
Authentication, schema and quota failures are not blindly retried. Caller
hangup, farewell and transfer ownership take precedence over recovery.
An attended-transfer decision retains ownership through bridge completion or
abort cleanup, including caller-facing prompts. Predial routing retains ownership
from destination setup, before deferred-transfer state is recorded. Disconnect handling also waits
for deferred routing to resolve within its bounded observation window; if the
transfer fails and returns ownership to the AI, the dead provider follows terminal
disconnect handling rather than leaving the call silent.

A short audio gap is possible. Only new caller audio arriving during replacement
is buffered, with a bounded most-recent window; previously sent audio and old
model output are never replayed. Failed recovery uses the engine's existing
audio-drain/fallback/termination handling. Unexpected clean closes on an active
call now also report a disconnect instead of leaving the provider silently dead.

INFO logs record `GoAway` time left, connection age and recovery outcomes without
resumption handles. Call diagnostics retain the call-time recovery settings.
Handles are memory-only and cleared on teardown; process restarts end calls and
do not resume past sessions. Disable the two switches and restart to roll back.

**Qualification before production:** on each intended API/model/transport, make a
30-minute call and confirm multiple `Google Live session resumed` events, one
greeting, conversation continuity, and clean teardown. Exercise interruption,
tool use, and hangup/transfer during turnover with both long-response playback
modes where applicable. Verify failed/rejected recovery produces one terminal
disconnect and no duplicate tool writes. Synthetic tests do not establish live
provider acceptance or an audio-gap guarantee.

Research checked 2026-10-20: [Developer API session management](https://ai.google.dev/gemini-api/docs/live-api/session-management),
[Live WebSocket schema](https://ai.google.dev/api/live), and
[Vertex session management](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api/start-manage-session).

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
exten => s,n,Set(AI_AGENT=demo_google_live)
exten => s,n,Set(AI_PROVIDER=google_live)
exten => s,n,Stasis(asterisk-ai-voice-agent)
exten => s,n,Hangup()
```

**For Google Cloud Pipeline:**

```ini
[from-ai-agent]
exten => s,1,NoOp(AI Voice Agent - Google Cloud Pipeline)
exten => s,n,Set(AI_AGENT=demo_google)
exten => s,n,Set(AI_PROVIDER=google_cloud_full)  ; example pipeline name (you define this in `pipelines:`)
exten => s,n,Stasis(asterisk-ai-voice-agent)
exten => s,n,Hangup()
```

**Recommended**: Set `AI_AGENT` and `AI_PROVIDER` when you want an explicit per-extension override:
- `AI_AGENT` selects the Agent (greeting, prompt, profile, tools)
- `AI_PROVIDER` selects the provider (e.g., `google_live`) or a pipeline name you defined under `pipelines:` (e.g., `google_cloud_full`)

If you omit these, the engine selects the default Agent/provider using the precedence rules in `docs/Configuration-Reference.md`.

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
exten => s,n,Set(AI_AGENT=demo_google)
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
same => n,Set(AI_AGENT=demo_google_live)
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

| Model ID | Surface | Status | Notes |
|----------|---------|------------------|-------|
| `gemini-2.5-flash-native-audio-latest` | Developer API | Preview alias | **Shipped default.** Tracks Google's newest 2.5 native-audio snapshot automatically. |
| `gemini-2.5-flash-native-audio-preview-12-2025` | Developer API | Preview, dated | Pin this for reproducibility — guarantees a fixed snapshot. |
| `gemini-3.1-flash-live-preview` | Developer API | Preview, newest generation | Gemini 3.1 generation Live model. Evaluate before flipping the default; tool-calling parity not yet validated for AAVA. |
| `gemini-3.8-live` | Developer API **or** Vertex AI | **GA** | Opt-in 3.8 Live. AAVA forces AUDIO output and uses ID-matched tool replies; read-only extension checks are non-blocking, while call-state actions remain blocking. Limited Vertex live-call validation is complete; broader qualification remains. |
| `gemini-live-2.5-flash-native-audio` | **Vertex AI** | **GA** | Use via `use_vertex_ai: true`. SLA, VPC-SC, fewer function-calling bugs (see [Provider-Vertex-Setup.md](Provider-Vertex-Setup.md)). |

**Recommendation:**
- **Production voice agents** → keep the established **Vertex AI mode** (`use_vertex_ai: true`) with `gemini-live-2.5-flash-native-audio` until 3.8 has broader production qualification. See the **Barge-In (Interruption)** section below and [Provider-Vertex-Setup.md](Provider-Vertex-Setup.md).
- **Evaluating Gemini 3.8 Live** → select `gemini-3.8-live` with either API mode. It is [GA on Vertex AI](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api) and [available on the Developer API](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live). The Admin UI locks Response Modalities to Audio Only for this model; enable output transcription separately if text is needed. Google defaults its function calls to non-blocking; AAVA keeps call-control tools blocking and allows only read-only extension status checks to run asynchronously. Test greeting, transcription, interruption, tools, transfer, and farewell before production use; the existing 2.5 default is unchanged.

- **Developer API evaluation / non-prod** → test the shipped `gemini-2.5-flash-native-audio-latest` with the local interruption path described below. Validate complete long responses, intentional interruptions and follow-up input on your transport before production rollout.
- **Pinned snapshot for reproducibility** → use `gemini-2.5-flash-native-audio-preview-12-2025` (or dated `-09-2025`) instead of the floating `-latest` alias.
- **Evaluating Gemini 3.1** → swap to `gemini-3.1-flash-live-preview` in a non-prod context first; tool-calling and barge-in parity not yet validated for AAVA — report back via Discord/issues.

On voiprnd, Vertex `us-central1` 3.8 call `1790560988.243` completed with interruption, extension and calendar tools, an unanswered attended transfer that returned to the agent, and a drained farewell/hangup. Vertex 2.5 call `1790561587.250` also completed after a model switch, including tools, unanswered-transfer recovery, and hangup. These are one environment's live validations, not a blanket guarantee for every region, Developer API mode, or production workload.

Gemini 3.8 Live tool declarations use an explicit execution policy. New tools default to `BLOCKING`; `check_extension_status` is `NON_BLOCKING` with `WHEN_IDLE` result scheduling. Calls are tracked by Google's function-call ID, duplicate IDs are ignored, and cancellations stop queued or read-only work. If a transfer or other state-changing action is already running, AAVA lets it finish rather than canceling halfway through; after a disconnect it records the outcome without sending a result into a closed session. Other Google Live models retain their existing tool protocol.

> Older Live models (`gemini-2.0-flash-live-001-preview-*`) are no longer listed on Google's models page and should not be used for new deployments.

#### Gemini 3.1 Flash Live — verified working on Developer API

End-to-end testing on 2026-05-09 with `llm_model: gemini-3.1-flash-live-preview` against the Developer API endpoint confirmed full conversational compatibility with AAVA's existing code path:

- WebSocket connect, `setupComplete` ACK, greeting playback, and **basic `hangup_call` tool execution** (with farewell `clientContent` round-trip) worked unchanged. **Broader tool-calling parity (Microsoft Calendar, transfer tools, multi-tool turns) was not exercised** in Phase 0 testing and remains the "not yet validated for AAVA" caveat noted in the model table above.
- Multi-part `serverContent` envelopes (a 3.1 behavioral change where a single event can carry both `modelTurn` and `outputTranscription` simultaneously) are handled correctly by the current parser — observed 94 multi-part envelopes in a 59-second test call, zero parse errors, zero unhandled message types.
- Tool-call → farewell `clientContent` round-trip succeeds mid-conversation despite Google's docs implying `sendClientContent` is initial-history-only on 3.1; Google appears to accept it in practice without `initial_history_in_client_content: true` in the session config.

**Caveats** (same as the 2.5 native-audio family on Developer API):

- AAVA uses the gated Google input path for 3.1 as well. Check local fallback detection during playback; an absent server-side `interrupted` event while the server receives silence does not identify an API/model failure.
- AAVA's `clientContent` callsites (the `_send_greeting` helper and the post-tool farewell handler in `src/providers/google_live.py`) work today but should be migrated to `realtimeInput` with a text field if Google tightens enforcement of the 3.1 history-only restriction in a future release.

Investigation logs and timeline are captured in the [#350 / #356 verification discussion on PR #384](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/pull/384).

### Hangup Fallback Tuning

Google Live may occasionally miss or delay `turnComplete` near the end of a farewell.  
The fallback watchdog protects against stuck calls:

- `hangup_fallback_audio_idle_sec`: hang up after this much trailing silence once audio has started.
- `hangup_fallback_min_armed_sec`: minimum time the fallback must stay armed before it can fire.
- `hangup_fallback_no_audio_timeout_sec`: fail-safe when no farewell audio arrives at all.
- `hangup_fallback_turn_complete_timeout_sec`: grace window to wait for `turnComplete` before fallback.

### Marker-based hangup heuristics

`hangup_markers_enabled` defaults to `false` and should remain off in production;
prefer the explicit `hangup_call` tool. When enabled, Google evaluates the completed
turn's caller and assistant text, using whole-word/phrase boundaries. A greeting
such as "thank you for calling" does not remain a farewell for later turns.
Weak caller markers require an assistant farewell; configured explicit end-call
markers can still end a call without one. Phrases such as "all set", "that's it",
or "bye" are ambiguous even with word boundaries, so false positives remain possible.

Google and Grok do not arm marker fallback while a deferred transfer is pending.
A `HangupReady` event gives a pending transfer priority. Before committing, the
engine clears superseded hangup timers/state so a transfer drain timeout can
return the caller to the AI, play its apology, and accept a later legitimate
hangup. Failed commits cancel their exact pending action and recover without
automatic replay on later completion events; a replacement action survives an
older action's failure. Grok also uses whole-word/phrase boundaries for its
farewell fallback.

Before enabling heuristics, validate a greeting followed by continued conversation,
a genuine farewell, a successful transfer, and a transfer drain timeout followed
by more conversation and a later hangup. Keep call IDs and the tested revision
with the validation evidence.

## Features

### 1. Barge-In (Interruption)

Google Live has two interruption paths in AAVA:

- **Gemini 3.8 with `full_duplex_barge_in_3_8: true`** receives real caller audio during playback and uses Google's native `serverContent.interrupted` signal. The engine flushes pending playback when that signal arrives.
- **Other Google Live models, or 3.8 with full duplex disabled**, retain silence substitution during TTS to limit self-echo. Local fallback detection inspects the original normalized caller audio before silence substitution, matching the ExternalMedia/RTP path. This applies to Developer API and Vertex instances, including named provider aliases.

The local fallback honors `barge_in.enabled`, `provider_fallback_enabled`, the provider allowlist, greeting/start protection, cooldown, and media isolation. With enhanced local VAD available, it uses the existing speech criteria. With local VAD disabled, it uses the existing energy threshold and sustained-frame requirement. `vad_mode: auto` combined with legacy `use_provider_vad: true` still selects provider mode; the fallback can inspect caller energy without enabling local VAD globally.

An absent Google interruption event during silence-gated playback does not establish a model or API limitation: the server is receiving silence. Check the local fallback events as well. Test sustained noise and phone/microphone echo alongside intentional interruptions before rollout.

#### Long-response playback (Developer API opt-in)

The Google Developer API can deliver many small audio chunks faster than telephony plays them. The original source queue can fill and discard chunks, causing a long answer to skip speech or end early. **Enable long-response playback** retains queued audio within a bounded budget and waits for it to reach the caller before releasing playback gating or completing terminal actions.

**Enable or disable in the Admin UI:**

1. Open **Providers**, edit the Google Live provider instance used by your Agent, and select Developer API mode under **API Mode**.
2. Select **Enable long-response playback**, or clear it to restore the original queue/completion path.
3. Click **Save Changes**, then **Restart AI Engine** once active calls have finished. Saving alone does not activate provider changes.
4. Reopen the provider to confirm the saved setting, then test a new call.

The checkbox defaults **off**, including after upgrading an existing installation. There is no automatic opt-in or data migration, and shipped model defaults stay the same. Existing Developer API users who need long responses must enable it explicitly; leaving it off retains the original queue limit. Named Google provider instances have independent settings.

The checkbox is hidden when Vertex is selected. Switching API mode preserves the saved preference; the feature runs only when the actual connected backend is the Developer API. If Vertex authentication falls back to Developer API, that saved preference governs the fallback connection. The AudioSocket microphone detection correction above applies to both Google backends independently of this option. Other providers and modular pipelines do not use the long-response queue.

For headless configuration, merge these fields into the existing provider entry and restart `ai_engine`:

```yaml
providers:
  google_live:
    long_audio_playback_enabled: true  # Default false; enable on a test instance first.
    long_audio_backlog_sec: 120       # Allowed range: 10–120 seconds.
```

`long_audio_backlog_sec` defaults to **120 seconds** and accepts **10–120**. It controls queued audio capacity, not total response duration or how long Google is asked to speak. The checkbox is the UI control; the advanced budget is configured in YAML. See the [configuration reference](Configuration-Reference.md#google-live-monolithic-agent).

The backlog is bounded by audio bytes and item count. Overflow or failed drain ends the call with an explicit error instead of silently dropping speech chunks. Playback drain, rather than generation completion, releases input gating. Barge-in discards the queued response and clears playback-owned gating so subsequent caller input can resume. Terminal tool actions retain their protocol completion boundary. On opted-in, silence-gated Developer calls, local fallback caps the required qualifying speech at 120 ms (or a shorter configured duration) within a 200-ms window, tolerating quiet gaps up to 40 ms; the existing energy threshold, enhanced-VAD votes, greeting protection, cooldown and media isolation still apply. Vertex and opt-out timing are unchanged; Gemini 3.8 native full-duplex interruption continues to use the provider signal.

An interrupted terminal response cannot reuse its old audio or completion boundary to hang up. If the Developer connection closes abnormally, already-accepted audio receives at most eight seconds to drain before cleanup, with transfer/caller teardown taking priority. This may preserve a short buffered farewell; it does not fix an upstream API error or recover audio that Google never generated. Overflows and stalled playback remain explicit failures. Disabling the checkbox restores the original queue/completion path after saving and restarting. Stopping old playback locally and receiving Google's next response are separate events; the option cannot guarantee the model's response latency.

**Live validation on voiprnd (2026-10-03, Google 2.5 / AudioSocket):**

| Call | Setting | Evidence |
|---|---|---|
| `1791083954.400` | Developer, off | Received 92.08 seconds of long-answer audio; the original queue dropped 1,173 chunks and emitted 45.18 seconds. |
| `1791084141.404` | Developer, on | Received 91.48 seconds and emitted 91.50 seconds including frame padding over 93.605 seconds, with zero drops. The long answer drained fully before the next caller turn; farewell/hangup also drained. |
| `1791083429.375` | Vertex | Connected to the Vertex endpoint, exercised interruptions and farewell/hangup, and never activated the opt-in queue. |

These calls establish the tested backend/transport behavior, not every model or deployment. Live successful/failed transfer and interrupted-farewell replacement remain qualification checks; automated tests cover their ownership and completion boundaries. Before enabling broadly, test an uninterrupted long answer, follow-up input, early/late and repeated interruptions, background noise/echo, normal and interrupted farewell, and transfer success/failure. Confirm discarded speech never resumes. For a failed call, use **Call History → Troubleshoot → Download Support Package** as described in the [troubleshooting guide](TROUBLESHOOTING_GUIDE.md#export-a-support-package-for-one-call).

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

1. Confirm which path the selected model uses in the **Barge-In (Interruption)** section. Verify that `barge_in.enabled`, `provider_fallback_enabled`, and the provider allowlist permit local detection for a gated Google instance.
2. Check engine startup for `Using provider-managed VAD; local VAD disabled`. The legacy `use_provider_vad: true` override selects that mode when `vad_mode` is `auto`; changing the global VAD mode affects other call paths and is not required for the normalized-PCM fallback.
3. Look for `Google Live server-side interruption detected` on a native full-duplex call, or `BARGE-IN (provider fallback) triggered` and `BARGE-IN action applied` on a locally detected interruption. Silence sent upstream during gated playback prevents server-side speech detection.
4. Compare original caller input energy with `barge_in.energy_threshold` (default 1,000) and the configured sustained-speech duration. Preserve greeting protection and cooldown; test speech, silence, background noise and microphone echo before adjusting thresholds.
5. Confirm queued audio stops, capture re-enables, the next question is heard, and an old response/completion event cannot resume discarded speech or release a newer response's gate.

## Migration from Pipeline to Live

### Before (Pipeline Mode)
```asterisk
Set(AI_AGENT=demo_google)
; AI_PROVIDER defaults to your configured `default_provider`
Stasis(asterisk-ai-voice-agent)
```

### After (Live API Mode)
```asterisk
Set(AI_AGENT=demo_google_live)
Set(AI_PROVIDER=google_live)  ; Enable real-time agent
Stasis(asterisk-ai-voice-agent)
```

## Support

- **Issues**: https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues
- **Docs**: https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/tree/main/docs


## Optional startup connection recovery

This full-agent provider supports bounded retries before session setup. Upgrades preserve a 10-second opening timeout and zero retries. Enable recovery per provider under **Startup connection recovery (Expert)** in the Admin UI, then restart AI Engine. Setup, keepalive, and mid-call behavior remain separate. See [connection recovery configuration and real-call tests](Provider-Connection-Recovery.md) for deadlines, rollback, and qualification steps.
