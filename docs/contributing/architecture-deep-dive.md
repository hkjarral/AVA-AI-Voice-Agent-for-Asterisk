---
trigger: always_on
description: Architecture Details for Asterisk AI Voice Agent v4.0 project
globs: src/**/*.py, *.py, docker-compose.yml, Dockerfile, config/ai-agent.yaml
---

# Asterisk AI Voice Agent - Architecture Documentation (v4.0)

## System Overview

The Asterisk AI Voice Agent v4.0 is a **production-ready, modular conversational AI system** that enables **real-time, two-way voice conversations** through Asterisk/FreePBX systems. It features a **modular pipeline architecture** that allows mixing and matching STT, LLM, and TTS providers, alongside support for **monolithic providers** like OpenAI Realtime and Deepgram Voice Agent.

### Key Architecture Components

- **Dual Transport Support** – AudioSocket (TCP, modern) for full agents and ExternalMedia RTP (UDP, legacy) for hybrid pipelines
- **Adaptive Streaming** – Downstream audio with automatic jitter buffering and file playback fallback
- **Modular Pipelines** – Independent STT, LLM, and TTS provider selection via YAML configuration
- **Production Monitoring** – Prometheus + Grafana stack with 50+ metrics and 5 dashboards
- **State Management** – Centralized SessionStore with type-safe call state tracking

### Golden Baselines (v4.0)

Three validated configurations ship production-ready:

1. **OpenAI Realtime** (`config/ai-agent.golden-openai.yaml`)
   - Monolithic provider (STT+LLM+TTS integrated)
   - Response time: 0.5-1.5 seconds
   - Server-side VAD for optimal turn detection

2. **Deepgram Voice Agent** (`config/ai-agent.golden-deepgram.yaml`)
   - Monolithic provider with Think stage reasoning
   - Response time: 1-2 seconds
   - Enterprise-grade quality

3. **Local Hybrid** (`config/ai-agent.golden-local-hybrid.yaml`)
   - Pipeline: Vosk (STT) + OpenAI (LLM) + Piper (TTS)
   - Response time: 3-7 seconds
   - Privacy-focused (audio stays local)

For deployment guidance, see [docs/PRODUCTION_DEPLOYMENT.md](../PRODUCTION_DEPLOYMENT.md).

## Architecture Overview

### Hybrid ARI + SessionStore + Conversation Coordinator

The production code still follows the **Hybrid ARI** call-control pattern and is in the process of migrating its state into the new `SessionStore` APIs:

- **Hybrid ARI**: `_handle_caller_stasis_start_hybrid()` answers the caller, creates a mixing bridge, and either originates a Local channel or spawns an ExternalMedia channel before handing media over to the rest of the engine.
- **SessionStore (in-progress)**: The engine now instantiates `SessionStore` and `PlaybackManager` (see `src/core/`), and new flows such as playback gating and RTP SSRC mapping query this shared store. Legacy dictionaries like `self.active_calls` and `self.caller_channels` still exist for backwards compatibility and will be phased out as handlers are rewritten to push/read data exclusively through `SessionStore`.
- **ConversationCoordinator (new)**: `ConversationCoordinator` subscribes to session changes, toggles audio capture, records barge-in attempts, schedules capture fallbacks, and keeps Prometheus gauges aligned with each call’s state. PlaybackManager delegates all gating changes to the coordinator.
- **Local Provider Tuning**: The local AI server now reads `LOCAL_LLM_*` and `LOCAL_STT/TTS_*` environment variables so operators can swap GGUF/ONNX assets or lower response latency without rebuilding images.

This staged architecture provides:

- **Improved State Consistency**: Critical paths (playback gating, RTP routing, TTS cleanup) now rely on a single store.
- **Type Safety for New Code**: New helpers work with dataclasses (`CallSession`, `PlaybackRef`) instead of ad-hoc dicts, while older handlers are refactored gradually.
- **Observability**: `/metrics` now exposes `ai_agent_tts_gating_active`, `ai_agent_audio_capture_enabled`, and `ai_agent_barge_in_events_total` counters, while `/health` includes a `conversation` block summarising gating and capture status.
- **Maintainability Path**: The separation between call control, state management, and observability is documented and enforced for new features, while older sections remain untouched until their migration tickets are completed.

### Streaming Transport Defaults (Milestone 5)

`StreamingPlaybackManager` converts provider output into paced 20 ms frames and enforces `streaming.*` settings from `config/ai-agent.yaml`:

- **`min_start_ms`** – initial jitter buffer warm-up (default 120 ms) before the first frame is sent. If the buffer starts below this threshold the engine waits until enough audio arrives.
- **`low_watermark_ms`** – when depth drops below this watermark playback pauses briefly to rebuild the buffer instead of restarting the transport.
- **`fallback_timeout_ms`** – adaptive timer reset after each successful send; if no audio is transmitted within this window playback falls back to file mode.
- **`provider_grace_ms`** – grace period after cleanup to absorb any late provider frames without logging warnings.
- Defaults currently favour reliability: 120 ms warm-up, 80 ms low watermark, 4 s fallback timeout, and 500 ms grace for late provider chunks.

