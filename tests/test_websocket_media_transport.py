import asyncio
import base64
import json
import random
import shutil
import ssl
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from src.config import supports_media_websocket as config_supports_media_websocket
from src.audio.transports.asterisk_websocket import (
    MAX_CONTROL_MESSAGE_BYTES,
    WebSocketMediaServer,
    WebSocketMediaBinding,
    supports_media_websocket,
)
from src.audio.transports.codec import (
    bytes_per_frame,
    decode_wire_audio,
    encode_wire_audio,
)


def _config(**overrides):
    config = {
        "bind_host": "127.0.0.1",
        "port": 0,
        "path": "/media",
        "auth": {"required": True, "username": "asterisk", "password": "secret"},
        "allowed_remote_hosts": ["127.0.0.1"],
        "media_start_timeout_ms": 500,
        "handshake_timeout_ms": 500,
        "drain_timeout_ms": 300,
        "output_wait_timeout_ms": 100,
        "pre_start_buffer_ms": 200,
        "max_connections": 4,
        "max_input_queue_frames": 2,
    }
    config.update(overrides)
    return config


def _headers():
    return {"Authorization": "Basic " + base64.b64encode(b"asterisk:secret").decode()}


def _start(nonce, channel_id="media-1", codec="ulaw"):
    return json.dumps(
        {
            "event": "MEDIA_START",
            "connection_id": "conn-1",
            "channel_id": channel_id,
            "format": codec,
            "optimal_frame_size": bytes_per_frame(codec, 20),
            "ptime": 20,
            "channel_variables": {"nonce": nonce},
        }
    )


