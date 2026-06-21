# Experimental ViciDial Integration Notes

Community-tested notes for exploring Asterisk AI Voice Agent with ViciDial in non-production environments.

> **Status: Experimental / Community-Tested**
>
> This guide reflects a limited setup tested with community users who requested a ViciDial path. It has not been reviewed or endorsed by ViciDial maintainers and should not be treated as production-ready ViciDial integration guidance.
>
> A ViciDial maintainer has raised concerns that direct AAVA/ARI origination through ViciDial dialplan contexts may bypass ViciDial's normal call-control, reporting, hangup processing, and compliance safeguards.
>
> For production ViciDial environments, the safer intended direction is for ViciDial to own call origination/routing and hand connected calls to AAVA through ViciDial Remote Agents, with follow-up transfer/control handled through ViciDial's Agent API such as `ra_call_control`.

## 1. Overview

ViciDial is a popular open-source contact center suite built on Asterisk. These notes describe an experimental AAVA path that uses the **Asterisk REST Interface (ARI)**, the same mechanism used for FreePBX, against a ViciDial-style Asterisk environment. ViciDial has its own dialplan conventions, channel technologies, routing patterns, reporting, and call-state lifecycle, so validate carefully before using any part of this outside a lab.

These notes cover:

- Inbound call routing (DID → AI agent)
- Experimental outbound campaign dialing (AAVA scheduler → ViciDial carrier trunks)
- Environment variable configuration
- Dialplan contexts required in `extensions.conf`
- Troubleshooting common issues
- Future production direction using ViciDial Remote Agents

### Key Differences: ViciDial vs FreePBX

| Feature | FreePBX | ViciDial |
| --- | --- | --- |
| Outbound dial context | `from-internal` | `default` |
| Dial prefix | None (trunk selected by route) | Carrier prefix (e.g. `911`, `913`) |
| Channel technology | PJSIP (`pjsip` module) | SIP (`chan_sip`) |
| Extension routing vars | `AMPUSER`, `FROMEXTEN` | Not used |
| Call origination | ARI only | AMI (ViciDial native); ARI/AAVA path below is experimental |
| Trunk configuration | FreePBX GUI → Trunks | `/etc/asterisk/sip.conf` peers |

## 2. Prerequisites

### 2.1 System Requirements

- ViciDial installation with **Asterisk 18+** (tested with Asterisk 18.26.4-vici)
- Docker and Docker Compose installed on the ViciDial server (or a co-located host)
- Repository cloned (e.g., `/root/Asterisk-AI-Voice-Agent`)
- ARI enabled in Asterisk (`/etc/asterisk/ari.conf`)
- HTTP server enabled (`/etc/asterisk/http.conf`)
- Port **8090/TCP** accessible for AudioSocket connections
- Valid `.env` containing ARI credentials and provider API keys

### 2.2 Enable and Configure ARI

ViciDial does not enable ARI by default. You need to create or verify the ARI configuration.

**`/etc/asterisk/ari.conf`**:

```ini
[general]
enabled = yes
pretty = yes

[admin]
type = user
read_only = no
password = your_secure_password_here
```

**`/etc/asterisk/http.conf`** (must be enabled for ARI):

```ini
[general]
enabled=yes
bindaddr=127.0.0.1
bindport=8088
```

Security note for remote deployments:
- Keep `bindaddr=127.0.0.1` unless remote ARI access is explicitly required.
- If remote access is required, bind to a specific trusted interface (not `0.0.0.0` when possible), restrict `bindport` with firewall/ACL rules, and prefer TLS (`ASTERISK_ARI_SCHEME=https`) with certificate validation.

After editing, reload Asterisk:

```bash
asterisk -rx "core reload"
```

Verify ARI is working:

```bash
export ARI_USER="${ASTERISK_ARI_USERNAME}"
export ARI_PASS="${ASTERISK_ARI_PASSWORD}"
curl -s -u "$ARI_USER:$ARI_PASS" http://127.0.0.1:8088/ari/asterisk/info | head -5
```

### 2.3 Verify Asterisk Modules

ViciDial typically uses `chan_sip` rather than `pjsip`. Confirm which modules are loaded:

```bash
asterisk -rx "module show like sip"
```

You should see `chan_sip.so` loaded. If you also see `res_pjsip.so`, both are available — but ViciDial trunks are usually configured as `chan_sip` peers.

## 3. Dialplan Configuration

For lab testing, add these two contexts to your `/etc/asterisk/extensions.conf`. They handle inbound AI agent calls and an experimental outbound campaign AMD (Answering Machine Detection) handoff.

