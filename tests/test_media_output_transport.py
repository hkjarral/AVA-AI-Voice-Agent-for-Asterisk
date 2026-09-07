from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.audio.transports.base import CompletionQuality
from src.audio.transports.output import OutputFrame, create_output_transport


@pytest.mark.asyncio
@pytest.mark.parametrize("codec,expected", [
    ("ulaw", b"\x34\x12\xfe\xff"), ("alaw", b"\x34\x12\xfe\xff"),
    ("slin", b"\x12\x34\xff\xfe"), ("slin16", b"\x12\x34\xff\xfe"),
])
async def test_rtp_delegate_preserves_legacy_wire_order_and_ssrc(codec, expected):
    server = SimpleNamespace(send_audio=AsyncMock(return_value=True))
    output = create_output_transport("externalmedia", lambda: server)
    assert await output.write(OutputFrame("call", b"\x34\x12\xfe\xff", codec, 8000, ssrc=42))
    server.send_audio.assert_awaited_once_with("call", expected, ssrc=42)
    assert output.completion_quality == CompletionQuality.QUIET_TAIL
    assert not output.supports_remote_flush


@pytest.mark.asyncio
async def test_audiosocket_delegate_preserves_connection_codec_and_rate():
    server = SimpleNamespace(send_audio=AsyncMock(return_value=True))
    output = create_output_transport("audiosocket", lambda: server)
    frame = OutputFrame("call", b"\x34\x12", "slin16", 16000, connection_id="connection")
    assert await output.write(frame)
    server.send_audio.assert_awaited_once_with("connection", frame.payload, encoding="slin16", sample_rate=16000)
    assert not await output.write(OutputFrame("call", b"a", "ulaw", 8000))
    assert output.completion_quality == CompletionQuality.QUIET_TAIL


@pytest.mark.asyncio
async def test_websocket_delegate_preserves_generation_and_correlated_operations():
    server = SimpleNamespace(
        send_audio=AsyncMock(return_value=True), begin_output=AsyncMock(return_value=4),
        finish_output=AsyncMock(return_value=True), abort_output=AsyncMock(return_value=True),
    )
    output = create_output_transport("websocket", lambda: server)
    generation = await output.begin_output("call")
    assert await output.write(OutputFrame("call", b"\x34\x12", "slin16", 16000, generation=generation))
    server.send_audio.assert_awaited_once_with("call", b"\x34\x12", 4)
    assert not await output.write(OutputFrame("call", b"a", "ulaw", 8000))
    assert await output.finish_output("call", generation)
    server.finish_output.assert_awaited_once_with("call", 4)
    assert await output.abort_output("call")
    server.abort_output.assert_awaited_once_with("call")
    assert output.completion_quality == CompletionQuality.CORRELATED_BOUNDARY
    assert output.supports_remote_flush


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["audiosocket", "externalmedia", "websocket"])
async def test_unavailable_output_is_fail_closed(kind):
    output = create_output_transport(kind, lambda: None)
    assert not await output.write(OutputFrame("call", b"a", "ulaw", 8000, generation=1))


def test_unknown_transport_is_not_silently_treated_as_rtp():
    with pytest.raises(ValueError, match="Unsupported audio transport"):
        create_output_transport("misspelled", lambda: None)
