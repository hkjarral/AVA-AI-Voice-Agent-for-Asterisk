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

### Planned Milestones

| Milestone | Status | Skills | Difficulty | Details |
|-----------|--------|--------|------------|---------|
| Azure Speech STT/TTS Adapters | Planned | Python, Azure SDK | Intermediate | Pipeline adapters following `src/pipelines/google.py` pattern |
| Anthropic Claude LLM Adapter | Planned | Python, Anthropic API | Intermediate | Pipeline adapter following OpenAI Chat pattern |
| SMS/MMS Notification Tool | Planned | Python, Twilio | Intermediate | Business tool following `src/tools/business/` pattern |
| Conference Bridge Tools | Planned | Python, ARI | Advanced | Create/manage multi-party calls via ARI |
| Calendar Appointment Tool | Planned | Python | Intermediate | Book/check appointment availability |
| Voicemail Retrieval Tool | Planned | Python, ARI | Intermediate | Retrieve and play voicemail messages |
| Hi-Fi Audio & Resampling | Planned | Python, Audio | Advanced | Higher-quality resamplers (speexdsp/soxr) |

### Good First Issues (Beginner-Friendly)

No Asterisk expertise needed for these — great for first-time contributors:

| Task | Skills | Label |
|------|--------|-------|
| Test coverage expansion for `src/tools/` | Python, pytest | `good first issue` |
| JSON Schema for `ai-agent.yaml` | JSON Schema, YAML | `good first issue` |
| Admin UI accessibility audit (Lighthouse/axe) | React, CSS | `good first issue` |
| CLI help text improvements | Go | `good first issue` |

---

## Future Vision

Longer-term goals that will shape the project's direction:

- **WebRTC Browser Client** — SIP client for browser-based calls without a physical phone
- **High Availability / Clustering** — Multi-instance `ai_engine` with session affinity and failover
- **Call Recording** — Consent-managed audio recording with storage backends
- **Multi-Language / i18n** — Dynamic language detection and provider switching per call
- **Real-Time Dashboard** — Live visualization of active calls with metrics
- **Voice Biometrics** — Voice-based authentication for sensitive operations
- **Streaming Latency <500ms** — Performance optimizations for sub-500ms end-to-end latency

---

## How to Contribute to the Roadmap

### Pick up existing work

1. Browse the [Planned Milestones](#planned-milestones) or [Good First Issues](#good-first-issues-beginner-friendly) above
2. Check [GitHub Issues](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues) filtered by `help wanted` or `good first issue`
3. Comment on the issue to claim it, or ask in [Discord](https://discord.gg/ysg8fphxUe)

### Propose something new

1. Open a [GitHub Discussion](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/discussions) in the "Ideas" category
2. If accepted, create a milestone spec using the [template](contributing/milestones/TEMPLATE.md) and submit as a Draft PR
3. See [GOVERNANCE.md](../GOVERNANCE.md) for the full feature proposal process

---

## References

- **[Milestone History](MILESTONE_HISTORY.md)** — Completed milestones 1-24
- **[CHANGELOG.md](../CHANGELOG.md)** — Detailed release notes
- **[Milestone Specs](contributing/milestones/)** — Technical specifications for each milestone
- **[Contributing Guide](../CONTRIBUTING.md)** — How to contribute code

---

**Last Updated**: February 2026 | **Current Version**: v6.1.1
