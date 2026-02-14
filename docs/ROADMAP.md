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
