from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import settings
from dotenv import load_dotenv
import os
import logging
import secrets
from pathlib import Path


def _ensure_shared_sqlite_perms() -> None:
    """
    Prevent cross-container permission issues on the shared SQLite DB when WAL creates
    `*.db-wal` / `*.db-shm` with a restrictive umask (causing ai-engine writes to fail).
    """
    try:
        # Prefer explicit env var; fallback matches OutboundStore default.
        db_path = (os.getenv("CALL_HISTORY_DB_PATH") or "data/call_history.db").strip() or "data/call_history.db"
        p = Path(db_path)
        parent = p.parent
        if parent:
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            try:
                os.chmod(str(parent), 0o2775)
            except Exception:
                pass

        # Ensure group-writable perms on db and WAL/SHM sidecars if present.
        for candidate in (p, Path(str(p) + "-wal"), Path(str(p) + "-shm")):
            if not candidate.exists():
                continue
            try:
                mode = candidate.stat().st_mode & 0o777
                os.chmod(str(candidate), mode | 0o020)  # add g+w
            except Exception:
                pass
    except Exception:
        # Never block Admin UI startup for this.
        pass


def _ensure_outbound_prompt_assets() -> None:
    """
    Install shipped outbound prompt assets into the runtime media directory.

    This keeps "out of the box" campaigns functional without requiring the user to upload
    consent/voicemail recordings before first use.
    """
    try:
        project_root = (os.getenv("PROJECT_ROOT") or "/app/project").strip() or "/app/project"
        src_dir = Path(project_root) / "assets" / "outbound_prompts" / "en-US"
        if not src_dir.exists():
            return

        media_dir = Path(os.getenv("AAVA_MEDIA_DIR") or "/mnt/asterisk_media/ai-generated")
        try:
            media_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        mapping = {
            "aava-consent-default.ulaw": "aava-consent-default.ulaw",
            "aava-voicemail-default.ulaw": "aava-voicemail-default.ulaw",
        }
        for src_name, dst_name in mapping.items():
            src = src_dir / src_name
            dst = media_dir / dst_name
            if not src.exists():
                continue
            if dst.exists() and dst.stat().st_size == src.stat().st_size:
                continue
            try:
                data = src.read_bytes()
                dst.write_bytes(data)
                try:
                    os.chmod(str(dst), 0o664)
                except Exception:
                    pass
            except Exception:
                continue
    except Exception:
        # Never block Admin UI startup for this.
        pass

# Load environment variables (wizard will create .env from .env.example on first Next click)
load_dotenv(settings.ENV_PATH)

# Ensure files created by this process (SQLite WAL/SHM) are group-writable.
try:
    os.umask(0o002)
except Exception:
    pass

_ensure_shared_sqlite_perms()
_ensure_outbound_prompt_assets()

# SECURITY: Admin UI binds to 0.0.0.0 by default (DX-first).
# If JWT_SECRET is missing/placeholder, generate an ephemeral secret so tokens
# aren't signed with a known insecure key. Scripts (preflight/install) should
# persist a strong JWT_SECRET into .env for stable restarts.
_uvicorn_host = os.getenv("UVICORN_HOST", "0.0.0.0")
_is_remote_bind = _uvicorn_host not in ("127.0.0.1", "localhost", "::1")
_placeholder_secrets = {"", "change-me-please", "changeme"}
_raw_jwt_secret = (os.getenv("JWT_SECRET", "") or "").strip()

if _is_remote_bind and _raw_jwt_secret in _placeholder_secrets:
    os.environ["JWT_SECRET"] = secrets.token_hex(32)
    logging.getLogger(__name__).warning(
        "JWT_SECRET is missing/placeholder while Admin UI is remote-accessible on %s. "
        "Generated an ephemeral JWT_SECRET for this process. For production, set a strong "
        "JWT_SECRET in .env and restrict port 3003 (firewall/VPN/reverse proxy).",
        _uvicorn_host,
    )

from api import config, system, wizard, logs, local_ai, ollama, mcp, calls, outbound  # noqa: E402
import auth  # noqa: E402

app = FastAPI(title="Asterisk AI Voice Agent Admin API")

# Initialize users (create default admin if needed)
auth.load_users()

# Warn if JWT_SECRET isn't set (localhost-only is okay for dev)
if getattr(auth, "USING_PLACEHOLDER_SECRET", False):
    logging.getLogger(__name__).warning(
        "JWT_SECRET is missing/placeholder; Admin UI is using an insecure secret. "
        "Set JWT_SECRET in .env for production (recommended: openssl rand -hex 32)."
    )

# Configure CORS
def _parse_cors_origins() -> list[str]:
    raw = (settings.get_setting("ADMIN_UI_CORS_ORIGINS", "") or "").strip()
    if not raw:
        # Safe-ish local defaults.
        return ["http://localhost:3003", "http://127.0.0.1:3003"]
    if raw == "*":
        return ["*"]
    # Comma-separated list
    return [o.strip() for o in raw.split(",") if o.strip()]


cors_origins = _parse_cors_origins()
cors_allow_credentials = "*" not in cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
# Public routes
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])

# Protected routes
app.include_router(config.router, prefix="/api/config", tags=["config"], dependencies=[Depends(auth.get_current_user)])
app.include_router(system.router, prefix="/api/system", tags=["system"], dependencies=[Depends(auth.get_current_user)])
app.include_router(wizard.router, prefix="/api/wizard", tags=["wizard"], dependencies=[Depends(auth.get_current_user)])
app.include_router(logs.router, prefix="/api/logs", tags=["logs"], dependencies=[Depends(auth.get_current_user)])
app.include_router(local_ai.router, prefix="/api/local-ai", tags=["local-ai"], dependencies=[Depends(auth.get_current_user)])
app.include_router(mcp.router, dependencies=[Depends(auth.get_current_user)])
app.include_router(ollama.router, tags=["ollama"], dependencies=[Depends(auth.get_current_user)])
app.include_router(calls.router, prefix="/api", tags=["calls"], dependencies=[Depends(auth.get_current_user)])
app.include_router(outbound.router, prefix="/api", tags=["outbound"], dependencies=[Depends(auth.get_current_user)])

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# Serve static files (Frontend)
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

# Mount static files if directory exists (production/docker)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")
    
    @app.get("/{full_path:path}")
    async def serve_react_app(full_path: str):
        # API routes are already handled above
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
            
        # Serve index.html for all other routes (SPA)
        response = FileResponse(os.path.join(static_dir, "index.html"))
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
