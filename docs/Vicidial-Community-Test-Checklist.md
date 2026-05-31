# ViciDial Community Test Checklist

Use this checklist when testing the ViciDial Remote Agent integration and sharing results with maintainers.

## Environment

- ViciDial version/build:
- Asterisk version:
- Channel driver: SIP / PJSIP / both
- AAVA commit/branch:
- Audio transport: AudioSocket / ExternalMedia
- AI provider/context used:

## Setup

- [ ] `VICIDIAL_API_USER` and `VICIDIAL_API_PASS` set in `.env`
- [ ] ViciDial API user allows the configured `source`
- [ ] `integrations.vicidial.enabled=true`
- [ ] `VICIDIAL_RA_CALL_ID` set before `Stasis(...)`
- [ ] `VICIDIAL_RA_AGENT_USER` set before `Stasis(...)`
- [ ] `h` extension present in every ViciDial context

Call ID source:

- [ ] `CALLERID(name)`
- [ ] SIP header extracted in dialplan
- [ ] Other:

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
