from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Callable, Dict, Any, Optional

class AIProviderInterface(ABC):
    """
    Abstract Base Class for AI Providers.

    This class defines the contract that all AI provider implementations must follow.
    """
    def __init__(self, on_event: Callable[[Dict[str, Any]], None]):
        self.on_event = on_event

    @property
    @abstractmethod
    def supported_codecs(self) -> List[str]:
        """Returns a list of supported codec names, in order of preference."""
        pass

    @abstractmethod
    async def start_session(self, call_id: str, on_event: callable):
        """Initializes the connection to the AI provider for a new call."""
        pass

    @abstractmethod
    async def send_audio(self, audio_chunk: bytes):
        """Sends a chunk of audio data to the AI provider."""
        pass

    @abstractmethod
    async def stop_session(self):
        """Closes the connection and cleans up resources for the call."""
        pass

    # Optional: providers can override to describe codec/sample alignment characteristics.
    def describe_alignment(
        self,
        *,
        audiosocket_format: str,
        streaming_encoding: str,
        streaming_sample_rate: int,
    ) -> List[str]:
        """
        Return human-readable issues when the provider's implementation conflicts with
        the configured AudioSocket/streaming formats. Defaults to no findings.
        """
        return []


@dataclass
class ProviderCapabilities:
    """Static capability hints for transport orchestration and audio processing.

    These are not guarantees; providers may still negotiate different formats at runtime.
    """
    # Audio format capabilities
    input_encodings: List[str]
    input_sample_rates_hz: List[int]
    output_encodings: List[str]
    output_sample_rates_hz: List[int]
    preferred_chunk_ms: int = 20
    can_negotiate: bool = True  # If False, use static config only
    
    # Provider type and audio processing capabilities
    is_full_agent: bool = False  # True for providers like OpenAI Realtime, Google Live, Deepgram Voice Agent
    has_native_vad: bool = False  # True if provider has built-in Voice Activity Detection
    has_native_barge_in: bool = False  # True if provider handles interruption/barge-in internally
    has_native_aec: bool = False  # True if provider has built-in Acoustic Echo Cancellation (safe to skip local VAD on telephony)
    requires_continuous_audio: bool = False  # True if provider needs continuous audio stream (not VAD-gated)


def _safe_list(val: Optional[List[Any]]) -> List[Any]:
    try:
        return list(val or [])
    except Exception:
        return []


class ProviderCapabilitiesMixin:
    def get_capabilities(self) -> Optional[ProviderCapabilities]:
        """Optional capability report. Override in concrete providers.

        Default returns None, meaning the orchestrator should rely on configuration
        or runtime acknowledgements instead of static capability hints.
        """
        return None
