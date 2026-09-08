"""Bounded control codecs. Protocol selection comes from the owned call slot."""

import json
import re
from typing import Any


def decode_control(message: str, mode: str) -> dict[str, Any]:
    if not isinstance(message, str) or len(message.encode("utf-8")) > 65500:
        raise ValueError("invalid control event")
    if mode == "json":
        event = json.loads(message)
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise ValueError("invalid JSON control event")
        return event
    if mode != "plain" or not message.isascii() or any(c in message for c in "\r\n\x00"):
        raise ValueError("invalid plain control event")
    parts = message.split()
    if not parts or not re.fullmatch(r"[A-Z_]+", parts[0]):
        raise ValueError("invalid plain event name")
    event = {"event": parts[0]}
    if parts[0] == "MEDIA_BUFFERING_COMPLETED":
        if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", parts[1]):
            raise ValueError("invalid completion identity")
        event["correlation_id"] = parts[1]
        return event
    for part in parts[1:]:
        key, sep, value = part.partition(":")
        if not sep or not key or not value or key in event:
            raise ValueError("invalid plain event field")
        event[key] = value
    for key in ("optimal_frame_size", "ptime", "queue_length"):
        if key in event:
            event[key] = int(event[key])
    if "queue_length" in event:
        event["queue_frames"] = event["queue_length"]
    if "queue_full" in event:
        event["queue_full"] = event["queue_full"] in {"1", "true"}
    return event


def encode_control(command: dict[str, str], mode: str) -> str:
    if mode == "json":
        return json.dumps(command, separators=(",", ":"), ensure_ascii=True)
    name = command.get("command", "")
    if mode != "plain" or name not in {
        "START_MEDIA_BUFFERING", "STOP_MEDIA_BUFFERING", "FLUSH_MEDIA",
        "CONTINUE_MEDIA", "PAUSE_MEDIA", "GET_STATUS", "REPORT_QUEUE_DRAINED",
    }:
        raise ValueError("unsupported plain media command")
    correlation = command.get("correlation_id")
    if correlation is not None:
        if name != "STOP_MEDIA_BUFFERING" or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", correlation):
            raise ValueError("invalid plain command identity")
        return f"{name} {correlation}"
    return name
