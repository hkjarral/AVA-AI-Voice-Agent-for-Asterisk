from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from .errors import MCPProtocolError


_HEADER_SEP = b"\r\n\r\n"
_WS = frozenset((0x20, 0x09, 0x0d, 0x0a))  # space, tab, CR, LF


def encode_message(payload: Dict[str, Any]) -> bytes:
    """Encode a JSON-RPC message as a newline-delimited frame.

    The MCP stdio transport is newline-delimited JSON (one compact object per
    line, no embedded newlines). json.dumps with compact separators never emits
    a raw newline (newlines inside string values are escaped), so a single
    trailing "\\n" delimits the message. This is what off-the-shelf MCP servers
    (npx @modelcontextprotocol/server-*, the Python mcp SDK, etc.) expect."""
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return (body + "\n").encode("utf-8")


def _parse_headers(raw: bytes) -> Dict[str, str]:
    try:
        text = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise MCPProtocolError(f"Invalid header encoding: {exc}") from exc

    headers: Dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise MCPProtocolError(f"Malformed header line: {line!r}")
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    return headers


def decode_frame(buffer: bytearray) -> Tuple[Optional[Dict[str, Any]], int]:
    """Decode a single MCP frame from an in-memory buffer.

    Primary framing is newline-delimited JSON (the MCP stdio standard). A
    legacy ``Content-Length:`` header is still accepted (auto-detected) so the
    bundled servers and any LSP-style server keep working. Returns
    (message_or_none, bytes_consumed); bytes_consumed includes any inter-message
    whitespace skipped before the frame.
    """
    n = len(buffer)
    i = 0
    while i < n and buffer[i] in _WS:  # skip inter-message blank lines/whitespace
        i += 1
    if i >= n:
        return None, 0

    if bytes(buffer[i:i + 15]).lower().startswith(b"content-length"):
        return _decode_lsp(buffer, i)
    return _decode_line(buffer, i)


def _decode_line(buffer: bytearray, start: int) -> Tuple[Optional[Dict[str, Any]], int]:
    nl = buffer.find(b"\n", start)
    if nl < 0:
        return None, 0  # message not yet complete
    line = bytes(buffer[start:nl]).strip()
    if not line:
        return None, 0
    try:
        message = json.loads(line.decode("utf-8"))
    except Exception as exc:
        raise MCPProtocolError(f"Invalid JSON line: {exc}") from exc
    return message, nl + 1


def _decode_lsp(buffer: bytearray, start: int) -> Tuple[Optional[Dict[str, Any]], int]:
    idx = buffer.find(_HEADER_SEP, start)
    if idx < 0:
        return None, 0

    header_bytes = bytes(buffer[start:idx])
    headers = _parse_headers(header_bytes)
    if "content-length" not in headers:
        raise MCPProtocolError("Missing Content-Length header")

    try:
        length = int(headers["content-length"])
    except ValueError as exc:
        raise MCPProtocolError(f"Invalid Content-Length: {headers['content-length']!r}") from exc

    body_start = idx + len(_HEADER_SEP)
    end = body_start + length
    if len(buffer) < end:
        return None, 0

    body_bytes = bytes(buffer[body_start:end])
    try:
        message = json.loads(body_bytes.decode("utf-8"))
    except Exception as exc:
        raise MCPProtocolError(f"Invalid JSON body: {exc}") from exc

    return message, end

