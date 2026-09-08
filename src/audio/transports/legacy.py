"""Thin lifecycle adapters for existing call-media implementations.

These wrappers intentionally do not reinterpret frames, own calls, or replace
the mature RTP/AudioSocket/WebSocket protocol implementations.  They expose a
common selected-listener lifecycle and capability description to the engine.
"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable, Mapping, Optional

from ...rtp_server import RTPServer
from ..audiosocket_protocol import normalize_slin_format
from ..audiosocket_server import AudioSocketServer
from .asterisk_websocket import WebSocketMediaServer, supports_media_websocket
from .base import CallMediaRequest, CompletionQuality, TransportCapabilities
from .identity import new_websocket_channel_id
from src.media_transport_capabilities import resolve_media_websocket_control


class ExternalMediaRuntime:
    kind = "externalmedia"
    force_cleanup_on_prepare_failure = False
    capabilities = TransportCapabilities(
        completion_quality=CompletionQuality.QUIET_TAIL,
    )

    def __init__(
        self,
        config: Any,
        *,
        on_pcm: Callable[[str, int, bytes], Awaitable[None]],
        on_aux: Callable[..., Awaitable[None]],
        port_range: tuple[int, int],
        allowed_remote_hosts: Optional[list[str]],
    ) -> None:
        section = config.external_media
        if section is None:
            raise ValueError("ExternalMedia configuration not found")
        self._config = config
        self._section = section
        self._on_aux = on_aux
        port = int(getattr(section, "rtp_port", 0) or 18080)
        wire_format = getattr(section, "format", "slin16")
        sample_rate = getattr(section, "sample_rate", None)
        if not sample_rate:
            sample_rate = 16000 if wire_format in ("slin16", "linear16", "pcm16") else 8000
        self.server = RTPServer(
            host=section.rtp_host,
            port=port,
            engine_callback=on_pcm,
            codec=getattr(section, "codec", "ulaw"),
            format=wire_format,
            sample_rate=sample_rate,
            port_range=port_range,
            allowed_remote_hosts=allowed_remote_hosts,
            lock_remote_endpoint=bool(getattr(section, "lock_remote_endpoint", True)),
        )

    async def start(self) -> None:
        await self.server.start()

    async def stop(self) -> None:
        await self.server.stop()

    async def close_call(
        self, call_id: str, *, connection_id: str | None = None
    ) -> None:
        await self.server.cleanup_session(call_id)

    async def prepare_call(self, session: Any) -> CallMediaRequest:
        call_id = str(session.call_id)
        port = await self.server.allocate_session(call_id)
        bind_host = str(self._section.rtp_host)
        advertise_host = str(
            getattr(self._section, "advertise_host", None) or bind_host
        )
        if advertise_host in {"0.0.0.0", "::"}:
            advertise_host = "127.0.0.1"
        codec = str(getattr(self._section, "codec", "ulaw"))
        return CallMediaRequest(
            kind=self.kind,
            call_id=call_id,
            codec=codec,
            sample_rate=int(getattr(self.server, "sample_rate", 0) or 0),
            operation="external_media",
            ari_params={
                "app": self._config.asterisk.app_name,
                "external_host": f"{advertise_host}:{port}",
                "format": codec,
                "direction": getattr(self._section, "direction", "both"),
                "encapsulation": "rtp",
            },
            fail_without_bridge=False,
            start_failure_reason="external-media-start-failed",
            attach_failure_reason="external-media-attach-failed",
            setup_failure_reason="external-media-setup-failed",
            metadata={
                "port": port,
                "bind_host": bind_host,
                "advertise_host": advertise_host,
            },
        )

    async def create_call(
        self, owner: Any, session: Any, request: CallMediaRequest
    ) -> str | None:
        return await owner._start_external_media_channel(
            session.call_id, request=request
        )

    async def attach_call(
        self, owner: Any, session: Any, channel_id: str
    ) -> bool:
        return await owner._attach_external_media_channel_direct(session, channel_id)

    async def wait_call_ready(
        self, owner: Any, session: Any, request: CallMediaRequest
    ) -> None:
        return None

    async def finalize_call_ready(
        self, owner: Any, session: Any, request: CallMediaRequest, binding: Any
    ) -> bool:
        session.status = "external_media_created"
        await owner._save_session(session)
        return True

    def is_aux_channel(
        self, channel: Mapping[str, Any], *, known_channel_ids: set[str]
    ) -> bool:
        channel_id = str(channel.get("id") or "")
        return bool(
            channel_id in known_channel_ids
            or str(channel.get("name") or "").startswith("UnicastRTP/")
        )

    async def handle_aux_channel(
        self, channel_id: str, channel: Mapping[str, Any]
    ) -> None:
        await self._on_aux(channel_id, dict(channel))

    def ready(self, *, asterisk_version: str | None = None) -> bool:
        return bool(self.server and self.server.running)

    def health(self) -> dict[str, Any]:
        return self.server.get_stats()


class AudioSocketRuntime:
    kind = "audiosocket"
    force_cleanup_on_prepare_failure = True
    capabilities = TransportCapabilities(
        completion_quality=CompletionQuality.QUIET_TAIL,
    )

    def __init__(
        self,
        config: Any,
        *,
        on_uuid: Callable[..., Awaitable[bool]],
        on_audio: Callable[..., Awaitable[None]],
        on_disconnect: Callable[..., Awaitable[None]],
        on_dtmf: Callable[..., Awaitable[None]],
        on_aux: Callable[..., Awaitable[None]],
    ) -> None:
        section = config.audiosocket
        if section is None:
            raise ValueError("AudioSocket configuration not found")
        self._config = config
        self._section = section
        self._on_aux = on_aux
        self.server = AudioSocketServer(
            host=section.host,
            port=section.port,
            on_uuid=on_uuid,
            on_audio=on_audio,
            on_disconnect=on_disconnect,
            on_dtmf=on_dtmf,
        )
        self._started = False

    async def start(self) -> None:
        await self.server.start()
        self._started = True

    async def stop(self) -> None:
        try:
            await self.server.stop()
        finally:
            self._started = False

    async def close_call(
        self, call_id: str, *, connection_id: str | None = None
    ) -> None:
        if connection_id:
            await self.server.disconnect(connection_id)

    async def prepare_call(self, session: Any) -> CallMediaRequest:
        audio_uuid = str(uuid.uuid4())
        bind_host = str(getattr(self._section, "host", None) or "127.0.0.1")
        advertise_host = str(
            getattr(self._section, "advertise_host", None) or bind_host
        )
        if advertise_host in {"0.0.0.0", "::"}:
            advertise_host = "127.0.0.1"
        profile = getattr(session, "transport_profile", None)
        encoding = getattr(profile, "wire_encoding", None) or getattr(
            self._section, "format", "slin"
        )
        rate = getattr(profile, "wire_sample_rate", None)
        try:
            codec, sample_rate = normalize_slin_format(encoding, rate)
        except Exception:
            # AudioSocket only carries signed-linear; non-slin formats
            # (e.g. ``format: ulaw``) have always originated c(slin).
            codec, sample_rate = "slin", 8000
        endpoint = (
            f"AudioSocket/{advertise_host}:{self._section.port}/"
            f"{audio_uuid}/c({codec})"
        )
        return CallMediaRequest(
            kind=self.kind,
            call_id=str(session.call_id),
            codec=codec,
            sample_rate=sample_rate,
            operation="originate",
            ari_params={
                "endpoint": endpoint,
                "app": self._config.asterisk.app_name,
                "timeout": "30",
            },
            ari_data={"variables": {"AUDIOSOCKET_UUID": audio_uuid}},
            correlation_id=audio_uuid,
            attachment="aux_event",
            readiness="aux_event",
            force_cleanup_on_failure=True,
            start_failure_reason="audiosocket-media-start-failed",
            attach_failure_reason="audiosocket-attach-failed",
            setup_failure_reason="audiosocket-media-setup-failed",
        )

    async def create_call(
        self, owner: Any, session: Any, request: CallMediaRequest
    ) -> str | None:
        return await owner._originate_audiosocket_channel_hybrid(
            session.call_id, request=request
        )

    async def attach_call(
        self, owner: Any, session: Any, channel_id: str
    ) -> bool:
        return False

    async def wait_call_ready(
        self, owner: Any, session: Any, request: CallMediaRequest
    ) -> None:
        return None

    async def finalize_call_ready(
        self, owner: Any, session: Any, request: CallMediaRequest, binding: Any
    ) -> bool:
        return False

    def is_aux_channel(
        self, channel: Mapping[str, Any], *, known_channel_ids: set[str]
    ) -> bool:
        channel_id = str(channel.get("id") or "")
        return bool(
            channel_id in known_channel_ids
            or str(channel.get("name") or "").startswith("AudioSocket/")
        )

    async def handle_aux_channel(
        self, channel_id: str, channel: Mapping[str, Any]
    ) -> None:
        await self._on_aux(channel_id, dict(channel))

    def ready(self, *, asterisk_version: str | None = None) -> bool:
        return self._started

    def health(self) -> dict[str, Any]:
        return {
            "listening": self.ready(),
            "active_connections": self.server.get_connection_count(),
        }


class WebSocketRuntime:
    kind = "websocket"
    force_cleanup_on_prepare_failure = True
    capabilities = TransportCapabilities(
        completion_quality=CompletionQuality.CORRELATED_BOUNDARY,
        supports_remote_flush=True,
        supports_remote_flow_control=True,
        supports_per_call_codec=True,
    )

    def __init__(
        self,
        config: Any,
        *,
        on_audio: Callable[..., Awaitable[None]],
        on_disconnect: Callable[..., Awaitable[None]],
        on_dtmf: Callable[..., Awaitable[None]],
        on_aux: Callable[..., Awaitable[None]],
        asterisk_version: Optional[Callable[[], Optional[str]]] = None,
    ) -> None:
        if getattr(config, "websocket_media", None) is None:
            raise ValueError("WebSocket media configuration not found")
        self._config = config
        self._section = config.websocket_media
        self._on_aux = on_aux
        self._asterisk_version = asterisk_version
        self.server = WebSocketMediaServer(
            config,
            on_audio=on_audio,
            on_disconnect=on_disconnect,
            on_dtmf=on_dtmf,
        )

    async def start(self) -> None:
        await self.server.start()

    async def stop(self) -> None:
        await self.server.stop()

    async def close_call(
        self, call_id: str, *, connection_id: str | None = None
    ) -> None:
        await self.server.unregister_call(call_id)

    @staticmethod
    def _profile_codec(session: Any, fallback: str) -> str:
        profile = getattr(session, "transport_profile", None)
        encoding = str(getattr(profile, "wire_encoding", None) or fallback).lower()
        rate = int(getattr(profile, "wire_sample_rate", 0) or 0)
        aliases = {
            "mulaw": "ulaw",
            "mu-law": "ulaw",
            "g711_ulaw": "ulaw",
            "g711ulaw": "ulaw",
            "a-law": "alaw",
            "g711_alaw": "alaw",
            "g711alaw": "alaw",
            "linear16": "slin16" if rate >= 16000 else "slin",
            "pcm16": "slin16" if rate >= 16000 else "slin",
        }
        codec = aliases.get(encoding, encoding)
        if codec == "slin16" and rate and rate < 16000:
            codec = "slin"
        if codec not in {"ulaw", "alaw", "slin", "slin16"}:
            raise ValueError(f"Unsupported WebSocket media codec: {encoding}")
        return codec

    async def prepare_call(self, session: Any) -> CallMediaRequest:
        requested = getattr(self._section, "control_format", "json")
        # JSON-only fixtures may omit the version callback; application factory
        # always supplies it. Auto/plain cannot proceed without version evidence.
        version_source = getattr(self, "_asterisk_version", None)
        mode = resolve_media_websocket_control(version_source(), requested) if version_source else ("json" if requested == "json" else None)
        if mode is None:
            raise ValueError("WebSocket control format cannot be resolved for Asterisk version")
        call_id = str(session.call_id)
        codec = self._profile_codec(
            session,
            str(getattr(self._section, "fallback_format", "ulaw") or "ulaw"),
        )
        channel_id = new_websocket_channel_id()
        nonce = self.server.register_call(call_id, channel_id, codec, control_format=mode)
        client = str(getattr(self._section, "connection_name", "aava_media"))
        plain_params = {
            "endpoint": f"WebSocket/{client}/c({codec})v(nonce={nonce})",
            "app": self._config.asterisk.app_name,
            "channelId": channel_id,
            "formats": codec,
            "timeout": 10,
        }
        return CallMediaRequest(
            kind=self.kind,
            call_id=call_id,
            codec=codec,
            sample_rate=16000 if codec == "slin16" else 8000,
            operation="websocket_originate" if mode == "plain" else "external_media",
            ari_params=plain_params if mode == "plain" else {
                "app": self._config.asterisk.app_name,
                "external_host": str(
                    getattr(self._section, "connection_name", "aava_media")
                ),
                "format": codec,
                "direction": "both",
                "encapsulation": "none",
                "transport": "websocket",
                "connection_type": "client",
                "transport_data": f"f(json)v(nonce={nonce})",
                "channel_id": channel_id,
            },
            channel_id=channel_id,
            correlation_id=nonce,
            metadata={"control_format": mode},
            readiness="protocol",
            force_cleanup_on_failure=True,
            start_failure_reason="websocket-media-start-failed",
            attach_failure_reason="websocket-media-setup-failed",
            setup_failure_reason="websocket-media-setup-failed",
        )

    async def create_call(
        self, owner: Any, session: Any, request: CallMediaRequest
    ) -> str | None:
        return await owner._start_websocket_media_channel(session, request=request)

    async def attach_call(
        self, owner: Any, session: Any, channel_id: str
    ) -> bool:
        return await owner._attach_websocket_media_channel(session, channel_id)

    async def wait_call_ready(
        self, owner: Any, session: Any, request: CallMediaRequest
    ) -> Any:
        return True if await owner._await_websocket_media_ready(session) else None

    async def finalize_call_ready(
        self, owner: Any, session: Any, request: CallMediaRequest, binding: Any
    ) -> bool:
        bridge_id = session.bridge_id
        if (
            not await owner._websocket_setup_is_owned(session, str(request.channel_id), bridge_id)
            or session.media_connection_state != "ready"
        ):
            return False
        owner.pending_websocket_channels.pop(str(request.channel_id), None)
        session.status = "websocket_media_connected"
        await owner._save_session(session, require_current=True)
        return bool(
            await owner._websocket_setup_is_owned(session, str(request.channel_id), bridge_id)
            and session.media_connection_state == "ready"
        )

    def is_aux_channel(
        self, channel: Mapping[str, Any], *, known_channel_ids: set[str]
    ) -> bool:
        channel_id = str(channel.get("id") or "")
        name = str(channel.get("name") or "")
        return bool(
            channel_id in known_channel_ids
            or name.startswith("WebSocket/")
            or name.startswith("MediaWebSocket/")
        )

    async def handle_aux_channel(
        self, channel_id: str, channel: Mapping[str, Any]
    ) -> None:
        await self._on_aux(channel_id, dict(channel))

    def ready(self, *, asterisk_version: str | None = None) -> bool:
        return bool(
            self.server
            and self.server.health().get("listening")
            and resolve_media_websocket_control(asterisk_version, getattr(self._section, "control_format", "json"))
        )

    def health(self) -> dict[str, Any]:
        version = self._asterisk_version() if self._asterisk_version else None
        requested = getattr(self._section, "control_format", "json")
        return {**self.server.health(), "requested_control_format": requested,
                "effective_control_format": resolve_media_websocket_control(version, requested)}
