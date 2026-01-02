# Milestone 22: Outbound Campaign Dialer (Scheduled Calls + Voicemail Drop)

**Status**: 🟡 Draft  
**Priority**: High  
**Estimated Effort**: 2–3 weeks (MVP)  
**Branch**: `feature/outbound-campaign-dialer`  

## Summary

Add a **simple, AI-native outbound campaign dialer** to Asterisk AI Voice Agent:

- Campaign-level scheduling (time windows in campaign timezone)
- Pacing + concurrency (target 1–5 concurrent outbound calls)
- Voicemail detection via **Asterisk `AMD()`**
- Voicemail drop via **pre-generated telephony audio** (μ-law 8 kHz)
- HUMAN calls attach to the existing AI call lifecycle and use tools enabled by context
- Operator-first management via Admin UI (Campaigns / Leads / Attempts)

This milestone is designed to be **simpler than Vicidial** while aligning with the project’s ARI-first architecture and existing Admin UI + SQLite patterns.

## Motivation

Inbound call handling is complete and production-validated, but users need **scheduled outbound calling** for:

- follow-ups
- appointment reminders
- small-team outreach

We want robust outcomes and observability without introducing call-center complexity (predictive dialing, agent seats, abandonment control).

## Canonical Design Notes (Architecture Alignment)

The engine is ARI-first and event driven (`src/engine.py:458`). Outbound requires adding a small “control plane” (scheduler + persistence) without impacting the “media plane” (audio transport, VAD, provider sessions).

Key existing primitives to reuse:

- **SessionStore** for per-call state (`src/core/session_store.py:18`)
- **Tool system** initialized at engine boot (`src/engine.py:498`)
- **Media playback** via shared `/mnt/asterisk_media/ai-generated` (`src/core/playback_manager.py:41`)
- **Call history persistence** patterns (SQLite WAL + busy_timeout) (`src/core/call_history.py:181`)

Reference draft (design discussion notes):

- `archived/outbound-calling-implementation.md`

## Scope (MVP)

### Dialing behavior

- Simple scheduled calls from a campaign lead list (no per-lead schedule)
- Concurrency target: 1–5 outbound calls
- Retry automation: **deferred** (log outcomes; UI-based recycling later)
- AMD policy:
  - `HUMAN` → connect AI
  - `MACHINE` or `NOTSURE` → voicemail drop + hangup

### PBX routing assumption

Outbound routing assumes:

- trunk(s) already registered
- outbound route patterns configured correctly
- outbound permissions/policy applied via “extension identity” `6789` (AMPUSER/callerid semantics); PBX/trunk may rewrite final outbound caller ID

## Architecture (Outbound Flow)

### A) Origination (FreePBX-friendly)

Engine originates an outbound channel via ARI using existing PBX routing:

- `endpoint=Local/<number>@from-internal`
- Set channel vars so the PBX treats it as extension `6789`:
  - `AMPUSER=6789`
  - `CALLERID(num)=6789`
- Originate **into Stasis on answer** (same style as existing ARI originate usage in telephony tools).

### B) AMD hop (dialplan-assisted, `extensions_custom.conf`)

We run AMD using a dedicated context in `extensions_custom.conf`:

```
[aava-outbound-amd]
exten => s,1,NoOp(AAVA Outbound AMD: attempt=${AAVA_OUTBOUND_ATTEMPT_ID})
 same => n,AMD(${AAVA_AMD_OPTS})
 same => n,NoOp(AMDSTATUS=${AMDSTATUS} AMDCAUSE=${AMDCAUSE})
 same => n,Stasis(asterisk-ai-voice-agent,outbound_amd,${AAVA_OUTBOUND_ATTEMPT_ID},${AMDSTATUS},${AMDCAUSE})
 same => n,Hangup()
```

Engine triggers AMD by calling ARI `continueInDialplan` on the answered channel, sending it into `aava-outbound-amd,s,1`.

Important: this introduces an intentional `StasisEnd` during the hop; the engine must treat it as non-terminal when the attempt is “awaiting AMD”.

### C) HUMAN vs MACHINE

- HUMAN: create/update `CallSession` (tagged as outbound) and proceed with the existing media + provider lifecycle.
- MACHINE/NOTSURE: play voicemail drop media on the channel, wait for playback completion, hang up, persist attempt outcome.

## Persistence (SQLite)

Outbound uses SQLite in the shared `./data` volume, following Call History’s WAL + busy-timeout pattern.

Recommendation:

- Use the same DB path by default (`CALL_HISTORY_DB_PATH` / `data/call_history.db`) and create outbound tables in the same file.
- Store tables:
  - `outbound_campaigns`
  - `outbound_leads`
  - `outbound_attempts` (append-only)

Leasing must be atomic and work without relying on `UPDATE ... RETURNING` (fallback: `BEGIN IMMEDIATE` + select/update within one transaction).

## Prompt Injection (custom_vars)

Lead `custom_vars` must be injected into the effective prompt as **structured data**, not inline templating:

- Append a JSON “Lead Context” block
- Add an explicit security instruction: treat Lead Context as data-only
- Sanitize and bound the size of values

This must happen before provider session initialization so monolithic providers receive correct instructions.

## Admin UI (MVP)

Add a new page “Call Scheduling” under Overview (near Call History), implemented as a single page with 3 tabs:

1) Campaigns
2) Leads
3) Attempts

