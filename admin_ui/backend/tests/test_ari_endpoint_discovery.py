import asyncio
import copy
import gzip
import json
import ssl
import sys
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auth
from api import system, tools
from services import ari_endpoint_discovery as discovery


CONNECTION = {
    "host": "pbx.example.test", "scheme": "https", "port": 8089,
    "username": "discovery-user", "password": "secret-never-return", "ssl_verify": True,
}
INVENTORY = [
    {"technology": "PJSIP", "resource": "0012", "state": "online", "channel_ids": ["private-call-id"], "password": "upstream-secret"},
    {"technology": "PJSIP", "resource": "carrier-trunk", "state": "offline"},
    {"technology": "SIP", "resource": "102"},
]


@pytest.fixture
def ari(monkeypatch):
    calls = []
    options = {}
    response = {"status": 200, "json": INVENTORY}
    real_client = httpx.AsyncClient

    async def handler(request):
        calls.append(request)
        if "error" in response:
            raise response["error"]
        if "delay" in response:
            await asyncio.sleep(response["delay"])
        return httpx.Response(
            response["status"], headers=response.get("headers", {}),
            stream=httpx.ByteStream(response.get("raw", json.dumps(response.get("json")).encode())),
        )

    def client(**kwargs):
        options.update(kwargs)
        return real_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(discovery.httpx, "AsyncClient", client)
    return calls, options, response


@pytest.mark.asyncio
async def test_discovery_only_lists_endpoints_and_projects_safe_fields(ari):
    calls, options, _ = ari
    result = await discovery.discover_endpoints(CONNECTION)
    assert [(request.method, str(request.url)) for request in calls] == [
        ("GET", "https://pbx.example.test:8089/ari/endpoints"),
    ]
    assert options["follow_redirects"] is False
    assert options["verify"] is True
    assert calls[0].headers["authorization"].startswith("Basic ")
    assert calls[0].headers["accept-encoding"] == "identity"
    assert result.model_dump()["endpoints"] == [
        {"technology": "PJSIP", "resource": "0012", "state": "online"},
        {"technology": "PJSIP", "resource": "carrier-trunk", "state": "offline"},
        {"technology": "SIP", "resource": "102", "state": "unknown"},
    ]
    serialized = result.model_dump_json()
    for sensitive in ["secret-never-return", "upstream-secret", "private-call-id", "password", "channel_ids"]:
        assert sensitive not in serialized
    assert result.discovered_at.endswith("+00:00")


@pytest.mark.asyncio
@pytest.mark.parametrize("status,category", [(401, "authentication"), (403, "forbidden"), (404, "unsupported"), (302, "upstream"), (500, "upstream")])
async def test_http_failures_are_sanitized_and_never_retried(ari, status, category):
    calls, _, response = ari
    response.update(status=status, json={"access_token": "private-response-token"})
    with pytest.raises(discovery.HTTPException) as error:
        await discovery.discover_endpoints(CONNECTION)
    assert error.value.detail == {"category": category, "message": discovery.ERRORS[category]}
    assert len(calls) == 1
    assert "private-response-token" not in str(error.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {}, [None], [{"resource": "102"}],
    [{"technology": "PJSIP", "resource": ""}],
    [{"technology": "PJSIP", "resource": 102}],
    [{"technology": "PJSIP", "resource": "102", "state": "available"}],
    [{"technology": "PJSIP", "resource": "102\n"}],
    INVENTORY + [INVENTORY[0]],
    INVENTORY + [{"technology": "PJSIP", "resource": "bad", "state": False}],
])
async def test_partial_malformed_or_duplicate_inventory_rejected_atomically(ari, payload):
    ari[2]["json"] = payload
    with pytest.raises(discovery.HTTPException) as error:
        await discovery.discover_endpoints(CONNECTION)
    assert error.value.detail["category"] == "invalid_response"


@pytest.mark.asyncio
async def test_timeout_bounds_whole_request_and_cancels_it(ari, monkeypatch):
    monkeypatch.setattr(discovery, "DISCOVERY_TIMEOUT_SECONDS", 0.01)
    ari[2]["delay"] = 1
    with pytest.raises(discovery.HTTPException) as error:
        await discovery.discover_endpoints(CONNECTION)
    assert error.value.status_code == 504
    assert error.value.detail["category"] == "timeout"
    assert len(ari[0]) == 1


