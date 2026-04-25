# Microsoft Calendar tool

The **microsoft_calendar** tool lets the AI voice agent list Outlook calendar events, find free appointment slots, create bookings, and delete bookings through Microsoft Graph.

Reference: Microsoft Graph [`calendar: getSchedule`](https://learn.microsoft.com/en-us/graph/api/calendar-getschedule?view=graph-rest-1.0) for free/busy lookup.

V1 uses **device-code OAuth**. The operator clicks Connect in the Admin UI, visits `https://microsoft.com/devicelogin`, enters the displayed code, and authorizes the app. No redirect URL or client secret is required.

## V1 scope

- Supports Microsoft 365 work or school accounts.
- Uses one connected account by default (`accounts.default`).
- Does not support personal Outlook.com accounts in V1.
- Uses delegated permissions, so the tool acts as the signed-in user, not as a tenant-wide application.
- Requires an explicit tenant ID or tenant domain. Do not use `/common`.

## Azure app registration

1. Open Azure Portal > Microsoft Entra ID > App registrations.
2. Create a new app registration.
3. Supported account type: single tenant.
4. Copy the **Application (client) ID**.
5. Copy the **Directory (tenant) ID** or use the tenant domain, such as `contoso.onmicrosoft.com`.
6. Open **Authentication**.
7. Under **Advanced settings**, turn **Allow public client flows** on.
8. Open **API permissions** and add delegated Microsoft Graph permissions:
   - `User.Read`
   - `Calendars.ReadWrite`
   - `offline_access`
9. Grant/admin-consent those permissions if your tenant requires it.

## Admin UI setup

1. Go to Admin UI > Tools > Microsoft Calendar.
2. Toggle the tool on.
3. Enter Tenant ID and Client ID.
4. Click **Connect**.
5. Visit the displayed Microsoft device-login URL and enter the code.
6. After authorization, the UI fills:
   - token cache path
   - signed-in user
   - calendar id
   - timezone
7. Pick a calendar if more than one is returned.
8. Click **Verify**.
9. Go to Contexts and enable `microsoft_calendar` for the context. Select the Microsoft account in the per-context section.

## YAML shape

```yaml
tools:
  microsoft_calendar:
    enabled: true
    free_prefix: ""              # blank = native free/busy mode
    busy_prefix: Busy
    min_slot_duration_minutes: 30
    max_slots_returned: 3
    max_event_duration_minutes: 240
    working_hours_start: 9
    working_hours_end: 17
    working_days: [0, 1, 2, 3, 4]
    accounts:
      default:
        tenant_id: contoso.onmicrosoft.com
        client_id: 11111111-1111-1111-1111-111111111111
        token_cache_path: /app/project/secrets/microsoft-calendar-default-token-cache.json
        user_principal_name: scheduler@contoso.com
        calendar_id: AAMk...
        timezone: America/New_York

contexts:
  appointment_agent:
    tools:
      - microsoft_calendar
    tool_overrides:
      microsoft_calendar:
        selected_accounts:
          - default
```

## Availability behavior

By default, `free_prefix` is blank. That means `get_free_slots` uses Microsoft Graph native free/busy (`getSchedule`) and intersects it with working hours. Operators do not need to create "Open" events.

Set `free_prefix` only for title-prefix mode. In that mode, events whose subject starts with the free prefix define bookable windows, and events whose subject starts with `busy_prefix` block time inside those windows.

Microsoft Graph requests are sent with `Prefer: outlook.timezone="UTC"`. The tool treats Graph responses as UTC-native and converts them into the configured IANA timezone for working-hours math and slot display.

## Runtime notes

- Token cache files live under `/app/project/secrets` and are written with `0640` permissions.
- Token refresh is protected with a file lock so `admin_ui` and `ai_engine` do not corrupt the MSAL cache.
- If refresh fails, the tool returns `error_code: auth_expired`; reconnect the account from Tools.
- `create_event` writes to `/me/calendars/{calendar_id}/events`, not `/me/events`, so the configured calendar selection is honored.
- `create_event` returns a short spoken `message` plus `agent_hint` containing the event id. This avoids reading raw ids aloud while still letting the model delete the exact event if the caller corrects the booking.
