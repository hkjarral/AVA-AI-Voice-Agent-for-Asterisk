from fastapi import APIRouter, HTTPException, UploadFile, File
import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
import os
import re
import asyncio
import glob
import tempfile
import sys
from contextlib import contextmanager
from pydantic import BaseModel
from typing import Dict, Any, Optional, Union
import settings

# A11: Maximum number of backups to keep
MAX_BACKUPS = 5

router = APIRouter()

# YAML duplicate-key protection:
# - PyYAML tolerates duplicate keys (last one wins), but the Admin UI frontend YAML parser rejects them.
# - Enforce "no duplicate mapping keys" on reads/writes so we fail fast with a clear error.
class _NoDuplicateSafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping_no_duplicates(loader: yaml.SafeLoader, node: MappingNode, deep: bool = False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key ({key!r})",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDuplicateSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_no_duplicates,
)


def _safe_load_no_duplicates(content: str):
    return yaml.load(content, Loader=_NoDuplicateSafeLoader)

# Regex to strip ANSI escape codes from logs
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi_codes(text: str) -> str:
    """Remove ANSI escape codes from text for clean log files."""
    return ANSI_ESCAPE.sub('', text)


def _rotate_backups(base_path: str) -> None:
    """
    A11: Keep only the last MAX_BACKUPS backup files.
    Deletes oldest backups when limit is exceeded.
    """
    pattern = f"{base_path}.bak.*"
    backups = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    
    # Delete oldest backups beyond MAX_BACKUPS
    for old_backup in backups[MAX_BACKUPS:]:
        try:
            os.remove(old_backup)
        except OSError:
            pass  # Ignore errors deleting old backups

class ConfigUpdate(BaseModel):
    content: str

@contextmanager
def _temporary_dotenv(path: str, defaults: Dict[str, str] | None = None):
    """
    Temporarily load KEY=VALUE pairs from a .env file into os.environ.

    This keeps config schema validation consistent with how ai-engine injects
    credentials/settings from environment variables at runtime.
    """
    env_pairs: Dict[str, str] = {}
    try:
        if path and os.path.exists(path):
            from dotenv import dotenv_values
            raw = dotenv_values(path)
            for key, value in (raw or {}).items():
                if key and value is not None:
                    env_pairs[str(key)] = str(value)
    except Exception:
        env_pairs = {}

    previous: Dict[str, Any] = {}
    for key, value in env_pairs.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value

    applied_defaults: Dict[str, str] = {}
    for key, value in (defaults or {}).items():
        if key not in os.environ or os.environ.get(key, "").strip() == "":
            previous[key] = os.environ.get(key)
            os.environ[key] = value
            applied_defaults[key] = value

    try:
        yield
    finally:
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(old_value)


def _resolve_json_schema_ref(schema_root: Dict[str, Any], ref: str) -> Dict[str, Any]:
    # Expected format: "#/$defs/SomeModel"
    if not ref.startswith("#/"):
        return {}
    node: Any = schema_root
    for part in ref.lstrip("#/").split("/"):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return {}
    return node if isinstance(node, dict) else {}


def _collect_unknown_keys(data: Any, schema_root: Dict[str, Any], schema_node: Dict[str, Any], prefix: str) -> list:
    """
    Best-effort unknown-key detection using Pydantic's JSON schema.

    We only warn when the schema node is a structured object with explicit
    properties and does NOT allow additionalProperties (dict-like blobs).
    """
    if not isinstance(schema_node, dict):
        return []

    if "$ref" in schema_node:
        resolved = _resolve_json_schema_ref(schema_root, schema_node["$ref"])
        if resolved:
            schema_node = resolved

    # Avoid false positives for union-ish nodes.
    for union_key in ("anyOf", "oneOf", "allOf"):
        if union_key in schema_node:
            return []

    if not isinstance(data, dict):
        return []

    properties = schema_node.get("properties")
    if not isinstance(properties, dict):
        return []

    additional = schema_node.get("additionalProperties")
    if additional not in (None, False):
        # This node is intentionally dict-like (e.g., providers, contexts).
        # Don't warn about unknown keys here.
        # Still descend into known properties when present.
        warnings: list = []
        for key, subschema in properties.items():
            if key in data:
                next_prefix = f"{prefix}.{key}" if prefix else key
                warnings.extend(_collect_unknown_keys(data[key], schema_root, subschema, next_prefix))
        return warnings

    warnings: list = []
    known_keys = set(properties.keys())
    for key in data.keys():
        if key not in known_keys:
            full = f"{prefix}.{key}" if prefix else str(key)
            warnings.append(f"Unknown config key: {full} (will be ignored)")

    for key, subschema in properties.items():
        if key in data:
            next_prefix = f"{prefix}.{key}" if prefix else key
            warnings.extend(_collect_unknown_keys(data[key], schema_root, subschema, next_prefix))

    return warnings


