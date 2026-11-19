# Calendly booking v1 implementation plan

Status: implemented and deployed; PR #774 is in final readiness review. The supported Google Live/Google Meet booking slice and corrected farewell lifecycle have passed real-call checks. The final duplicate-release compatibility correction passed local regression tests and deployed smoke checks; final-head CI/review remain pending. No merge has been performed.

Prepared October 23, 2026 after API/schema research and repository inspection; updated October 24 with implementation, failure-path tests, setup qualification, real booking and farewell evidence. This replaces the earlier broad calendar-management vision with one operator-owned Calendly connection and in-call appointment booking. OAuth, calendar administration and a general booking-management application remain deferred.

## Implementation and acceptance checkpoint

- The complete operator flow includes protected PAT verification/save, supported appointment discovery and selection, per-agent permissions, explicit setup qualification, native availability/recap/approval/booking, Call History outcomes and bounded pending-request recovery.
- Reliability uses durable one-POST admission, exact call/connection-bound proposals, unchanged canonical recaps, duplicate/replay barriers, positive bounded reconciliation and staff fallback. A reference-only final approval can reuse only the validated active-call details; it never inherits approval flags. Exact event and invitee cancellation can release a cross-operation duplicate while retaining the original operation barrier.
- Review corrections cover async UI draft preservation, visible request references, consistent null/empty selection, malformed readback escalation, ordered/interruption-safe Google audio, and durable linked-data erasure. A missing journal must retain erasure work; disabling new Call History collection must not revoke an existing erasure obligation. Explicit empty Agent permissions survive tool disable/re-enable; setup operations are scoped to the selected appointment and result; private resolution notes share detail-erasure rules. Invalid retention configuration cannot block cleanup, and failure of cleanup, history setup or one attempt must not skip other reconciliation work. Queued erasure obligations remain until a successful scrub, with bounded processing rather than silent expiry. Recovery requests load when the setup panel opens; unreadable responses remain visible errors. Shared rate-limit cooldowns defer due selection and lease admission without consuming recovery budget. An incomplete appointment is omitted from discovery while execution continues to enforce its binding.
- The optional Google audio reserve defaults to zero in shipped configuration. The development pilot uses 2000 ms to absorb observed bursty recaps. Synthetic arrival-trace tests preserve byte order; interruption, completion, stale response and teardown tests cover the lifecycle. Partial real frames are padded and paced after the empty-filler idle cutoff, preserving terminal drain safeguards.
- Last completed local full validation: 3,705 core tests passed, three skipped, 176 CI exclusions, 59.86% coverage; 984 backend tests and 522 frontend tests passed. Frontend validation passed with two workers after an unrestricted local run hit resource-related timeouts on unrelated screens; assertions/timeouts were unchanged. Lint/build and committed-secret/diff checks passed. The full CI gate, CodeQL and image-size budgets passed before the latest review batch; final results must match the final PR head.
- Both affected images were rebuilt and services recreated behind idle/paused gates, retaining operator configuration, credentials and data. All eight providers became ready; ARI/AudioSocket connected, migrations initialized, and post-ready logs had no error/critical events. Deployed synthetic confirmation and partial-tail smoke checks performed no external booking writes. Rebuilt UI checks confirmed paused scheduling, matching request references and the inherited Agent appointment-policy default. The latest isolated deployed smoke also preserved queued erasure across an absent journal and disabled history collection, retained replay barriers, discarded stale audio suspended in latency tracking, and preserved new-response bytes. It made no real history changes or external writes. The final deployed correction batch additionally verified blank-retention handling and resolution-note erasure; startup logs were clean and the rebuilt setup UI retained paused scheduling. The final minor batch also verified cooldown-budget preservation in the deployed runtime and automatic recovery-reference loading in the rebuilt UI. Deployed erasure smoke additionally rejected late finish writes and retained an erased barrier before any admission; it changed no real history and made no external write. The compatibility smoke also proved that erasure preserves a previously released cross-call duplicate while retaining the original operation barrier.

## Live-qualified configuration and evidence

| Configuration | Evidence and limit |
| --- | --- |
| Google Live, AudioSocket, individually owned one-to-one fixed 30-minute Google Meet appointment | Live setup and native voice booking qualified. The operator confirmed the setup invitation, intended Google Calendar placement and management links. Exact/list tracking readback and conflict-slot exclusion passed. |
| Other voice providers, conference/location kinds and supported question variants | Documentation/contracts and synthetic adapter/payload/failure coverage only. No live qualification is claimed by these calls; operators must qualify their selected configuration. Conference booking remains gated on its own setup qualification. |
| Lost-response/restart/disable/reconnect/malformed-response cases | Synthetic transport, concurrency, SQLite and crash/failure-injection evidence; no deliberately ambiguous live booking was resubmitted. |

Real booking call `1792829386.882` completed a canonical Saturday/date/time/timezone/duration/Google Meet/name/spelled-recipient recap, fresh caller approval and exactly one confirmed booking for October 24, 11:00–11:30 AM Pacific. Exact event/invitee readback matched the saved host, appointment, attendee and opaque marker; conference details and management links were ready and fresh availability excluded the slot. Actual delivery of that particular invitation was not independently observed; the earlier setup test established delivery/calendar behavior. All disposable appointments are now canceled with exact cancellation proof and retained replay barriers. Booking remains paused.

Farewell retest `1792866150.886` completed a brief non-booking exchange and full farewell on the same demo path. Terminal drain completed in 2.595 seconds, including queued speech and the existing 350 ms quiet window, followed by `audio_drained=true` hangup and normal cleanup. There was no timeout. The 47.56-second recording and independent offline transcription preserve the final words without the prior long silent tail. Health returned to zero calls/sessions/channels/playbacks/timers. These checks establish the recorded farewell and cleanup; no separate handset-quality report was captured for this retest.

Earlier calls exposed missing next-availability bounds, a canceled historical duplicate, weekday guessing, bursty playback and a reference-only approval contract mismatch. Those failures drove the corrected defaults, positive cancellation checks, server-computed spoken date, reserve/tail handling and active-call confirmation binding. They are not successful booking claims. The existing demo SMS-summary endpoint's separate post-hangup authentication failure is not Calendly invitation handling.

Raw logs, recordings, full transcripts, credentials, attendee details, remote resource/capability URLs and infrastructure/rollback locations remain in private maintainer evidence. They are not repository fixtures or PR attachments. The only arrival-trace fixture contains relative timings and byte counts with synthetic audio; operational rollback evidence is retained privately.

## 1. Outcome and scope

An operator connects their existing Calendly account, selects supported appointment types, assigns them to an AVA agent, and verifies the setup. A caller can then choose a time, confirm their details, and receive a real Calendly booking and its configured invitation. Subsequent changes happen through Calendly's management links or staff.

| Included in v1 | Deferred |
| --- | --- |
| One connected Calendly user per AVA installation, using that host/admin's personal access token (PAT) | OAuth, hosted onboarding, multiple connections, organization-wide host selection |
| Guided connection, verification, token replacement, and disconnect | Linking Google/Microsoft calendars inside AVA |
| Selection of existing active, fixed-duration, one-to-one appointment types owned by the connected user | Creating/editing appointment types, working hours, notifications, reminders, or calendar destinations |
| Per-agent appointment-type permissions | Group, round-robin, collective, variable-duration, paid, and complex location flows |
| Availability and native voice booking with required caller details | General operator booking form, booking dashboard, booking import/search by caller identity |
| One confirmed or unresolved booking operation per call | Multiple appointments or replacement/cancellation/rescheduling during a call |
| Setup-only test booking through the same booking service | Native cancellation/rescheduling, returning-caller verification, invitation resend |
| Call History outcome and a small unresolved-attempt recovery panel | Webhooks, continuous booking synchronization, delivery/RSVP tracking |
| Durable submission journal, duplicate protection, bounded reconciliation, restart recovery | Exactly-once claims or blind automatic retry of booking requests |

No new service/container is required. Calendly remains the authority for availability, appointment configuration, invitation generation, and later booking changes. Google/Microsoft tools, telephony routing, DNC, callbacks, dispositions, and CRM authorization are outside this change.

