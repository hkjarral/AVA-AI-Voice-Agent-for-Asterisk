"""Run a synthetic HTTP scope through the same Uvicorn protocol as both images."""

import sys
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from uvicorn.server import ServerState

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auth
from services.login_throttle import client_address


@pytest.mark.asyncio
@pytest.mark.parametrize(("trusted", "peer", "scheme", "effective", "login_origin"), [
    ("", "127.0.0.1", "https", "192.0.2.20", "127.0.0.1"),
    ("127.0.0.1,192.0.2.20", "127.0.0.1", "https", "192.0.2.10", "192.0.2.10"),
    ("127.0.0.1,192.0.2.20", "192.0.2.30", "http", "192.0.2.30", "192.0.2.30"),
])
async def test_redirect_and_login_peer(monkeypatch, tmp_path, trusted, peer, scheme, effective, login_origin):
    from services.proxy_peer import PeerAwareHTTPProtocol
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    monkeypatch.setenv("AAVA_TRUSTED_PROXIES", trusted)
    monkeypatch.setattr(auth, "USERS_PATH", str(tmp_path / "users.json"))
    monkeypatch.setattr(auth, "_verify_login", lambda *_: (None, False))
    inspect_address = Mock(wraps=client_address)
    monkeypatch.setattr(auth, "client_address", inspect_address)

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")

    @app.get("/location/")
    async def location(request: Request):
        return {"client": request.client.host}

    config = uvicorn.Config(app, http=PeerAwareHTTPProtocol, forwarded_allow_ips=trusted or None, log_config=None)
    config.load()
    protocol = config.http_protocol_class(config, ServerState(), {})
    headers = [("X-Forwarded-For", "192.0.2.10"), ("X-Forwarded-For", "192.0.2.20"), ("X-Forwarded-Proto", "https")]
    transport = httpx.ASGITransport(app=protocol.app, client=(peer, 5000))
    async with httpx.AsyncClient(transport=transport, base_url="http://example.test", headers=headers) as client:
        redirect = await client.get("/location")
        assert redirect.status_code == 307
        assert redirect.headers["location"] == f"{scheme}://example.test/location/"
        assert (await client.get("/location/")).json() == {"client": effective}
        response = await client.post("/api/auth/login", data={"username": uuid4().hex, "password": "synthetic"})
    assert response.status_code == 401
    inspect_address.assert_called_once_with(peer, "192.0.2.10, 192.0.2.20", trusted)
    assert client_address(*inspect_address.call_args.args) == login_origin


def test_shipped_launchers_enable_trusted_forwarding():
    admin = Path(__file__).resolve().parents[2]
    for dockerfile in (admin / "Dockerfile", admin / "backend" / "Dockerfile"):
        cmd = next(line for line in dockerfile.read_text().splitlines() if line.startswith("CMD"))
        assert "services.proxy_peer:PeerAwareHTTPProtocol" in cmd
        assert "--forwarded-allow-ips" in cmd
        assert "AAVA_TRUSTED_PROXIES" in cmd
