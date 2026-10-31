# OpenAI-compatible local transcription

AVA's Admin API exposes the configured Local AI STT backend through authenticated
`GET /v1/models` and `POST /v1/audio/transcriptions`. This is a bounded local
transcription facade, not OpenAI's hosted service or a complete OpenAI API clone.
There is no cloud fallback and no transcription screen in the Admin UI.

## Setup and credentials

1. Configure and load a supported Local AI STT backend: Vosk, Faster-Whisper or
   Whisper.cpp. Preserve its installed build profile and model volumes; an API
   request does not install a backend or download a model. See [local setup](LOCAL_ONLY_SETUP.md).
2. Set either `AAVA_SPEECH_API_KEY` for Bearer authentication or both
   `AAVA_SPEECH_API_USERNAME` and `AAVA_SPEECH_API_PASSWORD` for Basic authentication
   in the Admin backend environment. Both schemes can be configured. Keep these
   credentials separate from browser login and JWT credentials. The API fails
   closed with `503 speech_api_not_configured` when neither scheme is configured.
3. Point Admin's `HEALTH_CHECK_LOCAL_AI_URL` to the reachable Local AI WebSocket
   endpoint for split-server deployments. The transcription bridge defaults to
   `ws://127.0.0.1:8765`. If Local AI requires `LOCAL_WS_AUTH_TOKEN`, supply the
   matching token to Admin. Do not expose the inference WebSocket publicly.
4. Rebuild/recreate affected Admin and Local AI services when upgrading their
   code, and recreate Admin after changing its environment. A browser refresh or
   plain restart does not reload Compose environment values. Keep Admin behind
   HTTPS and trusted network controls; see [security](../SECURITY.md).

Use authenticated `GET /v1/models` to check the facade. Its advertised model
`whisper-1` is a compatibility identifier, not proof that Whisper is the installed
backend or that inference works. The transcription endpoint also accepts `default`.

## Request and response

Send multipart fields `file`, `model`, optional `language`, and optional
`response_format`. For example, with Basic authentication:

```bash
curl --fail-with-body -u stt-client \
  -F 'file=@sample.wav' -F 'model=whisper-1' \
  -F 'response_format=json' \
  https://YOUR-ADMIN-HOST/v1/audio/transcriptions
```

Curl prompts for the password rather than placing it in shell history. For Bearer
authentication, supply the speech API key through your client's protected secret
configuration; never commit it or paste it into public diagnostics. These are
explicit transcription requests, not a no-effect health probe.

| Response format | Contract |
| --- | --- |
| `json` (default) | Object with `text` |
| `text` | Plain transcript text |
| `verbose_json` | Text, decoded duration and one whole-file segment |
| `srt`, `vtt` | One whole-file subtitle cue from zero to decoded duration |

Segment/cue timestamps are synthetic, not model-derived word timings. A requested
`auto` language is not a promise of a detected-language label in the response.
Backend success with no recognized speech can return empty text; inference errors
are not converted into successful empty transcripts. A lost HTTP response does
not prove that a transcript was returned to the client.

Only Faster-Whisper supports a per-request two-letter language code or `auto`
(`detect_language` is an accepted alias). English-only `.en` models accept `en`
only. Omit `language` for Vosk and Whisper.cpp; their configured model language
applies. Other or unloaded backends fail explicitly.

## Bounds and live-call priority

- Approved inputs: WAV, MP3, M4A/MP4, OGG, WebM/Matroska, FLAC and AAC. FFmpeg
  comes from the pinned Admin dependency. Inputs use private temporary files;
  allowed demuxers and file-only input protocols reject playlists and external inputs.
- File cap: 25 MiB; total multipart cap: 25 MiB + 64 KiB. Decoded audio is at
  most 120 seconds of mono 16 kHz PCM16 (3,840,000 bytes).
- Upload receipt: 30-second timeout; decoding: 90-second timeout.
- Local AI admits one isolated, killable batch worker with no waiting queue and
  a 180-second worker deadline. Active live-call sessions reject new batch work;
  new live setup preempts running batch work before caller audio is required.
- Admin's STT response wait defaults to 240 seconds through
  `AAVA_SPEECH_STT_TIMEOUT_SECONDS`, clamped to 10–600. This is not a single
  end-to-end upload/decode deadline. A shorter response timeout cancels the
  WebSocket request and its worker.
- HTTP disconnect or timeout cancels decoder/worker work. Admission remains held
  until cleanup completes. No automatic batch retry or queue is added.

The live-call priority is a lifecycle safeguard, not a capacity or latency SLA.
See the [local protocol](local-ai-server/PROTOCOL.md#one-shot-transcription-admin-bridge).

## Errors and troubleshooting

| Status | Check |
| --- | --- |
| 401 | Speech credentials, not browser login |
| 400 / 415 | Multipart fields, model/language/response format and content type |
| 413 | Uploaded or decoded audio limit |
| 408 / 422 | Upload timeout, invalid audio or decode timeout |
| 429 | Batch already busy or a live-call session has priority |
| 502 / 503 | Local WebSocket authentication/connectivity, loaded backend, response validity, or worker failure |
| 504 | Admin STT response timeout |

Check [environment wiring](ENVIRONMENT_VARIABLES.md#local-transcription-api),
saved backend settings and service logs without publishing credentials or audio.
Do not diagnose a healthy `/ready` or model list as successful transcription.

## Qualification

Existing real-backend acceptance in [#784](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/pull/784)
covers CPU Faster-Whisper and its documented API formats, decoding, disconnect,
timeout and live-call preemption cases. GPU, real Vosk/Whisper.cpp inference,
sustained load and every provider/transport combination are not inferred tested.
For v7.7.1, this evidence is reused under the explicit
[release waiver](baselines/golden/v7.7.1-validation-matrix.md), not relabelled as
new exact-candidate acceptance.