async def _wait_until(predicate, timeout=0.5):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_media_start_binds_and_prestart_audio_does_not_block_reader():
    received = []
    disconnected = []

    async def on_audio(call_id, payload):
        received.append((call_id, payload))
        await asyncio.sleep(0.01)

    async def on_disconnect(call_id, reason):
        disconnected.append((call_id, reason))

    server = WebSocketMediaServer(_config(), on_audio, on_disconnect)
    await server.start()
    try:
        nonce = server.register_call("call-1", "media-1", "ulaw")
        host, port = server.address
        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}",
            subprotocols=["media"],
            additional_headers=_headers(),
        ) as ws:
            await ws.send(b"p" * 160)
            await ws.send(_start(nonce))
            binding = await server.wait_ready("call-1")
            assert (binding.codec, binding.sample_rate, binding.optimal_frame_size) == ("ulaw", 8000, 160)
            await ws.send(b"a" * 160)
            await asyncio.sleep(0.05)
            assert received == [("call-1", b"p" * 160), ("call-1", b"a" * 160)]
            snapshot = server.snapshot("call-1")
            assert snapshot["pending"] is False
            assert snapshot["queue"]["xoff"] is False
        await _wait_until(lambda: bool(disconnected))
        assert disconnected and disconnected[0][0] == "call-1"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_output_finish_and_abort_are_correlated_and_xoff_does_not_block_controls():
    async def noop_audio(*_args):
        return None

    async def noop_disconnect(*_args):
        return None

    server = WebSocketMediaServer(_config(output_wait_timeout_ms=10_000), noop_audio, noop_disconnect)
    await server.start()
    try:
        nonce = server.register_call("call-1", "media-1", "ulaw")
        host, port = server.address
        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}",
            subprotocols=["media"],
            additional_headers=_headers(),
        ) as ws:
            await ws.send(_start(nonce))
            await server.wait_ready("call-1")
            generation = await server.begin_output("call-1")
            assert json.loads(await ws.recv())["command"] == "START_MEDIA_BUFFERING"
            await ws.send(json.dumps({"event": "MEDIA_XOFF"}))
            await _wait_until(lambda: server.snapshot("call-1")["queue"]["xoff"])
            blocked_send = asyncio.create_task(server.send_audio("call-1", b"audio", generation))
            await asyncio.sleep(0.02)
            assert not blocked_send.done()

            abort = asyncio.create_task(server.abort_output("call-1"))
            assert json.loads(await ws.recv())["command"] == "FLUSH_MEDIA"
            mark = json.loads(await ws.recv())
            assert mark["command"] == "MARK_MEDIA"
            blocked_begin = asyncio.create_task(server.begin_output("call-1"))
            # An uncorrelated drain can never release the post-FLUSH fence.
            await ws.send(json.dumps({"event": "QUEUE_DRAINED"}))
            await asyncio.sleep(0.02)
            assert not abort.done()
            assert not blocked_begin.done()
            await ws.send(json.dumps({"event": "MEDIA_MARK_PROCESSED", "correlation_id": mark["correlation_id"]}))
            assert await abort is True
            # Generation cancellation wakes an XOFF-parked sender immediately;
            # it must not wait for the normal producer timeout.
            assert await asyncio.wait_for(blocked_send, 0.1) is False

            next_generation = await blocked_begin
            assert next_generation > generation
            assert json.loads(await ws.recv())["command"] == "START_MEDIA_BUFFERING"
            # FLUSH doesn't fabricate local XON. A new writer remains gated
            # until Asterisk explicitly reports the queue writable again.
            resumed_send = asyncio.create_task(server.send_audio("call-1", b"next", next_generation))
            await asyncio.sleep(0.02)
            assert not resumed_send.done()
            await ws.send(json.dumps({"event": "MEDIA_XON"}))
            assert await resumed_send is True
            assert await ws.recv() == b"next"
            finish = asyncio.create_task(server.finish_output("call-1", next_generation))
            stop = json.loads(await ws.recv())
            assert stop["command"] == "STOP_MEDIA_BUFFERING"
            await ws.send(json.dumps({"event": "MEDIA_BUFFERING_COMPLETED", "correlation_id": stop["correlation_id"]}))
            assert await finish is True
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_auth_path_subprotocol_and_duplicate_nonce_fail_closed():
    async def noop(*_args):
        return None

    server = WebSocketMediaServer(_config(), noop, noop)
    await server.start()
    try:
        nonce = server.register_call("call-1", "media-1", "ulaw")
        host, port = server.address
        with pytest.raises(InvalidStatus):
            async with connect(f"ws://{host}:{port}/wrong", subprotocols=["media"], additional_headers=_headers()):
                pass
        with pytest.raises(InvalidStatus):
            async with connect(f"ws://{host}:{port}/media?nonce={nonce}", additional_headers=_headers()):
                pass

        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
        ) as first:
            await first.send(_start(nonce))
            await server.wait_ready("call-1")
            async with connect(
                f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
            ) as duplicate:
                await duplicate.send(_start(nonce))
                with pytest.raises(Exception):
                    await duplicate.recv()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_xoff_timeout_fails_the_binding_but_barge_generation_does_not():
    disconnected = []

    async def noop_audio(*_args):
        return None

    async def on_disconnect(call_id, reason):
        disconnected.append((call_id, reason))

    server = WebSocketMediaServer(_config(output_wait_timeout_ms=30), noop_audio, on_disconnect)
    await server.start()
    try:
        nonce = server.register_call("call-1", "media-1", "ulaw")
        host, port = server.address
        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
        ) as ws:
            await ws.send(_start(nonce))
            await server.wait_ready("call-1")
            generation = await server.begin_output("call-1")
            await ws.recv()
            await ws.send(json.dumps({"event": "MEDIA_XOFF"}))
            await _wait_until(lambda: server.snapshot("call-1")["queue"]["xoff"])
            assert await server.send_audio("call-1", b"audio", generation) is False
            await _wait_until(lambda: bool(disconnected))
            assert "MEDIA_XON timeout" in disconnected[0][1]
            assert server.get_binding("call-1") is None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_xoff_between_writable_check_and_send_lock_retries_same_payload():
    async def noop(*_args):
        return None

    server = WebSocketMediaServer(_config(output_wait_timeout_ms=10_000), noop, noop)
    await server.start()
    try:
        nonce = server.register_call("call-1", "media-1", "ulaw")
        host, port = server.address
        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
        ) as ws:
            await ws.send(_start(nonce))
            binding = await server.wait_ready("call-1")
            generation = await server.begin_output("call-1")
            await ws.recv()
            # Make send_audio pass its first XON check, then queue behind a
            # held writer while the peer applies XOFF.
            await binding.send_lock.acquire()
            try:
                send = asyncio.create_task(server.send_audio("call-1", b"payload", generation))
                await asyncio.sleep(0.01)
                await ws.send(json.dumps({"event": "MEDIA_XOFF"}))
                await _wait_until(lambda: server.snapshot("call-1")["queue"]["xoff"])
            finally:
                binding.send_lock.release()
            await asyncio.sleep(0.02)
            assert not send.done()
            await ws.send(json.dumps({"event": "MEDIA_XON"}))
            assert await send is True
            assert await ws.recv() == b"payload"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_zero_prestart_buffer_rejects_binary_before_media_start():
    async def noop(*_args):
        return None

    server = WebSocketMediaServer(_config(pre_start_buffer_ms=0), noop, noop)
    await server.start()
    try:
        nonce = server.register_call("call-1", "media-1", "ulaw")
        host, port = server.address
        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
        ) as ws:
            await ws.send(b"not accepted")
            with pytest.raises(Exception):
                await ws.recv()
    finally:
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_patch",
    [
        {"channel_id": "different-media"},
        {"format": "opus"},
        {"ptime": 0},
        {"ptime": 10, "optimal_frame_size": 80},
        {"ptime": 40, "optimal_frame_size": 320},
        {"format": "slin", "optimal_frame_size": 321},
    ],
)
async def test_invalid_media_start_fails_registered_call_closed(event_patch):
    async def noop(*_args):
        return None

    server = WebSocketMediaServer(_config(), noop, noop)
    await server.start()
    try:
        nonce = server.register_call("call-1", "media-1", "ulaw")
        host, port = server.address
        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
        ) as ws:
            event = json.loads(_start(nonce))
            event.update(event_patch)
            await ws.send(json.dumps(event))
            with pytest.raises(Exception):
                await ws.recv()
            with pytest.raises(RuntimeError):
                await server.wait_ready("call-1", 0.1)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_prestart_buffer_is_bounded_at_largest_supported_wire_rate():
    async def noop(*_args):
        return None

    server = WebSocketMediaServer(_config(), noop, noop)
    await server.start()
    try:
        nonce = server.register_call("call-1", "media-1", "slin16")
        host, port = server.address
        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
        ) as ws:
            # 200 ms at 32 kB/s is 6400 bytes. One more byte must fail closed.
            await ws.send(b"x" * 6401)
            with pytest.raises(Exception):
                await ws.recv()
            with pytest.raises(RuntimeError):
                await server.wait_ready("call-1", 0.1)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_media_start_rejects_odd_signed_linear_frame_size():
    async def noop(*_args):
        return None

    server = WebSocketMediaServer(_config(), noop, noop)
    await server.start()
    try:
        nonce = server.register_call("call-1", "media-1", "slin")
        host, port = server.address
        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
        ) as ws:
            event = json.loads(_start(nonce, codec="slin"))
            event["optimal_frame_size"] = 321
            await ws.send(json.dumps(event))
            with pytest.raises(Exception):
                await ws.recv()
            with pytest.raises(RuntimeError):
                await server.wait_ready("call-1", 0.1)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_malformed_json_and_error_event_fail_closed_without_stale_completion():
    disconnected = []

    async def noop_audio(*_args):
        return None

    async def on_disconnect(call_id, reason):
        disconnected.append((call_id, reason))

    # A pre-start malformed command fails the registered socket promptly.
    malformed = WebSocketMediaServer(_config(), noop_audio, on_disconnect)
    await malformed.start()
    try:
        nonce = malformed.register_call("call-malformed", "media-malformed", "ulaw")
        host, port = malformed.address
        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
        ) as ws:
            await ws.send("{")
            with pytest.raises(Exception):
                await ws.recv()
            with pytest.raises(RuntimeError):
                await malformed.wait_ready("call-malformed", 0.1)
    finally:
        await malformed.stop()

    server = WebSocketMediaServer(_config(), noop_audio, on_disconnect)
    await server.start()
    try:
        nonce = server.register_call("call-1", "media-1", "ulaw")
        host, port = server.address
        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
        ) as ws:
            await ws.send(_start(nonce))
            await server.wait_ready("call-1")
            generation = await server.begin_output("call-1")
            await ws.recv()
            finish = asyncio.create_task(server.finish_output("call-1", generation))
            stop = json.loads(await ws.recv())
            await ws.send(json.dumps({"event": "ERROR", "correlation_id": stop["correlation_id"]}))
            assert await finish is False
            await _wait_until(lambda: server.get_binding("call-1") is None)
            await _wait_until(lambda: bool(disconnected))
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_finish_requires_matching_ack_and_compact_control_limit():
    async def noop(*_args):
        return None

    server = WebSocketMediaServer(_config(), noop, noop)
    await server.start()
    try:
        nonce = server.register_call("call-1", "media-1", "ulaw")
        host, port = server.address
        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
        ) as ws:
            await ws.send(_start(nonce))
            binding = await server.wait_ready("call-1")
            generation = await server.begin_output("call-1")
            await ws.recv()
            finish = asyncio.create_task(server.finish_output("call-1", generation))
            stop = json.loads(await ws.recv())
            await ws.send(json.dumps({"event": "MEDIA_BUFFERING_COMPLETED", "correlation_id": "old"}))
            await asyncio.sleep(0.01)
            assert not finish.done()
            await ws.send(json.dumps({"event": "MEDIA_BUFFERING_COMPLETED", "correlation_id": stop["correlation_id"]}))
            assert await finish is True
            # A late duplicate acknowledgement is harmless and cannot complete
            # a subsequent output generation.
            await ws.send(json.dumps({"event": "MEDIA_BUFFERING_COMPLETED", "correlation_id": stop["correlation_id"]}))

            base = {"command": "MARK_MEDIA", "correlation_id": ""}
            prefix_len = len(json.dumps(base, separators=(",", ":")).encode())
            at_limit = {"command": "MARK_MEDIA", "correlation_id": "a" * (MAX_CONTROL_MESSAGE_BYTES - prefix_len)}
            assert len(json.dumps(at_limit, separators=(",", ":")).encode()) == MAX_CONTROL_MESSAGE_BYTES
            assert await server._send_command(binding, at_limit)
            assert json.loads(await ws.recv()) == at_limit
            too_large = dict(at_limit, correlation_id=at_limit["correlation_id"] + "a")
            with pytest.raises(ValueError):
                await server._send_command(binding, too_large)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_remote_close_once_unregister_and_stop_leave_no_callback_tasks():
    disconnected = []

    async def noop_audio(*_args):
        return None

    async def on_disconnect(call_id, reason):
        disconnected.append((call_id, reason))

    server = WebSocketMediaServer(_config(), noop_audio, on_disconnect)
    await server.start()
    nonce = server.register_call("call-1", "media-1", "ulaw")
    host, port = server.address
    async with connect(
        f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
    ) as ws:
        await ws.send(_start(nonce))
        await server.wait_ready("call-1")
    await _wait_until(lambda: len(disconnected) == 1)
    await server.unregister_call("call-1")
    await asyncio.sleep(0)
    assert len(disconnected) == 1

    nonce = server.register_call("call-2", "media-2", "ulaw")
    async with connect(
        f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
    ) as ws:
        await ws.send(_start(nonce, "media-2"))
        await server.wait_ready("call-2")
        await server.stop()
        with pytest.raises(Exception):
            await ws.recv()
    assert len(disconnected) == 2
    assert not server._callback_tasks


