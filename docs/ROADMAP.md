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

### v6.5.0 — Local AI Performance & Polish

| Feature | Description | Key Files | Effort |
|---------|-------------|-----------|--------|
| **Local LLM Token Streaming (WebSocket)** | Server emits `llm_token` messages for pipeline `local_llm` adapter. Currently `_handle_llm_request()` in `local_ai_server/server.py` ignores `stream: true` and returns one `llm_response`. Wiring `process_llm_chat_streaming()` into the WS handler + setting `supports_streaming = True` on `LocalLLMAdapter` would give pipeline-mode users the same sentence-by-sentence overlap that full-mode already has. | `local_ai_server/server.py:5498` (WS handler), `src/pipelines/local.py:979` (adapter) | Medium (3-4h) |
| **Concurrent LLM+TTS Producer/Consumer** | In `_process_full_pipeline_streaming()` (`server.py:5067`), `await self.process_tts()` blocks the token loop ~200-800ms per sentence. Refactor into two `asyncio.create_task` — producer consumes tokens and pushes sentences to a queue, consumer synthesizes and emits. Needs backpressure and `_llm_lock` coordination. Marginal gain on CPU but significant with faster LLMs (GPU/remote). | `local_ai_server/server.py:5067-5187` | Medium (3-4h) |
| **Speculative LLM on Stable Partials** | Start LLM inference speculatively when STT partial transcript is stable >300ms with 5+ words. If final matches → use cached result (saves 300-1500ms). If not → discard and run fresh. Config-stubbed (`speculative_llm_enabled` etc. in `local_ai_server/config.py:154-157`). Requires `_llm_lock` coordination and session state for speculative results. Only benefits streaming STT backends (Vosk, Sherpa, Kroko) — not Whisper. | `local_ai_server/config.py:154`, `local_ai_server/server.py` (new), `local_ai_server/session.py` (new fields) | High (6-8h) |
| **Comfort Noise Injection** | Replace digital silence with low-level telephony comfort noise (~-40dB) during processing gaps (between STT final and first TTS audio). Pre-generate 1 second of µ-law noise at startup, inject into `StreamingPlaybackManager` when buffer is empty. Config-stubbed (`comfort_noise_enabled` in `local_ai_server/config.py:166`). Cosmetic improvement — filler audio already addresses the biggest UX gap. | `src/core/streaming_playback_manager.py`, `local_ai_server/config.py:166` | Low (2h) |

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

Great for first-time contributors. **AVA helps you with all of these** — just open Windsurf and describe what you want to do. See the [Operator Contributor Guide](contributing/OPERATOR_CONTRIBUTOR_GUIDE.md) to get started.

#### No-Code Tasks (Just Writing/Sharing)

| Task | Skills Needed | Why YOU Can Do This |
|------|---------------|---------------------|
| Write a "How I Deploy AAVA" case study | Just writing | Share your real deployment story |
| Document your FreePBX dialplan setup | Just writing | Copy your working dialplan + explain it |
| Add your `ai-agent.yaml` as an example config | Just YAML | Copy your working config |
| Report and document edge cases in call flows | Testing + writing | You make real calls every day |
| Translate a setup guide to your language | Any language | Help non-English speakers |

#### AI-Assisted Code Tasks (AVA Writes the Code)

| Task | Contribution Area | Why YOU Can Do This |
|------|-------------------|---------------------|
| Add a new STT/TTS/LLM pipeline adapter | [Modular Providers](contributing/adding-pipeline-adapter.md) | You know which providers work best — AVA writes the adapter |
| Add a pre-call CRM lookup hook | [Pre-Call Hooks](contributing/pre-call-hooks-development.md) | You have a CRM — AVA integrates it |
| Add a post-call webhook (Slack, Discord, n8n) | [Post-Call Hooks](contributing/post-call-hooks-development.md) | You use these tools daily — AVA connects them |
| Add an in-call appointment checker | [In-Call Hooks](contributing/in-call-hooks-development.md) | You book appointments by phone — AVA builds it |
| Test coverage for `src/tools/telephony/` | Python, pytest | You understand voicemail — AVA writes the tests |
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

**Last Updated**: April 2026 | **Current Version**: v6.4.2
