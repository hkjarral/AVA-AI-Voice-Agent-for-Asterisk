import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from services.frontend_static import mount_frontend

    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-a2c4d6f8.js").write_text("/* chunk */" * 256)
    (tmp_path / "index.html").write_text("<main>AVA</main>")
    app = FastAPI()
    mount_frontend(app, tmp_path)
    return TestClient(app)


def test_versioned_asset_supports_immutable_cache_and_revalidation(client):
    url = "/assets/index-a2c4d6f8.js"
    response = client.get(url)
    assert response.status_code == 200
    assert "max-age=31536000" in response.headers["cache-control"]
    assert "immutable" in response.headers["cache-control"]
    revalidated = client.get(url, headers={"If-None-Match": response.headers["etag"]})
    assert revalidated.status_code == 304
    assert "immutable" in revalidated.headers["cache-control"]


@pytest.mark.parametrize("path", ["/", "/index.html", "/providers"])
def test_shell_is_revalidated(client, path):
    assert "no-cache" in client.get(path).headers["cache-control"]


def test_static_misses_are_not_cached_immutably(client):
    response = client.get("/assets/missing.js")
    assert response.status_code == 404
    assert "immutable" not in response.headers.get("cache-control", "")
    assert client.get("/api/unknown").status_code == 404


def test_large_asset_compression_is_negotiated(client):
    url = "/assets/index-a2c4d6f8.js"
    encoded = client.get(url, headers={"Accept-Encoding": "gzip"})
    plain = client.get(url, headers={"Accept-Encoding": "identity"})
    assert encoded.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in encoded.headers["vary"].lower()
    assert "content-encoding" not in plain.headers
    assert encoded.content == plain.content