Defaults align with Deepgram’s recommended 100–120 ms buffering window and can be overridden per deployment. Refer to `docs/contributing/milestones/milestone-5-streaming-transport.md` for implementation details and tuning guidance.

- **OpenAI Realtime codec alignment** – inbound AudioSocket PCM16 (8 kHz) is upsampled to 24 kHz before publishing via `input_audio_buffer`, and OpenAI’s 24 kHz PCM16 output is resampled back to the configured target (8 kHz PCM16 for AudioSocket playback by default). Keep `openai_realtime.provider_input_sample_rate_hz` and `openai_realtime.output_sample_rate_hz` at 24000 so the session advertises the correct formats while the engine handles the final downsampling.

#### Post‑TTS End Protection (Echo‑Loop Mitigation)

To prevent the agent from hearing itself immediately after a turn ends, the engine enforces a short, configurable guard window right after TTS playback completes:

- `config/ai-agent.yaml`: `barge_in.post_tts_end_protection_ms` (default 350 ms in project YAML; model default 250 ms)
- `src/core/session_store.py`: stamps `CallSession.tts_ended_ts` when the last gating token is cleared
- `src/engine.py::_audiosocket_handle_audio()`: drops inbound frames while `now - tts_ended_ts < post_tts_end_protection_ms`

This guard absorbs trailing provider frames and bridge mix artifacts that can arrive just after playback finishes, eliminating self‑echo loops on follow‑on turns. Operators can tune the window (250–500 ms typical) depending on trunk quality and desired barge‑in responsiveness.

### Configurable Pipelines (Milestone 7)

`config/ai-agent.yaml` defines one or more named pipelines under the `pipelines` key:

```yaml
pipelines:
  default:
    stt: deepgram_streaming
    llm: openai_realtime
    tts: deepgram_tts
    options:
      language: en-US
  sales:
    stt: whisper_local
    llm: local_llm
    tts: azure_tts
active_pipeline: default
```

The engine loads the active pipeline at startup (or reload) and instantiates the referenced adapters. Each component adheres to the streaming interfaces defined in `src/pipelines/base.py`, allowing local and cloud services to be mixed without code changes. Example configs live in `examples/pipelines/` once Milestone 7 lands.

#### Pipeline Architecture Overview

| Layer | Responsibilities | Key Files / Types |
| ----- | ---------------- | ----------------- |
| Configuration (`YAML`) | Declare named pipelines and provider blocks (`providers.local`, `providers.deepgram`, `providers.google`, `providers.openai`, etc.). Each pipeline specifies `stt`, `llm`, `tts`, and an `options` map passed verbatim to adapters. | [`config/ai-agent.yaml`](../../config/ai-agent.yaml), [`docs/contributing/milestones/milestone-7-configurable-pipelines.md`](milestones/milestone-7-configurable-pipelines.md) |
| Pydantic Models | Validate YAML, normalize legacy configs, and expose typed access via `PipelineEntry`, `ProviderConfig`, `PipelineOptions`. | [`src/config.py`](../../src/config.py) |
| Orchestrator | Resolve the active pipeline, look up component factories, and hydrate adapters with provider + pipeline options. Handles hot reload by rebuilding component bindings while leaving in-flight calls untouched. | [`src/pipelines/orchestrator.py`](../../src/pipelines/orchestrator.py) |
| Component Adapters | Implement the STT / LLM / TTS interfaces for each provider. Adapters honor selective roles (e.g., `local_stt` can operate without LLM/TTS) and surface capability metadata to the orchestrator. | [`src/pipelines/local.py`](../../src/pipelines/local.py) (via adapters automatically registered), [`src/pipelines/deepgram.py`](../../src/pipelines/deepgram.py), [`src/pipelines/openai.py`](../../src/pipelines/openai.py), [`src/pipelines/google.py`](../../src/pipelines/google.py) |
| Engine Integration | `PipelineOrchestrator` injects the instantiated adapters into the conversation coordinator for new calls. Hot reload swaps adapters for subsequent calls after config validation succeeds. | [`src/engine.py`](../../src/engine.py), [`src/core/conversation_coordinator.py`](../../src/core/conversation_coordinator.py) |

##### Configuration Schema

```yaml
providers:
  local:
    enable_stt: true
    enable_llm: true
    enable_tts: true
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
    tts_voice: aura-asteria-en
pipelines:
  local_only:
    stt: local_stt
    llm: local_llm
    tts: local_tts
    options:
      locale: en-US
  hybrid_support:
    stt: local_stt
    llm: openai_realtime
    tts: deepgram_tts
    options:
      llm:
        temperature: 0.6
      tts:
        format: ulaw
active_pipeline: hybrid_support
```

- `providers.*` blocks define credentials and provider-wide defaults; adapters retrieve them through provider-specific config dataclasses. The local provider now accepts `ws_url`, `connect_timeout_sec`, `response_timeout_sec`, and `chunk_ms` so deployments can tune the WebSocket handshake and batching cadence without code changes.
- `pipelines.*.options` is merged with provider defaults and handed to adapters via `AdapterContext`. Nested maps (e.g., `options.tts.voice`) are preserved.
- `examples/pipelines/cloud_only_openai.yaml` provides a turnkey OpenAI-only configuration for modular pipelines (OpenAI STT + OpenAI LLM + OpenAI TTS).

