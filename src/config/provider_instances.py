"""Provider instance helpers for full-agent provider routing.

Provider instance keys are the stable operator-facing identity used by calls,
contexts, and history. The implementation kind is stored as ``type`` in YAML
and falls back to the legacy key for existing configs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


FULL_AGENT_KINDS = frozenset(
    {
        "local",
        "deepgram",
        "openai_realtime",
        "google_live",
        "elevenlabs_agent",
    }
)

FULL_AGENT_KINDS_WITH_NATIVE_TTS_GATING = frozenset(
    {
        "deepgram",
        "openai_realtime",
        "elevenlabs_agent",
    }
)

VALID_ROLE_SUFFIXES = ("_stt", "_llm", "_tts")

API_KEY_COMPATIBLE_KINDS = frozenset(
    {
        "deepgram",
        "openai_realtime",
        "google_live",
        "elevenlabs_agent",
    }
)

CREDENTIAL_NAME_TO_FIELD = {
    "api-key": "api_key_file",
    "agent-id": "agent_id_file",
    "vertex-json": "credentials_path",
}


class ProviderInstanceError(ValueError):
    """Raised when provider instance wiring is invalid."""


def is_modular_provider_key(provider_key: str) -> bool:
    return any(str(provider_key).endswith(suffix) for suffix in VALID_ROLE_SUFFIXES)


def provider_kind(provider_key: str, provider_cfg: Any) -> Optional[str]:
    """Return the full-agent implementation kind for a provider block.

    ``type`` wins when present. Legacy configs without ``type`` use the key as
    the kind. Modular adapter keys are ignored here because they are handled by
    the pipeline orchestrator.
    """
    if is_modular_provider_key(provider_key):
        return None
    if isinstance(provider_cfg, Mapping):
        raw_type = str(provider_cfg.get("type") or "").strip()
        if raw_type:
            return raw_type
    if provider_key in FULL_AGENT_KINDS:
        return provider_key
    return None


def is_full_agent_provider(provider_key: str, provider_cfg: Any) -> bool:
    kind = provider_kind(provider_key, provider_cfg)
    return bool(kind in FULL_AGENT_KINDS)


def validate_provider_key(provider_key: str) -> None:
    if not provider_key or not isinstance(provider_key, str):
        raise ProviderInstanceError("Provider key is required")
    if "/" in provider_key or "\\" in provider_key or provider_key in {".", ".."}:
        raise ProviderInstanceError("Provider key cannot contain path separators")
    if not all(ch.isalnum() or ch in ("_", "-", ".") for ch in provider_key):
        raise ProviderInstanceError(
            "Provider key may only contain letters, numbers, '.', '_' and '-'"
        )


def validate_provider_instances(config_data: Dict[str, Any]) -> None:
    providers = config_data.get("providers") or {}
    pipelines = config_data.get("pipelines") or {}
    contexts = config_data.get("contexts") or {}

    if not isinstance(providers, dict):
        return
    if not isinstance(pipelines, dict):
        pipelines = {}
    if not isinstance(contexts, dict):
        contexts = {}

    errors: list[str] = []
    pipeline_names = set(str(name) for name in pipelines.keys())
    provider_names = set(str(name) for name in providers.keys())

    for key, cfg in providers.items():
        try:
            validate_provider_key(str(key))
        except ProviderInstanceError as exc:
            errors.append(f"providers.{key}: {exc}")
            continue

        if str(key) in pipeline_names:
            errors.append(
                f"Provider instance key '{key}' collides with a pipeline name; "
                "provider and pipeline names must be unambiguous."
            )

        if is_modular_provider_key(str(key)):
            continue

        kind = provider_kind(str(key), cfg)
        if isinstance(cfg, Mapping) and cfg.get("type") and kind not in FULL_AGENT_KINDS:
            errors.append(
                f"Provider '{key}' declares unsupported full-agent type '{kind}'. "
                f"Valid types: {', '.join(sorted(FULL_AGENT_KINDS))}."
            )
        elif kind is None and isinstance(cfg, Mapping):
            capabilities = cfg.get("capabilities") or []
            if isinstance(capabilities, str):
                capabilities = [capabilities]
            if set(capabilities) >= {"stt", "llm", "tts"}:
                errors.append(
                    f"Provider '{key}' has full-agent capabilities but no valid type; "
                    "set type to one of the registered full-agent kinds."
                )

    local_instances = [
        key for key, cfg in providers.items() if provider_kind(str(key), cfg) == "local"
    ]
    if len(local_instances) > 1:
        errors.append(
            "Only one local full-agent provider instance is supported; found "
            + ", ".join(sorted(map(str, local_instances)))
        )

    def _target_exists(target: Any) -> bool:
        return isinstance(target, str) and (target in provider_names or target in pipeline_names)

    default_provider = config_data.get("default_provider")
    if default_provider and not _target_exists(default_provider):
        errors.append(
            f"default_provider '{default_provider}' does not match a provider key or pipeline name."
        )

    for ctx_name, ctx_cfg in contexts.items():
        if not isinstance(ctx_cfg, Mapping):
            continue
        target = ctx_cfg.get("provider")
        if target and not _target_exists(target):
            errors.append(
                f"contexts.{ctx_name}.provider '{target}' does not match a provider key or pipeline name."
            )

    for pipeline_name, pipeline_cfg in pipelines.items():
        if not isinstance(pipeline_cfg, Mapping):
            continue
        for role in ("stt", "llm", "tts"):
            component = pipeline_cfg.get(role)
            if component in provider_names:
                kind = provider_kind(str(component), providers.get(component))
                if kind in FULL_AGENT_KINDS:
                    errors.append(
                        f"Pipeline '{pipeline_name}' {role} component '{component}' "
                        "is a full-agent provider; modular slots must reference role adapters."
                    )

    if errors:
        raise ProviderInstanceError(
            "Provider instance validation failed:\n"
            + "\n".join(f"  - {error}" for error in errors)
        )


def full_agent_default(config_data: Dict[str, Any]) -> bool:
    providers = config_data.get("providers") or {}
    if not isinstance(providers, dict):
        return False
    default_provider = config_data.get("default_provider")
    if not isinstance(default_provider, str):
        return False
    cfg = providers.get(default_provider)
    if not isinstance(cfg, Mapping):
        return False
    explicit_type = str(cfg.get("type") or "").strip()
    return explicit_type in FULL_AGENT_KINDS


def read_secret_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def resolve_secret_value(
    provider_cfg: Mapping[str, Any],
    *,
    file_field: str,
    env_field: str,
    inline_field: str,
    legacy_env_names: Iterable[str] = (),
) -> str:
    file_path = str(provider_cfg.get(file_field) or "").strip()
    if file_path:
        try:
            return read_secret_file(file_path)
        except Exception:
            return ""

    env_name = str(provider_cfg.get(env_field) or "").strip()
    if env_name:
        import os

        return os.getenv(env_name, "").strip()

    inline_value = provider_cfg.get(inline_field)
    if inline_value:
        return str(inline_value).strip()

    import os

    for legacy_name in legacy_env_names:
        value = os.getenv(legacy_name, "").strip()
        if value:
            return value
    return ""
