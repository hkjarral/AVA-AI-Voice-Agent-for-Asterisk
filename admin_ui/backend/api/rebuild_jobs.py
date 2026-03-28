"""
Backend Rebuild Job Management

Handles Docker container rebuilds with progress tracking, error capture, and rollback.
Similar pattern to download jobs in wizard.py.
"""

import copy
import logging
import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

# Build time estimates (seconds) based on backend type
BUILD_TIME_ESTIMATES = {
    "faster_whisper": 180,  # ~3 min
    "whisper_cpp": 240,     # ~4 min
    "tone": 300,            # ~5 min (kenlm/pyctcdecode install)
    "melotts": 300,         # ~5 min
    "kroko_embedded": 120,  # ~2 min
    "vosk": 60,             # ~1 min
    "silero": 180,          # ~3 min (torch + model cache)
    "default": 180,         # ~3 min fallback
}

# Backend to build arg mapping
BACKEND_BUILD_ARGS = {
    "faster_whisper": "INCLUDE_FASTER_WHISPER",
    "whisper_cpp": "INCLUDE_WHISPER_CPP",
    "tone": "INCLUDE_TONE",
    "melotts": "INCLUDE_MELOTTS",
    "kroko_embedded": "INCLUDE_KROKO_EMBEDDED",
    "vosk": "INCLUDE_VOSK",
    "llama": "INCLUDE_LLAMA",
    "piper": "INCLUDE_PIPER",
    "kokoro": "INCLUDE_KOKORO",
    "sherpa": "INCLUDE_SHERPA",
    "silero": "INCLUDE_SILERO",
}

# Defaults used when keys are not present in `.env`.
# These follow the defaults embedded in docker-compose.yml / docker-compose.gpu.yml.
_DEFAULT_INCLUDE_BASE: Dict[str, bool] = {
    "faster_whisper": False,
    "whisper_cpp": False,
    "tone": False,
    "melotts": False,
    "kroko_embedded": False,
    "vosk": True,
    "llama": True,
    "piper": True,
    "kokoro": True,
    "sherpa": True,
    "silero": False,
}

_DEFAULT_INCLUDE_GPU: Dict[str, bool] = {
    **_DEFAULT_INCLUDE_BASE,
    # On GPU builds, Whisper backends default on (docker-compose.gpu.yml).
    "faster_whisper": True,
    "whisper_cpp": True,
    "tone": False,
}


