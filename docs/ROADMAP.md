# Roadmap

## Vision

Asterisk AI Voice Agent (AAVA) aims to be the definitive open-source AI voice agent platform for Asterisk/FreePBX. We're building toward a world where any organization can deploy intelligent, natural voice agents on their existing phone infrastructure — with full control over privacy, cost, and provider choice.

---

## What's Next

Active and upcoming work. Pick something up and [get involved](#how-to-contribute-to-the-roadmap)!

### Active Milestones

| # | Milestone | Status | Skills | Difficulty | Details |
|---|-----------|--------|--------|------------|---------|
| 22 | Outbound Campaign Dialer | Alpha (hardening) | Python, ARI, React | Advanced | [Spec](contributing/milestones/milestone-22-outbound-campaign-dialer.md) |

Outbound dialer shipped as Alpha in v5.0.0 — core scheduling, AMD, voicemail drop, consent gate, and Admin UI are working. Current focus: DNC, retry automation, outcome classification, and resilience hardening (see Phases 6-8 in spec).

### Completed Milestones (Recent)

| Milestone | Version | Details |
|-----------|---------|---------|
| Microsoft Calendar V1 | ✅ v6.4.2 | Outlook / Microsoft 365 calendar integration via device-code OAuth, Graph free/busy, per-context account binding, Tools UI Connect/Verify/Disconnect |
| Google Calendar — major overhaul | ✅ v6.4.2 | Multi-account / per-context binding (#338), JSON upload + auto-discover, Domain-Wide Delegation support, Tools UI Verify with distinct error codes, native free/busy mode |
| Reschedule reliability | ✅ v6.4.2 | Server-side `event_id` resolution + 400/404 fallback eliminates LLM-id-hallucination duplicate bookings; validated across Google Live, Deepgram, OpenAI Realtime, ElevenLabs |
| Date/time prompt placeholders | ✅ v6.4.2 | `{today}`, `{current_date}`, `{current_weekday}`, `{current_time}`, `{current_datetime_iso}` injected per-call so models stop reasoning with stale years |
| Google Live 30-voice catalog | ✅ v6.4.2 | Voice picker expanded from 8 hardcoded voices to full 30-voice catalog with Google's official tone descriptors (#349) |
| CPU Latency Optimization | ✅ v6.4.1 | Streaming LLM→TTS overlap, pipeline filler audio, Qwen 2.5-1.5B CPU LLM, preflight hardening |
| Matcha-TTS Backend | ✅ v6.4.1 | Matcha-TTS with audioop conversion, model catalog, vocoder auto-detection |
| Modular Provider Subtypes | ✅ v6.4.1 | UI for adding custom LLM/STT/TTS providers as pipeline components |
| Azure Speech STT/TTS Adapters | ✅ v6.3.2 | `src/pipelines/azure.py` — Fast REST, Realtime WebSocket, SSML TTS |
| MiniMax LLM Adapter | ✅ v6.3.2 | M2.7 models via OpenAI-compatible API with tool-calling |
| Call Recording Playback | ✅ v6.3.2 | Play back Asterisk recordings in Call Details modal |
| Attended Transfer Streaming & Screening | ✅ v6.4.0 | Three screening modes (basic_tts, ai_briefing, caller_recording), RTP streaming delivery, provider-agnostic tool guidance |
| Russian Speech Backends | ✅ v6.4.0 | Sherpa offline STT (VAD-gated), T-one STT (Russian CTC), Silero TTS (multi-language) |
| HTTP Tool Wildcard Extraction | ✅ v6.4.0 | JSONPath `[*]` array extraction in output variables |
| Conversation Timestamps | ✅ v6.4.0 | Per-message timestamps in conversation history + Call Log UI |
| Fullscreen UI Panels | ✅ v6.4.0 | Maximize/minimize toggle for dashboard panels |

### v6.5.0 — Local LLM Tool-Gated Response, Deepgram Flux, Gemini 3.1 (Shipped May 2026)

| Feature | Description | Status |
|---------|-------------|--------|
| **Local LLM tool_context / tool_result protocol (#368)** | New v2 WebSocket message types so the local LLM waits for tool execution and resumes with the result injected into context. Cross-call ACL leakage guarded; both `tool_context` and system-prompt sync fail-closed. | ✅ Shipped |
| **Deepgram Flux v2 + nova-3 default** | `flux-general-en` / `flux-general-multi` correctly emit `version: "v2"` plus `eot_threshold` / `eager_eot_threshold` / `keyterms`. Pydantic enforces ranges. Admin UI surfaces a "Flux Turn-Detection Tuning" panel when a `flux-*` model is selected. Default listen model flipped to `nova-3` to align YAML with pre-v6.5.0 hardcoded runtime behavior. | ✅ Shipped |
| **Gemini 3.1 Flash Live verified compatible (#350, #356)** | Multi-part `serverContent` envelopes handled correctly by the existing parser; pinned by 9 unit tests. No engine changes required. | ✅ Shipped |
| **#351 Google Live barge-in** | Resolved as a documentation issue — production answer is `use_vertex_ai: true`. Architectural silence-gating refactor deferred to v6.6 (the experiment in `1763a441` was reverted in `cead273a` because the AudioSocket forwarding path needs a broader audio-path overhaul). | ✅ Documented |
| **#370 HTTP-tool-test `.env`-first guard** | Admin UI Environment-page edits to `AAVA_HTTP_TOOL_TEST_*` take effect without an `ai_engine` restart. | ✅ Shipped |

### v6.5.4 — OpenAI Realtime GA cleanup across code/UI/docs (Shipped May 2026)

| Feature | Description | Status |
|---------|-------------|--------|
| **GA defaults across all code paths** | Follow-up to v6.5.3 hotfix. Pydantic defaults (`src/config.py`), Admin UI provider template (`ProvidersPage.tsx`), wizard backend, golden config, and example YAML all flipped to `api_version: ga` + `model: gpt-realtime`. Provider form (`OpenAIRealtimeProviderForm.tsx`) refactored to lift catalogs to module-scope constants and expose 3 new GA models (`gpt-realtime-1.5`, `gpt-realtime-2`, `gpt-realtime-mini`) verified against OpenAI's official model pages. | ✅ Shipped |
| **Diagnostic legacy-value handling** | Operators with sunset preview models pinned in their `ai-agent.local.yaml` now see a yellow warning banner above the form + their value rendered in a "Custom (legacy — will not connect)" optgroup. No auto-rewrite (too invasive); just makes broken state visible. | ✅ Shipped |
| **One-shot beta deprecation warning** | New `_warn_if_beta_deprecated()` helper in `src/providers/openai_realtime.py`, called from both `start_session` and the reconnect path, gated by `_beta_warned` to fire exactly once per provider lifetime when `api_version: beta` is configured. | ✅ Shipped |
| **Provider-OpenAI-Setup.md rewrite** | Model section rewritten with all four current GA options, use-case guidance, and the org-access caveat (Realtime API is a separate provisioning flag — even valid keys can return `model_not_found`). | ✅ Shipped |

### v6.5.3 — OpenAI Realtime GA migration hotfix (Shipped May 2026)

| Feature | Description | Status |
|---------|-------------|--------|
| **OpenAI Realtime GA defaults** | Flipped shipped `config/ai-agent.yaml` from `api_version: beta` + `gpt-4o-realtime-preview-2024-12-17` to `api_version: ga` + `gpt-realtime`. Required because OpenAI sunset the Realtime Beta API on 2026-05-12 and removed the preview model on 2026-05-07; v6.5.2 and earlier deployments hit `beta_api_shape_disabled` on the first session.update. No code change — the provider's GA wire-protocol path has shipped since v6.0.0. Setup: [docs/MIGRATION.md](MIGRATION.md). | ✅ Shipped |

### v6.5.2 — xAI Grok provider, multi-instance full-agent providers, Admin UI polish (Shipped May 2026)

| Feature | Description | Status |
|---------|-------------|--------|
| **xAI Grok Voice Agent realtime provider (PR #394)** | Fifth full-agent realtime provider, structurally parallel to OpenAI Realtime and Google Live. μ-law @ 8 kHz both directions (xAI accepts `audio/pcmu` natively, no resampling), five named voices + custom voice ID, custom function-tools identical to OpenAI Realtime, YAML escape hatch for xAI-native tools (`web_search`, `x_search`, `file_search`, `mcp`). 30-min hard session cap per xAI's docs; structured warning at 28 min. Multi-instance from day one. Setup: [docs/Provider-Grok-Setup.md](Provider-Grok-Setup.md). | ✅ Shipped |
| **Multi-instance full-agent providers** | Multiple instances of the same full-agent provider type with isolated credentials (e.g. `acme_google_live` + `globex_google_live`). Provider instance keys are immutable call-routing identities; YAML `type` selects the kind. Per-instance credentials at `/app/project/secrets/providers/<provider_key>/`. Routing via `AI_PROVIDER`, `contexts.<name>.provider`, or DID-based dispatch. Setup: [docs/Multi-Instance-Full-Agent-Providers.md](Multi-Instance-Full-Agent-Providers.md). | ✅ Shipped |
| **Uniform per-instance credentials UX (PR #395)** | Shared `ProviderCredentialsCard` paste-style uploader wired into Grok, OpenAI Realtime, Deepgram, Google Live, and ElevenLabs Agent forms. EnvPage adds a "Per-Instance Provider Credentials" status section so operators can audit credentials without SSH. | ✅ Shipped |
| **Dashboard System Topology overhaul** | Tri-state per-component health (`null` / `true` / `false`) with 2-strike debounce so transient probe blips don't flip dots red. Backend probe timeouts bumped (ai_engine 1.5 s → 5 s; local_ai_server 2.5 s → 5 s). Layout rebuilt as explicit CSS grid with responsive provider grid, Models 3-col grid, and Asterisk + AI Engine cards stretched to match Providers height. Provider cards grouped by type. | ✅ Shipped |
| **HelpTooltip backfill (~260 tooltips)** | Inline help across provider forms, Setup Wizard, LLM/MCP/Profiles/Models pages. New `HelpTooltip` is viewport-aware: measures the trigger via `getBoundingClientRect` and flips placement to keep the popover visible inside scrolled modals. | ✅ Shipped |
| **`.ulaw` call recording playback** | Call Details modal plays compact `.ulaw` recordings via server-side `audioop.ulaw2lin` WAV wrapping. `.WAV` uppercase, compressed WAV, and `.gsm` recordings transcode via `sox` with `AAVA_RECORDING_TRANSCODE_TIMEOUT_SEC` (default 120 s). | ✅ Shipped |

### v6.5.1 — CPU-demo profile + local provider hardening (Shipped May 2026)

| Feature | Description | Status |
|---------|-------------|--------|
| **CPU-demo profile end-to-end (PR #386)** | Faster-Whisper `tiny.en` + Piper + Qwen 0.5B wired through the Admin UI. New Device (`cpu`/`cuda`/`auto`) and Compute (`int8`/`float16`/`float32`) selectors with client-side gating that disables `float16` on CPU. New env vars `FASTER_WHISPER_DEVICE`, `FASTER_WHISPER_COMPUTE_TYPE` persisted by the admin layer. | ✅ Shipped |
| **Runtime toggles without model reload** | New WS protocol field `runtime_config` for `enable_filler_audio` and `llm_streaming_tts_overlap`. Filler-audio enable pre-synthesizes phrases via the active TTS; disable clears the cache. Control plane skips no-op values; admin layer accepts `no_change` so a toggle flip with the value already at the requested state no longer triggers a container recreate. | ✅ Shipped |
| **Local provider audio hot-path hardening** | `send_audio()` no longer awaits `_reconnect()` per audio frame on disconnect (was blocking the producer for ~157s of backoff); drops the chunk and kicks `_start_background_reconnect()` instead, gated on prior-connection state. `asyncio.Lock` around `_reconnect()` for single-flight against `_send_loop`'s direct on-`ConnectionClosed` path. STT fragment suppression in full/llm modes narrowed to filler only so common confirmations like `"ok"`, names, numbers reach the LLM. | ✅ Shipped |
| **Faster-Whisper verify-path tolerance** | Admin verify path no longer rolls back working CPU/int8 fallback configurations as "verification failed" when `local_ai_server` resets device/compute on CUDA model init failure. Frontend CUDA gate also reads pending dropdown selection so picking CUDA on a CPU-only host is caught client-side. | ✅ Shipped |

### v6.6.0 — Deferred from v6.5 + Local AI Performance & Polish

| Feature | Description | Key Files | Effort |
|---------|-------------|-----------|--------|
| **#351 silence-gating refactor (vad_mode-aware)** | The reverted experiment surfaced AudioSocket-path coupling that needs a broader audio-path overhaul + integration tests before silence-gating can honor `vad_mode`. | `src/providers/google_live.py`, AudioSocket forwarding path | High |
| **Local LLM Token Streaming (WebSocket)** | Server emits `llm_token` messages for pipeline `local_llm` adapter. Currently `_handle_llm_request()` in `local_ai_server/server.py` ignores `stream: true` and returns one `llm_response`. Wiring `process_llm_chat_streaming()` into the WS handler + setting `supports_streaming = True` on `LocalLLMAdapter` would give pipeline-mode users the same sentence-by-sentence overlap that full-mode already has. | `local_ai_server/server.py:5498` (WS handler), `src/pipelines/local.py:979` (adapter) | Medium (3-4h) |
| **Concurrent LLM+TTS Producer/Consumer** | In `_process_full_pipeline_streaming()` (`server.py:5067`), `await self.process_tts()` blocks the token loop ~200-800ms per sentence. Refactor into two `asyncio.create_task` — producer consumes tokens and pushes sentences to a queue, consumer synthesizes and emits. Needs backpressure and `_llm_lock` coordination. Marginal gain on CPU but significant with faster LLMs (GPU/remote). | `local_ai_server/server.py:5067-5187` | Medium (3-4h) |
| **Speculative LLM on Stable Partials** | Start LLM inference speculatively when STT partial transcript is stable >300ms with 5+ words. If final matches → use cached result (saves 300-1500ms). If not → discard and run fresh. Config-stubbed (`speculative_llm_enabled` etc. in `local_ai_server/config.py:154-157`). Requires `_llm_lock` coordination and session state for speculative results. Only benefits streaming STT backends (Vosk, Sherpa, Kroko) — not Whisper. | `local_ai_server/config.py:154`, `local_ai_server/server.py` (new), `local_ai_server/session.py` (new fields) | High (6-8h) |
| **Comfort Noise Injection** | Replace digital silence with low-level telephony comfort noise (~-40dB) during processing gaps (between STT final and first TTS audio). Pre-generate 1 second of µ-law noise at startup, inject into `StreamingPlaybackManager` when buffer is empty. Config-stubbed (`comfort_noise_enabled` in `local_ai_server/config.py:166`). Cosmetic improvement — filler audio already addresses the biggest UX gap. | `src/core/streaming_playback_manager.py`, `local_ai_server/config.py:166` | Low (2h) |
| **Local-LLM `tool_result` edge-case test suite** | Multiple tool results in flight for one call, reconnect during pending tool result, interaction with `farewell_mode=asterisk`. Protocol surface is now documented; tests are the next-cycle add. | `tests/test_local_ai_server_protocol_schema.py` (extend) | Medium |

### Planned Milestones

| Milestone | Status | Skills | Difficulty | Details |
|-----------|--------|--------|------------|---------|
| Anthropic Claude LLM Adapter | Planned | Python, Anthropic API | Intermediate | Pipeline adapter following OpenAI Chat pattern |
| SMS/MMS Notification Tool | Planned | Python, Twilio | Intermediate | Business tool following `src/tools/business/` pattern |
| Conference Bridge Tools | Planned | Python, ARI | Advanced | Create/manage multi-party calls via ARI |
| Calendar Appointment Tool | Planned | Python | Intermediate | Book/check appointment availability |
| Voicemail Retrieval Tool | Planned | Python, ARI | Intermediate | Retrieve and play voicemail messages |
| Hi-Fi Audio & Resampling | Planned | Python, Audio | Advanced | Higher-quality resamplers (speexdsp/soxr) |

### Good First Issues (Beginner-Friendly)

Great for first-time contributors. **AVA helps you with all of these** — just open Windsurf and describe what you want to do. Browse the live list of open beginner-friendly issues on GitHub: [`good first issue` issues](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues?q=is%3Aopen+is%3Aissue+label%3A%22good+first+issue%22).

#### No-Code Tasks (Just Writing/Sharing)

| Task | Skills Needed | Why YOU Can Do This |
|------|---------------|---------------------|
| Write a "How I Deploy AAVA" case study | Just writing | Share your real deployment story |
| Document your FreePBX dialplan setup | Just writing | Copy your working dialplan + explain it |
| Add your `ai-agent.yaml` as an example config | Just YAML | Copy your working config |
| Report and document edge cases in call flows | Testing + writing | You make real calls every day |
| Translate a setup guide to your language | Any language | Help non-English speakers |

#### AI-Assisted Code Tasks (AVA Helps You Write the Code)

| Task | Contribution Area | Why YOU Can Do This |
|------|-------------------|---------------------|
| Add a new STT/TTS/LLM pipeline adapter | [open issues: pipeline adapter](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues?q=is%3Aopen+is%3Aissue+pipeline+adapter) — see also `docs/contributing/pipeline-development.md` | You know which providers work best — AVA helps you write the adapter |
| Add a pre-call CRM lookup hook | [open issues: pre-call hooks](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues?q=is%3Aopen+is%3Aissue+pre-call) — see also `docs/contributing/tool-development.md` | You have a CRM — AVA integrates it |
| Add a post-call webhook (Slack, Discord, n8n) | [open issues: post-call hooks](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues?q=is%3Aopen+is%3Aissue+post-call) — see also `docs/TOOL_CALLING_GUIDE.md` (HTTP tools) | You use these tools daily — AVA connects them |
| Add an in-call appointment checker | [open issues: in-call hooks](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues?q=is%3Aopen+is%3Aissue+in-call) — see also `docs/Google-calendar-tool.md` | You book appointments by phone — AVA builds it |
| Test coverage for `src/tools/telephony/` | Python, pytest | You understand voicemail — AVA helps you write the tests |
| Improve error messages in `agent doctor` | Go CLI | You've seen the confusing errors — AVA fixes them |
| Admin UI accessibility audit (Lighthouse/axe) | React, CSS | Run the audit, AVA fixes what it finds |
| JSON Schema for `ai-agent.yaml` | JSON Schema, YAML | Define what's valid in the config you use daily |

---

## Future Vision

Longer-term goals that will shape the project's direction:

- **WebRTC Browser Client** — SIP client for browser-based calls without a physical phone
- **High Availability / Clustering** — Multi-instance `ai_engine` with session affinity and failover
- **Call Recording** — Consent-managed audio recording with storage backends (playback shipped in v6.3.2)
- **Multi-Language / i18n** — Dynamic language detection and provider switching per call (Russian backends shipped in v6.4.0)
- **Real-Time Dashboard** — Live visualization of active calls with metrics
- **Voice Biometrics** — Voice-based authentication for sensitive operations
- **Streaming Latency <500ms** — Performance optimizations for sub-500ms end-to-end latency

---

## How to Contribute to the Roadmap

### Pick up existing work

1. Browse the [Planned Milestones](#planned-milestones) or [Good First Issues](#good-first-issues-beginner-friendly) above
2. Check [GitHub Issues](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues) filtered by `help wanted` or `good first issue`
3. Comment on the issue to claim it, or ask in [Discord](https://discord.gg/ysg8fphxUe)

### Propose something new

1. Open a [GitHub Discussion](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/discussions) in the "Ideas" category
2. If accepted, create a milestone spec using the [template](contributing/milestones/TEMPLATE.md) and submit as a Draft PR
3. See [GOVERNANCE.md](../GOVERNANCE.md) for the full feature proposal process

---

## References

- **[Milestone History](MILESTONE_HISTORY.md)** — Completed milestones 1-24
- **[CHANGELOG.md](../CHANGELOG.md)** — Detailed release notes
- **[Milestone Specs](contributing/milestones/)** — Technical specifications for each milestone
- **[Contributing Guide](../CONTRIBUTING.md)** — How to contribute code

---

**Last Updated**: June 2026 | **Current Version**: v7.1.1
