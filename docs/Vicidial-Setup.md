# ViciDial Remote Agent Setup

This guide describes the ViciDial-native integration path for AAVA. ViciDial owns dialing, campaign state, compliance behavior, hangup processing, and reporting. AAVA receives already-connected calls as a ViciDial Remote Agent endpoint and uses ViciDial Agent API `ra_call_control` for AI-initiated hangup and cold transfer.

The old experimental AAVA-originated ViciDial outbound path has been removed. If your `.env` contains `AAVA_OUTBOUND_PBX_TYPE=vicidial`, AAVA intentionally refuses to start. See [ViciDial migration guide](Vicidial-Migration-From-Experimental-Outbound.md).

## Requirements

- ViciDial with Remote Agents configured.
- AAVA deployed where ViciDial/Asterisk can reach its Stasis/audio transport path.
- ViciDial Agent API access to `agc/api.php`.
- A ViciDial API user with:
  - API user/password
  - `source` value allowlisted in ViciDial Admin -> API Users
  - permission to use Agent API functions needed for `ra_call_control`.

## Configure AAVA

Use **Admin UI -> Core Configuration -> Integrations -> ViciDial Remote Agent**.

Set:

- Deployment mode:
  - `remote_aava_asterisk` for a separate AAVA Asterisk server behind a cross-connect
  - `same_box` for running AAVA Stasis on the ViciDial Asterisk server
- Agent API URL, for example `https://vicidial.example.com/agc/api.php`
- Source, for example `aava`
- API user reference: `${VICIDIAL_API_USER}`
- API password reference: `${VICIDIAL_API_PASS}`
- Status codes:
  - AI hangup: `AIHU`
  - AI ingroup transfer: `AIXFR`
  - AI extension transfer: `AIEXT`
- Destinations for ViciDial cold transfer:
  - `ingroup` destinations use `ingroup_choices`
  - `extension` destinations use `phone_number`

Add secrets to `.env`:

```env
VICIDIAL_API_USER=your_api_user
VICIDIAL_API_PASS=your_api_password
```

Restart the AI Engine after saving.

## Deployment Modes

AAVA supports two deployment modes. Both modes use the same runtime rule: the dialplan must set explicit ViciDial Remote Agent variables before `Stasis(...)`. AAVA does not sniff SIP headers or CallerID directly in Python.

Required final channel vars on the AAVA call:

- `VICIDIAL_RA_CALL_ID`
- `VICIDIAL_RA_AGENT_USER`

Optional channel vars captured in AAVA logs/RCA when present:

- `VICIDIAL_LEAD_ID`
- `VICIDIAL_CAMPAIGN_ID`
- `VICIDIAL_LIST_ID`
- `VICIDIAL_PHONE_NUMBER`
- `VICIDIAL_CALLER_NAME`
- `VICIDIAL_INGROUP`

Use a real AAVA context for `AI_CONTEXT`; do not set it to `vicidial_remote_agent` unless that is an actual context you created.

### Remote AAVA Asterisk Server

Recommended for production and higher-volume ViciDial systems.

```text
ViciDial server -> Remote Agent extension/cross-connect -> AAVA Asterisk server -> Stasis/AAVA
```

In this mode, ViciDial sends the Remote Agent call to a trunk or extension that lands on a separate Asterisk server running AAVA ARI/Stasis. This keeps ARI/WebSocket/Stasis load away from the production ViciDial Asterisk process.

The ViciDial side should preserve normal ViciDial call lifecycle and logging. Do not add ViciDial `call_log` AGI lines to the separate AAVA Asterisk server.

On the AAVA Asterisk server, map forwarded metadata into AAVA channel vars. Example using placeholder PJSIP headers:

```asterisk
same => n,Set(__AI_CONTEXT=sales)
same => n,Set(__VICIDIAL_RA_CALL_ID=${PJSIP_HEADER(read,X-VICIDIAL-CALL-ID)})
same => n,Set(__VICIDIAL_RA_AGENT_USER=${PJSIP_HEADER(read,X-VICIDIAL-AGENT-USER)})
same => n,Set(__VICIDIAL_LEAD_ID=${PJSIP_HEADER(read,X-VICIDIAL-LEAD-ID)})
same => n,Set(__VICIDIAL_CAMPAIGN_ID=${PJSIP_HEADER(read,X-VICIDIAL-CAMPAIGN-ID)})
same => n,Set(__VICIDIAL_CALLER_NAME=${PJSIP_HEADER(read,X-VICIDIAL-CALLER-NAME)})
same => n,Stasis(asterisk-ai-voice-agent)
```

