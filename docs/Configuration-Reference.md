# Configuration Reference

This document explains every major option in `config/ai-agent.yaml`, the precedence model for greeting/persona, and the impact of fine‑tuning parameters across AudioSocket/ExternalMedia, VAD, Barge‑In, Streaming, and Providers.

## Local Override File (`ai-agent.local.yaml`)

Operator customizations are stored in `config/ai-agent.local.yaml` (git-ignored). At startup the engine deep-merges this file on top of the base `config/ai-agent.yaml`:

- **Base file** (`config/ai-agent.yaml`) — shipped golden defaults, git-tracked. Updated by upstream releases.
- **Local override** (`config/ai-agent.local.yaml`) — operator changes only. All Admin UI saves, CLI wizard writes, and `agent setup` output go here.

Keys in the local file win over the base file (deep merge — nested dicts are merged recursively, scalars are replaced). If the local file does not exist, the base file is used as-is.

This separation means `git pull` during updates will never conflict with operator config, eliminating the merge-conflict problem on `ai-agent.yaml`.

## Configuration Architecture (v5.0)

Starting in v4.0, the project added a **modular pipeline architecture** alongside monolithic provider support:

### Monolithic Providers
- **Single provider** handles STT, LLM, and TTS internally
- Examples: `openai_realtime`, `deepgram` Voice Agent
- Configuration: Set `default_provider: "openai_realtime"` or `default_provider: "deepgram"`
- **Best for**: Simplicity, fastest response times

### Pipeline Configurations
- **Separate providers** for STT, LLM, and TTS
- Examples: Local Hybrid (Vosk STT + OpenAI LLM + Piper TTS)
- Configuration: Define under `pipelines:` block and set `active_pipeline: "pipeline_name"`
- **Best for**: Flexibility, privacy (local audio processing), cost control

### Pipeline LLM Hangup Guardrail (hangup_call)

Some pipeline LLMs can be overly eager to emit `hangup_call`. A per-pipeline guardrail can require explicit end-of-call intent in the user's transcript before honoring `hangup_call`.

- `pipelines.<name>.options.llm.hangup_call_guardrail`: `true`/`false` (unset = auto; enabled by default for specific adapters)
- `pipelines.<name>.options.llm.hangup_call_guardrail_mode`: `relaxed`/`normal`/`strict` (unset = use global hangup policy mode)
- `pipelines.<name>.options.llm.hangup_call_guardrail_markers.end_call`: list of caller phrases that count as end-of-call intent (unset/empty = use global hangup policy defaults)

### Golden Baselines
See the 5 validated configurations in `config/`:
- `ai-agent.golden-openai.yaml` - OpenAI Realtime (monolithic, fastest)
- `ai-agent.golden-deepgram.yaml` - Deepgram Voice Agent (monolithic, enterprise)
- `ai-agent.golden-google-live.yaml` - Google Live (monolithic, lowest latency)
- `ai-agent.golden-elevenlabs.yaml` - ElevenLabs Agent (monolithic, premium voice)
- `ai-agent.golden-local-hybrid.yaml` - Local Hybrid (pipeline, privacy-focused)

For comprehensive inline documentation, refer to the golden baseline YAML files directly.

### Local AI Server Backends (v4.4.2+)

Environment variables for selecting local STT/TTS backends:

| Variable | Options | Default | Description |
|----------|---------|---------|-------------|
| `LOCAL_STT_BACKEND` | `vosk`, `sherpa`, `kroko`, `faster_whisper` | `vosk` | Speech-to-text engine |
| `LOCAL_TTS_BACKEND` | `piper`, `kokoro`, `melotts` | `piper` | Text-to-speech engine |

**STT Backends**:
- **Vosk**: Offline ASR with good accuracy, multiple language models
- **Sherpa-ONNX**: Low-latency streaming ASR using ONNX runtime
- **Kroko**: High-quality streaming ASR with 12+ languages (requires API key for hosted mode)
- **Faster-Whisper**: Whisper inference via `faster-whisper` (model IDs like `base`, `small`, etc., or a local model directory depending on your install)

