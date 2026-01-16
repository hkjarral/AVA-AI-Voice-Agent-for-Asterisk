# AI Voice Agent Roadmap

This roadmap tracks the development and evolution of the Asterisk AI Voice Agent. It combines the foundational work (Milestones 1-7), v4.0 GA production-readiness work (Transport Orchestrator & Audio Profiles), and future enhancements.

Each milestone includes scope, implementation details, and verification criteria.

## Milestone 1 — SessionStore-Only State (✅ Completed)

- **Goal**: Remove remaining legacy dictionaries from `engine.py` and rely exclusively on `SessionStore` / `PlaybackManager` for call state.
- **Tasks**:
  - Replace reads/writes to `active_calls`, `caller_channels`, etc. with SessionStore helpers.
  - Update cleanup paths so `/health` reflects zero active sessions after hangup.
  - Add lightweight logging when SessionStore migrations hit unknown fields.
- **Acceptance**:
  - Single test call shows: ExternalMedia channel created, RTP frames logged, gating tokens add/remove, `/health` returns `active_calls: 0` within 5s of hangup.
  - No `active_calls[...]` or similar dict mutations remain in the codebase.

## Milestone 2 — Provider Switch CLI (✅ Completed)

- **Goal**: Provide one command to switch the active provider, restart the engine, and confirm readiness.
- **What We Shipped**:
  - Added `scripts/switch_provider.py` and Makefile targets `provider-switch`, `provider-switch-remote`, and `provider-reload` for local + server workflows.
  - Health endpoint now reports the active provider readiness so the change can be validated at a glance.
- **Verification**:
  - `make provider=<name> provider-reload` updates `config/ai-agent.yaml`, restarts `ai-engine`, and the next call uses the requested provider. Logged on 2025-09-22 during regression.

## Milestone 3 — Model Auto-Fetch (✅ Completed)

- **Goal**: Automatically download and cache local AI models based on the host architecture.
- **What We Shipped**:
  - Added `models/registry.json` and the `scripts/model_setup.sh` utility to detect hardware tier, download the right STT/LLM/TTS bundles, and verify integrity.
  - Makefile task `make model-setup` (documented in Agents/Architecture) calls the script and skips work when models are already cached.
- **Verification**:
  - First-run downloads populate `models/` on both laptops and the server; subsequent runs detect cached artifacts and exit quickly. Local provider boots cleanly after `make model-setup`.

## Milestone 4 — Conversation Coordinator & Metrics (✅ Completed)

- **Goal**: Centralize gating/barge-in decisions and expose observability.
- **What We Shipped**:
  - Introduced `ConversationCoordinator` (SessionStore-integrated) plus Prometheus gauges/counters for capture state and barge-in attempts.
  - Health endpoint now exposes a `conversation` summary and `/metrics` serves Prometheus data.
- **Verification**:
  - 2025-09-22 regression call shows coordinator toggling capture around playback, `ai_agent_tts_gating_active` returning to zero post-call, and `/metrics` scrape succeeding from the server.

## Milestone 5 — Streaming Transport Production Readiness (✅ Completed)

- **Goal**: Promote the AudioSocket streaming path to production quality with adaptive pacing, configurable defaults, and telemetry. Details and task breakdown live in `docs/contributing/milestones/milestone-5-streaming-transport.md`.
- **What We Shipped**:
  - Configurable streaming defaults in `config/ai-agent.yaml` (`min_start_ms`, `low_watermark_ms`, `fallback_timeout_ms`, `provider_grace_ms`, `jitter_buffer_ms`).
  - Post‑TTS end protection window (`barge_in.post_tts_end_protection_ms`) to prevent agent self‑echo when capture resumes.
  - Deepgram input alignment to 8 kHz (`providers.deepgram.input_sample_rate_hz: 8000`) to match AudioSocket frames.
  - AudioSocket wire format validated as `slin` with provider guardrails (keep provider input/target encodings aligned with the configured AudioSocket format).
  - Expanded YAML comments with tuning guidance for operators.
  - Regression docs updated with findings and resolutions.
- **Verification (2025‑09‑24 13:17 PDT)**:
  - Two-way telephonic conversation acceptable end‑to‑end; no echo‑loop in follow‑on turns.
  - Gating toggles around playback as expected; post‑TTS guard drops residual frames.
  - Deepgram regression replay shows no “low RMS” warnings once μ-law alignment is in place.
  - Operators can fine‑tune behaviour via YAML without code changes.

## Milestone 6 — OpenAI Realtime Voice Agent (✅ Completed)

- **Goal**: Add an OpenAI Realtime provider so Deepgram ↔️ OpenAI switching happens via configuration alone. Milestone instructions: `docs/contributing/milestones/milestone-6-openai-realtime.md`.
- **Dependencies**: Milestone 5 complete; OpenAI API credentials configured.
- **Primary Tasks**:
  - Implement `src/providers/openai_realtime.py` with streaming audio events.
  - Extend configuration schema and env documentation (`README.md`, `docs/contributing/architecture-deep-dive.md`).
  - Align provider payloads with the latest OpenAI Realtime guide:
    - Use `session.update` with nested `audio` schema and `output_modalities` (e.g., `session.audio.input.format`, `session.audio.input.turn_detection`, `session.audio.output.format`, `session.audio.output.voice`).
    - Remove deprecated/unknown fields (e.g., `session.input_audio_sample_rate_hz`).
    - Use `response.create` without `response.audio`; rely on session audio settings. For greeting, send explicit `response.instructions`.
    - Add `event_id` on client events and handle `response.done`, `response.output_audio.delta`, `response.output_audio_transcript.*`.
  - Greeting behavior: send `response.create` immediately on connect with explicit directive (e.g., “Please greet the user with the following: …”).
  - VAD/commit policy:
    - When server VAD is enabled (`session.audio.input.turn_detection`), stream with `input_audio_buffer.append` only; do not `commit`.
    - When VAD is disabled, serialize commits and aggregate ≥160 ms per commit.
