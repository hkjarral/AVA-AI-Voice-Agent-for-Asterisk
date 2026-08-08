# Milestone 24: Tool System Enhancements (Pre-Call, In-Call, Post-Call)

**Status**: In Progress  
**Priority**: High  
**Estimated Effort**: 3–4 weeks (MVP)  
**Branch**: `develop`  

## Summary

Enhance the existing tool system with **three execution phases** and **flexible configuration**:

- **Pre-call tools (pre-conversation)**: Fetch context **after call answer but before the AI speaks** (CRM lookup, caller enrichment)
- **In-call tools**: Existing tool execution during AI conversation (unchanged)
- **Post-call tools**: Fire-and-forget webhooks/actions after call ends (CRM updates, n8n/Make/Zapier triggers)
- **Global vs per-context** tool configuration with Admin UI support
- **Generic HTTP integration** compatible with GoHighLevel, n8n, Make, Zapier, and custom endpoints

This milestone extends the proven tool architecture (`src/tools/`) while maintaining backward compatibility with existing in-call tools.

## Motivation

Community feedback indicates users need:

1. **Pre-call context enrichment**: Query CRM (e.g., GoHighLevel) by caller ID to inject customer name, account notes, or last interaction into the system prompt before the AI speaks.
2. **Post-call automation**: Push call outcomes, transcripts, and summaries to external systems (n8n workflows, Make scenarios, Zapier zaps, custom webhooks) without blocking call cleanup.
3. **Global tools**: Some tools (e.g., `hangup_call`, a global webhook) should be available in every context without manual configuration.

The existing `SendEmailSummaryTool` is effectively a post-call tool but runs during the conversation. This milestone formalizes the pattern and makes it configurable.

## Research Findings

### GoHighLevel Integration

**API Capabilities (verified via official GHL Marketplace docs)**:

| Endpoint | Method | Use Case | Auth |
|----------|--------|----------|------|
| `/contacts/search` | POST | Search contacts (e.g., by phone) | Bearer |
| `/contacts/` | POST | Create/upsert contact | Bearer |
| `/contacts/:contactId/notes` | POST | Add a note to a contact | Bearer |

> ⚠️ **Note**: The `GET /contacts/` endpoint is **deprecated**. Use `POST /contacts/search` instead.

**Authentication Options**:
- **OAuth 2.0** (Marketplace apps): Full OAuth flow with refresh tokens, granular scopes
- **Private Integration Token** (simpler): Long-lived API key generated in Agency/Location settings

Both use `Authorization: Bearer <token>` header. Private tokens are simpler but lack granular scopes.

**Pre-Call Use Case (CRM Lookup by Phone)** (validate request/response shape via official docs; Admin UI “Test” is available in v5.3.1+):

```http
POST https://services.leadconnectorhq.com/contacts/search
Authorization: Bearer <access_token>
Version: 2021-07-28
Content-Type: application/json

{
  "locationId": "<location_id>",
  "filters": [
    {
      "field": "phone",
      "operator": "eq",
      "value": "+15551234567"
    }
  ],
  "limit": 1
}
```

**Example Response** (200 OK):

```json
{
  "contacts": [
    {
      "id": "abc123def456",
      "locationId": "loc_xyz",
      "firstName": "John",
      "lastName": "Smith",
      "email": "john.smith@example.com",
      "phone": "+15551234567",
      "tags": ["VIP", "Premium"],
      "customFields": [
        { "id": "cf_123", "key": "last_call_notes", "value": "Discussed renewal" },
        { "id": "cf_456", "key": "account_status", "value": "Active" }
      ],
      "dateAdded": "2024-01-15T10:30:00Z",
      "dateUpdated": "2025-01-20T14:22:00Z"
    }
  ],
  "total": 1
}
```

**Response Mapping (MVP = dot-path only, string-only outputs)**:

| Output Variable | Path | Notes |
|-----------------|------------|-------|
| `customer_first_name` | `contacts[0].firstName` | Direct field |
| `customer_last_name` | `contacts[0].lastName` | Direct field |
| `customer_email` | `contacts[0].email` | Direct field |
| `contact_id` | `contacts[0].id` | **Store for post-call update** |

> Note: JMESPath-style expressions (e.g., `join(...)`, filtering `customFields` by key) are **Post-MVP**.

**Post-Call Use Case (Add Note)**:

```http
POST https://services.leadconnectorhq.com/contacts/{contactId}/notes
Authorization: Bearer <access_token>
Version: 2021-07-28
Content-Type: application/json

{
  "userId": "<ghl_user_id>",
  "body": "AI Call Summary (127s):\n\nCustomer inquired about order status...\n\nOutcome: Transferred to sales"
}
```

**Example Response** (201 Created):

```json
{
  "id": "note_789",
  "contactId": "abc123def456",
  "body": "AI Call Summary (127s):\n\nCustomer inquired about...",
  "createdAt": "2026-01-26T21:36:00Z"
}
```

### n8n / Make / Zapier Webhook Integration

**Common Pattern (verified via official n8n docs)**:

All major automation platforms support **incoming webhooks** with similar patterns:

| Platform | Webhook URL Format | Max Payload | Key Features |
|----------|-------------------|-------------|---------------|
| n8n | `https://your-n8n.url/webhook/<path>` | 16 MB | JSON body, custom headers, respond with JSON |
| Make | `https://hook.make.com/<webhook-id>` | 5 MB | JSON body, query params |
| Zapier | `https://hooks.zapier.com/hooks/catch/<account>/<zap>` | 10 MB | JSON body |

**n8n Webhook Node Details** (from official docs):

- **HTTP Methods**: GET, POST, PUT, PATCH, DELETE, HEAD
- **Response Modes**:
  - `Immediately`: Returns 200 + "Workflow got started" (fire-and-forget) ✅ **Use this for post-call**
  - `When Last Node Finishes`: Waits for workflow completion, returns result
  - `Using Respond to Webhook Node`: Custom response
- **Authentication Options**: None, Basic Auth, Header Auth, JWT Auth
- **Path Parameters**: Supports `/:variable` for dynamic routing

