import copy
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import tools as tools_api
from api import config as config_api


@pytest.fixture
def client(monkeypatch):
    # In-memory config seeded with the special ai_identity entry (a builtin, not a managed tool).
    state = {
        "cfg": {
            "tools": {"ai_identity": {"name": "AI Agent", "number": "6789"}},
            "in_call_tools": {},
        }
    }
    monkeypatch.setattr(tools_api, "_load_cfg", lambda: copy.deepcopy(state["cfg"]))

    def fake_persist(cfg):
        state["cfg"] = copy.deepcopy(cfg)
        return {"status": "success", "restart_required": True}

    monkeypatch.setattr(tools_api, "_persist_cfg", fake_persist)

    app = FastAPI()
    app.include_router(tools_api.router, prefix="/api/tools")
    client = TestClient(app)
    client.cfg_state = state  # expose for assertions
    return client


def test_crud_roundtrip_pre_call(client):
    # Create
    r = client.post("/api/tools/managed", json={
        "name": "crm_lookup", "phase": "pre_call",
        "url": "https://api.example.com/lookup",
        "output_variables": {"customer_name": "contact.name"},
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "crm_lookup"
    assert body["phase"] == "pre_call"
    assert body["kind"] == "generic_http_lookup"
    assert body["block"] == "tools"
    assert body["config"]["method"] == "GET"  # pre_call default
    assert body["config"]["timeout_ms"] == 2000

    # List excludes ai_identity but includes our tool
    names = {t["name"] for t in client.get("/api/tools/managed").json()}
    assert names == {"crm_lookup"}

    # Get
    assert client.get("/api/tools/managed/crm_lookup").json()["name"] == "crm_lookup"

    # Patch
    r = client.patch("/api/tools/managed/crm_lookup", json={"enabled": False, "timeout_ms": 1234})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["config"]["timeout_ms"] == 1234
    # url preserved by partial update
    assert r.json()["config"]["url"] == "https://api.example.com/lookup"

    # Delete
    assert client.delete("/api/tools/managed/crm_lookup").status_code == 204
    assert client.get("/api/tools/managed/crm_lookup").status_code == 404


def test_in_call_tool_goes_to_in_call_block(client):
    r = client.post("/api/tools/managed", json={
        "name": "check_availability", "phase": "in_call",
        "url": "https://api.example.com/avail", "method": "POST",
        "description": "Check slot availability",
        "parameters": [{"name": "date", "type": "string", "required": True}],
    })
    assert r.status_code == 201, r.text
    assert r.json()["block"] == "in_call_tools"
    assert r.json()["kind"] == "in_call_http_lookup"
    assert client.cfg_state["cfg"]["in_call_tools"]["check_availability"]["phase"] == "in_call"
    assert "check_availability" not in client.cfg_state["cfg"]["tools"]


def test_post_call_webhook(client):
    r = client.post("/api/tools/managed", json={
        "name": "post_hook", "phase": "post_call",
        "url": "https://hooks.example.com/x", "is_global": True,
        "payload_template": '{"id": "{call_id}"}',
    })
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "generic_webhook"
    assert r.json()["is_global"] is True
    assert r.json()["config"]["method"] == "POST"


def test_create_duplicate_returns_409(client):
    payload = {"name": "dup", "phase": "pre_call", "url": "https://a.example.com"}
    assert client.post("/api/tools/managed", json=payload).status_code == 201
    assert client.post("/api/tools/managed", json=payload).status_code == 409


def test_create_reserved_name_rejected(client):
    r = client.post("/api/tools/managed", json={
        "name": "ai_identity", "phase": "pre_call", "url": "https://a.example.com"})
    assert r.status_code == 422


@pytest.mark.parametrize("name", [
    "transfer", "hangup_call", "leave_voicemail", "send_email_summary",
    "request_transcript", "google_calendar", "microsoft_calendar",
    "transfer_call", "live_agent_transfer",
])
def test_create_builtin_name_collision_rejected(client, name):
    # Managed HTTP tools must not collide with built-in telephony/tool names.
    r = client.post("/api/tools/managed", json={
        "name": name, "phase": "pre_call", "url": "https://a.example.com"})
    assert r.status_code == 422, name
    # And nothing should have been written under that name.
    assert name not in client.cfg_state["cfg"]["tools"]


def test_builtin_reserved_set_covers_static_names():
    # The reserved set used for validation must include the documented built-ins.
    reserved = tools_api._reserved_builtin_names()
    for n in ("transfer", "hangup_call"):
        assert n in reserved


def test_create_rejects_dynamically_reserved_name(client, monkeypatch):
    # Names contributed by the live registry probe (not in the static set) must
    # also be rejected. Simulate one via the reserved-names seam.
    monkeypatch.setattr(
        tools_api, "_reserved_builtin_names",
        lambda: frozenset(tools_api.BUILTIN_RESERVED_TOOL_NAMES) | {"synthetic_engine_tool"},
    )
    r = client.post("/api/tools/managed", json={
        "name": "synthetic_engine_tool", "phase": "pre_call", "url": "https://a.example.com"})
    assert r.status_code == 422
    assert "synthetic_engine_tool" not in client.cfg_state["cfg"]["tools"]


@pytest.mark.parametrize("phase,expected_kind", [
    ("pre_call", "generic_http_lookup"),
    ("in_call", "in_call_http_lookup"),
    ("post_call", "generic_webhook"),
])
def test_create_derives_kind_from_phase(client, phase, expected_kind):
    r = client.post("/api/tools/managed", json={
        "name": f"derive_{phase}", "phase": phase, "url": "https://a.example.com"})
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == expected_kind
    block = "in_call_tools" if phase == "in_call" else "tools"
    assert client.cfg_state["cfg"][block][f"derive_{phase}"]["kind"] == expected_kind


@pytest.mark.parametrize("phase,kind", [
    ("pre_call", "generic_http_lookup"),
    ("in_call", "in_call_http_lookup"),
    ("post_call", "generic_webhook"),
])
def test_create_accepts_matching_kind(client, phase, kind):
    r = client.post("/api/tools/managed", json={
        "name": f"match_{phase}", "phase": phase, "kind": kind,
        "url": "https://a.example.com"})
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == kind


@pytest.mark.parametrize("phase,bad_kind", [
    ("pre_call", "foo"),
    ("pre_call", "generic_webhook"),       # valid kind, wrong phase
    ("in_call", "generic_http_lookup"),    # valid kind, wrong phase
    ("post_call", "in_call_http_lookup"),  # valid kind, wrong phase
])
def test_create_rejects_mismatched_kind(client, phase, bad_kind):
    r = client.post("/api/tools/managed", json={
        "name": "badkind", "phase": phase, "kind": bad_kind,
        "url": "https://a.example.com"})
    assert r.status_code == 422, r.text
    assert "badkind" not in client.cfg_state["cfg"]["tools"]
    assert "badkind" not in client.cfg_state["cfg"]["in_call_tools"]


def test_patch_rejects_mismatched_kind(client):
    client.post("/api/tools/managed", json={
        "name": "pk", "phase": "pre_call", "url": "https://a.example.com"})
    r = client.patch("/api/tools/managed/pk", json={"kind": "foo"})
    assert r.status_code == 422
    # Original kind preserved (no partial write).
    assert client.cfg_state["cfg"]["tools"]["pk"]["kind"] == "generic_http_lookup"


def test_put_rejects_mismatched_kind(client):
    client.post("/api/tools/managed", json={
        "name": "rk", "phase": "pre_call", "url": "https://a.example.com"})
    r = client.put("/api/tools/managed/rk", json={
        "phase": "pre_call", "kind": "generic_webhook", "url": "https://b.example.com"})
    assert r.status_code == 422
    assert client.cfg_state["cfg"]["tools"]["rk"]["url"] == "https://a.example.com"


def test_patch_phase_change_rederives_kind(client):
    client.post("/api/tools/managed", json={
        "name": "rd", "phase": "pre_call", "url": "https://a.example.com"})
    r = client.patch("/api/tools/managed/rd", json={"phase": "post_call"})
    assert r.status_code == 200
    assert r.json()["kind"] == "generic_webhook"


def test_create_invalid_name_rejected(client):
    for bad in ("1bad", "has space", "bad-dash", "mcp_thing"):
        r = client.post("/api/tools/managed", json={
            "name": bad, "phase": "pre_call", "url": "https://a.example.com"})
        assert r.status_code == 422, bad


@pytest.mark.parametrize("payload", [
    {"name": "empty_url", "phase": "pre_call", "url": ""},
    {"name": "relative_url", "phase": "pre_call", "url": "/lookup"},
    {"name": "bad_scheme", "phase": "pre_call", "url": "ftp://example.com/x"},
    {
        "name": "bad_timeout_zero", "phase": "pre_call",
        "url": "https://example.com", "timeout_ms": 0,
    },
    {
        "name": "bad_timeout_negative", "phase": "pre_call",
        "url": "https://example.com", "timeout_ms": -1,
    },
    {
        "name": "bad_timeout_large", "phase": "pre_call",
        "url": "https://example.com", "timeout_ms": 300_001,
    },
    {
        "name": "bad_method", "phase": "pre_call",
        "url": "https://example.com", "method": "TRACE",
    },
])
def test_create_rejects_invalid_http_configuration(client, payload):
    before = copy.deepcopy(client.cfg_state["cfg"])
    r = client.post("/api/tools/managed", json=payload)
    assert r.status_code == 422, r.text
    assert client.cfg_state["cfg"] == before


def test_create_accepts_env_backed_url(client):
    r = client.post("/api/tools/managed", json={
        "name": "env_url", "phase": "pre_call",
        "url": "${CRM_BASE_URL}/contacts/{caller_number}",
    })
    assert r.status_code == 201, r.text
    assert r.json()["config"]["method"] == "GET"


def test_put_rejects_invalid_http_configuration_without_persisting(client):
    client.post("/api/tools/managed", json={
        "name": "replace_validation", "phase": "pre_call",
        "url": "https://example.com",
    })
    before = copy.deepcopy(client.cfg_state["cfg"])
    r = client.put("/api/tools/managed/replace_validation", json={
        "phase": "post_call", "url": "", "method": "post",
    })
    assert r.status_code == 422, r.text
    assert client.cfg_state["cfg"] == before


def test_create_normalizes_http_method(client):
    r = client.post("/api/tools/managed", json={
        "name": "normalized_method", "phase": "post_call",
        "url": "https://example.com", "method": "patch",
    })
    assert r.status_code == 201, r.text
    assert r.json()["config"]["method"] == "PATCH"


@pytest.mark.parametrize("patch", [
    {"phase": None},
    {"url": None},
    {"enabled": None},
    {"is_global": None},
    {"url": ""},
    {"timeout_ms": 0},
    {"timeout_ms": -1},
    {"timeout_ms": 300_001},
    {"method": "TRACE"},
])
def test_patch_rejects_invalid_required_http_configuration(client, patch):
    client.post("/api/tools/managed", json={
        "name": "valid_tool", "phase": "pre_call", "url": "https://example.com",
    })
    before = copy.deepcopy(client.cfg_state["cfg"])
    r = client.patch("/api/tools/managed/valid_tool", json=patch)
    assert r.status_code == 422, r.text
    assert client.cfg_state["cfg"] == before


def test_patch_null_clears_optional_field_without_persisting_null(client):
    client.post("/api/tools/managed", json={
        "name": "clear_optional", "phase": "pre_call",
        "url": "https://example.com", "description": "temporary",
    })
    r = client.patch("/api/tools/managed/clear_optional", json={"description": None})
    assert r.status_code == 200, r.text
    assert "description" not in client.cfg_state["cfg"]["tools"]["clear_optional"]


def test_put_replaces_and_can_move_block(client):
    client.post("/api/tools/managed", json={
        "name": "mover", "phase": "pre_call", "url": "https://a.example.com"})
    # Replace as a post_call webhook -> moves from tools (pre_call) to tools (post_call kind)
    r = client.put("/api/tools/managed/mover", json={
        "phase": "in_call", "url": "https://b.example.com",
        "description": "now in-call"})
    assert r.status_code == 200
    assert r.json()["block"] == "in_call_tools"
    assert r.json()["config"]["url"] == "https://b.example.com"
    # Old pre_call entry must be gone, not duplicated
    assert "mover" not in client.cfg_state["cfg"]["tools"]
    assert "mover" in client.cfg_state["cfg"]["in_call_tools"]


def test_patch_phase_change_moves_block(client):
    client.post("/api/tools/managed", json={
        "name": "ph", "phase": "pre_call", "url": "https://a.example.com"})
    r = client.patch("/api/tools/managed/ph", json={"phase": "post_call"})
    assert r.status_code == 200
    assert r.json()["kind"] == "generic_webhook"
    assert r.json()["block"] == "tools"
    assert r.json()["phase"] == "post_call"


def test_get_missing_404(client):
    assert client.get("/api/tools/managed/ghost").status_code == 404


def test_patch_missing_404(client):
    assert client.patch("/api/tools/managed/ghost", json={"enabled": False}).status_code == 404


def test_delete_missing_404(client):
    assert client.delete("/api/tools/managed/ghost").status_code == 404


# ---------------------------------------------------------------------------
# Built-in tools + tools settings
# ---------------------------------------------------------------------------

def test_builtin_list_includes_all_known(client):
    tools = client.get("/api/tools/builtin").json()
    names = [t["name"] for t in tools]
    assert names == list(tools_api.BUILTIN_TOOL_NAMES)
    # None configured in the seed (only ai_identity present)
    assert all(t["configured"] is False and t["enabled"] is False for t in tools)


def test_builtin_get_unknown_404(client):
    assert client.get("/api/tools/builtin/not_a_tool").status_code == 404


def test_builtin_patch_enables_and_merges(client):
    r = client.patch("/api/tools/builtin/hangup_call", json={
        "enabled": True, "farewell_message": "Bye!",
        "policy": {"mode": "normal"},
    })
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True
    assert r.json()["configured"] is True
    assert r.json()["config"]["farewell_message"] == "Bye!"

    # Deep merge: adding a nested key keeps existing nested keys
    r = client.patch("/api/tools/builtin/hangup_call", json={"policy": {"enforce_transcript_offer": True}})
    cfg = r.json()["config"]
    assert cfg["policy"]["mode"] == "normal"
    assert cfg["policy"]["enforce_transcript_offer"] is True
    assert cfg["farewell_message"] == "Bye!"


def test_builtin_patch_null_removes_key(client):
    client.patch("/api/tools/builtin/leave_voicemail", json={"enabled": True, "extension": "2765"})
    r = client.patch("/api/tools/builtin/leave_voicemail", json={"extension": None})
    assert "extension" not in r.json()["config"]
    assert r.json()["config"]["enabled"] is True


def test_builtin_put_replaces(client):
    client.patch("/api/tools/builtin/transfer", json={"enabled": True, "technology": "PJSIP"})
    r = client.put("/api/tools/builtin/transfer", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["config"] == {"enabled": False}


def test_builtin_patch_unknown_404(client):
    assert client.patch("/api/tools/builtin/ghost", json={"enabled": True}).status_code == 404


def test_settings_get_and_patch_farewell_delay(client):
    assert client.get("/api/tools/settings").json()["farewell_hangup_delay_sec"] is None
    r = client.patch("/api/tools/settings", json={"farewell_hangup_delay_sec": 4})
    assert r.status_code == 200
    assert r.json()["farewell_hangup_delay_sec"] == 4.0
    assert client.cfg_state["cfg"]["farewell_hangup_delay_sec"] == 4.0


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", -1, 301])
def test_settings_rejects_invalid_farewell_delay_without_persisting(client, value):
    before = copy.deepcopy(client.cfg_state["cfg"])
    r = client.patch("/api/tools/settings", json={"farewell_hangup_delay_sec": value})
    assert r.status_code == 422, r.text
    assert client.cfg_state["cfg"] == before


@pytest.mark.parametrize("value", [0, 300])
def test_settings_accepts_farewell_delay_boundaries(client, value):
    r = client.patch("/api/tools/settings", json={"farewell_hangup_delay_sec": value})
    assert r.status_code == 200, r.text
    assert r.json()["farewell_hangup_delay_sec"] == float(value)


def test_settings_patch_tools_block_key(client):
    r = client.patch("/api/tools/settings", json={"default_action_timeout": 30})
    assert r.status_code == 200
    assert r.json()["settings"]["default_action_timeout"] == 30
    assert client.cfg_state["cfg"]["tools"]["default_action_timeout"] == 30


def test_settings_rejects_builtin_tool_key(client):
    r = client.patch("/api/tools/settings", json={"transfer": {"enabled": True}})
    assert r.status_code == 422


@pytest.mark.parametrize("body, why", [
    ({"ai_identity": {"name": "x"}}, "RESERVED_TOOL_NAMES"),
    ({"mcp_example": {"enabled": True}}, "mcp_* prefix"),
    ({"transfer_call": {"enabled": True}}, "registry-reserved engine tool name"),
    ({"new_tool": {"kind": "generic_http_lookup", "url": "https://x.example"}}, "managed-tool-shaped doc (has 'kind')"),
    ({"check_extension_status": {"enabled": True}}, "engine built-in (now in BUILTIN_TOOL_NAMES)"),
])
def test_settings_rejects_reserved_or_tool_shaped(client, body, why):
    # /settings must not be a back door for creating/modifying tools, which have
    # their own validated endpoints (/builtin, /managed). All of these must 422.
    before = copy.deepcopy(client.cfg_state["cfg"])
    r = client.patch("/api/tools/settings", json=body)
    assert r.status_code == 422, f"{why}: {r.text}"
    # Rejected before persistence -> config is untouched.
    assert client.cfg_state["cfg"] == before, f"{why}: config was mutated"


def test_settings_excludes_tools_and_identity(client):
    # ai_identity and managed http tools must not leak into settings
    client.post("/api/tools/managed", json={
        "name": "http_one", "phase": "pre_call", "url": "https://a.example.com"})
    settings = client.get("/api/tools/settings").json()["settings"]
    assert "ai_identity" not in settings
    assert "http_one" not in settings


def test_openapi_surfaces_managed_tool_schema():
    """response_model wiring produces typed schemas in the spec."""
    app = FastAPI()
    app.include_router(tools_api.router, prefix="/api/tools")
    spec = TestClient(app).get("/openapi.json").json()
    assert "/api/tools/managed" in spec["paths"]
    assert "/api/tools/managed/{name}" in spec["paths"]
    assert "ManagedToolOut" in spec["components"]["schemas"]
    out = spec["components"]["schemas"]["ManagedToolOut"]
    for col in ("name", "phase", "kind", "block", "enabled", "is_global", "config"):
        assert col in out["properties"], col
    list_schema = spec["paths"]["/api/tools/managed"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert list_schema.get("type") == "array"
    # Built-in + settings resources are present and typed too.
    assert "/api/tools/builtin" in spec["paths"]
    assert "/api/tools/builtin/{name}" in spec["paths"]
    assert "/api/tools/settings" in spec["paths"]
    assert "BuiltinToolOut" in spec["components"]["schemas"]
    assert "ToolsSettingsOut" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# MED-E1: email validation must run on EVERY persistence path, including the
# structured Tools API (regression: _persist_cfg -> persist_config_content used
# to bypass _assert_tool_emails_valid, which only the raw /yaml editor called).
# ---------------------------------------------------------------------------

@pytest.fixture
def email_client(monkeypatch):
    """Client whose writes go through the REAL config_api.persist_config_content,
    so the centralized email validation is exercised end-to-end via the built-in
    Tools API. Schema validation + filesystem are stubbed so the test is hermetic;
    only the email check runs for real, and persisted writes are captured."""
    state = {
        "cfg": {
            "tools": {"ai_identity": {"name": "AI Agent", "number": "6789"}},
            "in_call_tools": {},
        },
        "writes": [],
    }

    monkeypatch.setattr(tools_api, "_load_cfg", lambda: copy.deepcopy(state["cfg"]))

    # Hermetic persist path: only _assert_tool_emails_valid (kept real) decides
    # whether the write happens. _persist_cfg itself is NOT stubbed.
    monkeypatch.setattr(config_api, "_validate_ai_agent_config", lambda content: {"warnings": []})
    monkeypatch.setattr(config_api, "_migrate_inline_provider_secrets", lambda parsed: False)
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: {})
    monkeypatch.setattr(config_api, "_read_base_config_dict", lambda: {})
    monkeypatch.setattr(config_api, "_compute_local_override", lambda base, parsed: parsed)

    def _record_write(content):
        state["writes"].append(content)
        state["cfg"] = yaml.safe_load(content) or {}

    monkeypatch.setattr(config_api, "_write_local_config", _record_write)

    app = FastAPI()
    app.include_router(tools_api.router, prefix="/api/tools")
    client = TestClient(app)
    client.state = state
    return client


def test_builtin_patch_rejects_invalid_email_and_does_not_persist(email_client):
    r = email_client.patch("/api/tools/builtin/send_email_summary",
                           json={"admin_email": "not-an-email"})
    assert r.status_code == 422, r.text
    assert email_client.state["writes"] == []  # invalid data must not be persisted
    assert "admin_email" not in email_client.state["cfg"]["tools"].get("send_email_summary", {})


def test_builtin_put_rejects_invalid_by_context_email(email_client):
    r = email_client.put("/api/tools/builtin/send_email_summary",
                         json={"admin_email_by_context": {"sales": "ok@example.com", "support": "nope@@bad"}})
    assert r.status_code == 422, r.text
    assert email_client.state["writes"] == []


def test_builtin_patch_accepts_valid_email_and_persists(email_client):
    r = email_client.patch("/api/tools/builtin/send_email_summary",
                           json={"admin_email": "ops@example.com"})
    assert r.status_code == 200, r.text
    assert len(email_client.state["writes"]) == 1  # valid data persisted once
    persisted = yaml.safe_load(email_client.state["writes"][0])
    assert persisted["tools"]["send_email_summary"]["admin_email"] == "ops@example.com"
