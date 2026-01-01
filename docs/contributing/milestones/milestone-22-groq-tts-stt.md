# Milestone 22: Groq STT + TTS (Modular Pipelines)

**Status**: ✅ Complete  
**Priority**: High  
**Estimated Effort**: 3–5 days  
**Branch**: `feature/groq-tts-stt-implementation`  
**Completed**: January 1, 2026

## Summary

Add **Groq Cloud Speech-to-Text (STT)** and **Groq Text-to-Speech (TTS)** as **modular pipeline components**, enabling a fully cloud pipeline:

- `stt: groq_stt`
- `llm: groq_llm` (OpenAI-compatible chat via Groq)
- `tts: groq_tts`

This milestone focuses on the modular pipeline system (STT/LLM/TTS adapters) and Admin UI configuration, not a monolithic “Groq realtime agent”.

## What shipped in this branch (beyond Groq speech)

This milestone also includes related “make it work end-to-end” hardening needed to support swapping providers inside modular pipelines without regressions:

- **Modular OpenAI STT + TTS correctness** (per official OpenAI docs): defaults, option validation, and safer fallbacks when swapping providers.
- **Tools source-of-truth: Contexts only** (pipeline/provider tool enablement is deprecated and removed from UI).
- **Admin UI reliability**: YAML editor + forms behave consistently, prevent invalid configs, and avoid silently hiding user-defined values.
- **Capability-driven modular provider UX**: modular providers advertise `capabilities` and pipeline dropdowns filter based on those capabilities.

## Motivation

Operators want a simple cloud-only stack that works well on telephony (μ-law @ 8 kHz) while keeping the project’s modular pipeline architecture intact:

- Groq provides fast inference and OpenAI-compatible APIs.
- STT and TTS are separate endpoints and fit naturally into the existing pipeline orchestrator.

## Architecture Fit

### Where it plugs in

- Component contracts: `src/pipelines/base.py`
  - `STTComponent.transcribe(call_id, audio_pcm16, sample_rate_hz, options) -> str`
  - `TTSComponent.synthesize(call_id, text, options) -> AsyncIterator[bytes]`
- Pipeline resolution and adapter construction: `src/pipelines/orchestrator.py`
- Engine drives chunked STT + streaming playback: `src/engine.py`

### Audio format + sampling decisions

- **Inbound from telephony**: typically μ-law (`ulaw/mulaw`) @ **8 kHz**
- **Engine STT handoff**: PCM16LE @ **16 kHz** (pipeline STT adapters receive PCM16 bytes + `sample_rate_hz`)
- **Groq STT request**: wrap PCM16LE in a **mono WAV container** (`wave` stdlib) and send `multipart/form-data`
- **Groq TTS response**: request/receive **WAV**, decode to PCM16LE + sample rate, then:
  - resample to the audio profile target (usually **8 kHz**)
  - convert to μ-law for Asterisk playback
  - chunk into ~20 ms frames for streaming playback

## Implementation Details

## Implementation Phases

### Phase 1: Groq pipeline adapters (1–2 days)

| Step | Files | Description |
|------|-------|-------------|
| 1.1 | `src/pipelines/groq.py` | Implement `GroqSTTAdapter` (multipart WAV upload → transcript) |
| 1.2 | `src/pipelines/groq.py` | Implement `GroqTTSAdapter` (audio.speech → WAV decode → PCM → telephony format) |
| 1.3 | `tests/test_pipeline_groq_adapters.py` | Add adapter-focused tests (STT request + TTS decode/convert) |

### Phase 2: Config models + orchestrator wiring (1 day)

| Step | Files | Description |
|------|-------|-------------|
| 2.1 | `src/config.py` | Add `GroqSTTProviderConfig` + `GroqTTSProviderConfig` |
| 2.2 | `src/pipelines/orchestrator.py` | Hydrate Groq provider configs and register `groq_stt`/`groq_tts` factories |
| 2.3 | `src/config/security.py` | Inject `GROQ_API_KEY` from env into Groq provider blocks |

### Phase 3: Admin UI support (0.5–1 day)

| Step | Files | Description |
|------|-------|-------------|
| 3.1 | `admin_ui/frontend/src/utils/providerNaming.ts` | Ensure `type: groq` is treated as pipeline-capable |
| 3.2 | `admin_ui/frontend/src/components/config/providers/GenericProviderForm.tsx` | Add Groq STT/TTS models and voice option hints |
| 3.3 | `admin_ui/backend/api/config.py` | Provider connectivity tests: OpenAI-compatible and Groq Speech validation |

### Phase 4: Examples + docs (0.5 day)

| Step | Files | Description |
|------|-------|-------------|
| 4.1 | `examples/pipelines/cloud_only_groq.yaml` | Provide a cloud-only Groq pipeline example |
| 4.2 | `docs/Configuration-Reference.md` | Document Groq Speech provider knobs and requirements |

### Groq adapters

