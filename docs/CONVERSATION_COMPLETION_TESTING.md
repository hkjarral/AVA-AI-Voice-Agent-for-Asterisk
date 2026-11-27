# Conversation completion acceptance

Feature: [#904](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues/904).
Implementation and operator configuration: [Conversation completion](Conversation-Completion.md).

## Manual lab testing

Create two isolated test Agents using the feature guide, one for Google Live
and one for the `local_hybrid` pipeline. Assign unused test extensions in your
PBX; the example names and extensions below are illustrative:

| Extension | Agent | Mode |
| --- | --- | --- |
| 6894 | `issue904_validation` | Google Live |
| 6895 | `issue904_hybrid` | Local STT / OpenAI LLM / Local TTS pipeline |

Configure each test Agent to offer a short survey. Disable unrelated global
notification and lookup automations for these acceptance Agents. Preserve normal
Agents and leave the global completion default as hangup.

1. Call **6894** and accept the survey. Hear the farewell once, then the distinctive
   "You have reached the test customer survey" prompt. Enter a rating from 1 to 5.
   The AI must not continue speaking over the survey.
2. Call again and decline, or say "Please end this call." Hear the farewell once
   and disconnect. The survey must not play.
3. Call again and ask "Return me to the main menu." Hear the transfer announcement,
   then the distinctive test main-menu prompt. This uses `blind_transfer`, not
   a second completion tool call.
4. At the menu, press **1**. A fresh AI conversation should begin on the same phone
   call. Decline the survey to finish it. Call History should retain both AI
   conversations instead of overwriting the first.
5. Repeat the supported scenarios on **6895** to exercise the modular pipeline.
6. In **Tools → In-Call → Hangup → Conversation completion**, inspect the global
   allowed destinations and default. Under **Agents → Edit → Tools → After
   conversation**, verify inherited versus explicit settings. Optional survey
   routes must not appear as automatic default choices. Explicit `next_step=hangup`
   always disconnects after the farewell.

The example destination keys are `issue904_survey` and `issue904_menu`. Edit or clone
the dedicated test Agent rather than changing a normal inbound Agent for testing.
After a global edit, Save & Apply; Agent edits apply to new conversations. An IVR
return is the end of one AI conversation and the start of another when re-entered.

## Automated coverage

- Completion selection, explicit decline overriding automatic defaults, exact
  scoped keys, deleted/disallowed routes and global disables.
- Shared transfer, inactivity and provider-failure routing regressions.
- Farewell drain, missing destinations, confirmed rejection versus uncertain
  response, early StasisEnd, retirement during journal preparation and duplicate
  terminal requests.
- Call-scoped provider enums and immutable configuration across reloads.
- Durable pending-action observation after restart without redirect replay or
  destructive caller hangup.
- Structured Tools API, raw YAML and Agent API validation; legacy opaque policy
  round-trip; frontend controls, opt-in default exclusion and Agent serialization.

Private raw call evidence, identifiers and detailed deployment state are kept in
ignored `logs/archived/rca-*` archives, not in this document. Automated SIP callers
send validated synthetic audio and count received RTP packets; they do not record
caller audio. A successful routing trace does not establish subjective audio quality.

## Rollback and fixture cleanup

Disable completion destinations and restore default `hangup`, then Save & Apply.
Clear Agent `hangup_call` policies before running an older binary. See the main
feature guide for journal and database backup requirements.

When the fixtures are no longer needed, remove the two dedicated Agents and named
routes, the `issue904_custom.conf` include/file and custom prompt WAVs. Do not
replace the entire operator configuration or Agent database with an old backup.
Remove the temporary loopback SIP peer and browser-test container after automated
verification; manual extensions and named destinations remain for operator testing.