**TTS Backends**:
- **Piper**: Fast local TTS with multiple voices
- **Kokoro**: High-quality neural TTS with natural prosody (voices: af_heart, af_bella, am_michael)
- **MeloTTS**: High-quality multilingual TTS with voice IDs (depends on installed voices/models)

**Model/voice identifiers**:
- `providers.local.stt_model` and `providers.local.tts_voice` are treated as **backend-specific identifiers** (paths for some backends, IDs for others) and are used by the Admin UI “switch/rebuild” flows.

See [LOCAL_ONLY_SETUP.md](LOCAL_ONLY_SETUP.md) for detailed configuration.

---

## Call Selection & Precedence (Provider / Pipeline / Context)

On each call, the engine selects:
- a **context** (greeting/prompt/tools/profile)
- a **provider mode** (full agent provider vs pipeline)

This selection is intentionally flexible so you can keep safe defaults while still overriding behavior per extension.

### Context selection

- If your dialplan sets `AI_CONTEXT`, that context name is used.
- Otherwise, the engine uses the `default` context.

### Audio profile selection

Audio profiles control the call’s negotiated sample rates/encodings (telephony wire format, provider input/output format, and internal pacing). They are defined under `profiles:` in `config/ai-agent.yaml`.

Highest priority first:

1. **Dialplan override**: `AI_AUDIO_PROFILE` (if set)
2. **Context mapping**: `contexts.<name>.profile` (if set for the selected context)
3. **Global default**: `profiles.default` (fallback is `telephony_ulaw_8k` if unset)

### Provider selection

Highest priority first:

1. **Dialplan override**: `AI_PROVIDER` (if set)
2. **Context override**: `contexts.<name>.provider` (if set for the selected context)
3. **Global default**: `default_provider`

### Pipeline selection

If the selected provider path is a pipeline-based configuration, the engine uses:

- `active_pipeline` to determine which pipeline to run.
- If `active_pipeline` is unset/null, the engine falls back to the first available pipeline in `pipelines:`.

### Recommended approach

- Keep `default_provider` + `active_pipeline` set to a known-good baseline.
- Use `AI_CONTEXT` for persona/tool scoping.
- Use `AI_PROVIDER` only when you want an explicit per-extension override.

See also:
- [Installation Guide](INSTALLATION.md)
- [Transport Compatibility](Transport-Mode-Compatibility.md)

---

## Outbound Campaign Dialer (Milestone 22)

Outbound calling is implemented as an **engine-driven scheduler + SQLite + ARI originate**, with **dialplan-assisted AMD** for voicemail detection.

### Assumptions (MVP)

- Your outbound **trunk(s) and outbound routes** are already configured in Asterisk/FreePBX.
- Outbound calls originate as **extension identity `6789`** (default), and routing happens via your existing FreePBX dialplan patterns.
- Persistence reuses the existing Call History SQLite DB path (`CALL_HISTORY_DB_PATH`) by adding outbound tables.

### Key env vars

| Variable | Default | Description |
|----------|---------|-------------|
| `AAVA_OUTBOUND_EXTENSION_IDENTITY` | `6789` | Extension identity for FreePBX routing (sets `AMPUSER` + `CALLERID(num)` on originate) |
| `AAVA_OUTBOUND_AMD_CONTEXT` | `aava-outbound-amd` | Dialplan context name used for AMD hop (`continueInDialplan`) |
| `AAVA_MEDIA_DIR` | `/mnt/asterisk_media/ai-generated` | Where the Admin UI uploads voicemail drop `.ulaw` files |

### Dialplan requirements

- Create a custom dialplan context (default: `[aava-outbound-amd]`) that runs `AMD(${AAVA_AMD_OPTS})` and returns to Stasis with:
  - `outbound_amd` (action)
  - `${AAVA_ATTEMPT_ID}` (attempt correlation)
  - `${AMDSTATUS}` and `${AMDCAUSE}`