- **What We Shipped**:
  - Implemented `src/providers/openai_realtime.py` with robust event handling and transcript parsing.
  - Fixed keepalive to use native WebSocket `ping()` frames (no invalid `{"type":"ping"}` payloads).
  - Resampled AudioSocket PCM16 (8 kHz) to 24 kHz before commit and advertised 24 kHz PCM16 input/output in `session.update` so OpenAI Realtime codecs stay in sync.
  - μ-law alignment: requested `g711_ulaw` from OpenAI and passed μ-law bytes directly to Asterisk (file playback path), eliminating conversion artifacts.
  - Greeting on connect using `response.create` with explicit instructions.
  - Hardened error logging to avoid structlog conflicts; added correlation and visibility of `input_audio_buffer.*` acks.
  - Added YAML streaming tuning knobs (`min_start_ms`, `low_watermark_ms`, `jitter_buffer_ms`, `provider_grace_ms`) and wired them into `StreamingPlaybackManager`.
  - Refreshed `examples/pipelines/cloud_only_openai.yaml` so the modular OpenAI pipeline (STT+LLM+TTS) works out-of-the-box.

- **Verification (2025‑09‑25 08:59 PDT)**:
  - Successful regression call with initial greeting; two-way conversation sustained.
  - Multiple agent turns played cleanly (e.g., 16000B ≈2.0s and 40000B ≈5.0s μ-law files) with proper gating and `PlaybackFinished`.
  - No OpenAI `invalid_request_error` on keepalive; ping fix validated.

- **Acceptance**:
  - Setting `default_provider: openai_realtime` results in a successful regression call with greeting and two-way audio.
  - Logs show `response.created` → output audio chunks → playback start/finish with gating clear; no `unknown_parameter` errors.

## Milestone 7 — Configurable Pipelines & Hot Reload (✅ Completed)

- **Goal**: Support multiple named pipelines (STT/LLM/TTS) defined in YAML, with hot reload for rapid iteration. See `docs/contributing/milestones/milestone-7-configurable-pipelines.md`.
- **What We Shipped**:
  - YAML pipelines with `active_pipeline` switching and safe hot reload.
  - Pipeline adapters for Local, OpenAI (Realtime + Chat), Deepgram (STT/TTS), and Google (REST) with option merging.
  - Engine integration that injects pipeline components per call and preserves in‑flight sessions across reloads.
  - Logging defaults and knobs; streaming transport integration consistent with Milestone 5.
- **Validation (2025‑09‑27 → 2025‑09‑28)**:
  - Local‑only pipeline (TinyLlama) 2‑minute regression passed: greeting, STT finals, LLM replies (6–13 s), local TTS playback.
  - Hybrid pipeline A: local STT + OpenAI LLM + local TTS passed (two‑way conversation, stable gating).
  - Hybrid pipeline B: local STT + OpenAI LLM + Deepgram TTS passed (fast greeting/turns, clean playback).
  - Evidence captured in `archived/regressions/local-call-framework.md` with timestamps, byte sizes, and latency notes.
- **Acceptance**:
  - Swapping `active_pipeline` applies on the next call after reload.
  - Custom pipeline regressions succeed using YAML only.
  - Changing OpenAI/Deepgram endpoints or voice/model via YAML takes effect on next call.

## Milestone 8 — Transport Stabilization (✅ Completed Oct 25, 2025)

- **Goal**: Eliminate audio garble and pacing issues by enforcing AudioSocket invariants and proper format handling. Details in `docs/contributing/milestones/milestone-8-transport-stabilization.md`.
- **Tag**: `v1.0-p0-transport-stable`
- **What We Shipped**:
  - Fixed critical AudioSocket format override bug (commit `1a049ce`) preventing caller codec from overriding YAML wire format settings.
  - Enforced little-endian PCM16 on AudioSocket wire; removed all egress byte-swap logic.
  - Added pacer idle cutoff (1200ms) to prevent underflows and long tails.
  - Set `chunk_size_ms: auto` with 20ms default; reframe provider chunks to pacer cadence.
  - One-shot TransportCard logging at call start showing wire/provider formats.
- **Verification (2025-10-25, Call 1761424308.2043)**:
  - Clean two-way conversation end-to-end; user confirmed "Audio pipeline is working really well."
  - Zero underflows observed; wall_seconds ≈ content duration (no long tails).
  - SNR: 64.6-68.2 dB (excellent); provider bytes ratio 1.0 (perfect alignment).
  - TransportCard present in logs; no egress swap messages anywhere.
- **Acceptance**:
  - Golden metrics match baseline within 10% tolerance.
  - No garbled greeting; underflows ≈ 0; correct AudioSocket format per YAML.

