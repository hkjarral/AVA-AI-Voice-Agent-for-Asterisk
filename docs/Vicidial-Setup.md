# VICIdial Remote Agent Integration

Connect VICIdial to AAVA by making AAVA a VICIdial **Remote Agent**. VICIdial remains the
system of record for campaigns, dialing, customer channels, agent state, transfers,
dispositions, callbacks, DNC, and reports. AAVA supplies the conversation for the Remote Agent
leg and asks VICIdial to complete or transfer the call through `ra_call_control`.

This replaces the deprecated experiment in which AAVA originated calls through VICIdial's
carrier dialplan. Do not use `AAVA_OUTBOUND_PBX_TYPE=vicidial` for new deployments.

## Support boundary

The first validated profile is a separate-box LAN/VPN deployment:

- VICIdial/ViciBox 12: VICIdial `2.14b0.5`, schema `1723`, revision `3896`, Asterisk
  `18.26.4-vici`, `chan_sip`
- AAVA/FreePBX 17: Asterisk `18.26.4`, `chan_pjsip`
- two concurrent Remote Agent lines: users `9001`–`9002`, sharing VICIdial
  Phone/extension `8371`
- one AAVA Agent mapping: `9001`–`9002 / 8371` → `demo_deepgram`

Treat other VICIdial releases, Asterisk versions, multi-server dialers, NAT/public Internet
topologies and PJSIP-on-VICIdial as separate acceptance profiles.
VICIdial supports PJSIP on sufficiently recent builds, but it changes the PBX setup and must be
tested independently.

VICIdial's official documentation notes that Remote Agents can be used for outbound auto-dial,
but older maintainer guidance warns about added delivery latency. Test pacing, abandonment,
answer-to-agent delay, and applicable calling regulations with your real carrier and workload.

## Architecture and ownership

```text
customer
   │
   │ existing VICIdial carrier / DID
   ▼
VICIdial campaign or inbound group
   │
   │ customer answered and assigned to Remote Agent 9001
   │ SIP call to Phone 8371; VICIdial call ID in Caller-ID name
   ▼
AAVA FreePBX trunk → exact trusted context/extension → AAVA Agent
   │
   ├─ normal completion ─────► Agent API ra_call_control/HANGUP + status
   ├─ in-group transfer ─────► Agent API ra_call_control/INGROUPTRANSFER
   ├─ extension transfer ────► Agent API ra_call_control/EXTENSIONTRANSFER
   ├─ DNC ───────────────────► Non-Agent API add_dnc_phone, then HANGUP
   └─ callback ──────────────► Non-Agent API update_lead + verification, then HANGUP
```

AAVA never changes production VICIdial database tables. The SQL examples in the lab section are
test fixtures only. Production configuration belongs in the VICIdial and FreePBX web interfaces.

## Prerequisites

- A working VICIdial campaign and carrier for outbound calls, or a working DID/in-group for
  inbound calls.
- Routed IP connectivity between both PBXs for SIP and RTP.
- A unique VICIdial Phone and SIP secret for the AAVA Remote Agent leg.
- An active VICIdial user range reserved for AAVA.
- An active AAVA Agent slug.
- HTTPS for VICIdial APIs where practical; otherwise keep API traffic on a private LAN/VPN.
- Time synchronization on both PBXs and the correct VICIdial IANA timezone in AAVA.
- Statuses of no more than six characters created in VICIdial before mapping them in AAVA.

## 1. Prepare VICIdial

Field labels vary slightly by VICIdial build. Use the VICIdial Admin help for the installed
version when a label differs.

### 1.1 Create the Remote Agent Phone

In **Admin → Phones → Add A New Phone**:

- Extension: a dedicated value such as `8371`
- Dialplan Number: normally the same value
- Server IP: the VICIdial dialer that will call AAVA
- Protocol: `SIP` for the validated ViciBox profile
- Active: `Y`
- Host: dynamic/registration-based when AAVA sends a registration
- Direct media/reinvite: disabled
- Codec: `ulaw` for the baseline profile
- DTMF: RFC2833/RFC4733
- Qualify: enabled when OPTIONS works in both directions

