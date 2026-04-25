from fastapi import APIRouter, HTTPException, UploadFile, File
import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode, SequenceNode, ScalarNode
import os
import re
import asyncio
import glob
import tempfile
import sys
import logging
import ssl
import smtplib
from email.message import EmailMessage
from contextlib import contextmanager
from pydantic import BaseModel
from typing import Dict, Any, Optional, Union
from urllib.parse import urlparse
import settings

# A11: Maximum number of backups to keep
MAX_BACKUPS = 5

router = APIRouter()
logger = logging.getLogger(__name__)

def _is_prefix(key: str, prefixes: tuple[str, ...]) -> bool:
    return any(key.startswith(p) for p in prefixes)


def _running_container_names() -> set:
    """Return a set of container names that are currently running."""
    try:
        import docker  # type: ignore
        client = docker.from_env()
        return {c.name for c in client.containers.list(filters={"status": "running"})}
    except Exception:
        return set()


def _upsert_env_key(key: str, value: str) -> None:
    """Insert or update a single key in the project .env file."""
    env_path = settings.ENV_PATH
    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()

    found = False
    for i, line in enumerate(lines):
        stripped = line.lstrip("# ").strip()
        if stripped.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            found = True
            break

    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)


def _ai_engine_env_key(key: str) -> bool:
    return (
        # Track .env-owned health settings that require ai_engine recreate when changed.
        # Keep HEALTH_BIND_HOST excluded because compose can inject it even when unset,
        # which otherwise causes perpetual drift in Env UI.
        _is_prefix(key, ("ASTERISK_", "LOG_", "DIAG_", "CALL_HISTORY_", "HEALTH_CHECK_"))
        or key in ("HEALTH_API_TOKEN", "HEALTH_BIND_PORT")
        or key in (
            "OPENAI_API_KEY",
            "GROQ_API_KEY",
            "DEEPGRAM_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_CLOUD_PROJECT",  # Vertex AI
            "GOOGLE_CLOUD_LOCATION",  # Vertex AI
            "GOOGLE_APPLICATION_CREDENTIALS",  # Vertex AI service account
            "TELNYX_API_KEY",
            "RESEND_API_KEY",
            "ELEVENLABS_API_KEY",
            "ELEVENLABS_AGENT_ID",
            "TZ",
            "STREAMING_LOG_LEVEL",
        )
        or _is_prefix(key, ("AUDIO_TRANSPORT", "DOWNSTREAM_MODE", "AUDIOSOCKET_", "EXTERNAL_MEDIA_", "BARGE_IN_"))
        or _is_prefix(key, ("SMTP_",))
        # Local provider runtime uses these env vars via ${LOCAL_WS_*} placeholders in ai-agent.yaml
        or _is_prefix(key, ("LOCAL_WS_",))
    )


def _local_ai_env_key(key: str) -> bool:
    return (
        _is_prefix(key, ("LOCAL_", "KROKO_", "FASTER_WHISPER_", "WHISPER_CPP_", "MELOTTS_", "KOKORO_"))
        or key in ("SHERPA_MODEL_PATH",)
    )


def _admin_ui_env_key(key: str) -> bool:
    return (
        key in ("JWT_SECRET", "DOCKER_SOCK", "DOCKER_GID", "TZ")
        or _is_prefix(key, ("UVICORN_", "ADMIN_UI_"))
    )


def _assert_no_duplicate_yaml_keys(node: yaml.Node) -> None:
    """
    Detect duplicate mapping keys before calling yaml.safe_load().

    We avoid yaml.load() here to keep CodeQL happy while still enforcing our
    "no duplicate keys" constraint for Admin UI config edits.
    """
    if isinstance(node, MappingNode):
        seen: dict[str, ScalarNode] = {}
        for key_node, value_node in node.value:
            # Config files use string keys; if not, fall back to a stable repr.
            if isinstance(key_node, ScalarNode):
                key = str(key_node.value)
            else:
                key = str(key_node)
            if key in seen:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key ({key!r})",
                    key_node.start_mark,
                )
            if isinstance(key_node, ScalarNode):
                seen[key] = key_node
            _assert_no_duplicate_yaml_keys(value_node)
    elif isinstance(node, SequenceNode):
        for item in node.value:
            _assert_no_duplicate_yaml_keys(item)


def _safe_load_no_duplicates(content: str):
    node = yaml.compose(content, Loader=yaml.SafeLoader)
    if node is not None:
        _assert_no_duplicate_yaml_keys(node)
    return yaml.safe_load(content)


def _deep_merge_dicts(base: dict, override: dict) -> dict:
    """
    Recursively deep-merge *override* into a copy of *base*.

    Deletion semantics:
    - If the override explicitly sets a key to null/None, that key is removed from
      the merged output. This is important because our operator override file is
      merged on top of a git-tracked base file and needs a way to represent
      "delete this upstream default".
    """
    merged = dict(base)
    for key, override_val in override.items():
        if override_val is None:
            merged.pop(key, None)
            continue
        base_val = merged.get(key)
        if isinstance(base_val, dict) and isinstance(override_val, dict):
            merged[key] = _deep_merge_dicts(base_val, override_val)
        else:
            merged[key] = override_val
    return merged


def _read_base_config_dict() -> dict:
    """Read base config/ai-agent.yaml as a dict (no local overrides)."""
    if not os.path.exists(settings.CONFIG_PATH):
        return {}
    with open(settings.CONFIG_PATH, "r") as f:
        base = _safe_load_no_duplicates(f.read()) or {}
    return base if isinstance(base, dict) else {}


def _compute_local_override(base: dict, desired: dict) -> dict:
    """
    Compute a minimal operator-local override that, when merged over *base*,
    yields *desired* (including deletions via null tombstones).
    """
    if not isinstance(base, dict) or not isinstance(desired, dict):
        # Defensive: treat the desired value as a full replacement.
        return desired

    override: dict = {}

    # Include updates/additions.
    for key, desired_val in desired.items():
        if key not in base:
            override[key] = desired_val
            continue

        base_val = base.get(key)
        if isinstance(base_val, dict) and isinstance(desired_val, dict):
            child = _compute_local_override(base_val, desired_val)
            if child:
                override[key] = child
            continue

        if base_val != desired_val:
            override[key] = desired_val

    # Include deletions (tombstones).
    for key in base.keys():
        if key not in desired:
            override[key] = None

    return override


def _read_merged_config_dict() -> dict:
    """
    Read and return the merged config (base + local override) as a dict.

    Loads ``config/ai-agent.yaml`` (base), then deep-merges
    ``config/ai-agent.local.yaml`` (operator overrides) on top if it exists.
    """
    if not os.path.exists(settings.CONFIG_PATH):
        return {}
    with open(settings.CONFIG_PATH, "r") as f:
        base = _safe_load_no_duplicates(f.read()) or {}

    if not os.path.exists(settings.LOCAL_CONFIG_PATH):
        return base

    try:
        with open(settings.LOCAL_CONFIG_PATH, "r") as f:
            local = _safe_load_no_duplicates(f.read()) or {}
    except Exception:
        return base

    if not isinstance(local, dict):
        return base

    return _deep_merge_dicts(base, local)


def _read_merged_config_content() -> str:
    """Return the merged config as a YAML string (for display / validation)."""
    merged = _read_merged_config_dict()
    return yaml.dump(merged, default_flow_style=False, sort_keys=False) if merged else ""


def _write_local_config(content: str) -> None:
    """
    Atomically write *content* to the local override config file.

    Creates a backup of the existing local file (if any), validates permissions,
    and performs an atomic temp-file + rename write.
    """
    import datetime
    dir_path = os.path.dirname(settings.LOCAL_CONFIG_PATH)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)

    # Backup existing local file
    if os.path.exists(settings.LOCAL_CONFIG_PATH):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{settings.LOCAL_CONFIG_PATH}.bak.{timestamp}"
        with open(settings.LOCAL_CONFIG_PATH, "r") as src:
            with open(backup_path, "w") as dst:
                dst.write(src.read())
        _rotate_backups(settings.LOCAL_CONFIG_PATH)

    # Preserve permissions from existing local or base file
    original_mode = None
    for candidate in (settings.LOCAL_CONFIG_PATH, settings.CONFIG_PATH):
        if os.path.exists(candidate):
            original_mode = os.stat(candidate).st_mode
            break

    with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, suffix=".tmp") as f:
        f.write(content)
        temp_path = f.name

    if original_mode is not None:
        os.chmod(temp_path, original_mode)

    os.replace(temp_path, settings.LOCAL_CONFIG_PATH)


# Regex to strip ANSI escape codes from logs
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi_codes(text: str) -> str:
    """Remove ANSI escape codes from text for clean log files."""
    return ANSI_ESCAPE.sub('', text)

def _url_host(url: str) -> str:
    try:
        return (urlparse(str(url)).hostname or "").lower()
    except Exception:
        return ""


# SECURITY: Hardcoded base URLs for provider validation requests.
# Maps hostname → canonical base URL.  This prevents SSRF via user-supplied
# chat_base_url in YAML config by never forwarding the raw user string.
_SAFE_BASE_URLS: dict[str, str] = {
    "api.telnyx.com": "https://api.telnyx.com/v2/ai",
    "api.openai.com": "https://api.openai.com/v1",
    "api.groq.com": "https://api.groq.com/openai/v1",
    "openrouter.ai": "https://openrouter.ai/api/v1",
    "api.anthropic.com": "https://api.anthropic.com/v1",
    "api.deepgram.com": "https://api.deepgram.com/v1",
    "api.elevenlabs.io": "https://api.elevenlabs.io/v1",
    "generativelanguage.googleapis.com": "https://generativelanguage.googleapis.com/v1beta",
}


