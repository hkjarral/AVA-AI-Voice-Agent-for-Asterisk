# Microsoft Calendar booking and invitations

`microsoft_calendar` books appointments in a configured Microsoft 365 calendar,
optionally includes caller-confirmed attendees, and can cancel or reschedule a
booking created during the **same call**. Changes to bookings from earlier calls
require staff. A spoken name, phone number, email, or Graph event ID does not
authorize changing an earlier booking.

## Supported deployment

- Microsoft 365 work/school accounts with an Exchange mailbox accessible through
  Graph, using an explicit Entra tenant. The Admin UI supports the `default`
  account. Runtime legacy named account bindings remain Agent-scoped; an
  explicit `account_key` is required for mutations with multiple selected accounts.
- Delegated device-code OAuth; no client secret or redirect endpoint.
- Existing scopes: `User.Read` (identify the signed-in user) and
  `Calendars.ReadWrite` (calendar operations and invitations). MSAL adds
  `offline_access`, `openid`, and `profile`; do not pass reserved scopes to MSAL.
- Personal Outlook.com, group/resource/shared mailbox workflows, automatic staff
  attendees, recurring series changes, Teams links, reminders sent by AVA, and
  delivery/acceptance tracking are outside this release. Calendar visibility and
  Verify do not establish every mailbox/license capability; use the acceptance
  test below before rollout.

## Configure in Admin UI

1. Register a single-tenant app in Microsoft Entra. Enable **Allow public client
   flows**. Grant delegated `User.Read` and `Calendars.ReadWrite`; consent must
   satisfy the tenant's policy. Do not add `Mail.Send` for calendar invitations.
2. **Tools → Microsoft Calendar**: enter the tenant/client IDs and choose
   **Connect**. Follow the device-code link and authorize the scheduling user.
   Select the desired **named calendar**, set its IANA timezone, and **Verify**.
   Verify checks identity, calendar visibility, and rejects calendars explicitly
   marked read-only. It creates no event or invitation.
3. Configure hours, working days, duration and booking horizon. For the scoped
   example: `America/Phoenix`, Monday–Friday, **08:00–17:00**, 30 minutes.
   Hours guide suggestions. Explicitly enable **Enforce working hours and booking
   horizon** to apply them to exact checks, creation and rescheduling. It defaults
   off for existing installations; the suggestion start remains 09:00 until configured.
4. Enable **Allow caller invitations** when desired. The default is disabled;
   appointment-only installations do not acquire attendees implicitly.
5. Edit business/location/contact details and subject/body templates. Preview
   uses synthetic Phoenix booking data and never sends an invitation.
6. **Agents → Tools**: enable `microsoft_calendar` and choose the permitted
   calendar account(s). The existing access policy is authoritative. The
   runtime adds booking guidance when this tool is enabled; caller emails are
   per-booking arguments, not saved Agent settings. Existing saved business
   prompts should be reviewed separately before deployment.
7. **Save & Apply** applies tool configuration to new calls. Installing Python
   code requires an AI engine restart; active calls retain their captured
   configuration. Do not upgrade in the middle of a booking conversation.

## Upgrading existing installations

Keep the existing OAuth connection, token cache, user identity and saved calendar
ID. This upgrade requires no database migration or calendar re-registration.
Verify reads the explicitly configured calendar directly, so a different ID
representation in calendar discovery does not reject an otherwise valid saved
ID. It never selects a different/default calendar or matches by name. Calendar
discovery excludes immutable-ID preferences; event reads, pagination and writes
retain them. Missing, forbidden and explicitly read-only calendars still fail.
Reconnect only when authorization actually expires or is revoked, rather than
as an upgrade workaround.

The configured `user_principal_name` must match the MSAL cached account username.
New connections save that canonical username, even when Graph mail or the sign-in
claim uses an alias. If an older configuration contains an alias or manually
entered identity that does not match the cache, verification returns
`account_identity_mismatch` rather than claiming the token expired. An operator
must confirm the intended account and correct this field to its cached username,
then Verify again; the existing cache and calendar ID can remain unchanged. Do
not substitute a username merely to bypass this check. An empty/expired cache
still requires reconnecting. Runtime never falls back to a different cached
identity, even when it is the only account in the cache.

