# Outbound Calling (Alpha) — Outbound Campaign Dialer

Outbound calling is available as an **alpha feature** in **v5.1.4**.

This feature adds a simple, AI-native outbound dialer inspired by Vicidial-style campaigns, but designed to stay aligned with AAVA’s **ARI-first** architecture and Admin UI model.

## What You Get (v1)

- **Campaign scheduler** (campaign timezone + daily window)
- **Lead list import via CSV** (safe default: `skip_existing`)
- **Pacing + concurrency** (validated for GA at `max_concurrent=1`; higher values allowed but not validated for GA yet)
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
- `AAVA_MEDIA_DIR` (default `/mnt/asterisk_media/ai-generated`)

## Setup Steps (FreePBX-friendly)

1. Update to AAVA `v5.1.4` (or `main`) and start `admin_ui` + `ai_engine`.
2. In Admin UI, open **Call Scheduling** and create a campaign.
3. Configure (optional):
   - Consent gate
   - Voicemail drop
   - AMD tuning
4. Open the campaign **Setup Guide** tab and copy the generated dialplan snippet into:
   - `/etc/asterisk/extensions_custom.conf`
5. Reload dialplan:
   - `asterisk -rx "dialplan reload"`
6. Import leads via CSV and click **Start**.

## Testing Checklist (New User)

Use a local extension (e.g., `2765`) and an external number (E.164) to validate:

- Consent enabled: press `1` to accept → AI connects; press `2` → call ends; no input → `consent_timeout`.
- Voicemail enabled: let it ring out or go to voicemail → voicemail drop plays; attempt outcome recorded.
- HUMAN path: correct context/provider chosen; tools (e.g., `hangup_call`) work.

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