See `docs/contributing/milestones/milestone-22-outbound-campaign-dialer.md` for the full snippet and smoke test checklist.

## Canonical persona and greeting

- llm.initial_greeting: Text the agent speaks first (if provider supports explicit greeting or the engine plays via TTS).
- llm.prompt: The agent persona/instructions used by LLMs.
- Precedence at runtime:
  1) Provider/pipeline overrides (if explicitly set, e.g., `providers.openai_realtime.instructions`, `providers.deepgram.greeting`)
  2) `llm.prompt` and `llm.initial_greeting` in YAML
  3) Env defaults `AI_ROLE`, `GREETING`

## Transports

- audio_transport: `audiosocket` | `externalmedia`
  - **audiosocket**: TCP-based audio transport.
  - **externalmedia**: RTP/UDP-based audio transport.
  - **Selection**: Use the validated combinations in **[Transport & Playback Mode Compatibility Guide](Transport-Mode-Compatibility.md)**. Transport selection depends on provider mode and playback method (not a single strict rule).
- downstream_mode: `stream` | `file`
  - **stream**: Real-time streaming (20ms frames). Best UX. Works with full agents.
  - **file**: File-based playback via bridge. Most robust for pipelines; streaming-first is supported with automatic fallback to file when enabled.

## AudioSocket

- audiosocket.host: Bind address for AudioSocket listener.
- audiosocket.advertise_host: Address Asterisk connects to (optional; defaults to `audiosocket.host`). Use for NAT/VPN.
- audiosocket.port: TCP port.
- audiosocket.format: `slin` (**validated**, 16-bit signed linear @ 8 kHz). Other values may exist in code/config, but only `slin` is currently tested and documented as stable.

## ExternalMedia

- external_media.rtp_host: Bind address for RTP server.
- external_media.advertise_host: Address Asterisk sends RTP to (optional; defaults to `external_media.rtp_host`). Use for NAT/VPN.
- external_media.rtp_port: Port for inbound RTP.
- external_media.port_range: Optional range (`start:end`) for dynamic per-call RTP allocation; defaults to `rtp_port`.
- external_media.codec: `ulaw` | `slin16` (8 kHz).
- external_media.direction: `both` | `sendonly` | `recvonly`.
- external_media.lock_remote_endpoint: When true (default), **do not** accept mid-call changes to the inbound RTP source `(ip,port)` for that call.
- external_media.allowed_remote_hosts: Optional list of **IP addresses** allowed as inbound RTP sources. When set, packets from other sources are dropped (recommended when the RTP source IP is stable).
  - Note: if `asterisk.host` is an IP literal, the engine may default `allowed_remote_hosts` to `[asterisk.host]` unless explicitly configured.
  - If `asterisk.host` is a **hostname**, set `external_media.allowed_remote_hosts` explicitly (the platform does not auto-allowlist hostnames).
- Note: `external_media.jitter_buffer_ms` is no longer used (RTP buffering is not configurable here). Use `streaming.jitter_buffer_ms` for downstream playback pacing.

## Barge‑In

Controls interruption of TTS playback when the caller speaks.

- barge_in.enabled: true/false
- barge_in.initial_protection_ms: 200–600 ms. Drop inbound immediately after TTS starts to avoid self‑echo.
- barge_in.min_ms: 250–600 ms. Minimum sustained speech before a barge‑in is acknowledged (de‑bounce).
- barge_in.energy_threshold: 1000–3000. RMS energy threshold; raise on noisy lines.
- barge_in.cooldown_ms: 500–1500 ms. Ignore new barge‑ins after one triggers.
- barge_in.post_tts_end_protection_ms: 250–500 ms. Short guard to avoid clipping the start of the next caller utterance.
- barge_in.pipeline_min_ms: 80–250 ms. Pipeline-only (local file playback) minimum talk duration before triggering barge-in.
- barge_in.pipeline_energy_threshold: 200–1200. Pipeline-only RMS threshold (more sensitive than full-agent mode).
- barge_in.pipeline_talk_detect_enabled: true/false. Pipeline-only; uses Asterisk `TALK_DETECT` (ARI `ChannelTalkingStarted`) to trigger barge-in during channel playback.
- barge_in.pipeline_talk_detect_silence_ms: 800–2000. Pipeline-only; `TALK_DETECT(set)` silence window.
- barge_in.pipeline_talk_detect_talking_threshold: 64–256. Pipeline-only; `TALK_DETECT(set)` talking threshold.

