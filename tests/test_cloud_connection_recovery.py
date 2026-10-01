import asyncio
import errno
import socket
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from src.config.connection_recovery import CloudConnectionConfig
from src.providers.connection_recovery import (
    ConnectionHTTPError, connect_with_recovery, is_transient_connect_error,
)


@pytest.mark.asyncio
async def test_legacy_defaults_make_one_attempt():
    open_connection = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError):
        await connect_with_recovery(open_connection, SimpleNamespace(), provider="test", call_id="legacy")
    open_connection.assert_awaited_once_with(10.0)


@pytest.mark.asyncio
async def test_transient_failure_then_success_without_replaying_setup(monkeypatch):
    socket = SimpleNamespace(remote_address=("127.0.0.1", 443))
    open_connection = AsyncMock(side_effect=[ConnectionResetError(), socket])
    sleep = AsyncMock()
    monkeypatch.setattr("src.providers.connection_recovery.asyncio.sleep", sleep)
    result = await connect_with_recovery(open_connection, CloudConnectionConfig(connect_max_retries=1), provider="named", call_id="retry")
    assert result is socket
    assert open_connection.await_count == 2
    sleep.assert_awaited_once_with(0.25)


@pytest.mark.parametrize("exc", [
    ValueError("config"), ssl.SSLCertVerificationError(),
    socket.gaierror(socket.EAI_NONAME, "no host"), ConnectionHTTPError(401),
    ConnectionHTTPError(403), ConnectionHTTPError(429),
    InvalidStatus(Response(400, "Bad request", {}, b"")),
])
@pytest.mark.asyncio
async def test_permanent_errors_fail_immediately(exc):
    open_connection = AsyncMock(side_effect=exc)
    with pytest.raises(type(exc)):
        await connect_with_recovery(open_connection, CloudConnectionConfig(connect_max_retries=3), provider="test", call_id="permanent")
    assert open_connection.await_count == 1


@pytest.mark.parametrize("exc", [
    TimeoutError(), OSError(errno.EHOSTUNREACH, "host"),
    socket.gaierror(socket.EAI_AGAIN, "temporary"), ConnectionHTTPError(503),
    InvalidStatus(Response(502, "Bad gateway", {}, b"")),
])
def test_transient_classification(exc):
    assert is_transient_connect_error(exc)


@pytest.mark.asyncio
async def test_exhaustion_is_bounded(monkeypatch):
    monkeypatch.setattr("src.providers.connection_recovery.asyncio.sleep", AsyncMock())
    open_connection = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError):
        await connect_with_recovery(open_connection, CloudConnectionConfig(connect_max_retries=2), provider="test", call_id="exhausted")
    assert open_connection.await_count == 3


@pytest.mark.asyncio
async def test_total_deadline_cancels_preconnect_work():
    cancelled = asyncio.Event()
    async def opening(timeout):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    loop = asyncio.get_running_loop()
    start = loop.time()
    with pytest.raises(TimeoutError):
        await connect_with_recovery(opening, CloudConnectionConfig(connect_max_retries=3, connect_total_timeout_sec=0.03), provider="test", call_id="deadline")
    assert cancelled.is_set()
    assert loop.time() - start < 0.5