def _safe_base_url(user_url: str, fallback: str) -> str:
    """Return a hardcoded base URL for a known provider host, or *fallback*.

    The returned string is NEVER derived from *user_url* — only the hostname
    is extracted for lookup.  This breaks the CodeQL taint chain.
    """
    host = _url_host(user_url)
    return _SAFE_BASE_URLS.get(host, fallback)


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

        # Snapshot current merged config for hot-reload comparison
        old_merged = _read_merged_config_dict()

        # Parse desired merged config content from UI.
        new_parsed = _safe_load_no_duplicates(update.content) or {}
        if not isinstance(new_parsed, dict):
            raise HTTPException(status_code=400, detail="Config YAML must be a mapping at the top level")

        # Convert desired merged config into a minimal local override (supports deletions).
        base = _read_base_config_dict()
        local_override = _compute_local_override(base, new_parsed)
        local_content = yaml.dump(local_override or {}, default_flow_style=False, sort_keys=False)

        # Write to LOCAL override file (keeps base ai-agent.yaml clean for git)
        _write_local_config(local_content)
        
        # Determine recommended apply method based on what changed
        # hot_reload: contexts, MCP servers, greetings/instructions only
        # restart: most YAML changes (providers, pipelines, transport, VAD, etc.)
        # recreate: .env changes (handled separately in /env endpoint)
        recommended_method = "restart"  # Default for YAML changes
        
        # Check if change is limited to hot-reloadable sections
        try:
            if old_merged:
                # Keys that can be hot-reloaded
                hot_reload_keys = {'contexts', 'profiles', 'mcp'}
                
                # Check if only hot-reloadable keys changed
                all_keys = set(old_merged.keys()) | set(new_parsed.keys())
                changed_keys = set()
                for key in all_keys:
                    if old_merged.get(key) != new_parsed.get(key):
                        changed_keys.add(key)
                
                if changed_keys and changed_keys.issubset(hot_reload_keys):
                    recommended_method = "hot_reload"
        except Exception:
            pass  # Fall back to restart if comparison fails
        
        apply_plan = ([{"service": "ai_engine", "method": "hot_reload", "endpoint": "/api/system/containers/ai_engine/reload"}]
                     if recommended_method == "hot_reload"
                     else [{"service": "ai_engine", "method": "restart", "endpoint": "/api/system/containers/ai_engine/restart"}])

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
        # Return the merged config (base + local overrides) so the editor
        # always shows the effective configuration the engine will use.
        config_content = _read_merged_config_content()
        _safe_load_no_duplicates(config_content)  # Validate YAML and reject duplicate keys
        return {"content": config_content}
    except yaml.YAMLError as e:
        logger.info("YAML parse error while reading config YAML", exc_info=True)
        # Extract detailed error information for user-friendly display
        error_info = {
            "type": "yaml_error",
            "message": "Invalid YAML",
            "line": None,
            "column": None,
            "context": None,
            "snippet": None,
        }
        # Extract line/column from YAML error marks
        if hasattr(e, 'problem_mark') and e.problem_mark:
            mark = e.problem_mark
            error_info["line"] = mark.line + 1  # Convert to 1-indexed
            error_info["column"] = mark.column + 1
        if hasattr(e, 'context_mark') and e.context_mark:
            ctx_mark = e.context_mark
            error_info["context"] = f"Line {ctx_mark.line + 1}, column {ctx_mark.column + 1}"
        # Try to extract a snippet around the error line
        if error_info["line"]:
            try:
                lines = config_content.splitlines()
                line_num = error_info["line"] - 1  # 0-indexed
                start = max(0, line_num - 2)
                end = min(len(lines), line_num + 3)
                snippet_lines = []
                for i in range(start, end):
                    prefix = ">>> " if i == line_num else "    "
                    snippet_lines.append(f"{prefix}{i+1}: {lines[i]}")
                error_info["snippet"] = "\n".join(snippet_lines)
            except Exception:
                pass
        # Return content along with error so Raw YAML editor can still load it for fixing
        return {
            "content": config_content,
            "yaml_error": error_info
        }
    except Exception as e:
        logger.error("Unexpected error while reading config YAML", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read config YAML") from e

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
        # If file logging is enabled but no path is provided, default to the shared media volume.
        # This matches the UI recommendation and prevents "log to file" from silently falling back
        # to a non-writable working directory inside the container.
        try:
            log_to_file_raw = env_data.get("LOG_TO_FILE")
            log_to_file = str(log_to_file_raw or "").strip().lower()
            enabled = log_to_file in ("1", "true", "yes", "on")
            if enabled and not (str(env_data.get("LOG_FILE_PATH") or "").strip()):
                env_data["LOG_FILE_PATH"] = "/mnt/asterisk_media/ai-engine.log"
        except Exception:
            pass

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

        impacts_ai_engine = any(_ai_engine_env_key(k) for k in changed_keys)
        impacts_local_ai = any(_local_ai_env_key(k) for k in changed_keys)
        impacts_admin_ui = any(_admin_ui_env_key(k) for k in changed_keys)

        # Only suggest restart for containers that are actually running.
        # This avoids confusing "Apply Changes" prompts for e.g. local_ai_server
        # when it is not deployed.
        running = _running_container_names()

        apply_plan = []
        # NOTE: For ai_engine/local_ai_server, env_file (.env) changes require a force-recreate.
        # The frontend calls /restart?recreate=true for these services.
        if impacts_ai_engine and "ai_engine" in running:
            apply_plan.append({"service": "ai_engine", "method": "recreate", "endpoint": "/api/system/containers/ai_engine/restart"})
        if impacts_local_ai and "local_ai_server" in running:
            apply_plan.append({"service": "local_ai_server", "method": "recreate", "endpoint": "/api/system/containers/local_ai_server/restart"})
        if impacts_admin_ui and "admin_ui" in running:
            # Admin UI reads .env from disk at startup; a restart is sufficient in most cases.
            apply_plan.append({"service": "admin_ui", "method": "restart", "endpoint": "/api/system/containers/admin_ui/restart"})

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


@router.get("/env/status")
async def get_env_status():
    """
    Detect whether the running containers are out-of-sync with the project's `.env` file.

    This allows the UI to keep showing a correct "Apply Changes" plan even after a refresh,
    since `.env` edits persist but container environments only update on recreate/restart.
    """
    try:
        from dotenv import dotenv_values
    except Exception as e:
        raise HTTPException(status_code=500, detail="python-dotenv is required for env status") from e

    env_map = dotenv_values(settings.ENV_PATH) if os.path.exists(settings.ENV_PATH) else {}
    env_map = {k: str(v) for k, v in (env_map or {}).items() if k and v is not None}

    try:
        import docker  # type: ignore
        client = docker.from_env()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Docker unavailable for env status: {str(e)}")

    def _container_env(name: str) -> Dict[str, str]:
        try:
            c = client.containers.get(name)
            raw = (c.attrs.get("Config", {}) or {}).get("Env", []) or []
            out: Dict[str, str] = {}
            for item in raw:
                if not isinstance(item, str) or "=" not in item:
                    continue
                k, v = item.split("=", 1)
                out[str(k)] = str(v)
            return out
        except Exception:
            return {}

    def _diff_keys(*, desired: Dict[str, str], actual: Dict[str, str], key_pred) -> list[str]:
        keys = set()
        keys.update([k for k in desired.keys() if key_pred(k)])
        keys.update([k for k in actual.keys() if key_pred(k)])
        diffs = []
        for k in sorted(keys):
            want = str(desired.get(k, "") or "")
            got = str(actual.get(k, "") or "")
            if want != got:
                diffs.append(k)
        return diffs

    # Only compute drift for containers that are actually running.
    # Comparing .env against a non-existent container yields false drift for
    # every matching key (e.g. all LOCAL_* keys when local_ai_server is absent).
    running = _running_container_names()

    ai_env = _container_env("ai_engine") if "ai_engine" in running else {}
    local_env = _container_env("local_ai_server") if "local_ai_server" in running else {}
    admin_env = _container_env("admin_ui") if "admin_ui" in running else {}

    drift_ai = _diff_keys(desired=env_map, actual=ai_env, key_pred=_ai_engine_env_key) if "ai_engine" in running else []
    drift_local = _diff_keys(desired=env_map, actual=local_env, key_pred=_local_ai_env_key) if "local_ai_server" in running else []
    drift_admin = _diff_keys(desired=env_map, actual=admin_env, key_pred=_admin_ui_env_key) if "admin_ui" in running else []

    apply_plan = []
    if drift_local:
        apply_plan.append({"service": "local_ai_server", "method": "recreate", "endpoint": "/api/system/containers/local_ai_server/restart"})
    if drift_ai:
        apply_plan.append({"service": "ai_engine", "method": "recreate", "endpoint": "/api/system/containers/ai_engine/restart"})
    if drift_admin:
        apply_plan.append({"service": "admin_ui", "method": "restart", "endpoint": "/api/system/containers/admin_ui/restart"})

    return {
        "pending_restart": bool(apply_plan),
        "apply_plan": apply_plan,
        "drift": {
            "ai_engine": drift_ai,
            "local_ai_server": drift_local,
            "admin_ui": drift_admin,
        },
    }

class ProviderTestRequest(BaseModel):
    name: str
    config: Dict[str, Any]

class SmtpTestRequest(BaseModel):
    to_email: str
    from_email: Optional[str] = None
    subject: Optional[str] = None
    text: Optional[str] = None
    # Optional overrides (when testing unsaved UI form values).
    smtp_host: Optional[str] = None
    smtp_port: Optional[Union[int, str]] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_tls_mode: Optional[str] = None  # starttls | smtps | none
    smtp_tls_verify: Optional[Union[bool, str]] = None
    smtp_timeout_seconds: Optional[Union[float, str]] = None

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
                            value = line.split('=', 1)[1].strip()
                            # Strip surrounding single or double quotes (common .env convention)
                            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                                value = value[1:-1]
                            return value
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

                if data.get("type") == "status_response" and data.get("status") == "ok":
                    models = data.get("models", {})
                    stt_loaded = models.get("stt", {}).get("loaded", False)
                    llm_loaded = models.get("llm", {}).get("loaded", False)
                    tts_loaded = models.get("tts", {}).get("loaded", False)

                    stt_backend = data.get("stt_backend", "unknown")
                    tts_backend = data.get("tts_backend", "unknown")
                    llm_model = models.get("llm", {}).get("path", "").split("/")[-1] if models.get("llm", {}).get("path") else "none"

                    status_parts = []
                    status_parts.append(f"STT: {stt_backend} ✓" if stt_loaded else "STT: not loaded")
                    status_parts.append(f"LLM: {llm_model} ✓" if llm_loaded else "LLM: not loaded")
                    status_parts.append(f"TTS: {tts_backend} ✓" if tts_loaded else "TTS: not loaded")

                    all_loaded = stt_loaded and llm_loaded and tts_loaded
                    return {
                        "success": all_loaded,
                        "message": f"Local AI Server connected ({effective_url}). {' | '.join(status_parts)}",
                    }
                return {"success": False, "message": "Local AI Server responded but status invalid"}
            except Exception as e:
                logger.debug("Local AI Server validation failed", error=str(e), exc_info=True)
                return {"success": False, "message": f"Cannot connect to Local AI Server at {ws_url} (see server logs)"}
        
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
        # TELNYX (OpenAI-compatible) - validate /models + a tiny /chat/completions
        # ============================================================
        provider_type = str(provider_config.get('type') or '').lower()
        chat_base_url = (provider_config.get('chat_base_url') or provider_config.get('base_url') or '').rstrip('/')
        host = _url_host(chat_base_url)
        is_telnyx = provider_type in ('telnyx', 'telenyx') or ('telnyx' in provider_name) or host == 'api.telnyx.com'
        if is_telnyx:
            base_url = _safe_base_url(chat_base_url, 'https://api.telnyx.com/v2/ai')
            api_key = get_env_key('TELNYX_API_KEY') or os.getenv('TELNYX_API_KEY') or ''
            if not api_key:
                return {"success": False, "message": "TELNYX_API_KEY not set in .env"}

            # Prefer explicit model config; if unset, use a safe default for testing.
            model = (provider_config.get('chat_model') or provider_config.get('model') or '').strip()
            if not model:
                model = "Qwen/Qwen3-235B-A22B"

            api_key_ref = (provider_config.get('api_key_ref') or '').strip()
            if model.startswith('openai/') and not api_key_ref:
                return {
                    "success": False,
                    "message": "Telnyx external models like openai/* require api_key_ref (Integration Secret identifier).",
                }

            def _telnyx_error_summary(resp: httpx.Response) -> str:
                try:
                    j = resp.json()
                    if isinstance(j, dict) and isinstance(j.get("errors"), list) and j["errors"]:
                        e0 = j["errors"][0] if isinstance(j["errors"][0], dict) else {}
                        code = e0.get("code")
                        title = e0.get("title")
                        detail = e0.get("detail")
                        parts = [p for p in [code, title, detail] if p]
                        if parts:
                            return " / ".join(str(p) for p in parts)
                except Exception:
                    pass
                text = (resp.text or "").strip().replace("\n", " ")
                return text[:180] if text else f"HTTP {resp.status_code}"

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    models_resp = await client.get(
                        f"{base_url}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    if models_resp.status_code != 200:
                        return {"success": False, "message": f"Telnyx /models failed: {_telnyx_error_summary(models_resp)}"}

                    payload: Dict[str, Any] = {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "You are a test assistant."},
                            {"role": "user", "content": "Reply with exactly: OK"},
                        ],
                        "temperature": 0.0,
                        "max_tokens": 16,
                    }
                    if api_key_ref:
                        payload["api_key_ref"] = api_key_ref

                    chat_resp = await client.post(
                        f"{base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=payload,
                    )
                    if chat_resp.status_code == 200:
                        return {"success": True, "message": f"Connected to Telnyx. Chat completion OK with model: {model}"}
                    return {"success": False, "message": f"Telnyx chat completion failed: {_telnyx_error_summary(chat_resp)}"}
            except Exception as e:
                logger.debug("Telnyx provider validation failed", error=str(e), exc_info=True)
                return {"success": False, "message": f"Cannot connect to Telnyx at {base_url} (see server logs)"}

        # ============================================================
        # OPENAI-COMPATIBLE (OpenAI / Groq / OpenRouter / etc.) - validate /models
        # ============================================================
        if provider_type == 'openai':
            chat_base_url = _safe_base_url(
                provider_config.get('chat_base_url') or '', 'https://api.openai.com/v1'
            )
            api_key = provider_config.get('api_key')
            if not api_key:
                inferred_env = None
                host = _url_host(chat_base_url)
                if 'groq' in provider_name or host == 'api.groq.com':
                    inferred_env = 'GROQ_API_KEY'
                elif 'openai' in provider_name or host == 'api.openai.com':
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
                # Avoid leaking exception internals in API responses (CodeQL).
                logger.debug("OpenAI-compatible provider validation failed", error=str(e), exc_info=True)
                return {"success": False, "message": f"Cannot connect to provider at {chat_base_url} (see server logs)"}

        # ============================================================
        # GROQ SPEECH (STT/TTS) - validate via /models (OpenAI-compatible)
        # ============================================================
        if provider_config.get('type') == 'groq':
            api_key = provider_config.get('api_key') or get_env_key('GROQ_API_KEY') or os.getenv('GROQ_API_KEY') or ''
            if not api_key:
                return {"success": False, "message": "GROQ_API_KEY not set (set api_key or env var)"}

            # SECURITY: For provider validation, do not call user-provided base URLs.
            # Keep this check pinned to the official Groq OpenAI-compatible endpoint.
            base_url = 'https://api.groq.com/openai/v1'

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
                logger.debug("Groq Speech provider validation failed", error=str(e), exc_info=True)
                return {"success": False, "message": f"Cannot connect to provider at {base_url} (see server logs)"}
                
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
        
        # ============================================================
        # AZURE SPEECH SERVICE (STT / TTS)
        # ============================================================
        if provider_config.get('type') == 'azure' or 'azure' in provider_name:
            api_key = get_env_key('AZURE_SPEECH_KEY') or os.getenv('AZURE_SPEECH_KEY') or ''
            if not api_key:
                return {"success": False, "message": "AZURE_SPEECH_KEY not set in .env file"}
            region = provider_config.get('region', 'eastus')
            # Validate region to prevent SSRF via crafted region values
            import re
            _azure_region_re = re.compile(r"^[a-z][a-z0-9-]{0,48}[a-z0-9]$")
            region = str(region).strip().lower()
            if not region or not _azure_region_re.match(region):
                return {"success": False, "message": f"Invalid Azure region '{region}'. Expected lowercase alphanumeric (e.g. 'eastus')."}
            # Hit the token endpoint — a 200 or 400 response proves the key is recognized
            token_url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        token_url,
                        headers={"Ocp-Apim-Subscription-Key": api_key},
                        timeout=10.0,
                    )
                    if response.status_code == 200:
                        capabilities = provider_config.get('capabilities', [])
                        cap_str = '/'.join(str(c).upper() for c in capabilities) if capabilities else 'Speech'
                        return {"success": True, "message": f"Connected to Azure Speech Service ({region}). {cap_str} key valid."}
                    if response.status_code == 401:
                        return {"success": False, "message": "Invalid AZURE_SPEECH_KEY (401 Unauthorized)"}
                    return {"success": False, "message": f"Azure Speech API returned HTTP {response.status_code} for region '{region}'"}
            except Exception as e:
                logger.debug("Azure Speech provider validation failed", error=str(e), exc_info=True)
                return {"success": False, "message": f"Cannot connect to Azure Speech Service at region '{region}' (see server logs)"}

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
            # Add YAML config (base + local override)
            if os.path.exists(settings.CONFIG_PATH):
                zip_file.write(settings.CONFIG_PATH, 'ai-agent.yaml')
            if os.path.exists(settings.LOCAL_CONFIG_PATH):
                zip_file.write(settings.LOCAL_CONFIG_PATH, 'ai-agent.local.yaml')
            
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


