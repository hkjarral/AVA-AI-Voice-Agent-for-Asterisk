"""WS-F (HIGH-4): MCP stdio framing is newline-delimited JSON (the MCP standard),
with a Content-Length fallback for legacy/LSP-style servers."""
import pytest

from src.mcp.stdio_framing import encode_message, decode_frame
from src.mcp.errors import MCPProtocolError


def test_encode_is_newline_delimited_no_header():
    data = encode_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert data.endswith(b"\n")
    assert b"Content-Length" not in data
    assert data.count(b"\n") == 1  # single trailing delimiter, no embedded newlines


def test_decode_newline_delimited_stream():
    buf = bytearray(b'{"jsonrpc":"2.0","id":1}\n{"id":2}\n')
    msg, consumed = decode_frame(buf)
    assert msg == {"jsonrpc": "2.0", "id": 1}
    del buf[:consumed]
    msg2, consumed2 = decode_frame(buf)
    assert msg2 == {"id": 2}
    del buf[:consumed2]
    assert decode_frame(buf) == (None, 0)


def test_decode_partial_line_waits():
    assert decode_frame(bytearray(b'{"id":1')) == (None, 0)  # no newline yet


def test_decode_skips_blank_lines_between_messages():
    buf = bytearray(b'\n\n{"id":1}\n')
    msg, consumed = decode_frame(buf)
    assert msg == {"id": 1}
    del buf[:consumed]
    assert len(buf) == 0


def test_decode_content_length_fallback():
    body = b'{"id":9}'
    buf = bytearray(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    msg, consumed = decode_frame(buf)
    assert msg == {"id": 9}
    assert consumed == len(buf)


def test_roundtrip_unicode():
    buf = bytearray(encode_message({"city": "São Paulo", "n": 1}))
    msg, consumed = decode_frame(buf)
    assert msg["city"] == "São Paulo"
    assert consumed == len(buf)


def test_invalid_json_line_raises():
    with pytest.raises(MCPProtocolError):
        decode_frame(bytearray(b'{not json}\n'))