## Milestone 9 — Audio Gating & Echo Prevention (✅ Completed Oct 26, 2025)

- **Goal**: Enable production-ready OpenAI Realtime provider with echo prevention and natural conversation flow. Corresponds to ROADMAPv4 P0.5 milestone.
- **What We Shipped**:
  - Audio Gating Manager (`src/core/audio_gating_manager.py`) with VAD-based interrupt detection.
  - Provider-specific gating (opt-in per provider) integrated with OpenAI Realtime.
  - Critical VAD configuration: `webrtc_aggressiveness: 1` (balanced mode) to prevent false echo detection.
  - 24kHz PCM16 input/output format for OpenAI; server-side VAD integration.
  - Golden baseline configuration documented in `OPENAI_REALTIME_GOLDEN_BASELINE.md`.
- **Verification (2025-10-26, Call 1761449250.2163)**:
  - Duration: 45.9s, SNR: 64.7 dB; zero self-interruption events.
  - Buffered: 0 chunks (vs 50 with aggressiveness: 0); gate closures: 1 time (vs 50+).
  - Natural conversation flow validated; user feedback: "much better results" ✅
- **Acceptance**:
  - Clean audio with no self-interruption; OpenAI's server VAD handles turn-taking naturally.
  - Gate stays open properly when agent speaking (correct behavior).

## Milestone 10 — Transport Orchestrator & Audio Profiles (✅ Completed Oct 26, 2025)

- **Goal**: Provider-agnostic operation with per-call audio profile selection and automatic capability negotiation. Implementation of ROADMAPv4 P1 milestone.
- **Dependencies**: Milestones 8-9 complete; golden baselines established.
- **What We Shipped**:
  - `TransportOrchestrator` class (`src/core/transport_orchestrator.py`) for dynamic profile resolution.
  - Audio profiles system in YAML: `telephony_ulaw_8k`, `openai_realtime_24k`, `wideband_pcm_16k`, `telephony_responsive`.
  - Per-call channel variable overrides (all optional; fallback to YAML):
    - `AI_PROVIDER` — which provider (deepgram, openai_realtime, local_hybrid)
    - `AI_AUDIO_PROFILE` — which transport profile
    - `AI_CONTEXT` — semantic context mapping to YAML `contexts.*` for prompt/greeting/profile
  - Provider capability negotiation with ACK parsing (Deepgram `SettingsApplied`, OpenAI `session.updated`).
  - Legacy config synthesis for backward compatibility; zero-change upgrade path.
- **Verification (2025-10-26)**:
  - Deepgram validation (Call 1761504353.2179): SNR 66.8 dB, `telephony_responsive` profile applied correctly.
  - OpenAI Realtime validation (Call 1761505357.2187): SNR 64.77 dB, `openai_realtime_24k` profile, perfect gating.
  - Dynamic profile switching via `AI_AUDIO_PROFILE` confirmed working without YAML edits.
- **Acceptance**:
  - Switching `AI_AUDIO_PROFILE` changes transport plan; call remains stable.
  - Provider ACK empty → remediation logged; call continues with fallback.
  - Multi-provider parity (Deepgram + OpenAI) demonstrated.

## Milestone 11 — Post-Call Diagnostics & Troubleshooting (✅ Completed Oct 26, 2025)

- **Goal**: Automated post-call RCA with AI-powered diagnosis matching manual analysis quality. ROADMAPv4 P2.1 deliverable.
- **What We Shipped**:
  - `agent rca` CLI command for instant post-call analysis (CLI v5.1.4; legacy alias: `agent troubleshoot`).
  - RCA-level metrics extraction from Docker logs (provider bytes, drift, underflows, VAD, transport alignment).
  - Golden baseline comparison (OpenAI Realtime, Deepgram, streaming performance).
  - Format/sampling alignment detection (config vs runtime validation; catches AudioSocket format mismatches).
  - AI-powered diagnosis with context-aware prompts (OpenAI/Anthropic); filters benign warnings.
  - Quality scoring: 0-100 with EXCELLENT/FAIR/POOR/CRITICAL verdicts.
  - Greeting segment awareness (excludes timing artifacts from quality scoring).
- **Usage Examples**:

  ```bash
  agent rca                                    # Analyze most recent call
  agent rca --call 1761523231.2199
  agent rca --json                             # JSON-only output
  agent troubleshoot --last --no-llm            # Legacy (hidden) advanced flag
  ```

- **Verification (2025-10-26)**:
  - Call 2199 alignment test: Manual RCA "GOOD - SNR 67.3 dB" matched `agent rca` "EXCELLENT - 100/100" ✅
  - Format detection validated; catches slin vs ulaw mismatches, frame size alignment errors.
- **Acceptance**:
  - Accurate call detection (filters AudioSocket infrastructure channels).
  - RCA-level metrics depth matches manual analysis; AI diagnosis provides actionable fixes.

## Milestone 12 — Setup & Validation Tools (✅ Completed Oct 26, 2025)

