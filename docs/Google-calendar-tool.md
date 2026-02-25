# Google Calendar tool

The **google_calendar** tool lets the AI voice agent interact with Google Calendar: list events, get a single event, create events, and find free appointment slots (with duration and slot alignment).

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
| `free_prefix` | Default prefix for events that define available windows (e.g. `"Open"`). The LLM can override this per-call. | *(none -- must be provided by LLM or config)* |
| `busy_prefix` | Default prefix for events that define booked slots (e.g. `"Busy"`). The LLM can override this per-call. | *(none -- must be provided by LLM or config)* |
| `min_slot_duration_minutes` | Default appointment duration in minutes for `get_free_slots`. | `15` |

Example config with defaults:

```yaml
tools:
  google_calendar:
    enabled: true
    free_prefix: "Open"
    busy_prefix: "Busy"
    min_slot_duration_minutes: 30
```

## Actions

| Action | Purpose |
|--------|--------|
| `list_events` | List events in a time range (`time_min`, `time_max`). |
| `get_event` | Get one event by `event_id`. |
| `create_event` | Create event with `summary`, `start_datetime`, `end_datetime` (optional `description`). |
| `get_free_slots` | Return start times where a slot of given `duration` (minutes) fits. Uses `free_prefix` / `busy_prefix` to compute available intervals; slot starts are aligned to multiples of `duration` (e.g. 15 -> :00, :15, :30, :45). |

All times use ISO 8601. The tool is registered as `google_calendar` and is in the **business** tool category.

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
  -> The tool does not support delete/cancel; use `list_events` to find the event and `get_event` by `event_id` if needed. Cancellation would require another integration or manual step unless extended.
