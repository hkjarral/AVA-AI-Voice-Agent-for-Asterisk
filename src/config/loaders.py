"""
Configuration file loaders and path resolution.

This module handles:
- Path resolution (relative to absolute)
- YAML file loading
- Environment variable expansion in YAML with default value support
"""

import os
import re
import yaml
from pathlib import Path
from typing import Callable, Optional


# Project root directory (parent of src/)
_PROJ_DIR = Path(__file__).parent.parent.parent.resolve()

# Only find delimiter tokens with regex. Parsing the body ourselves avoids
# backtracking over malformed, operator-supplied configuration text.
_ENV_BRACE_TOKENS = re.compile(r"\$\{|\}")


def substitute_braced_env_vars(
    text: str,
    resolve: Callable[[str, Optional[str], str, str], str],
) -> str:
    """Substitute complete ${NAME[operator]default} references in linear time."""
    parts = []
    copied_until = 0
    opening = None
    for token in _ENV_BRACE_TOKENS.finditer(text):
        if token.group() == "${":
            # A nested opening invalidates the earlier incomplete reference.
            opening = token.start()
            continue
        if opening is None:
            continue

        body = text[opening + 2:token.start()]
        var_name, separator, suffix = body.partition(":")
        valid_name = var_name.isascii() and var_name.isidentifier()
        valid_operator = not separator or (bool(suffix) and suffix[0] in "-=")
        if valid_name and valid_operator:
            operator = ":" + suffix[0] if separator else None
            default_value = suffix[1:] if operator else ""
            original = text[opening:token.end()]
            replacement = resolve(var_name, operator, default_value, original)
            parts.extend((text[copied_until:opening], replacement))
            copied_until = token.end()
        opening = None

    parts.append(text[copied_until:])
    return "".join(parts)


def _expand_env_vars_with_defaults(text: str) -> str:
    """Expand braced references, then the historical simple $VAR form."""
    def resolve(var_name: str, operator: Optional[str], default_value: str, original: str) -> str:
        env_value = os.environ.get(var_name)
        if operator:
            return env_value if env_value else default_value
        return env_value if env_value is not None else original

    result = substitute_braced_env_vars(text, resolve)
    
    # Then handle any remaining simple $VAR patterns
    result = os.path.expandvars(result)
    
    return result


def resolve_config_path(path: str) -> str:
    """
    Resolve configuration file path to absolute path.
    
    If the provided path is not absolute, it is resolved relative to the project root.
    
    Args:
        path: Configuration file path (absolute or relative)
        
    Returns:
        Absolute path to configuration file
        
    Complexity: 2
    """
    if not os.path.isabs(path):
        return os.path.join(_PROJ_DIR, path)
    return path


def load_yaml_with_env_expansion(path: str) -> dict:
    """
    Load YAML file with environment variable expansion.
    
    Reads the YAML file, expands environment variable references with shell-style
    default value support, then parses the YAML content.
    
    Supports:
    - ${VAR} - Basic expansion
    - ${VAR:-default} - Use default if VAR is unset or empty  
    - ${VAR:=default} - Use default if VAR is unset or empty
    - $VAR - Simple expansion
    
    Args:
        path: Absolute path to YAML configuration file
        
    Returns:
        Parsed configuration dictionary
        
    Raises:
        FileNotFoundError: If configuration file doesn't exist
        yaml.YAMLError: If YAML parsing fails
        
    Complexity: 3
    """
    try:
        with open(path, 'r') as f:
            config_str = f.read()
        
        # Substitute environment variables with shell-style default support
        config_str_expanded = _expand_env_vars_with_defaults(config_str)
        
        # Parse YAML
        config_data = yaml.safe_load(config_str_expanded)
        
        return config_data if config_data is not None else {}
        
    except FileNotFoundError:
        raise FileNotFoundError(f"Configuration file not found at: {path}")
    except yaml.YAMLError as e:
        raise yaml.YAMLError(f"Error parsing YAML configuration: {e}")


def deep_merge_dicts(base: dict, override: dict) -> dict:
    """
    Recursively deep-merge *override* into a copy of *base*.

    - Dict values are merged recursively.
    - If *override* explicitly sets a key to None, that key is deleted from the merged output.
      This allows operator-local overrides to remove upstream defaults.
    - All other types (lists, scalars) in *override* replace the base value.
    - Keys only in *base* are preserved (new upstream defaults propagate automatically).

    Args:
        base: The upstream/default configuration dictionary.
        override: The operator-local overrides to apply on top.

    Returns:
        A new merged dictionary (neither input is mutated).
    """
    merged = dict(base)
    for key, override_val in override.items():
        if override_val is None:
            merged.pop(key, None)
            continue
        base_val = merged.get(key)
        if isinstance(base_val, dict) and isinstance(override_val, dict):
            merged[key] = deep_merge_dicts(base_val, override_val)
        else:
            merged[key] = override_val
    return merged


def load_yaml_with_local_override(path: str) -> dict:
    """
    Load the base YAML config and deep-merge an optional local override file.

    Given a base path like ``config/ai-agent.yaml``, this function:

    1. Loads and env-expands the base file (required — raises if missing).
    2. Looks for a sibling ``config/ai-agent.local.yaml``.
    3. If the local file exists, loads/env-expands it and deep-merges over the base.

    This allows operators to keep their customisations in a gitignored local
    file while the upstream base stays clean and conflict-free during updates.

    Args:
        path: Absolute path to the base YAML configuration file.

    Returns:
        Merged configuration dictionary.
    """
    import structlog
    logger = structlog.get_logger("config.loaders")

    base_data = load_yaml_with_env_expansion(path)

    # Derive the local override path: config/ai-agent.yaml → config/ai-agent.local.yaml
    stem, ext = os.path.splitext(path)
    local_path = f"{stem}.local{ext}"

    if not os.path.isfile(local_path):
        return base_data

    try:
        local_data = load_yaml_with_env_expansion(local_path)
    except Exception as exc:
        logger.warning(
            "Failed to load local config override; using base config only",
            local_path=local_path,
            error=str(exc),
        )
        return base_data

    if not isinstance(local_data, dict):
        logger.warning(
            "Local config override is not a mapping; ignoring",
            local_path=local_path,
        )
        return base_data

    logger.info("Merging operator local config override", local_path=local_path)

    # Log provider-level overrides so operators can see what changed.
    local_providers = local_data.get("providers", {})
    if local_providers:
        for pname, poverrides in local_providers.items():
            if isinstance(poverrides, dict):
                logger.info(
                    "Local override applied to provider",
                    provider=pname,
                    overridden_keys=sorted(poverrides.keys()),
                )

    return deep_merge_dicts(base_data, local_data)
