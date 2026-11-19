# Appointment booking providers

Ava does not require a hosted scheduling vendor. Appointment booking can use a
self-hosted calendar through Ava's existing Streamable HTTP MCP client. Hosted
provider adapters are optional and should be enabled only by operators who have
chosen that provider.

## Self-hosted MCP route

Use a narrowly allowlisted MCP server that owns the calendar protocol and
credentials. Ava owns the call, caller confirmation, tool assignment and the
decision not to replay an unknown write. The MCP server owns availability,
calendar writes and authoritative readback.

The example below uses illustrative calendar MCP tool names and keeps the
exposed Ava names provider-neutral. Replace the tool names, endpoint and
environment variable with the operator's reviewed server contract and protected
deployment values. Never put a bearer value in YAML.

```yaml
mcp:
  enabled: true
  servers:
    calendar:
      transport: streamable-http
      url: https://calendar-mcp.example.com/mcp
      headers:
        Authorization: "Bearer ${CALENDAR_MCP_TOKEN}"
      defaults:
        timeout_ms: 15000
      tools:
        - name: calendar_find_availability
          expose_as: appointment_find_availability
          description: >-
            Find self-hosted calendar availability. Returned windows are not
            reservations. Never describe an error as a busy calendar.
        - name: calendar_create_event
          expose_as: appointment_create_booking
          description: >-
            Create one appointment only after the caller confirms the exact
            date, time, timezone, duration, title and attendee address. Do not
            retry an unknown result; ask staff to verify the calendar first.
```

These descriptions are guidance to the Agent, not enforcement. The reviewed MCP
server must enforce confirmation, duplicate prevention and unknown-result safety
when the workflow requires those guarantees. Preserve Ava's caller-confirmation
and unknown-result instructions even when the server also enforces them.

After adding or changing the MCP server, restart the AI Engine during a
controlled window and verify the server is connected before assigning or testing
its tools. Assign both exposed tools only to the intended Agent. Confirm the
calendar name, timezone, organizer identity, attendee-delivery behavior and
conflict handling with a disposable synthetic appointment before allowing real
callers to book.
The generic MCP client refuses redirects, does not expose configured headers and
does not replay an unknown-result tool call.

The availability tool should return free windows rather than reservations. The
Agent may choose a duration-sized range inside a returned window, then must read
the exact proposed details to the caller before invoking
`appointment_create_booking`. A successful calendar write proves provider
acceptance, not email delivery or RSVP acceptance.

## Operator notifications

Pushover remains a separate Ava tool. It may notify an operator about a booking
result or an uncertain outcome, but Pushover acceptance is not a booking,
calendar readback, display receipt or human acknowledgement. Keep the calendar
result and notification result as separate evidence.

## Optional hosted adapters

The built-in `calendly_calendar` adapter remains for existing Calendly users and
is disabled by default. Calendly's direct booking API requires a paid plan. Its
credential, journal, recovery and erasure state belong only to that adapter;
installations using MCP booking that have never initialized Calendly do not need
them. When switching from Calendly to MCP, retain the journal, privacy marker and
History erasure state; remove credentials through the normal disconnect flow.
New history on a stock installation that has never initialized Calendly does not
create Calendly erasure-queue entries. History written before this upgrade retains its prior
conservative erasure guarantee, even if all adapter files are temporarily absent;
see the [Calendly privacy guide](Calendly-calendar-tool.md#storage-privacy-and-recovery).

Do not relabel an arbitrary MCP write tool as booking without checking its
schema and failure semantics. A new provider should expose bounded availability,
one confirmed write, stable identifiers and authoritative readback. If it cannot
distinguish rejection from an unknown write outcome, it must fail closed and
route the result to staff rather than submit a second appointment.