Use the generated **`conf_secret`** as the SIP registration secret. The Phone `pass` field is not
the SIP secret. Give every Phone a unique secret and never place it in the AAVA mapping database,
screenshots, logs, or support bundles.

### 1.2 Create the Remote Agent user range

Create a dedicated VICIdial User, for example `9001`. For concurrency greater than one, reserve
a contiguous user range and create every user before increasing the mapping's line count.
VICIdial deliberately increments the user for each line while reusing the same Remote Agent
`conf_exten`. For example, two lines starting at `9001` require users `9001` and `9002`, but both
use Phone/registration `8371`; do not create Phone `8372`. The Non-Agent API's `agent_status`
must recognize every user in the range. A row in `vicidial_live_agents` alone is not sufficient.

The user must be active and permitted to use the selected campaign/in-groups. Do not reuse a
human's login. The Remote Agent user and the API user are different accounts.

### 1.3 Create the Remote Agent

In **Admin → Remote Agents** create or update:

- User Start: `9001`
- Number of Lines: `1` initially
- Phone Login/extension (`conf_exten`): `8371`
- Status: `ACTIVE`
- Campaign: the outbound campaign, for example `AVATEST`
- On-Hook Agent: `N` for the validated classic outbound mode
- Closer Campaigns: selected in-groups for inbound/closer delivery

`On-Hook Agent=Y` changes the inbound Remote Agent lifecycle and is not a substitute for the
classic outbound mode. Validate it only as part of a separate inbound acceptance profile.

For a mapping that handles both outbound and inbound calls, set the outbound campaign's
**Allow Inbound and Blended** field to `Y` and select the same inbound groups on the campaign and
Remote Agent. For an inbound-only Remote Agent, select VICIdial's `CLOSER` campaign and the
desired inbound groups. The installed VICIdial help defines `CLOSER` for inbound-only Remote
Agents; it is a VICIdial mode, not an AAVA campaign that must be created.

For lab rows created with SQL, `closer_campaigns` must be an empty string when unused, not
`NULL`. Use the UI in production.

For the first outbound acceptance test, set the campaign **Drop Call Seconds** high enough for
the customer-answer-to-Remote-Agent delivery path. The validated lab uses `30` seconds; its
original `5` second value produced `DROP / QUEUETIMEOUT` while the development Remote Agent was
being recycled. Measure the real answer-to-agent delay, abandonment policy, and regulatory
requirements before tuning this value for production. Do not treat `30` as a universal pacing
default.

### 1.4 Create statuses and campaign disposition choices

Create explicit statuses for AAVA lifecycle outcomes and make them available to the campaign.
The defaults in AAVA are:

| Meaning | Default status |
| --- | --- |
| AAVA graceful hangup | `AIHU` |
| Customer hung up | `AICU` |
| In-group transfer | `AIXFR` |
| Extension transfer | `AIEXT` |
| AAVA/control failure | `AIFAIL` |
| Do not call | `DNC` |
| Scheduled callback | `CALLBK` |

Add business outcomes such as `SALE` or `NI` only when they already exist and are valid for the
campaign. AAVA exposes only the dispositions configured in the mapping.

### 1.5 Create a least-privilege API user

Create a dedicated API user. For the validated build it needs user level 8, Agent API access,
View Reports, Modify Lists, and Modify Leads. Restrict its function allowlist to:

```text
campaigns_list callid_info agent_status logged_in_agents ra_call_control
add_dnc_phone update_lead lead_callback_info
```

Do not grant `ALL_FUNCTIONS` after setup. AAVA's connection check performs only read-only calls;
it does not probe mutating functions.

## 2. Configure the AAVA-facing trunk in FreePBX

In **Connectivity → Trunks → Add SIP (chan_pjsip) Trunk**, create `vicidial-ra`.

