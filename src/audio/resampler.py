"""
Audio resampling and format conversion helpers.

These utilities provide common conversions required when bridging between
provider audio formats (OpenAI Realtime PCM16 @ 24 kHz, etc.) and the
AudioSocket expectations (typically μ-law or PCM16 at 8 kHz).

The ``resample_audio`` function defaults to numpy linear interpolation with
exact ``arange * step`` positioning and 1-sample state carry so that chunk
boundaries are interpolated correctly — zero crackling. An opt-in
``bandlimited`` mode provides alias-safe integer downsampling for provider
audio that is produced above the telephony sample rate.
"""

from __future__ import annotations

import audioop
import numpy as np
from functools import lru_cache
from typing import Optional, Tuple

# Default sample width for PCM16 little-endian audio
_PCM_SAMPLE_WIDTH = 2
_BANDLIMITED_STATE_TAG = "bandlimited_fir_v1"
_BANDLIMITED_FILTER_TAPS = 255
_BANDLIMITED_CUTOFF_FRACTION_OF_TARGET = 0.45


@lru_cache(maxsize=16)
def _bandlimited_downsample_filter(source_rate: int, target_rate: int) -> np.ndarray:
    """Return a unity-gain Blackman-windowed low-pass FIR for downsampling."""
    cutoff_hz = float(target_rate) * _BANDLIMITED_CUTOFF_FRACTION_OF_TARGET
    cutoff_cycles_per_sample = cutoff_hz / float(source_rate)
    center = (_BANDLIMITED_FILTER_TAPS - 1) / 2.0
    positions = np.arange(_BANDLIMITED_FILTER_TAPS, dtype=np.float64) - center
    taps = 2.0 * cutoff_cycles_per_sample * np.sinc(
        2.0 * cutoff_cycles_per_sample * positions
    )
    taps *= np.blackman(_BANDLIMITED_FILTER_TAPS)
    taps /= np.sum(taps)
    taps.setflags(write=False)
    return taps


def _resample_bandlimited_integer_downsample(
    audio: np.ndarray,
    source_rate: int,
    target_rate: int,
    state: Optional[tuple],
) -> Tuple[bytes, tuple]:
    """Stateful low-pass FIR followed by integer decimation."""
    factor = source_rate // target_rate
    taps = _bandlimited_downsample_filter(source_rate, target_rate)
    history_size = len(taps) - 1
    history = np.zeros(history_size, dtype=np.float64)
    phase = 0

    if (
        isinstance(state, tuple)
        and len(state) == 5
        and state[0] == _BANDLIMITED_STATE_TAG
        and state[1] == source_rate
        and state[2] == target_rate
    ):
        try:
            candidate_history = np.asarray(state[4], dtype=np.float64)
            if candidate_history.shape == (history_size,):
                history = candidate_history
                phase = int(state[3]) % factor
        except (TypeError, ValueError):
            history = np.zeros(history_size, dtype=np.float64)
            phase = 0

    extended = np.concatenate((history, audio))
    # The symmetric FIR makes np.convolve's coefficient reversal immaterial.
    filtered = np.convolve(extended, taps, mode="valid")
    start = (-phase) % factor
    resampled = filtered[start::factor]
    new_phase = (phase + len(audio)) % factor
    new_history = extended[-history_size:].copy()
    new_state = (
        _BANDLIMITED_STATE_TAG,
        source_rate,
        target_rate,
        new_phase,
        new_history,
    )
    resampled = np.clip(np.rint(resampled), -32768, 32767).astype(np.int16)
    return resampled.tobytes(), new_state


def mulaw_to_pcm16le(data: bytes) -> bytes:
    """
    Convert μ-law audio data (8-bit) to PCM16 little-endian samples.
    """
    if not data:
        return b""
    return audioop.ulaw2lin(data, _PCM_SAMPLE_WIDTH)


def pcm16le_to_mulaw(data: bytes) -> bytes:
    """
    Convert PCM16 little-endian samples to μ-law (8-bit) encoding.
    """
    if not data:
        return b""
    return audioop.lin2ulaw(data, _PCM_SAMPLE_WIDTH)