- **Goal**: Complete operator workflow from zero to production; minimize time to first call. ROADMAPv4 P2.2 milestone.
- **What We Shipped**:
  - `agent setup` — Interactive setup wizard (CLI v5.1.4; legacy aliases: `agent init`, `agent quickstart`).
  - `agent check` — Standard diagnostics report (CLI v5.1.4; legacy alias: `agent doctor`).
  - `agent rca` — Post-call RCA (CLI v5.1.4; legacy alias: `agent troubleshoot`).
  - `agent update` — Safe updater for repo checkouts (CLI v5.1+).
  - Hidden advanced commands retained for compatibility: `agent demo`, `agent dialplan`, `agent config validate`.
  - Health checks with exit codes for CI/CD integration; JSON output for programmatic use.
- **Verification (2025-10-26)**:
  - `agent check` output: `PASS ... Overall: PASS (system looks healthy)`
  - All tools validated on production server; work without user configuration changes.
- **Acceptance**:
  - `agent setup` completes setup in < 5 minutes.
  - `agent check` validates environment before first call; clear error messages with remediation.
  - **Impact**:
  - New operator to first call: **<30 minutes** (vs hours previously).
  - Self-service debugging without developer intervention.

## Milestone 13 — Config Cleanup & Migration (✅ Completed Oct 26, 2025)

- **Goal**: Simplify configuration, reduce operator footguns, and establish clear separation between production and diagnostic settings. ROADMAPv4 P2.3 milestone.
- **What We Shipped**:
  - Migration script: `scripts/migrate_config_v4.py` with dry-run and apply modes.
  - Moved 8 diagnostic settings to environment variables (`DIAG_*` prefix, `LOG_*` settings).
  - Deprecated 9 legacy settings with migration path and warnings.
  - Config version 4 schema validation; backward compatible with deprecation warnings.
  - 21% cleaner config (374 → 294 lines); 49% smaller file (16K → 8.1K).
- **Settings Moved to Environment Variables**:
  - `DIAG_EGRESS_SWAP_MODE`, `DIAG_ENABLE_TAPS`, `DIAG_TAP_PRE_SECS`, `DIAG_TAP_POST_SECS`
  - `DIAG_TAP_OUTPUT_DIR`, `STREAMING_LOG_LEVEL`
  - Safer production defaults (diagnostics opt-in only).
- **Verification (2025-10-26)**:
  - Migration script tested on production config; container rebuilt successfully.
  - Health checks pass (`agent check: PASS`); no deprecation warnings with env vars.
- **Acceptance**:
  - Deprecated knobs removed from YAML schema; warnings logged if env var override set.
  - Config version field validated on load; migration instructions clear.
- **Impact**:
  - Clearer separation: production vs diagnostic settings in separate files.
  - Easier maintenance; diagnostic settings in one place (`.env`).

---

## Milestone 14 — Monitoring, Feedback & Guided Setup (Call History–First) (🟡 Iterating)

- **Goal**: Ship an ops/debugging experience that is **Call History–first** (per-call), with **Troubleshoot** workflows in Admin UI + CLI, and **low-cardinality** `/metrics` (optional BYO dashboards).
- **Implementation Notes**:
  - Prometheus/Grafana are no longer shipped/provisioned from this repo; treat external dashboards as a bring-your-own workflow.
  - Per-call debugging lives in Call History and Troubleshoot, not time-series labels.
- **Docs**: `docs/contributing/milestones/milestone-14-monitoring-stack.md`

---

## Milestone 16 — Tool Calling System (✅ Completed Nov 10, 2025)

- **Goal**: Implement unified, provider-agnostic tool calling architecture enabling AI agents to perform real-world actions. Detailed specifications in `docs/contributing/milestones/milestone-16-tool-calling-system.md`.
- **What We Shipped**:
  - **Core Framework** (537 lines): `Tool`, `ToolDefinition`, `ToolRegistry`, `ToolExecutionContext`
  - **Provider Adapters** (417 lines): Deepgram + OpenAI Realtime format translation
  - **Telephony Tools** (736 lines): `transfer_call`, `cancel_transfer`, `hangup_call`
  - **Business Tools** (822 lines): `request_transcript`, `send_email_summary`
  - **Direct SIP Origination**: Eliminated Local channels for perfect audio
  - **Conversation Tracking**: Real-time turn history in both providers
  - **Documentation**: `TOOL_CALLING_GUIDE.md` (600+ lines)
- **Verification (2025-11-10)**:
  - Transfer execution: <150ms for 4-step cleanup sequence
  - Audio quality: Perfect bidirectional (SNR >60 dB), zero Local channel issues
  - Email delivery: 100% success rate with DNS MX validation
  - Calls validated: 1762880919.4536 (Deepgram), 1762734947.4251 (OpenAI)
- **Acceptance**:
  - Tools work with all providers without modification
  - <100ms execution overhead per tool call
  - Production-validated with 50+ successful executions
- **Impact**:
  - ~2,500 lines of new code across 10 files
  - Enabled AI agents to perform complex call workflows
  - Zero audio issues with direct SIP origination

### Testing & Quality Assurance (Milestone 16 Extension) (✅ Completed Nov 12, 2025)

- **Goal**: Establish comprehensive test coverage for tool calling system with automated CI/CD enforcement to protect critical features and enable confident iteration.
- **What We Shipped**:
  - **Test Suite** (2,400 lines across 5 files): 111 tool-specific tests
  - **CI/CD Integration**: baseline coverage gate on `staging`/`main` (`.github/workflows/ci.yml`)
  - **Regression hardening**: extended checks on `staging`/`main` merges (`.github/workflows/regression-hardening.yml`)
