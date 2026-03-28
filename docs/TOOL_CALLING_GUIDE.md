# Tool Calling Guide

**Version**: 4.2  
**Status**: Production Ready  
**Last Updated**: January 2026

Complete guide to AI tool calling in Asterisk AI Voice Agent—enabling AI agents to perform actions like call transfers and email management.

---

## Table of Contents

- [Overview](#overview)
- [Supported Providers](#supported-providers)
- [Available Tools](#available-tools)
- [Pre-Call Tools (HTTP Lookups)](#pre-call-tools-http-lookups)
- [In-Call HTTP Tools](#in-call-http-tools)
- [Post-Call Tools (Webhooks)](#post-call-tools-webhooks)
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

#### 5. Check Extension Status (Availability)

**Purpose**: Check whether an internal extension is available (e.g., `NOT_INUSE`) during the call so the AI can decide whether to transfer or continue the conversation.

**How it works**:
- Queries ARI device states (`GET /ari/deviceStates/{deviceStateName}`), typically using `<TECH>/<EXT>`:
  - `PJSIP/2765`
  - `SIP/6000`

**Configuration (optional but recommended)**:
```yaml
tools:
  extensions:
    internal:
      "2765":
        dial_string: "PJSIP/2765"
        device_state_tech: "PJSIP"  # auto | PJSIP | SIP | IAX2 | DAHDI
```

**Tool output**:
- Returns `device_state` and `available` (boolean).

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
Subject: Call Summary - +1 (555) 010-1234 - 2025-11-10 16:43

Hello Admin,

Call Summary
Duration: 1m 24s
Caller: John Smith (+1 (555) 010-1234)
Time: November 10, 2025 at 4:43 PM

Transcript:
AI: Hello! Thanks for calling. How can I help you today?
Caller: I need help with my account
AI: I'd be happy to help. Let me transfer you to support.
...
```

---

## Pre-Call Tools (HTTP Lookups)

**New in v4.2** — Pre-call tools run after call answer but before AI speaks, enabling CRM enrichment and caller data lookup.

### Pre-Call Overview

Pre-call tools fetch external data (e.g., CRM contact info, account status) and inject it into AI prompts via output variables. This allows the AI to greet callers by name, reference their account, or provide personalized service.

### Generic HTTP Lookup Tool

The `generic_http_lookup` tool makes HTTP requests to external APIs and maps response fields to prompt variables.

**Configuration Example (GoHighLevel)**:

```yaml
tools:
  ghl_contact_lookup:
    kind: generic_http_lookup
    phase: pre_call
    enabled: true
    timeout_ms: 2000
    hold_audio_file: "custom/please-wait"  # Optional MOH during lookup
    hold_audio_threshold_ms: 500           # Play if lookup takes >500ms
    url: "https://rest.gohighlevel.com/v1/contacts/lookup"
    method: GET
    headers:
      Authorization: "Bearer ${GHL_API_KEY}"
    query_params:
      phone: "{caller_number}"
    output_variables:
      customer_name: "contacts[0].firstName"
      customer_email: "contacts[0].email"
      account_type: "contacts[0].customFields.account_type"
```

**Variable Substitution**:

| Variable | Description |
|----------|-------------|
| `{caller_number}` | Caller's phone number (ANI) |
| `{called_number}` | DID that was called |
| `{caller_name}` | Caller ID name |
| `{context_name}` | AI_CONTEXT from dialplan |
| `{call_id}` | Unique call identifier |
| `{campaign_id}` | Outbound campaign ID |
| `{lead_id}` | Outbound lead ID |
| `${ENV_VAR}` | Environment variable |

**Response Path Extraction**:

- Simple fields: `"firstName"` → `response["firstName"]`
- Nested fields: `"contact.email"` → `response["contact"]["email"]`
- Array access: `"contacts[0].name"` → `response["contacts"][0]["name"]`

**Using Output Variables in Prompts**:

```yaml
contexts:
  support:
    prompt: |
      You are a helpful support agent.
      The caller's name is {customer_name}.
      Their account type is {account_type}.
      Greet them by name and offer personalized assistance.
    tools:
      - hangup_call
      - transfer
```

### Pre-Call Example Configurations

**HubSpot Contact Lookup**:

```yaml
tools:
  hubspot_lookup:
    kind: generic_http_lookup
    phase: pre_call
    enabled: true
    timeout_ms: 3000
    url: "https://api.hubapi.com/crm/v3/objects/contacts/search"
    method: POST
    headers:
      Authorization: "Bearer ${HUBSPOT_API_KEY}"
      Content-Type: "application/json"
    body_template: |
      {
        "filterGroups": [{
          "filters": [{
            "propertyName": "phone",
            "operator": "EQ",
            "value": "{caller_number}"
          }]
        }]
      }
    output_variables:
      customer_name: "results[0].properties.firstname"
      customer_company: "results[0].properties.company"
```

**Custom CRM API**:

```yaml
tools:
  custom_crm:
    kind: generic_http_lookup
    phase: pre_call
    enabled: true
    url: "https://api.yourcrm.com/v1/lookup"
    method: GET
    headers:
      X-API-Key: "${CRM_API_KEY}"
    query_params:
      phone: "{caller_number}"
      context: "{context_name}"
    output_variables:
      customer_name: "data.full_name"
      customer_status: "data.status"
      last_interaction: "data.last_call_date"
```

---

## In-Call HTTP Tools

**New in v4.2** — In-call HTTP tools are AI-invoked during a live conversation to fetch real-time data from external APIs.

### In-Call Overview

Unlike pre-call tools (automatic, before AI speaks) and post-call webhooks (after hangup), in-call HTTP tools are invoked by the AI during the conversation when it needs fresh data. The AI decides when to call them based on conversation context.

**Use Cases**:
- Check appointment availability
- Look up order status  
- Query real-time inventory
- Fetch account balance
- Any API call where the AI needs data mid-conversation

### In-Call HTTP Tool Configuration

```yaml
in_call_tools:
  check_availability:
    kind: in_call_http_lookup
    enabled: true
    description: "Check appointment availability for a given date and time"
    timeout_ms: 5000
    url: "https://api.example.com/availability"
    method: POST
    headers:
      Authorization: "Bearer ${API_KEY}"
      Content-Type: "application/json"
    parameters:
      - name: date
        type: string
        description: "Date in YYYY-MM-DD format"
        required: true
      - name: time
        type: string
        description: "Time in HH:MM format"
        required: true
    body_template: |
      {
        "customer_id": "{customer_id}",
        "date": "{date}",
        "time": "{time}"
      }
    return_raw_json: false
    output_variables:
      available: "data.available"
      next_slot: "data.next_available_slot"
    error_message: "I'm sorry, I couldn't check availability right now."
```

### Enable In-Call HTTP Tools per Context

In-call HTTP tools are allowlisted per context (same as other in-call tools). In the Admin UI, you enable these under **Contexts → In-Call Tools**.

**Example**:
```yaml
contexts:
  support:
    tools:
      - hangup_call
      - attended_transfer
    in_call_http_tools:
      - check_availability
      - order_status
```

### Variable Substitution (Precedence)

In-call HTTP tools have access to three types of variables:

1. **Context variables** (auto-injected): `{caller_number}`, `{called_number}`, `{call_id}`, etc.
2. **Pre-call variables** (from pre-call HTTP lookups): `{customer_id}`, `{customer_name}`, etc.
3. **AI parameters** (provided at runtime): Whatever the AI passes when invoking the tool

This means you can use data fetched by pre-call tools in your in-call tool requests. For example, if a pre-call lookup fetches `customer_id`, you can use `{customer_id}` in the in-call tool's body template.

### Key Differences from Other Tool Types

| Aspect | Pre-Call Tools | In-Call HTTP Tools | Post-Call Webhooks |
|--------|---------------|--------------------|--------------------|
| Trigger | Automatic (after answer) | AI-invoked | Automatic (after hangup) |
| Parameters | Context variables only | AI params + context + pre-call vars | Call data + context |
| Timing | Before AI speaks | During conversation | After call ends |
| Results | Injected into prompt | Returned to AI | Fire-and-forget |

### In-Call Example Configurations

**Order Status Lookup**:

```yaml
in_call_tools:
  order_status:
    kind: in_call_http_lookup
    enabled: true
    description: "Look up the status of an order by order number"
    timeout_ms: 5000
    url: "https://api.example.com/orders/{order_number}"
    method: GET
    headers:
      Authorization: "Bearer ${API_KEY}"
    parameters:
      - name: order_number
        type: string
        description: "The order number to look up"
        required: true
    output_variables:
      status: "data.status"
      estimated_delivery: "data.estimated_delivery"
      tracking_number: "data.tracking_number"
    error_message: "I couldn't find that order. Please verify the order number."
```

**Appointment Booking**:

```yaml
in_call_tools:
  book_appointment:
    kind: in_call_http_lookup
    enabled: true
    description: "Book an appointment for a customer"
    timeout_ms: 8000
    url: "https://api.example.com/appointments"
    method: POST
    headers:
      Authorization: "Bearer ${API_KEY}"
      Content-Type: "application/json"
    parameters:
      - name: date
        type: string
        description: "Appointment date (YYYY-MM-DD)"
        required: true
      - name: time
        type: string
        description: "Appointment time (HH:MM)"
        required: true
      - name: service_type
        type: string
        description: "Type of service requested"
        required: true
    body_template: |
      {
        "customer_phone": "{caller_number}",
        "customer_id": "{customer_id}",
        "date": "{date}",
        "time": "{time}",
        "service": "{service_type}"
      }
    return_raw_json: false
    output_variables:
      confirmation_number: "data.confirmation_id"
      appointment_time: "data.scheduled_time"
    error_message: "I wasn't able to book that appointment. Would you like to try a different time?"
```

---

## Post-Call Tools (Webhooks)

**New in v4.2** — Post-call tools run after call ends, enabling webhook notifications to external systems.

### Post-Call Overview

Post-call tools are fire-and-forget—they execute after cleanup and don't block the call flow. Use them to:

- Send call data to CRMs (GoHighLevel, HubSpot)
- Trigger automation workflows (n8n, Make, Zapier)
- Update external databases
- Generate AI-powered call summaries

### Generic Webhook Tool

The `generic_webhook` tool sends HTTP requests with call data to external endpoints.

**Configuration Example (n8n Webhook)**:

```yaml
tools:
  n8n_call_completed:
    kind: generic_webhook
    phase: post_call
    enabled: true
    is_global: true              # Run for ALL calls
    timeout_ms: 10000
    url: "https://n8n.yourserver.com/webhook/call-completed"
    method: POST
    headers:
      Authorization: "Bearer ${N8N_WEBHOOK_TOKEN}"
      Content-Type: "application/json"
    payload_template: |
      {
        "call_id": "{call_id}",
        "caller_number": "{caller_number}",
        "called_number": "{called_number}",
        "caller_name": "{caller_name}",
        "context": "{context_name}",
        "provider": "{provider}",
        "duration_seconds": {call_duration},
        "outcome": "{call_outcome}",
        "start_time": "{call_start_time}",
        "end_time": "{call_end_time}",
        "transcript": {transcript_json},
        "summary": "{summary}",
        "summary_json": {summary_json}
      }
```

**AI-Powered Summary Generation**:

```yaml
tools:
  crm_update:
    kind: generic_webhook
    enabled: true
    is_global: true
    url: "https://api.crm.com/calls"
    generate_summary: true        # Generate AI summary using OpenAI
    summary_max_words: 100        # Limit summary length
    payload_template: |
      {
        "phone": "{caller_number}",
        "summary": "{summary}",
        "summary_json": {summary_json},
        "transcript": {transcript_json}
      }
```

When `generate_summary: true`, the tool uses OpenAI to create a concise summary of the conversation before sending the webhook.

**Payload Variables**:

| Variable | Type | Description |
|----------|------|-------------|
| `{call_id}` | string | Unique call identifier |
| `{caller_number}` | string | Caller's phone number |
| `{called_number}` | string | DID that was called |
| `{caller_name}` | string | Caller ID name |
| `{context_name}` | string | AI context used |
| `{provider}` | string | AI provider (deepgram, openai_realtime, etc.) |
| `{call_direction}` | string | "inbound" or "outbound" |
| `{call_duration}` | number | Duration in seconds |
| `{call_outcome}` | string | Outcome (completed, transferred, etc.) |
| `{call_start_time}` | string | ISO timestamp |
| `{call_end_time}` | string | ISO timestamp |
| `{transcript_json}` | JSON | Full conversation as JSON array |
| `{summary}` | string | AI-generated summary (if enabled) |
| `{summary_json}` | JSON | AI-generated summary as a JSON string (safe for unquoted insertion) |
| `{campaign_id}` | string | Outbound campaign ID |
| `{lead_id}` | string | Outbound lead ID |

**Note**: `{transcript_json}` is inserted as raw JSON (not quoted), so place it directly in the template without quotes.

### Post-Call Example Configurations

**GoHighLevel Contact Update**:

```yaml
tools:
  ghl_update:
    kind: generic_webhook
    enabled: true
    is_global: true
    url: "https://rest.gohighlevel.com/v1/contacts/{lead_id}/notes"
    method: POST
    headers:
      Authorization: "Bearer ${GHL_API_KEY}"
      Content-Type: "application/json"
    generate_summary: true
    payload_template: |
      {
        "body": "AI Call Summary:\n{summary}\n\nDuration: {call_duration}s"
      }
```

**Make (Integromat) Webhook**:

```yaml
tools:
  make_webhook:
    kind: generic_webhook
    enabled: true
    is_global: true
    url: "https://hook.us1.make.com/xxxxxxxxxxxxx"
    method: POST
    payload_template: |
      {
        "event": "call_completed",
        "data": {
          "call_id": "{call_id}",
          "phone": "{caller_number}",
          "duration": {call_duration},
          "transcript": {transcript_json}
        }
      }
```

**Zapier Webhook**:

```yaml
tools:
  zapier_trigger:
    kind: generic_webhook
    enabled: true
    is_global: true
    url: "https://hooks.zapier.com/hooks/catch/xxxxx/yyyyy/"
    method: POST
    generate_summary: true
    payload_template: |
      {
        "caller_phone": "{caller_number}",
        "call_summary": "{summary}",
        "call_summary_json": {summary_json},
        "call_duration": {call_duration},
        "timestamp": "{call_end_time}"
      }
```

### Context-Specific Webhooks

Run webhooks only for specific contexts:

```yaml
tools:
  sales_webhook:
    kind: generic_webhook
    enabled: true
    is_global: false              # Not global
    url: "https://sales.example.com/webhook"
    payload_template: |
      {"call_id": "{call_id}", "outcome": "{call_outcome}", "summary_json": {summary_json}}

contexts:
  sales:
    tools:
      - transfer
      - hangup_call
      - sales_webhook              # Only runs for sales context
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
  # LIVE AGENTS - Human extensions used by live_agent_transfer()
  # ----------------------------------------------------------------------------
  #
  # These are NOT normal transfer destinations. They represent real human endpoints
  # (extensions) that the AI can hand off to when the caller explicitly asks for a
  # "live agent" / "human" / "representative".
  #
  # Admin UI: Tools -> Live Agents
  #
  extensions:
    internal:
      "6000":
        name: "Live Support Agent"
        dial_string: "PJSIP/6000"
        device_state_tech: auto
        description: "Live customer service representative"
        transfer: true

  # ----------------------------------------------------------------------------
  # LIVE AGENT OVERRIDE (Advanced/Legacy)
  # ----------------------------------------------------------------------------
  #
  # Default behavior: live_agent_transfer() routes to tools.extensions.internal.
  #
  # Advanced/legacy override: route live-agent requests via a normal transfer
  # destination key (queue/ringgroup/extension). Enable this only if you
  # intentionally want "live agent" requests to behave like a normal transfer
  # destination.
  #
  # transfer:
  #   live_agent_destination_key: "support_queue"
  #   destinations:
  #     support_queue:
  #       type: queue
  #       target: "301"
  #       description: "Technical support queue"
  #       live_agent: true

  # ----------------------------------------------------------------------------
  # ATTENDED_TRANSFER - Warm transfer with announcement + DTMF acceptance
  # ----------------------------------------------------------------------------
  attended_transfer:
    enabled: true
    moh_class: "default"              # Asterisk MOH class for caller during dial/briefing
    dial_timeout_seconds: 30
    accept_timeout_seconds: 15
    tts_timeout_seconds: 8
    screening_mode: "basic_tts"       # basic_tts | ai_briefing (experimental) | caller_recording
    ai_briefing_timeout_seconds: 2.0  # Experimental Local AI Server LLM summary timeout; falls back to basic_tts on failure
    ai_briefing_intro_template: "Hi, this is Ava. Here is a short summary of the caller."
    caller_screening_prompt: "Before I connect you, please say your name and the reason for your call."
    caller_screening_max_seconds: 6
    caller_screening_silence_ms: 1200
    accept_digit: "1"
    decline_digit: "2"
    announcement_template: "Hi, this is Ava. I'm transferring {caller_display} regarding {context_name}."
    agent_accept_prompt_template: "Press 1 to accept this transfer, or 2 to decline."
    caller_connected_prompt: "Connecting you now."  # Optional
    caller_declined_prompt: "I’m not able to complete that transfer right now. Would you like me to take a message?"  # Optional
    # Local AI Server dependency notes:
    # - basic_tts: requires Local AI Server TTS
    # - caller_recording: requires Local AI Server TTS for intro/prompt
    #                     operators are responsible for meeting local caller notice/consent requirements
    #                     screening audio is used transiently for the transfer workflow, not as a retained recording feature
    # - ai_briefing (experimental): requires Local AI Server TTS and Local AI Server LLM capability
    #                               falls back to basic_tts if summary generation is unavailable

  # ----------------------------------------------------------------------------
  # CHECK_EXTENSION_STATUS - Availability checks for configured targets
  # ----------------------------------------------------------------------------
  check_extension_status:
    restrict_to_configured_extensions: true  # Recommended safety guardrail

  # ----------------------------------------------------------------------------
  # CANCEL_TRANSFER - Cancel in-progress transfer
  # ----------------------------------------------------------------------------
  cancel_transfer:
    enabled: true
    allow_during_ring: true            # Cancel while ringing
  
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
    provider: "auto"                   # auto | smtp | resend
    from_email: "agent@yourdomain.com"
    from_name: "AI Voice Agent"
    admin_email: "admin@yourdomain.com"
    # Optional: route different contexts to different inboxes
    # admin_email_by_context:
    #   support: "support@yourdomain.com"
    #   sales: "sales@yourdomain.com"
    # Optional: route sender address per context
    # from_email_by_context:
    #   support: "support-bot@yourdomain.com"
    #   sales: "sales-bot@yourdomain.com"
    include_transcript: true
    include_metadata: true
    # Optional: subject prefix and per-context overrides
    # subject_prefix: "[AAVA]"
    # subject_prefix_by_context:
    #   support: "[Support]"
    #   sales: "[Sales]"
    # Optional: include context tag like [support] in the subject
    # include_context_in_subject: true
    # Optional: override full HTML template (Jinja2). You can edit/preview in Admin UI → Tools.
    # html_template: |
    #   <html>...</html>
  
  # ----------------------------------------------------------------------------
  # REQUEST_TRANSCRIPT - Caller-initiated transcript requests
  # ----------------------------------------------------------------------------
  request_transcript:
    enabled: true                      # Allow caller transcript requests
    provider: "auto"                   # auto | smtp | resend
    from_email: "agent@yourdomain.com"
    from_name: "AI Voice Agent"
    admin_email: "admin@yourdomain.com"  # Admin receives BCC
    # Optional: route BCC by context
    # admin_email_by_context:
    #   support: "support@yourdomain.com"
    #   sales: "sales@yourdomain.com"
    # Optional: route sender address per context
    # from_email_by_context:
    #   support: "support-bot@yourdomain.com"
    #   sales: "sales-bot@yourdomain.com"
    confirm_email: true                # AI reads back email
    validate_domain: true              # DNS MX lookup
    # Optional: include context tag like [support] in the subject
    # include_context_in_subject: true
    # Note: by default, only the most recent confirmed email is used per call.
    # Set to true to allow multiple recipients (not recommended for most deployments).
    # allow_multiple_recipients: false
    max_attempts: 2                    # Retry attempts for invalid email
    common_domains: ["gmail.com", "yahoo.com", "outlook.com"]
    # Optional: override full HTML template (Jinja2). You can edit/preview in Admin UI → Tools.
    # html_template: |
    #   <html>...</html>
```

### Enable Tools per Context / Pipeline (Allowlisting)

Tools are allowlisted per **context** (and optionally per **pipeline**).

Additionally, some tools can be marked **global** (enabled by default) and then selectively disabled per context using `disable_global_in_call_tools`.

**Context example**:
```yaml
contexts:
  support:
    provider: google_live
    tools:
      - attended_transfer   # warm transfer
      - live_agent_transfer # explicit: caller asked for a live human agent
      - cancel_transfer
      - hangup_call
      - request_transcript
    disable_global_in_call_tools:
      - check_extension_status   # optional: disable global availability checks in this context
```

**Recommendation**: for deterministic transfer behavior, enable either `transfer` or `attended_transfer` in a given context/pipeline (not both), unless your prompt explicitly distinguishes when to use each.

### Environment Variables (.env)

```bash
# Resend API (for email tools)
RESEND_API_KEY=re_xxxxxxxxxxxx

# SMTP (optional): local mail server for email tools
SMTP_HOST=smtp.yourcompany.com
SMTP_PORT=587
SMTP_USERNAME=your_user
SMTP_PASSWORD=your_password
SMTP_TLS_MODE=starttls  # starttls | smtps | none
SMTP_TLS_VERIFY=true
SMTP_TIMEOUT_SECONDS=10
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
| `DIALED_NUMBER` | Called number (internal calls) | `3000` |
| `CALLERID(name)` | Caller's name (auto-available to AI) | Any string |
| `CALLERID(num)` | Caller's number (auto-available to AI) | Phone number |

**Called Number Capture**: The AI engine automatically captures the "called number" (the DID or extension that was dialed) and makes it available as `{called_number}` in tools and prompts:
- **External calls**: Captured from `__FROM_DID` (set by FreePBX for inbound DID routes)
- **Internal calls**: Set `DIALED_NUMBER` in dialplan before Stasis (e.g., `Set(DIALED_NUMBER=3000)`)
- **Fallback**: If neither is available, defaults to `"unknown"`

See [FreePBX Integration Guide](FreePBX-Integration-Guide.md) for complete dialplan documentation.

### 6. Template Variables in Prompts

Context prompts support template variables for call-specific data. This is especially useful for MCP tools that need caller information.

| Variable | Description | Default |
|----------|-------------|---------|
| `{caller_name}` | Caller ID name | `"there"` |
| `{caller_number}` | Caller phone number (ANI) | `"unknown"` |
| `{called_number}` | DID or extension that was dialed | `"unknown"` |
| `{caller_id}` | Alias for `{caller_number}` | `"unknown"` |
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

**HTTP Tools (v4.2+)**:
- `src/tools/http/generic_lookup.py` - Pre-call HTTP lookup tool
- `src/tools/http/generic_webhook.py` - Post-call webhook tool
- `src/tools/context.py` - PreCallContext, PostCallContext dataclasses

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

**Found a bug?** [Open an issue](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues)  
**Have questions?** [Start a discussion](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/discussions)

---

**Last Updated**: January 2026  
**Version**: 4.2.0  
**Status**: ✅ Production Ready