The header names above are examples. Use the exact SIP/PJSIP/IAX metadata names your ViciDial-side forwarding script provides.

### Same-Box ViciDial Asterisk

Useful for labs, smaller installs, and users who cannot run a second Asterisk server.

```text
ViciDial Asterisk server -> local AAVA Stasis app
```

Same-box mode is simpler, but it runs ARI/Stasis/WebSocket activity on the same Asterisk process ViciDial depends on. Avoid this mode for high-volume production testing unless you understand and accept that operational risk.

```asterisk
same => n,Set(__AI_CONTEXT=sales)
same => n,Set(__VICIDIAL_RA_CALL_ID=${CALLERID(name)})
same => n,Set(__VICIDIAL_RA_AGENT_USER=1028)
same => n,Stasis(asterisk-ai-voice-agent)

exten => h,1,AGI(agi://127.0.0.1:4577/call_log--HVcauses--PRI-----NODEBUG-----${HANGUPCAUSE}-----${DIALSTATUS}-----${DIALEDTIME}-----${ANSWEREDTIME}-----${HANGUPCAUSE(${HANGUPCAUSE_KEYS()},tech)})
```

> Required for same-box mode: every context on a ViciDial server must include the correct ViciDial `h` extension. Missing it can leave calls stuck in ViciDial tables such as `vicidial_log` or `vicidial_live_agents` and break reporting.

## Runtime Behavior

For normal AAVA calls, telephony tools continue to use ARI.

For calls with `VICIDIAL_RA_CALL_ID` and `VICIDIAL_RA_AGENT_USER`:

- `hangup_call` calls ViciDial Agent API `ra_call_control` with `stage=HANGUP`.
- `live_agent_transfer` and `blind_transfer` call `stage=INGROUPTRANSFER` or `stage=EXTENSIONTRANSFER`.
- `attended_transfer` is hidden from the AI because ViciDial `ra_call_control` does not support warm/conference transfer.
- ViciDial transfer availability is controlled by `integrations.vicidial.enabled` and configured ViciDial destinations. `tools.transfer.enabled=false` disables normal ARI transfer behavior, but it does not disable ViciDial transfer routing for ViciDial sessions.

ARI hangup fallback applies only to hangup and only when `fallback_to_ari_on_hangup_failure` is enabled. Transfer failures never fall back to ARI because that would bypass ViciDial call state.

When a ViciDial transfer fails, AAVA keeps the caller in the AI conversation and returns a tool error to the provider so the agent can apologize, continue helping, or try another configured destination.

## Test Flow

1. Configure the integration in Admin UI.
2. Click **Test Connection**. The test sends a deliberately invalid `ra_call_control` call ID and treats the expected "no active call" style response as success. This verifies URL, credentials, and source allowlisting without touching a real call.
3. Send one ViciDial Remote Agent call to the AAVA extension.
4. Confirm AAVA logs `ViciDial Remote Agent session detected`.
5. Test AI hangup and confirm ViciDial reports the configured status code.
6. Test ingroup transfer and extension transfer with configured destinations.
7. If possible, test concurrent calls and report whether one `agent_user` can safely handle multiple calls in your ViciDial version.

Use [ViciDial community test checklist](Vicidial-Community-Test-Checklist.md) when reporting results.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| AAVA does not detect ViciDial session | Confirm `VICIDIAL_RA_CALL_ID` and `VICIDIAL_RA_AGENT_USER` are set before `Stasis(...)`. |
| API test says source not authorized | Add the configured `source` value to the ViciDial API user allowlist. |
| Transfers fail | Confirm destination key, `ingroup_choices`/`phone_number`, and ViciDial permissions. |
| Hangup works but ViciDial reports look wrong | Disable ARI fallback and verify `ra_call_control` succeeds. |
| Calls stuck in ViciDial tables | Confirm the `h` extension exists in every ViciDial context. |

## Current Limitations

- AAVA does not originate ViciDial campaign calls.
- ViciDial Agent API has no function to initiate a new outbound call to a Remote Agent.
- Warm/conference transfer is not supported through `ra_call_control`.
- This integration remains experimental until validated by community users on real ViciDial installs.