##### Adapter Mapping

| YAML Value | Adapter Class | Notes |
| ---------- | ------------- | ----- |
| `local_stt`, `local_llm`, `local_tts` | `LocalSTTAdapter`, `LocalLLMAdapter`, `LocalTTSAdapter` (registered by the orchestrator when local provider is enabled) | Respect selective enable flags so unused roles do not bind WebSocket channels. |
| `deepgram_streaming`, `deepgram_llm`, `deepgram_tts` | `DeepgramSTTAdapter`, `DeepgramLLMAdapter` (future), `DeepgramTTSAdapter` | STT uses WebSocket AudioSocket transport; TTS synthesizes via REST and converts to μ-law. |
| `openai_stt`, `openai_llm`, `openai_tts` | `OpenAISTTAdapter`, `OpenAILLMAdapter`, `OpenAITTSAdapter` | STT uses `audio/transcriptions`; LLM uses Chat Completions by default; TTS uses `audio/speech` and converts to μ-law for telephony playback. |
| `google_stt`, `google_llm`, `google_tts` | `GoogleSTTAdapter`, `GoogleLLMAdapter`, `GoogleTTSAdapter` | REST-based integrations leveraging Google Speech-to-Text, Generative Language, and Text-to-Speech APIs. |

When the configuration watcher detects a change, it:

1. Reloads YAML via `load_config()`, producing a new `Config` instance with validated `PipelineEntry` objects.
2. Instantiates a fresh `PipelineOrchestrator` with provider configs and the requested active pipeline.
3. Swaps the orchestrator reference inside the engine; in-flight calls continue using the previous adapters, while new conversations resolve components from the updated pipeline.

### Hot Reload Strategy

Configuration changes propagate through the existing async watcher introduced in Milestone 1. When `config/ai-agent.yaml` changes:

1. The watcher validates the schema via Pydantic models.
2. Streaming parameters, logging levels, and pipeline definitions reload in memory.
3. Active calls keep their current pipeline; new calls use the updated configuration.

Operators can trigger a reload manually with `make engine-reload` (wrapper around `kill -HUP $(pgrep -f ai-engine)` on the host). This preserves uptime while enabling rapid iteration on streaming quality or provider selection.

### Monitoring & Analytics

This project ships **aggregate, low-cardinality Prometheus metrics** and a **per-call Call History** database for debugging and support workflows.

The legacy bundled Prometheus/Grafana compose + dashboards were removed from the main repo path; operators should bring their own monitoring stack and scrape `/metrics` if desired. See `docs/MONITORING_GUIDE.md`.

## Recent Progress and Current State

- **Production Ready**: Real calls run end-to-end using the Hybrid ARI flow with ExternalMedia capture.
- **AudioSocket Regression Pass (2025-09-22)**: Latest regression validated the AudioSocket-first capture path from `ai-agent-media-fork`, with a two-way Deepgram call completing successfully end-to-end.
- **SessionStore Adoption Started**: Playback gating, RTP SSRC tracking, and health reporting use `SessionStore`, with remaining handlers scheduled for migration.
- **AudioSocket Listener Integration**: With `audio_transport=audiosocket` the engine now exposes the TCP listener itself (default `0.0.0.0:8090`, configurable via the new `audiosocket.*` block) and binds inbound UUIDs straight into `SessionStore` before forwarding frames through the VAD pipeline.
- **ExternalMedia RTP Integration**: When `audio_transport=externalmedia` the engine accepts RTP (UDP) on the configured port or range (default `18080:18099`), resamples to 16 kHz, and forwards frames through the VAD pipeline.
- **Downstream Playback**: `PlaybackManager` writes μ-law files to `/mnt/asterisk_media/ai-generated` and triggers deterministic bridge playbacks with gating.
- **Complete Pipeline**: RTP → VAD/Fallback → Provider WebSocket → LLM/TTS → File playback all operate in production.
- **Deepgram Integration Hardened**: The Deepgram provider now uses a typed config with environment fallbacks, so the cloud path can be enabled without disturbing the local provider wiring.
- **Deepgram Continuous Streaming**: Deepgram sessions now stream caller audio frame-by-frame (via `continuous_input`) while VAD still drives conversation state, leaving the local provider path unchanged.
- **Deepgram AgentAudio Handler Patched**: `on_provider_event` now updates `CallSession` directly and cancels provider timeout tasks, fixing the NameError that previously left audio capture permanently gated.
- **Streaming Backlog**: File-based playback of every `AgentAudio` micro-chunk keeps capture gated for most of the turn; we now buffer Deepgram chunks until `AgentAudioDone`, but additional gating tweaks are required before callers can barge in mid-response.
- **Response Latency (Instrumented)**: Latest regression kept every turn under ~1.8 s; latency histograms (`ai_agent_turn_latency_seconds`, `ai_agent_transcription_to_audio_seconds`) now expose the timing data while gauges reset cleanly post-call.
- **Greeting Compatibility**: Providers lacking `text_to_speech` (e.g., Deepgram Voice Agent) now skip engine-side greeting synthesis to avoid startup exceptions.
- **Ongoing Cleanup**: Legacy dict-based state and verbose logging remain until remaining handlers are refactored to the new core abstractions.
- **Fallback Audio Processing**: Configuration defaults to 4-second buffers (`fallback_interval_ms=4000`) to guarantee STT ingestion when VAD is silent.
- **Echo‑Loop Resolved**: Added post‑TTS end protection (`barge_in.post_tts_end_protection_ms`) and aligned Deepgram input to 8 kHz; two‑way telephonic conversation confirmed stable (2025‑09‑24 13:17 PDT).