@pytest.mark.asyncio
async def test_disconnect_callback_can_stop_server_without_self_await():
    holder = {}
    callback_finished = asyncio.Event()

    async def noop_audio(*_args):
        return None

    async def on_disconnect(*_args):
        await holder["server"].stop()
        callback_finished.set()

    server = WebSocketMediaServer(_config(), noop_audio, on_disconnect)
    holder["server"] = server
    await server.start()
    try:
        nonce = server.register_call("call-1", "media-1", "ulaw")
        host, port = server.address
        async with connect(
            f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
        ) as ws:
            await ws.send(_start(nonce))
            await server.wait_ready("call-1")
        await asyncio.wait_for(callback_finished.wait(), 0.5)
        assert server.listening is False
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_abort_mark_timeout_and_task_cancellation_close_ready_binding():
    disconnected = []

    async def noop_audio(*_args):
        return None

    async def on_disconnect(call_id, reason):
        disconnected.append((call_id, reason))

    async def connect_ready(server, call_id, channel_id):
        nonce = server.register_call(call_id, channel_id, "ulaw")
        host, port = server.address
        ws = await connect(
            f"ws://{host}:{port}/media?nonce={nonce}", subprotocols=["media"], additional_headers=_headers()
        )
        await ws.send(_start(nonce, channel_id))
        await server.wait_ready(call_id)
        await server.begin_output(call_id)
        await ws.recv()
        return ws

    timeout_server = WebSocketMediaServer(_config(drain_timeout_ms=30), noop_audio, on_disconnect)
    await timeout_server.start()
    try:
        async with await connect_ready(timeout_server, "timeout", "media-timeout") as ws:
            assert await timeout_server.abort_output("timeout") is False
            # FLUSH and MARK were emitted, but no matching mark arrived.
            assert json.loads(await ws.recv())["command"] == "FLUSH_MEDIA"
            assert json.loads(await ws.recv())["command"] == "MARK_MEDIA"
            await _wait_until(lambda: timeout_server.get_binding("timeout") is None)
        await _wait_until(lambda: any("MEDIA_MARK_PROCESSED timeout" in reason for _, reason in disconnected))
    finally:
        await timeout_server.stop()

    cancel_server = WebSocketMediaServer(_config(drain_timeout_ms=10_000), noop_audio, on_disconnect)
    await cancel_server.start()
    try:
        async with await connect_ready(cancel_server, "cancel", "media-cancel") as ws:
            abort = asyncio.create_task(cancel_server.abort_output("cancel"))
            assert json.loads(await ws.recv())["command"] == "FLUSH_MEDIA"
            assert json.loads(await ws.recv())["command"] == "MARK_MEDIA"
            abort.cancel()
            with pytest.raises(asyncio.CancelledError):
                await abort
            await _wait_until(lambda: cancel_server.get_binding("cancel") is None)
        await _wait_until(lambda: any("mark wait cancelled" in reason for _, reason in disconnected))
    finally:
        await cancel_server.stop()


