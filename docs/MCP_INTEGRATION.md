# MCP Tool Integration
**Status**: Shipped (v4.5.2+)  
**Scope**: `ai-engine` (`src/`) tool calling + provider adapters + config + Admin UI

This document describes how AVA integrates Model Context Protocol (MCP) tools into the existing unified tool calling system. It is written to match the repo’s current architecture (ToolRegistry, per-context tool lists, provider adapters, SessionStore) and to support safe testing on a development server without impacting production baselines.

## Goals

- Add MCP tools as first-class tools in the existing `ToolRegistry`, so providers can call them the same way they call `transfer`, `hangup_call`, etc.
- Respect the existing per-call context system: `contexts.<name>.tools` must control which MCP tools are exposed and executable for that call.
- Keep tool names provider-safe (OpenAI/Deepgram/Google/ElevenLabs schema constraints) while still routing to `(mcp_server, mcp_tool)` internally.
- Preserve voice UX: provide a consistent spoken “result message” even when MCP returns structured JSON.
- Run safely in production-like environments: process lifecycle management for stdio servers, timeouts, metrics, logging, and secure secret handling.

## Non-goals

- Replacing existing tool calling or provider adapters.
- Automatically discovering arbitrary MCP servers over the network without explicit config.

## Admin UI

MCP servers can be configured via **Admin UI → Core Configuration → MCP** page (`/mcp`). This provides:
- Server list with status indicators
- Add/edit/remove MCP server configurations
- Hot-reload without container restart

## How This Fits the Current Tool Architecture

AVA already has:

- A unified `Tool` abstraction (`src/tools/base.py`) with provider-agnostic schemas.
- A global tool registry (`src/tools/registry.py`) that providers use to advertise tools and the engine uses to execute tools.
- Per-context tool lists (`contexts.<name>.tools` in YAML) which are already used by Google Live to filter the exposed tool schema.
- Provider adapters that translate tool schemas/events (OpenAI Realtime / Deepgram / Google Live / ElevenLabs).

The MCP integration should:

1. Register MCP tools into `ToolRegistry` as wrapper tools (`Tool` subclasses).
2. Filter *exposed schemas* per call using `contexts.<name>.tools` for all providers (not just Google Live).
3. Enforce an *execution allowlist* in the engine so a provider can’t call tools that weren’t exposed for that call.

## Naming & Namespacing (Provider-Safe)

Many providers have constraints on tool/function names (e.g., OpenAI tool names don’t accept dots `.` and have tight charset/length rules). Because of this:

- Do **not** expose tools as `aviation-atis.get_atis`.
- Instead, expose a sanitized tool name such as: `mcp_aviation_atis_get_atis`.

Internally, the tool wrapper maintains the mapping:

- `exposed_name` (provider-safe) → `(server_id, tool_name)` (MCP routing)

### Recommended mapping rules

- `server_id`: lower snake case, e.g. `aviation_atis` (normalize from config key)
- `tool_name`: as reported by MCP server, normalized to snake case if needed
- `exposed_name`: `mcp_{server_id}_{tool_name}` with:
  - only `[a-z0-9_]+`
  - length capped (recommend <= 64)

If a tool name collides, require an explicit override (see config).

## Voice UX for Structured Tool Results

MCP tools typically return structured JSON. To keep the voice agent predictable, each MCP tool wrapper should produce:

- `data`: the raw MCP result (for logging/debugging/follow-up reasoning)
- `message`: a short string intended to be spoken

Recommended configuration knobs:

- `speech_field`: pick a single field from the MCP response JSON to speak (e.g. `atis_text`)
- `speech_template`: format a spoken message using simple `{placeholders}` from the JSON

If neither is provided, the wrapper should fall back to:

- `message = "<short summary>"` (e.g., “I’ve fetched the result.”)
- Keep the raw JSON in `data` for the LLM to reason about in a follow-up turn.

## Slow Response Announcements

Some MCP tools may take seconds. For good caller UX, support a “please wait” announcement:

- `slow_response_threshold_ms`: after this delay, play/speak a short waiting message
- `slow_response_message`: what to say

Important implementation constraint:

- For **pipeline** mode (`local_hybrid`), the engine can synthesize filler speech safely.
- For **monolithic providers** (OpenAI Realtime / Deepgram Agent / Google Live / ElevenLabs Agent), the provider owns the audio stream; engine-injected speech can overlap or desync unless explicitly coordinated with `ConversationCoordinator`/gating.

Recommendation:

- Phase 1: implement slow-response announcements for pipeline mode first.
- Phase 2: add provider-specific support (or disable for monolithic providers by default).

## Google Live: 1011 Internal Errors (Practical Mitigation)

In some cases Google Live can close the WebSocket with `1011 Internal error` shortly after receiving `toolResponse` payloads. This appears to be a provider-side issue, but it is often correlated with tool responses that are large or deeply nested.

Mitigation implemented in this branch:

- Tool responses sent to Google Live are **sanitized and size-capped**, prioritizing `status`, `message`, and a few small control flags.
- Raw MCP payloads (e.g., `data`) are **not** forwarded to Google Live by default.

## Configuration (Proposed YAML)

This is the proposed config shape for `config/ai-agent.yaml`. It’s designed to avoid conflicting with existing `tools:` configuration.

```yaml
mcp:
  enabled: true
  servers:
    aviation_atis:
      transport: stdio
      # Built-in deterministic server (ai-engine container): src/mcp_servers/aviation_atis_server.py
      # Configure per-aerodrome extras (name/runway/frequency/advisory) via a separate YAML passed as an arg.
      command:
        - "python3"
        - "-m"
        - "src.mcp_servers.aviation_atis_server"
        - "--config"
        - "/app/config/aviation_atis.yaml"
      env:
        # met.no requires an identifying User-Agent; pass one via config file or env.
        METNO_USER_AGENT: "Asterisk-AI-Voice-Agent (+https://github.com/...)"
        # Optional override (defaults to 300)
        # METNO_CACHE_TTL_SECONDS: "300"
      restart:
        enabled: true
        max_restarts: 5
        backoff_ms: 1000
      defaults:
        timeout_ms: 10000
        slow_response_threshold_ms: 2000
        slow_response_message: "Let me look that up for you, one moment..."
      tools:
        - name: get_atis
          expose_as: mcp_aviation_atis_get_atis
          description: "Get current ATIS for an airport"
          speech_field: atis_text
          # speech_template: "The current ATIS for {icao} is: {atis_text}"
```

### Aviation ATIS server config (per-aerodrome)

The deterministic ATIS server uses `met.no` (tafmetar feed) for METAR fetch and supports a 5-minute cache + background refresh to reduce caller-visible lag.

- Example config: `config/aviation_atis.example.yaml`
- Copy to: `config/aviation_atis.yaml` (or any path) and pass via `--config`
- Optional: `defaults.explicit_not_available: true` to speak explicit “not available” lines when runway/frequency/advisories are not configured.
- The ATIS MCP server re-reads its `--config` file when it changes (no engine restart required for those per-aerodrome tweaks).

### Context scoping

Use existing context tool lists to expose MCP tools only where needed:

```yaml
contexts:
  aviation_support:
    provider: openai_realtime
    tools:
      - transfer
      - hangup_call
      - mcp_aviation_atis_get_atis
```

Rule:

- `contexts.<name>.tools` must list the **exposed tool names** (the provider-safe names).

## Template Variables for Prompts

Context prompts and greetings support template variable substitution for call-specific data. This is essential for MCP tools that need caller information (e.g., customer lookup by phone number).

### Available Variables

| Variable | Description | Default | Source |
|----------|-------------|---------|--------|
| `{caller_name}` | Caller ID name | `"there"` | `CALLERID(name)` from Asterisk |
| `{caller_number}` | Caller phone number (ANI) | `"unknown"` | `CALLERID(num)` from Asterisk |
| `{call_id}` | Unique call identifier | (always set) | Internal call ID |
| `{context_name}` | AI_CONTEXT from dialplan | `""` | `Set(AI_CONTEXT=...)` |
| `{call_direction}` | Call direction | `"inbound"` | `"inbound"` or `"outbound"` |
| `{campaign_id}` | Outbound campaign ID | `""` | Outbound dialer only |
| `{lead_id}` | Outbound lead/contact ID | `""` | Outbound dialer only |

### Example: Auto-Lookup Customer by Caller ID

```yaml
contexts:
  customer_support:
    provider: openai_realtime
    greeting: "Hi {caller_name}, thanks for calling support!"
    prompt: |
      You are a customer support agent.
      
      ## Caller Information
      - Phone: {caller_number}
      - Name: {caller_name}
      - Call ID: {call_id}
      
      ## Instructions
      1. Use the lookup_customer tool with phone number {caller_number} to find their account
      2. If no account found, ask for their account number or service address
      3. Be helpful and professional
    tools:
      - mcp_uisp_server_lookup_customer
      - transfer
      - hangup_call
```

