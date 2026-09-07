"""Codec helpers for Asterisk Media WebSocket raw payloads.

The driver sends raw codec bytes, without RTP or AudioSocket headers.  Linear
formats are already signed, little-endian PCM16; companded formats are decoded
with the standard-library conversion routines.
"""

from __future__ import annotations

import audioop
from typing import Final

SUPPORTED_WIRE_CODECS: Final[frozenset[str]] = frozenset({"ulaw", "alaw", "slin", "slin16"})
_ALIASES: Final[dict[str, str]] = {
    "ulaw": "ulaw",
    "mulaw": "ulaw",
    "mu-law": "ulaw",
    "alaw": "alaw",
    "a-law": "alaw",
    "slin": "slin",
    "slin8": "slin",
    "slin16": "slin16",
}
_SAMPLE_RATES: Final[dict[str, int]] = {
    "ulaw": 8000,
    "alaw": 8000,
    "slin": 8000,
    "slin16": 16000,
}


def canonical_wire_codec(codec: str) -> str:
    """Return a supported canonical codec name or raise ``ValueError``."""
    try:
        return _ALIASES[str(codec).strip().lower()]
    except (KeyError, AttributeError) as exc:
        raise ValueError(f"unsupported Asterisk WebSocket codec: {codec!r}") from exc


def sample_rate_for_codec(codec: str) -> int:
    return _SAMPLE_RATES[canonical_wire_codec(codec)]


def bytes_per_frame(codec: str, ptime_ms: int) -> int:
    """Expected complete-frame size for an integer-millisecond packet time."""
    if not isinstance(ptime_ms, int) or not 1 <= ptime_ms <= 120:
        raise ValueError("ptime must be an integer from 1 through 120 ms")
    rate = sample_rate_for_codec(codec)
    if (rate * ptime_ms) % 1000:
        raise ValueError("ptime doesn't produce a whole number of samples")
    samples = rate * ptime_ms // 1000
    return samples if canonical_wire_codec(codec) in {"ulaw", "alaw"} else samples * 2


def _require_pcm16(payload: bytes) -> bytes:
    if len(payload) % 2:
        raise ValueError("signed-linear PCM16 payload must contain whole samples")
    return payload


def decode_wire_audio(payload: bytes, codec: str) -> bytes:
    """Decode raw wire audio into little-endian signed PCM16 bytes."""
    codec = canonical_wire_codec(codec)
    payload = bytes(payload)
    if codec == "ulaw":
        return audioop.ulaw2lin(payload, 2)
    if codec == "alaw":
        return audioop.alaw2lin(payload, 2)
    return _require_pcm16(payload)


def encode_wire_audio(pcm16le: bytes, codec: str) -> bytes:
    """Encode little-endian signed PCM16 bytes for an Asterisk wire codec."""
    codec = canonical_wire_codec(codec)
    pcm16le = _require_pcm16(bytes(pcm16le))
    if codec == "ulaw":
        return audioop.lin2ulaw(pcm16le, 2)
    if codec == "alaw":
        return audioop.lin2alaw(pcm16le, 2)
    return pcm16le