@pytest.mark.asyncio
async def test_payload_and_count_limits(ari, monkeypatch):
    monkeypatch.setattr(discovery, "MAX_RESPONSE_BYTES", 10)
    with pytest.raises(discovery.HTTPException) as error:
        await discovery.discover_endpoints(CONNECTION)
    assert error.value.detail["category"] == "too_large"
    monkeypatch.setattr(discovery, "MAX_RESPONSE_BYTES", 1024 * 1024)
    monkeypatch.setattr(discovery, "MAX_ENDPOINTS", 1)
    with pytest.raises(discovery.HTTPException) as error:
        await discovery.discover_endpoints(CONNECTION)
    assert error.value.detail["category"] == "too_large"


@pytest.mark.asyncio
async def test_compressed_inventory_is_rejected_without_decompression(ari, monkeypatch):
    calls, _, response = ari
    # A small gzip payload expands well beyond the configured inventory budget.
    response.update(headers={"Content-Encoding": "gzip"}, raw=gzip.compress(b" " * (2 * discovery.MAX_RESPONSE_BYTES)))
    assert len(response["raw"]) < discovery.MAX_RESPONSE_BYTES

    async def forbidden_decode(*args, **kwargs):
        raise AssertionError("Discovery must never decode compressed content")
        yield  # Async iterator contract.

    monkeypatch.setattr(httpx.Response, "aiter_bytes", forbidden_decode)
    with pytest.raises(discovery.HTTPException) as error:
        await discovery.discover_endpoints(CONNECTION)
    assert error.value.detail["category"] == "invalid_response"
    assert calls[0].headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
async def test_raw_inventory_limit_handles_exact_boundary_and_multiple_chunks(ari, monkeypatch):
    response = ari[2]
    # Cross the raw iterator's 64 KiB chunk boundary with valid JSON whitespace.
    raw = b" " * 65536 + b"[]"
    response.update(headers={"Content-Encoding": "identity"}, raw=raw)
    monkeypatch.setattr(discovery, "MAX_RESPONSE_BYTES", len(raw))
    assert (await discovery.discover_endpoints(CONNECTION)).endpoints == []
    monkeypatch.setattr(discovery, "MAX_RESPONSE_BYTES", len(raw) - 1)
    with pytest.raises(discovery.HTTPException) as error:
        await discovery.discover_endpoints(CONNECTION)
    assert error.value.detail["category"] == "too_large"


@pytest.mark.asyncio
async def test_missing_credentials_never_contacts_ari(ari):
    with pytest.raises(discovery.HTTPException) as error:
        await discovery.discover_endpoints({**CONNECTION, "password": ""})
    assert error.value.detail["category"] == "not_configured"
    assert not ari[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["tls", "connection", "timeout"])
async def test_transport_errors_have_no_exception_details(ari, category):
    error = httpx.ConnectError("private host and credentials")
    if category == "tls":
        error.__cause__ = ssl.SSLCertVerificationError("private certificate")
    elif category == "timeout":
        error = httpx.ReadTimeout("private URL")
    ari[2]["error"] = error
    with pytest.raises(discovery.HTTPException) as caught:
        await discovery.discover_endpoints(CONNECTION)
    assert caught.value.detail == {"category": category, "message": discovery.ERRORS[category]}


def review_body():
    return {
        "additions": [{"key": "sales", "target": "0012", "description": "Sales", "dialplan_context": "from-internal"}],
        "existing_destinations": {"support": {"type": "queue", "target": "300", "custom": "preserve"}, "placeholder": None},
        "extension_context": "from-internal",
    }


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    dependencies = [Depends(auth.get_current_user)]
    app.include_router(system.router, prefix="/api/system", dependencies=dependencies)
    app.include_router(tools.router, prefix="/api/tools", dependencies=dependencies)
    app.dependency_overrides[auth.get_current_user] = lambda: "operator"
    monkeypatch.setattr(system, "_ari_env_settings", lambda: CONNECTION)
    return TestClient(app)


