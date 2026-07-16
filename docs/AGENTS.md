# Agents

An **agent** is the v1a evolution of a "context": a named configuration bundle that defines what an AI caller hears, how it sounds, and what it can do. Each agent packages:

- **Provider** — which AI backend handles the call (e.g. `deepgram`, `openai_realtime`, `local_hybrid`)
- **Prompt** — the system-level instructions and persona
- **Greeting** — the first thing the agent says when it picks up
- **Connection audio** — optional caller-only ringback/comfort media while the provider or pipeline initializes
- **Voice** — per-agent voice override (v7.3.0+): pick a voice for this agent, or leave empty to use the provider's default voice. Multiple agents can share one provider, each with its own voice. See [Voice Selection](VOICE_SELECTION.md)
- **Audio profile** — telephony format / sample-rate profile (e.g. `telephony_ulaw_8k`)
- **Tools** — optional callable tools plus a per-agent transfer-destination policy. Global configuration owns inventory and hard disables; an agent can only narrow it.

Agents are managed in the Admin UI **Agents** tab and stored in `agents.db`. v7.4 removes Contexts from navigation and runtime routing. Visiting the old `/contexts` URL shows a one-time migration notice and then opens Agents.

---

## Source of truth: agents.db

| Path | Location |
|------|----------|
| Inside container | `/app/data/operator/agents.db` |
| Host (relative to repo root) | `./data/operator/agents.db` |

`agents.db` is a WAL-mode SQLite database. The Admin UI owns the write path (CRUD, migration, reconcile). The engine reads it per-call at the moment a call enters Stasis — no restart needed after editing an agent.

On v7.4 startup, a headless-safe compatibility bridge atomically imports legacy YAML Contexts into `agents.db` when the store is empty. A populated Agent store is never overwritten. After startup, runtime routing reads Agents only and fails closed if the database is absent or unreadable.

---

## Selecting an agent from the Asterisk dialplan

### AI_AGENT (preferred)

```asterisk
[from-ai-agent-sales]
exten => s,1,NoOp(AI Agent - Sales)
 same => n,Set(AI_AGENT=sales)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

Set `AI_AGENT` to the agent's **slug** (the identifier shown on each agent card in the Admin UI). The engine resolves the full configuration from `agents.db` and uses it for the call.

### AI_CONTEXT (deprecated compatibility alias)

`AI_CONTEXT` continues to resolve migrated Agents display-name-first in v7.4, so existing dialplans keep working while operators move to `AI_AGENT`. The engine emits one deprecation warning per process. New dialplans must use `AI_AGENT`.

```asterisk
 same => n,Set(AI_CONTEXT=sales)   ; legacy form, still accepted
```

### Priority when both are set

If both `AI_AGENT` and `AI_CONTEXT` are present on the same channel, `AI_AGENT` wins.

### Combining with AI_PROVIDER

`AI_AGENT` and `AI_PROVIDER` are independent. You can set both:

```asterisk
 same => n,Set(AI_PROVIDER=deepgram)
 same => n,Set(AI_AGENT=sales)
 same => n,Stasis(asterisk-ai-voice-agent)
```

`AI_PROVIDER` overrides which provider/pipeline handles the call; `AI_AGENT` selects the greeting, prompt, tools, and (v7.3.0+) the agent's voice. If `AI_PROVIDER` is not set, the engine uses the provider field stored on the agent itself (or `default_provider` from `ai-agent.yaml` as the final fallback).

> **Note:** Some docs in this repository may still show `AI_CONTEXT` in dialplan examples — that is the legacy, still-supported variable and works identically to `AI_AGENT`.

---

## Fresh-install starter set

Completing the setup wizard on a genuinely empty installation creates exactly three general-purpose Agents: **Receptionist** (default), **Sales**, and **Support**. They include conservative prompts and `hangup_call`, but no invented transfer routes. Existing installations and populated Agent stores are unchanged.

## Add-Agent templates

When you click **Add Agent** in the Admin UI, you can pick one of five starter templates. Each pre-fills the prompt and greeting:

| Template | Slug suffix | Use case |
|----------|-------------|----------|
| Receptionist | `receptionist` | General inbound reception, transfers, and FAQs |
| After Hours | `after_hours` | Closed-office message, callback capture |
| Appointment Booker | `appointment_booker` | Schedule / confirm / cancel appointments |
| Order Status | `order_status` | Look up and relay order or shipment status |
| Support Triage | `support_triage` | Classify issues and route or log tickets |

Templates are a starting point; edit the prompt and greeting to match your use case before saving.

---

## Per-agent tool access and reloads

The Tools page remains the global inventory. Under **Agents → Edit Agent → Tools → blind_transfer**, choose one transfer scope:

- **Inherit** — use every globally configured destination (the backward-compatible default).
- **Selected** — expose only checked destination keys. Empty or stale selections fail closed.
- **None** — expose no transfer destinations.

The same effective snapshot governs provider schemas, prompt guidance, execution, deferred transfer, and audit metadata. The transfer scope is shared by blind/attended/live-agent transfer and extension-status checks. A global disabled tool always wins.

**Tools → Save & Apply** builds an isolated generation for built-ins and managed HTTP tools. New calls capture the new generation; active calls keep their previous registry and configuration until they end. Build/validation failure leaves the previous generation running. Python code, environment/credential changes, provider/VAD changes, and MCP process configuration still require an engine restart.

---

## Headless compatibility import

An engine-only installation may still upgrade from legacy `ai-agent.yaml` or `config/contexts/*.yaml`. Before accepting calls, the engine validates all merged Contexts and creates `agents.db` through a same-directory temporary database, integrity check, file lock, and atomic replace. Invalid legacy data blocks startup rather than partially importing. Once imported, edit Agents through the API/Admin UI or provision `agents.db`; YAML is no longer a live runtime persona source.

---

## Agent stats

Each agent card in the Admin UI shows:

- **Calls (30d)** — call count over the rolling 30-day window
- **Last call** — timestamp of the most recent call

Both figures come from `call_records.context_name` in `call_history.db` joined on the agent slug. Because the engine records the **resolved** slug into `context_name` regardless of whether `AI_AGENT` or `AI_CONTEXT` was used to trigger the call, stats join correctly for both variable names.

---

## Related

- [OPERATOR_MIGRATION.md](OPERATOR_MIGRATION.md) — one-time YAML→agents.db migration, drift warnings, reconcile/acknowledge, and rollback.
- [Configuration-Reference.md](Configuration-Reference.md) — legacy YAML schema and current global configuration.
- [FreePBX-Integration-Guide.md](FreePBX-Integration-Guide.md) — dialplan setup and channel variable reference.
