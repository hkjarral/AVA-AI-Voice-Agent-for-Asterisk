"""
Audio resampling and format conversion helpers.

These utilities provide common conversions required when bridging between
provider audio formats (OpenAI Realtime PCM16 @ 24 kHz, etc.) and the
AudioSocket expectations (typically μ-law or PCM16 at 8 kHz).

The ``resample_audio`` function uses numpy linear interpolation with exact
``arange * step`` positioning and 1-sample state carry so that chunk
boundaries are interpolated correctly — zero crackling.
"""

from __future__ import annotations

import audioop
import numpy as np
from typing import Optional, Tuple

# Default sample width for PCM16 little-endian audio
_PCM_SAMPLE_WIDTH = 2


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
) -> Tuple[bytes, Optional[tuple]]:
    # NOTE: sample_width and channels are kept for API compatibility
    # but are not used by the NumPy interpolation implementation.
    """
    Resample PCM audio between sample rates.

    Uses numpy linear interpolation with exact ``arange * step`` positioning
    and 1-sample state carry for seamless chunk-by-chunk streaming.

    The state tuple carries ``(prev_last_sample_float,)`` so that the
    boundary between consecutive chunks is interpolated correctly.

    Returns a tuple of (converted_bytes, new_state).
    """
    if not pcm_bytes or source_rate == target_rate:
        return pcm_bytes, state

    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
    n_in = len(audio)
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