"""Provider instance helpers for full-agent provider routing.

Provider instance keys are the stable operator-facing identity used by calls,
contexts, and history. The implementation kind is stored as ``type`` in YAML
and falls back to the legacy key for existing configs.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

# Strict allowlist for provider instance keys (operator-facing identifiers).
# We deliberately keep this tight: alphanumerics, dot, underscore, hyphen,
# 1–64 chars. Anything outside this set is rejected before any filesystem
# operation that interpolates the key, so a malicious YAML/admin-API caller
# cannot construct a path that escapes the secrets root via the key itself.
_PROVIDER_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

# Static root for per-instance provider secrets. Every filesystem path built
# from a provider key MUST resolve underneath this directory; see
# safe_secret_path() below.
PROVIDER_SECRETS_ROOT = "/app/project/secrets/providers"


FULL_AGENT_KINDS = frozenset(
    {
        "local",
        "deepgram",
        "openai_realtime",
        "google_live",
        "elevenlabs_agent",
        "grok",
    }
)

FULL_AGENT_KINDS_WITH_NATIVE_TTS_GATING = frozenset(
    {
        "deepgram",
        "openai_realtime",
        "elevenlabs_agent",
        "grok",
    }
)

VALID_ROLE_SUFFIXES = ("_stt", "_llm", "_tts")

API_KEY_COMPATIBLE_KINDS = frozenset(
    {
        "deepgram",
        "openai_realtime",
        "google_live",
        "elevenlabs_agent",
        "grok",
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
            if raw_type == "full" and provider_key in FULL_AGENT_KINDS:
                return provider_key
            return raw_type
    if provider_key in FULL_AGENT_KINDS:
        return provider_key
    return None


def is_full_agent_provider(provider_key: str, provider_cfg: Any) -> bool:
    kind = provider_kind(provider_key, provider_cfg)
    return bool(kind in FULL_AGENT_KINDS)


def validate_provider_key(provider_key: str) -> None:
    """Strict allowlist check for provider instance keys.

    Used at every entry point that derives a filesystem path from the key.
    The regex (`[A-Za-z0-9_.-]{1,64}`) is intentionally narrower than what
    YAML permits so we can never produce a path that escapes the secrets
    root via traversal characters (``/``, ``\\``, ``..``, control chars,
    null bytes, etc.).
    """
    if not provider_key or not isinstance(provider_key, str):
        raise ProviderInstanceError("Provider key is required")
    if not _PROVIDER_KEY_PATTERN.fullmatch(provider_key):
        raise ProviderInstanceError(
            "Provider key may only contain letters, numbers, '.', '_' and '-' (1-64 chars)"
        )
    if provider_key in {".", ".."}:
        raise ProviderInstanceError("Provider key cannot be '.' or '..'")


def safe_secret_path(provider_key: str, filename: str, *, root: str = PROVIDER_SECRETS_ROOT) -> str:
    """Build a secrets-root-bounded absolute path from a provider key + filename.

    Validates ``provider_key`` against the strict allowlist, validates
    ``filename`` against a similarly strict allowlist (no separators / no
    traversal), resolves both to a real path, and re-checks containment.
    Callers must use this helper for every filesystem operation that
    interpolates a provider key — never build paths from untrusted strings
    directly.
    """
    validate_provider_key(provider_key)
    if not filename or not isinstance(filename, str):
        raise ProviderInstanceError("Credential filename is required")
    if not re.fullmatch(r"^[A-Za-z0-9_.\-]{1,64}$", filename) or filename in {".", ".."}:
        raise ProviderInstanceError("Invalid credential filename")
    root_real = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root_real, provider_key, filename))
    # Containment check: candidate must be inside root_real (allow == root_real
    # for callers that want the root itself, though the join above prevents
    # that in practice because we always pass a filename).
    if not (candidate == root_real or candidate.startswith(root_real + os.sep)):
        raise ProviderInstanceError("Provider credential path escapes secrets root")
    return candidate


def read_secret_file_for_provider(provider_key: str, filename: str, *, root: str = PROVIDER_SECRETS_ROOT) -> str:
    """Read a per-provider secret file through safe_secret_path.

    Uses os.open with O_NOFOLLOW to defeat symlink attacks at the
    leaf — even though safe_secret_path() validates the directory
    structure, a symlink planted at the credential file itself could
    redirect the read to an arbitrary location.
    """
    import os as _os

    safe_path = safe_secret_path(provider_key, filename, root=root)
    fd = _os.open(safe_path, _os.O_RDONLY | _os.O_NOFOLLOW)
    try:
        with _os.fdopen(fd, "rb") as f:
            return f.read().decode("utf-8").strip()
    except BaseException:
        try:
            _os.close(fd)
        except OSError:
            pass
        raise


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
        raw_type = str(cfg.get("type") or "").strip() if isinstance(cfg, Mapping) else ""
        if raw_type == "full" and str(key) in FULL_AGENT_KINDS:
            continue

        if isinstance(cfg, Mapping) and raw_type and kind not in FULL_AGENT_KINDS:
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

    def _is_full_agent_provider_key(target: str) -> bool:
        """A routing target may name a provider only if that provider is
        a full-agent kind. Modular `*_stt` / `*_llm` / `*_tts` adapters
        live in `providers` for pipeline composition; they are NOT
        valid call-routing destinations (the engine's
        `_load_providers` loop skips them when assembling routable
        providers). Codex P1 on PR #396."""
        cfg = providers.get(target)
        if not isinstance(cfg, Mapping):
            return False
        return provider_kind(str(target), cfg) in FULL_AGENT_KINDS

    def _target_exists(target: Any) -> bool:
        if not isinstance(target, str):
            return False
        if target in pipeline_names:
            return True
        # Reject modular provider keys (e.g. `deepgram_stt`) — only
        # full-agent provider instances can be routing targets.
        return target in provider_names and _is_full_agent_provider_key(target)

    default_provider = config_data.get("default_provider")
    if default_provider and not _target_exists(default_provider):
        if isinstance(default_provider, str) and default_provider in provider_names:
            errors.append(
                f"default_provider '{default_provider}' references a modular provider "
                "(STT/LLM/TTS adapter), not a full-agent provider or pipeline. "
                "Use a full-agent provider instance key or a pipeline name instead."
            )
        else:
            errors.append(
                f"default_provider '{default_provider}' does not match a full-agent "
                "provider instance key or pipeline name."
            )

    for ctx_name, ctx_cfg in contexts.items():
        if not isinstance(ctx_cfg, Mapping):
            continue
        target = ctx_cfg.get("provider")
        if target and not _target_exists(target):
            if isinstance(target, str) and target in provider_names:
                errors.append(
                    f"contexts.{ctx_name}.provider '{target}' references a modular provider "
                    "(STT/LLM/TTS adapter), not a full-agent provider or pipeline. "
                    "Use a full-agent provider instance key or a pipeline name instead."
                )
            else:
                errors.append(
                    f"contexts.{ctx_name}.provider '{target}' does not match a full-agent "
                    "provider instance key or pipeline name."
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
    return provider_kind(default_provider, cfg) in FULL_AGENT_KINDS


# Allowed roots for secret files referenced from provider YAML. The
# Admin UI always writes under PROVIDER_SECRETS_ROOT; operator-managed
# legacy configs may also reference well-known system paths. Anything
# outside this allowlist is rejected before any filesystem operation.
_ALLOWED_SECRET_ROOTS = (
    PROVIDER_SECRETS_ROOT,           # /app/project/secrets/providers (new style)
    "/app/project/secrets",          # legacy /app/project/secrets/openai.key etc.
    "/run/secrets",                  # Docker / systemd secrets
    "/etc/aava",                     # legacy operator paths under /etc/aava
    "/var/lib/aava",                 # legacy operator paths under /var/lib/aava
)


def read_secret_file(path: str) -> str:
    """Read a secret file referenced from provider config.

    The ``path`` value originates from operator-managed YAML
    (``api_key_file`` / ``agent_id_file`` / ``credentials_path``). The
    Admin UI always writes paths under :data:`PROVIDER_SECRETS_ROOT`
    via :func:`safe_secret_path`; hand-edited configs may reference
    well-known legacy roots like ``/etc/aava`` or ``/run/secrets``.
    Anything outside the allowlist is rejected before any filesystem
    operation — closes the operator-typo → arbitrary-file-read gap
    that the previous "trust any absolute path" fallback left open
    (CodeRabbit major on PR #396).

    File is opened with ``O_RDONLY | O_NOFOLLOW`` to defeat symlink
    attacks (if an attacker can plant a symlink at the resolved path,
    ``O_NOFOLLOW`` refuses to traverse it).
    """
    import os as _os

    if not isinstance(path, str) or not path.strip():
        raise ProviderInstanceError("Secret file path is required")
    if "\x00" in path:
        raise ProviderInstanceError("Secret file path contains NUL byte")

    # Normalize before any other check so `..` collapses out.
    normalized = _os.path.normpath(path.strip())
    if not _os.path.isabs(normalized):
        raise ProviderInstanceError(
            "Secret file path must be absolute (no relative paths allowed)"
        )

    # Containment check against the static allowlist. This is the
    # CodeQL-recognized "validated against a known constant" sanitizer
    # pattern (CWE-022). The allowlist roots are all module-level
    # string constants — never request-derived.
    allowed = False
    for root in _ALLOWED_SECRET_ROOTS:
        root_norm = _os.path.normpath(root)
        if normalized == root_norm or normalized.startswith(root_norm + _os.sep):
            allowed = True
            break
    if not allowed:
        raise ProviderInstanceError(
            f"Secret file path must live under one of: {', '.join(_ALLOWED_SECRET_ROOTS)}"
        )

    # Open with O_NOFOLLOW to refuse traversing a symlink at the
    # leaf — defense against an attacker planting a symlink to
    # /etc/passwd at one of the legacy paths. Explicit os.open with
    # an int fd also gives CodeQL a different sink pattern than
    # Path.read_text(), which may avoid the false positive entirely.
    fd = _os.open(normalized, _os.O_RDONLY | _os.O_NOFOLLOW)
    try:
        with _os.fdopen(fd, "rb") as f:
            return f.read().decode("utf-8").strip()
    except BaseException:
        # If fdopen failed before claiming the fd, close it manually.
        try:
            _os.close(fd)
        except OSError:
            pass
        raise


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
        except (OSError, UnicodeError, ProviderInstanceError):
            # Treat missing / permission-denied / undecodable files as
            # "no credentials" so the caller can fall back to env/inline.
            # Don't swallow programmer errors (CodeRabbit on PR #396).
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
