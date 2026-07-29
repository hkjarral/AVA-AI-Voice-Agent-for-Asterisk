import io, json, zipfile
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import support as support_api
from agents_store import AgentsStore

def _client(tmp_path, monkeypatch, seed):
    db = str(tmp_path / "agents.db")
    store = AgentsStore(db_path=db)
    seed(store)
    monkeypatch.setattr(support_api, "_store", lambda: AgentsStore(db_path=db))
    app = FastAPI()
    app.include_router(support_api.router, prefix="/api")
    return TestClient(app)

def test_bundle_redacts_sensitive_columns(tmp_path, monkeypatch):
    def seed(s):
        s.create(display_name="Maria - Vendas", provider="openai_realtime",
                 prompt="secret system prompt", greeting="hello there", notes="internal note",
                 role_label="Sales Lead", extension="801")
    client = _client(tmp_path, monkeypatch, seed)
    resp = client.get("/api/support-bundle")
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    agents = json.loads(z.read("agents_redacted.json"))
    a = agents[0]
    assert a["slug"] == "maria_vendas"               # operational fields kept
    assert a["display_name"].startswith("[name len=")
    assert a["prompt"].startswith("[prompt len=")
    assert "sha=" in a["prompt"]
    assert a["greeting"].startswith("[greeting len=")
    assert a["role_label"].startswith("[role len=")
    assert a["extension"].startswith("[ext len=")
    # raw sensitive text must NOT appear anywhere
    assert "secret system prompt" not in json.dumps(agents)
    assert "internal note" not in json.dumps(agents)
    assert "Sales Lead" not in json.dumps(agents)
    assert "801" not in json.dumps(agents)

def test_bundle_never_contains_env(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, lambda s: s.create(display_name="A", provider="x", prompt="p"))
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/support-bundle").content))
    assert ".env" not in z.namelist()

def test_bundle_handles_malformed_extra_json(tmp_path, monkeypatch):
    def seed(s):
        s.create(display_name="X", provider="x", prompt="p", extra_json="not-json")
    client = _client(tmp_path, monkeypatch, seed)
    resp = client.get("/api/support-bundle")
    assert resp.status_code == 200
    agents = json.loads(zipfile.ZipFile(io.BytesIO(resp.content)).read("agents_redacted.json"))
    assert agents[0]["extra_json"] == ["<unparseable>"]


def test_tools_json_structure_only(tmp_path, monkeypatch):
    def seed(s):
        s.create(display_name="T", provider="x", prompt="p",
                 tools_json='[{"name": "lookup", "url": "https://secret.example.com/api?key=abc"}]')
    client = _client(tmp_path, monkeypatch, seed)
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/support-bundle").content))
    agents = json.loads(z.read("agents_redacted.json"))
    assert "https://" not in json.dumps(agents)       # URLs scrubbed (structure only)


def test_tools_json_string_list_does_not_leak_urls(tmp_path, monkeypatch):
    """tools_json that is a JSON array of raw URL strings must never expose those URLs."""
    secret_url = "https://secret.example.com/x?key=abc"
    def seed(s):
        s.create(display_name="U", provider="x", prompt="p",
                 tools_json=json.dumps([secret_url]))
    client = _client(tmp_path, monkeypatch, seed)
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/support-bundle").content))
    bundle_text = z.read("agents_redacted.json").decode()
    assert "https://" not in bundle_text
    assert "secret.example.com" not in bundle_text