### Health Endpoint

- A minimal health endpoint is available from the `ai-engine` (default `0.0.0.0:15000/health`). It reports:
  - `ari_connected`: ARI WebSocket/HTTP status
  - `rtp_server_running`: whether the RTP server is active
  - `audiosocket_listening`: whether the built-in AudioSocket listener is running
- `active_calls`: number of tracked calls (via `SessionStore.get_session_stats()`)
  - `providers`: readiness flags per provider
  - `audio_transport`: current transport mode (`audiosocket`, `externalmedia`, etc.)
  - `conversation`: summary now includes `latest_turn_latency_s` and `latest_transcription_latency_s` derived from new latency timers.
  
Configure via env:

- `HEALTH_HOST` (default `0.0.0.0`), `HEALTH_PORT` (default `15000`).

### Known Constraints

- RTP server requires the configured port or port range (default `18080:18099`) to be available for ExternalMedia integration
- ExternalMedia channels must be properly bridged with caller channels for audio flow
- SSRC mapping is critical for audio routing - first RTP packet automatically maps SSRC to caller
- TTS gating requires proper PlaybackFinished event handling for feedback prevention
- Fallback audio processing uses 4-second intervals (`fallback_interval_ms=4000`) for reliable STT processing

## GA Track & Next Steps

1. **Milestone 5 – Streaming Transport Production Readiness**
   - Harden pacing logic, expose configurable defaults, and ship telemetry so greetings/turns are clear by default.
2. **Milestone 6 – OpenAI Realtime Voice Agent**
   - Implement the OpenAI Realtime provider, align codecs, and document regression expectations alongside Deepgram.
3. **Milestone 7 – Configurable Pipelines & Hot Reload**
   - Enable YAML-driven STT/LLM/TTS composition, validate hot reload, and update examples/tests.
4. **Milestone 8 – Optional Monitoring Stack**
   - Provide Prometheus + Grafana dashboards with Makefile helpers and extension hooks for future analytics.
5. **GA Release**
   - Run full regression suite (Deepgram + OpenAI), publish telemetry-backed tuning guide, update quick-start/install docs, and tag the GA release.

### Roadmap Tracking

Ongoing milestones and their acceptance criteria live in `docs/ROADMAP.md`. Update that file after each deliverable so any collaborator—or tool-specific assistant—can resume work without manual hand-off.

### IDE Playbooks

- **Codex / CLI**: `Agents.md` and `.agent/workflows/` summarize deployment runbooks and regression expectations for terminal-first workflows.
- **Cursor**: `.cursor/rules/asterisk_ai_voice_agent.mdc` mirrors the same guardrails, emphasizing SessionStore usage, dual upstream transport, and streaming-first playback.
- **Windsurf**: `.windsurf/rules/asterisk_ai_voice_agent.md` keeps IDE prompts aligned with the roadmap so code and documentation stays in sync.
- **Shared artifacts**: Golden baselines (`docs/baselines/golden/`), regression evidence (`docs/resilience.md`), and architecture/roadmap snapshots (`docs/contributing/architecture-deep-dive.md`, `docs/ROADMAP.md`) are the canonical hand-off regardless of editor; update them after every call so all IDEs inherit the latest context.

## Contributing

- See the repository-level [Contributing Guide](../../CONTRIBUTING.md) for branching strategy and PR workflow.
- Typical flow:
  - Fork and branch from `develop`.
  - Open a PR against `staging` with a clear description and testing notes.
  - Keep changes small and documented; update `docs/` where behavior changes.
- License: MIT. See [LICENSE](../../LICENSE).

## Architecture Diagrams

### 1. EXTERNALMEDIA CALL FLOW

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   ASTERISK      │    │   AI ENGINE     │    │   LOCAL AI      │    │   SHARED MEDIA  │
│   (PJSIP/SIP)   │    │   CONTAINER     │    │   SERVER        │    │   DIRECTORY     │
└─────────────────┘    └─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │                       │
         │ 1. Incoming Call     │                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │                       │                       │
         │ 2. ExternalMedia Stream│                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │                       │                       │
         │ 3. StasisStart Event │                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │                       │                       │
         │ 4. Answer Channel     │                       │                       │
         │◄──────────────────────┤                       │                       │
         │                       │                       │                       │
         │ 5. Real-time Audio    │                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │                       │                       │
         │                       │ 6. Forward to Local AI Server                 │
         │                       ├──────────────────────►│                       │
         │                       │                       │                       │
         │                       │                       │ 7. STT Processing    │
         │                       │                       ├──────────────────────►│
         │                       │                       │                       │
         │                       │                       │ 8. LLM Processing    │
         │                       │                       ├──────────────────────►│
         │                       │                       │                       │
         │                       │                       │ 9. TTS Synthesis     │
         │                       │                       ├──────────────────────►│
         │                       │                       │                       │
         │                       │ 10. Audio Response    │                       │
         │                       │◄──────────────────────┤                       │
         │                       │                       │                       │
         │ 11. Save Audio File   │                       │                       │
         │◄──────────────────────┤                       │                       │
         │                       │                       │                       │
         │ 12. Play Audio File   │                       │                       │
         │◄──────────────────────┤                       │                       │
         │                       │                       │                       │
         │ 13. Call Complete     │                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │                       │                       │