@pytest.mark.parametrize(
    ("version", "expected"),
    [("20.17.0", False), ("20.18.0", True), ("21.11.0", False), ("22.8.0", True), ("23.2.0", True)],
)
def test_json_control_version_floors(version, expected):
    assert supports_media_websocket(version) is expected


@pytest.mark.parametrize(
    "version",
    [None, "", "20.17.9", "20.18.0", "21.99.0", "22.8.0", "23.2.0", "24.0.0", "24.0 then 20.18"],
)
def test_transport_and_config_version_gates_stay_equivalent(version):
    assert supports_media_websocket(version) is config_supports_media_websocket(version)


def test_companded_and_linear_codec_helpers_round_trip_and_frame_sizes():
    pcm = b"\x00\x00\x01\x80\xff\x7f\x00\x00"
    for codec in ("ulaw", "alaw", "slin", "slin16"):
        encoded = encode_wire_audio(pcm, codec)
        decoded = decode_wire_audio(encoded, codec)
        assert len(decoded) == len(pcm)
    assert bytes_per_frame("ulaw", 20) == 160
    assert bytes_per_frame("alaw", 20) == 160
    assert bytes_per_frame("slin", 20) == 320
    assert bytes_per_frame("slin16", 20) == 640
    assert len(json.dumps({"command": "MARK_MEDIA", "correlation_id": "m1234567890"}, separators=(",", ":")).encode()) <= MAX_CONTROL_MESSAGE_BYTES