- **Docs**: `tests/README.md`

---

## Milestone 17 — Google Live Provider (✅ Completed Nov 14, 2025)

- **Goal**: Add Google Live as a low-latency “full agent” provider option.
- **Docs**: `docs/contributing/milestones/milestone-17-google-live.md`

---

## Milestone 18 — Hybrid Pipelines Tool Implementation (✅ Completed Nov 19, 2025)

- **Goal**: Enable tool execution for modular pipelines and validate AudioSocket/ExternalMedia parity.
- **Docs**: `docs/contributing/milestones/milestone-18-hybrid-pipelines-tool-implementation.md`

---

## Milestone 19 — Admin UI Implementation (✅ Completed Dec 2025)

- **Goal**: Production-ready Admin UI for setup, configuration, logs, and container operations.
- **Docs**: `docs/contributing/milestones/milestone-19-admin-ui-implementation.md`

---

## Milestone 20 — ElevenLabs Provider (✅ Completed Dec 2, 2025)

- **Goal**: Add ElevenLabs Conversational AI provider (premium voice quality) with tool calling support.
- **Docs**: `docs/contributing/milestones/milestone-20-elevenlabs.md`

---

## Milestone 21 — Call History & Analytics Dashboard (✅ Completed Dec 18, 2025)

- **Goal**: Persist call records and transcripts with operator-friendly debugging and export.
- **Docs**: `docs/contributing/milestones/milestone-21-call-history.md`

Keep this roadmap updated after each milestone to help any collaborator—or future AI assistant—pick up where we left off.

---

## Future Roadmap

## Milestone 22 — Outbound Campaign Dialer (🟡 Draft)

- **Goal**: Add scheduled outbound campaigns with pacing, AMD detection, voicemail drop, and Admin UI management.
- **Docs**: `docs/contributing/milestones/milestone-22-outbound-campaign-dialer.md`

---

## Milestone 15 — Groq Speech (STT/TTS) for Modular Pipelines (🟡 Draft)

- **Goal**: Add Groq Speech STT/TTS as modular pipeline components (`groq_stt`, `groq_tts`) to complement `groq_llm`.
- **Docs**: `docs/contributing/milestones/milestone-15-groq-speech-pipelines.md`

### Hi-Fi Audio & Multi-Provider Demos (Planned)

- **Goal**: Improve resampling quality for hi-fi profiles and demonstrate multi-provider parity. ROADMAPv4 P3 milestone.
- **Dependencies**: Milestones 8-13 complete; golden baselines validated.
- **What We Plan to Ship**:
  - Higher-quality resamplers (speexdsp/soxr) for `hifi_pcm_24k` profile to improve frequency response.
  - Multi-provider demonstration configs showing Deepgram + OpenAI combinations.
  - Extended metrics for cadence reframe efficiency and pacer drift control.
  - Side-by-side audio quality comparisons at 24kHz sampling rate.
- **Timeline**: 2-4 days
- **Acceptance**:
  - Side-by-side playback comparisons show improved frequency response at 24kHz vs audioop resampling.
  - Drift remains ≈ 0%; pacer efficiency metrics stable.
  - Published demo configs for common provider combinations (deepgram-stt + openai-llm + deepgram-tts).

---

### v4.3.0 (November 2025) - Pipeline Tool Execution & Transport Validation

**Release Date**: November 19, 2025  
**Focus**: Full transport/mode parity and tool execution for modular pipelines

> **Note**: This work was completed in Milestone 18 and released as v4.3.0 (not v4.2.x). The v4.2.1 patch release (Nov 18) focused on onboarding improvements (quickstart wizard, enhanced installer).

**AudioSocket + Pipeline Validation**:

- Validated AudioSocket transport with pipeline mode (Milestone 18)
- 4-call comprehensive test suite (Google Live, Deepgram, OpenAI Realtime, local_hybrid)
- All transport/mode combinations now production-ready
- Documentation updated with historical context and v4.0+ validation notes

**Tool Execution for Pipelines** (AAVA-85):

- Extended tool calling to modular pipelines via OpenAI Chat Completions API
- Pipeline orchestrator executes tools via unified tool_registry
- Validated tools: transfer, hangup_call, send_email_summary, request_transcript
- Fixed 5 critical bugs during implementation (config, hangup method, farewell handling)

**Configuration Improvements**:

- Tightened validation warnings (only flag when neither providers nor pipelines configured)
- Updated Transport-Mode-Compatibility.md to reflect AudioSocket + Pipeline as validated
- Removed incorrect warnings about valid configurations

**Production Validation**:

- Call 1763610866.6294: local_hybrid pipeline, 54.57s, tool execution successful
- Call 1763582071.6214: transfer tool, ringgroup resolution working
- Zero false configuration warnings in production

---

### v4.5.3 (December 2025) - Security Hardening Sprint

**Release Date**: December 22, 2025  
**Focus**: Security hardening, RTP safety, and production readiness

**Security Hardening** (AAVA-131, AAVA-136, AAVA-137):