```

### 2. DEEPGRAM PROVIDER CALL FLOW

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   ASTERISK      │    │   AI ENGINE     │    │   DEEPGRAM      │    │   OPENAI        │
│   (PJSIP/SIP)   │    │   CONTAINER     │    │   CLOUD         │    │   CLOUD         │
└─────────────────┘    └─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │                       │
         │ 1. Incoming Call     │                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │                       │                       │
         │ 2. ExternalMedia Stream│                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │                       │                       │
         │ 3. StasisStart Event │                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │                       │                       │
         │ 4. Answer Channel     │                       │                       │
         │◄──────────────────────┤                       │                       │
         │                       │                       │                       │
         │ 5. Real-time Audio    │                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │                       │                       │
         │                       │ 6. Forward to Deepgram                       │
         │                       ├──────────────────────►│                       │
         │                       │                       │                       │
         │                       │                       │ 7. STT + LLM + TTS   │
         │                       │                       ├──────────────────────►│
         │                       │                       │                       │
         │                       │ 8. Audio Response    │                       │
         │                       │◄──────────────────────┤                       │
         │                       │                       │                       │
         │ 9. Save Audio File   │                       │                       │
         │◄──────────────────────┤                       │                       │
         │                       │                       │                       │
         │ 10. Play Audio File  │                       │                       │
         │◄──────────────────────┤                       │                       │
         │                       │                       │                       │
         │ 11. Call Complete    │                       │                       │
         ├──────────────────────►│                       │                       │
         │                       │                       │                       │
```

### 3. EXTERNALMEDIA SERVER ARCHITECTURE

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   ASTERISK      │    │   AI ENGINE     │    │   PROVIDER      │
│   ExternalMedia   │    │   ExternalMedia   │    │   SYSTEM        │
│ (Port Range      │    │   Server        │    │                 │
│ 18080-18099)     │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │ 1. TCP Connection     │                       │
         ├──────────────────────►│                       │
         │                       │                       │
         │ 2. Raw Audio Stream   │                       │
         ├──────────────────────►│                       │
         │                       │                       │
         │                       │ 3. Process Audio      │
         │                       ├──────────────────────►│
         │                       │                       │
         │                       │ 4. AI Response        │
         │                       │◄──────────────────────┤
         │                       │                       │
         │ 5. File Playback      │                       │
         │◄──────────────────────┤                       │
         │                       │                       │