Notes (pipelines / `local_hybrid`):

- Pipelines play TTS locally (file playback), so the platform can flush playback on barge-in without colliding with provider-owned VAD/cancellation.
- With ExternalMedia, Asterisk channel playback may pause/alter the inbound RTP stream; `TALK_DETECT` is the preferred trigger source for pipeline barge-in.
- Prereqs: Asterisk must have talk detection available (`app_talkdetect.so` / `func_talkdetect.so`). Verify with `asterisk -rx 'module show like talkdetect'` and `asterisk -rx 'core show function TALK_DETECT'`.

Notes (OpenAI Realtime / AudioSocket):

- OpenAI can emit `input_audio_buffer.speech_started` when the provider has no cancellable response but the platform is still draining buffered audio; the platform treats this as a barge-in trigger to flush local output immediately.

Tuning guidance:

- Noisy lines: raise `energy_threshold` and `min_ms`.
- Fast, chatty interactions: lower `min_ms` and `post_tts_end_protection_ms` cautiously.

## Streaming (downstream_mode=stream)

Controls the pacing and robustness of streamed agent audio.

- streaming.sample_rate: Output sample rate (typically 8000 for telephony).
- streaming.jitter_buffer_ms: 80–150 ms. Higher = more robust to jitter, slightly higher latency.
- streaming.keepalive_interval_ms: TCP keepalive interval for streaming connections.
- streaming.connection_timeout_ms: Time to consider a streaming connection dead.
- streaming.fallback_timeout_ms: No audio for this long triggers fallback to file playback.
- streaming.chunk_size_ms: 20 ms recommended for telephony cadence.
- streaming.min_start_ms: 250–400 ms. Warm‑up buffer before first frame; too low risks underruns.
- streaming.low_watermark_ms: Brief pause/guard band; increase if underruns occur.
- streaming.provider_grace_ms: Absorb late provider chunks to avoid tail-chop artifacts.
- streaming.logging_level: Verbosity for the streaming manager.
- streaming.egress_force_mulaw: When true, converts outbound streaming audio to μ-law 8 kHz regardless of provider encoding.
- streaming.greeting_rtp_wait_ms: ExternalMedia-only. How long to wait (ms) for the remote RTP endpoint to be discovered during the initial greeting before falling back to file playback (prevents “dead air until caller speaks” in some Asterisk setups).

## VAD (Voice Activity Detection)

Defines how inbound speech is segmented into utterances for STT.

- vad.webrtc_aggressiveness: 0–3. 0=least aggressive (best for 8 kHz telephony), 3=most aggressive (may clip speech).
- vad.webrtc_start_frames: Consecutive frames above threshold to start recording.
- vad.webrtc_end_silence_frames: Silence frames to finalize an utterance (e.g., 50 → ~1000 ms at 20 ms frames).
- vad.min_utterance_duration_ms: Lower bound on utterance length. Raise if STT returns empty.
- vad.max_utterance_duration_ms: Hard cap to prevent runaway capture.
- vad.utterance_padding_ms: Padding around detected speech.
- vad.fallback_enabled: When true, sends audio at a fixed interval if VAD fails to detect speech.
- vad.fallback_interval_ms: Interval between fallback sends.
- vad.fallback_buffer_size: Bytes to accumulate at fallback thresholds.
- vad.upstream_squelch_enabled: When true, replaces low-energy/noise frames with silence for continuous-audio providers that have native VAD (improves end-of-turn detection in noisy environments; may suppress quiet callers if too aggressive).
- vad.upstream_squelch_base_rms: Minimum RMS threshold (PCM16 space) before audio is treated as “speech”.
- vad.upstream_squelch_noise_factor: Dynamic threshold multiplier relative to estimated noise floor.
- vad.upstream_squelch_noise_ema_alpha: EMA smoothing factor (0–1) for noise floor estimation.
- vad.upstream_squelch_min_speech_frames: Hysteresis: speech frames required to enter “speaking”.
- vad.upstream_squelch_end_silence_frames: Hysteresis: silence frames required to exit “speaking”.