When `enforce_booking_limits` is absent or false, existing working-hour values
continue to guide suggestions, while exact checks, creation and rescheduling
allow other hours/days and dates beyond the displayed horizon. Enable the switch
in Tools after reviewing the intended policy. Disabling it retains the saved
hours/horizon values. Maximum duration remains a separate setting (default 240
minutes, as before). Invitations stay off until explicitly enabled; upgrading or
opening the form neither adds attendees nor changes the saved calendar ID.

Existing appointment-only creation arguments remain supported. Multiple distinct
appointments can be booked during one call, with separate agreement for each.
Runtime calendar guidance reaches both full-agent providers and modular/hybrid
pipelines using the call's captured Agent account scope and tool allowlist;
saved business prompts are not rewritten.

The following safeguards intentionally affect older integrations: new bookings
must use future, whole-minute, unambiguous times; availability and conflict checks
use the selected calendar rather than mailbox-wide schedules; cancellation and
rescheduling require explicit agreement and current-call ownership. Earlier-call
or untracked bookings require staff. Generic event reads omit invitation bodies
to protect caller notes. Integrations that used arbitrary-ID deletion or relied
on private body text must adapt their workflow before rollout.

Restart the engine outside active booking calls. Existing Outlook events remain
in place. A rollback needs no ID/cache conversion; prior code ignores the new
opt-in settings, and staff must handle changes whose in-memory ownership was lost.

## Caller conversation

1. Determine the desired appointment date, time, timezone, duration and purpose.
   Resolve “tomorrow” from the calendar-local clock in runtime guidance. Clarify
   ambiguous requests and read back an explicit date/time.
2. Call **`check_availability`** using `start_datetime` and `end_datetime` for the
   exact requested interval. Offer alternatives only if it is busy or outside
   enforced working hours. Invalid/past times and times beyond an enabled horizon
   must be corrected first.
3. If the caller wants an invitation and invitations are enabled, ask them to
   spell each email, read it back and obtain agreement to create the meeting
   and send invitations. Syntax validation does not verify mailbox ownership
   or deliverability. Confirm only relevant notes the caller agrees to share.
4. Call `create_event`. The tool rechecks the chosen calendar immediately before
   creation. Pass `booking_confirmed: true`; invitations also require
   `invitation_confirmed: true` and `attendee_emails`.
5. Report the structured result. Never promise a booking before success. If
   invitees were supplied, say the booking was created and Microsoft accepted
   the invitation request. Do not claim it arrived or was accepted.

```json
{
  "action": "create_event",
  "summary": "Consultation",
  "start_datetime": "2026-10-05T13:00:00-07:00",
  "end_datetime": "2026-10-05T13:30:00-07:00",
  "attendee_emails": ["caller@example.com"],
  "booking_confirmed": true,
  "invitation_confirmed": true,
  "caller_name": "Example Caller",
  "meeting_purpose": "Service consultation",
  "confirmed_notes": "Discuss service options."
}
```

If the caller declines invitations, omit attendees and use an appointment-only
booking. `invitation_confirmed: true` without an email is rejected. Existing
appointment-only create calls without the new confirmation flags remain
compatible while invitations are disabled; explicit `booking_confirmed: false`
always blocks creation. New conversation guidance requires agreement for every
booking. Confirmation flags are the agent's assertion of caller agreement,
not independent proof from a recording.

## Availability and time handling

Availability reads **the selected calendar's paginated `calendarView`**, the same
calendar targeted by creation, rescheduling and cancellation. Mailbox-level
`getSchedule` does not select a named calendar and is no longer used for booking
availability. Busy, tentative, out-of-office, working-elsewhere and unknown
statuses block bookings; cancelled/free events do not. Malformed intervals and
unavailable calendars fail closed.