```

## Key File Architecture

```
src/
├── engine.py                    # Hybrid ARI orchestrator (legacy dicts + SessionStore bridge)
│   ├── _handle_stasis_start()   # Entry point for caller/local/external-media channels
│   ├── _on_rtp_audio()          # Routes RTP frames through VAD/fallback and to providers
│   └── on_provider_event()      # Handles AgentAudio events from providers
│
├── core/
│   ├── models.py                # Typed dataclasses (CallSession, PlaybackRef, ProviderSession)
│   ├── session_store.py         # Central store for call/session/playback state
│   └── playback_manager.py      # Deterministic playback + gating logic
│
├── rtp_server.py               # ExternalMedia RTP server (per-call UDP sockets, default range 18080-18099)
│   ├── start()                  # Bind UDP socket and launch receiver loop
│   ├── _rtp_receiver()          # Parse RTP headers, resample μ-law → PCM16 16 kHz
│   └── engine_callback          # Dispatches SSRC-tagged audio back to engine
│
├── providers/
│   ├── base.py                  # AIProviderInterface abstract class
│   ├── deepgram.py              # Cloud provider (WebSocket streaming)
│   └── local.py                 # Local provider (bridges to local AI server via WebSocket)
│
├── ari_client.py                # Asterisk REST Interface client
└── config.py                    # Pydantic configuration models + loader
```

## Critical Differences

| **Aspect** | **ExternalMedia Architecture** | **Previous Snoop Architecture** |
|------------|------------------------------|----------------------------------|
| **Audio Input** | RTP (UDP) via ExternalMedia | ARI ChannelAudioFrame events |
| **Reliability** | Guaranteed real-time stream | Unreliable event-based system |
| **Asterisk Config** | Requires dialplan modification | No dialplan changes needed |
| **Connection Type** | UDP media stream + ARI control | WebSocket event subscription |
| **Audio Format** | Raw ulaw stream | Base64 encoded frames |
| **Error Handling** | Connection-based recovery | Event-based error handling |
| **Performance** | Lower latency, higher throughput | Higher latency, event overhead |

## ExternalMedia Integration

### Call Flow: ExternalMedia Model

The current implementation keeps Asterisk in control of the media pipe while the engine coordinates call state and audio processing.

1. **Call Initiation**: A new call hits the Stasis dialplan context (`from-ai-agent` or similar), handing control to `engine.py`.
2. **ExternalMedia Origination**: `_handle_caller_stasis_start_hybrid()` answers the caller, creates a mixing bridge, and originates an ExternalMedia channel via ARI (`_start_external_media_channel`). When that channel enters Stasis, the engine bridges it with the caller and records the mapping in `SessionStore`.
3. **Audio Stream Starts**: Once bridged, Asterisk streams μ-law RTP packets to the engine’s `RTPServer` (default `0.0.0.0:18080-18099`). `RTPServer` parses RTP headers, resamples audio to 16 kHz, and calls `_on_rtp_audio(ssrc, pcm_16k)`.
4. **Real-time Conversation**:
   - `_on_rtp_audio` tracks the SSRC→call association in `SessionStore`, applies VAD / fallback buffering, and forwards PCM frames to the active provider through `provider.send_audio`.
   - The provider (Deepgram or Local WebSocket server) performs STT → LLM → TTS and emits AgentAudio events back to the engine.
5. **Media Playback**:
   - `PlaybackManager.play_audio` writes the synthesized μ-law bytes to `/mnt/asterisk_media/ai-generated`, registers a gating token in `SessionStore`, and instructs ARI to play the file on the bridge with a deterministic playback ID.
6. **Cleanup**:
   - `PlaybackManager.on_playback_finished` handles the `PlaybackFinished` event, clears the gating token, and removes the temporary audio file.

This orchestration leverages ExternalMedia for reliable inbound audio while keeping outbound playback file-based until streaming TTS is released.

## Dialplan Configuration

### ARI-Based Architecture (v4.0)

v4.0 uses **ARI-based architecture** where the engine handles all audio transport setup. The dialplan's sole responsibility is to hand the call to Stasis.

**Minimal Dialplan** (works for all 3 golden baselines):

```asterisk
[from-ai-agent]
exten => s,1,NoOp(Asterisk AI Voice Agent v4.0)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

**How It Works:**
1. Call enters `Stasis(asterisk-ai-voice-agent)`
2. Engine receives StasisStart event via ARI
3. Engine creates mixing bridge
4. **For full agents** (OpenAI Realtime, Deepgram): Engine originates `AudioSocket/<host:port>/<uuid>/c(slin)` channel via ARI
5. **For hybrid pipelines** (Local Hybrid): Engine originates ExternalMedia channel via ARI
6. Engine bridges transport channel with caller
7. Two-way audio flows through bridge

**Optional: Provider Override via Channel Variables:**

