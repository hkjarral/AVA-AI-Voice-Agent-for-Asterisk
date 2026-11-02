"""Call-local speech evidence for upstream noise-floor recovery, not turn taking."""
from __future__ import annotations

import audioop
from typing import Optional

try:
    import webrtcvad
except ImportError:  # Optional/custom installations must preserve caller audio.
    webrtcvad = None


class UpstreamSpeechEvidence:
    """Frame a private detector copy; never change provider audio or share VAD state.

    WebRTC accepts PCM16 mono at 8/16/32/48 kHz in 10/20/30 ms frames.
    Other AudioSocket rates are resampled here only. Buffer less than 10 ms;
    bound each observation to 120 ms of work. Missing/failed evidence is unknown,
    never a negative speech verdict.
    """

    def __init__(self) -> None:
        self.available = webrtcvad is not None
        self._vad = None
        self._input_rate: Optional[int] = None
        self._resample_state = None
        self._pending = b""

    def pause(self) -> None:
        """Do not join partial frames or detector hangover across output gating."""
        self._vad = None
        self._input_rate = None
        self._resample_state = None
        self._pending = b""

    def observe(self, pcm16: bytes, rate: int) -> Optional[bool]:
        if not self.available:
            return None
        try:
            if rate <= 0 or len(pcm16) % 2 or len(pcm16) > rate * 2 * 120 // 1000:
                self.pause()
                return None
            if rate != self._input_rate:
                self.pause()
                self._input_rate = rate
                self._vad = webrtcvad.Vad(2)
            detector_rate = rate if rate in (8000, 16000, 32000, 48000) else 16000
            if detector_rate != rate:
                pcm16, self._resample_state = audioop.ratecv(
                    pcm16, 2, 1, rate, detector_rate, self._resample_state
                )
            data = self._pending + pcm16
            frame_bytes = detector_rate * 2 // 100  # Exactly 10 ms.
            complete_bytes = len(data) // frame_bytes * frame_bytes
            self._pending = data[complete_bytes:]
            voiced = False
            for start in range(0, complete_bytes, frame_bytes):
                # Consume every frame even after a positive result: VAD is stateful.
                positive = self._vad.is_speech(data[start:start + frame_bytes], detector_rate)
                voiced = voiced or positive
            if voiced:
                return True
            # A resampler can leave a fractional frame on every packet. Retain
            # that tail, but still use evidence from the complete real frames.
            return False if complete_bytes else None
        except Exception:
            self.available = False
            self.pause()
            return None