def _validate_ai_agent_config(content: str) -> Dict[str, Any]:
    """
    Validate ai-agent.yaml content against the canonical AppConfig schema.

    Returns:
      {"warnings": [...]} on success

    Raises:
      HTTPException(400) on validation errors
    """
    try:
        parsed = _safe_load_no_duplicates(content) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {str(exc)}")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Invalid YAML: expected a mapping at the document root")

    # Ensure project root is importable so we can reuse canonical Pydantic models.
    project_root = getattr(settings, "PROJECT_ROOT", None)
    if project_root and project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        from pydantic import ValidationError
        from src.config import AppConfig, load_config
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Server misconfiguration: cannot import config schema (src.config). Error: {exc}",
        )

    warnings: list[str] = []

    # Warn if user put credentials in YAML (they will be ignored by design).
    try:
        asterisk_block = parsed.get("asterisk") if isinstance(parsed.get("asterisk"), dict) else {}
        if isinstance(asterisk_block, dict) and any(k in asterisk_block for k in ("username", "password")):
            warnings.append("Asterisk credentials in YAML are ignored; set ASTERISK_ARI_USERNAME/ASTERISK_ARI_PASSWORD in .env instead.")

        providers_block = parsed.get("providers") if isinstance(parsed.get("providers"), dict) else {}
        if isinstance(providers_block, dict):
            for provider_name, provider_cfg in providers_block.items():
                if isinstance(provider_cfg, dict) and "api_key" in provider_cfg:
                    warnings.append(f"providers.{provider_name}.api_key in YAML is ignored; set the provider API key in .env instead.")
    except Exception:
        pass

    # If ARI credentials are not present, validate with placeholders but warn the user.
    env_required_defaults: Dict[str, str] = {}
    try:
        from dotenv import dotenv_values
        dotenv_map = dotenv_values(settings.ENV_PATH) if os.path.exists(settings.ENV_PATH) else {}
        get_dotenv = lambda k: str(dotenv_map.get(k) or "").strip()

        ari_user_present = bool(get_dotenv("ASTERISK_ARI_USERNAME") or get_dotenv("ARI_USERNAME") or os.environ.get("ASTERISK_ARI_USERNAME") or os.environ.get("ARI_USERNAME"))
        ari_pass_present = bool(get_dotenv("ASTERISK_ARI_PASSWORD") or get_dotenv("ARI_PASSWORD") or os.environ.get("ASTERISK_ARI_PASSWORD") or os.environ.get("ARI_PASSWORD"))

        if not ari_user_present:
            warnings.append("Missing ARI username in .env (ASTERISK_ARI_USERNAME or ARI_USERNAME). Engine will not connect to Asterisk ARI until set.")
            env_required_defaults["ASTERISK_ARI_USERNAME"] = "__MISSING__"
        if not ari_pass_present:
            warnings.append("Missing ARI password in .env (ASTERISK_ARI_PASSWORD or ARI_PASSWORD). Engine will not connect to Asterisk ARI until set.")
            env_required_defaults["ASTERISK_ARI_PASSWORD"] = "__MISSING__"
    except Exception:
        pass

    # Validate using the same loader pipeline as ai-engine (env injection + defaults + normalization).
    dir_path = os.path.dirname(settings.CONFIG_PATH)
    with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, suffix=".validate.yaml") as f:
        f.write(content)
        tmp_path = f.name

    try:
        with _temporary_dotenv(settings.ENV_PATH, defaults=env_required_defaults):
            load_config(tmp_path)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Config schema validation failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Config validation failed: {exc}")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    try:
        schema = AppConfig.model_json_schema()
        warnings.extend(_collect_unknown_keys(parsed, schema, schema, prefix=""))
    except Exception:
        pass

    return {"warnings": warnings}


