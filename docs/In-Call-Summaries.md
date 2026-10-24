# In-call HTTP summaries

See the [v7.7.0 validation matrix](baselines/golden/v7.7.0-validation-matrix.md)
for existing automated evidence and the explicit waiver of further live testing.
A summary uses the conversation history available at invocation. It cannot
describe unobserved human conversation, and it does not filter recorded turns
by transfer state or provide an after-transfer hook.

In Tools → In-call HTTP, enable **Generate AI Summary**, select a configured LLM,
and set the summary prompt, maximum words and timeout. Only that provider receives
the transcript. The prompt supports `{max_words}`. Saving these tools uses the
normal Save & Apply generation: active calls keep their captured tool settings.

Use `"summary": {summary_json}` in a JSON body, or `"summary": "{summary}"`.
Add `"summary_status": "{summary_status}"` and optionally
`"summary_error_code": "{summary_error_code}"`. Generated fields are reserved;
model arguments and enrichment cannot supply their values. Templates retain the
existing single-pass escaping and JSON validation.

The snapshot includes the latest available transcription and any other recorded
turns; transfer does not establish a transcript cutoff. Invoke the tool **before**
transferring when a pre-transfer summary is needed. Do not assume that later
invocation removes post-transfer turns, or that a prompt creates an after-transfer hook.
Post-call timing and legacy post-call summary configurations are unchanged.

Summary generation and HTTP delivery have separate configured budgets; their sum
bounds the tool. Cancellation propagates to the summary job. Cleanup detected
before delivery prevents sending. A failure defaults to sending other details
with an empty summary and explicit unavailable status; choose **Do not send the
request** when a summary is required. Generation diagnostics are recorded separately
from delivery status in Call History. A timeout before response headers may leave
delivery uncertain; an observed HTTP outcome is retained if body processing hits
the combined deadline. Applications creating tickets should use their own
idempotency key such as call ID.

Internal acknowledgements remain tool data, with conversation/tool continuation.
The operator's agent instructions should say when to invoke the tool and what to
say naturally after success or failure. Do not put notification acknowledgements
into the voice script.

HTTP delivery diagnostics distinguish accepted 2xx responses, explicit 4xx
rejections (except ambiguous 408 timeouts), uncertain transport/server failures,
and requests skipped before dispatch. A successful HTTP response can still have
an invalid or oversized body: delivery remains accepted while result processing
reports its own error. HTTP acceptance does not establish application-side success.
Pipeline snapshots retain the recorded caller turn once even when assistant text
follows it; a genuinely pending caller turn is still included separately.

Modular-pipeline continuations receive only a neutral internal-notification outcome
and explicit silent-continuation instructions, preserving the captured agent prompt
and remaining tools. Full notification details stay in Call History. Ollama's
ordinary tool read-out behavior does not apply to internal notifications.