## 2. API facts, assumptions, and qualification gates

References and expandable request/response schemas were checked October 23, 2026. Research used public documentation and repository source only: no Calendly credentials were read, no authenticated account was queried, and no booking or invitation was created. Documented contracts, AVA design decisions, and live acceptance checks are distinguished below.

| Fact or boundary | Design consequence | Source |
| --- | --- | --- |
| Calendly documents PAT use for a host/admin accessing their own organization; integrations acting on behalf of external end-users use OAuth | Scope setup to one operator-owned account. Do not advertise this PAT flow as hosted customer-account onboarding | [Authentication](https://developer.calendly.com/docs/authentication/overview), [AI booking guide](https://developer.calendly.com/docs/api-guides/schedule-events-with-ai-agents) |
| Direct booking is `POST /invitees`, available on Standard and higher; it supports PAT authentication | Read-only verification cannot prove paid booking eligibility. Show this prerequisite and establish booking readiness through the explicit test | [Create Event Invitee](https://developer.calendly.com/api-docs/calendly-api/scheduled-events/create-event-invitee) |
| Booking runs configured Calendly notifications and workflows | Do not add a second AVA confirmation-email subsystem | [Create Event Invitee](https://developer.calendly.com/api-docs/calendly-api/scheduled-events/create-event-invitee) |
| Availability requests cover at most 31 days and use future UTC bounds | Start with a bounded search window; never query indefinitely or calculate bookable hours independently | [Available times](https://developer.calendly.com/api-docs/calendly-api/event-types/list-event-type-available-times) |
| Event metadata includes questions, duration, owner, locations, and scheduling configuration | Classify support from actual metadata and reject unknown required configurations | [Get Event Type](https://developer.calendly.com/api-docs/calendly-api/event-types/get-event-type) |
| Paid non-Enterprise booking limits are 10 requests/minute, 50/hour, 100/day per user; trial limit is 5/day | Handle quota explicitly; other integrations share upstream per-user limits. Avoid unnecessary test bookings | [Rate limits](https://developer.calendly.com/api-docs/overview/rate-limits) |
| Cancel/reschedule links are available on invitees and included in Calendly calendar events/email confirmations; no direct rescheduling endpoint is documented | Direct callers to their confirmation; expose authenticated operator links without adding management mutations | [FAQ](https://developer.calendly.com/docs/getting-started/frequently-asked-questions) |
| The reviewed booking reference documents no idempotency-key guarantee | A local journal prevents AVA resubmission; it cannot make an ambiguous remote write safe to retry | [Create Event Invitee](https://developer.calendly.com/api-docs/calendly-api/scheduled-events/create-event-invitee) |

### 2.1 Authentication and scope decision

Use a PAT created by the connected host/admin for their own installation. Calendly's [PAT instructions](https://developer.calendly.com/docs/authentication/how-to-authenticate-with-personal-access-tokens) explicitly support selecting scopes; tokens cannot be retrieved again after generation. V1 has no refresh-token flow: invalid/revoked PATs require verified replacement.

Request `users:read`, `event_types:read`, `availability:read`, and `scheduled_events:write`. The [scope catalog](https://developer.calendly.com/docs/authentication/scopes) says write includes corresponding read, so `scheduled_events:read` is covered; select it explicitly if the token UI requires it. There is a documentation inconsistency: the catalog associates available-times with event-type read, while the [availability endpoint](https://developer.calendly.com/api-docs/calendly-api/event-types/list-event-type-available-times) requires availability read. Request both. Do not request event-type/availability write, organization administration, webhook, contacts, or outgoing-communications scopes.

Verify identity through [current user](https://developer.calendly.com/api-docs/calendly-api/users/get-current-user), pin the user URI and current organization, and query [event types](https://developer.calendly.com/api-docs/calendly-api/event-types/list-event-types) with that `user`. The list may include team/organization entries; listing access is not ownership. Require an individual profile whose owner matches the saved user, plus the support checks below. A missing/ambiguous profile is unsupported in v1. Changing user or organization invalidates readiness/proposals rather than broadening account access.

Read-only checks establish read access only. They do not prove booking-write permission, paid eligibility, calendar linking, or delivery. The explicit setup booking is the write-capability acceptance check. No invalid dummy POST should be used as a permission probe.

### 2.2 Lost-response research result

**Positive correlation is supported by the documented data model; safe automatic resubmission is not established.** The booking request accepts tracking, and invitee reads expose tracking. V1 will persist a random opaque attempt marker before dispatch, put it in `tracking.utm_content`, and compare it on recovery. This is AVA's correlation design, not a Calendly idempotency or uniqueness guarantee.

The expanded [booking schema](https://developer.calendly.com/api-docs/calendly-api/scheduled-events/create-event-invitee#request.body.tracking) marks all six tracking members required-but-nullable when the tracking object is present. Send all six: backend-owned `utm_source="ava"`, `utm_medium="voice"` or `"setup_test"`, `utm_content="ava-v1-<random-uuid>"`, and null `utm_campaign`, `utm_term`, `salesforce_uuid`. Never put a call ID, caller details, token, or model-supplied value in the marker. Tracking can appear in the host's reporting/CRM; it is correlation data, not authentication material.

[List Events](https://developer.calendly.com/api-docs/calendly-api/scheduled-events/list-scheduled-events) permits host/user, invitee-email, start-time, and status filtering. [List Event Invitees](https://developer.calendly.com/api-docs/calendly-api/scheduled-events/list-event-invitees) supports email/status filtering and returns tracking; [Get Event Invitee](https://developer.calendly.com/api-docs/calendly-api/scheduled-events/get-event-invitee) provides exact-resource recovery. There is no documented event-type or tracking-marker search filter: compare those locally after a bounded candidate search. Section 7.3 fixes the algorithm and match rules.

Neither these references nor [API conventions](https://developer.calendly.com/api-docs/overview/api/api-conventions) establish a read-after-write visibility bound, immutable tracking, marker uniqueness, or a booking idempotency key. The [AI guide](https://developer.calendly.com/docs/api-guides/schedule-events-with-ai-agents) suggests backoff on 5xx, but supplies no deduplication contract. V1 deliberately does not repeat a potentially committed booking POST. One exact marker match can establish positive evidence; no result, disappearance, a free slot, or a completed search cannot establish that the original write never committed.

**Qualification decision:** enable discovery-based automatic positive reconciliation only after an explicit setup booking proves exact marker readback through both exact and list reads for that saved connection. Record this capability against the connection identity/revision. Until then, exact saved identifiers can support recovery, and unknown-ID loss requires staff. Revoked access, unexpected marker loss, or account changes disable the discovery capability. This keeps live qualification from being a hidden prerequisite for safe failure handling.

### 2.3 Supported metadata and payload decisions

The following is the chosen v1 allowlist, not a promise to implement every variant the API exposes:

- Require active, undeleted `StandardEventType`, `booking_method="instant"`, `kind="solo"`, no pooling, no payment, positive fixed duration, and the verified individual owner. Reject poll, adhoc, multi-pool, round-robin, collective, and unknown enum values. Accept duration options only when absent/empty or exactly the one advertised duration. The schema describes `solo` as individual ownership; do not claim that label alone proves one-invitee capacity. Require the operator's one-to-one setup confirmation and reject slots showing remaining capacity above one; a last available group seat alone is not proof either. Revalidate metadata before dispatch. [Event-type schema](https://developer.calendly.com/api-docs/calendly-api/event-types/get-event-type)
- Enabled required questions may be `string`, `text`, `phone_number`, or `single_select`. Preserve upstream position and exact case-sensitive question text; answers are strings. Reject malformed/duplicate positions and unsupported required variants. Offer only declared single-select choices; an unqualified “Other” path is unavailable. Optional unknown questions can be omitted. Use explicit AVA bounds: at most 10 required questions, 20 choices per selection, 500 characters per question, and 1,000 per answer; exceeding these makes the type unsupported rather than truncating a required answer. These are AVA product limits, not claimed Calendly limits. [Question metadata](https://developer.calendly.com/api-docs/calendly-api/event-types/get-event-type#response.body.resource.custom_questions), [answer payload](https://developer.calendly.com/api-docs/calendly-api/scheduled-events/create-event-invitee#request.body.questions_and_answers)

For location, follow the guide's stricter rule: omit the request location only when the type has none; otherwise provide the single selected kind and its required fields. Never pass the upstream metadata object wholesale. [Location rules](https://developer.calendly.com/docs/api-guides/schedule-events-with-ai-agents), [expanded request variants](https://developer.calendly.com/api-docs/calendly-api/scheduled-events/create-event-invitee#request.body.location)

| Configured location | Booking request decision |
| --- | --- |
| None (`null` or empty list) | Omit `location` |
| One `physical` or `custom` | Send matching `kind` and the exact configured `location` string; missing fixed text is unsupported |
| One `inbound_call` | Send `kind` only; read the host phone from metadata for the recap, never substitute caller ID |
| One `google_conference`, `microsoft_teams_conference`, or `zoom_conference` | Send `kind` only; enable that kind after its disposable acceptance fixture passes |
| Multiple choices, `ask_invitee`, `outbound_call`, other/unknown kinds | Unsupported initially; show Calendly/staff handoff |

[Get Event](https://developer.calendly.com/api-docs/calendly-api/scheduled-events/get-scheduled-event) carries generated conference information separately from the invitee response. Its response uses location `type`, not request `kind`; conference states include `processing`, `initiated`, `pushed`, `failed`, and null. Only use a returned `join_url` with ready (`pushed`) state. One best-effort event read may enrich an already persisted booking within the remaining deadline. Missing/pending/failed conference detail does not mean booking failed and must never trigger another POST. Avoid deprecated `calendar_event` metadata as placement or invitation proof.

### 2.4 Error classification and remaining live acceptance

Persist a fully validated 201 outcome before announcing confirmation. Treat an explicit Calendly validation/auth/permission/not-found response, and its documented quota rejection, as rejected only after validating its origin and error shape. Unexpected gateway HTML, a lost/malformed success, transport interruption after dispatch, or 5xx is uncertain. Do not invent a 409 conflict contract: the reference lists 400/401/403/404/500 and the rate-limit guide separately documents 429. A definite unavailable-slot response permits a revised proposal and fresh confirmation; it never causes an implicit retry of the original attempt.

At the research checkpoint, live acceptance required a specifically authorized account, disposable recipient/booking, calendar-placement checks, exact error fixtures, tracking readback, and cleanup. Those were implementation acceptance tests, not unanswered design choices. The current live-qualified configuration and remaining limits are recorded in the evidence matrix above; other configurations require their own qualification. The public raw OpenAPI download was inaccessible during this research, so the request/response details above were read from the expanded official reference UI rather than assumed from an SDK.

## 3. Operator UI flow

Use the existing Tools page and Agent editor. Add a dedicated Calendly configuration panel within the existing navigation; do not create a separate scheduling application.

### 3.1 Tools → Calendly → Connect

- Initially show “Not connected,” a short explanation, Standard-or-higher booking prerequisite, and an “Open Calendly to create a token” help link.
- Accept the token in a password input. “Verify connection” performs read-only calls, shows connected person and timezone, and does not create appointments or save configuration silently.
- “Save connection” persists only a successfully verified candidate. Discard the submitted token from component state after success/close. Never place it in URLs, browser storage, analytics, or ordinary config payloads.
- For an existing connection, show “Token saved” and a separate Replace action; do not return the stored token or use an editable mask as a value. A failed candidate leaves the existing connection intact.
- Distinguish “Connection verified,” “Appointments selected,” “Applied to engine,” and “Test booking verified.” A successful `/users/me` request is not evidence of correct calendar linking or invitation delivery.
- Display a useful error for invalid/revoked token, insufficient access, network timeout, inaccessible credential storage, and unavailable engine. Avoid exposing upstream response bodies.

### 3.2 Choose existing appointments

- Fetch only the connected user's event types, following bounded pagination. Display name, host, duration, location, active status, and support status.
- Permit selection only for the qualified subset. Unsupported entries remain visible with a concrete reason and “Open in Calendly”; they cannot become native bookable resources.
- Apply the exact location allowlist and payload rules in section 2.3. Required invitee-selected/outbound-phone locations and multiple choices are deferred initially.
- Apply the question allowlist/bounds in section 2.3. Required multi-select, unknown, malformed, or excessive question sets make the appointment unsupported. Do not silently omit required questions. Extra guests, SMS reminder opt-in, payments, and arbitrary custom fields are excluded.
- Use stable AVA resource keys mapped to connection identity and immutable Calendly event-type identifiers. Friendly labels may change; a renamed/recreated upstream appointment must not silently rebind a stale key.
- Provide “Refresh from Calendly” and “Open Calendly settings.” Refresh never changes selected inventory silently or claims it verified underlying calendar connections.
- “Save & Apply” follows the current configuration-generation workflow. Surface saved-but-not-applied state and failed reloads. Avoid showing “Ready” while only the browser's draft selection exists.

### 3.3 Agents → Edit Agent → Tools

- Add the native `calendly_calendar` tool with the existing `inherit`, `selected`, and `none` resource policy pattern.
- Display appointment labels such as “Consultation · Alex · 30 minutes.” Default an explicitly newly configured Calendly assignment to selected resources; do not auto-enable it on existing agents.
- Show empty/deleted selections as errors that grant no access. The agent's policy can narrow global inventory, never enlarge it.
- Explain that ordinary appointment/agent edits affect new calls. Active calls retain their starting generation. “Disable booking now”/disconnect is a separate live connection gate for immediate revocation.
- Provide a link to the existing test-call instructions; do not alter Asterisk routing or dialplan setup to make this feature work.

### 3.4 Guided setup test

1. Run read-only connection and appointment checks and present the selected host/location/timezone.
2. Explain that Google/Microsoft calendar linking and conflict calendars are configured in Calendly. Ask the operator to inspect those settings there.
3. Offer real available times for one selected appointment. Collect an operator-controlled test recipient and the required answers.
4. Show an explicit final summary and a “Create test booking and send invitation” action. No booking occurs merely from Verify, Save, Refresh, or Apply.
5. Submit using the same journal/service as calls. Disable repeated clicks and retain the same server-created test operation ID across refreshes/retries. Browser button disabling alone is insufficient duplicate protection.
6. Show confirmed/pending/failed truthfully. For a confirmed booking, verify marker readback through exact and filtered list reads before marking discovery reconciliation qualified. Separately ask the operator to verify one-to-one configuration, calendar placement, actual invitation receipt, and management links. Record human checks as operator evidence with timestamps, distinct from API evidence. Never create a second test booking automatically if readback fails.
7. Provide the returned Calendly management link for cleanup and a cleanup checklist. AVA does not add a cancellation API just for tests. Mark cleanup as operator-reported unless verified by a scoped read.

This form is restricted to setup testing, not a general operator booking workspace. A setup test does not qualify voice behavior; a test call is a separate release check.

### 3.5 Results and recovery

- In existing Call History, show appointment label, requested time/timezone, submission outcome, attempt reference, and last reconciliation time. Preserve existing redaction policy for names, emails, answers, and identifiers.
- Keep historical labels truthful: “Confirmed when booked,” not a claim of continuously synchronized current status. External changes may make it stale.
- In the Calendly panel, show only unresolved/recent setup-test attempts and a count/link for call-associated unresolved attempts. Show whether discovery reconciliation is qualified and explain manual recovery when it is not. This small recovery view is required; importing/searching all Calendly appointments is excluded.
- “Check booking status” performs bounded read-only reconciliation. “Open in Calendly” supports staff investigation. Show unavailable credentials, incomplete search, no unique match, and last check separately.
- Provide an audited “Record external resolution” action with a bounded note/outcome. A staff note does not become API-confirmed booking evidence or unlock automatic retry. Keep the operation tombstone and prevent creating a replacement from the completed call.
- Cancellation/rescheduling links are bearer capabilities: expose them only to authenticated authorized operators in booking details, never public list responses, provider schemas, generic audit messages, or raw logs. The caller uses links in Calendly's own confirmation.

### 3.6 Disable, disconnect, and reconnect

- “Disable booking now” immediately closes the shared submission gate while retaining credentials for read-only recovery. This is the recommended incident control.
- Disconnect first closes that gate and advances a connection revision, then removes the saved token. Explain that existing appointments remain in Calendly and unresolved submissions may need staff checking there.
- A submission admitted before the gate closed may still complete afterward. Show it as in flight/pending and reconcile its result; never claim disconnect canceled a remote booking.
- Replacement with the same verified user can retain resource mappings. Reconnecting a different user creates a new connection identity and clears appointment selections/readiness; old calls cannot inherit the new account.
- Never delete history or unresolved-attempt records as a side effect of disconnect.

## 4. Caller flow and native tool contract

Expose one native tool, `calendly_calendar`, with action values rather than independent function names. The initial actions are `list_appointment_types`, `get_available_slots`, and `create_booking`. No caller-visible arbitrary booking lookup, cancellation, rescheduling, or link-sending action is required.

| Action | Inputs and behavior | Bounded output |
| --- | --- | --- |
| `list_appointment_types` | Trusted call/agent scope resolves permitted keys; do not accept a user/account URI | Allowed names, keys, fixed durations, location summaries, required questions |
| `get_available_slots` | Permitted appointment key, caller IANA timezone; optional future date range | Omitted bounds search server now + one minute through seven days later; supplied start without end searches seven days from start. Return earliest slots first, at most 10; speak 2–3 choices. Include actual search bounds, truncation, errors, and timezone. Empty means only that window is empty; ask before searching later. Explicit bounds remain future-only and at most 31 days |
| `create_booking` | Previously offered slot reference, appointment key, caller name/email, timezone and validated answers; subsequent submission also requires server-issued confirmation reference and both confirmation flags | First request returns canonical recap/email spelling without a POST; after fresh spoken approval the unchanged, call-bound recap permits one submission and a confirmed/pending/rejected outcome with stable attempt reference |

1. Determine the appointment purpose and choose an allowed appointment type. Use the trusted call clock for relative dates; clarify ambiguous spoken dates/timezones rather than inventing them.
2. Query Calendly and offer a short list in the caller's timezone. Availability is not a reservation; an API failure is not evidence of a busy slot.
3. Collect required name/email and appointment questions. Read back the exact recipient address, including the domain, and obtain consent for the invitation. With no usable email or declined invitation, offer staff/self-service guidance; v1 does not create an appointment without the required recipient.
4. Recap appointment, date, time, timezone, duration, location, recipient, and material answers. Obtain explicit booking approval. A changed detail invalidates prior approval.
5. Require a current call-bound slot reference issued by the backend (initial lifetime: 5 minutes). Re-fetch upstream metadata/availability before submission; a materially changed duration/location/question invalidates the proposal and requires reconfirmation. Never silently substitute a different slot.
6. Book once. If the slot is now taken with a definite rejection, offer new times and reconfirm. If the outcome is uncertain, stop further submission and explain that staff must verify confirmation.
7. On confirmed success, state the actual appointment details and that Calendly handles the configured confirmation. Do not claim delivered/accepted email or an RSVP. If conference details are still being generated, describe them as pending and direct the caller to their confirmation.
8. A repeat tool call with identical details returns the retained outcome. After success, requests for a different appointment/time/recipient use the Calendly confirmation or staff; do not cancel/recreate behind the scenes.

The explicit confirmation field and proposal binding are runtime guards. They cannot prove that a model actually spoke the recap or that the caller said yes. Prompt guidance and live conversation acceptance must verify that behavior; do not claim audio-level consent enforcement from a boolean alone.

The caller's number/name, agent identity, attempt ID, account identity, endpoint, and credential are trusted service/context values. Model arguments cannot select arbitrary hosts, URIs, request headers, management links, or existing event IDs.

## 5. Frontend implementation map

Recheck paths before implementation. Prefer a dedicated component/service to extending the existing large calendar form with another large inline block.

| Existing or proposed area | Planned change |
| --- | --- |
| `admin_ui/frontend/src/pages/ToolsPage.tsx` | Add built-in inventory entry, open the Calendly panel, and reflect saved/applied/readiness state |
| `admin_ui/frontend/src/components/config/ToolForm.tsx` | Route Calendly editing to its dedicated component while retaining existing tools |
| Proposed `components/config/CalendlyToolForm.tsx` and typed API helper | Connection, appointment selection, setup test, disable/disconnect, unresolved-attempt panel |
| `components/agents/AgentToolPicker.tsx`, `agentToolConfig.ts`, `pages/AgentsPage.tsx` | Resource options, policy edit/serialize/round-trip, missing-resource messaging |
| `pages/CallHistoryPage.tsx` and existing tool-detail renderer | Redacted outcome and an authenticated detail link; no second booking list |

Use existing controls, layout, error display, and accessibility conventions. Provide keyboard operation, associated labels, focus on invalid fields, busy states, and narrow-screen layouts. Opening Calendly settings must preserve non-secret setup progress.

Invalidate verification whenever token/connection/appointment inputs change. Use request revision IDs or cancellation to ignore late responses. Prevent a request made for account A from marking account B verified. Do not automatically resubmit a booking on React remount, browser reload, route return, timeout, or reconnect.

## 6. Backend and runtime architecture

### 6.1 Shared modules and responsibilities

Both the Admin backend and AI Engine call the same Python booking service. The engine must book with Admin UI stopped. Do not proxy engine bookings through the Admin API or introduce a cross-process HTTP dependency.

| Existing or proposed area | Responsibility |
| --- | --- |
| Proposed `src/tools/business/calendly_client.py` | Fixed-origin API transport, typed/bounded parsing, scoped reads, one booking POST, sanitized errors |
| Proposed `src/tools/business/calendly_service.py` | Identity validation, support classifier, proposals, validation, journal transitions, reconciliation |
| Proposed `src/core/calendly_store.py` | Shared SQLite schema, transactional uniqueness/leases, connection gate, safe migrations |
| Proposed `src/tools/business/calendly_calendar.py` | Native action schema and adapter to service using trusted call context |
| Proposed `admin_ui/backend/api/calendly.py`, registered with authentication in `main.py` | Connection/setup/recovery endpoints; no duplicate booking implementation |
| `admin_ui/backend/api/tools.py`, config persistence, `src/config.py` | Built-in inventory, non-secret validation, defaults, save/apply compatibility |
| `src/tools/registry.py`, `runtime_config.py`, `runtime_guidance.py` | Registration, isolated tool generations, agent resource policy, prompt guidance |
| `src/tools/adapters/sanitize.py`, provider adapters/dispatch paths | Preserve structured slots/outcomes safely across supported providers |
| Narrow engine startup/shutdown hooks | Start/stop unresolved-attempt reconciliation; bounded lifecycle only |

No change to provider/media ownership or call teardown ordering is intended. Run blocking file/SQLite work outside the audio event loop; use the project's supported bounded HTTP client pattern and clean up clients/tasks.

### 6.2 Proposed authenticated Admin endpoints

Names below are the intended contract, subject to minor alignment with current routing conventions. All are behind existing Admin authentication and request protections. Validation errors never return a token or raw provider body.

| Endpoint under `/api/calendly` | Contract |
| --- | --- |
| `GET /connection` | Safe saved identity, state/revision, last verification, selected/applied/test status; no credential |
| `POST /connection/verify` | Read-only verify a candidate or saved token; return identity and warnings; no appointment creation |
| `PUT /connection` | Verify and atomically save a candidate; optimistic revision check; no ordinary YAML token field |
| `POST /connection/disable` | Close live booking admission; retain recovery credential |
| `DELETE /connection` | Close admission, advance revision, remove credential, retain history |
| `GET /appointment-types` | Bounded current-user discovery and support diagnostics; allow opaque local pagination |
| Existing tool save/apply API | Persist non-secret enabled/selected inventory and publish generation; keep one config authority |
| `POST /test-proposals` | Read-only slots and validated test proposal; short-lived server-generated operation identity |
| `POST /test-bookings` | Explicitly confirmed setup test only; same service/journal; repeated operation is idempotent locally |
| `GET /attempts` and `GET /attempts/{id}` | Paginated unresolved/setup view and authenticated details; associated call links |
| `POST /attempts/{id}/reconcile` | Read-only upstream reconciliation with lease/rate limits; never submit again |
| `POST /attempts/{id}/external-resolution` | Audited staff report with expected revision; no fabricated API confirmation or new booking |

All mutations accept a saved revision/operation identity where concurrent edits matter. Resolve every attempt to local known connection/operation records, never a caller-supplied remote URI. Do not add a general-purpose API proxy or arbitrary account lookup.

### 6.3 Configuration, credentials, and permissions

- `tools.calendly_calendar` owns non-secret global enabled state, selected event-type mappings, and bounded search settings. Disabled by default; absent config leaves existing installs unchanged.
- Add a `calendly_calendar` agent policy with `appointment_policy` and `appointment_keys` using the existing JSON tool-config mechanism. Do not add an agent database column unless current storage genuinely requires it.
- Every discovery/result/prompt/schema/execution path uses the same effective allowed set. Empty selected sets, stale keys, unknown ownership, missing scope, and deleted upstream resources grant no access.
- Store the PAT in a dedicated canonical file under the existing shared secrets mount, `secrets/calendly-default-token.json`, with atomic replace and restrictive permissions readable by the actual engine/Admin UIDs. Reuse suitable existing helpers; do not build a new vault. Prove file access in both containers.
- Persist only a credential reference and verified principal/revision in configuration/state. Do not bake the PAT into an immutable registry generation, provider schema, agent row, export, or `.env` process snapshot.
- Pair credential writes with a fail-closed connection state: verify the candidate first, close admission while committing a replacement, atomically write the file, then promote matching metadata. An interrupted save remains disabled/recoverable rather than pairing identity A with token B.
- Serialize connection mutation using a stable cross-process lock and revision checks; never delete a lock file while another process could hold it. Re-read the current gate before booking admission.
- Existing call scopes never grow on reload. Same-principal credential replacement may service the same identity after verification; a different-principal reconnect cannot service an old call. Remote appointment metadata is revalidated before writes even for old generations.
- Normal Save & Apply removals follow existing new-call generation semantics. Use the explicit live disable/disconnect gate to stop further admissions from existing calls; document this distinction in UI and tests.

### 6.4 Shared durable state

Use a small integration-owned SQLite database, `data/operator/calendly.db`, shared through existing mounts. Avoid mixing operational booking state into `agents.db` or treating Call History as the submission journal.

Minimum records:

| Record | Required data/invariants |
| --- | --- |
| Schema metadata | Versioned additive migration, integrity/compatibility checks |
| Connection | Stable ID, verified user/organization, revision, admission enabled state, credential reference, verification timestamps, discovery-reconciliation qualification/revision |
| Operation/proposal | Trusted origin (`call` or `setup_test`), call ID or server test ID, agent/generation, allowed resource identity, proposal expiry and canonical detail revision |
| Attempt | Opaque ID, operation ID, request fingerprint, duplicate guard, persisted random tracking marker, submission state/timestamps, safe reason codes, response identifiers, reconciliation counters/lease |
| Private booking detail | Minimum name/email/timezone/question payload needed for submission/recovery; protected remote IDs/management links; never generic list output |
| Resolution history | Evidence source (`api`, `operator`, `local`), previous/new state, staff identity where applicable, timestamp, bounded note |

Require durable commit before POST, transaction-enforced unique active/confirmed operation, bounded SQLite busy handling, and leases/CAS transitions for competing service/worker processes. Do not hold a database transaction open across network I/O. No journal availability means no booking submission; do not fall back to memory.

Retain an operation tombstone so retries after restart do not become new bookings. Minimize retained personal details; document retention consistent with Call History. Unresolved operations cannot be silently purged or unblocked by a retention job. If sensitive payloads are removed, keep sufficient non-secret guard/state metadata to preserve the restriction. Backups of database/WAL and credential files are private deployment data.

## 7. Reliability and complete lifecycle

### 7.1 Submission state machine

| State | Meaning | Permitted next step |
| --- | --- | --- |
| `prepared` | Validated proposal/attempt durably recorded; no dispatch admitted | Admit once after confirmation, live gate, ownership, metadata, slot, and journal checks; or expire/abort |
| `submitting` | Single submission claim committed; remote acceptance may occur | Persist success, definite rejection, or uncertainty; duplicate callers wait/read, never POST |
| `confirmed` | Validated successful booking response or uniquely correlated active remote evidence | Return retained result; no further booking in this call |
| `resolved_remote_cancelled` | Exact correlated record exists but is now canceled | Report created-then-canceled evidence truthfully; no recreate or fresh submission from this call |
| `rejected` | Evidence establishes no booking was accepted | Report failure; an explicitly reconfirmed corrected/new slot may create a new attempt within the same operation |
| `aborted_before_submit` | No dispatch was admitted, including gate closure or expired proposal | Re-propose/reconfirm if the call remains eligible |
| `uncertain` | Timeout, interrupted dispatch, malformed success, server error with possible commit, or lost local success persistence | Read-only reconcile; block further booking in the operation |
| `needs_operator` | Bounded reconciliation exhausted, unavailable, or ambiguous | Staff checks Calendly; recorded external resolution never silently resubmits |
| `resolved_externally` | Staff recorded handling outside AVA | Retain audited evidence and tombstone; no replacement from the old call |
| `erased` | Call History deletion removed linked details and notes | Immutable tombstone; late booking/recovery/staff writers cannot repopulate it. Keep replay/duplicate barriers, stop recovery and direct staff to Calendly; erasure does not cancel a remote appointment |

Distinguish errors known before network dispatch from errors after dispatch. Treat 5xx/lost responses/malformed success as possibly committed unless the API contract proves otherwise. Only classify a response as a definite rejection when its semantics are qualified; do not infer this solely from generic HTTP retry conventions.

On restart, an expired `submitting` lease becomes uncertain, not prepared. A process crash after claim but before actual network send is conservatively uncertain. A confirmed response followed by a failed local commit must not be announced as durably confirmed until persisted; attempt safe persistence and reconcile if necessary.

### 7.2 Duplicate and concurrency rules

- Define the primary booking operation from trusted call ID (or server-created setup-test ID). Tool invocation IDs, model-generated IDs, tab refreshes, and random retries cannot create a new operation.
- Enforce at most one confirmed/unresolved booking operation per call across the connection lifecycle; reconnecting or changing details must not bypass it.
- Identical repeated invocation returns the same attempt/outcome. Changed details cannot bypass an in-flight or uncertain attempt; a previous success sends the caller to management links/staff.
- Add a private cross-call duplicate guard for the same connection principal, event type, UTC slot, and canonical recipient while a local confirmed/unresolved record exists. A later call receives a generic staff-verification result without another caller's details. This is not identity verification or arbitrary booking lookup.
- Do not over-normalize email local parts or names and merge different people. Do not deduplicate every caller seeking the same slot; different recipients may independently receive a definite slot conflict from Calendly.
- Do not promise duplicate prevention against bookings created by unrelated applications, unretained historical records, or modified remote appointments. Avoid permanent local slot locks that claim upstream availability.
- Admission and connection disable are serialized locally. A claimed submission predating disable may settle; claims after disable fail. There is no attempt to retract an already sent POST.

### 7.3 Bounded reconciliation: resolved algorithm

This is a positive-evidence lookup, not a transaction-status endpoint or a safe-to-retry probe. It operates only on known local attempts. It never authorizes a later caller to inspect or change an appointment.

1. Acquire the shared attempt lease/CAS revision. Load the saved principal, event-type URI, exact UTC slot, recipient, tracking marker, and any validated remote IDs. Confirm the credential belongs to the same user/organization. Unknown-ID discovery additionally requires the saved connection's successful readback qualification from section 2.2.
2. If both saved IDs exist, read the exact event and invitee and verify their relationship and expected identity/details. If only the event is known, inspect that event's invitees with the saved email and exact marker. IDs partially recovered from a malformed response are candidates, not trusted success evidence. A forbidden or missing exact record does not establish a failed original write.
3. Otherwise query `/scheduled_events` using the saved `user`, `invitee_email`, `min_start_time=T-1 second`, `max_start_time=T+1 second`, `count=100`, and ascending start-time sort. Use the slot's start, not submission time: Calendly's bounds filter event start times. Run separate `active` and `canceled` searches, avoid assumed default-status behavior, and compare exact UTC start and event type locally. Do not add nonexistent `event_type`, `utm_content`, or `created_at` query filters. A user's personal-event lookup can include former organizations, so saved resource identity and host membership checks remain mandatory.
4. For matching event candidates, inspect `/scheduled_events/{id}/invitees` with the saved email, `count=100`, and explicit active/canceled status where needed. Follow validated opaque page tokens only within budget. Compare `tracking.utm_content` to the exact saved marker; also verify event/invitee relationship, connected host membership, event type, start/end/duration and material attendee details. Creation timestamps are corroboration, not an assumed visibility deadline or sole ownership proof.
5. Persist success only when the required search completed within budget and exactly one event/invitee pair meets all checks. Active event plus active invitee becomes `confirmed`. An exact correlated canceled event/invitee becomes `resolved_remote_cancelled`; it proves the booking existed, not that it remains active. Changed/rescheduled relationships, inconsistent statuses, mismatched details, or more than one match require staff. Do not follow rescheduling chains into new appointment authority in v1.
6. Retain uncertainty for zero/missing-marker matches, partial searches, overflow, unexpected response shapes, expired private lookup details, access failure, or ambiguous evidence. After finite automatic passes, expose `needs_operator`. No result can reclassify an admitted write as safe to resend merely because it is absent from current reads.

Chosen AVA budgets: 3 automatic passes scheduled approximately 5, 30, and 120 seconds after uncertainty (persist next-due time/counters across restart); at most 8 upstream reads and 20 seconds per pass. Event discovery uses at most 2 pages per status, and every invitee read/page consumes the remaining shared read budget. If uniqueness cannot be established within that budget, return `search_incomplete`, never success/no-booking. Known-ID recovery usually needs only 2 reads. Pause for rate limits/revoked credentials without busy polling; no more than 3 executed automatic passes regardless of restart. Stale in-progress passes count conservatively against that limit.

The engine owns scheduled checks. Manual Admin checks use the same leases, a 30-second per-attempt cooldown and shared connection quota, and do not require the engine. An operator may request a later read-only check after the automatic budget is exhausted; this never repeats the POST. Qualification/readback failure is visible and routes unknown-ID loss directly to staff. Completed bookings are not continuously polled.

The exact-marker policy is a conservative AVA inference from the documented tracking/read APIs. It is not proof against remote tampering or future duplicates after an observation. The marker must not be accepted from model arguments, used as authentication, exposed in spoken results, or used alone to grant mutation rights.

### 7.4 Latency, quotas, and cleanup

- Use a maximum 31-day query range, default 7-day search, 3 suggested slots, 10 returned slots, and a bounded number of provider calls/pages. Return explicit exhaustion/truncation instead of inventing no availability.
- Fix the initial service deadline at 15 seconds including validation, database waits, revalidation and one POST, reserving the final 2 seconds for durable outcome recording. Connection timeout is at most 3 seconds, individual reads at most 5 seconds, all capped by remaining time. Do not admit the POST unless at least 5 seconds remain before the finalization reserve. Set the native definition to 20 seconds to leave adapter/result-finalization headroom, but enforce the 15-second monotonic deadline inside the service: existing adapters directly await `tool.execute`, so `max_execution_time` alone is not enforcement. Do not stack retries beyond the budget.
- Booking POST has no implicit transport retry. At most one bounded safe read retry fits inside the same budget; respect qualified upstream quota/reset guidance. Explicit rejection/quota errors require an actionable caller response, not an unbounded queue.
- Persist observed rate-limit cooldowns shared by engine and setup tests. Use a small shared submission admission limit (initially one in-flight POST per connected user) and bounded wait; other integrations still consume upstream quota, so local counters cannot prove remaining allowance.
- A caller disconnect before admission prevents the booking. Disconnect/cancellation during an admitted write records possible completion and reconciles it. Call cleanup releases local resources without erasing the attempt or issuing compensating cancellation.
- Background task cancellation and engine shutdown preserve admitted attempts. Do not keep telephony/media teardown waiting for remote recovery. Readiness failure must affect Calendly availability rather than crash an otherwise working engine.

### 7.5 Privacy and transport

Fixed HTTPS API origin, TLS verification, redirects disabled, bounded response sizes, and strict resource-ID/path validation apply to discovery, booking, and pagination/reconciliation. Treat returned next-page/resource URLs as untrusted; validate origin/path or reconstruct requests from validated identifiers. Do not fetch management URLs server-side.

Treat appointment names/descriptions/questions and upstream error text as data, never prompt instructions. Prefer plain text over HTML and bound lengths/answers. Extend audit redaction for invitee/attendee fields, question answers, tokens, and cancellation/reschedule capability URLs. Provider results should contain only what is needed for the current conversation; generic nested `result` serialization must not leak private service state.

## 8. Automated verification

Use synthetic fixtures, mocked HTTP transports, temporary databases/secret files, and a controllable clock. Automated tests must create no live appointments or invitations. Test the actual service/adapters and state transitions rather than only mocked UI happy paths.

| Suite | Required cases |
| --- | --- |
| API client/support classifier | PAT errors; exact endpoint/owner restrictions; event pagination; unknown enum/metadata; inactive/deleted/paid/group/round-robin/variable-duration entries; qualified questions/locations; malformed/oversized responses; timeout; 429; off-origin redirects/pagination |
| Availability and proposal validation | Trusted relative dates; explicit caller timezone; UTC conversion; DST gaps/overlaps; past/range boundaries; no slots vs API failure; truncation; stale/forged/wrong-call slot refs; metadata changes and fresh reconfirmation |
| Booking service | Required details/answers/email/confirmation; slot conflict; response validation; missing conference URL; exact one-POST behavior; success persistence; definite rejection; server error; malformed success; caller disconnect before/after admission |
| Journal and concurrency | Two simultaneous same-call submits; duplicate setup clicks; cross-call exact duplicate guard; changed details during pending/success; separate callers; DB busy/full/read-only/corrupt/newer schema; migration idempotence; retained tombstones |
| Crash/recovery fault injection | Crash before journal, after prepare, after admission before send, after remote commit before response, after response before local commit; restart never blindly replays POST |
| Reconciliation | Qualified exact marker readback and unqualified fallback; all-six-member tracking payload; exact known IDs vs untrusted partial IDs; active/canceled records; exact start-window boundaries; no unsupported query filters; delayed/zero/multiple matches; matching marker but wrong host/type/details; missing marker; changed/rescheduled relationship; page/read overflow; 401/403/404/429; engine/manual lease races; restart-persisted finite passes; operator evidence |
| Connection/credentials | Exact requested scopes and missing-scope responses; failed candidate preserves original; interrupted token save; restrictive access after preflight; revision races; same-user rotation vs user/organization change; readback qualification invalidation; disable/disconnect racing admission; no token in reads/exports/errors/logs |
| Agent/generation integration | Inherit/selected/none; stale/empty keys; disabled global tool; schemas/prompts/results restricted to effective inventory; old generation never grows after reload; live gate stops old-call admissions; failed apply retains old generation |
| Admin backend | Authentication on every route; bounded inputs; no user-controlled remote IDs/paths; optimistic revisions; verify has no write side effect; test explicit confirmation/operation identity; masked listing vs private details; safe external resolution audit |
| Frontend | Connect/edit/replace/disconnect; stale verification responses; unsupported reasons; selection/apply failure; agent round-trip; setup confirmation and retry/remount; recovery pending/staff state; cleanup handoff; keyboard/mobile behavior |
| Provider and regression | Native tool schema and structured output through OpenAI GA/Beta, Google Live, Deepgram, ElevenLabs and supported tool-capable pipelines as applicable; correct function/action naming; no loss of slot arrays/status; no technical acknowledgements spoken; existing calendars, transfer, hangup, metadata and audit behavior retained |
| Persistence/privacy deadlines | FULL journal synchronization requested; 1-second busy bound; 15-second internal service deadline even with adapters lacking a wrapper; credential file and directory fsync failures; 30-day-or-shorter detail retention; Call History purge independent of tombstones; expired recovery details; restored-backup admission disabled |

Implemented test suites include `tests/tools/business/test_calendly_client.py`, `test_calendly_service.py`, `tests/test_calendly_store.py`, `admin_ui/backend/tests/test_calendly_endpoints.py`, and colocated frontend component tests. Extend existing policy, reload, sanitizer, adapter, and Agent config tests where they already assert shared behavior.

Verification commands (run in the stated directory, following current CI dependency setup):

```bash
# Repository root: focused new tests plus shared runtime regressions.
python -m pytest tests/tools/business/test_calendly_client.py tests/tools/business/test_calendly_service.py tests/test_calendly_store.py tests/tools/test_runtime_config_v740.py tests/test_tool_reload_v740.py tests/test_tool_runtime_guidance.py tests/test_tool_adapter_allowlist.py -q

# admin_ui/backend: endpoint tests first, then the backend suite.
PYTHONPATH=../.. python -m pytest tests/test_calendly_endpoints.py -q
PYTHONPATH=../.. python -m pytest tests -q

# admin_ui/frontend: focused components during development, then release checks.
npm test
npm run lint
npm run build
```

Run affected existing calendar, provider response, Agent persistence, Call History, and secret-scan tests. The final CI gate must also pass its current root Python/coverage, backend, frontend, security, and image requirements. Passing a frontend build alone is not calendar qualification.

## 9. Acceptance criteria

All criteria below must be demonstrated or explicitly scoped by a supported-configuration matrix before release. “Implemented” and “live qualified” are separate evidence labels.

| ID | Acceptance criterion | Evidence |
| --- | --- | --- |
| A01 | Fresh operator completes connection and selection without YAML, OAuth app registration, or public callback ingress | UI walkthrough plus backend/frontend tests |
| A02 | Verify/Save/Refresh/Apply never creates a booking; setup test requires explicit recipient/details approval | Mocked call count and UI test |
| A03 | Unsupported appointments cannot be selected/booked; existing Calendly settings are left authoritative | Classifier/runtime negative tests and UI reasons |
| A04 | Two agents see only their permitted appointments; empty/deleted selections fail closed | Provider schema, discovery, execution and Agent round-trip tests |
| A05 | A supported caller completes purpose → available slot → email readback → final approval → one booking with truthful recap | Authorized test call and synthetic conversation checklist |
| A06 | Required questions and supported location reach Calendly correctly; missing details do not create a booking | Mocked payload tests and disposable live fixture |
| A07 | Caller timezone and DST cases resolve correctly; an API failure is not described as busy | Clock-controlled tests plus voice acceptance |
| A08 | A taken slot produces a definite failure and a newly confirmed choice, without silent time substitution | Mocked race and conversation acceptance |
| A09 | Repeated model calls, double-clicks, remounts and concurrent requests submit at most one POST per attempt; only a definite rejection plus revised proposal/fresh confirmation permits another attempt | Transport assertions and real multiprocess/SQLite concurrency test |
| A10 | Lost responses and crashes preserve pending state; restart never blindly replays a booking | Fault injection at all listed crash boundaries |
| A11 | Qualified unknown-ID recovery requires one exact marker match plus complete bounded evidence; known-ID checks validate identity/details; canceled/mismatched/absent/incomplete evidence never causes blind retry or false active success | Full reconciliation test matrix and explicit setup readback; manual fallback demonstrated when unqualified |
| A12 | Staff can find pending attempts, run a bounded check, and record external resolution without a general booking dashboard | Recovery UI/API walkthrough |
| A13 | Engine books with Admin UI stopped; unavailable journal fails only scheduling; teardown/media remain healthy | Disposable deployment integration test |
| A14 | Disable/disconnect blocks admissions after the gate closes, preserves prior in-flight outcomes, and does not cancel appointments | Cross-process race and restart tests |
| A15 | Reconnect to a different account cannot reuse old-call authority or silently rebind appointment keys | Ownership/generation tests |
| A16 | Calendly invitation appears at the authorized test recipient and in the intended host calendar; management links work | Google/Microsoft-qualified test matrix with private evidence and cleanup |
| A17 | UI/speech never claim email delivery, RSVP, native later-call management, or continuously current status without evidence | Result/guidance tests and manual acceptance |
| A18 | Tokens, answers and bearer management links stay out of provider schemas, generic logs, exports and public/list results | Redaction/negative tests and safe artifact inspection |
| A19 | Every declared tool-capable provider path consumes slots/status correctly; unchanged telephony/calendar regressions pass | Provider matrix and final CI |
| A20 | Test bookings are cleaned up in Calendly; no test artifacts/customer secrets are committed | Operator cleanup checklist and final diff/secret review |

Live tests require a named account/calendar, specific authorized recipient, and disposable booking scope. Do not send to customer attendees or alter production settings to satisfy acceptance. Use the repository's archive-first call-analysis procedure for live evidence, keep raw evidence private, and record sanitized results in the PR.

## 10. Implementation sequence and completion gates

- [x] **Resolve documentation/design questions:** inspect official expandable schemas and repository conventions; fix scopes, marker reconciliation, metadata allowlist, storage/retention and time budgets in this plan. This checkbox records research only.
- [x] **Capture executable qualification fixtures:** synthetic contract/failure/payload fixtures and the explicitly authorized Google setup/readback checks are complete. The configuration matrix above distinguishes mocked support from live qualification; no expanded OAuth/event-editor work.
- [x] **Build the reliability foundation:** shared client/store/service, protected credential/gate lifecycle, journal migration, one-POST admission, duplicate guards, crash recovery and bounded reconciliation tests.
- [x] **Complete one vertical slice:** connect → assign → voice book → exact Calendly inspection passed for the qualified Google configuration in call `1792829386.882`; farewell/drain acceptance passed in `1792866150.886`.
- [x] **Finish operator setup:** supported-type diagnostics, Agent selection, stale verification handling, explicit setup test/cleanup, and saved/applied/readiness states.
- [x] **Finish recovery and provider integration:** unresolved-attempt UI, authenticated management links, operator evidence, background lifecycle, sanitizer/adapter handling and trusted prompt guidance. Live qualification covers Google Live only.
- [x] **Verify failure paths:** synthetic concurrency, ambiguous-write/crash/restart, resource deletion/revocation, disable/reconnect, quotas, direct engine-service independence from Admin HTTP, and call-lifecycle checks passed. Real deployment/cleanup and farewell evidence supplement the synthetic failure matrix; deliberate ambiguous live writes were not replayed.
- [x] **Qualify live acceptance:** the explicitly authorized Google Live/Google Meet slice, invitation/calendar placement, exact/list readback, real voice booking, farewell and cleanup are complete. The matrix above visibly limits the live claim; other configurations need their own qualification.
- [x] **Document and freeze:** setup/troubleshooting, scope/limits, backup/rollback, compatibility, changelog and live checkpoints are documented. Final readiness CI/review remains the separate merge gate on the current frozen head; the final review batch must pass it before merge readiness.

During implementation, follow [the PR workflow](contributing/PULL_REQUEST_WORKFLOW.md): continue draft development without repeated bot reviews, request the final full review once at freeze, and do not push while final reviews are pending. The original research-only phase created no branch, PR, deployment, token, or live booking; later authorized implementation and acceptance checkpoints are recorded above.

## 11. Rollout, migration, and rollback

- Ship disabled by default with additive config/policy support and a versioned integration database. Do not change existing Google/Microsoft calendar defaults or automatically modify Agent prompts/tools.
- Validate actual mount ownership, database/WAL permissions, PAT path, backup/restore, and engine-only behavior in a disposable environment before a pilot. No new public ingress or firewall work is needed.
- Start with one explicitly selected pilot agent and one qualified appointment. Confirm that no existing calendar tool can accidentally book the same request in parallel; give the agent clear tool-choice guidance without globally disabling other agents' tools.
- Treat initial Python code installation as a deployment/restart according to existing procedures; ordinary non-secret Save & Apply uses tool generations afterward. Never suggest that live config reload installs Python modules.
- Back up configuration, affected Agent policy rows, integration database using a SQLite-safe method, and protected credential files. Do not copy a live SQLite main file without its committed WAL state.
- Rollback closes the live gate first, allows admitted attempts to settle/be recorded, and removes Calendly tool references and its agent-policy scope before running an older version that rejects unknown scopes. Preserve booking history and pending attempts for staff recovery.
- Rollback never deletes real appointments, silently resets submission state, or replays booking requests. If older code cannot read the integration database, leave it preserved and reconcile externally.
- On return to the feature version, migration is idempotent and pending attempts remain blocked. Failed migration/newer incompatible schema disables scheduling safely and reports a useful error. Restoring an older journal backup can lose post-backup remote submissions: restore procedure must keep the admission gate disabled until staff reviews that interval. No local durability setting can reconstruct missing remote history or justify replay.

## 12. Resolved research decisions and remaining acceptance work

The original three open design questions are resolved for v1. Live account behavior is intentionally not labeled proven by documentation.

| Original question | Decision | Remaining evidence |
| --- | --- | --- |
| Lost-response correlation | Backend-owned `utm_content` marker, exact host/type/slot/attendee checks, bounded positive recovery, no negative-proof retry. Unknown-ID automation is gated on saved connection readback qualification | Disposable booking plus exact/list readback; mocked lost-response and restart scenarios. Until qualified, use visible staff fallback |
| Question/location support | Fixed allowlist and bounds in section 2.3; exact question text/position, minimal matching location payload, separate conference readiness | Payload fixtures for the supported kinds; Google/Microsoft placement/invitation checks; no implicit broadening |
| Ownership, storage, retention and timeouts | Use existing shared mounts/UID convention, async transport dependencies and SQLite patterns with stronger journal durability and shorter busy waits; explicit defaults below | Container-permission, durability/fault-injection, retention/redaction and provider-path acceptance tests |

### 12.1 Storage and concurrency decisions from repository inspection

- Both services share `./data` at `/app/data`. The engine mounts `./secrets` at `/app/project/secrets`; Admin mounts the whole project at `/app/project`. Use `/app/data/operator/calendly.db` and `/app/project/secrets/calendly-default-token.json`. Standard images target UID 1000: Admin declares it explicitly; the engine creates its normal appuser, and preflight assumes UID 1000. This establishes the intended convention, not proof about an arbitrary customized deployment. [Compose](../docker-compose.yml), [engine image](../Dockerfile), [Admin image](../admin_ui/Dockerfile), [preflight](../preflight.sh)
- Choose owner-only token/database/WAL/lock files and a private Calendly state directory, using the shared service UID. The existing secrets parent is 2770; the state directory can be 0700. Verify actual ownership instead of weakening access to make tests pass. Preflight can recursively set secret files to 0660 when repairing the secrets parent; update that repair path to preserve Calendly's private files, or fail a permission check with a targeted repair action. Do not chmod unrelated credentials. [Existing token persistence](../src/tools/business/ms_graph_client.py)
- Both Python environments already include `aiohttp` and `portalocker`; use those and standard-library `sqlite3` through bounded executor/to-thread work. No new SDK, database driver, service, or runtime package is required by the design. [Engine dependencies](../requirements.txt), [Admin dependencies](../admin_ui/backend/requirements.txt)
- Existing history/outbound stores use SQLite WAL, `synchronous=NORMAL`, 30-second busy timeout and executor work. For this submission journal, choose WAL plus `synchronous=FULL`, foreign keys, 1-second busy timeout, short transactional CAS/unique constraints, and a 2-second connection-mutation lock limit. The existing long busy wait exceeds the caller budget and NORMAL does not meet the intended power-loss durability boundary. Do not change existing stores as part of this feature. [Call History](../src/core/call_history.py), [outbound store](../src/core/outbound_store.py)
- Credential persistence must flush/fsync the private temporary file, atomically replace it, and fsync the containing directory before reopening admission. File contents include identity/revision so a startup mismatch fails closed. Existing Microsoft file-replace code is a reference for access conventions, not proof of crash durability. Protect migrations using an initialization lock; storage failure disables scheduling rather than dropping to memory.

### 12.2 Retention and redaction decisions

[Call History](../src/core/call_history.py) defaults `CALL_HISTORY_RETENTION_DAYS` to 0 (no automatic deletion), with explicit operator purge APIs. Calendly private lookup payloads should not inherit indefinite retention implicitly.

- Choose a 30-day default for Calendly personal details/management links, shortened to the positive Call History retention value when it is less than 30 days. Apply the same deadline to unresolved private details; state/duplicate protection survives their deletion. A later privacy configuration may shorten it further without unblocking old operations.
- Raw answers and the full outbound request are not needed after a terminal result is persisted: erase them after the call/setup operation completes, retaining a private fingerprint and minimal recipient/type/slot/remote IDs only as needed for the limited recovery window. Uncertain attempts retain only the fields needed by section 7.3; do not preserve the whole call transcript.
- Call History deletion/purge must atomically mark matched attempts `erased`, clear leases and linked details/notes, and leave minimal operation/duplicate tombstones. If admission has not yet committed, the same transaction creates an operation-only erased barrier so a queued admission cannot create private data afterward. Later booking or reconciliation completions cannot repopulate erased rows, and staff resolution or canceled-duplicate release cannot reopen them. Erasure retains only cross-call duplicate barriers that were still active; definite rejection, pre-submit abort and positively verified cancellation stay released for other calls. The original operation remains blocked in every case. Erasure does not cancel an admitted upstream write. Read-only lookup by retained request reference reports erasure without links/details and directs staff to Calendly; erased calls leave the pending queue. After lookup details expire, show “Details expired; resolve in Calendly” and stop automatic/API discovery for that attempt.
- Tombstones retain attempt/operation IDs, state/evidence classification, opaque marker and private duplicate/request digests, without names, emails, answers or management URLs. Digests are sensitive pseudonymous data, not claimed anonymous data. Never expose them in public listings, prompts or exports; retain operation barriers rather than silently permitting replay.
- Reuse `strict` Call History redaction by default and extend [execution-history redaction](../src/tools/execution_history.py) for answer containers and capability URLs. Secret/capability exclusion from generic Calendly results is unconditional even when an operator opts into less Call History PII redaction. An authenticated private-detail endpoint is the deliberate access path.

### 12.3 Time budget and release evidence decisions

The inspected OpenAI, Deepgram and ElevenLabs adapters directly await tool execution, and Google Live uses a tool-adapter execution path. Therefore the service owns its deadline/cancellation/journaling contract; changing only `max_execution_time` is insufficient. Use the 15-second internal/20-second definition budgets in section 7.4 and separately bounded background checks in section 7.3. [OpenAI adapter](../src/tools/adapters/openai.py), [Deepgram adapter](../src/tools/adapters/deepgram.py), [ElevenLabs adapter](../src/tools/adapters/elevenlabs.py), [Google Live](../src/providers/google_live.py)

Remaining release checks are measurable tests: current token UI/scopes, paid booking permission, marker round-trip, supported field payloads, invitation receipt, host-calendar placement/conflict behavior, native voice behavior, and deployment failure recovery. Mark each supported variant as documentation-supported, mocked-tested, and live-qualified separately. A failed optional conference-kind fixture removes that kind from support; a failed marker fixture uses the documented manual fallback. A failed basic booking/invitation flow blocks release.

The complete earlier onboarding/calendar-management vision remains a possible follow-up. It is not an acceptance requirement for this release. The original research phase performed no implementation or live test; subsequent authorized implementation and qualification are summarized above.