- `src/pipelines/groq.py`
  - `GroqSTTAdapter`: POST `audio/transcriptions` using `multipart/form-data`, robust transcript parsing (`json` and `text`).
  - `GroqTTSAdapter`: POST `audio/speech`, accepts raw bytes or JSON-wrapped base64 audio, decodes WAV → PCM16LE and converts to the target telephony format.

### Provider configuration models

- `src/config.py`
  - `GroqSTTProviderConfig`
  - `GroqTTSProviderConfig`

### Pipeline orchestrator wiring

- `src/pipelines/orchestrator.py`
  - Hydrates Groq configs (`_hydrate_groq_stt_config`, `_hydrate_groq_tts_config`)
  - Registers factories: `groq_stt`, `groq_tts`

### Admin UI support

- `admin_ui/frontend/src/utils/providerNaming.ts`
  - Ensures `type: groq` is treated as a registered provider type for pipeline selection UX.
- `admin_ui/frontend/src/components/config/providers/GenericProviderForm.tsx`
  - Provides sensible dropdown options for Groq STT models, Groq TTS models, and voices.
- `admin_ui/frontend/src/pages/System/EnvPage.tsx`
  - `GROQ_API_KEY` environment variable support surfaced in the UI.

## Configuration

### Required environment variable

- `GROQ_API_KEY`
- `OPENAI_API_KEY` (only if using modular OpenAI components)

### Provider examples

`config/ai-agent.yaml` includes modular provider entries:

```yaml
providers:
  groq_llm:
    type: openai
    api_key: ${GROQ_API_KEY}
    chat_base_url: https://api.groq.com/openai/v1
    chat_model: llama-3.3-70b-versatile
    tools_enabled: false

  groq_stt:
    type: groq
    capabilities: [stt]
    enabled: false
    stt_base_url: https://api.groq.com/openai/v1/audio/transcriptions
    stt_model: whisper-large-v3-turbo
    response_format: json
    temperature: 0
    request_timeout_sec: 15

  groq_tts:
    type: groq
    capabilities: [tts]
    enabled: false
    tts_base_url: https://api.groq.com/openai/v1/audio/speech
    tts_model: canopylabs/orpheus-v1-english
    voice: hannah
    response_format: wav
    max_input_chars: 200
    target_encoding: mulaw
    target_sample_rate_hz: 8000
    chunk_size_ms: 20
    request_timeout_sec: 15
```

### Pipeline example

- `examples/pipelines/cloud_only_groq.yaml`

```yaml
pipelines:
  cloud_only_groq:
    stt: groq_stt
    llm: groq_llm
    tts: groq_tts

active_pipeline: cloud_only_groq
```

## OpenAI modular components (updates included)

These are **modular pipeline components** and are separate from `openai_realtime` (which is a full agent provider).

- **STT** (`openai_stt`):
  - Default model is `whisper-1`.
  - Supported models also include the GPT-4o transcribe family (see the UI + config reference).
  - Uses `response_format` correctly and validates compatibility by model family.
- **TTS** (`openai_tts`):
  - Built-in voices only (no custom voice IDs yet).
  - Uses `/v1/audio/speech` with `response_format` (not `format`), preferring `wav`/`pcm` for low-latency decoding.
  - Adds safe fallbacks when a pipeline swap leaves a Groq voice/model in the OpenAI TTS config.

## Tools: Contexts are the source of truth

Tool enablement and allowlisting is managed **only via Contexts**.

- Legacy `pipelines.*.tools` is treated as deprecated and removed from the Admin UI pipeline editor.
- Admin UI save flows sanitize configs to strip `pipelines.*.tools` when saving YAML from form pages.
- Providers that do not reliably support tool calling (notably Groq’s OpenAI-compatible chat) will not block the call:
  - If a tool-enabled request fails with Groq tool-calling errors, we retry once without tools to keep the conversation flowing.
  - This means “tools enabled in context” stays the source of truth, but a given LLM may effectively run tool-less if it can’t support them.

## Testing

- `tests/test_pipeline_groq_adapters.py`
  - Validates STT request construction and transcript parsing.
  - Validates TTS WAV decode + conversion to target encoding and chunking behavior.
  - Includes coverage for Groq tool-calling failure fallback behavior when using Groq OpenAI-compatible chat.

## Operational Notes / Known Issues

- **Groq Speech Terms**: first-time use may require accepting terms in the Groq console; otherwise requests can fail until accepted.
- **Rate limits (429)**: both STT and TTS can hit RPM/TPD limits depending on account tier; mitigation options:
  - lower call volume in test windows
  - add retry/backoff for `429` (bounded) where appropriate
  - switch TTS/STT to local providers as a temporary fallback when testing at scale
- **Orpheus TTS constraints**: WAV response is the stable baseline and `max_input_chars` should be enforced to avoid failures.

## Follow-ups (Optional)

- Add a small bounded retry policy for Groq Speech `429` and `>=500` (respecting the project’s streaming-first latency constraints).
- Consider server-side streaming TTS if/when Groq supports it reliably (would reduce perceived latency by starting playback earlier).
