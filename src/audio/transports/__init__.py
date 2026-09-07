"""Transport adapters for the call media boundary.

The package is intentionally independent from the engine so transports can be
started and tested without importing provider or ARI state.
"""

from .asterisk_websocket import WebSocketMediaServer, supports_media_websocket
from .base import (
    CallMediaRequest,
    CallMediaSetupResult,
    CompletionQuality,
    MediaBinding,
    SelectedTransportRuntime,
    TransportCapabilities,
)
from .codec import (
    SUPPORTED_WIRE_CODECS,
    canonical_wire_codec,
    decode_wire_audio,
    encode_wire_audio,
    sample_rate_for_codec,
)

__all__ = [
    "WebSocketMediaServer",
    "CallMediaRequest",
    "CallMediaSetupResult",
    "CompletionQuality",
    "MediaBinding",
    "SelectedTransportRuntime",
    "TransportCapabilities",
    "SUPPORTED_WIRE_CODECS",
    "canonical_wire_codec",
    "decode_wire_audio",
    "encode_wire_audio",
    "sample_rate_for_codec",
    "supports_media_websocket",
]
