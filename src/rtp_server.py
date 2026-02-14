"""
RTP Server for External Media Integration with Asterisk.
Handles bidirectional RTP audio streams for the AI voice agent.
"""

import asyncio
import socket
import struct
import audioop
from .audio.resampler import resample_audio
import time
import random
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable, Any, Tuple, Iterable

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class RTPSession:
    """Represents an active RTP session for a call."""
    call_id: str
    local_port: int
    socket: socket.socket
    created_at: float
    last_packet_at: float
    remote_host: Optional[str] = None
    remote_port: Optional[int] = None
    sequence_number: int = 0
    timestamp: int = 0
    ssrc: Optional[int] = None
    outbound_ssrc: Optional[int] = None  # Track our own SSRC for echo filtering
    expected_sequence: int = 0
    packet_loss_count: int = 0
    last_sequence: int = 0
    jitter_buffer: list = field(default_factory=list)
    frames_received: int = 0
    frames_processed: int = 0
    resample_state: Optional[tuple] = None
    receiver_task: Optional[asyncio.Task] = None
    send_sequence_initialized: bool = False
    send_timestamp_initialized: bool = False
    echo_packets_filtered: int = 0  # Count filtered echo packets


class RTPServer:
    """
    RTP Server for handling bidirectional audio streams with Asterisk External Media.

    This server:
        1. Receives RTP packets from Asterisk (caller audio) in configured codec
        2. Converts to configured format @ sample_rate for provider processing
        3. Sends provider audio back to Asterisk as RTP using the same SSRC
    """

    RTP_VERSION = 2
    RTP_HEADER_SIZE = 12
    SAMPLE_RATE = 8000  # Asterisk-side sample rate (codec-dependent)
    SAMPLES_PER_PACKET = 160  # 20 ms @ 8 kHz

    def __init__(
        self,
        host: str,
        port: int,
        engine_callback: Callable[[str, int, bytes], Any],
        codec: str = "ulaw",
        format: str = "slin16",
        sample_rate: int = 16000,
        port_range: Optional[Tuple[int, int]] = None,
        *,
        allowed_remote_hosts: Optional[Iterable[str]] = None,
        lock_remote_endpoint: bool = True,
    ):
        self.host = host
        self.base_port = int(port)
        self.engine_callback = engine_callback
        self.codec = self._normalise_codec(codec)
        self.format = format  # Engine-side format
        self.sample_rate = sample_rate  # Engine-side sample rate

        if port_range:
            start, end = port_range
        else:
            start = end = self.base_port
        if start > end:
            start, end = end, start
        self.port_range: Tuple[int, int] = (max(1, start), max(1, end))

        self.sessions: Dict[str, RTPSession] = {}
        self.session_tasks: Dict[str, asyncio.Task] = {}
        self.port_allocation: Dict[int, str] = {}
        self.ssrc_to_call_id: Dict[int, str] = {}
        self.running: bool = False
        self.lock_remote_endpoint: bool = bool(lock_remote_endpoint)
        self.allowed_remote_hosts = (
            {str(h).strip() for h in allowed_remote_hosts if str(h).strip()}
            if allowed_remote_hosts is not None
            else None
        )

        logger.info(
            "RTP Server initialized",
            host=self.host,
            port_range=self.port_range,
            codec=self.codec,
            format=self.format,
            sample_rate=self.sample_rate,
            lock_remote_endpoint=self.lock_remote_endpoint,
            allowed_remote_hosts=sorted(self.allowed_remote_hosts) if self.allowed_remote_hosts else None,
        )

    async def start(self) -> None:
        """Mark the RTP server as ready. Per-call sockets are allocated on demand."""
        if self.running:
            logger.warning("RTP server already running")
            return
        self.running = True
        logger.info(
            "RTP Server ready",
            host=self.host,
            port_range=self.port_range,
            codec=self.codec,
            format=self.format,
            sample_rate=self.sample_rate,
        )

    async def stop(self) -> None:
        """Stop the RTP server and clean up all sessions."""
        if not self.running:
            return

        self.running = False

        # Cancel receiver tasks first so sockets can close cleanly.
        for task in list(self.session_tasks.values()):
            task.cancel()
        for task in list(self.session_tasks.values()):
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("RTP receiver task cleanup error", error=str(exc))

        # Close sockets / release ports.
        for session in list(self.sessions.values()):
            await self._cleanup_session(session)

        self.session_tasks.clear()
        self.sessions.clear()
        self.port_allocation.clear()
        self.ssrc_to_call_id.clear()

        logger.info("RTP Server stopped")

    async def allocate_session(self, call_id: str) -> int:
        """Allocate and bind a UDP socket for a call, returning the chosen port."""
        if not self.running:
            raise RuntimeError("RTP server not started")

        if call_id in self.sessions:
            return self.sessions[call_id].local_port

        port = self._reserve_port(call_id)
        if port is None:
            raise RuntimeError("No free RTP ports available in configured range")

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.host, port))
        sock.setblocking(False)

        now = time.time()
        session = RTPSession(
            call_id=call_id,
            local_port=port,
            socket=sock,
            created_at=now,
            last_packet_at=now,
        )
        self.sessions[call_id] = session

        loop = self._get_loop()
        task = loop.create_task(self._rtp_receiver_loop(session))
        session.receiver_task = task
        self.session_tasks[call_id] = task

        logger.info("RTP session allocated", call_id=call_id, port=port, codec=self.codec)
        return port

    async def cleanup_session(self, call_id: str) -> None:
        """Public helper to clean up a specific RTP session."""
        session = self.sessions.pop(call_id, None)
        if not session:
            return
        await self._cleanup_session(session)
        self.session_tasks.pop(call_id, None)
        logger.info("RTP session cleaned up", call_id=call_id)

    def map_ssrc_to_call_id(self, ssrc: int, call_id: str) -> None:
        """Record SSRC → call ID mapping for outbound lookups."""
        self.ssrc_to_call_id[ssrc] = call_id
        session = self.sessions.get(call_id)
        if session:
            session.ssrc = ssrc
        logger.info("SSRC mapped to call ID", ssrc=ssrc, call_id=call_id)

    def get_call_id_for_ssrc(self, ssrc: int) -> Optional[str]:
        return self.ssrc_to_call_id.get(ssrc)

    async def send_audio(self, call_id: str, chunk: bytes, *, ssrc: Optional[int] = None) -> bool:
        """Send provider audio back to Asterisk as RTP for the specified call."""
        if not chunk:
            return True

        session = self.sessions.get(call_id)
        if not session:
            logger.debug("RTP send skipped (no session)", call_id=call_id)
            return False
        if session.remote_host is None or session.remote_port is None:
            logger.debug("RTP send deferred; remote endpoint unknown", call_id=call_id)
            return False

        # Generate unique outbound SSRC (different from caller's SSRC for echo filtering)
        if session.outbound_ssrc is None:
            # Generate a unique SSRC for our outbound stream
            # Make it different from caller's inbound SSRC
            if session.ssrc is not None:
                # Flip some bits to make it different but deterministic
                session.outbound_ssrc = (session.ssrc ^ 0xFFFFFFFF) & 0xFFFFFFFF
            else:
                # Random if we don't have caller's SSRC yet
                session.outbound_ssrc = random.randint(0, 0xFFFFFFFF)
            logger.info(
                "RTP outbound SSRC established for echo filtering",
                call_id=call_id,
                outbound_ssrc=session.outbound_ssrc,
                inbound_ssrc=session.ssrc,
            )
        
        out_ssrc = session.outbound_ssrc
        if out_ssrc is None:
            logger.debug("RTP send deferred; SSRC not established", call_id=call_id)
            return False

        # Initialise outbound sequence / timestamp the first time we transmit.
        if not session.send_sequence_initialized:
            session.sequence_number = session.sequence_number or random.randint(0, 0xFFFF)
            session.send_sequence_initialized = True
        if not session.send_timestamp_initialized:
            session.timestamp = session.timestamp or random.randint(0, 0xFFFFFFFF)
            session.send_timestamp_initialized = True

        header = self._build_rtp_header(
            sequence=session.sequence_number,
            timestamp=session.timestamp,
            ssrc=out_ssrc,
        )
        packet = header + chunk

        try:
            # Prefer connected UDP sockets for lower overhead.
            if not self._socket_is_connected(session.socket):
                try:
                    session.socket.connect((session.remote_host, session.remote_port))
                except Exception as exc:
                    logger.debug(
                        "RTP connect failed; falling back to sendto",
                        call_id=call_id,
                        error=str(exc),
                    )
                    session.socket = session.socket  # no-op for mypy hints
            sent = session.socket.send(packet) if self._socket_is_connected(session.socket) else session.socket.sendto(packet, (session.remote_host, session.remote_port))
            if sent != len(packet):
                logger.debug("Short RTP send", call_id=call_id, expected=len(packet), sent=sent)
        except BlockingIOError:
            logger.debug("RTP send would block", call_id=call_id)
            return False
        except Exception as exc:
            logger.error("RTP send failed", call_id=call_id, error=str(exc))
            return False

        session.sequence_number = (session.sequence_number + 1) & 0xFFFF
        session.timestamp = (session.timestamp + self.SAMPLES_PER_PACKET) & 0xFFFFFFFF
        session.frames_processed += 1
        return True

    def has_remote_endpoint(self, call_id: str) -> bool:
        """Return True once we've learned the inbound RTP (ip,port) for this call."""
        session = self.sessions.get(call_id)
        if not session:
            return False
        return bool(session.remote_host) and bool(session.remote_port)

    async def _rtp_receiver_loop(self, session: RTPSession) -> None:
        """Per-session receive loop that forwards inbound audio to the engine."""
        loop = self._get_loop()
        sock = session.socket
        call_id = session.call_id

        logger.debug("RTP receiver loop started", call_id=call_id, port=session.local_port)

        while self.running and call_id in self.sessions:
            try:
                data, addr = await loop.sock_recvfrom(sock, 1500)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self.running:
                    logger.error("RTP receiver error", call_id=call_id, error=str(exc))
                break

            if len(data) < self.RTP_HEADER_SIZE:
                continue

            version = data[0] >> 6
            if version != self.RTP_VERSION:
                logger.debug("Invalid RTP version", call_id=call_id, version=version)
                continue

            sequence = struct.unpack("!H", data[2:4])[0]
            timestamp = struct.unpack("!I", data[4:8])[0]
            ssrc = struct.unpack("!I", data[8:12])[0]
            payload = data[self.RTP_HEADER_SIZE:]

            # CRITICAL: Filter echo - drop packets with our own outbound SSRC
            # This prevents the agent from hearing its own audio output in the bridge
            if session.outbound_ssrc is not None and ssrc == session.outbound_ssrc:
                session.echo_packets_filtered += 1
                if session.echo_packets_filtered <= 5:  # Log first few
                    logger.debug(
                        "RTP echo packet filtered (our own SSRC)",
                        call_id=call_id,
                        ssrc=ssrc,
                        filtered_count=session.echo_packets_filtered,
                    )
                continue

            # Record remote endpoint on first packet.
            if session.remote_host is None:
                if self.allowed_remote_hosts is not None and addr[0] not in self.allowed_remote_hosts:
                    logger.warning(
                        "RTP packet rejected (source not allowed)",
                        call_id=call_id,
                        remote_host=addr[0],
                        remote_port=addr[1],
                    )
                    continue
                session.remote_host, session.remote_port = addr[0], addr[1]
                logger.info(
                    "RTP remote endpoint established",
                    call_id=call_id,
                    remote_host=session.remote_host,
                    remote_port=session.remote_port,
                )
            elif (addr[0] != session.remote_host) or (addr[1] != session.remote_port):
                if self.allowed_remote_hosts is not None and addr[0] not in self.allowed_remote_hosts:
                    logger.warning(
                        "RTP packet rejected (source not allowed)",
                        call_id=call_id,
                        remote_host=addr[0],
                        remote_port=addr[1],
                    )
                    continue
                if self.lock_remote_endpoint:
                    logger.warning(
                        "RTP remote endpoint mismatch (locked; dropping packet)",
                        call_id=call_id,
                        expected_host=session.remote_host,
                        expected_port=session.remote_port,
                        actual_host=addr[0],
                        actual_port=addr[1],
                    )
                    continue
                session.remote_host, session.remote_port = addr[0], addr[1]
                logger.info(
                    "RTP remote endpoint updated",
                    call_id=call_id,
                    remote_host=session.remote_host,
                    remote_port=session.remote_port,
                )

            # Maintain SSRC mapping (only for inbound caller audio, not our echo)
            if session.ssrc is None:
                session.ssrc = ssrc
                self.ssrc_to_call_id[ssrc] = call_id
                logger.info(
                    "RTP inbound SSRC established (caller audio)",
                    call_id=call_id,
                    inbound_ssrc=ssrc,
                )

            # Seed outbound sequence/timestamp with inbound values so the far-end sees continuity.
            if not session.send_sequence_initialized:
                session.sequence_number = sequence
            if not session.send_timestamp_initialized:
                session.timestamp = timestamp

            await self._handle_inbound_packet(session, sequence, timestamp, payload, ssrc)

        logger.debug("RTP receiver loop stopped", call_id=call_id, port=session.local_port)

    async def _handle_inbound_packet(
        self,
        session: RTPSession,
        sequence: int,
        timestamp: int,
        payload: bytes,
        ssrc: int,
    ) -> None:
        """Decode inbound RTP audio and forward PCM16 16 kHz to the engine."""
        call_id = session.call_id
        session.frames_received += 1
        session.last_packet_at = time.time()

        # Packet loss / ordering diagnostics.
        if session.expected_sequence == 0:
            session.expected_sequence = sequence
        else:
            expected = session.expected_sequence
            if sequence != expected:
                if sequence > expected:
                    lost = sequence - expected
                    session.packet_loss_count += lost
                    logger.debug(
                        "RTP packet loss detected",
                        call_id=call_id,
                        expected=expected,
                        received=sequence,
                        lost=lost,
                    )
                else:
                    logger.debug(
                        "RTP out-of-order packet",
                        call_id=call_id,
                        expected=expected,
                        received=sequence,
                    )
        session.expected_sequence = (sequence + 1) & 0xFFFF
        session.last_sequence = sequence

        try:
            pcm_decoded = self._decode_payload(payload)
            # Use configured sample_rate instead of hardcoded constant
            # CRITICAL: Must match what engine expects based on config
            if self.sample_rate != self.SAMPLE_RATE:
                # Resample from codec rate to configured engine rate
                pcm_resampled, state = resample_audio(pcm_decoded, self.SAMPLE_RATE, self.sample_rate, state=session.resample_state)
                session.resample_state = state
            else:
                # No resampling needed
                pcm_resampled = pcm_decoded
        except Exception as exc:
            logger.error("RTP payload decode failed", call_id=call_id, error=str(exc))
            return

        try:
            await self.engine_callback(call_id, ssrc, pcm_resampled)
        except Exception as exc:
            logger.error("Engine callback failed", call_id=call_id, error=str(exc))
        else:
            session.frames_processed += 1

    def get_session_info(self, call_id: str) -> Optional[Dict[str, Any]]:
        session = self.sessions.get(call_id)
        if not session:
            return None
        return {
            "call_id": session.call_id,
            "local_port": session.local_port,
            "remote_host": session.remote_host,
            "remote_port": session.remote_port,
            "ssrc": session.ssrc,
            "frames_received": session.frames_received,
            "frames_processed": session.frames_processed,
            "packet_loss_count": session.packet_loss_count,
            "last_sequence": session.last_sequence,
            "expected_sequence": session.expected_sequence,
            "created_at": session.created_at,
            "last_packet_at": session.last_packet_at,
        }

    def get_stats(self) -> Dict[str, Any]:
        now = time.time()
        active_sessions = sum(1 for s in self.sessions.values() if now - s.last_packet_at < 30)
        return {
            "running": self.running,
            "host": self.host,
            "port_range": self.port_range,
            "codec": self.codec,
            "sessions_total": len(self.sessions),
            "sessions_active": active_sessions,
            "frames_received": sum(s.frames_received for s in self.sessions.values()),
            "frames_processed": sum(s.frames_processed for s in self.sessions.values()),
            "packet_loss_total": sum(s.packet_loss_count for s in self.sessions.values()),
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _reserve_port(self, call_id: str) -> Optional[int]:
        start, end = self.port_range
        for port in range(start, end + 1):
            if port not in self.port_allocation:
                self.port_allocation[port] = call_id
                return port
        return None

    def _release_port(self, port: int) -> None:
        self.port_allocation.pop(port, None)

    async def _cleanup_session(self, session: RTPSession) -> None:
        call_id = session.call_id
        if session.receiver_task:
            session.receiver_task.cancel()
            try:
                await session.receiver_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("RTP receiver task finalisation error", call_id=call_id, error=str(exc))

        if session.socket:
            try:
                session.socket.close()
            except Exception:
                pass

        if session.local_port in self.port_allocation:
            self._release_port(session.local_port)
        if session.ssrc in self.ssrc_to_call_id:
            self.ssrc_to_call_id.pop(session.ssrc, None)

    def _normalise_codec(self, codec: str) -> str:
        value = (codec or "ulaw").lower()
        if value in ("mulaw", "g711_ulaw", "mu-law"):
            return "ulaw"
        if value in ("slin16", "linear16", "pcm16"):
            return "slin16"
        return value

    def _decode_payload(self, payload: bytes) -> bytes:
        if self.codec == "ulaw":
            return audioop.ulaw2lin(payload, 2)
        if self.codec == "slin16":
            return payload
        raise ValueError(f"Unsupported codec '{self.codec}'")

    def _build_rtp_header(self, sequence: int, timestamp: int, ssrc: int) -> bytes:
        version_p_x_cc = self.RTP_VERSION << 6
        payload_type = self._payload_type_byte()
        return struct.pack("!BBHII", version_p_x_cc, payload_type, sequence & 0xFFFF, timestamp & 0xFFFFFFFF, ssrc & 0xFFFFFFFF)

    def _payload_type_byte(self) -> int:
        if self.codec == "ulaw":
            return 0
        if self.codec == "slin16":
            return 11  # static payload type for L16/1 channel
        return 0

    def _socket_is_connected(self, sock: socket.socket) -> bool:
        try:
            sock.getpeername()
            return True
        except OSError:
            return False

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.get_event_loop()