**Post-Call Webhook Payload (recommended structure)**:
```json
{
  "event_type": "call_completed",
  "call_id": "1763582133.6224",
  "caller_number": "+15551234567",
  "called_number": "+15559876543",
  "context_name": "sales",
  "provider": "deepgram",
  "call_duration": 127,
  "call_outcome": "answered_human",
  "transcript": [
    {"role": "assistant", "content": "Hello, how can I help you today?"},
    {"role": "user", "content": "I'd like to check my order status."}
  ],
  "summary": "Customer called to check order status for order #12345...",
  "tool_calls": [
    {"name": "transfer", "parameters": {"destination": "sales_team"}, "result": "success"}
  ],
  "custom_data": {}
}
```

**Key Learnings**:
- All platforms expect `POST` with `Content-Type: application/json`
- Custom headers supported (for API keys, signatures)
- Response is typically ignored (fire-and-forget)
- Retry logic handled by the receiving platform, not the sender

**n8n Webhook behavior (official docs)**:
- Webhook node can respond **Immediately** (returns the configured response code and “Workflow got started”), or wait until “When Last Node Finishes”, or use a dedicated “Respond to Webhook” node.
- Response code and response data are configurable in n8n.
- n8n Test vs Production URLs: Test URLs require "Listen for Test Event" to be active

### Available Template Variables (from engine)

These variables are available for prompt/greeting substitution and tool payloads. Source: `docs/MCP_INTEGRATION.md` and `src/engine.py` (`_apply_prompt_template_substitution()`).

| Variable | Description | Default | Source |
|----------|-------------|---------|--------|
| `{caller_name}` | Caller ID name | `"there"` | `CALLERID(name)` from Asterisk |
| `{caller_number}` | Caller phone number (ANI) | `"unknown"` | `CALLERID(num)` from Asterisk |
| `{call_id}` | Unique call identifier | (always set) | Internal call ID |
| `{context_name}` | AI_CONTEXT from dialplan | `""` | `Set(AI_CONTEXT=...)` |
| `{call_direction}` | Call direction | `"inbound"` | `"inbound"` or `"outbound"` |
| `{campaign_id}` | Outbound campaign ID | `""` | Outbound dialer only |
| `{lead_id}` | Outbound lead/contact ID | `""` | Outbound dialer only |
| `{called_number}` | DID that was called | `""` | `CALLERID(dnid)` or channel var |

**Pre-call tools will add** additional variables to this list (e.g., `{customer_name}`, `{customer_email}`, `{contact_id}`).

**Post-call payloads** have access to session data:

| Variable | Description |
|----------|-------------|
| `{call_duration}` | Call duration in seconds |
| `{call_outcome}` | Outcome (e.g., `answered_human`, `voicemail`, `no_answer`) |
| `{transcript_json}` | Full conversation history as JSON array |
| `{summary}` | AI-generated call summary (if available) |
| `{tool_calls_json}` | List of in-call tool executions as JSON |
| `{pre_call_results_json}` | Data from pre-call tools as JSON (whole object) |
| `{provider}` | Provider name used for the call |

Each pre-call output variable is **also** exposed as its own placeholder (e.g.
`{customer_name}`), so a post-call body can reference it directly:
`"customer_name": "{customer_name}"`. A built-in variable always wins — a
pre-call variable named like a built-in (e.g. `caller_number`) never overrides it.

### Existing Tool Architecture (Code Analysis)

**Current Files**:
- `src/tools/base.py` — `Tool` ABC, `ToolDefinition`, `ToolParameter` dataclasses
- `src/tools/context.py` — `ToolExecutionContext` with session/ARI access
- `src/tools/registry.py` — `ToolRegistry` singleton, tool lookup, schema export
- `src/engine.py` — `_execute_provider_tool()` handles in-call execution

**Extension Points Identified**:
1. `ToolDefinition` → add `phase`, `output_variables`, `is_global`, `timeout_ms`
2. `ToolRegistry` → add phase-based filtering methods
3. `ToolExecutionContext` → create `PreCallContext` and `PostCallContext` variants
4. `engine.py` → add `_execute_pre_call_tools()` and `_execute_post_call_tools()` methods
5. `ContextConfig` → add `pre_call_tools` and `post_call_tools` lists

## Architecture (Tool Execution Flow)

### A) Pre-Call Tool Execution

```text
┌─────────────────────────────────────────────────────────────────────┐
│  Call Lands in Stasis (_handle_caller_stasis_start_hybrid)          │
├─────────────────────────────────────────────────────────────────────┤
│  1. Resolve context_name from dialplan / channel vars               │
│  2. Get context config (prompt, provider, tools, etc.)              │
│  3. Collect pre_call_tools:                                         │
│     └─ Global pre-call tools (is_global=true, unless opted out)     │
│     └─ Context-specific pre-call tools                              │
│  4. Answer the call (caller is now in AI engine)                    │
│  5. Optional: play brief hold audio (MOH / ringback / short prompt) │
│  6. Execute all pre-call tools IN PARALLEL (asyncio.gather)         │
│     └─ Each tool has configurable timeout_ms (default 2000ms)       │
│     └─ On timeout/error: log warning, return empty values           │
│  7. Collect output_variables from each tool result                  │
│     └─ {customer_name: "John", last_call_notes: "..."}              │
│     └─ null/None values → empty string ""                           │
│  8. Inject variables into system prompt template                    │
│     └─ "Hello {customer_name}" → "Hello John"                       │
│  9. Store pre_call_results in session for in-call tool access       │
│ 10. Continue with provider setup and AI conversation                │
└─────────────────────────────────────────────────────────────────────┘
```

**Hook Point**: `src/engine.py` in `_handle_caller_stasis_start_hybrid()`, after context resolution and answer, before provider initialization / greeting.

**PreCallContext** provides:
- `caller_number` — Caller's phone number (ANI)
- `called_number` — DID that was called
- `context_name` — Resolved context
- `channel_vars` — Custom Asterisk channel variables
- `config` — Full app config

### B) In-Call Tool Execution (Unchanged)

Existing flow via `_execute_provider_tool()` remains unchanged:
- Provider (Deepgram, OpenAI, ElevenLabs, pipelines) requests tool execution
- `ToolExecutionContext` provides call_id, session, ARI client
- Tool executes and returns result to provider
- Terminal tools (hangup, transfer) end the conversation

### C) Post-Call Tool Execution

