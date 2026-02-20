# Pipeline Adapter Templates

Templates for building modular STT, LLM, and TTS adapters.

## Available Templates

| File | Role | Base Class |
|------|------|-----------|
| `template_stt_adapter.py` | Speech-to-Text | `STTComponent` |
| `template_llm_adapter.py` | Language Model | `LLMComponent` |
| `template_tts_adapter.py` | Text-to-Speech | `TTSComponent` |

## How to Use

Open any template in Windsurf and tell AVA:

> "Help me build an STT adapter like this for Azure Speech"

> "Help me build a TTS adapter like this for Amazon Polly"

AVA knows the architecture and will guide you through the implementation.

## Guide

[docs/contributing/adding-pipeline-adapter.md](../../docs/contributing/adding-pipeline-adapter.md)

## Reference Implementations

- STT: `src/pipelines/deepgram.py`
- LLM: `src/pipelines/openai_chat.py`
- TTS: `src/pipelines/google.py`