@router.post("/yaml")
async def update_yaml_config(update: ConfigUpdate):
    try:
        # Validate YAML + schema before saving.
        validation = _validate_ai_agent_config(update.content)
        warnings = validation.get("warnings") or []

        # Create backup before saving
        if os.path.exists(settings.CONFIG_PATH):
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{settings.CONFIG_PATH}.bak.{timestamp}"
            with open(settings.CONFIG_PATH, 'r') as src:
                with open(backup_path, 'w') as dst:
                    dst.write(src.read())
            # A11: Rotate backups - keep only last MAX_BACKUPS
            _rotate_backups(settings.CONFIG_PATH)

        # A8: Atomic write via temp file + rename (preserve permissions)
        dir_path = os.path.dirname(settings.CONFIG_PATH)
        # Get original file permissions if file exists
        original_mode = None
        if os.path.exists(settings.CONFIG_PATH):
            original_mode = os.stat(settings.CONFIG_PATH).st_mode
        
        with tempfile.NamedTemporaryFile('w', dir=dir_path, delete=False, suffix='.tmp') as f:
            f.write(update.content)
            temp_path = f.name
        
        # Restore original permissions before replace
        if original_mode is not None:
            os.chmod(temp_path, original_mode)
        
        os.replace(temp_path, settings.CONFIG_PATH)  # Atomic on POSIX
        
        # Determine recommended apply method based on what changed
        # hot_reload: contexts, MCP servers, greetings/instructions only
        # restart: most YAML changes (providers, pipelines, transport, VAD, etc.)
        # recreate: .env changes (handled separately in /env endpoint)
        recommended_method = "restart"  # Default for YAML changes
        
        # Check if change is limited to hot-reloadable sections
        try:
            old_content = None
            # Read the backup we just created to compare
            import glob
            backups = sorted(glob.glob(f"{settings.CONFIG_PATH}.bak.*"), reverse=True)
            if backups:
                with open(backups[0], 'r') as f:
                    old_content = f.read()
            
            if old_content:
                old_parsed = _safe_load_no_duplicates(old_content) or {}
                new_parsed = _safe_load_no_duplicates(update.content) or {}
                
                # Keys that can be hot-reloaded
                hot_reload_keys = {'contexts', 'profiles', 'mcp'}
                
                # Check if only hot-reloadable keys changed
                all_keys = set(old_parsed.keys()) | set(new_parsed.keys())
                changed_keys = set()
                for key in all_keys:
                    if old_parsed.get(key) != new_parsed.get(key):
                        changed_keys.add(key)
                
                if changed_keys and changed_keys.issubset(hot_reload_keys):
                    recommended_method = "hot_reload"
        except Exception:
            pass  # Fall back to restart if comparison fails
        
        apply_plan = ([{"service": "ai-engine", "method": "hot_reload", "endpoint": "/api/system/containers/ai_engine/reload"}]
                     if recommended_method == "hot_reload"
                     else [{"service": "ai-engine", "method": "restart", "endpoint": "/api/system/containers/ai_engine/restart"}])

        return {
            "status": "success",
            "restart_required": recommended_method != "hot_reload",
            "recommended_apply_method": recommended_method,
            "apply_plan": apply_plan,
            "message": f"Configuration saved. {'Hot reload' if recommended_method == 'hot_reload' else 'Restart'} AI Engine to apply changes.",
            "warnings": warnings,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/yaml")
async def get_yaml_config():
    print(f"Accessing config at {settings.CONFIG_PATH}")
    if not os.path.exists(settings.CONFIG_PATH):
        print("Config file not found")
        raise HTTPException(status_code=404, detail="Config file not found")
    try:
        with open(settings.CONFIG_PATH, 'r') as f:
            config_content = f.read()
        _safe_load_no_duplicates(config_content)  # Validate YAML and reject duplicate keys
        return {"content": config_content}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=500, detail=f"Error reading or parsing YAML config: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.get("/env")
async def get_env_config():
    """
    Read .env file and return parsed key-value pairs.
    Uses dotenv_values for correct parsing of quoted/escaped values.
    """
    env_vars = {}
    if os.path.exists(settings.ENV_PATH):
        try:
            # Use dotenv_values for proper parsing of quoted values
            from dotenv import dotenv_values
            env_vars = dotenv_values(settings.ENV_PATH)
            # Convert to regular dict (dotenv_values returns OrderedDict)
            # and filter out None values (unset keys)
            env_vars = {k: v for k, v in env_vars.items() if v is not None}
        except ImportError:
            # Fallback to manual parsing if python-dotenv not available
            with open(settings.ENV_PATH, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '=' in line:
                            key, value = line.split('=', 1)
                            # Strip surrounding quotes if present
                            value = value.strip()
                            if (value.startswith('"') and value.endswith('"')) or \
                               (value.startswith("'") and value.endswith("'")):
                                value = value[1:-1]
                            env_vars[key.strip()] = value
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return env_vars

@router.post("/env")
async def update_env(env_data: Dict[str, Optional[str]]):
    """
    Update .env file with provided key-value pairs.
    
    - Pass a string value to set/update a key
    - Pass None or "__DELETE__" to remove a key entirely (line is removed, not commented)
    - Values with spaces, #, quotes, $, etc. are automatically quoted
    - Already-quoted values from UI are stored as-is (no double-quoting)
    """
    try:
        # A12: Validate env data before writing
        for key, value in env_data.items():
            if not key or not key.strip():
                raise HTTPException(status_code=400, detail="Empty key not allowed")
            if value is not None and '\n' in str(value):
                raise HTTPException(status_code=400, detail=f"Newlines not allowed in value: {key}")
            if '\n' in key:
                raise HTTPException(status_code=400, detail=f"Newlines not allowed in key: {key}")
            if '=' in key:
                raise HTTPException(status_code=400, detail=f"Key cannot contain '=': {key}")
        
        # Create backup before saving
        if os.path.exists(settings.ENV_PATH):
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{settings.ENV_PATH}.bak.{timestamp}"
            with open(settings.ENV_PATH, 'r') as src:
                with open(backup_path, 'w') as dst:
                    dst.write(src.read())
            # A11: Rotate backups
            _rotate_backups(settings.ENV_PATH)

        # Read existing lines
        lines = []
        if os.path.exists(settings.ENV_PATH):
            with open(settings.ENV_PATH, 'r') as f:
                lines = f.readlines()

        # Create a map of keys to ALL their line indices (handles duplicates)
        # SECURITY: Track all occurrences so we can remove duplicates that might contain old secrets
        from collections import defaultdict
        key_occurrences = defaultdict(list)  # key -> [line_idx, line_idx, ...]
        existing_values = {}  # key -> current value (for change detection)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                if '=' in stripped:
                    key, raw_val = stripped.split('=', 1)
                    key = key.strip()
                    key_occurrences[key].append(i)
                    # Parse existing value for change detection (strip quotes)
                    val = raw_val.strip()
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    existing_values[key] = val

        # Update existing keys or append new ones
        new_lines = lines.copy()
        
        # Ensure we have a newline at the end if the file is not empty
        if new_lines and not new_lines[-1].endswith('\n'):
            new_lines[-1] += '\n'

        # Track keys to delete (value is None or special marker)
        keys_to_delete = set()
        # Track keys being updated (to remove duplicate earlier occurrences)
        keys_to_update = set()
        
        for key, value in env_data.items():
            # Skip empty keys
            if not key:
                continue
            
            # Support deletion: None or special "__DELETE__" marker removes the key
            # SECURITY: Actually remove the line to avoid leaking old secrets
            if value is None or value == "__DELETE__":
                keys_to_delete.add(key)
                continue
            
            str_value = str(value)
            
            # Only track as changed if value actually differs from existing
            existing_val = existing_values.get(key)
            # Normalize for comparison: strip quotes from incoming if present
            cmp_value = str_value
            if (cmp_value.startswith('"') and cmp_value.endswith('"')) or (cmp_value.startswith("'") and cmp_value.endswith("'")):
                cmp_value = cmp_value[1:-1]
            if existing_val != cmp_value:
                keys_to_update.add(key)
            
            # Check if value is already properly quoted (from UI round-trip)
            # Don't double-quote values that are already quoted
            already_quoted = (
                (str_value.startswith('"') and str_value.endswith('"') and len(str_value) >= 2) or
                (str_value.startswith("'") and str_value.endswith("'") and len(str_value) >= 2)
            )
            
            if already_quoted:
                # Value is already quoted, use as-is
                line_content = f"{key}={str_value}\n"
            else:
                # Determine if quoting is needed
                needs_quoting = (
                    not str_value or  # Empty string
                    ' ' in str_value or  # Spaces
                    '#' in str_value or  # Comments
                    '"' in str_value or  # Internal quotes need escaping
                    "'" in str_value or  # Single quotes
                    '$' in str_value or  # Variable expansion
                    '`' in str_value or  # Command substitution
                    '\\' in str_value    # Backslashes
                )
                
                if needs_quoting:
                    # Escape internal double quotes and backslashes, then wrap in quotes
                    escaped_value = str_value.replace('\\', '\\\\').replace('"', '\\"')
                    line_content = f'{key}="{escaped_value}"\n'
                else:
                    line_content = f"{key}={str_value}\n"
            
            occurrences = key_occurrences.get(key, [])
            if occurrences:
                # Update the LAST occurrence, mark earlier ones for removal
                last_idx = occurrences[-1]
                new_lines[last_idx] = line_content
                # SECURITY: Remove all earlier occurrences (may contain old secrets)
                for idx in occurrences[:-1]:
                    new_lines[idx] = None  # Mark for removal
            else:
                # Append new key
                new_lines.append(line_content)
        
        # SECURITY: Remove ALL occurrences of deleted keys (not just last)
        for key in keys_to_delete:
            for idx in key_occurrences.get(key, []):
                new_lines[idx] = None  # Mark for removal
        
        # Filter out removed lines
        new_lines = [line for line in new_lines if line is not None]

        # A8: Atomic write via temp file + rename (preserve permissions)
        dir_path = os.path.dirname(settings.ENV_PATH)
        # Get original file permissions if file exists
        original_mode = None
        if os.path.exists(settings.ENV_PATH):
            original_mode = os.stat(settings.ENV_PATH).st_mode
        
        with tempfile.NamedTemporaryFile('w', dir=dir_path, delete=False, suffix='.tmp') as f:
            f.writelines(new_lines)
            temp_path = f.name
        
        # Restore original permissions before replace
        if original_mode is not None:
            os.chmod(temp_path, original_mode)
        
        os.replace(temp_path, settings.ENV_PATH)  # Atomic on POSIX
        
        changed_keys = sorted(set(keys_to_update) | set(keys_to_delete))

        def _is_prefix(key: str, prefixes: tuple[str, ...]) -> bool:
            return any(key.startswith(p) for p in prefixes)

        def _ai_engine_env_key(key: str) -> bool:
            return (
                _is_prefix(key, ("ASTERISK_", "LOG_", "DIAG_", "CALL_HISTORY_", "HEALTH_"))
                or key in ("OPENAI_API_KEY", "DEEPGRAM_API_KEY", "GOOGLE_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "TZ", "STREAMING_LOG_LEVEL")
                or _is_prefix(key, ("AUDIO_TRANSPORT", "DOWNSTREAM_MODE", "AUDIOSOCKET_", "EXTERNAL_MEDIA_", "BARGE_IN_"))
            )

        def _local_ai_env_key(key: str) -> bool:
            return (
                _is_prefix(key, ("LOCAL_", "KROKO_", "FASTER_WHISPER_", "WHISPER_CPP_", "MELOTTS_", "KOKORO_"))
                or key in ("SHERPA_MODEL_PATH",)
            )

        def _admin_ui_env_key(key: str) -> bool:
            return (
                key in ("JWT_SECRET", "DOCKER_SOCK")
                or _is_prefix(key, ("UVICORN_", "ADMIN_UI_"))
            )

        impacts_ai_engine = any(_ai_engine_env_key(k) for k in changed_keys)
        impacts_local_ai = any(_local_ai_env_key(k) for k in changed_keys)
        impacts_admin_ui = any(_admin_ui_env_key(k) for k in changed_keys)

        apply_plan = []
        if impacts_ai_engine:
            apply_plan.append({"service": "ai-engine", "method": "restart", "endpoint": "/api/system/containers/ai_engine/restart"})
        if impacts_local_ai:
            apply_plan.append({"service": "local-ai-server", "method": "restart", "endpoint": "/api/system/containers/local_ai_server/restart"})
        if impacts_admin_ui:
            apply_plan.append({"service": "admin-ui", "method": "restart", "endpoint": "/api/system/containers/admin_ui/restart"})

        message = "Environment saved. Restart impacted services to apply changes."
        if impacts_admin_ui:
            message += " (Restarting Admin UI will invalidate sessions if JWT_SECRET changed.)"

        return {
            "status": "success",
            "restart_required": bool(apply_plan),
            "recommended_apply_method": "recreate",
            "apply_plan": apply_plan,
            "changed_keys": changed_keys,
            "message": message,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ProviderTestRequest(BaseModel):
    name: str
    config: Dict[str, Any]

@router.post("/providers/test")
async def test_provider_connection(request: ProviderTestRequest):
    """Test connection to a provider based on its configuration"""
    try:
        import httpx
        import os
        
        # Helper to read API keys from .env file
        def get_env_key(key_name: str) -> str:
            """Read API key from .env file"""
            if os.path.exists(settings.ENV_PATH):
                with open(settings.ENV_PATH, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith(f"{key_name}="):
                            return line.split('=', 1)[1].strip()
            return ''
        
        # Helper to substitute environment variables in config values
        def substitute_env_vars(item):
            import re
            if isinstance(item, dict):
                return {k: substitute_env_vars(v) for k, v in item.items()}
            elif isinstance(item, list):
                return [substitute_env_vars(i) for i in item]
            elif isinstance(item, str):
                # Match ${VAR} or ${VAR:-default} or ${VAR:=default}
                # Capture group 1: Var name, Group 2: Default value (optional)
                pattern = r'\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?:[:=-]([^}]*))?\}'
                
                def replace(match):
                    var_name = match.group(1)
                    default_value = match.group(2)
                    # Check .env file FIRST - this has the latest values from UI edits
                    # The Admin UI container's os.environ may be stale (from container start)
                    val = get_env_key(var_name)
                    if val:
                        return val
                    # Fall back to os.environ (for vars not in .env or set at container start)
                    val = os.getenv(var_name)
                    if val is not None and val != "":
                        return val
                    # Then check if we have a default value
                    if default_value is not None:
                        return default_value
                    # If neither, return empty string (standard shell behavior)
                    return "" 
                
                return re.sub(pattern, replace, item)
            return item

        # Apply substitution to the config
        provider_config = substitute_env_vars(request.config)
        provider_name = request.name.lower()
        
        # ============================================================
        # LOCAL PROVIDER - test connection to local_ai_server
        # ============================================================
        if 'local' in provider_name or provider_config.get('type') == 'local':
            import websockets
            import json
            
            # Get WebSocket URL from either base_url or ws_url
            ws_url = provider_config.get('base_url') or provider_config.get('ws_url') or 'ws://127.0.0.1:8765'
            # Handle env var format
            if '${' in ws_url:
                ws_url = 'ws://127.0.0.1:8765'  # Default fallback
            
            try:
                def _fallback_ws_url(url: str) -> str:
                    """
                    In host-networked deployments, `local_ai_server` DNS does not resolve because it is not
                    a Docker bridge network hostname. Fall back to localhost for best compatibility.
                    """
                    try:
                        if 'local_ai_server' in url:
                            return url.replace('local_ai_server', '127.0.0.1')
                    except Exception:
                        pass
                    return url

                async def _try_connect(url: str):
                    async with websockets.connect(url, open_timeout=5.0) as ws:
                        # Send status request to check models
                        await ws.send(json.dumps({"type": "status"}))
                        response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        data = json.loads(response)
                        return data

                try:
                    data = await _try_connect(ws_url)
                    effective_url = ws_url
                except Exception as e:
                    alt = _fallback_ws_url(ws_url)
                    if alt != ws_url:
                        data = await _try_connect(alt)
                        effective_url = alt
                    else:
                        raise e

                    # Send status request to check models
                    if data.get("type") == "status_response" and data.get("status") == "ok":
                        models = data.get("models", {})
                        stt_loaded = models.get("stt", {}).get("loaded", False)
                        llm_loaded = models.get("llm", {}).get("loaded", False)
                        tts_loaded = models.get("tts", {}).get("loaded", False)
                        
                        stt_backend = data.get("stt_backend", "unknown")
                        tts_backend = data.get("tts_backend", "unknown")
                        llm_model = models.get("llm", {}).get("path", "").split("/")[-1] if models.get("llm", {}).get("path") else "none"
                        
                        status_parts = []
                        if stt_loaded:
                            status_parts.append(f"STT: {stt_backend} ✓")
                        else:
                            status_parts.append(f"STT: not loaded")
                        if llm_loaded:
                            status_parts.append(f"LLM: {llm_model} ✓")
                        else:
                            status_parts.append(f"LLM: not loaded")
                        if tts_loaded:
                            status_parts.append(f"TTS: {tts_backend} ✓")
                        else:
                            status_parts.append(f"TTS: not loaded")
                        
                        all_loaded = stt_loaded and llm_loaded and tts_loaded
                        return {
                            "success": all_loaded,
                            "message": f"Local AI Server connected ({effective_url}). {' | '.join(status_parts)}"
                        }
                    else:
                        return {"success": False, "message": "Local AI Server responded but status invalid"}
            except Exception as e:
                return {"success": False, "message": f"Cannot connect to Local AI Server at {ws_url}: {str(e)}"}
        
        # ============================================================
        # ELEVENLABS AGENT - check before other providers
        # ============================================================
        if 'elevenlabs' in provider_name or 'agent_id' in provider_config:
            api_key = get_env_key('ELEVENLABS_API_KEY')
            if not api_key:
                return {"success": False, "message": "ELEVENLABS_API_KEY not set in .env file"}
            
            async with httpx.AsyncClient() as client:
                # Use /v1/voices endpoint for validation (works with all API key types)
                response = await client.get(
                    "https://api.elevenlabs.io/v1/voices",
                    headers={"xi-api-key": api_key, "Accept": "application/json"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    data = response.json()
                    voice_count = len(data.get('voices', []))
                    return {"success": True, "message": f"Connected to ElevenLabs ({voice_count} voices available)"}
                return {"success": False, "message": f"ElevenLabs API error: HTTP {response.status_code}"}
        
        # ============================================================
        # OPENAI REALTIME
        # ============================================================
        if 'realtime_base_url' in provider_config or 'turn_detection' in provider_config:
            # OpenAI Realtime
            api_key = get_env_key('OPENAI_API_KEY')
            if not api_key:
                return {"success": False, "message": "OPENAI_API_KEY not set in .env file"}
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    return {"success": True, "message": f"Connected to OpenAI (HTTP {response.status_code})"}
                return {"success": False, "message": f"OpenAI API error: HTTP {response.status_code}"}

        # ============================================================
        # OPENAI-COMPATIBLE (OpenAI / Groq / OpenRouter / etc.)
        # ============================================================
        if provider_config.get('type') == 'openai':
            chat_base_url = (provider_config.get('chat_base_url') or 'https://api.openai.com/v1').rstrip('/')
            api_key = provider_config.get('api_key')
            if not api_key:
                inferred_env = None
                if 'groq' in provider_name or 'api.groq.com' in chat_base_url:
                    inferred_env = 'GROQ_API_KEY'
                elif 'openai' in provider_name or 'api.openai.com' in chat_base_url:
                    inferred_env = 'OPENAI_API_KEY'

                if inferred_env:
                    api_key = get_env_key(inferred_env) or os.getenv(inferred_env) or ''

            if not api_key:
                return {"success": False, "message": "API key missing for OpenAI-compatible provider (set api_key or env var)"}

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{chat_base_url}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=10.0,
                    )
                    if response.status_code == 200:
                        try:
                            data = response.json()
                            models = data.get('data') or []
                            return {"success": True, "message": f"Connected (OpenAI-compatible). Found {len(models)} models."}
                        except Exception:
                            return {"success": True, "message": f"Connected (OpenAI-compatible) (HTTP {response.status_code})"}
                    if response.status_code == 401:
                        return {"success": False, "message": "Invalid API key (401)"}
                    return {"success": False, "message": f"Provider API error: HTTP {response.status_code}"}
            except Exception as e:
                return {"success": False, "message": f"Cannot connect to provider at {chat_base_url}: {str(e)}"}

        # ============================================================
        # GROQ SPEECH (STT/TTS) - validate via /models (OpenAI-compatible)
        # ============================================================
        if provider_config.get('type') == 'groq':
            api_key = provider_config.get('api_key') or get_env_key('GROQ_API_KEY') or os.getenv('GROQ_API_KEY') or ''
            if not api_key:
                return {"success": False, "message": "GROQ_API_KEY not set (set api_key or env var)"}

            # Derive an OpenAI-compatible base URL for validation.
            # Prefer the configured STT/TTS base URLs if present, otherwise default to Groq.
            base_url = 'https://api.groq.com/openai/v1'
            raw_url = (provider_config.get('stt_base_url') or provider_config.get('tts_base_url') or '').strip()
            if raw_url and '/audio/' in raw_url:
                base_url = raw_url.rsplit('/audio/', 1)[0]
            base_url = base_url.rstrip('/')

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{base_url}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=10.0,
                    )
                    if response.status_code == 200:
                        try:
                            data = response.json()
                            models = data.get('data') or []
                            return {"success": True, "message": f"Connected (Groq Speech). Found {len(models)} models."}
                        except Exception:
                            return {"success": True, "message": f"Connected (Groq Speech) (HTTP {response.status_code})"}
                    if response.status_code == 401:
                        return {"success": False, "message": "Invalid API key (401)"}
                    return {"success": False, "message": f"Provider API error: HTTP {response.status_code}"}
            except Exception as e:
                return {"success": False, "message": f"Cannot connect to provider at {base_url}: {str(e)}"}
                
        elif 'google_live' in provider_config or ('llm_model' in provider_config and 'gemini' in provider_config.get('llm_model', '')):
            # Google Live
            api_key = get_env_key('GOOGLE_API_KEY')
            if not api_key:
                return {"success": False, "message": "GOOGLE_API_KEY not set in .env file"}
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                    timeout=10.0
                )
                if response.status_code == 200:
                    return {"success": True, "message": f"Connected to Google API (HTTP {response.status_code})"}
                return {"success": False, "message": f"Google API error: HTTP {response.status_code}"}
                
        elif 'ws_url' in provider_config:
            # Local provider (WebSocket)
            ws_url = provider_config.get('ws_url', '')
            if not ws_url:
                 return {"success": False, "message": "No WebSocket URL provided"}
            
            try:
                import websockets
                # Try connecting to the WebSocket
                async with websockets.connect(ws_url, open_timeout=5.0) as ws:
                    await ws.close()
                return {"success": True, "message": "Local AI server is reachable via WebSocket"}
            except ImportError:
                 return {"success": False, "message": "websockets library not installed"}
            except Exception as e:
                # If local-ai-server is on host network, ensure we use host.docker.internal or host networking properties
                return {"success": False, "message": f"Cannot reach local AI server at {ws_url}. Error: {str(e)}"}
        
        # ============================================================
        # OLLAMA - Self-hosted LLM
        # ============================================================
        if 'ollama' in provider_name or provider_config.get('type') == 'ollama':
            import aiohttp
            base_url = provider_config.get('base_url', 'http://localhost:11434').rstrip('/')
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{base_url}/api/tags"
                    timeout = aiohttp.ClientTimeout(total=10)
                    async with session.get(url, timeout=timeout) as response:
                        if response.status == 200:
                            data = await response.json()
                            models = data.get("models", [])
                            return {"success": True, "message": f"Connected to Ollama! Found {len(models)} models."}
                        else:
                            return {"success": False, "message": f"Ollama returned status {response.status}"}
            except aiohttp.ClientConnectorError:
                return {"success": False, "message": f"Cannot connect to Ollama at {base_url}. Ensure Ollama is running and accessible."}
            except asyncio.TimeoutError:
                return {"success": False, "message": "Connection timeout - is Ollama running?"}
            except Exception as e:
                return {"success": False, "message": f"Ollama connection failed: {str(e)}"}
                
        elif 'model' in provider_config or 'stt_model' in provider_config or 'chat_model' in provider_config or 'tts_model' in provider_config:
            # Check if it's Deepgram or OpenAI standard
            # Deepgram often has 'deepgram' in name or model names like 'nova'
            if provider_config.get('model', '').startswith('nova') or 'deepgram' in provider_name.lower():
                # Deepgram
                api_key = get_env_key('DEEPGRAM_API_KEY')
                if not api_key:
                    return {"success": False, "message": "DEEPGRAM_API_KEY not set in .env file"}
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        "https://api.deepgram.com/v1/projects",
                        headers={"Authorization": f"Token {api_key}"},
                        timeout=10.0
                    )
                    if response.status_code == 200:
                        return {"success": True, "message": f"Connected to Deepgram (HTTP {response.status_code})"}
                    return {"success": False, "message": f"Deepgram API error: HTTP {response.status_code}"}
            else:
                # OpenAI Standard or Generic
                # Try OpenAI first
                api_key = get_env_key('OPENAI_API_KEY')
                if api_key:
                   async with httpx.AsyncClient() as client:
                        try:
                            response = await client.get(
                                "https://api.openai.com/v1/models",
                                headers={"Authorization": f"Bearer {api_key}"},
                                timeout=5.0
                            )
                            if response.status_code == 200:
                                return {"success": True, "message": f"Connected to OpenAI (HTTP {response.status_code})"}
                        except:
                            pass
                
                # If we are here, it might be a local provider using 'model' key (e.g. local_tts)
                # but without ws_url? Usually local providers have ws_url. 
                # If it's pure local without WS (e.g. wrapper), assume success if file paths exist?
                return {"success": True, "message": "Provider configuration valid (No specific connection test available)"}
        
        return {"success": False, "message": "Unknown provider type - cannot test"}
        
    except httpx.TimeoutException:
        return {"success": False, "message": "Connection timeout"}
    except Exception as e:
        return {"success": False, "message": f"Test failed: {str(e)}"}

@router.get("/export")
async def export_configuration():
    """Export configuration as a ZIP file"""
    try:
        import zipfile
        import io
        from datetime import datetime
        
        # Create ZIP in memory
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Add YAML config
            if os.path.exists(settings.CONFIG_PATH):
                zip_file.write(settings.CONFIG_PATH, 'ai-agent.yaml')
            
            # Add ENV file
            if os.path.exists(settings.ENV_PATH):
                zip_file.write(settings.ENV_PATH, '.env')
            
            # Add timestamp file
            timestamp = datetime.now().isoformat()
            zip_file.writestr('backup_info.txt', f'Backup created: {timestamp}\n')
        
        zip_buffer.seek(0)
        
        # Return as downloadable file
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            zip_buffer, 
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=config-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/export-logs")
async def export_logs():
    """Export logs and sanitized configuration for troubleshooting"""
    try:
        import zipfile
        import io
        import glob
        from datetime import datetime
        import subprocess
        
        # Create ZIP in memory
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # 1. Sanitized YAML
            if os.path.exists(settings.CONFIG_PATH):
                try:
                    import yaml
                    with open(settings.CONFIG_PATH, 'r') as f:
                        parsed = yaml.safe_load(f) or {}

                    def redact(obj):
                        if isinstance(obj, dict):
                            out = {}
                            for k, v in obj.items():
                                key = str(k).lower()
                                if any(s in key for s in ["api_key", "apikey", "token", "secret", "password", "pass", "key"]):
                                    out[k] = "[REDACTED]"
                                else:
                                    out[k] = redact(v)
                            return out
                        if isinstance(obj, list):
                            return [redact(v) for v in obj]
                        return obj

                    redacted = redact(parsed)
                    zip_file.writestr(
                        'ai-agent-sanitized.yaml',
                        yaml.safe_dump(redacted, sort_keys=False, default_flow_style=False),
                    )
                except Exception:
                    # Fallback: write raw if sanitization fails
                    with open(settings.CONFIG_PATH, 'r') as f:
                        zip_file.writestr('ai-agent-sanitized.yaml', f.read())
            
            # 2. Sanitized ENV (Just keys, no values)
            if os.path.exists(settings.ENV_PATH):
                env_keys = []
                with open(settings.ENV_PATH, 'r') as f:
                    for line in f:
                        if '=' in line and not line.startswith('#'):
                            key = line.split('=')[0].strip()
                            env_keys.append(f"{key}=[REDACTED]")
                zip_file.writestr('.env.sanitized', '\n'.join(env_keys))

            # 2b. Host OS info (if mounted) and basic Docker versions
            for os_release in ("/host/etc/os-release", "/etc/os-release"):
                if os.path.exists(os_release):
                    try:
                        with open(os_release, "r") as f:
                            zip_file.writestr("os-release.txt", f.read())
                        break
                    except Exception:
                        pass

            def add_cmd(name: str, cmd: list):
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    content = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
                    zip_file.writestr(name, content.strip() + "\n")
                except Exception as e:
                    zip_file.writestr(name, f"Failed to run {cmd}: {e}\n")

            add_cmd("docker-version.txt", ["docker", "version"])
            add_cmd("docker-compose-version.txt", ["docker", "compose", "version"])
            add_cmd("docker-ps.txt", ["docker", "ps", "-a"])
            
            # 3. Logs from Docker Containers
            try:
                import docker
                client = docker.from_env()
                containers_to_log = ['ai_engine', 'local_ai_server', 'admin_ui']
                
                found_logs = False
                for container_name in containers_to_log:
                    try:
                        container = client.containers.get(container_name)
                        # Capture full logs (no tail limit)
                        logs = container.logs().decode('utf-8', errors='replace')
                        if logs:
                            # Strip ANSI escape codes for clean log files
                            clean_logs = strip_ansi_codes(logs)
                            zip_file.writestr(f'{container_name}.log', clean_logs)
                            found_logs = True
                    except Exception as e:
                        zip_file.writestr(f'{container_name}_error.txt', f"Could not fetch logs: {str(e)}")
                
                if not found_logs:
                    zip_file.writestr('logs_info.txt', 'No logs retrieved from containers.')

            except Exception as e:
                 zip_file.writestr('docker_error.txt', f"Failed to connect to Docker API: {str(e)}")

            # Add timestamp
            timestamp = datetime.now().isoformat()
            zip_file.writestr('export_info.txt', f'Debug export created: {timestamp}\n')
        
        zip_buffer.seek(0)
        
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            zip_buffer, 
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=debug-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/import")
async def import_configuration(file: UploadFile = File(...)):
    """Import configuration from a ZIP file"""
    try:
        import zipfile
        import io
        import shutil
        import datetime
        
        content = await file.read()
        zip_buffer = io.BytesIO(content)
        
        if not zipfile.is_zipfile(zip_buffer):
             raise HTTPException(status_code=400, detail="Invalid file format. Must be a ZIP file.")
        
        # Create backups of current config
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if os.path.exists(settings.CONFIG_PATH):
            backup_path = f"{settings.CONFIG_PATH}.bak.{timestamp}"
            shutil.copy2(settings.CONFIG_PATH, backup_path)
            
        if os.path.exists(settings.ENV_PATH):
            backup_path = f"{settings.ENV_PATH}.bak.{timestamp}"
            shutil.copy2(settings.ENV_PATH, backup_path)
        
        with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
            # Check contents
            file_names = zip_ref.namelist()
            if 'ai-agent.yaml' not in file_names and '.env' not in file_names:
                raise HTTPException(status_code=400, detail="ZIP must contain ai-agent.yaml or .env")
            
            # Extract
            if 'ai-agent.yaml' in file_names:
                with open(settings.CONFIG_PATH, 'wb') as f:
                    f.write(zip_ref.read('ai-agent.yaml'))
                    
            if '.env' in file_names:
                with open(settings.ENV_PATH, 'wb') as f:
                    f.write(zip_ref.read('.env'))
                    
        return {"success": True, "message": "Configuration imported successfully."}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


def update_yaml_provider_field(provider_name: str, field: str, value: Any) -> bool:
    """
    Update a single field in a provider's YAML config.

    This helper is used by model-management flows (local-ai sync).

    Safety properties (aligned with update_yaml_config):
    - Creates a timestamped backup and rotates old backups
    - Validates resulting YAML against the canonical AppConfig schema
    - Writes atomically (temp file + rename) and preserves file mode
    """
    try:
        if not os.path.exists(settings.CONFIG_PATH):
            return False

        with open(settings.CONFIG_PATH, 'r') as f:
            config = _safe_load_no_duplicates(f.read())

        if not isinstance(config, dict):
            return False

        providers = config.get('providers')
        if not isinstance(providers, dict):
            providers = {}
        provider_block = providers.get(provider_name)
        if not isinstance(provider_block, dict):
            provider_block = {}

        if value is None:
            provider_block.pop(field, None)
        else:
            provider_block[field] = value

        providers[provider_name] = provider_block
        config['providers'] = providers

        content = yaml.dump(config, default_flow_style=False, sort_keys=False)

        # Validate before writing
        _validate_ai_agent_config(content)

        # Backup + rotate
        if os.path.exists(settings.CONFIG_PATH):
            import datetime
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = f"{settings.CONFIG_PATH}.bak.{timestamp}"
            with open(settings.CONFIG_PATH, 'r') as src:
                with open(backup_path, 'w') as dst:
                    dst.write(src.read())
            _rotate_backups(settings.CONFIG_PATH)

        # Atomic write (preserve permissions)
        dir_path = os.path.dirname(settings.CONFIG_PATH)
        original_mode = os.stat(settings.CONFIG_PATH).st_mode if os.path.exists(settings.CONFIG_PATH) else None

        with tempfile.NamedTemporaryFile('w', dir=dir_path, delete=False, suffix='.tmp') as tf:
            tf.write(content)
            temp_path = tf.name

        if original_mode is not None:
            os.chmod(temp_path, original_mode)

        os.replace(temp_path, settings.CONFIG_PATH)

        return True
    except Exception as e:
        print(f"Error updating YAML provider field: {e}")
        return False


@router.get("/options/{provider_type}")

async def get_provider_options(provider_type: str):
    """Get available options (models, voices) for a specific provider."""
    
    # Common catalogs
    DEEPGRAM_MODELS = [
        {"id": "nova-2", "name": "Nova 2 (General)", "cost": "Low", "latency": "Ultra Low"},
        {"id": "nova-2-phonecall", "name": "Nova 2 (Phonecall)", "cost": "Low", "latency": "Ultra Low"},
        {"id": "nova-2-medical", "name": "Nova 2 (Medical)", "cost": "Low", "latency": "Ultra Low"},
        {"id": "nova-2-meeting", "name": "Nova 2 (Meeting)", "cost": "Low", "latency": "Ultra Low"},
        {"id": "nova-2-general", "name": "Nova 2 (General Legacy)", "cost": "Low", "latency": "Ultra Low"},
        {"id": "listen", "name": "General (Listen)", "cost": "Medium", "latency": "Low"},
    ]
    
    OPENAI_LLM_MODELS = [
        {"id": "gpt-4o", "name": "GPT-4o (Omni)", "description": "Most capable"},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "description": "Fast & Cheap"},
        {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "description": "High intelligence"},
        {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "description": "Legacy fast"},
    ]
    
    OPENAI_STT_MODELS = [
        {"id": "whisper-1", "name": "Whisper V1"}
    ]
    
    GOOGLE_MODELS = [
        {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash (Fastest)"},
        {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro (Best Quality)"},
    ]

    GOOGLE_VOICES = [
        {"id": "en-US-Standard-A", "name": "US Female Standard"},
        {"id": "en-US-Standard-B", "name": "US Male Standard"},
        {"id": "en-US-Neural2-A", "name": "US Female Neural"},
        {"id": "en-US-Neural2-C", "name": "US Male Neural"},
        {"id": "en-US-Studio-O", "name": "US Female Studio"},
        {"id": "en-US-Studio-Q", "name": "US Male Studio"},
    ]

    # Return options based on provider
    if provider_type == "deepgram":
        return {"models": DEEPGRAM_MODELS}
        
    elif provider_type == "openai":
        return {
            "stt_models": OPENAI_STT_MODELS,
            "llm_models": OPENAI_LLM_MODELS,
            "tts_models": [{"id": "tts-1", "name": "TTS-1"}, {"id": "tts-1-hd", "name": "TTS-1 HD"}]
        }
        
    elif provider_type == "google":
        return {
            "models": GOOGLE_MODELS,
            "voices": GOOGLE_VOICES
        }
        
    elif provider_type == "elevenlabs":
        return {
            "models": [
                {"id": "eleven_turbo_v2_5", "name": "Turbo v2.5"},
                {"id": "eleven_multilingual_v2", "name": "Multilingual v2"},
                {"id": "eleven_monolingual_v1", "name": "Monolingual v1"}
            ]
        }
        
    elif provider_type == "local":
        return {"message": "Use /api/local-ai/models for dynamic local models"}
        
    return {"error": "Unknown provider type"}