### 3.1 Inbound Context: `[from-ai-agent]`

This context routes inbound calls to the AAVA Stasis application. Point your DID or inbound route to this context.

```ini
[from-ai-agent]
exten => s,1,NoOp(AI Agent Call)
 same => n,Set(AI_CONTEXT=default)
 same => n,Set(AI_PROVIDER=local_hybrid)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

**Customization**:

- `AI_CONTEXT`: Set to the name of an AI context defined in `config/ai-agent.yaml` (e.g., `default`, `sales`, `support`). See [Configuration-Reference.md](Configuration-Reference.md).
- `AI_PROVIDER`: Set to the provider or pipeline to use (e.g., `deepgram`, `local_hybrid`, `openai_realtime`).

### 3.2 Outbound AMD Context: `[aava-outbound-amd]`

This context is the experimental handoff point after an outbound call is answered. AAVA originates the call, the dialplan processes AMD, then hands the call to the Stasis application.

#### Option A: Direct Connect (No AMD)

Simplest setup — skips answering machine detection and connects the called party directly to the AI agent:

```ini
[aava-outbound-amd]
; Direct connection to AI agent — no AMD, no consent gate, no voicemail
exten => s,1,NoOp(AAVA Outbound Direct Connect)
 same => n,NoOp(Attempt=${AAVA_ATTEMPT_ID} Campaign=${AAVA_CAMPAIGN_ID} Lead=${AAVA_LEAD_ID})
 same => n,Answer()
 same => n,Stasis(asterisk-ai-voice-agent,outbound_amd,${AAVA_ATTEMPT_ID},HUMAN,DIRECT,,skipped)
 same => n,Hangup()
```

#### Option B: Full AMD with Voicemail Drop and Consent Gate

More complete test setup with answering machine detection, voicemail drop, and optional DTMF consent gate:

```ini
[aava-outbound-amd]
exten => s,1,NoOp(AAVA Outbound AMD hop)
 same => n,NoOp(Attempt=${AAVA_ATTEMPT_ID} Campaign=${AAVA_CAMPAIGN_ID} Lead=${AAVA_LEAD_ID})
 same => n,ExecIf($["${AAVA_AMD_OPTS}" = ""]?Set(AAVA_AMD_OPTS=2000,2000,1000,5000))
 same => n,AMD(${AAVA_AMD_OPTS})
 same => n,NoOp(AMDSTATUS=${AMDSTATUS} AMDCAUSE=${AMDCAUSE})
 ; Guardrails: reduce false MACHINE on silent humans
 same => n,GotoIf($["${AMDCAUSE:0:7}" = "TOOLONG"]?human)
 same => n,GotoIf($["${AMDCAUSE:0:14}" = "INITIALSILENCE"]?human)
 same => n,GotoIf($["${AMDSTATUS}" = "HUMAN"]?human)
 same => n,GotoIf($["${AMDSTATUS}" = "NOTSURE"]?machine)
 ; MACHINE path: optional voicemail drop
 same => n(machine),GotoIf($["${AAVA_VM_ENABLED}" = "1"]?vm:machine_done)
 same => n(vm),WaitForSilence(1500,3,10)
 same => n(machine_done),Stasis(asterisk-ai-voice-agent,outbound_amd,${AAVA_ATTEMPT_ID},MACHINE,${AMDCAUSE},,)
 same => n,Hangup()
 ; HUMAN path: optional consent gate (DTMF 1 accept / 2 deny)
 same => n(human),GotoIf($["${AAVA_CONSENT_ENABLED}" = "1"]?consent:human_done)
 same => n(consent),Set(TIMEOUT(response)=${IF($["${AAVA_CONSENT_TIMEOUT}"=""]?5:${AAVA_CONSENT_TIMEOUT})})
 same => n,NoOp(AAVA CONSENT enabled=${AAVA_CONSENT_ENABLED} timeout=${AAVA_CONSENT_TIMEOUT} playback=${AAVA_CONSENT_PLAYBACK})
 ; IMPORTANT: Use Read() with a prompt so DTMF is captured while the consent message plays.
 ; If we Playback() then Read(), DTMF pressed during Playback is consumed and Read() times out.
 same => n,Read(AAVA_CONSENT_DTMF,${AAVA_CONSENT_PLAYBACK},1)
 same => n,NoOp(AAVA CONSENT dtmf=${AAVA_CONSENT_DTMF})
 same => n,GotoIf($["${AAVA_CONSENT_DTMF}" = "1"]?human_ok)
 same => n,GotoIf($["${AAVA_CONSENT_DTMF}" = "2"]?human_denied)
 same => n(human_timeout),Stasis(asterisk-ai-voice-agent,outbound_amd,${AAVA_ATTEMPT_ID},HUMAN,${AMDCAUSE},,timeout)
 same => n,Hangup()
 same => n(human_denied),Stasis(asterisk-ai-voice-agent,outbound_amd,${AAVA_ATTEMPT_ID},HUMAN,${AMDCAUSE},2,denied)
 same => n,Hangup()
 same => n(human_ok),Stasis(asterisk-ai-voice-agent,outbound_amd,${AAVA_ATTEMPT_ID},HUMAN,${AMDCAUSE},1,accepted)
 same => n,Hangup()
 same => n(human_done),Stasis(asterisk-ai-voice-agent,outbound_amd,${AAVA_ATTEMPT_ID},HUMAN,${AMDCAUSE},,skipped)
 same => n,Hangup()
