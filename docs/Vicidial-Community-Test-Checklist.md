# ViciDial Community Test Checklist

Use this checklist when testing the ViciDial Remote Agent integration and sharing results with maintainers.

## Environment

- ViciDial version/build:
- Asterisk version:
- Channel driver: SIP / PJSIP / both
- AAVA commit/branch:
- Audio transport: AudioSocket / ExternalMedia
- AI provider/context used:

Deployment mode:

- [ ] Remote AAVA Asterisk server via SIP/PJSIP/IAX cross-connect
- [ ] Same ViciDial Asterisk server

Metadata transport:

- [ ] CALLERID(name) on same ViciDial Asterisk server
- [ ] SIP header
- [ ] PJSIP header
- [ ] IAXVAR
- [ ] Other:

## Setup

- [ ] `VICIDIAL_API_USER` and `VICIDIAL_API_PASS` set in `.env`
- [ ] ViciDial API user allows the configured `source`
- [ ] `integrations.vicidial.enabled=true`
- [ ] `integrations.vicidial.deployment_mode` matches the tested architecture
- [ ] `VICIDIAL_RA_CALL_ID` set before `Stasis(...)`
- [ ] `VICIDIAL_RA_AGENT_USER` set before `Stasis(...)`
- [ ] Same-box only: full ViciDial `h` extension present in every ViciDial context
- [ ] Remote AAVA Asterisk only: ViciDial-side call lifecycle/logging preserved before cross-connect

Call ID source:

- [ ] `CALLERID(name)`
- [ ] SIP header extracted in dialplan
- [ ] PJSIP header extracted in dialplan
- [ ] IAXVAR extracted in dialplan
- [ ] Other:

Optional metadata forwarded to AAVA:

- [ ] `VICIDIAL_LEAD_ID`
- [ ] `VICIDIAL_CAMPAIGN_ID`
- [ ] `VICIDIAL_LIST_ID`
- [ ] `VICIDIAL_PHONE_NUMBER`
- [ ] `VICIDIAL_CALLER_NAME`
- [ ] `VICIDIAL_INGROUP`

## Single-Call Tests

- [ ] Remote Agent call reaches AAVA
- [ ] AAVA logs `ViciDial Remote Agent session detected`
- [ ] Caller and AI can converse
- [ ] `hangup_call` ends the call through ViciDial
- [ ] ViciDial report/log shows expected hangup status, default `AIHU`

## Transfer Tests

- [ ] Ingroup transfer succeeds
- [ ] Ingroup transfer report/log shows expected status, default `AIXFR`
- [ ] Extension transfer succeeds
- [ ] Extension transfer report/log shows expected status, default `AIEXT`
- [ ] Unknown destination fails gracefully and keeps caller in AAVA

## Concurrency

- [ ] Two or more simultaneous Remote Agent calls tested
- [ ] Same `agent_user` used for concurrent calls
- [ ] Unique `agent_user` per concurrent call
- [ ] Any ViciDial state interference observed:

## Logs To Collect

- AAVA logs around `RCA_CALL_START`
- RCA bundle path when available, using `archived/logs/YYYY-MM-DD_<descriptor>/`
- AAVA logs around ViciDial `ra_call_control`
- ViciDial Agent API response text
- Relevant ViciDial report/log rows
- Dialplan snippet used for the Remote Agent handoff

## Notes

Please include any ViciDial forum guidance you receive about Remote Agent concurrency or SIP header names for the Remote Agent call ID.