```text
┌─────────────────────────────────────────────────────────────────────┐
│  Call Ends (hangup, transfer, StasisEnd, or disconnect)            │
├─────────────────────────────────────────────────────────────────────┤
│  1. Gather comprehensive session data:                              │
│     └─ call_id, caller_number, called_number, context_name          │
│     └─ call_duration, call_outcome                                  │
│     └─ conversation_history (transcript)                            │
│     └─ tool_calls (list of in-call tool executions)                 │
│     └─ summary (if generated)                                       │
│     └─ pre_call_results (data from pre-call tools)                  │
│  2. Collect post_call_tools:                                        │
│     └─ Global post-call tools (is_global=true) — fire unless opted out│
│     └─ Context-specific post-call tools                             │
│  3. Fire all post-call tools ASYNC (fire-and-forget)                │
│     └─ asyncio.create_task() — do NOT await                         │
│     └─ No retry on failure (fire-and-forget by design)              │
│  4. Log at INFO level: "Post-call tools fired" with tool names      │
│  5. Log at DEBUG level: Full payload details                        │
│  6. Proceed with normal call cleanup (session, metrics, etc.)       │
└─────────────────────────────────────────────────────────────────────┘
```

**Hook Point**: `src/engine.py` in `_on_channel_destroyed()` or end of call lifecycle, after session data is finalized.

**PostCallContext** provides:
- All session data (call_id, caller_number, duration, etc.)
- `conversation_history` — Full transcript as list
- `tool_calls` — List of in-call tool executions
- `summary` — Call summary if available
- `pre_call_results` — Data fetched by pre-call tools

## Scope (MVP)

### Tool Phase System

- Extend `ToolDefinition` with `phase: ToolPhase` enum (`pre_call`, `in_call`, `post_call`)
- Add `output_variables: List[str]` for pre-call tools to declare what they provide
- Add `is_global: bool` for tools that should be available in all contexts
- Add `timeout_ms: int` for per-tool timeout configuration (default 2000ms)

### Pre-Call Tools

- `PreCallTool` base class with `PreCallContext`
- Parallel execution with per-tool configurable timeout
- On timeout/error: use empty string `""` for all output_variables, log warning
- Variable injection into system prompt using `{variable_name}` syntax

### Post-Call Tools

- `PostCallTool` base class with `PostCallContext`
- Fire-and-forget execution via `asyncio.create_task()`
- No retry mechanism (by design; receiving systems handle retries)
- Comprehensive session data passed to each tool

### Built-in Tool Implementations

1. **GenericHTTPLookupTool** (pre-call)
   - Configurable URL, method, headers, response mapping
   - Maps JSON response fields to output_variables
   - Works with GoHighLevel Contacts API, custom CRMs

2. **GenericWebhookTool** (post-call)
   - Configurable URL, method, headers, payload template
   - Template supports `{call_id}`, `{caller_number}`, `{transcript_json}`, etc.
   - Works with n8n, Make, Zapier, custom endpoints

3. **Existing tools** (in-call, unchanged)
   - `transfer`, `hangup_call`, `leave_voicemail`, etc.
   - `send_email_summary` can optionally be recategorized as post-call

### Global vs Per-Context Configuration

- Tools marked `is_global: true` are enabled by default in every context
- Contexts can opt out of global tools when needed (compliance/cost control):
  - `disable_global_pre_call_tools: [...]`
  - `disable_global_in_call_tools: [...]`
  - `disable_global_post_call_tools: [...]`
- Per-context tools can be enabled/disabled per context
- Context config lists: `pre_call_tools`, `tools` (in-call), `post_call_tools`

## Implementation Addendum (Repo-Specific)

This section maps the milestone requirements onto the **current** repo structure, engine lifecycle, and Admin UI patterns so implementation work is straightforward and doesn’t fight existing abstractions.

### Current Reality Check (Important Constraints)

1. **Template substitution already exists and should be reused**:
   - `src/engine.py` has `_apply_prompt_template_substitution()` which is **safe** (unknown placeholders remain as-is) and already supports `{caller_name}`, `{caller_number}`, `{context_name}`, outbound vars, etc.
   - Do **not** rely on Python `str.format(...)` for greeting/prompt once pre-call variables are introduced; `str.format` will throw on unknown keys and is harder to make “safe”.

2. **Env var placeholders in YAML are not globally resolved today**:
   - `docs/Configuration-Reference.md` explicitly notes `${VAR}` placeholder resolution is limited in scope today.
   - If this milestone expects `${GHL_API_KEY}` inside `tools.*` definitions, we must implement **tool-side env resolution** (recommended) or expand YAML resolution scope.

3. **Call cleanup runs concurrently and can fire multiple times**:
   - The main engine already has “concurrent cleanup” scenarios (see `src/engine.py` cleanup guardrails and existing end-of-call email tool logic).
   - Post-call tool firing must be **idempotent per `call_id`** to avoid duplicate webhooks/CRM writes.

4. **Admin UI already has Tools + Contexts surfaces**:
   - Tools: `admin_ui/frontend/src/pages/ToolsPage.tsx` renders a large `ToolForm` used for existing tool settings (transfer, voicemail, attended transfer, etc.).
   - Contexts: `admin_ui/frontend/src/pages/ContextsPage.tsx` + `admin_ui/frontend/src/components/config/ContextForm.tsx` currently allowlist **in-call tools only** via `contexts.<name>.tools`.
   - This milestone should extend these existing screens (vs building a totally new “Tools system” UI from scratch).

### Proposed Minimal Data Model Extensions

Add the smallest set of new fields needed to support the phase system without refactoring existing tooling:

- `src/core/models.py` (`CallSession`)
  - `pre_call_results: Dict[str, str] = field(default_factory=dict)`
  - `pre_call_tool_status: Dict[str, Any] = field(default_factory=dict)` (optional; per-tool timing/status for observability)
  - `post_call_fired: bool = False` (or equivalent idempotency guard in engine-level structure)

Optional (recommended for UI/debugging parity):

- `src/core/call_history.py` (`CallRecord` + persistence)
  - `pre_call_results: Dict[str, Any]` (stored as JSON TEXT)
  - Rationale: Post-call systems and operators often need to see “what enrichment happened” for a call.

### Tool Registry + Definition Changes

#### Tool phase metadata

Add `ToolPhase` to `src/tools/base.py`:

- `ToolPhase.PRE_CALL`, `ToolPhase.IN_CALL`, `ToolPhase.POST_CALL`
- Default for existing tools should be `IN_CALL` to preserve current behavior.

Extend `ToolDefinition` (`src/tools/base.py`) with **optional** fields:

- `phase: ToolPhase = ToolPhase.IN_CALL`
- `is_global: bool = False`
- `output_variables: List[str] = field(default_factory=list)` (pre-call only)
- `timeout_ms: Optional[int] = None` (phase tools); keep existing `max_execution_time` for provider tool execution until migrated

#### Provider exposure filtering (avoid accidental provider tool exposure)

Provider schema generation and context allowlisting must remain **in-call only**:

- Update `src/tools/registry.py`:
  - Add `*_schema_filtered(..., phase=ToolPhase.IN_CALL)` helpers or equivalent.
  - Ensure pre-call and post-call tools are not emitted to providers by default.

This is especially important because the Admin UI currently derives “available tools” from `config.tools.*.enabled` and will otherwise show phase tools in the same list as in-call tools.

### Tool Definition + Configuration Shape (YAML)

#### Tool entries need a discriminator (`kind`)

Today, `config.tools` contains both:
- “system tool settings” blocks (e.g., `transfer`, `attended_transfer`, `ai_identity`, etc.), and
- “tool instances” like `send_email_summary` which are enabled/disabled by `enabled: true/false`.

For configured tools (HTTP lookup/webhook), add a discriminator field so the engine can register/execute them correctly:

- `kind: generic_http_lookup` (pre-call)
- `kind: generic_webhook` (post-call)
- `kind: builtin` (optional, default when absent)

Example (revised from earlier YAML in this doc; note the `kind` field and env-var substitution):

```yaml
tools:
  # Pre-call tools (run after answer, before AI speaks)
  ghl_contact_lookup:
    kind: generic_http_lookup
    phase: pre_call
    enabled: true
    is_global: false
    timeout_ms: 2000
    hold_audio_file: "custom/please-wait"  # played via ARI if threshold exceeded
    hold_audio_threshold_ms: 500  # ms before playing hold audio (default: 500)
    method: POST
    url: "https://services.leadconnectorhq.com/contacts/search"
    headers:
      Authorization: "Bearer ${GHL_API_KEY}"
      Version: "2021-07-28"
      Content-Type: "application/json"
    body_template: |
      {
        "locationId": "${GHL_LOCATION_ID}",
        "filters": [
          {"field": "phone", "operator": "eq", "value": "{caller_number}"}
        ],
        "limit": 1
      }
    # MVP mapping: dot paths + `[index]` only; output values are strings.
    output_variables:
      customer_first_name: "contacts[0].firstName"
      customer_email: "contacts[0].email"
      contact_id: "contacts[0].id"

  custom_crm_lookup:
    kind: generic_http_lookup
    phase: pre_call
    enabled: false
    is_global: false
    timeout_ms: 3000
    method: POST
    url: "${CUSTOM_CRM_URL}/api/lookup"
    headers:
      X-API-Key: "${CUSTOM_CRM_API_KEY}"
      Content-Type: "application/json"
    body_template: |
      {"phone": "{caller_number}"}
    output_variables:
      account_status: "status"
      account_balance: "balance"

  # Post-call tools (fire-and-forget after call ends)
  webhook_automation:
    kind: generic_webhook
    phase: post_call
    enabled: true
    is_global: true  # fires for ALL calls (unless opted out per context)
    timeout_ms: 10000
    method: POST
    url: "${WEBHOOK_URL}"
    headers:
      Content-Type: "application/json"
      X-API-Key: "${WEBHOOK_API_KEY}"
    generate_summary: true
    summary_max_words: 120
    payload_template: |
      {
        "schema_version": 1,
        "event_type": "call_completed",
        "call_id": "{call_id}",
        "caller_number": "{caller_number}",
        "called_number": "{called_number}",
        "context_name": "{context_name}",
        "provider": "{provider}",
        "call_duration": {call_duration},
        "call_outcome": "{call_outcome}",
        "summary_json": {summary_json},
        "transcript": {transcript_json},
        "tool_calls": {tool_calls_json},
        "pre_call_results": {pre_call_results_json}
      }

  ghl_update_contact:
    kind: generic_webhook
    phase: post_call
    enabled: false
    is_global: false
    timeout_ms: 10000
    method: POST
    url: "https://services.leadconnectorhq.com/contacts/{contact_id}/notes"
    headers:
      Authorization: "Bearer ${GHL_API_KEY}"
      Version: "2021-07-28"
      Content-Type: "application/json"
    payload_template: |
      {
        "userId": "${GHL_USER_ID}",
        "body": "AI Call Summary ({call_duration}s):\\n\\n{summary}"
      }

# Note: `send_email_summary` is auto-triggered during call cleanup when enabled and is not configured as a post-call tool.
```

### Context-Level Tool Assignment

```yaml
contexts:
  sales:
    prompt: |
      You are a helpful sales assistant for Acme Corp.
      
      Customer Information:
      - Name: {customer_first_name}
      - Email: {customer_email}
      - Contact ID: {contact_id}
      
      Use this information to provide personalized service.
    greeting: "Hello {customer_first_name}, thanks for calling Acme Corp sales!"
    provider: deepgram
    
    # Pre-call tools (run after answer, before AI speaks)
    pre_call_tools:
      - ghl_contact_lookup
    
    # In-call tools (AI can invoke during conversation)
    tools:
      - transfer
      - hangup_call  # Also global, but explicit listing is fine
      - leave_voicemail
    
    # Post-call tools (fire-and-forget after call ends)
    post_call_tools:
      - ghl_update_contact
    # Note: webhook_automation is global, fires automatically unless opted out per context

  support:
    prompt: |
      You are a support agent. Customer: {customer_first_name}
    greeting: "Hi {customer_first_name}, how can I help you today?"
    provider: deepgram
    
    pre_call_tools:
      - ghl_contact_lookup
    tools:
      - transfer
      - hangup_call
    # Global tools are enabled by default. Opt out per context if needed:
    disable_global_post_call_tools:
      - webhook_automation
    post_call_tools: []  # Only global post-call tools fire

  default:
    prompt: "You are a helpful AI assistant."
    greeting: "Hello, how can I help you?"
    provider: deepgram
    pre_call_tools: []  # No enrichment
    tools:
      - hangup_call
    post_call_tools: []
```

## Admin UI Design