@pytest.mark.parametrize("phase", ["opening", "backoff"])
@pytest.mark.asyncio
async def test_hangup_cancels_without_another_attempt(monkeypatch, phase):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    open_connection = AsyncMock(side_effect=blocked if phase == "opening" else TimeoutError())
    if phase == "backoff":
        monkeypatch.setattr("src.providers.connection_recovery.asyncio.sleep", blocked)
    task = asyncio.create_task(connect_with_recovery(open_connection, CloudConnectionConfig(connect_max_retries=3), provider="test", call_id="hangup"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert open_connection.await_count == 1


@pytest.mark.asyncio
async def test_calls_have_independent_retry_state(monkeypatch):
    monkeypatch.setattr("src.providers.connection_recovery.asyncio.sleep", AsyncMock())
    first, second = object(), object()
    a = AsyncMock(side_effect=[TimeoutError(), first])
    b = AsyncMock(return_value=second)
    results = await asyncio.gather(*[
        connect_with_recovery(factory, CloudConnectionConfig(connect_max_retries=1), provider="test", call_id=call)
        for factory, call in [(a, "a"), (b, "b")]
    ])
    assert results == [first, second]
    assert a.await_count == 2 and b.await_count == 1


@pytest.mark.asyncio
async def test_logging_never_includes_exception_secrets(monkeypatch):
    logger = SimpleNamespace(info=AsyncMock(), warning=Mock())
    monkeypatch.setattr("src.providers.connection_recovery.logger", logger)
    with pytest.raises(ConnectionError):
        await connect_with_recovery(AsyncMock(side_effect=ConnectionError("wss://example?key=SECRET")), CloudConnectionConfig(), provider="test", call_id="safe")
    assert "SECRET" not in str(logger.warning.call_args)


@pytest.mark.parametrize("values", [
    {"connect_timeout_sec": 0}, {"connect_timeout_sec": float("nan")},
    {"connect_timeout_sec": float("inf")}, {"connect_timeout_sec": True},
    {"connect_max_retries": -1}, {"connect_max_retries": 4},
    {"connect_max_retries": 1.5}, {"connect_max_retries": True},
    {"connect_total_timeout_sec": 0}, {"connect_total_timeout_sec": float("inf")},
])
def test_policy_rejects_invalid_values(values):
    with pytest.raises(ValidationError):
        CloudConnectionConfig(**values)


@pytest.mark.parametrize("kind", ["google_live", "openai_realtime", "grok", "deepgram", "elevenlabs_agent"])
def test_runtime_configs_keep_defaults_and_explicit_zero(kind):
    from src.config import GoogleProviderConfig, OpenAIRealtimeProviderConfig, GrokProviderConfig, DeepgramProviderConfig
    from src.providers.elevenlabs_config import ElevenLabsAgentConfig
    cls = {"google_live": GoogleProviderConfig, "openai_realtime": OpenAIRealtimeProviderConfig,
           "grok": GrokProviderConfig, "deepgram": DeepgramProviderConfig, "elevenlabs_agent": ElevenLabsAgentConfig}[kind]
    for data in ({}, {"connect_max_retries": 0}):
        cfg = cls.from_dict(dict(data)) if kind == "elevenlabs_agent" else cls(**data)
        assert cfg.connect_timeout_sec == 10
        assert cfg.connect_max_retries == 0
        assert cfg.connect_total_timeout_sec is None
    data = {"connect_timeout_sec": 4, "connect_max_retries": 1, "connect_total_timeout_sec": 12}
    cfg = cls.from_dict(dict(data)) if kind == "elevenlabs_agent" else cls(**data)
    assert (cfg.connect_timeout_sec, cfg.connect_max_retries, cfg.connect_total_timeout_sec) == (4, 1, 12)


@pytest.mark.parametrize("kind", ["google_live", "openai_realtime", "grok", "deepgram", "elevenlabs_agent"])
@pytest.mark.asyncio
async def test_provider_retries_only_connection_before_setup(monkeypatch, kind):
    import importlib
    from unittest.mock import MagicMock
    from src.config import GoogleProviderConfig, OpenAIRealtimeProviderConfig, GrokProviderConfig, DeepgramProviderConfig
    from src.providers.elevenlabs_config import ElevenLabsAgentConfig

    classes = {
        "google_live": (GoogleProviderConfig, "GoogleLiveProvider", "_send_setup"),
        "openai_realtime": (OpenAIRealtimeProviderConfig, "OpenAIRealtimeProvider", "_send_session_update"),
        "grok": (GrokProviderConfig, "GrokProvider", "_send_session_update"),
        "deepgram": (DeepgramProviderConfig, "DeepgramProvider", "_configure_agent"),
        "elevenlabs_agent": (ElevenLabsAgentConfig, "ElevenLabsAgentProvider", "_send_session_config"),
    }
    module = importlib.import_module(f"src.providers.{kind}")
    config_cls, provider_cls, setup_name = classes[kind]
    cfg = config_cls(api_key="test", connect_max_retries=1, connect_timeout_sec=4, **({"agent_id": "test-agent"} if kind == "elevenlabs_agent" else {}))
    if kind == "deepgram":
        from src.config import LLMConfig
        instance = getattr(module, provider_cls)(cfg, LLMConfig(), AsyncMock())
    else:
        instance = getattr(module, provider_cls)(cfg, AsyncMock())
    instance.set_provider_identity(provider_key=f"customer_{kind}", provider_kind=kind)
    ws = MagicMock()
    ws.recv = AsyncMock(return_value='{"type":"session.created","session":{}}')
    ws.close = AsyncMock()
    ws.state.name = "OPEN"
    connect = AsyncMock(side_effect=[TimeoutError(), ws])
    monkeypatch.setattr(module.websockets, "connect", connect)
    monkeypatch.setattr("src.providers.connection_recovery.asyncio.sleep", AsyncMock())
    if kind == "elevenlabs_agent":
        instance._get_signed_url = AsyncMock(return_value="wss://example.invalid?token=test")
    class SetupBoundary(Exception):
        pass
    setup = AsyncMock(side_effect=SetupBoundary())
    setattr(instance, setup_name, setup)
    instance._receive_loop = AsyncMock()
    try:
        with pytest.raises(SetupBoundary):
            await instance.start_session("integration", context={"tools": []})
        assert connect.await_count == 2
        assert all(call.kwargs["open_timeout"] == 4 for call in connect.await_args_list)
        setup.assert_awaited_once()
        if kind == "elevenlabs_agent":
            assert instance._get_signed_url.await_count == 2
    finally:
        await instance.stop_session()


@pytest.mark.asyncio
async def test_real_websocket_handshake_retry(monkeypatch):
    """Exercise actual websockets rejection and socket cleanup on loopback."""
    import websockets
    from websockets.asyncio.server import serve
    attempts = 0
    async def reject_first(connection, request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return connection.respond(503, "temporary")
    async def handler(ws):
        await ws.wait_closed()
    async with serve(handler, "127.0.0.1", 0, process_request=reject_first) as server:
        port = server.sockets[0].getsockname()[1]
        ws = await connect_with_recovery(
            lambda timeout: websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=timeout, proxy=None),
            CloudConnectionConfig(connect_max_retries=1), provider="loopback", call_id="real-handshake",
        )
        assert attempts == 2
        await ws.close()


@pytest.mark.asyncio
async def test_http_proxy_rejection_is_retried_with_real_client():
    import websockets
    from websockets.asyncio.server import serve
    calls = 0
    async def reject_proxy(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    async def handler(ws):
        await ws.wait_closed()
    async with await asyncio.start_server(reject_proxy, "127.0.0.1", 0) as proxy:
        proxy_port = proxy.sockets[0].getsockname()[1]
        async with serve(handler, "127.0.0.1", 0) as upstream:
            port = upstream.sockets[0].getsockname()[1]
            async def opening(timeout):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return await websockets.connect("wss://example.invalid", proxy=f"http://127.0.0.1:{proxy_port}", open_timeout=timeout)
                return await websockets.connect(f"ws://127.0.0.1:{port}", proxy=None, open_timeout=timeout)
            ws = await connect_with_recovery(opening, CloudConnectionConfig(connect_max_retries=1), provider="proxy-test", call_id="proxy-handshake")
            assert calls == 2
            await ws.close()


def test_premature_handshake_disconnect_is_transient_but_bad_http_is_not():
    from websockets.exceptions import InvalidMessage
    disconnected = InvalidMessage("did not receive a valid HTTP response")
    disconnected.__cause__ = EOFError("connection closed")
    assert is_transient_connect_error(disconnected)
    assert not is_transient_connect_error(InvalidMessage("invalid HTTP response"))