def _is_truthy(value: Optional[str]) -> bool:
    raw = (value or "").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _read_env_file(env_path: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not os.path.exists(env_path):
        return values
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if "=" not in line:
                    continue
                if line.strip().startswith("#"):
                    continue
                key, _, value = line.partition("=")
                k = (key or "").strip()
                if not k:
                    continue
                v = (value or "").strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                    v = v[1:-1]
                values[k] = v
    except Exception:
        return values
    return values


def _env_enabled_defaults() -> Dict[str, bool]:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    env = _read_env_file(env_path)
    gpu_available = _is_truthy(env.get("GPU_AVAILABLE"))
    if not gpu_available:
        return _DEFAULT_INCLUDE_BASE
    gpu_compose_path = os.path.join(PROJECT_ROOT, "docker-compose.gpu.yml")
    return _DEFAULT_INCLUDE_GPU if os.path.exists(gpu_compose_path) else _DEFAULT_INCLUDE_BASE


def _backup_env_file() -> Optional[str]:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return None
    backup_path = os.path.join(PROJECT_ROOT, f".env.backup.{int(time.time())}")
    try:
        shutil.copy2(env_path, backup_path)
        return backup_path
    except Exception as e:
        logger.error("Failed to backup .env: %s", e)
        return None


def _restore_env_backup(backup_path: str) -> bool:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    try:
        if backup_path and os.path.exists(backup_path):
            shutil.copy2(backup_path, env_path)
            os.remove(backup_path)
            return True
        return False
    except Exception as e:
        logger.error("Failed to restore .env backup: %s", e)
        return False


def _upsert_env(updates: Dict[str, str]) -> bool:
    try:
        from services.fs import upsert_env_vars

        env_path = os.path.join(PROJECT_ROOT, ".env")
        if not os.path.exists(env_path):
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("# Auto-created by Admin UI (backend enable)\n")
        upsert_env_vars(env_path, updates, header="Local AI backend enable")
        return True
    except Exception as e:
        logger.error("Failed to update .env: %s", e)
        return False


def _build_args_for_backend(backend: str, env: Dict[str, str]) -> List[str]:
    """
    Extra build args for `docker compose build`.

    We still pass the backend INCLUDE_* arg explicitly (defense-in-depth) so CPU builds
    that don't reference a given build arg in docker-compose.yml still receive it.
    """
    args: List[str] = []
    arg_name = BACKEND_BUILD_ARGS.get(backend)
    if arg_name:
        args.extend(["--build-arg", f"{arg_name}=true"])

    if backend == "kroko_embedded":
        sha = (env.get("KROKO_SERVER_SHA256") or "").strip()
        if sha:
            args.extend(["--build-arg", f"KROKO_SERVER_SHA256={sha}"])
        onnx_sha = (env.get("ONNX_RUNTIME_SHA256") or "").strip()
        if onnx_sha:
            args.extend(["--build-arg", f"ONNX_RUNTIME_SHA256={onnx_sha}"])
    return args


@dataclass
class RebuildJob:
    """Tracks a container rebuild operation."""
    id: str
    backend: str
    service: str = "local_ai_server"
    created_at: float = field(default_factory=time.time)
    running: bool = True
    completed: bool = False
    error: Optional[str] = None
    rolled_back: bool = False
    output: List[str] = field(default_factory=list)
    progress: Dict[str, Any] = field(
        default_factory=lambda: {
            "phase": "pending",  # pending, backup, updating, building, restarting, verifying, done, error
            "percent": 0,
            "estimated_seconds": 0,
            "elapsed_seconds": 0,
            "start_time": None,
            "message": "",
        }
    )


_rebuild_jobs: Dict[str, RebuildJob] = {}
_rebuild_jobs_lock = threading.Lock()
_latest_rebuild_job_id: Optional[str] = None
_active_rebuild: bool = False  # Only one rebuild at a time


def get_enabled_backends() -> Dict[str, bool]:
    """
    Return which backends are configured as enabled via `.env` INCLUDE_* flags.

    This matches how docker-compose.yml parameterizes build args, and avoids relying
    on docker-compose.override.yml (which can cause host path resolution issues when
    compose is executed inside the admin_ui container).
    """
    defaults = _env_enabled_defaults()
    env_path = os.path.join(PROJECT_ROOT, ".env")
    env = _read_env_file(env_path)

    enabled: Dict[str, bool] = {}
    for backend, arg_name in BACKEND_BUILD_ARGS.items():
        raw = env.get(arg_name)
        if raw is None:
            enabled[backend] = bool(defaults.get(backend, False))
        else:
            enabled[backend] = _is_truthy(raw)
    return enabled


def _create_rebuild_job(backend: str) -> RebuildJob:
    """Create and register a new rebuild job."""
    with _rebuild_jobs_lock:
        return _create_rebuild_job_locked(backend)


def _create_rebuild_job_locked(backend: str) -> RebuildJob:
    """
    Create and register a new rebuild job.

    Caller must hold `_rebuild_jobs_lock`.
    """
    global _latest_rebuild_job_id
    job_id = str(uuid.uuid4())
    job = RebuildJob(id=job_id, backend=backend)
    job.progress["start_time"] = time.time()
    job.progress["estimated_seconds"] = BUILD_TIME_ESTIMATES.get(backend, BUILD_TIME_ESTIMATES["default"])
    job.progress["message"] = f"Starting {backend} backend installation..."
    
    _rebuild_jobs[job_id] = job
    _latest_rebuild_job_id = job_id
    # Keep only last 10 jobs
    if len(_rebuild_jobs) > 10:
        oldest = sorted(_rebuild_jobs.values(), key=lambda j: j.created_at)[:-10]
        for j in oldest:
            _rebuild_jobs.pop(j.id, None)
    return job


def get_rebuild_job(job_id: Optional[str] = None) -> Optional[RebuildJob]:
    """Return the requested job, or the most recent job if job_id is None."""
    with _rebuild_jobs_lock:
        job: Optional[RebuildJob] = None
        if job_id:
            job = _rebuild_jobs.get(job_id)
        elif _latest_rebuild_job_id:
            job = _rebuild_jobs.get(_latest_rebuild_job_id)
        return copy.deepcopy(job) if job else None


def _job_output(job_id: str, line: str) -> None:
    """Append a log line to a rebuild job."""
    with _rebuild_jobs_lock:
        job = _rebuild_jobs.get(job_id)
        if not job:
            return
        job.output.append(str(line))
        if len(job.output) > 500:
            job.output = job.output[-500:]


def _job_set_progress(job_id: str, **updates: Any) -> None:
    """Update progress fields for an in-flight rebuild job."""
    with _rebuild_jobs_lock:
        job = _rebuild_jobs.get(job_id)
        if not job:
            return
        job.progress.update(updates)
        if job.progress.get("start_time"):
            job.progress["elapsed_seconds"] = time.time() - job.progress["start_time"]


def _job_finish(job_id: str, *, completed: bool, error: Optional[str] = None, rolled_back: bool = False) -> None:
    """Mark a rebuild job as finished."""
    global _active_rebuild
    with _rebuild_jobs_lock:
        job = _rebuild_jobs.get(job_id)
        if not job:
            return
        job.running = False
        job.completed = bool(completed)
        job.error = error
        job.rolled_back = rolled_back
        job.progress["phase"] = "done" if completed else "error"
        job.progress["percent"] = 100 if completed else job.progress.get("percent", 0)
        _active_rebuild = False


def is_rebuild_in_progress() -> bool:
    """Check if a rebuild is currently in progress."""
    return _active_rebuild

def _run_docker_build(job_id: str, service: str = "local_ai_server") -> bool:
    """
    Run docker compose build via updater-runner so relative host binds resolve correctly.

    Output is captured (not truly streamed) because builds run in an ephemeral helper container.
    """
    try:
        from api.system import (
            _compose_files_flags_for_service,
            _project_host_root_from_admin_ui_container,
            _run_updater_ephemeral,
        )

        host_root = _project_host_root_from_admin_ui_container()
        compose_files = _compose_files_flags_for_service(service)
        compose_prefix = f"{compose_files} " if compose_files else ""

        job = get_rebuild_job(job_id)
        backend = (job.backend if job else "").strip().lower()
        env_path = os.path.join(PROJECT_ROOT, ".env")
        env = _read_env_file(env_path)
        build_args = _build_args_for_backend(backend, env)
        build_args_str = " ".join(shlex.quote(arg) for arg in build_args)
        service_arg = shlex.quote(service)

        cmd = (
            "set -euo pipefail; "
            "cd \"$PROJECT_ROOT\"; "
            f"docker compose {compose_prefix}-p asterisk-ai-voice-agent build --no-cache {build_args_str} {service_arg}"
        )
        _job_output(job_id, f"$ {cmd}")

        started = time.time()
        hb_stop = threading.Event()

        def _hb() -> None:
            while not hb_stop.is_set():
                time.sleep(10)
                if hb_stop.is_set():
                    return
                elapsed = int(time.time() - started)
                _job_set_progress(job_id, message=f"Building... ({elapsed}s elapsed)")

        hb_thread = threading.Thread(target=_hb, daemon=True)
        hb_thread.start()
        try:
            code, out = _run_updater_ephemeral(
                host_root,
                env={"PROJECT_ROOT": host_root},
                command=cmd,
                timeout_sec=1800,
            )
        finally:
            hb_stop.set()

        for line in (out or "").splitlines():
            if line.strip():
                _job_output(job_id, line.rstrip())
        return code == 0
    except Exception as e:
        _job_output(job_id, f"Build error: {e}")
        return False


def _run_docker_up(job_id: str, service: str = "local_ai_server") -> bool:
    """Force-recreate the service so `.env` changes apply."""
    try:
        from api.system import _recreate_via_compose

        import asyncio

        async def _run() -> Dict[str, Any]:
            return await _recreate_via_compose(service, health_check=True)

        recreated = asyncio.run(_run())
        _job_output(
            job_id,
            f"recreate: {recreated.get('status')} (health={recreated.get('health_status', 'n/a')})",
        )
        return recreated.get("status") in {"success", "degraded"}
    except Exception as e:
        _job_output(job_id, f"Restart error: {e}")
        return False


def _verify_backend_loaded(job_id: str, backend: str, timeout: int = 60) -> bool:
    """Wait for container to be healthy and verify backend is available."""
    _job_output(job_id, "Waiting for local_ai_server to be healthy...")
    
    start = time.time()
    while time.time() - start < timeout:
        try:
            # Check container health
            docker_bin = shutil.which("docker") or "docker"
            result = subprocess.run(
                [docker_bin, "inspect", "--format", "{{.State.Health.Status}}", "local_ai_server"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                _job_output(job_id, f"Health probe inspect failed: {(result.stderr or '').strip()}")
                time.sleep(3)
                continue
            status = result.stdout.strip()
            
            if status == "healthy":
                _job_output(job_id, "Container is healthy, checking backend availability...")

                try:
                    import asyncio
                    import json
                    import os
                    import websockets

                    async def _probe() -> Dict[str, Any]:
                        env = _read_env_file(os.path.join(PROJECT_ROOT, ".env"))
                        port = (env.get("LOCAL_WS_PORT") or "8765").strip() or "8765"
                        token = (env.get("LOCAL_WS_AUTH_TOKEN") or "").strip()
                        url = f"ws://127.0.0.1:{port}"
                        async with websockets.connect(url, open_timeout=5, max_size=None) as ws:
                            if token:
                                await ws.send(json.dumps({"type": "auth", "auth_token": token}))
                                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                                msg = json.loads(raw)
                                if msg.get("type") != "auth_response" or msg.get("status") != "ok":
                                    raise RuntimeError(f"auth_failed: {msg}")

                            await ws.send(json.dumps({"type": "capabilities"}))
                            raw = await asyncio.wait_for(ws.recv(), timeout=5)
                            msg = json.loads(raw)
                            if msg.get("type") != "capabilities_response":
                                raise RuntimeError(f"unexpected: {msg}")
                            return msg.get("capabilities", {}) or {}

                    caps = asyncio.run(_probe())
                    cap_key = backend
                    if backend == "kroko":
                        cap_key = "kroko_embedded"
                    if caps.get(cap_key):
                        _job_output(job_id, f"✅ Backend '{cap_key}' is now available!")
                        return True
                    available = sorted([k for k, v in (caps or {}).items() if v])
                    _job_output(job_id, f"Backend '{cap_key}' not available yet (available={available})")
                except Exception as e:
                    _job_output(job_id, f"Capabilities probe failed: {e}")
            
            time.sleep(3)
        except subprocess.TimeoutExpired:
            _job_output(job_id, "Health probe timed out; retrying...")
            time.sleep(3)
        except Exception as e:
            _job_output(job_id, f"Health check error: {e}")
            time.sleep(3)
    
    _job_output(job_id, "⚠️ Timeout waiting for backend verification")
    return False


def start_rebuild_job(backend: str) -> Dict[str, Any]:
    """Start a rebuild job for a backend. Returns job info."""
    global _active_rebuild
    
    # Validate backend (stateless check, safe outside lock)
    if backend not in BACKEND_BUILD_ARGS:
        return {"error": f"Unknown backend: {backend}"}
    
    # Check if already enabled (stateless check, safe outside lock)
    enabled = get_enabled_backends()
    if enabled.get(backend):
        return {"error": f"Backend '{backend}' is already enabled", "already_enabled": True}
    
    # Atomic check-and-set under lock to prevent concurrent rebuild race condition
    with _rebuild_jobs_lock:
        if _active_rebuild:
            return {"error": "A rebuild is already in progress", "job_id": _latest_rebuild_job_id}
        _active_rebuild = True
        job = _create_rebuild_job_locked(backend)
    
    # Start rebuild in background thread (outside lock)
    try:
        thread = threading.Thread(target=_rebuild_worker, args=(job.id, backend), daemon=True)
        thread.start()
    except Exception as e:
        _job_output(job.id, f"❌ ERROR: failed to start rebuild worker: {e}")
        _job_finish(job.id, completed=False, error=f"Failed to start rebuild worker: {e}")
        return {"error": "Failed to start rebuild worker"}
    
    return {
        "job_id": job.id,
        "backend": backend,
        "estimated_seconds": job.progress["estimated_seconds"],
        "message": f"Starting {backend} backend installation...",
    }


def _rebuild_worker(job_id: str, backend: str) -> None:
    """Background worker that performs the rebuild."""
    backup_path = None
    env_path = os.path.join(PROJECT_ROOT, ".env")
    env_preexisting = os.path.exists(env_path)
    
    try:
        # Phase 1: Backup
        _job_set_progress(job_id, phase="backup", percent=5, message="Creating backup...")
        _job_output(job_id, "Creating backup of .env...")
        backup_path = _backup_env_file()
        if env_preexisting and not backup_path:
            raise RuntimeError("Failed to back up existing .env; aborting rebuild")
        if backup_path:
            _job_output(job_id, f"Backup created: {backup_path}")
        
        # Phase 2: Update config
        _job_set_progress(job_id, phase="updating", percent=10, message="Updating build configuration...")
        _job_output(job_id, f"Setting {BACKEND_BUILD_ARGS[backend]}=true in .env...")
        if not _upsert_env({BACKEND_BUILD_ARGS[backend]: "true"}):
            raise Exception("Failed to update .env")
        _job_output(job_id, "Build configuration updated successfully (.env)")
        
        # Phase 3: Build
        _job_set_progress(job_id, phase="building", percent=15, message="Building container (this may take several minutes)...")
        _job_output(job_id, "Starting Docker build...")
        
        if not _run_docker_build(job_id):
            raise Exception("Docker build failed")
        
        _job_set_progress(job_id, percent=80, message="Build completed, restarting service...")
        
        # Phase 4: Restart
        _job_set_progress(job_id, phase="restarting", percent=85, message="Restarting local_ai_server...")
        _job_output(job_id, "Restarting container...")
        
        if not _run_docker_up(job_id):
            raise Exception("Failed to restart container")
        
        # Phase 5: Verify
        _job_set_progress(job_id, phase="verifying", percent=90, message="Verifying backend availability...")
        
        if not _verify_backend_loaded(job_id, backend):
            _job_output(job_id, "⚠️ Backend verification timed out, but build completed successfully")
        
        # Success
        _job_set_progress(job_id, phase="done", percent=100, message=f"✅ {backend} backend installed successfully!")
        _job_output(job_id, f"✅ {backend} backend installation complete!")
        
        # Cleanup backup
        if backup_path and os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError as e:
                _job_output(job_id, f"⚠️ Backup cleanup failed: {e}")
        
        _job_finish(job_id, completed=True)
        
    except Exception as e:
        error_msg = str(e)
        _job_output(job_id, f"❌ ERROR: {error_msg}")
        _job_set_progress(job_id, phase="error", message=f"Failed: {error_msg}")
        
        # Rollback
        rolled_back = False
        if backup_path:
            _job_output(job_id, "Rolling back configuration...")
            if _restore_env_backup(backup_path):
                _job_output(job_id, "Configuration restored from backup")
                _job_output(job_id, "Re-applying restored configuration...")
                if _run_docker_up(job_id):
                    rolled_back = True
                else:
                    _job_output(job_id, "⚠️ Rollback config restored, but service recreate failed")
            else:
                _job_output(job_id, "⚠️ Failed to restore backup - manual intervention may be needed")
        elif not env_preexisting:
            try:
                if os.path.exists(env_path):
                    os.remove(env_path)
                    _job_output(job_id, "Removed auto-created .env after failed rebuild")
            except OSError as cleanup_err:
                _job_output(job_id, f"⚠️ Failed to remove auto-created .env: {cleanup_err}")
        
        _job_finish(job_id, completed=False, error=error_msg, rolled_back=rolled_back)
