import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.client import connect

from src.media_transport_capabilities import resolve_media_websocket_control
from src.audio.transports.websocket_control import decode_control, encode_control
from src.audio.transports.asterisk_websocket import WebSocketMediaServer
from src.audio.transports.legacy import WebSocketRuntime
from tests.test_websocket_media_transport import _config, _headers, _start


@pytest.mark.parametrize("version,requested,expected", [
    ("20.17.0-1.sng7", "auto", "plain"), ("20.17.0", "plain", "plain"),
    ("20.17.0", "json", None), ("20.17.1", "auto", None),
    ("20.17", "auto", None), ("20.16.0", "plain", None),
    ("22.10.1", "auto", "json"), ("22.10.1", "plain", None),
    ("21.12.0", "auto", None), ("24.0.0 build 20.17.0", "auto", None),
    (None, "auto", None), ("22.10.1", "oops", None),
])
def test_capability_is_explicit_and_fail_closed(version, requested, expected):
    assert resolve_media_websocket_control(version, requested) == expected


@pytest.mark.parametrize("message", [
    '{}', 'MEDIA_START ptime:20 ptime:40', 'MEDIA_START ptime:bad',
    'MEDIA_BUFFERING_COMPLETED', 'MEDIA_BUFFERING_COMPLETED a b',
    'MEDIA_XON\n', 'MEDIA_START event:MEDIA_XON',
])
def test_plain_parser_rejects_malformed(message):
    with pytest.raises(ValueError):
        decode_control(message, "plain")


def test_plain_status_and_dtmf_are_normalized():
    assert decode_control('STATUS queue_length:900 queue_full:true', 'plain')['queue_frames'] == 900
    assert decode_control('DTMF_END digit:5 channel_id:a', 'plain')['digit'] == '5'
    with pytest.raises(ValueError):
        encode_control({'command': 'MARK_MEDIA'}, 'plain')


@pytest.mark.asyncio
@pytest.mark.parametrize("codec,frame", [("ulaw", 160), ("alaw", 160), ("slin", 320), ("slin16", 640)])
async def test_plain_audio_completion_flush_stale_ids_and_cleanup(codec, frame):
    received, dtmf, disconnected = AsyncMock(), AsyncMock(), AsyncMock()
    server = WebSocketMediaServer(_config(), received, disconnected, dtmf)
    await server.start()
    try:
        nonce = server.register_call('c', 'm', codec, control_format='plain')
        host, port = server.address
        async with connect(f'ws://{host}:{port}/media?nonce={nonce}', subprotocols=['media'], additional_headers=_headers()) as ws:
            await ws.send(f'MEDIA_START connection_id:x channel_id:m format:{codec} optimal_frame_size:{frame} ptime:20')
            binding = await server.wait_ready('c')
            assert binding.control_format == 'plain'
            await ws.send(bytes(frame))
            await ws.send('DTMF_END digit:5 channel_id:m')
            generation = await server.begin_output('c')
            assert await ws.recv() == 'START_MEDIA_BUFFERING'
            assert await server.send_audio('c', bytes(frame), generation)
            assert await ws.recv() == bytes(frame)
            finish = asyncio.create_task(server.finish_output('c', generation))
            stop = await ws.recv()
            completion = stop.split()[1]
            await ws.send(f'MEDIA_BUFFERING_COMPLETED {completion}')
            assert await finish
            for _ in range(3):
                generation = await server.begin_output('c')
                assert await ws.recv() == 'START_MEDIA_BUFFERING'
                await ws.send('MEDIA_XOFF')
                await asyncio.sleep(0.01)
                writer = asyncio.create_task(server.send_audio('c', bytes(frame), generation))
                abort = asyncio.create_task(server.abort_output('c'))
                assert await ws.recv() == 'FLUSH_MEDIA'
                assert await ws.recv() == 'CONTINUE_MEDIA'
                fence = (await ws.recv()).split()[1]
                await ws.send(f'MEDIA_BUFFERING_COMPLETED {completion}')
                await ws.send(f'MEDIA_MARK_PROCESSED correlation_id:{fence}')
                await ws.send('QUEUE_DRAINED')
                await asyncio.sleep(0.01)
                assert not abort.done()
                assert await writer is False
                await ws.send(f'MEDIA_BUFFERING_COMPLETED {fence}')
                assert await abort
                assert not binding.xon.is_set()  # acknowledgement is not an invented XON
                await ws.send('MEDIA_XON')
            await asyncio.sleep(0.01)
            received.assert_awaited()
            dtmf.assert_awaited_once_with('c', '5')
        await server.unregister_call('c')
        assert server.get_binding('c') is None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_no_protocol_guessing_or_fallback():
    server = WebSocketMediaServer(_config(), AsyncMock(), AsyncMock())
    await server.start()
    try:
        nonce = server.register_call('c', 'm', 'ulaw', control_format='plain')
        host, port = server.address
        async with connect(f'ws://{host}:{port}/media?nonce={nonce}', subprotocols=['media'], additional_headers=_headers()) as ws:
            await ws.send(_start(nonce, channel_id='m'))
            await ws.wait_closed()
            assert ws.close_code == 1008
        with pytest.raises(RuntimeError):
            await server.wait_ready('c')
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_runtime_originate_mode_is_pinned_and_json_path_unchanged():
    version = ['20.17.0']
    config = SimpleNamespace(websocket_media=SimpleNamespace(control_format='auto'), asterisk=SimpleNamespace(app_name='aava'))
    runtime = WebSocketRuntime(config, on_audio=AsyncMock(), on_disconnect=AsyncMock(), on_dtmf=AsyncMock(), on_aux=AsyncMock(), asterisk_version=lambda: version[0])
    request = await runtime.prepare_call(SimpleNamespace(call_id='plain'))
    assert request.operation == 'websocket_originate'
    assert request.ari_params['channelId'] == request.channel_id
    assert request.ari_params['endpoint'].startswith('WebSocket/aava_media/c(ulaw)v(nonce=')
    version[0] = '22.10.1'
    newer = await runtime.prepare_call(SimpleNamespace(call_id='json'))
    assert newer.operation == 'external_media'
    assert newer.ari_params['transport_data'].startswith('f(json)v(nonce=')
    assert runtime.server._calls['plain'].control_format == 'plain'
    assert runtime.server._calls['json'].control_format == 'json'
    version[0] = '21.12.0'
    with pytest.raises(ValueError):
        await runtime.prepare_call(SimpleNamespace(call_id='unsupported'))


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_plain_missing_fence_or_cancellation_fails_closed(cancel):
    server = WebSocketMediaServer(_config(drain_timeout_ms=100), AsyncMock(), AsyncMock())
    await server.start()
    try:
        nonce = server.register_call('c', 'm', 'ulaw', control_format='plain')
        host, port = server.address
        async with connect(f'ws://{host}:{port}/media?nonce={nonce}', subprotocols=['media'], additional_headers=_headers()) as ws:
            await ws.send('MEDIA_START connection_id:x channel_id:m format:ulaw optimal_frame_size:160 ptime:20')
            binding = await server.wait_ready('c')
            abort = asyncio.create_task(server.abort_output('c'))
            for _ in range(3):
                await ws.recv()
            if cancel:
                abort.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await abort
            else:
                assert await abort is False
            assert binding.state != 'ready'
    finally:
        await server.stop()
