# HTTP tool templates, validation, and previews

HTTP tools share phase-aware template guidance in **Tools**. Choose the pre-call,
in-call, or post-call phase that matches when the request should run. The editor
lists available variables and highlights references in JSON body templates:

- Green: AVA supplies this variable for the selected phase/settings.
- Amber: a model argument, custom enrichment/metadata, or environment-backed
  value must be supplied at runtime. A synthetic preview cannot prove it exists.
- Red: a definite error, such as a final-outcome variable used in-call or invalid
  JSON. Enabled configurations must pass validation before activation.

Disabled tools can retain unfinished templates for later editing. Existing saved
templates are never replaced by an upgrade or by opening the editor. **Use example**
explicitly replaces the body and selects JSON Content-Type; **Copy example** copies
the selected phase's example. Event names in examples are illustrative payload
values, not an AVA event-subscription contract.

Applying an example also clears a pending Content-Type header row so it cannot
override JSON when the tool is reviewed or saved.

## Validate without sending

**Validate & Preview** renders synthetic values with the runtime serializer. It
does not send HTTP, invoke an LLM, save configuration, or load server credentials.
Environment substitutions use synthetic replacements. Keep credentials in
`${ENV_VAR}` references, not literal header/body strings. Custom enrichment values
use neutral synthetic data and remain warnings until supplied by the call.

**Send test request** is a separate, explicitly triggered network operation. It
uses the same JSON escaping and URL/body rendering as the runtime, with test
values and configured environment references. Its displayed URL/body reflect those
test values, with environment values and sensitive JSON fields masked. It preserves
the existing HTTP-test destination restrictions and redirect checks. Invalid JSON is rejected before
dispatch; it is never sent as an unvalidated raw body. A test does not generate
an AI summary and cannot prove the receiver's application-side result. Expanded
environment values are removed from returned diagnostics, including JSON-escaped
and URL/form-encoded receiver echoes.

Validation also runs when enabled tools are saved through the managed API or
loaded into an engine tool generation. A failed reload retains the previous
generation, and active calls retain their captured settings. On initial startup,
an invalid HTTP template is logged with its tool, phase and safe error code and
that tool is omitted from the running generation; valid tools still start. The
saved configuration is unchanged. Correct the template and apply it to enable the
omitted tool. Routing/handoff and Pushover configuration policies remain strict.
An invalid Agent-inline HTTP template follows the existing invalid Agent policy
path: record a configuration failure before provider startup, without publishing
partial tools or falling back to another Agent.
Python changes still require an engine restart.

## Variables and phases

| Variable | Pre-call | In-call | Post-call |
| --- | --- | --- | --- |
| `{call_id}`, `{caller_number}`, `{called_number}`, `{caller_name}`, `{context_name}` | Available | Available | Available |
| `{campaign_id}`, `{lead_id}` | Available; may be empty | Not built-in | Available; may be empty |
| `{transcript_json}` | Unavailable | Recorded conversation so far | Final recorded AVA conversation |
| `{summary}`, `{summary_json}` | Unavailable | Available when generation is enabled; legacy explicitly declared model parameters remain supported when disabled | Generated, existing, or empty summary |
| `{summary_status}`, `{summary_error_code}` | Unavailable | Available when generation is enabled | Generation diagnostics are recorded separately in Call History |
| `{call_duration}`, `{call_outcome}`, `{call_start_time}`, `{call_end_time}` | Unavailable | Unavailable | Final recorded values |
| `{provider}`, `{call_direction}`, `{schema_version}`, `{tool_calls_json}`, `{pre_call_results_json}`, `{call_metadata_json}` | Unavailable | Not built-in | Available |
| Custom model parameters | Unavailable | Declared parameters only | Unavailable |
| Custom enrichment/metadata | Unavailable in JSON bodies; text/form retains literal unknown placeholders | Completed pre-call enrichment and effective call metadata | Final available enrichment and metadata |

`{transcript_json}` is a serialized JSON array. Insert it **outside quotes**.
`{summary_json}` is a JSON-encoded string, also inserted outside quotes; alternatively
use `"{summary}"` inside a JSON string. The renderer escapes substitutions once,
without reinterpreting braces or environment references inside replacement data.
It retains typed JSON values and rejects invalid unquoted values.

## In-call briefing example

Enable **Generate AI Summary** and select a configured provider before using the
summary field below. Remove that field if summaries are disabled; transcript
support does not depend on summary generation.

```json
{
  "event_type": "call_briefing",
  "call_id": "{call_id}",
  "caller_number": "{caller_number}",
  "summary": {summary_json},
  "transcript": {transcript_json}
}
```

Each invocation snapshots the recorded conversation, including the latest
available caller utterance without duplicating a turn already recorded by the
provider/pipeline. The summary and transcript use that same snapshot; subsequent
turns cannot mutate it. An empty conversation is `[]`. Model arguments and
enrichment cannot override the transcript. A later invocation takes a fresh
snapshot, with no cross-call cache.

This is the AVA conversation available at invocation, not a promise of a complete
phone-call transcript. It cannot include unobserved human conversation after
transfer. Invoke the tool before transfer when a pre-transfer briefing is needed;
there is no automatic transfer-success hook. Receiver-specific event names,
ticket updates, and deduplication remain operator/integration choices.

## Preparation and delivery errors

An unresolved JSON variable produces `missing_template_variable` plus the variable
name; invalid unquoted values produce `invalid_template_value`; malformed JSON
produces `invalid_json_template`. Local preparation failures have delivery
`skipped`, and summaries not started because of preparation failure are marked
`skipped`. The call ID, tool name, phase, and safe error fields are retained in
logs, Call History, and support exports without substituted transcript/credential
values. Generation status remains independent of HTTP delivery status.

Runtime preparation is checked before an LLM summary request. Transport failures
can leave delivery uncertain; an observed response status is retained even when
response processing fails. HTTP acceptance does not prove that a ticket or other
external action succeeded. See [in-call summaries](In-Call-Summaries.md) and the
[HTTP safety rules](TOOL_CALLING_GUIDE.md#http-template-safety-and-compatibility).

Implementation references: [Python 3.11 JSON serialization](https://docs.python.org/3.11/library/json.html)
and [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259). This feature adds no vendor API,
account requirement, or database migration.

Native Pushover uses the same guidance controls for its plain-text title/message,
with its own supported-variable catalog and rendering contract. See
[Pushover template guidance](Pushover.md#template-guidance-and-validation).
