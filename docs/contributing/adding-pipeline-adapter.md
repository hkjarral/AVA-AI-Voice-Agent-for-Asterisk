# Adding a Pipeline Adapter (STT / LLM / TTS)

Pipeline adapters are modular components that handle a single role — speech-to-text (STT), language model (LLM), or text-to-speech (TTS). They can be mixed and matched freely (e.g., Deepgram STT + OpenAI LLM + Google TTS).

> **Using AVA?** Open this file in Windsurf and say: "Help me build a [STT/LLM/TTS] adapter for [provider name]."

## When to Use This

Use a pipeline adapter when:
- The service handles only STT, only LLM, or only TTS (not all three)
- You want to mix providers (e.g., best STT from one, best TTS from another)
- The service uses REST APIs or streaming HTTP (not a single persistent WebSocket)

For monolithic providers that handle everything, see [Adding a Full Agent Provider](adding-full-agent-provider.md).

## Architecture

```
Asterisk → AudioTransport → STTAdapter → LLMAdapter → TTSAdapter → AudioTransport → Asterisk
```

Each adapter extends a base class from `src/pipelines/base.py` and is resolved by `PipelineOrchestrator`.

## STT Adapter

### Step-by-Step

1. Create `src/pipelines/your_provider_stt.py`:

```python
import structlog
from typing import Dict, Any, Optional
from src.pipelines.base import STTComponent

logger = structlog.get_logger(__name__)

class YourProviderSTT(STTComponent):
    """STT adapter for YourProvider."""

    def __init__(self, call_id: str, options: Dict[str, Any]):
        self.call_id = call_id
        self.options = options
        self._api_key = options.get("api_key") or self._auto_detect_credentials()

    async def transcribe(self, call_id: str, audio_pcm16: bytes,
                         sample_rate_hz: int = 16000,
                         options: Optional[Dict[str, Any]] = None) -> str:
        """Transcribe audio to text.

        Args:
            call_id: Unique call identifier.
            audio_pcm16: Raw PCM16 audio bytes.
            sample_rate_hz: Sample rate (default 16000).
            options: Additional transcription options.

        Returns:
            Transcribed text string.
        """
        # TODO: Send audio to your STT API
        # Return the transcribed text
        return ""

    async def start(self):
        """Initialize resources (optional)."""
        pass

    async def stop(self):
        """Clean up resources (optional)."""
        pass
```

2. Register in `src/pipelines/orchestrator.py`:

```python
# Add factory method
def _make_your_provider_stt_factory():
    def factory(call_id: str, options: Dict[str, Any]) -> Component:
        from src.pipelines.your_provider_stt import YourProviderSTT
        return YourProviderSTT(call_id, options)
    return factory

# Register in _register_factories()
self._registry["your_provider_stt"] = _make_your_provider_stt_factory()
```

3. Add YAML config:

```yaml
providers:
  your_provider:
    api_key: ${YOUR_PROVIDER_API_KEY}
    stt_model: "default-model"
    stt_language: "en-US"

pipelines:
  custom:
    stt: your_provider_stt
    llm: openai_llm      # mix with any LLM
    tts: deepgram_tts     # mix with any TTS
```

## LLM Adapter

Create `src/pipelines/your_provider_llm.py`:

```python
import structlog
from typing import Dict, Any, Optional, Union
from src.pipelines.base import LLMComponent, LLMResponse

logger = structlog.get_logger(__name__)

class YourProviderLLM(LLMComponent):
    """LLM adapter for YourProvider."""

    def __init__(self, call_id: str, options: Dict[str, Any]):
        self.call_id = call_id
        self.options = options

    async def generate(self, call_id: str, transcript: str,
                       context: Optional[Dict[str, Any]] = None,
                       options: Optional[Dict[str, Any]] = None) -> Union[str, LLMResponse]:
        """Generate AI response from transcript.

        Args:
            call_id: Unique call identifier.
            transcript: User's transcribed speech.
            context: Conversation context (system prompt, history, etc.).
            options: Additional generation options.

        Returns:
            Response text or LLMResponse with tool_calls.
        """
        # TODO: Call your LLM API
        # For tool calls, return LLMResponse:
        # return LLMResponse(text="", tool_calls=[...])
        return ""
```

Register the same way as STT, using `your_provider_llm` as the key.

## TTS Adapter

Create `src/pipelines/your_provider_tts.py`:

```python
import structlog
from typing import Dict, Any, Optional, AsyncIterator
from src.pipelines.base import TTSComponent

logger = structlog.get_logger(__name__)

class YourProviderTTS(TTSComponent):
    """TTS adapter for YourProvider."""

    def __init__(self, call_id: str, options: Dict[str, Any]):
        self.call_id = call_id
        self.options = options

    async def synthesize(self, call_id: str, text: str,
                         options: Optional[Dict[str, Any]] = None) -> AsyncIterator[bytes]:
        """Synthesize text to audio.

        Args:
            call_id: Unique call identifier.
            text: Text to speak.
            options: Additional synthesis options.

        Yields:
            PCM16 audio chunks (16kHz, mono).
        """
        # TODO: Call your TTS API and yield audio chunks
        yield b""
```

## Testing

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_stt_transcribe():
    adapter = YourProviderSTT("test-call", {"api_key": "test"})
    result = await adapter.transcribe("test-call", b"\x00" * 3200)
    assert isinstance(result, str)
```

## Key Rules

- Internal audio format is **PCM16 @ 16kHz** — match this for zero transcoding
- Use `_auto_detect_credentials()` from `Component` base for env var fallback
- Component key format: `<provider>_<role>` (e.g., `azure_stt`, `anthropic_llm`)
- Register factory functions in `orchestrator.py` (lazy import inside factory)
- Emit Prometheus metrics and expose readiness via `/health`
- Use `structlog` for all logging

## Reference Implementations

- STT: `src/pipelines/deepgram.py` (Deepgram STT)
- LLM: `src/pipelines/openai_chat.py` (OpenAI Chat Completions)
- TTS: `src/pipelines/google.py` (Google Cloud TTS)

## Documentation Checklist

- [ ] Create `docs/Provider-YourProvider-Setup.md`
- [ ] Update `docs/Configuration-Reference.md` with new fields
- [ ] Update `docs/ROADMAP.md` status if this was a planned milestone
- [ ] Add example pipeline config to `examples/pipelines/`
