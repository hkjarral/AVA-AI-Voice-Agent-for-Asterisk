# Multi-Instance Full-Agent Providers

Use this when one deployment serves multiple customers and each customer needs isolated provider credentials, for example separate Google Vertex service account JSON files or separate OpenAI, Deepgram, or ElevenLabs API keys.

Full-agent provider instance keys are stable routing identities. The YAML `type` selects the implementation:

```yaml
providers:
  acme_google_live:
    enabled: true
    type: google_live
    display_name: "Acme Google Live"
    customer: "Acme"
    use_vertex_ai: true
    vertex_project: "acme-gcp-project"
    vertex_location: "us-central1"
    credentials_path: "/app/project/secrets/providers/acme_google_live/vertex-service-account.json"

  globex_google_live:
    enabled: true
    type: google_live
    display_name: "Globex Google Live"
    customer: "Globex"
    api_key_file: "/app/project/secrets/providers/globex_google_live/api-key"
```

Supported full-agent `type` values are `openai_realtime`, `deepgram`, `google_live`, `elevenlabs_agent`, and `local`. `local` is limited to one instance.

## Admin UI Flow

1. Open **Providers**.
2. Click **Add Provider**.
3. Enter a stable provider key, such as `acme_google_live`.
4. Pick the full-agent provider type.
5. Add an optional display name and customer label.
6. Upload credentials from the provider form or credential controls.
7. Save and restart the AI Engine when prompted.

Provider keys are immutable after creation. To change a key, create a new provider instance and update contexts or dialplan references.

## Credential Files

Admin-managed credential files are written under:

```text
/app/project/secrets/providers/<provider_key>/
```

Credential fields:

- `api_key_file`: OpenAI Realtime, Deepgram, Google Developer API mode, ElevenLabs.
- `agent_id_file`: ElevenLabs full-agent instances.
- `credentials_path`: Google Live Vertex service account JSON.

The new per-provider Google Vertex upload does not edit `.env`. The legacy global Vertex endpoint is still available for older single-provider setups and still updates `GOOGLE_APPLICATION_CREDENTIALS` in `.env`.

## Routing Calls

Set Asterisk channel variables before `Stasis(asterisk-ai-voice-agent)`.

Precedence is:

1. `AI_PROVIDER`
2. `contexts.<name>.provider`
3. `default_provider`

Short aliases are removed. Use exact provider instance keys or exact pipeline names.

### Direct Provider Pinning

```asterisk
[from-acme-inbound]
exten => s,1,NoOp(ACME inbound -> Google Live)
 same => n,Answer()
 same => n,Set(AI_PROVIDER=acme_google_live)
 same => n,Set(AI_CONTEXT=acme_support)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

Use this when the dialplan should decide the customer/provider directly.

### Context-Based Routing

```yaml
contexts:
  acme_support:
    provider: acme_google_live
  globex_sales:
    provider: globex_openai_realtime
```

```asterisk
exten => s,1,Answer()
 same => n,Set(AI_CONTEXT=acme_support)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

This is preferred for stable customer routing because changing a customer to a different provider is a config change, not a dialplan change.

### DID-Based Dispatch

Use `Gosub` rather than `Macro` for new dialplans:

```asterisk
[ava-route]
exten => s,1,NoOp(Routing DID=${ARG1})
 same => n,Set(AI_CONTEXT=default)
 same => n,ExecIf($["${ARG1}" = "18002221111"]?Set(AI_CONTEXT=acme_support))
 same => n,ExecIf($["${ARG1}" = "18002223333"]?Set(AI_CONTEXT=globex_sales))
 same => n,Return()

[from-pstn]
exten => _X.,1,Answer()
 same => n,Set(DIALED_NUMBER=${EXTEN})
 same => n,Gosub(ava-route,s,1(${EXTEN}))
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

In some FreePBX custom-destination paths `${EXTEN}` may be `s` rather than the inbound DID. In that case, use one custom destination per DID/customer, or pass/set the DID explicitly before `Stasis()`.

## Common Pitfalls

- `AI_PROVIDER=openai`, `AI_PROVIDER=google`, and `provider: deepgram_agent` no longer work. Use exact keys such as `openai_realtime`, `google_live`, `deepgram`, or your custom instance key.
- Provider instance keys cannot collide with pipeline names.
- Full-agent instances cannot be used in modular pipeline `stt`, `llm`, or `tts` slots.
- Deleting a provider is blocked while `default_provider`, a context, or an active call references it.
- Inline `api_key` and `agent_id` values are still read for legacy configs, but the Admin UI migrates raw inline secrets to provider-scoped files on save.
