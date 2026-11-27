# Conversation completion and dialplan handoff

Related: [#904](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues/904).

AVA can finish an AI conversation without disconnecting the phone call. The
existing `hangup_call` tool accepts an optional `next_step`: `hangup` or a named,
approved Transfer Destination. The engine finishes the farewell, drains caller
output, then performs the selected action. There is no second tool call after goodbye.

Existing installations retain farewell-and-hangup behavior. This capability is
for inbound AVA-owned calls. Outbound and externally owned calls (including
VICIdial) retain their existing terminal workflows.

## Operator setup

1. Create the actual survey, IVR, announcement or other flow in Asterisk.
2. In **Tools → Transfer Destinations**, add a named route. Use **Dialplan (IVR,
   survey, announcement)** for an explicit context, extension and priority.
   Existing extension, queue and ring-group destinations work too. Dialplan
   destinations support blind transfer and completion, not attended/live-agent
   transfer or predial.
3. Enable **Require caller opt-in** for optional surveys. This adds model guidance
   and prevents the route being configured as an automatic completion default.
   Acceptance is determined by the conversation/model; route selection alone is
   not independently verified consent evidence.
4. In **Tools → Hangup Call → Conversation completion**, select allowed next
   destinations. Keep **Default after conversation → End call** for optional surveys.
   An unconditional announcement or IVR can be the automatic default.
5. **Save & Apply** the global tool settings. Existing calls keep their captured
   configuration; new calls get the new settings.
6. Under **Agents → Edit → Tools → After conversation**, inherit or narrow those
   destinations and choose an inherited or explicit default. The shared Transfer
   destination access policy also applies, including when the agent has no
   model-callable transfer tool enabled. Save the agent; changes affect new calls.
7. Enable `hangup_call` for completion, and `blind_transfer` if callers may request
   a return to the IVR during conversation. Add the prompt instructions below.

Global tool disables always win. Agent policies can only narrow global routes.
An explicitly empty Agent completion destination selection disables handoff.
Agent defaults cannot point to opt-in, globally unapproved or disallowed routes.

| Setting | Meaning | Default / activation |
| --- | --- | --- |
| Global allowed next destinations | Exact keys from Transfer Destinations that completion may use | Empty; Save & Apply, new conversations |
| Global default after conversation | Action when `next_step` is omitted | `hangup`; Save & Apply, new conversations |
| Agent allowed destinations | Inherit global approval or select a subset of scoped routes | Inherit; Agent save, new conversations |
| Agent default | Inherit, explicitly hang up, or select an eligible automatic route | Inherit; Agent save, new conversations |
| Destination caller opt-in | Require offer/acceptance in model guidance; prohibit automatic defaults | False; Save & Apply |
| Dialplan priority | Positive integer priority at destination context/extension | 1; Save & Apply |

## Survey prompt and call flow

Example agent instruction (replace keys with the exact configured keys):

> When the caller's request is resolved, ask whether they would like a short
> customer survey. If they explicitly accept, call `hangup_call` with
> `farewell_message` "Thank you. Please stay on the line for the survey." and
> `next_step` "customer_survey". If they decline, are uncertain, or ask to
> disconnect, call `hangup_call` with a brief goodbye and `next_step` "hangup".
> Do not route an ambiguous answer to the survey. Invoke the tool once; use its
> farewell as the final sentence and do not start another conversation turn.
> If the caller asks to return to the main menu during the conversation, call
> `blind_transfer` with `destination` "main_ivr" and follow the configured
> transfer-announcement flow.

Acceptance: one `hangup_call` invocation → farewell audio → drain → survey dialplan.
Decline: one invocation with `next_step=hangup` → farewell audio → disconnect.
No `next_step`: use the captured Agent/global default. Explicit `hangup` always
wins over a configured default. An unknown, invalid or disallowed explicit choice
falls back to hangup; it never substitutes another destination. The first accepted
terminal request wins over duplicate or conflicting tool requests.

A bounded audio timeout can commit completion without confirmed audio drain. Call
History records that distinction. Caller hangup and existing transfers supersede
completion. Silence timeouts and provider-start failure retain their independent
routing policies and do not invoke the normal completion default.

## Configuration reference

Global inventory and completion policy (`config/ai-agent.local.yaml` is suitable
for operator overrides):

```yaml
tools:
  transfer:
    enabled: true
    destinations:
      customer_survey:
        type: dialplan
        target: s
        dialplan_context: ava-customer-survey
        priority: 1
        description: Optional customer satisfaction survey
        requires_opt_in: true
      main_ivr:
        type: dialplan
        target: s
        dialplan_context: company-main-ivr
        priority: 1
        description: Main menu
  hangup_call:
    enabled: true
    completion:
      default_next_step: hangup
      destination_keys: [customer_survey, main_ivr]
```

Agent `tool_configs_json` is stored in `agents.db`, alongside existing resource
policies. For example, this Agent permits survey completion but always hangs up
if the model omits `next_step`:

```json
{
  "transfer": {
    "destination_policy": "selected",
    "destination_keys": ["customer_survey", "main_ivr"]
  },
  "hangup_call": {
    "default_next_step": "hangup",
    "destination_keys": ["customer_survey"]
  }
}
```

Omitting an Agent field inherits it. `destination_keys: []` explicitly denies
completion handoffs. The per-call schema exposes only `hangup` and effective
completion destination keys. Completion resolves exact keys, never fuzzy matches.
Existing destination labels with spaces or Unicode are supported, for example
`Live Agent` or `受付メニュー`; use the saved key exactly. Blank labels, surrounding
whitespace, control characters and labels longer than 128 characters are rejected.

Example destination dialplan (the operator owns its contents):

```asterisk
[ava-customer-survey]
exten => s,1,Playback(custom/survey-introduction)
 same => n,Read(SCORE,custom/rate-your-experience,1,,1,10)
 same => n,NoOp(Survey collected)
 same => n,Hangup()
```

## Mid-conversation IVR return and re-entry

`blind_transfer(destination="main_ivr")` uses the same destination inventory,
Agent access policy and guarded ARI handoff as completion. Its existing deferred
announcement/audio flow remains in control. A custom dialplan route skips predial
and preserves its configured priority.

The phone call continues after handoff, but the current AI conversation ends.
If the IVR sends the same live channel back into AVA, cleanup finishes before
admission of a fresh conversation. Every new conversation uses a history call ID
of `<caller_channel_id>:<conversation_id>`; existing records keep their IDs.
Diagnostics retain the original caller channel ID and a unique conversation ID.
Calendly bookings use the same conversation history key, so History erasure
scrubs only that conversation's booking evidence. Post-call tools run for each completed AI conversation, not at
the final end of the entire PBX call.

## Handoff outcomes, recovery and rollback

The shared executor prevalidates completion and explicit dialplan destinations
using `DIALPLAN_EXISTS`, claims ownership before `continueInDialplan`, and only
restores it after confirmed rejection. ARI acceptance means the request was
accepted; it does not prove the survey was completed or a destination answered.

A missing/unverifiable target or confirmed rejection falls back to farewell/hangup.
An uncertain response (timeout, exception, missing/5xx response) might follow an
accepted redirect. AVA retains the caller protection, observes channel state and
never blindly retries continue or deletes that caller. An unresolved handoff logs
that PBX inspection is required. Inspect the live channel/dialplan and resolve it
through normal PBX operations.

Completion writes non-secret action evidence before dispatch to
`/app/data/operator/handoff_actions.db` (`HANDOFF_JOURNAL_PATH` overrides it).
Restart recovery observes pending actions without replaying routing or hanging up
channels. It does not reconstruct the old provider conversation. Keep this SQLite
file with the operator backup; do not delete unresolved evidence during rollback.
The file is additive and does not migrate the Agent or Call History schemas.
Call History shows requested/resolved next steps, routing result and audio drain
confirmation; the current journal result supplements early cleanup snapshots.

To disable, set global completion destinations to empty and default to `hangup`,
then Save & Apply. Active calls retain their prior snapshot. Before a code rollback,
remove completion settings and custom dialplan destination types from configuration,
clear Agent `hangup_call` overrides, and back up SQLite using its backup API.
Older binaries do not understand the new Agent policy scope. Preserve operator
configuration and evidence rather than restoring unrelated databases wholesale.

## Implementation and API research

The terminal engine retains sole ownership of farewell drain and completion.
`src/tools/telephony/dialplan_handoff.py` is shared by blind transfer, inactivity
fallback, provider-start failure and conversation completion. Existing triggers
retain their own prevalidation and failure policies; the announcement coordinator
is separate from the routing executor.

Verified against the official [Asterisk Channels REST API](https://docs.asterisk.org/Latest_API/API_Documentation/Asterisk_REST_Interface/Channels_REST_API/#continueindialplan)
on 2026-11-26: `POST /channels/{channelId}/continue` accepts context, extension and
priority for leaving Stasis. The existing ARI client's accepted/rejected/uncertain
response contract is preserved. Live qualification must record the actual Asterisk
version, provider, transport, test call IDs and raw private evidence.

For setup validation and survey/IVR acceptance scenarios, use the
[conversation completion testing guide](CONVERSATION_COMPLETION_TESTING.md).