Common pitfalls:

- Too-short utterances (e.g., 20 ms) cause empty STT transcripts → raise `min_utterance_duration_ms` and ensure `webrtc_end_silence_frames` is not too low.
- Overly aggressive VAD (aggressiveness=2/3) may clip 8 kHz speech; prefer 0–1 for telephony.

## LLM block

- llm.initial_greeting: First message spoken by the agent (if provider supports explicit greeting or engine plays via TTS).
- llm.prompt: Persona/system instruction used by LLMs.
- llm.api_key: Optional API key for LLMs that require it.

## Providers

### OpenAI Realtime (monolithic agent)

- providers.openai_realtime.api_key: injected from `OPENAI_API_KEY` (env-only; do not commit secrets to YAML).
- providers.openai_realtime.api_version: `ga` (default) or `beta` (legacy payload/header behavior).
- providers.openai_realtime.model, voice, base_url: Model and voice.
- providers.openai_realtime.instructions: Persona override. Leave empty to inherit `llm.prompt`.
- providers.openai_realtime.greeting: Explicit greeting. Leave empty to inherit `llm.initial_greeting`.
- providers.openai_realtime.response_modalities: list of modalities, typically `[\"audio\"]` or `[\"audio\", \"text\"]`.
- providers.openai_realtime.provider_input_encoding/provider_input_sample_rate_hz: Format sent to OpenAI (typically PCM16); prefer matching this to the engine’s internal PCM rate to avoid extra resampling.
- providers.openai_realtime.input_encoding/input_sample_rate_hz: Inbound format; use `ulaw` at 8 kHz when AudioSocket() is invoked with `,ulaw` (engine converts to PCM before sending to OpenAI).
- providers.openai_realtime.output_encoding/output_sample_rate_hz: Provider output; for telephony, prefer `mulaw` at 8 kHz (for example: `output_encoding: mulaw`, `output_sample_rate_hz: 8000`) to avoid mid-stream PCM → μ-law conversion artifacts.
- providers.openai_realtime.target_encoding/target_sample_rate_hz: Downstream transport expectations (e.g., μ‑law at 8 kHz).
- providers.openai_realtime.egress_pacer_enabled: When true, OpenAI provider emits fixed 20 ms audio cadence (silence on underrun); prefer `false` when downstream playback already paces reliably.
- providers.openai_realtime.turn_detection: Server‑side VAD (type, silence_duration_ms, threshold, prefix_padding_ms); improves turn handling.
  - Metrics: `ai_agent_openai_assumed_output_sample_rate_hz`, `ai_agent_openai_provider_output_sample_rate_hz`, and `ai_agent_openai_measured_output_sample_rate_hz` are **low-cardinality gauges** (latest observed across calls). Use Call History for per-call debugging.

### OpenAI (pipelines)

Modular OpenAI pipeline components use `type: openai` provider blocks:

- `openai_llm`: Chat Completions (`chat_base_url`, `chat_model`)
- `openai_stt`: Speech-to-Text via `audio/transcriptions` (`stt_base_url`, `stt_model`)
- `openai_tts`: Text-to-Speech via `audio/speech` (`tts_base_url`, `tts_model`, `voice`, `response_format`)

Requirements:

- `OPENAI_API_KEY` must be set in the environment.

### Telnyx AI Inference (pipelines)

Telnyx AI Inference is supported as a modular LLM component:

- `telnyx_llm`: OpenAI-compatible Chat Completions (`chat_base_url`, `chat_model`, `temperature`, `max_tokens`, `response_timeout_sec`, `api_key_ref`)

Requirements:

- `TELNYX_API_KEY` must be set in the environment.

Notes:

- Telnyx supports many model IDs. Use the exact model ID returned by Telnyx `/models`.
- Some model IDs represent **external providers** (for example `openai/gpt-4o`). Those require `providers.telnyx_llm.api_key_ref` to be set (Integration Secret identifier) or Telnyx will return `400` with "OpenAI API key required…".
- For pipeline selection, set `AI_PROVIDER=telnyx_hybrid` (pipeline name) in your dialplan when forcing a per-extension pipeline.

### Deepgram Voice Agent

- providers.deepgram.api_key: injected from `DEEPGRAM_API_KEY` (env-only; do not commit secrets to YAML).
- providers.deepgram.model, providers.deepgram.tts_model: Deepgram Voice Agent + Aura TTS models.
- `providers.deepgram.agent_language`: Language for Deepgram Voice Agent mode (default: `en`).
- providers.deepgram.greeting: Agent greeting. Leave empty to inherit `llm.initial_greeting`.
- providers.deepgram.instructions: Persona override for the “think” stage; leave empty to inherit `llm.prompt`.
- providers.deepgram.input_encoding/input_sample_rate_hz: Keep `input_encoding=ulaw` at 8 kHz when AudioSocket runs μ-law transport.
- providers.deepgram.continuous_input: true to stream audio continuously.
  - Metrics: `ai_agent_deepgram_input_sample_rate_hz` and `ai_agent_deepgram_output_sample_rate_hz` are **low-cardinality gauges** (latest observed across calls). Use Call History for per-call debugging.

### Google (pipelines)

- google_llm.system_instruction/system_prompt: Persona; if missing, adapter falls back to `llm.prompt`.
- google_tts/tts fields: voice, language, audio encoding/sample rate, target format.
- google_stt/stt fields: encoding, language, model, sampleRateHertz.

### Groq Speech (pipelines)

Groq Speech uses OpenAI-compatible REST endpoints:

- STT: `https://api.groq.com/openai/v1/audio/transcriptions`
- TTS: `https://api.groq.com/openai/v1/audio/speech` (Orpheus, WAV-only)

Requirements:

- `GROQ_API_KEY` must be set in the environment.

Config notes:

- `groq_stt` options: `stt_model` (`whisper-large-v3-turbo`, `whisper-large-v3`), plus optional `language`, `prompt`, `response_format` (`json|verbose_json|text`), `temperature`, `timestamp_granularities`.
- `groq_tts` options: `tts_model` (`canopylabs/orpheus-v1-english`, `canopylabs/orpheus-arabic-saudi`), `voice` (Orpheus voice IDs), `response_format` (`wav` only), and output format controls (`target_encoding`/`target_sample_rate_hz` or pipeline `tts.format`).

### Local provider (pipelines)

- Local STT/LLM/TTS parameters live under pipeline `options`. The engine plays `llm.initial_greeting` first if configured.

### Google Live (monolithic agent)

