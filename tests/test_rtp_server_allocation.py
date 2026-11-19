"""Socket setup failures must not consume the shared RTP port range."""

import socket
from unittest.mock import AsyncMock, Mock

import pytest

from src.rtp_server import RTPServer


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["create", "bind", "setblocking"])
async def test_failed_socket_setup_releases_port_for_next_call(monkeypatch, failure_stage):
    server = RTPServer("127.0.0.1", 18180, AsyncMock())
    await server.start()
    factory = Mock()
    monkeypatch.setattr("src.rtp_server.socket.socket", factory)
    monkeypatch.setattr(server, "_rtp_receiver_loop", AsyncMock())
    try:
        # Use the sole available port repeatedly. A bind error happens before
        # RTPSession exists, so cleanup_session cannot release the reservation.
        for attempt in range(3):
            failed_socket = Mock()
            failure = OSError(f"synthetic {failure_stage} failure")
            factory.side_effect = failure if failure_stage == "create" else None
            factory.return_value = failed_socket
            if failure_stage != "create":
                getattr(failed_socket, failure_stage).side_effect = failure

            call_id = f"failed-{attempt}"
            with pytest.raises(OSError) as raised:
                await server.allocate_session(call_id)
            assert raised.value is failure
            await server.cleanup_session(call_id)
            assert server.port_allocation == {}
            assert server.sessions == {}
            assert server.session_tasks == {}
            if failure_stage == "create":
                failed_socket.close.assert_not_called()
            else:
                failed_socket.close.assert_called_once_with()

        # A later call can allocate the same port without restarting the server.
        healthy_socket = Mock()
        factory.reset_mock()
        factory.side_effect = None
        factory.return_value = healthy_socket
        assert await server.allocate_session("healthy") == 18180
        assert await server.allocate_session("healthy") == 18180
        factory.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
        healthy_socket.bind.assert_called_once_with(("127.0.0.1", 18180))
        healthy_socket.setblocking.assert_called_once_with(False)
        assert server.sessions["healthy"].socket is healthy_socket
        assert server.port_allocation == {18180: "healthy"}
        await server.cleanup_session("healthy")
        healthy_socket.close.assert_called_once_with()
        assert server.port_allocation == {}
        assert server.sessions == {}
        assert server.session_tasks == {}
    finally:
        await server.stop()
