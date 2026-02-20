# Adding a Full Agent Provider

A full agent provider handles STT + LLM + TTS end-to-end over a single connection (typically WebSocket). Examples: OpenAI Realtime, Deepgram Agent, ElevenLabs Agent.

> **Using AVA?** Open this file in Windsurf and say: "Help me build a full agent provider like OpenAI Realtime."

## When to Use This

Use a full agent provider when:
- The service handles speech-to-text, language model, and text-to-speech in one API
- The service uses a persistent WebSocket connection
- You want the lowest possible latency (no separate STT→LLM→TTS round-trips)

For modular adapters (separate STT, LLM, or TTS), see [Adding a Pipeline Adapter](adding-pipeline-adapter.md).

## Architecture

```
Asterisk → AudioTransport → YourProvider (STT+LLM+TTS) → AudioTransport → Asterisk
                                    ↕
                             ToolRegistry (function calling)
```

Full agent providers implement `AIProviderInterface` from `src/providers/base.py` and communicate via an event callback.

## Step-by-Step

### 1. Create the Provider Module

Create `src/providers/your_provider.py`:

```python
import asyncio
import structlog
from typing import Dict, Any, List, Callable, Optional
from dataclasses import dataclass

from src.providers.base import AIProviderInterface, ProviderCapabilities, ProviderCapabilitiesMixin

logger = structlog.get_logger(__name__)

@dataclass
class YourProviderConfig:
    """Configuration for YourProvider."""
    api_key: str = ""
    model: str = "default-model"
    voice: str = "default-voice"

class YourProvider(AIProviderInterface, ProviderCapabilitiesMixin):
    """Full agent provider for YourService."""

    def __init__(self, config: YourProviderConfig, on_event: Callable[[Dict[str, Any]], None], **kwargs):
        self.config = config
        self._on_event = on_event
        self._call_id: Optional[str] = None

    @property
    def supported_codecs(self) -> List[str]:
        return ["pcm16"]  # List supported audio codecs

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            input_encodings=["pcm16"],
            input_sample_rates_hz=[16000],
            output_encodings=["pcm16"],
            output_sample_rates_hz=[16000],
            preferred_chunk_ms=20,
            is_full_agent=True,
            has_native_vad=True,
        )

    async def start_session(self, call_id: str, on_event: Callable):
        """Connect to the provider and start a session."""
        self._call_id = call_id
        self._on_event = on_event
        # TODO: Open WebSocket, authenticate, configure session
        logger.info("session_started", call_id=call_id)

    async def send_audio(self, audio_chunk: bytes):
        """Send audio data to the provider."""
        # TODO: Send audio over WebSocket
        pass

    async def stop_session(self):
        """Close the session and clean up."""
        # TODO: Close WebSocket, cancel background tasks
        logger.info("session_stopped", call_id=self._call_id)
```

### 2. Emit Events

Your provider communicates back to the engine via the `on_event` callback. Key events:

```python
# Audio output from the provider
self._on_event({
    "type": "audio",
    "audio": audio_bytes,  # PCM16 audio data
})

# Transcript of what the user said
self._on_event({
    "type": "transcript",
    "text": "Hello, I need help",
    "is_final": True,
})

# AI text response
self._on_event({
    "type": "response",
    "text": "How can I help you today?",
})

# Tool call request
self._on_event({
    "type": "tool_call",
    "tool_name": "transfer_call",
    "tool_call_id": "call_123",
    "parameters": {"destination": "support"},
})
```

### 3. Register the Provider

Add your provider to the factory/resolver in `src/main.py` or the appropriate registration point (check existing provider registration patterns).

### 4. Add Configuration

Add to `config/ai-agent.yaml`:

```yaml
providers:
  your_provider:
    api_key: ${YOUR_PROVIDER_API_KEY}
    model: "default-model"
    voice: "default-voice"
```

Add the env var to `.env`:

```
YOUR_PROVIDER_API_KEY=your-key-here
```

### 5. Add Tests

Create `tests/providers/test_your_provider.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.providers.your_provider import YourProvider, YourProviderConfig

@pytest.mark.asyncio
async def test_start_session():
    config = YourProviderConfig(api_key="test-key")
    on_event = MagicMock()
    provider = YourProvider(config, on_event)
    # Test session lifecycle
```

### 6. Update Documentation

- Add `docs/Provider-YourProvider-Setup.md` with setup instructions
- Update `docs/Configuration-Reference.md` with new config fields
- Update `docs/ROADMAP.md` if this was a planned milestone

## Reference Implementation

See `src/providers/openai_realtime.py` as the canonical full agent provider example.

## Key Rules

- Honor negotiated audio profiles — never hard-code codecs
- Emit Prometheus metrics for latency and errors
- Expose readiness via `/health`
- Use `structlog` for all logging
- Handle WebSocket disconnects gracefully with reconnect logic
- Support tool calling via `ToolRegistry` and the appropriate tool adapter

## Common Pitfalls

See [COMMON_PITFALLS.md](COMMON_PITFALLS.md) for real issues from production, especially:
- OpenAI Realtime modalities must be `['audio', 'text']`, not audio-only
- VAD sensitivity tuning (`webrtc_aggressiveness: 1` for OpenAI Realtime)
- Schema format differences between providers