@pytest.mark.asyncio
@pytest.mark.parametrize("codec,rate", [("ulaw", 8000), ("alaw", 8000), ("slin", 8000), ("slin16", 16000)])
async def test_inbound_messages_are_reframed_without_losing_split_samples(codec, rate):
    async def noop(*args):
        pass
    server = WebSocketMediaServer(_config(), noop, noop)
    size = bytes_per_frame(codec, 20)
    binding = WebSocketMediaBinding("call", "media", codec, rate, size, 20, "conn", input_queue=asyncio.Queue(4))
    payload = bytes(index % 256 for index in range(size * 3))
    server._queue_audio(binding, payload[:3])
    assert binding.input_queue.empty()
    server._queue_audio(binding, payload[3:size + 1])
    assert await binding.input_queue.get() == payload[:size]
    server._queue_audio(binding, payload[size + 1:])
    assert await binding.input_queue.get() == payload[size:size * 2]
    assert await binding.input_queue.get() == payload[size * 2:]
    assert binding.input_remainder == b""
    server._queue_audio(binding, payload * 2 + b"x")
    assert binding.input_queue.qsize() == 4
    assert binding.input_drops == 2
    assert binding.input_remainder == b"x"


@pytest.mark.asyncio
@pytest.mark.parametrize("codec,rate", [("ulaw", 8000), ("alaw", 8000), ("slin", 8000), ("slin16", 16000)])
async def test_seeded_fragmentation_preserves_complete_frames_and_bounded_remainder(codec, rate):
    """Exercise arbitrary message cuts, including cuts inside PCM16 samples."""
    server = WebSocketMediaServer(_config(), AsyncMock(), AsyncMock())
    size = bytes_per_frame(codec, 20)
    rng = random.Random(20260906)
    for _ in range(50):
        payload = rng.randbytes(size * rng.randint(1, 24) + rng.randrange(size))
        binding = WebSocketMediaBinding(
            "call", "media", codec, rate, size, 20, "conn",
            input_queue=asyncio.Queue(32),
        )
        position = 0
        while position < len(payload):
            chunk_size = rng.randint(1, size * 3)
            server._queue_audio(binding, payload[position:position + chunk_size])
            position += chunk_size
            assert len(binding.input_remainder) < size
            assert binding.input_queue.qsize() <= 32
        frames = []
        while not binding.input_queue.empty():
            frame = binding.input_queue.get_nowait()
            assert len(frame) == size
            frames.append(frame)
        assert b"".join(frames) + binding.input_remainder == payload
        assert binding.input_drops == 0


