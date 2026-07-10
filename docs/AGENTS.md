# Agents

An **agent** is the v1a evolution of a "context": a named configuration bundle that defines what an AI caller hears, how it sounds, and what it can do. Each agent packages:

- **Provider** — which AI backend handles the call (e.g. `deepgram`, `openai_realtime`, `local_hybrid`)
- **Prompt** — the system-level instructions and persona
- **Greeting** — the first thing the agent says when it picks up
- **Voice** — per-agent voice override (v7.3.0+): pick a voice for this agent, or leave empty to use the provider's default voice. Multiple agents can share one provider, each with its own voice. See [Voice Selection](VOICE_SELECTION.md)
- **Audio profile** — telephony format / sample-rate profile (e.g. `telephony_ulaw_8k`)
- **Tools** — optional callable tools (calendar, HTTP, MCP, etc.)

Agents are managed in the Admin UI **Agents** tab and stored in `agents.db`. The legacy **Contexts** tab is now read-only; use the Agents tab for all create/edit/delete operations.

---

## Source of truth: agents.db

| Path | Location |
|------|----------|
| Inside container | `/app/data/operator/agents.db` |
| Host (relative to repo root) | `./data/operator/agents.db` |

`agents.db` is a WAL-mode SQLite database. The Admin UI owns the write path (CRUD, migration, reconcile). The engine reads it per-call at the moment a call enters Stasis — no restart needed after editing an agent.

If `agents.db` is absent (headless or YAML-only installs), the engine falls back to reading `ai-agent.yaml` + `config/contexts/*.yaml` and all existing behavior is preserved unchanged. See [Headless / YAML-only mode](#headless--yaml-only-mode) below.

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

### AI_CONTEXT (legacy — still supported)

`AI_CONTEXT` continues to work and is fully equivalent to `AI_AGENT`. Existing dialplans do not need to be updated.

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

## Starter templates

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

## Headless / YAML-only mode

Installs that run only `ai_engine` (no Admin UI) never write `agents.db` and never need to. The engine's `EngineAgentStore.available()` returns `False` when the database file is absent, and the call path falls back to the existing YAML context resolution (`ai-agent.yaml` + `config/contexts/*.yaml`) exactly as it always has.

This is a supported, permanent operating mode — not a deprecated fallback. Small or privacy-sensitive deployments that prefer flat-file config can keep using YAML indefinitely.

See [Configuration-Reference.md](Configuration-Reference.md) for the YAML context schema.

---

## Agent stats

Each agent card in the Admin UI shows:

- **Calls (30d)** — call count over the rolling 30-day window
- **Last call** — timestamp of the most recent call

Both figures come from `call_records.context_name` in `call_history.db` joined on the agent slug. Because the engine records the **resolved** slug into `context_name` regardless of whether `AI_AGENT` or `AI_CONTEXT` was used to trigger the call, stats join correctly for both variable names.

---

## Admin UI — Contexts tab

The **Contexts** tab is now **read-only**. It remains available for reference (viewing YAML-sourced context names and their current values), but all create, edit, clone, and delete actions have been removed. Use the **Agents** tab to manage agents going forward.

---

## Related

- [OPERATOR_MIGRATION.md](OPERATOR_MIGRATION.md) — one-time YAML→agents.db migration, drift warnings, reconcile/acknowledge, and rollback.
- [Configuration-Reference.md](Configuration-Reference.md) — full YAML context schema (headless / YAML-only path).
- [FreePBX-Integration-Guide.md](FreePBX-Integration-Guide.md) — dialplan setup and channel variable reference.
