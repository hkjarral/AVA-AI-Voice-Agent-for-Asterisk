# Issue #613 Outbound `custom_vars` Validation

Validation evidence for the ARI originate-variable correction and fail-closed
outbound lead-context lifecycle. Replace every pending result with the exact
command, call ID, and observed outcome before the branch is approved for a
draft pull request.

## Candidate

- **Branch:** `codex/issue-613-outbound-custom-vars`
- **Base:** `origin/main` at `18d4e769`
- **Database migration:** none
- **Configuration migration:** none
- **Live target:** voiprnd, Asterisk 20

## Automated gates

| Gate | Result |
|---|---|
| ARI request-boundary tests | PASS — `tests/test_ari_client_originate.py` |
| Outbound serialization and fail-closed lifecycle tests | PASS — `tests/test_outbound_custom_vars.py`, including unrelated-write isolation and restart recovery |
| Lead-context log redaction tests | PASS — direct/inherited ChannelVarSet values and nested provider prompts |
| AudioSocket and unified predial-transfer regression tests | PASS — focused originate-variable assertions |
| Full Python suite | PASS — 2,268 passed, 7 skipped |
| Admin backend suite | PASS — 543 passed |
| Release docs and committed-secret guards | PASS |
| Source compilation, shell syntax, and diff checks | PASS |

## Live call-path matrix

| Scenario | Call ID / evidence | Result |
|---|---|---|
| Local channel: variable present on `;1` and `;2` before answer | Calls `1786749996.733`, `1786750047.740`; archive `rca-20260814-232559` | PASS — direct and inherited variables observed before answer |
| Direct PJSIP channel: originate variables present | Channel `1786750106.747`; archive `rca-20260814-232559` | PASS — variables observed; rejected endpoint never started AI |
| HUMAN: Lead Context reaches Agent prompt and behavior | Call `1786749996.733`; archive `rca-20260814-232559` | PASS — distinct context reached the selected Agent and AudioSocket media completed |
| MACHINE: AMD and voicemail outcome unchanged | Call `1786750047.740`; archive `rca-20260814-232559` | FAIL — live dialplan mapped `NOTSURE/TOOLONG-5000` to HUMAN; separate AMD policy/configuration finding |
| Consent accepted, denied, and timeout | PENDING | PENDING |
| AudioSocket UUID origination | Calls `1786749996.733`, `1786750047.740`; archive `rca-20260814-232559` | PASS — two accepts, media RX confirmations, and first outbound frames |
| Unified predial transfer metadata and cleanup | PENDING | PENDING |
| Nonempty context cannot be confirmed: no provider session starts | PENDING | PENDING |
| Engine restart while ringing: durable context recovery or fail-closed rejection | PENDING | PENDING |
| Post-test ARI/session/channel health | `runtime/health-after.json` in archive `rca-20260814-232559` | PASS — healthy, ARI connected, zero active calls/sessions/channels/timers |

## Privacy and failure evidence

- **RETEST REQUIRED:** candidate `e632de50` exposed the raw value through
  `ChannelVarSet` and provider Settings logs. Unit coverage now redacts direct
  and inherited variables plus the appended provider prompt block; confirm the
  replacement deployment contains only `[lead context redacted]`.
- Confirm oversized context fails before ARI origination and records an
  actionable attempt/lead error.
- Confirm an ambiguous write response with matching read-back continues, while
  an absent or mismatched read-back fails closed.

## Compatibility and rollback

- Validate the documented ARI `variables` request body on the supported
  Asterisk deployment without using the Asterisk 20.20 bulk-variable endpoint.
- Pause campaigns before rollback, restore the prior application image, and
  verify no database downgrade is required.
