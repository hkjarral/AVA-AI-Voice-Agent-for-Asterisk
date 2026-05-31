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

## Required Dialplan Variables

AAVA detects ViciDial calls only when the dialplan sets explicit ViciDial Remote Agent variables. Use a real AAVA context for `AI_CONTEXT`; do not set it to `vicidial_remote_agent` unless that is an actual context you created.

```asterisk
same => n,Set(__AI_CONTEXT=sales)
same => n,Set(__VICIDIAL_RA_CALL_ID=${CALLERID(name)})
same => n,Set(__VICIDIAL_RA_AGENT_USER=1028)
same => n,Stasis(asterisk-ai-voice-agent)

exten => h,1,AGI(agi://127.0.0.1:4577/call_log)
```

If your ViciDial install sends the Remote Agent call ID in a SIP header, extract that header in dialplan and set `VICIDIAL_RA_CALL_ID` before calling `Stasis(...)`.

> Required: every context on a ViciDial server must include the `h` extension. Missing it can leave calls stuck in ViciDial tables such as `vicidial_log` or `vicidial_live_agents` and break reporting.

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