- Preflight is treated as required in docs; stronger “you are exposing a control plane” warnings for operators.
- Remote access is explicit: Admin UI supports `UVICORN_HOST=0.0.0.0` (default in docker-compose for out-of-box UX) and requires `JWT_SECRET`; production guidance recommends reverse proxy/VPN and tight firewall rules.
- ARI runtime reconnect supervisor with exponential backoff; `/ready` reflects true ARI connection state; removed blocking IO from async runtime paths (`time.sleep` → `asyncio.sleep`).

**RTP Safety & Hardening**:

- ExternalMedia RTP hardening: remote endpoint pinning (`external_media.lock_remote_endpoint`) and allowlist support (`external_media.allowed_remote_hosts`).
- Fixed SSRC routing to prevent cross-call audio mixing by using authoritative `call_id` in engine callback.

**Observability & Debugging**:

- Default provider is now `local_hybrid` (pipeline-first GA default); readiness probe is pipeline-aware when `default_provider` references a pipeline.
- Low-cardinality Prometheus metrics only (no per-call labels like `call_id`); per-call debugging is via Admin UI → Call History.
- Removed legacy bundled Prometheus/Grafana monitoring stack and `monitoring/` assets from the main repo path (bring-your-own monitoring).
- Local AI logging: suppressed noisy websocket handshake errors; improved client connection logging.

**Admin UI Adoption Readiness** (AAVA-130):

- Fixed JWT_SECRET load-order vulnerability; CORS restricted by default with env override.
- Atomic writes for config-mutating endpoints; fixed config export crash (`CONFIG_PATH.exists()`).
- Admin UI backend: model switching mappings for `faster_whisper` STT and `melotts` TTS.

**Tests**:

- Added tests for RTP routing/security and Prometheus label cardinality.

---

### v4.5.2 (December 2025) - Local AI Enhancements

**Release Date**: December 16, 2025  
**Focus**: Local AI capabilities, model management, and stability fixes

- Kokoro API mode (`KOKORO_MODE=api`) for OpenAI-compatible TTS endpoints.
- Model hot-swap via WebSocket (STT/TTS/LLM without container restart) and improved auto-reconnect behavior.
- MCP tool integration framework and initial tool implementations.
- Compatibility fixes (websockets 15.x, resend 2.x, sherpa-onnx 1.12.19).

---

### v4.5.0 (December 2025) - Admin UI Stability & Ops UX

**Release Date**: December 11, 2025  
**Focus**: Admin UI stability, safer config writes, and better operator feedback

- Admin UI stability fixes across config editor state, validation, and restart-required UX.
- Docker operations improvements (prefer Docker SDK restart; compose fallback).
- Atomic writes + backup rotation for `config/ai-agent.yaml` and `.env`.
- Health endpoint improvements (uptime, pending timers, live conversation metrics).

---

### v4.4.2 (December 2025) - Local AI Enhancements

**Release Date**: December 8, 2025  
**Focus**: New STT/TTS backends, model management, DevOps improvements

**New STT Backends**:

- **Kroko ASR** (AAVA-92): High-quality streaming ASR with 12+ languages, no hallucinations
- **Sherpa-ONNX** (AAVA-95): Low-latency local streaming ASR using sherpa-onnx
- Configure via `LOCAL_STT_BACKEND=kroko|sherpa|vosk`

**New TTS Backends**:

- **Kokoro TTS** (AAVA-95): High-quality neural TTS with multiple voices (af_heart, af_bella, am_michael)
- **ElevenLabs TTS Adapter** (AAVA-114): Cloud TTS for modular pipelines
- Configure via `LOCAL_TTS_BACKEND=kokoro|piper`

**Model Management System** (AAVA-99, 101-104, 108):

- Dashboard quick-switch for STT/TTS/LLM models
- Model enumeration API: `GET /api/local-ai/models/available`
- Model switch API: `POST /api/local-ai/models/switch` with hot-reload
- 2-step UI flow: "Pending" badge + "Apply & Restart" button
- Error handling with rollback on switch failure

**Admin UI Improvements**:

- **Pipeline UI Backend Display** (AAVA-116): Shows active STT/TTS backend for local components
- **Directory Health Card** (AAVA-93): Dashboard shows media directory permissions

**DevOps & CI** (AAVA-112, 113):

- Optional build args to exclude unused backends (smaller images)
- CI image size checks with budgets (ai-engine: 1.5GB, local-ai-server: 4GB)
- Enhanced Trivy vulnerability scanning for both images
- Outdated dependency reporting

**Bug Fixes**:

- Local pipeline validation fix (AAVA-118): Pipelines no longer disabled on validation failure
- Docker DNS troubleshooting docs (AAVA-119)
- Config capability validation (AAVA-115)

**Documentation**:

- New `LOCAL_ONLY_SETUP.md` guide for fully local deployment

---

### v4.4.1 (November 2025) - Admin UI v1.0

**Release Date**: November 30, 2025  
**Focus**: Web-based administration interface

**Admin UI v1.0**:

- **Setup Wizard**: Visual provider configuration with API key validation
- **Real-time Dashboard**: System metrics, container status, CPU/memory/disk
- **Configuration Management**: Full CRUD for providers, pipelines, contexts, audio profiles
- **Live Log Streaming**: WebSocket-based log viewer from ai-engine
- **Raw YAML Editor**: Monaco-based editor with syntax validation
- **Environment Manager**: Visual editor for `.env` variables
- **Container Control**: Start/stop/restart containers from UI
- **JWT Authentication**: Token-based auth with 24-hour expiry, default admin/admin

