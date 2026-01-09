# Tool Calling Guide

**Version**: 4.1  
**Status**: Production Ready  
**Last Updated**: November 2025

Complete guide to AI tool calling in Asterisk AI Voice Agent—enabling AI agents to perform actions like call transfers and email management.

---

## Table of Contents

- [Overview](#overview)
- [Supported Providers](#supported-providers)
- [Available Tools](#available-tools)
- [Configuration](#configuration)
- [Dialplan Setup](#dialplan-setup)
- [Testing](#testing)
- [Production Examples](#production-examples)
- [Troubleshooting](#troubleshooting)
- [Architecture](#architecture)

---

## Overview

### What is Tool Calling?

Tool calling enables AI agents to perform real-world actions during conversations instead of just responding with text:

- **Call Transfers**: Transfer callers to human agents or departments
- **Email Management**: Send transcripts and call summaries via email
- **Graceful Hangups**: End calls with appropriate farewell messages

### Key Benefits

✅ **Provider-Agnostic**: Write tools once, use with any AI provider  
✅ **Production-Ready**: Validated with real-world call traffic  
✅ **Type-Safe**: Strong typing with comprehensive validation  
✅ **Unified Architecture**: Single codebase for all providers  
✅ **Easy to Extend**: Add new tools with minimal code

---

## Supported Providers

| Provider | Status | Notes |
|----------|--------|-------|
| **OpenAI Realtime** | ✅ Full Support | Production validated (Nov 9, 2025) |
| **Deepgram Voice Agent** | ✅ Full Support | Production validated (Nov 9, 2025) |
| **Google Gemini Live** | ✅ Full Support | Production validated (Nov 2025) |
| **Modular Pipelines (local_hybrid)** | ✅ Full Support | Production validated (Nov 19, 2025) - AAVA-85 |

All tools work identically across supported providers—no code changes needed when switching providers.

### MCP Tools (Experimental)

This repo is adding support for **MCP-backed tools** (Model Context Protocol) that can be called the same way as built-in tools, using the existing `ToolRegistry` + provider adapters.

- Design + branch guide: `docs/MCP_INTEGRATION.md`
- Key constraint: MCP tools must be exposed with **provider-safe names** (no `.` namespacing), and must respect `contexts.<name>.tools` allowlisting.

### Modular Pipeline Tool Execution

**Status**: ✅ Production validated (Nov 19, 2025)

Modular pipelines (e.g., `local_hybrid`) now support full tool execution through OpenAI Chat Completions API integration. This enables cost-effective tool calling with local STT/TTS and cloud LLM.

**How It Works**:
1. User speech detected via STT (Vosk, Google, etc.)
2. LLM (OpenAI Chat API) receives tool schemas and conversation context
3. LLM returns `tool_calls` in response if tool needed
4. Pipeline orchestrator executes tools via unified registry
5. Tool results incorporated into conversation

**Supported Tools**: All 6 tools validated in production
- ✅ `transfer` - Tested with call transfers to ring groups
- 🟡 `attended_transfer` - Deployed (warm transfer w/ announcement + DTMF acceptance; requires Local AI Server)
- ✅ `hangup_call` - Tested with farewell messages
- ✅ `send_email_summary` - Tested with auto-summaries
- ✅ `request_transcript` - Tested with email delivery
- 🟡 `cancel_transfer` - Deployed (requires active transfer to test)
- 🟡 `leave_voicemail` - Deployed (requires voicemail config)

**Configuration**:
```yaml
pipelines:
  local_hybrid:
    stt: vosk_local          # Local STT
    llm: openai              # Cloud LLM with function calling
    tts: piper_local         # Local TTS
    tools:
      - transfer
      - hangup_call
      - send_email_summary
      - request_transcript
```

**Production Evidence**:
- **Call 1763582071.6214**: Transfer to sales team ring group (✅ Success)
- **Call 1763582133.6224**: Hangup + transcript email (✅ Success)

**Key Benefits**:
- Cost-effective: Local STT/TTS, only pay for LLM tool detection
- Privacy-focused: Audio processed locally, only text to cloud LLM
- Feature parity: Same tools as monolithic providers
- Flexible: Mix and match STT/LLM/TTS components

**See Also**:
- Implementation details: `docs/contributing/milestones/milestone-18-hybrid-pipelines-tool-implementation.md`
- Common pitfalls: `docs/contributing/COMMON_PITFALLS.md#tool-execution-issues`

---

## Available Tools

### Telephony Tools

#### 1. Unified Transfer Tool

**Purpose**: Transfer caller to extensions, queues, or ring groups with intelligent routing

**Transfer Types**:

- **Extension**: Direct dial to specific agent (uses ARI `redirect`)
- **Queue**: Transfer to ACD queue for next available agent (uses ARI `continue` to `ext-queues`)
- **Ring Group**: Transfer to ring group that rings multiple agents (uses ARI `continue` to `ext-group`)

**Key Features**:
- Single unified interface for all transfer types
- Smart routing based on destination configuration
- Proper cleanup handling for each transfer type
- Caller remains connected after AI session ends

**Example Conversations**:
```
# Extension Transfer
Caller: "I need to speak with John in sales"
AI: "Transferring you to Sales agent now."
[Direct dial to extension 2765]

# Queue Transfer
Caller: "I need help from support"
AI: "Transferring you to Technical support queue now."
[Caller enters queue 300, hears MOH, next agent answers]

# Ring Group Transfer
Caller: "Can I talk to the sales team?"
AI: "Transferring you to Sales team ring group now."
[Ring group 600 rings all members simultaneously]
```

**Technical Implementation**:
- Extension transfers use `continue` to the configured dialplan context (e.g., `from-internal`)
- Queue/Ring Group transfers use `continue` (channel leaves Stasis, `transfer_active` flag prevents premature hangup)
- All transfer types verified in production

**Production Evidence**: 
- Extension: Call ID `1762734947.4251` (OpenAI) ✅
- Queue: Call ID `1763002719.4744` ✅
- Ring Group: Call ID `1763005247.4767` ✅

#### 2. Attended Transfer (Warm Transfer)

**Purpose**: Warm transfer with operator-style handoff (MOH + announcement + DTMF accept/decline)

**Behavior**:
- Caller is placed on **Music On Hold** while the destination is contacted.
- Destination hears a **one-way announcement** (TTS) summarizing caller + context.
- Destination must press **DTMF** to accept/decline:
  - Default: `1 = accept`, `2 = decline`, timeout = decline.
- On accept: AI audio is removed and the engine bridges **caller ↔ destination** directly.
- On decline/timeout: MOH stops and the AI resumes with the caller (optionally plays a short “unable to transfer” prompt).
- Engine remains alive as a passive bridge supervisor until hangup.

**Key constraints**:
- This tool is separate from `transfer`; it does **not** change existing transfer behavior.
- Only supported for `type: extension` destinations.
- Requires **Local AI Server** (announcement/prompt TTS is mandatory).
- Config compatibility: `agent_accept_prompt_template` is the canonical key; `agent_accept_prompt` is accepted as a legacy alias.

**How destination selection works**:
- The tool parameter is `destination` and it maps to a key under `tools.transfer.destinations`.
  - Example: `destination: "support_agent"` → dials `target: "6000"`.
- The engine supports fuzzy matching for common user terms (e.g., `"sales"`, `"support"`, `"6000"`), but for deterministic behavior configure prompts to use destination keys.

**Recommended context policy**:
- For predictable behavior, enable either `transfer` or `attended_transfer` per context/pipeline.
- If you enable both, add an explicit rule in the context prompt describing when to use each.

#### 3. Cancel Transfer

**Purpose**: Allow caller to cancel in-progress transfer

**Example**:
```
Caller: "Actually, never mind"
AI: "No problem, I've cancelled that transfer. How else can I help?"
```

#### 4. Hangup Call

**Purpose**: Gracefully end call with farewell message

**Example**:
```
AI: "Is there anything else I can help you with today?"
Caller: "No, that's all"
AI: "Thank you for calling. Goodbye!"
[Call ends]
```

### Business Tools

#### 5. Request Transcript (Caller-Initiated)

**Purpose**: Caller requests email transcript during call

**Features**:
- Email parsing from speech ("john dot smith at gmail dot com")
- Domain validation via DNS MX records
- Confirmation flow (AI reads back email)
- Deduplication (prevents duplicate sends)
- Admin receives BCC

**Example Conversation**:
```
Caller: "Can you email me a transcript of this call?"
AI: "I'd be happy to send you a transcript. What email address should I use?"
Caller: "john dot smith at gmail dot com"
AI: "That's john.smith@gmail.com - is that correct?"
Caller: "Yes"
AI: "Perfect! I'll send the transcript there shortly."
[Email sent after call ends]
```

**Production Evidence**: Call ID `1762745321.4286`
- Email validation: ✅ Working
- Confirmation flow: ✅ Implemented
- Deduplication: ✅ Prevents duplicates

#### 6. Send Email Summary (Auto-Triggered)

**Purpose**: Automatically send call summary to admin after every call

**Content**:
- Full conversation transcript
- Call duration and metadata
- Caller information
- Professional HTML formatting

**Example Email**:
```
Subject: Call Summary - (925) 736-6718 - 2025-11-10 16:43

Hello Admin,

Call Summary
Duration: 1m 24s
Caller: John Smith ((925) 736-6718)
Time: November 10, 2025 at 4:43 PM

Transcript:
AI: Hello! Thanks for calling. How can I help you today?
Caller: I need help with my account
AI: I'd be happy to help. Let me transfer you to support.
...
```

---

## Configuration

### Enable Tools in config/ai-agent.yaml

```yaml
# ============================================================================
# TOOL CALLING CONFIGURATION (v4.1+)
# ============================================================================

tools:
  # ----------------------------------------------------------------------------
  # UNIFIED TRANSFER - Transfer to extensions, queues, or ring groups
  # ----------------------------------------------------------------------------
  transfer:
    enabled: true
    destinations:
      # Direct extension transfers (using redirect - stays in Stasis)
      sales_agent:
        type: extension
        target: "2765"
        description: "Sales agent"
        attended_allowed: true         # Allows attended_transfer (warm transfer) to this destination
      
      support_agent:
        type: extension
        target: "6000"
        description: "Support agent"
        attended_allowed: true
      
      # Queue transfers (using continue to ext-queues)
      sales_queue:
        type: queue
        target: "300"
        description: "Sales team queue"
      
      support_queue:
        type: queue
        target: "301"
        description: "Technical support queue"
      
      billing_queue:
        type: queue
        target: "302"
        description: "Billing department queue"
      
      # Ring group transfers (using continue to ext-group)
      sales_team:
        type: ringgroup
        target: "600"
        description: "Sales team ring group"
      
      support_team:
        type: ringgroup
        target: "601"
        description: "Support team ring group"

  # ----------------------------------------------------------------------------
  # ATTENDED_TRANSFER - Warm transfer with announcement + DTMF acceptance
  # ----------------------------------------------------------------------------
  attended_transfer:
    enabled: true
    moh_class: "default"              # Asterisk MOH class for caller during dial/briefing
    dial_timeout_seconds: 30
    accept_timeout_seconds: 15
    tts_timeout_seconds: 8
    accept_digit: "1"
    decline_digit: "2"
    announcement_template: "Hi, this is Ava. I'm transferring {caller_display} regarding {context_name}."
    agent_accept_prompt_template: "Press 1 to accept this transfer, or 2 to decline."
    caller_connected_prompt: "Connecting you now."  # Optional
    caller_declined_prompt: "I’m not able to complete that transfer right now. Would you like me to take a message?"  # Optional

  # ----------------------------------------------------------------------------
  # CANCEL_TRANSFER - Cancel in-progress transfer
  # ----------------------------------------------------------------------------
  cancel_transfer:
    enabled: true
    allow_during_ring: true            # Cancel while ringing
    allow_after_answer: false          # Can't cancel after agent picks up
  
  # ----------------------------------------------------------------------------
  # HANGUP_CALL - Gracefully end call
  # ----------------------------------------------------------------------------
  hangup_call:
    enabled: true
    require_confirmation: false        # Don't ask "shall I hang up?"
    farewell_message: "Thank you for calling. Goodbye!"
  
  # ----------------------------------------------------------------------------
  # LEAVE_VOICEMAIL - Send caller to voicemail
  # ----------------------------------------------------------------------------
  leave_voicemail:
    enabled: true
    extension: "2765"                  # Voicemail box extension number
  
  # IMPORTANT: FreePBX VoiceMail app requires bidirectional RTP and voice activity
  # before playing greeting. Tool asks "Are you ready to leave a message now?" to
  # prompt caller response, which triggers voice activity and establishes RTP path.
  # Without this, there's a 5-8 second delay until caller speaks or timeout occurs.
  
  # ----------------------------------------------------------------------------
  # SEND_EMAIL_SUMMARY - Auto-send call summaries to admin
  # ----------------------------------------------------------------------------
  send_email_summary:
    enabled: true                      # Enable auto-send after calls
    provider: "resend"
    api_key: "${RESEND_API_KEY}"       # Set in .env file
    from_email: "agent@yourdomain.com"
    from_name: "AI Voice Agent"
    admin_email: "admin@yourdomain.com"
    include_transcript: true
    include_metadata: true
  
  # ----------------------------------------------------------------------------
  # REQUEST_TRANSCRIPT - Caller-initiated transcript requests
  # ----------------------------------------------------------------------------
  request_transcript:
    enabled: true                      # Allow caller transcript requests
    provider: "resend"
    api_key: "${RESEND_API_KEY}"
    from_email: "agent@yourdomain.com"
    from_name: "AI Voice Agent"
    admin_email: "admin@yourdomain.com"  # Admin receives BCC
    confirm_email: true                # AI reads back email
    validate_domain: true              # DNS MX lookup
    max_attempts: 2                    # Retry attempts for invalid email
    common_domains: ["gmail.com", "yahoo.com", "outlook.com"]
```

### Enable Tools per Context / Pipeline (Allowlisting)

Tools are allowlisted per **context** (and optionally per **pipeline**). If a tool is not allowlisted, the provider will not expose it to the model.

**Context example**:
```yaml
contexts:
  support:
    provider: google_live
    tools:
      - attended_transfer   # warm transfer
      - cancel_transfer
      - hangup_call
      - request_transcript
```

**Recommendation**: for deterministic transfer behavior, enable either `transfer` or `attended_transfer` in a given context/pipeline (not both), unless your prompt explicitly distinguishes when to use each.

### Environment Variables (.env)

```bash
# Resend API (for email tools)
RESEND_API_KEY=re_xxxxxxxxxxxx

# Get API key from: https://resend.com
```

**Best Practice**: Only `RESEND_API_KEY` goes in `.env` (secret). Email addresses go in `ai-agent.yaml` (configuration, not secret).

---

## Dialplan Setup

### Prerequisites

For tools to work, you need proper FreePBX/Asterisk configuration.

### 1. Create AI Agent Virtual Extension

**IMPORTANT**: The AI needs its own extension for CallerID when making transfers.

**In FreePBX**:
1. Navigate: **Applications → Extensions → Add Extension**
2. Extension Type: **Virtual Extension** (no physical device needed)
3. Configure:
   - Extension Number: **6789** (or customize in `ai-agent.yaml`)
   - Display Name: **AI Agent**
   - User Extension: **No**
   - Voicemail: **Disabled**

**Why is this needed?**
- When the AI transfers calls, it originates a new channel with this CallerID
- Without a valid CallerID, transfers may show as "Anonymous" and get rejected
- Agents see "AI Agent <6789>" on their phone display, identifying the transfer source

**Customize in `config/ai-agent.yaml`**:
```yaml
tools:
  ai_identity:
    name: "AI Agent"    # Change display name
    number: "6789"      # Change extension number (must match FreePBX)
```

**Verify in Asterisk**:
```bash
asterisk -rx "dialplan show 6789@from-internal"
```

### 2. Create Transfer Destination Extensions

Tools like `transfer` and `attended_transfer` need extensions (and/or queues/ring groups) to transfer **TO**:

**In FreePBX**:
1. Navigate: Applications → Extensions → Add Extension
2. Extension Type: Generic SIP Device or Virtual Extension
3. Configure:
   - Extension Number: **6000**
   - Display Name: "Support Team"
   - Destination: Ring Group or actual SIP device

**Repeat for departments**:
- 6001: Sales Team
- 6002: Billing Team
- 6003: Technical Support

**Verify in Asterisk**:
```bash
asterisk -rx "dialplan show 6000@from-internal"
```

### 3. Basic Dialplan (No Tools)

```asterisk
[from-ai-agent]
exten => s,1,NoOp(AI Agent - Basic)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

### 4. Dialplan with Context Selection

```asterisk
[from-ai-agent-support]
exten => s,1,NoOp(AI Agent - Support Line)
 same => n,Set(AI_CONTEXT=support)           ; Support persona
 same => n,Set(AI_PROVIDER=openai_realtime)  ; Fast provider
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

[from-ai-agent-sales]
exten => s,1,NoOp(AI Agent - Sales Line)
 same => n,Set(AI_CONTEXT=sales)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

### 5. Channel Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `AI_CONTEXT` | Select custom greeting/persona | `support`, `sales`, `billing` |
| `AI_PROVIDER` | Override provider for this call | `openai_realtime`, `deepgram` |
| `CALLERID(name)` | Caller's name (auto-available to AI) | Any string |
| `CALLERID(num)` | Caller's number (auto-available to AI) | Phone number |

See [FreePBX Integration Guide](FreePBX-Integration-Guide.md) for complete dialplan documentation.

### 6. Template Variables in Prompts

Context prompts support template variables for call-specific data. This is especially useful for MCP tools that need caller information.

| Variable | Description | Default |
|----------|-------------|---------|
| `{caller_name}` | Caller ID name | `"there"` |
| `{caller_number}` | Caller phone number (ANI) | `"unknown"` |
| `{call_id}` | Unique call identifier | (always set) |
| `{context_name}` | AI_CONTEXT from dialplan | `""` |
| `{call_direction}` | `"inbound"` or `"outbound"` | `"inbound"` |
| `{campaign_id}` | Outbound campaign ID | `""` |
| `{lead_id}` | Outbound lead/contact ID | `""` |

**Example**:
```yaml
contexts:
  customer_support:
    prompt: |
      The caller's phone number is {caller_number}.
      Use the lookup_customer tool with this number to find their account.
    tools:
      - mcp_crm_lookup_customer
      - hangup_call
```

See [MCP Integration Guide](MCP_INTEGRATION.md#template-variables-for-prompts) for detailed documentation.

---

## Testing

### Test Transfer Tool

**1. Prerequisites**:
- Extension 6000 configured in FreePBX
- `tools.transfer.enabled: true` in config
- `tools.transfer.destinations` contains a destination (example: `support_agent`)

**2. Make Test Call**:
```
You: "I need to speak with support"
Expected: AI says "I'll transfer you to support"
Expected: Call transfers to extension 6000
Expected: Bidirectional audio after agent answers
```

**3. Verify in Logs**:
```bash
docker logs ai_engine | egrep "Transfer requested|Unified transfer tool"

# Expected output:
# [INFO] Transfer requested ... destination=support_agent
# [INFO] ✅ Extension transfer initiated ...
```

### Test Attended Transfer (Warm Transfer)

**1. Prerequisites**:
- Local AI Server running (required for announcement/prompt TTS)
- `tools.attended_transfer.enabled: true`
- Destination configured with `attended_allowed: true`:
  - Example: `tools.transfer.destinations.support_agent.attended_allowed: true`
- Context/pipeline enables `attended_transfer` tool (recommended to disable `transfer` for deterministic behavior)

**2. Make Test Call**:
```
You: "Please transfer me to support"
Expected: Caller hears MOH while agent is contacted
Expected: Destination hears announcement + DTMF prompt
Expected: Agent presses 1 → caller bridged to destination; AI audio removed
```

**3. Verify in Logs**:
```bash
docker logs ai_engine | egrep "Attended transfer requested|ATTENDED TRANSFER COMPLETE|Channel DTMF received"
```

### Test Email Tool

**1. Prerequisites**:
- `RESEND_API_KEY` set in `.env`
- `request_transcript.enabled: true` in config
- Valid `from_email` configured in Resend dashboard

**2. Make Test Call**:
```
You: "Can you email me a transcript?"
AI: "What email address should I use?"
You: "john at gmail dot com"
AI: "That's john@gmail.com - is that correct?"
You: "Yes"
AI: "Perfect! I'll send the transcript there."
```

**3. Check Email**:
- Check inbox for transcript email
- Verify admin received BCC
- Check Resend dashboard for delivery status: https://resend.com/logs

**4. Verify in Logs**:
```bash
docker logs ai_engine | grep "request_transcript"

# Expected output:
# [INFO] 🔧 Tool call: request_transcript({'email': 'john@gmail.com'})
# [INFO] ✅ Email validation passed: john@gmail.com
# [INFO] ✅ Email sent successfully to john@gmail.com
```

---

## Production Examples

### Warm Transfer Flow (Deepgram)

**Call ID**: `1762731796.4233` (Nov 9, 2025)

**Timeline**:
```
00:43:12  Caller enters AI conversation
00:43:45  Caller: "I need help from support"
00:43:46  AI detects intent → Deepgram sends FunctionCallRequest
00:43:46  Tool executes: transfer(destination="support_agent")
00:43:46  Resolved: support_agent → 6000
00:43:49  Agent answers (extension 6000)
00:43:49  AI cleanup sequence:
          1. Remove UnicastRTP from bridge (<50ms)
          2. Stop Deepgram session (<30ms)
          3. Add SIP/6000 to bridge (<20ms)
          4. Update session metadata (<10ms)
00:43:49  Result: [Caller ↔ SIP/6000] direct audio
00:44:27  Call continues 38+ seconds (stable)
```

**Technical Achievement**: No Local channels = perfect bidirectional audio

### OpenAI Realtime Transfer

**Call ID**: `1762734947.4251` (Nov 9, 2025)

**Key Difference**: Same tool code, different provider adapter

**Event Sequence**:
1. OpenAI: `response.output_item.done` (function_call detected)
2. Adapter: Parses `item.name="transfer_call"` (legacy alias) and maps it to `transfer`
3. Registry: Routes to unified tool
4. Tool: **Exact same execution** as Deepgram (504 lines of shared code)
5. OpenAI: Receives function output, speaks confirmation

**Validation**: Provider-agnostic architecture confirmed ✅

### Email Transcript Request

**Call ID**: `1762745321.4286` (Nov 10, 2025)

**Conversation Flow**:
```
03:28:45  Caller: "Can you email me the transcript?"
03:28:46  AI: "What email address should I use?"
03:28:50  Caller: "test at gmail dot com"
03:28:51  Email parser: "test at gmail dot com" → "test@gmail.com"
03:28:51  DNS validation: MX records found for gmail.com ✅
03:28:52  AI: "That's test@gmail.com - is that correct?"
03:28:54  Caller: "Yes"
03:28:55  AI: "Perfect! I'll send the transcript there."
03:29:20  Call ends
03:29:20  Tool executes: request_transcript({'email': 'test@gmail.com'})
03:29:21  Email sent via Resend API ✅
03:29:21  Admin BCC sent ✅
```

**Features Validated**:
- ✅ Speech-to-email parsing
- ✅ DNS MX validation
- ✅ Confirmation flow
- ✅ Deduplication
- ✅ Admin BCC

---

## Troubleshooting

### Transfer Not Working

**Symptom**: AI says "I'll transfer you" but nothing happens

**Checks**:
```bash
# 1. Verify extension exists
asterisk -rx "dialplan show 6000@from-internal"

# 2. Check tool enabled in config
grep -A 20 "transfer:" config/ai-agent.yaml

# 3. Check logs for errors
docker logs ai_engine | grep -i "transfer"

# 4. Verify SIP endpoint reachable
asterisk -rx "pjsip show endpoint 6000"
```

**Common Issues**:
| Issue | Solution |
|-------|----------|
| Extension doesn't exist | Create virtual extension in FreePBX |
| Wrong SIP format | Use `SIP/6000` not `6000` or `SIP:6000` |
| `tool.enabled: false` | Set to `true` in config |
| Destination not mapped | Add to `tools.transfer.destinations` in config |

### Email Not Sending

**Symptom**: AI confirms but email never arrives

**Checks**:
```bash
# 1. Verify API key set
grep RESEND_API_KEY .env

# 2. Check Resend dashboard
# https://resend.com/logs

# 3. Check logs
docker logs ai_engine | grep -i "email"

# 4. Verify from_email in Resend
# Must be verified domain
```

**Common Issues**:
| Issue | Solution |
|-------|----------|
| API key missing | Add `RESEND_API_KEY` to `.env` |
| `from_email` not verified | Verify domain in Resend dashboard |
| Invalid recipient | Check DNS MX records for domain |
| Tool disabled | Set `enabled: true` in config |

### Audio Lost After Transfer

**Symptom**: Transfer succeeds but no audio between caller and agent

**This should NOT happen** with v4.1's direct SIP origination. If it does:

```bash
# 1. Check bridge type (should be simple_bridge)
asterisk -rx "bridge show <bridge_id>"

# 2. Verify no Local channels involved
asterisk -rx "core show channels" | grep Local

# 3. Check logs for cleanup sequence
docker logs ai_engine | grep "cleanup"
```

**Diagnostic**:
- ✅ **Correct**: Bridge contains [Caller, SIP/6000] only
- ❌ **Wrong**: Bridge contains Local channels or 3+ channels

### AI Can't Parse Email

**Symptom**: AI can't understand email address from speech

**Solutions**:
1. Add common domains to config:
```yaml
request_transcript:
  common_domains: ["gmail.com", "company.com", "outlook.com"]
```

2. Train callers: "Please say your email slowly, for example: john dot smith at gmail dot com"

3. Implement retry logic (already in v4.1):
```yaml
request_transcript:
  max_attempts: 2  # Retry if first attempt invalid
```

---

## Architecture

### Unified Tool System

```
┌──────────────────────────────────────────────┐
│      Tool Registry (Write Once)              │
│  • transfer       • attended_transfer        │
│  • request_transcript                        │
│  • hangup_call    • send_email_summary       │
└──────────────┬───────────────────────────────┘
               │
    ┌──────────┴──────────┬
    ▼                     ▼
┌───────────┐      ┌─────────────┐
│  OpenAI   │      │  Deepgram   │
│  Adapter  │      │   Adapter   │
│ (215 lines)│     │  (202 lines) │
└───────────┘      └─────────────┘
     │                    │
     └────────────────────┴──────────────┐
                         │                │
                         ▼                ▼
              ┌──────────────┐  ┌──────────────┐
              │  ARI Client  │  │ Email Service│
              │  (Telephony) │  │  (Business)  │
              └──────────────┘  └──────────────┘
```

### Key Files

**Core Framework**:
- `src/tools/base.py` (231 lines) - Base classes and abstractions
- `src/tools/context.py` (108 lines) - Execution context
- `src/tools/registry.py` (198 lines) - Singleton registry

**Provider Adapters**:
- `src/tools/adapters/deepgram.py` (202 lines) - Deepgram integration
- `src/tools/adapters/openai.py` (215 lines) - OpenAI Realtime integration

**Tools**:
- `src/tools/telephony/unified_transfer.py` - Unified transfer tool (`transfer`)
- `src/tools/telephony/attended_transfer.py` - Warm transfer (`attended_transfer`)
- `src/tools/telephony/cancel_transfer.py` - Cancel transfer tool
- `src/tools/telephony/hangup.py` - Hangup call tool
- `src/tools/business/request_transcript.py` (475 lines) - Transcript request tool
- `src/tools/business/email_summary.py` (347 lines) - Email summary tool

**Integration**:
- `src/engine.py` (lines 433-440) - Tool registry initialization
- `src/providers/deepgram.py` (lines 807-857, 1137-1151) - Deepgram tool integration
- `src/providers/openai_realtime.py` (lines 1107-1120) - OpenAI tool integration

### Tool Execution Flow

1. **AI Detection**: Provider detects intent and generates function call
2. **Adapter Translation**: Provider-specific adapter converts to unified format
3. **Registry Lookup**: Tool retrieved from registry by name
4. **Validation**: Parameters validated against tool definition
5. **Execution**: Tool logic executes with context (ARI, session, etc.)
6. **Result**: Success/failure returned to provider
7. **AI Response**: Provider speaks result to caller

**Total Code Duplication**: 0 lines ✅  
Tools written once, work with any provider.

### Design Principles

1. **Write Once, Use Anywhere**: Same tool code for all providers
2. **Type Safety**: Strong typing with dataclasses and validation
3. **Provider Agnostic**: Adapters handle format translation
4. **Extensible**: New tools require minimal code (~100-500 lines)
5. **Production Ready**: Validated with real call traffic

---

## Related Documentation

- **[FreePBX Integration Guide](FreePBX-Integration-Guide.md)** - Dialplan setup and channel variables
- **[Configuration Reference](Configuration-Reference.md)** - All YAML settings
- **[Architecture Deep Dive](contributing/architecture-deep-dive.md)** - System design and components
- **[Tool Architecture Case Study](contributing/milestones/milestone-16-tool-calling-system.md)** - Design decisions and implementation details

---

## Support

**Found a bug?** [Open an issue](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues)  
**Have questions?** [Start a discussion](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/discussions)

---

**Last Updated**: November 10, 2025  
**Version**: 4.1.0  
**Status**: ✅ Production Ready