### Tools Page (Three Tabs)

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Tools Configuration                                          [Save] │
├─────────────────────────────────────────────────────────────────────┤
│ [ Pre-Call ]  [ In-Call ]  [ Post-Call ]                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│ Pre-Call Tools                                                      │
│ These tools run after the call is answered, before the AI speaks.  │
│ Use them to fetch customer data from your CRM or external systems. │
│                                                                     │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ ☑ GoHighLevel Contact Lookup           [🌐 Global] [⚙ Config]  │ │
│ │   Outputs: customer_name, customer_email, last_call_notes       │ │
│ │   Timeout: 2000ms                                               │ │
│ ├─────────────────────────────────────────────────────────────────┤ │
│ │ ☐ Custom HTTP Lookup                            [⚙ Config]     │ │
│ │   Outputs: (configure in settings)                              │ │
│ │   Timeout: 3000ms                                               │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ [ + Add Pre-Call Tool ]                                             │
│                                                                     │
│ ─────────────────────────────────────────────────────────────────── │
│                                                                     │
│ Timeout Behavior                                                    │
│ When a pre-call tool times out or fails:                           │
│ ○ Start call immediately with empty values (recommended)           │
│ ○ Wait longer (up to 2x timeout)                                   │
│ ○ Use cached values from last successful call (if available)       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Context Page (Tool Sections)

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Context: sales                                               [Save] │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│ System Prompt                                                       │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ You are a helpful sales assistant.                              │ │
│ │                                                                 │ │
│ │ Customer: {customer_name}                                       │ │
│ │ Last notes: {last_call_notes}                                   │ │
│ │                                                                 │ │
│ │ Available variables: {customer_name}, {customer_email},         │ │
│ │ {last_call_notes}, {customer_tags}, {caller_number}, {called_number}│ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ ▼ Pre-Call Tools                                                    │
│   Fetch data after answer, before the AI speaks                    │
│   ┌───────────────────────────────────────────────────────────────┐ │
│   │ ☑ GoHighLevel Contact Lookup                                  │ │
│   │ ☐ Custom HTTP Lookup                                          │ │
│   └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ ▼ In-Call Tools                                                     │
│   Tools the AI can use during the conversation                     │
│   ┌───────────────────────────────────────────────────────────────┐ │
│   │ 🔒 hangup_call (Global default)                [Opt-out ☐]    │ │
│   │ ☑ transfer                                                    │ │
│   │ ☑ leave_voicemail                                             │ │
│   │ ☐ request_transcript                                          │ │
│   └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ ▼ Post-Call Tools                                                   │
│   Run after the call ends (fire-and-forget)                        │
│   ┌───────────────────────────────────────────────────────────────┐ │
│   │ 🔒 webhook_automation (Global default)         [Opt-out ☐]    │ │
│   │ ☑ send_email_summary                                          │ │
│   │ ☐ ghl_update_contact                                          │ │
│   └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Tool Configuration Modal

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Configure: GoHighLevel Contact Lookup                        [Save] │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│ Basic Settings                                                      │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ Name:        [ GoHighLevel Contact Lookup          ]            │ │
│ │ Enabled:     [✓]                                                │ │
│ │ Global:      [ ] (Available in all contexts)                    │ │
│ │ Timeout:     [ 2000 ] ms                                        │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ HTTP Request                                                        │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ URL:    [ https://services.leadconnectorhq.com/contacts/search ]│ │
│ │ Method: [ POST ▼ ]                                              │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ Headers                                                             │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ Authorization: [ Bearer ${GHL_API_KEY}           ] [−]          │ │
│ │ Version:       [ 2021-07-28                      ] [−]          │ │
│ │ [ + Add Header ]                                                │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ Body Template                                                       │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ {                                                               │ │
│ │   "locationId": "${GHL_LOCATION_ID}",                            │ │
│ │   "filters": [                                                   │ │
│ │     {"field": "phone", "operator": "eq", "value": "{caller_number}"}│ │
│ │   ],                                                            │ │
│ │   "limit": 1                                                    │ │
│ │ }                                                               │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ Output Variables (for pre-call tools)                               │
│ Map response fields to prompt variables                             │
│ ┌─────────────────────────────────────────────────────────────────┐ │
│ │ customer_first_name: [ contacts[0].firstName ] [−]              │ │
│ │ customer_last_name:  [ contacts[0].lastName ] [−]               │ │
│ │ customer_email:      [ contacts[0].email ] [−]                  │ │
│ │ [ + Add Variable ]                                              │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│ [ Save ]                                             [ Cancel ]    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Implementation Plan (Phases)

### Phase 1 — Backend Foundation (1 week) ✅ COMPLETE

**Extend Tool Definitions**:
- ✅ Add `ToolPhase` enum to `src/tools/base.py`
- ✅ Extend `ToolDefinition` with `phase`, `output_variables`, `is_global`, `timeout_ms`, `hold_audio_file`, `hold_audio_threshold_ms`
- ✅ Create `PreCallContext` and `PostCallContext` dataclasses in `src/tools/context.py`
- ✅ Add `PreCallTool` and `PostCallTool` abstract base classes in `src/tools/base.py`
- ✅ Update `ToolRegistry` with phase-based filtering: `get_by_phase()`, `get_global_tools()`, `get_tools_for_context()`

**Files modified**:
- `src/tools/base.py` — Added `ToolPhase` enum, extended `ToolDefinition`, added `PreCallTool` and `PostCallTool` ABCs
- `src/tools/context.py` — Added `PreCallContext` and `PostCallContext` dataclasses
- `src/tools/registry.py` — Added phase-based filtering methods

### Phase 2 — Engine Integration (1 week) ✅ COMPLETE

**Pre-Call Execution**:
- ✅ Add `_execute_pre_call_tools()` method to `Engine` — parallel execution with `asyncio.gather()` and per-tool timeouts
- ✅ Hold audio support — plays Asterisk sound file if tool exceeds `hold_audio_threshold_ms`
- ✅ Store results in session (`pre_call_results`) for in-call access and prompt templating
- ✅ Hook into `_start_provider_session()` — runs before provider starts/greeting plays

**Post-Call Execution**:
- ✅ Add `_execute_post_call_tools()` method to `Engine` — fire-and-forget via `asyncio.create_task()`
- ✅ Comprehensive `PostCallContext` with call metrics, transcript, tool calls, pre-call results
- ✅ Hook into `_cleanup_call()` — runs after call ends, before session is removed

**Context Configuration**:
- ✅ Extend `ContextConfig` with `pre_call_tools`, `post_call_tools`, `disable_global_*` fields
- ✅ Update `_load_contexts()` to parse new fields from YAML

**Session Model**:
- ✅ Add `pre_call_results: Dict[str, str]` field to `CallSession` for storing CRM lookup data

**Files modified**:
- `src/engine.py` — Added `_execute_pre_call_tools()`, `_execute_post_call_tools()`, hooks in `_start_provider_session()` and `_cleanup_call()`
- `src/core/transport_orchestrator.py` — Extended `ContextConfig` with phase tool fields
- `src/core/models.py` — Added `pre_call_results` field to `CallSession`

### Phase 3 — Built-in Tools (1 week) ✅ COMPLETE

**GenericHTTPLookupTool** (pre-call):
- ✅ Configurable HTTP client (aiohttp) with timeout handling
- ✅ URL, method, headers, query params, body template with variable substitution
- ✅ Simple dot-notation response mapping to output_variables (string-only outputs)
- ✅ Environment variable substitution (`${VAR_NAME}`) for secrets
- ✅ Context variable substitution (`{caller_number}`, `{call_id}`, etc.)

**GenericWebhookTool** (post-call):
- ✅ Configurable HTTP POST with custom payload templates
- ✅ Variable substitution for payload, URL, headers
- ✅ Fire-and-forget execution (no retry)
- ✅ Comprehensive logging with URL redaction for security

**Registry Integration**:
- ✅ `initialize_http_tools_from_config()` method to load tools from YAML
- ✅ Tools registered with `kind: generic_http_lookup` or `kind: generic_webhook`
- ✅ Engine calls HTTP tool init after default tools

**Files created**:
- `src/tools/http/__init__.py` — Module exports
- `src/tools/http/generic_lookup.py` — Pre-call HTTP lookup tool
- `src/tools/http/generic_webhook.py` — Post-call webhook tool

**Files modified**:
- `src/tools/registry.py` — Added `initialize_http_tools_from_config()` method
- `src/engine.py` — Added HTTP tool initialization in `start()`
- `config/ai-agent.yaml` — Added example HTTP tool configurations

### Phase 4 — Configuration & Validation (0.5 week) ✅ COMPLETE

**YAML Schema Updates**:
- ✅ Add `pre_call_tools` and `post_call_tools` to context config (Phase 2)
- ✅ Environment variable substitution for secrets (`${API_KEY}`)
- ✅ Example configs in `config/ai-agent.yaml`

**Files modified**:
- `config/ai-agent.yaml` — Example HTTP tool configurations
- `src/core/transport_orchestrator.py` — ContextConfig phase tool fields (Phase 2)

### Production Validation ✅ COMPLETE (Jan 26, 2026)

**Test Environment**:
- Server: your-server.example.com
- Webhook URL: `https://your-webhook.example.com/call-events`
- Provider: google_live

**Test Results**:
- ✅ Post-call webhook fired successfully after call hangup
- ✅ Webhook received full call payload (call_id, caller, transcript, duration, etc.)
- ✅ HTTP 200 response confirmed
- ✅ No errors in ai_engine logs

**Bug Fixed During Testing**:
- Logging used structlog-style kwargs but standard Python logger doesn't support that
- Fix: Changed to f-string format in `generic_webhook.py` and `generic_lookup.py`
- Commit: 09a8d89

### Phase 5 — Admin UI (1 week) ✅ COMPLETE (Jan 26, 2026)

**Tools Page Enhancements**:
- ✅ Three-tab layout (Pre-Call, In-Call, Post-Call)
- ✅ HTTPToolForm component for webhook/lookup configuration
- ✅ URL, headers, payload template, output variable mapping UI
- ✅ Add/edit/delete HTTP tools with modal dialogs

**Context Page Enhancements**:
- ✅ Three collapsible tool sections per context (Pre-Call, In-Call, Post-Call)
- ✅ Global tool indicators (🔒 icon)
- ✅ Checkbox enable/disable for per-context pre_call_tools and post_call_tools
- ✅ Pass httpTools from config to ContextForm

**Files modified**:
- `admin_ui/frontend/src/pages/ToolsPage.tsx` — Phase tabs + HTTPToolForm integration
- `admin_ui/frontend/src/components/config/HTTPToolForm.tsx` — NEW: HTTP tool config UI
- `admin_ui/frontend/src/components/config/ContextForm.tsx` — Phase-based tool sections
- `admin_ui/frontend/src/pages/ContextsPage.tsx` — Pass httpTools prop

### Phase 5.1 — Webhook Summary Generation ✅ COMPLETE (Jan 26, 2026)

### Phase 5.2 — In-Call HTTP Tools ✅ COMPLETE (Jan 28, 2026)

**Feature**: HTTP tools that the AI can invoke during a conversation to fetch real-time data.

Unlike pre-call tools (which run before AI speaks) and post-call webhooks (which fire after hangup), in-call HTTP tools are **AI-invoked** during the live conversation. The AI decides when to call them based on conversation context.

**Use Cases**:
- Check appointment availability
- Look up order status
- Query real-time inventory
- Fetch account balance
- Any API call where the AI needs fresh data mid-conversation

**Configuration Example**:

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

**Key Differences from Pre-Call Tools**:

| Aspect | Pre-Call Tools | In-Call HTTP Tools |
|--------|---------------|--------------------|
| Trigger | Automatic (after answer) | AI-invoked (during conversation) |
| Parameters | Context variables only | AI provides parameters + context variables |
| Timing | Before AI speaks | During live conversation |
| Results | Injected into prompt | Returned to AI for response |

**Variable Substitution (Precedence)**:
1. **Context variables** (auto-injected): `{caller_number}`, `{called_number}`, `{call_id}`, etc.
2. **Pre-call variables** (from pre-call HTTP lookups): `{customer_id}`, `{customer_name}`, etc.
3. **AI parameters** (provided at runtime): Whatever the AI passes when invoking the tool

**Implementation**:
- ✅ `InCallHTTPTool` class in `src/tools/http/in_call_lookup.py`
- ✅ `InCallHTTPConfig` dataclass for tool configuration
- ✅ AI parameters schema generation for provider function calling
- ✅ Variable substitution from context, pre-call results, and AI params
- ✅ Output variable extraction or raw JSON return
- ✅ Error handling with configurable error message
- ✅ Hold audio support during long requests
- ✅ Pre-call variables accessible in in-call tools via `session.pre_call_results`

**Admin UI**:
- ✅ In-Call HTTP Tools section in Tools page (In-Call tab)
- ✅ Tool configuration modal with:
  - Description field for AI context
  - AI parameters definition (name, type, description, required)
  - URL, method, headers, query params, body template
  - Output variables mapping
  - Return raw JSON toggle
  - Error message configuration
  - Test values for AI parameters
- ✅ Test functionality with results panel
- ✅ Correct phase-specific titles and labels

**Files created**:
- `src/tools/http/in_call_lookup.py` — In-call HTTP tool implementation

**Files modified**:
- `src/tools/registry.py` — Added `initialize_in_call_http_tools_from_config()` method
- `src/engine.py` — Added per-context in-call HTTP tool registration
- `src/core/transport_orchestrator.py` — Extended `ContextConfig` with `in_call_http_tools`
- `admin_ui/frontend/src/pages/ToolsPage.tsx` — Added In-Call HTTP Tools section
- `admin_ui/frontend/src/components/config/HTTPToolForm.tsx` — Extended for in_call phase

**Feature**: AI-generated call summaries for post-call webhooks instead of sending full transcripts.

**Configuration**:

```yaml
demo_post_call_webhook:
  kind: generic_webhook
  phase: post_call
  generate_summary: true       # Enable AI summarization
  summary_max_words: 100       # Max words for summary
  payload_template: |
    {
      "transcript": "{summary}",  # Use {summary} instead of {transcript_json}
      ...
    }
```

**Implementation**:
- ✅ Added `generate_summary` and `summary_max_words` to `WebhookConfig` dataclass
- ✅ Added `_generate_summary()` method using OpenAI gpt-4o-mini
- ✅ Summary generated before payload template substitution
- ✅ Admin UI toggle in HTTPToolForm for post-call webhooks
- ✅ Added `openai>=1.0.0` to requirements.txt

**Files modified**:
- `src/tools/http/generic_webhook.py` — Summary generation logic
- `admin_ui/frontend/src/components/config/HTTPToolForm.tsx` — UI toggle
- `requirements.txt` — Added openai dependency

**Test Evidence** (Call 1769480034.541):

```log
Generated summary for webhook: demo_post_call_webhook length=327
Sending webhook: demo_post_call_webhook POST https://your-webhook.example.com/call-events
Webhook sent successfully: demo_post_call_webhook status=200
Post-call tool completed duration_ms=6074.03 tool=demo_post_call_webhook
```

### Lessons Learned (Phase 5.2)

1. **Pre-call variables in in-call tools**: Initially, in-call tools only had access to context variables and AI parameters. Added support to fetch `session.pre_call_results` so variables from pre-call CRM lookups can be used in in-call tool requests.

2. **Phase-specific UI labels**: The HTTPToolForm component needed phase-aware titles, descriptions, and button labels. Initially showed "Post-Call Webhooks" for in-call tools due to fallback logic.

3. **Test Results Panel**: Had to duplicate the Test Results Panel rendering for the in_call phase section since it was only conditionally rendered for pre_call.

### Lessons Learned (Phase 5.1)

1. **Missing dependency**: Initial summary generation failed with `No module named 'openai'`. The openai package wasn't in requirements.txt because monolithic providers import it dynamically. Added `openai>=1.0.0` to requirements.txt.

2. **Admin UI persistence**: TypeScript interface `HTTPToolConfig` must include all fields that need to be saved. Added `generate_summary?: boolean` and `summary_max_words?: number` to the interface.

3. **Two-step save flow**: Admin UI requires (1) Save in modal → updates local state, then (2) Save Changes button → persists to YAML. Users may miss step 2.

4. **Docker cache**: When debugging UI issues, `docker compose build --no-cache` may be needed to ensure frontend changes are included.

### Phase 6 — Testing & Documentation (0.5 week)

- Unit tests for new tool base classes
- Integration tests for pre-call and post-call execution
- Update `docs/TOOL_CALLING_GUIDE.md`
- Add example configurations for GoHighLevel, n8n, Make

## Acceptance Criteria (MVP)

### Pre-Call Tools

- [ ] Pre-call tools execute in parallel after call is answered and context is resolved (before AI speaks)
- [ ] Each tool respects its configured `timeout_ms`
- [ ] On timeout/error, output_variables default to empty string `""`
- [ ] Variables are injected into system prompt (`{customer_name}` → actual value)
- [ ] Pre-call results stored in session for in-call tool access
- [ ] Logs show pre-call tool execution and timing at INFO level
- [ ] Caller does not experience dead air while pre-call tools run (per-tool `hold_audio_file` + `hold_audio_threshold_ms`)

### Post-Call Tools

- [ ] Post-call tools fire asynchronously after call ends
- [ ] Tools do not block call cleanup (fire-and-forget)
- [ ] Comprehensive session data passed to each tool
- [ ] INFO log confirms each tool was fired
- [ ] DEBUG log shows full payload for each tool

### Global vs Per-Context

- [ ] Global tools (`is_global: true`) appear in all contexts automatically
- [ ] Contexts can opt out of global tools (by phase)
- [ ] Per-context tools can be enabled/disabled per context
- [ ] Admin UI shows 🔒 icon for global tools

### GenericHTTPLookupTool

- [ ] Configurable URL, method, headers, query params
- [ ] Environment variable substitution (`${API_KEY}`)
- [ ] Dot-path response mapping to output_variables (string-only outputs)
- [ ] Works with GoHighLevel Contacts API

### GenericWebhookTool

- [ ] Configurable URL, method, headers, payload template
- [ ] Template substitution for call data (`{call_id}`, `{transcript_json}`)
- [ ] Works with n8n, Make, Zapier webhooks
- [ ] No retry on failure (fire-and-forget)

### Admin UI

- [ ] Tools page has three tabs (Pre-Call, In-Call, Post-Call)
- [ ] Tool configuration modal with HTTP settings
- [ ] Context page shows all three tool categories
- [ ] Global tools visually distinguished

## Testing & Verification (Smoke)

### 1) Pre-Call Tool Test

1. Configure `ghl_contact_lookup` (or mock HTTP endpoint)
2. Add `{customer_name}` to a context's system prompt
3. Place a test call
4. Verify logs show pre-call tool execution
5. Verify AI greeting includes the resolved customer name

### 2) Post-Call Webhook Test

1. Configure `webhook_automation` with a test endpoint (e.g., webhook.site)
2. Place a test call and end it
3. Verify webhook receives the payload
4. Verify call cleanup was not delayed

### 3) Timeout Handling Test

1. Configure a pre-call tool with a slow/unreachable endpoint
2. Set `timeout_ms: 1000`
3. Place a test call
4. Verify call proceeds within ~1s with empty values
5. Verify warning logged for timeout

### 4) Global Tool Test

1. Mark a tool as `is_global: true`
2. Create a context that doesn't explicitly list the tool
3. Verify the tool still executes for calls in that context

## Observability

### Logging Requirements

Pre-call and post-call tool execution logs should include:
- `call_id`, `context_name`
- `tool_name`, `tool_phase`
- `duration_ms`, `status` (success/timeout/error)
- For pre-call: `output_variables` (keys only, not values for privacy)
- For post-call: `payload_size_bytes`

### Metrics (Optional, Post-MVP)

```promql
aava_tool_execution_total{phase, tool_name, status}
aava_tool_execution_duration_seconds{phase, tool_name}
aava_precall_timeout_total{tool_name}
aava_postcall_fire_total{tool_name}
```

## Security Considerations

- API keys stored in environment variables, not YAML directly
- Pre-call response data sanitized before prompt injection
- Size limits on pre-call output_variables values
- Webhook payloads may contain PII; ensure endpoints are secured
- Rate limiting for pre-call HTTP calls (per-minute limits configurable)

## Regression Risks

This section documents known risks introduced by the pre-call and post-call HTTP tool system.

### SSRF (Server-Side Request Forgery)

**Risk**: Operators can configure arbitrary URLs in HTTP tools, which could be exploited to:
- Probe internal network services
- Access cloud metadata endpoints (e.g., `169.254.169.254`)
- Exfiltrate data via DNS/HTTP

**Mitigations (MVP)**:
- Short timeouts (default 2-5s) limit exposure
- Response size limits prevent large data exfiltration
- Secrets redacted in logs and UI

**Recommended Hardening (Post-MVP)**:
- Outbound HTTP allowlist (host/scheme policy)
- Block private/loopback/link-local IP ranges by default
- Operator override for self-hosted tools (`allow_private_ips: true`)

### Prompt Injection via CRM Data

**Risk**: Malicious data in CRM responses could be injected into AI prompts, potentially:
- Altering AI behavior
- Exfiltrating conversation data
- Bypassing safety guardrails

**Mitigations**:
- Output variables are string-only (no structured injection)
- Variable values are truncated to configurable max length
- Only operator-configured paths are extracted from responses
- Values are inserted as literal text, not evaluated as prompts

**Recommendation**: Document that operators should validate CRM data sources and use trusted integrations.

### Dependency Surface Expansion

**Risk**: Adding aiohttp as a runtime dependency expands the attack surface:
- aiohttp vulnerabilities could affect the system
- SSL/TLS configuration must be maintained

**Mitigations**:
- aiohttp is a well-maintained, widely-used library
- SSL verification enabled by default
- Regular dependency updates via CI/CD

### Outbound HTTP Safety (Recommended)

Phase tools introduce operator-configured outbound HTTP requests (including the Admin UI “Test” feature), which can create **SSRF-style risk** if misconfigured.

MVP stance:
- Allow outbound requests to any URL (operator-managed), but keep strong defaults: short timeouts, response size limits, and secret redaction in logs/UI.

Post-MVP hardening (recommended):
- Add an outbound HTTP allowlist policy (host + scheme + optional port), applied to:
  - pre-call HTTP tools
  - post-call webhooks
  - Admin UI test requests
- Default allowlist should be **empty** (deny-by-default) in high-security environments; permissive in dev.
- Block non-HTTP schemes and (by default) block requests to private/loopback/link-local IP ranges to reduce SSRF impact.
- Add explicit operator override controls (e.g., `allow_private_ips: true`) for self-hosted automation tools.

## Migration Path

### Existing `send_email_summary` Tool

The existing `SendEmailSummaryTool` runs during in-call but behaves like a post-call tool. Migration options:

1. **Keep as-is**: Works, but not ideal
2. **Recategorize**: Change `phase` to `post_call` and move trigger logic
3. **Dual-phase**: Allow tool to be invoked either way (in-call on request, post-call automatically)

Recommendation: Recategorize as `post_call` with backward-compatible behavior.

### Existing `tools` Configuration

The existing `tools:` list in contexts remains unchanged for in-call tools. Adding `pre_call_tools:` and `post_call_tools:` is additive and backward compatible.

## Post-MVP Enhancements

### Phase 7 — Advanced HTTP Features

- OAuth 2.0 token refresh for GoHighLevel
- Response caching for pre-call tools
- Retry with exponential backoff for post-call tools (configurable)
- Request/response logging for debugging
- Outbound HTTP allowlist + SSRF hardening (host/scheme policy, private IP blocking)

### Phase 8 — Specialized Integrations

- Dedicated GoHighLevel tool with UI wizard
- Dedicated n8n workflow selector
- Salesforce, HubSpot, Zoho CRM integrations

### Phase 9 — Enhanced Admin UI

- Tool execution history/logs in Admin UI
- Real-time webhook testing with response preview
- Variable auto-complete in prompt editor

## References

- GoHighLevel Contacts API index: <https://marketplace.gohighlevel.com/docs/ghl/contacts/contacts/>
- GoHighLevel Search Contacts (advanced): <https://marketplace.gohighlevel.com/docs/ghl/contacts/search-contacts-advanced/>
- GoHighLevel Create Note: <https://marketplace.gohighlevel.com/docs/ghl/contacts/create-note/>
- GoHighLevel Private Integrations Token: <https://marketplace.gohighlevel.com/docs/Authorization/PrivateIntegrationsToken/>
- GoHighLevel Scopes: <https://marketplace.gohighlevel.com/docs/Authorization/Scopes/>
- GoHighLevel OAuth: <https://marketplace.gohighlevel.com/docs/oauth/GettingStarted>
- n8n Webhook Node: <https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/>
- n8n Respond to Webhook Node: <https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.respondtowebhook/>
- Existing tool implementation: `src/tools/`
- Tool calling guide: `docs/TOOL_CALLING_GUIDE.md`