def resample_audio(
    pcm_bytes: bytes,
    source_rate: int,
    target_rate: int,
    *,
    sample_width: int = _PCM_SAMPLE_WIDTH,
    channels: int = 1,
    state: Optional[tuple] = None,
    mode: str = "linear",
) -> Tuple[bytes, Optional[tuple]]:
    """
    Resample PCM audio between sample rates.

    Mono-only / PCM16-only: the NumPy interpolation implementation assumes
    single-channel 16-bit little-endian samples. The ``sample_width`` and
    ``channels`` parameters exist only to document and enforce that
    assumption — passing anything else raises ``ValueError`` rather than
    silently producing wrong audio (e.g. interleaved-stereo corruption).

    ``mode="linear"`` uses numpy linear interpolation with exact
    ``arange * step`` positioning and 1-sample state carry for seamless
    chunk-by-chunk streaming. This remains the default for compatibility.

    ``mode="bandlimited"`` applies a stateful low-pass FIR before integer
    downsampling. Non-integer ratios and upsampling retain the legacy linear
    behavior so this experimental mode cannot disrupt unsupported paths.

    The state tuple carries ``(prev_last_sample_float,)`` so that the
    boundary between consecutive chunks is interpolated correctly.

    Note: state does not track fractional phase, so non-integer rate ratios
    (e.g. 24 k→8 k) with variable chunk sizes may accumulate sample-count
    drift over very long streams.  All production ratios in this project are
    integer multiples (8 k↔16 k = 2:1) where this is not a concern.

    Returns a tuple of (converted_bytes, new_state).
    """
    if channels != 1:
        raise ValueError(
            f"resample_audio is mono-only (channels=1); got channels={channels}. "
            "The NumPy interpolation does not de-interleave; resample each "
            "channel separately or downmix to mono first."
        )
    if sample_width != _PCM_SAMPLE_WIDTH:
        raise ValueError(
            f"resample_audio is PCM16-only (sample_width={_PCM_SAMPLE_WIDTH}); "
            f"got sample_width={sample_width}."
        )
    normalized_mode = str(mode or "linear").strip().lower()
    if normalized_mode not in ("linear", "bandlimited"):
        raise ValueError(
            f"Unsupported resample mode {mode!r}; expected 'linear' or 'bandlimited'."
        )
    if not pcm_bytes or source_rate == target_rate:
        return pcm_bytes, state

    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
    n_in = len(audio)
    if (
        normalized_mode == "bandlimited"
        and source_rate > target_rate
        and source_rate % target_rate == 0
    ):
        return _resample_bandlimited_integer_downsample(
            audio, source_rate, target_rate, state
        )

    n_out = int(round(n_in * target_rate / source_rate))
    if n_out == 0:
        return b"", state

    # Recover last input sample from previous chunk
    prev_last: Optional[float] = None
    if state is not None:
        try:
            if isinstance(state, tuple) and len(state) > 0:
                prev_last = float(state[0])
        except (TypeError, ValueError, IndexError):
            prev_last = None

    # Exact step between output samples in input-sample units.
    # For 2× upsampling (8 k→16 k): step = 0.5 exactly.
    step = float(n_in) / float(n_out)

    if prev_last is not None:
        # Prepend previous chunk's last sample for boundary interpolation.
        #   extended[0] = prev_last  (position −1 relative to current chunk)
        #   extended[1] = audio[0]   (position 0)
        #   extended[k] = audio[k-1]
        #
        # Output positions (extended-index space):
        #   1*step, 2*step, …, n_out*step
        # e.g. for 2×: 0.5, 1.0, 1.5, …, 160.0
        #   pos 0.5 → (prev_last + audio[0]) / 2  ← smooth boundary
        #   pos 1.0 → audio[0]
        extended = np.empty(n_in + 1, dtype=np.float64)
        extended[0] = prev_last
        extended[1:] = audio
        out_pos = np.arange(1, n_out + 1, dtype=np.float64) * step
        resampled = np.interp(out_pos, np.arange(n_in + 1, dtype=np.float64), extended)
    else:
        # First chunk — no history.
        out_pos = np.arange(n_out, dtype=np.float64) * step
        resampled = np.interp(out_pos, np.arange(n_in, dtype=np.float64), audio)

    new_state: Optional[tuple] = (float(audio[-1]),)
    resampled = np.clip(resampled, -32768, 32767).astype(np.int16)
    return resampled.tobytes(), new_state


def convert_pcm16le_to_target_format(pcm_bytes: bytes, target_format: str) -> bytes:
    """
    Convert PCM16 little-endian audio into the target encoding.

    Currently supports μ-law and PCM16 (no-op for PCM targets).
    """
    if not pcm_bytes:
        return b""

    fmt = (target_format or "").lower()
    if fmt in ("ulaw", "mulaw", "mu-law"):
        return pcm16le_to_mulaw(pcm_bytes)
    # Default: assume PCM target
    return pcm_bytes
