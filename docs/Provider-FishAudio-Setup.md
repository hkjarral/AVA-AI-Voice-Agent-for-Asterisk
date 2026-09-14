# Fish Audio Provider Setup Guide

## Overview

[Fish Audio](https://fish.audio) is a text-to-speech service built on the S1/S2
speech models, with a large public voice library and voice cloning. It is a
pipeline TTS adapter in AVA: pair it with any STT and LLM provider.

It suits telephony for one specific reason: the API returns **raw PCM at a sample
rate you choose**, streamed over a chunked HTTP response. AVA asks for the call's
own rate, so an 8 kHz call is synthesised at 8 kHz, converted to µ-law chunk by
chunk, and played while the sentence is still being generated — no intermediate
resample, no waiting for the full sentence.

| | |
|---|---|
| Capability | TTS (pipeline adapter) |
| Provider key | `fishaudio_tts` |
| Models | `s1`, `s2-pro`, `s2.1-pro` (default), `drama-3-preview`, `s2.1-pro-free` (no API credit needed, for testing) |
| Output used | `pcm` (streamed) or `wav` (buffered) |
| Pricing | Per character, see [fish.audio](https://fish.audio) — a free tier is available for testing |
| Per-agent voice | Not applicable (modular adapter); the voice is set on the provider or pipeline |

## Quick Start

### 1. Get an API key

1. Create an account on [fish.audio](https://fish.audio).
2. Open the API keys page and create a key.
3. Optional: browse the voice library and copy the id of the voice you want.
   That id is the `reference_id` below; leave it empty to use your account
   default.

### 2. Configure the environment variable

```bash
# .env
FISH_AUDIO_API_KEY=your-api-key
```

The adapter is not registered when the key is missing: a pipeline referencing it
falls back to a placeholder adapter and logs a warning, instead of failing calls.

### 3. Configure the provider

```yaml
providers:
  fishaudio_tts:
    type: fishaudio
    capabilities:
      - tts
    enabled: true
    model: s2.1-pro        # s1, s2-pro, s2.1-pro, drama-3-preview
    reference_id: null     # voice id from your Fish Audio library
    audio_format: pcm      # pcm (streamed) or wav (buffered)
    sample_rate: null      # null follows the call: 8 kHz telephony, 16 kHz wideband
    transport: http        # http, or websocket for the realtime session
    latency: low           # low, normal, balanced
    chunk_length: 200      # 100-300, provider-side synthesis granularity
    normalize: true
    temperature: 0.7
    top_p: 0.7
    speed: null            # prosody.speed override
    volume: null           # prosody.volume override
    request_timeout_sec: 15 # whole-request budget, streamed body included
    output_resampler: inherit
```

`latency: low` favours time to first audio, which is what a phone call needs.
Leave `sample_rate` at `null` unless you have a reason to force a rate: a rate
Fish Audio cannot emit (anything outside 8, 16, 24, 32, 44.1 and 48 kHz) falls
back to 16 kHz and is resampled locally.

### 4. Configure a pipeline

```yaml
pipelines:
  hybrid_fishaudio:
    stt: local_stt
    llm: openai_llm
    tts: fishaudio_tts
    options:
      tts:
        format:
          encoding: mulaw
          sample_rate: 8000
```

Every provider key can also be overridden per pipeline under `options.tts`, and
per request at runtime (useful to switch voice mid-call).

### 5. Test a call

```bash
agent check
docker compose restart ai_engine
```

Place a call into the pipeline and watch the engine log:

```
Fish Audio TTS synthesis started   call_id=... model=s2.1-pro source_sample_rate=8000
Fish Audio TTS synthesis completed call_id=... first_audio_ms=210 output_bytes=27040
```

`first_audio_ms` is the time from the request to the first audio chunk handed to
the transport: that is the number to watch when tuning `latency` and
`chunk_length`.

## Testing without an account

`scripts/fish_audio_mock.py` answers like the service does — Bearer auth, the
`model` header, body and sample-rate validation, chunked audio — so the whole
path can be exercised, including a real call:

```bash
python scripts/fish_audio_mock.py &

# unit tests
pytest tests/test_pipeline_fish_audio_adapters.py

# same integration test, against the mock instead of the service
FISH_AUDIO_API_KEY=mock-key FISH_AUDIO_BASE_URL=http://127.0.0.1:8788/v1 \
    pytest -m integration tests/test_pipeline_fish_audio_adapters.py
```

To route an actual call through it, set `base_url: http://127.0.0.1:8788/v1` on
the provider. The mock serves a generated tone by default; point
`FISH_MOCK_LOCAL_WS=ws://127.0.0.1:8765` at a local AI server to hear speech
instead. Three hooks in the request text exercise failure handling:
`FISH_MOCK_401`, `FISH_MOCK_SLOW` and `FISH_MOCK_EMPTY`.

## Realtime transport (websocket)

Fish Audio also exposes a realtime endpoint, `wss://api.fish.audio/v1/tts/live`,
which keeps one session open and accepts text as it is written. With
`transport: websocket` the adapter opens that session for a whole turn and sends
each fragment as the engine produces it, so the provider starts speaking while
the model is still composing the rest — and the engine no longer waits for a
fragment to be synthesised before consuming the next tokens.

```yaml
providers:
  fishaudio_tts:
    transport: websocket
    # ws_base_url: wss://api.fish.audio/v1   # defaults to base_url with a ws scheme
```

Requirements and behaviour:

- `msgpack` (already in `requirements.txt`) — the realtime protocol is
  MessagePack. It is imported only when the websocket transport is used.
- The engine feeds text progressively only for adapters that declare
  `supports_text_stream`; with `transport: http` the same adapter keeps the
  one-request-per-fragment path, so nothing else changes.
- A superseded turn (barge-in, a newer transcript) cancels the session; the
  provider sees the socket close, which is expected.
- The session log lines are `Fish Audio realtime session opening` and
  `... completed`, with `fragments`, `first_audio_ms` and `output_bytes`.

Test it against the mock, which serves the realtime endpoint too:

```bash
python scripts/fish_audio_mock.py &     # HTTP on 8788, realtime on 8789
FISH_AUDIO_API_KEY=mock-key FISH_AUDIO_WS_BASE_URL=ws://127.0.0.1:8789/v1 \
    pytest -m integration tests/test_pipeline_fish_audio_adapters.py
```

## Checking against the live service for free

The `s2.1-pro-free` model answers without API credit, so both endpoints can
be exercised at no cost:

```bash
FISH_AUDIO_API_KEY=... FISH_AUDIO_MODEL=s2.1-pro-free \
    FISH_AUDIO_REFERENCE_ID=<a voice id from the library> \
    pytest -m integration tests/test_pipeline_fish_audio_adapters.py
```

Worth knowing before you top up: API credit is billed separately from the
platform credit shown in the web app. An account with platform credits but no
developer balance gets `402 Payment Required` from the paid models, and the
realtime endpoint refuses the websocket handshake with the same status.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Fish Audio TTS requires an API key` | `FISH_AUDIO_API_KEY` is unset, or the provider block has an empty `api_key`. |
| Pipeline resolves to a placeholder adapter | Same cause, or `enabled: false`. The startup log says `Fish Audio TTS pipeline adapter not registered`. |
| HTTP 401 in the engine log | Key rejected by the service; check it has TTS access and remaining credit. |
| HTTP 402 / quota errors | Out of credit on the Fish Audio account. |
| HTTP 422 `unsupported sample_rate` | Something forced a rate the service does not emit. Leave `sample_rate: null`. |
| `Unsupported Fish Audio TTS output format` | `audio_format` must be `pcm` or `wav`; mp3 and opus are not used for calls. |
| Audio plays but sounds thin or metallic | Check the transport encoding and rate in `options.tts.format`; on 8 kHz telephony the adapter should report `source_sample_rate=8000` (no resample). |
| First audio is slow | Try `latency: low` and a smaller `chunk_length`; check network latency to the API, and confirm the greeting is not synthesised on the caller's first turn. |
| Turn fails after ~15 s | The request budget (`request_timeout_sec`) elapsed; the provider never finished streaming. |
| `realtime transport requires msgpack` | Install `msgpack` (it ships in `requirements.txt`) or switch back to `transport: http`. |
| No audio at all, no error | The service answered without audio (`output_bytes=0`); the call continues silently. Check the text sent and the account status. |

## References

- Fish Audio API reference: <https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech>
- Provider keys and audio fields: [Configuration-Reference.md](Configuration-Reference.md)
- Adapter: `src/pipelines/fish_audio.py`
- Tests: `tests/test_pipeline_fish_audio_adapters.py`
