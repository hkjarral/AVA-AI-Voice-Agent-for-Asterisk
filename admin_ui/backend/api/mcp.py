from fastapi import APIRouter, HTTPException
import os
import httpx

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


def _ai_engine_base_urls() -> list[str]:
    """Return candidate ai-engine health base URLs (no trailing /health)."""
    candidates: list[str] = []
    env = os.getenv("HEALTH_CHECK_AI_ENGINE_URL")
    if env:
        candidates.append(env.replace("/health", ""))
    # Common defaults:
    # - host networking: 127.0.0.1
    # - bridge networking: service/container DNS names
    candidates.extend(["http://127.0.0.1:15000", "http://ai-engine:15000", "http://ai_engine:15000"])
    # Dedupe while preserving order
    out: list[str] = []
    for c in candidates:
        c = (c or "").strip().rstrip("/")
        if c and c not in out:
            out.append(c)
    return out


@router.get("/status")
async def get_mcp_status():
    """Proxy MCP status from ai-engine (runs MCP servers)."""
    try:
        for base in _ai_engine_base_urls():
            url = f"{base}/mcp/status"
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(url)
                if resp.status_code != 200:
                    raise HTTPException(status_code=resp.status_code, detail=resp.text)
                return resp.json()
            except httpx.ConnectError as e:
                continue
        raise HTTPException(status_code=503, detail="AI Engine is not reachable")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="AI Engine is not reachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/servers/{server_id}/test")
async def test_mcp_server(server_id: str):
    """Proxy a safe MCP server test to ai-engine container context."""
    try:
        for base in _ai_engine_base_urls():
            url = f"{base}/mcp/test/{server_id}"
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url)
                if resp.status_code not in (200, 500):
                    raise HTTPException(status_code=resp.status_code, detail=resp.text)
                # LOW-T5: a 500 from the engine may carry a non-JSON body; surface the
                # actual text instead of letting resp.json() raise an opaque 500.
                try:
                    return resp.json()
                except ValueError:
                    raise HTTPException(status_code=resp.status_code or 500, detail=resp.text)
            except httpx.ConnectError as e:
                continue
        raise HTTPException(status_code=503, detail="AI Engine is not reachable")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="AI Engine is not reachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
