# Google Calendar tool

The **google_calendar** tool lets the AI voice agent interact with Google Calendar: list events, get a single event, create events, delete events, and find free appointment slots (with duration and slot alignment).

## Prerequisites / Setup

### 1. Enable the Google Calendar API

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or select an existing one).
3. Navigate to **APIs & Services > Library**.
4. Search for **Google Calendar API** and click **Enable**.

### 2. Create a Service Account

1. In the Cloud Console, go to **APIs & Services > Credentials**.
2. Click **Create Credentials > Service Account**.
3. Give it a name (e.g. `asterisk-calendar`) and click **Create**.
4. Skip the optional role/access steps and click **Done**.
5. Click on the newly created service account, go to the **Keys** tab.
6. Click **Add Key > Create new key > JSON** and download the key file.
7. Place the JSON key file somewhere accessible to the Asterisk AI Voice Agent (e.g. `credentials/google-calendar-sa.json`).

### 3. Share Your Calendar with the Service Account

1. Open [Google Calendar](https://calendar.google.com/) in a browser.
2. Find the calendar you want the agent to use in the left sidebar.
3. Click the three-dot menu next to the calendar name and select **Settings and sharing**.
4. Under **Share with specific people or groups**, click **Add people and groups**.
5. Enter the service account email (found in the JSON key file as `client_email`, looks like `name@project.iam.gserviceaccount.com`).
6. Set the permission to **Make changes to events** (so the agent can create bookings).
7. Click **Send**.
8. Copy the **Calendar ID** from the **Integrate calendar** section (looks like `abc123@group.calendar.google.com`, or use `primary` for the main calendar).

### 4. Set Environment Variables

Add these to your `.env` file:

```bash
GOOGLE_CALENDAR_CREDENTIALS=credentials/google-calendar-sa.json
GOOGLE_CALENDAR_ID=abc123@group.calendar.google.com
GOOGLE_CALENDAR_TZ=America/New_York  # Your calendar's timezone
```

### 5. Enable in the Admin UI

1. Open the Admin UI and go to the **Tools** section.
2. Toggle **Google Calendar** to enabled.

Or set it directly in `config/ai-agent.yaml`:

```yaml
tools:
  google_calendar:
    enabled: true
```

### 6. Add to Your Context

Make sure `google_calendar` is in the tools list for the context(s) that should have calendar access:

```yaml
contexts:
  my_context:
    tools:
      - google_calendar
      - hangup_call
      # ... other tools
```

## Implementation

- **`gcal_tool.py`** -- Tool definition and execution (actions, config, slot logic).
- **`gcalendar.py`** -- Low-level Google Calendar API client (`GCalendar`).

## Dependencies

The tool requires the `google-api-python-client` package. It is already listed in the project's `requirements.txt`, but if you are installing manually:

```bash
pip install google-api-python-client>=2.0.0
```

## Environment

| Variable | Description |
|----------|-------------|
| `GOOGLE_CALENDAR_CREDENTIALS` | Path to the service account JSON key file (required). |
| `GOOGLE_CALENDAR_ID` | Calendar ID (default: `primary`). |
| `GOOGLE_CALENDAR_TZ` | Timezone for operations (fallback: `TZ`, then system/UTC). |

## Why `get_free_slots` is in this tool

AI models are generally weak at handling large datasets and at carrying out precise logical operations on them (e.g. interval arithmetic, consistent time alignment). If we fed the model a long list of raw calendar events and asked it to compute "free slots," we'd risk mistakes, inconsistency, and heavy token use. So the tool does that work in code and returns a small, deterministic list of slot start times the model can simply read out and act on.

The Google Calendar API only returns a list of events. For appointment booking over the phone, the agent needs to answer "When are you free?" with concrete, bookable start times. **`get_free_slots`** does that by:

1. **Interpreting the calendar** -- Events whose titles start with `free_prefix` (e.g. "Open") are treated as available windows; events with `busy_prefix` (e.g. "Busy" for a booked slot) are treated as blocked. The tool subtracts busy blocks from free blocks to get truly available intervals.
2. **Duration and alignment** -- It returns only start times where a slot of the requested length (e.g. 30 minutes) fits, and aligns those starts to round times (e.g. :00 and :30 for 30-minute slots). That avoids half-off times and gives the AI a short list of times it can read out naturally (e.g. "I have 2pm, 2:30pm, and 3pm").

So instead of the LLM having to fetch raw events and infer availability and alignment, this tool provides ready-to-say slot starts and supports creating the booking with `create_event` in the same flow.

## Config (ai-agent.yaml / Admin UI)

Under `tools.google_calendar`:

| Key | Description | Default |
|-----|-------------|---------|
| `enabled` | Turn the tool on or off. | `false` |
| `free_prefix` | Prefix for events that define available windows (e.g. `"Open"`). When non-empty, the tool uses **title-prefix mode** and only events titled with this prefix count as available time. When blank (`''`) or absent, the tool uses **free/busy mode** (Google's native `freebusy.query()` API) intersected with a working-hours mask. The LLM may override the prefix string within title-prefix mode but cannot switch modes — the operator's setting wins. Now exposed in the Tools UI. | *(blank → free/busy mode)* |
| `busy_prefix` | Prefix for events that define booked slots (e.g. `"Busy"`). Only used in title-prefix mode. The LLM can override this per-call. Now exposed in the Tools UI. | `"Busy"` (backend fallback, title-prefix mode only) |
| `min_slot_duration_minutes` | Default appointment duration in minutes for `get_free_slots`. | `30` (backend default and Admin UI default for new entries) |
| `max_slots_returned` | Cap on number of slot start-times surfaced to the LLM by `get_free_slots`. Set to `0` to disable the cap. | `3` |
| `max_event_duration_minutes` | Refuse `create_event` calls that book a longer event. Set to `0` to disable the cap. | `240` |
| `working_hours_start` / `working_hours_end` / `working_days` | Working hours mask used in free/busy mode. Hours are 24h calendar-local; days use Python `weekday()` convention (Mon=0..Sun=6). | `9` / `17` / `[0,1,2,3,4]` |
| `calendars` | Map of named calendars (multi-account support). Each entry can set `credentials_path`, `calendar_id`, `timezone`, `subject` (DWD). | *(optional)* |

**Mode-selection rule (canonical):** the operator's `free_prefix` setting
determines mode and always wins over any LLM-supplied value:

- `free_prefix: 'Open'` (or any non-empty string) → **title-prefix mode** —
  `get_free_slots` scans the calendar for events titled with the prefix.
  The LLM may pass a different prefix to use within title-prefix mode, but
  cannot switch out of it.
- `free_prefix: ''` (explicit blank) or no `free_prefix` key at all →
  **free/busy mode** — `get_free_slots` calls Google's native `freebusy.query()`
  intersected with the configured working-hours mask. Operators don't need
  to seed "Open" events. Even if the LLM passes `free_prefix='Open'`, the
  operator's deliberate choice (or default) of free/busy wins.

**Defaults note:** when the operator config omits `busy_prefix` or
`min_slot_duration_minutes`, the backend falls back to `"Busy"` and `30`
minutes respectively. `free_prefix` does NOT have a backend default — its
absence is the explicit signal for free/busy mode.

> **Behavior change in this release:** the backend default for
> `min_slot_duration_minutes` was previously `15`; it is now `30`, matching
> the UI default and what the docs always advertised. YAML configs that
> explicitly set `min_slot_duration_minutes` keep their value; only configs
> that omit the key see the change.

Example (single or multiple calendars):

```yaml
tools:
  google_calendar:
    enabled: true
    free_prefix: "Open"
    busy_prefix: "Busy"
    min_slot_duration_minutes: 30
    calendars:
      work:
        credentials_path: credentials/work-sa.json
        calendar_id: abc@group.calendar.google.com
        timezone: America/Denver
      personal:
        credentials_path: credentials/personal-sa.json
        calendar_id: primary
        timezone: America/Denver
```

### Per-context calendar selection

Each context binds to **exactly one calendar**. This keeps the routing
unambiguous: when the caller says "book me for 2pm," the agent always
knows which calendar the event belongs to.

In the Admin UI (Contexts → Edit Context → Google Calendar), pick one
calendar. The others become disabled until you clear the selection.

Equivalent YAML:

```yaml
contexts:
  sales:
    tools:
      - google_calendar
    tool_overrides:
      google_calendar:
        selected_calendars: [work]   # single entry — the UI enforces this
```

**Missing vs. empty `selected_calendars`:**

| `selected_calendars` value | Behavior |
|----------------------------|----------|
| Omitted (not present) | Context uses **all** configured calendars (legacy / single-calendar default). |
| `[calendar_key]` | Context uses that one calendar. **Recommended.** |
| `[]` (empty list) | No calendars available to this context — all calendar actions return an authorization error (fail-closed). |

- If `calendars` is omitted at the tool root but env vars are set, the tool will auto-materialize `calendars.default` from `GOOGLE_CALENDAR_*` and use it.

### Power-user: cross-calendar availability via YAML

The UI constrains each context to one calendar because that matches how
99% of deployments use the tool. However, the backend still supports
multiple `selected_calendars` entries for one specific use case:
**aggregating availability across multiple calendars in `get_free_slots`**
(e.g. "find a time when both my work and personal calendars are free").

This has to be set up in YAML — the UI will not produce a multi-calendar
selection, and editing a context in the UI after setting this will reset
it to single-select.

```yaml
contexts:
  unified_assistant:
    tools:
      - google_calendar
    tool_overrides:
      google_calendar:
        selected_calendars: [work, personal]   # YAML-only — not representable in UI
```

When multiple calendars are selected:

- `get_free_slots` aggregates across all of them. `aggregate_mode: all` (default) returns times free on every calendar; `aggregate_mode: any` returns times free on any calendar.
- `list_events` merges events from all selected calendars.
- `create_event`, `delete_event`, `get_event` fall back to the first calendar in the list when the LLM doesn't pass `calendar_key` — this is why the UI forces single-select, to avoid the LLM silently picking a default.

If you use multi-calendar YAML, the LLM needs `calendar_key` to be
explicit for create/delete actions, so you must prompt it with the
available calendar keys in your context instructions (e.g. "Available
calendars: `work`, `personal`. Use `calendar_key` to specify which one
for any booking.").

## Actions

In the normal (UI-configured) case, each context has exactly one
selected calendar, so the LLM does not need to pass `calendar_key` —
the tool uses the context's single calendar automatically. The
`calendar_key` and `aggregate_mode` parameters below only matter for
the multi-calendar YAML setup described above.

| Action | Purpose |
|--------|--------|
| `list_events` | List events in a time range (`time_min`, `time_max`). With one calendar selected, returns events from that calendar. With multiple (YAML only), aggregates across all selected calendars; pass `calendar_key` to target a specific one. |
| `get_event` | Get one event by `event_id`. Uses the context's single calendar, or pass `calendar_key` for multi-calendar YAML setups. |
| `create_event` | Create event with `summary`, `start_datetime`, `end_datetime` (optional `description`). Uses the context's single calendar; in multi-calendar YAML setups, pass `calendar_key` to target a specific one (otherwise falls back to the first selected calendar). |
| `delete_event` | Delete an event by `event_id`. Uses the context's single calendar, or pass `calendar_key` for multi-calendar YAML setups. |
| `get_free_slots` | Return start times where a slot of given `duration` (minutes) fits. Uses `free_prefix` / `busy_prefix` to compute available intervals. With multiple calendars selected (YAML only), aggregates via `aggregate_mode`: `all` (default) = intersection (time is free on every calendar), `any` = union. Pass `calendar_key` to constrain to one calendar. Slot starts are aligned to multiples of `duration`. |

All times use ISO 8601. The tool is registered as `google_calendar` and is in the **business** tool category.

### `get_free_slots` response shape

`get_free_slots` returns a structured response so the LLM (and any downstream
prompt logic) can react appropriately to the two distinct empty-result cases:

```json
{
  "status": "success",
  "message": "Free 30-minute slots (America/Los_Angeles): 2026-04-27 09:00–09:30, 09:30–10:00, 10:00–10:30. All times are in America/Los_Angeles. When booking via create_event, pass start_datetime and end_datetime as the SAME local-time strings (no Z, no offset — calendar timezone is implicit) and set end_datetime = start_datetime + 30 minutes. (showing 3 of 28 available; propose 2-3 of these to the caller — do not read the full list).",
  "slots": ["2026-04-27T09:00:00-07:00", "2026-04-27T09:30:00-07:00", "2026-04-27T10:00:00-07:00"],
  "slots_with_end": [
    {"start": "2026-04-27T09:00:00-07:00", "end": "2026-04-27T09:30:00-07:00"},
    {"start": "2026-04-27T09:30:00-07:00", "end": "2026-04-27T10:00:00-07:00"},
    {"start": "2026-04-27T10:00:00-07:00", "end": "2026-04-27T10:30:00-07:00"}
  ],
  "slot_duration_minutes": 30,
  "calendar_timezone": "America/Los_Angeles",
  "open_windows_found": true,
  "busy_blocks_found": false,
  "reason": "available",
  "availability_mode": "freebusy",
  "total_slots_available": 28,
  "slots_truncated": true,
  "calendars_without_open_windows": []
}
```

The `reason` field is one of:

| `reason` | Meaning | Suggested LLM behavior |
|---|---|---|
| `available` | Slots returned. `slots` is non-empty. `message` includes a duration nudge and the calendar timezone. | Read back at most 2-3 options in plain language. Never read the entire `slots` list aloud. |
| `fully_booked` | Open availability windows (or working-hours mask in free/busy mode) exist for the requested range but are entirely covered by Busy blocks. `slots` is empty. | Apologize and suggest a different day. |
| `no_open_windows` | No availability for the range. In title-prefix mode, no events titled with `free_prefix` exist. In free/busy mode, the requested time range falls outside configured working hours. `slots` is empty. | Tell the caller you don't see availability; offer to take a message or try a different date range. |

`availability_mode` is one of:

| `availability_mode` | Means | When it triggers |
|---|---|---|
| `title_prefix` | Title-prefix mode — the tool scans event titles for events starting with `free_prefix` (default `'Open'`) for available windows, minus events starting with `busy_prefix` (default `'Busy'`). | When `free_prefix` is configured (truthy after strip) — either via Tools UI or YAML, or passed by the LLM. |
| `freebusy` | Native Google free/busy mode — the tool calls Google's `freebusy.query()` API for busy intervals and intersects with a working-hours mask (default Mon–Fri 09:00–17:00 in calendar tz). No need to seed "Open" events. | When `free_prefix` is blank/empty in config (operator deliberately cleared it in the UI) AND the LLM doesn't pass a value either. |

The `slots` array is normalized to ISO-8601 strings with explicit calendar
timezone offsets (e.g. `2026-04-27T09:00:00-07:00`). The `message` string
includes a human-readable form (`2026-04-27 09:00–09:30`) plus the calendar
timezone name and an explicit duration instruction so models that read the
message field don't fabricate end times.

The `total_slots_available` and `slots_truncated` fields surface the cap
behavior — without a cap, a multi-day open window can return 20–50+ slot
starts and an LLM may read the full list aloud (multiple minutes of robotic
monologue). The cap is configurable via `tools.google_calendar.max_slots_returned`
in the Tools UI; default is `3`. Set to `0` (or any value `≤ 0`) to disable
the cap.

`open_windows_found` and `busy_blocks_found` surface the underlying counts
directly — useful if a custom downstream tool wants to distinguish these
without parsing `reason`. `calendars_without_open_windows` (multi-calendar
aggregations only) names which selected calendars contributed zero open
windows — useful in `aggregate_mode='all'` mode when the intersection is
empty because of one calendar specifically.

### Native free/busy mode (no `free_prefix` configured)

If you leave **Free prefix** blank in the Tools UI (or omit `free_prefix`
from YAML), `get_free_slots` switches to Google's native free/busy API and
synthesizes available windows from a working-hours mask intersected with
busy events. Operators don't need to seed "Open" availability events.

Working hours can be tuned via these YAML keys under `tools.google_calendar`
(default Mon–Fri 09:00–17:00 in the calendar's local timezone):

```yaml
tools:
  google_calendar:
    working_hours_start: 9          # 24h, calendar-local
    working_hours_end: 17           # 24h, calendar-local
    working_days: [0, 1, 2, 3, 4]   # Python weekday() — Mon=0..Sun=6
```

Existing configs with `free_prefix: 'Open'` (or any non-empty value) keep
title-prefix mode — the mode switch is implicit on `free_prefix` being
blank, so this is fully back-compatible.

### Event duration cap on `create_event`

`create_event` refuses bookings with non-positive duration (`error_code:
invalid_duration`) or duration > 240 minutes (`error_code: duration_too_long`).
This is a server-side guard against an LLM bug seen in live testing where
the model picked the right slot start but extended `end_datetime` to fill
the rest of the post-start availability window — booking a 7-hour meeting
instead of 30 minutes. The guard returns a fail-fast error with retry
instructions in the message, so the model self-corrects.

Configurable via `tools.google_calendar.max_event_duration_minutes` (default
`240`). Set to `0` to disable the cap.

### `create_event` success — `event_id` surfaced for delete-then-recreate

The success response includes the event id in BOTH the structured `id`/`event_id`
fields AND the `message` string itself, with explicit instruction to copy
verbatim:

```json
{
  "status": "success",
  "message": "Event created with id 'XYZ'. To modify or delete this event later (e.g. if the caller corrects the time), call delete_event with this exact event_id — do not invent or guess one.",
  "id": "XYZ",
  "event_id": "XYZ",
  "link": "https://...",
  "calendar": "calendar_1"
}
```

Without the prominent message-level mention, models like Gemini have been
observed fabricating plausible-looking event ids when asked to delete a
booking after a caller correction (real bug from live testing). Pinning the
id in the message field — which most providers expose to the model verbatim
— addresses this at the prompt level.

As a server-side safety net, `GCalendarTool` also tracks the most-recent
successful `create_event` per `call_id`. If `delete_event` returns 404 with
a hallucinated id and we have a real id from the same call, the tool falls
back to deleting that one and returns success with a message explaining the
recovery. Tracking is cleared on first delete so a stale id can't be matched
twice. This means the **delete-then-recreate** flow on caller corrections is
robust even when models slip up on id verbatim copying.

## JSON upload (Admin UI) — recommended setup path

In the Tools page Google Calendar section, each calendar row has an
**📁 Upload JSON** button (or **Replace JSON** if a path is already set).
Clicking it opens a file picker; choose your service-account JSON and:

1. The file is uploaded to the admin_ui backend, validated as a Google SA
   key, and written to a stable path under `secrets/` keyed off a hash of
   the SA's `client_email`. The same SA always gets the same filename, so
   re-uploading after a private-key rotation overwrites the existing file
   and any references to it (in YAML, Contexts page, etc.) keep working.

2. The backend then authenticates as the SA and calls Google's
   `calendarList.list()` API to discover which calendars the SA has access
   to. Three outcomes:

   - **1 calendar accessible** (the 90% case): the row's `Credentials
     Path`, `Calendar ID`, and `Timezone` fields are auto-filled with the
     discovered calendar's details. A green ✓ confirmation appears.
     Click Save and you're done — no typing required.
   - **Multiple calendars accessible**: the path fills automatically; a
     dropdown picker appears letting the operator choose which calendar
     the row should target. Picking a calendar fills the remaining
     fields.
   - **Zero calendars accessible**: the path fills, but a yellow callout
     surfaces the SA email and instructs the operator to share their
     calendar with that email (with `Make changes to events` permission)
     and click **Replace JSON** to re-discover.

The legacy manual flow (SCP a JSON to the host's `secrets/` directory,
paste the in-container path into the form, share the calendar in
Google Calendar's UI, paste calendar ID + timezone, click Verify) still
works — the Credentials Path text field accepts any path the operator
types — but you should not need it for normal setups.

## Domain-Wide Delegation (DWD) — for Workspaces with external-sharing restrictions

If your Google Workspace admin has disabled external sharing for primary
calendars (the *"Only free/busy information"* policy at admin.google.com →
Apps → Google Workspace → Calendar → External sharing options), the
default flow of "share your calendar with the SA email" won't work — the
share UI will refuse to grant write access to the service-account
domain. DWD is the workaround: instead of sharing, the SA **impersonates**
a real user in your domain.

### When to use DWD

- Your Workspace admin policy blocks external sharing
- You want the SA to access multiple users' calendars without each user
  individually sharing with the SA
- You want events created by the SA to attribute to a real user, not the
  service account

### When NOT to use DWD

- Your calendar is shareable with the SA via the normal "Share with
  specific people" flow — DWD adds setup overhead for no benefit
- You're a Workspace admin who can simply loosen the external-sharing
  policy — that's the simpler fix

### Setup (operator-side)

1. **In Cloud Console** — open your service account → **Show domain-wide
   delegation** → enable it. Note the OAuth Client ID (NOT the email
   — this is the #1 setup pitfall).

2. **In admin.google.com** (you must be a Workspace admin):
   - Security → Access and data control → API controls
   - Click **Manage Domain Wide Delegation**
   - **Add new** with the Client ID from step 1 and scope:
     `https://www.googleapis.com/auth/calendar`
   - Save

3. **In AAVA Admin UI**:
   - Tools → Google Calendar → expand **🪪 Domain-Wide Delegation
     (advanced)** under your calendar row
   - Paste the email of the user you want the SA to impersonate (e.g.
     `you@yourdomain.com`)
   - Click out of the field — auto-verify fires; success returns
     ✓ Reachable, failure surfaces `dwd_not_configured` with the
     specific underlying error from Google's token endpoint

### Equivalent YAML

```yaml
tools:
  google_calendar:
    enabled: true
    calendars:
      work:
        credentials_path: /app/project/secrets/google-calendar-<hash>.json
        calendar_id: you@yourdomain.com
        timezone: America/New_York
        subject: you@yourdomain.com   # ← DWD: impersonate this user
```

The `subject` field is per-calendar so a single AAVA install can
impersonate different users for different calendars in the same tenant.

### Why client_id and email both appear in the Identity badge

The Identity badge shows two values, both with copy buttons:

- `client_email` — what you'd paste into the *standard* "Share with
  specific people" flow (the non-DWD path)
- `client_id` — the OAuth Client ID, what admin.google.com expects for
  Domain-Wide Delegation setup

These look superficially similar but are NOT interchangeable. Pasting
the email into the DWD form returns `unauthorized_client` from Google's
token endpoint at runtime; pasting the client_id into the calendar
share UI does nothing useful. AAVA shows both to make the distinction
unambiguous.

## Identity badge & Verify button (Admin UI)

In the Tools page Google Calendar section, each calendar entry shows:

- **Service Account email** — auto-loaded from the credentials JSON file.
  Click the copy icon and paste it into Google Calendar's "Share with
  specific people" UI to grant the SA access to your calendar.
- **Client ID** — also auto-loaded. **This is the OAuth client ID, NOT
  the email.** It's what you paste into admin.google.com → Security →
  Access and data control → API controls → Domain-wide delegation when
  setting up DWD. Mixing up email vs Client ID is the #1 DWD setup
  pitfall — surfacing both with copy buttons prevents it.
- **🩺 Verify access** button — POSTs current form state (including
  unsaved edits) to `/api/config/google-calendar/{key}/verify`, which
  uses raw `googleapiclient` to call `calendars.get(calendar_id)` as
  the SA and returns one of:
    - ✓ `Reachable: <calendar summary> (<actual timezone>)` — green
    - ⚠ Drift warning if the configured timezone doesn't match the
      calendar's actual timezone (silent footgun otherwise)
    - ✗ Specific error code: `forbidden_calendar` ("share calendar
      with the SA"), `calendar_not_found` ("wrong calendar id"),
      `auth_failed` ("bad credentials"), `dwd_not_configured`,
      `credentials_file_not_found`, etc.

## ElevenLabs Agent — extra setup step (tools registered on platform)

> **Read this if you're using the `elevenlabs_agent` provider.** Skip if you're
> only using `google_live`, `openai_realtime`, `deepgram`, or `local_hybrid`.

For `google_live`, `openai_realtime`, `deepgram`, and `local_hybrid`, AAVA
sends the tool schema to the provider over the websocket at session start.
Each call learns the available tools dynamically. Nothing extra to set up.

**ElevenLabs Conversational AI does not work that way.** Tools are registered
**ahead of time on the ElevenLabs platform**, attached to your specific
agent. At runtime, the agent emits `client_tool_call` events over the
websocket and AAVA executes them locally. If a tool isn't registered on
the agent, the LLM running on ElevenLabs' side literally doesn't know it
exists — it falls back to the prompt-instruction wording instead of calling
the tool. The most common failure mode in our live testing was the agent
saying *"the calendar tool is not configured at the moment, I suggest you
email the maintainer directly"* with no `client_tool_call` ever firing.

To use the calendar tool with ElevenLabs Agent you must register it once:

### Where

elevenlabs.io → **Conversational AI** → your Agent → **Tools** tab →
**Add Tool** → select **Client Tool** (not Webhook, not System).

### JSON to paste (recommended path — dashboard accepts this verbatim, or POST to the Tools API)

```json
{
  "type": "client",
  "name": "google_calendar",
  "description": "Use this tool to interact with Google Calendar to list events, get a specific event, create a new event, delete an event, or find free slots. Call get_free_slots first when scheduling a meeting (always pass time_min and time_max in ISO 8601 UTC, e.g. time_min='2026-04-27T09:00:00Z' time_max='2026-04-30T17:00:00Z'), then propose 2 to 3 specific times to the caller in plain language — never read the entire slots list aloud. Once the caller picks a time, call create_event with summary, description, start_datetime, and end_datetime to book it. If the tool returns error_code 'missing_parameters', retry with valid time_min and time_max — that is NOT a configuration error.",
  "disable_interruptions": false,
  "force_pre_tool_speech": false,
  "pre_tool_speech": "auto",
  "tool_call_sound": null,
  "tool_call_sound_behavior": "auto",
  "tool_error_handling_mode": "auto",
  "execution_mode": "post_tool_speech",
  "assignments": [],
  "expects_response": true,
  "response_timeout_secs": 30,
  "parameters": [
    {
      "id": "action",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "The calendar operation to perform.",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": ["list_events", "get_event", "create_event", "delete_event", "get_free_slots"],
      "is_system_provided": false,
      "required": true
    },
    {
      "id": "calendar_key",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "Optional. Named calendar key (from tools.google_calendar.calendars) to target a single calendar. Leave empty to use the calendar bound to this context.",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": false
    },
    {
      "id": "aggregate_mode",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "For multi-calendar get_free_slots: 'all' = intersection (default — only times free on every calendar), 'any' = union (free on at least one). Ignored when calendar_key is set.",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": ["all", "any"],
      "is_system_provided": false,
      "required": false
    },
    {
      "id": "time_min",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "ISO 8601 start time (UTC, e.g. '2026-04-27T09:00:00Z'). Required for list_events and get_free_slots.",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": false
    },
    {
      "id": "time_max",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "ISO 8601 end time (UTC, e.g. '2026-04-30T17:00:00Z'). Required for list_events and get_free_slots.",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": false
    },
    {
      "id": "free_prefix",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "Optional for get_free_slots. The prefix of events that define working hours (e.g. 'Open'). Leave empty to use Google's native free/busy with default working hours (Mon–Fri 09:00–17:00).",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": false
    },
    {
      "id": "busy_prefix",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "Optional for get_free_slots in title-prefix mode. The prefix of events that block availability (e.g. 'Busy'). Ignored in free/busy mode.",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": false
    },
    {
      "id": "duration",
      "type": "integer",
      "value_type": "llm_prompt",
      "description": "Appointment duration in minutes. Used by get_free_slots to return only start times where this many minutes fit. Slot starts align to multiples of this duration. Default 30.",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": false
    },
    {
      "id": "event_id",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "The exact ID of the event. Required for get_event and delete_event.",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": false
    },
    {
      "id": "summary",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "Title of the event. Required for create_event.",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": false
    },
    {
      "id": "description",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "Detailed description of the event. Optional for create_event.",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": false
    },
    {
      "id": "start_datetime",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "ISO 8601 start time for the new event (e.g. '2026-04-27T19:00:00Z'). Required for create_event.",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": false
    },
    {
      "id": "end_datetime",
      "type": "string",
      "value_type": "llm_prompt",
      "description": "ISO 8601 end time for the new event (e.g. '2026-04-27T19:30:00Z'). Required for create_event.",
      "dynamic_variable": "",
      "constant_value": "",
      "enum": null,
      "is_system_provided": false,
      "required": false
    }
  ],
  "dynamic_variables": {
    "dynamic_variable_placeholders": {}
  },
  "response_mocks": []
}
```

If your dashboard exposes a JSON-Schema import field instead, the equivalent
shape is:

```json
{
  "tool_config": {
    "type": "client",
    "name": "google_calendar",
    "description": "...",
    "expects_response": true,
    "response_timeout_secs": 30,
    "parameters": {
      "type": "object",
      "properties": {
        "action": {"type": "string", "enum": ["list_events", "get_event", "create_event", "delete_event", "get_free_slots"]},
        "calendar_key": {"type": "string"},
        "aggregate_mode": {"type": "string", "enum": ["all", "any"]},
        "time_min": {"type": "string"},
        "time_max": {"type": "string"},
        "free_prefix": {"type": "string"},
        "busy_prefix": {"type": "string"},
        "duration": {"type": "integer"},
        "event_id": {"type": "string"},
        "summary": {"type": "string"},
        "description": {"type": "string"},
        "start_datetime": {"type": "string"},
        "end_datetime": {"type": "string"}
      },
      "required": ["action"]
    }
  }
}
```

API endpoint for programmatic creation:
`POST https://api.elevenlabs.io/v1/convai/tools` with header
`xi-api-key: <your_key>`.

### After creating the tool

**Assign it to the agent** AAVA's `elevenlabs_agent` config points at — in
the agent settings → Tools tab → enable `google_calendar`. Some ElevenLabs
deployments expose tools as a per-agent attach step separate from the
global tool definition, so creating the tool is necessary but not
sufficient.

### Notes on the schema

- **`expects_response: true`** (vs `false` on tools like `hangup_call`) —
  the agent needs the slot list / event id back to speak about it.
- **`response_timeout_secs: 30`** — Google Calendar API is usually <1 s,
  but DWD token refreshes and `freebusy.query` over wide ranges can take
  a few seconds. Matches AAVA's tool definition `max_execution_time=30`.
- **`execution_mode: "post_tool_speech"`** + **`pre_tool_speech: "auto"`** —
  ElevenLabs fills an "ok, let me check the calendar…" filler while the
  API call runs, masking latency. Switch to `"during_tool_call"` if you
  prefer the agent stays silent until the result is back.
- **`required` is set only on `action`** — every other parameter is
  conditionally required based on which `action` value the model picks
  (e.g. `summary` is only required for `create_event`). ElevenLabs'
  `required` flag is unconditional, so marking everything required would
  block legitimate `get_free_slots` calls that omit `summary`/`event_id`.
- **`enum` is set only on `action` and `aggregate_mode`** — the others
  use `null`.

### Verifying it took effect

Place a single test call against `demo_elevenlabs`. In the AAVA engine
logs you should see:

```text
{"event": "[elevenlabs] [<call_id>] Received message type: client_tool_call"}
{"event": "[elevenlabs] [<call_id>] Tool call: google_calendar"}
{"event": "GCalendarTool execution triggered by LLM", ...}
{"event": "Tool response to AI", "action": "get_free_slots", "status": "success", ...}
```

If you still get the "calendar tool is not configured" fallback after
registering, check:

1. The tool name on ElevenLabs is exactly `google_calendar` (case-
   sensitive, no spaces).
2. The tool is **enabled** on the specific agent that AAVA's
   `elevenlabs_agent` config points at (not just created globally).
3. AAVA has the right `agent_id` in `config/ai-agent.yaml` under
   `providers.elevenlabs_agent.agent_id`.

## Prompt examples (how callers use the tool)

Example things a caller might say, and the kind of **google_calendar** call the agent should make in response.

- **"What do I have on my calendar tomorrow?"**
  -> `list_events` with `time_min` / `time_max` covering tomorrow in the calendar's timezone.

- **"When are you free for a 30-minute appointment next Tuesday?"**
  -> `get_free_slots` with `time_min` / `time_max` for that day, `duration: 30`, and (if not in config) `free_prefix` / `busy_prefix` as needed.

- **"Do you have 2pm available?"**
  -> Either `get_free_slots` for that day and check if 2pm is in the list, or `list_events` for a short window around 2pm and interpret.

- **"Book me for 2:30pm next Tuesday for 30 minutes."**
  -> `create_event` with `summary` (e.g. appointment title), `start_datetime` = 2:30pm that day, `end_datetime` = 3:00pm that day; optional `description`.

- **"What's the details of my appointment on Thursday at 10?"**
  -> `list_events` for that morning, find the matching event, then optionally `get_event` with that event's `event_id` for full details.

- **"Cancel my 3pm meeting."**
  -> `list_events` for that day to find the event, then `delete_event` with that event's `event_id` to cancel it.

## Sample context system prompts

The LLM only knows what your context's `instructions` (system prompt)
tells it. A good prompt turns the calendar tool from a generic API
into a specific, reliable agent. Two templates below — a standard
single-calendar agent (the common case), and the multi-calendar YAML
variant.

### Single-calendar appointment agent (recommended default)

Use this when the context is bound to one calendar via the Admin UI.
No `calendar_key` is needed because the backend already knows which
calendar to use.

```text
You are the scheduling assistant for Acme Dental. You book, reschedule,
and cancel appointments using the google_calendar tool.

Time & timezone rules:
- The calendar is configured for America/New_York. Always speak and
  reason in that timezone. If the caller says a bare time like "2pm",
  assume it's 2pm Eastern.
- Today's date is provided in the runtime context — never guess the date.
- All datetimes sent to google_calendar must be ISO 8601 (e.g.
  2026-04-23T14:00:00). Seconds may be :00.

Finding available times:
- When a caller asks for availability, use the action get_free_slots.
  - time_min / time_max: cover the day or range they asked about, in
    the calendar timezone.
  - duration: default 30 minutes unless the caller says otherwise.
  - free_prefix: "Open"
  - busy_prefix: "Busy"
- Read back at most 3 options at a time (e.g. "I have 9am, 10:30am, or
  2pm — which works?"). Don't recite the whole list.

Booking:
- Once the caller confirms a time, call create_event.
  - summary: "{caller_name} — {reason}" (e.g. "Jane Smith — Cleaning")
  - start_datetime / end_datetime: the agreed slot, aligned to the
    slot grid get_free_slots returned.
  - description: include the caller's phone number and any notes.
- After a successful booking, read back the confirmed date and time
  once, then move on. Do not re-book the same slot.

Looking up or canceling:
- For "what do I have on {day}?" use list_events for that day.
- For cancellations, use list_events first to get the event_id, confirm
  with the caller which appointment they mean, then call delete_event.

Never promise a time you haven't verified with get_free_slots first.
If the tool returns an error, apologize briefly and offer to take a
message or try a different day.
```

### Multi-calendar (YAML-only escape hatch)

If you've configured multiple calendars in `selected_calendars` via
YAML (see "Power-user: cross-calendar availability" above), the LLM
needs to know the calendar keys by name and which to pick. Add a
section like this to your system prompt:

```text
This context has access to two calendars:
- work — used for customer meetings, demos, and anything work-related.
- personal — used for personal appointments (dentist, gym, family).

When the caller asks to book or cancel something:
- If they say "at work", "team meeting", "customer call", or similar,
  pass calendar_key: "work".
- If they say "personal", "doctor", "family", etc., pass
  calendar_key: "personal".
- If it's ambiguous, ask: "Should I put that on your work or personal
  calendar?" — do not guess.

When the caller asks "when am I free", use get_free_slots WITHOUT a
calendar_key. The tool will aggregate across both calendars and
return only times that are free on both (aggregate_mode: all), which
is the right behavior for finding a time that works for everything.

When the caller asks "what do I have on {day}", use list_events
without calendar_key to see everything across both calendars.
```

> **Tip:** pair the multi-calendar prompt with a clearly-named context
> (e.g. `unified_assistant`) so it's obvious the LLM is meant to pick
> between calendars. Don't mix this prompt into a context that's also
> bound to a single-calendar UI selection — you'll get contradictory
> signals.
