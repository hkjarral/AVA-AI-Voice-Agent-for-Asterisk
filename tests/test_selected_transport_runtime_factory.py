from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.audio.transports import CompletionQuality
from src.audio.transports.factory import (
    TransportCallbacks,
    create_selected_transport_runtime,
)


def _callbacks():
    return TransportCallbacks(
        on_rtp_pcm=AsyncMock(),
        on_externalmedia_aux=AsyncMock(),
        on_audiosocket_uuid=AsyncMock(),
        on_audiosocket_audio=AsyncMock(),
        on_audiosocket_disconnect=AsyncMock(),
        on_audiosocket_dtmf=AsyncMock(),
        on_audiosocket_aux=AsyncMock(),
        on_websocket_audio=AsyncMock(),
        on_websocket_disconnect=AsyncMock(),
        on_websocket_dtmf=AsyncMock(),
        on_websocket_aux=AsyncMock(),
    )


class _FakeRTPServer:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.running = False

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False

    def get_stats(self):
        return {"running": self.running}

    async def cleanup_session(self, call_id):
        self.closed_call = call_id


class _FakeAudioSocketServer:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._server = None

    async def start(self):
        self._server = object()

    async def stop(self):
        self._server = None

    def get_connection_count(self):
        return 3

    async def disconnect(self, connection_id):
        self.closed_connection = connection_id


class _FakeWebSocketServer:
    def __init__(self, config, **kwargs):
        self.config = config
        self.kwargs = kwargs
        self.listening = False

    async def start(self):
        self.listening = True

    async def stop(self):
        self.listening = False

    def health(self):
        return {"listening": self.listening, "active_connections": 2}

    async def unregister_call(self, call_id):
        self.closed_call = call_id


@pytest.mark.asyncio
async def test_externalmedia_factory_delegates_lifecycle_and_callback(monkeypatch):
    monkeypatch.setattr("src.audio.transports.legacy.RTPServer", _FakeRTPServer)
    callbacks = _callbacks()
    config = SimpleNamespace(
        audio_transport="externalmedia",
        external_media=SimpleNamespace(
            rtp_host="127.0.0.1",
            rtp_port=18080,
            codec="ulaw",
            format="slin16",
            sample_rate=None,
            lock_remote_endpoint=True,
        ),
    )

    runtime = create_selected_transport_runtime(
        config,
        callbacks,
        rtp_port_range=(18080, 18090),
        rtp_allowed_remote_hosts=["127.0.0.1"],
    )

    assert runtime.kind == "externalmedia"
    assert runtime.server.kwargs["engine_callback"] is callbacks.on_rtp_pcm
    assert runtime.server.kwargs["sample_rate"] == 16000
    assert runtime.capabilities.completion_quality is CompletionQuality.QUIET_TAIL
    assert not runtime.ready()
    await runtime.start()
    assert runtime.ready()
    assert runtime.health() == {"running": True}
    await runtime.close_call("call-1")
    assert runtime.server.closed_call == "call-1"
    await runtime.stop()
    assert not runtime.ready()


@pytest.mark.asyncio
async def test_audiosocket_factory_is_thin_and_reports_quiet_tail(monkeypatch):
    monkeypatch.setattr(
        "src.audio.transports.legacy.AudioSocketServer", _FakeAudioSocketServer
    )
    callbacks = _callbacks()
    config = SimpleNamespace(
        audio_transport="audiosocket",
        audiosocket=SimpleNamespace(host="127.0.0.1", port=9092),
    )

    runtime = create_selected_transport_runtime(config, callbacks)

    assert runtime.server.kwargs["on_audio"] is callbacks.on_audiosocket_audio
    assert runtime.capabilities.completion_quality is CompletionQuality.QUIET_TAIL
    await runtime.start()
    assert runtime.ready()
    assert runtime.health()["active_connections"] == 3
    await runtime.close_call("call-1", connection_id="conn-1")
    assert runtime.server.closed_connection == "conn-1"
    await runtime.stop()


@pytest.mark.asyncio
async def test_websocket_factory_advertises_correlated_remote_control(monkeypatch):
    monkeypatch.setattr(
        "src.audio.transports.legacy.WebSocketMediaServer", _FakeWebSocketServer
    )
    callbacks = _callbacks()
    config = SimpleNamespace(
        audio_transport="websocket",
        websocket_media=SimpleNamespace(),
    )

    runtime = create_selected_transport_runtime(config, callbacks)

    assert runtime.server.kwargs["on_audio"] is callbacks.on_websocket_audio
    assert runtime.capabilities.completion_quality is CompletionQuality.CORRELATED_BOUNDARY
    assert runtime.capabilities.supports_remote_flush
    assert runtime.capabilities.supports_remote_flow_control
    assert runtime.capabilities.supports_per_call_codec
    await runtime.start()
    assert runtime.ready(asterisk_version="Asterisk 20.18.0")
    assert not runtime.ready(asterisk_version="Asterisk 20.17.0")
    await runtime.close_call("call-1")
    assert runtime.server.closed_call == "call-1"
    await runtime.stop()


def test_factory_rejects_unknown_transport():
    with pytest.raises(ValueError, match="Unsupported audio transport"):
        create_selected_transport_runtime(
            SimpleNamespace(audio_transport="mystery"),
            _callbacks(),
        )
