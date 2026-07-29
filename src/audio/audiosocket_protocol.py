"""AudioSocket wire-format helpers.

Asterisk identifies signed-linear sample rates with distinct TLV message types.
Keeping this mapping in one module prevents the channel codec, inbound decoder,
and outbound frame writer from drifting apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AudioSocketAudioFrame:
    """One decoded AudioSocket audio frame."""

    payload: bytes
    message_type: int
    encoding: str
    sample_rate: int


AUDIO_TYPE_TO_FORMAT: dict[int, tuple[str, int]] = {
    0x10: ("slin", 8000),
    0x11: ("slin12", 12000),
    0x12: ("slin16", 16000),
    0x13: ("slin24", 24000),
    0x14: ("slin32", 32000),
    0x15: ("slin44", 44100),
    0x16: ("slin48", 48000),
    0x17: ("slin96", 96000),
    0x18: ("slin192", 192000),
}
FORMAT_TO_AUDIO_TYPE: dict[tuple[str, int], int] = {
    value: message_type for message_type, value in AUDIO_TYPE_TO_FORMAT.items()
}

_RATE_TO_ENCODING = {rate: encoding for encoding, rate in FORMAT_TO_AUDIO_TYPE}
_ENCODING_ALIASES = {
    "slin8": "slin",
}


def normalize_slin_format(encoding: str, sample_rate: Optional[int] = None) -> tuple[str, int]:
    """Return the canonical AudioSocket signed-linear format and rate.

    Generic PCM aliases use ``sample_rate`` to select the correct AudioSocket
    message type. Explicit ``slinNN`` names must agree with the supplied rate.
    """

    token = str(encoding or "").strip().lower().replace("_", "").replace("-", "")
    requested_rate = int(sample_rate) if sample_rate is not None else None

    # linear16/pcm16 describe the sample width, not a 16 kHz rate. Resolve
    # them with the accompanying rate; retain 16 kHz as the legacy no-rate
    # interpretation used by existing provider configuration.
    if token in {"linear16", "pcm16"} and requested_rate in _RATE_TO_ENCODING:
        token = _RATE_TO_ENCODING[requested_rate]
    elif token in {"linear16", "pcm16"}:
        token = "slin16"
    elif token in {"linear", "pcm"} and requested_rate in _RATE_TO_ENCODING:
        token = _RATE_TO_ENCODING[requested_rate]
    elif token in {"linear", "pcm"}:
        token = "slin"
    else:
        token = _ENCODING_ALIASES.get(token, token)

    inferred_rate = next(
        (rate for candidate, rate in FORMAT_TO_AUDIO_TYPE if candidate == token),
        None,
    )
    if inferred_rate is None:
        raise ValueError(f"Unsupported AudioSocket encoding: {encoding!r}")
    if requested_rate is not None and requested_rate != inferred_rate:
        raise ValueError(
            f"AudioSocket encoding {token} requires {inferred_rate} Hz, got {requested_rate} Hz"
        )
    return token, inferred_rate


def audio_message_type(encoding: str, sample_rate: Optional[int] = None) -> int:
    """Resolve an AudioSocket audio message type for a signed-linear format."""

    canonical = normalize_slin_format(encoding, sample_rate)
    return FORMAT_TO_AUDIO_TYPE[canonical]


def supports_multirate_audiosocket(version: Optional[str]) -> bool:
    """Whether an upstream Asterisk version supports AudioSocket types 0x11-0x18.

    Multi-rate support first shipped in Asterisk 20.17, 21.12, 22.7, and 23.1.
    Unknown versions fail closed so selecting a wideband profile cannot silently
    produce one-way or corrupt audio on an older server.
    """

    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.\d+)?", str(version or ""))
    if not match:
        return False
    major, minor = (int(part) for part in match.groups())
    minimum_minor = {20: 17, 21: 12, 22: 7, 23: 1}
    if major >= 24:
        return True
    return major in minimum_minor and minor >= minimum_minor[major]
