# Microsoft Teams implementation verification

Status: local implementation verified; production deployment and live acceptance
are pending. No invitation or test calendar mutation was performed during local
preparation. Do not treat capability discovery or mocked tests as proof of a
working join link, delivered invitation, or spoken call behavior.

## Scope

- Opt-in `teams_enabled`, preserving existing appointment defaults and the
  explicitly selected calendar.
- Calendar-backed Graph events, real join-link extraction, shared provider
  sanitization, protected HTML/body updates and conditional mutations.
- Confirmed cancellation/replacement, deterministic replacement generations,
  uncertain-write handling and conservative lost-ownership recovery.
- Email/purpose confirmation guidance, truthful final booking outcomes and
  silent metadata continuation through OpenAI GA/Beta responses.
- Configuration persistence, stale Verify success/error rejection, and readable
  prerequisites/upgrade guidance in the existing Microsoft Calendar editor.

## Automated and local UI evidence

All tests use synthetic data and mocked Graph HTTP. No real caller transcript
was replayed to an external model. Source baseline: `origin/main` at `b8104904`.

| Target | Result |
| --- | --- |
| Unmodified main calendar/Graph baseline | 140 passed |
| Feature engine/tool/provider/history/metadata regression selection (review fixes) | 315 passed |
| Admin config, managed-tools and Microsoft upgrade tests | 158 passed |
| Complete frontend suite | 471 passed across 66 files |
| Frontend lint/build | Passed; existing repository warning budget retained |
| Local browser fixture | Passed Teams switch, verification, settings reopen, stale-result clearing; no page errors |
| Older deployment candidate engine/tool selection (review fixes) | 217 passed, 16 main-only cases excluded |
| Older deployment candidate calendar verification | 10 passed, 6 newer identity/cache cases excluded |
| Older deployment candidate frontend selection/build | 15 passed; build passed using its existing lockfile |

The older deployment candidate is a focused source backport. It preserves its
one-active-booking guard and always-enforced booking limits. Main's implementation
retains multiple independently confirmed appointments and opt-in limit behavior.
The older-version exclusions cover newer multiple-booking/cache-identity and
opt-in-limit semantics; they are not claims of production feature parity.
Two test assertions inspect the older tracker directly rather than main's newer
tracking accessor. No implementation behavior is mocked away for that adaptation.

The candidate uses existing Microsoft dependencies and the standard library;
there is no dependency upgrade or data migration. Its current engine uses mounted
Python source; its Admin assets/backend need an image update. The planned focused
Admin layer preserves the running image's dependencies. That image layer and
actual Linux runtime imports still require deployment-stage verification.

## Failure scenarios covered

- Unsupported/missing/forbidden/read-only calendars and disabled Teams mode.
- Distinct Teams/Outlook links and filtered internal fields.
- Same-event time changes preserving generated HTML, join metadata and attendees.
- Equivalent Graph HTML serialization after PATCH or a lost response, adopting the
  observed body for later moves/cancellation while rejecting changed details,
  Microsoft content, link targets, metadata, attendees, subject and time.
- Incomplete PATCH responses verified by one read; a failed verification read
  retains the pending write. Unicode separators use HTMLParser-compatible LF
  offsets so the outgoing body replaces all old AVA details precisely.
- Changed bodies, time, attendees, providers, join URLs, moved events and recurring-event refusal.
- ETag failures, rejected writes, cancellation verification and forbidden reads
  after uncertain cancellation.
- Lost create/PATCH responses, stable retries, identical replacements and A → B → A.
- Multiple bookings on main; restart, missing ownership and wrong binding refusal.
- Pending/late Teams links without duplicate POSTs or invented readiness.
- Existing booking limits, adapter continuation, transfer/hangup and metadata results.

## Live acceptance boundary

For v7.7.0, the maintainer explicitly waived further deployment and live testing
and accepted the above existing evidence with these limits. This does not turn
mocked Graph tests into evidence of actual Teams provisioning, invitation delivery
or a working join link. See the [release matrix](baselines/golden/v7.7.0-validation-matrix.md).
The following remains the acceptance procedure for a future authorized rollout.

Obtain the authorized recipient, meeting details and rollout window. Present the
target-specific deployment manifest and rollback commands before changing the
server. Reconfirm idle PBX channels and AVA calls/sessions before any restart.

Verify actual Teams provisioning and body-container preservation on the selected
calendar, recipient delivery, a real Calendly conflict, same-call rescheduling,
confirmed cancellation/replacement, cleanup, hangup and existing post-call
integrations. Archive raw call evidence privately before analysis. Obtain explicit
authorization before performing those remote writes or deployment changes.

Limitations: call-local ownership is not durable; uncertain creation/provisioning
without a trusted body baseline requires staff for subsequent changes. No
cross-calendar conflict aggregation or delivery/attendee-acceptance tracking is
introduced. Existing saved business prompts need a focused review at rollout;
generic runtime guidance does not override a contradictory business policy.