**ElevenLabs Conversational AI** (AAVA-90):

- Full agent provider with WebSocket-based real-time conversations
- Premium voice quality with natural conversation flow
- Tool calling support (define in ElevenLabs dashboard, execute locally)

**Background Music** (AAVA-89):

- In-call ambient music using Asterisk Music On Hold
- Configurable per-context via Admin UI or YAML

**Provider Registration System**:

- Explicit validation of supported provider types
- Full agent vs modular provider classification

---

### v4.5 Planning (Q1 2026)

**Reality Check (Current State)**:

- **Primary operator workflow**: Admin UI → Call History + Troubleshoot; CLI parity via `agent check` + `agent rca` (legacy aliases: `agent doctor`, `agent troubleshoot`; `agent demo` hidden).
- **Observability stance**: Call History–first; `/metrics` is supported but must remain low-cardinality (BYO dashboards; no repo-shipped Prometheus/Grafana stack).
- **Core runtime**: Asterisk 18+ ARI + Stasis app (`asterisk-ai-voice-agent`), ExternalMedia RTP default; AudioSocket supported; per-call overrides via `AI_PROVIDER` / `AI_AUDIO_PROFILE` / `AI_CONTEXT`.
- **Providers/pipelines in GA**: full-agent providers (`deepgram`, `openai_realtime`, `google_live`, `elevenlabs_agent`) + modular pipelines (including `local_hybrid`; adapters include Google STT/TTS and ElevenLabs TTS for pipelines).
- **Change-safety today**: GitHub Actions CI is intentionally scoped to `staging`/`main`; CI validates Python tests + coverage baseline, plus regression hardening checks (CLI/Admin UI build + Docker build sanity). Live Asterisk/PSTN calls are not part of CI by default.

**Testing & Quality**:

- ✅ Unit tests for tool adapters and email tools (111 tests, 27-29% coverage - Milestone 16)
- ✅ AudioSocket + Pipeline validation (4-call test suite - Milestone 18)
- ⏳ Integration tests for transfer workflows (unit tests complete, full workflow pending)
- 🎯 Increase CI coverage threshold to 30% then 40% (currently 27%)
- ✅ GitHub Actions CI is branch-scoped to `staging`/`main` with a baseline coverage gate (`.github/workflows/ci.yml`)
- ⏳ Regression hardening workflow for `staging`/`main` merges (extended checks: CLI + Admin UI build + Docker builds) (`.github/workflows/regression-hardening.yml`)

**Admin UI Adoption Readiness** (AAVA-130 - ✅ COMPLETED Dec 2025):

- ✅ AAVA-130: Fix JWT secret load-order vulnerability; tighten CORS defaults
- ✅ AAVA-130: Fix config export crash (`CONFIG_PATH.exists()`)
- ✅ AAVA-130: Atomic writes for remaining hotspots (`wizard.py`, `local_ai.py`, `system.py`)
- ✅ AAVA-130: Lightweight config validation endpoint (syntax + basic schema/footguns)
- ✅ Restart orchestration improvements (restart vs recreate, readiness verification via `/ready`)
- ✅ ARI scheme/port alignment across wizard → `.env` → engine runtime

**Additional Tool Categories**:

- ✅ Queue management tools - Transfer to queue (AAVA-63) - IMPLEMENTED
- ✅ Voicemail tools - Leave voicemail - IMPLEMENTED
- 🔄 Calendar appointment tool - Book/check availability (AAVA-66)
- ⏳ Voicemail retrieval - Retrieve messages
- ⏳ Conference bridge tools (create, manage participants)
- ⏳ SMS/MMS tools (send text messages to caller)

**Additional Providers**:

- ✅ Google Gemini - google_live provider with Gemini 2.5 Flash - IMPLEMENTED
- ✅ ElevenLabs - elevenlabs_agent provider - IMPLEMENTED (v4.4.1)
- ✅ Google Cloud Speech - google_stt/google_tts adapters - IMPLEMENTED (src/pipelines/google.py)
- ✅ ElevenLabs TTS adapter - elevenlabs_tts for pipelines - IMPLEMENTED (AAVA-114)
- 🚧 Next (Planned): Azure + Claude, after regression hardening
  - **Azure Speech Services (STT/TTS)**: implement as modular adapters first (pipelines) with streaming support and codec/profile alignment via `TransportOrchestrator`.
  - **Anthropic Claude (LLM)**: add as a pipeline LLM adapter with tool calling parity and consistent error handling.
  - **Prerequisite**: expand provider/pipeline contract tests so merges to `staging`/`main` have reliable regression signal without requiring live Asterisk calls.

**Advanced Features**:

- WebRTC SIP client integration for browser-based calls
- High availability / clustering for multi-server deployments
- Real-time dashboard for active call monitoring
- Call recording with consent management

**Performance**:

- GPU acceleration for local-ai-server
- Streaming latency optimizations (<500ms target)
- Memory usage profiling and optimization
- Parallel tool execution

**Documentation**:

- Video tutorials for setup and configuration
- Architecture deep-dives with diagrams
- Case studies from production deployments
- Tool calling best practices guide

---

## Release History

### v4.1.0 (November 2025) - Tool Calling & Agent Actions

