# Voice Selection

As of v7.3.0, **voice belongs to agents**. Configure a provider once, then create multiple
agents that share it — each with its own voice. The provider-level voice setting remains as
the **default/fallback** for agents that don't choose one.

## Precedence

For every call, the engine resolves the session voice in this order:

```text
1. Per-call override        (rare; set programmatically via provider overrides)
2. Agent voice              (Agents page → Voice field; stored in agents.db)
   — or the optional `voice:` key on a YAML context (headless installs)
3. Provider default         (the provider's own voice setting — same behavior as pre-7.3.0)
```

The decision is logged once per call:
`Session voice resolved  voice=<name>  source=override|agent|provider-default` — and recorded
in Call History (detail view shows e.g. **"Voice: marin (from agent)"**), so you can always
verify which voice a call actually used.

Agent voice changes apply **immediately** — agents.db is read at call time, no engine restart.

## Per-provider behavior

| Provider | Agent voice? | Voice values | Validation |
|---|---|---|---|
| **OpenAI Realtime** | ✅ Dropdown | 10 GA voices (alloy, ash, ballad, cedar, coral, echo, marin, sage, shimmer, verse) | Closed list. An unrecognized value (e.g. stale text from the pre-7.3.0 display-only field) logs a warning and **falls back to the provider default — the call never fails** |
| **xAI Grok** | ✅ Suggestions + free text | eve, ara, rex, sal, leo — or a custom cloned `voice_id` | Pass-through (clone IDs are valid, so no local validation) |
| **Google Live** | ✅ Dropdown | 30 prebuilt voices (Aoede, Kore, Charon, Puck, …) | Validated against the prebuilt catalog (case-insensitive) — unknown values warn and **fall back to the configured voice**, never failing the call |
| **Deepgram Voice Agent** | ✅ Dropdown | Aura models (`aura-2-thalia-en`, …, legacy `aura-*-en`) | Validated against the Aura catalog — unknown values warn and fall back; applies to both the primary session and Deepgram's retry path |
| **ElevenLabs Agent** | ❌ Platform-managed | Voice is baked into the agent on the ElevenLabs platform | An agent voice set in AVA is ignored with an explanatory log |
| **Local full-agent / pipelines** | ❌ (v7.3.0) | Voice comes from the provider / pipeline TTS configuration | Per-agent pipeline TTS voice is planned for a later release |

## Configuring

- **Agents page → edit agent → Voice.** The control adapts to the selected AI Engine:
  a dropdown for OpenAI/Google Live/Deepgram (validated catalogs), suggestions-plus-free-entry
  for Grok (custom clone IDs allowed), and a disabled field with an explanation for
  ElevenLabs and pipelines. "— provider default —" (or empty) means "use the provider's
  voice", exactly like before 7.3.0.
- **Provider pages** now label their voice fields **Default Voice** — that value is used by
  every agent that doesn't set its own.
- **YAML contexts** (headless installs) accept an optional `voice:` key with the same
  semantics as the agent field.

## Upgrading from ≤7.2.x

Nothing to do. Existing agents have no voice set, so every call keeps using the provider
default. The old free-text "display-only" voice field becomes live: if you ever typed a
**valid** voice into it, that agent starts using it (the feature working as intended); junk
values are flagged in the Agents form ("unrecognized — will fall back") and never break calls.

## Troubleshooting

- **"The voice didn't change"** — check Call History detail for the call: the Voice row shows
  what was used and why. `provider default` + a warning in engine logs
  (`Agent voice not in OpenAI GA catalog`) means the agent's stored value isn't valid.
- **ElevenLabs** — change the voice in the ElevenLabs agent configuration (platform side).
- **Pipelines** — set the voice on the pipeline's TTS provider (`tts_model`, `voice_id`, etc.).
