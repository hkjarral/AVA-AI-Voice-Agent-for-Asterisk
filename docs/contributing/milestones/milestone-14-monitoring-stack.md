# Milestone 14 — Monitoring, Feedback & Guided Setup (Call History–First)

Note (CLI v5.0): The public CLI surface is now `agent setup`, `agent check`, `agent rca`, and `agent version`. Legacy command names used below (`agent troubleshoot`, etc.) remain available as hidden aliases for compatibility.

This milestone is now implemented via **Call History** + **Troubleshoot** (Admin UI + `agent troubleshoot`), with Prometheus kept **optional** and **low-cardinality only**.

---

## Status (Updated)

As of `v4.5.3`, the project moved to a **Call History–first** debugging model and enforces **low-cardinality metrics** (no per-call identifiers like `call_id`/`call_uuid` in Prometheus labels).

The bundled Prometheus/Grafana stack and `monitoring/` assets are **not shipped** in the main repo path anymore. Operators who want time-series monitoring should use a bring-your-own stack and scrape `/metrics` (see `docs/MONITORING_GUIDE.md`).

This document is updated to reflect the current design and what “Monitoring & Feedback” means for AVA today.

---

## 1) Goals & Non-Goals

**Goals**

- Make production debugging fast and reliable via **Call History** (per-call) + **Troubleshoot** views (operator workflow).
- Keep metrics safe and scalable: **low-cardinality only**; never encode per-call identifiers into time-series labels.
- Make it obvious “what happened” on a call: provider/pipeline/context, transport/profile, tool usage, error paths, and key audio/runtime counters.

**Non-Goals**

- Shipping a bundled Prometheus/Grafana stack from this repo.
- Turning PSTN call automation into CI gating (too flaky and expensive for OSS defaults).

---

## 2) Operator Workflow (What Success Looks Like)

After any call (success or failure), an operator should be able to answer these questions from Admin UI within 60 seconds:

- Did the call connect and clean up correctly?
- Which provider/pipeline/context/audio profile was used?
- Did STT produce finals? Did tools run? Did TTS play?
- Was there barge-in, jitter/fallback, or codec/profile mismatch?
- If it failed: what error, where, and what remediation?

Primary surfaces:

- Admin UI → **Call History**
- Admin UI → **Troubleshoot** (logs, VAD/gating, tools, provider/pipeline status)
- CLI → `agent troubleshoot --last` / `agent troubleshoot --call <call_id>` (for server-side RCA)

---

## 3) Objectives & Success Criteria

**Per-call observability**

- Every completed call creates a Call History record with:
  - timeline/turn transcript
  - provider/pipeline/context and audio profile/transport
  - tool call events
  - outcome + error message (if any)

**Troubleshoot UX**

- Admin UI troubleshoot views make it easy to pull:
  - raw logs for a call
  - key counters (barge-ins, gating state changes, alignment checks)
  - “what to try next” guidance (operator-facing)

**Safe time-series metrics**

- `/metrics` remains low-cardinality under load (no per-call identifiers).
- Metrics complement Call History; they do not replace it.

Verification quick check: place a single call → find it in Call History → open Troubleshoot → confirm provider/pipeline/tool events are visible and the call is fully traceable end-to-end.

---

## 4) Scope: What We Build Next (Incremental)

This milestone is ongoing as an “ops UX” workstream. The highest ROI follow-ups are:

1) **Call History hardening**
   - Retention controls (days/rows) and a safe purge tool.
   - Optional transcript redaction hooks (PII-aware deployments).
   - Export/import ergonomics (support tickets, offline RCA).

2) **Troubleshoot hardening**
   - One-click “RCA bundle” export (selected logs + call record + config snapshot).
   - Clear, operator-facing failure classification (provider auth, ARI disconnect, RTP issues, tool errors).

3) **Recommendation engine (Call History–based)**
   - Rule-based suggestions derived from stored call summaries (not Prometheus labels).
   - Show recommendations in Admin UI; optionally export aggregate counts to `/metrics` with bounded labels.

---

## 5) Low-Cardinality Metrics (Optional, BYO Monitoring)

If an operator wants dashboards, the supported approach is:

- Scrape `/metrics` from `ai-engine` and (optionally) `local-ai-server`
- Build dashboards externally (Grafana or equivalent)
- Use Call History for per-call debugging (do not attempt per-call time-series labeling)

See: `docs/MONITORING_GUIDE.md`

---

## 6) Security & Data Considerations

If you store transcripts, you are storing sensitive customer data. At minimum:

- Provide retention/purge controls.
- Ensure logs and Call History avoid leaking secrets (API keys, ARI passwords).
- Document how to protect the Admin UI (reverse proxy/VPN/firewall) since it is a control plane.
