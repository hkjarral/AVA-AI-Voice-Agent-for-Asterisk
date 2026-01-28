from fastapi import APIRouter, HTTPException
import docker
from typing import List, Optional
from pydantic import BaseModel
import psutil
import os
import shutil
import logging
import re
import subprocess
import uuid
import yaml
from services.fs import upsert_env_vars

logger = logging.getLogger(__name__)


def _validate_git_ref(ref: str) -> str:
    """
    Basic defense-in-depth: reject values that could be interpreted by git as options.

    We intentionally keep this permissive (allow typical branch names with `/._-`) and
    rely on `git` to enforce full refname rules.
    """
    r = (ref or "").strip()
    if not r or r.startswith("-") or any(c.isspace() for c in r):
        raise HTTPException(status_code=400, detail="Invalid ref")
    return r


def _extract_mounts(container) -> List[dict]:
    """
    Normalize Docker mount info into a stable, UI-friendly shape.
    Returns a list of dicts with snake_case keys.
    """
    mounts: List[dict] = []
    try:
        raw_mounts = container.attrs.get("Mounts", []) or []
        for m in raw_mounts:
            mounts.append(
                {
                    "type": m.get("Type"),
                    "source": m.get("Source"),
                    "destination": m.get("Destination"),
                    "rw": m.get("RW"),
                    "mode": m.get("Mode"),
                    "propagation": m.get("Propagation"),
                    "name": m.get("Name"),
                    "driver": m.get("Driver"),
                }
            )
    except Exception as e:
        logger.debug("Error extracting mounts for %s: %s", getattr(container, "name", "<unknown>"), e)
    return mounts


def _dotenv_value(key: str) -> Optional[str]:
    """
    Read a key from the project's `.env` file (not the current process environment).

    This is used for diagnostics and Tier-3 friendliness where users edit `.env` directly.
    Note: Many settings are loaded into the container environment at *container creation time*,
    so relying on os.environ alone can appear "stale" after editing `.env` without recreating.
    """
    try:
        from settings import ENV_PATH
        if not os.path.exists(ENV_PATH):
            return None
        from dotenv import dotenv_values
        raw = dotenv_values(ENV_PATH)
        val = raw.get(key)
        if val is None:
            return None
        return str(val).strip()
    except Exception:
        return None


def _sanitize_for_log(value: str) -> str:
    """Best-effort: prevent log injection via control characters."""
    try:
        return (value or "").replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    except Exception:
        return "<unprintable>"


def _is_safe_container_identifier(value: str) -> bool:
    """
    Accept only Docker-like container identifiers (defense-in-depth).
    This avoids passing arbitrary user input into logs or subprocess calls.
    """
    import re

    if not value:
        return False
    # Disallow leading '-' to avoid option-like values when used as CLI args.
    if value.startswith("-"):
        return False
    # Docker container names are typically [a-zA-Z0-9][a-zA-Z0-9_.-]*
    return re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$", value) is not None


