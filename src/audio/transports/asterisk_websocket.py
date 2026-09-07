"""Asterisk ``chan_websocket`` media listener.

This is deliberately a listener for Asterisk's *per-call outbound* media
connections.  It isn't an ARI WebSocket and it never reconnects a call: the
engine owns ARI and registers a one-time nonce before creating externalMedia.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import ipaddress
import json
import os
import secrets
import ssl
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from src.media_transport_capabilities import supports_media_websocket

from .codec import (
    bytes_per_frame,
    canonical_wire_codec,
    sample_rate_for_codec,
)
from .websocket_control import decode_control, encode_control

from src.logging_config import get_logger

logger = get_logger(__name__)

MAX_MEDIA_MESSAGE_BYTES = 65_500
MAX_CONTROL_MESSAGE_BYTES = 128
MEDIA_SUBPROTOCOL = "media"

AudioCallback = Callable[[str, bytes], Awaitable[None]]
DisconnectCallback = Callable[[str, str], Awaitable[None]]
DTMFCallback = Callable[[str, str], Awaitable[None]]


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _section(config: Any) -> Any:
    """Accept a root AppConfig, a websocket_media model, or a dict in tests."""
    nested = _value(config, "websocket_media", None)
    return config if nested is None else nested


@dataclass
class WebSocketMediaBinding:
    call_id: str
    channel_id: str
    codec: str
    sample_rate: int
    optimal_frame_size: int
    ptime: int
    connection_id: str
    control_format: str = "json"
    state: str = "ready"
    generation: int = 0
    websocket: Optional[ServerConnection] = field(default=None, repr=False)
    input_queue: Optional[asyncio.Queue[bytes]] = field(default=None, repr=False)
    input_task: Optional[asyncio.Task[None]] = field(default=None, repr=False)
    input_remainder: bytes = field(default=b"", repr=False)
    xon: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    admission: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    writer_cancel: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    transition_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    finish_waiters: dict[str, asyncio.Future[bool]] = field(default_factory=dict, repr=False)
    mark_waiters: dict[str, asyncio.Future[bool]] = field(default_factory=dict, repr=False)
    output_state: str = "idle"
    queue_frames: Optional[int] = None
    xoff_count: int = 0
    input_drops: int = 0
    output_drops: int = 0

    def __post_init__(self) -> None:
        self.xon.set()
        self.admission.set()


@dataclass
class _CallSlot:
    call_id: str
    channel_id: str
    codec: str
    nonce: str
    control_format: str = "json"
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    binding: Optional[WebSocketMediaBinding] = None
    error: Optional[str] = None
    claimed: bool = False
    disconnected: bool = False


class WebSocketMediaServer:
    """Authenticated, bounded Media WebSocket server for Asterisk.

    Inbound audio is queued per call and delivered by a worker, so an STT or
    provider callback can never stop control processing (notably XON/XOFF and
    a barge-in FLUSH).  Output is intentionally pull-based: while XOFF is set,
    ``send_audio`` waits rather than growing an unbounded application queue.
    """

    def __init__(
        self,
        config: Any,
        on_audio: AudioCallback,
        on_disconnect: DisconnectCallback,
        on_dtmf: Optional[DTMFCallback] = None,
    ) -> None:
        self._config = _section(config)
        self._on_audio = on_audio
        self._on_disconnect = on_disconnect
        self._on_dtmf = on_dtmf
        self._calls: dict[str, _CallSlot] = {}
        self._server: Any = None
        self._sockets: set[ServerConnection] = set()
        self._callback_tasks: set[asyncio.Task[Any]] = set()
        self._lock = asyncio.Lock()
        self.listening = False
        self.address: Optional[tuple[str, int]] = None
        self._metrics = {
            "connections": 0,
            "rejected": 0,
            "protocol_errors": 0,
            "input_drops": 0,
            "xoff": 0,
        }

        self._host = str(_value(self._config, "bind_host", _value(self._config, "host", "127.0.0.1")))
        self._port = int(_value(self._config, "port", 8787))
        self._path = str(_value(self._config, "path", "/media"))
        self._max_connections = int(_value(self._config, "max_connections", 100))
        self._max_message_bytes = min(
            MAX_MEDIA_MESSAGE_BYTES,
            max(1, int(_value(self._config, "max_message_bytes", MAX_MEDIA_MESSAGE_BYTES))),
        )
        self._input_queue_frames = max(1, int(_value(self._config, "max_input_queue_frames", 32)))
        self._handshake_timeout = max(0.1, float(_value(self._config, "handshake_timeout_ms", 5000)) / 1000)
        self._media_start_timeout = max(0.1, float(_value(self._config, "media_start_timeout_ms", 5000)) / 1000)
        self._drain_timeout = max(0.1, float(_value(self._config, "drain_timeout_ms", 30000)) / 1000)
        self._output_wait_timeout = max(
            0.1, float(_value(self._config, "output_wait_timeout_ms", self._drain_timeout * 1000)) / 1000
        )
        # Zero is meaningful: deployments that don't need the affected
        # Asterisk ordering workaround can reject pre-MEDIA_START media.
        pre_start_ms = max(0, int(_value(self._config, "pre_start_buffer_ms", 200)))
        # slin16 is the largest v1 codec: 32,000 raw bytes/sec.
        self._pre_start_max_bytes = min(self._max_message_bytes, (32_000 * pre_start_ms) // 1000)
        self._allowed_peers = self._normalise_allowed_hosts(
            _value(self._config, "allowed_remote_hosts", ["127.0.0.1", "::1"])
        )
        auth = _value(self._config, "auth", {}) or {}
        self._auth_required = bool(_value(auth, "required", True))
        self._auth_username = _value(auth, "username", None)
        password = _value(auth, "password", None)
        password_env = _value(auth, "password_env", None)
        self._auth_password = str(password) if password is not None else (os.getenv(str(password_env)) if password_env else None)

    @staticmethod
    def _normalise_allowed_hosts(hosts: Any) -> set[str]:
        if hosts is None:
            return set()
        if isinstance(hosts, str):
            hosts = [hosts]
        result = {str(host).strip() for host in hosts if str(host).strip()}
        # No DNS lookup belongs in a media listener.  This spelling is useful
        # for the default local-only deployment without weakening peer checks.
        if "localhost" in result:
            result.update({"127.0.0.1", "::1"})
        return result

    async def start(self) -> None:
        """Bind the listener.  Calling start twice is harmless."""
        if self.listening:
            return
        if self._max_connections < 1:
            raise ValueError("websocket_media.max_connections must be positive")
        if not self._path.startswith("/") or "?" in self._path:
            raise ValueError("websocket_media.path must be an absolute path without a query")
        if self._auth_required and (not self._auth_username or self._auth_password is None):
            raise ValueError("WebSocket media authentication requires username and password_env")
        if not self._auth_required and not self._is_loopback_host(self._host):
            raise ValueError("WebSocket media authentication is required on non-loopback listeners")
        if not self._allowed_peers:
            raise ValueError("WebSocket media listener requires an allowed_remote_hosts peer allowlist")

        ssl_context = self._ssl_context()
        self._server = await serve(
            self._handle_socket,
            self._host,
            self._port,
            subprotocols=[MEDIA_SUBPROTOCOL],
            compression=None,
            process_request=self._process_request,
            open_timeout=self._handshake_timeout,
            close_timeout=self._handshake_timeout,
            max_size=self._max_message_bytes,
            max_queue=self._input_queue_frames,
            ssl=ssl_context,
        )
        sockets = getattr(self._server, "sockets", ())
        if not sockets:
            await self.stop()
            raise RuntimeError("Media WebSocket listener did not expose a bound socket")
        host, port = sockets[0].getsockname()[:2]
        self.address = (str(host), int(port))
        self.listening = True

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        if host.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def _ssl_context(self) -> Optional[ssl.SSLContext]:
        tls = _value(self._config, "tls", {}) or {}
        if not bool(_value(tls, "enabled", False)):
            return None
        cert_file, key_file = _value(tls, "cert_file", None), _value(tls, "key_file", None)
        if not cert_file or not key_file:
            raise ValueError("websocket_media TLS requires cert_file and key_file")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_file), str(key_file))
        return context

    async def stop(self) -> None:
        """Stop admission and close active calls without attempting reconnects."""
        server, self._server = self._server, None
        self.listening = False
        self.address = None
        if server is not None:
            server.close()
        await asyncio.gather(
            *(self.unregister_call(call_id) for call_id in list(self._calls)), return_exceptions=True
        )
        if server is not None:
            await server.wait_closed()
        tasks = tuple(self._callback_tasks)
        if tasks:
            # A disconnect callback may itself trigger shutdown. Never make a
            # task await itself in that otherwise legitimate lifecycle path.
            current = asyncio.current_task()
            await asyncio.gather(*(task for task in tasks if task is not current), return_exceptions=True)

    def register_call(self, call_id: str, channel_id: str, codec: str, *, control_format: str = "json") -> str:
        """Reserve one nonce before the ARI externalMedia request is made."""
        codec = canonical_wire_codec(codec)
        if control_format not in {"json", "plain"}:
            raise ValueError("call requires a resolved control format")
        if not call_id or not channel_id:
            raise ValueError("call_id and channel_id are required")
        if call_id in self._calls:
            raise ValueError(f"media WebSocket call is already registered: {call_id}")
        if any(slot.channel_id == channel_id for slot in self._calls.values()):
            raise ValueError("media channel ID is already registered")
        nonce = secrets.token_urlsafe(18)
        self._calls[call_id] = _CallSlot(call_id, channel_id, codec, nonce, control_format)
        return nonce

    async def wait_ready(self, call_id: str, timeout: Optional[float] = None) -> WebSocketMediaBinding:
        slot = self._calls.get(call_id)
        if slot is None:
            raise KeyError(f"unknown media WebSocket call: {call_id}")
        await asyncio.wait_for(slot.ready.wait(), self._media_start_timeout if timeout is None else timeout)
        if slot.binding is None or slot.binding.state != "ready":
            raise RuntimeError(slot.error or "media WebSocket closed before MEDIA_START")
        return slot.binding

    def get_binding(self, call_id: str) -> Optional[WebSocketMediaBinding]:
        slot = self._calls.get(call_id)
        return slot.binding if slot and slot.binding and slot.binding.state == "ready" else None

    async def unregister_call(self, call_id: str) -> None:
        slot = self._calls.pop(call_id, None)
        if slot is None:
            return
        slot.error = slot.error or "unregistered"
        slot.ready.set()
        binding = slot.binding
        if binding is not None:
            await self._close_binding(slot, binding, "unregistered")

    async def begin_output(self, call_id: str) -> int:
        binding = self._required_binding(call_id)
        # A post-FLUSH MARK fence controls new output admission.  Do not replace
        # this with QUEUE_DRAINED: it is uncorrelated and can be from an old run.
        while True:
            await binding.admission.wait()
            async with binding.transition_lock:
                # An abort can clear the fence between the outer wait and lock
                # acquisition. Go around rather than admitting a stale start.
                if not binding.admission.is_set():
                    continue
                if binding.state != "ready":
                    raise RuntimeError("media WebSocket is not connected")
                if binding.output_state != "idle":
                    raise RuntimeError("a media output segment is already active")
                binding.generation += 1
                binding.output_state = "buffering"
                # A stale writer keeps a reference to the previous event.
                binding.writer_cancel = asyncio.Event()
                if not await self._send_command(binding, {"command": "START_MEDIA_BUFFERING"}):
                    binding.output_state = "failed"
                    await self._fail_binding(binding, "could not start media buffering")
                    raise RuntimeError("could not start Asterisk media buffering")
                return binding.generation

    async def send_audio(self, call_id: str, payload: bytes, generation: int) -> bool:
        binding = self.get_binding(call_id)
        if binding is None or generation != binding.generation or binding.output_state != "buffering":
            return False
        payload = bytes(payload)
        if not payload:
            return True
        for start in range(0, len(payload), self._max_message_bytes):
            # XOFF can arrive after the optimistic XON check while another
            # writer owns send_lock. That is ordinary backpressure, not a
            # generation failure: release the lock and wait for XON again.
            while True:
                if generation != binding.generation or binding.output_state != "buffering":
                    binding.output_drops += 1
                    return False
                if not await self._wait_writable(binding, generation):
                    binding.output_drops += 1
                    if generation == binding.generation and binding.output_state == "buffering":
                        await self._fail_binding(binding, "MEDIA_XON timeout")
                    return False
                try:
                    async with binding.send_lock:
                        if generation != binding.generation or binding.output_state != "buffering":
                            return False
                        if not binding.xon.is_set():
                            continue
                        assert binding.websocket is not None
                        await asyncio.wait_for(
                            binding.websocket.send(payload[start : start + self._max_message_bytes]),
                            self._output_wait_timeout,
                        )
                    break
                except (asyncio.TimeoutError, ConnectionClosed):
                    if generation == binding.generation and binding.output_state == "buffering":
                        await self._fail_binding(binding, "media write timeout")
                        return False
        return True

    async def finish_output(self, call_id: str, generation: int) -> bool:
        binding = self.get_binding(call_id)
        if binding is None:
            return False
        async with binding.transition_lock:
            if generation != binding.generation or binding.output_state != "buffering":
                return False
            correlation_id = self._correlation("b")
            waiter: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            binding.finish_waiters[correlation_id] = waiter
            binding.output_state = "finishing"
            if not await self._send_command(
                binding, {"command": "STOP_MEDIA_BUFFERING", "correlation_id": correlation_id}
            ):
                binding.finish_waiters.pop(correlation_id, None)
                binding.output_state = "failed"
                await self._fail_binding(binding, "could not stop media buffering")
                return False
        try:
            return await asyncio.wait_for(waiter, self._drain_timeout)
        except asyncio.TimeoutError:
            await self._fail_binding(binding, "MEDIA_BUFFERING_COMPLETED timeout")
            return False
        finally:
            binding.finish_waiters.pop(correlation_id, None)
            if generation == binding.generation and binding.output_state == "finishing":
                binding.output_state = "idle"

    async def abort_output(self, call_id: str) -> bool:
        binding = self.get_binding(call_id)
        if binding is None:
            return False
        # Serialize an interruption through its MARK fence. Otherwise two
        # concurrent aborts could let the first fence reopen admission ahead of
        # the second flush.
        async with binding.transition_lock:
            binding.generation += 1  # Invalidates a writer waiting on XON.
            abort_generation = binding.generation
            binding.writer_cancel.set()
            binding.output_state = "aborting"
            binding.admission.clear()
            for waiter in tuple(binding.finish_waiters.values()):
                if not waiter.done():
                    waiter.set_result(False)
            binding.finish_waiters.clear()
            correlation_id = self._correlation("f")
            waiter: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            binding.mark_waiters[correlation_id] = waiter
            # Control writes never wait for XON. FLUSH preempts a backpressured
            # producer; the next segment nevertheless waits for a genuine XON.
            if not await self._send_command(binding, {"command": "FLUSH_MEDIA"}):
                binding.mark_waiters.pop(correlation_id, None)
                binding.output_state = "failed"
                await self._fail_binding(binding, "could not flush media")
                return False
            # 20.17 has no MARK. CONTINUE clears a possible paused state;
            # STOP queues a real correlated boundary after FLUSH in that driver.
            fence_command = "MARK_MEDIA"
            if binding.control_format == "plain":
                fence_command = "STOP_MEDIA_BUFFERING"
                if not await self._send_command(binding, {"command": "CONTINUE_MEDIA"}):
                    binding.mark_waiters.pop(correlation_id, None)
                    await self._fail_binding(binding, "could not resume flushed media")
                    return False
            if not await self._send_command(
                binding, {"command": fence_command, "correlation_id": correlation_id}
            ):
                binding.mark_waiters.pop(correlation_id, None)
                binding.output_state = "failed"
                await self._fail_binding(binding, "could not mark flushed media")
                return False
            try:
                completed = await asyncio.wait_for(waiter, self._drain_timeout)
            except asyncio.CancelledError:
                # Leaving a ready socket with admission cleared would strand
                # subsequent output forever. Cancellation is not a normal
                # barge-in result, so fail the transport deterministically.
                binding.output_state = "abort_cancelled"
                await self._fail_binding(binding, "post-FLUSH mark wait cancelled")
                raise
            except asyncio.TimeoutError:
                completed = False
            finally:
                binding.mark_waiters.pop(correlation_id, None)
            if completed and binding.state == "ready" and binding.generation == abort_generation:
                binding.output_state = "idle"
                binding.admission.set()
            elif not completed:
                # Fail closed: a following segment must not overtake a flush
                # whose queue-front fence was never observed.
                binding.output_state = "abort_timeout"
                reason = "post-FLUSH MEDIA_MARK_PROCESSED timeout" if binding.control_format == "json" else "post-FLUSH MEDIA_BUFFERING_COMPLETED timeout"
                await self._fail_binding(binding, reason)
            return completed

    def snapshot(self, call_id: str) -> dict[str, Any]:
        slot = self._calls.get(call_id)
        if slot is None:
            return {"call_id": call_id, "pending": False, "state": "missing"}
        binding = slot.binding
        if binding is None:
            return {"call_id": call_id, "pending": True, "state": "pending", "channel_id": slot.channel_id}
        return {
            "call_id": call_id,
            "pending": False,
            "state": binding.state,
            "channel_id": binding.channel_id,
            "codec": binding.codec,
            "control_format": binding.control_format,
            "sample_rate": binding.sample_rate,
            "optimal_frame_size": binding.optimal_frame_size,
            "ptime": binding.ptime,
            "generation": binding.generation,
            "output_state": binding.output_state,
            "queue": {"frames": binding.queue_frames, "xoff": not binding.xon.is_set()},
            "input_queue": binding.input_queue.qsize() if binding.input_queue else 0,
            "input_drops": binding.input_drops,
            "output_drops": binding.output_drops,
        }

    def health(self) -> dict[str, Any]:
        return {
            "listening": self.listening,
            "address": self.address,
            "active_connections": len(self._sockets),
            "pending_calls": sum(slot.binding is None for slot in self._calls.values()),
            "metrics": dict(self._metrics),
        }

    def _required_binding(self, call_id: str) -> WebSocketMediaBinding:
        binding = self.get_binding(call_id)
        if binding is None:
            raise RuntimeError(f"media WebSocket call is not ready: {call_id}")
        return binding

    async def _process_request(self, connection: ServerConnection, request: Any) -> Any:
        path = urlsplit(request.path).path
        if path != self._path:
            return self._reject(connection, HTTPStatus.NOT_FOUND, "unknown media path")
        protocols = request.headers.get("Sec-WebSocket-Protocol", "")
        if MEDIA_SUBPROTOCOL not in {item.strip() for item in protocols.split(",")}:
            return self._reject(connection, HTTPStatus.BAD_REQUEST, "media subprotocol required")
        if not self._peer_allowed(connection):
            return self._reject(connection, HTTPStatus.FORBIDDEN, "peer not allowed")
        if len(self._sockets) >= self._max_connections:
            return self._reject(connection, HTTPStatus.SERVICE_UNAVAILABLE, "media connection limit")
        if self._auth_required and not self._valid_basic_auth(request.headers.get("Authorization")):
            response = self._reject(connection, HTTPStatus.UNAUTHORIZED, "invalid media credentials")
            response.headers["WWW-Authenticate"] = 'Basic realm="asterisk-media"'
            return response
        query = parse_qs(urlsplit(request.path).query, keep_blank_values=True)
        nonce_values = query.get("nonce", [])
        if len(nonce_values) > 1:
            return self._reject(connection, HTTPStatus.BAD_REQUEST, "invalid nonce")
        connection.aava_nonce = nonce_values[0] if nonce_values else None
        return None

    def _peer_allowed(self, connection: ServerConnection) -> bool:
        if not self._allowed_peers:
            return True
        remote = getattr(connection, "remote_address", None)
        host = str(remote[0]) if isinstance(remote, tuple) and remote else ""
        return host in self._allowed_peers

    def _valid_basic_auth(self, header: Optional[str]) -> bool:
        if not header or not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return False
        return hmac.compare_digest(username.encode("utf-8"), str(self._auth_username).encode("utf-8")) and hmac.compare_digest(
            password.encode("utf-8"), str(self._auth_password).encode("utf-8")
        )

    def _reject(self, connection: ServerConnection, status: HTTPStatus, message: str) -> Any:
        self._metrics["rejected"] += 1
        return connection.respond(status, f"{message}\n")

    async def _handle_socket(self, websocket: ServerConnection) -> None:
        requested_nonce = getattr(websocket, "aava_nonce", None)
        slot: Optional[_CallSlot] = self._slot_for_nonce(requested_nonce) if requested_nonce else None
        # process_request has a pre-upgrade 503 check. Handshakes can pass it
        # concurrently, so enforce the same cap before this upgraded socket is
        # admitted to the active set as well.
        if len(self._sockets) >= self._max_connections:
            self._metrics["rejected"] += 1
            if slot is not None and not slot.ready.is_set():
                slot.error = "media connection limit"
                slot.ready.set()
            await websocket.close(code=1013, reason="media connection limit")
            return
        self._sockets.add(websocket)
        self._metrics["connections"] += 1
        if requested_nonce and slot is None:
            await self._protocol_close(websocket, "unknown media nonce")
            self._sockets.discard(websocket)
            return
        binding: Optional[WebSocketMediaBinding] = None
        pre_start: list[bytes] = []
        pre_start_bytes = 0
        timeout_task = asyncio.create_task(self._media_start_deadline(websocket))
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    if len(message) > self._max_message_bytes:
                        await self._protocol_close(websocket, "media message too large")
                        return
                    if binding is None:
                        pre_start_bytes += len(message)
                        if pre_start_bytes > self._pre_start_max_bytes:
                            await self._protocol_close(websocket, "MEDIA_START required before media")
                            return
                        pre_start.append(message)
                    else:
                        self._queue_audio(binding, message)
                    continue
                binding, slot = await self._handle_text(websocket, message, binding, slot)
                if binding is not None and pre_start:
                    for buffered in pre_start:
                        self._queue_audio(binding, buffered)
                    pre_start.clear()
                    pre_start_bytes = 0
        except ConnectionClosed:
            pass
        finally:
            timeout_task.cancel()
            self._sockets.discard(websocket)
            if binding is not None and slot is not None:
                await self._close_binding(slot, binding, "socket closed")
            elif slot is not None and not slot.ready.is_set():
                # A registered call must not wait a second full setup timeout
                # after its authenticated, nonce-bearing socket has failed.
                slot.error = slot.error or "socket closed before MEDIA_START"
                slot.ready.set()

    async def _media_start_deadline(self, websocket: ServerConnection) -> None:
        await asyncio.sleep(self._media_start_timeout)
        # It is okay for this close to race a valid MEDIA_START; closing an open
        # socket after the deadline is conservative and only occurs when no
        # binding was installed by the handler's normal path.
        if websocket in self._sockets:
            # The handler tests its binding independently; this task is only a
            # stale-socket guard. A valid bound socket records this attribute.
            if not getattr(websocket, "aava_bound", False):
                await self._protocol_close(websocket, "MEDIA_START timeout")

    async def _handle_text(
        self,
        websocket: ServerConnection,
        message: str,
        binding: Optional[WebSocketMediaBinding],
        slot: Optional[_CallSlot],
    ) -> tuple[Optional[WebSocketMediaBinding], Optional[_CallSlot]]:
        try:
            mode = binding.control_format if binding else (slot.control_format if slot else "json")
            event = decode_control(message, mode)
        except (TypeError, ValueError):
            self._metrics["protocol_errors"] += 1
            await self._protocol_close(websocket, "invalid media control event")
            return binding, slot
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            self._metrics["protocol_errors"] += 1
            await self._protocol_close(websocket, "invalid JSON control event")
            return binding, slot
        event_name = event["event"]
        if binding is None:
            if event_name != "MEDIA_START":
                await self._protocol_close(websocket, "MEDIA_START required")
                return binding, slot
            bound = await self._bind_media_start(websocket, event)
            return bound, self._calls.get(bound.call_id) if bound else slot
        await self._handle_event(binding, event_name, event)
        return binding, slot

    async def _bind_media_start(self, websocket: ServerConnection, event: dict[str, Any]) -> Optional[WebSocketMediaBinding]:
        variables = event.get("channel_variables")
        variables = variables if isinstance(variables, dict) else {}
        nonce = getattr(websocket, "aava_nonce", None) or variables.get("nonce") or variables.get("AAVA_MEDIA_NONCE")
        if not isinstance(nonce, str) or not nonce.isascii():
            await self._protocol_close(websocket, "missing media nonce")
            return None
        channel_id = event.get("channel_id")
        codec = event.get("format")
        frame_size, ptime = event.get("optimal_frame_size"), event.get("ptime")
        try:
            codec = canonical_wire_codec(codec)
            if not isinstance(channel_id, str) or not channel_id:
                raise ValueError("missing channel_id")
            if not isinstance(frame_size, int) or not 1 <= frame_size <= self._max_message_bytes:
                raise ValueError("invalid optimal_frame_size")
            # v1 deliberately freezes the documented 20 ms cadence. Engine
            # VAD counters, streaming frame accounting, and its fallback
            # silence all use that cadence; accepting a valid 10/40 ms driver
            # frame here would make the negotiated binding internally false.
            if not isinstance(ptime, int) or ptime != 20:
                raise ValueError("invalid ptime")
            expected = bytes_per_frame(codec, ptime)
            if frame_size != expected:
                raise ValueError("unexpected optimal_frame_size")
            if codec in {"slin", "slin16"} and frame_size % 2:
                raise ValueError("linear frame isn't sample aligned")
        except ValueError:
            await self._protocol_close(websocket, "invalid MEDIA_START")
            return None
        async with self._lock:
            slot = next((item for item in self._calls.values() if hmac.compare_digest(item.nonce, nonce)), None)
            if slot is None or slot.claimed or slot.channel_id != channel_id or slot.codec != codec:
                await self._protocol_close(websocket, "unknown or duplicate media call")
                return None
            if slot.control_format == "plain" and not getattr(websocket, "aava_nonce", None):
                await self._protocol_close(websocket, "plain control requires nonce-bearing URI")
                return None
            # If both forms were supplied, require equality.  This prevents a
            # query nonce from authenticating a different v(nonce) value.
            variable_nonce = variables.get("nonce") or variables.get("AAVA_MEDIA_NONCE")
            if variable_nonce is not None and (not isinstance(variable_nonce, str) or not variable_nonce.isascii() or not hmac.compare_digest(nonce, variable_nonce)):
                await self._protocol_close(websocket, "mismatched media nonce")
                return None
            slot.claimed = True
            binding = WebSocketMediaBinding(
                call_id=slot.call_id,
                channel_id=channel_id,
                codec=codec,
                sample_rate=sample_rate_for_codec(codec),
                optimal_frame_size=frame_size,
                ptime=ptime,
                connection_id=str(event.get("connection_id", "")),
                control_format=slot.control_format,
                websocket=websocket,
                input_queue=asyncio.Queue(maxsize=self._input_queue_frames),
            )
            binding.input_task = asyncio.create_task(self._consume_audio(binding))
            slot.binding = binding
            slot.ready.set()
            websocket.aava_bound = True
        return binding

    async def _handle_event(self, binding: WebSocketMediaBinding, event_name: str, event: dict[str, Any]) -> None:
        if event_name == "MEDIA_XOFF":
            binding.xon.clear()
            binding.xoff_count += 1
            self._metrics["xoff"] += 1
        elif event_name == "MEDIA_XON":
            binding.xon.set()
        elif event_name == "STATUS":
            frames = event.get("queue_frames", event.get("queue"))
            binding.queue_frames = frames if isinstance(frames, int) else binding.queue_frames
            if event.get("queue_full") is True:
                binding.xon.clear()
            elif event.get("queue_full") is False:
                binding.xon.set()
        elif event_name == "MEDIA_BUFFERING_COMPLETED":
            self._resolve(binding.finish_waiters, event.get("correlation_id"), True)
            if binding.control_format == "plain":
                self._resolve(binding.mark_waiters, event.get("correlation_id"), True)
        elif event_name == "MEDIA_MARK_PROCESSED":
            if binding.control_format == "json":
                self._resolve(binding.mark_waiters, event.get("correlation_id"), True)
        elif event_name == "ERROR":
            correlation_id = event.get("correlation_id")
            self._resolve(binding.finish_waiters, correlation_id, False)
            self._resolve(binding.mark_waiters, correlation_id, False)
            if isinstance(correlation_id, str) and (
                correlation_id in binding.finish_waiters or correlation_id in binding.mark_waiters
            ):
                await self._fail_binding(binding, "Asterisk media command error")
        elif event_name == "DTMF_END" and self._on_dtmf:
            digit = event.get("digit", event.get("dtmf"))
            if isinstance(digit, str) and len(digit) == 1:
                self._schedule_callback(self._on_dtmf(binding.call_id, digit))
        # QUEUE_DRAINED is intentionally diagnostics-only.  It has no
        # correlation id and must never complete a current generation.

    @staticmethod
    def _resolve(waiters: dict[str, asyncio.Future[bool]], correlation_id: Any, value: bool) -> None:
        if not isinstance(correlation_id, str):
            return
        waiter = waiters.get(correlation_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(value)

    def _slot_for_nonce(self, nonce: Any) -> Optional[_CallSlot]:
        if not isinstance(nonce, str) or not nonce.isascii():
            return None
        return next(
            (slot for slot in self._calls.values() if hmac.compare_digest(slot.nonce, nonce)), None
        )

    def _queue_audio(self, binding: WebSocketMediaBinding, payload: bytes) -> None:
        if binding.state != "ready" or binding.input_queue is None:
            return
        # A WebSocket message is not a VAD frame. Preserve split samples and
        # split/combined messages, but deliver exactly the negotiated 20 ms.
        # Remainder storage is bounded to strictly less than one wire frame.
        data = binding.input_remainder + payload
        frame_size = binding.optimal_frame_size
        complete_bytes = len(data) - len(data) % frame_size
        binding.input_remainder = data[complete_bytes:]
        for offset in range(0, complete_bytes, frame_size):
            try:
                binding.input_queue.put_nowait(data[offset:offset + frame_size])
            except asyncio.QueueFull:
                binding.input_drops += 1
                self._metrics["input_drops"] += 1

    async def _consume_audio(self, binding: WebSocketMediaBinding) -> None:
        assert binding.input_queue is not None
        try:
            while binding.state == "ready":
                payload = await binding.input_queue.get()
                if binding.state != "ready":
                    return
                try:
                    await self._on_audio(binding.call_id, payload)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("media websocket audio callback failed", call_id=binding.call_id)
        except asyncio.CancelledError:
            raise

    async def _send_command(self, binding: WebSocketMediaBinding, command: dict[str, str]) -> bool:
        encoded = encode_control(command, binding.control_format)
        if len(encoded.encode("utf-8")) > MAX_CONTROL_MESSAGE_BYTES:
            raise ValueError("Asterisk WebSocket control command exceeds 128 bytes")
        try:
            async with binding.send_lock:
                if binding.state != "ready" or binding.websocket is None:
                    return False
                await asyncio.wait_for(binding.websocket.send(encoded), self._output_wait_timeout)
            return True
        except (asyncio.TimeoutError, ConnectionClosed):
            return False

    async def _wait_writable(self, binding: WebSocketMediaBinding, generation: int) -> bool:
        """Wait for XON, but let abort invalidate a parked producer promptly."""
        if binding.xon.is_set():
            return generation == binding.generation and binding.output_state == "buffering"
        cancellation = binding.writer_cancel
        xon_wait = asyncio.create_task(binding.xon.wait())
        cancel_wait = asyncio.create_task(cancellation.wait())
        try:
            done, pending = await asyncio.wait(
                (xon_wait, cancel_wait), timeout=self._output_wait_timeout, return_when=asyncio.FIRST_COMPLETED
            )
            generation_cancelled = cancel_wait in done
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            return (
                bool(done)
                and not generation_cancelled
                and generation == binding.generation
                and binding.output_state == "buffering"
                and binding.xon.is_set()
            )
        finally:
            for task in (xon_wait, cancel_wait):
                if not task.done():
                    task.cancel()

    @staticmethod
    def _correlation(prefix: str) -> str:
        # Short opaque ASCII ids preserve the 128-byte control-message budget.
        return f"{prefix}{secrets.token_urlsafe(9)}"

    async def _protocol_close(self, websocket: ServerConnection, reason: str) -> None:
        self._metrics["protocol_errors"] += 1
        try:
            await websocket.close(code=1008, reason=reason[:80])
        except ConnectionClosed:
            pass

    async def _close_binding(self, slot: _CallSlot, binding: WebSocketMediaBinding, reason: str) -> None:
        if binding.state == "closed":
            return
        binding.state = "closed"
        binding.output_state = "closed"
        binding.queue_frames = None
        binding.input_remainder = b""
        slot.error = slot.error or reason
        slot.ready.set()
        binding.xon.set()
        binding.admission.set()  # Wake starts parked behind an interrupted flush.
        binding.writer_cancel.set()
        for waiter in tuple(binding.finish_waiters.values()) + tuple(binding.mark_waiters.values()):
            if not waiter.done():
                waiter.set_result(False)
        if binding.input_task and binding.input_task is not asyncio.current_task():
            binding.input_task.cancel()
            await asyncio.gather(binding.input_task, return_exceptions=True)
        if binding.websocket is not None:
            try:
                await binding.websocket.close()
            except ConnectionClosed:
                pass
        if not slot.disconnected:
            slot.disconnected = True
            self._schedule_callback(self._on_disconnect(slot.call_id, reason))

    async def _fail_binding(self, binding: WebSocketMediaBinding, reason: str) -> None:
        """Turn a bounded output failure into one deterministic call failure."""
        slot = self._calls.get(binding.call_id)
        if slot is not None:
            await self._close_binding(slot, binding, reason)

    def _schedule_callback(self, callback: Awaitable[Any]) -> None:
        task = asyncio.create_task(callback)
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)
