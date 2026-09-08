"""Factory for the one configured inbound call-media listener."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from .base import SelectedTransportRuntime
from .legacy import AudioSocketRuntime, ExternalMediaRuntime, WebSocketRuntime


@dataclass(frozen=True)
class TransportCallbacks:
    on_rtp_pcm: Callable[[str, int, bytes], Awaitable[None]]
    on_externalmedia_aux: Callable[..., Awaitable[None]]
    on_audiosocket_uuid: Callable[..., Awaitable[bool]]
    on_audiosocket_audio: Callable[..., Awaitable[None]]
    on_audiosocket_disconnect: Callable[..., Awaitable[None]]
    on_audiosocket_dtmf: Callable[..., Awaitable[None]]
    on_audiosocket_aux: Callable[..., Awaitable[None]]
    on_websocket_audio: Callable[..., Awaitable[None]]
    on_websocket_disconnect: Callable[..., Awaitable[None]]
    on_websocket_dtmf: Callable[..., Awaitable[None]]
    on_websocket_aux: Callable[..., Awaitable[None]]
    asterisk_version: Optional[Callable[[], Optional[str]]] = None


def create_selected_transport_runtime(
    config: Any,
    callbacks: TransportCallbacks,
    *,
    rtp_port_range: Optional[tuple[int, int]] = None,
    rtp_allowed_remote_hosts: Optional[list[str]] = None,
) -> SelectedTransportRuntime:
    """Construct, but do not bind, the configured selected transport."""

    kind = str(getattr(config, "audio_transport", "") or "").lower()
    if kind in {"externalmedia", "rtp"}:
        section = getattr(config, "external_media", None)
        port = int(getattr(section, "rtp_port", 0) or 18080)
        return ExternalMediaRuntime(
            config,
            on_pcm=callbacks.on_rtp_pcm,
            on_aux=callbacks.on_externalmedia_aux,
            port_range=rtp_port_range or (port, port),
            allowed_remote_hosts=rtp_allowed_remote_hosts,
        )
    if kind == "audiosocket":
        return AudioSocketRuntime(
            config,
            on_uuid=callbacks.on_audiosocket_uuid,
            on_audio=callbacks.on_audiosocket_audio,
            on_disconnect=callbacks.on_audiosocket_disconnect,
            on_dtmf=callbacks.on_audiosocket_dtmf,
            on_aux=callbacks.on_audiosocket_aux,
        )
    if kind == "websocket":
        return WebSocketRuntime(
            config,
            on_audio=callbacks.on_websocket_audio,
            on_disconnect=callbacks.on_websocket_disconnect,
            on_dtmf=callbacks.on_websocket_dtmf,
            on_aux=callbacks.on_websocket_aux,
            asterisk_version=callbacks.asterisk_version,
        )
    raise ValueError(f"Unsupported audio transport: {kind or '<empty>'}")
