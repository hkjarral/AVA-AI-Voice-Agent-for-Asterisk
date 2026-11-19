"""Validated, optional transfer-announcement settings shared by API and runtime."""
from __future__ import annotations

import math
from typing import Any


def normalize_local_handoff_providers(raw: Any) -> set[str]:
    """Preserve legacy provider-list coercion for both speech-owner decisions."""
    if raw is False or raw is None:
        return set()
    if raw is True:
        raw = ["deepgram"]
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, (list, tuple, set)):
        raw = ["deepgram"]
    return {str(item or "").strip().lower() for item in raw if str(item or "").strip()}


def validate_handoff_config(tools: dict) -> None:
    if not isinstance(tools, dict):
        return
    transfer = tools.get("transfer") or {}
    if not isinstance(transfer, dict):
        return
    cfg = transfer.get("handoff")
    if cfg is not None:
        _validate(cfg, "tools.transfer.handoff")
    extensions = tools.get("extensions") or {}
    internal = extensions.get("internal") if isinstance(extensions, dict) else {}
    inventories = ((transfer.get("destinations") or {}, "tools.transfer.destinations"),
                   (internal or {}, "tools.extensions.internal"))
    for inventory, location in inventories:
        if isinstance(inventory, dict):
            for key, entry in inventory.items():
                if isinstance(entry, dict) and entry.get("handoff") is not None:
                    _validate(entry["handoff"], f"{location}.{key}.handoff", override=True)


def _validate(cfg: Any, location: str, *, override: bool = False) -> None:
    if not isinstance(cfg, dict):
        raise ValueError(f"{location} must be an object")
    allowed = {"mode", "message"} if override else {"enabled", "message", "start_timeout_sec", "finish_timeout_sec"}
    if set(cfg) - allowed:
        raise ValueError(f"{location}: unknown settings {sorted(set(cfg) - allowed)}")
    if override:
        if cfg.get("mode", "inherit") not in {"inherit", "disabled", "custom"}:
            raise ValueError(f"{location}.mode must be inherit, disabled or custom")
        enabled = cfg.get("mode") == "custom"
    else:
        if "enabled" in cfg and not isinstance(cfg["enabled"], bool):
            raise ValueError(f"{location}.enabled must be a boolean")
        enabled = cfg.get("enabled", False)
        for name, default, maximum in (("start_timeout_sec", 4, 10), ("finish_timeout_sec", 20, 60)):
            value = cfg.get(name, default)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError(f"{location}.{name} must be greater than 0 and at most {maximum}")
    message = cfg.get("message", "")
    if not isinstance(message, str) or len(message) > 1000:
        raise ValueError(f"{location}.message must be text of at most 1000 characters")
    if enabled and not message.strip():
        raise ValueError(f"{location}.message is required when enabled")


def resolve_handoff(transfer: dict, destination: dict | None = None) -> dict | None:
    cfg = dict(transfer.get("handoff") or {})
    override = (destination or {}).get("handoff") or {}
    mode = override.get("mode", "inherit")
    if mode == "disabled":
        return None
    if mode == "custom":
        cfg.update(enabled=True, message=override.get("message", ""))
    if not cfg.get("enabled") or not str(cfg.get("message") or "").strip():
        return None
    return {"message": cfg["message"].strip(),
            "start_timeout_sec": cfg.get("start_timeout_sec", 4.0),
            "finish_timeout_sec": cfg.get("finish_timeout_sec", 20.0)}