@pytest.mark.asyncio
async def test_seeded_unrelated_control_events_cannot_resolve_current_boundaries():
    """Malformed and stale correlation values must never cross generations."""
    server = WebSocketMediaServer(_config(), AsyncMock(), AsyncMock())
    binding = WebSocketMediaBinding("call", "media", "ulaw", 8000, 160, 20, "conn")
    loop = asyncio.get_running_loop()
    finish = loop.create_future()
    mark = loop.create_future()
    binding.finish_waiters["current-finish"] = finish
    binding.mark_waiters["current-mark"] = mark
    rng = random.Random(20260906)
    for _ in range(300):
        event_name = rng.choice([
            "QUEUE_DRAINED", "MEDIA_BUFFERING_COMPLETED", "MEDIA_MARK_PROCESSED",
            "ERROR", "FUTURE_UNKNOWN_EVENT",
        ])
        correlation = rng.choice([None, False, 0, [], {}, "", "old-generation"])
        await server._handle_event(binding, event_name, {"correlation_id": correlation})
        assert not finish.done()
        assert not mark.done()
    # A valid id on the wrong event type is also insufficient.
    await server._handle_event(binding, "MEDIA_MARK_PROCESSED", {"correlation_id": "current-finish"})
    await server._handle_event(binding, "MEDIA_BUFFERING_COMPLETED", {"correlation_id": "current-mark"})
    assert not finish.done() and not mark.done()
    await server._handle_event(binding, "MEDIA_BUFFERING_COMPLETED", {"correlation_id": "current-finish"})
    await server._handle_event(binding, "MEDIA_MARK_PROCESSED", {"correlation_id": "current-mark"})
    assert finish.result() is True
    assert mark.result() is True


@pytest.mark.asyncio
async def test_closed_binding_wakes_output_start_parked_behind_flush():
    async def noop(*args):
        pass
    server = WebSocketMediaServer(_config(), noop, noop)
    server.register_call("call", "media", "ulaw")
    slot = server._calls["call"]
    binding = WebSocketMediaBinding("call", "media", "ulaw", 8000, 160, 20, "conn")
    slot.binding = binding
    binding.admission.clear()
    start = asyncio.create_task(server.begin_output("call"))
    await asyncio.sleep(0)
    await server._close_binding(slot, binding, "test disconnect")
    with pytest.raises(RuntimeError, match="not connected"):
        await asyncio.wait_for(start, 0.5)
    await server.stop()


@pytest.mark.asyncio
async def test_post_upgrade_admission_cap_cannot_be_bypassed_by_handshake_race():
    async def noop(*args):
        pass
    server = WebSocketMediaServer(_config(max_connections=1), noop, noop)
    nonce = server.register_call("call", "media", "ulaw")
    server._sockets.add(object())
    candidate = SimpleNamespace(aava_nonce=nonce, close=AsyncMock())
    await server._handle_socket(candidate)
    candidate.close.assert_awaited_once_with(code=1013, reason="media connection limit")
    assert len(server._sockets) == 1
    with pytest.raises(RuntimeError, match="connection limit"):
        await server.wait_ready("call")
    server._sockets.clear()
    await server.stop()