With blank `free_prefix`, available intervals are suggestion/booking-policy windows
minus busy calendar events. With a configured prefix, matching events define open windows,
clipped to the applicable policy windows; other non-free events also block bookings even without
a “Busy” title. The model cannot enable prefix mode when the operator chose
blank. Both availability actions and mutations use the operator's configured
prefixes; legacy tool arguments cannot override them.

`get_free_slots` returns grid-based suggestions, normally the first three. It
includes `slots_returned`, `total_slots_available` and `slots_truncated`. Even a
complete suggestion list does not represent every possible off-grid start.
**Absence from suggestions is never proof that a requested interval is busy.**
For example, 08:00/08:30/09:00 plus `slots_truncated: true` says nothing about
13:00; check 13:00–13:30 explicitly. Queries are bounded to 93 days; split a wider
search into smaller ranges. Suggestions omit past intervals and, when enforcement
is enabled, intervals beyond the horizon.

ISO offsets are respected, naive times use the configured IANA timezone, and
Graph is requested in UTC. Invalid timezones, date-only values, subminute times,
nonexistent daylight-saving times and ambiguous naive times are rejected.
Ambiguous local times need an explicit UTC offset. End must be after start.
With enforcement enabled, 16:30–17:00 is allowed under 08:00–17:00,
while 16:45–17:15 is not.

## Invitation templates

Graph receives the rendered subject and a **plain-text** `body.content`. No
separate email provider is used. Supported `{{placeholders}}`:

| Source | Placeholders |
| --- | --- |
| Caller-confirmed tool arguments | `caller_name`, `meeting_purpose`, `confirmed_notes` |
| Operator settings | `business_name`, `location`, `contact_details`, `rescheduling_instructions` |
| Validated booking interval | `appointment_date`, `start_time`, `end_time`, `timezone_label` |

`summary` is the fallback purpose and `description` the fallback notes. Unknown
or malformed placeholders and oversized content are rejected before creation.
Replacement runs once: caller text containing `{{...}}` is treated as literal
text, never executable content or another template. Subject limit: 255
characters; rendered body: 8,000; at most ten attendee addresses, deduplicated
case-insensitively. Caller email formats outside the supported ordinary ASCII
address syntax require staff assistance.

Generic `get_event` reads return the title, interval and calendar identifier,
but never the invitation body, which may contain another caller's private notes.

Rescheduling refreshes date/time placeholders in both the invitation subject
and body while preserving the confirmed caller details and attendees.

## Same-call cancellation and rescheduling

- Each current-call booking is tracked independently. Obtain explicit agreement to cancel
  or reschedule the exact booking, including updates/cancellation notices to
  existing attendees. Do not expose opaque Graph IDs in speech.
- **Cancel:** `{"action":"delete_event","cancellation_confirmed":true}`.
  Omit `event_id` for the most recently selected booking, or pass an ID returned
  for another booking created during this call. The tool verifies current-call
  ownership and account/calendar binding. An untracked ID is rejected without
  trying it. Arbitrary older event IDs are not accepted for mutations.
- **Reschedule:** call `reschedule_event` with new start/end and
  `booking_confirmed:true`, selecting the booking as above. The tool checks the new interval,
  excludes its own event, and PATCHes the same event. It preserves attendees and
  updates the template's date/time wording. A busy new interval leaves the
  original event intact. Do not delete then create.
- Rescheduling changes time only. Changing attendees, purpose, or other details
  requires staff. If Outlook changed the invitation body or enabled an online
  meeting, the tool refuses to overwrite it. Recurring bookings and meetings
  this calendar does not organize also require staff.
- Organizer meeting deletion causes Microsoft to generate cancellation notices.
  A repeated cancellation does not send another DELETE. A missing event is
  reported as no longer present; cancellation-message delivery remains unknown.
- Later-call requests, lost/evicted same-call tracking, and unverifiable changes
  go to staff under the existing business policy. This does not authorize an
  immediate human transfer: qualification, business-hours and after-hours
  message/emergency rules remain unchanged.
- A cancelled identical booking cannot be blindly recreated using its old
  retry identity; staff can rebook it. A changed slot can be booked anew after
  cancellation, with fresh caller agreement.

