from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Callable, Dict, Any, Optional
import time
import asyncio

from structlog import get_logger

logger = get_logger(__name__)

class AIProviderInterface(ABC):
    """
    Abstract Base Class for AI Providers.

    This class defines the contract that all AI provider implementations must follow.
    """
    def __init__(self, on_event: Callable[[Dict[str, Any]], None]):
        self.on_event = on_event
        self.provider_key: str = self.__class__.__name__
        self.provider_kind: str = self.__class__.__name__

    @property
    def on_event(self):
        return getattr(self, "_handoff_callback", None)

    @on_event.setter
    def on_event(self, callback):
        from .handoff import HandoffEvidence, EventCallback
        if not hasattr(self, "_handoff_evidence"):
            self._handoff_evidence = HandoffEvidence()
        self._provider_event_callback = callback
        if callback is None:
            self._handoff_callback = None
            return
        async def wrapped(event):
            native = getattr(self, "_current_response_id", None)
            enriched = self._handoff_evidence.event(event, native)
            if any(key.startswith("_handoff_") for key in enriched):
                enriched = {key: value for key, value in enriched.items() if not key.startswith("_handoff_")}
            return await callback(enriched)
        self._handoff_callback = EventCallback(wrapped, callback)

    async def prepare_handoff_result(self, result, call_id, origin_id=None):
        action = result.get("deferred_transfer") if isinstance(result, dict) else None
        if not isinstance(action, dict) or not action.get("handoff") or not self.on_event:
            return
        origin = self._handoff_evidence.prepare(call_id, action, origin_id or self._handoff_evidence.state(call_id).get("tool_origin") or getattr(self, "_current_response_id", None))
        if origin is not None:
            await self.on_event({"type": "HandoffPrepared", "call_id": call_id,
                                 "action_id": action["id"], "origin": origin})

    async def handoff_result_sent(self, result, call_id, *, success=True):
        action = result.get("deferred_transfer") if isinstance(result, dict) else None
        if not isinstance(action, dict) or not self.on_event:
            return
        if not action.get("handoff"):
            # No announcement bounds the wait for the reply turn that commits
            # this action, so let the engine time it from here: the model has
            # the result now. A failed send arms it too, since no reply follows.
            # Per-call flags live in the handoff evidence, which every provider
            # resets when a call's session starts and stops.
            flags = self._handoff_evidence.deferred_flags(call_id)
            flags["result_sent"] = True
            await self.on_event({"type": "DeferredTransferArmed", "call_id": call_id,
                                 "action_id": action.get("id"), "success": success})
            if flags["caller_speaking"]:
                await self.emit_caller_speech(call_id, started=True)  # Still talking.
            return
        await self.on_event({"type": "HandoffToolResultSent", "call_id": call_id,
                             "action_id": action["id"], "sent_at": time.monotonic(),
                             "success": success})

    async def emit_caller_speech(self, call_id, *, started):
        """Report a provider VAD caller-speech start or end to the engine.

        Only for providers that report both edges; the engine pauses the
        deferred-transfer silent-reply timer between them. Sent only once this
        provider has delivered an unannounced deferred-transfer result on the
        call, so other calls see no new events.
        """
        if not call_id or not self.on_event:
            return
        evidence = self._handoff_evidence
        if call_id not in evidence.deferred and not started:
            return  # Nothing to track or report for this call.
        flags = evidence.deferred_flags(call_id)
        flags["caller_speaking"] = bool(started)
        if not flags["result_sent"]:
            return
        event = {
            "type": "CallerSpeechStarted" if started else "CallerSpeechStopped",
            "call_id": call_id,
            "provider": self.provider_event_name(),
            "vad": True,
        }
        try:
            await self.on_event(event)
        except Exception:
            logger.debug("Failed to emit caller speech edge", call_id=call_id, exc_info=True)

    async def complete_handoff_audio(self, call_id, native_id=None):
        event = {"type": "HandoffAudioComplete", "call_id": call_id}
        if native_id is not None:
            event["audio_response_id"] = native_id
        enriched = self._handoff_evidence.event(event, native_id)
        if self._handoff_evidence.state(call_id)["active"] and self.on_event:
            await self.on_event(enriched)

    async def flush_handoff_pacer(self):
        """Admit the selected response's remaining bytes before sealing it.

        Only used for an opted-in announcement. The transport already paces
        queued audio; don't leave bytes in a provider pacer after completion.
        """
        call_id = getattr(self, "_call_id", None)
        if not call_id or not self._handoff_evidence.state(call_id)["active"] or not getattr(self, "_egress_pacer_enabled", False):
            return True
        self._pacer_running = False
        task = getattr(self, "_pacer_task", None)
        if task and not task.done() and task is not asyncio.current_task():
            try:
                await asyncio.wait_for(asyncio.shield(task), 0.25)
            except asyncio.TimeoutError:
                return False
        async with self._pacer_lock:
            tail = bytes(self._outbuf)
            self._outbuf.clear()
        if tail:
            self._in_audio_burst = True
            await self.on_event({"type": "AgentAudio", "call_id": call_id, "data": tail,
                                 "encoding": self.config.target_encoding,
                                 "sample_rate": self.config.target_sample_rate_hz})
        return True

    def interrupt_handoff(self, call_id):
        if hasattr(self, "_handoff_evidence"):
            self._handoff_evidence.turn(call_id, getattr(self, "_current_response_id", None))["interrupted"] = True

    async def handoff_transcript(self, call_id, text, native_id=None):
        if not hasattr(self, "_handoff_evidence"):
            return
        evidence = self._handoff_evidence.transcript(call_id, text, native_id)
        if self._handoff_evidence.state(call_id)["active"] and self.on_event:
            await self.on_event({"type": "HandoffTranscript", "call_id": call_id,
                                 "text": text, "handoff_evidence": evidence})

    def set_provider_identity(self, *, provider_key: str, provider_kind: str) -> None:
        self.provider_key = provider_key
        self.provider_kind = provider_kind

    def provider_event_name(self) -> str:
        return self.provider_key or self.provider_kind

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

    async def speak_text(self, text: str) -> bool:
        """Ask the provider to speak text with the active agent voice.

        Providers that support engine-initiated announcements should override this
        method and return ``True`` after accepting the request.
        """
        return False

    @property
    def terminal_output_protected(self) -> bool:
        """Whether the active terminal farewell must not be interrupted.

        Providers may override this while a confirmed end-of-call response is
        being spoken.  The engine uses it to reject echo/noise barge-in without
        changing ordinary conversational interruption behavior.
        """
        return False

    def release_terminal_output_protection(self) -> None:
        """Resume ordinary provider input after a terminal action is rejected.

        Providers that suppress caller input while a farewell or hangup is in
        progress should override this hook and clear that provider-local state.
        """
        return None

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

    # Provider-native formats to use when an Audio Profile explicitly opts a
    # call into a wideband linear-PCM transport.  These are deliberately
    # separate from the general capability lists: some APIs accept several
    # rates but have one native/recommended boundary (for example Gemini Live
    # receives 16 kHz PCM and emits 24 kHz PCM).  None means that the provider
    # has no declared wideband route in AAVA and the normal configured format
    # remains authoritative.
    wideband_input_encoding: Optional[str] = None
    wideband_input_sample_rate_hz: Optional[int] = None
    wideband_output_encoding: Optional[str] = None
    wideband_output_sample_rate_hz: Optional[int] = None


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
