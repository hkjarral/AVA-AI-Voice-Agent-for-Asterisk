---
id: asterisk-ai
title: Asterisk AI Application Server
slug: /integrations/asterisk-ai/
sidebar_position: 1
---

### Overview

This guide will help you setup an Asterisk AI application server that has two way communication, though some entries are static, it will shed light on the usability of both FreePBX and FusionPBX.  The setup of this took two days of research, using various tools such as OpenAI, Claude, and Grok.

Further documentation and setup is found by going to: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/

At the time of writing this, the development was fairly active, and offers a Discord as well for support.  We go into detail on the bottom of this page for supporting the maintainer, as well as their support Discord.

### Prerequisites 

### Servers
- 2 vCPU/4G of ram/50G of space - FS PBX
- 2 vCPU/8G of ram/50G of space - FreePBX & Asterisk AI

FreePBX, Asterisk AI, and Ollama can operate off of the same system.  If you choose to locally host your LLM or have a machine that handles Ollama (current support) you will have the option in the configuration on which IP to communicate with Ollama's API.

### Demonstration

In our demonstration we will be setting up Asterisk AI to host two contexts, with appropriate settings in FreePBX (Asterisk) and FS PBX respectively.

This assumes you'll be hosting Ollama on a dedicated system.

## FusionPBX Configuration Changes

## Overview

These changes allow FreePBX to route calls through FusionPBX to external trunks (VoIP.ms) without authentication issues.

---

## Set up FSPBX to FreePBX IP Auth Trunk

### Steps

1. Gateway - Any name can be chosen
2. Register - False
3. Context - public
4. Profile: external
5. Enabled = YES (toggled to the right)
6. Description: To-FreePBX

## SIP Profile - Internal

**Navigation:** Advanced → SIP Profiles → (Click) Internal to edit

### Steps

1. Go to **Advanced → SIP Profiles**
2. Click **Internal** profile
3. Make sure **auth-calls** is set to **true**
4. Make sure **apply-inbound-acl** is set to **providers**
5. Ensure it's **Enabled**
6. Click **SAVE**

### Reload Command

```bash
fs_cli -x "sofia profile internal restart"
```

---

## 2. Dialplan - Route 1xxx extension 

**Navigation:** Dialplan → Inbound Routes → Add

### Configuration

| Field | Value |
|-------|-------|
| **Name** | From-FreePBX-to-1000 |
| **Context** | public |
| **Order** | 100 |
| **Enabled** | True |
| **Description** | Transfer calls from FreePBX to default context for outbound routing |

### Conditions

| Condition | Type | Data |
|-----------|------|------|
| Condition 1 | destination_number | Choose one | 

### Actions

| Action | Type | Data |
|--------|------|------|
| Action 1 | Other | Choose one |

Hit `Save` and click what we just added again.

### Steps

After adding the above, we will edit, and make sure it has the following (our example uses 1000 as a direct to extension for a deskphone):

1. In Inbound routes
2. Click From-FreePBX-to-1000
3. Fill in the fields as shown in the table above
4. Add Condition 1: Type = `destination_number`, Data = `^(1000)$`
5. Add Action 1: Type = `bridge`, Data = `user/1000@pbx.blueintegrations.com`
6. Click **SAVE**

### Reload Command (if needed)

```bash
fs_cli -x "reloadxml"
```

---

## Quick Start to setting up Asterisk AI

Get the **Admin UI running in 2 minutes** while on the Debian instance, GIT should be installed, and in having so, the following is the howto for installation:

### 1. Run Pre-flight Check (Required)

```bash
# Clone repository
git clone https://github.com/hkjarral/Asterisk-AI-Voice-Agent.git
cd Asterisk-AI-Voice-Agent

# Run preflight with auto-fix (creates .env, generates JWT_SECRET)
sudo ./preflight.sh --apply-fixes
```

**Important:** Preflight creates your `.env` file and generates a secure `JWT_SECRET`. Always run this first!

### 2. Start the Admin UI

```bash
# Start the Admin UI container
docker compose -p asterisk-ai-voice-agent up -d --build --force-recreate admin_ui
```

### 3. Access the Dashboard

Open in your browser:
- **Local:** `http://localhost:3003`
- **Remote server:** `http://<server-ip>:3003`

**Default Login:** `admin` / `admin`

Follow the **Setup Wizard** to configure your providers and make a test call.

> ⚠️ **Security:** The Admin UI is accessible on the network. **Change the default password immediately** and restrict port 3003 via firewall, VPN, or reverse proxy for production use.

### 4. Verify Installation