Cancellation and rescheduling send the freshly observed event ETag in `If-Match`.
A missing, blank, non-string or wildcard version and a 412 precondition failure
require staff; there is no unconditional retry. Refusing an unversioned
reschedule creates no pending operation; a later attempt with a concrete version
can proceed after renewed confirmation. Subject/time/attendee/body edits detected
before the request also stop the change. Provider enforcement of conditional event requests must be checked
in the approved mailbox acceptance test; this does not establish a distributed
reservation guarantee.

## Failures, concurrency and restart recovery

Creation supplies a deterministic Graph `transactionId` based on the call,
account/calendar binding and normalized booking content. Repeating identical
arguments first looks for the existing transaction in the selected interval.
If found, it reports reconciliation without another POST. This works after a
process restart when the original call ID and arguments remain available.
Later calls have different call IDs and do not gain mutation authority.

Within a process, mutation workers share a lock through read/check/write/state
tracking, including when an already-dispatched write's awaiting coroutine is
cancelled. Lock acquisition waits at most ten seconds; a contended attempt
returns `calendar_busy` without reading or changing the calendar. Cancellation and
expiry are checked before acquiring the lock and before requesting each write;
the 30-second deadline starts before executor submission. An in-flight HTTP write cannot be undone by cancellation,
so the worker retains the lock and reconciles its result as before. Competing AVA
calls recheck the slot serially. This is **not a distributed reservation lock**;
other engine processes or Outlook users can still race the check. Graph event
creation is not an atomic “create only if free” operation. Use one scheduling
engine or external resource arbitration if exclusive booking is required.

A transport timeout, 5xx response or malformed success result after a write is
`mutation_uncertain`, with reservation/invitation/delivery state unknown. Retry
identical arguments to reconcile. If creation is still not visible, ask staff;
do not change arguments and create another reservation. PATCH retries inspect
the current start/end to reconcile an applied update; an unobserved pending
update requires staff rather than blindly sending another notification. Pending changes block a
different mutation. After a restart without tracked change state, staff must
verify Outlook before any cancellation/reschedule.

Expired authorization requires reconnecting through Tools; forbidden/wrong
calendar/read-only errors require operator correction. These preflight failures
do not create an event. HTTP diagnostics are mapped to safe agent messages;
attendee lists/purpose/notes are redacted under the existing history policy.
MSAL refresh remains file-locked, and the token cache remains owner-only (0600).
The client refuses foreign Graph pagination links and does not silently choose
a different cached user when the configured identity cannot be found.

| Result field | Meaning |
| --- | --- |
| `reservation_status` | `created`, `rescheduled`, `cancelled`, or `unknown` |
| `invitation_status` | `request_accepted`, `cancellation_request_accepted`, `not_requested`, or `unknown` |
| `delivery_status` | `unknown` for requested messages; `not_applicable` without invitees |
| `attendee_acceptance_status` | `unknown` for invitees; `not_applicable` without invitees |
| `reconciled` | Success was recovered/read back instead of repeating the write |

These fields and exact availability survive provider result sanitization.
Attendee response tracking and delivery receipts are not implemented.

## Example operator configuration

```yaml
tools:
  microsoft_calendar:
    enabled: true
    invitations_enabled: false  # operator opts in after test-mailbox approval
    free_prefix: ""
    busy_prefix: Busy
    min_slot_duration_minutes: 30
    max_slots_returned: 3
    max_event_duration_minutes: 240
    enforce_booking_limits: true  # explicit adoption of hours/days/horizon
    booking_horizon_days: 365
    working_hours_start: 8
    working_hours_end: 17
    working_days: [0, 1, 2, 3, 4]
    business_name: Example Business
    location: Phone consultation
    contact_details: Contact our office
    rescheduling_instructions: Call our office for staff assistance with later changes.
    invitation_subject_template: "{{business_name}}: {{meeting_purpose}}"
    accounts:
      default:
        tenant_id: example.onmicrosoft.com
        client_id: 11111111-1111-1111-1111-111111111111
        token_cache_path: /app/project/secrets/microsoft-calendar-default-token-cache.json
        user_principal_name: scheduler@example.com
        calendar_id: named-calendar-id
        timezone: America/Phoenix
```

