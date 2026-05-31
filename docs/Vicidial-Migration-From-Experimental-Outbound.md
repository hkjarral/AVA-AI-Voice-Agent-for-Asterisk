# Migrating From Experimental ViciDial Outbound

The experimental AAVA-originated ViciDial outbound path has been removed. It could bypass ViciDial call state, reporting, hangup processing, and compliance workflows. AAVA now supports ViciDial through the Remote Agent architecture.

## What Changed

Removed:

- `AAVA_OUTBOUND_PBX_TYPE=vicidial`
- Admin UI outbound PBX type option `ViciDial (experimental)`
- Documentation that recommended AAVA-originated ViciDial campaign dialing

Kept:

- FreePBX outbound campaigns
- Generic Asterisk outbound campaigns
- ViciDial Remote Agent integration

## Required Action

If your `.env` contains:

```env
AAVA_OUTBOUND_PBX_TYPE=vicidial
```

remove or change it before starting AAVA. Startup intentionally fails while this value is present.

Then configure:

1. ViciDial Remote Agent routing in ViciDial.
2. AAVA Admin UI -> Core Configuration -> Integrations -> ViciDial Remote Agent.
3. Dialplan variables `VICIDIAL_RA_CALL_ID` and `VICIDIAL_RA_AGENT_USER`.
4. The ViciDial `h` extension in every context.

See [ViciDial Remote Agent setup](Vicidial-Setup.md).

## Why Remote Agent

ViciDial should own:

- lead/campaign dialing
- agent state
- hangup lifecycle
- reports and call logs
- compliance safeguards

AAVA should receive the connected call and use ViciDial Agent API `ra_call_control` for cold transfer or hangup.

## Known ViciDial Limitations

- There is no Agent API function to initiate a new outbound call to a Remote Agent.
- `ra_call_control` supports `HANGUP`, `INGROUPTRANSFER`, and `EXTENSIONTRANSFER`.
- Warm/conference transfer is not supported through `ra_call_control`.