**Release Date**: November 10, 2025  
**Focus**: Unified tool calling architecture enabling AI agents to perform real-world actions

**Tool Calling System**:

- **Unified Architecture**: Write tools once, use with any provider (Deepgram, OpenAI, custom pipelines)
- **Provider Adapters**: Automatic translation between provider formats (202 lines Deepgram, 215 lines OpenAI)
- **Base Framework**: `Tool`, `ToolDefinition`, `ToolRegistry` classes (537 lines total)
- **Execution Context**: Session-aware context with ARI access for real-time call control

**Telephony Tools** (5 tools shipped):

- **`transfer_call`**: Warm/blind transfers with direct SIP origination (504 lines)
  - Department name resolution ("support" → 6000)
  - Perfect bidirectional audio (eliminated Local channel issues)
  - <150ms execution time validated in production
- **`cancel_transfer`**: Cancel in-progress transfer before agent answers
- **`hangup_call`**: Graceful call termination with farewell message
  - Deepgram/OpenAI integration with HangupReady event
  - Prevents race conditions with farewell audio

**Business Tools** (2 tools shipped):

- **`request_transcript`**: Caller-initiated transcript delivery (475 lines)
  - Email parsing from speech ("john dot smith at gmail")
  - DNS MX validation and confirmation flow
  - Deduplication and admin BCC
- **`send_email_summary`**: Auto-send call summaries (347 lines)
  - Full conversation transcript with timestamps
  - Professional HTML formatting

**Agent CLI Tools**:

- **Binary Distribution**: Pre-built binaries for 5 platforms (Linux AMD64/ARM64, macOS Intel/Apple Silicon, Windows)
- **One-Line Installer**: `curl -sSL ... | bash` with platform auto-detection
- **GitHub Actions CI**: Automated builds and releases
- **CLI Commands (v5.0+)**: `setup`, `check`, `rca`, `update`, `version` (legacy aliases: `init`, `doctor`, `troubleshoot`)

**Conversation Tracking**:

- Real-time turn tracking in both Deepgram and OpenAI providers
- `conversation_history` field in `CallSession` model
- Enables email tools with full transcript context

**Critical Fixes**:

- **Direct SIP Origination**: Eliminated Local channels for perfect audio (AAVA-57, AAVA-58)
- **OpenAI VAD Timing**: Fixed greeting protection with `response.done` event (AAVA-62)
- **Email Tool Race Conditions**: Fixed 5 async bugs (AAVA-52)
- **Deepgram Hangup**: Added HangupReady event emission after farewell audio

**Documentation**:

- **New**: `docs/TOOL_CALLING_GUIDE.md` - Comprehensive 600+ line guide
- **Updated**: FreePBX Integration Guide, CLI Tools Guide, README, `docs/contributing/architecture-deep-dive.md`

**Production Validation**:

- Call IDs: 1762731796.4233 (Deepgram), 1762734947.4251 (OpenAI)
- Transfer execution: <150ms for 4-step cleanup sequence
- Email delivery: 100% success rate with MX validation

---

### v4.0.0 (October 2025) - Production-Ready GA Release

**Milestones Delivered**: 1-13 (September-October 2025)

**Foundation (Milestones 1-7)**:
- SessionStore-only state management
- Provider switching CLI and hot reload
- Model auto-fetch for local AI
- Conversation coordinator with metrics
- Streaming transport production readiness (AudioSocket)
- OpenAI Realtime voice agent integration
- Configurable pipelines with hot reload

**Production Readiness (Milestones 8-13)**:
- Transport stabilization (fixed AudioSocket format override bug)
- Audio gating & echo prevention (VAD-based, zero self-interruption)
- Transport orchestrator & audio profiles (provider-agnostic architecture)
- Post-call diagnostics (`agent rca` with AI-powered RCA; legacy alias: `agent troubleshoot`)
- Setup & validation tools (`agent setup/check`; advanced hidden: `agent demo`; legacy aliases: `agent init/doctor`)
- Config cleanup & migration (49% smaller configs)

**3 Golden Baselines Validated**:
- **Deepgram Voice Agent**: SNR 66.8 dB, telephony_responsive profile
- **OpenAI Realtime**: SNR 64.7 dB, zero self-interruption, natural conversation flow
- **Local Hybrid**: Vosk + OpenAI + Piper (functional with modern hardware)

**Technical Achievements**:
- Zero underflows, perfect pacing (drift ≈0%)
- Per-call context system with channel variables (`AI_PROVIDER`, `AI_AUDIO_PROFILE`, `AI_CONTEXT`)
- Both AudioSocket and ExternalMedia RTP transports validated
- New operator to first call: **<30 minutes** (vs hours previously)

---

### Pre-Release Development

- **v3.0** - Released Sep 16, 2025
- **v2.0** - Internal development (never released)
- **v1.0** - Initial concept (never released)

---

## Technical References

For detailed implementation plans and specifications:

- **docs/contributing/milestones/**: Detailed milestone implementation plans and technical specifications
- **OPENAI_REALTIME_GOLDEN_BASELINE.md**: OpenAI Realtime production configuration and validation

---

**Last Updated**: December 27, 2025  
**Roadmap Version**: 2.8 (Aligned milestone numbering with `docs/contributing/milestones/`, clarified Call History–first observability, and removed duplicate Milestone 21 section)