def test_review_is_atomic_nonpersisting_and_keeps_legacy_manual_entries(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Discovery and review must never persist configuration")
    monkeypatch.setattr(tools, "_persist_cfg", forbidden)
    original = review_body()
    body = copy.deepcopy(original)
    response = client.post("/api/tools/transfer-destinations/validate", json=body)
    assert response.status_code == 200, response.text
    assert response.json() == {"destinations": {"sales": {
        "type": "extension", "target": "0012", "description": "Sales",
        "dialplan_context": "from-internal", "attended_allowed": False,
    }}}
    assert body == original
    body["additions"].append({**body["additions"][0], "key": "other"})
    assert client.post("/api/tools/transfer-destinations/validate", json=body).status_code == 409


@pytest.mark.parametrize("existing", [
    {"sales": None}, {"sales": {"type": "queue", "target": "300"}},
    {"legacy": {"type": "extension", "target": "0012", "context": "from-internal"}},
    {"legacy": {"type": "extension", "target": "0012"}},
])
def test_conflicts_include_keys_placeholders_legacy_context_and_inherited_context(client, existing):
    body = {**review_body(), "existing_destinations": existing}
    response = client.post("/api/tools/transfer-destinations/validate", json=body)
    assert response.status_code == 409
    assert "destinations" not in response.json()


@pytest.mark.parametrize("field,value", [
    ("key", "__proto__"), ("key", "a/b"), ("key", ""),
    ("target", "102@outside"), ("target", 102), ("target", "102\n"),
    ("dialplan_context", ""), ("dialplan_context", "from-internal/../../"),
    ("description", " "), ("description", "Hello\nworld"), ("description", "x" * 201),
])
def test_review_field_validation(client, field, value):
    body = review_body()
    body["additions"][0][field] = value
    assert client.post("/api/tools/transfer-destinations/validate", json=body).status_code == 422


def test_discovery_and_review_require_admin_authentication(client):
    client.app.dependency_overrides.clear()
    assert client.get("/api/system/ari/endpoints").status_code == 401
    assert client.post("/api/tools/transfer-destinations/validate", json=review_body()).status_code == 401


def test_discovery_api_uses_existing_settings_and_does_not_write_config(client, ari):
    response = client.get("/api/system/ari/endpoints")
    assert response.status_code == 200
    assert len(response.json()["endpoints"]) == 3
    assert [(request.method, request.url.path) for request in ari[0]] == [("GET", "/ari/endpoints")]


def test_invalid_json_and_empty_inventory():
    with pytest.raises(discovery.HTTPException):
        discovery._parse_inventory(b"not-json secret")
    assert discovery._parse_inventory(b"[]") == []


def test_reviewed_additions_round_trip_through_real_persistence_and_unrelated_edit(tmp_path, monkeypatch):
    from api import config

    source = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    source["tools"]["transfer"]["destinations"] = review_body()["existing_destinations"]
    source["tools"]["extensions"] = {"internal": {"102": {"name": "Existing", "dial_string": "PJSIP/102"}}}
    source["tools"]["custom_settings"] = {"reference": "${TRANSFER_SECRET}", "disabled": False}
    base_path, local_path, env_path = tmp_path / "ai-agent.yaml", tmp_path / "ai-agent.local.yaml", tmp_path / ".env"
    base_path.write_text(yaml.safe_dump(source))
    env_path.write_text("ASTERISK_ARI_USERNAME=test\nASTERISK_ARI_PASSWORD=test\n")
    monkeypatch.setattr(config.settings, "CONFIG_PATH", str(base_path))
    monkeypatch.setattr(config.settings, "LOCAL_CONFIG_PATH", str(local_path))
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env_path))
    monkeypatch.setattr(config, "_migrate_inline_provider_secrets", lambda _parsed: False)
    desired = copy.deepcopy(source)
    additions = discovery.validate_additions(discovery.DestinationReview(**review_body()))["destinations"]
    desired["tools"]["transfer"]["destinations"].update(additions)
    result = config.persist_config_content(yaml.safe_dump(desired))
    assert result["recommended_apply_method"] == "hot_reload"
    reopened = config._read_merged_config_dict()
    assert reopened["tools"] == desired["tools"]
    reopened["tools"]["hangup_call"]["farewell_message"] = "See you soon."
    config.persist_config_content(yaml.safe_dump(reopened))
    again = config._read_merged_config_dict()
    assert again["tools"]["transfer"] == desired["tools"]["transfer"]
    assert again["tools"]["extensions"] == source["tools"]["extensions"]
    assert again["tools"]["custom_settings"] == source["tools"]["custom_settings"]
    assert "${TRANSFER_SECRET}" in local_path.read_text() or "${TRANSFER_SECRET}" in base_path.read_text()


@pytest.mark.parametrize("policy,keys,expected", [
    ("inherit", [], {"support", "sales"}),
    ("selected", ["support"], {"support"}),
    ("selected", ["sales"], {"sales"}),
    ("selected", [], set()),
    ("selected", ["deleted"], set()),
    ("none", [], set()),
])
def test_discovered_destinations_obey_existing_agent_permissions(policy, keys, expected):
    from src.tools.runtime_config import resolve_agent_tool_config

    additions = discovery.validate_additions(discovery.DestinationReview(**review_body()))["destinations"]
    original = {"tools": {"transfer": {
        "enabled": False,
        "destinations": {"support": {"type": "queue", "target": "300"}, **additions},
    }}}
    before = copy.deepcopy(original)
    effective = resolve_agent_tool_config(original, {"transfer": {"destination_policy": policy, "destination_keys": keys}})
    assert set(effective.config["tools"]["transfer"]["destinations"]) == expected
    assert effective.config["tools"]["transfer"]["enabled"] is False
    assert original == before
