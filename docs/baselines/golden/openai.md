# OpenAI Realtime — Golden Baseline

Use these references for a known-good OpenAI Realtime flow:

- OpenAI Realtime Golden Baseline: `docs/case-studies/OpenAI-Realtime-Golden-Baseline.md`
- Golden Baseline Analysis: `archived/logs/remote/rca-20251026-033115/GOLDEN_BASELINE_ANALYSIS.md`

Quick checks to match the baseline:
- AudioSocket upstream active; downstream streaming with automatic file fallback
- Session.created received before session.update; 24 kHz PCM16 alignment acknowledged
- Latency P95 ≲ 2s; no self-interrupting playback; correct gating windows