@pytest.mark.asyncio
async def test_input_callback_can_unregister_its_own_call_without_self_await():
    async def on_audio(call_id, payload):
        await server.unregister_call(call_id)
    async def noop(*args):
        pass
    server = WebSocketMediaServer(_config(), on_audio, noop)
    server.register_call("call", "media", "ulaw")
    binding = WebSocketMediaBinding("call", "media", "ulaw", 8000, 160, 20, "conn", input_queue=asyncio.Queue(2))
    server._calls["call"].binding = binding
    binding.input_task = asyncio.create_task(server._consume_audio(binding))
    server._queue_audio(binding, b"\xff" * 160)
    await asyncio.wait_for(binding.input_task, 0.5)
    assert binding.state == "closed"
    assert server.get_binding("call") is None
    await server.stop()


@pytest.mark.asyncio
async def test_wss_requires_trusted_certificate_and_keeps_media_auth(tmp_path):
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl required to generate an ephemeral test certificate")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run([
        openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        "-keyout", str(key), "-out", str(cert),
    ], check=True, capture_output=True, timeout=10)
    async def noop(*args):
        pass
    server = WebSocketMediaServer(_config(tls={"enabled": True, "cert_file": str(cert), "key_file": str(key)}), noop, noop)
    await server.start()
    try:
        nonce = server.register_call("call", "media", "ulaw")
        host, port = server.address
        uri = f"wss://{host}:{port}/media?nonce={nonce}"
        with pytest.raises(ssl.SSLCertVerificationError):
            async with connect(uri, ssl=ssl.create_default_context(), subprotocols=["media"], additional_headers=_headers()):
                pass
        trust = ssl.create_default_context(cafile=str(cert))
        with pytest.raises(InvalidStatus) as error:
            async with connect(uri, ssl=trust, subprotocols=["media"]):
                pass
        assert error.value.response.status_code == 401
        async with connect(uri, ssl=trust, subprotocols=["media"], additional_headers=_headers()) as ws:
            await ws.send(_start(nonce, "media"))
            assert (await server.wait_ready("call")).codec == "ulaw"
    finally:
        await server.stop()


def test_unicode_credentials_compare_safely_and_unicode_nonce_is_rejected():
    async def noop(*args):
        pass
    server = WebSocketMediaServer(_config(auth={"required": True, "username": "asterisk", "password": "test-é"}), noop, noop)
    header = "Basic " + base64.b64encode("asterisk:test-é".encode()).decode()
    assert server._valid_basic_auth(header)
    wrong = "Basic " + base64.b64encode("aé:wrong".encode()).decode()
    assert not server._valid_basic_auth(wrong)
    server.register_call("call", "media", "ulaw")
    assert server._slot_for_nonce("nonce-é") is None


@pytest.mark.asyncio
async def test_closed_binding_reports_no_pending_output():
    async def noop(*args):
        pass
    server = WebSocketMediaServer(_config(), noop, noop)
    server.register_call("call", "media", "ulaw")
    slot = server._calls["call"]
    binding = WebSocketMediaBinding("call", "media", "ulaw", 8000, 160, 20, "conn")
    slot.binding = binding
    binding.output_state = "buffering"
    binding.queue_frames = 7
    await server._close_binding(slot, binding, "peer closed")
    snapshot = server.snapshot("call")
    assert snapshot["output_state"] == "closed"
    assert snapshot["queue"]["frames"] is None
    await server.stop()


@pytest.mark.asyncio
async def test_status_queue_not_full_restores_writable():
    async def noop(*args):
        pass
    server = WebSocketMediaServer(_config(), noop, noop)
    binding = WebSocketMediaBinding("call", "media", "ulaw", 8000, 160, 20, "conn")
    await server._handle_event(binding, "STATUS", {"queue_full": True, "queue_frames": 9})
    assert not binding.xon.is_set()
    await server._handle_event(binding, "STATUS", {"queue_full": False, "queue_frames": 0})
    assert binding.xon.is_set()
