# Provider Development

This guide covers how to add new AI providers and pipeline adapters to AAVA.

## Two Integration Surfaces

AAVA has two ways to integrate AI providers:

### 1. Full-Agent Providers

Monolithic providers that handle STT+LLM+TTS as a single streaming session. These connect via WebSocket and manage the entire conversation flow.

- **Location**: `src/providers/`
- **Examples**: `openai_realtime.py`, `deepgram.py`, `google_live.py`, `elevenlabs.py`
- **When to use**: When the provider offers an all-in-one voice API

### 2. Pipeline Adapters

Modular components that slot into the STT/LLM/TTS pipeline. Each adapter handles one piece of the pipeline and can be mixed with other adapters.

- **Location**: `src/pipelines/`
- **Examples**: `google.py` (google_stt, google_tts), `elevenlabs_tts.py`, `groq.py`
- **When to use**: When adding a standalone STT, LLM, or TTS service

## Tutorial: Adding a Pipeline Adapter

Pipeline adapters are the most common contribution. Here's how to add one, using the existing Google adapter (`src/pipelines/google.py`) as the reference pattern.

### Step 1: Create the Adapter File

```python
# src/pipelines/my_provider.py

import structlog
from typing import AsyncIterator, Optional

logger = structlog.get_logger(__name__)


async def my_provider_stt(audio_chunks: AsyncIterator[bytes], config: dict) -> AsyncIterator[str]:
    """
    Stream audio chunks to My Provider's STT API and yield transcription text.

    Args:
        audio_chunks: Async iterator of raw audio bytes (format per audio profile)
        config: Provider config from ai-agent.yaml providers.my_provider section
    """
    api_key = config.get('api_key', '')
    model = config.get('stt_model', 'default-model')

    # Connect to provider API
    # Stream audio chunks
    # Yield transcription results as they arrive
    async for chunk in audio_chunks:
        # Your STT integration here
        pass


async def my_provider_tts(text: str, config: dict) -> bytes:
    """
    Convert text to speech using My Provider's TTS API.

    Args:
        text: Text to synthesize
        config: Provider config section

    Returns:
        Raw audio bytes in the format specified by the audio profile
    """
    api_key = config.get('api_key', '')
    voice = config.get('voice', 'default-voice')

    # Call provider TTS API
    # Return audio bytes
    return b''
```

### Step 2: Register in Pipeline Orchestrator

The pipeline orchestrator in `src/pipelines/` needs to know about your adapter. Add the mapping so that config references like `stt: my_provider_stt` resolve to your function.

### Step 3: Add Configuration

Add a provider section to `config/ai-agent.yaml`:

```yaml
providers:
  my_provider:
    api_key: "${MY_PROVIDER_API_KEY}"
    stt_model: "model-name"
    tts_model: "model-name"
    voice: "default-voice"

pipelines:
  my_pipeline:
    stt: my_provider_stt
    llm: openai_chat      # Can mix with other providers
    tts: my_provider_tts
```

### Step 4: Add Environment Variable

Add the API key to `.env.example`:

```bash
MY_PROVIDER_API_KEY=your-key-here
```

### Step 5: Create a Setup Guide

Create `docs/Provider-MyProvider-Setup.md` following the pattern of existing setup guides:
- Provider overview and pricing
- API key setup instructions
- Configuration examples
- Troubleshooting tips

### Step 6: Test

1. Write unit tests for your adapter
2. Test with a real call using your provider
3. Verify `agent check` still passes

## Expectations for Contributions

- Add or update setup docs (user-facing): `docs/Provider-*-Setup.md`
- Add tests under `tests/` when behavior changes
- Follow the existing code style (structlog for logging, async patterns)
- Verify audio format alignment with the transport layer (sample rate, encoding)

## References

- Architecture overview: [architecture-quickstart.md](architecture-quickstart.md)
- Architecture deep dive: [architecture-deep-dive.md](architecture-deep-dive.md)
- Provider implementation references:
  - [Provider-Google-Implementation.md](references/Provider-Google-Implementation.md)
  - [Provider-Deepgram-Implementation.md](references/Provider-Deepgram-Implementation.md)
  - [Provider-OpenAI-Implementation.md](references/Provider-OpenAI-Implementation.md)
- Existing setup guides:
  - [Provider-Google-Setup.md](../Provider-Google-Setup.md)
  - [Provider-Deepgram-Setup.md](../Provider-Deepgram-Setup.md)
  - [Provider-OpenAI-Setup.md](../Provider-OpenAI-Setup.md)
  - [Provider-ElevenLabs-Setup.md](../Provider-ElevenLabs-Setup.md)
