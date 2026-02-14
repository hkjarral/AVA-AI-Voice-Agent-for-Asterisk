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


# Project root directory (parent of src/)
_PROJ_DIR = Path(__file__).parent.parent.parent.resolve()

# Pattern to match ${VAR:-default} or ${VAR:=default} shell-style syntax
_ENV_VAR_PATTERN = re.compile(r'\$\{([^}:]+)(:-|:=)?([^}]*)?\}')


def _expand_env_vars_with_defaults(text: str) -> str:
    """
    Expand environment variables with support for shell-style defaults.
    
    Supports:
    - ${VAR} - Basic expansion
    - ${VAR:-default} - Use default if VAR is unset or empty
    - ${VAR:=default} - Use default if VAR is unset or empty (same as :- for our purposes)
    - $VAR - Simple expansion (handled by os.path.expandvars)
    
    Args:
        text: String containing environment variable references
        
    Returns:
        String with environment variables expanded
    """
    def replace_match(match):
        var_name = match.group(1)
        operator = match.group(2)  # :- or := or None
        default_value = match.group(3) or ""
        
        env_value = os.environ.get(var_name)
        
        if operator in (":-", ":="):
            # Use default if env var is unset or empty
            if env_value is None or env_value == "":
                return default_value
            return env_value
        else:
            # No default operator, just expand ${VAR}
            return env_value if env_value is not None else match.group(0)
    
    # First handle ${VAR:-default} and ${VAR:=default} patterns
    result = _ENV_VAR_PATTERN.sub(replace_match, text)
    
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