```asterisk
[from-ai-agent-support]
exten => s,1,NoOp(AI Agent - Customer Support)
 same => n,Set(AI_PROVIDER=deepgram)  ; Optional override
 same => n,Set(AI_CONTEXT=support)    ; Custom context
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

**Important:** Do NOT use `AudioSocket()` in the dialplan. The engine manages AudioSocket channels internally via ARI.

### AudioSocket Streaming (Feature-Flag) — Wire Format & Pacing

When `audio_transport=audiosocket` and `downstream_mode=stream` are enabled, the engine can stream provider audio back to Asterisk over the same AudioSocket connection.

- Wire format selection is controlled by `audiosocket.format` in `config/ai-agent.yaml` (or `AUDIOSOCKET_FORMAT` env).
  - `ulaw` (default): engine sends μ-law 8 kHz frames of 160 bytes per 20 ms frame.
  - `slin16` (aka `slinear`): engine converts provider μ-law to PCM16 and sends 320-byte frames per 20 ms.
- Outbound pacing: the engine segments audio into exact 20 ms frames and sends them at real-time cadence to prevent Asterisk buffer overruns (`translate.c: Out of buffer space`).
- Inbound decode: if the dialplan sends μ-law (typical), the engine decodes μ-law → PCM16 at 8 kHz before resampling to 16 kHz for VAD.
- Codec guardrail: startup audits now warn if provider `input_encoding` disagrees with `audiosocket.format`; keep both set to `ulaw` when the dialplan calls `AudioSocket(...,ulaw)`.
- Provider streaming events: providers emit `AgentAudio` bytes with `streaming_chunk=true` and a final `AgentAudioDone` with `streaming_done=true` to control the streaming window.

#### AudioSocket Channel Interface (ARI-Originated)

- The Hybrid ARI flow now originates an `AudioSocket/<host:port>/<uuid>/c(slin)` channel directly via ARI and bridges it with the caller.
- Dialplan responsibility is limited to answering the inbound call and running `Stasis(asterisk-ai-voice-agent)`; the `ai-agent-media-fork` Local context is no longer required.
- The engine tracks the AudioSocket channel (`session.audiosocket_channel_id`) and ensures it enters the same mixing bridge as the caller before streaming begins.
- UUID binding still happens via the AudioSocket server; `session.audiosocket_uuid` maps the TLV handshake back to the caller so outbound streaming remains aligned.
- Cleanup tears down the AudioSocket channel, removes its bridge mapping, and disconnects the TCP connection to keep compatibility with both Deepgram and the local provider.

Implementation references:

- `src/core/streaming_playback_manager.py` — format-aware conversion, 20 ms frame segmentation, pacing, remainder buffering
- `src/engine.py::_audiosocket_handle_audio` — inbound μ-law decode and 8k→16k resample for VAD
- `src/providers/deepgram.py` — emits `AgentAudio`/`AgentAudioDone` with streaming flags and call_id
- `src/config.py::AudioSocketConfig` — `audiosocket.format` (default `ulaw`)

### Transport Selection

**AudioSocket (Modern - Recommended for Full Agents)**:
- **Use for**: OpenAI Realtime, Deepgram Voice Agent (monolithic providers)
- **Advantages**: Lower latency, streaming TTS support, simpler architecture
- **Implementation**: Engine originates AudioSocket channel via ARI
- **Configuration**: `audio_transport: audiosocket` in config YAML

**ExternalMedia RTP (Legacy - For Hybrid Pipelines)**:
- **Use for**: Local Hybrid, modular STT+LLM+TTS pipelines
- **Advantages**: Battle-tested, file-based playback compatibility
- **Implementation**: Engine originates ExternalMedia channel via ARI
- **Configuration**: `audio_transport: externalmedia` in config YAML

### Integration Quick Start

1. **Add Dialplan**: Create `[from-ai-agent]` context (3 lines)
2. **Configure FreePBX Route**: Point inbound route to `from-ai-agent` custom destination
3. **Select Configuration**: Choose golden baseline in `config/ai-agent.yaml`
4. **Test**: Place call, monitor engine logs for StasisStart → bridge creation

### Optional: ExternalMedia RTP Bridging

In deployments that require RTP/SRTP interop, an optional path using Asterisk `ExternalMedia` may be enabled to bridge media via RTP. This is not required for the default ExternalMedia architecture and should be considered only when standards-based RTP interop is necessary.

## Streaming TTS over ExternalMedia Gateway (Feature Flag)

With `downstream_mode=stream`, the engine now streams provider audio directly back to Asterisk over the ExternalMedia RTP leg (μ-law @ 8 kHz). The jitter buffer is managed in process and the transport will automatically fall back to file playback if the downstream path becomes unhealthy. The remaining work in this area focuses on:

- **Barge-in**: detect inbound speech while streaming and cancel/attenuate TTS on demand.
- **Reliability**: expand keepalive/reconnect logic beyond the initial implementation and expose underrun/overrun counters.
- **Observability**: extend metrics with end-to-end latency, queue depth, and retransmission counters for streamed audio.

The streaming path remains feature-flagged; deployments can switch back to file playback instantly by reverting `downstream_mode` to `file`.

## Streaming Observability (Milestone 6)

The engine exposes additional Prometheus metrics and an expanded `/health` for streaming state:

- Prometheus metrics (scrape `/metrics`):
  - `ai_agent_streaming_active{call_id}` — 1 when a streaming playback is active
  - `ai_agent_streaming_bytes_total{call_id}` — bytes queued to streaming playback
  - `ai_agent_streaming_fallbacks_total{call_id}` — count of file fallbacks invoked
  - `ai_agent_streaming_jitter_buffer_depth{call_id}` — queued chunks in jitter buffer
  - `ai_agent_streaming_last_chunk_age_seconds{call_id}` — seconds since last chunk
  - `ai_agent_streaming_keepalives_sent_total{call_id}` — keepalive ticks sent
  - `ai_agent_streaming_keepalive_timeouts_total{call_id}` — timeouts detected by keepalive
  - RTP ingress metrics:
    - `ai_agent_rtp_frames_received_total`, `ai_agent_rtp_frames_processed_total`, `ai_agent_rtp_packet_loss_total`, `ai_agent_rtp_active_sessions`
  - Conversation latency (existing):
    - `ai_agent_turn_latency_seconds`, `ai_agent_transcription_to_audio_seconds`
    - `ai_agent_last_turn_latency_seconds{call_id,provider}`, `ai_agent_last_transcription_latency_seconds{call_id,provider}`

- Health endpoint (`/health`) now includes a `streaming` block (in addition to `conversation`):
  - `active_streams`: count of active stream playbacks
  - `ready_count`, `response_count`: provider-reported readiness/response flags
  - `fallbacks_total`: cumulative fallbacks across active calls
  - `last_error`: last streaming error string (if any)

### Streaming flow summary

1. Provider emits `AgentAudio` micro-chunks with `streaming_chunk=true`.
2. Engine enqueues chunks to `StreamingPlaybackManager`, which manages a jitter buffer sized from `streaming.jitter_buffer_ms` and `streaming.chunk_size_ms` and now streams those chunks back to Asterisk via the shared `RTPServer` (μ-law frames over the existing ExternalMedia leg).
3. Keepalive loop sends periodic ticks; if `last_chunk_age > connection_timeout_ms`, fallback to file playback is triggered and the remaining audio is pushed through the legacy playback path.
4. On `AgentAudioDone` with `streaming_done=true`, streaming closes cleanly, gating clears, and the conversation returns to `listening`.

All streaming state is also reflected in `SessionStore` (`CallSession` fields: `streaming_*`) and resets on cleanup.

## Real-Time Conversation Management

### RTP Server Pattern

Two-way audio hinges on the `RTPServer` implementation in `src/rtp_server.py`:

- **Transport**: UDP socket bound to the configured host/port range (default `0.0.0.0:18080-18099`) – Asterisk’s `ExternalMedia()` application sends 20 ms μ-law frames to this endpoint.
- **Packet Handling**: `_rtp_receiver()` parses RTP headers, tracks expected sequence numbers/packet loss, converts μ-law to PCM16, and resamples 8 kHz audio to 16 kHz using `audioop.ratecv`.
- **Engine Callback**: Every decoded frame is delivered back to `engine._on_rtp_audio(ssrc, pcm_16k)` where VAD, fallback buffering, and provider routing are performed. SSRCs are mapped to call sessions on the first packet through `SessionStore`.
- **Outbound Audio**: Downstream audio remains file-based (no RTP transmit path yet); playback continues to flow through ARI bridges managed by `PlaybackManager`.

### State Management

Call lifecycle is tracked across both the legacy dictionaries and the new `SessionStore`:

- **Connecting**: Caller enters Stasis, bridge is created, and ExternalMedia channel is originated.
- **Streaming**: ExternalMedia RTP arrives; SSRC mapping enables per-call routing into the VAD pipeline.
- **Processing**: Providers receive buffered frames via `send_audio`; responses transition conversation state to `processing` until playback completes.
- **Speaking**: `PlaybackManager` writes μ-law files, toggles gating tokens, and awaits `PlaybackFinished` events.
- **Cleanup**: `_cleanup_call()` tears down bridges/channels and removes sessions from both legacy maps and `SessionStore`.

### Connection & Error Handling

- **Per-call Isolation**: Each SSRC maps to a single call; `RTPServer` maintains lightweight `RTPSession` stats (packet loss, jitter buffer state).
- **Resilience**: Packet loss and out-of-order packets are logged; fallback buffering ensures speech still reaches STT if VAD misses it.
- **Resource Cleanup**: `engine.stop()` stops the RTP server and `SessionStore.cleanup_expired_sessions()` removes stale entries.

### Performance Targets

- **Audio Latency**: Maintain <200 ms decode/dispatch for inbound RTP frames.
- **End-to-End Response**: Aim for <2 s voice response; provider timeout watchdogs reset conversations after 30 s.
- **Streaming STT**: Fallback sends 4 s audio chunks (configurable) when VAD is silent to keep transcripts flowing.
- **Parallel Processing**: Greeting playback gates AudioSocket capture until TTS completes to avoid echo.

## Testing and Verification

### ExternalMedia Testing

- **Socket Availability**: Confirm the RTP server binds to the configured UDP port or port range (default `18080:18099`) without collisions.
- **Audio Stream Testing**: Stream μ-law audio over ExternalMedia and verify RTP frames reach `_on_rtp_audio`.
- **Provider Integration**: Ensure buffered audio reaches the active provider WebSocket session.
- **Error Handling**: Simulate packet loss / SSRC churn and monitor recovery logging.

### Critical Testing Points

- **RTP Server**: Must be listening on the configured UDP port or range (default `18080:18099`)
- **SSRC Mapping**: Must associate the first packet on each SSRC with the active call
- **Audio Format Handling**: Must process μ-law audio correctly
- **Provider Integration**: Must forward audio to correct provider
- **File Playback**: Must successfully play generated audio to callers
- **Connection Cleanup**: Must properly close connections on call end

## Troubleshooting Guide

### ExternalMedia-Specific Issues

**No RTP Packets Observed**:

- Check that the RTP server is running on the configured UDP port/range (default `18080:18099`)
- Verify the dialplan invokes `ExternalMedia()` with the correct host/port
- Confirm firewall rules allow UDP traffic on the configured port

**Audio Not Received**:

- Verify the ExternalMedia channel is established (confirm `StasisStart` for the caller and ExternalMedia entries)
- Check audio format compatibility (μ-law when `external_media.codec=ulaw`)
- Monitor RTP server logs for packet receipt and decoder errors

**Connection Drops**:

- Confirm Asterisk keeps the ExternalMedia channel bridged; unbridged channels stop media immediately
- Check network stability between Asterisk and the container hosting the RTP server
- Review RTP server logs for timeouts (`last_packet_at`) and packet-loss counters

**Performance Issues**:

- Monitor RTP packet loss and jitter metrics emitted by `RTPServer`
- Check VAD/fallback buffer sizes in engine logs for overflows
- Verify provider processing speed (watch WebSocket send queue depth)

When issues arise:

1. Check RTP server logs for packet activity and SSRC mapping events
2. Verify Asterisk dialplan configuration
3. Send test RTP packets (e.g., `rtpplay`, `pjsip send media`) to the configured RTP port (default `18080`)
4. Monitor audio stream processing
5. Check provider integration and response times
6. Verify file-based playback functionality
