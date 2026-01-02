# Milestone 15: Groq Speech (STT/TTS) for Modular Pipelines

**Status**: 🟡 Draft / Missing milestone doc (restored)  
**Priority**: Medium  
**Estimated Effort**: 5–10 days  
**Branch**: `feature/groq-speech-pipelines`  

## Summary

Add **Groq Cloud Speech-to-Text (STT)** and **Text-to-Speech (TTS)** as **modular pipeline components**:

- `stt: groq_stt`
- `llm: groq_llm` (already supported as OpenAI-compatible chat in config)
- `tts: groq_tts`

This is pipeline-only (not a monolithic realtime provider).

## Where the “Groq milestone” is today

There is currently **no milestone document** under `docs/contributing/milestones/` for Groq Speech.

The existing material lives in:

- `archived/groq-tts-stt-modular-implementation.md` (implementation plan / notes)

And config already includes Groq LLM pipeline entries:

- `config/ai-agent.yaml` contains `groq_llm` and example pipelines like `local_hybrid_groq`.

This milestone doc formalizes that archived plan into the project’s milestone format.

## Motivation

Groq’s OpenAI-compatible endpoints make it a pragmatic option for:

- faster / cheaper LLM inference (text-only path)
- potential cloud STT/TTS options that integrate cleanly with the pipeline architecture

## Scope

### In scope

- Add STT adapter: Groq Speech transcription (OpenAI-compatible `/audio/transcriptions`)
- Add TTS adapter: Groq Speech synthesis (OpenAI-compatible `/audio/speech`)
- Add config schema support + docs
- Add pipeline factories in `PipelineOrchestrator`

### Out of scope (initial milestone)

- “Realtime Groq voice agent” (single websocket STT+LLM+TTS). This milestone is pipeline-only.
- Tool calling enablement for `groq_llm` if Groq LLM behavior is incompatible (keep disabled if needed).

## Design Notes

### Pipeline integration points

Groq STT/TTS must implement the pipeline component interfaces:

- `STTComponent.transcribe(...)`
- `TTSComponent.synthesize(...)`

and be registered as factories in `src/pipelines/orchestrator.py`.

### Audio format expectations

The engine expects TTS adapters to yield audio in the downstream target format (telephony-friendly μ-law 8 kHz for most profiles) and chunked in 20ms frames when streaming playback is used.

## Implementation Plan

1) Add Groq provider config model(s) in `src/config.py` (or reuse existing “openai-compatible” shapes with minimal new fields).
2) Add pipeline adapters in `src/pipelines/groq.py`:
   - `GroqSTTAdapter`
   - `GroqTTSAdapter`
3) Register factories in `src/pipelines/orchestrator.py`:
   - `groq_stt`
   - `groq_tts`
4) Update configuration docs:
   - `docs/Configuration-Reference.md` (Groq keys and examples)
5) Add an example pipeline file under `examples/` (optional but recommended).

## Acceptance Criteria

- A pipeline can be configured with `stt: groq_stt` and `tts: groq_tts` and successfully complete a basic turn.
- Errors are readable (HTTP status + body snippet) and do not crash the engine.
- Configuration is documented and validated by Admin UI config validation flow.

