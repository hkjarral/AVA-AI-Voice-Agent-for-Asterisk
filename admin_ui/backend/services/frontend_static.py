"""Serve the Admin UI bundle and its client-side routes."""

from pathlib import Path
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware


class BuildAssets(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304) and re.search(r"-[A-Za-z0-9_-]{8}(?:\.[^.]+)+$", Path(path).name):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def mount_frontend(app: FastAPI, static_dir: Path) -> None:
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
    if not static_dir.is_dir():
        return

    static_files = StaticFiles(directory=static_dir)
    app.mount("/assets", BuildAssets(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_react_app(full_path: str):
        if full_path.startswith("api/") or full_path in ("api", "docs", "redoc", "openapi.json"):
            raise HTTPException(status_code=404, detail="Not found")
        if full_path and full_path != "index.html":
            resolved_path, stat_result = static_files.lookup_path(full_path)
            if stat_result and Path(resolved_path).is_file():
                return FileResponse(resolved_path)
        return FileResponse(static_dir / "index.html", headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        })
