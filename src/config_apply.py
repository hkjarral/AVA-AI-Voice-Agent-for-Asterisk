"""Shared classification for applying configuration changes.

The Admin UI and AI Engine must agree on whether a saved YAML change can be
hot-reloaded or needs a process restart.  Keeping this policy here prevents a
hash mismatch from being presented as an unconditional restart requirement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Mapping, Optional


HOT_RELOADABLE_ROOT_KEYS: FrozenSet[str] = frozenset(
    {
        "contexts",
        "profiles",
        "barge_in",
        "no_input",
        "tools",
        "in_call_tools",
        "farewell_hangup_delay_sec",
        "on_provider_failure",
        "provider_failure_prompt",
        "provider_failure_redirect_context",
        "provider_failure_redirect_extension",
        "provider_failure_redirect_priority",
    }
)

_INVALID_CONFIG_CHANGE_KEY = "__invalid_config__"


def _as_mapping(config: Any) -> Optional[Mapping[str, Any]]:
    """Return a mapping representation, or ``None`` when conversion is unsafe."""
    if isinstance(config, Mapping):
        return config
    model_dump = getattr(config, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            return None
        return dumped if isinstance(dumped, Mapping) else None
    legacy_dict = getattr(config, "dict", None)
    if callable(legacy_dict):
        try:
            dumped = legacy_dict()
        except Exception:
            return None
        return dumped if isinstance(dumped, Mapping) else None
    raw = getattr(config, "__dict__", None)
    return raw if isinstance(raw, Mapping) else None


@dataclass(frozen=True)
class ConfigApplyDecision:
    changed_keys: FrozenSet[str]
    apply_required: bool
    restart_required: bool
    recommended_apply_method: str

    def apply_plan(self) -> list[Dict[str, str]]:
        if not self.apply_required:
            return []
        method = "restart" if self.restart_required else "hot_reload"
        endpoint = (
            "/api/system/containers/ai_engine/restart"
            if self.restart_required
            else "/api/system/containers/ai_engine/reload"
        )
        return [{"service": "ai_engine", "method": method, "endpoint": endpoint}]


def classify_config_change(old_config: Any, new_config: Any) -> ConfigApplyDecision:
    """Classify a root-level effective-config change.

    Empty changes need no action. Changes limited to the immutable new-call
    sections can be hot-reloaded; every other change remains fail-closed on the
    restart path.
    """

    old = _as_mapping(old_config)
    new = _as_mapping(new_config)
    if old is None or new is None:
        return ConfigApplyDecision(
            changed_keys=frozenset({_INVALID_CONFIG_CHANGE_KEY}),
            apply_required=True,
            restart_required=True,
            recommended_apply_method="restart",
        )
    changed = frozenset(
        key
        for key in set(old) | set(new)
        if key not in old or key not in new or old[key] != new[key]
    )
    apply_required = bool(changed)
    restart_required = apply_required and not changed.issubset(
        HOT_RELOADABLE_ROOT_KEYS
    )
    if not apply_required:
        method = "none"
    elif restart_required:
        method = "restart"
    else:
        method = "hot_reload"
    return ConfigApplyDecision(
        changed_keys=changed,
        apply_required=apply_required,
        restart_required=restart_required,
        recommended_apply_method=method,
    )
