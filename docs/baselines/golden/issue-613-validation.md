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
| Outbound serialization and fail-closed lifecycle tests | PASS — `tests/test_outbound_custom_vars.py`, including unrelated-write isolation |
| AudioSocket and unified predial-transfer regression tests | PASS — focused originate-variable assertions |
| Full Python suite | PASS — 2,260 passed, 6 skipped, 1 deselected |
| Admin backend suite | PASS — 543 passed |
| Release docs and committed-secret guards | PASS |
| Source compilation, shell syntax, and diff checks | PASS |

## Live call-path matrix

| Scenario | Call ID / evidence | Result |
|---|---|---|
| Local channel: variable present on `;1` and `;2` before answer | PENDING | PENDING |
| Direct PJSIP channel: originate variables present | PENDING | PENDING |
| HUMAN: Lead Context reaches Agent prompt and behavior | PENDING | PENDING |
| MACHINE: AMD and voicemail outcome unchanged | PENDING | PENDING |
| Consent accepted, denied, and timeout | PENDING | PENDING |
| AudioSocket UUID origination | PENDING | PENDING |
| Unified predial transfer metadata and cleanup | PENDING | PENDING |
| Nonempty context cannot be confirmed: no provider session starts | PENDING | PENDING |
| Post-test ARI/session/channel health | PENDING | PENDING |

## Privacy and failure evidence

- Confirm engine logs contain attempt, campaign, lead, and channel identifiers
  plus bounded byte counts, but not the `custom_vars` value.
- Confirm oversized context fails before ARI origination and records an
  actionable attempt/lead error.
- Confirm an ambiguous write response with matching read-back continues, while
  an absent or mismatched read-back fails closed.

## Compatibility and rollback

- Validate the documented ARI `variables` request body on the supported
  Asterisk deployment without using the Asterisk 20.20 bulk-variable endpoint.
- Pause campaigns before rollback, restore the prior application image, and
  verify no database downgrade is required.