Zero disables the duration/horizon limit. Empty working days or invalid hours
fail closed wherever those settings apply; invalid timezones always fail closed. Configuration supplies no production credentials
in this repository. No database migration is required; same-call tracking is a
bounded in-memory cache, not an authorization mechanism for later calls.

## Acceptance plan and rollout

For v7.6.2, the maintainer confirmed this acceptance plan tested and validated
on 2026-10-02, including the stale-ETag cancellation scenario. The
[release matrix](baselines/golden/v7.6.2-validation-matrix.md) records this as
maintainer-confirmed evidence without publishing mailbox/recipient details or
inventing a call ID/test revision. This is not blanket tenant qualification or
authorization to change a production calendar; the per-installation rollout
requirements below remain applicable.

Automated tests use only synthetic fixtures/mocked Graph HTTP. Before production:

1. Obtain separate explicit approval identifying a **test organizer mailbox,
   named test calendar, recipient address, subject, date/time and invitation**.
   Connect/verify only that approved test account; enable invitations there.
2. Configure Phoenix weekdays 08:00–17:00 and 30 minutes; enable hours/horizon
   enforcement. Request 13:00 after a
   day query yields only three morning suggestions. Confirm exact availability,
   email readback and consent. Inspect exactly one event in the named calendar.
3. Inspect the test recipient's mailbox separately for the invitation, correct
   body and timezone; record observed delivery rather than inferring it from
   tool success. Accept/decline manually and verify the agent still reports
   acceptance unknown unless separately observed by staff.
4. In the same call, move to 14:00. Verify the same event ID, retained attendee,
   updated body and update message. Cancel it and inspect the cancellation.
5. Test invitation refusal, malformed email, missing consent, occupied slots,
   closing boundary, authorization expiry, timeout/retry and later-call change
   refusal. Use synthetic errors; do not invalidate production credentials.
6. On a synthetic event in the approved test calendar, read its ETag, edit it
   externally, then attempt DELETE with the stale ETag. Verify Graph rejects it
   with 412 and retains the event; confirm the tool routes the conflict to staff.
   Do not enable automated cancellation until this provider behavior is verified.
7. Verify the original saved calendar ID without an alias workaround after the
   approved test upgrade, including a non-default named calendar. Check repeated
   Verify/read-only availability with the existing cache; do not reconnect or
   rewrite an ID to mask a verification failure.
8. Record test evidence before approving rollout. Do not edit existing production
   appointments. No live invitation is authorized merely by this plan.

After merge approval, deploy the reviewed engine/backend/frontend together,
restart outside active calls, verify health and use the approved test plan.
First leave invitations disabled, then opt in for the intended business calendar.
Production deployment, prompt changes and merge remain separate decisions.

Rollback: disable invitations in Tools for new calls, or disable the calendar
tool and let staff handle requests. A code rollback restores prior behavior;
existing Graph events/invitations remain and must be handled deliberately by
staff. Older code cannot recover the new in-memory same-call state. Coordinate
rollback outside active booking calls; do not delete events as rollback cleanup.

## Microsoft references

- [Create event and automatic invitations](https://learn.microsoft.com/en-us/graph/api/user-post-events?view=graph-rest-1.0)
- [Immutable ID support excludes calendar containers](https://learn.microsoft.com/en-us/graph/outlook-immutable-id)
- [Event transactionId and immutable IDs](https://learn.microsoft.com/en-us/graph/api/resources/event?view=graph-rest-1.0)
- [Selected calendarView](https://learn.microsoft.com/en-us/graph/api/calendar-list-calendarview?view=graph-rest-1.0)
- [Update event](https://learn.microsoft.com/en-us/graph/api/event-update?view=graph-rest-1.0)
- [Organizer deletion and cancellation notices](https://learn.microsoft.com/en-us/graph/api/event-delete?view=graph-rest-1.0)
- [Device authorization grant](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)
