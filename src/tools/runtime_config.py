"""Agent-scoped tool configuration and immutable runtime snapshots.

Global YAML remains the inventory and hard-disable authority.  An agent policy
may only narrow that inventory.  The returned config is a deep copy so every
call can safely retain it while a later tool generation is applied.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional


TRANSFER_SCOPE = "transfer"
TRANSFER_POLICIES = frozenset({"inherit", "selected", "none"})
TRANSFER_TOOL_NAMES = frozenset(
    {
        "blind_transfer",
        "attended_transfer",
        "live_agent_transfer",
        "check_extension_status",
    }
)


class ToolConfigPolicyError(ValueError):
    """Raised when an agent tool policy is malformed or unsupported."""


def normalize_agent_tool_configs(value: Any) -> Dict[str, Any]:
    """Validate and canonicalize the v7.4 per-agent tool policy document."""
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ToolConfigPolicyError("tool_configs_json must contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ToolConfigPolicyError("tool configuration must be a JSON object")

    unknown_scopes = sorted(set(value) - {TRANSFER_SCOPE})
    if unknown_scopes:
        raise ToolConfigPolicyError(
            f"unsupported tool configuration scope(s): {', '.join(unknown_scopes)}"
        )

    result: Dict[str, Any] = {}
    raw_transfer = value.get(TRANSFER_SCOPE)
    if raw_transfer is None:
        return result
    if not isinstance(raw_transfer, dict):
        raise ToolConfigPolicyError("transfer tool configuration must be an object")

    unknown_keys = sorted(set(raw_transfer) - {"destination_policy", "destination_keys"})
    if unknown_keys:
        raise ToolConfigPolicyError(
            f"unsupported transfer policy field(s): {', '.join(unknown_keys)}"
        )

    policy = str(raw_transfer.get("destination_policy") or "inherit").strip().lower()
    if policy not in TRANSFER_POLICIES:
        raise ToolConfigPolicyError(
            "transfer.destination_policy must be inherit, selected, or none"
        )

    raw_keys = raw_transfer.get("destination_keys", [])
    if raw_keys is None:
        raw_keys = []
    if not isinstance(raw_keys, list):
        raise ToolConfigPolicyError("transfer.destination_keys must be an array")
    destination_keys = []
    seen = set()
    for raw_key in raw_keys:
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ToolConfigPolicyError(
                "transfer.destination_keys entries must be non-empty strings"
            )
        key = raw_key.strip()
        if key not in seen:
            destination_keys.append(key)
            seen.add(key)

    if policy != "selected" and destination_keys:
        raise ToolConfigPolicyError(
            "transfer.destination_keys may only be set when destination_policy is selected"
        )

    result[TRANSFER_SCOPE] = {
        "destination_policy": policy,
        "destination_keys": destination_keys if policy == "selected" else [],
    }
    return result


def dump_agent_tool_configs(value: Any) -> Optional[str]:
    """Return stable JSON for storage, or ``None`` for the inherited default."""
    normalized = normalize_agent_tool_configs(value)
    if not normalized:
        return None
    transfer = normalized.get(TRANSFER_SCOPE) or {}
    if transfer.get("destination_policy") == "inherit":
        return None
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class EffectiveToolConfig:
    config: Dict[str, Any]
    policy: str
    requested_destination_keys: tuple[str, ...]
    effective_destination_keys: tuple[str, ...]
    stale_destination_keys: tuple[str, ...]


def resolve_agent_tool_config(
    global_config: Mapping[str, Any],
    agent_tool_configs: Any,
) -> EffectiveToolConfig:
    """Resolve an immutable-per-call config where an agent can only narrow routes."""
    config = copy.deepcopy(dict(global_config or {}))
    policies = normalize_agent_tool_configs(agent_tool_configs)
    transfer_policy = policies.get(TRANSFER_SCOPE) or {
        "destination_policy": "inherit",
        "destination_keys": [],
    }
    policy = transfer_policy["destination_policy"]

    tools = config.setdefault("tools", {})
    if not isinstance(tools, dict):
        tools = {}
        config["tools"] = tools
    transfer = tools.setdefault("transfer", {})
    if not isinstance(transfer, dict):
        transfer = {}
        tools["transfer"] = transfer
    destinations = transfer.get("destinations") or {}
    if not isinstance(destinations, dict):
        destinations = {}

    requested = tuple(transfer_policy.get("destination_keys") or ())
    if policy == "inherit":
        effective = dict(destinations)
        stale: tuple[str, ...] = ()
    elif policy == "none":
        effective = {}
        stale = ()
    else:
        effective = {key: destinations[key] for key in requested if key in destinations}
        stale = tuple(key for key in requested if key not in destinations)
    transfer["destinations"] = effective

    # The transfer scope also governs direct live-agent/status targets.  In selected
    # mode retain only internal extensions represented by an allowed extension route;
    # in none mode retain none.  Inherit preserves the existing global inventory.
    if policy != "inherit":
        extensions = tools.setdefault("extensions", {})
        if not isinstance(extensions, dict):
            extensions = {}
            tools["extensions"] = extensions
        internal = extensions.get("internal") or {}
        if not isinstance(internal, dict):
            internal = {}
        allowed_extensions = {
            str(cfg.get("target") or "").strip()
            for cfg in effective.values()
            if isinstance(cfg, dict)
            and str(cfg.get("type") or "").strip().lower() == "extension"
        }
        extensions["internal"] = {
            str(key): cfg
            for key, cfg in internal.items()
            if str(key) in allowed_extensions
        }
        configured_live_key = str(transfer.get("live_agent_destination_key") or "").strip()
        if configured_live_key and configured_live_key not in effective:
            transfer.pop("live_agent_destination_key", None)

    return EffectiveToolConfig(
        config=config,
        policy=policy,
        requested_destination_keys=requested,
        effective_destination_keys=tuple(effective),
        stale_destination_keys=stale,
    )


@dataclass(frozen=True)
class ToolRuntimeGeneration:
    generation_id: int
    config: Dict[str, Any]
    registry: Any
    config_hash: str
    created_at: str

    @classmethod
    def build(
        cls,
        *,
        generation_id: int,
        config: Mapping[str, Any],
        preserved_tools: Optional[Iterable[Any]] = None,
    ) -> "ToolRuntimeGeneration":
        from src.tools.registry import ToolRegistry

        snapshot = copy.deepcopy(dict(config or {}))
        registry = ToolRegistry.isolated()
        registry.initialize_default_tools()
        registry.initialize_http_tools_from_config(snapshot.get("tools") or {})
        registry.initialize_in_call_http_tools_from_config(
            snapshot.get("in_call_tools") or {}, cache_key="global"
        )
        for tool in preserved_tools or ():
            name = getattr(getattr(tool, "definition", None), "name", None)
            if name and not registry.has(name):
                registry.register_instance(tool)

        payload = json.dumps(snapshot, sort_keys=True, default=str, separators=(",", ":"))
        return cls(
            generation_id=int(generation_id),
            config=snapshot,
            registry=registry,
            config_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16],
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def for_agent(self, agent_tool_configs: Any) -> EffectiveToolConfig:
        return resolve_agent_tool_config(self.config, agent_tool_configs)
