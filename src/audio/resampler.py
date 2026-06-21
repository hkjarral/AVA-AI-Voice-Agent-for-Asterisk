"""
Audio resampling and format conversion helpers.

These utilities provide common conversions required when bridging between
provider audio formats (OpenAI Realtime PCM16 @ 24 kHz, Google Live PCM16 @
24 kHz, etc.) and the AudioSocket expectations (typically μ-law or PCM16 at
8 kHz).

``resample_audio`` does two things when downsampling:
  1. Applies a streaming-safe FIR lowpass (anti-aliasing) filter so that
     high-frequency content above the new Nyquist (target_rate / 2) is
     attenuated before decimation. Without this, sibilant/fricative energy
     (roughly 4-9 kHz) in 24 kHz TTS output folds back into the audible band
     when decimated straight to 8 kHz, producing a scratchy/fuzzy artifact
     on "s"/"sh" sounds.
  2. Performs the original numpy linear interpolation with exact
     ``arange * step`` positioning and 1-sample state carry so that chunk
     boundaries are interpolated correctly.

When upsampling or when source_rate == target_rate, the lowpass stage is
skipped entirely (no aliasing risk in that direction for this project's use
case) and behavior is identical to before.
"""
from __future__ import annotations

import audioop
import numpy as np
from typing import Dict, Optional, Tuple

# Default sample width for PCM16 little-endian audio
_PCM_SAMPLE_WIDTH = 2

# Cache of designed FIR lowpass kernels keyed by (source_rate, target_rate)
# so we don't recompute the sinc/window design on every chunk.
_FIR_KERNEL_CACHE: Dict[Tuple[int, int], np.ndarray] = {}

# Number of taps for the anti-aliasing FIR. Odd length -> symmetric kernel
# with a clean integer group delay of (num_taps - 1) / 2 samples at the
# source rate. 63 taps gives a reasonably sharp rolloff with negligible
# CPU cost per chunk and ~1.3ms of added latency at 24kHz (inaudible/
# irrelevant for telephony jitter budgets).
_FIR_NUM_TAPS = 31


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


def _design_lowpass_kernel(source_rate: int, target_rate: int, num_taps: int = _FIR_NUM_TAPS) -> np.ndarray:
    """
    Design a windowed-sinc FIR lowpass filter for anti-aliasing before
    decimating from source_rate to target_rate.

    Cutoff is placed at 90% of the new Nyquist (target_rate / 2) to leave a
    guard band before the new Nyquist, expressed as a fraction of the
    *source* Nyquist for use directly against samples at source_rate.
    """
    new_nyquist_hz = target_rate / 2.0
    cutoff_hz = new_nyquist_hz * 0.9
    normalized_cutoff = cutoff_hz / (source_rate / 2.0)  # fraction of source Nyquist, 0..1
    normalized_cutoff = min(max(normalized_cutoff, 1e-6), 0.999)

    n = np.arange(num_taps, dtype=np.float64) - (num_taps - 1) / 2.0
    h = normalized_cutoff * np.sinc(normalized_cutoff * n)
    window = np.blackman(num_taps)
    h *= window
    h_sum = np.sum(h)
    if h_sum != 0:
        h /= h_sum  # unity DC gain
    return h.astype(np.float64)