```

After adding the contexts, reload the dialplan:

```bash
asterisk -rx "dialplan reload"
```

## 4. Environment Variable Configuration

For lab testing, add these to your `.env` file in the AAVA project root. These tell the `ai_engine` how to originate outbound calls through ViciDial's Asterisk dialplan.

### 4.1 ARI Connection (same as FreePBX)

```env
ASTERISK_ARI_USERNAME=admin
ASTERISK_ARI_PASSWORD=your_secure_password_here
ASTERISK_ARI_PORT=8088
```

### 4.2 Experimental ViciDial Outbound Settings

```env
# Experimental/community-tested only; skips FreePBX-specific AMPUSER/FROMEXTEN vars
AAVA_OUTBOUND_PBX_TYPE=vicidial

# ViciDial's outbound dial context (usually "default", which includes carrier routes)
AAVA_OUTBOUND_DIAL_CONTEXT=default

# Carrier dial prefix — must match a pattern in your ViciDial dialplan
# This is the same prefix configured in ViciDial Admin → Campaigns → Dial Prefix
AAVA_OUTBOUND_DIAL_PREFIX=913

# ViciDial uses chan_sip, not pjsip
AAVA_OUTBOUND_CHANNEL_TECH=sip

# Extension identity for caller ID on outbound calls
AAVA_OUTBOUND_EXTENSION_IDENTITY=1000
```

### 4.3 Finding Your Dial Prefix

The dial prefix must match a carrier route pattern in your ViciDial `extensions-vicidial.conf`. To find what prefixes are available:

```bash
grep -n '^exten => _9' /etc/asterisk/extensions-vicidial.conf
```

Example output:

```text
171:exten => _911.,1,AGI(agi://127.0.0.1:4577/call_log)
181:exten => _912.,1,AGI(agi://127.0.0.1:4577/call_log)
187:exten => _913.,1,AGI(agi://127.0.0.1:4577/call_log)
```

Each prefix routes to a different carrier trunk. Check which one your ViciDial campaigns use:

```bash
mysql asterisk -e "SELECT campaign_id, campaign_name, dial_prefix FROM vicidial_campaigns;"
```

Use the same prefix, or the one that routes through your preferred carrier.

> **Tip**: If a carrier returns `SIP 428 Use Identity Header`, that route requires STIR/SHAKEN authentication. Try a different dial prefix that routes through a carrier without that requirement.

### 4.4 Environment Variable Reference

| Variable | Default | ViciDial Value | Description |
| --- | --- | --- | --- |
| `AAVA_OUTBOUND_PBX_TYPE` | `freepbx` | `vicidial` | Controls FreePBX-specific channel vars. `vicidial` is experimental/community-tested; `generic` is available for non-FreePBX systems. |
| `AAVA_OUTBOUND_DIAL_CONTEXT` | `from-internal` | `default` | Asterisk dialplan context for `Local/` channel origination. |
| `AAVA_OUTBOUND_DIAL_PREFIX` | *(empty)* | e.g. `913` | Prefix prepended to phone number. Must match a carrier pattern in your dialplan. |
| `AAVA_OUTBOUND_CHANNEL_TECH` | `auto` | `sip` | Channel technology for internal extension probing. `auto` tries PJSIP then SIP. `sip` for chan_sip only. `local_only` skips probing. |
| `AAVA_OUTBOUND_EXTENSION_IDENTITY` | `6789` | e.g. `1000` | Extension identity for caller ID on outbound calls. |
| `AAVA_OUTBOUND_AMD_CONTEXT` | `aava-outbound-amd` | `aava-outbound-amd` | Dialplan context for AMD hop (usually same for both). |

These settings are also configurable via the **Admin UI** → **System** → **Environment Variables** → **Outbound Campaign** section. The Admin UI labels this path as experimental.

## 5. How Outbound Dialing Works

In the experimental ARI-originated path, when AAVA's outbound scheduler fires a campaign call, the following happens:

```text
1. ai_engine reads campaign + lead from DB
2. Constructs endpoint: Local/<prefix><phone>@<context>
   Example: Local/915551234567@default
3. ARI POST /channels (originate) with channel variables:
   - AAVA_OUTBOUND=1
   - AAVA_CAMPAIGN_ID, AAVA_LEAD_ID, AAVA_ATTEMPT_ID
   - CALLERID(num), CALLERID(name)
   - AI_CONTEXT (from campaign settings)
4. Asterisk routes through [default] context:
   - Matches _913. pattern in [vicidial-auto-external]
   - Dials SIP/carrier_trunk/<carrier_prefix><phone>
5. Called party answers → Asterisk executes [aava-outbound-amd]
6. AMD context hands channel to Stasis(asterisk-ai-voice-agent)
7. ai_engine handles AI conversation (greeting, STT, LLM, TTS)
```

> **Important:** This flow may bypass parts of ViciDial's normal call-control, reporting, hangup processing, and compliance handling. Treat it as a community-tested lab path, not a ViciDial-native production design.

### Call Flow Diagram

```text
┌──────────┐    ARI originate     ┌──────────────┐
│ ai_engine │ ──────────────────→ │   Asterisk    │
│           │                     │  [default]    │
│           │                     │  _913. match  │
│           │                     │      ↓        │
│           │                     │ SIP/carrier   │
│           │                     │      ↓        │
│           │    Stasis handoff   │ [aava-outbound│
│           │ ←────────────────── │  -amd]        │
│           │                     └──────────────┘
│  AI call  │ ←──── AudioSocket ────→ │ Asterisk │
│  handling │    (bidirectional audio) │  bridge  │
└──────────┘                          └──────────┘
```

## 6. Deployment Steps

### 6.1 Initial Setup

```bash
# Clone or update the repository
cd /root/Asterisk-AI-Voice-Agent
git checkout main
git pull origin main

# Copy example env and configure
cp .env.example .env
# Edit .env with your ARI credentials and experimental ViciDial settings (see Section 4)
```

### 6.2 Add Dialplan Contexts

Edit `/etc/asterisk/extensions.conf` and add the `[from-ai-agent]` and `[aava-outbound-amd]` contexts from Section 3.

```bash
# Reload dialplan
asterisk -rx "dialplan reload"

# Verify contexts are loaded
asterisk -rx "dialplan show from-ai-agent"
asterisk -rx "dialplan show aava-outbound-amd"
```

### 6.3 Build and Start Containers

```bash
# Build all services
docker compose build

# Start services
docker compose up -d

# Verify containers are running
docker ps
```

Expected output:

```text
NAMES             STATUS
ai_engine         Up X seconds
admin_ui          Up X seconds
local_ai_server   Up X seconds (healthy)
```

### 6.4 Verify ARI Connection

```bash
docker logs --since 2m ai_engine 2>&1 | grep -i "ARI\|connected\|outbound"
```

You should see:

```text
Successfully connected to ARI HTTP endpoint.
Successfully connected to ARI WebSocket.
Outbound scheduler started
```

## 7. Testing

### 7.1 Inbound Test

Route a DID or internal extension to the `[from-ai-agent]` context and place a call. Check logs:

```bash
docker logs --since 2m ai_engine 2>&1 | grep -i "stasis\|greeting\|streaming"
```

### 7.2 Outbound Test

1. Open the **Admin UI** (default: `http://<server>:3003`)
2. Navigate to **Call Scheduling**
3. Create a new campaign:
   - Set a campaign name
   - Choose an AI context
   - Configure caller ID
4. Import leads (add at least one phone number)
5. Start the campaign
6. Watch the logs:

```bash
# ai_engine logs (call lifecycle)
docker logs -f ai_engine 2>&1 | grep -i "outbound\|originate\|stasis\|amd"

# Asterisk CLI (dialplan execution)
asterisk -rvvv
```

A successful outbound call log should show:

```text
Outbound originate → endpoint=Local/915551234567@default
Called 915551234567@default
SIP/carrier answered
HYBRID ARI - StasisStart event received → args=['outbound_amd', ...]
Outbound AMD result → amd_status=HUMAN
HYBRID ARI - Caller channel entered Stasis
STREAMING OUTBOUND - Setup → stream_id=stream:greeting:...
```

## 8. Troubleshooting

### SIP 428 Use Identity Header

```text
WARNING: chan_sip.c: SIP identity required by proxy. Giving up.
```

**Cause**: The carrier trunk requires STIR/SHAKEN identity headers.

**Fix**: Use a different dial prefix that routes through a carrier without STIR/SHAKEN requirements. Check available prefixes with:

```bash
grep -n '^exten => _9' /etc/asterisk/extensions-vicidial.conf
```

### Outbound Call Not Routing

If the call never reaches Asterisk:

1. Verify ARI connection: `docker logs ai_engine 2>&1 | grep "ARI"`
2. Verify env vars: `docker exec ai_engine env | grep AAVA_OUTBOUND`
3. Verify dialplan context exists: `asterisk -rx "dialplan show default"`

### Call Routes But Does Not Enter Stasis

If the carrier call connects but the AI agent never picks up:

1. Verify the `[aava-outbound-amd]` context exists: `asterisk -rx "dialplan show aava-outbound-amd"`
2. Check that `AAVA_OUTBOUND_AMD_CONTEXT` matches the context name in your dialplan
3. Verify the Stasis app name matches: `asterisk -rx "stasis show apps"`

### chan_sip vs pjsip

ViciDial typically uses `chan_sip`. If you see errors about PJSIP endpoints not found:

```env
# Force SIP-only probing (no PJSIP)
AAVA_OUTBOUND_CHANNEL_TECH=sip

# Or skip endpoint probing entirely (always use Local/ channel)
AAVA_OUTBOUND_CHANNEL_TECH=local_only
```

### AudioSocket Not Connecting

Verify the AudioSocket server is listening:

```bash
docker logs ai_engine 2>&1 | grep "AudioSocket"
```

Ensure port 8090/TCP is accessible from Asterisk to the ai_engine container.

## 9. Recommended Future Direction

The intended production direction is a ViciDial-native Remote Agent design:

1. ViciDial owns inbound/outbound call handling, campaign logic, routing, reporting, compliance behavior, and hangup processing.
2. When a call is connected, ViciDial sends it to a Remote Agent extension.
3. That extension invokes AAVA for the AI voice interaction.
4. When the AI interaction is complete, AAVA uses ViciDial's Agent API, such as `ra_call_control`, to transfer the call to an Ingroup or external destination.

This design should be reviewed with ViciDial maintainers or the official ViciDial forum before replacing these experimental notes.

## 10. Admin UI Configuration

The experimental ViciDial-specific settings are available in the Admin UI under **System** → **Environment Variables** → **Outbound Campaign (Alpha)**:

- **PBX Type**: Select "ViciDial (experimental)"
- **Dial Context**: Set to your ViciDial outbound context (usually `default`)
- **Dial Prefix**: Set to your carrier prefix (e.g. `913`)
- **Channel Tech**: Select "SIP only (chan_sip)" for ViciDial

Changes made in the Admin UI are saved to the `.env` file and take effect after restarting the ai_engine container.

## 11. Files Reference

| File | Purpose |
| --- | --- |
| `.env` | Environment variables including experimental ViciDial-specific outbound settings |
| `.env.example` | Documented example with all available variables |
| `src/engine.py` | Core engine — outbound origination logic, endpoint selection, channel vars |
| `config/ai-agent.yaml` | AI context definitions, provider configuration, audio profiles |
| `admin_ui/frontend/src/pages/System/EnvPage.tsx` | Admin UI environment variable editor |
| `/etc/asterisk/extensions.conf` | Asterisk dialplan — add `[from-ai-agent]` and `[aava-outbound-amd]` here |
| `/etc/asterisk/ari.conf` | ARI user configuration |
| `/etc/asterisk/http.conf` | Asterisk HTTP server (required for ARI) |
| `/etc/asterisk/extensions-vicidial.conf` | ViciDial carrier route patterns (read-only reference) |

## 12. Related Documentation

- [INSTALLATION.md](INSTALLATION.md) — First-time installation
- [Configuration-Reference.md](Configuration-Reference.md) — Full configuration reference
- [FreePBX-Integration-Guide.md](FreePBX-Integration-Guide.md) — FreePBX-specific setup
- [OUTBOUND_CALLING.md](OUTBOUND_CALLING.md) — Outbound campaign dialer documentation
- [Transport-Mode-Compatibility.md](Transport-Mode-Compatibility.md) — Audio transport options