- `providers.google_live.api_key`: injected from `GOOGLE_API_KEY` (env-only; do not commit secrets to YAML).
- `providers.google_live.llm_model`: Live LLM model name (see `config/ai-agent.yaml` for shipped defaults).
- `providers.google_live.tts_voice_name`: Live voice name (provider-specific).
- `providers.google_live.response_modalities`: `audio`, `text`, or `audio_text` (provider behavior varies by model generation).
- `providers.google_live.hangup_fallback_audio_idle_sec`: idle-audio timeout after hangup is armed.
- `providers.google_live.hangup_fallback_min_armed_sec`: minimum armed duration before fallback can fire.
- `providers.google_live.hangup_fallback_no_audio_timeout_sec`: timeout when provider emits no farewell audio.
- `providers.google_live.hangup_fallback_turn_complete_timeout_sec`: grace period waiting for `turnComplete` before fallback hangup.
- `providers.google_live.hangup_markers_enabled`: enable/disable marker-based hangup heuristics (end_call / assistant_farewell) used to arm `cleanup_after_tts`. Recommended `false` for production (prefer tool-driven hangup via `hangup_call`).
- `providers.google_live.ws_keepalive_enabled`: enable protocol-level WebSocket ping keepalive (pings only fire when the connection is idle).
- `providers.google_live.ws_keepalive_interval_sec`: ping interval when keepalive is enabled.
- `providers.google_live.ws_keepalive_idle_sec`: minimum idle time (no `realtimeInput`) before sending a ping.

### Deepgram Voice Agent (monolithic agent)

- `providers.deepgram.voice_agent_base_url`: WebSocket endpoint for Deepgram Voice Agent.
  - Default: `wss://agent.deepgram.com/v1/agent/converse`
  - YAML overrides allow regional endpoints or proxy URLs.

### ElevenLabs Agent (monolithic agent)

Full agent provider using ElevenLabs Conversational AI for premium voice quality.

> **Scope Note**: ElevenLabs is supported as a full agent (`elevenlabs_agent`) and as a TTS-only pipeline adapter (`elevenlabs_tts`).

- `providers.elevenlabs_agent.api_key`: injected from `ELEVENLABS_API_KEY` (env-only; do not commit secrets to YAML).
- `providers.elevenlabs_agent.agent_id`: injected from `ELEVENLABS_AGENT_ID` (env-only).
- `providers.elevenlabs_agent.voice_id`: Voice ID for TTS output (configured in agent dashboard).
- `providers.elevenlabs_agent.model_id`: Model ID (e.g., `eleven_flash_v2_5`).
- `providers.elevenlabs_agent.voice_settings`: Optional object with `stability`, `similarity_boost`, `style` (0.0-1.0).

**Tool Calling**: ElevenLabs tools must be defined in the ElevenLabs dashboard. The engine executes tool calls locally based on matching function names. See [ElevenLabs Implementation Guide](contributing/references/Provider-ElevenLabs-Implementation.md) for tool schema format.

**Audio Format**: ElevenLabs uses PCM16 at 16kHz. The engine automatically resamples from telephony μ-law 8kHz.

Example:
```yaml
providers:
  elevenlabs_agent:
    enabled: true
    voice_id: "pNInz6obpgDQGcFmaJgB"
    model_id: "eleven_flash_v2_5"
```

## Precedence summary

- Provider/pipeline explicit overrides (instructions/greeting) take priority.
- Otherwise providers/pipelines inherit `llm.prompt` / `llm.initial_greeting`.
- Env `AI_ROLE`/`GREETING` act as defaults when YAML does not specify values.

## Health & Contexts

- Health endpoint:
  - `health.host`: Bind address for `/live`, `/ready`, `/health`, and `/metrics` (default `127.0.0.1`).
  - `health.port`: Port for the health/metrics HTTP server (default `15000`).
  - Environment variables `HEALTH_BIND_HOST` / `HEALTH_BIND_PORT` override the YAML values when set.
- Contexts:
  - Inline: `contexts:` block in `config/ai-agent.yaml` defines named contexts (prompt, greeting, profile, provider, tools).
  - External: YAML files in `config/contexts/*.yaml` are also loaded.
    - Each file must define a `name` field; that becomes the context key.
    - `system_prompt` in external files is treated as `prompt` if `prompt` is not present.
    - If the same context `name` exists both inline and in an external file, the inline definition in `ai-agent.yaml` wins.

### Context Options

Each context supports the following fields:

- `prompt`: System prompt/persona instructions for the AI.
- `greeting`: Initial greeting spoken when call connects.
- `profile`: Audio profile name to use for this context.
- `provider`: Provider override for this context.
- `tools`: List of **in-call** tool names to enable for this context.
- `pre_call_tools`: List of pre-call tool names to run after answer, before the AI speaks (HTTP lookups/enrichment).
- `in_call_http_tools`: List of in-call HTTP tool names to allowlist for this context (defined under `in_call_tools:`).
- `post_call_tools`: List of post-call tool names to run after the call ends (webhooks/automation).
- `disable_global_pre_call_tools`: Disable specific global pre-call tools for this context.
- `disable_global_in_call_tools`: Disable specific global in-call tools for this context.
- `disable_global_post_call_tools`: Disable specific global post-call tools for this context.
- `background_music`: Music On Hold class name for ambient music during calls (see below).

### HTTP Tools (Phase Tools)

HTTP tools are configured in YAML and/or via the Admin UI:

- **Pre-call HTTP lookups**: live under `tools:<name>` with `kind: generic_http_lookup` and `phase: pre_call`.
- **Post-call webhooks**: live under `tools:<name>` with `kind: generic_webhook` and `phase: post_call`.
- **In-call HTTP tools**: live under `in_call_tools:<name>` with `kind: in_call_http_lookup` (AI-invoked during conversation).

See `docs/TOOL_CALLING_GUIDE.md` for full examples and variable substitution details.

### Background Music

Play ambient music during AI conversations. Music is mixed into the call audio.

- `contexts.<name>.background_music`: MOH class name (e.g., `default`, `ambient`).
  - When set, a snoop channel with Music On Hold starts when the call begins.
  - Music continues until the call ends.
  - Leave empty/omit to disable background music.

**Setup Requirements**:

1. Place audio files in `/var/lib/asterisk/moh/<class-name>/`
2. For FreePBX: Configure via **Settings → Music On Hold**
3. Supported formats: WAV, ulaw, alaw, sln, mp3

**Best Practices**:

- Use low-volume (15-20%) ambient/instrumental music
- Music is heard by the AI (affects VAD); loud music reduces accuracy
- Test with real calls before production

Example:
```yaml
contexts:
  support:
    greeting: "Hello, how can I help?"
    prompt: "You are a helpful support agent."
    background_music: "ambient"  # MOH class name
```

## Environment Variable Resolution

Environment variable placeholders (`${VAR}`, `${VAR:-default}`) are expanded for the **entire YAML file** when `config/ai-agent.yaml` is loaded.

Example (supported):
```yaml
providers:
  local:
    base_url: ${LOCAL_WS_URL:-ws://127.0.0.1:8765}  # ✅ Resolved
```

Notes:
- Expansion happens **before YAML parsing**. Use `${VAR:-default}` to avoid empty-string surprises.
- Avoid putting secrets directly in YAML; prefer `.env` + `${VAR}` placeholders.

## Admin UI HTTP Tool Testing (Security)

The Admin UI includes an HTTP tool **Test** feature that makes real outbound HTTP requests.

By default, the Admin UI blocks test requests to localhost/private targets to reduce SSRF risk if the UI is exposed beyond a trusted network.

- `AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE=1`: allow private/localhost targets (trusted network only).
- `AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS=host1,host2`: allow specific hostnames.
- `AAVA_HTTP_TOOL_TEST_FOLLOW_REDIRECTS=1`: allow redirects (default is disabled).


## Tips

- For noisy trunks, start with:
  - `barge_in.energy_threshold=2200`, `barge_in.min_ms=450`, `vad.webrtc_aggressiveness=1`.
- For lowest latency, start with:
  - `streaming.min_start_ms=250`, `streaming.jitter_buffer_ms=80`, `barge_in.min_ms=300` (expect more sensitivity to jitter).

## MCP (Experimental)

MCP-backed tools (Model Context Protocol) can be exposed through the existing tool calling system.

- Design + configuration guide: `docs/MCP_INTEGRATION.md`