```bash
# Start ai_engine (required for health checks)
docker compose -p asterisk-ai-voice-agent up -d --build ai_engine

# Check ai_engine health
curl http://localhost:15000/health
# Expected: {"status":"healthy"}

# View logs for any errors
docker compose -p asterisk-ai-voice-agent logs ai_engine | tail -20
```

### 5. Connect Asterisk

The wizard will generate the necessary dialplan configuration for your Asterisk server.

There's additional contexts setup, that you can repurpose as your own or add additional.

Once those are added you should be able to accept calls to the contexts in quesetion.

---

### FreePBX Configuration Settings

These changes allow FreePBX to route calls through to FusionPBX.

We are assuming that you have an Debian installation of FreePBX already setup, and functional.  Doing so means you have an extension setup, and can access simple services like Voicemail on FreePBX/Asterisk.

### Provision Trunk

Under **Connectivity** you will find **Trunks**, once clicked, hit **Add Trunk**

Give the trunk a name: FSPBX

Click **pjsip settings**

1. Make sure Authentication is set to None.
2. Make sure Registration is set to None.
3. Set **SIP Server** to **FSPBX**
4. Set **SIP Server Port** to **5060**
5. Set **Context** to **from-trunk**

Choose **Advanced**

1. Set **Match (Permit)** to IP of FSPBX
2. Set **From Domain** to hostname of "fspbx domain"
3. Set **Rewrite Contact** to **Yes**
4. Set **Force rport** to **Yes**

Choose **Submit** to save changes - you may apply settings

### Configuration Editor

Under **Admin** click **Config Edit**, a list of files will be visible, the file we want to edit is **extensions_custom.conf**.  We will want to place the following:

```
[from-ai-agent]
exten => s,1,NoOp(AI Agent - DID 5556016126)
 same => n,Set(AI_PROVIDER=local_hybrid)
 same => n,Set(AI_CONTEXT=CompanyA)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
 
[from-ai-agent-CompanyB]
exten => s,1,NoOp(AI Agent - DID 5556016043)
 same => n,Set(AI_PROVIDER=local_hybrid)
 same => n,Set(AI_CONTEXT=CompanyB)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
 
[custom-to-fusionpbx]
exten => s,1,NoOp(Forcing call out FusionPBX trunk)
exten => s,n,Dial(PJSIP/${EXTEN}@FSPBX)  ; or SIP/ depending on trunk type
exten => s,n,Hangup()
```

The two AI agent mentions are for two contexts, CompanyA and CompanyB are the custom extensions that will be visible under **Custom Destinations** when viewing the **Inbound Route**.

The third **custom-to-fusionpbx** is our method of handing the call off to the FSPBX trunk, much like we handled the call with Fusion to FreePBX, it is Asterisk method of handing off.

### Provision Custom Destinations

Under **Custom Destinations** in (under) **Admin** there will be an **Add Destination**, click.

1. Target: `from-ai-agent,s,1` 
2. Description: `Inbound CompanyA`

And click **Submit** 

The same would be replicated for additional, like CompanyB reference.

Hint/Warning: Ensure, when adding the second one that you label it as `from-ai-agent-CompanyB,s,1`

### Provision Inbound Route

Using 5556016043 and 5556016126 as our make believe numbers for setup, do the following.

Under **Connectivity** you will find **Inbound routes**, once clicked hit **Add Inbound Route**

Give the Inbound route a name: Any inbound to 5556016043

1. Make sure the DID number is 5556016043
2. Under **Set Destination** we want to set a **Custom Destination**, Inbound CompanyA, and Inbound CompanyB should be options, set these respectively.

And click **Submit** 

Hint/Warning: Ensure that when applying the destinations that you've keyed in the correct inbound number.

Disclaimer:  The reason for IP Auth is due to FreePBX/Asterisk handling of routing.  Routing is handled better with IP Auth style authentication.

### Provision Extension 1000 on FreePBX

Click Add Extension, set **User Extension** to the extension that is on FusionPBX, that you want to "mirror"

Display Name: Your name

Click **Advanced** 

Under **Dial** in the **Edit Extension** area, make sure the text field reads `PJSIP/1000@FSPBX`.

And click **Submit** 

**Apply config** is pressed to make all changes we've made

---

As far as configuration goes, for Asterisk AI, we strongly encourage you review the [documentation](https://github.com/hkjarral/Asterisk-AI-Voice-Agent?tab=readme-ov-file#-documentation), we also encourage checking out their [Discord](https://discord.gg/ysg8fphxUe).  The maintainer is quite active, and if you've caught him at a good time will offer assistance.  

Be sure to support the work of the maintainer by going [here](https://ko-fi.com/asteriskaivoiceagent).