### How It Works

1. When a call arrives, Asterisk passes `CALLERID(name)` and `CALLERID(num)` to the AI engine
2. The engine substitutes `{caller_number}` and other variables in your prompt **before** sending to the LLM
3. The LLM sees the actual phone number and can pass it to your MCP tool
4. Your MCP tool receives the real data, not template placeholders

### For MCP Tool Builders

Your MCP tool will receive **actual substituted values**, not template placeholders:

```python
# ✅ Correct - tool receives actual data:
{'phone': '15551234567'}

# ❌ Wrong - means substitution didn't happen (check prompt syntax):
{'phone': '{caller_number}'}
```

### Safe Fallback Behavior

- **Unknown placeholders** are left unchanged (e.g., `{unknown_var}` stays as-is)
- **Missing data** uses defaults (e.g., no caller name → `"there"`)
- **Invalid syntax** doesn't break prompts (graceful fallback to original text)

### Defaults Configuration

Default values are defined in `src/engine.py` method `_apply_prompt_template_substitution()`. To customize defaults, modify this method or use conditional logic in your prompts.

## Execution & Security Model

### Allowlisting (required)

Two layers:

1. **Schema filtering**: only publish tool schemas listed in `contexts.<name>.tools`.
2. **Execution allowlist**: the engine must reject tool calls not in the call’s allowed tool list, even if the provider attempts them.

This is important because the current engine execution path can execute any registered tool if called by name.

### Secrets

- Use environment expansion in YAML (`${VAR}`) and pass secrets to stdio servers through environment variables.
- Do not log env var values.
- Prefer running stdio MCP servers inside the same container only when required; otherwise connect to an external MCP server via a controlled interface.

## MCP Server Lifecycle

For stdio servers:

- Start on engine startup (or lazily on first use).
- Capture stdout/stderr to logs (rate-limited).
- Restart on crash with backoff, with a max restart limit.
- Stop gracefully on engine shutdown.

For hot reload:

- `/reload` can reload MCP servers/tools when there are **no active calls** (it restarts MCP stdio subprocesses and re-registers MCP tools).
- If there are active calls, MCP reload is deferred (plan a restart or reload during an idle window).

## Observability

Recommended metrics (Prometheus):

- `ai_agent_mcp_tool_calls_total{server,tool,status}`
- `ai_agent_mcp_tool_latency_seconds{server,tool}`
- `ai_agent_mcp_server_restarts_total{server}`
- `ai_agent_mcp_server_up{server}` (gauge)

Also add structured logs that include:

- `call_id`, `context_name`, `provider_name`
- `server`, `tool`, `exposed_name`
- timing + timeout vs success

## Testing on a Development Server

### Suggested approach

1. Configure a single MCP server with a single simple tool (fast, deterministic).
2. Enable it only in a dedicated context (`AI_CONTEXT=...`) so production contexts remain unaffected.
3. Validate:
   - Tool schema appears only in that context
   - Tool calls outside the context are rejected
   - Tool result is spoken via `message`
4. Add a slow tool variant to validate timeout and (pipeline-only initially) slow-response announcements.

### Admin UI support

- Admin UI exposes an MCP page for editing YAML `mcp:` config and testing servers.
- Server “Test” and discovery run in the **ai-engine container context** via:
  - `GET /mcp/status` (ai-engine health server)
  - `POST /mcp/test/{server_id}`

### Minimal smoke config

- Add a dedicated context (e.g. `demo_mcp`) and list only one MCP tool plus `hangup_call`.
- Route a dev extension to `Set(AI_CONTEXT=demo_mcp)`.

## Implementation Checklist (What the Branch Will Change)

- `src/tools/`:
  - Add MCP tool wrapper(s) that implement `Tool.execute()` by calling MCP.
  - Support `speech_field` / `speech_template` and consistent return shape (`status`, `message`, `data`).
- `src/tools/registry.py`:
  - Add a way to register dynamically discovered/configured tools (MCP) at startup.
  - Add a way to generate *filtered* schemas per call/tool list.
- Provider adapters:
  - Update OpenAI Realtime and Deepgram schema generation to accept a filtered tool list (like Google Live already does).
- `src/engine.py`:
  - Enforce per-call allowlisting in `_execute_provider_tool()`.
- Config:
  - Extend `src/config.py` to model `mcp:` config (and validate naming).
- Docs:
  - Update `docs/TOOL_CALLING_GUIDE.md` and `docs/README.md` to reference MCP integration.
