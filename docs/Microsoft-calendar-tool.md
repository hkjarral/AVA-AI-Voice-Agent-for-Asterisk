# Microsoft Calendar tool

The **microsoft_calendar** tool lets the AI voice agent list Outlook calendar
events, find free appointment slots, create bookings, and delete bookings
through Microsoft Graph.

V1 uses **device-code OAuth** — no redirect URL, no client secret, no public
HTTPS endpoint. The operator clicks **Connect** in the Admin UI, visits
`microsoft.com/devicelogin`, enters a short code, and authorizes the app.

References:
- Microsoft Graph [`calendar: getSchedule`](https://learn.microsoft.com/en-us/graph/api/calendar-getschedule?view=graph-rest-1.0) — free/busy lookup
- Microsoft Identity [device authorization grant](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code) — auth flow
- Microsoft Identity [public client flows](https://learn.microsoft.com/en-us/entra/identity-platform/msal-client-applications) — why the toggle below matters

## V1 scope

- Supports **Microsoft 365 work or school accounts** (i.e. tenant-bound
  accounts in Microsoft Entra ID).
- **Personal Outlook.com accounts are NOT supported in V1.** The
  `Allow public client flows` toggle, the `Calendars.ReadWrite` scope, and
  the device-code flow itself work against personal accounts in principle,
  but V1 enforces single-tenant Entra ID configuration and rejects the
  `/common` tenant.
- Uses **delegated permissions**, so the tool acts as the signed-in user
  (the person who authorized at `microsoft.com/devicelogin`), not as a
  tenant-wide application.
- One connected account per deployment (`accounts.default`). Multi-account
  is a V2 follow-up.

---

## Step 1 — Azure app registration

You only do this once per AAVA deployment. The result is one **Application
(client) ID** GUID and one **Directory (tenant) ID** GUID that you'll paste
into the Admin UI.

### 1.1 Create the app

1. Go to **portal.azure.com** → search for **App registrations** → click
   **+ New registration**.
2. **Name:** anything memorable, e.g. `AVA Calendar`.
3. **Supported account types:** select
   **"Accounts in this organizational directory only (your-tenant — Single tenant)"**.
   - V1 rejects the multi-tenant / `/common` tenant configuration. Single
     tenant is the supported choice.
4. **Redirect URI:** leave **completely blank**. Device-code flow does not
   use a redirect URI. If you set one here, it has no effect on V1; if you
   leave it blank, the app still works.
5. Click **Register**.

Azure lands you on the new app's **Overview** page. **Copy the
"Application (client) ID"** GUID — you'll paste it into AAVA later.

The **Directory (tenant) ID** is also on this page (or visible at the top
right under the user menu). Copy that too — you can use either the GUID
form or the domain form (e.g. `contoso.onmicrosoft.com`); both work.

### 1.2 Enable public client flows

The "Allow public client flows" toggle is what makes device-code OAuth
work. By default it is **OFF** — you have to turn it on explicitly.

1. Left sidebar → **Manage** → **Authentication** (or "Authentication
   (Preview)" — both lead to the same setting).
2. **In the new "Preview" experience:** click the **Settings** tab (third
   tab, after "Redirect URI configuration" and "Supported accounts"). The
   toggle is under **Web and SPA settings** → **Allow public client flows**.
3. **In the classic experience:** scroll to **Advanced settings** at the
   bottom → **Allow public client flows**.
4. Set the toggle to **Enabled** (Yes).
5. Click **Save** at the bottom.

You should see the toast `Update application Authentication —
Successfully updated` confirming the change.

### 1.3 Add API permissions

You need two delegated Microsoft Graph permissions for V1:
**`Calendars.ReadWrite`** and **`offline_access`**.

A third permission, **`User.Read`**, is added automatically when you
register a new app — you do **not** need to add it again. Don't worry if
it's already in the list.

1. Left sidebar → **API permissions**.
2. Click **+ Add a permission**.
3. Pick **Microsoft Graph**.
4. Pick **Delegated permissions** (NOT "Application permissions" — that's
   tenant-wide app-only auth, which V1 doesn't use).
5. In the search box, type `Calendars.ReadWrite` → expand the **Calendars**
   group → tick the `Calendars.ReadWrite` checkbox.
6. Clear the search and type `offline_access` → expand **OpenId permissions**
   → tick the `offline_access` checkbox.
7. Click **Add permissions**.

You should now see three rows in the **Configured permissions** table, all
type **Delegated**, all "Admin consent required = No":
- `User.Read`
- `Calendars.ReadWrite`
- `offline_access`

### 1.4 Admin consent (optional for V1)

Above the permissions table you'll see **"Grant admin consent for
{tenant}"**. For V1 with a single signed-in account (the person who will
use the device-code flow is the same as the calendar owner), **you can
skip this**. Microsoft will prompt the user to consent during their first
device-code sign-in, which is sufficient.

You only need admin consent if:
- Multiple users in the tenant will sign in through this same app, AND
- Your tenant policy requires admin pre-consent for new apps.

If unsure for a small/personal-org tenant, skip it and try the connect
flow first; you'll know within seconds if Microsoft asks for admin
consent during sign-in.

---

## Step 2 — Connect in the Admin UI

1. Open the Admin UI → **Tools** page → scroll to **Microsoft Calendar**.
2. Toggle the tool **on** (the switch in the top right of the section).
3. Fill in:
   - **Tenant ID:** paste the GUID or domain you copied from Azure
     (e.g. `2c2a015b-c089-4ae2-8fcd-...` or `contoso.onmicrosoft.com`).
   - **Client ID:** paste the **Application (client) ID** GUID from the
     Azure app's Overview page.
   - **Token cache path:** leave the default
     (`/app/project/secrets/microsoft-calendar-default-token-cache.json`).
     The path is content-addressed by account key; the file doesn't exist
     yet — it gets created when you complete the device-code flow.
   - **Timezone:** your operator timezone (e.g. `America/New_York`,
     `America/Los_Angeles`). This is used for slot display + working-hours
     math; the calendar's own timezone is preserved separately on Graph.
4. Click **Connect**.

A panel appears with a **device code** (e.g. `ABCD-EFGH`) and a
**verification URL** (`microsoft.com/devicelogin`).

5. In any browser, visit the verification URL → enter the code → sign in
   as the user whose calendar you want AAVA to use → approve the
   permissions.
6. Back in the AAVA UI, the panel polls Microsoft until authorization
   completes. When it succeeds:
   - **Signed-in user** field auto-fills with the UPN
     (e.g. `haider.jarral@cybridllc.com`).
   - **Calendar ID** field becomes a dropdown of accessible calendars.
     Pick the one you want — usually your default calendar is named simply
     `Calendar`. If only one is accessible, it auto-fills.
7. Click **Verify**. The Verify button turns green with the calendar's
   display name confirmed.

---

## Step 3 — Wire it into a context

By default no demo context has `microsoft_calendar` enabled, so you have
to add it manually.

1. Admin UI → **Contexts** page → open the context you want to test
   (e.g. `demo_deepgram` or `demo_openai`).
2. Under **Tools**, **uncheck `google_calendar`** (if it's enabled). You
   *can* leave both enabled — the SCHEDULING prompt now picks
   `microsoft_calendar` first when both are present — but it's cleaner
   to test one tool at a time.
3. Check `microsoft_calendar`.
4. A new **Microsoft Calendar (Per-Context)** section appears below.
   Tick the `default` account.
5. **Save** the context.

---

## Step 4 — Test with a phone call

Place a call against the context you just configured. Try a flow like:

> *"I'd like to schedule a meeting with the author."*
>
> Agent proposes 2–3 slot options. Pick one:
>
> *"3 PM works."*
>
> Agent confirms the booking.
>
> *"Actually, can we move it to 4 PM instead?"*
>
> Agent should call `delete_event` on the original booking, then
> `create_event` for the corrected time.

Check Outlook to confirm exactly **one** event landed at 4 PM (not two —
that would mean delete-then-recreate failed, which is a known LLM-
hallucination scenario the tool's server-side fallback catches).

---

## YAML shape

After Connect + Verify completes, the resulting YAML in
`config/ai-agent.local.yaml` (UI-edited overlay) looks like this. You
can also configure it manually in `config/ai-agent.yaml` if you prefer
YAML editing:

```yaml
tools:
  microsoft_calendar:
    enabled: true
    free_prefix: ""              # blank = native Microsoft Graph free/busy
    busy_prefix: Busy            # only used in title-prefix mode
    min_slot_duration_minutes: 30
    max_slots_returned: 3        # cap; set to 0 to disable
    max_event_duration_minutes: 240   # refuse longer bookings; 0 disables
    working_hours_start: 9       # only used in free/busy mode
    working_hours_end: 17
    working_days: [0, 1, 2, 3, 4]   # Mon=0..Sun=6 (Python weekday())
    accounts:
      default:
        tenant_id: contoso.onmicrosoft.com   # OR the GUID — both work
        client_id: 11111111-1111-1111-1111-111111111111
        token_cache_path: /app/project/secrets/microsoft-calendar-default-token-cache.json
        user_principal_name: scheduler@contoso.com   # filled by Connect
        calendar_id: AAMk...                          # filled by Connect / picker
        timezone: America/New_York

contexts:
  demo_deepgram:
    tools:
      - microsoft_calendar
    tool_overrides:
      microsoft_calendar:
        selected_accounts:
          - default
```

---

## Availability behavior

`get_free_slots` runs in one of two modes, chosen by `free_prefix`:

- **`free_prefix: ""` (default, blank)** — native Microsoft Graph
  **free/busy mode**. The tool calls `POST /me/calendar/getSchedule` for
  the signed-in user and intersects busy intervals with a working-hours
  mask (Mon–Fri 09:00–17:00 by default; tunable via the `working_*` keys).
  **You don't need to seed any "Open" events** — the tool figures out
  availability from your real calendar plus working hours.
- **`free_prefix: "Open"` (or any non-empty value)** — title-prefix mode.
  The tool scans your calendar for events whose subject starts with
  that prefix as available windows, minus events whose subject starts
  with `busy_prefix` (default `"Busy"`). Use this only if your
  organization already has an explicit "Open" booking-window convention.

The operator's `free_prefix` setting always wins over any value the LLM
passes per-call. The LLM may *narrow* the prefix string within active
title-prefix mode, but cannot escape free/busy mode by passing
`free_prefix='Open'` when the operator has chosen blank.

`getSchedule` is called in 31-day chunks for long time ranges (Microsoft
caps the API per request), so multi-week availability searches still
work — they just take more round-trips.

---

## Runtime notes

- Microsoft Graph requests carry `Prefer: outlook.timezone="UTC"`. The
  tool treats Graph as UTC-native and converts to operator timezone on
  the way out, avoiding off-by-N-hour booking bugs that come from
  Outlook's per-user timezone preference settings.
- Token cache files live under `/app/project/secrets/` and are written
  with `0640` permissions: owner (admin_ui) read/write, group
  (asterisk) read, world none. This is the minimum that lets ai_engine
  (running as `appuser` in the `asterisk` group) refresh tokens without
  giving world access. CodeQL will warn about it; this is a documented
  architectural choice, not a leak.
- Token refresh is protected with a `portalocker` file lock so
  `admin_ui` and `ai_engine` don't corrupt the MSAL cache when both
  refresh concurrently.
- If a refresh fails (token expired beyond refresh window, password
  changed, consent revoked, tenant policy expired the grant), the tool
  returns `error_code: auth_expired` with a clear "reconnect required"
  message. Operator action: open Tools → Microsoft Calendar →
  **Disconnect** → **Connect** again.
- `create_event` always writes to `/me/calendars/{calendar_id}/events`,
  honoring the configured calendar selection. (It does **not** fall back
  to `/me/events`, which would write to the user's default calendar
  regardless of selection.)
- `create_event` returns a short spoken `message: "Event created."` plus
  a structured `agent_hint` containing the event id and the
  delete-then-recreate guidance. The model uses `agent_hint` for tool
  args; the spoken `message` stays clean (no raw event-id read aloud).
- A server-side fallback catches the case where the model hallucinates
  an event_id on `delete_event` (a real LLM bug pattern): the tool
  tracks the most-recent successful `create_event` per `call_id` and
  retries the delete with the tracked id if the supplied id 404s.

---

## Troubleshooting

### `Microsoft connect failed: Request failed with status code 500`

Check the admin_ui container logs for the underlying error.

#### `ValueError: You cannot use any scope value that is reserved.`

This means the device-code flow was initiated with reserved scopes.
MSAL Python hard-rejects `openid`, `profile`, and `offline_access` on
`initiate_device_flow` because it auto-adds them. (`User.Read` is NOT
reserved — it's a regular Graph permission and must be passed
explicitly.) AAVA's device-flow endpoint passes
`["User.Read", "Calendars.ReadWrite"]` — if you see this error, the
build is older than the post-`b4e71a66` rev and needs a redeploy.

#### `403 Authorization_RequestDenied` / "Insufficient privileges to complete the operation."

The device-code flow succeeded, but the issued access token doesn't
cover the Graph endpoint we tried to call (typically `/me`). This
happens when `User.Read` was omitted from the scopes — Microsoft only
issues tokens for scopes that were *requested AND consented*. Even
though `User.Read` is granted in the app registration, the token
won't include it unless the device-code initiator asks for it. AAVA
now passes `["User.Read", "Calendars.ReadWrite"]` for this reason.
Fix: redeploy past the post-`b4e71a66` rev, then **Disconnect** in
the UI (deletes `data/secrets/ms_calendar_token.json`) and
**Connect** again so the device-code flow re-prompts with both
scopes.

### "AADSTS50194: Application is not configured as a multi-tenant application"

Means your app registration is single-tenant (correct for V1) but you
typed the tenant ID wrong, or you're trying to sign in with a user from
a different tenant. Use the tenant ID GUID from the Azure portal's
**Overview** page (or the domain form like `contoso.onmicrosoft.com`).
V1 rejects `/common` deliberately.

### "AADSTS65001: The user or administrator has not consented to use the application"

The signed-in user hasn't consented to the requested permissions, AND
admin consent hasn't been granted tenant-wide. Either:
- Have the user click through the consent prompt during sign-in
  (the device-code flow shows it), OR
- Have a tenant admin click **Grant admin consent for {tenant}** on the
  app's API permissions page in Azure.

### "AADSTS65002: Consent between first party application and first party resource is not allowed"

You picked the multi-tenant or `/common` audience by mistake. Edit the
app registration's **Authentication** → **Supported account types** and
switch to single tenant.

### Connect succeeds but Verify shows "calendar not found"

The signed-in account doesn't have access to the `calendar_id` you
selected. Most likely cause: you picked a calendar from a dropdown
that listed shared calendars, but the share permission expired or was
revoked. Disconnect → Connect again, or pick your default `Calendar`.

### `auth_expired` during a live call

The refresh token got invalidated mid-flight. Common causes:
- Password change on the Microsoft account
- Tenant conditional-access policy expired the refresh token
- The user's session got revoked by an admin

Action: operator opens Tools → Microsoft Calendar → Disconnect →
Connect again. The agent meanwhile speaks the configured
"calendar tool not available" fallback (the SCHEDULING-block recovery
substring `not configured` matches the `auth_expired` message).

---

## How V1 differs from Google Calendar

| | Google Calendar | Microsoft Calendar (V1) |
|---|---|---|
| Auth model | Service account (with optional Domain-Wide Delegation) | Delegated user OAuth (device-code) |
| Setup artifact | SA JSON file (uploaded via UI) | Tenant ID + Client ID (typed in UI) |
| Multi-account | Yes (`calendars: {key: {...}}`) from day 1 | V1 single account; multi-account in V2 |
| Free/busy | `freebusy.query()` API or "Open" event titles | `/me/calendar/getSchedule` API or "Open" event titles |
| Public access required | No (SA acts on its own credentials) | No (device code, no redirect URL) |
| Personal Gmail support | Yes (with the SA model) | NOT in V1 (work/school accounts only) |
| Tenant-wide deployment | Single SA can be shared with many calendars | One signed-in user account per V1 deployment |

---

## Internal implementation notes (for code reviewers)

- **MSAL device-flow scope quirk** — `app.initiate_device_flow(scopes=...)`
  reserves only `openid`, `profile`, `offline_access` (auto-added).
  `User.Read` is a regular Graph permission and must be passed
  explicitly, otherwise the issued token cannot call `/me`
  (Authorization_RequestDenied 403 in `_ms_device_flow_worker`). AAVA
  passes `["User.Read", "Calendars.ReadWrite"]`. The token still
  covers `offline_access` because MSAL adds it automatically. (See
  commit `b4e71a66` and its follow-up.)
- **Token cache locking** — `portalocker.Lock` is held around the
  `_load_cache_locked()` + `acquire_token_silent()` + `_persist_cache_locked()`
  sequence inside `MicrosoftGraphClient.acquire_token`. Multi-process
  refresh from admin_ui and ai_engine is safe.
- **Path validation** — `_resolve_ms_token_cache_path()` uses the
  `Path.relative_to(secrets_dir)` sanitizer pattern (CodeQL-recognized)
  so the static analyzer can verify the path is constrained.
- **31-day getSchedule chunking** — `_get_schedule_busy_blocks_chunked()`
  splits long time ranges into ≤31-day windows because Microsoft Graph's
  per-request limit means a wider span returns silently truncated data.
- **`agent_hint` field on `create_event` success** — the round-8 lesson
  from Google Calendar: keep `message` short ("Event created.") because
  models read it verbatim to the caller; put structured guidance in
  `agent_hint`.
- **`_last_event_per_call` LRU + delete fallback** — round-5 Google
  lesson, mirrored here: bounded at 1024 entries, hallucinated event_id
  on `delete_event` falls back to the tracked id from the same call.
