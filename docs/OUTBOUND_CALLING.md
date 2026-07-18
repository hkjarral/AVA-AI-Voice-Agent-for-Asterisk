# Outbound Calling — Outbound Campaign Dialer

Outbound calling is available through **Admin UI → Call Scheduling**. Version 7.4.1 focuses on correctness and scheduling safety while the broader Outbound Campaigns redesign remains planned for 7.5.0.

This feature adds a simple, AI-native outbound dialer inspired by Vicidial-style campaigns, but designed to stay aligned with AAVA’s **ARI-first** architecture and Admin UI model.

## What You Get (v1)

- **Campaign scheduler** (campaign timezone + daily window)
- **Lead list import via CSV** using an active Agent slug (safe default: `skip_existing`)
- **Pacing + concurrency** (`max_concurrent` caps campaign activity; the safety ramp starts at most one new lead per scheduler tick)
- **Asterisk AMD voicemail detection** (`AMD()`)
- **Voicemail drop** (play a pre-recorded message and hang up)
- **Consent gate (optional)**: play a consent prompt and capture DTMF (`1` accept / `2` deny)
- **Recording library**: upload once, reuse across campaigns

## Key Assumptions

- Your **outbound trunk(s)** and **outbound routes** are already configured in Asterisk/FreePBX.
- AAVA originates outbound calls using your configured **outbound identity extension** (default `6789`), so FreePBX routing and caller-ID rules apply consistently.
- This is a **single-node** design.

## Architecture (High Level)

- **Control plane**: scheduler + SQLite state (stored alongside Call History DB).
- **Media plane**: once answered, the call attaches to the existing AAVA session lifecycle.
- **AMD**: the engine sends the answered channel into dialplan via ARI `continueInDialplan` to run `AMD()` and return to Stasis with an outcome.
- **NOTSURE** is treated as MACHINE by default to avoid burning AI sessions.

## Environment Variables

See `docs/Configuration-Reference.md` for the full list and semantics. The most common:

- `AAVA_OUTBOUND_EXTENSION_IDENTITY` (default `6789`)
- `AAVA_OUTBOUND_AMD_CONTEXT` (default `aava-outbound-amd`)
- `AAVA_OUTBOUND_ATTEMPT_STALE_SECONDS` (default `120`, minimum `10`; shared by startup and runtime cleanup)
- `AAVA_MEDIA_DIR` (default `/mnt/asterisk_media/ai-generated`)

## Agent Routing

- New campaigns and CSV files should select an active Agent **slug** from the Agents page.
- The downloadable sample CSV uses the preferred `agent` column. The older `context` column remains accepted as a deprecated compatibility alias.
- Outbound origination sets `AI_AGENT` as the canonical routing variable and also carries `AI_CONTEXT` through the AMD dialplan hop for v7.4 compatibility. When both are present, `AI_AGENT` wins.
- Existing database/API field names such as `default_context` and `context_override` remain unchanged in 7.4.1 to avoid a breaking schema or automation migration; their values are treated as Agent slugs in the UI and runtime.

## 7.4.1 Number and Scheduling Semantics

- For scheduled outbound HUMAN calls, both `caller_number` and `called_number` expose the lead/customer number to prompts and tools. This preserves the existing outbound `caller_number` behavior and fixes pre-call URLs such as `?phone={called_number}`.
- Invalid IANA timezones, malformed daily `HH:MM` values, malformed/reversed absolute windows, and unexpected validation errors fail closed: the campaign does not originate a call.
- Daily start and end values must use zero-padded 24-hour `HH:MM`. Equal values continue to mean a 24-hour window; cross-midnight windows remain supported.
- An active outbound HUMAN call is counted once against `max_concurrent`, even while its in-memory attempt correlation remains active.
- The scheduler intentionally leases at most one new lead per tick. `min_interval_seconds_between_calls` can further slow origination but does not increase the configured concurrency ceiling.

## Setup Steps (FreePBX-friendly)

1. Update to AAVA `v7.4.1` (or the corresponding development branch) and start `admin_ui` + `ai_engine`.
2. In Admin UI, open **Call Scheduling** and create a campaign.
3. Configure (optional):
   - Consent gate
   - Voicemail drop
   - AMD tuning
4. Open the campaign **Setup Guide** tab and copy the generated dialplan snippet into:
   - `/etc/asterisk/extensions_custom.conf`
5. Reload dialplan:
   - `asterisk -rx "dialplan reload"`
6. Import leads via CSV using the preferred `agent` column and an active Agent slug, then click **Start**.

## Testing Checklist (New User)

Use a local extension (e.g., `2765`) and an external number (E.164) to validate:

- Consent enabled: press `1` to accept → AI connects; press `2` → call ends; no input → `consent_timeout`.
- Voicemail enabled: let it ring out or go to voicemail → voicemail drop plays; attempt outcome recorded.
- HUMAN path: correct Agent/provider chosen; tools (e.g., `hangup_call`) work.

## Where to Look When Something Breaks

- Admin UI → **Call Scheduling**:
  - Lead “Last Error”, “Outcome”, “AMD”, “DTMF”, and “Call History” modal
- Engine logs:
  - `docker compose logs -f ai_engine`
- Asterisk console:
  - `asterisk -rvvvvv`
- First-line setup fixes:
  - `sudo ./preflight.sh --apply-fixes`
  - `agent check`
  - `docs/TROUBLESHOOTING_GUIDE.md`

## Reference Implementation Notes

- Full milestone design + implementation notes:
  - `docs/contributing/milestones/milestone-22-outbound-campaign-dialer.md`