def get_docker_compose_cmd() -> List[str]:
    """
    Find docker-compose binary dynamically.
    Returns the command as a list (either ['docker-compose'] or ['docker', 'compose']).
    """
    # Try docker-compose standalone first
    compose_path = shutil.which('docker-compose')
    if compose_path:
        return [compose_path]
    
    # Try docker compose (v2 plugin)
    docker_path = shutil.which('docker')
    if docker_path:
        # Verify 'docker compose' works
        import subprocess
        try:
            result = subprocess.run(
                [docker_path, 'compose', 'version'],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                return [docker_path, 'compose']
        except:
            pass
    
    # Fallback to hardcoded paths for backwards compatibility
    if os.path.exists('/usr/local/bin/docker-compose'):
        return ['/usr/local/bin/docker-compose']
    if os.path.exists('/usr/bin/docker-compose'):
        return ['/usr/bin/docker-compose']
    
    raise FileNotFoundError('docker-compose not found in PATH or standard locations')

router = APIRouter()

class ContainerInfo(BaseModel):
    id: str
    name: str
    status: str
    state: str

@router.get("/containers")
async def get_containers():
    try:
        from datetime import datetime, timezone
        
        client = docker.from_env()
        containers = client.containers.list(all=True)
        result = []
        for c in containers:
            # Get image name
            image_name = c.image.tags[0] if c.image.tags else c.image.short_id
            
            # Calculate uptime from StartedAt
            uptime = None
            started_at = None
            if c.status == "running":
                try:
                    started_str = c.attrs['State'].get('StartedAt', '')
                    if started_str and started_str != '0001-01-01T00:00:00Z':
                        # Docker uses nanoseconds (9 digits), Python only handles microseconds (6)
                        # Truncate nanoseconds to microseconds and normalize timezone
                        import re
                        # Match: 2025-12-03T06:23:45.362413338+00:00 or 2025-12-03T06:23:45.362413338Z
                        match = re.match(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)(Z|[+-]\d{2}:\d{2})?', started_str)
                        if match:
                            base = match.group(1)
                            frac = match.group(2)[:6].ljust(6, '0')  # Truncate to 6 digits
                            tz = match.group(3) or '+00:00'
                            if tz == 'Z':
                                tz = '+00:00'
                            normalized = f"{base}.{frac}{tz}"
                            started_dt = datetime.fromisoformat(normalized)
                        else:
                            # Fallback for simple format
                            started_dt = datetime.fromisoformat(started_str.replace('Z', '+00:00'))
                        
                        started_at = started_str
                        now = datetime.now(timezone.utc)
                        delta = now - started_dt
                        
                        # Format uptime nicely
                        days = delta.days
                        hours, remainder = divmod(delta.seconds, 3600)
                        minutes, _ = divmod(remainder, 60)
                        
                        if days > 0:
                            uptime = f"{days}d {hours}h {minutes}m"
                        elif hours > 0:
                            uptime = f"{hours}h {minutes}m"
                        else:
                            uptime = f"{minutes}m"
                except Exception as e:
                    logger.debug("Error calculating uptime for %s: %s", c.name, e)
            
            # Get exposed ports
            ports = []
            try:
                port_bindings = c.attrs.get('NetworkSettings', {}).get('Ports', {})
                for container_port, host_bindings in (port_bindings or {}).items():
                    if host_bindings:
                        for binding in host_bindings:
                            host_port = binding.get('HostPort', '')
                            if host_port:
                                ports.append(f"{host_port}:{container_port}")
            except Exception:
                pass
            
            result.append({
                "id": c.id,
                "name": c.name,
                "image": image_name,
                "status": c.status,
                "state": c.attrs['State']['Status'],
                "uptime": uptime,
                "started_at": started_at,
                "ports": ports,
                "mounts": _extract_mounts(c),
            })
        return result
    except Exception as e:
        logger.error("Error listing containers: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/containers/{container_id}/start")
async def start_container(container_id: str):
    """Start a stopped container using docker-compose or Docker API."""
    import subprocess
    
    # Map container names to docker compose service names (canonical)
    service_map = {
        "ai_engine": "ai_engine",
        "admin_ui": "admin_ui",
        "local_ai_server": "local_ai_server",
    }
    # Accept both canonical underscored and legacy hyphenated service names as inputs.
    container_name_map = {
        "ai_engine": "ai_engine",
        "admin_ui": "admin_ui",
        "local_ai_server": "local_ai_server",
        "ai-engine": "ai_engine",
        "admin-ui": "admin_ui",
        "local-ai-server": "local_ai_server",
    }
    
    service_name = service_map.get(container_id)
    
    # If not in map, it might be an ID or a raw name.
    if not service_name:
        try:
            client = docker.from_env()
            container = client.containers.get(container_id)
            name = container.name.lstrip('/')
            service_name = service_map.get(name)
        except:
            service_name = None

    # If the caller used compose service names (canonical or legacy)
    if not service_name and container_id in container_name_map:
        service_name = service_map.get(container_name_map[container_id])

    # Only allow starting AAVA services from Admin UI.
    if service_name not in set(service_map.values()):
        raise HTTPException(status_code=400, detail="Only AAVA services can be started from Admin UI")
    
    host_root = _project_host_root_from_admin_ui_container()
    logger.info("Starting %s via updater-runner (host_root=%s)", _sanitize_for_log(service_name), _sanitize_for_log(host_root))

    def _compose_up_cmd(svc: str, *, build: bool) -> str:
        flag = "--build" if build else "--no-build"
        return (
            "set -euo pipefail; "
            "cd \"$PROJECT_ROOT\"; "
            f"docker compose -p asterisk-ai-voice-agent up -d {flag} {svc}"
        )

    try:
        if service_name == "local_ai_server":
            # Fast path: start without build if the image already exists.
            code, out = _run_updater_ephemeral(
                host_root,
                env={"PROJECT_ROOT": host_root},
                command=_compose_up_cmd(service_name, build=False),
                timeout_sec=120,
            )
            if code == 0:
                return {"status": "success", "output": out.strip() or "Container started"}

            err = (out or "").strip()
            needs_build_markers = [
                "No such image",
                "pull access denied",
                "failed to solve",
                "unable to find image",
                "requires build",
            ]
            if any(m.lower() in err.lower() for m in needs_build_markers):
                code2, out2 = _run_updater_ephemeral(
                    host_root,
                    env={"PROJECT_ROOT": host_root},
                    command=_compose_up_cmd(service_name, build=True),
                    timeout_sec=1800,
                )
                if code2 == 0:
                    return {"status": "success", "output": out2.strip() or "Container started"}
                raise HTTPException(status_code=500, detail=f"Failed to build/start local_ai_server: {(out2 or '').strip()[:800]}")

            raise HTTPException(status_code=500, detail=f"Failed to start: {err[:800] or 'Unknown error'}")

        code, out = _run_updater_ephemeral(
            host_root,
            env={"PROJECT_ROOT": host_root},
            command=_compose_up_cmd(service_name, build=True),
            timeout_sec=600,
        )
        if code == 0:
            return {"status": "success", "output": out.strip() or "Container started"}
        raise HTTPException(status_code=500, detail=f"Failed to start: {(out or '').strip()[:800]}")
    except HTTPException:
        raise
    except Exception:
        logger.error("Error starting service %s", _sanitize_for_log(str(service_name)), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start service")


async def _check_active_calls() -> dict:
    """
    Check if AI Engine has active calls in progress.
    
    Returns:
        Dict with active_calls count and warning message if any.
    """
    import httpx
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for url in [
                "http://127.0.0.1:15000/sessions/stats",
                "http://ai_engine:15000/sessions/stats",
                "http://ai-engine:15000/sessions/stats",
            ]:
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue

                    data = resp.json()
                    active_calls = data.get("active_calls", data.get("active_sessions", 0))
                    return {"active_calls": active_calls, "reachable": True}
                except httpx.ConnectError:
                    continue
    except Exception:
        logger.debug("Could not check active calls", exc_info=True)
    
    return {"active_calls": 0, "reachable": False}


@router.post("/containers/{container_id}/restart")
async def restart_container(container_id: str, force: bool = False):
    """
    Restart a container using Docker SDK (preferred) or docker-compose.
    
    Args:
        container_id: Container name or service name
        force: If False and active calls exist, returns warning instead of restarting
    
    Returns:
        Success response with health_status, or warning if active calls and not forced.
    """
    # Map container names to docker compose service names (canonical)
    service_map = {
        "ai_engine": "ai_engine",
        "admin_ui": "admin_ui",
        "local_ai_server": "local_ai_server",
    }
    
    # Accept both canonical underscored and legacy hyphenated service names as inputs.
    container_name_map = {
        "ai_engine": "ai_engine",
        "admin_ui": "admin_ui",
        "local_ai_server": "local_ai_server",
        "ai-engine": "ai_engine",
        "admin-ui": "admin_ui",
        "local-ai-server": "local_ai_server",
    }
    
    # Resolve container name
    is_known = False
    container_name = container_id
    if container_id in service_map:
        # Input is already a container name like "ai_engine"
        container_name = container_id
        is_known = True
    elif container_id in container_name_map:
        # Input is a service name (canonical or legacy)
        container_name = container_name_map[container_id]
        is_known = True

    if not _is_safe_container_identifier(container_name):
        raise HTTPException(status_code=400, detail=f"Invalid container id: {container_id!r}")
    
    safe_container_name = _sanitize_for_log(container_name)
    logger.info("Restarting container: %s", safe_container_name)

    # Check for active calls before restarting AI Engine (unless forced)
    if container_name == "ai_engine" and not force:
        call_status = await _check_active_calls()
        if call_status["active_calls"] > 0:
            return {
                "status": "warning",
                "message": f"Cannot restart: {call_status['active_calls']} active call(s) in progress",
                "active_calls": call_status["active_calls"],
                "action_required": "Set force=true to restart anyway, or wait for calls to complete",
            }

    # NOTE: docker restart does NOT reload env_file changes.
    # Restart is still useful for recovering from crashes and non-env changes.
    # Use explicit "recreate" actions (via updater-runner) when env_file changes must be applied.

    # Special-case: Restarting admin-ui from inside admin-ui is inherently racy if we try to
    # force-recreate it (the API process is the one being replaced). Use a scheduled Docker-SDK
    # restart which is significantly more reliable from within the container itself.
    if container_name == "admin_ui":
        import asyncio

        async def _restart_admin_ui_later():
            try:
                await asyncio.sleep(0.75)
                client = docker.from_env()
                client.containers.get("admin_ui").restart(timeout=10)
            except Exception as e:
                logger.error("Failed to restart admin_ui via Docker SDK: %s", e)

        asyncio.create_task(_restart_admin_ui_later())
        return {
            "status": "success",
            "method": "docker-sdk",
            "output": "Admin UI restart scheduled (page will reload shortly)",
            "note": (
                "If you need admin_ui env_file changes to apply, run on the host: "
                "`docker compose -p asterisk-ai-voice-agent up -d --force-recreate admin_ui`."
            ),
        }
    
    try:
        # A5: Use Docker SDK for cleaner restart (no stop/rm/up)
        client = docker.from_env()
        container = client.containers.get(container_name)
        
        # Restart with 10 second timeout for graceful stop
        container.restart(timeout=10)
        
        logger.info("Container %s restarted successfully via Docker SDK", safe_container_name)
        payload = {
            "status": "success", 
            "method": "docker-sdk",
            "output": f"Container {safe_container_name} restarted"
        }
        if container_name in ("ai_engine", "local_ai_server"):
            payload["note"] = (
                "Restart does not reload env_file (.env) changes. "
                "If you changed .env, use a recreate/force-recreate for this service."
            )
        return payload
        
    except docker.errors.NotFound:
        # Container doesn't exist. Only allow compose-based start for known AAVA services.
        logger.warning("Container %s not found", safe_container_name)
        if is_known:
            logger.warning("Attempting docker-compose up for %s", safe_container_name)
            return await _start_via_compose(container_id, service_map)
        raise HTTPException(status_code=404, detail=f"Container not found: {container_id}")
        
    except docker.errors.APIError as e:
        logger.error("Docker API error restarting %s: %s", safe_container_name, _sanitize_for_log(str(e)))
        # Fallback to docker-compose only for known AAVA services.
        if is_known:
            return await _start_via_compose(container_id, service_map)
        raise HTTPException(status_code=500, detail="Docker API error restarting container")
        
    except Exception as e:
        logger.error("Error restarting container %s", safe_container_name, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to restart container")


async def _start_via_compose(container_id: str, service_map: dict):
    """Helper to start a container via docker-compose."""
    service_name = service_map.get(container_id)
    if not service_name:
        raise HTTPException(status_code=400, detail="Unsupported service for compose start")
    
    try:
        host_root = _project_host_root_from_admin_ui_container()
        build_flag = "--build" if service_name == "local_ai_server" else "--no-build"
        timeout_sec = 1800 if service_name == "local_ai_server" else 300
        cmd = (
            "set -euo pipefail; "
            "cd \"$PROJECT_ROOT\"; "
            f"docker compose -p asterisk-ai-voice-agent up -d {build_flag} {service_name}"
        )

        code, out = _run_updater_ephemeral(
            host_root,
            env={"PROJECT_ROOT": host_root},
            command=cmd,
            timeout_sec=timeout_sec,
        )
        if code == 0:
            return {"status": "success", "method": "docker-compose", "output": (out or '').strip() or "Container started"}
        raise HTTPException(status_code=500, detail=f"Failed to start via compose: {(out or '').strip()[:800]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _recreate_via_compose(service_name: str, health_check: bool = True):
    """
    Force-recreate a compose service so env_file changes (.env) are applied.
    
    Args:
        service_name: Docker Compose service name (e.g., "ai_engine")
        health_check: If True, poll health endpoint after recreate (default: True)
    
    Returns:
        Dict with status, method, and health_status fields
    """
    import httpx
    host_root = _project_host_root_from_admin_ui_container()

    # Normalize legacy hyphenated service names to canonical underscored service names.
    legacy_to_canonical = {
        "ai-engine": "ai_engine",
        "admin-ui": "admin_ui",
        "local-ai-server": "local_ai_server",
    }
    service_name = legacy_to_canonical.get(service_name, service_name)
    
    # Map service names to container names and health URLs
    # NOTE: Use /ready endpoint for ai_engine (returns 503 when degraded, 200 when ready)
    # local_ai_server is WebSocket-only, no HTTP health endpoint
    service_config = {
        "ai_engine": {
            "container": "ai_engine",
            "health_url": "http://127.0.0.1:15000/ready",  # /ready returns proper status codes
            "health_timeout": 30,
        },
        "admin_ui": {
            "container": "admin_ui",
            "health_url": None,  # No health check for admin-ui
            "health_timeout": 10,
        },
        "local_ai_server": {
            "container": "local_ai_server",
            "health_url": None,  # WebSocket server - no HTTP health endpoint
            "health_timeout": 60,
        }
    }
    config = service_config.get(service_name, {"container": service_name, "health_url": None, "health_timeout": 30})
    container_name = config["container"]

    try:
        # First stop and remove the existing container to avoid name conflicts
        try:
            client = docker.from_env()
            container = client.containers.get(container_name)
            logger.info(f"Stopping container {container_name} before recreate")
            container.stop(timeout=10)
            container.remove()
            logger.info(f"Container {container_name} stopped and removed")
        except docker.errors.NotFound:
            logger.info(f"Container {container_name} not found, will create fresh")
        except Exception as e:
            logger.warning(f"Error stopping container: {e}")
        
        # Run compose in updater-runner so relative binds resolve on the host correctly.
        cmd = (
            "set -euo pipefail; "
            "cd \"$PROJECT_ROOT\"; "
            f"docker compose -p asterisk-ai-voice-agent up -d --force-recreate --no-build {service_name}"
        )
        timeout_sec = 600 if service_name == "local_ai_server" else 300
        code, out = _run_updater_ephemeral(
            host_root,
            env={"PROJECT_ROOT": host_root},
            command=cmd,
            timeout_sec=timeout_sec,
        )
        if code != 0:
            raise HTTPException(status_code=500, detail=f"Failed to recreate via compose: {(out or '').strip()[:800]}")
        
        # Health check polling after successful recreate
        health_status = "skipped"
        if health_check and config["health_url"]:
            health_status = await _poll_health(
                config["health_url"], 
                timeout_seconds=config["health_timeout"],
                service_name=service_name
            )
        
        # Return appropriate status based on health check result
        # Don't claim success if health check timed out or failed
        if health_status == "timeout":
            return {
                "status": "degraded",
                "method": "docker-compose",
                "output": (out or "").strip() or "Service recreated but health check timed out",
                "health_status": health_status,
            }
        elif health_status == "unhealthy":
            return {
                "status": "degraded",
                "method": "docker-compose",
                "output": (out or "").strip() or "Service recreated but not healthy",
                "health_status": health_status,
            }
        
        return {
            "status": "success", 
            "method": "docker-compose", 
            "output": (out or "").strip() or "Service recreated",
            "health_status": health_status,
        }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail="Failed to recreate service") from e


async def _poll_health(url: str, timeout_seconds: int = 30, service_name: str = "service") -> str:
    """
    Poll a health endpoint until it returns success or timeout.
    
    Returns: "healthy", "unhealthy", or "timeout"
    """
    import httpx
    import asyncio
    
    start_time = asyncio.get_event_loop().time()
    poll_interval = 2  # seconds between polls
    last_status_code = None
    
    logger.info(f"Polling health for {service_name} at {url} (timeout: {timeout_seconds}s)")
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout_seconds:
                # If we got responses but they were non-200, return unhealthy
                # If we never connected, return timeout
                if last_status_code is not None and last_status_code != 200:
                    logger.warning(f"Health check unhealthy for {service_name}: last status {last_status_code}")
                    return "unhealthy"
                logger.warning(f"Health check timeout for {service_name} after {timeout_seconds}s")
                return "timeout"
            
            try:
                resp = await client.get(url)
                last_status_code = resp.status_code
                if resp.status_code == 200:
                    logger.info(f"Health check passed for {service_name} after {elapsed:.1f}s")
                    return "healthy"
                else:
                    logger.debug(f"Health check returned {resp.status_code}, retrying...")
            except httpx.ConnectError:
                logger.debug(f"Health check connection failed, retrying...")
            except Exception as e:
                logger.debug(f"Health check error: {e}, retrying...")
            
            await asyncio.sleep(poll_interval)


@router.post("/containers/ai_engine/reload")
async def reload_ai_engine():
    """
    Hot-reload AI Engine configuration without restarting the container.
    This reloads ai-agent.yaml changes ONLY. 
    
    NOTE: .env changes are NOT applied by hot-reload because the AI Engine reads
    credentials from os.environ at startup (via security.py inject_* functions).
    For .env changes, use /containers/ai_engine/restart which force-recreates the container.
    
    Returns restart_required=True if:
    - New providers need to be added (hot-reload can't add new providers)
    - AI Engine's /reload endpoint signals "reload deferred" or "restart needed"
    
    This endpoint does NOT detect .env changes - callers must track that separately.
    """
    try:
        import httpx
        env = os.getenv("HEALTH_CHECK_AI_ENGINE_URL")
        candidates = []
        if env:
            candidates.append(env.replace("/health", "/reload"))
        candidates.extend(
            [
                "http://127.0.0.1:15000/reload",
                "http://ai-engine:15000/reload",
                "http://ai_engine:15000/reload",
            ]
        )
        # Dedupe
        seen = set()
        urls = []
        for u in candidates:
            u = (u or "").strip()
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        
        resp = None
        async with httpx.AsyncClient(timeout=10.0) as client:
            for url in urls:
                try:
                    logger.info(f"Sending reload request to AI Engine at {url}")
                    resp = await client.post(url)
                    break
                except httpx.ConnectError:
                    continue
        if resp is None:
            raise HTTPException(status_code=503, detail="AI Engine is not reachable")
        
        if resp.status_code == 200:
            data = resp.json()
            changes = data.get("changes", [])
                
            # Check if any change requires a restart (new providers, removed providers, deferred reload)
            restart_required = any(
                any(marker in str(c).lower() for marker in ("restart needed", "reload deferred"))
                for c in changes
            )
                
            if restart_required:
                return {
                    "status": "partial",
                    "message": "Config updated but some changes require a restart to fully apply",
                    "changes": changes,
                    "restart_required": True
                }
                
            return {
                "status": "success",
                "message": data.get("message", "Configuration reloaded"),
                "changes": changes,
                "restart_required": False
            }
        
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"AI Engine reload failed: {resp.text}"
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="AI Engine is not running. Start it first."
        )
    except Exception as e:
        logger.error(f"Error reloading AI Engine: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/metrics")
async def get_system_metrics():
    try:
        # interval=None is non-blocking, returns usage since last call
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "cpu": {
                "percent": cpu_percent,
                "count": psutil.cpu_count()
            },
            "memory": {
                "total": memory.total,
                "available": memory.available,
                "percent": memory.percent,
                "used": memory.used
            },
            "disk": {
                "total": disk.total,
                "free": disk.free,
                "percent": disk.percent
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/docker/disk-usage")
async def get_docker_disk_usage():
    """
    Get Docker disk usage breakdown (images, containers, build cache, volumes).
    This helps identify what's consuming disk space.
    """
    import subprocess
    
    try:
        # Run docker system df to get disk usage
        result = subprocess.run(
            ["docker", "system", "df", "-v", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            # Fallback to non-JSON format
            result = subprocess.run(
                ["docker", "system", "df"],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            # Parse the text output
            lines = result.stdout.strip().split('\n')
            usage = {
                "images": {"total": 0, "active": 0, "size": "0B", "reclaimable": "0B"},
                "containers": {"total": 0, "active": 0, "size": "0B", "reclaimable": "0B"},
                "volumes": {"total": 0, "active": 0, "size": "0B", "reclaimable": "0B"},
                "build_cache": {"total": 0, "active": 0, "size": "0B", "reclaimable": "0B"},
            }
            
            for line in lines[1:]:  # Skip header
                parts = line.split()
                if len(parts) >= 5:
                    type_name = parts[0].lower()
                    if type_name == "images":
                        usage["images"] = {
                            "total": int(parts[1]) if parts[1].isdigit() else 0,
                            "active": int(parts[2]) if parts[2].isdigit() else 0,
                            "size": parts[3],
                            "reclaimable": parts[4] if len(parts) > 4 else "0B"
                        }
                    elif type_name == "containers":
                        usage["containers"] = {
                            "total": int(parts[1]) if parts[1].isdigit() else 0,
                            "active": int(parts[2]) if parts[2].isdigit() else 0,
                            "size": parts[3],
                            "reclaimable": parts[4] if len(parts) > 4 else "0B"
                        }
                    elif type_name == "local" and len(parts) > 1 and parts[1].lower() == "volumes":
                        usage["volumes"] = {
                            "total": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
                            "active": int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0,
                            "size": parts[4] if len(parts) > 4 else "0B",
                            "reclaimable": parts[5] if len(parts) > 5 else "0B"
                        }
                    elif type_name == "build":
                        usage["build_cache"] = {
                            "total": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
                            "active": int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0,
                            "size": parts[4] if len(parts) > 4 else "0B",
                            "reclaimable": parts[5] if len(parts) > 5 else "0B"
                        }
            
            return usage
        
        # Parse JSON output and transform to expected format
        import json
        data = json.loads(result.stdout)
        
        # Transform the verbose JSON format to our expected structure
        def format_bytes(size_bytes):
            """Convert bytes to human readable string."""
            if size_bytes == 0:
                return "0B"
            for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                if abs(size_bytes) < 1024.0:
                    return f"{size_bytes:.1f}{unit}"
                size_bytes /= 1024.0
            return f"{size_bytes:.1f}PB"
        
        # Calculate totals from the detailed JSON data
        images = data.get("Images", []) or []
        containers = data.get("Containers", []) or []
        volumes = data.get("Volumes", []) or []
        build_cache = data.get("BuildCache", []) or []
        
        def parse_size(size_str):
            """Parse size string like '10.2GB' to bytes."""
            if not size_str or size_str == "0B":
                return 0
            try:
                # Check longer units first to avoid "GB" matching "B"
                units = [('TB', 1024**4), ('GB', 1024**3), ('MB', 1024**2), ('KB', 1024), ('B', 1)]
                size_upper = size_str.upper()
                for unit, multiplier in units:
                    if size_upper.endswith(unit):
                        return float(size_str[:-len(unit)]) * multiplier
                return float(size_str)
            except:
                return 0
        
        def safe_int(val, default=0):
            """Safely convert value to int."""
            if val is None:
                return default
            try:
                return int(val)
            except (ValueError, TypeError):
                return default
        
        # Calculate image stats
        img_total_size = sum(parse_size(img.get("Size", "0B")) for img in images)
        img_reclaimable = sum(parse_size(img.get("Size", "0B")) for img in images if safe_int(img.get("Containers")) == 0)
        img_active = sum(1 for img in images if safe_int(img.get("Containers")) > 0)
        
        # Calculate container stats  
        cont_total_size = sum(parse_size(c.get("Size", "0B")) for c in containers)
        cont_reclaimable = sum(parse_size(c.get("Size", "0B")) for c in containers if c.get("State") != "running")
        cont_active = sum(1 for c in containers if c.get("State") == "running")
        
        # Calculate volume stats
        vol_total_size = sum(parse_size(v.get("Size", "0B")) for v in volumes)
        vol_active = sum(1 for v in volumes if safe_int(v.get("Links")) > 0)
        
        # Calculate build cache stats
        bc_total_size = sum(parse_size(bc.get("Size", "0B")) for bc in build_cache)
        bc_reclaimable = sum(parse_size(bc.get("Size", "0B")) for bc in build_cache if bc.get("InUse") == "false")
        bc_active = sum(1 for bc in build_cache if bc.get("InUse") == "true")
        
        return {
            "images": {
                "total": len(images),
                "active": img_active,
                "size": format_bytes(img_total_size),
                "reclaimable": format_bytes(img_reclaimable)
            },
            "containers": {
                "total": len(containers),
                "active": cont_active,
                "size": format_bytes(cont_total_size),
                "reclaimable": format_bytes(cont_reclaimable)
            },
            "volumes": {
                "total": len(volumes),
                "active": vol_active,
                "size": format_bytes(vol_total_size),
                "reclaimable": "0B"  # Volumes shouldn't be auto-reclaimed
            },
            "build_cache": {
                "total": len(build_cache),
                "active": bc_active,
                "size": format_bytes(bc_total_size),
                "reclaimable": format_bytes(bc_reclaimable)
            }
        }
        
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Timeout getting Docker disk usage")
    except Exception as e:
        logger.error(f"Error getting Docker disk usage: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class PruneRequest(BaseModel):
    prune_build_cache: bool = True
    prune_images: bool = False  # Dangerous - only unused images
    prune_containers: bool = False  # Stopped containers only
    prune_volumes: bool = False  # Very dangerous - data loss


class PruneResponse(BaseModel):
    success: bool
    space_reclaimed: str
    details: dict


@router.post("/docker/prune", response_model=PruneResponse)
async def prune_docker_resources(request: PruneRequest):
    """
    Clean up Docker resources to free disk space.
    
    WARNING: This operation is destructive and cannot be undone.
    - Build cache: Safe to prune (will rebuild as needed)
    - Images: Removes unused images (tagged images in use are preserved)
    - Containers: Removes stopped containers only
    - Volumes: DANGEROUS - can cause data loss
    """
    import subprocess
    
    total_reclaimed = 0
    details = {}
    
    try:
        # Always prune build cache first (safest, often largest)
        # Use -a to remove ALL build cache, not just unused layers
        if request.prune_build_cache:
            result = subprocess.run(
                ["docker", "builder", "prune", "-a", "-f"],
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode == 0:
                # Parse reclaimed space from output
                output = result.stdout + result.stderr
                details["build_cache"] = "pruned"
                # Try to extract size
                for line in output.split('\n'):
                    if 'reclaimed' in line.lower() or 'freed' in line.lower():
                        details["build_cache_output"] = line.strip()
            else:
                details["build_cache"] = f"error: {result.stderr}"
        
        # Prune unused images (all images not used by containers)
        # Use -a to remove ALL unused images, not just dangling ones
        if request.prune_images:
            result = subprocess.run(
                ["docker", "image", "prune", "-a", "-f"],
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode == 0:
                details["images"] = "pruned"
                for line in result.stdout.split('\n'):
                    if 'reclaimed' in line.lower():
                        details["images_output"] = line.strip()
            else:
                details["images"] = f"error: {result.stderr}"
        
        # Prune stopped containers
        if request.prune_containers:
            result = subprocess.run(
                ["docker", "container", "prune", "-f"],
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode == 0:
                details["containers"] = "pruned"
            else:
                details["containers"] = f"error: {result.stderr}"
        
        # Prune unused volumes (DANGEROUS)
        if request.prune_volumes:
            result = subprocess.run(
                ["docker", "volume", "prune", "-f"],
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode == 0:
                details["volumes"] = "pruned"
            else:
                details["volumes"] = f"error: {result.stderr}"
        
        # Run system prune to get total reclaimed (without volumes for safety)
        result = subprocess.run(
            ["docker", "system", "df"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        return PruneResponse(
            success=True,
            space_reclaimed=details.get("build_cache_output", "Unknown"),
            details=details
        )
        
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Timeout during Docker prune")
    except Exception as e:
        logger.error(f"Error pruning Docker resources: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def get_system_health():
    """
    Aggregate health status from Local AI Server and AI Engine.
    """
    def _dedupe_preserve_order(items: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for item in items:
            if not item or item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    async def check_local_ai():
        try:
            import websockets
            import json
            import asyncio
            from settings import get_setting
            
            env_uri = (_dotenv_value("HEALTH_CHECK_LOCAL_AI_URL") or "").strip()
            if not env_uri:
                env_uri = (os.getenv("HEALTH_CHECK_LOCAL_AI_URL") or "").strip()
            candidates = _dedupe_preserve_order([
                env_uri,
                "ws://127.0.0.1:8765",
                "ws://local_ai_server:8765",
                "ws://local-ai-server:8765",
                "ws://host.docker.internal:8765",
            ])

            last_error: Optional[str] = None
            errors_by_uri: dict = {}
            for uri in candidates:
                logger.debug("Checking Local AI at %s", uri)
                try:
                    async with websockets.connect(uri, open_timeout=2.5) as websocket:
                        logger.debug("Local AI connected, sending status...")
                        auth_token = (get_setting("LOCAL_WS_AUTH_TOKEN", os.getenv("LOCAL_WS_AUTH_TOKEN", "")) or "").strip()
                        if auth_token:
                            await websocket.send(json.dumps({"type": "auth", "auth_token": auth_token}))
                            raw = await asyncio.wait_for(websocket.recv(), timeout=5)
                            auth_data = json.loads(raw)
                            if auth_data.get("type") != "auth_response" or auth_data.get("status") != "ok":
                                raise RuntimeError(f"Local AI auth failed: {auth_data}")
                        await websocket.send(json.dumps({"type": "status"}))
                        logger.debug("Local AI sent, waiting for response...")
                        response = await asyncio.wait_for(websocket.recv(), timeout=5)
                        logger.debug("Local AI response: %s...", response[:100])
                        data = json.loads(response)
                        if data.get("type") == "status_response":
                            # Prefer explicit fields from local-ai-server (v2 protocol), fallback to heuristics.
                            kroko = data.get("kroko") or {}
                            kokoro = data.get("kokoro") or {}

                            kroko_embedded = bool(kroko.get("embedded", False))
                            kroko_port = kroko.get("port")

                            kokoro_mode = (kokoro.get("mode") or "local").lower()
                            kokoro_voice = kokoro.get("voice")

                            # Back-compat for older payloads that didn't include structured metadata
                            if not kokoro_voice:
                                tts_display = data.get("models", {}).get("tts", {}).get("display") or ""
                                if "(" in tts_display and ")" in tts_display:
                                    kokoro_voice = tts_display.split("(")[1].rstrip(")")

                            data["kroko_embedded"] = kroko_embedded
                            data["kroko_port"] = kroko_port
                            data["kokoro_mode"] = kokoro_mode
                            data["kokoro_voice"] = kokoro_voice
                            
                            warning = None
                            if env_uri and uri != env_uri:
                                warning = (
                                    f"HEALTH_CHECK_LOCAL_AI_URL is set but unreachable ({env_uri}); "
                                    f"connected via fallback ({uri})."
                                )
                            return {
                                "status": "connected",
                                "details": data,
                                "probe": {
                                    "selected": uri,
                                    "attempted": candidates,
                                    "errors": errors_by_uri,
                                }
                                ,
                                "warning": warning,
                            }
                        else:
                            last_error = "Invalid response type"
                except Exception as e:
                    last_error = f"{type(e).__name__}: {str(e)}"
                    errors_by_uri[uri] = last_error
                    continue

            # Prefer an actionable error for the configured URL (if set),
            # otherwise for localhost (most common on host-network installs).
            preferred_error = None
            if env_uri:
                preferred_error = errors_by_uri.get(env_uri)
            if not preferred_error:
                preferred_error = errors_by_uri.get("ws://127.0.0.1:8765")
            if not preferred_error:
                preferred_error = last_error

            return {
                "status": "error",
                "details": {"error": preferred_error or "Unreachable"},
                "probe": {
                    "selected": None,
                    "attempted": candidates,
                    "errors": errors_by_uri,
                    "error": preferred_error or "Unreachable",
                }
            }
        except Exception as e:
            logger.debug("Local AI Check Error: %s: %s", type(e).__name__, str(e))
            return {
                "status": "error",
                "details": {"error": f"{type(e).__name__}: {str(e)}"},
            }

    async def check_ai_engine():
        try:
            import httpx
            env_url = (_dotenv_value("HEALTH_CHECK_AI_ENGINE_URL") or "").strip()
            if not env_url:
                env_url = (os.getenv("HEALTH_CHECK_AI_ENGINE_URL") or "").strip()
            candidates = _dedupe_preserve_order([
                env_url,
                "http://127.0.0.1:15000/health",
                "http://ai_engine:15000/health",
                "http://ai-engine:15000/health",
                "http://host.docker.internal:15000/health",
            ])

            timeout = httpx.Timeout(5.0, connect=1.5)
            last_error: Optional[str] = None

            async with httpx.AsyncClient(timeout=timeout) as client:
                for url in candidates:
                    logger.debug("Checking AI Engine at %s", url)
                    try:
                        resp = await client.get(url)
                        logger.debug("AI Engine response: %s", resp.status_code)
                        if resp.status_code == 200:
                            warning = None
                            if env_url and url != env_url:
                                warning = (
                                    f"HEALTH_CHECK_AI_ENGINE_URL is set but unreachable ({env_url}); "
                                    f"connected via fallback ({url})."
                                )
                            return {
                                "status": "connected",
                                "details": resp.json(),
                                "probe": {
                                    "selected": url,
                                    "attempted": candidates,
                                }
                                ,
                                "warning": warning,
                            }
                        last_error = f"HTTP {resp.status_code}"
                    except Exception as e:
                        last_error = f"{type(e).__name__}: {str(e)}"
                        continue

            return {
                "status": "error",
                "details": {"error": last_error or "Unreachable"},
                "probe": {
                    "selected": None,
                    "attempted": candidates,
                    "error": last_error or "Unreachable",
                }
            }
        except Exception as e:
            logger.debug("AI Engine Check Error: %s: %s", type(e).__name__, str(e))
            return {
                "status": "error",
                "details": {"error": f"{type(e).__name__}: {str(e)}"},
            }

    import asyncio
    local_ai, ai_engine = await asyncio.gather(check_local_ai(), check_ai_engine())

    return {
        "local_ai_server": local_ai,
        "ai_engine": ai_engine
    }


@router.get("/directories")
async def get_directory_health():
    """
    Check health of directories required for audio playback.
    Returns status of media directory, symlink, and permissions.
    """
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    ast_media_dir = os.getenv("AST_MEDIA_DIR", "")
    in_docker = bool(os.path.exists("/.dockerenv") or os.getenv("DOCKER_CONTAINER", ""))
    
    # Expected paths
    host_media_root = os.path.join(project_root, "asterisk_media")
    host_media_dir = os.path.join(project_root, "asterisk_media", "ai-generated")
    asterisk_sounds_link = "/var/lib/asterisk/sounds/ai-generated"
    container_media_dir = "/mnt/asterisk_media/ai-generated"
    
    checks = {
        "media_dir_configured": {
            "status": "unknown",
            "configured_path": ast_media_dir,
            "expected_path": container_media_dir,
            "message": ""
        },
        "host_directory": {
            "status": "unknown",
            "path": container_media_dir if in_docker else host_media_dir,
            "media_root": host_media_root,
            "media_root_is_symlink": False,
            "media_root_symlink_target": None,
            "media_root_resolved": None,
            "paths_checked": [host_media_dir, container_media_dir],
            "exists": False,
            "writable": False,
            "message": ""
        },
        "asterisk_symlink": {
            "status": "unknown",
            "path": asterisk_sounds_link,
            "exists": False,
            "target": None,
            "valid": False,
            "message": ""
        }
    }
    
    # Check 1: AST_MEDIA_DIR configured
    if ast_media_dir:
        if "ai-generated" in ast_media_dir:
            checks["media_dir_configured"]["status"] = "ok"
            checks["media_dir_configured"]["message"] = "Correctly configured"
        else:
            checks["media_dir_configured"]["status"] = "warning"
            checks["media_dir_configured"]["message"] = "Missing 'ai-generated' subdirectory in path"
    else:
        checks["media_dir_configured"]["status"] = "error"
        checks["media_dir_configured"]["message"] = "AST_MEDIA_DIR not set in environment"
    
    # Check 2: Host directory exists and is writable
    try:
        broken_media_root = False
        if os.path.islink(host_media_root):
            checks["host_directory"]["media_root_is_symlink"] = True
            try:
                checks["host_directory"]["media_root_symlink_target"] = os.readlink(host_media_root)
            except OSError:
                checks["host_directory"]["media_root_symlink_target"] = None
            checks["host_directory"]["media_root_resolved"] = os.path.realpath(host_media_root)

            # If the symlink target is missing, report an actionable error.
            if not os.path.exists(checks["host_directory"]["media_root_resolved"] or ""):
                # In Docker, the symlink may point to a host path that is not present inside the container,
                # even though the actual media volume is mounted at /mnt/asterisk_media.
                if in_docker and os.path.exists("/mnt/asterisk_media"):
                    checks["host_directory"]["status"] = "warning"
                    checks["host_directory"]["message"] = (
                        "asterisk_media is a symlink but the container cannot verify its target path. "
                        "This can be normal when Docker mounts the resolved host directory at /mnt/asterisk_media. "
                        "If you see missing audio directories after a reboot, verify the host mount persists and restart containers."
                    )
                else:
                    checks["host_directory"]["status"] = "error"
                    checks["host_directory"]["message"] = (
                        "asterisk_media is a symlink but its target is missing. "
                        "This commonly happens after a reboot when the external media mount did not come up. "
                        "Ensure the mount is persisted on the host (e.g., /etc/fstab or a systemd mount unit), "
                        "then restart containers (or rerun preflight.sh on the host)."
                    )
                    broken_media_root = True

        # Prefer checking the mounted media volume path from inside containers.
        path_to_check = container_media_dir if in_docker else host_media_dir
        if not broken_media_root and os.path.exists(path_to_check):
            checks["host_directory"]["exists"] = True
            # Test write permission
            test_file = os.path.join(path_to_check, ".write_test")
            try:
                with open(test_file, "w") as f:
                    f.write("test")
                os.remove(test_file)
                checks["host_directory"]["writable"] = True
                if checks["host_directory"]["status"] != "warning":
                    checks["host_directory"]["status"] = "ok"
                    checks["host_directory"]["message"] = "Directory exists and is writable"
            except PermissionError:
                checks["host_directory"]["status"] = "error"
                checks["host_directory"]["message"] = "Directory exists but not writable"
        elif not broken_media_root:
            checks["host_directory"]["status"] = "error"
            if in_docker:
                checks["host_directory"]["message"] = f"Directory does not exist (checked: {path_to_check})"
            else:
                checks["host_directory"]["message"] = "Directory does not exist"
    except Exception as e:
        checks["host_directory"]["status"] = "error"
        checks["host_directory"]["message"] = f"Error checking directory: {str(e)}"
    
    # Check 3: Asterisk symlink
    # Note: When running in Docker, we can't verify the symlink because
    # /var/lib/asterisk/sounds is on the host and not mounted into the container.
    # If the other checks pass, assume symlink is OK (user can verify with test call).
    try:
        if os.path.islink(asterisk_sounds_link):
            checks["asterisk_symlink"]["exists"] = True
            target = os.readlink(asterisk_sounds_link)
            checks["asterisk_symlink"]["target"] = target
            
            # Check if target contains the project path or is correct
            if host_media_dir in target or target == host_media_dir:
                checks["asterisk_symlink"]["valid"] = True
                checks["asterisk_symlink"]["status"] = "ok"
                checks["asterisk_symlink"]["message"] = f"Symlink valid → {target}"
            else:
                checks["asterisk_symlink"]["status"] = "warning"
                checks["asterisk_symlink"]["message"] = f"Symlink points to {target}, expected {host_media_dir}"
        elif os.path.exists(asterisk_sounds_link):
            checks["asterisk_symlink"]["exists"] = True
            # If running on host and it's a mount point, treat as OK (bind mount mode).
            if not in_docker and os.path.ismount(asterisk_sounds_link):
                checks["asterisk_symlink"]["status"] = "ok"
                checks["asterisk_symlink"]["valid"] = True
                checks["asterisk_symlink"]["message"] = "Bind mount present (Asterisk can read generated audio)"
            else:
                checks["asterisk_symlink"]["status"] = "warning"
                checks["asterisk_symlink"]["message"] = "Path exists but is not a symlink"
        elif in_docker:
            # Running in Docker - can't verify symlink but if other checks pass, assume OK
            checks["asterisk_symlink"]["status"] = "ok"
            checks["asterisk_symlink"]["message"] = "Cannot verify from Docker (symlink is on host)"
            checks["asterisk_symlink"]["docker_note"] = True
        else:
            checks["asterisk_symlink"]["status"] = "error"
            checks["asterisk_symlink"]["message"] = "Symlink does not exist"
    except Exception as e:
        checks["asterisk_symlink"]["status"] = "error"
        checks["asterisk_symlink"]["message"] = f"Error checking symlink: {str(e)}"
    
    # Calculate overall health
    statuses = [c["status"] for c in checks.values()]
    if all(s == "ok" for s in statuses):
        overall = "healthy"
    elif any(s == "error" for s in statuses):
        overall = "error"
    else:
        overall = "warning"
    
    return {
        "overall": overall,
        "checks": checks
    }


@router.post("/directories/fix")
async def fix_directory_issues():
    """
    Attempt to fix directory permission and symlink issues.
    Note: Symlink creation requires host access - use preflight.sh for that.
    """
    import subprocess
    
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    host_media_root = os.path.join(project_root, "asterisk_media")
    host_media_dir = os.path.join(project_root, "asterisk_media", "ai-generated")
    asterisk_sounds_link = "/var/lib/asterisk/sounds/ai-generated"
    in_docker = bool(os.path.exists("/.dockerenv") or os.getenv("DOCKER_CONTAINER", ""))
    
    fixes_applied = []
    errors = []
    manual_steps = []

    desired_gid = 995
    try:
        env_gid = (_dotenv_value("ASTERISK_GID") or "").strip()
        if env_gid.isdigit():
            desired_gid = int(env_gid)
    except Exception:
        desired_gid = 995
    desired_uid = 1000

    path_to_fix = "/mnt/asterisk_media/ai-generated" if in_docker else host_media_dir
    
    # If asterisk_media is a symlink to a missing target (common after reboot with external mounts),
    # auto-fix inside the container cannot repair it.
    try:
        if os.path.islink(host_media_root):
            resolved = os.path.realpath(host_media_root)
            if not os.path.exists(resolved):
                # Inside Docker, the symlink may point to a host path that isn't present in the container.
                # If the media volume is mounted at /mnt/asterisk_media, don't hard-fail on this probe.
                if in_docker and os.path.exists("/mnt/asterisk_media"):
                    logger.debug(
                        "asterisk_media symlink target not visible inside container; ignoring (resolved=%s)",
                        resolved,
                    )
                else:
                    errors.append(
                        f"asterisk_media points to missing target: {host_media_root} -> {resolved}"
                    )
                    manual_steps.append(
                        "Ensure your external media mount persists across reboots (e.g., /etc/fstab or systemd mount unit), then restart containers."
                    )
                    manual_steps.append("Run on host: sudo ./preflight.sh --apply-fixes")
                    return {
                        "success": False,
                        "fixes_applied": fixes_applied,
                        "errors": errors,
                        "manual_steps": manual_steps,
                        "restart_required": False,
                    }
    except Exception:
        logger.debug("Failed to inspect asterisk_media symlink target", exc_info=True)

    # Prefer applying permission fixes on the host via Docker when running inside a container.
    # This avoids discrepancies between container-visible paths and host bind-mount resolution.
    if in_docker:
        try:
            client = docker.from_env()
            container = client.containers.get("admin_ui")
            mounts = container.attrs.get("Mounts", []) or []
            host_project_path = None
            for m in mounts:
                if m.get("Destination") == "/app/project":
                    host_project_path = m.get("Source")
                    break

            if host_project_path:
                script = f"""
set -eu
mkdir -p /project/asterisk_media/ai-generated
chown {desired_uid}:{desired_gid} /project/asterisk_media /project/asterisk_media/ai-generated || true
chmod 2750 /project/asterisk_media /project/asterisk_media/ai-generated || true
echo "media permissions fixed"
"""
                output = client.containers.run(
                    "alpine:latest",
                    command=["sh", "-c", script],
                    volumes={host_project_path: {"bind": "/project", "mode": "rw"}},
                    remove=True,
                )
                msg = (output.decode().strip() if output else "").strip()
                if msg:
                    fixes_applied.append(msg)
            else:
                manual_steps.append("Could not detect host project path for /app/project mount (admin_ui)")
        except Exception:
            logger.debug("Failed to apply host-side media permission fix via Docker", exc_info=True)
            manual_steps.append("Run on host: sudo ./preflight.sh --apply-fixes")
    
    # Fix 1: Create directory if missing
    try:
        os.makedirs(path_to_fix, exist_ok=True)
        fixes_applied.append(f"Ensured directory exists: {path_to_fix}")
    except Exception as e:
        errors.append(f"Failed to create directory: {str(e)}")
    
    # Fix 2: Permissions are best applied on the host via preflight/install to keep
    # Asterisk + containers aligned. Avoid chmod here (CodeQL flags group/world bits).
    manual_steps.append("For permission alignment, run on host: sudo ./preflight.sh --apply-fixes")
    
    # Fix 3: Symlink - can only be done from host, not container
    if in_docker:
        # Check if symlink already exists on host via mounted path
        # The symlink is on the host at /var/lib/asterisk/sounds which isn't mounted
        manual_steps.append(
            f"Run on host: sudo ln -sf {host_media_dir} {asterisk_sounds_link}"
        )
        manual_steps.append(
            f"Or run: ./preflight.sh --apply-fixes"
        )
    else:
        # Running on host - can create symlink directly
        try:
            if os.path.islink(asterisk_sounds_link):
                os.unlink(asterisk_sounds_link)
                fixes_applied.append(f"Removed old symlink: {asterisk_sounds_link}")
            elif os.path.exists(asterisk_sounds_link):
                errors.append(f"Cannot fix: {asterisk_sounds_link} exists and is not a symlink")
            
            if not os.path.exists(asterisk_sounds_link):
                os.symlink(host_media_dir, asterisk_sounds_link)
                fixes_applied.append(f"Created symlink: {asterisk_sounds_link} → {host_media_dir}")
        except PermissionError:
            try:
                result = subprocess.run(
                    ["sudo", "ln", "-sf", host_media_dir, asterisk_sounds_link],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    fixes_applied.append(f"Created symlink with sudo: {asterisk_sounds_link} → {host_media_dir}")
                else:
                    errors.append(f"Failed to create symlink with sudo: {result.stderr}")
            except Exception as e:
                errors.append(f"Failed to create symlink: {str(e)}")
        except Exception as e:
            errors.append(f"Failed to manage symlink: {str(e)}")
    
    # Fix 4: Update .env if needed
    env_file = os.path.join(project_root, ".env")
    try:
        result = upsert_env_vars(
            env_file,
            {"AST_MEDIA_DIR": "/mnt/asterisk_media/ai-generated"},
            header="Auto-fix: media directory",
        )
        if result.added_keys:
            fixes_applied.append("Added AST_MEDIA_DIR to .env (requires container restart)")
        elif result.updated_keys:
            fixes_applied.append("Updated AST_MEDIA_DIR in .env (requires container restart)")
    except Exception as e:
        errors.append(f"Failed to update .env: {str(e)}")
    
    return {
        "success": len(errors) == 0,
        "fixes_applied": fixes_applied,
        "errors": errors,
        "manual_steps": manual_steps if manual_steps else None,
        "restart_required": any("restart" in f.lower() for f in fixes_applied)
    }


# =============================================================================
# Platform Detection API (AAVA-126)
# =============================================================================

class PlatformCheck(BaseModel):
    id: str
    status: str  # ok, warning, error
    message: str
    blocking: bool
    action: dict = None

class PlatformInfo(BaseModel):
    os: dict
    docker: dict
    compose: dict
    project: dict = None
    selinux: dict = None
    directories: dict
    asterisk: dict = None

class PlatformResponse(BaseModel):
    platform: PlatformInfo
    checks: List[PlatformCheck]
    summary: dict


_PLATFORMS_CACHE = None
_PLATFORMS_CACHE_MTIME = None


def _detect_project_version(project_root: str) -> dict:
    """
    Best-effort project version detection for Admin UI.

    Preference order:
      1) AAVA_PROJECT_VERSION env var (operator override)
      2) git describe (when repo checkout is present)
      3) Parse README.md for a `vX.Y.Z` token
      4) unknown
    """
    override = (os.getenv("AAVA_PROJECT_VERSION") or "").strip()
    if override:
        return {"version": override, "source": "env"}

    try:
        # Use -c safe.directory to avoid "dubious ownership" failures on some hosts.
        proc = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={project_root}",
                "-C",
                project_root,
                "describe",
                "--tags",
                "--always",
                "--dirty",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if proc.returncode == 0:
            version = (proc.stdout or "").strip()
            if version:
                return {"version": version, "source": "git"}
    except Exception:
        pass

    try:
        readme_path = os.path.join(project_root, "README.md")
        if os.path.exists(readme_path):
            with open(readme_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            m = re.search(r"\bv\d+\.\d+\.\d+\b", text)
            if m:
                return {"version": m.group(0), "source": "readme"}
    except Exception:
        pass

    return {"version": "unknown", "source": "unknown"}


def _github_docs_url(path_or_url: Optional[str]) -> Optional[str]:
    if not path_or_url:
        return None
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    base = os.getenv(
        "AAVA_DOCS_BASE_URL",
        "https://github.com/hkjarral/Asterisk-AI-Voice-Agent/blob/main/",
    )
    return base.rstrip("/") + "/" + path_or_url.lstrip("/")


def _load_platforms_yaml() -> Optional[dict]:
    global _PLATFORMS_CACHE, _PLATFORMS_CACHE_MTIME

    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    path = os.path.join(project_root, "config", "platforms.yaml")
    if not os.path.exists(path):
        return None

    try:
        mtime = os.path.getmtime(path)
        if _PLATFORMS_CACHE is not None and _PLATFORMS_CACHE_MTIME == mtime:
            return _PLATFORMS_CACHE
    except Exception:
        mtime = None

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        _PLATFORMS_CACHE = data
        _PLATFORMS_CACHE_MTIME = mtime
        return data
    except Exception:
        return None


def _deep_merge_dict(base: dict, override: dict) -> dict:
    out = dict(base or {})
    for k, v in (override or {}).items():
        if k == "inherit":
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out.get(k), v)
        else:
            out[k] = v
    return out


def _resolve_platform(platforms: dict, platform_key: str) -> Optional[dict]:
    if not platforms or not platform_key:
        return None
    node = platforms.get(platform_key)
    if not isinstance(node, dict):
        return None

    parent_key = node.get("inherit")
    if parent_key:
        parent = _resolve_platform(platforms, parent_key) or {}
        return _deep_merge_dict(parent, node)
    return dict(node)


def _select_platform_key(platforms: Optional[dict], os_id: str, os_family: str) -> Optional[str]:
    if not platforms:
        return None

    if os_id and os_id in platforms and isinstance(platforms.get(os_id), dict):
        if platforms[os_id].get("name") or platforms[os_id].get("docker"):
            return os_id

    # Find by os_ids membership.
    for key, node in platforms.items():
        if not isinstance(node, dict):
            continue
        ids = node.get("os_ids") or []
        if isinstance(ids, list) and os_id in ids:
            return key

    # Fallback to family base.
    if os_family and os_family in platforms and isinstance(platforms.get(os_family), dict):
        return os_family

    return None


def _detect_os():
    """Detect OS from /etc/os-release or container environment."""
    os_info = {
        "id": "unknown",
        "version": "unknown", 
        "family": "unknown",
        "arch": os.uname().machine,
        "is_eol": False,
        "in_container": os.path.exists("/.dockerenv")
    }
    
    # Try to read host OS info (mounted from host in docker-compose)
    os_release_paths = [
        "/host/etc/os-release",  # Mounted from host
        "/etc/os-release"         # Container's own
    ]
    
    for path in os_release_paths:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    for line in f:
                        if line.startswith("ID="):
                            os_info["id"] = line.split("=")[1].strip().strip('"')
                        elif line.startswith("VERSION_ID="):
                            os_info["version"] = line.split("=")[1].strip().strip('"')
                
                # Determine family
                os_id = os_info["id"]
                if os_id in ["ubuntu", "debian", "linuxmint"]:
                    os_info["family"] = "debian"
                elif os_id in ["centos", "rhel", "rocky", "almalinux", "fedora"]:
                    os_info["family"] = "rhel"
                
                # Check EOL status
                eol_versions = {
                    "ubuntu": ["18.04", "20.04"],
                    "debian": ["9", "10"],
                    "centos": ["7", "8"]
                }
                if os_info["version"] in eol_versions.get(os_id, []):
                    os_info["is_eol"] = True
                
                break
            except Exception:
                pass
    
    return os_info


def _detect_docker():
    """Detect Docker version and mode."""
    sock_path = "/var/run/docker.sock"
    socket_present = os.path.exists(sock_path)
    docker_info = {
        "installed": False,
        "reachable": False,
        "version": None,
        "api_version": None,
        "mode": "unknown",
        "status": "error",
        "message": "Docker not detected",
        "socket_present": socket_present,
        "socket_path": sock_path,
        "socket_gid": None,
        "socket_mode": None,
        "process_uid": os.getuid(),
        "process_gid": os.getgid(),
        "process_groups": list(os.getgroups()),
        "cli_present": shutil.which("docker") is not None,
        "is_docker_desktop": False,
        "permission_denied": False,
        "needs_docker_gid": None,
    }

    if socket_present:
        try:
            st = os.stat(sock_path)
            docker_info["socket_gid"] = int(getattr(st, "st_gid", 0))
            docker_info["socket_mode"] = oct(getattr(st, "st_mode", 0) & 0o777)
            if docker_info["socket_gid"] is not None:
                docker_info["needs_docker_gid"] = docker_info["socket_gid"] not in set(docker_info["process_groups"] or [])
        except Exception:
            pass
    
    try:
        client = docker.from_env()
        version_info = client.version()
        docker_info["installed"] = True
        docker_info["reachable"] = True
        docker_info["version"] = version_info.get("Version", "unknown")
        docker_info["api_version"] = version_info.get("ApiVersion", "unknown")
        docker_info["status"] = "ok"
        docker_info["message"] = None

        # Docker Desktop / Engine metadata (helps Tier-3 messaging)
        try:
            info = client.info()
            operating_system = info.get("OperatingSystem") or ""
            docker_info["operating_system"] = operating_system or None
            docker_info["engine_arch"] = info.get("Architecture") or None
            docker_info["os_type"] = info.get("OSType") or None
            docker_info["is_docker_desktop"] = "Docker Desktop" in operating_system
        except Exception:
            pass
        
        # Check version
        try:
            major = int(docker_info["version"].split(".")[0])
            if major < 20:
                docker_info["status"] = "error"
                docker_info["message"] = "Docker version too old (minimum: 20.10)"
            elif major < 25:
                docker_info["status"] = "warning"
                docker_info["message"] = "Upgrade to Docker 25.x+ recommended"
        except:
            pass
        
        # Detect rootless (check socket path)
        docker_host = os.environ.get("DOCKER_HOST", "")
        if "rootless" in docker_host or "/run/user/" in docker_host:
            docker_info["mode"] = "rootless"
        else:
            docker_info["mode"] = "rootful"
            
    except Exception as e:
        msg = str(e) if e is not None else "unknown error"
        docker_info["message"] = msg
        docker_info["reachable"] = False

        # Distinguish common "Docker installed but not accessible" cases.
        # In practice this is usually a docker.sock mount/GID mismatch in admin_ui.
        lowered = msg.lower()
        if "permission denied" in lowered or "errno 13" in lowered:
            docker_info["installed"] = docker_info["cli_present"] or docker_info["socket_present"]
            docker_info["status"] = "error"
            docker_info["permission_denied"] = True
            docker_info["message"] = "Docker daemon not accessible from Admin UI (permission denied to docker.sock)"
    
    return docker_info


def _detect_compose():
    """Detect Docker Compose version."""
    import subprocess
    
    compose_info = {
        "installed": False,
        "version": None,
        "type": "unknown",
        "status": "error",
        "message": "Docker Compose not detected"
    }

    def _parse_version(text: str) -> str:
        import re

        if not text:
            return ""
        text = text.strip()
        # Common outputs:
        # - "v2.24.6"
        # - "2.24.6"
        # - "Docker Compose version v2.24.6"
        m = re.search(r"\bv?(\d+\.\d+\.\d+)\b", text)
        return m.group(1) if m else ""

    def _classify_version(version: str) -> None:
        # version is normalized to "X.Y.Z"
        try:
            parts = version.split(".")
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else 0
        except Exception:
            return

        if major > 2:
            # Compose may report major versions >2 depending on packaging; treat as modern.
            return
        if major == 2:
            if minor < 20:
                compose_info["status"] = "warning"
                compose_info["message"] = "Upgrade to Compose 2.20+ recommended"
        elif major == 1:
            compose_info["status"] = "error"
            compose_info["message"] = "Compose v1 is EOL and unsupported"

    # Method 1 (preferred): Infer host Compose version from Docker container labels.
    # Compose is client-side; the most reliable way to learn the *host* Compose version
    # (when running Admin UI inside a container) is the version label attached to
    # containers created by Compose on the host.
    #
    # Note: this reflects the Compose version used to create/recreate the current stack.
    try:
        client = docker.from_env()
        versions = []
        for container in client.containers.list():
            labels = container.labels or {}
            v = (labels.get("com.docker.compose.version") or "").strip().lstrip("v")
            if v:
                versions.append(v)

        if versions:
            # If multiple versions exist, pick the most common.
            from collections import Counter

            version = Counter(versions).most_common(1)[0][0]
            compose_info["installed"] = True
            compose_info["version"] = version
            compose_info["type"] = "host_label"
            compose_info["status"] = "ok"
            compose_info["message"] = None
            _classify_version(version)
            return compose_info
    except Exception:
        pass

    # Method 2: Use the same compose command resolution we use for operations (container CLI).
    try:
        compose_cmd = get_docker_compose_cmd()
        # First try `version --short` (supported by docker compose and docker-compose v2)
        result = subprocess.run(
            compose_cmd + ["version", "--short"],
            capture_output=True, text=True, timeout=5
        )
        out = (result.stdout or "").strip()
        if result.returncode != 0 or not out:
            # Fall back to full `version` output and parse.
            result = subprocess.run(
                compose_cmd + ["version"],
                capture_output=True, text=True, timeout=5
            )
            out = (result.stdout or "") + "\n" + (result.stderr or "")

        version = _parse_version(out)
        if version:
            compose_info["installed"] = True
            compose_info["version"] = version
            compose_info["status"] = "ok"
            compose_info["message"] = None

            # Type
            if len(compose_cmd) >= 2 and compose_cmd[-1] == "compose":
                compose_info["type"] = "plugin"
            else:
                # docker-compose binary can be v1 or v2
                compose_info["type"] = "standalone"

            _classify_version(version)
            return compose_info
    except Exception:
        # Compose CLI not available inside container (or docker not in PATH).
        pass
    
    return compose_info


def _detect_selinux():
    """Detect SELinux status."""
    selinux_info = {
        "present": False,
        "mode": None,
        "tools_installed": False
    }
    
    # Check if SELinux is present
    if os.path.exists("/sys/fs/selinux"):
        selinux_info["present"] = True

        # Prefer kernel-provided status (works even if getenforce isn't installed).
        try:
            enforce_path = "/sys/fs/selinux/enforce"
            if os.path.exists(enforce_path):
                with open(enforce_path, "r") as f:
                    val = f.read().strip()
                selinux_info["mode"] = "enforcing" if val == "1" else "permissive"
        except Exception:
            pass
        
        # Get mode
        try:
            import subprocess
            result = subprocess.run(["getenforce"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                selinux_info["mode"] = result.stdout.strip().lower()
        except:
            pass
        
        # Check if semanage is available
        try:
            import subprocess
            result = subprocess.run(["which", "semanage"], capture_output=True, timeout=5)
            selinux_info["tools_installed"] = result.returncode == 0
        except:
            pass
    
    return selinux_info


def _detect_directories():
    """Check required directories."""
    media_dir = os.environ.get("AST_MEDIA_DIR", "/mnt/asterisk_media/ai-generated")
    in_container = os.path.exists("/.dockerenv")
    
    # When running in container, check if media dir is mounted
    # The path inside container may differ from host path
    container_media_path = "/app/media" if in_container else media_dir
    
    # Also check the actual configured path
    paths_to_check = [media_dir, container_media_path, "/mnt/asterisk_media/ai-generated"]
    
    exists = False
    writable = False
    actual_path = media_dir
    
    for path in paths_to_check:
        if os.path.exists(path):
            exists = True
            writable = os.access(path, os.W_OK)
            actual_path = path
            break
    
    # If in container and no local path found, check via Docker client
    if in_container and not exists:
        try:
            client = docker.from_env()
            # Check if there's a volume mount for media
            for container in client.containers.list():
                if container.name in ["ai_engine", "admin_ui"]:
                    mounts = container.attrs.get("Mounts", [])
                    for mount in mounts:
                        if "asterisk_media" in mount.get("Source", "") or "ai-generated" in mount.get("Source", ""):
                            # Volume is mounted on host
                            exists = True
                            writable = True  # Assume writable if mounted
                            actual_path = mount.get("Source", media_dir)
                            break
        except:
            pass
    
    dir_info = {
        "media": {
            "path": actual_path,
            "exists": exists,
            "writable": writable,
            "status": "ok" if (exists and writable) else "warning",
            "in_container": in_container
        }
    }
    
    return dir_info


def _detect_asterisk():
    """Detect Asterisk installation."""
    asterisk_info = {
        "detected": False,
        "version": None,
        "config_dir": None,
        "freepbx": {
            "detected": False,
            "version": None
        }
    }
    
    # Check common paths
    asterisk_paths = ["/etc/asterisk", "/usr/local/etc/asterisk"]
    for path in asterisk_paths:
        if os.path.exists(path) and os.path.exists(os.path.join(path, "asterisk.conf")):
            asterisk_info["detected"] = True
            asterisk_info["config_dir"] = path
            break
    
    # Check for Asterisk binary
    try:
        import subprocess
        result = subprocess.run(["asterisk", "-V"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            asterisk_info["version"] = result.stdout.strip()
    except:
        pass
    
    # Check for FreePBX
    if os.path.exists("/etc/freepbx.conf") or os.path.exists("/etc/sangoma/pbx"):
        asterisk_info["freepbx"]["detected"] = True
        try:
            import subprocess
            result = subprocess.run(["fwconsole", "-V"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                asterisk_info["freepbx"]["version"] = result.stdout.strip()
        except:
            pass
    
    return asterisk_info


def _check_port(port: int, is_own_port: bool = False) -> dict:
    """Check if a port is in use and by what."""
    import socket
    
    result = {
        "port": port,
        "in_use": False,
        "is_own_port": is_own_port,
        "status": "ok"
    }
    
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            connect_result = s.connect_ex(('localhost', port))
            result["in_use"] = (connect_result == 0)
    except:
        pass
    
    # If it's our own port (admin-ui on 3003), it's expected to be in use
    if is_own_port and result["in_use"]:
        result["status"] = "ok"  # Expected
    elif result["in_use"]:
        result["status"] = "warning"
    
    return result


def _build_checks(os_info, docker_info, compose_info, selinux_info, dir_info, asterisk_info, platform_cfg: Optional[dict]) -> List[dict]:
    """Build list of checks with status and actions."""
    checks = []
    docker_cfg = (platform_cfg or {}).get("docker", {}) if isinstance(platform_cfg, dict) else {}
    compose_cfg = (platform_cfg or {}).get("compose", {}) if isinstance(platform_cfg, dict) else {}
    selinux_cfg = (platform_cfg or {}).get("selinux", {}) if isinstance(platform_cfg, dict) else {}
    firewall_cfg = (platform_cfg or {}).get("firewall", {}) if isinstance(platform_cfg, dict) else {}
    
    # Architecture check
    if os_info["arch"] != "x86_64":
        if docker_info.get("is_docker_desktop"):
            checks.append({
                "id": "architecture",
                "status": "warning",
                "message": f"Architecture: {os_info['arch']} (Tier 3 best-effort on Docker Desktop; production requires x86_64 Linux)",
                "blocking": False,
                "action": None
            })
        else:
            checks.append({
                "id": "architecture",
                "status": "error",
                "message": f"Unsupported architecture: {os_info['arch']} (x86_64 required)",
                "blocking": True,
                "action": None
            })
    else:
        checks.append({
            "id": "architecture",
            "status": "ok",
            "message": f"Architecture: {os_info['arch']}",
            "blocking": False,
            "action": None
        })

    # Tier 3 note (Docker Desktop): common source of confusion is "running but not reachable" due to networking differences.
    if docker_info.get("is_docker_desktop"):
        checks.append({
            "id": "tier3_docker_desktop",
            "status": "warning",
            "message": "Docker Desktop detected (Tier 3 best-effort). If services are running but show as unreachable, set HEALTH_CHECK_* URLs and ensure DOCKER_SOCK is correct.",
            "blocking": False,
            "action": {
                "type": "link",
                "label": "Troubleshooting (Tier 3 / Docker Desktop)",
                "value": _github_docs_url("docs/TROUBLESHOOTING_GUIDE.md"),
            }
        })
    
    # OS EOL check
    if os_info["is_eol"]:
        checks.append({
            "id": "os_eol",
            "status": "warning",
            "message": f"{os_info['id']} {os_info['version']} is EOL or nearing EOL",
            "blocking": False,
            "action": {
                "type": "link",
                "label": "Upgrade Guide",
                "value": "https://docs.docker.com/engine/install/"
            }
        })
    
    # Docker check
    if not docker_info["installed"]:
        docs_url = _github_docs_url(docker_cfg.get("aava_docs")) or "https://docs.docker.com/engine/install/"
        # Distinguish "not installed" vs "not reachable from Admin UI" (rootless socket mount is a common cause).
        if not docker_info.get("socket_present", True):
            checks.append({
                "id": "docker_socket",
                "status": "error",
                "message": "Docker socket not accessible to Admin UI (rootless Docker likely)",
                "blocking": True,
                "action": {
                    "type": "command",
                    "label": "Set DOCKER_SOCK and restart admin_ui",
                    "value": "export DOCKER_SOCK=/run/user/$(id -u)/docker.sock && docker compose -p asterisk-ai-voice-agent up -d --force-recreate admin_ui",
                    "docs_url": _github_docs_url(docker_cfg.get("rootless_docs")) or _github_docs_url("docs/CROSS_PLATFORM_PLAN.md"),
                    "docs_label": "Rootless Docker docs",
                }
            })
        else:
            install_cmd = (docker_cfg.get("install_cmd") or "curl -fsSL https://get.docker.com | sh").strip()
            checks.append({
                "id": "docker_installed",
                "status": "error",
                "message": "Docker not detected by Admin UI",
                "blocking": True,
                "action": {
                    "type": "command",
                    "label": "Install Docker",
                    "value": install_cmd,
                    "docs_url": docs_url,
                    "docs_label": "AAVA installation docs",
                }
            })
    elif docker_info.get("permission_denied"):
        checks.append({
            "id": "docker_socket_perms",
            "status": "error",
            "message": (
                "Admin UI cannot access Docker socket (permission denied). "
                f"Socket gid={docker_info.get('socket_gid')}, process groups={docker_info.get('process_groups')}"
            ),
            "blocking": True,
            "action": {
                "type": "command",
                "label": "Set DOCKER_GID and recreate admin_ui",
                "value": "\n".join([
                    "ls -ln /var/run/docker.sock",
                    "DOCKER_GID=$(ls -ln /var/run/docker.sock | awk '{print $4}')",
                    "grep -qE '^[# ]*DOCKER_GID=' .env && sed -i.bak -E \"s/^[# ]*DOCKER_GID=.*/DOCKER_GID=$DOCKER_GID/\" .env || echo \"DOCKER_GID=$DOCKER_GID\" >> .env",
                    "docker compose -p asterisk-ai-voice-agent up -d --force-recreate admin_ui",
                ]),
                "docs_url": _github_docs_url("docs/TROUBLESHOOTING_GUIDE.md"),
                "docs_label": "Troubleshooting guide",
            },
        })
    elif docker_info["status"] == "error":
        docs_url = _github_docs_url(docker_cfg.get("aava_docs")) or "https://docs.docker.com/engine/install/"
        start_cmd = docker_cfg.get("start_cmd") or "sudo systemctl start docker"
        rootless_start_cmd = docker_cfg.get("rootless_start_cmd")
        checks.append({
            "id": "docker_version",
            "status": "error",
            "message": docker_info["message"],
            "blocking": True,
            "action": {
                "type": "command",
                "label": "Start Docker (or upgrade if needed)",
                "value": start_cmd,
                "rootless_value": rootless_start_cmd,
                "docs_url": docs_url,
                "docs_label": "AAVA installation docs",
            }
        })
    elif docker_info["status"] == "warning":
        checks.append({
            "id": "docker_version",
            "status": "warning",
            "message": docker_info["message"],
            "blocking": False,
            "action": None
        })
    else:
        checks.append({
            "id": "docker_version",
            "status": "ok",
            "message": f"Docker {docker_info['version']}",
            "blocking": False,
            "action": None
        })
    
    # Compose check
    if not compose_info["installed"]:
        docs_url = _github_docs_url(compose_cfg.get("aava_docs")) or "https://docs.docker.com/compose/install/"
        install_cmd = (compose_cfg.get("install_cmd") or "sudo apt-get install -y docker-compose-plugin").strip()
        checks.append({
            "id": "compose_installed",
            "status": "error",
            "message": "Docker Compose not installed",
            "blocking": True,
            "action": {
                "type": "command",
                "label": "Install Docker Compose",
                "value": install_cmd,
                "docs_url": docs_url,
                "docs_label": "AAVA installation docs",
            }
        })
    elif compose_info["status"] == "error":
        docs_url = _github_docs_url(compose_cfg.get("aava_docs")) or "https://docs.docker.com/compose/install/"
        upgrade_cmd = (compose_cfg.get("upgrade_cmd") or compose_cfg.get("install_cmd") or "").strip()
        checks.append({
            "id": "compose_version",
            "status": "error",
            "message": compose_info["message"],
            "blocking": True,
            "action": {
                "type": "command",
                "label": "Upgrade Docker Compose",
                "value": upgrade_cmd or "sudo apt-get update && sudo apt-get install -y docker-compose-plugin",
                "docs_url": docs_url,
                "docs_label": "AAVA installation docs",
            }
        })
    elif compose_info["status"] == "warning":
        checks.append({
            "id": "compose_version",
            "status": "warning",
            "message": compose_info["message"],
            "blocking": False,
            "action": None
        })
    else:
        checks.append({
            "id": "compose_version",
            "status": "ok",
            "message": f"Docker Compose {compose_info['version']}",
            "blocking": False,
            "action": None
        })
    
    # Media directory check
    media = dir_info["media"]
    if not media["exists"]:
        checks.append({
            "id": "media_directory",
            "status": "warning",
            "message": f"Media directory missing: {media['path']}",
            "blocking": False,
            "action": {
                "type": "command",
                "label": "Create Directory",
                "value": f"sudo mkdir -p {media['path']} && sudo chown -R $(id -u):$(id -g) {media['path']}",
                "rootless_value": f"mkdir -p {media['path']}",
                "docs_url": _github_docs_url((platform_cfg or {}).get("docker", {}).get("aava_docs")) or _github_docs_url("docs/INSTALLATION.md"),
                "docs_label": "Media directory docs",
            }
        })
    elif not media["writable"]:
        checks.append({
            "id": "media_directory",
            "status": "warning",
            "message": f"Media directory not writable: {media['path']}",
            "blocking": False,
            "action": {
                "type": "command",
                "label": "Fix Permissions",
                "value": f"sudo chown -R $(id -u):$(id -g) {media['path']}",
                "rootless_value": None,
                "docs_url": _github_docs_url((platform_cfg or {}).get("docker", {}).get("aava_docs")) or _github_docs_url("docs/INSTALLATION.md"),
                "docs_label": "Media directory docs",
            }
        })
    else:
        checks.append({
            "id": "media_directory",
            "status": "ok",
            "message": f"Media directory: {media['path']}",
            "blocking": False,
            "action": None
        })
    
    # SELinux check
    if selinux_info["present"] and selinux_info["mode"] == "enforcing":
        if not selinux_info["tools_installed"]:
            docs_url = _github_docs_url(selinux_cfg.get("aava_docs")) or _github_docs_url("docs/INSTALLATION.md")
            install_cmd = (selinux_cfg.get("tools_install_cmd") or "sudo dnf install -y policycoreutils-python-utils").strip()
            checks.append({
                "id": "selinux",
                "status": "warning",
                "message": "SELinux enforcing but semanage not installed",
                "blocking": False,
                "action": {
                    "type": "command",
                    "label": "Install SELinux Tools",
                    "value": install_cmd,
                    "docs_url": docs_url,
                    "docs_label": "SELinux docs",
                }
            })
        else:
            docs_url = _github_docs_url(selinux_cfg.get("aava_docs")) or _github_docs_url("docs/INSTALLATION.md")
            context_cmd_tmpl = selinux_cfg.get("context_cmd") or "sudo semanage fcontext -a -t container_file_t '{path}(/.*)?'"
            restore_cmd_tmpl = selinux_cfg.get("restore_cmd") or "sudo restorecon -Rv {path}"
            ctx_cmd = context_cmd_tmpl.format(path=media["path"])
            restore_cmd = restore_cmd_tmpl.format(path=media["path"])
            checks.append({
                "id": "selinux",
                "status": "warning",
                "message": "SELinux enforcing - context fix may be needed",
                "blocking": False,
                "action": {
                    "type": "command",
                    "label": "Fix SELinux Context",
                    "value": f"{ctx_cmd} && {restore_cmd}",
                    "docs_url": docs_url,
                    "docs_label": "SELinux docs",
                }
            })
    elif selinux_info["present"]:
        checks.append({
            "id": "selinux",
            "status": "ok",
            "message": f"SELinux: {selinux_info['mode'] or 'disabled'}",
            "blocking": False,
            "action": None
        })
    
    # Port check - port 3003 is admin-ui's own port, so it's expected to be in use
    port_check = _check_port(3003, is_own_port=True)
    # Only show port check if it's NOT in use (which would be unexpected for admin-ui)
    # or skip entirely since this is admin-ui's port
    # We'll show a success message instead
    checks.append({
        "id": "port_3003",
        "status": "ok",
        "message": "Admin UI port 3003 active",
        "blocking": False,
        "action": None
    })
    
    # Asterisk check
    if asterisk_info["detected"]:
        checks.append({
            "id": "asterisk",
            "status": "ok",
            "message": f"Asterisk config: {asterisk_info['config_dir']}",
            "blocking": False,
            "action": None
        })
        if asterisk_info["freepbx"]["detected"]:
            checks.append({
                "id": "freepbx",
                "status": "ok",
                "message": f"FreePBX: {asterisk_info['freepbx']['version'] or 'detected'}",
                "blocking": False,
                "action": None
            })
    
    return checks


@router.get("/platform")
async def get_platform():
    """
    Get platform detection and check results.
    AAVA-126: Cross-Platform Support
    """
    os_info = _detect_os()
    docker_info = _detect_docker()
    compose_info = _detect_compose()
    selinux_info = _detect_selinux()
    dir_info = _detect_directories()
    asterisk_info = _detect_asterisk()
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    project_info = _detect_project_version(project_root)

    platforms = _load_platforms_yaml()
    platform_key = _select_platform_key(platforms, os_info.get("id"), os_info.get("family"))
    platform_cfg = _resolve_platform(platforms or {}, platform_key) if platform_key else None
    if isinstance(platform_cfg, dict):
        platform_cfg["_key"] = platform_key

    checks = _build_checks(os_info, docker_info, compose_info, selinux_info, dir_info, asterisk_info, platform_cfg)
    
    # Build summary
    passed = sum(1 for c in checks if c["status"] == "ok")
    warnings = sum(1 for c in checks if c["status"] == "warning")
    errors = sum(1 for c in checks if c["status"] == "error")
    blocking = sum(1 for c in checks if c.get("blocking", False))
    
    return {
        "platform": {
            "os": os_info,
            "docker": docker_info,
            "compose": compose_info,
            "project": project_info,
            "selinux": selinux_info,
            "directories": dir_info,
            "asterisk": asterisk_info,
            "platform_key": platform_key,
        },
        "checks": checks,
        "summary": {
            "total_checks": len(checks),
            "passed": passed,
            "warnings": warnings,
            "errors": errors,
            "blocking_errors": blocking,
            "ready": blocking == 0
        }
    }


@router.post("/preflight")
async def run_preflight():
    """
    Re-run preflight checks and return fresh results.
    AAVA-126: Cross-Platform Support
    """
    # Same as GET /platform but explicitly named for clarity
    return await get_platform()


class ContainerAction(BaseModel):
    containers: List[str] = None  # None = all


@router.post("/containers/start")
async def start_containers(action: ContainerAction = None):
    """Start containers."""
    import subprocess
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    
    cmd = ["docker", "compose", "up", "-d"]
    if action and action.containers:
        cmd.extend(action.containers)
    
    try:
        result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=120)
        return {
            "success": result.returncode == 0,
            "output": result.stdout or result.stderr
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/containers/stop")
async def stop_containers(action: ContainerAction = None):
    """Stop containers."""
    import subprocess
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    
    cmd = ["docker", "compose", "stop"]
    if action and action.containers:
        cmd.extend(action.containers)
    
    try:
        result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=120)
        return {
            "success": result.returncode == 0,
            "output": result.stdout or result.stderr
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/containers/restart-all")
async def restart_all_containers():
    """Restart all containers."""
    import subprocess
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    
    try:
        # Stop
        subprocess.run(["docker", "compose", "stop"], cwd=project_root, timeout=60)
        # Start
        result = subprocess.run(["docker", "compose", "up", "-d"], cwd=project_root, capture_output=True, text=True, timeout=120)
        return {
            "success": result.returncode == 0,
            "output": result.stdout or result.stderr
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AriTestRequest(BaseModel):
    host: str
    port: int = 8088
    username: str
    password: str
    scheme: str = "http"
    ssl_verify: bool = True  # Set to False for self-signed certs


@router.post("/test-ari")
async def test_ari_connection(request: AriTestRequest):
    """Test connection to Asterisk ARI endpoint"""
    import httpx
    
    try:
        # Build ARI URL
        ari_url = f"{request.scheme}://{request.host}:{request.port}/ari/asterisk/info"
        
        # Configure SSL verification (disable for self-signed certs)
        verify = request.ssl_verify if request.scheme == "https" else True
        
        async with httpx.AsyncClient(timeout=10.0, verify=verify) as client:
            response = await client.get(
                ari_url,
                auth=(request.username, request.password)
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "message": "Successfully connected to Asterisk ARI",
                    "asterisk_version": data.get("system", {}).get("version", "Unknown"),
                    "build": data.get("build", {})
                }
            elif response.status_code == 401:
                return {
                    "success": False,
                    "error": "Authentication failed - check username and password"
                }
            elif response.status_code == 403:
                return {
                    "success": False,
                    "error": "Access forbidden - check ARI user permissions"
                }
            else:
                return {
                    "success": False,
                    "error": f"Unexpected response: HTTP {response.status_code}"
                }
                
    except httpx.ConnectError as e:
        logger.debug("ARI connection error", exc_info=True)
        error_str = str(e).lower()
        # Check for SSL-specific errors
        if "ssl" in error_str or "certificate" in error_str:
            return {
                "success": False,
                "error": "SSL certificate error - try disabling 'Verify SSL Certificate' for self-signed certs."
            }
        return {
            "success": False,
            "error": f"Connection refused - is Asterisk running at {request.host}:{request.port}?"
        }
    except httpx.ConnectTimeout:
        return {
            "success": False,
            "error": f"Connection timeout - check if {request.host}:{request.port} is reachable"
        }
    except Exception as e:
        logger.debug("ARI connection failed", exc_info=True)
        error_str = str(e).lower()
        # Check for SSL-specific errors in generic exceptions
        if "ssl" in error_str or "certificate" in error_str or "verify" in error_str:
            return {
                "success": False,
                "error": "SSL certificate verification failed - uncheck 'Verify SSL Certificate' for self-signed certs or hostname mismatches."
            }
        return {
            "success": False,
            "error": "Connection failed - check host/port/scheme and credentials."
        }


# =============================================================================
# Updates API (UI-driven agent update)
# =============================================================================

_UPDATER_IMAGE_REPO = "asterisk-ai-voice-agent-updater"
_UPDATER_IMAGE_LOCK = None
_UPDATES_STATUS_CACHE: dict = {"checked_at": 0.0, "data": None}
_UPDATES_STATUS_CACHE_TTL_SEC = 600  # 10 minutes
_UPDATES_STATUS_CACHE_LOCK = None


def _updater_remote_image_repo() -> str:
    """
    Return the remote registry/repo for the updater image.

    Default matches our GHCR naming convention for other published images.
    """
    explicit = (os.getenv("AAVA_UPDATER_IMAGE_REMOTE") or "").strip()
    if explicit:
        return explicit
    owner = (os.getenv("AAVA_GHCR_OWNER") or "hkjarral").strip().lower()
    return f"ghcr.io/{owner}/{_UPDATER_IMAGE_REPO}"


def _updater_lock():
    global _UPDATER_IMAGE_LOCK
    if _UPDATER_IMAGE_LOCK is None:
        import threading
        _UPDATER_IMAGE_LOCK = threading.Lock()
    return _UPDATER_IMAGE_LOCK


def _updates_status_cache_lock():
    global _UPDATES_STATUS_CACHE_LOCK
    if _UPDATES_STATUS_CACHE_LOCK is None:
        import threading
        _UPDATES_STATUS_CACHE_LOCK = threading.Lock()
    return _UPDATES_STATUS_CACHE_LOCK


# Allow an optional registry host with port prefix (e.g. "registry.example.com:5000/...").
_DOCKER_IMAGE_REF_RE = re.compile(
    r"^(?:[a-z0-9]+(?:[._-][a-z0-9]+)*:[0-9]+/)?"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127})?$"
)


def _validate_docker_image_ref(ref: str) -> str:
    r = (ref or "").strip()
    if not r or r.startswith("-") or any(c.isspace() for c in r) or "\x00" in r:
        raise ValueError("invalid docker image ref")
    if not _DOCKER_IMAGE_REF_RE.fullmatch(r):
        raise ValueError("invalid docker image ref")
    return r


def _run_docker(args: list[str], *, cwd: Optional[str] = None, timeout_sec: int = 60) -> tuple[int, str]:
    """
    Run Docker CLI and return (exit_code, combined_output).
    """
    if not args or any((not isinstance(a, str) or a == "" or "\x00" in a or any(c.isspace() for c in a)) for a in args):
        raise ValueError("invalid docker args")
    try:
        proc = subprocess.run(
            ["docker", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=os.environ.copy(),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return int(proc.returncode), out
    except subprocess.TimeoutExpired as e:
        out = (getattr(e, "stdout", "") or "") + (getattr(e, "stderr", "") or "")
        return 124, out or "timeout"
    except Exception as e:
        return 1, str(e)


def _is_semver_tag(ref: str) -> bool:
    r = (ref or "").strip()
    if not r:
        return False
    if r.startswith("v"):
        r = r[1:]
    return bool(re.match(r"^[0-9]+\.[0-9]+\.[0-9]+$", r))


def _updater_pull_tags_for_ref(ref: str) -> list[str]:
    """
    Return candidate remote tags for pulling a prebuilt updater image.

    Release workflows publish both vX.Y.Z and X.Y.Z tags; try both plus latest.
    """
    r = (ref or "").strip()
    if not r:
        return ["latest"]
    # For non-version selectors, don't try to synthesize `v<ref>` variants.
    if r in {"latest", "main"}:
        return [r]

    tags: list[str] = [r]
    if _is_semver_tag(r):
        if r.startswith("v"):
            tags.append(r[1:])
        else:
            tags.append("v" + r)
    tags.append("latest")
    # preserve order while de-duping
    seen = set()
    uniq: list[str] = []
    for t in tags:
        if not t or t in seen:
            continue
        seen.add(t)
        uniq.append(t)
    return uniq


def _project_host_root_from_admin_ui_container() -> str:
    """
    Resolve the host path that backs /app/project in the admin_ui container.

    We need a host path because Docker binds are evaluated by the daemon on the host,
    not from inside this container.
    """
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    # Prefer a stable container name. Our compose file uses `container_name: admin_ui`.
    # HOSTNAME can be the host's hostname in some deployments, so it's not reliable.
    explicit_name = (os.getenv("AAVA_ADMIN_UI_CONTAINER_NAME") or "").strip()
    candidates = [c for c in [explicit_name, "admin_ui", (os.getenv("HOSTNAME") or "").strip()] if c]

    try:
        client = docker.from_env()

        c = None
        last_err = None
        for ident in candidates:
            try:
                c = client.containers.get(ident)
                break
            except Exception as e:
                last_err = e
                continue
        if c is None:
            raise last_err or RuntimeError("container lookup failed")

        mounts = c.attrs.get("Mounts", []) or []
        for m in mounts:
            if m.get("Destination") == project_root and m.get("Type") == "bind":
                src = (m.get("Source") or "").strip()
                if src:
                    return src
        raise HTTPException(status_code=500, detail=f"Cannot resolve host mount backing {project_root}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to resolve project host root: %s", e)
        raise HTTPException(status_code=500, detail="Failed to resolve project host root")


def _docker_sock_host_path_from_admin_ui_container() -> str:
    """
    Resolve the host path that backs /var/run/docker.sock in the admin_ui container.

    Some deployments mount a non-standard Docker socket path (via DOCKER_SOCK in `.env`).
    When starting updater containers, we must mount the *host* socket path.
    """
    explicit_name = (os.getenv("AAVA_ADMIN_UI_CONTAINER_NAME") or "").strip()
    candidates = [c for c in [explicit_name, "admin_ui", (os.getenv("HOSTNAME") or "").strip()] if c]

    try:
        client = docker.from_env()

        c = None
        last_err = None
        for ident in candidates:
            try:
                c = client.containers.get(ident)
                break
            except Exception as e:
                last_err = e
                continue
        if c is None:
            raise last_err or RuntimeError("container lookup failed")

        mounts = c.attrs.get("Mounts", []) or []
        for m in mounts:
            if m.get("Destination") == "/var/run/docker.sock" and m.get("Type") == "bind":
                src = (m.get("Source") or "").strip()
                if src:
                    return src
    except Exception:
        # Fall back to the default path; most hosts use /var/run/docker.sock (or a symlink).
        pass

    return "/var/run/docker.sock"


def _ensure_updater_image(host_project_root: str) -> None:
    """
    Ensure the updater image exists locally; build it on-demand from the project checkout.
    """
    raise RuntimeError("_ensure_updater_image requires a tag; use _ensure_updater_image_for_sha")


def _current_project_head_sha() -> Optional[str]:
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    try:
        # Prefer git when available, but fall back to reading `.git/HEAD` directly because
        # the admin_ui container may not include the git binary.
        import shutil
        if shutil.which("git"):
            proc = subprocess.run(
                ["git", "-c", f"safe.directory={project_root}", "-C", project_root, "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=1.5,
            )
            if proc.returncode == 0:
                sha = (proc.stdout or "").strip()
                if sha:
                    return sha

        def _read_text(path: str) -> Optional[str]:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return None

        def _resolve_gitdir(root: str) -> Optional[str]:
            git_path = os.path.join(root, ".git")
            if os.path.isdir(git_path):
                return git_path
            if os.path.isfile(git_path):
                # Worktree/submodule style: `.git` file contains `gitdir: <path>`
                raw = _read_text(git_path) or ""
                raw = raw.strip()
                if raw.startswith("gitdir:"):
                    gd = raw.split(":", 1)[1].strip()
                    if not gd:
                        return None
                    if not os.path.isabs(gd):
                        gd = os.path.normpath(os.path.join(root, gd))
                    return gd
            return None

        def _read_packed_ref(gitdir: str, ref: str) -> Optional[str]:
            import os
            packed = _read_text(os.path.join(gitdir, "packed-refs"))
            if not packed:
                return None
            for line in packed.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("^"):
                    continue
                parts = line.split()
                if len(parts) != 2:
                    continue
                sha, name = parts
                if name == ref:
                    return sha
            return None

        gitdir = _resolve_gitdir(project_root)
        if not gitdir:
            return None

        head = (_read_text(f"{gitdir}/HEAD") or "").strip()
        if not head:
            return None
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            if not ref:
                return None
            ref_path = os.path.join(gitdir, ref)
            sha = (_read_text(ref_path) or "").strip()
            if sha:
                return sha
            return _read_packed_ref(gitdir, ref)
        return head
    except Exception:
        pass
    return None


def _updater_image_tag_for_sha(sha: Optional[str]) -> str:
    if not sha:
        return f"{_UPDATER_IMAGE_REPO}:latest"
    return f"{_UPDATER_IMAGE_REPO}:sha-{sha[:12]}"


def _ensure_updater_image_for_sha(host_project_root: str, tag: str) -> None:
    lock = _updater_lock()
    with lock:
        try:
            # `host_project_root` is used for bind mounts when *running* updater containers, but for
            # building the updater image we must use a path that exists inside this container.
            if not host_project_root:
                raise HTTPException(
                    status_code=500,
                    detail=f"Invalid project root for updater build: {host_project_root!r}",
                )

            client = docker.from_env()
            try:
                client.images.get(tag)
                return
            except Exception:
                pass

            build_root = os.getenv("PROJECT_ROOT", "/app/project")
            # Avoid docker-py image build streaming decode issues by using Docker CLI.
            # Always use host networking to avoid restricted bridge DNS/egress environments.
            logger.info(
                "Building updater image: %s (context=%s, network=host)",
                _sanitize_for_log(tag),
                _sanitize_for_log(build_root),
            )
            safe_tag = _validate_docker_image_ref(tag)
            code, out = _run_docker(
                ["build", "--network=host", "-f", "updater/Dockerfile", "-t", safe_tag, "."],
                cwd=build_root,
                timeout_sec=1800,
            )
            if code != 0:
                tail = "\n".join((out or "").splitlines()[-40:]).strip()
                hint = (
                    "If this fails due to DNS/egress restrictions, try on the host:\n"
                    "  cd /root/Asterisk-AI-Voice-Agent\n"
                    "  docker buildx build --network=host --progress=plain -f updater/Dockerfile .\n"
                    "Or update via CLI:\n"
                    "  agent update"
                )
                if tail:
                    logger.error("Updater build failed (tail):\n%s", tail)
                    raise HTTPException(status_code=500, detail=f"Failed to build updater image:\n{tail}\n\n{hint}")
                raise HTTPException(status_code=500, detail=f"Failed to build updater image.\n\n{hint}")
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Failed to build updater image")
            raise HTTPException(status_code=500, detail="Failed to build updater image") from e


def _ensure_updater_image_for_ref(host_project_root: str, local_tag: str, *, prefer_pull_ref: Optional[str], allow_build: bool) -> None:
    """
    Ensure the updater image exists locally under `local_tag`.

    For stable release tag updates, prefer pulling a published updater image from GHCR,
    and retag it to the local tag used by updater jobs.
    """
    client = docker.from_env()
    try:
        client.images.get(local_tag)
        return
    except Exception:
        pass

    # Best-effort: pull a published updater image (preferred for most installs).
    if prefer_pull_ref:
        remote_repo = _updater_remote_image_repo()
        for t in _updater_pull_tags_for_ref(prefer_pull_ref):
            remote_ref = _validate_docker_image_ref(f"{remote_repo}:{t}")
            code, out = _run_docker(["pull", remote_ref], timeout_sec=900)
            if code == 0:
                _run_docker(["tag", remote_ref, _validate_docker_image_ref(local_tag)], timeout_sec=60)
                logger.info("Pulled updater image: %s -> %s", _sanitize_for_log(remote_ref), _sanitize_for_log(local_tag))
                return
            logger.warning(
                "Failed to pull updater image %s: %s",
                _sanitize_for_log(remote_ref),
                _sanitize_for_log((out or "").strip().splitlines()[-1:][0] if (out or "").strip().splitlines() else ""),
            )

    if not allow_build:
        raise HTTPException(status_code=500, detail="Updater image missing and local build is disabled")

    _ensure_updater_image_for_sha(host_project_root, local_tag)


def _run_updater_ephemeral(
    host_project_root: str,
    *,
    env: dict,
    command: Optional[str] = None,
    timeout_sec: int = 30,
    capture_stderr: bool = True,
    prefer_pull_ref: Optional[str] = None,
    allow_build: bool = True,
) -> tuple[int, str]:
    """
    Run the updater image as a short-lived container and return (exit_code, stdout/stderr).

    If command is provided, we override the default entrypoint with bash -lc <command>.
    """
    import uuid

    sha = _current_project_head_sha()
    tag = _updater_image_tag_for_sha(sha)
    _ensure_updater_image_for_ref(host_project_root, tag, prefer_pull_ref=prefer_pull_ref, allow_build=allow_build)

    client = docker.from_env()
    name = f"aava-update-ephemeral-{uuid.uuid4().hex[:10]}"

    host_docker_sock = _docker_sock_host_path_from_admin_ui_container()
    volumes = {
        # IMPORTANT: mount the project at the same absolute path inside the container as on the host.
        # Docker Compose resolves relative bind mounts on the HOST filesystem, so if we mounted the repo
        # at a different in-container path (e.g. /app/project), compose would hand Docker non-existent
        # host paths like /app/project/src.
        host_project_root: {"bind": host_project_root, "mode": "rw"},
        host_docker_sock: {"bind": "/var/run/docker.sock", "mode": "rw"},
    }

    try:
        if command:
            # NOTE: docker-py will split string commands (like a shell), which breaks `bash -lc "<cmd>"`
            # by making only the first token the `-c` argument (often `set`), and the remainder positional args.
            # Always pass the command as a single argv element so bash receives it as one string.
            container = client.containers.run(
                tag,
                command=[command],
                entrypoint=["bash", "-lc"],
                environment=env,
                volumes=volumes,
                name=name,
                detach=True,
            )
        else:
            container = client.containers.run(
                tag,
                environment=env,
                volumes=volumes,
                name=name,
                detach=True,
            )

        result = container.wait(timeout=timeout_sec)
        status = int((result or {}).get("StatusCode", 1))
        logs = (container.logs(stdout=True, stderr=capture_stderr) or b"").decode("utf-8", errors="replace")
        return status, logs
    finally:
        try:
            c = client.containers.get(name)
            c.remove(force=True)
        except Exception:
            pass


def _parse_semver_tag(tag: str) -> Optional[tuple[int, int, int]]:
    tag = (tag or "").strip()
    if not tag.startswith("v"):
        return None
    parts = tag[1:].split(".")
    if len(parts) < 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except Exception:
        return None


def _select_latest_v_tag(ls_remote_text: str) -> Optional[dict]:
    """
    Parse `git ls-remote --tags origin 'refs/tags/v*'` output and return:
      { "tag": "vX.Y.Z", "sha": "<commit-sha>" }

    Handles annotated tags by preferring peeled `^{}` lines.
    """
    refs: dict[str, dict] = {}
    for line in (ls_remote_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            sha, ref = line.split("\t", 1)
        except Exception:
            continue
        if not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/"):]
        peeled = False
        if name.endswith("^{}"):
            name = name[:-3]
            peeled = True
        if not name.startswith("v"):
            continue
        ent = refs.get(name) or {"sha": None, "peeled_sha": None}
        if peeled:
            ent["peeled_sha"] = sha
        else:
            ent["sha"] = sha
        refs[name] = ent

    best = None
    best_ver = None
    for tag, ent in refs.items():
        ver = _parse_semver_tag(tag)
        if not ver:
            continue
        if best_ver is None or ver > best_ver:
            best_ver = ver
            best = {"tag": tag, "sha": ent.get("peeled_sha") or ent.get("sha")}
    if best and best.get("sha"):
        return best
    return None


class UpdateStatusResponse(BaseModel):
    local: dict
    remote: Optional[dict] = None
    update_available: Optional[bool] = None
    changelog_latest: Optional[str] = None
    error: Optional[str] = None


def _extract_changelog_section(changelog_md: str, version: str) -> Optional[str]:
    """
    Extract the Keep-a-Changelog section for a given version (e.g., "5.2.4").
    Returns markdown (including the header) or None if not found.
    """
    if not changelog_md:
        return None
    v = (version or "").strip()
    if v.startswith("v"):
        v = v[1:]
    if not v:
        return None

    lines = changelog_md.splitlines()
    start_idx = None
    pat = f"## [{v}]"
    for i, line in enumerate(lines):
        if line.strip().startswith(pat):
            start_idx = i
            break
    if start_idx is None:
        return None
    end_idx = None
    for j in range(start_idx + 1, len(lines)):
        if lines[j].startswith("## ["):
            end_idx = j
            break
    block = "\n".join(lines[start_idx:end_idx]).strip()
    return block or None


@router.get("/updates/status", response_model=UpdateStatusResponse)
async def updates_status(check_remote: bool = False, build_updater: bool = False, force: bool = False):
    """
    Return update status for the Updates UI.

    IMPORTANT:
    - By default, this endpoint does NOT build the updater image and does NOT contact the remote.
    - The Updates page opts in by setting `check_remote=true` (and optionally `build_updater=true`).
    """
    if not build_updater and not check_remote:
        project_root = os.getenv("PROJECT_ROOT", "/app/project")

        def _read_text(p: str) -> Optional[str]:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return None

        def _current_branch(root: str) -> Optional[str]:
            gitpath = os.path.join(root, ".git")
            gitdir = gitpath
            if os.path.isfile(gitpath):
                raw = (_read_text(gitpath) or "").strip()
                if raw.startswith("gitdir:"):
                    gd = raw.split(":", 1)[1].strip()
                    if not gd:
                        return None
                    if not os.path.isabs(gd):
                        gd = os.path.normpath(os.path.join(root, gd))
                    gitdir = gd
                else:
                    return None

            head = (_read_text(os.path.join(gitdir, "HEAD")) or "").strip()
            if not head:
                return None
            if head.startswith("ref:"):
                ref = head.split(":", 1)[1].strip()
                if ref.startswith("refs/heads/"):
                    return ref[len("refs/heads/") :]
                return ref
            return "detached"

        branch = _current_branch(project_root) or "unknown"
        head_sha = _current_project_head_sha() or "unknown"
        describe = head_sha[:12] if isinstance(head_sha, str) and head_sha not in ("", "unknown") else "unknown"
        return UpdateStatusResponse(
            local={"branch": branch, "head_sha": head_sha, "describe": describe},
            remote=None,
            update_available=None,
            changelog_latest=None,
            error="Not checked",
        )

    # Cache (best-effort)
    try:
        import time

        now = time.time()
        with _updates_status_cache_lock():
            cached = _UPDATES_STATUS_CACHE.get("data")
            checked_at = float(_UPDATES_STATUS_CACHE.get("checked_at") or 0.0)
        if not force and cached and (now - checked_at) < _UPDATES_STATUS_CACHE_TTL_SEC:
            return UpdateStatusResponse(**cached)
    except Exception:
        logger.debug("Failed to read updates status cache", exc_info=True)

    host_root = _project_host_root_from_admin_ui_container()

    # Local info (via updater container; admin_ui may not include git)
    try:
        code, out = _run_updater_ephemeral(
            host_root,
            env={"PROJECT_ROOT": host_root},
            command=(
                "set -euo pipefail; "
                "cd \"$PROJECT_ROOT\"; "
                "git -c safe.directory=\"$PROJECT_ROOT\" rev-parse --abbrev-ref HEAD; "
                "git -c safe.directory=\"$PROJECT_ROOT\" rev-parse HEAD; "
                "git -c safe.directory=\"$PROJECT_ROOT\" describe --tags --always --dirty; "
                "git -c safe.directory=\"$PROJECT_ROOT\" describe --tags --abbrev=0 --match 'v*' 2>/dev/null || true"
            ),
            timeout_sec=30,
            prefer_pull_ref="latest",
            allow_build=bool(build_updater),
        )
    except HTTPException as e:
        return UpdateStatusResponse(
            local={"branch": "unknown", "head_sha": "unknown", "describe": "unknown"},
            remote=None,
            update_available=None,
            changelog_latest=None,
            error=(str(getattr(e, "detail", None) or "Local status unavailable"))[:400],
        )
    except Exception:
        return UpdateStatusResponse(
            local={"branch": "unknown", "head_sha": "unknown", "describe": "unknown"},
            remote=None,
            update_available=None,
            changelog_latest=None,
            error="Local status unavailable",
        )

    if code != 0:
        return UpdateStatusResponse(
            local={"branch": "unknown", "head_sha": "unknown", "describe": "unknown"},
            remote=None,
            update_available=None,
            changelog_latest=None,
            error="Local status unavailable (updater image not ready)",
        )

    lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
    if len(lines) < 3:
        return UpdateStatusResponse(
            local={"branch": "unknown", "head_sha": "unknown", "describe": "unknown"},
            remote=None,
            update_available=None,
            changelog_latest=None,
            error="Local status unavailable (unexpected git output)",
        )

    branch = lines[0]
    head_sha = lines[1]
    describe = lines[2]
    deployed_tag = lines[3] if len(lines) >= 4 and lines[3] else None
    if branch == "HEAD":
        branch = "detached"

    if not check_remote:
        payload = {
            "local": {"branch": branch, "head_sha": head_sha, "describe": describe, "deployed_tag": deployed_tag},
            "remote": None,
            "update_available": None,
            "changelog_latest": None,
            "error": None,
        }
        try:
            import time

            with _updates_status_cache_lock():
                _UPDATES_STATUS_CACHE["checked_at"] = time.time()
                _UPDATES_STATUS_CACHE["data"] = payload
        except Exception:
            logger.debug("Failed to write updates status cache", exc_info=True)
        return UpdateStatusResponse(**payload)

    # Remote v* tags
    try:
        code2, out2 = _run_updater_ephemeral(
            host_root,
            env={"PROJECT_ROOT": host_root},
            command=(
                "set -euo pipefail; "
                "cd \"$PROJECT_ROOT\"; "
                "git -c safe.directory=\"$PROJECT_ROOT\" ls-remote --tags origin 'refs/tags/v*'"
            ),
            timeout_sec=15,
            prefer_pull_ref="latest",
            allow_build=bool(build_updater),
        )
    except HTTPException:
        code2, out2 = 1, ""
    if code2 != 0:
        payload = {
            "local": {"branch": branch, "head_sha": head_sha, "describe": describe, "deployed_tag": deployed_tag},
            "remote": None,
            "update_available": None,
            "changelog_latest": None,
            "error": "Remote unavailable (offline or blocked)",
        }
        try:
            import time

            with _updates_status_cache_lock():
                _UPDATES_STATUS_CACHE["checked_at"] = time.time()
                _UPDATES_STATUS_CACHE["data"] = payload
        except Exception:
            logger.debug("Failed to write updates status cache", exc_info=True)
        return UpdateStatusResponse(**payload)

    latest = _select_latest_v_tag(out2 or "")
    if not latest:
        payload = {
            "local": {"branch": branch, "head_sha": head_sha, "describe": describe, "deployed_tag": deployed_tag},
            "remote": None,
            "update_available": None,
            "changelog_latest": None,
            "error": "No v* tags found on remote",
        }
        try:
            import time

            with _updates_status_cache_lock():
                _UPDATES_STATUS_CACHE["checked_at"] = time.time()
                _UPDATES_STATUS_CACHE["data"] = payload
        except Exception:
            logger.debug("Failed to write updates status cache", exc_info=True)
        return UpdateStatusResponse(**payload)

    tag = latest["tag"]
    tag_sha = latest["sha"]

    rel_cmd = (
        "cd \"$PROJECT_ROOT\"; "
        f"git -c safe.directory=\"$PROJECT_ROOT\" fetch -q origin refs/tags/{tag}:refs/tags/{tag} >/dev/null 2>&1 || true; "
        f"head='{head_sha.strip()}'; target='{tag_sha.strip()}'; "
        "git -c safe.directory=\"$PROJECT_ROOT\" cat-file -e \"$target^{commit}\" >/dev/null 2>&1 || { echo unknown; exit 0; }; "
        "if [ \"$head\" = \"$target\" ]; then echo equal; exit 0; fi; "
        "git -c safe.directory=\"$PROJECT_ROOT\" merge-base --is-ancestor \"$head\" \"$target\" >/dev/null 2>&1 && { echo behind; exit 0; }; "
        "git -c safe.directory=\"$PROJECT_ROOT\" merge-base --is-ancestor \"$target\" \"$head\" >/dev/null 2>&1 && { echo ahead; exit 0; }; "
        "echo diverged; exit 0"
    )

    try:
        code3, out3 = _run_updater_ephemeral(
            host_root,
            env={"PROJECT_ROOT": host_root},
            command=rel_cmd,
            timeout_sec=20,
            prefer_pull_ref="latest",
            allow_build=bool(build_updater),
        )
    except HTTPException:
        code3, out3 = 1, ""
    relation = (out3 or "").strip().splitlines()[-1].strip() if code3 == 0 and (out3 or "").strip() else "unknown"

    if relation == "equal":
        update_available = False
        error = None
    elif relation == "behind":
        update_available = True
        error = None
    elif relation == "ahead":
        update_available = False
        error = None
    elif relation == "diverged":
        update_available = None
        error = "Local branch diverged from latest release (run plan for details)"
    else:
        update_available = None
        error = "Unable to compare local version to remote (offline or missing objects)"

    changelog_latest = None
    try:
        code4, out4 = _run_updater_ephemeral(
            host_root,
            env={"PROJECT_ROOT": host_root},
            command=(
                "set -euo pipefail; "
                "cd \"$PROJECT_ROOT\"; "
                f"git -c safe.directory=\"$PROJECT_ROOT\" fetch -q origin refs/tags/{tag}:refs/tags/{tag} >/dev/null 2>&1 || true; "
                f"git -c safe.directory=\"$PROJECT_ROOT\" show {tag}:CHANGELOG.md 2>/dev/null || true"
            ),
            timeout_sec=20,
            prefer_pull_ref="latest",
            allow_build=bool(build_updater),
        )
        if code4 == 0 and (out4 or "").strip():
            changelog_latest = _extract_changelog_section(out4, tag)
    except Exception:
        changelog_latest = None

    payload = {
        "local": {"branch": branch, "head_sha": head_sha, "describe": describe, "deployed_tag": deployed_tag},
        "remote": {"latest_tag": tag, "latest_tag_sha": tag_sha},
        "update_available": update_available,
        "changelog_latest": changelog_latest,
        "error": error,
    }
    try:
        import time

        with _updates_status_cache_lock():
            _UPDATES_STATUS_CACHE["checked_at"] = time.time()
            _UPDATES_STATUS_CACHE["data"] = payload
    except Exception:
        logger.debug("Failed to write updates status cache", exc_info=True)
    return UpdateStatusResponse(**payload)


class UpdatePlanResponse(BaseModel):
    plan: dict


class UpdateBranchesResponse(BaseModel):
    branches: list[str]
    error: Optional[str] = None


@router.get("/updates/branches", response_model=UpdateBranchesResponse)
async def updates_branches(build_updater: bool = False):
    """
    Return the list of remote branches on origin for the Updates UI dropdown.
    """
    host_root = _project_host_root_from_admin_ui_container()

    try:
        code, out = _run_updater_ephemeral(
            host_root,
            env={"PROJECT_ROOT": host_root},
            command=(
                "set -euo pipefail; "
                "cd \"$PROJECT_ROOT\"; "
                "git -c safe.directory=\"$PROJECT_ROOT\" ls-remote --heads origin"
            ),
            timeout_sec=20,
            prefer_pull_ref="latest",
            allow_build=bool(build_updater),
        )
    except HTTPException:
        code, out = 1, ""
    if code != 0:
        return UpdateBranchesResponse(branches=[], error="Remote branches unavailable (offline or blocked)")

    branches: list[str] = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            _sha, ref = line.split("\t", 1)
        except Exception:
            continue
        if not ref.startswith("refs/heads/"):
            continue
        name = ref[len("refs/heads/") :].strip()
        if not name:
            continue
        branches.append(name)

    branches = sorted(set(branches))
    return UpdateBranchesResponse(branches=branches, error=None)


@router.get("/updates/plan", response_model=UpdatePlanResponse)
async def updates_plan(ref: str = "main", include_ui: bool = False, checkout: bool = True):
    """
    Return a pre-update plan from `agent update --plan --plan-json`.
    """
    host_root = _project_host_root_from_admin_ui_container()

    ref = _validate_git_ref(ref)
    env = {
        "PROJECT_ROOT": host_root,
        "AAVA_UPDATE_MODE": "plan",
        "AAVA_UPDATE_INCLUDE_UI": "true" if include_ui else "false",
        "AAVA_UPDATE_REMOTE": "origin",
        "AAVA_UPDATE_REF": ref,
        "AAVA_UPDATE_CHECKOUT": "true" if checkout else "false",
    }
    # Capture stdout only so JSON output isn't polluted by installer/self-update hints on stderr.
    code, out = _run_updater_ephemeral(
        host_root,
        env=env,
        timeout_sec=120,
        capture_stderr=False,
        prefer_pull_ref="latest",
        allow_build=True,
    )
    if code != 0:
        raise HTTPException(status_code=500, detail=f"Failed to compute update plan: {out.strip()[:400]}")

    import json
    try:
        plan = json.loads(out)
    except Exception:
        raise HTTPException(status_code=500, detail="Updater returned invalid JSON")
    return UpdatePlanResponse(plan=plan)


class UpdateRunRequest(BaseModel):
    include_ui: bool = False
    ref: str = "main"
    checkout: bool = True
    update_cli_host: bool = True
    cli_install_path: Optional[str] = None


class UpdateRunResponse(BaseModel):
    job_id: str


@router.post("/updates/run", response_model=UpdateRunResponse)
async def updates_run(body: UpdateRunRequest):
    host_root = _project_host_root_from_admin_ui_container()
    host_docker_sock = _docker_sock_host_path_from_admin_ui_container()
    sha = _current_project_head_sha()
    tag = _updater_image_tag_for_sha(sha)
    ref = _validate_git_ref(body.ref or "main")
    # Prefer pulling a published updater image. For stable version updates (vX.Y.Z),
    # try to pull the matching updater tag; otherwise fall back to pulling :latest.
    prefer_pull = ref if _is_semver_tag(ref) else "latest"
    _ensure_updater_image_for_ref(host_root, tag, prefer_pull_ref=prefer_pull, allow_build=True)

    job_id = uuid.uuid4().hex

    # Create an initial job marker immediately so the UI doesn't hit a race where the
    # updater container hasn't created its state/log files yet.
    try:
        project_root = os.getenv("PROJECT_ROOT", "/app/project")
        jobs_dir = os.path.join(project_root, ".agent", "updates", "jobs")
        os.makedirs(jobs_dir, exist_ok=True)
        state_path = os.path.join(jobs_dir, f"{job_id}.json")
        log_path = os.path.join(jobs_dir, f"{job_id}.log")
        import json
        from datetime import datetime, timezone

        payload = {
            "job_id": job_id,
            "status": "starting",
            "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "finished_at": None,
            "include_ui": bool(body.include_ui),
            "exit_code": None,
            "log_path": log_path,
            "ref": (body.ref or "main").strip(),
            "checkout": bool(body.checkout),
            "update_cli_host": bool(body.update_cli_host),
            "cli_install_path": (body.cli_install_path or "").strip() or None,
        }
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        # Best-effort only; the updater container will still manage state/logs.
        pass

    client = docker.from_env()
    name = f"aava-update-{job_id[:12]}"

    volumes = {
        host_root: {"bind": host_root, "mode": "rw"},
        host_docker_sock: {"bind": "/var/run/docker.sock", "mode": "rw"},
    }
    # Use the already validated ref.
    env = {
        "PROJECT_ROOT": host_root,
        "AAVA_UPDATE_MODE": "run",
        "AAVA_UPDATE_JOB_ID": job_id,
        "AAVA_UPDATE_INCLUDE_UI": "true" if body.include_ui else "false",
        "AAVA_UPDATE_REMOTE": "origin",
        "AAVA_UPDATE_REF": ref,
        "AAVA_UPDATE_CHECKOUT": "true" if body.checkout else "false",
        "AAVA_UPDATE_UPDATE_CLI_HOST": "true" if body.update_cli_host else "false",
    }
    cli_path = (body.cli_install_path or "").strip()
    if cli_path:
        env["AAVA_UPDATE_CLI_INSTALL_PATH"] = cli_path

    try:
        client.containers.run(
            tag,
            environment=env,
            volumes=volumes,
            name=name,
            detach=True,
            auto_remove=True,
        )
    except Exception as e:
        logger.exception("Failed to start update runner: %s", e)
        raise HTTPException(status_code=500, detail="Failed to start update runner")

    return UpdateRunResponse(job_id=job_id)


class UpdateRollbackRequest(BaseModel):
    from_job_id: str


class UpdateRollbackResponse(BaseModel):
    job_id: str


@router.post("/updates/rollback", response_model=UpdateRollbackResponse)
async def updates_rollback(body: UpdateRollbackRequest):
    """
    Roll back to the pre-update branch + restore config from the backup captured by a prior update job.

    This starts a detached updater container with `AAVA_UPDATE_MODE=rollback`.
    """
    host_root = _project_host_root_from_admin_ui_container()
    host_docker_sock = _docker_sock_host_path_from_admin_ui_container()
    sha = _current_project_head_sha()
    tag = _updater_image_tag_for_sha(sha)
    _ensure_updater_image_for_ref(host_root, tag, prefer_pull_ref="latest", allow_build=True)

    from_job_id_raw = (body.from_job_id or "").strip()
    if not from_job_id_raw:
        raise HTTPException(status_code=400, detail="from_job_id is required")
    try:
        from_job_id = uuid.UUID(from_job_id_raw).hex
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid from_job_id format")

    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    jobs_dir = os.path.join(project_root, ".agent", "updates", "jobs")
    src_state_path = os.path.join(jobs_dir, f"{from_job_id}.json")
    if not os.path.exists(src_state_path):
        raise HTTPException(status_code=404, detail="Source update job not found")

    import json
    from datetime import datetime, timezone

    src_job = {}
    try:
        with open(src_state_path, "r", encoding="utf-8") as f:
            src_job = json.load(f) or {}
    except Exception:
        src_job = {}

    include_ui = bool(src_job.get("include_ui"))
    pre_update_branch = (src_job.get("pre_update_branch") or "").strip() or None
    backup_dir_rel = (src_job.get("backup_dir_rel") or "").strip() or None
    update_cli_host = bool(src_job.get("update_cli_host", True))
    cli_install_path = (src_job.get("cli_install_path") or "").strip() or None

    import uuid
    job_id = uuid.uuid4().hex

    # Create an initial job marker immediately so the UI can start polling right away.
    try:
        os.makedirs(jobs_dir, exist_ok=True)
        state_path = os.path.join(jobs_dir, f"{job_id}.json")
        log_path = os.path.join(jobs_dir, f"{job_id}.log")
        payload = {
            "job_id": job_id,
            "type": "rollback",
            "rollback_from_job_id": from_job_id,
            "status": "starting",
            "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "finished_at": None,
            "include_ui": include_ui,
            "update_cli_host": update_cli_host,
            "cli_install_path": cli_install_path,
            "exit_code": None,
            "log_path": log_path,
        }
        if pre_update_branch:
            payload["ref"] = pre_update_branch
            payload["pre_update_branch"] = pre_update_branch
        if backup_dir_rel:
            payload["backup_dir_rel"] = backup_dir_rel

        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass

    client = docker.from_env()
    name = f"aava-rollback-{job_id[:12]}"

    volumes = {
        host_root: {"bind": host_root, "mode": "rw"},
        host_docker_sock: {"bind": "/var/run/docker.sock", "mode": "rw"},
    }
    env = {
        "PROJECT_ROOT": host_root,
        "AAVA_UPDATE_MODE": "rollback",
        "AAVA_UPDATE_JOB_ID": job_id,
        "AAVA_UPDATE_ROLLBACK_FROM_JOB": from_job_id,
        # Prefer the include_ui setting from the source job as a fallback for older jobs.
        "AAVA_UPDATE_INCLUDE_UI": "true" if include_ui else "false",
        "AAVA_UPDATE_UPDATE_CLI_HOST": "true" if update_cli_host else "false",
    }
    if cli_install_path:
        env["AAVA_UPDATE_CLI_INSTALL_PATH"] = cli_install_path

    try:
        client.containers.run(
            tag,
            environment=env,
            volumes=volumes,
            name=name,
            detach=True,
            auto_remove=True,
        )
    except Exception as e:
        logger.exception("Failed to start rollback runner: %s", e)
        raise HTTPException(status_code=500, detail="Failed to start rollback runner")

    return UpdateRollbackResponse(job_id=job_id)


class UpdateJobResponse(BaseModel):
    job: dict
    log_tail: Optional[str] = None


def _tail_text_file(path: str, max_lines: int = 250, max_bytes: int = 512 * 1024) -> str:
    """
    Return the last `max_lines` lines of a text file without reading the entire file into memory.
    """
    if max_lines < 1:
        max_lines = 1
    if max_bytes < 4 * 1024:
        max_bytes = 4 * 1024

    try:
        file_size = os.path.getsize(path)
        if file_size <= 0:
            return ""

        with open(path, "rb") as f:
            chunk_size = 32 * 1024
            pos = file_size
            remaining = min(file_size, max_bytes)
            data = b""

            while remaining > 0:
                read_size = min(chunk_size, remaining)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                data = chunk + data
                if data.count(b"\n") >= max_lines + 1:
                    break
                remaining -= read_size

        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""


class UpdateHistoryItem(BaseModel):
    job: dict


class UpdateHistoryResponse(BaseModel):
    jobs: list[dict]


@router.get("/updates/history", response_model=UpdateHistoryResponse)
async def updates_history(limit: int = 10):
    """
    Return the most recent update jobs (summary).

    Data source: `.agent/updates/jobs/*.json` persisted on the project volume.
    """
    if limit < 1:
        limit = 1
    if limit > 25:
        limit = 25

    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    jobs_dir = os.path.join(project_root, ".agent", "updates", "jobs")
    if not os.path.isdir(jobs_dir):
        return UpdateHistoryResponse(jobs=[])

    import glob
    import json
    from datetime import datetime

    def _parse_dt(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            # Most of our timestamps are Zulu ISO.
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    items: list[tuple[float, dict]] = []
    for path in glob.glob(os.path.join(jobs_dir, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                job = json.load(f) or {}
        except Exception:
            continue

        # Sort key: finished_at > started_at > mtime.
        st = _parse_dt(job.get("finished_at")) or _parse_dt(job.get("started_at"))
        ts = st.timestamp() if st else os.path.getmtime(path)
        items.append((ts, job))

    items.sort(key=lambda x: x[0], reverse=True)
    return UpdateHistoryResponse(jobs=[j for _, j in items[:limit]])


@router.get("/updates/jobs/{job_id}", response_model=UpdateJobResponse)
async def updates_job(job_id: str):
    job_id_raw = (job_id or "").strip()
    if not job_id_raw:
        raise HTTPException(status_code=400, detail="Invalid job_id format")
    try:
        job_id = uuid.UUID(job_id_raw).hex
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid job_id format")
    project_root = os.getenv("PROJECT_ROOT", "/app/project")
    jobs_dir = os.path.join(project_root, ".agent", "updates", "jobs")
    state_path = os.path.join(jobs_dir, f"{job_id}.json")
    log_path = os.path.join(jobs_dir, f"{job_id}.log")

    import json

    if not os.path.exists(state_path) and not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail="Update job not found")

    job = {}
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                job = json.load(f) or {}
        except Exception:
            job = {"job_id": job_id, "status": "unknown"}
    else:
        job = {"job_id": job_id, "status": "running"}

    tail = None
    if os.path.exists(log_path):
        tail = _tail_text_file(log_path, max_lines=250)

    return UpdateJobResponse(job=job, log_tail=tail)