@router.post("/env/smtp/test")
async def test_smtp_settings(req: SmtpTestRequest):
    """
    Send a test email using SMTP_* settings from the project's .env file.

    This validates connectivity + auth using the *configured* SMTP settings (as saved by the UI),
    even before the ai_engine container is force-recreated to pick up env_file changes.
    """
    try:
        from dotenv import dotenv_values
    except Exception as e:
        raise HTTPException(status_code=500, detail="python-dotenv is required for SMTP test") from e

    if not (req.to_email or "").strip():
        raise HTTPException(status_code=400, detail="to_email is required")

    env_map = dotenv_values(settings.ENV_PATH) if os.path.exists(settings.ENV_PATH) else {}

    host = str((req.smtp_host if req.smtp_host is not None else (env_map or {}).get("SMTP_HOST")) or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="SMTP_HOST is not set (save it in .env or pass smtp_host)")

    tls_mode = str((req.smtp_tls_mode if req.smtp_tls_mode is not None else (env_map or {}).get("SMTP_TLS_MODE")) or "starttls").strip().lower()
    if tls_mode not in {"starttls", "smtps", "none"}:
        raise HTTPException(status_code=400, detail="SMTP_TLS_MODE must be starttls, smtps, or none")

    port_raw = str((req.smtp_port if req.smtp_port is not None else (env_map or {}).get("SMTP_PORT")) or "").strip()
    try:
        port = int(port_raw) if port_raw else (465 if tls_mode == "smtps" else 587)
    except Exception:
        raise HTTPException(status_code=400, detail="SMTP_PORT must be an integer")

    username = str((req.smtp_username if req.smtp_username is not None else (env_map or {}).get("SMTP_USERNAME")) or "").strip() or None
    password = str((req.smtp_password if req.smtp_password is not None else (env_map or {}).get("SMTP_PASSWORD")) or "").strip() or None

    timeout_raw = str((req.smtp_timeout_seconds if req.smtp_timeout_seconds is not None else (env_map or {}).get("SMTP_TIMEOUT_SECONDS")) or "10").strip()
    try:
        timeout_s = float(timeout_raw or "10")
    except Exception:
        timeout_s = 10.0

    tls_verify_raw = req.smtp_tls_verify if req.smtp_tls_verify is not None else (env_map or {}).get("SMTP_TLS_VERIFY")
    if isinstance(tls_verify_raw, bool):
        tls_verify = tls_verify_raw
    else:
        tls_verify = str(tls_verify_raw or "true").strip().lower() in {"1", "true", "yes", "on"}
    context = ssl.create_default_context()
    if not tls_verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    from_email = (req.from_email or "").strip()
    if not from_email:
        # Best-effort default: many SMTP servers expect From to match the authenticated mailbox.
        from_email = (username or "test@localhost")

    subject = (req.subject or "").strip() or "Asterisk AI Voice Agent - SMTP Test"
    text = (req.text or "").strip() or (
        "This is a test email sent by the Admin UI to verify your SMTP settings.\n\n"
        "If you received this, SMTP is configured correctly."
    )

    msg = EmailMessage()
    msg["To"] = req.to_email.strip()
    msg["From"] = from_email
    msg["Subject"] = subject
    msg.set_content(text)

    def _send_sync() -> None:
        if tls_mode == "smtps":
            with smtplib.SMTP_SSL(host=host, port=port, timeout=timeout_s, context=context) as smtp:
                smtp.ehlo()
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(msg, to_addrs=[req.to_email.strip()])
            return

        with smtplib.SMTP(host=host, port=port, timeout=timeout_s) as smtp:
            smtp.ehlo()
            if tls_mode == "starttls":
                smtp.starttls(context=context)
                smtp.ehlo()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(msg, to_addrs=[req.to_email.strip()])

    try:
        await asyncio.to_thread(_send_sync)
        return {
            "success": True,
            "message": "Test email accepted by SMTP server",
            "host": host,
            "port": port,
            "tls_mode": tls_mode,
            "tls_verify": tls_verify,
        }
    except Exception as e:
        # Do not echo secrets; only return the error string.
        raise HTTPException(status_code=500, detail=f"SMTP test failed: {str(e)}")

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
            # 1. Sanitized YAML (merged base + local override)
            try:
                import yaml
                parsed = _read_merged_config_dict()

                import re
                email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
                # Pattern for hostnames that look like internal infrastructure
                hostname_pattern = re.compile(r'\b(?:pbx|sip|voip|trunk|asterisk)[a-zA-Z0-9.-]*\.[a-zA-Z]{2,}\b', re.IGNORECASE)
                
                def redact(obj):
                    if isinstance(obj, dict):
                        out = {}
                        for k, v in obj.items():
                            key = str(k).lower()
                            # Redact sensitive keys
                            if any(s in key for s in ["api_key", "apikey", "token", "secret", "password", "pass", "key"]):
                                out[k] = "[REDACTED]"
                            # Redact email fields
                            elif "email" in key:
                                out[k] = "[EMAIL_REDACTED]"
                            else:
                                out[k] = redact(v)
                        return out
                    if isinstance(obj, list):
                        return [redact(v) for v in obj]
                    # Redact email addresses and sensitive hostnames in string values
                    if isinstance(obj, str):
                        result = email_pattern.sub('[EMAIL_REDACTED]', obj)
                        result = hostname_pattern.sub('[HOSTNAME_REDACTED]', result)
                        return result
                    return obj

                if parsed:
                    redacted = redact(parsed)
                    zip_file.writestr(
                        'ai-agent-sanitized.yaml',
                        yaml.safe_dump(redacted, sort_keys=False, default_flow_style=False),
                    )
            except Exception:
                # Fallback: write raw base if sanitization fails
                if os.path.exists(settings.CONFIG_PATH):
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
                            # Redact sensitive information for privacy (AAVA-162)
                            import re
                            # Email addresses
                            clean_logs = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[EMAIL_REDACTED]', clean_logs)
                            # PBX/SIP/VoIP hostnames (likely internal infrastructure)
                            clean_logs = re.sub(r'\b(?:pbx|sip|voip|trunk|asterisk)[a-zA-Z0-9.-]*\.[a-zA-Z]{2,}\b', '[HOSTNAME_REDACTED]', clean_logs, flags=re.IGNORECASE)
                            # API key previews (e.g., api_key_preview=AIzaSyB2..._H_M)
                            clean_logs = re.sub(r'(api_key_preview=)[^\s\]]+', r'\1[REDACTED]', clean_logs)
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
        
        if os.path.exists(settings.LOCAL_CONFIG_PATH):
            backup_path = f"{settings.LOCAL_CONFIG_PATH}.bak.{timestamp}"
            shutil.copy2(settings.LOCAL_CONFIG_PATH, backup_path)
            
        if os.path.exists(settings.ENV_PATH):
            backup_path = f"{settings.ENV_PATH}.bak.{timestamp}"
            shutil.copy2(settings.ENV_PATH, backup_path)
        
        with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
            # Check contents
            file_names = zip_ref.namelist()
            if 'ai-agent.yaml' not in file_names and 'ai-agent.local.yaml' not in file_names and '.env' not in file_names:
                raise HTTPException(status_code=400, detail="ZIP must contain ai-agent.yaml, ai-agent.local.yaml, or .env")
            
            # Extract: imported ai-agent.yaml content goes to the LOCAL override
            # so the git-tracked base stays clean.
            if 'ai-agent.local.yaml' in file_names:
                with open(settings.LOCAL_CONFIG_PATH, 'wb') as f:
                    f.write(zip_ref.read('ai-agent.local.yaml'))
            elif 'ai-agent.yaml' in file_names:
                with open(settings.LOCAL_CONFIG_PATH, 'wb') as f:
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

    Reads the merged config (base + local), applies the change, validates,
    and writes the result to the LOCAL override file so the git-tracked
    base stays clean.
    """
    try:
        base_config = _read_base_config_dict()
        merged_config = _read_merged_config_dict()
        if not merged_config:
            return False

        providers = merged_config.get('providers')
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
        merged_config['providers'] = providers

        # Validate the fully-merged config, then persist only minimal local override
        # so base defaults can continue to evolve across releases.
        merged_content = yaml.dump(merged_config, default_flow_style=False, sort_keys=False)

        # Validate before writing
        _validate_ai_agent_config(merged_content)

        local_override = _compute_local_override(base_config, merged_config)
        content = yaml.dump(local_override, default_flow_style=False, sort_keys=False)

        # Write to LOCAL override file
        _write_local_config(content)

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


# ─────────────────────────────────────────────────────────────────────────────
# Vertex AI Service Account JSON Management
# ─────────────────────────────────────────────────────────────────────────────

# Store in project secrets dir - Admin UI has write access, ai_engine mounts it
VERTEX_CREDENTIALS_PATH = "/app/project/secrets/gcp-service-account.json"
VERTEX_REGIONS = [
    {"value": "us-central1", "label": "US Central (Iowa)"},
    {"value": "us-east1", "label": "US East (South Carolina)"},
    {"value": "us-east4", "label": "US East (Northern Virginia)"},
    {"value": "us-west1", "label": "US West (Oregon)"},
    {"value": "us-west4", "label": "US West (Las Vegas)"},
    {"value": "europe-west1", "label": "Europe West (Belgium)"},
    {"value": "europe-west2", "label": "Europe West (London)"},
    {"value": "europe-west3", "label": "Europe West (Frankfurt)"},
    {"value": "europe-west4", "label": "Europe West (Netherlands)"},
    {"value": "asia-east1", "label": "Asia East (Taiwan)"},
    {"value": "asia-northeast1", "label": "Asia Northeast (Tokyo)"},
    {"value": "asia-southeast1", "label": "Asia Southeast (Singapore)"},
    {"value": "australia-southeast1", "label": "Australia (Sydney)"},
]


@router.get("/vertex-ai/regions")
async def get_vertex_regions():
    """Return available Vertex AI regions."""
    return {"regions": VERTEX_REGIONS}


@router.get("/vertex-ai/credentials")
async def get_vertex_credentials_status():
    """Check if Vertex AI credentials are uploaded and return metadata."""
    import json
    
    if not os.path.exists(VERTEX_CREDENTIALS_PATH):
        return {
            "uploaded": False,
            "filename": None,
            "project_id": None,
            "client_email": None,
            "uploaded_at": None,
        }
    
    try:
        stat = os.stat(VERTEX_CREDENTIALS_PATH)
        with open(VERTEX_CREDENTIALS_PATH, 'r') as f:
            creds = json.load(f)
        
        return {
            "uploaded": True,
            "filename": "gcp-service-account.json",
            "project_id": creds.get("project_id"),
            "client_email": creds.get("client_email"),
            "uploaded_at": stat.st_mtime,
        }
    except Exception as e:
        logger.error(f"Error reading Vertex AI credentials: {e}")
        return {
            "uploaded": True,
            "filename": "gcp-service-account.json",
            "project_id": None,
            "client_email": None,
            "error": "Failed to read credentials file",
        }


@router.post("/vertex-ai/credentials")
async def upload_vertex_credentials(file: UploadFile = File(...)):
    """Upload a GCP service account JSON file for Vertex AI authentication."""
    import json
    
    if not file.filename or not file.filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="File must be a JSON file")
    
    try:
        content = await file.read()
        # Validate JSON structure
        creds = json.loads(content)
        
        required_fields = ["type", "project_id", "private_key", "client_email"]
        missing = [f for f in required_fields if f not in creds]
        if missing:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid service account JSON. Missing fields: {', '.join(missing)}"
            )
        
        if creds.get("type") != "service_account":
            raise HTTPException(
                status_code=400,
                detail="JSON file must be a service account key (type: service_account)"
            )
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(VERTEX_CREDENTIALS_PATH), exist_ok=True)
        
        # Write atomically
        temp_path = VERTEX_CREDENTIALS_PATH + ".tmp"
        with open(temp_path, 'wb') as f:
            f.write(content)
        os.chmod(temp_path, 0o600)  # Restrict permissions
        os.replace(temp_path, VERTEX_CREDENTIALS_PATH)
        
        # Auto-upsert GOOGLE_APPLICATION_CREDENTIALS in .env so the env var
        # persists across container recreates.  The container mount path is
        # /app/project/secrets/gcp-service-account.json (see docker-compose.yml).
        try:
            _upsert_env_key("GOOGLE_APPLICATION_CREDENTIALS", "/app/project/secrets/gcp-service-account.json")
        except Exception:
            logger.warning("Could not auto-set GOOGLE_APPLICATION_CREDENTIALS in .env")

        logger.info(f"Vertex AI credentials uploaded: project={creds.get('project_id')}")
        
        return {
            "status": "success",
            "message": "Service account JSON uploaded successfully",
            "project_id": creds.get("project_id"),
            "client_email": creds.get("client_email"),
        }
        
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading Vertex AI credentials: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload credentials")


@router.delete("/vertex-ai/credentials")
async def delete_vertex_credentials():
    """Delete the uploaded Vertex AI credentials file."""
    if not os.path.exists(VERTEX_CREDENTIALS_PATH):
        raise HTTPException(status_code=404, detail="No credentials file found")
    
    try:
        os.remove(VERTEX_CREDENTIALS_PATH)
        logger.info("Vertex AI credentials deleted")
        return {"status": "success", "message": "Credentials deleted"}
    except Exception as e:
        logger.error(f"Error deleting Vertex AI credentials: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete credentials")


@router.post("/vertex-ai/verify")
async def verify_vertex_credentials():
    """Verify Vertex AI credentials by attempting to get an access token."""
    import json
    
    if not os.path.exists(VERTEX_CREDENTIALS_PATH):
        raise HTTPException(status_code=400, detail="No credentials file uploaded")
    
    try:
        # Try to use google-auth to verify credentials
        import asyncio
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        
        def _refresh_credentials():
            """Blocking credential refresh - run in thread to avoid blocking event loop."""
            creds = service_account.Credentials.from_service_account_file(
                VERTEX_CREDENTIALS_PATH,
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            creds.refresh(Request())
            return creds
        
        # Run blocking credential refresh in thread pool
        credentials = await asyncio.to_thread(_refresh_credentials)
        
        # Read project info
        with open(VERTEX_CREDENTIALS_PATH, 'r') as f:
            creds_data = json.load(f)
        
        return {
            "status": "success",
            "message": "Credentials verified successfully",
            "project_id": creds_data.get("project_id"),
            "client_email": creds_data.get("client_email"),
            "token_expiry": credentials.expiry.isoformat() if credentials.expiry else None,
        }
        
    except ImportError:
        # google-auth not installed in admin_ui - just validate JSON structure
        try:
            with open(VERTEX_CREDENTIALS_PATH, 'r') as f:
                creds_data = json.load(f)
            
            required = ["type", "project_id", "private_key", "client_email"]
            if all(k in creds_data for k in required):
                return {
                    "status": "success",
                    "message": "Credentials file structure is valid (full verification requires google-auth)",
                    "project_id": creds_data.get("project_id"),
                    "client_email": creds_data.get("client_email"),
                    "warning": "Install google-auth for full token verification",
                }
            else:
                raise HTTPException(status_code=400, detail="Invalid credentials structure")
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in credentials file")
            
    except Exception as e:
        logger.error(f"Error verifying Vertex AI credentials: {e}")
        raise HTTPException(status_code=400, detail="Verification failed - check credentials are valid")


# ─────────────────────────────────────────────────────────────────────────────
# Google Calendar — Per-Key Info & Verify
# ─────────────────────────────────────────────────────────────────────────────
#
# These endpoints support the Tools page Google Calendar section. They cover:
#   - Surfacing the service account identity (client_email + client_id) for
#     each calendar entry, so operators don't have to grep the JSON file to
#     know what email to share their calendar with, and so DWD setup can use
#     the correct client_id (NOT the email — admin.google.com expects the
#     OAuth client ID).
#   - Verifying that the configured credentials can actually read the
#     configured calendar — distinguishing "bad credentials" from "calendar
#     not shared" from "wrong calendar id" with separate error codes.
#
# Verify uses the raw googleapiclient (not the GCalendar wrapper, which
# swallows API exceptions as [] / None / False — unusable for diagnostics).
# Per Codex feedback: error codes must surface 401 / 403 / 404 distinctly.
#
# Verify accepts an optional POST body so the UI can test unsaved form state
# without forcing a save first. Body fields override persisted config.

# Calendar keys are user-chosen identifiers (e.g. "work", "calendar_1").
# They appear in URL paths for these endpoints, so we constrain them tightly
# to prevent path-traversal-shaped values from being smuggled through.
# The same regex is used by the gcal tool's calendar resolver — keys outside
# this set are not addressable in YAML either.
_CALENDAR_KEY_PATTERN = re.compile(r"^[a-z0-9_-]{1,64}$", re.IGNORECASE)


def _validate_calendar_key_or_400(key: str) -> str:
    """Validate a calendar key from a URL path. Raise 400 on bad input.

    Allowed: alphanumeric, underscore, hyphen, 1-64 chars. Rejects path
    components, slashes, dots, control chars, oversized values, unicode etc.
    """
    if not isinstance(key, str) or not _CALENDAR_KEY_PATTERN.fullmatch(key or ""):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_calendar_key",
                "message": "Calendar key must be 1-64 chars of [a-z0-9_-] only.",
            },
        )
    return key


def _read_google_calendar_entry(key: str) -> dict:
    """Look up the persisted config for one calendar key. Returns {} if absent."""
    try:
        merged = _read_merged_config_dict()
    except Exception:
        return {}
    gcal = (merged.get("tools") or {}).get("google_calendar") or {}
    cals = gcal.get("calendars") or {}
    if not isinstance(cals, dict):
        return {}
    entry = cals.get(key)
    return entry if isinstance(entry, dict) else {}


_ALLOWED_CREDENTIALS_DIRS = (
    "/app/project/secrets",
    "/app/secrets",
    "/secrets",
)


def _assert_creds_path_in_allowed_dir(norm_path: str, original: str) -> None:
    """Reject any path that doesn't resolve under one of our known secrets
    directories. Defense-in-depth even when ``creds_path`` came from
    persisted config (the UI writes new uploads into GOOGLE_CALENDAR_SECRETS_DIR
    but legacy YAML may point elsewhere — we still constrain to a fixed set
    of mount roots so user-controlled values can never escape into reading
    arbitrary host files via this endpoint). Closes CodeQL warnings re
    ``Uncontrolled data used in path expression``.
    """
    real_dirs = [os.path.realpath(d) for d in _ALLOWED_CREDENTIALS_DIRS]
    for safe in real_dirs:
        try:
            common = os.path.commonpath([norm_path, safe])
        except ValueError:
            continue
        if common == safe:
            return
    raise HTTPException(
        status_code=400,
        detail={
            "error_code": "credentials_path_outside_allowed_dirs",
            "message": (
                f"credentials_path '{original}' resolves outside the allowed "
                f"secrets directories ({', '.join(_ALLOWED_CREDENTIALS_DIRS)}). "
                "Move the file under one of those mounts and update the path."
            ),
        },
    )


def _load_sa_metadata(creds_path: str) -> dict:
    """Read the SA JSON at ``creds_path`` and return its identity metadata.

    Raises HTTPException with structured detail on any failure: file missing,
    not JSON, not a service-account file, unreadable, etc.
    """
    import json

    if not creds_path:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "missing_credentials_path",
                "message": "No credentials_path configured for this calendar key.",
            },
        )

    # Canonicalize and resolve; refuse traversal-shaped paths.
    try:
        norm_path = os.path.realpath(creds_path)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_credentials_path",
                "message": "credentials_path could not be resolved.",
            },
        ) from e

    # Constrain user-supplied creds_path to a known set of mount roots
    # via Path.relative_to() — the CodeQL-recognized sanitizer pattern.
    # Without this, an operator (or a UI bug) could submit a path like
    # "/etc/passwd" and trigger a file read against an arbitrary host
    # file. After this guard, `safe_path` is provably-rooted under one
    # of the allow-listed dirs and CAN be used in subsequent file ops.
    #
    # Past attempts using commonpath() in a list-loop weren't recognized
    # by CodeQL's py/path-injection analyzer; the relative_to() pattern
    # below is the canonical sanitizer per CodeQL's published
    # documentation.
    from pathlib import Path
    _candidate_path = Path(norm_path)
    safe_path: Path | None = None
    for _allowed in _ALLOWED_CREDENTIALS_DIRS:
        _allowed_real = Path(_allowed).resolve()
        try:
            _candidate_path.relative_to(_allowed_real)
        except ValueError:
            continue
        # `relative_to` succeeded → _candidate_path is provably under
        # _allowed_real. Construct the sanitized path by re-rooting at
        # the safe prefix to make the constraint visible to data-flow
        # analysis rather than relying on the unmodified user input.
        safe_path = _allowed_real / _candidate_path.relative_to(_allowed_real)
        break
    if safe_path is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "credentials_path_outside_allowed_dirs",
                "message": (
                    f"credentials_path '{creds_path}' resolves outside the allowed "
                    f"secrets directories ({', '.join(_ALLOWED_CREDENTIALS_DIRS)}). "
                    "Move the file under one of those mounts and update the path."
                ),
            },
        )

    if not safe_path.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "credentials_file_not_found",
                "message": f"No file at credentials_path '{creds_path}'.",
            },
        )

    if not safe_path.is_file():
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "credentials_path_not_a_file",
                "message": f"credentials_path '{creds_path}' is not a regular file.",
            },
        )

    try:
        with safe_path.open("r") as f:
            raw = f.read()
    except OSError as e:
        # Most common case here is a permissions error (e.g. file owned by
        # root with mode 600 and admin_ui running as appuser). Surface it
        # explicitly so the operator knows to chmod 640 + chgrp appuser.
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "credentials_file_unreadable",
                "message": (
                    f"Cannot read credentials file: {e}. "
                    "This is usually a permissions issue — the admin_ui "
                    "process needs read access (try chmod 640 + group "
                    "ownership 'appuser')."
                ),
            },
        ) from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "credentials_not_json",
                "message": "credentials_path file does not contain valid JSON.",
            },
        ) from e

    if not isinstance(data, dict) or data.get("type") != "service_account":
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "credentials_not_service_account",
                "message": "credentials_path does not point to a Google service-account JSON file.",
            },
        )

    required = ("client_email", "private_key", "private_key_id")
    if not all(data.get(k) for k in required):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "credentials_missing_fields",
                "message": f"Service account JSON missing required fields: {required}.",
            },
        )

    return {
        "client_email": data.get("client_email", ""),
        "client_id": data.get("client_id", ""),
        "project_id": data.get("project_id", ""),
        "private_key_id": data.get("private_key_id", ""),
    }


@router.get("/google-calendar/{key}/info")
async def get_google_calendar_info(key: str, credentials_path: Optional[str] = None):
    """Return SA identity metadata for a configured calendar entry.

    The operator needs `client_email` to share their calendar with the SA,
    and `client_id` for Domain-Wide Delegation setup. Both are surfaced so
    the operator never has to crack open the JSON file by hand.

    Accepts an optional ``credentials_path`` query parameter so the UI can
    load identity for unsaved form state (e.g. a manual path edit before
    Save). Without this, the UI would load stale identity from the
    persisted YAML for any path that's been typed but not saved. Symmetric
    with /verify's POST-body override. Codex feedback #4.
    """
    _validate_calendar_key_or_400(key)
    entry = _read_google_calendar_entry(key)
    # Override-then-fallback: if the caller passed credentials_path explicitly,
    # use that; otherwise fall back to the persisted entry's path.
    effective_path = (credentials_path or "").strip() or (entry.get("credentials_path") or "").strip()
    metadata = _load_sa_metadata(effective_path)
    return {
        "key": key,
        "calendar_id": entry.get("calendar_id", ""),
        "configured_timezone": entry.get("timezone", ""),
        **metadata,
    }


class _GoogleCalendarVerifyRequest(BaseModel):
    """Optional overrides so the UI can verify unsaved form state.

    All fields are optional; missing fields fall back to the persisted
    configuration for the calendar key. This means the operator can edit
    the form and click Verify without saving first — Codex feedback #1.
    """
    credentials_path: Optional[str] = None
    calendar_id: Optional[str] = None
    timezone: Optional[str] = None
    subject: Optional[str] = None  # For Domain-Wide Delegation (Phase 1)


def _verify_calendar_access_sync(
    creds_path: str,
    calendar_id: str,
    configured_timezone: str,
    subject: Optional[str],
) -> dict:
    """Blocking work: build SA creds, optionally impersonate, hit Calendar API.

    Run via asyncio.to_thread. Raises HTTPException with structured detail on
    any failure (so the FastAPI handler doesn't have to translate exceptions).

    Per Codex feedback #5: when ``subject`` is set, refresh the token and call
    ``calendars.get()`` as the impersonated user. Building the credential with
    ``with_subject()`` alone can succeed even when DWD scopes/admin consent
    haven't been configured — the failure only surfaces on the first API call.
    """
    import json

    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "google_libs_not_installed",
                "message": "google-auth + googleapiclient are required for verify.",
            },
        )

    # Constrain creds_path to the allow-listed secrets dirs before opening
    # the file. The verify endpoint accepts creds_path from the request body
    # (so operators can verify unsaved UI edits), so without this check it
    # could be used to read arbitrary host files. Same guard applied in
    # _load_sa_metadata and _discover_accessible_calendars; this closes the
    # gap CodeRabbit flagged on this endpoint specifically.
    try:
        norm_creds_path = os.path.realpath(creds_path)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_credentials_path",
                "message": f"credentials_path could not be resolved: {e}",
            },
        ) from e
    _assert_creds_path_in_allowed_dir(norm_creds_path, creds_path)

    # Build SA creds. Failures here are credential-shape problems, not
    # API-side problems — distinguish them.
    try:
        creds = service_account.Credentials.from_service_account_file(
            norm_creds_path,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_credentials",
                "message": f"Could not load service-account credentials: {e}",
            },
        ) from e

    if subject:
        creds = creds.with_subject(subject)

    # Force a token refresh — surfaces DWD misconfiguration BEFORE we hit the
    # Calendar API. With DWD, with_subject() succeeds at construction but the
    # token mint can fail (admin consent not granted, scopes wrong, etc.).
    try:
        creds.refresh(Request())
    except Exception as e:
        # Heuristic: "unauthorized_client" is the canonical DWD-not-configured
        # response from Google's token endpoint.
        msg = str(e).lower()
        if subject and ("unauthorized_client" in msg or "invalid_grant" in msg):
            raise HTTPException(
                status_code=401,
                detail={
                    "error_code": "dwd_not_configured",
                    "message": (
                        f"Domain-Wide Delegation is not configured for "
                        f"subject '{subject}'. Add the service account's "
                        f"client_id (NOT email) at admin.google.com → "
                        f"Security → Access and data control → API controls "
                        f"→ Domain-wide delegation, with scope "
                        f"'https://www.googleapis.com/auth/calendar'. "
                        f"Underlying error: {e}"
                    ),
                },
            )
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "auth_failed",
                "message": f"Could not obtain access token: {e}",
            },
        )

    # Now hit the Calendar API. Distinguish 401/403/404 cleanly.
    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        cal = service.calendars().get(calendarId=calendar_id).execute()
    except HttpError as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        # Try to extract Google's own error reason for nicer messages.
        try:
            err_payload = json.loads(e.content.decode("utf-8")) if hasattr(e, "content") else {}
            reason = (((err_payload.get("error") or {}).get("errors") or [{}])[0]).get("reason", "")
        except Exception:
            reason = ""

        if status == 401:
            raise HTTPException(
                status_code=401,
                detail={
                    "error_code": "auth_failed",
                    "message": f"Calendar API rejected the credentials (401). reason={reason!r}",
                },
            )
        if status == 403:
            # Most common cause: calendar exists but isn't shared with the SA.
            raise HTTPException(
                status_code=403,
                detail={
                    "error_code": "forbidden_calendar",
                    "message": (
                        f"The service account is not authorized to access "
                        f"calendar '{calendar_id}'. Most commonly this means "
                        f"the calendar hasn't been shared with the service "
                        f"account email. (HTTP 403, reason={reason!r})"
                    ),
                },
            )
        if status == 404:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "calendar_not_found",
                    "message": (
                        f"Calendar '{calendar_id}' not found. Check the ID — "
                        f"primary calendars use the user's email; secondary "
                        f"calendars look like 'c_xxx@group.calendar.google.com'."
                    ),
                },
            )
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "calendar_api_error",
                "message": f"Calendar API error: HTTP {status} reason={reason!r}",
            },
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "unknown",
                "message": f"Unexpected error during verify: {e}",
            },
        )

    actual_tz = cal.get("timeZone") or ""
    drift_warning = None
    if configured_timezone and actual_tz and configured_timezone != actual_tz:
        drift_warning = (
            f"Configured timezone '{configured_timezone}' does not match the "
            f"calendar's actual timezone '{actual_tz}'. Events will use the "
            f"configured timezone, which may produce wrong wall-clock times. "
            f"Update the Timezone field to match."
        )

    # Auto-subscribe: when Verify succeeds against a calendar that ISN'T in
    # the SA's calendarList, insert it so future discovery (calendarList.list)
    # actually returns it. Without this, calendars shared via "Share with
    # specific people" remain invisible to discovery even though they're
    # fully accessible via direct calendars.get(). Best-effort — the verify
    # itself succeeded, so we report success regardless of insert outcome.
    auto_subscribed = False
    try:
        # Cheap check: was this calendar already in the SA's calendarList?
        try:
            service.calendarList().get(calendarId=calendar_id).execute()
        except HttpError as e:
            # 404 here = not subscribed → insert it
            if getattr(getattr(e, "resp", None), "status", None) == 404:
                try:
                    service.calendarList().insert(body={"id": calendar_id}).execute()
                    auto_subscribed = True
                except HttpError as insert_err:
                    # If insert fails (e.g. policy block, quota), the verify
                    # is still valid — just no subscription. Don't surface
                    # as a failure to the user, but log so an operator
                    # debugging "why isn't this showing in the picker"
                    # can see what happened. CodeRabbit minor finding.
                    logger.debug(
                        "calendarList().insert failed for %s (auto-subscribe non-fatal): %s",
                        calendar_id, insert_err,
                    )
            else:
                logger.debug(
                    "calendarList().get returned non-404 error for %s (non-fatal): %s",
                    calendar_id, e,
                )
    except Exception as outer_err:
        logger.debug(
            "Auto-subscribe outer try failed for %s (non-fatal): %s",
            calendar_id, outer_err, exc_info=True,
        )

    return {
        "status": "ok",
        "calendar_summary": cal.get("summary", ""),
        "calendar_actual_timezone": actual_tz,
        "configured_timezone": configured_timezone,
        "drift_warning": drift_warning,
        "impersonating_subject": subject or None,
        "auto_subscribed": auto_subscribed,  # True iff we just added this to the SA's calendarList
    }


@router.post("/google-calendar/{key}/verify")
async def verify_google_calendar(key: str, override: Optional[_GoogleCalendarVerifyRequest] = None):
    """Verify that the configured (or about-to-be-configured) credentials can
    read the configured calendar. Returns structured success or 4xx with an
    error_code field the UI can display nicely.

    Override fields in the POST body win over persisted config so the UI can
    test unsaved form state without forcing a save first.
    """
    _validate_calendar_key_or_400(key)

    entry = _read_google_calendar_entry(key)

    # Effective config = persisted, with optional POST-body overrides on top.
    # Subject must initialize from the persisted entry too — without this,
    # API callers (or future UI paths) that omit subject in the POST body
    # would verify without impersonation and get a false success/failure.
    # Codex feedback #5.
    creds_path = (entry.get("credentials_path") or "").strip()
    calendar_id = (entry.get("calendar_id") or "").strip()
    configured_tz = (entry.get("timezone") or "").strip()
    persisted_subject = (entry.get("subject") or "").strip()
    subject: Optional[str] = persisted_subject or None
    if override is not None:
        if override.credentials_path is not None:
            creds_path = override.credentials_path.strip()
        if override.calendar_id is not None:
            calendar_id = override.calendar_id.strip()
        if override.timezone is not None:
            configured_tz = override.timezone.strip()
        if override.subject is not None:
            subject = override.subject.strip() or None

    if not creds_path:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "missing_credentials_path",
                "message": "No credentials_path configured or supplied for this calendar key.",
            },
        )
    if not calendar_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "missing_calendar_id",
                "message": "No calendar_id configured or supplied for this calendar key.",
            },
        )

    return await asyncio.to_thread(
        _verify_calendar_access_sync,
        creds_path,
        calendar_id,
        configured_tz,
        subject,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Google Calendar — JSON Upload + Auto-discover
# ─────────────────────────────────────────────────────────────────────────────
#
# Drag-drop upload flow. After file save, the endpoint authenticates as the
# uploaded SA and calls calendarList.list() to discover which calendars the
# SA has been shared with. The UI uses the discovery result to:
#   - Auto-fill calendar_id + timezone if exactly one calendar is accessible
#   - Show a picker if multiple calendars are accessible
#   - Show a "share your calendar with this email" hint if zero calendars
#
# This collapses what was a multi-step flow (SCP file, configure path,
# share calendar in Google UI, paste calendar ID, paste timezone, click
# Verify) into a single drag-drop that auto-fills the row and auto-verifies.

# Where uploaded SA files live. Bind-mounted between admin_ui (writer) and
# ai_engine (reader) by docker-compose.yml; this matches the existing path
# scheme used by VERTEX_CREDENTIALS_PATH.
GOOGLE_CALENDAR_SECRETS_DIR = "/app/project/secrets"

# Stable filename pattern keyed off the SA's client_email hash. This means:
# - Re-uploading the same SA (e.g. private key rotation) overwrites the same
#   file → existing UI/YAML credentials_path references stay valid.
# - Uploading a different SA writes to a different file → no silent collision.
# - Calendar UI key renames don't require renaming the file (keys are UI
#   labels, files are content-addressed). Per Codex feedback #3.
_CALENDAR_UPLOAD_FILENAME_RE = re.compile(r"^google-calendar-[a-f0-9]{12}\.json$")


def _calendar_filename_for_email(client_email: str) -> str:
    """Compute the stable filename for an uploaded SA file.

    sha256(client_email)[:12] is enough entropy to avoid accidental collisions
    while keeping the filename short and human-recognizable in `ls`.
    """
    import hashlib
    digest = hashlib.sha256(client_email.encode("utf-8")).hexdigest()[:12]
    return f"google-calendar-{digest}.json"


def _resolve_calendar_secret_path(filename: str) -> str:
    """Resolve `filename` to an absolute path under GOOGLE_CALENDAR_SECRETS_DIR.

    Refuses anything outside the secrets dir (path traversal protection) or
    that doesn't match the stable-hash filename pattern. Returns the
    canonical absolute path. Per Codex feedback #2.
    """
    if not _CALENDAR_UPLOAD_FILENAME_RE.fullmatch(filename or ""):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_filename",
                "message": "Filename must match the stable-hash pattern google-calendar-XXXXXXXXXXXX.json.",
            },
        )
    candidate = os.path.realpath(os.path.join(GOOGLE_CALENDAR_SECRETS_DIR, filename))
    secrets_dir = os.path.realpath(GOOGLE_CALENDAR_SECRETS_DIR)
    # Final defense: must be a direct child of the secrets dir
    if os.path.dirname(candidate) != secrets_dir:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "path_outside_secrets_dir",
                "message": "Resolved path escapes the secrets directory.",
            },
        )
    return candidate


def _discover_accessible_calendars(creds_path: str) -> dict:
    """Authenticate as the SA and list all calendars it can access.

    Returns a dict with two keys:
      "ok": True/False
      "calendars": [{ id, summary, timezone, access_role }, ...]  (only on success)
      "error_code"/"error_message" on failure

    Failures here are non-fatal for the upload itself — the file IS saved
    even if discovery fails. The UI shows a yellow "Re-check" button.
    """
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        return {
            "ok": False,
            "error_code": "google_libs_not_installed",
            "error_message": "google-auth + googleapiclient are required to discover calendars.",
        }

    # Constrain creds_path to known secrets dirs (defense-in-depth — same
    # rationale as in _load_sa_metadata; keeps CodeQL happy and prevents
    # this endpoint from being a vector for reading arbitrary host files).
    try:
        norm_creds = os.path.realpath(creds_path)
    except Exception:
        return {"ok": False, "error_code": "invalid_credentials_path",
                "error_message": "credentials_path could not be resolved."}
    try:
        _assert_creds_path_in_allowed_dir(norm_creds, creds_path)
    except HTTPException as e:
        return {"ok": False, "error_code": e.detail.get("error_code", "credentials_path_disallowed"),
                "error_message": e.detail.get("message", str(e))}

    try:
        creds = service_account.Credentials.from_service_account_file(
            norm_creds,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
    except Exception as e:
        return {"ok": False, "error_code": "invalid_credentials", "error_message": str(e)}

    try:
        creds.refresh(Request())
    except Exception as e:
        return {"ok": False, "error_code": "auth_failed", "error_message": str(e)}

    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        result = service.calendarList().list(maxResults=250).execute()
    except HttpError as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        return {
            "ok": False,
            "error_code": f"calendar_list_http_{status or 'unknown'}",
            "error_message": str(e),
        }
    except Exception as e:
        return {"ok": False, "error_code": "unknown", "error_message": str(e)}

    calendars = []
    for c in result.get("items", []) or []:
        calendars.append({
            "id": c.get("id", ""),
            "summary": c.get("summary", ""),
            "timezone": c.get("timeZone", ""),
            "access_role": c.get("accessRole", ""),
        })
    # Surface higher-permission calendars first so the UI's auto-pick prefers
    # the most useful one when there are multiples
    role_priority = {"owner": 0, "writer": 1, "reader": 2, "freeBusyReader": 3}
    calendars.sort(key=lambda c: (role_priority.get(c["access_role"], 99), c["summary"].lower()))
    return {"ok": True, "calendars": calendars}


@router.post("/google-calendar/credentials")
async def upload_google_calendar_credentials(file: UploadFile = File(...)):
    """Upload a Google service-account JSON file and discover accessible calendars.

    Single round-trip:
      1. Validate the upload (size, JSON shape, SA shape)
      2. Compute stable-hash filename (so re-uploading same SA reuses path)
      3. Write atomically to secrets/<filename> with broad-read perms so the
         ai_engine container's appuser can read the file at runtime
      4. Authenticate as the SA and call calendarList.list() to discover
         which calendars the SA has been shared with
      5. Return identity + container path + accessible calendar list

    The UI uses the response to:
      - Auto-fill calendar_id + timezone if exactly 1 calendar is accessible
      - Show a picker if >1 calendars are accessible
      - Tell the operator to share their calendar with the SA email if 0
    """
    import json

    # Hard cap on file size — SA JSONs are ~2KB; anything close to 100KB is
    # almost certainly malicious or wrong. Read into memory directly because
    # we need to validate JSON shape before touching disk.
    MAX_BYTES = 100 * 1024
    raw = await file.read()
    if not raw:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "empty_file", "message": "Uploaded file is empty."},
        )
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "file_too_large",
                "message": f"Service account JSON files are typically 2-3 KB; rejected file is {len(raw)} bytes (cap {MAX_BYTES}).",
            },
        )

    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=400,
            detail={"error_code": "credentials_not_json", "message": "Uploaded file is not valid JSON."},
        )

    if not isinstance(data, dict) or data.get("type") != "service_account":
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "credentials_not_service_account",
                "message": "Uploaded JSON must be a Google service-account key (type: service_account).",
            },
        )

    required = ("client_email", "private_key", "private_key_id")
    if not all(data.get(k) for k in required):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "credentials_missing_fields",
                "message": f"Service-account JSON is missing required fields: {required}.",
            },
        )

    client_email = data["client_email"]
    filename = _calendar_filename_for_email(client_email)
    target_path = _resolve_calendar_secret_path(filename)
    container_path = f"{GOOGLE_CALENDAR_SECRETS_DIR}/{filename}"

    # Detect overwrite for the UI to surface "replaced existing credential"
    # — Codex feedback #3 about key rotation visibility
    was_replaced = os.path.exists(target_path)
    previous_key_id: Optional[str] = None
    if was_replaced:
        try:
            with open(target_path, "r") as f:
                prev_data = json.load(f)
            previous_key_id = prev_data.get("private_key_id")
        except Exception:
            previous_key_id = None

    # Atomic write: tmp + replace, so a crash mid-write doesn't leave an
    # invalid file at the target path. Use a unique tmp filename per
    # request (PID + monotonic counter via uuid) so two concurrent uploads
    # of the same SA file don't race on os.replace — each request writes
    # its own tmp and they replace serially. Without this, a second upload
    # could see the first's half-written tmp and either overwrite mid-
    # write or replace away from a corrupted source.
    import uuid
    os.makedirs(GOOGLE_CALENDAR_SECRETS_DIR, exist_ok=True)
    tmp_path = f"{target_path}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    try:
        with open(tmp_path, "wb") as f:
            f.write(raw)
        # 0o640 is the minimum permission set required by AAVA's split-
        # container architecture: admin_ui (writer, runs as root) owns the
        # file; ai_engine (reader, runs as `appuser` in the `asterisk`
        # group) reads via group permissions; world has no access. Going
        # tighter (0o600 / owner-only) would block ai_engine from reading
        # the SA credential and break the calendar tool entirely.
        # CodeQL still flags 0o640 as "group-readable", which is true but
        # a deliberate cross-container boundary, not a security weakness —
        # the bind-mount is scoped to two specific containers and group
        # membership is set at image-build time, not at runtime. The
        # `nosec` annotation below documents this intent so the warning
        # doesn't keep recurring on every rescan.
        os.chmod(tmp_path, 0o640)  # nosec B103 - cross-container read; see comment above
        os.replace(tmp_path, target_path)
    except OSError as e:
        # Clean up the .tmp on failure
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "write_failed",
                "message": f"Failed to write credentials file: {e}",
            },
        )

    # Now discover which calendars the SA can actually access. This is done
    # in a thread because both creds.refresh() and the API call are blocking.
    # Failures here are NON-fatal for the upload — the file is on disk
    # successfully; the UI just falls back to the manual-fill flow.
    discovery = await asyncio.to_thread(_discover_accessible_calendars, target_path)

    return {
        "status": "success",
        "filename": filename,
        "container_path": container_path,
        "replaced": was_replaced,
        "previous_key_id": previous_key_id,
        "identity": {
            "client_email": client_email,
            "client_id": data.get("client_id", ""),
            "project_id": data.get("project_id", ""),
            "private_key_id": data.get("private_key_id", ""),
        },
        "discovery": discovery,
    }


@router.delete("/google-calendar/credentials/{filename}")
async def delete_google_calendar_credentials(filename: str):
    """Remove an uploaded SA credentials file.

    Only filenames matching the stable-hash pattern are accepted; any other
    shape is rejected with 400 (path traversal protection). Returns 404 if
    the file doesn't exist (idempotent-ish — repeated DELETEs after the
    first are safe to retry but the second one tells the operator the file
    was already gone).

    Refuses to delete if any other calendar entry still references the
    same credentials file. Filenames are content-addressed by client_email,
    so reusing one SA across multiple calendar keys is intentional and
    common (e.g., one SA managing multiple calendars in the same domain).
    Without this check, deleting via one key would silently break every
    OTHER key that pointed at the same file.
    """
    # Resolve the requested filename inside the secrets dir. The helper
    # validates the filename pattern + path-traversal protection, but
    # CodeQL doesn't trace value-flow through helpers, so we re-state the
    # constraint inline below using Path.relative_to() — the canonical
    # CodeQL-recognized sanitizer. (Same approach as _load_sa_metadata.)
    target_path = _resolve_calendar_secret_path(filename)
    from pathlib import Path
    _secrets_dir = Path(GOOGLE_CALENDAR_SECRETS_DIR).resolve()
    _candidate = Path(target_path).resolve()
    try:
        _candidate.relative_to(_secrets_dir)
    except ValueError:
        # Defense-in-depth — should be unreachable given
        # _resolve_calendar_secret_path's filename regex + dirname check,
        # but keeps the constraint visible to static analysis.
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "path_outside_secrets_dir",
                "message": "Resolved path escapes the secrets directory.",
            },
        )
    # Reconstruct the safe path explicitly under the secrets dir so the
    # sanitization is visible to data-flow analysis rather than relying
    # on the unmodified input.
    safe_target_path = _secrets_dir / _candidate.relative_to(_secrets_dir)
    if not safe_target_path.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "credentials_file_not_found",
                "message": f"No credentials file at {filename}.",
            },
        )

    # Check for other calendar entries still pointing at this file
    container_path = f"{GOOGLE_CALENDAR_SECRETS_DIR}/{filename}"
    try:
        merged = _read_merged_config_dict()
    except Exception:
        merged = {}
    referenced_by: list[str] = []
    cals = ((merged.get("tools") or {}).get("google_calendar") or {}).get("calendars") or {}
    if isinstance(cals, dict):
        for k, v in cals.items():
            if not isinstance(v, dict):
                continue
            entry_path = (v.get("credentials_path") or "").strip()
            if not entry_path:
                continue
            try:
                entry_real = os.path.realpath(entry_path)
            except Exception:
                entry_real = entry_path
            if entry_real == str(safe_target_path) or entry_path == container_path:
                referenced_by.append(str(k))
    if referenced_by:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "credentials_file_in_use",
                "message": (
                    f"Cannot delete '{filename}': still referenced by calendar "
                    f"key(s) {sorted(referenced_by)}. Remove or reassign those "
                    f"calendar entries first, then retry the delete."
                ),
                "referenced_by": sorted(referenced_by),
            },
        )
    try:
        # safe_target_path is provably under _secrets_dir via the
        # Path.relative_to() guard above. Using the Path object's
        # unlink() method here keeps the data-flow chain explicit
        # for static analysis.
        safe_target_path.unlink()
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "delete_failed",
                "message": f"Failed to remove credentials file: {e}",
            },
        ) from e
    return {"status": "success", "filename": filename}


# ─────────────────────────────────────────────────────────────────────────────
# Microsoft Calendar — Device-Code OAuth + Verify
# ─────────────────────────────────────────────────────────────────────────────

MICROSOFT_CALENDAR_SECRETS_DIR = "/app/project/secrets"
# V1 is single-account by design: only `accounts.default` is supported.
# The token-cache file path is therefore a literal constant — never derived
# from user input. When multi-account ships (V2), this constraint can be
# lifted and the path will be derived from a per-account allowlist. Keeping
# the path as a literal is what lets CodeQL's `py/path-injection` query
# treat the value as untainted; it's also what the deployed product
# contract has been all along (UI hardcodes `account_key="default"`,
# docs document one-account V1).
MICROSOFT_CALENDAR_TOKEN_CACHE_PATH = (
    f"{MICROSOFT_CALENDAR_SECRETS_DIR}/microsoft-calendar-default-token-cache.json"
)
_MS_TENANT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_MS_CLIENT_ID_PATTERN = re.compile(r"^[0-9a-fA-F-]{32,64}$")
_MS_DEVICE_FLOWS: dict[str, dict] = {}


def _ms_flow_lock():
    import threading
    if not hasattr(_ms_flow_lock, "_lock"):
        _ms_flow_lock._lock = threading.Lock()  # type: ignore[attr-defined]
    return _ms_flow_lock._lock  # type: ignore[attr-defined]


def _validate_ms_account_key_or_400(key: str) -> str:
    """V1 supports exactly one Microsoft Calendar account: `default`.

    Rejecting all other keys with a literal-string equality check (rather
    than a regex pattern) is the simplest contract that matches what the
    UI already enforces and what the docs document, and it lets CodeQL's
    `py/path-injection` query treat any downstream path that depends on
    the validated key as untainted (a literal-string equality check is a
    recognized sanitizer; `re.fullmatch` against a character class is
    not). When multi-account ships in V2, this validator can grow to
    accept a finite allowlist of keys; the path resolver should then
    derive from a per-key allowlist rather than a free-form string.
    """
    candidate = (key if isinstance(key, str) else "").strip()
    if candidate != "default":
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "unsupported_account_key",
                "message": (
                    "Microsoft Calendar V1 supports only account_key='default'. "
                    "Multi-account support is planned for a future release."
                ),
            },
        )
    return "default"


def _validate_ms_tenant_client_or_400(tenant_id: str, client_id: str) -> tuple[str, str]:
    tenant = (tenant_id or "").strip()
    client = (client_id or "").strip()
    if not tenant or not _MS_TENANT_PATTERN.fullmatch(tenant):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_tenant_id",
                "message": "tenant_id must be an explicit Entra tenant id or tenant domain. Do not use 'common' in V1.",
            },
        )
    if tenant.lower() == "common":
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "common_tenant_not_supported",
                "message": "Microsoft Calendar V1 requires an explicit tenant_id, not /common.",
            },
        )
    if not client or not _MS_CLIENT_ID_PATTERN.fullmatch(client):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_client_id",
                "message": "client_id must be the Application (client) ID from Azure App registrations.",
            },
        )
    return tenant, client


def _ms_token_cache_path_for_key(account_key: str) -> str:
    # V1 single-account: validate the key (only "default" is accepted) and
    # return the literal constant path. No user-controlled bytes flow into
    # the result.
    _validate_ms_account_key_or_400(account_key)
    return MICROSOFT_CALENDAR_TOKEN_CACHE_PATH


class _MicrosoftDeviceStartRequest(BaseModel):
    tenant_id: str
    client_id: str
    account_key: str = "default"


class _MicrosoftVerifyRequest(BaseModel):
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    token_cache_path: Optional[str] = None
    user_principal_name: Optional[str] = None
    calendar_id: Optional[str] = None
    timezone: Optional[str] = None
    account_key: str = "default"


class _MicrosoftDisconnectRequest(BaseModel):
    token_cache_path: Optional[str] = None
    account_key: str = "default"


def _ms_graph_request_with_token(token: str, method: str, path: str, body: Optional[dict] = None) -> dict:
    import json
    import urllib.error
    import urllib.request

    # Accept either a relative path (e.g. "/me") or an absolute Graph URL
    # (e.g. an "@odata.nextLink" returned from a paginated response). Same
    # shape as `MicrosoftGraphClient._request` in the runtime client.
    if path.startswith("https://"):
        url = path
    else:
        url = f"https://graph.microsoft.com/v1.0{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": 'outlook.timezone="UTC"',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        raise HTTPException(
            status_code=e.code,
            detail={
                "error_code": "microsoft_graph_error",
                "message": ((payload.get("error") or {}).get("message") or f"Microsoft Graph HTTP {e.code}"),
                "graph": payload,
            },
        )


def _persist_ms_token_cache(cache, account_key: str) -> None:
    # Derive the cache path from the validated account_key (regex-allowlisted
    # in `_ms_token_cache_path_for_key`). The user-supplied token_cache_path
    # never reaches `os.path.*` operations — see Codex review feedback on PR
    # #357 for context (CodeQL py/path-injection couldn't track the previous
    # sanitizer through a function return).
    safe_path = _ms_token_cache_path_for_key(account_key)
    import portalocker  # type: ignore

    os.makedirs(os.path.dirname(safe_path), exist_ok=True)
    lock_path = f"{safe_path}.lock"
    with portalocker.Lock(lock_path, timeout=10):
        tmp_path = f"{safe_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(cache.serialize())
        # Owner-only (0o600). admin_ui and ai_engine both run as uid 1000 in
        # the production containers, so they share owner-rw access; no group
        # bit is required for cross-container token-cache coordination.
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, safe_path)
        try:
            os.chmod(safe_path, 0o600)
        except OSError:
            pass


def _ms_device_flow_worker(flow_id: str, tenant_id: str, client_id: str, account_key: str) -> None:
    import msal  # type: ignore

    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )
    with _ms_flow_lock():
        flow = dict(_MS_DEVICE_FLOWS.get(flow_id, {}).get("flow") or {})
    try:
        result = app.acquire_token_by_device_flow(flow)
        if not result or "access_token" not in result:
            error = (result or {}).get("error") if isinstance(result, dict) else None
            description = (result or {}).get("error_description") if isinstance(result, dict) else None
            with _ms_flow_lock():
                _MS_DEVICE_FLOWS[flow_id]["status"] = "error"
                _MS_DEVICE_FLOWS[flow_id]["error"] = {
                    "error_code": "authorization_failed",
                    "message": description or error or "Microsoft device-code authorization failed.",
                }
            return
        _persist_ms_token_cache(cache, account_key)
        access_token = result["access_token"]
        me = _ms_graph_request_with_token(access_token, "GET", "/me")
        # Paginate /me/calendars. Without this, accounts with many calendars
        # would see a truncated picker on Connect (caught by CodeRabbit on
        # PR #357). Mirrors `MicrosoftGraphClient.list_calendars()`'s shape.
        calendars: list = []
        next_url: Optional[str] = "/me/calendars"
        while next_url:
            page = _ms_graph_request_with_token(access_token, "GET", next_url)
            calendars.extend(page.get("value") or [])
            next_url = page.get("@odata.nextLink")
        username = (
            (result.get("id_token_claims") or {}).get("preferred_username")
            or me.get("userPrincipalName")
            or me.get("mail")
            or ""
        )
        with _ms_flow_lock():
            _MS_DEVICE_FLOWS[flow_id]["status"] = "success"
            _MS_DEVICE_FLOWS[flow_id]["result"] = {
                "token_cache_path": token_cache_path,
                "user_principal_name": username,
                "display_name": me.get("displayName") or "",
                "mail": me.get("mail") or "",
                "calendars": [
                    {
                        "id": cal.get("id"),
                        "name": cal.get("name"),
                        "is_default_calendar": cal.get("isDefaultCalendar"),
                    }
                    for cal in calendars
                ],
            }
    except Exception as exc:
        logger.error("Microsoft device-code flow failed", exc_info=True)
        with _ms_flow_lock():
            _MS_DEVICE_FLOWS[flow_id]["status"] = "error"
            _MS_DEVICE_FLOWS[flow_id]["error"] = {
                "error_code": "authorization_failed",
                "message": str(exc),
            }


@router.post("/microsoft-calendar/device/start")
async def start_microsoft_calendar_device_flow(req: _MicrosoftDeviceStartRequest):
    import threading
    import time
    import uuid

    try:
        import msal  # type: ignore
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "msal_not_installed",
                "message": "msal is required for Microsoft Calendar device-code OAuth.",
            },
        )

    tenant_id, client_id = _validate_ms_tenant_client_or_400(req.tenant_id, req.client_id)
    account_key = _validate_ms_account_key_or_400(req.account_key or "default")
    token_cache_path = _ms_token_cache_path_for_key(account_key)

    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )
    # MSAL Python only treats `openid`, `profile`, and `offline_access` as
    # reserved — it auto-adds those itself and raises ValueError if the
    # caller passes them. `User.Read` is a regular Graph delegated
    # permission and MUST be requested explicitly; otherwise the issued
    # access token cannot call `/me` (Authorization_RequestDenied 403),
    # which we use post-auth to confirm the signed-in identity.
    flow = app.initiate_device_flow(scopes=["User.Read", "Calendars.ReadWrite"])
    if not flow or "user_code" not in flow:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "device_flow_start_failed",
                "message": flow.get("error_description") if isinstance(flow, dict) else "Could not start Microsoft device-code flow.",
            },
        )

    flow_id = uuid.uuid4().hex
    now = int(time.time())
    with _ms_flow_lock():
        _MS_DEVICE_FLOWS[flow_id] = {
            "status": "pending",
            "flow": flow,
            "created_at": now,
            "expires_at": now + int(flow.get("expires_in") or 900),
            "tenant_id": tenant_id,
            "client_id": client_id,
            "account_key": account_key,
            "token_cache_path": token_cache_path,
        }
    worker = threading.Thread(
        target=_ms_device_flow_worker,
        args=(flow_id, tenant_id, client_id, account_key),
        daemon=True,
    )
    worker.start()
    return {
        "status": "pending",
        "flow_id": flow_id,
        "user_code": flow.get("user_code"),
        "verification_uri": flow.get("verification_uri") or flow.get("verification_url"),
        "message": flow.get("message"),
        "expires_in": flow.get("expires_in"),
        "interval": flow.get("interval", 5),
    }


@router.get("/microsoft-calendar/device/status/{flow_id}")
async def get_microsoft_calendar_device_flow_status(flow_id: str):
    import time

    if not re.fullmatch(r"^[a-f0-9]{32}$", flow_id or ""):
        raise HTTPException(
            status_code=400,
            detail={"error_code": "invalid_flow_id", "message": "Invalid device flow id."},
        )
    with _ms_flow_lock():
        item = dict(_MS_DEVICE_FLOWS.get(flow_id) or {})
    if not item:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "flow_not_found", "message": "Microsoft device flow not found or expired."},
        )
    if item.get("status") == "pending" and item.get("expires_at") and int(time.time()) > int(item["expires_at"]):
        with _ms_flow_lock():
            if flow_id in _MS_DEVICE_FLOWS:
                _MS_DEVICE_FLOWS[flow_id]["status"] = "expired"
        item["status"] = "expired"
    response = {
        "status": item.get("status"),
        "expires_at": item.get("expires_at"),
        "account_key": item.get("account_key"),
    }
    if item.get("result"):
        response["result"] = item["result"]
    if item.get("error"):
        response["error"] = item["error"]
    return response


def _read_microsoft_calendar_account(account_key: str) -> dict:
    try:
        merged = _read_merged_config_dict()
    except Exception:
        return {}
    ms_cfg = (merged.get("tools") or {}).get("microsoft_calendar") or {}
    accounts = ms_cfg.get("accounts") or {}
    if isinstance(accounts, dict) and account_key in accounts and isinstance(accounts[account_key], dict):
        return accounts[account_key]
    return ms_cfg if isinstance(ms_cfg, dict) else {}


@router.post("/microsoft-calendar/verify")
async def verify_microsoft_calendar(req: _MicrosoftVerifyRequest):
    project_root = getattr(settings, "PROJECT_ROOT", None)
    if project_root and project_root not in sys.path:
        sys.path.insert(0, project_root)
    from src.tools.business.ms_graph_client import MicrosoftAccountConfig, MicrosoftGraphApiError, MicrosoftGraphClient

    account_key = _validate_ms_account_key_or_400(req.account_key or "default")
    persisted = _read_microsoft_calendar_account(account_key)
    tenant_id = (req.tenant_id if req.tenant_id is not None else persisted.get("tenant_id", "") or "").strip()
    client_id = (req.client_id if req.client_id is not None else persisted.get("client_id", "") or "").strip()
    token_cache_path = (req.token_cache_path if req.token_cache_path is not None else persisted.get("token_cache_path", "") or "").strip()
    user_principal_name = (
        req.user_principal_name if req.user_principal_name is not None else persisted.get("user_principal_name", "") or ""
    ).strip()
    calendar_id = (req.calendar_id if req.calendar_id is not None else persisted.get("calendar_id", "") or "").strip()
    timezone = (req.timezone if req.timezone is not None else persisted.get("timezone", "") or "UTC").strip() or "UTC"

    _validate_ms_tenant_client_or_400(tenant_id, client_id)
    # Derive the path from the validated account_key. Any user-supplied
    # token_cache_path is accepted only as a basename consistency check —
    # it never reaches `os.path.*` operations. (Codex review feedback on
    # PR #357: CodeQL py/path-injection couldn't track the previous
    # sanitizer through a function return; deriving from account_key
    # eliminates the user-controlled path data entirely.)
    safe_token_path = _ms_token_cache_path_for_key(account_key)
    if token_cache_path:
        posted_basename = os.path.basename(token_cache_path)
        if posted_basename and posted_basename != os.path.basename(safe_token_path):
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "token_cache_path_mismatch",
                    "message": (
                        "Supplied token_cache_path does not match the canonical path "
                        "derived from account_key. Omit token_cache_path or align it "
                        "to the canonical filename."
                    ),
                },
            )
    # `safe_token_path` is `MICROSOFT_CALENDAR_TOKEN_CACHE_PATH`, a literal
    # constant — no user input can affect it (V1 is single-account; the
    # validator above only accepts `account_key="default"`).
    if not os.path.exists(safe_token_path):
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "auth_expired",
                "message": "Microsoft Calendar is not connected yet. Use Connect to authorize this account.",
            },
        )
    missing = [
        name for name, value in {
            "user_principal_name": user_principal_name,
            "calendar_id": calendar_id,
        }.items() if not value
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "missing_fields",
                "message": f"Missing required Microsoft Calendar fields: {', '.join(missing)}.",
            },
        )

    account = MicrosoftAccountConfig(
        tenant_id=tenant_id,
        client_id=client_id,
        token_cache_path=safe_token_path,
        user_principal_name=user_principal_name,
        calendar_id=calendar_id,
        timezone=timezone,
    )
    client = MicrosoftGraphClient(account)
    try:
        me = await asyncio.to_thread(client.me)
        calendars = await asyncio.to_thread(client.list_calendars)
    except MicrosoftGraphApiError as exc:
        raise HTTPException(
            status_code=exc.status or 400,
            detail={
                "error_code": exc.error_code,
                "message": str(exc),
            },
        )
    matched = next((cal for cal in calendars if cal.get("id") == calendar_id), None)
    if not matched:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "calendar_not_found",
                "message": "The connected Microsoft account cannot see the configured calendar_id.",
            },
        )
    return {
        "status": "ok",
        "user_principal_name": me.get("userPrincipalName") or user_principal_name,
        "display_name": me.get("displayName") or "",
        "calendar_id": calendar_id,
        "calendar_name": matched.get("name") or "",
        "configured_timezone": timezone,
    }


@router.post("/microsoft-calendar/disconnect")
async def disconnect_microsoft_calendar(req: _MicrosoftDisconnectRequest):
    account_key = _validate_ms_account_key_or_400(req.account_key or "default")
    # Derive the path from the validated account_key (regex-allowlisted).
    # Any user-supplied token_cache_path is accepted only as a basename
    # consistency check — it never reaches `os.path.*` operations. (See
    # Codex review feedback on PR #357.)
    safe_path = _ms_token_cache_path_for_key(account_key)
    posted = (req.token_cache_path or "").strip()
    if posted:
        posted_basename = os.path.basename(posted)
        if posted_basename and posted_basename != os.path.basename(safe_path):
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "token_cache_path_mismatch",
                    "message": (
                        "Supplied token_cache_path does not match the canonical path "
                        "derived from account_key. Omit token_cache_path or align it "
                        "to the canonical filename."
                    ),
                },
            )
    removed = False
    # `safe_path` is `MICROSOFT_CALENDAR_TOKEN_CACHE_PATH`, a literal
    # constant — no user input can affect it (V1 is single-account; the
    # validator above only accepts `account_key="default"`). The `.lock`
    # sibling shares the same trusted prefix.
    for path in (safe_path, f"{safe_path}.lock"):
        try:
            if os.path.exists(path):
                os.remove(path)
                removed = True
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "error_code": "disconnect_failed",
                    "message": f"Failed to remove Microsoft token cache: {exc}",
                },
            ) from exc
    return {"status": "success", "removed": removed, "token_cache_path": safe_path}