### General/registration settings

| Field | Value |
| --- | --- |
| Username | Remote Agent Phone extension, e.g. `8371` |
| Auth username | same as Username |
| Secret | VICIdial Phone `conf_secret` |
| Authentication | Outbound |
| Registration | Send |
| SIP server | VICIdial dialer IP/FQDN |
| SIP port | normally `5060` |
| Context | a dedicated context such as `from-vicidial-ra` |
| Contact user | Remote Agent Phone extension |
| Match/permit | exact VICIdial signaling address when supported |
| Codec | `ulaw` baseline |
| Direct media | No |

Choose the FreePBX PJSIP transport that reaches VICIdial. On a multi-homed host, this may require
a dedicated LAN transport. Apply Config, then confirm both sides:

```bash
# AAVA/FreePBX
asterisk -rx "pjsip show registrations"
asterisk -rx "pjsip show endpoint vicidial-ra"

# VICIdial dialer
asterisk -rx "sip show peer 8371"
```

One green sample is insufficient. Observe at least two registration/qualification cycles and
confirm the contact address is the intended AAVA interface.

When more than one registered `chan_sip` Phone arrives from the same NAT address, the VICIdial
server may identify the INVITE by source address or `From` instead of the authenticated username.
On affected Asterisk 18 `chan_sip` systems, review the installed `sip.conf.sample` and consider
`match_auth_username=yes`. This is a global peer-matching behavior change: back up the generated
configuration, apply it through the VICIdial/ViciBox-supported mechanism, reload, and retest all
affected registrations rather than treating it as a universal default.

## 3. Create the AAVA connection and mapping

Open **Admin → Call Scheduling → VICIdial Remote Agents**.

### 3.1 API connection

Add a connection with:

- VICIdial base URL, normally `https://dialer.example.com`
- VICIdial SIP host and port
- network topology: LAN/VPN, AAVA behind NAT, or public/SBC
- the VICIdial server's IANA timezone, for example `America/Phoenix`
- API credential environment-variable names, not credential values
- SIP/RTP ports used by the two PBXs

Set the referenced variables in the AAVA deployment environment:

```env
VICIDIAL_API_USER=<dedicated API username>
VICIDIAL_API_PASS=<dedicated API password>
```

Recreate the `ai_engine` and `admin_ui` containers after changing environment variables. Click
**Verify API**. The check must pass both API version endpoints, authentication, campaign listing,
and logged-agent visibility.

### 3.2 Remote Agent mapping

Create the initial mapping:

- Direction: outbound, inbound, or both
- VICIdial campaign ID and any inbound/closer campaigns
- Starting Remote Agent user: `9001`
- Number of lines: `1`
- Remote Agent extension: `8371`
- One-line fallback user: `9001` only for a one-line mapping
- AAVA Agent: `demo_deepgram` or another active Agent slug
- Trusted context: `from-vicidial-ra`
- Trusted endpoint: `vicidial-ra` where `CHANNEL(endpoint)` is available
- allowed dispositions, lifecycle statuses, DNC/callback policy, and cold-transfer destinations

The one-line fallback still requires `agent_status.callerid` to exactly match the live VICIdial
call ID. It does not trust a static user without call correlation and is not used for multi-line
mappings. Some VICIdial builds replace `CALLERID(name)` with the customer's display name before
dialing the Remote Agent. AAVA handles that variance by scanning only the mapping's configured
Remote Agent user range, validating each active agent's VICIdial call code with `callid_info`,
enforcing campaign and direction, and accepting exactly one match. Missing or ambiguous matches
fail closed.

For an outbound auto call, `callid_info.user` can be `VDAD`; this is the dialer owner, not the
Remote Agent user. AAVA obtains the Remote Agent identity from the mapped `agent_status` record and
joins the two API views by their exact VICIdial call code, campaign, direction, and phone number.

