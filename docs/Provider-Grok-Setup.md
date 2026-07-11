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

## Multi-instance deployments

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

Default input: **μ-law @ 8 kHz passthrough.** xAI accepts `audio/pcmu`
natively, so caller audio can pass from Asterisk without input resampling.

Default output: **PCM16 @ 24 kHz from xAI, converted to μ-law @ 8 kHz for
Asterisk.** Live sessions have shown that xAI emits 24 kHz PCM16 even when a
different per-session output format is requested. AAVA declares that observed
provider rate explicitly, then downsamples and encodes it at the transport
boundary. Do not configure Grok output as 8 kHz μ-law unless a captured
session trace proves that your xAI endpoint actually emits it.

For wideband caller input (when AudioSocket runs in `slin16` mode), switch the
provider input encoding while retaining the observed 24 kHz output:

```yaml
grok:
  provider_input_encoding: "linear16"
  provider_input_sample_rate_hz: 24000
  output_encoding: "linear16"          # xAI output observed by AAVA
  output_sample_rate_hz: 24000
  target_encoding: "ulaw"              # Asterisk transport output
  target_sample_rate_hz: 8000
```

The provider resamples input only when provider and transport rates differ; the
normal telephony output conversion remains 24 kHz PCM16 → 8 kHz μ-law.

## Barge-in and platform announcements

- xAI server VAD is the primary turn detector. `turn_detection` controls the
  threshold, prefix padding, and end-of-turn silence.
- ExternalMedia also enables AVA's local fallback so caller speech can flush
  caller-facing and provider-side buffers before the xAI interruption event.
  Cancelled deltas are discarded until the replacement response.
- Named instances such as `grok3` inherit the canonical `grok` voice, audio, and
  turn-detection settings, then apply their own credentials and metadata.
- Caller-inactivity check-ins use xAI's interruptible `force_message` item. It
  speaks the configured text verbatim and supplies a normal response lifecycle;
  AVA waits for generated audio to drain before starting grace or hangup.

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

- **Long sessions.** xAI's current [Voice Agent model page](https://docs.x.ai/developers/models/voice-agent-api) lists a 120-minute maximum session. AAVA retains a conservative warning threshold of 1680 seconds (28 minutes) for compatibility with older xAI limits and deployments; it is a warning, not the current documented hard cap. Set `session_warn_after_seconds: 0` to disable it. Provider/socket closure still uses normal call teardown.
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
- **No audio coming back**: confirm caller input is `audio/pcmu` at 8 kHz and provider output is declared as `linear16` at 24 kHz with an 8 kHz μ-law transport target. If rates disagree, inspect the call's `RCA_CALL_START` and Grok session-assumption logs before changing YAML.
- **Session drops near 30 min**: this is no longer the current documented xAI limit. Archive the call and check provider close/error events, account policy, and whether an older endpoint or organization limit applies. The 28-minute AAVA warning is intentionally conservative.