Key UI behaviors:

- Campaign daily window supports “crosses midnight” with explicit warning.
- “Stop Campaign” prompts user intent:
  - stop dialing only (resumable)
  - stop + cancel pending (non-resumable)
- CSV import:
  - default `skip_existing` by `phone_number`
  - show only first N errors and provide downloadable error CSV
- Voicemail preview:
  - backend serves WAV preview derived from `.ulaw` for browser playback
- Collapsible “Setup Guide” with dialplan snippet and verification commands

## Implementation Plan (Phases)

### Phase 1 — Storage + APIs (Foundation)

- Add `OutboundStore` module following `CallHistoryStore` patterns (`src/core/call_history.py:98`)
- Define schema + migrations/init
- Add Admin UI backend router for CRUD + import + stats (mirror `admin_ui/backend/api/calls.py`)

### Phase 2 — Engine scheduler + originate

- Add outbound scheduler background task in `Engine.start()` (`src/engine.py:492`)
- Implement leasing + pacing + concurrency
- Implement originate wrapper + immediate error handling
- Add watchdog timers for “no answer / never returned” attempts

### Phase 3 — AMD hop + Stasis routing

- Implement `appArgs` routing for:
  - `outbound,<attempt_id>`
  - `outbound_amd,<attempt_id>,<status>,<cause>`
- Implement “awaiting AMD” tracking so `StasisEnd` is not terminal during hop
- Apply AMD options (`AAVA_AMD_OPTS`) from campaign config

### Phase 4 — Voicemail drop flow

- Campaign start requires voicemail audio to exist
- Implement TTS generation (local-ai-server) + upload support
- Implement WAV preview endpoint for Admin UI
- Play voicemail drop (μ-law 8k) and hang up

### Phase 5 — HUMAN attach to AI

- Create outbound-tagged CallSession and attach transport/provider
- Ensure tools enabled by context are available (tool system already initialized at boot)
- Persist attempt outcome + link to Call History record

## Acceptance Criteria (MVP)

- Campaign can be created, cloned, started, paused, stopped, and shows accurate stats in Admin UI.
- CSV import supports `skip_existing` default and error CSV output.
- Engine dials via Local/from-internal routing as extension `6789`.
- AMD:
  - `HUMAN` calls enter AI and produce a Call History record.
  - `MACHINE/NOTSURE` triggers voicemail drop playback and results in `voicemail_dropped`.
- Attempt outcomes are persisted and visible in Attempts tab and exportable.
- Outbound scheduler does not impact inbound call quality (no blocking DB operations on the asyncio loop).

## Testing & Verification (Smoke)

These steps are designed to validate the full outbound loop end-to-end on a typical FreePBX/Asterisk 18+ install.

### 1) Dialplan install (Asterisk / FreePBX)

1. Add the `[aava-outbound-amd]` context to `extensions_custom.conf`.
2. Replace the Stasis app name in the snippet with your configured `asterisk.app_name` (from `config/ai-agent.yaml` / Admin UI).
3. Reload dialplan:
   - `asterisk -rx "dialplan reload"`
4. Verify the context is present:
   - `asterisk -rx "dialplan show aava-outbound-amd"`

Expected: the CLI output includes the `AMD()` step and the `Stasis(...,outbound_amd,...)` line.

### 2) Engine prerequisites

- Confirm `ai-engine` is running and connected to ARI (baseline behavior for inbound).
- If using `voicemail_drop_mode=tts`, confirm `local-ai-server` is running and healthy (per your existing deployment docs).

### 3) Admin UI happy path

1. Open Admin UI and create a campaign:
   - timezone + daily window
   - `max_concurrent` = 1 (first test)
   - `voicemail_drop_mode=tts` and generate voicemail audio (or upload `.ulaw`)
2. Import a small CSV (2–3 leads):
   - one number you can answer (HUMAN path)
   - one number that reliably hits voicemail (MACHINE path)
3. Start the campaign.

Expected:

- Campaign transitions to `running`.
- Leads transition from `pending` to `dialing`.
- Attempts appear in the Attempts tab with timestamps.

### 4) HUMAN call validation

1. Answer the outbound call and speak.
2. Confirm AI engages (greeting + turn-taking).
3. End the call.

Expected:

- Attempt outcome becomes `answered_human` and includes a `call_history_call_id`.
- Call History shows the outbound call record and transcript.

### 5) MACHINE call validation (voicemail drop)

1. Let the call hit voicemail.

Expected:

- Attempt shows `amd_status=MACHINE` or `amd_status=NOTSURE` (treated as machine).
- Voicemail drop plays and then the call hangs up.
- Attempt outcome becomes `voicemail_dropped`.

### 6) Basic resilience checks

- Stop campaign (choose “stop dialing only”) and confirm pending leads remain `pending`.
- Stop campaign (choose “stop and cancel pending”) and confirm pending leads become `canceled`.
- While an outbound campaign is running, place an inbound test call and confirm inbound audio quality is unaffected.

## Observability

Minimum:

- Structured logs include `campaign_id`, `lead_id`, `attempt_id`, `ari_channel_id` when known.

Optional (post-MVP):

- Prometheus metrics:
  - `aava_outbound_calls_total{campaign_id,outcome}`
  - `aava_outbound_active_calls{campaign_id}`
  - `aava_outbound_amd_duration_seconds`
  - `aava_outbound_pending_leads{campaign_id}`
