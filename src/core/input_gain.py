"""Call-owned PCM input gain; no logging or transport side effects."""

import audioop
import math
from dataclasses import dataclass

import numpy as np


@dataclass
class InputGainState:
    provider_name: str
    target_rms: int
    max_gain_db: float
    noise_floor_rms: int = 200
    level: float = 0.0
    gain: float = 1.0
    frames: int = 0
    below_floor_frames: int = 0
    excessive_frames: int = 0
    excessive_seconds: float = 0.0
    active_seconds: float = 0.0
    active_energy: float = 0.0
    warned: bool = False

    def process(self, pcm: bytes, sample_rate: int) -> bytes:
        if not pcm:
            return pcm
        samples = np.frombuffer(pcm, dtype='<i2')
        duration = len(samples) / sample_rate
        rms = audioop.rms(pcm, 2)
        self.frames += 1
        # A new call starts at unity. Quiet speech raises gain gradually rather
        # than receiving maximum boost in its first frame.
        if not self.level:
            self.level = float(max(self.target_rms, rms))
        if rms <= self.noise_floor_rms:
            self.below_floor_frames += 1
            desired = 1.0
            # Hold the speech envelope during gaps so noise cannot drive gain up.
        else:
            self.active_seconds += duration
            self.active_energy += rms * rms * duration
            # Fast response to louder speech; slow recovery towards quiet speech.
            tau = 0.010 if rms > self.level else 0.500
            self.level = rms + (self.level - rms) * math.exp(-duration / tau)
            desired = max(1.0, min(self.target_rms / self.level,
                                   10 ** (self.max_gain_db / 20.0)))
        # Exponential sample ramp avoids frame-boundary gain steps. Time
        # constants use audio duration, independent of the negotiated sample rate.
        tau = 0.010 if desired < self.gain else 0.050
        ramp = desired + (self.gain - desired) * np.exp(
            -np.arange(1, len(samples) + 1) / (sample_rate * tau)
        )
        # Loud transients must not retain the preceding quiet-speech boost.
        # Headroom limiting takes precedence over smoothing at these boundaries.
        peak = int(np.max(np.abs(samples.astype(np.int32))))
        if peak:
            ramp = np.minimum(ramp, max(1.0, 32767.0 / peak))
        self.gain = float(ramp[-1])
        excessive_samples = int(np.count_nonzero(ramp > 10.0))
        if rms > self.noise_floor_rms and excessive_samples:
            self.excessive_frames += 1
            self.excessive_seconds += excessive_samples / sample_rate
        # Preserve PCM16's saturating behavior; never wrap peaks across zero.
        return np.clip(samples * ramp, -32768, 32767).astype('<i2').tobytes()

    def summary(self) -> dict:
        return {
            'frames': self.frames,
            'below_floor_frames': self.below_floor_frames,
            'excessive_gain_frames': self.excessive_frames,
            'excessive_gain_seconds': round(self.excessive_seconds, 3),
            'active_audio_rms': round(math.sqrt(self.active_energy / self.active_seconds))
            if self.active_seconds else 0,
            'noise_floor_rms': self.noise_floor_rms,
        }
