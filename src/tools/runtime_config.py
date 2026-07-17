"""Agent-scoped tool configuration and immutable runtime snapshots.

Global YAML remains the inventory and hard-disable authority.  An agent policy
may only narrow that inventory.  The returned config is a deep copy so every
call can safely retain it while a later tool generation is applied.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional


TRANSFER_SCOPE = "transfer"
GOOGLE_CALENDAR_SCOPE = "google_calendar"
MICROSOFT_CALENDAR_SCOPE = "microsoft_calendar"
VOICEMAIL_SCOPE = "voicemail"
RESOURCE_POLICIES = frozenset({"inherit", "selected", "none"})
TRANSFER_POLICIES = RESOURCE_POLICIES
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


def _normalize_string_list(raw: Any, field_name: str) -> list[str]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ToolConfigPolicyError(f"{field_name} must be an array")
    result: list[str] = []
    seen: set[str] = set()
    for raw_key in raw:
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ToolConfigPolicyError(
                f"{field_name} entries must be non-empty strings"
            )
        key = raw_key.strip()
        if key not in seen:
            result.append(key)
            seen.add(key)
    return result


def _normalize_multi_resource_scope(
    raw: Any,
    *,
    scope: str,
    policy_field: str,
    keys_field: str,
) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ToolConfigPolicyError(f"{scope} tool configuration must be an object")
    unknown_keys = sorted(set(raw) - {policy_field, keys_field})
    if unknown_keys:
        raise ToolConfigPolicyError(
            f"unsupported {scope} policy field(s): {', '.join(unknown_keys)}"
        )
    policy = str(raw.get(policy_field) or "inherit").strip().lower()
    if policy not in RESOURCE_POLICIES:
        raise ToolConfigPolicyError(
            f"{scope}.{policy_field} must be inherit, selected, or none"
        )
    keys = _normalize_string_list(raw.get(keys_field, []), f"{scope}.{keys_field}")
    if policy != "selected" and keys:
        raise ToolConfigPolicyError(
            f"{scope}.{keys_field} may only be set when {policy_field} is selected"
        )
    return {
        policy_field: policy,
        keys_field: keys if policy == "selected" else [],
    }


def _normalize_voicemail_scope(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ToolConfigPolicyError("voicemail tool configuration must be an object")
    unknown_keys = sorted(set(raw) - {"mailbox_policy", "mailbox_key"})
    if unknown_keys:
        raise ToolConfigPolicyError(
            f"unsupported voicemail policy field(s): {', '.join(unknown_keys)}"
        )
    policy = str(raw.get("mailbox_policy") or "inherit").strip().lower()
    if policy not in RESOURCE_POLICIES:
        raise ToolConfigPolicyError(
            "voicemail.mailbox_policy must be inherit, selected, or none"
        )
    mailbox_key = raw.get("mailbox_key")
    if mailbox_key is not None:
        if not isinstance(mailbox_key, str) or not mailbox_key.strip():
            raise ToolConfigPolicyError(
                "voicemail.mailbox_key must be a non-empty string when set"
            )
        mailbox_key = mailbox_key.strip()
    if policy != "selected" and mailbox_key:
        raise ToolConfigPolicyError(
            "voicemail.mailbox_key may only be set when mailbox_policy is selected"
        )
    return {
        "mailbox_policy": policy,
        "mailbox_key": mailbox_key if policy == "selected" else None,
    }


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

    supported_scopes = {
        TRANSFER_SCOPE,
        GOOGLE_CALENDAR_SCOPE,
        MICROSOFT_CALENDAR_SCOPE,
        VOICEMAIL_SCOPE,
    }
    unknown_scopes = sorted(set(value) - supported_scopes)
    if unknown_scopes:
        raise ToolConfigPolicyError(
            f"unsupported tool configuration scope(s): {', '.join(unknown_scopes)}"
        )

    result: Dict[str, Any] = {}
    raw_transfer = value.get(TRANSFER_SCOPE)
    if raw_transfer is not None:
        result[TRANSFER_SCOPE] = _normalize_multi_resource_scope(
            raw_transfer,
            scope=TRANSFER_SCOPE,
            policy_field="destination_policy",
            keys_field="destination_keys",
        )

    raw_google = value.get(GOOGLE_CALENDAR_SCOPE)
    if raw_google is not None:
        result[GOOGLE_CALENDAR_SCOPE] = _normalize_multi_resource_scope(
            raw_google,
            scope=GOOGLE_CALENDAR_SCOPE,
            policy_field="calendar_policy",
            keys_field="calendar_keys",
        )

    raw_microsoft = value.get(MICROSOFT_CALENDAR_SCOPE)
    if raw_microsoft is not None:
        result[MICROSOFT_CALENDAR_SCOPE] = _normalize_multi_resource_scope(
            raw_microsoft,
            scope=MICROSOFT_CALENDAR_SCOPE,
            policy_field="account_policy",
            keys_field="account_keys",
        )

    raw_voicemail = value.get(VOICEMAIL_SCOPE)
    if raw_voicemail is not None:
        result[VOICEMAIL_SCOPE] = _normalize_voicemail_scope(raw_voicemail)
    return result


def dump_agent_tool_configs(value: Any) -> Optional[str]:
    """Return stable JSON for storage, or ``None`` for the inherited default."""
    normalized = normalize_agent_tool_configs(value)
    if not normalized:
        return None
    inherited_policy_fields = {
        TRANSFER_SCOPE: "destination_policy",
        GOOGLE_CALENDAR_SCOPE: "calendar_policy",
        MICROSOFT_CALENDAR_SCOPE: "account_policy",
        VOICEMAIL_SCOPE: "mailbox_policy",
    }
    stored = {
        scope: config
        for scope, config in normalized.items()
        if config.get(inherited_policy_fields[scope]) != "inherit"
    }
    if not stored:
        return None
    return json.dumps(stored, sort_keys=True, separators=(",", ":"))


def merge_legacy_tool_overrides(
    agent_tool_configs: Any,
    legacy_tool_overrides: Any,
) -> Dict[str, Any]:
    """Convert legacy Context calendar bindings into v7.4 Agent policies.

    Explicit ``tool_configs`` always wins. Missing legacy selections preserve
    inheritance; an explicitly empty legacy list becomes ``none`` so migration
    retains the old fail-closed behavior.
    """
    merged = normalize_agent_tool_configs(agent_tool_configs)
    if not isinstance(legacy_tool_overrides, Mapping):
        return merged

    google = legacy_tool_overrides.get(GOOGLE_CALENDAR_SCOPE)
    if GOOGLE_CALENDAR_SCOPE not in merged and isinstance(google, Mapping):
        selected = google.get("selected_calendars")
        if isinstance(selected, (list, tuple)):
            keys = [str(key).strip() for key in selected if str(key).strip()]
            merged[GOOGLE_CALENDAR_SCOPE] = {
                "calendar_policy": "selected" if keys else "none",
                "calendar_keys": list(dict.fromkeys(keys)),
            }

    microsoft = legacy_tool_overrides.get(MICROSOFT_CALENDAR_SCOPE)
    if MICROSOFT_CALENDAR_SCOPE not in merged and isinstance(microsoft, Mapping):
        selected = microsoft.get("selected_accounts")
        if isinstance(selected, (list, tuple)):
            keys = [str(key).strip() for key in selected if str(key).strip()]
            merged[MICROSOFT_CALENDAR_SCOPE] = {
                "account_policy": "selected" if keys else "none",
                "account_keys": list(dict.fromkeys(keys)),
            }
    return normalize_agent_tool_configs(merged)


@dataclass(frozen=True)
class EffectiveToolConfig:
    config: Dict[str, Any]
    policy: str
    requested_destination_keys: tuple[str, ...]
    effective_destination_keys: tuple[str, ...]
    stale_destination_keys: tuple[str, ...]
    policies: Dict[str, str] = field(default_factory=dict)
    effective_resource_keys: Dict[str, tuple[str, ...]] = field(default_factory=dict)
    stale_resource_keys: Dict[str, tuple[str, ...]] = field(default_factory=dict)


def _inventory_keys(config: Mapping[str, Any], inventory_field: str) -> tuple[str, ...]:
    inventory = config.get(inventory_field) or {}
    if isinstance(inventory, Mapping) and inventory:
        return tuple(
            str(key) for key, value in inventory.items() if isinstance(value, Mapping)
        )
    return ("default",) if config else ()


def _apply_multi_resource_policy(
    tools: Dict[str, Any],
    policies: Mapping[str, Any],
    *,
    scope: str,
    tool_name: str,
    policy_field: str,
    keys_field: str,
    inventory_field: str,
    selection_field: str,
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    policy_config = policies.get(scope) or {policy_field: "inherit", keys_field: []}
    policy = policy_config[policy_field]
    tool_config = tools.setdefault(tool_name, {})
    if not isinstance(tool_config, dict):
        tool_config = {}
        tools[tool_name] = tool_config
    inventory = _inventory_keys(tool_config, inventory_field)
    requested = tuple(policy_config.get(keys_field) or ())
    stale = tuple(key for key in requested if key not in inventory)
    if policy == "inherit":
        effective = inventory
    elif policy == "none":
        effective = ()
        tool_config[selection_field] = []
    else:
        effective = tuple(key for key in requested if key in inventory)
        tool_config[selection_field] = list(effective)
    # Calendar tools previously looked up Context overlays after their base
    # config. Mark the immutable Agent resolution so stale YAML Context data
    # cannot override this per-call policy.
    tool_config["_agent_scope_resolved"] = True
    return policy, requested, effective, stale


def _voicemail_inventory(config: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    mailboxes = config.get("mailboxes") or {}
    if isinstance(mailboxes, Mapping) and mailboxes:
        return {
            str(key): dict(value)
            for key, value in mailboxes.items()
            if isinstance(value, Mapping)
        }
    extension = str(config.get("extension") or "").strip()
    return {"default": {"name": "Default", "extension": extension}} if extension else {}


def _apply_voicemail_policy(
    tools: Dict[str, Any], policies: Mapping[str, Any]
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    policy_config = policies.get(VOICEMAIL_SCOPE) or {
        "mailbox_policy": "inherit",
        "mailbox_key": None,
    }
    policy = policy_config["mailbox_policy"]
    tool_config = tools.setdefault("leave_voicemail", {})
    if not isinstance(tool_config, dict):
        tool_config = {}
        tools["leave_voicemail"] = tool_config
    inventory = _voicemail_inventory(tool_config)
    requested = (
        (str(policy_config["mailbox_key"]),)
        if policy == "selected" and policy_config.get("mailbox_key")
        else ()
    )
    stale = tuple(key for key in requested if key not in inventory)
    if policy == "inherit":
        effective = tuple(inventory)
    elif policy == "none":
        effective = ()
        tool_config.pop("extension", None)
        tool_config["mailboxes"] = {}
        tool_config.pop("selected_mailbox_key", None)
    else:
        effective = tuple(key for key in requested if key in inventory)
        if effective:
            selected_key = effective[0]
            selected = dict(inventory[selected_key])
            tool_config["mailboxes"] = {selected_key: selected}
            tool_config["selected_mailbox_key"] = selected_key
            extension = str(selected.get("extension") or "").strip()
            if extension:
                tool_config["extension"] = extension
            else:
                tool_config.pop("extension", None)
        else:
            tool_config.pop("extension", None)
            tool_config["mailboxes"] = {}
            tool_config.pop("selected_mailbox_key", None)
    tool_config["_agent_scope_resolved"] = True
    return policy, requested, effective, stale


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
        effective = {
            key: destinations[key]
            for key in requested
            if key in destinations and isinstance(destinations[key], Mapping)
        }
        # Placeholder inventory entries (for example ``support_queue: null``)
        # are not executable routes. Keep them fail-closed and surface them as
        # stale just like a selected resource removed from the global catalog.
        stale = tuple(key for key in requested if key not in effective)
    transfer["destinations"] = effective

    # The transfer scope also governs direct live-agent/status targets.  In selected
    # mode retain only internal extensions represented by an allowed extension route;
    # in none mode retain none.  Inherit preserves the existing global inventory.
    if policy != "inherit":
        extensions = tools.setdefault("extensions", {})
        if not isinstance(extensions, dict):
            extensions = {}
            tools["extensions"] = extensions
        status_check = tools.setdefault("check_extension_status", {})
        if not isinstance(status_check, dict):
            status_check = {}
            tools["check_extension_status"] = status_check
        # A narrowed transfer policy also narrows status targets. Override a
        # permissive global setting so an Agent cannot probe extensions outside
        # the immutable effective inventory captured for this call.
        status_check["restrict_to_configured_extensions"] = True
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

    resource_results = {
        GOOGLE_CALENDAR_SCOPE: _apply_multi_resource_policy(
            tools,
            policies,
            scope=GOOGLE_CALENDAR_SCOPE,
            tool_name=GOOGLE_CALENDAR_SCOPE,
            policy_field="calendar_policy",
            keys_field="calendar_keys",
            inventory_field="calendars",
            selection_field="selected_calendars",
        ),
        MICROSOFT_CALENDAR_SCOPE: _apply_multi_resource_policy(
            tools,
            policies,
            scope=MICROSOFT_CALENDAR_SCOPE,
            tool_name=MICROSOFT_CALENDAR_SCOPE,
            policy_field="account_policy",
            keys_field="account_keys",
            inventory_field="accounts",
            selection_field="selected_accounts",
        ),
        VOICEMAIL_SCOPE: _apply_voicemail_policy(tools, policies),
    }
    all_policies = {TRANSFER_SCOPE: policy}
    effective_resources = {TRANSFER_SCOPE: tuple(effective)}
    stale_resources = {TRANSFER_SCOPE: stale}
    for scope, (scope_policy, _requested, scope_effective, scope_stale) in resource_results.items():
        all_policies[scope] = scope_policy
        effective_resources[scope] = scope_effective
        stale_resources[scope] = scope_stale

    return EffectiveToolConfig(
        config=config,
        policy=policy,
        requested_destination_keys=requested,
        effective_destination_keys=tuple(effective),
        stale_destination_keys=stale,
        policies=all_policies,
        effective_resource_keys=effective_resources,
        stale_resource_keys=stale_resources,
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