def resample_audio(
    pcm_bytes: bytes,
    source_rate: int,
    target_rate: int,
    *,
    sample_width: int = _PCM_SAMPLE_WIDTH,
    channels: int = 1,
    state: Optional[tuple] = None,
) -> Tuple[bytes, Optional[tuple]]:
    """
    Resample PCM audio between sample rates.
    Mono-only / PCM16-only: the NumPy interpolation implementation assumes
    single-channel 16-bit little-endian samples. The ``sample_width`` and
    ``channels`` parameters exist only to document and enforce that
    assumption — passing anything else raises ``ValueError`` rather than
    silently producing wrong audio (e.g. interleaved-stereo corruption).

    When downsampling (target_rate < source_rate), a streaming FIR lowpass
    is applied first to prevent aliasing, then a phase-locked linear
    interpolation performs the actual rate conversion.

    The state tuple carries ``(phase, last_sample, fir_tail_or_None)``.
    ``phase`` is the fractional input-sample position (relative to the
    start of the *next* chunk) of the next output sample to be produced.
    Carrying it forward exactly -- using the true, un-rounded
    source/target rate ratio on every chunk, rather than a step recomputed
    from that chunk's own rounded sample count -- keeps the decimation
    grid phase-locked across chunk boundaries no matter how the caller
    slices audio into chunks (the previous implementation re-derived step
    per chunk, which silently shifted the grid at every boundary whenever
    a chunk size wasn't an exact multiple of the rate ratio -- audible as
    a click/pop at every chunk boundary). ``last_sample`` is the final
    post-filter sample of the previous chunk, used as a single sample of
    lookback when ``phase`` is slightly negative (the next output falls
    just before the new chunk starts). ``fir_tail`` holds the last
    ``num_taps - 1`` pre-filter samples from the previous chunk so the FIR
    filter itself is continuous across chunk boundaries; it is
    ``None``/unused when not downsampling.
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
    if not pcm_bytes or source_rate == target_rate:
        return pcm_bytes, state
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
    n_in = len(audio)
    if n_in == 0:
        return b"", state
    is_downsample = target_rate < source_rate
    prev_phase: float = 0.0
    prev_last: Optional[float] = None
    prev_fir_tail: Optional[np.ndarray] = None
    if state is not None and isinstance(state, tuple) and len(state) > 0:
        try:
            prev_phase = float(state[0])
        except (TypeError, ValueError, IndexError):
            prev_phase = 0.0
        if len(state) > 1 and state[1] is not None:
            try:
                prev_last = float(state[1])
            except (TypeError, ValueError):
                prev_last = None
        if len(state) > 2 and state[2] is not None:
            prev_fir_tail = state[2]
    new_fir_tail: Optional[np.ndarray] = None
    if is_downsample:
        kernel = _FIR_KERNEL_CACHE.get((source_rate, target_rate))
        if kernel is None:
            kernel = _design_lowpass_kernel(source_rate, target_rate)
            _FIR_KERNEL_CACHE[(source_rate, target_rate)] = kernel
        num_taps = len(kernel)
        history_len = num_taps - 1
        if prev_fir_tail is not None and len(prev_fir_tail) == history_len:
            history = prev_fir_tail
        else:
            history = np.zeros(history_len, dtype=np.float64)
        extended_for_filter = np.concatenate([history, audio])
        # 'valid' mode with (num_taps - 1) samples of history prepended
        # yields exactly n_in output samples, correctly centered/delayed
        # for this symmetric kernel, continuous across chunk boundaries.
        filtered = np.convolve(extended_for_filter, kernel, mode="valid")
        new_fir_tail = extended_for_filter[-history_len:] if history_len > 0 else np.zeros(0, dtype=np.float64)
        working = filtered
    else:
        working = audio
    n_work = len(working)
    # Exact (un-rounded) input-samples-per-output-sample ratio, fixed for
    # the life of the call -- see docstring for why this matters.
    step = float(source_rate) / float(target_rate)
    if not (-1.0 <= prev_phase < step + 1.0):
        # Defensive clamp; shouldn't occur in normal operation.
        prev_phase = 0.0
    lookback = prev_last if prev_last is not None else (float(working[0]) if n_work > 0 else 0.0)
    extended = np.empty(n_work + 1, dtype=np.float64)
    extended[0] = lookback
    extended[1:] = working
    # extended index 0 = lookback (local position -1); index i (i>=1) =
    # working[i-1] (local position i-1). So local position p -> index p+1.
    if n_work - 1 - prev_phase < 0:
        n_out = 0
    else:
        n_out = int(np.floor((n_work - 1 - prev_phase) / step)) + 1
    n_out = max(n_out, 0)
    if n_out == 0:
        new_phase = prev_phase - n_work
        new_last = float(working[-1]) if n_work > 0 else lookback
        new_state: Optional[tuple] = (new_phase, new_last, new_fir_tail)
        return b"", new_state
    local_pos = prev_phase + np.arange(n_out, dtype=np.float64) * step
    out_pos = local_pos + 1.0
    resampled = np.interp(out_pos, np.arange(n_work + 1, dtype=np.float64), extended)
    next_local_pos = prev_phase + n_out * step
    new_phase = next_local_pos - n_work
    new_last = float(working[-1])
    new_state = (new_phase, new_last, new_fir_tail)
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
