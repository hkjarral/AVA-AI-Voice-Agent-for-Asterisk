import docker
import logging
import copy
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx
import os
import yaml
import subprocess
import stat
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from pathlib import PurePosixPath
import hashlib
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile

logger = logging.getLogger(__name__)
from settings import ENV_PATH, CONFIG_PATH, LOCAL_CONFIG_PATH, ensure_env_file, PROJECT_ROOT
from services.fs import upsert_env_vars, atomic_write_text
from api.models_catalog import (
    get_full_catalog, get_models_by_language, get_available_languages,
    LANGUAGE_NAMES, REGION_NAMES, VOSK_STT_MODELS, SHERPA_STT_MODELS,
    KROKO_STT_MODELS, PIPER_TTS_MODELS, KOKORO_TTS_MODELS, LLM_MODELS
)

router = APIRouter()

DISK_WARNING_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB
DISK_BLOCK_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB (hard stop for downloads)
GOOGLE_LIVE_DEFAULT_MODEL = "gemini-2.5-flash-native-audio-latest"
GOOGLE_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GOOGLE_LIVE_PREFERRED_MODELS = [
    GOOGLE_LIVE_DEFAULT_MODEL,
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-2.5-flash-native-audio-preview-09-2025",
    "gemini-live-2.5-flash-native-audio",
    "gemini-live-2.5-flash-preview-native-audio-09-2025",
    "gemini-live-2.5-flash-preview",
]


def _extract_google_live_models(models: List[Dict[str, Any]]) -> List[str]:
    """Extract model names that support bidiGenerateContent (Gemini Live)."""
    live_models: List[str] = []
    for model in models:
        methods = model.get("supportedGenerationMethods", [])
        if "bidiGenerateContent" in methods:
            model_name = model.get("name", "").replace("models/", "")
            if model_name:
                live_models.append(model_name)
    return live_models


def _select_google_live_model(live_models: List[str]) -> Optional[str]:
    """Pick the best available Google Live model using preferred order."""
    for preferred_model in GOOGLE_LIVE_PREFERRED_MODELS:
        if preferred_model in live_models:
            return preferred_model
    if live_models:
        return live_models[0]
    return None


async def _discover_google_live_model(api_key: str) -> Optional[str]:
    """Discover an available Google Live model for the provided API key."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                GOOGLE_MODELS_URL,
                params={"key": api_key},
                timeout=10.0,
            )
            if response.status_code != 200:
                return None
            data = response.json()
            models = data.get("models", [])
            live_models = _extract_google_live_models(models)
            return _select_google_live_model(live_models)
    except Exception:
        return None


def _parse_optional_bool(raw: Optional[str]) -> Optional[bool]:
    """Parse common bool env values; return None when unset/unknown."""
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _detect_gpu_from_env_or_runtime() -> bool:
    """
    Detect GPU availability for admin recommendations and startup behavior.
    Prefers GPU_AVAILABLE from .env (injected as container env), falls back to nvidia-smi.
    """
    gpu_env = _parse_optional_bool(os.environ.get("GPU_AVAILABLE"))
    if gpu_env is not None:
        return gpu_env

    try:
        nvidia_smi = shutil.which("nvidia-smi")
        if not nvidia_smi:
            return False
        result = subprocess.run([nvidia_smi], capture_output=True, timeout=2)
        return result.returncode == 0
    except Exception:
        return False


def _gpu_override_enabled_from_preflight() -> bool:
    """
    Enable GPU compose override only when preflight has explicitly set GPU_AVAILABLE.
    This avoids breaking CPU fallback on hosts where nvidia-smi exists but Docker GPU
    passthrough is not configured yet.
    """
    gpu_env = _parse_optional_bool(os.environ.get("GPU_AVAILABLE"))
    return gpu_env is True


def _format_bytes(num_bytes: int) -> str:
    """Format bytes into a human-readable string."""
    if num_bytes < 0:
        return "unknown"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    unit = 0
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024.0
        unit += 1
    if unit <= 1:
        return f"{int(size)} {units[unit]}"
    return f"{size:.1f} {units[unit]}"


def _disk_preflight(path: str, *, required_bytes: int = 0) -> Tuple[bool, Optional[str]]:
    """
    Returns (ok, warning_or_error_message).
    - Warns when free space < DISK_WARNING_BYTES.
    - Blocks when free space < max(DISK_BLOCK_BYTES, required_bytes).
    """
    try:
        total, used, free = shutil.disk_usage(path)
    except Exception:
        return True, None

    block_at = max(DISK_BLOCK_BYTES, int(required_bytes or 0))
    if free < block_at:
        msg = (
            f"Insufficient disk space: free={_format_bytes(free)} required={_format_bytes(block_at)} "
            f"(path={path})."
        )
        return False, msg

    if free < DISK_WARNING_BYTES:
        return True, f"Low disk space: only {_format_bytes(free)} free (path={path})."
    return True, None


def _compute_local_override_fallback(base: Any, merged: Any) -> Any:
    """
    Compute a minimal override tree using simple deep-diff semantics.

    `None` in the merged tree acts as a tombstone and is preserved as-is.
    """
    if isinstance(base, dict) and isinstance(merged, dict):
        out: Dict[str, Any] = {}

        for key, merged_val in merged.items():
            if key not in base:
                out[key] = merged_val
                continue
            child = _compute_local_override_fallback(base[key], merged_val)
            if child is not _NO_OVERRIDE:
                out[key] = child

        for key in base.keys() - merged.keys():
            out[key] = None

        return out

    if base == merged:
        return _NO_OVERRIDE
    return merged


_NO_OVERRIDE = object()


def _detect_host_project_path_via_docker() -> Optional[str]:
    """
    When the Admin UI calls docker-compose from inside the container, the daemon (on the host)
    resolves bind mount paths using the host filesystem.

    This helper finds the host-side path backing /app/project.
    """
    try:
        client = docker.from_env()
        container = client.containers.get("admin_ui")
        mounts = container.attrs.get("Mounts", []) or []
        for m in mounts:
            if m.get("Destination") == "/app/project":
                src = m.get("Source")
                if src:
                    return str(src)
    except Exception:
        return None
    return None


def _attempt_fix_models_permissions() -> Dict[str, Any]:
    """
    Best-effort remediation for common fresh-install issue:
    - ./models (host) exists but is owned by root (or non-writable),
      so admin_ui (UID 1000) can't create models/{stt,tts,llm,kroko}.
    """
    results: Dict[str, Any] = {"success": False, "messages": [], "errors": []}

    host_project_path = _detect_host_project_path_via_docker()
    if not host_project_path:
        results["errors"].append("Could not detect host project path for /app/project mount")
        return results

    # Best-effort: align group ownership with the repo's install/preflight behavior.
    # If ASTERISK_GID is set in .env, prefer it; otherwise, fall back to appuser's default GID (1000).
    models_gid = "1000"
    try:
        from dotenv import dotenv_values

        if os.path.exists(ENV_PATH):
            env_values = dotenv_values(ENV_PATH)
            gid = (env_values.get("ASTERISK_GID") or "").strip()
            if gid.isdigit():
                models_gid = gid
    except Exception:
        pass

    try:
        client = docker.from_env()

        script = f"""
set -eu
mkdir -p /project/models/stt /project/models/tts /project/models/llm /project/models/kroko
chown -R 1000:{models_gid} /project/models || true
chmod -R ug+rwX /project/models || true
find /project/models -type d -exec chmod 2775 {{}} + 2>/dev/null || true
echo "models permissions fixed"
"""
        output = client.containers.run(
            "alpine:latest",
            command=["sh", "-c", script],
            volumes={host_project_path: {"bind": "/project", "mode": "rw"}},
            remove=True,
        )
        msg = (output.decode().strip() if output else "").strip()
        if msg:
            results["messages"].append(msg)
        results["success"] = True
        return results
    except Exception as e:
        results["errors"].append(f"models permission fix failed: {e}")
        return results


def _ensure_models_dir_ready(path: str) -> None:
    """Ensure a models directory exists and is writable (best-effort auto-remediation)."""
    def _is_writable_dir(dir_path: str) -> bool:
        if not os.path.isdir(dir_path):
            return False
        try:
            fd, tmp = tempfile.mkstemp(prefix=".model_write_test_", dir=dir_path)
            os.close(fd)
            os.remove(tmp)
            return True
        except Exception:
            return False

    if os.path.exists(path) and not os.path.isdir(path):
        raise HTTPException(
            status_code=409,
            detail=f"Models path exists but is not a directory: {path}. Remove or rename it and retry.",
        )

    if os.path.isdir(path) and _is_writable_dir(path):
        return

    try:
        os.makedirs(path, exist_ok=True)
    except PermissionError:
        pass

    if _is_writable_dir(path):
        return

    fix = _attempt_fix_models_permissions()
    if not fix.get("success"):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Permission denied writing models directory ({path}). "
                f"Run: sudo ./preflight.sh --apply-fixes"
            ),
        )
    if not _is_writable_dir(path):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Permission denied writing models directory ({path}) after attempted auto-fix. "
                f"Run: sudo ./preflight.sh --apply-fixes"
            ),
        )


def _url_content_length(url: str) -> Optional[int]:
    """Return Content-Length for `url` (best-effort), or None when unavailable."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            val = resp.headers.get("Content-Length")
            if val:
                return int(val)
    except Exception:
        return None
    return None


