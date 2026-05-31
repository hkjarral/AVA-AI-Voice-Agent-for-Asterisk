# ViciDial Remote Agent Integration Design

## Summary

Build v1 as a ViciDial-native Remote Agent integration. ViciDial owns dialing, campaign logic, compliance, call state, and reporting. AAVA receives already-connected Remote Agent calls, runs the existing provider/pipeline flow, and controls the live ViciDial call only through Agent API `ra_call_control`.

This is an integration, not an AI-callable tool. Config lives under `integrations.vicidial`, with Admin UI under Core Configuration -> Integrations -> ViciDial Remote Agent.

## Branch And Delivery

Implementation branch: `vicidial-remote-agent-integration`, created from updated `main`.

Do not build this on the wording-only branch `vicidial-experimental-clarifications`; keep that branch scoped to PR #406.

Delivery slices:

1. Add this design doc and prepare the ViciDial forum question.
2. Add backend config, startup validation, and `src/tools/telephony/vicidial.py`.
3. Wire `ToolExecutionContext.vicidial_session` and tool delegation.
4. Add Admin UI Integrations page and ViciDial editor.
5. Remove old experimental ViciDial outbound path.
6. Add tests, setup docs, migration docs, release notes, and community testing checklist.

## Core Decisions

- Require explicit `VICIDIAL_RA_CALL_ID` and `VICIDIAL_RA_AGENT_USER`.
- Do not infer call IDs inside AAVA; the dialplan owns call-id extraction from `CALLERID(name)` or a verified SIP header.
- Use real `AI_CONTEXT` values such as `sales` or `support`; ViciDial detection comes from ViciDial channel vars.
- Delegate existing `hangup_call`, `live_agent_transfer`, and `blind_transfer` on ViciDial sessions.
- Hide warm-transfer tools such as `attended_transfer` on ViciDial sessions.
- Fail startup if `AAVA_OUTBOUND_PBX_TYPE=vicidial` is still present.
- Keep FreePBX and generic Asterisk outbound behavior unchanged.

## Runtime Shape

At `StasisStart`, AAVA reads explicit channel vars. When `integrations.vicidial.enabled=true` and `VICIDIAL_RA_CALL_ID` is present, AAVA attaches a `VicidialSession` to the call and passes it through `ToolExecutionContext.vicidial_session`.

ViciDial sessions dispatch:

- `hangup_call` cleanup -> `ra_call_control stage=HANGUP`
- `live_agent_transfer` / `blind_transfer` -> configured `INGROUPTRANSFER` or `EXTENSIONTRANSFER`

Non-ViciDial calls continue through existing ARI behavior.

## Config

Config lives under:

```yaml
integrations:
  vicidial:
    enabled: true
    api_url: "https://vicidial.example.com/agc/api.php"
    source: "aava"
    user: "${VICIDIAL_API_USER}"
    pass: "${VICIDIAL_API_PASS}"
    timeout_ms: 5000
    verify_ssl: true
    fallback_to_ari_on_hangup_failure: false
    default_agent_user: ""
    status_codes:
      ai_hangup: "AIHU"
      ai_ingroup_transfer: "AIXFR"
      ai_extension_transfer: "AIEXT"
    default_live_agent_destination: "default_ingroup"
    destinations:
      default_ingroup:
        type: ingroup
        ingroup_choices: DEFAULTINGROUP
      tier2_extension:
        type: extension
        phone_number: "16005551212"
```

`source` must be allowlisted for the API user in ViciDial Admin -> API Users.

## UI

Admin UI adds Core Configuration -> Integrations -> ViciDial Remote Agent.

The page includes connection settings, env-backed credentials, remote-agent metadata, destination editor, status codes, dialplan snippet, warnings, and a connection test. The test sends `ra_call_control` with a deliberately invalid call ID and treats the expected "no active call" style response as proof that URL, credentials, and source allowlisting work.

The existing System -> Environment page exposes `VICIDIAL_API_USER` and `VICIDIAL_API_PASS`. The old outbound PBX dropdown no longer offers ViciDial.

## Docs For Community Testing

Community testers should use:

- `docs/Vicidial-Setup.md`
- `docs/Vicidial-Community-Test-Checklist.md`
- `docs/Vicidial-Migration-From-Experimental-Outbound.md`
- `docs/Configuration-Reference.md`
- `docs/TOOL_CALLING_GUIDE.md`
- `CHANGELOG.md`

The setup guide must show a real AAVA context, not `AI_CONTEXT=vicidial_remote_agent`, and must include:

```asterisk
same => n,Set(__AI_CONTEXT=sales)
same => n,Set(__VICIDIAL_RA_CALL_ID=${CALLERID(name)})
same => n,Set(__VICIDIAL_RA_AGENT_USER=1028)
same => n,Stasis(asterisk-ai-voice-agent)

exten => h,1,AGI(agi://127.0.0.1:4577/call_log)
```

Every context on a ViciDial server must include the `h` extension. Missing it can leave calls stuck in ViciDial tables and break reporting.

## Forum Question Draft

Post to the ViciDial Development forum at design-doc merge time:

```text
Subject: Remote Agent ra_call_control concurrency question for AAVA integration

We are building AAVA's ViciDial integration around Remote Agents. ViciDial owns dialing, campaign state, compliance, and reporting. AAVA receives already-connected Remote Agent calls and uses Agent API ra_call_control only for HANGUP, INGROUPTRANSFER, and EXTENSIONTRANSFER.

One implementation detail is unclear from the docs:

Can a single Remote Agent agent_user safely receive multiple concurrent calls when each call is controlled with ra_call_control using its own call ID value, or should every concurrent call use a distinct agent_user to avoid state interference?

We currently plan to require VICIDIAL_RA_AGENT_USER from the dialplan per call and treat default_agent_user as lab-only until this is confirmed.
```

## Open Items

- Post the forum question from an account with ViciDial forum access.
- Confirm the exact SIP header name used by real installs when call ID is not in `CALLERID(name)`.
- Recruit community users with real ViciDial installs before promoting from experimental to supported.
- Consider per-destination status-code overrides after v1 validation.
