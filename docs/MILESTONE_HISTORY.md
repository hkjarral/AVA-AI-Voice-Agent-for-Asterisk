# Milestone History

Archive of completed development milestones for the Asterisk AI Voice Agent. For the forward-looking roadmap, see [ROADMAP.md](ROADMAP.md). For detailed release notes, see [CHANGELOG.md](../CHANGELOG.md).

---

## Completed Milestones

| # | Milestone | Completed | Summary |
|---|-----------|-----------|---------|
| 1 | SessionStore-Only State | Sep 2025 | Replaced legacy dictionaries with centralized `SessionStore` / `PlaybackManager` for all call state |
| 2 | Provider Switch CLI | Sep 2025 | One-command provider switching via `scripts/switch_provider.py` and Makefile targets |
| 3 | Model Auto-Fetch | Sep 2025 | Automatic local model download/caching via `scripts/model_setup.sh` with hardware detection |
| 4 | Conversation Coordinator & Metrics | Sep 2025 | Centralized gating/barge-in decisions with Prometheus gauges and `/metrics` endpoint |
| 5 | [Streaming Transport Production Readiness](contributing/milestones/milestone-5-streaming-transport.md) | Sep 2025 | AudioSocket streaming promoted to production with adaptive pacing and configurable defaults |
| 6 | [OpenAI Realtime Voice Agent](contributing/milestones/milestone-6-openai-realtime.md) | Sep 2025 | OpenAI Realtime provider with streaming audio, 24kHz PCM16, and server-side VAD |
| 7 | [Configurable Pipelines & Hot Reload](contributing/milestones/milestone-7-configurable-pipelines.md) | Sep 2025 | YAML-defined pipelines (STT/LLM/TTS) with hot reload and `active_pipeline` switching |
| 8 | [Transport Stabilization](contributing/milestones/milestone-8-transport-stabilization.md) | Oct 2025 | Eliminated audio garble via AudioSocket invariants; SNR 64-68 dB validated |
| 9 | Audio Gating & Echo Prevention | Oct 2025 | VAD-based Audio Gating Manager; zero self-interruption with OpenAI Realtime |
| 10 | Transport Orchestrator & Audio Profiles | Oct 2025 | Provider-agnostic `TransportOrchestrator` with per-call audio profile selection |
| 11 | Post-Call Diagnostics | Oct 2025 | `agent rca` CLI command with AI-powered diagnosis and golden baseline comparison |
| 12 | [Setup & Validation Tools](contributing/milestones/milestone-12-setup-validation-tools.md) | Oct 2025 | `agent setup`, `agent check`, `agent update` — new operator to first call in <30 minutes |
| 13 | [Config Cleanup & Migration](contributing/milestones/milestone-13-config-cleanup-migration.md) | Oct 2025 | 49% smaller configs; diagnostic settings moved to environment variables |
| 14 | [Monitoring, Feedback & Guided Setup](contributing/milestones/milestone-14-monitoring-stack.md) | Dec 2025 | Call History-first debugging model, low-cardinality `/metrics`, BYO Prometheus (bundled stack removed) |
| 15 | [Groq Speech STT/TTS](contributing/milestones/milestone-15-groq-speech-pipelines.md) | Jan 2026 | Cloud-only Groq pipeline (STT+LLM+TTS) for modular pipelines |
| 16 | [Tool Calling System](contributing/milestones/milestone-16-tool-calling-system.md) | Nov 2025 | Unified provider-agnostic tool framework: transfers, hangup, email, voicemail (2,500 lines, 111 tests) |
| 17 | [Google Live Provider](contributing/milestones/milestone-17-google-live.md) | Nov 2025 | Gemini Live as a full-agent provider with <1 second latency |
| 18 | [Hybrid Pipelines Tool Implementation](contributing/milestones/milestone-18-hybrid-pipelines-tool-implementation.md) | Nov 2025 | Tool execution for modular pipelines — feature parity with full-agent providers |
| 19 | [Admin UI Implementation](contributing/milestones/milestone-19-admin-ui-implementation.md) | Dec 2025 | Production-ready Admin UI: setup wizard, dashboard, config editor, live logs |
| 20 | [ElevenLabs Provider](contributing/milestones/milestone-20-elevenlabs.md) | Dec 2025 | ElevenLabs Conversational AI with premium voice quality and tool calling |
| 21 | [Call History & Analytics](contributing/milestones/milestone-21-call-history.md) | Dec 2025 | Persistent call records with transcripts, debugging, and export |
| 22 | [Outbound Campaign Dialer](contributing/milestones/milestone-22-outbound-campaign-dialer.md) | Jan 2026 | Alpha — scheduled outbound campaigns, AMD, voicemail drop, consent gate, Admin UI Call Scheduling |
| 23 | [NAT/Advertise Host](contributing/milestones/milestone-23-nat-advertise-host.md) | Feb 2026 | Separate bind vs advertise host for NAT/VPN/hybrid cloud deployments |
| 24 | [Phase Tools & Tool Enhancements](contributing/milestones/milestone-24-tools-enhancements.md) | Feb 2026 | Pre-call HTTP lookups, in-call HTTP tools, post-call webhooks, extension status checking |

---

## Version History

| Version | Date | Highlights |
|---------|------|------------|
| v6.1.1 | Feb 2026 | Operator config overrides, live agent transfer, ViciDial compatibility, Admin UI Asterisk audit |
| v6.0.0 | Feb 2026 | OpenAI Realtime GA API, email system overhaul, NAT/GPU support, Google Live improvements |
| v5.3.1 | Jan 2026 | Phase tools (pre/in/post-call HTTP), extension status checking, Deepgram language config |
| v5.0.0 | Jan 2026 | Outbound Campaign Dialer (Alpha), Groq Speech, Ollama improvements, attended transfer |
| v4.5.3 | Dec 2025 | Security hardening, RTP safety, ExternalMedia endpoint pinning |
| v4.4.1 | Nov 2025 | Admin UI v1.0, ElevenLabs provider, background music |
| v4.3.0 | Nov 2025 | Pipeline tool execution, AudioSocket + pipeline validation |
| v4.1.0 | Nov 2025 | Tool calling system, agent CLI tools |
| v4.0.0 | Oct 2025 | Production-ready GA release (Milestones 1-13) |

For exhaustive release notes, see [CHANGELOG.md](../CHANGELOG.md).

---

**See also**: [Detailed milestone specs](contributing/milestones/) | [Current Roadmap](ROADMAP.md)