If the customer disconnects first, VICIdial may finalize the customer record before the Remote
Agent SIP leg leaves Stasis. A late `ra_call_control` then correctly reports that no active call
exists. AAVA reconciles `callid_info` with the mapped agent. The agent is considered released when
its call ID is cleared or when `agent_status.real_time_sub_status` is `DEAD`; VICIdial uses
`DEAD` after the active-call row disappears and before the live-agent cleanup cycle returns the
agent to `READY`. AAVA records VICIdial's actual terminal status (for example `XFER` with a
caller-side termination) and does not claim that the configured AAVA status was written.
AI-initiated hangup and explicit disposition still use `ra_call_control` while the call is active.

### 3.3 Apply the generated dialplan

Open **Setup guide**, copy the generated context into
`/etc/asterisk/extensions_custom.conf`, and reload the dialplan. The generated extension is exact,
not a wildcard. A representative context is:

```ini
[from-vicidial-ra]
exten => 8371,1,NoOp(VICIdial Remote Agent call: ${CALLERID(all)})
 same => n,GotoIf($["${CHANNEL(endpoint)}"="vicidial-ra"]?trusted:reject)
 same => n(reject),Hangup(21)
 same => n(trusted),NoOp(Trusted VICIdial endpoint accepted)
 same => n,Set(__AAVA_CALL_OWNER=vicidial)
 same => n,Set(__VICIDIAL_RA_CALL_ID=${CALLERID(name)})
 same => n,Set(__VICIDIAL_MAPPING_ID=<mapping UUID>)
 same => n,Set(__AI_AGENT=demo_deepgram)
 same => n,Answer()
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

Always use the generated mapping UUID. Do not copy the placeholder above. When the SIP leg carries
a valid VICIdial call ID, AAVA confirms it with `callid_info` and exact
`agent_status.callerid` correlation. If the leg carries a customer display name instead, AAVA uses
the bounded mapped-user lookup described above. Failed or ambiguous correlation is rejected; the
call does not continue as an uncontrolled ordinary AAVA call.

## 4. Network and NAT profiles

### LAN or site-to-site VPN (recommended)

- Use private routed addresses.
- Add both PBX subnets to each Asterisk `local_net` configuration where applicable.
- Permit SIP signaling and the configured RTP ranges only between the two PBX addresses.
- Disable direct media so each PBX remains in its owned media path.
- Disable SIP ALG.

### AAVA behind NAT

- Prefer outbound PJSIP registration so VICIdial learns the current AAVA contact.
- Set FreePBX/Asterisk external signaling and media addresses plus every internal `local_net`.
- Use `rtp_symmetric`, `force_rport`, and `rewrite_contact` as required by the topology.
- Forward the chosen SIP port and RTP range to AAVA, restricted to VICIdial where possible.
- Inspect SDP on both answer paths; a successful registration does not prove correct media NAT.

### Public/SBC

- Prefer a VPN or an SBC with TLS/SRTP rather than exposing either PBX directly.
- Restrict signaling, RTP, and API access to known peers.
- Use certificates with hostname verification for API HTTPS.
- Test failover, re-registration, symmetric routing, and source-address identification.

Do not advertise a topology as supported until its own two-way-media and lifecycle matrix passes.

## 5. How completion and disposition work

VICIdial does not use AAVA's ordinary ARI hangup path for these calls.

1. AAVA resolves the live call ID, Remote Agent user, lead, campaign, phone, and direction.
2. The Agent may select one configured business disposition during the conversation.
3. DNC and callback side effects are deferred until terminal completion.
4. On completion, AAVA performs and verifies any required side effect.
5. AAVA calls `ra_call_control` with `stage=HANGUP` and the requested/default status.
6. A VICIdial success response marks the requested status confirmed. If the customer ended first
   and the active call is gone, AAVA may instead confirm the exact terminal call log against the
   mapped agent's released/`DEAD` state and records the observed native status.
7. Any API rejection that cannot pass that exact terminal reconciliation remains visible as
   unconfirmed; AAVA never reports an ARI fallback as a successful VICIdial disposition.

For transfers, AAVA uses `INGROUPTRANSFER` or `EXTENSIONTRANSFER`. These are cold transfers; the
Remote Agent leg disconnects after VICIdial accepts the transfer. Warm/consultative Remote Agent
transfer is not offered by `ra_call_control`.

### DNC

The `dnc` choice appears only when explicitly allowlisted. AAVA calls `add_dnc_phone` for the
mapped campaign or `SYSTEM_INTERNAL`; an “already exists” response is treated as idempotent
success. In the acceptance lab, confirm the resulting DNC row as well as the final call status.

### Scheduled callback

The `callback` choice appears only when explicitly allowlisted. Offset-aware ISO times are
converted to the configured VICIdial timezone; naive times are treated as VICIdial-local time.
AAVA creates the callback with `update_lead`, queries `lead_callback_info`, and requires an exact
active callback match before requesting terminal HANGUP with the callback status.

### Readiness

**Run checks** distinguishes configuration readiness from live-call readiness. A mapping becomes
Ready only after every configured direction has completed a correlated call with a confirmed
VICIdial terminal action. Registration, two-way audio, DTMF, transfers, DNC/callback effects, and
report rows still belong in the deployment acceptance record.

For multi-line mappings, **Run checks** calls `agent_status` for every user and separately requires
all users to appear in `logged_in_agents`. This prevents a stale or manually inserted live-agent
row from making an uncreated VICIdial user look ready.

## 6. Lab-only customer leg

Never test a campaign with a carrier that only `Answer()`s locally or nests another `Local/`
channel. VICIdial needs a real non-Local customer channel for answer routing and correlation.

The voiprnd lab uses a separate dynamic Phone/registration `8381` for the customer leg. It is not
the Remote Agent Phone `8371`. The `LOOPTEST` carrier's test pattern dials `SIP/8381`; voiprnd
routes exact inbound extension `8381` to the human test extension `2765`.

For inbound fixtures, set the lab Phone's outbound caller ID to a valid test number that matches
the lead format expected by the VICIdial country-code settings. Authentication still uses the
Phone extension. Keeping caller identity separate from SIP authentication prevents an extension
such as `8381` from being created as the lead phone number.

Representative lab carrier dialplan generated by VICIdial:

```ini
exten => _85XXXXXXXXX,1,NoOp(AAVA lab customer leg via SIP registration 8381)
 same => n,Dial(SIP/8381,60,)
 same => n,Hangup()
