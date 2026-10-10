"""Bounded retries before a provider has started any session side effects.

Providers own authentication, socket options and setup. This utility never
replays setup, greetings, tools or an established session.
"""
import asyncio
import aiohttp
import errno
import socket
import ssl
from collections.abc import Mapping
from typing import Awaitable, Callable, TypeVar

from structlog import get_logger
from websockets.exceptions import InvalidMessage, InvalidProxyMessage, InvalidStatus, InvalidProxyStatus

from src.config.connection_recovery import CloudConnectionConfig

logger = get_logger(__name__)
T = TypeVar("T")


class ConnectionHTTPError(ConnectionError):
    """A pre-connect HTTP rejection without URL, credentials or response body."""
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Provider connection HTTP status {status}")


def is_transient_connect_error(exc: Exception) -> bool:
    if isinstance(exc, (ssl.SSLError, ssl.CertificateError, aiohttp.ClientSSLError)):
        return False
    if isinstance(exc, (InvalidStatus, InvalidProxyStatus, ConnectionHTTPError)):
        status = exc.response.status_code if isinstance(exc, (InvalidStatus, InvalidProxyStatus)) else exc.status
        # Rate limiting is deliberately not retried on a short call-start budget.
        return status in {500, 502, 503, 504}
    if isinstance(exc, (InvalidMessage, InvalidProxyMessage)):
        # websockets wraps an opening-handshake disconnect in InvalidMessage.
        return isinstance(exc.__cause__, EOFError)
    if isinstance(exc, socket.gaierror):
        return exc.errno == socket.EAI_AGAIN
    if isinstance(exc, (TimeoutError, ConnectionError, EOFError, aiohttp.ClientConnectionError)):
        return True
    if isinstance(exc, OSError):
        return exc.errno in {
            errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED,
            errno.ETIMEDOUT, errno.ENETUNREACH, errno.EHOSTUNREACH,
            errno.ENETDOWN, errno.EPIPE,
        }
    return False


async def connect_with_recovery(
    open_connection: Callable[[float], Awaitable[T]],
    config,
    *,
    provider: str,
    call_id: str,
    started_at: float | None = None,
) -> T:
    """Open a socket once by default; optionally retry transient failures.

    The factory must honor the supplied socket-opening timeout. An optional
    total deadline also bounds pre-connect authentication and backoff. Each
    call has independent state; CancelledError always propagates.
    """
    policy = CloudConnectionConfig.model_validate({
        name: config.get(name, default) if isinstance(config, Mapping) else getattr(config, name, default)
        for name, default in (
            ("connect_timeout_sec", 10.0), ("connect_max_retries", 0),
            ("connect_total_timeout_sec", None),
        )
    })
    loop = asyncio.get_running_loop()
    started = started_at if started_at is not None else loop.time()
    deadline = started + policy.connect_total_timeout_sec if policy.connect_total_timeout_sec is not None else None
    attempts = policy.connect_max_retries + 1
    for attempt in range(1, attempts + 1):
        try:
            if deadline is None:
                ws = await open_connection(policy.connect_timeout_sec)
            else:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError("Provider connection budget exhausted")
                ws = await asyncio.wait_for(
                    open_connection(min(policy.connect_timeout_sec, remaining)),
                    timeout=remaining,
                )
            # Peer information is only available after a successful handshake.
            logger.info(
                "Provider connection established", provider=provider, call_id=call_id,
                attempt=attempt, elapsed_sec=round(loop.time() - started, 3),
                peer_address=getattr(ws, "remote_address", None),
            )
            return ws
        except asyncio.CancelledError:
            logger.info("Provider connection cancelled", provider=provider, call_id=call_id, attempt=attempt)
            raise
        except Exception as exc:
            retry = attempt < attempts and is_transient_connect_error(exc)
            delay = min(0.25 * (2 ** (attempt - 1)), 1.0)
            if deadline is not None and loop.time() + delay >= deadline:
                retry = False
            # Exception strings/URLs can contain API keys or signed tokens.
            logger.warning(
                "Provider connection attempt failed", provider=provider, call_id=call_id,
                attempt=attempt, max_attempts=attempts,
                elapsed_sec=round(loop.time() - started, 3),
                error_type=type(exc).__name__, retrying=retry,
            )
            if not retry:
                raise
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
