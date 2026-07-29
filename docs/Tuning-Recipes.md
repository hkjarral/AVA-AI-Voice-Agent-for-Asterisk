# Tuning Recipes

Quick, copy-pasteable presets for common deployment scenarios. Adjust to suit your trunks and network.

See also: `docs/Configuration-Reference.md` for detailed option effects and ranges.

## Quiet office lines (sensitive, responsive)

```yaml
barge_in:
  enabled: true
  initial_protection_ms: 300
  min_ms: 280
  energy_threshold: 1200
  cooldown_ms: 800
  post_tts_end_protection_ms: 250

streaming:
  jitter_buffer_ms: 90
  min_start_ms: 280
  low_watermark_ms: 180
```

Why: Lower energy threshold and min_ms make barge-in more responsive. Keep jitter and warm-up modest.

## Noisy call center (robust, less false triggers)

```yaml
barge_in:
  enabled: true
  initial_protection_ms: 400
  min_ms: 450
  energy_threshold: 2200
  cooldown_ms: 1200
  post_tts_end_protection_ms: 300

vad:
  webrtc_aggressiveness: 1
  webrtc_end_silence_frames: 35
  min_utterance_duration_ms: 2600

streaming:
  jitter_buffer_ms: 140
  min_start_ms: 350
  low_watermark_ms: 240
```

Why: Higher thresholds reduce false barge-ins; larger buffers handle jitter; slightly longer utterances improve STT.

## Lowest latency (more sensitive to jitter/echo)

```yaml
barge_in:
  enabled: true
  initial_protection_ms: 250
  min_ms: 280
  energy_threshold: 1400
  cooldown_ms: 600
  post_tts_end_protection_ms: 250

streaming:
  jitter_buffer_ms: 80
  min_start_ms: 250
  low_watermark_ms: 160
```

Why: Minimal buffering and faster barge-in. Expect higher risk of underruns and occasional self-echo on some trunks.

## Stability-first (tolerant, slightly higher latency)

```yaml
barge_in:
  enabled: true
  initial_protection_ms: 450
  min_ms: 500
  energy_threshold: 2000
  cooldown_ms: 1200
  post_tts_end_protection_ms: 300

streaming:
  jitter_buffer_ms: 150
  min_start_ms: 380
  low_watermark_ms: 260
  provider_grace_ms: 600
```

Why: Larger buffers and conservative barge-in minimize glitches at the cost of slightly slower starts.

## OpenAI Realtime server-side turn detection

Enable on the provider to improve turn-taking (optional):

```yaml
providers:
  openai_realtime:
    # ...
    turn_detection:
      type: "server_vad"
      silence_duration_ms: 200
      threshold: 0.5
      prefix_padding_ms: 200
```

Use with or without local VAD. If both run, prefer conservative local VAD (e.g., longer end-silence) to avoid clashes.

## Streaming vs file playback

- `downstream_mode: stream`: best UX, requires stable network; tune `streaming.*` buffers.
- `downstream_mode: file`: more tolerant to jitter and provider hiccups, at the cost of response latency.

## Audio transport alignment (μ-law ↔ PCM16)

```ini
; Dialplan handshake (optional but recommended)
exten => s,n,Set(AI_TRANSPORT_FORMAT=slin16)   ; or ulaw
exten => s,n,Set(AI_TRANSPORT_RATE=8000)       ; 8k/16k/24k
```

- If the dialplan omits those variables, the engine falls back to `config.audiosocket.format` and logs the default it is using.
- The engine auto-aligns downstream streaming targets; when a provider’s config disagrees, it will log an actionable warning (with the exact YAML keys to fix) and expose `ai_agent_codec_alignment{call_id,provider}` as `0` in `/metrics`.
- To keep providers in PCM16 internally while the AudioSocket leg remains μ-law, enable:

```yaml
streaming:
  egress_force_mulaw: true
```

  This converts outbound streaming audio back to μ-law/8 kHz right before it is written to Asterisk, regardless of provider output.

- RMS/DC offset diagnostics for each stage are published as `ai_agent_audio_rms{stage=...}` and `ai_agent_audio_dc_offset{stage=...}` so you can alert on silent or biased audio before customers notice.

---

## Pipeline Tuning (v4.0)

v4.0 introduces modular pipelines where STT, LLM, and TTS are separate. Tuning considerations differ from monolithic providers.

### Local Hybrid Pipeline (Vosk + OpenAI + Piper)

**Optimal Settings** for 3-7 second response time:

```yaml
pipelines:
  local_hybrid:
    stt: local_stt
    llm: openai_llm
    tts: local_tts
    options:
      stt:
        chunk_ms: 320              # Balanced latency vs context
        streaming: true
        stream_format: "pcm16_16k" # Match internal format (no transcoding)
      llm:
        model: "gpt-4o-mini"       # Fast, cost-effective
        temperature: 0.7
        max_tokens: 150            # Keep responses concise
      tts:
        format:
          encoding: ulaw
          sample_rate: 8000

vad:
  enabled: true
  mode: "webrtc"
  webrtc_aggressiveness: 2         # Normal (good for Vosk)
  min_utterance_duration_ms: 250
  webrtc_end_silence_frames: 35    # ~700ms at 20ms frames — balance responsiveness vs cutting off
  utterance_padding_ms: 300

barge_in:
  enabled: true
  min_ms: 400                      # Prevent false interrupts during silence
  energy_threshold: 1800
```

**Why**: Vosk performs better with slightly longer audio chunks. `gpt-4o-mini` is faster than `gpt-4o` (500ms vs 1.5s). Pipeline mode requires `downstream_mode: file`.

**Hardware Note**: Modern CPU (2020+) required for acceptable latency. See [HARDWARE_REQUIREMENTS.md](HARDWARE_REQUIREMENTS.md).

---

### OpenAI Realtime (Monolithic)

**Optimal Settings** for <1.5 second response time:

```yaml
default_provider: "openai_realtime"

providers:
  openai_realtime:
    enabled: true
    turn_detection:
      type: "server_vad"           # Use OpenAI's server-side VAD
      silence_duration_ms: 200     # Quick turn detection
      threshold: 0.5
      prefix_padding_ms: 200

vad:
  enabled: true
  webrtc_aggressiveness: 1         # CRITICAL: Low for OpenAI (prevents conflicts)
  webrtc_end_silence_frames: 50    # ~1000ms at 20ms frames — let server VAD handle turn detection

barge_in:
  enabled: false                   # Server-side turn handling sufficient

streaming:
  jitter_buffer_ms: 90             # Low latency streaming
  min_start_ms: 250
  low_watermark_ms: 180

downstream_mode: "stream"          # Real-time streaming for best UX
```

**Why**: OpenAI Realtime has server-side VAD that handles turn detection. Keep local VAD passive (`aggressiveness: 1`) to avoid conflicts. Streaming mode provides lowest latency.

**Critical**: `webrtc_aggressiveness: 1` is essential - higher values cause audio clipping with OpenAI's VAD.

---

### Deepgram Voice Agent (Monolithic)

**Optimal Settings** for 1-2 second response time:

```yaml
default_provider: "deepgram"

providers:
  deepgram:
    enabled: true
    model: "nova-2-general"
    tts_model: "aura-asteria-en"
    continuous_input: true         # Stream audio continuously
    input_encoding: "linear16"     # Best quality (PCM16)
    input_sample_rate_hz: 8000
    output_encoding: "mulaw"
    output_sample_rate_hz: 8000

barge_in:
  enabled: false                   # Deepgram handles turn-taking

vad:
  enabled: true
  webrtc_aggressiveness: 1         # Low (Deepgram has server-side VAD)
  webrtc_end_silence_frames: 40    # ~800ms at 20ms frames

streaming:
  jitter_buffer_ms: 100
  min_start_ms: 300
  low_watermark_ms: 200

downstream_mode: "file"            # More robust for Deepgram
```

**Why**: Deepgram Voice Agent includes "Think" stage for reasoning. `file` mode is more robust than `stream` with Deepgram's multi-stage processing. `continuous_input: true` required for real-time conversation.

**Cost**: ~$0.03/min (cheaper than OpenAI Realtime)

---

## Provider Comparison

| Configuration | Response Time | Cost/min | Tuning Complexity | Best For |
|---------------|---------------|----------|-------------------|----------|
| **OpenAI Realtime** | 0.5-1.5s | ~$0.06 | Low (server VAD) | Speed, simplicity |
| **Deepgram** | 1-2s | ~$0.03 | Low (server VAD) | Cost, reasoning |
| **Local Hybrid** | 3-7s | ~$0.002 | Medium (local VAD) | Privacy, cost |

---

## Monitoring Integration

Use Prometheus metrics to validate tuning:

```promql
# Turn response latency (target: <2s p95)
histogram_quantile(0.95, rate(ai_agent_turn_response_seconds_bucket[5m]))

# Underflow rate (target: 0 per call)
rate(ai_agent_stream_underflow_events_total[5m]) / rate(ai_agent_streaming_sessions_total[5m])

# Barge-in effectiveness (if enabled)
rate(ai_agent_barge_in_triggered_total[5m])
```

See [MONITORING_GUIDE.md](MONITORING_GUIDE.md) for dashboard setup and alert thresholds.
