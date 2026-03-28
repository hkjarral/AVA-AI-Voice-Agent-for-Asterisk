# Telephony Tools Surface Audit

This document captures the first cleanup tranche for telephony tools and their UI/config surfaces.

## Current Classification

| Item | UI Surface | Runtime State | Classification | Action |
| --- | --- | --- | --- | --- |
| `tools.transfer` | surfaced | active | keep | canonical blind transfer config |
| `tools.attended_transfer.delivery_mode` | surfaced | active | keep | operator-facing, required for shared-storage-free callee streaming |
| `tools.attended_transfer.screening_mode` | surfaced | active | keep | `basic_tts`, experimental `ai_briefing`, and `caller_recording` are the supported modes |
| `tools.attended_transfer.pass_caller_info_to_context` | hidden | compat-only | deprecate | stop writing from UI, keep read-compat for one release |
| `screened_*` attended-transfer placeholders | hidden in docs/tooltips only | compat-only | deprecate | remove with `ai_summary` after compat window |
| `extensions.internal.*.pass_caller_info` | not surfaced | legacy-only | deprecate | stop creating on new rows, keep read-compat for one release |
| `tools.transfer.live_agent_destination_key` | surfaced as advanced/legacy | active | keep | revisit after telemetry window |
| `tools.check_extension_status.restrict_to_configured_extensions` | now surfaced | active | keep | safety guardrail for model-driven availability checks |
| `tools.cancel_transfer.allow_during_ring` | surfaced | active | keep | real runtime behavior |
| `tools.cancel_transfer.allow_after_answer` | not surfaced | unused | remove | dead config/doc option |
| `tools.hangup_call.fallback_media_uri` | not surfaced | advanced runtime fallback | config-only | keep unsurfaced unless product wants operator-facing fallback media |
| `tools.attended_transfer.external_media_helper.*` | not surfaced | internal runtime override | config-only | keep out of normal Tools UI; Transport owns networking |
| `transfer_call` | not surfaced | legacy tool code only | deprecate | warn on use, remove after compat window if telemetry stays quiet |
| `transfer_to_queue` | not surfaced | legacy tool code only | deprecate | warn on use, remove after compat window if telemetry stays quiet |

## Missing UI Surface Added

- `tools.check_extension_status.restrict_to_configured_extensions`

This setting materially changes runtime behavior and is now the only missing operator-facing control promoted from config-only to the Tools UI in this tranche.

## Deferred Removal Candidates

Remove after one compatibility release if telemetry and docs/prompt audits stay clean:

- `tools.attended_transfer.pass_caller_info_to_context`
- `ai_summary` attended-transfer branch
- `screened_*` attended-transfer placeholders
- `extensions.internal.*.pass_caller_info`
- `transfer_call`
- `transfer_to_queue`