def _sha256_file(path: str) -> str:
    """Compute SHA256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_sha256_sidecar(path: str, sha256_hex: str) -> None:
    """Write a `.sha256` sidecar for a downloaded artifact."""
    atomic_write_text(f"{path}.sha256", f"{sha256_hex}  {os.path.basename(path)}\n")


def _is_within_directory(base_dir: str, candidate_path: str) -> bool:
    """Return True when `candidate_path` resolves under `base_dir`."""
    base = os.path.abspath(base_dir)
    cand = os.path.abspath(candidate_path)
    return cand == base or cand.startswith(base + os.sep)


def _safe_extract_zip(zip_path: str, dest_dir: str) -> List[str]:
    """
    Safely extract a zip into dest_dir using a staging dir and then move
    the extracted top-level entries into dest_dir.
    """
    staging = tempfile.mkdtemp(prefix=".extract_", dir=dest_dir)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                name = info.filename
                if not name:
                    continue
                pp = PurePosixPath(name)
                if pp.is_absolute() or ".." in pp.parts:
                    raise RuntimeError(f"Unsafe zip member path: {name}")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise RuntimeError(f"Unsafe zip member (symlink): {name}")
                out_path = os.path.join(staging, *pp.parts)
                if not _is_within_directory(staging, out_path):
                    raise RuntimeError(f"Unsafe zip extraction path: {name}")

            zf.extractall(staging)

        moved: List[str] = []
        for entry in os.listdir(staging):
            src = os.path.join(staging, entry)
            dst = os.path.join(dest_dir, entry)
            if os.path.exists(dst):
                if os.path.isdir(dst) and not os.path.islink(dst):
                    shutil.rmtree(dst)
                else:
                    os.remove(dst)
            shutil.move(src, dst)
            moved.append(entry)
        return moved
    finally:
        try:
            shutil.rmtree(staging)
        except Exception:
            pass


def _safe_extract_tar(tar_path: str, dest_dir: str) -> List[str]:
    """
    Safely extract a tar into dest_dir using a staging dir and then move
    the extracted top-level entries into dest_dir.
    """
    staging = tempfile.mkdtemp(prefix=".extract_", dir=dest_dir)
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            for member in tf.getmembers():
                name = member.name
                if not name:
                    continue
                pp = PurePosixPath(name)
                if pp.is_absolute() or ".." in pp.parts:
                    raise RuntimeError(f"Unsafe tar member path: {name}")
                if member.issym() or member.islnk():
                    raise RuntimeError(f"Unsafe tar member (link): {name}")
                if not (member.isfile() or member.isdir()):
                    raise RuntimeError(f"Unsafe tar member type: {name}")
                out_path = os.path.join(staging, *pp.parts)
                if not _is_within_directory(staging, out_path):
                    raise RuntimeError(f"Unsafe tar extraction path: {name}")

            tf.extractall(staging)

        moved: List[str] = []
        for entry in os.listdir(staging):
            src = os.path.join(staging, entry)
            dst = os.path.join(dest_dir, entry)
            if os.path.exists(dst):
                if os.path.isdir(dst) and not os.path.islink(dst):
                    shutil.rmtree(dst)
                else:
                    os.remove(dst)
            shutil.move(src, dst)
            moved.append(entry)
        return moved
    finally:
        try:
            shutil.rmtree(staging)
        except Exception:
            pass


@dataclass
class DownloadJob:
    id: str
    kind: str
    created_at: float = field(default_factory=time.time)
    running: bool = True
    completed: bool = False
    error: Optional[str] = None
    output: List[str] = field(default_factory=list)
    progress: Dict[str, Any] = field(
        default_factory=lambda: {
            "bytes_downloaded": 0,
            "total_bytes": 0,
            "percent": 0,
            "speed_bps": 0,
            "eta_seconds": None,
            "start_time": None,
            "current_file": "",
        }
    )


_download_jobs: Dict[str, DownloadJob] = {}
_download_jobs_lock = threading.Lock()
_latest_download_job_id: Optional[str] = None


def _create_download_job(kind: str, *, current_file: str = "") -> DownloadJob:
    """Create and register a new in-memory download job."""
    global _latest_download_job_id
    job_id = str(uuid.uuid4())
    job = DownloadJob(id=job_id, kind=kind)
    job.progress["start_time"] = time.time()
    job.progress["current_file"] = current_file
    with _download_jobs_lock:
        _download_jobs[job_id] = job
        _latest_download_job_id = job_id
        if len(_download_jobs) > 25:
            oldest = sorted(_download_jobs.values(), key=lambda j: j.created_at)[:-25]
            for j in oldest:
                _download_jobs.pop(j.id, None)
    return job


def _get_download_job(job_id: Optional[str]) -> Optional[DownloadJob]:
    """Return the requested job, or the most recent job if `job_id` is None."""
    with _download_jobs_lock:
        if job_id:
            return _download_jobs.get(job_id)
        if _latest_download_job_id:
            return _download_jobs.get(_latest_download_job_id)
        return None


def _job_output(job_id: str, line: str) -> None:
    """Append a log line to a download job (trims to a fixed buffer)."""
    with _download_jobs_lock:
        job = _download_jobs.get(job_id)
        if not job:
            return
        job.output.append(str(line))
        if len(job.output) > 200:
            job.output = job.output[-200:]


def _job_set_progress(job_id: str, **updates: Any) -> None:
    """Update progress fields for an in-flight download job."""
    with _download_jobs_lock:
        job = _download_jobs.get(job_id)
        if not job:
            return
        job.progress.update(updates)


def _job_finish(job_id: str, *, completed: bool, error: Optional[str] = None) -> None:
    """Mark a download job as finished (success or error)."""
    with _download_jobs_lock:
        job = _download_jobs.get(job_id)
        if not job:
            return
        job.running = False
        job.completed = bool(completed)
        job.error = error


def setup_host_symlink() -> dict:
    """Create /app/project symlink on host for Docker path resolution.
    
    The admin_ui container uses PROJECT_ROOT=/app/project internally.
    When docker-compose runs from inside the container, the docker daemon
    (on the host) resolves paths like /app/project/models on the HOST.
    This symlink ensures the host's /app/project points to the actual project.
    """
    results = {"success": True, "messages": [], "errors": []}
    
    try:
        client = docker.from_env()
        
        # Create symlink on host: /app/project -> actual project path
        # We detect the actual host path from the admin_ui container's mount
        admin_container = client.containers.get("admin_ui")
        mounts = admin_container.attrs.get("Mounts", [])
        
        # Find the mount for /app/project
        host_project_path = None
        for mount in mounts:
            if mount.get("Destination") == "/app/project":
                host_project_path = mount.get("Source")
                break
        
        if host_project_path:
            # Run alpine container to create symlink on host
            symlink_script = f'''
                mkdir -p /app 2>/dev/null || true
                if [ -L /app/project ]; then
                    # Symlink exists, check if pointing to correct path
                    CURRENT=$(readlink /app/project)
                    if [ "$CURRENT" = "{host_project_path}" ]; then
                        echo "Symlink already correct"
                        exit 0
                    fi
                fi
                rm -rf /app/project 2>/dev/null || true
                ln -sfn {host_project_path} /app/project
                echo "Created symlink /app/project -> {host_project_path}"
            '''
            output = client.containers.run(
                "alpine:latest",
                command=["sh", "-c", symlink_script],
                volumes={"/app": {"bind": "/app", "mode": "rw"}},
                remove=True,
            )
            results["messages"].append(output.decode().strip() if output else "Symlink setup complete")
        else:
            results["messages"].append("Could not detect host project path from mounts")
            
    except Exception as e:
        results["errors"].append(f"Symlink setup error: {e}")
    
    return results


def setup_media_paths() -> dict:
    """Setup media directories and symlink for Asterisk playback.
    
    Mirrors the setup_media_paths() function from install.sh to ensure
    the wizard provides the same out-of-box experience.
    """
    results = {
        "success": True,
        "messages": [],
        "errors": []
    }
    
    # First, ensure host symlink exists for Docker path resolution
    symlink_result = setup_host_symlink()
    results["messages"].extend(symlink_result.get("messages", []))
    results["errors"].extend(symlink_result.get("errors", []))
    
    # Path inside container (mounted from host)
    container_media_dir = "/mnt/asterisk_media/ai-generated"
    # Path on host (PROJECT_ROOT is mounted from host)
    host_media_dir = os.path.join(PROJECT_ROOT, "asterisk_media", "ai-generated")
    
    # 1. Create directories with proper permissions (775 = rwxrwxr-x)
    try:
        os.makedirs(host_media_dir, mode=0o775, exist_ok=True)
        # Ensure parent also has correct permissions
        os.chmod(os.path.dirname(host_media_dir), 0o775)
        os.chmod(host_media_dir, 0o775)
        results["messages"].append(f"Created media directory: {host_media_dir}")
    except Exception as e:
        results["errors"].append(f"Failed to create media directory: {e}")
        results["success"] = False
    
    # 2. Try to create symlink on host via docker exec on host system
    # This runs a privileged command to create the symlink
    try:
        # Check if we can access docker socket
        client = docker.from_env()
        
        # Get the actual host path for PROJECT_ROOT
        # The symlink should be: /var/lib/asterisk/sounds/ai-generated -> {PROJECT_ROOT}/asterisk_media/ai-generated
        # We need to detect the actual host path
        
        # Run a command on host to create the symlink
        # Using alpine image with host volume mounts
        symlink_script = f'''
            mkdir -p /mnt/asterisk_media/ai-generated 2>/dev/null || true
            chmod 775 /mnt/asterisk_media/ai-generated 2>/dev/null || true
            chmod 775 /mnt/asterisk_media 2>/dev/null || true
            if [ -L /var/lib/asterisk/sounds/ai-generated ] || [ -e /var/lib/asterisk/sounds/ai-generated ]; then
                rm -rf /var/lib/asterisk/sounds/ai-generated 2>/dev/null || true
            fi
            ln -sfn /mnt/asterisk_media/ai-generated /var/lib/asterisk/sounds/ai-generated 2>/dev/null || true
            if [ -d /var/lib/asterisk/sounds/ai-generated ]; then
                echo "SUCCESS: Symlink created"
            else
                echo "FALLBACK: Creating alternative symlink"
                # Try alternative path if /mnt/asterisk_media doesn't exist
                PROJ_MEDIA="{PROJECT_ROOT}/asterisk_media/ai-generated"
                if [ -d "$PROJ_MEDIA" ]; then
                    ln -sfn "$PROJ_MEDIA" /var/lib/asterisk/sounds/ai-generated 2>/dev/null || true
                fi
            fi
        '''
        
        # Run on host via privileged container
        container = client.containers.run(
            "alpine:latest",
            command=["sh", "-c", symlink_script],
            volumes={
                "/var/lib/asterisk/sounds": {"bind": "/var/lib/asterisk/sounds", "mode": "rw"},
                "/mnt/asterisk_media": {"bind": "/mnt/asterisk_media", "mode": "rw"},
                PROJECT_ROOT: {"bind": PROJECT_ROOT, "mode": "rw"},
            },
            remove=True,
            detach=False,
        )
        output = container.decode() if isinstance(container, bytes) else str(container)
        results["messages"].append(f"Symlink setup: {output.strip()}")
        
    except docker.errors.ImageNotFound:
        results["messages"].append("Alpine image not found, will pull on next attempt")
        try:
            client.images.pull("alpine:latest")
            results["messages"].append("Pulled alpine image")
        except:
            results["errors"].append("Could not pull alpine image for symlink setup")
    except Exception as e:
        # Symlink creation failed, provide manual instructions
        results["messages"].append(f"Auto symlink setup skipped: {e}")
        results["messages"].append(
            "Manual setup required: Run on host:\n"
            f"  sudo ln -sfn {PROJECT_ROOT}/asterisk_media/ai-generated /var/lib/asterisk/sounds/ai-generated"
        )
    
    return results


@router.post("/init-env")
async def init_env():
    """Initialize .env from .env.example on first wizard step.
    
    Called when user clicks Next from step 1 (provider selection).
    This ensures .env exists with default values before proceeding.
    """
    created = ensure_env_file()
    return {"created": created, "env_path": ENV_PATH}


@router.get("/load-config")
async def load_existing_config():
    """Load existing configuration from .env file.
    
    Used to pre-populate wizard fields if config already exists.
    """
    from dotenv import dotenv_values
    import re
    
    config = {}
    
    # Load from .env if it exists
    if os.path.exists(ENV_PATH):
        env_values = dotenv_values(ENV_PATH)
        config = {
            "asterisk_host": env_values.get("ASTERISK_HOST", "127.0.0.1"),
            "asterisk_username": env_values.get("ASTERISK_ARI_USERNAME", ""),
            "asterisk_password": env_values.get("ASTERISK_ARI_PASSWORD", ""),
            "asterisk_port": int(env_values.get("ASTERISK_ARI_PORT", "8088")),
            "asterisk_scheme": env_values.get("ASTERISK_ARI_SCHEME", "http"),
            # App name is YAML-owned (asterisk.app_name). Keep env fallbacks for legacy setups only.
            "asterisk_app": (
                env_values.get("ASTERISK_APP_NAME")
                or env_values.get("ASTERISK_ARI_APP")
                or "asterisk-ai-voice-agent"
            ),
            "openai_key": env_values.get("OPENAI_API_KEY", ""),
            "groq_key": env_values.get("GROQ_API_KEY", ""),
            "deepgram_key": env_values.get("DEEPGRAM_API_KEY", ""),
            "google_key": env_values.get("GOOGLE_API_KEY", ""),
            "elevenlabs_key": env_values.get("ELEVENLABS_API_KEY", ""),
            "elevenlabs_agent_id": env_values.get("ELEVENLABS_AGENT_ID", ""),
            "local_stt_backend": env_values.get("LOCAL_STT_BACKEND", "vosk"),
            "local_tts_backend": env_values.get("LOCAL_TTS_BACKEND", "piper"),
            "kroko_embedded": _parse_optional_bool(env_values.get("KROKO_EMBEDDED")) is True,
            "kroko_api_key": env_values.get("KROKO_API_KEY", ""),
            "kokoro_mode": (env_values.get("KOKORO_MODE", "local") or "local").strip().lower(),
            "kokoro_voice": env_values.get("KOKORO_VOICE", "af_heart"),
            "kokoro_api_base_url": env_values.get("KOKORO_API_BASE_URL", ""),
            "kokoro_api_key": env_values.get("KOKORO_API_KEY", ""),
            "local_llm_model": "",
        }
    
    # Load AI config from YAML if it exists
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                yaml_config = yaml.safe_load(f)

            # Canonical: app name lives in YAML (asterisk.app_name)
            asterisk_yaml = (yaml_config.get("asterisk") or {}) if isinstance(yaml_config.get("asterisk"), dict) else {}
            if asterisk_yaml.get("app_name"):
                config["asterisk_app"] = asterisk_yaml.get("app_name")
            
            # Get default context settings
            default_ctx = yaml_config.get("contexts", {}).get("default", {})
            prompt = (default_ctx.get("prompt") or "").strip()
            greeting = (default_ctx.get("greeting") or "").strip()

            # Wizard stores prompt as: "You are <ai_name>, a <ai_role>. ..."
            # Best-effort parse to prepopulate ai_name/ai_role without inventing new YAML keys.
            ai_name = "Asterisk Agent"
            ai_role = "Helpful Assistant"
            if prompt:
                match = re.match(r"^You are\s+(?P<name>[^,]+),\s*a\s+(?P<role>[^.]+)\.", prompt, flags=re.IGNORECASE)
                if match:
                    ai_name = (match.group("name") or "").strip() or ai_name
                    ai_role = (match.group("role") or "").strip() or ai_role

            config["ai_name"] = ai_name
            config["ai_role"] = ai_role
            if greeting:
                config["greeting"] = greeting
            
            active_pipeline = (yaml_config.get("active_pipeline") or "").strip()
            if active_pipeline == "local_hybrid":
                config["provider"] = "local_hybrid"
            elif default_ctx.get("provider"):
                config["provider"] = default_ctx.get("provider")
            elif yaml_config.get("default_provider"):
                config["provider"] = yaml_config.get("default_provider")
        except:
            pass
    
    return config


@router.get("/engine-status")
async def get_engine_status():
    """Check if ai-engine container is running.
    
    Used in wizard completion step to determine if user needs
    to start the engine (first time) or if it's already running.
    """
    try:
        client = docker.from_env()
        try:
            container = client.containers.get("ai_engine")
            return {
                "running": container.status == "running",
                "status": container.status,
                "exists": True
            }
        except docker.errors.NotFound:
            return {
                "running": False,
                "status": "not_found",
                "exists": False
            }
    except Exception as e:
        return {
            "running": False,
            "status": "error",
            "exists": False,
            "error": str(e)
        }


@router.post("/setup-media-paths")
async def setup_media_paths_endpoint():
    """Setup media directories and symlinks for Asterisk audio playback.
    
    This endpoint ensures the AI Engine can write audio files that Asterisk
    can read for playback. Creates directories and symlinks as needed.
    """
    result = setup_media_paths()
    return result


@router.post("/start-engine")
async def start_engine(action: str = "start"):
    """Start, restart, or rebuild the ai-engine container.
    
    Args:
        action: "start" (default), "restart", or "rebuild"
    
    Uses docker compose (installed in container) to manage containers.
    Returns detailed progress and error information.
    """
    import subprocess
    import shutil
    from settings import PROJECT_ROOT
    
    print(f"DEBUG: AI Engine action={action} from PROJECT_ROOT={PROJECT_ROOT}")
    
    # Setup media paths first
    media_setup = setup_media_paths()
    
    steps = []
    
    def add_step(name: str, status: str, message: str = ""):
        steps.append({"name": name, "status": status, "message": message})
        print(f"DEBUG: Step '{name}': {status} - {message}")
    
    try:
        # Step 1: Check Docker availability
        docker_bin = shutil.which("docker")
        if not docker_bin:
            add_step("check_docker", "error", "docker binary not found in PATH")
            return {
                "success": False,
                "action": "error",
                "message": "docker binary not found in PATH",
                "steps": steps,
                "media_setup": media_setup,
            }

        add_step("check_docker", "running", "Checking Docker availability...")
        result = subprocess.run(
            [docker_bin, "compose", "version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            add_step("check_docker", "error", "Docker Compose not available")
            return {
                "success": False,
                "action": "error",
                "message": "Docker Compose not available in container",
                "steps": steps,
                "media_setup": media_setup
            }
        add_step("check_docker", "complete", f"Docker Compose available")
        
        # Step 2: Check current container status
        add_step("check_container", "running", "Checking container status...")
        client = docker.from_env()
        container_exists = False
        container_running = False
        try:
            container = client.containers.get("ai_engine")
            container_exists = True
            container_running = container.status == "running"
            add_step("check_container", "complete", f"Container exists, status: {container.status}")
        except docker.errors.NotFound:
            add_step("check_container", "complete", "Container does not exist")
        
        # Step 3: Determine action
        if action == "rebuild":
            add_step("rebuild", "running", "Rebuilding AI Engine image...")
            result = subprocess.run(
                [docker_bin, "compose", "-p", "asterisk-ai-voice-agent", "build", "--no-cache", "ai_engine"],
                cwd=PROJECT_ROOT,
                capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                add_step("rebuild", "error", result.stderr[:500] if result.stderr else "Build failed")
                return {
                    "success": False,
                    "action": "error",
                    "message": f"Failed to rebuild: {result.stderr[:200] if result.stderr else 'Unknown error'}",
                    "steps": steps,
                    "media_setup": media_setup
                }
            add_step("rebuild", "complete", "Image rebuilt successfully")
        
        # Step 4: Build image if container doesn't exist (docker compose handles image naming)
        if not container_exists:
            add_step("build", "running", "Building AI Engine image (this may take 1-2 minutes)...")
            build_result = subprocess.run(
                [docker_bin, "compose", "-p", "asterisk-ai-voice-agent", "build", "ai_engine"],
                cwd=PROJECT_ROOT,
                capture_output=True, text=True, timeout=300  # 5 min timeout for build
            )
            if build_result.returncode != 0:
                error_msg = build_result.stderr or build_result.stdout or "Build failed"
                add_step("build", "error", error_msg[:500])
                return {
                    "success": False,
                    "action": "error",
                    "message": f"Failed to build AI Engine image: {error_msg[:200]}",
                    "steps": steps,
                    "stdout": build_result.stdout,
                    "stderr": build_result.stderr,
                    "media_setup": media_setup
                }
            add_step("build", "complete", "Image built successfully")
        
        # Step 5: Start/restart container using docker compose
        #
        # NOTE: `docker compose restart` does NOT re-read env_file (.env). For wizard flows we want
        # env updates (keys/transport/etc.) to apply reliably, so use `up --force-recreate --no-build`.
        if action == "restart" and container_running:
            add_step("restart", "running", "Restarting AI Engine...")
            result = subprocess.run(
                [docker_bin, "compose", "-p", "asterisk-ai-voice-agent", "up", "-d", "--force-recreate", "--no-build", "ai_engine"],
                cwd=PROJECT_ROOT,
                capture_output=True, text=True, timeout=60
            )
        else:
            add_step("start", "running", "Starting AI Engine container...")
            # Use up -d with --force-recreate if container exists
            cmd = [docker_bin, "compose", "-p", "asterisk-ai-voice-agent", "up", "-d"]
            if container_exists:
                cmd.append("--force-recreate")
            cmd.append("ai_engine")
            
            result = subprocess.run(
                cmd,
                cwd=PROJECT_ROOT,
                capture_output=True, text=True, timeout=60  # Container start should be quick after build
            )
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            add_step("start" if action != "restart" else "restart", "error", error_msg[:500])
            return {
                "success": False,
                "action": "error",
                "message": f"Failed to start AI Engine: {error_msg[:200]}",
                "steps": steps,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "media_setup": media_setup
            }
        
        add_step("start" if action != "restart" else "restart", "complete", "Container started")
        
        # Step 5: Wait for health check
        add_step("health_check", "running", "Waiting for AI Engine to be ready...")
        import httpx
        import asyncio
        
        health_url = "http://127.0.0.1:15000/health"
        max_attempts = 30
        for attempt in range(max_attempts):
            try:
                async with httpx.AsyncClient(timeout=2.0) as http_client:
                    resp = await http_client.get(health_url)
                    if resp.status_code == 200:
                        health_data = resp.json()
                        add_step("health_check", "complete", f"AI Engine healthy - {len(health_data.get('providers', {}))} providers loaded")
                        return {
                            "success": True,
                            "action": action,
                            "message": "AI Engine started successfully",
                            "steps": steps,
                            "health": health_data,
                            "media_setup": media_setup
                        }
            except Exception:
                pass
            await asyncio.sleep(1)
        
        add_step("health_check", "warning", "Health check timed out but container is running")
        return {
            "success": True,
            "action": action,
            "message": "AI Engine started (health check pending)",
            "steps": steps,
            "media_setup": media_setup
        }
        
    except subprocess.TimeoutExpired as e:
        add_step("timeout", "error", f"Operation timed out after {e.timeout}s")
        return {
            "success": False,
            "action": "timeout",
            "message": f"Operation timed out. Check container logs.",
            "steps": steps,
            "media_setup": media_setup
        }
    except Exception as e:
        add_step("error", "error", str(e))
        return {
            "success": False,
            "action": "error",
            "message": str(e),
            "steps": steps,
            "media_setup": media_setup
        }

# ============== Local AI Server Setup ==============

# Model catalog - now imported from models_catalog.py for multi-language support
# Use get_full_catalog() to get the complete catalog with all language models


@router.get("/local/available-models")
async def get_available_models(language: Optional[str] = None):
    """Return catalog of available models with system recommendations.
    
    Args:
        language: Optional language code to filter models (e.g., 'en-US', 'fr-FR')
    """
    import psutil
    import subprocess
    
    # Get system info for recommendations
    ram_gb = psutil.virtual_memory().total // (1024**3)
    cpu_cores = psutil.cpu_count() or 1

    gpu_detected = _detect_gpu_from_env_or_runtime()
    
    # Get the full catalog or filtered by language
    if language:
        full_catalog = get_models_by_language(language)
        # Add LLM models (language-independent)
        full_catalog["llm"] = LLM_MODELS
    else:
        full_catalog = get_full_catalog()
    
    # Add recommendation flags based on system
    catalog = {}
    for category, models in full_catalog.items():
        catalog[category] = []
        for model in models:
            model_copy = model.copy()
            # Mark as system-recommended based on RAM + basic CPU/GPU heuristics.
            recommended_ram = int(model.get("recommended_ram_gb", 0) or 0)
            meets_ram = ram_gb >= recommended_ram

            system_recommended = False
            if category == "llm":
                model_id = model.get("id")
                if model_id == "tinyllama":
                    system_recommended = meets_ram and cpu_cores >= 2
                elif model_id == "phi3_mini":
                    system_recommended = meets_ram and cpu_cores >= 4
                elif model_id == "llama32_3b":
                    system_recommended = meets_ram and (gpu_detected or cpu_cores >= 6)
                elif model_id == "mistral_7b_instruct":
                    system_recommended = meets_ram and (gpu_detected or cpu_cores >= 12)
                elif model_id == "llama3_8b_instruct":
                    system_recommended = meets_ram and (gpu_detected or cpu_cores >= 16)
                else:
                    system_recommended = bool(model.get("recommended")) and meets_ram
            else:
                system_recommended = bool(model.get("recommended")) and meets_ram

            if system_recommended:
                model_copy["system_recommended"] = True
            catalog[category].append(model_copy)
    
    return {
        "catalog": catalog,
        "system_ram_gb": ram_gb,
        "system_cpu_cores": cpu_cores,
        "system_gpu_detected": gpu_detected,
        "languages": get_available_languages(),
        "language_names": LANGUAGE_NAMES,
        "region_names": REGION_NAMES
    }


@router.get("/local/available-languages")
async def get_languages():
    """Return list of all available languages for STT and TTS models."""
    return {
        "languages": get_available_languages(),
        "language_names": LANGUAGE_NAMES,
        "region_names": REGION_NAMES
    }


@router.get("/local/detect-tier")
async def detect_local_tier():
    """Detect system tier for local AI models based on CPU/RAM/GPU."""
    import subprocess
    from settings import PROJECT_ROOT
    
    try:
        # Get system info
        import psutil
        cpu_count = psutil.cpu_count()
        ram_gb = psutil.virtual_memory().total // (1024**3)
        
        gpu_detected = _detect_gpu_from_env_or_runtime()
        
        # Determine tier
        if gpu_detected:
            if ram_gb >= 32 and cpu_count >= 8:
                tier = "HEAVY_GPU"
            elif ram_gb >= 16 and cpu_count >= 4:
                tier = "MEDIUM_GPU"
            else:
                tier = "LIGHT_CPU"
        else:
            if ram_gb >= 32 and cpu_count >= 16:
                tier = "HEAVY_CPU"
            elif ram_gb >= 16 and cpu_count >= 8:
                tier = "MEDIUM_CPU"
            elif ram_gb >= 8 and cpu_count >= 4:
                tier = "LIGHT_CPU"
            else:
                tier = "LIGHT_CPU"
        
        # Tier descriptions
        tier_info = {
            "LIGHT_CPU": {
                "models": "TinyLlama 1.1B + Vosk Small + Piper Medium",
                "performance": "25-40 seconds per turn",
                "download_size": "~1.5 GB"
            },
            "MEDIUM_CPU": {
                "models": "Phi-3-mini 3.8B + Vosk 0.22 + Piper Medium",
                "performance": "20-30 seconds per turn",
                "download_size": "~3.5 GB"
            },
            "HEAVY_CPU": {
                "models": "Phi-3-mini 3.8B + Vosk 0.22 + Piper Medium",
                "performance": "25-35 seconds per turn",
                "download_size": "~3.5 GB"
            },
            "MEDIUM_GPU": {
                "models": "Phi-3-mini 3.8B + Vosk 0.22 + Piper Medium (GPU)",
                "performance": "8-12 seconds per turn",
                "download_size": "~3.5 GB"
            },
            "HEAVY_GPU": {
                "models": "Llama-2 13B + Vosk 0.22 + Piper High (GPU)",
                "performance": "10-15 seconds per turn",
                "download_size": "~10 GB"
            }
        }
        
        return {
            "cpu_cores": cpu_count,
            "ram_gb": ram_gb,
            "gpu_detected": gpu_detected,
            "tier": tier,
            "tier_info": tier_info.get(tier, {})
        }
    except Exception as e:
        return {"error": str(e)}


@router.post("/local/download-models")
async def download_local_models(tier: str = "auto"):
    """Start model download in background. Returns immediately."""
    import subprocess
    from settings import PROJECT_ROOT
    
    try:
        ok, warn_or_err = _disk_preflight(os.path.join(PROJECT_ROOT, "models"))
        if not ok:
            return {"status": "error", "message": warn_or_err}

        job = _create_download_job("script", current_file="scripts/model_setup.sh")

        # Run model_setup.sh with --assume-yes
        cmd = ["bash", "scripts/model_setup.sh", "--assume-yes"]
        if tier != "auto":
            cmd.extend(["--tier", tier])
        
        def run_download():
            try:
                process = subprocess.Popen(
                    cmd,
                    cwd=PROJECT_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )

                for line in iter(process.stdout.readline, ''):
                    if line:
                        _job_output(job.id, line.strip())
                
                process.wait()
                if process.returncode != 0:
                    _job_finish(job.id, completed=False, error=f"Download failed with code {process.returncode}")
                else:
                    _job_finish(job.id, completed=True)
            except Exception as e:
                _job_finish(job.id, completed=False, error=str(e))
        
        # Start download thread
        thread = threading.Thread(target=run_download, daemon=True)
        thread.start()
        
        return {
            "status": "started",
            "message": "Model download started. This may take several minutes.",
            "job_id": job.id,
            "disk_warning": warn_or_err if warn_or_err and warn_or_err.startswith("Low disk space") else None,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/local/download-progress")
async def get_download_progress(job_id: Optional[str] = None):
    """Get current download progress and output."""
    job = _get_download_job(job_id)
    if not job:
        return {
            "job_id": None,
            "running": False,
            "completed": False,
            "error": None,
            "output": [],
            "bytes_downloaded": 0,
            "total_bytes": 0,
            "percent": 0,
            "speed_bps": 0,
            "eta_seconds": None,
            "current_file": "",
        }

    return {
        "job_id": job.id,
        "running": job.running,
        "completed": job.completed,
        "error": job.error,
        "output": job.output[-20:] if job.output else [],  # Last 20 lines
        # Detailed progress info
        "bytes_downloaded": job.progress.get("bytes_downloaded", 0),
        "total_bytes": job.progress.get("total_bytes", 0),
        "percent": job.progress.get("percent", 0),
        "speed_bps": job.progress.get("speed_bps", 0),
        "eta_seconds": job.progress.get("eta_seconds"),
        "current_file": job.progress.get("current_file", "")
    }


class SingleModelDownload(BaseModel):
    model_id: str
    type: str  # stt, tts, llm
    download_url: str
    model_path: Optional[str] = None
    config_url: Optional[str] = None  # For TTS models that need JSON config
    voice_files: Optional[Dict[str, str]] = None  # For Kokoro TTS voice files
    expected_sha256: Optional[str] = None  # Optional integrity check


@router.post("/local/download-model")
async def download_single_model(request: SingleModelDownload):
    """Download a single model from the catalog."""
    from settings import PROJECT_ROOT

    # Determine target directory based on type
    # Special case: Kroko embedded models go to models/kroko/
    is_kroko_embedded = request.model_id and request.model_id.startswith("kroko_") and request.model_id != "kroko_cloud"
    
    if is_kroko_embedded:
        target_dir = os.path.join(PROJECT_ROOT, "models", "kroko")
    elif request.type == "stt":
        target_dir = os.path.join(PROJECT_ROOT, "models", "stt")
    elif request.type == "tts":
        target_dir = os.path.join(PROJECT_ROOT, "models", "tts")
    elif request.type == "llm":
        target_dir = os.path.join(PROJECT_ROOT, "models", "llm")
    else:
        return {"status": "error", "message": f"Invalid model type: {request.type}"}
    
    # Ensure target directory exists
    _ensure_models_dir_ready(target_dir)

    url_lower = (request.download_url or "").lower()
    is_archive_guess = any(x in url_lower for x in (".zip", ".tar.gz", ".tgz", ".tar.bz2", ".tar"))
    content_len = _url_content_length(request.download_url) or 0
    required = content_len * (3 if is_archive_guess else 2)
    ok, warn_or_err = _disk_preflight(target_dir, required_bytes=required)
    if not ok:
        return {"status": "error", "message": warn_or_err}

    job = _create_download_job("single", current_file=request.model_id)
    
    def download_worker():
        try:
            import json

            _job_output(job.id, f"📥 Starting download: {request.model_id}")
            _job_output(job.id, f"   URL: {request.download_url}")
            
            # Determine file extension
            if '.zip' in url_lower:
                ext = '.zip'
                is_archive = True
            elif '.tar.gz' in url_lower or '.tgz' in url_lower:
                ext = '.tar.gz'
                is_archive = True
            elif '.tar.bz2' in url_lower:
                ext = '.tar.bz2'
                is_archive = True
            elif '.tar' in url_lower:
                ext = '.tar'
                is_archive = True
            else:
                # Single file (e.g., ONNX model)
                ext = os.path.splitext(request.download_url)[1] or ''
                is_archive = False
            
            # Download to temp file
            temp_file = os.path.join(target_dir, f".{request.model_id}.{uuid.uuid4().hex}.download{ext}.part")
            start_time = time.time()
            last_update_time = start_time
            
            def progress_hook(block_num, block_size, total_size):
                nonlocal last_update_time
                
                bytes_downloaded = block_num * block_size
                current_time = time.time()
                elapsed = current_time - start_time
                
                if total_size > 0:
                    percent = min(100, (bytes_downloaded * 100) // total_size)
                    speed_bps = bytes_downloaded / elapsed if elapsed > 0 else 0
                    remaining_bytes = total_size - bytes_downloaded
                    eta_seconds = remaining_bytes / speed_bps if speed_bps > 0 else None
                    
                    _job_set_progress(
                        job.id,
                        bytes_downloaded=bytes_downloaded,
                        total_bytes=total_size,
                        percent=percent,
                        speed_bps=int(speed_bps),
                        eta_seconds=int(eta_seconds) if eta_seconds else None,
                        current_file=request.model_id,
                    )
                    
                    # Update output every 2 seconds
                    if current_time - last_update_time >= 2:
                        last_update_time = current_time
                        mb_done = bytes_downloaded / (1024 * 1024)
                        mb_total = total_size / (1024 * 1024)
                        speed_mbps = speed_bps / (1024 * 1024)
                        eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s" if eta_seconds else "calculating..."
                        _job_output(job.id, f"   {mb_done:.1f}/{mb_total:.1f} MB ({percent}%) - {speed_mbps:.2f} MB/s - ETA: {eta_str}")
            
            urllib.request.urlretrieve(request.download_url, temp_file, progress_hook)
            _job_set_progress(job.id, percent=100)
            _job_output(job.id, "✅ Download complete, verifying checksum...")

            sha = _sha256_file(temp_file)
            if request.expected_sha256 and sha.lower() != request.expected_sha256.lower():
                raise RuntimeError(f"SHA256 mismatch: expected={request.expected_sha256} got={sha}")
            
            if is_archive:
                # Extract archive
                _job_output(job.id, "📦 Extracting archive safely...")
                if ext == '.zip':
                    moved = _safe_extract_zip(temp_file, target_dir)
                elif ext in ['.tar.gz', '.tar', '.tgz', '.tar.bz2']:
                    moved = _safe_extract_tar(temp_file, target_dir)
                else:
                    moved = []

                root_folder = moved[0] if moved else None
                if root_folder:
                    meta_path = os.path.join(target_dir, root_folder, ".download.json")
                    atomic_write_text(
                        meta_path,
                        json.dumps(
                            {
                                "source_url": request.download_url,
                                "archive_sha256": sha,
                                "downloaded_at": int(time.time()),
                            },
                            indent=2,
                            sort_keys=False,
                        )
                        + "\n",
                    )
                    _job_output(job.id, f"✅ Extracted to {target_dir}/{root_folder}")
                else:
                    _job_output(job.id, f"✅ Extracted to {target_dir}")
                
                # Clean up archive file after extraction
                os.remove(temp_file)
                _job_output(job.id, "🧹 Cleaned up archive file")
            else:
                # Single file - rename to model_path or keep original name
                # Special handling for Kokoro which uses a directory structure
                if request.model_id == "kokoro_82m":
                    kokoro_dir = os.path.join(target_dir, "kokoro")
                    os.makedirs(kokoro_dir, exist_ok=True)
                    final_path = os.path.join(kokoro_dir, "kokoro-v1_0.pth")
                elif request.model_path:
                    final_path = os.path.join(target_dir, request.model_path)
                else:
                    final_path = os.path.join(target_dir, os.path.basename(request.download_url))
                
                os.makedirs(os.path.dirname(final_path), exist_ok=True)
                shutil.move(temp_file, final_path)
                _write_sha256_sidecar(final_path, sha)
                _job_output(job.id, f"✅ Saved to {final_path} (sha256={sha[:12]}...)")
                
                # Download config file for TTS models (e.g., Piper .onnx.json)
                if request.config_url and request.type == "tts":
                    # For Kokoro, config goes in the model directory; for Piper, next to .onnx
                    if request.model_id == "kokoro_82m":
                        kokoro_dir = os.path.dirname(final_path)
                        config_dest = os.path.join(kokoro_dir, "config.json")
                    else:
                        config_dest = final_path + ".json"
                    _job_output(job.id, f"📥 Downloading config file...")
                    try:
                        tmp_cfg = config_dest + f".{uuid.uuid4().hex}.part"
                        urllib.request.urlretrieve(request.config_url, tmp_cfg)
                        cfg_sha = _sha256_file(tmp_cfg)
                        shutil.move(tmp_cfg, config_dest)
                        _write_sha256_sidecar(config_dest, cfg_sha)
                        _job_output(job.id, f"✅ Config saved to {config_dest}")
                    except Exception as config_err:
                        _job_output(job.id, f"⚠️ Config download failed: {config_err}")
                
                # Download voice files for Kokoro TTS
                if request.voice_files and request.type == "tts":
                    kokoro_dir = os.path.dirname(final_path)
                    voices_dir = os.path.join(kokoro_dir, "voices")
                    os.makedirs(voices_dir, exist_ok=True)
                    _job_output(job.id, f"📥 Downloading voice files...")
                    for voice_name, voice_url in request.voice_files.items():
                        try:
                            voice_dest = os.path.join(voices_dir, f"{voice_name}.pt")
                            tmp_voice = voice_dest + f".{uuid.uuid4().hex}.part"
                            urllib.request.urlretrieve(voice_url, tmp_voice)
                            v_sha = _sha256_file(tmp_voice)
                            shutil.move(tmp_voice, voice_dest)
                            _write_sha256_sidecar(voice_dest, v_sha)
                            _job_output(job.id, f"✅ Voice '{voice_name}' saved")
                        except Exception as voice_err:
                            _job_output(job.id, f"⚠️ Voice '{voice_name}' download failed: {voice_err}")

            _job_finish(job.id, completed=True)
            _job_output(job.id, f"🎉 Model {request.model_id} installed successfully!")
            
        except Exception as e:
            _job_finish(job.id, completed=False, error=str(e))
            _job_output(job.id, f"❌ Error: {str(e)}")
            # Clean up partial download on error
            try:
                if "temp_file" in locals() and os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass
    
    # Start download thread
    thread = threading.Thread(target=download_worker, daemon=True)
    thread.start()
    
    return {
        "status": "started",
        "message": f"Downloading {request.model_id}...",
        "job_id": job.id,
        "disk_warning": warn_or_err if warn_or_err and warn_or_err.startswith("Low disk space") else None,
    }


class ModelSelection(BaseModel):
    stt: str  # backend name (e.g., "vosk")
    llm: str  # model id (e.g., "phi-3-mini")
    tts: str  # backend name (e.g., "piper")
    language: Optional[str] = "en-US"
    kroko_embedded: Optional[bool] = False
    kroko_api_key: Optional[str] = None
    kokoro_mode: Optional[str] = "local"
    kokoro_voice: Optional[str] = "af_heart"
    kokoro_api_base_url: Optional[str] = None
    kokoro_api_key: Optional[str] = None
    # Local Hybrid support: download/apply only STT/TTS (skip LLM model download/config)
    skip_llm_download: Optional[bool] = False
    # New fields for exact model selection
    stt_model_id: Optional[str] = None  # exact model id (e.g., "vosk_en_us_small")
    tts_model_id: Optional[str] = None  # exact model id (e.g., "piper_en_us_lessac_medium")
    # Optional: custom LLM GGUF URL download (advanced)
    llm_download_url: Optional[str] = None
    llm_model_path: Optional[str] = None  # optional filename under models/llm/
    llm_name: Optional[str] = None  # optional display name for logs


@router.post("/local/download-selected-models")
async def download_selected_models(selection: ModelSelection):
    """Download user-selected models from the catalog."""
    from settings import PROJECT_ROOT

    # Ensure base models directory exists and is writable before starting downloads.
    _ensure_models_dir_ready(os.path.join(PROJECT_ROOT, "models"))
    _ensure_models_dir_ready(os.path.join(PROJECT_ROOT, "models", "stt"))
    _ensure_models_dir_ready(os.path.join(PROJECT_ROOT, "models", "tts"))
    _ensure_models_dir_ready(os.path.join(PROJECT_ROOT, "models", "llm"))
    _ensure_models_dir_ready(os.path.join(PROJECT_ROOT, "models", "kroko"))

    # Get full catalog
    catalog = get_full_catalog()
    
    # Find appropriate model - prefer exact model_id, fallback to backend+language
    def find_stt_model(backend: str, language: str, model_id: str = None):
        """Find the best STT model. Prefers exact model_id match."""
        # First try exact model ID match
        if model_id:
            for model in catalog["stt"]:
                if model.get("id") == model_id:
                    return model
        # Fallback to backend + language match
        for model in catalog["stt"]:
            if model.get("backend") == backend and model.get("language") == language:
                return model
        # Fallback to English if language not available
        for model in catalog["stt"]:
            if model.get("backend") == backend and model.get("language") == "en-US":
                return model
        # Final fallback to any model with that backend
        for model in catalog["stt"]:
            if model.get("backend") == backend:
                return model
        return None
    
    def find_tts_model(backend: str, language: str, model_id: str = None):
        """Find the best TTS model. Prefers exact model_id match."""
        # First try exact model ID match
        if model_id:
            for model in catalog["tts"]:
                if model.get("id") == model_id:
                    return model
        # Fallback to backend + language match
        for model in catalog["tts"]:
            if model.get("backend") == backend and model.get("language") == language:
                return model
        # Fallback to English if language not available
        for model in catalog["tts"]:
            if model.get("backend") == backend and model.get("language") == "en-US":
                return model
        # Final fallback to any model with that backend
        for model in catalog["tts"]:
            if model.get("backend") == backend:
                return model
        return None
    
    # Get model info from catalog - prefer exact model_id if provided
    stt_model = find_stt_model(selection.stt, selection.language, selection.stt_model_id)
    skip_llm_download = bool(selection.skip_llm_download)
    llm_model = None if skip_llm_download else next((m for m in catalog["llm"] if m.get("id") == selection.llm), None)
    tts_model = find_tts_model(selection.tts, selection.language, selection.tts_model_id)

    # Support custom GGUF LLM downloads (Wizard advanced path)
    if not skip_llm_download and selection.llm == "custom_gguf_url":
        url = (selection.llm_download_url or "").strip()
        if not url:
            return {"status": "error", "message": "Custom LLM selected but llm_download_url is empty"}
        filename = (selection.llm_model_path or "").strip()
        if not filename:
            filename = os.path.basename(url.split("?", 1)[0])
        if not filename.endswith(".gguf"):
            return {"status": "error", "message": "Custom LLM filename must end with .gguf"}
        llm_model = {
            "id": "custom_gguf_url",
            "name": selection.llm_name or filename,
            "download_url": url,
            "model_path": filename,
            "size_mb": 0,
            "size_display": "Custom",
        }

    if not stt_model:
        return {"status": "error", "message": f"Unknown STT model: {selection.stt}"}
    if not skip_llm_download and not llm_model:
        return {"status": "error", "message": f"Unknown LLM model: {selection.llm}"}
    if not tts_model:
        return {"status": "error", "message": f"Unknown TTS model: {selection.tts}"}

    kokoro_mode = (selection.kokoro_mode or "local").lower()
    skip_kokoro_download = tts_model.get("backend") == "kokoro" and kokoro_mode in ("api", "hf")

    models_dir = os.path.join(PROJECT_ROOT, "models")
    _ensure_models_dir_ready(models_dir)

    # Disk preflight (best-effort: HEAD Content-Length). Archives need extra room for extraction.
    urls: List[Tuple[str, bool]] = []
    if stt_model.get("download_url"):
        stt_url = stt_model["download_url"]
        urls.append((stt_url, any(x in stt_url.lower() for x in (".zip", ".tar", ".tgz"))))
    if not skip_llm_download and llm_model and llm_model.get("download_url"):
        llm_url = llm_model["download_url"]
        urls.append((llm_url, False))
    if not skip_kokoro_download and tts_model.get("download_url"):
        tts_url = tts_model["download_url"]
        urls.append((tts_url, False))
    if not skip_kokoro_download and tts_model.get("config_url"):
        urls.append((tts_model["config_url"], False))
    if not skip_kokoro_download and tts_model.get("voice_files"):
        for _, voice_url in (tts_model.get("voice_files") or {}).items():
            urls.append((voice_url, False))

    required_bytes = 0
    for u, is_archive in urls:
        cl = _url_content_length(u) or 0
        required_bytes += cl * (3 if is_archive else 2)

    ok, warn_or_err = _disk_preflight(models_dir, required_bytes=required_bytes)
    if not ok:
        return {"status": "error", "message": warn_or_err}

    job = _create_download_job("selected", current_file="models")
    _job_output(job.id, f"🌍 Selected language: {selection.language}")
    if skip_llm_download:
        _job_output(job.id, "ℹ️ Skipping LLM download (Local Hybrid mode)")

    def download_file(url: str, dest_path: str, label: str, expected_sha256: Optional[str] = None):
        """Download a file with progress reporting and write a sha256 sidecar."""
        tmp_path = dest_path + f".{uuid.uuid4().hex}.part"
        try:
            _job_output(job.id, f"⬇️ Downloading {label}...")
            _job_set_progress(job.id, current_file=label)

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            start_time = time.time()
            last_update = start_time

            def report_progress(block_num, block_size, total_size):
                nonlocal last_update
                bytes_done = block_num * block_size
                if total_size > 0:
                    now = time.time()
                    percent = int(min(100, (bytes_done * 100) // total_size))
                    elapsed = max(0.001, now - start_time)
                    speed_bps = int(bytes_done / elapsed)
                    remaining = max(0, total_size - bytes_done)
                    eta = int(remaining / speed_bps) if speed_bps > 0 else None

                    _job_set_progress(
                        job.id,
                        bytes_downloaded=int(bytes_done),
                        total_bytes=int(total_size),
                        percent=percent,
                        speed_bps=speed_bps,
                        eta_seconds=eta,
                        current_file=label,
                    )

                    if now - last_update >= 2:
                        last_update = now
                        mb_done = bytes_done / (1024 * 1024)
                        mb_total = total_size / (1024 * 1024)
                        _job_output(job.id, f"   {label}: {mb_done:.1f}/{mb_total:.1f} MB ({percent}%)")

            urllib.request.urlretrieve(url, tmp_path, report_progress)
            _job_output(job.id, "🔐 Verifying checksum...")
            sha = _sha256_file(tmp_path)
            if expected_sha256 and sha.lower() != expected_sha256.lower():
                raise RuntimeError(f"SHA256 mismatch: expected={expected_sha256} got={sha}")

            shutil.move(tmp_path, dest_path)
            _write_sha256_sidecar(dest_path, sha)
            _job_output(job.id, f"✅ {label} downloaded successfully")
            return True
        except Exception as e:
            _job_output(job.id, f"❌ Failed to download {label}: {e}")
            return False
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
    
    def run_downloads():
        success = True
        
        try:
            import json

            # Download STT model
            if stt_model.get("download_url"):
                stt_dir = os.path.join(models_dir, "stt")
                os.makedirs(stt_dir, exist_ok=True)
                
                if stt_model.get("backend") == "vosk":
                    # Vosk is a zip file
                    zip_path = os.path.join(stt_dir, "vosk-model.zip")
                    if download_file(stt_model["download_url"], zip_path, "Vosk STT Model", stt_model.get("sha256")):
                        _job_output(job.id, "📦 Extracting Vosk model safely...")
                        sha = _sha256_file(zip_path)
                        moved = _safe_extract_zip(zip_path, stt_dir)
                        root = moved[0] if moved else None
                        if root:
                            atomic_write_text(
                                os.path.join(stt_dir, root, ".download.json"),
                                json.dumps(
                                    {
                                        "source_url": stt_model["download_url"],
                                        "archive_sha256": sha,
                                        "downloaded_at": int(time.time()),
                                    },
                                    indent=2,
                                    sort_keys=False,
                                )
                                + "\n",
                            )
                        try:
                            os.remove(zip_path)
                        except Exception:
                            pass
                        try:
                            os.remove(zip_path + ".sha256")
                        except Exception:
                            pass
                        _job_output(job.id, "✅ Vosk model extracted")
                    else:
                        success = False
                elif stt_model.get("backend") == "sherpa":
                    # Sherpa is a tar.bz2 archive
                    archive_path = os.path.join(stt_dir, "sherpa-model.tar.bz2")
                    if download_file(stt_model["download_url"], archive_path, "Sherpa STT Model", stt_model.get("sha256")):
                        _job_output(job.id, "📦 Extracting Sherpa model safely...")
                        sha = _sha256_file(archive_path)
                        moved = _safe_extract_tar(archive_path, stt_dir)
                        root = moved[0] if moved else None
                        if root:
                            atomic_write_text(
                                os.path.join(stt_dir, root, ".download.json"),
                                json.dumps(
                                    {
                                        "source_url": stt_model["download_url"],
                                        "archive_sha256": sha,
                                        "downloaded_at": int(time.time()),
                                    },
                                    indent=2,
                                    sort_keys=False,
                                )
                                + "\n",
                            )
                        try:
                            os.remove(archive_path)
                        except Exception:
                            pass
                        try:
                            os.remove(archive_path + ".sha256")
                        except Exception:
                            pass
                        _job_output(job.id, "✅ Sherpa model extracted")
                    else:
                        success = False
                elif stt_model.get("backend") == "kroko" and stt_model.get("embedded"):
                    # Kroko embedded ONNX models go to models/kroko/
                    kroko_dir = os.path.join(models_dir, "kroko")
                    os.makedirs(kroko_dir, exist_ok=True)
                    dest = os.path.join(kroko_dir, stt_model["model_path"])
                    if not download_file(stt_model["download_url"], dest, "Kroko Embedded STT Model", stt_model.get("sha256")):
                        success = False
                else:
                    # Single file model
                    dest = os.path.join(stt_dir, stt_model["model_path"])
                    if not download_file(stt_model["download_url"], dest, "STT Model", stt_model.get("sha256")):
                        success = False
            else:
                _job_output(job.id, f"ℹ️ STT: {stt_model['name']} (no download needed)")
            
            # Download LLM model (optional for Local Hybrid mode)
            if not skip_llm_download and llm_model:
                if llm_model.get("download_url"):
                    llm_dir = os.path.join(models_dir, "llm")
                    os.makedirs(llm_dir, exist_ok=True)
                    dest = os.path.join(llm_dir, llm_model["model_path"])
                    if not download_file(llm_model["download_url"], dest, "LLM Model", llm_model.get("sha256")):
                        success = False
                else:
                    _job_output(job.id, f"ℹ️ LLM: {llm_model['name']} (no download needed)")
            
            # Download TTS model
            if skip_kokoro_download:
                _job_output(
                    job.id,
                    f"ℹ️ TTS: {tts_model['name']} (no download needed for Kokoro mode={kokoro_mode})"
                )
            elif tts_model.get("download_url"):
                tts_dir = os.path.join(models_dir, "tts")
                os.makedirs(tts_dir, exist_ok=True)
                
                if tts_model.get("backend") == "kokoro":
                    # Kokoro has multiple files: model, config, and voice files
                    kokoro_dir = os.path.join(tts_dir, "kokoro")
                    os.makedirs(kokoro_dir, exist_ok=True)
                    voices_dir = os.path.join(kokoro_dir, "voices")
                    os.makedirs(voices_dir, exist_ok=True)
                    
                    # Download main model
                    model_dest = os.path.join(kokoro_dir, "kokoro-v1_0.pth")
                    if not download_file(tts_model["download_url"], model_dest, "Kokoro TTS Model", tts_model.get("sha256")):
                        success = False
                    
                    # Download config
                    if tts_model.get("config_url"):
                        config_dest = os.path.join(kokoro_dir, "config.json")
                        download_file(tts_model["config_url"], config_dest, "Kokoro Config", tts_model.get("config_sha256"))
                    
                    # Download voice files
                    if tts_model.get("voice_files"):
                        for voice_name, voice_url in tts_model["voice_files"].items():
                            voice_dest = os.path.join(voices_dir, f"{voice_name}.pt")
                            download_file(voice_url, voice_dest, f"Kokoro Voice: {voice_name}")
                else:
                    # Standard single-file TTS model (Piper)
                    dest = os.path.join(tts_dir, tts_model["model_path"])
                    if not download_file(tts_model["download_url"], dest, "TTS Model", tts_model.get("sha256")):
                        success = False
                    
                    # Also download config file if present
                    if tts_model.get("config_url"):
                        config_dest = dest + ".json"
                        download_file(tts_model["config_url"], config_dest, "TTS Config", tts_model.get("config_sha256"))
            else:
                _job_output(job.id, f"ℹ️ TTS: {tts_model['name']} (no download needed)")
            
            # Update .env with selected models
            _job_output(job.id, "📝 Updating configuration...")
            env_updates = []
            
            # Persist backend selections (even if no download needed)
            env_updates.append(f"LOCAL_STT_BACKEND={stt_model.get('backend') or selection.stt}")
            env_updates.append(f"LOCAL_TTS_BACKEND={tts_model.get('backend') or selection.tts}")
            if skip_llm_download:
                env_updates.append("LOCAL_AI_MODE=minimal")
            else:
                env_updates.append("LOCAL_AI_MODE=full")

            # Kroko toggle (embedded vs cloud)
            if (stt_model.get("backend") or selection.stt) == "kroko":
                env_updates.append(f"KROKO_EMBEDDED={'1' if selection.kroko_embedded else '0'}")
                env_updates.append(f"KROKO_LANGUAGE={selection.language or 'en-US'}")
                if not selection.kroko_embedded and selection.kroko_api_key:
                    env_updates.append(f"KROKO_API_KEY={selection.kroko_api_key}")
            
            # Set model paths
            if stt_model.get("model_path"):
                stt_backend = (stt_model.get("backend") or selection.stt or "").lower()
                if stt_backend == "sherpa":
                    stt_path = os.path.join("/app/models/stt", stt_model["model_path"])
                    env_updates.append(f"SHERPA_MODEL_PATH={stt_path}")
                elif stt_backend == "kroko":
                    if selection.kroko_embedded:
                        stt_path = os.path.join("/app/models/kroko", stt_model["model_path"])
                        env_updates.append(f"KROKO_MODEL_PATH={stt_path}")
                elif stt_backend == "faster_whisper":
                    env_updates.append(f"FASTER_WHISPER_MODEL={stt_model['model_path']}")
                else:
                    stt_path = os.path.join("/app/models/stt", stt_model["model_path"])
                    env_updates.append(f"LOCAL_STT_MODEL_PATH={stt_path}")
            
            if not skip_llm_download and llm_model and llm_model.get("model_path") and llm_model.get("download_url"):
                llm_path = os.path.join("/app/models/llm", llm_model["model_path"])
                env_updates.append(f"LOCAL_LLM_MODEL_PATH={llm_path}")
            
            if tts_model.get("model_path"):
                tts_backend = (tts_model.get("backend") or selection.tts or "").lower()
                if tts_backend == "kokoro":
                    tts_path = os.path.join("/app/models/tts", tts_model["model_path"])
                    env_updates.append(f"KOKORO_MODEL_PATH={tts_path}")
                elif tts_backend == "melotts":
                    env_updates.append(f"MELOTTS_VOICE={tts_model['model_path']}")
                elif tts_model.get("download_url"):
                    tts_path = os.path.join("/app/models/tts", tts_model["model_path"])
                    env_updates.append(f"LOCAL_TTS_MODEL_PATH={tts_path}")

            # Kokoro mode: local vs api/hf (no local files required)
            if (tts_model.get("backend") or selection.tts) == "kokoro":
                mode = kokoro_mode
                if mode not in ("local", "api", "hf"):
                    mode = "local"
                env_updates.append(f"KOKORO_MODE={mode}")
                env_updates.append(f"KOKORO_VOICE={selection.kokoro_voice or 'af_heart'}")
                if mode == "api":
                    if selection.kokoro_api_base_url:
                        env_updates.append(f"KOKORO_API_BASE_URL={selection.kokoro_api_base_url}")
                    if selection.kokoro_api_key:
                        env_updates.append(f"KOKORO_API_KEY={selection.kokoro_api_key}")
            
            # Write to .env
            if env_updates:
                env_path = os.path.join(PROJECT_ROOT, ".env")
                updates_dict = {}
                for update in env_updates:
                    if "=" not in update:
                        continue
                    k, v = update.split("=", 1)
                    updates_dict[k.strip()] = v.strip()

                upsert_env_vars(
                    env_path,
                    updates_dict,
                    header="Model selections from wizard",
                )
                
                _job_output(job.id, "✅ Configuration updated")
            
            if success:
                _job_output(job.id, "🎉 All models downloaded successfully!")
                _job_finish(job.id, completed=True)
            else:
                _job_finish(job.id, completed=False, error="Some downloads failed")
                
        except Exception as e:
            _job_finish(job.id, completed=False, error=str(e))
            _job_output(job.id, f"❌ Error: {e}")
    
    # Start download thread
    thread = threading.Thread(target=run_downloads, daemon=True)
    thread.start()
    
    total_mb = (
        stt_model.get("size_mb", 0)
        + (0 if skip_llm_download or not llm_model else llm_model.get("size_mb", 0))
        + (0 if skip_kokoro_download else tts_model.get("size_mb", 0))
    )
    
    return {
        "status": "started",
        "message": f"Downloading {total_mb} MB of models...",
        "models": {
            "stt": stt_model["name"],
            "llm": None if skip_llm_download or not llm_model else llm_model["name"],
            "tts": tts_model["name"]
        },
        "job_id": job.id,
        "disk_warning": warn_or_err if warn_or_err and warn_or_err.startswith("Low disk space") else None,
    }


@router.get("/local/models-status")
async def check_models_status():
    """Check if required models are downloaded.
    
    Detects all supported STT/TTS backends:
    - STT: Vosk (vosk-model*), Sherpa-ONNX (sherpa*), Kroko (kroko*)
    - TTS: Piper (*.onnx), Kokoro (kokoro/voices/*.pt)
    - LLM: GGUF models (*.gguf)
    """
    from settings import PROJECT_ROOT
    import os
    
    models_dir = os.path.join(PROJECT_ROOT, "models")
    
    # STT models grouped by backend
    stt_backends = {
        "vosk": [],
        "sherpa": [],
        "kroko": []
    }
    
    # TTS models grouped by backend
    tts_backends = {
        "piper": [],
        "kokoro": []
    }
    
    # LLM models
    llm_models = []
    
    stt_dir = os.path.join(models_dir, "stt")
    llm_dir = os.path.join(models_dir, "llm")
    tts_dir = os.path.join(models_dir, "tts")
    
    # Scan STT models
    if os.path.exists(stt_dir):
        for item in os.listdir(stt_dir):
            item_path = os.path.join(stt_dir, item)
            if item.startswith("vosk-model") and os.path.isdir(item_path):
                stt_backends["vosk"].append(item)
            elif "sherpa" in item.lower() and os.path.isdir(item_path):
                stt_backends["sherpa"].append(item)
    
    # Check for Kroko models (separate directory)
    kroko_dir = os.path.join(models_dir, "kroko")
    if os.path.exists(kroko_dir):
        for item in os.listdir(kroko_dir):
            if item.endswith(".onnx") or item.endswith(".data"):
                stt_backends["kroko"].append(item)
    
    # Scan LLM models
    if os.path.exists(llm_dir):
        for item in os.listdir(llm_dir):
            if item.endswith(".gguf"):
                llm_models.append(item)
    
    # Scan TTS models
    if os.path.exists(tts_dir):
        for item in os.listdir(tts_dir):
            item_path = os.path.join(tts_dir, item)
            if item.endswith(".onnx"):
                tts_backends["piper"].append(item)
            elif item == "kokoro" and os.path.isdir(item_path):
                # Check for Kokoro voice files
                voices_dir = os.path.join(item_path, "voices")
                if os.path.exists(voices_dir):
                    for voice in os.listdir(voices_dir):
                        if voice.endswith(".pt"):
                            tts_backends["kokoro"].append(voice.replace(".pt", ""))
                # Also check for model files directly in kokoro dir
                if not tts_backends["kokoro"]:
                    # Fall back to checking for .pt files in kokoro dir
                    for f in os.listdir(item_path):
                        if f.endswith(".pt"):
                            tts_backends["kokoro"].append(f.replace(".pt", ""))
    
    # Compute ready state: at least one STT backend, one TTS backend, and LLM
    stt_ready = any(stt_backends.values())
    tts_ready = any(tts_backends.values())
    llm_ready = len(llm_models) > 0
    ready = stt_ready and tts_ready and llm_ready
    
    # Flatten for backward compatibility
    stt_models = (
        stt_backends["vosk"] + 
        [f"sherpa:{m}" for m in stt_backends["sherpa"]] +
        [f"kroko:{m}" for m in stt_backends["kroko"]]
    )
    tts_models = (
        tts_backends["piper"] +
        [f"kokoro:{v}" for v in tts_backends["kokoro"]]
    )
    
    return {
        "ready": ready,
        "stt_models": stt_models,
        "llm_models": llm_models,
        "tts_models": tts_models,
        # New detailed breakdown by backend
        "stt_backends": stt_backends,
        "tts_backends": tts_backends,
        "status": {
            "stt_ready": stt_ready,
            "llm_ready": llm_ready,
            "tts_ready": tts_ready
        }
    }


@router.post("/local/start-server")
async def start_local_ai_server():
    """Start the local-ai-server container.
    
    Also sets up media paths for audio playback to work correctly.
    Uses --no-build when possible (fast), falls back to background build if needed.
    """
    import subprocess
    from settings import PROJECT_ROOT
    
    # Setup media paths first (same as start_engine)
    print("DEBUG: Setting up media paths for local AI server...")
    media_setup = setup_media_paths()
    print(f"DEBUG: Media setup result: {media_setup}")
    
    try:
        # Use preflight-derived flag to decide compose override.
        # Do not infer from runtime nvidia-smi here because passthrough may be unavailable.
        gpu_available = _gpu_override_enabled_from_preflight()

        # Build docker compose command - use GPU override file if GPU detected
        cmd_base = ["docker", "compose", "-p", "asterisk-ai-voice-agent"]
        if gpu_available:
            print("DEBUG: GPU override enabled (GPU_AVAILABLE=true), using docker-compose.gpu.yml")
            cmd_base += ["-f", "docker-compose.yml", "-f", "docker-compose.gpu.yml"]

        # Fast path: start from existing image (avoid triggering a rebuild on every click)
        cmd_no_build = cmd_base + ["up", "-d", "--no-build", "local_ai_server"]
        result = subprocess.run(
            cmd_no_build,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return {
                "success": True,
                "message": "Local AI Server started.",
                "media_setup": media_setup,
            }

        stderr = (result.stderr or result.stdout or "").strip()

        # If a stale container name is blocking startup, remove and retry once.
        if "Conflict" in stderr and "local_ai_server" in stderr:
            try:
                client = docker.from_env()
                try:
                    old_container = client.containers.get("local_ai_server")
                    print(f"DEBUG: Removing conflicting local_ai_server container ({old_container.status})")
                    old_container.remove(force=True)
                except docker.errors.NotFound:
                    pass
            except Exception as e:
                print(f"DEBUG: Error removing conflicting container: {e}")

            retry = subprocess.run(
                cmd_no_build,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if retry.returncode == 0:
                return {
                    "success": True,
                    "message": "Local AI Server started.",
                    "media_setup": media_setup,
                }
            stderr = (retry.stderr or retry.stdout or "").strip()

        # Slow path: image missing or needs build; kick off build+up in background.
        needs_build_markers = [
            "No such image",
            "pull access denied",
            "failed to solve",
            "unable to find image",
            "requires build",
        ]
        if any(m.lower() in stderr.lower() for m in needs_build_markers):
            log_path = os.path.join(PROJECT_ROOT, "logs", "local_ai_server_start.log")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            logf = open(log_path, "a")
            logf.write("\n\n=== local_ai_server start (wizard) ===\n")
            logf.flush()

            cmd_build = cmd_base + ["up", "-d", "--build", "local_ai_server"]
            subprocess.Popen(
                cmd_build,
                cwd=PROJECT_ROOT,
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            logf.close()

            return {
                "success": True,
                "building": True,
                "message": f"Local AI Server image is being built in the background (this can take 10-60 minutes for GPU builds). See {log_path} or container logs once created.",
                "media_setup": media_setup,
            }

        return {
            "success": False,
            "message": f"Failed to start local_ai_server: {stderr or 'Unknown error'}",
            "media_setup": media_setup,
        }
    except FileNotFoundError as e:
        return {"success": False, "message": f"Failed to start: {e}", "media_setup": media_setup}
    except Exception as e:
        return {"success": False, "message": str(e), "media_setup": media_setup}


@router.get("/local/server-logs")
async def get_local_server_logs():
    """Get local-ai-server container logs."""
    import subprocess
    
    try:
        # Get recent logs for display
        result = subprocess.run(
            ["docker", "logs", "--tail", "30", "local_ai_server"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        logs = result.stdout or result.stderr
        lines = logs.strip().split('\n') if logs else []
        
        # Check if server is ready by looking at ALL logs (not just tail)
        # The startup message might be pushed out by connection logs
        ready_result = subprocess.run(
            ["docker", "logs", "local_ai_server"],
            capture_output=True,
            text=True,
            timeout=10
        )
        all_logs = (ready_result.stdout or "") + (ready_result.stderr or "")
        
        # Check for ready indicators in full log history
        ready = "Enhanced Local AI Server started" in all_logs or \
                "All models loaded successfully" in all_logs or \
                "models loaded" in all_logs.lower()
        
        return {
            "logs": lines[-20:],
            "ready": ready
        }
    except subprocess.TimeoutExpired:
        return {"logs": [], "ready": False, "error": "Timeout getting logs"}
    except Exception as e:
        # Fallback: if container isn't created yet (e.g., still building), show the build/start log if present.
        try:
            import os

            log_path = os.path.join(os.getenv("PROJECT_ROOT", "/app/project"), "logs", "local_ai_server_start.log")
            if os.path.exists(log_path):
                with open(log_path, "r") as f:
                    tail = f.read().splitlines()[-50:]
                return {"logs": tail[-20:], "ready": False, "error": str(e)}
        except Exception:
            pass
        return {"logs": [], "ready": False, "error": str(e)}


@router.get("/local/server-status")
async def get_local_server_status():
    """Check if local-ai-server is running and healthy."""
    import docker
    import websockets
    import json
    import asyncio
    
    try:
        client = docker.from_env()
        try:
            container = client.containers.get("local_ai_server")
            running = container.status == "running"
        except:
            running = False
        
        # Try health check
        healthy = False
        if running:
            try:
                ws_url = os.getenv("HEALTH_CHECK_LOCAL_AI_URL", "ws://127.0.0.1:8765")
                async with websockets.connect(ws_url, open_timeout=5) as ws:
                    auth_token = (os.getenv("LOCAL_WS_AUTH_TOKEN", "") or "").strip()
                    if auth_token:
                        await ws.send(json.dumps({"type": "auth", "auth_token": auth_token}))
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        auth_data = json.loads(raw)
                        if auth_data.get("type") != "auth_response" or auth_data.get("status") != "ok":
                            raise RuntimeError(f"Local AI auth failed: {auth_data}")

                    await ws.send(json.dumps({"type": "status"}))
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    data = json.loads(raw)
                    healthy = data.get("type") == "status_response" and data.get("status") == "ok"
            except:
                pass
        
        return {
            "running": running,
            "healthy": healthy
        }
    except Exception as e:
        return {"running": False, "healthy": False, "error": str(e)}


class ModelSwitchRequest(BaseModel):
    stt_backend: Optional[str] = None  # vosk, sherpa, kroko
    stt_model_path: Optional[str] = None
    llm_model_path: Optional[str] = None
    tts_backend: Optional[str] = None  # piper, kokoro
    tts_model_path: Optional[str] = None
    kokoro_voice: Optional[str] = None


@router.post("/local/switch-model")
async def switch_local_model(request: ModelSwitchRequest):
    """Switch models on the running local-ai-server without restart.
    
    Sends a WebSocket message to the local AI server to switch models dynamically.
    Also updates .env for persistence across restarts.
    """
    import websockets
    import json
    from settings import PROJECT_ROOT
    
    # Build the switch request
    switch_data = {"type": "switch_model"}
    env_updates = []
    
    if request.stt_backend:
        switch_data["stt_backend"] = request.stt_backend
        env_updates.append(f"LOCAL_STT_BACKEND={request.stt_backend}")
    
    if request.stt_model_path:
        switch_data["stt_model_path"] = request.stt_model_path
        env_updates.append(f"LOCAL_STT_MODEL_PATH={request.stt_model_path}")
    
    if request.llm_model_path:
        switch_data["llm_model_path"] = request.llm_model_path
        env_updates.append(f"LOCAL_LLM_MODEL_PATH={request.llm_model_path}")
    
    if request.tts_backend:
        switch_data["tts_backend"] = request.tts_backend
        env_updates.append(f"LOCAL_TTS_BACKEND={request.tts_backend}")
    
    if request.tts_model_path:
        switch_data["tts_model_path"] = request.tts_model_path
        env_updates.append(f"LOCAL_TTS_MODEL_PATH={request.tts_model_path}")
    
    if request.kokoro_voice:
        switch_data["kokoro_voice"] = request.kokoro_voice
        env_updates.append(f"KOKORO_VOICE={request.kokoro_voice}")
    
    # Update .env for persistence
    if env_updates:
        try:
            env_path = os.path.join(PROJECT_ROOT, ".env")
            updates_dict = {}
            for update in env_updates:
                if "=" not in update:
                    continue
                k, v = update.split("=", 1)
                updates_dict[k.strip()] = v.strip()

            upsert_env_vars(
                env_path,
                updates_dict,
                header="Model switch from Dashboard",
            )
        except Exception as e:
            return {"success": False, "message": f"Failed to update .env: {e}"}
    
    # Send switch command to local AI server via WebSocket
    try:
        async with websockets.connect("ws://127.0.0.1:8765", ping_interval=None) as ws:
            auth_token = (os.getenv("LOCAL_WS_AUTH_TOKEN", "") or "").strip()
            if auth_token:
                await ws.send(json.dumps({"type": "auth", "auth_token": auth_token}))
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                auth_data = json.loads(raw)
                if auth_data.get("type") != "auth_response" or auth_data.get("status") != "ok":
                    raise RuntimeError(f"Local AI auth failed: {auth_data}")

            await ws.send(json.dumps(switch_data))
            response = await ws.recv()
            result = json.loads(response)
            
            if result.get("status") == "success":
                return {
                    "success": True,
                    "message": result.get("message", "Models switched successfully"),
                    "changed": result.get("changed", []),
                    "env_updated": env_updates
                }
            else:
                return {
                    "success": False,
                    "message": result.get("message", "Switch failed"),
                }
    except Exception as e:
        return {
            "success": False, 
            "message": f"Could not connect to local AI server: {e}. Restart the server for changes to take effect.",
            "env_updated": env_updates
        }


class ApiKeyValidation(BaseModel):
    provider: str
    api_key: str
    agent_id: Optional[str] = None  # Required for ElevenLabs Conversational AI

class AsteriskConnection(BaseModel):
    host: str
    username: str
    password: str
    port: int = 8088
    scheme: str = "http"
    ssl_verify: bool = True  # Set to False for self-signed certs or IP/hostname mismatches
    app: str = "asterisk-ai-voice-agent"

@router.post("/validate-key")
async def validate_api_key(validation: ApiKeyValidation):
    """Validate an API key by testing it against the provider's API"""
    try:
        import httpx
        
        provider = validation.provider.lower()
        api_key = validation.api_key.strip() if validation.api_key else ""
        
        if not api_key:
            return {"valid": False, "error": "API key is empty"}
        
        logger.info(f"Validating {provider} API key (length: {len(api_key)})")
        
        async with httpx.AsyncClient() as client:
            if provider == "openai":
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    return {"valid": True, "message": "OpenAI API key is valid"}
                elif response.status_code == 401:
                    return {"valid": False, "error": "Invalid API key"}
                else:
                    return {"valid": False, "error": f"API error: HTTP {response.status_code}"}

            elif provider == "groq":
                response = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    return {"valid": True, "message": "Groq API key is valid"}
                elif response.status_code == 401:
                    return {"valid": False, "error": "Invalid API key"}
                else:
                    return {"valid": False, "error": f"API error: HTTP {response.status_code}"}
                    
            elif provider == "deepgram":
                response = await client.get(
                    "https://api.deepgram.com/v1/projects",
                    headers={"Authorization": f"Token {api_key}"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    return {"valid": True, "message": "Deepgram API key is valid"}
                elif response.status_code == 401:
                    return {"valid": False, "error": "Invalid API key"}
                else:
                    return {"valid": False, "error": f"API error: HTTP {response.status_code}"}
                    
            elif provider == "google":
                response = await client.get(
                    GOOGLE_MODELS_URL,
                    params={"key": api_key},
                    timeout=10.0
                )
                if response.status_code == 200:
                    # Check if the required model with bidiGenerateContent is available
                    data = response.json()
                    models = data.get("models", [])

                    # Find models that support bidiGenerateContent (required for Live API)
                    live_models = _extract_google_live_models(models)
                    
                    if not live_models:
                        return {
                            "valid": False, 
                            "error": "API key valid but no Live API models available. Your API key doesn't have access to Gemini Live models (bidiGenerateContent). Try creating a new key at aistudio.google.com"
                        }
                    
                    selected_model = _select_google_live_model(live_models)
                    if selected_model:
                        return {
                            "valid": True,
                            "message": f"Google API key is valid. Live model '{selected_model}' is available.",
                            "selected_model": selected_model,
                            "available_models": live_models,
                        }

                    # Defensive fallback (should not happen if live_models is non-empty)
                    return {
                        "valid": True,
                        "message": f"Google API key is valid. Available Live models: {', '.join(live_models[:3])}",
                        "available_models": live_models,
                    }
                elif response.status_code in [400, 403]:
                    return {"valid": False, "error": "Invalid API key"}
                else:
                    return {"valid": False, "error": f"API error: HTTP {response.status_code}"}
            
            elif provider == "elevenlabs":
                # For ElevenLabs Conversational AI, validate using agent endpoint
                # Agent-scoped API keys don't have user_read permission
                agent_id = validation.agent_id
                
                if agent_id:
                    # Validate by fetching agent details (works with agent-scoped keys)
                    response = await client.get(
                        f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}",
                        headers={"xi-api-key": api_key},
                        timeout=10.0
                    )
                    logger.info(f"ElevenLabs agent API response: {response.status_code}")
                    if response.status_code == 200:
                        agent_data = response.json()
                        agent_name = agent_data.get("name", "Unknown")
                        return {"valid": True, "message": f"ElevenLabs API key valid. Agent: {agent_name}"}
                    elif response.status_code == 401:
                        error_detail = response.json().get("detail", {})
                        error_msg = error_detail.get("message", "Invalid API key") if isinstance(error_detail, dict) else "Invalid API key"
                        return {"valid": False, "error": error_msg}
                    elif response.status_code == 404:
                        return {"valid": False, "error": "Agent not found. Check your Agent ID."}
                    else:
                        return {"valid": False, "error": f"API error: HTTP {response.status_code}"}
                else:
                    # Fallback: try user endpoint (for full-access keys)
                    response = await client.get(
                        "https://api.elevenlabs.io/v1/user",
                        headers={"xi-api-key": api_key},
                        timeout=10.0
                    )
                    if response.status_code == 200:
                        return {"valid": True, "message": "ElevenLabs API key is valid"}
                    elif response.status_code == 401:
                        error_detail = response.json().get("detail", {})
                        error_msg = error_detail.get("message", "Invalid API key") if isinstance(error_detail, dict) else "Invalid API key"
                        # Hint about agent_id if it's a permissions issue
                        if "missing_permissions" in str(error_detail):
                            error_msg = "API key valid but agent-scoped. Please provide Agent ID for validation."
                        return {"valid": False, "error": error_msg}
                    else:
                        return {"valid": False, "error": f"API error: HTTP {response.status_code}"}
            
            else:
                return {"valid": False, "error": f"Unknown provider: {provider}"}
                
    except httpx.TimeoutException:
        return {"valid": False, "error": "Connection timeout"}
    except Exception as e:
        return {"valid": False, "error": str(e)}

@router.post("/validate-connection")
async def validate_asterisk_connection(conn: AsteriskConnection):
    """Test Asterisk ARI connection"""
    try:
        import httpx
        
        # Try to connect to ARI interface
        base_url = f"{conn.scheme}://{conn.host}:{conn.port}/ari"
        
        # Configure SSL verification (disable for self-signed certs)
        verify = conn.ssl_verify if conn.scheme == "https" else True
        
        async with httpx.AsyncClient(verify=verify) as client:
            response = await client.get(
                f"{base_url}/asterisk/info",
                auth=(conn.username, conn.password),
                timeout=5.0
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "valid": True,
                    "message": f"Connected to Asterisk {data.get('system', {}).get('version', 'Unknown')}"
                }
            elif response.status_code == 401:
                return {"valid": False, "error": "Invalid username or password"}
            else:
                return {"valid": False, "error": f"Connection failed: HTTP {response.status_code}"}
                
    except httpx.ConnectError as e:
        logger.debug("ARI validation connect error", exc_info=True)
        error_str = str(e).lower()
        
        # Categorize SSL errors more precisely
        if "ssl" in error_str or "certificate" in error_str:
            # Certificate verification errors (only suggest unchecking if verify is ON)
            if conn.ssl_verify and ("certificate verify" in error_str or "certificate_verify" in error_str or "self-signed" in error_str or "hostname" in error_str):
                return {"valid": False, "error": "SSL certificate verification failed. Try unchecking 'Verify SSL Certificate' for self-signed certs or hostname mismatches."}
            # SSL protocol/handshake errors (server may not support HTTPS)
            elif "record layer" in error_str or "handshake" in error_str or "wrong version" in error_str:
                return {"valid": False, "error": f"SSL handshake failed. The server may not support HTTPS on port {conn.port}. Try using 'http' scheme or a different port."}
            # Generic SSL error - show details
            else:
                return {"valid": False, "error": "SSL error - check scheme, port, and certificate settings."}
        
        return {"valid": False, "error": f"Cannot connect to {conn.host}:{conn.port} - is Asterisk running and reachable?"}
    except httpx.TimeoutException:
        return {"valid": False, "error": f"Connection timeout to {conn.host}:{conn.port} - Check if the server is reachable and the port is correct."}
    except Exception as e:
        logger.debug("ARI validation failed", exc_info=True)
        error_str = str(e).lower()
        
        # Categorize SSL errors more precisely
        if "ssl" in error_str or "certificate" in error_str:
            if conn.ssl_verify and ("certificate verify" in error_str or "self-signed" in error_str):
                return {"valid": False, "error": "SSL certificate verification failed. Try unchecking 'Verify SSL Certificate'."}
            elif "record layer" in error_str or "handshake" in error_str:
                return {"valid": False, "error": "SSL handshake failed. The server may not support HTTPS on this port."}
            else:
                return {"valid": False, "error": "SSL error - check scheme, port, and certificate settings."}
        
        return {"valid": False, "error": "Connection failed - check host/port/scheme and credentials."}

@router.get("/status")
async def get_setup_status():
    """
    Check if initial setup has been completed
    Returns configured: true if .env exists with required keys
    """
    try:
        if not os.path.exists(ENV_PATH):
            return {"configured": False, "message": "Environment file not found"}
        
        # Read .env and check for minimal required config
        with open(ENV_PATH, 'r') as f:
            content = f.read()
            has_asterisk_host = "ASTERISK_HOST=" in content
            has_username = "ASTERISK_ARI_USERNAME=" in content
            
            if has_asterisk_host and has_username:
                return {"configured": True, "message": "Setup complete"}
            else:
                return {"configured": False, "message": "Incomplete configuration"}
                
    except Exception as e:
        return {"configured": False, "message": str(e)}

class SetupConfig(BaseModel):
    provider: str = "openai_realtime"
    asterisk_host: str
    asterisk_username: str
    asterisk_password: str
    asterisk_port: int = 8088
    asterisk_scheme: str = "http"
    asterisk_app: str = "asterisk-ai-voice-agent"
    asterisk_server_ip: Optional[str] = None  # Required when asterisk_host is a hostname (for RTP security)
    asterisk_ssl_verify: bool = True  # Set to False to skip SSL certificate verification
    openai_key: Optional[str] = None
    groq_key: Optional[str] = None
    deepgram_key: Optional[str] = None
    google_key: Optional[str] = None
    elevenlabs_key: Optional[str] = None
    elevenlabs_agent_id: Optional[str] = None
    cartesia_key: Optional[str] = None
    greeting: str
    ai_name: str
    ai_role: str
    hybrid_llm_provider: Optional[str] = None
    local_stt_backend: Optional[str] = None
    local_stt_model: Optional[str] = None
    kroko_embedded: Optional[bool] = False
    kroko_api_key: Optional[str] = None
    local_tts_backend: Optional[str] = None
    local_tts_model: Optional[str] = None
    kokoro_mode: Optional[str] = "local"
    kokoro_voice: Optional[str] = "af_heart"
    kokoro_api_key: Optional[str] = None
    kokoro_api_base_url: Optional[str] = None
    local_llm_model: Optional[str] = None
    local_llm_custom_url: Optional[str] = None
    local_llm_custom_filename: Optional[str] = None

# ... (keep existing endpoints) ...

@router.post("/save")
async def save_setup_config(config: SetupConfig):
    """Persist wizard configuration into `.env` and baseline config files."""
    # Validation: Check for required keys based on provider
    if config.provider == "openai_realtime" and not config.openai_key:
            raise HTTPException(status_code=400, detail="OpenAI API Key is required for OpenAI Realtime provider")
    if config.provider == "deepgram":
        if not config.deepgram_key:
            raise HTTPException(status_code=400, detail="Deepgram API Key is required for Deepgram provider")
        if not config.openai_key:
            raise HTTPException(status_code=400, detail="OpenAI API Key is required for Deepgram Think stage")
    if config.provider == "google_live" and not config.google_key:
            raise HTTPException(status_code=400, detail="Google API Key is required for Google Live provider")
    # Local hybrid uses a cloud LLM (Groq/OpenAI) or Ollama
    if config.provider == "local_hybrid":
        llm_provider = (config.hybrid_llm_provider or "groq").lower()
        if llm_provider == "openai" and not config.openai_key:
            raise HTTPException(status_code=400, detail="OpenAI API Key is required for Local Hybrid pipeline when using OpenAI")
        if llm_provider == "groq" and not config.groq_key:
            raise HTTPException(status_code=400, detail="Groq API Key is required for Local Hybrid pipeline when using Groq")
    if config.provider == "elevenlabs_agent":
        if not config.elevenlabs_key:
            raise HTTPException(status_code=400, detail="ElevenLabs API Key is required for ElevenLabs Conversational provider")
        if not config.elevenlabs_agent_id:
            raise HTTPException(status_code=400, detail="ElevenLabs Agent ID is required for ElevenLabs Conversational provider")

    try:
        import shutil
        import datetime
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        # Backup existing files
        if os.path.exists(ENV_PATH):
            shutil.copy2(ENV_PATH, f"{ENV_PATH}.bak.{timestamp}")
            
        if os.path.exists(CONFIG_PATH):
            shutil.copy2(CONFIG_PATH, f"{CONFIG_PATH}.bak.{timestamp}")

        # 1. Update .env
        env_updates = {
            "ASTERISK_HOST": config.asterisk_host,
            "ASTERISK_ARI_USERNAME": config.asterisk_username,
            "ASTERISK_ARI_PASSWORD": config.asterisk_password,
            "ASTERISK_ARI_PORT": str(config.asterisk_port),
            "ASTERISK_ARI_SCHEME": config.asterisk_scheme,
            "ASTERISK_ARI_SSL_VERIFY": "true" if config.asterisk_ssl_verify else "false",
            "AI_NAME": config.ai_name,
            "AI_ROLE": config.ai_role,
            "GREETING": config.greeting,
        }
        
        if config.openai_key:
            env_updates["OPENAI_API_KEY"] = config.openai_key
        if config.groq_key:
            env_updates["GROQ_API_KEY"] = config.groq_key
        if config.deepgram_key:
            env_updates["DEEPGRAM_API_KEY"] = config.deepgram_key
        if config.google_key:
            env_updates["GOOGLE_API_KEY"] = config.google_key
        if config.elevenlabs_key:
            env_updates["ELEVENLABS_API_KEY"] = config.elevenlabs_key
        if config.elevenlabs_agent_id:
            env_updates["ELEVENLABS_AGENT_ID"] = config.elevenlabs_agent_id
        if config.cartesia_key:
            env_updates["CARTESIA_API_KEY"] = config.cartesia_key

        if config.provider in ("local", "local_hybrid"):
            catalog = get_full_catalog()
            stt_by_id = {m.get("id"): m for m in catalog.get("stt", []) if m.get("id")}
            tts_by_id = {m.get("id"): m for m in catalog.get("tts", []) if m.get("id")}
            llm_by_id = {m.get("id"): m for m in catalog.get("llm", []) if m.get("id")}

            stt_model = stt_by_id.get(config.local_stt_model or "")
            tts_model = tts_by_id.get(config.local_tts_model or "")
            llm_model = llm_by_id.get(config.local_llm_model or "")

            stt_backend = (config.local_stt_backend or (stt_model or {}).get("backend") or "").strip().lower()
            tts_backend = (config.local_tts_backend or (tts_model or {}).get("backend") or "").strip().lower()

            if stt_backend:
                env_updates["LOCAL_STT_BACKEND"] = stt_backend
            if tts_backend:
                env_updates["LOCAL_TTS_BACKEND"] = tts_backend
            env_updates["LOCAL_AI_MODE"] = "minimal" if config.provider == "local_hybrid" else "full"

            stt_model_path = (stt_model or {}).get("model_path")
            if stt_backend == "sherpa" and stt_model_path:
                env_updates["SHERPA_MODEL_PATH"] = os.path.join("/app/models/stt", stt_model_path)
            elif stt_backend == "kroko":
                env_updates["KROKO_EMBEDDED"] = "1" if config.kroko_embedded else "0"
                if config.kroko_api_key:
                    env_updates["KROKO_API_KEY"] = config.kroko_api_key
                if config.kroko_embedded and stt_model_path:
                    env_updates["KROKO_MODEL_PATH"] = os.path.join("/app/models/kroko", stt_model_path)
            elif stt_backend == "faster_whisper" and stt_model_path:
                env_updates["FASTER_WHISPER_MODEL"] = stt_model_path
            elif stt_model_path:
                env_updates["LOCAL_STT_MODEL_PATH"] = os.path.join("/app/models/stt", stt_model_path)

            tts_model_path = (tts_model or {}).get("model_path")
            if tts_backend == "kokoro":
                mode = (config.kokoro_mode or "local").strip().lower()
                if mode not in ("local", "api", "hf"):
                    mode = "local"
                env_updates["KOKORO_MODE"] = mode
                env_updates["KOKORO_VOICE"] = (config.kokoro_voice or "af_heart").strip()
                if tts_model_path:
                    env_updates["KOKORO_MODEL_PATH"] = os.path.join("/app/models/tts", tts_model_path)
                if mode == "api":
                    if config.kokoro_api_base_url:
                        env_updates["KOKORO_API_BASE_URL"] = config.kokoro_api_base_url
                    if config.kokoro_api_key:
                        env_updates["KOKORO_API_KEY"] = config.kokoro_api_key
            elif tts_backend == "melotts":
                if tts_model_path:
                    env_updates["MELOTTS_VOICE"] = tts_model_path
            elif tts_model_path:
                env_updates["LOCAL_TTS_MODEL_PATH"] = os.path.join("/app/models/tts", tts_model_path)

            if config.provider == "local":
                if config.local_llm_model == "custom_gguf_url":
                    custom_name = (config.local_llm_custom_filename or "").strip()
                    if custom_name:
                        env_updates["LOCAL_LLM_MODEL_PATH"] = os.path.join("/app/models/llm", custom_name)
                elif llm_model and llm_model.get("model_path"):
                    env_updates["LOCAL_LLM_MODEL_PATH"] = os.path.join("/app/models/llm", llm_model["model_path"])

        upsert_env_vars(ENV_PATH, env_updates, header="Setup Wizard")

        # 2. Update ai-agent.yaml - APPEND MODE
        # If provider already exists, just enable it and update greeting
        # If provider doesn't exist, create full config
        # Don't auto-disable other providers (user manages via Dashboard)
        # Read merged config (base + local override) so wizard sees operator changes.
        compute_local_override_fn = None
        try:
            from api.config import _read_merged_config_dict, _read_base_config_dict, _compute_local_override
            yaml_config = _read_merged_config_dict()
            base_config = _read_base_config_dict()
            compute_local_override_fn = _compute_local_override
        except Exception as exc:
            logger.warning("Wizard config helper import failed; using fallback override diff: %s", exc)
            yaml_config = None
            base_config = None
        if not yaml_config and os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                yaml_config = yaml.safe_load(f)
        if yaml_config is not None:
            pre_edit_config = copy.deepcopy(yaml_config) if isinstance(yaml_config, dict) else {}
            
            yaml_config.setdefault("providers", {})
            providers = yaml_config["providers"]
            
            # Helper to check if provider already exists with config
            def provider_exists(name: str) -> bool:
                return name in providers and len(providers[name]) > 1  # More than just 'enabled'

            selected_google_live_model = GOOGLE_LIVE_DEFAULT_MODEL
            if config.provider == "google_live" and config.google_key:
                discovered_model = await _discover_google_live_model(config.google_key)
                if discovered_model:
                    selected_google_live_model = discovered_model
                    logger.info("Resolved Google Live model for setup: %s", selected_google_live_model)
                else:
                    logger.warning(
                        "Could not resolve Google Live model during setup; falling back to default: %s",
                        selected_google_live_model,
                    )
            
            # Full agent providers - clear active_pipeline when setting as default
            if config.provider in ["openai_realtime", "deepgram", "google_live", "elevenlabs_agent", "local"]:
                yaml_config["default_provider"] = config.provider
                yaml_config["active_pipeline"] = None  # Full agents don't use pipelines
            
            if config.provider == "openai_realtime":
                providers.setdefault("openai_realtime", {})["enabled"] = True
                # Only set full config if provider doesn't exist yet
                if not provider_exists("openai_realtime"):
                    providers["openai_realtime"].update({
                        "api_version": "beta",
                        "model": "gpt-4o-realtime-preview-2024-12-17",
                        "voice": "alloy",
                        "input_encoding": "ulaw",
                        "input_sample_rate_hz": 8000,
                        "target_encoding": "mulaw",
                        "target_sample_rate_hz": 8000,
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "silence_duration_ms": 1000,
                            "prefix_padding_ms": 300,
                            "create_response": True
                        }
                    })
                # Always update greeting and instructions
                providers["openai_realtime"]["greeting"] = config.greeting
                providers["openai_realtime"]["instructions"] = f"You are {config.ai_name}, a {config.ai_role}. Be helpful and concise. Always speak your responses out loud."
                
            elif config.provider == "deepgram":
                providers.setdefault("deepgram", {})["enabled"] = True
                if not provider_exists("deepgram"):
                    providers["deepgram"].update({
                        "model": "nova-2-general",
                        "tts_model": "aura-asteria-en",
                        "input_encoding": "mulaw",
                        "input_sample_rate_hz": 8000,
                        "output_encoding": "mulaw",
                        "output_sample_rate_hz": 8000
                    })
                providers["deepgram"]["greeting"] = config.greeting
                providers["deepgram"]["instructions"] = f"You are {config.ai_name}, a {config.ai_role}. Be helpful and concise."
                
            elif config.provider == "google_live":
                providers.setdefault("google_live", {})["enabled"] = True
                if not provider_exists("google_live"):
                    providers["google_live"].update({
                        "api_key": "${GOOGLE_API_KEY}",
                        "llm_model": selected_google_live_model,
                        "input_encoding": "ulaw",
                        "input_sample_rate_hz": 8000,
                        "provider_input_encoding": "linear16",
                        "provider_input_sample_rate_hz": 16000,
                        "target_encoding": "ulaw",
                        "target_sample_rate_hz": 8000,
                        "response_modalities": "audio",
                        "type": "full",
                        "capabilities": ["stt", "llm", "tts"]
                    })
                providers["google_live"]["llm_model"] = selected_google_live_model
                providers["google_live"]["greeting"] = config.greeting
                providers["google_live"]["instructions"] = f"You are {config.ai_name}, a {config.ai_role}. Be helpful and concise."

            elif config.provider == "elevenlabs_agent":
                providers.setdefault("elevenlabs_agent", {})["enabled"] = True
                if not provider_exists("elevenlabs_agent"):
                    providers["elevenlabs_agent"].update({
                        "api_key": "${ELEVENLABS_API_KEY}",
                        "agent_id": "${ELEVENLABS_AGENT_ID}",
                        "type": "full",
                        "capabilities": ["stt", "llm", "tts"],
                        "input_encoding": "ulaw",
                        "input_sample_rate_hz": 8000,
                        "target_encoding": "ulaw",
                        "target_sample_rate_hz": 8000
                    })

            elif config.provider == "local":
                providers.setdefault("local", {})["enabled"] = True
                if not provider_exists("local"):
                    providers["local"].update({
                        "type": "full",
                        "capabilities": ["stt", "llm", "tts"],
                        "base_url": "${LOCAL_WS_URL:-ws://127.0.0.1:8765}",
                        "connect_timeout_sec": 2.0,
                        "response_timeout_sec": 10.0,
                        "chunk_ms": 320
                    })
                providers["local"]["greeting"] = config.greeting
                providers["local"]["instructions"] = f"You are {config.ai_name}, a {config.ai_role}. Be helpful and concise."

            elif config.provider == "local_hybrid":
                # local_hybrid is a PIPELINE (Local STT + Cloud/Local LLM + Local TTS)
                yaml_config["active_pipeline"] = "local_hybrid"
                yaml_config["default_provider"] = "local"  # Fallback provider
                
                # Configure local provider
                providers.setdefault("local", {})["enabled"] = True
                if not provider_exists("local"):
                    providers["local"].update({
                        "type": "full",
                        "capabilities": ["stt", "llm", "tts"],
                        "base_url": "${LOCAL_WS_URL:-ws://127.0.0.1:8765}",
                        "connect_timeout_sec": 2.0,
                        "response_timeout_sec": 10.0,
                        "chunk_ms": 320
                    })
                
                # Configure pipeline components
                providers.setdefault("local_stt", {})["enabled"] = True
                if not provider_exists("local_stt"):
                    providers["local_stt"].update({
                        "ws_url": "${LOCAL_WS_URL:-ws://127.0.0.1:8765}",
                        "stt_backend": "vosk"
                    })
                
                providers.setdefault("local_tts", {})["enabled"] = True
                if not provider_exists("local_tts"):
                    providers["local_tts"]["ws_url"] = "${LOCAL_WS_URL:-ws://127.0.0.1:8765}"
                
                llm_provider = (config.hybrid_llm_provider or "groq").lower()
                if llm_provider == "openai":
                    providers.setdefault("openai_llm", {})["enabled"] = True
                    if not provider_exists("openai_llm"):
                        providers["openai_llm"].update({
                            "api_key": "${OPENAI_API_KEY}",
                            "chat_base_url": "https://api.openai.com/v1",
                            "chat_model": "gpt-4o-mini",
                            "type": "openai",
                            "capabilities": ["llm"],
                        })
                elif llm_provider == "groq":
                    providers.setdefault("groq_llm", {})["enabled"] = True
                    if not provider_exists("groq_llm"):
                        providers["groq_llm"].update({
                            "api_key": "${GROQ_API_KEY}",
                            "chat_base_url": "https://api.groq.com/openai/v1",
                            "chat_model": "llama-3.3-70b-versatile",
                            "tools_enabled": False,
                            "type": "openai",
                            "capabilities": ["llm"],
                        })
                elif llm_provider == "ollama":
                    providers.setdefault("ollama_llm", {})["enabled"] = True
                    if not provider_exists("ollama_llm"):
                        providers["ollama_llm"].update({
                            "base_url": "http://localhost:11434",
                            "model": "llama3.2",
                            "temperature": 0.7,
                            "max_tokens": 200,
                            "timeout_sec": 60,
                            "tools_enabled": True,
                            "type": "ollama",
                            "capabilities": ["llm"],
                        })
                
                # Define the pipeline
                llm_component = "openai_llm"
                if llm_provider == "groq":
                    llm_component = "groq_llm"
                elif llm_provider == "ollama":
                    llm_component = "ollama_llm"

                yaml_config.setdefault("pipelines", {})["local_hybrid"] = {
                    "stt": "local_stt",
                    "llm": llm_component,
                    "tts": "local_tts"
                }

            # C6 Fix: Create default context
            default_context = {
                "greeting": config.greeting,
                "prompt": f"You are {config.ai_name}, a {config.ai_role}. Be helpful and concise.",
                "provider": config.provider if config.provider != "local_hybrid" else "local",
                "profile": "telephony_ulaw_8k"
            }
            if config.provider == "local_hybrid":
                default_context["pipeline"] = "local_hybrid"
            yaml_config.setdefault("contexts", {})["default"] = default_context

            # Canonical: ARI application name is YAML-owned (asterisk.app_name).
            asterisk_block = yaml_config.get("asterisk")
            if not isinstance(asterisk_block, dict):
                asterisk_block = {}
                yaml_config["asterisk"] = asterisk_block
            asterisk_block["app_name"] = config.asterisk_app
            # Set allowed_remote_hosts when using hostname (for RTP security)
            if config.asterisk_server_ip:
                yaml_config.setdefault("external_media", {})["allowed_remote_hosts"] = [config.asterisk_server_ip]

            local_override = None
            if compute_local_override_fn and isinstance(base_config, dict):
                try:
                    local_override = compute_local_override_fn(base_config, yaml_config)
                except Exception as exc:
                    logger.warning(
                        "Wizard override diff failed for %s (base_config_present=%s): %s",
                        LOCAL_CONFIG_PATH,
                        isinstance(base_config, dict),
                        exc,
                    )

            if not isinstance(local_override, dict):
                fallback_base = pre_edit_config if isinstance(pre_edit_config, dict) else {}
                try:
                    fallback_override = _compute_local_override_fallback(fallback_base, yaml_config)
                    local_override = fallback_override if isinstance(fallback_override, dict) else {}
                except Exception as exc:
                    logger.warning(
                        "Wizard fallback override diff failed for %s (base_config_present=%s): %s",
                        LOCAL_CONFIG_PATH,
                        isinstance(base_config, dict),
                        exc,
                    )
                    local_override = {}

            atomic_write_text(
                LOCAL_CONFIG_PATH,
                yaml.dump(local_override, default_flow_style=False, sort_keys=False),
                mode_from_existing=True,
            )
        
        # Config saved - engine start will be handled by completion step UI
        return {"status": "success", "provider": config.provider}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/skip")
async def skip_setup():
    """
    Skip the setup wizard by creating a minimal .env file
    This allows advanced users to configure manually
    """
    try:
        # Create minimal .env with a marker that setup was acknowledged
        if not os.path.exists(ENV_PATH):
            atomic_write_text(
                ENV_PATH,
                (
                    "# Setup wizard skipped - configure manually\n"
                    "ASTERISK_HOST=127.0.0.1\n"
                    "ASTERISK_ARI_USERNAME=asterisk\n"
                    "ASTERISK_ARI_PASSWORD=\n"
                ),
                mode_from_existing=True,
            )
        
        return {"status": "success", "message": "Setup skipped successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
