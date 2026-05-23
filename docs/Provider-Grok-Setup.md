# xAI Grok Voice Agent — Provider Setup

AAVA ships a full-agent realtime provider for xAI's [Grok Voice Agent API](https://docs.x.ai/developers/model-capabilities/audio/voice-agent). The provider is structurally parallel to OpenAI Realtime and Google Live: it owns the full conversation loop (server-side VAD, barge-in, tool calls) and exposes the same engine surface as any other full-agent provider.

## Quick start (single-instance)

1. Get an API key from xAI ([console](https://x.ai/api)).
2. Set it in your environment:
   ```bash
   export XAI_API_KEY=xai-...
   ```
3. Add a `grok` block to `config/ai-agent.yaml` (see [config/ai-agent.example.yaml](../config/ai-agent.example.yaml) for the full template):
   ```yaml
   providers:
     grok:
       enabled: true
       api_key: "${XAI_API_KEY}"
       model: "grok-voice-latest"
       voice: "eve"
   default_provider: grok
   ```
4. Restart the AI engine and place a test call.

## Multi-tenant deployments

Grok is a registered full-agent kind in the multi-instance system. To run isolated instances per customer, give each instance a stable key and a separate credential file:

```yaml
providers:
  acme_grok:
    enabled: true
    type: grok
    display_name: "Acme Grok"
    customer: "Acme"
    api_key_file: "/app/project/secrets/providers/acme_grok/api-key"
    voice: "eve"

  globex_grok:
    enabled: true
    type: grok
    display_name: "Globex Grok"
    customer: "Globex"
    api_key_file: "/app/project/secrets/providers/globex_grok/api-key"
    voice: "rex"
```

Route calls via the channel variable `AI_PROVIDER=acme_grok` or set `contexts.<name>.provider: acme_grok`. See [Multi-Instance-Full-Agent-Providers.md](Multi-Instance-Full-Agent-Providers.md) for routing details.

## Supported voices

| Name | Notes |
|---|---|
| `eve` | Energetic, upbeat (default) |
| `ara` | Warm, friendly |
| `rex` | Confident, clear |
| `sal` | Smooth, balanced |
| `leo` | Authoritative, strong |
| `<voice-id>` | Custom cloned voice from your xAI workspace |

Set `voice:` to either a named voice or a custom voice ID. The admin UI exposes both modes.

## Audio path

Default: **μ-law @ 8 kHz both directions, no resampling.** xAI accepts `audio/pcmu` natively, and Asterisk's native telephony format is μ-law @ 8 kHz, so frames pass straight through. This saves ~10–15 ms latency and CPU per concurrent call.

For wideband audio (when AudioSocket runs in `slin16` mode), switch the encoding in YAML:

```yaml
grok:
  provider_input_encoding: "linear16"
  provider_input_sample_rate_hz: 24000
  output_encoding: "linear16"
  output_sample_rate_hz: 24000
```

The provider then resamples 8 kHz ↔ 24 kHz at the AudioSocket boundary. Audio quality improves, at the cost of ~10–15 ms latency.

## Tool calling

The custom function-tool schema is identical to OpenAI Realtime: `{"type": "function", "name", "description", "parameters"}`. All tools registered in `tool_registry` work out of the box.

xAI ships four native tools (`web_search`, `x_search`, `file_search`, `mcp`) that are not exposed in the admin UI. Advanced users can enable them via YAML:

```yaml
grok:
  extra_tools:
    - {type: "web_search"}
    - {type: "x_search", allowed_x_handles: ["xai"]}
    - {type: "file_search", vector_store_ids: ["..."], max_num_results: 10}
    - {type: "mcp", server_url: "https://...", server_label: "...", allowed_tools: ["..."]}
```

Entries in `extra_tools` are forwarded verbatim into the `session.update.tools` array.

## Known limits

- **30-minute hard session cap.** xAI closes the WebSocket at the 30-min mark per the docs. AAVA logs a structured warning at the threshold set by `session_warn_after_seconds` (default 1680 sec = 28 min) so operators can correlate any user-facing call drops with this documented limit. Set to `0` to disable the warning. There is no automatic reconnect in v1; existing call-teardown handles the close cleanly.
- **100 concurrent sessions per team** (xAI account-level limit, not enforced by AAVA).
- **Voice cloning workflow** is not in the admin UI; clone voices in your xAI workspace and paste the resulting voice ID into the `voice` field.

## Verification

After enabling, place a test call and verify the logs show, in order:

```text
Provider loaded successfully ... kind=grok
Connecting to Grok Voice Agent
✅ Received session.created
Grok session.update payload ... input_format=audio/pcmu ... voice=eve
response.output_audio.delta  (audio streaming to caller)
```

> **Note on `session.updated` ACK:** xAI does NOT consistently send a
> `session.updated` ACK in response to `session.update`. AAVA waits up to ~2 seconds
> and proceeds either way — you may see `✅ Grok session.updated ACK received` if
> xAI sends one, or `⚠️ Grok session.updated ACK timeout - proceeding anyway` if
> it doesn't. Both are healthy; the call still works. Don't diagnose the timeout
> log as a failure.

If a tool fires:

```text
Grok tool call received ... tool=<your_tool>
✅ Sent function output to Grok: ok
✅ Triggered Grok response generation (audio+text)
```

## Pricing

xAI's published rate is $3/hr per session (`$0.05/min`). Materially cheaper than OpenAI Realtime. See [xAI pricing](https://docs.x.ai/developers/pricing) for current rates.

## Troubleshooting

- **"Grok Voice Agent provider requires XAI_API_KEY"**: the engine couldn't resolve a credential. Verify `api_key`, `api_key_file`, or the `XAI_API_KEY` env var (legacy single-instance fallback).
- **No audio coming back**: confirm `audio/pcmu` matches your AudioSocket format. If AudioSocket runs in `slin16` mode, the engine logs a `describe_alignment()` warning at startup; switch the YAML defaults per the "Audio path" section above.
- **Session drops near 30 min**: expected per xAI docs. Watch for the 28-min warning log line to confirm.