```

This carrier is only a PSTN substitute for an isolated lab. Existing VICIdial customers keep
their real carriers unchanged.

## 7. Acceptance sequence

Run checks in this order and retain a redacted evidence bundle tied to the AAVA commit and exact
VICIdial build:

1. Agent and Non-Agent API version/authentication.
2. Campaign, active AAVA Agent, mapping, and Remote Agent user range.
3. Stable SIP registrations and qualification on both PBXs.
4. Remote Agent visible, then READY.
5. Outbound customer answer → correct call/lead/user correlation → mapped AAVA Agent.
6. Inbound/closer delivery for mappings that enable it.
7. Two-way audio, no initial dead air beyond the measured Remote Agent delivery delay, and DTMF.
8. AAVA graceful hangup and customer-first hangup; verify VICIdial log/list status and READY return.
9. Configured in-group and extension cold transfers.
10. Normal business disposition, campaign/system DNC, and scheduled callback.
11. Busy, no-answer, AMD/drop, API timeout/rejection, AAVA restart, and VICIdial restart paths.
12. Multi-line concurrency only after one-line validation passes.

An AAVA engine restart during an active Remote Agent call releases the Asterisk and VICIdial
channels and returns the Remote Agent user to `READY`; the validated lab did not leave a stuck
call. The current call-history/session store is process-local, however, so that interrupted call
does not receive a completed AAVA Call History row after restart. Treat durable mid-call recovery
and reporting as a separate architecture enhancement, not as proof that the telephony teardown
failed.

Call History shows external call identity, direction, Remote Agent user, requested versus confirmed
disposition, and sanitized API evidence. Never retain API passwords, SIP secrets, or authorization
headers.

## 8. Troubleshooting

### Registration is rejected or flaps

- Verify the Phone `conf_secret`, not `pass`.
- Confirm transport, source address, contact user, expiration, and duplicate registrations.
- Confirm only one endpoint-identification rule owns the VICIdial source address.
- Inspect both PBXs during a single registration cycle; do not leave SIP logging enabled.

### Remote Agent remains PAUSED or disappears

- Confirm the Remote Agent is ACTIVE, the user/campaign exists, and the campaign is active.
- For SQL-built lab data, change `closer_campaigns` from `NULL` to an empty string when unused.
- Confirm the VICIdial keepalive and manager processes are running.
- Confirm the Phone is reachable from the dialer.

### Customer answers but AAVA never receives the call

- Confirm the customer carrier created a real SIP/PJSIP/IAX/PSTN channel, not a local answer loop.
- Confirm campaign prefix and generated carrier dialplan.
- Inspect `vicidial_log.term_reason`. `QUEUETIMEOUT` at the campaign's Drop Call Seconds value
  means VICIdial stopped waiting before the Remote Agent accepted the delivery; raise the value
  for a controlled acceptance test, then tune it using measured production behavior.
- Confirm Remote Agent user selection and extension `8371` in VICIdial logs/live tables.
- Confirm the INVITE reaches the exact FreePBX context and extension.

### AAVA rejects the call at admission

- Confirm the generated mapping UUID and owner variable are present.
- Confirm `CALLERID(name)` contains the VICIdial call ID unchanged.
- Query `callid_info` and `agent_status` with the dedicated API user; the returned user must be in
  the mapping range and `agent_status.callerid` must exactly match.
- For blended inbound calls, `callid_info.campaign_id` is the inbound group while
  `agent_status.campaign_id` is the agent's login/outbound campaign. Configure the inbound group
  under **Closer groups**; do not require those two campaign fields to be equal.

### One-way or no audio

- Compare SIP/SDP addresses with the real signaling path.
- Verify RTP firewall ranges on both hosts, `local_net`, external media address, and symmetric RTP.
- Disable direct media and SIP ALG.
- Verify codec agreement; begin with `ulaw` end to end.

### Final status is missing

- Inspect the Call History VICIdial evidence for API rejection or correlation failure.
- Confirm the API user's `ra_call_control` permission and status length/existence.
- Query VICIdial reports/logs and confirm the Remote Agent returned to READY.
- A transient `INCALL` with `real_time_sub_status=DEAD` after caller hangup is VICIdial's cleanup
  interval. Call History should retain the exact terminal status, and the agent must subsequently
  return to `READY`.
- Do not repair production reports with direct SQL; correct the API/status configuration and retest.

## 9. Disable and rollback

1. Disable the AAVA mapping; new VICIdial-owned calls are rejected.
2. Set the VICIdial Remote Agent INACTIVE or remove its campaign/in-group assignment.
3. Remove the generated AAVA dialplan context and reload the dialplan if decommissioning.
4. Disable/delete the FreePBX trunk registration.
5. Revoke the dedicated API user and rotate its password.
6. Leave production VICIdial carrier, lead, call-log, DNC, and callback data intact.

## Official references

- [VICIdial Agent API](https://vicidial.org/docs/AGENT_API.txt) — `ra_call_control`, Remote Agent
  call ID, HANGUP, and cold-transfer contract
- [VICIdial Non-Agent API](https://vicidial.org/docs/NON-AGENT_API.txt) — `callid_info`,
  `agent_status`, `logged_in_agents`, DNC, callback, and permissions
- [VICIdial PJSIP support](https://vicidial.org/docs/PJSIP_SUPPORT.txt) — build-dependent PJSIP
  enablement and carrier examples
- [VICIdial documentation index](https://vicidial.org/docs/)
