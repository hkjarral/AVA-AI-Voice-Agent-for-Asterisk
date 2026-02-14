# Telnyx Modular LLM Provider (`telnyx_llm`) — Implementation Plan

Date: 2026-02-13  
Status: Planning → Implementing (target: validate on `staging`, then ship to `main` via PR)

## Credits / Attribution

This work builds on the original Telnyx AI Inference contribution in PR #219 (“Add Telnyx AI Inference as LLM provider option”).

- Contributor: **Abhishek** (`abhishek@telnyx.com`)
- Original scope: docs + golden baseline + `.env.example` update

When shipping:
- Include a “Credits” section in the `staging` PR description and in the promotion PR to `main`.
- Add a short `CHANGELOG.md` entry crediting Abhishek.

## Goal

Add a first-class **modular pipeline LLM provider** for Telnyx AI Inference:

- Component key: `telnyx_llm` (optional alias `telenyx_llm` for typo tolerance)
- Usable in `pipelines:` as the `llm:` component
- Supports **all model IDs** allowed by the customer’s Telnyx API key (no `gpt-*` filtering)
- Uses **env-only secrets**: `TELNYX_API_KEY` (never committed to YAML)

Non-goals:
- No SIP trunking changes (Asterisk/FreePBX remains responsible)
- No Telnyx “realtime agent” mode unless Telnyx provides a compatible realtime API

## Why a dedicated provider (vs `openai_llm` + `base_url`)

Pros:
- Clean secret wiring: `TELNYX_API_KEY` maps to `telnyx_llm` (no `OPENAI_API_KEY` confusion)
- No OpenAI-specific “model compatibility” heuristics blocking Claude/Llama/Mistral IDs
- Smaller regression surface (avoid loosening OpenAI logic for everyone)

Cons:
- Some duplication of OpenAI-compatible chat-completions logic
- Requires keeping parity with Telnyx’s OpenAI-compatible API surface over time

## Configuration Shape

`.env`:
```bash
TELNYX_API_KEY=...
```

`config/ai-agent.yaml`:
```yaml
providers:
  telnyx_llm:
    enabled: true
    chat_base_url: "https://api.telnyx.com/v2/ai"
    # api_key is injected from TELNYX_API_KEY (env-only)

pipelines:
  telnyx_hybrid:
    stt: local_stt
    llm: telnyx_llm
    tts: local_tts
    options:
      llm:
        model: "claude-3-5-sonnet"  # or gpt-4o-mini, llama-*, mistral-*, etc.
        temperature: 0.7
        max_tokens: 150
```

Dialplan selection (force per-extension pipeline):
```ini
Set(AI_PROVIDER=telnyx_hybrid)
Set(AI_CONTEXT=demo_telnyx)
```

## Implementation Plan (Engine)

### 1) Secret injection (env-only)

- File: `src/config/security.py`
- Inject `TELNYX_API_KEY` into provider blocks named `telnyx*` (and optional `telenyx*`) or pointing at host `api.telnyx.com`.

Regression risk:
- Medium if injection is too broad and overwrites other providers.
Mitigation:
- Match on provider-name prefix or Telnyx host only.

### 2) Telnyx adapter

- File: `src/pipelines/telnyx.py`
- Implement `TelnyxLLMAdapter` using OpenAI-compatible `POST {chat_base_url}/chat/completions`.
- Support tool calling schemas (same OpenAI tool schema) + one retry without tools on tool-call failure.
- Do **not** restrict model IDs.

### 3) Register factories in `PipelineOrchestrator`

- File: `src/pipelines/orchestrator.py`
- Hydrate `providers.telnyx_llm` into a config object (`TelnyxLLMProviderConfig`) so we can support Telnyx-specific fields like `api_key_ref`.
- Register factories:
  - `telnyx_llm`
  - optional alias `telenyx_llm`

### 4) Docs + baselines

- Update `docs/Provider-Telnyx-Setup.md` to use `telnyx_llm` and correct pipeline selection (`AI_PROVIDER=telnyx_hybrid`).
- Update `config/ai-agent.golden-telnyx.yaml` to use `llm: telnyx_llm` and `TELNYX_API_KEY`.
- Update indexes:
  - `docs/README.md`
  - `docs/Configuration-Reference.md`

## Admin UI Considerations

Minimum UI (recommended for shipping):
- Add `TELNYX_API_KEY` to env drift/restart classification.
  - File: `admin_ui/backend/api/config.py`
- Render `TELNYX_API_KEY` as a secret field in Env UI.
  - File: `admin_ui/frontend/src/pages/System/EnvPage.tsx`

Nice-to-have:
- Add “Test connection” inference for Telnyx in `/providers/test` OpenAI-compatible path.
- Add Provider tile + wizard wiring.

## Test Plan

### Automated

- Add config-load integration test for `config/ai-agent.golden-telnyx.yaml` with dummy `TELNYX_API_KEY`.
- Run `pytest -q`.

### Staging manual validation (call tests)

Model matrix (at least one from each family):
- `gpt-4o-mini`
- `claude-3-5-sonnet`
- `llama-*`
- `mistral-*`

Scenarios:
- tool calling enabled (`tools: [hangup_call]`) and disabled
- invalid model ID (ensure we get clear error logs)

Observability:
- Ensure logs never print API keys or full request payloads.

## Rollout Plan (staging → main)

1) Merge PR into `staging` (with contributor credit).
2) Deploy `staging` to the dev/staging server via git pull + service restart.
3) Run manual call tests.
4) Promote to `main` via PR (no direct merges) and include the same credit.
