"""Wire-output delegates; conversion, pacing and local drain remain in SPM.

Legacy finish/abort operations acknowledge local handoff/invalidation only.
They never claim the remote queue acknowledgments available with WebSocket.
"""
from __future__ import annotations

import audioop
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .base import CompletionQuality

from src.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class OutputFrame:
    call_id: str
    payload: bytes
    encoding: str
    sample_rate: int
    connection_id: Optional[str] = None
    ssrc: Optional[int] = None
    generation: Optional[int] = None


class MediaOutputTransport:
    """Thin common wire boundary, with late-bound server projections."""

    completion_quality = CompletionQuality.QUIET_TAIL
    supports_remote_flush = False

    def __init__(self, server: Callable[[], Any]):
        self._server = server

    async def begin_output(self, call_id: str) -> int:
        return 0  # Legacy segment ownership is the SPM stream ID.

    async def write(self, frame: OutputFrame) -> bool:
        raise NotImplementedError

    async def finish_output(self, call_id: str, generation: int) -> bool:
        # SPM's local queue drain and quiet-tail policy remain authoritative.
        return True

    async def abort_output(self, call_id: str) -> bool:
        # SPM discards its queues; legacy transports have no remote FLUSH.
        return True


class RTPOutputTransport(MediaOutputTransport):
    async def write(self, frame: OutputFrame) -> bool:
        server = self._server()
        if server is None:
            return False
        payload = frame.payload
        if frame.encoding.lower() in {"slin", "slin16", "pcm16", "linear16"} and payload:
            try:
                payload = audioop.byteswap(payload, 2)
            except Exception:
                # Preserve the existing RTP sender's compatibility behavior.
                logger.warning("RTP byte-swap failed; sending original frame", call_id=frame.call_id)
        return bool(await server.send_audio(frame.call_id, payload, ssrc=frame.ssrc))


class AudioSocketOutputTransport(MediaOutputTransport):
    async def write(self, frame: OutputFrame) -> bool:
        server = self._server()
        if server is None or not frame.connection_id:
            return False
        return bool(await server.send_audio(
            frame.connection_id, frame.payload,
            encoding=frame.encoding, sample_rate=frame.sample_rate,
        ))


class WebSocketOutputTransport(MediaOutputTransport):
    completion_quality = CompletionQuality.CORRELATED_BOUNDARY
    supports_remote_flush = True

    async def begin_output(self, call_id: str) -> int:
        server = self._server()
        if server is None:
            raise RuntimeError("WebSocket output transport is unavailable")
        return await server.begin_output(call_id)

    async def write(self, frame: OutputFrame) -> bool:
        server = self._server()
        if server is None or frame.generation is None:
            return False
        # No RTP header, AudioSocket type byte, or network-order PCM swap.
        return bool(await server.send_audio(frame.call_id, frame.payload, frame.generation))

    async def finish_output(self, call_id: str, generation: int) -> bool:
        server = self._server()
        return bool(server and await server.finish_output(call_id, generation))

    async def abort_output(self, call_id: str) -> bool:
        server = self._server()
        return bool(server and await server.abort_output(call_id))


def create_output_transport(kind: str, server: Callable[[], Any]) -> MediaOutputTransport:
    adapters = {
        "externalmedia": RTPOutputTransport,
        "audiosocket": AudioSocketOutputTransport,
        "websocket": WebSocketOutputTransport,
    }
    try:
        adapter = adapters[kind]
    except KeyError as exc:
        raise ValueError(f"Unsupported audio transport: {kind}") from exc
    return adapter(server)
