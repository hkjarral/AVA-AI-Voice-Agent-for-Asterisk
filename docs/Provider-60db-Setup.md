# 60db TTS Setup

60db is a modular TTS adapter. Pair it with an existing STT and LLM in a
pipeline; it is not a full-agent routing target.

The first-run wizard selects full agents; configure 60db under Providers after setup.

## Credentials and voice

Set `SIXTYDB_API_KEY` in `.env`, or save a provider-scoped API key under
**Providers → Add → Modular → TTS → 60db**. The existing credential uploader
stores keys in owner-only files. Copy a workspace voice ID from 60db's voice
catalog; the adapter requires an explicit ID and never substitutes a voice.

```yaml
providers:
  sixtydb_tts:
    type: sixtydb
    capabilities: [tts]
    enabled: true
    api_key_env: SIXTYDB_API_KEY
    voice_id: your-workspace-voice-id
    # model_id: 60db-fast-v01  # optional; omitted uses the service default
    speed: 1.0
    connect_timeout_sec: 10
    read_timeout_sec: 30
    output_resampler: inherit

pipelines:
  hybrid_sixtydb:
    stt: local_stt
    llm: openai_llm
    tts: sixtydb_tts
    options:
      tts:
        format:
          encoding: mulaw
          sample_rate: 8000
```

The STT and LLM components must already be configured. Select `hybrid_sixtydb`
as the Agent's routing target, then restart the engine after changing provider
configuration or credentials. Custom keys ending in `_tts` are supported with
`type: sixtydb`. `api_key_file` and `api_key_env` reuse AVA's managed secret
resolver; secrets should not be stored as inline YAML values.

## Audio and limits

The adapter requests mono LINEAR16 at 16 kHz. NDJSON PCM streams progressively;
JSON and binary WAV responses are buffered before validation. AVA's shared
resampler and encoder produce 8 kHz mu-law or PCM16 at 8/16 kHz as selected by
the transport. The wideband declaration lets AudioSocket select native
16 kHz PCM. Output format remains owned by the transport profile.

Text must contain 1–5000 characters. Speed accepts 0.5–2.0. HTTP responses
are capped at 32 MiB; malformed audio, incompatible sample rates, empty
responses, and API errors fail the current synthesis. Task cancellation or
closing the generator releases its response; stopping the adapter closes its
session. Read timeout limits the gap between chunks, not the total utterance.
REST buffering adds latency; this integration does not offer WebSocket text streaming.

## Verification and troubleshooting

```bash
pytest tests/test_pipeline_sixtydb_adapter.py
agent check
docker compose restart ai_engine
```

Missing credentials/voice or a disabled provider causes referenced pipelines to
fail closed at startup. HTTP 401/403 indicates an authorization problem;
402 indicates credit or entitlement; 429 indicates rate/concurrency limits.
Check the workspace voice and model availability before retrying.

Provider credential verification checks the authenticated voice endpoint;
it does not prove synthesis or phone playback. Validate a real Asterisk call
with greeting, two turns, interruption, and hangup before production use.
Live synthesis and a real PBX call were not available during this contribution.

API reference: [60db TTS](https://docs.60db.ai/api-reference/tts/text-to-speech).
