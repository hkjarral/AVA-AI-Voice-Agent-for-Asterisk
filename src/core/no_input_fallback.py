"""Shared validation for the inbound-only, engine-owned inactivity action."""

from typing import Any, Mapping


DEFAULT_TRANSFER_MESSAGE = "I still can't hear you. I'll transfer your call now."
FALLBACK_TYPES = {"extension", "queue", "ringgroup"}


def validate_fallback_policy(raw: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError("Caller inactivity settings must be an object")
    action = raw.get("timeout_action", "hangup")
    if action not in ("hangup", "transfer"):
        raise ValueError("Inbound timeout action must be hangup or transfer")
    key = raw.get("fallback_destination", "")
    if not isinstance(key, str) or len(key) > 128:
        raise ValueError("Fallback destination must be a configured destination key")
    if action == "transfer" and not key.strip():
        raise ValueError("Choose a fallback destination for inbound transfers")
    message = raw.get("transfer_message")
    if message is not None and (not isinstance(message, str) or not 1 <= len(message.strip()) <= 500):
        raise ValueError("Transfer message must contain 1 to 500 characters")


def fallback_destination(config: Mapping[str, Any], key: str) -> dict:
    """Use the call's exact scoped inventory. Never fuzzy-match a deleted key."""
    tools = config.get("tools") or {}
    transfer = tools.get("transfer") or {} if isinstance(tools, dict) else {}
    if not isinstance(transfer, dict):
        raise ValueError("Fallback transfer service is unavailable")
    destinations = transfer.get("destinations") or {}
    destination = destinations.get(key) if isinstance(destinations, dict) else None
    if (transfer.get("enabled") is False or not isinstance(destination, dict)
            or destination.get("type") not in FALLBACK_TYPES
            or isinstance(destination.get("target"), bool)
            or not isinstance(destination.get("target"), (str, int))
            or not str(destination["target"]).strip()):
        raise ValueError("Fallback destination is unavailable or outside this agent's transfer scope")
    return destination
