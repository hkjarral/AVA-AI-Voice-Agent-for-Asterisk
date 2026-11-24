from copy import deepcopy
from secrets import token_hex
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import config, tools
from services.yaml_secrets import mask_yaml_secrets, restore_yaml_secrets


def test_mask_and_restore_yaml_preserves_comments_and_reference_fields():
    key = token_hex()
    source = f"# operator note\nproviders:\n  example:\n    api_key: {key} # credential\n    api_key_env: MODEL_AUTH\n"
    masked = mask_yaml_secrets(source)
    assert key not in masked and "# operator note" in masked
    marker = yaml.safe_load(masked)["providers"]["example"]["api_key"]
    assert marker.startswith("«secret:providers.example.api_key~")
    assert "api_key_env: MODEL_AUTH" in masked
    assert restore_yaml_secrets(masked, source) == source
    edited = masked.replace(marker, "replacement")
    assert yaml.safe_load(restore_yaml_secrets(edited, source))["providers"]["example"]["api_key"] == "replacement"


def test_references_stay_visible_and_embedded_defaults_are_masked():
    source = "api_key: ${MY_API_KEY}\nheaders:\n  Authorization: Bearer ${HTTP_AUTH}\n"
    assert mask_yaml_secrets(source) == source
    secret = token_hex()
    assert secret not in mask_yaml_secrets("api_key: ${MY_API_KEY:-" + secret + "}\n")


def test_secret_paths_keep_dotted_keys_and_list_positions_distinct():
    source = "a.b:\n  - api_key: one\na:\n  b:\n    - api_key: two\n"
    result = yaml.safe_load(mask_yaml_secrets(source))
    one, two = result["a.b"][0]["api_key"], result["a"]["b"][0]["api_key"]
    assert one != two
    with pytest.raises(ValueError):
        restore_yaml_secrets(mask_yaml_secrets(source).replace(two, one), source)
    with pytest.raises(ValueError):
        restore_yaml_secrets("x: «secret:a.b.api_key»\n", source)


@pytest.fixture
def api(tmp_path, monkeypatch):
    path = tmp_path / "base.yaml"
    local = tmp_path / "local.yaml"
    key = token_hex()
    cfg = {"providers": {"example": {"api_key": key}},
           "tools": {"hangup_call": {"smtp_password": key},
                     "custom_setting": {"service_token": key},
                     "lookup": {"kind": "generic_http_lookup", "phase": "pre_call", "enabled": True,
                                "url": "https://lookup.invalid", "headers": {"Authorization": key}}}}
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    local.write_text("# operator note\n{}\n")
    monkeypatch.setattr(config.settings, "CONFIG_PATH", str(path))
    monkeypatch.setattr(config.settings, "LOCAL_CONFIG_PATH", str(local))
    monkeypatch.setattr(config.settings, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(config, "_fetch_engine_config_state", AsyncMock(return_value=None))
    monkeypatch.setattr(config, "_validate_ai_agent_config", lambda *args, **kwargs: {"warnings": []})
    monkeypatch.setattr(config, "_migrate_inline_provider_secrets", lambda parsed: False)
    app = FastAPI()
    app.include_router(config.router, prefix="/config")
    app.include_router(tools.router, prefix="/tools")
    return TestClient(app), path, local, key


@pytest.mark.parametrize("route", ["/config", "/config/", "/tools/managed", "/tools/managed/lookup",
                                   "/tools/builtin", "/tools/builtin/hangup_call", "/tools/settings"])
def test_structured_reads_return_secret_presence(api, route):
    client, _, _, key = api
    response = client.get(route)
    assert response.status_code == 200 and key not in response.text
    assert '"set":true' in response.text


def test_raw_yaml_noop_is_byte_identical_and_concurrent_secret_change_is_preserved(api):
    client, base, local, key = api
    response = client.get("/config/yaml")
    content = response.json()["content"]
    assert key not in content and "«secret:providers.example.api_key~" in content
    original = local.read_bytes()
    assert client.post("/config/yaml", json={"content": content}).status_code == 200
    assert local.read_bytes() == original
    rotated = token_hex()
    base.write_text(base.read_text().replace(key, rotated))
    changed = client.post("/config/yaml", json={"content": content + "log_level: debug\n"})
    assert changed.status_code == 200 and rotated not in changed.text
    assert config._read_merged_config_dict()["providers"]["example"]["api_key"] == rotated


@pytest.mark.parametrize("method,route,payload", [
    ("patch", "/tools/builtin/hangup_call", {"smtp_password": {"set": True}}),
    ("put", "/tools/builtin/hangup_call", {"smtp_password": {"set": False}}),
    ("patch", "/tools/settings", {"custom_setting": {"service_token": {"set": True}}}),
    ("patch", "/tools/managed/lookup", {"headers": {"Authorization": {"set": True}}}),
    ("put", "/tools/managed/lookup", {"phase": "pre_call", "url": "https://lookup.invalid",
                                      "headers": {"Authorization": {"set": True}}}),
])
def test_structured_write_preserves_and_masks_echoed_secret(api, method, route, payload):
    client, _, local, key = api
    response = client.request(method, route, json=payload)
    assert response.status_code == 200 and key not in response.text
    assert '"set":true' in response.text
    assert "{'set'" not in local.read_text() and "set: true" not in local.read_text()
    cfg = config._read_merged_config_dict()["tools"]
    assert cfg["lookup"]["headers"]["Authorization"] == cfg["custom_setting"]["service_token"] == cfg["hangup_call"]["smtp_password"] == key


def test_managed_create_masks_secret_and_cannot_claim_unrelated_stored_value(api):
    client, _, _, key = api
    body = {"name": "new_tool", "phase": "pre_call", "url": "https://lookup.invalid", "headers": {"X-API-Key": {"set": True}}}
    assert client.post("/tools/managed", json=body).status_code == 400
    body["headers"]["X-API-Key"] = key
    response = client.post("/tools/managed", json=body)
    assert response.status_code == 201 and key not in response.text
    assert response.json()["config"]["headers"]["X-API-Key"] == {"set": True}


def test_invalid_yaml_never_returns_text_or_logs_source(api, caplog):
    client, base, _, key = api
    base.write_text(f"api_key: [{key}\n")
    response = client.get("/config/yaml")
    assert response.status_code == 400 and key not in response.text and key not in caplog.text
    refused = client.post("/config/yaml", json={"content": f"api_key: [{key}\n"})
    assert refused.status_code == 400 and key not in refused.text and key not in caplog.text


def test_invalid_structured_requests_do_not_reflect_private_input(api):
    client, _, _, key = api
    for route, body in [
        ("/config/yaml", {"content": {"api_key": key}}),
        ("/tools/managed", {"phase": "pre_call", "headers": {"Authorization": [key]}}),
    ]:
        result = client.post(route, json=body)
        assert result.status_code == 422 and key not in result.text


def test_opaque_structured_credentials_and_aliases_are_private(api):
    client, base, _, key = api
    base.write_text(f"provider_token: &t {key}\nlegacy_alias: *t\ncredentials: &c {{account: &account {key}}}\naccount_alias: *account\ncopy: *c\n")
    for route in ("/config", "/config/yaml"):
        assert key not in client.get(route).text


def test_concurrent_managed_edits_cannot_revert_a_credential_rotation(api, monkeypatch):
    client, base, _, old = api
    rotated = token_hex()
    async def rotate(_doc):
        base.write_text(base.read_text().replace(old, rotated))
    monkeypatch.setattr(tools, "_validate_summary_provider_selection", rotate)
    result = client.patch("/tools/managed/lookup", json={"headers": {"Authorization": {"set": True}}})
    assert result.status_code == 409
    assert config._read_merged_config_dict()["tools"]["lookup"]["headers"]["Authorization"] == rotated


def test_saved_provider_probe_uses_credential_only_for_saved_config(api, monkeypatch):
    import httpx

    client, base, _, key = api
    provider = {"llm_model": "gemini-test", "api_key": key}
    base.write_text(yaml.safe_dump({"providers": {"google_live": provider}}))
    masked = yaml.safe_load(client.get("/config/yaml").json()["content"])["providers"]["google_live"]
    stub = AsyncMock()
    stub.__aenter__.return_value = stub
    stub.get.return_value = httpx.Response(200)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: stub)
    reply = client.post("/config/providers/test", json={"name": "google_live", "config": masked})
    assert reply.json()["success"] and stub.get.await_args.kwargs["headers"] == {"x-goog-api-key": key}
    changed = client.post("/config/providers/test", json={"name": "google_live", "config": {**masked, "base_url": "https://changed.invalid"}})
    assert not changed.json()["success"] and stub.get.await_count == 1


def test_http_tool_probe_uses_scoped_saved_header_and_masks_receiver_echo(api, monkeypatch):
    import httpx

    client, _, _, key = api
    doc = yaml.safe_load(client.get("/config/yaml").json()["content"])["tools"]["lookup"]
    payload = {"tool_name": "lookup", "url": doc["url"], "headers": doc["headers"]}
    stub = AsyncMock()
    stub.__aenter__.return_value = stub
    stub.request.return_value = httpx.Response(200, text=key)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: stub)
    monkeypatch.setattr(tools, "_validate_http_tool_test_target", lambda url: None)
    response = client.post("/tools/test-http", json=payload)
    assert response.status_code == 200 and key not in response.text
    assert stub.request.await_args.kwargs["headers"]["Authorization"] == key
    refused = client.post("/tools/test-http", json={**payload, "url": "https://changed.invalid"})
    assert refused.status_code == 400 and stub.request.await_count == 1


@pytest.mark.parametrize("stored", ["api_key: [", "- a list\n"])
def test_valid_raw_save_leaves_unreadable_local_document_untouched(api, stored):
    client, _, local, key = api
    body = stored + " # " + key
    local.write_text(body)
    response = client.post("/config/yaml", json={"content": "valid_setting: true\n"})
    assert response.status_code == 400 and key not in response.text
    assert local.read_text() == body


def test_blank_raw_save_leaves_existing_document_untouched(api):
    client, _, local, key = api
    body = "api_key: " + key + "\n"
    local.write_text(body)
    response = client.post("/config/yaml", json={"content": "   \n"})
    assert response.status_code == 400 and key not in response.text
    assert local.read_text() == body


def test_location_and_embedded_credentials_mask_without_hiding_destinations(api):
    from services.yaml_secrets import mask_config, restore_config

    key = token_hex()
    url = f"https://user:{key}@example.invalid/hooks/{key}?sig={key}&mode={key}"
    doc = {"url": url, "body_template": f'{{"authorization": "Bearer {key}"}}',
           "servers": {"fixture": {"env": {"LABEL": key}, "args": ["sk-" + key]}},
           "ws_extra_headers": {"Version": key, "Accept": "application/json"},
           "headers": {"apikey": key, "X-Auth": key}, "query_params": {"sig": key},
           "service_account_json": '{"opaque":"' + key + '"}'}
    doc = {"tools": {"sample": doc}}
    for raw in (True, False):
        masked = mask_config(doc, raw=raw)
        visible = masked["tools"]["sample"]
        assert key not in repr(masked)
        assert "example.invalid" in str(visible["url"]) and "/hooks" not in str(visible["url"])
        assert visible["ws_extra_headers"]["Accept"] == "application/json"
        assert restore_config(masked, doc) == doc


def test_fingerprinted_markers_can_move_only_to_private_unchanged_values():
    from services.yaml_secrets import mask_config, restore_config

    key = token_hex()
    current = {"tools": {"old": {"url": "https://example.invalid", "headers": {"X-Auth": key}}},
               "items": [{"title": "first"}, {"api_key": key}]}
    masked = mask_config(current, raw=True)
    clone = masked["tools"]["old"].copy()
    edited = {"tools": {"new": clone}, "items": masked["items"][1:]}
    assert restore_config(edited, current) == {"tools": {"new": current["tools"]["old"]}, "items": current["items"][1:]}
    marker = clone["headers"]["X-Auth"]
    with pytest.raises(ValueError):
        restore_config({"title": marker}, current)
    with pytest.raises(ValueError):
        restore_config({"tools": {"new": {**clone, "url": "https://other.invalid"}}}, current)
    current["tools"]["old"]["headers"]["X-Auth"] = token_hex()
    with pytest.raises(ValueError):
        restore_config(edited, current)


def test_reject_public_presence_markers_and_preserve_plain_selectors(api):
    from services.yaml_secrets import mask_config, restore_config

    doc = {"tools": {"leave_voicemail": {"default_mailbox_key": "operations"},
                     "microsoft_calendar": {"accounts": {"one": {"token_cache_path": "/stored/cache.json"}}}}}
    assert mask_config(doc) == doc
    with pytest.raises(ValueError):
        restore_config({"headers": {"Accept": {"set": True}}}, {"headers": {"Accept": "text/plain"}})
    with pytest.raises(ValueError):
        restore_config({"ordinary": {"set": False}}, {})


def test_masked_probe_handles_stored_url_query_body_and_structured_headers(api, monkeypatch):
    import httpx

    client, base, _, secret = api
    tool = {"url": f"https://example.invalid/hooks/{secret}?item={secret}", "method": "POST",
            "phase": "pre_call", "kind": "generic_http_lookup", "headers": {"X-Auth": secret},
            "query_params": {"note": secret}, "body_template": '{"msg":"Bearer ' + secret + '"}'}
    base.write_text(yaml.safe_dump({"tools": {"fixture": tool}}))
    visible = client.get("/tools/managed/fixture").json()["config"]
    stub = AsyncMock()
    stub.__aenter__.return_value = stub
    stub.request.return_value = httpx.Response(200, text=secret)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: stub)
    monkeypatch.setattr(tools, "_validate_http_tool_test_target", lambda _: None)
    response = client.post("/tools/test-http", json={**visible, "tool_name": "fixture"})
    assert response.status_code == 200 and response.json()["success"] and secret not in response.text
    assert stub.request.await_args.kwargs["url"] == tool["url"]
    modified = {**visible, "url": {**visible["url"], "origin": "https://unapproved.invalid"}, "tool_name": "fixture"}
    assert client.post("/tools/test-http", json=modified).status_code == 400 and stub.request.await_count == 1


def test_markers_still_resolve_if_another_edit_already_moved_the_same_value():
    from services.yaml_secrets import mask_config, restore_config

    key = token_hex()
    original = {"items": [{"api_key": key}, {"title": "item"}]}
    edited = {"items": [{"title": "item"}, mask_config(original, raw=True)["items"][0]]}
    current = {"items": [{"title": "item"}, {"api_key": key}]}
    assert restore_config(edited, current) == current
    current["items"][1]["api_key"] = token_hex()
    with pytest.raises(ValueError):
        restore_config(edited, current)


@pytest.mark.parametrize("block", ["tools", "pre_call_tools", "post_call_tools", "in_call_tools"])
def test_structural_tool_url_and_destination_bound_markers(block):
    from services.yaml_secrets import mask_config, restore_config

    secret = token_hex()
    origin = "https://fixture.invalid:8443"
    url = origin + "/hook/x?model=nova-3"
    src = {block: {"old": {"url": url, "headers": {"X-Auth": secret}, "query_params": {"sig": secret}}}}
    structured = mask_config(src)
    assert structured[block]["old"]["url"] == {"origin": origin, "set": True}
    assert restore_config(structured, src) == src
    raw = mask_config(src, raw=True)
    masked = raw[block]["old"]
    assert origin in masked["url"] and "/hook" not in masked["url"] and "model" not in masked["url"]
    assert restore_config({block: {"new": masked}}, src) == {block: {"new": src[block]["old"]}}
    for dest in ("https://new.invalid:8443/hook", "http://fixture.invalid:8443/hook", "https://fixture.invalid:443/hook"):
        for entry in (masked, structured[block]["old"]):
            with pytest.raises(ValueError, match="destination"):
                restore_config({block: {"old": {**entry, "url": dest}}}, src)
    with pytest.raises(ValueError):
        restore_config({block: {"old": {**masked, "url": masked["url"].replace(origin, origin + "/other")}}}, src)
    same_origin = {**masked, "url": origin + "/new-hook"}
    assert restore_config({block: {"old": same_origin}}, src)[block]["old"]["headers"]["X-Auth"] == secret


def test_structural_credential_locations_and_public_prose_roundtrip():
    from services.yaml_secrets import mask_config, restore_config

    secret = token_hex()
    long = "a" + secret
    public = "Give basic information. Bearer " + long
    src = {"prompt": public, "description": public, "greeting": public, "instructions": public,
           "provider_url": "https://fixture.invalid/v1?model=nova-3",
           "mcp":{"servers":{"one":{"args":["--api-key", secret, "--extra-token=" + secret,
                                            "postgres://public:" + secret + "@db.invalid/reports",
                                            "sk_" + secret, "Token " + secret, "ApiKey " + secret],
                                     "command":["synthetic", "--password", secret]}}},
           "body_template": '{"api_key":"' + secret + '","value":"ok"}',
           "payload_template": 'ordinary=visible&auth_token=' + secret}
    masked = mask_config(src, raw=True)
    assert masked["prompt"] == masked["greeting"] == masked["description"] == masked["instructions"] == public
    assert masked["provider_url"] == src["provider_url"]
    assert secret not in str(masked["mcp"]) and secret not in masked["body_template"] and secret not in masked["payload_template"]
    assert restore_config(masked, src) == src


def test_managed_patch_changing_origin_requires_replacement_of_omitted_credentials(api):
    client, _, _, _ = api
    moved = "https://new.invalid/lookup"
    for body in ({"url": moved}, {"url": moved, "headers":{"Authorization":{"set":True}}}):
        response = client.patch("/tools/managed/lookup", json=body)
        assert response.status_code == 400 and "destination" in response.text.lower()
    response = client.patch("/tools/managed/lookup", json={"url": moved, "headers":{"Authorization": token_hex()}})
    assert response.status_code == 200 and response.json()["config"]["url"] == {"set":True,"origin":"https://new.invalid"}

def test_a2a_validation_accepts_stored_url_and_binds_retained_auth(api):
    client, base, _, _ = api
    tool = {"kind": "a2a_agent", "phase": "in_call", "agent_url": "https://fixture.invalid/service",
            "description": "Delegate a request", "bearer_token_env": "AVA_A2A_SYNTHETIC"}
    base.write_text(yaml.safe_dump({"in_call_tools": {"fixture": tool}}))
    public = client.get("/tools/managed/fixture").json()["config"]
    draft = {"name": "fixture", "config": public}
    assert client.post("/tools/a2a/validate", json=draft).status_code == 200
    public["agent_url"] = "https://elsewhere.invalid/service"
    assert client.post("/tools/a2a/validate", json=draft).status_code in {400, 422}


def test_saved_probe_renders_operator_environment_origin(api, monkeypatch):
    import httpx

    client, base, _, secret = api
    tool = {"kind": "generic_http_lookup", "url": "${FIXTURE_ORIGIN}/lookup/{call_id}", "phase": "pre_call",
            "headers": {"X-Auth": secret}, "method": "GET"}
    base.write_text(yaml.safe_dump({"tools": {"fixture": tool}}))
    public = client.get("/tools/managed/fixture").json()["config"]
    assert public["url"] == {"origin": "", "set": True}
    stub = AsyncMock()
    stub.__aenter__.return_value = stub
    stub.request.return_value = httpx.Response(200, text="ok")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: stub)
    monkeypatch.setattr(tools, "get_setting", lambda key, default="": "https://fixture.invalid" if key == "FIXTURE_ORIGIN" else default)
    monkeypatch.setattr(tools, "_validate_http_tool_test_target", lambda _: None)
    result = client.post("/tools/test-http", json={**public, "tool_name": "fixture", "test_values": {"call_id": "fixture"}})
    assert result.status_code == 200 and result.json()["success"]
    assert stub.request.await_args.kwargs["url"] == "https://fixture.invalid/lookup/fixture"


def test_short_stored_url_component_cannot_change_probe_response_shape(api, monkeypatch):
    import httpx

    client, base, _, _ = api
    tool = {"kind": "generic_http_lookup", "url": "https://fixture.invalid/body", "phase": "pre_call", "method": "GET"}
    base.write_text(yaml.safe_dump({"tools": {"fixture": tool}}))
    visible = client.get("/tools/managed/fixture").json()["config"]
    stub = AsyncMock()
    stub.__aenter__.return_value = stub
    stub.request.return_value = httpx.Response(200, json={"body": "body"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: stub)
    monkeypatch.setattr(tools, "_validate_http_tool_test_target", lambda _: None)
    response = client.post("/tools/test-http", json={**visible, "tool_name": "fixture"})
    assert response.status_code == 200
    assert response.json()["body"] == {"***": "***"}


def test_instance_names_are_not_credential_or_prose_fields():
    from services.yaml_secrets import mask_config, restore_config

    value = token_hex()
    config = {"tools": {name: {"url": "https://fixture.invalid/endpoint", "description": "public",
                               "body_template": '{"token":"' + value + '"}'}
                        for name in ["description", "auth_token"]}}
    masked = mask_config(config, raw=True)
    assert value not in str(masked)
    assert all(doc["description"] == "public" for doc in masked["tools"].values())
    assert restore_config(masked, config) == config


@pytest.mark.parametrize("field", ["base_url", "endpoint", "ws_url", "websocket_endpoint", "chat_base_url", "voice_agent_base_url"])
@pytest.mark.parametrize("raw", [False, True])
def test_provider_retained_key_binds_to_endpoint_origin(field, raw):
    from services.yaml_secrets import mask_config, restore_config

    key = token_hex()
    origin = "wss://fixture.invalid:8443"
    saved = {"providers": {"example": {field: origin + "/runtime", "api_key": key}}}
    draft = deepcopy(mask_config(saved, raw=raw))
    provider = draft["providers"]["example"]
    provider.update({field: origin + "/v2?model=example", "display_name": "Edited"})
    assert restore_config(draft, saved)["providers"]["example"]["api_key"] == key
    provider[field] = "wss://replacement.invalid:8443/v2"
    with pytest.raises(ValueError, match="api_key") as denied:
        restore_config(draft, saved)
    assert key not in str(denied.value)
    provider["api_key"] = token_hex()
    assert restore_config(draft, saved) == draft


@pytest.mark.parametrize(("field", "credential"), [
    ("api_key", "${FIXTURE_AUTH}"), ("api_key_env", "FIXTURE_AUTH"),
    ("api_key_file", "/fixture/managed-key"), ("api_key", "literal"),
])
def test_provider_default_destination_binds_existing_credential_source(field, credential):
    from services.yaml_secrets import mask_config, restore_config

    saved = {"providers": {"fixture": {field: credential}}}
    draft = deepcopy(mask_config(saved))
    draft["providers"]["fixture"]["base_url"] = "https://fixture.invalid"
    with pytest.raises(ValueError, match=field):
        restore_config(draft, saved)
    draft["providers"]["fixture"][field] = "replacement"
    assert restore_config(draft, saved) == draft


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize(("destination", "previous", "replacement", "secrets"), [
    ("url", "https://fixture.invalid/rpc", "https://replacement.invalid/rpc", {"headers": {"Custom": "opaque"}}),
    ("command", ["fixture", "service"], ["replacement", "service"], {"env": {"LABEL": "opaque"}}),
    ("command", ["fixture", "service"], ["fixture", "different"], {"env": {"LABEL": "${SAVED_VALUE}"}}),
])
def test_mcp_credentials_bind_to_transport_destination(raw, destination, previous, replacement, secrets):
    from services.yaml_secrets import mask_config, restore_config

    container = next(iter(secrets))
    saved = {"mcp": {"servers": {"fixture": {destination: previous, **secrets}}}}
    draft = deepcopy(mask_config(saved, raw=raw))
    server = draft["mcp"]["servers"]["fixture"]
    server["enabled"] = False
    assert restore_config(draft, saved)["mcp"]["servers"]["fixture"][container] == secrets[container]
    server[destination] = replacement
    with pytest.raises(ValueError, match=container) as denied:
        restore_config(draft, saved)
    assert "opaque" not in str(denied.value) and "SAVED_VALUE" not in str(denied.value)
    server[container] = {}
    assert restore_config(draft, saved) == draft


def test_destination_refusal_names_only_the_retained_field():
    from services.yaml_secrets import mask_config, restore_config

    key = token_hex()
    stored = {"url": "https://fixture.invalid", "headers": {key: key}, "payload_template": '{"token":"' + key + '"}'}
    saved = {"post_call_tools": {"fixture": stored}}
    draft = mask_config(saved, raw=True)
    tool = draft["post_call_tools"]["fixture"]
    tool.update(url="https://replacement.invalid", headers={"X-Auth": "replacement"})
    with pytest.raises(ValueError, match="payload_template") as denied:
        restore_config(draft, saved)
    assert key not in str(denied.value)
    tool["headers"], tool["payload_template"] = mask_config(saved, raw=True)["post_call_tools"]["fixture"]["headers"], "{}"
    with pytest.raises(ValueError, match="headers") as denied:
        restore_config(draft, saved)
    assert key not in str(denied.value)


def test_empty_credential_and_public_provider_settings_allow_a_destination_change():
    from services.yaml_secrets import mask_config, restore_config

    saved = {"providers": {"fixture": {"api_key": "", "headers": {"Accept": "application/json"}}}}
    draft = deepcopy(mask_config(saved))
    draft["providers"]["fixture"]["ws_url"] = "wss://fixture.invalid"
    assert restore_config(draft, saved)["providers"]["fixture"]["api_key"] == ""


def test_provider_editor_verification_checks_saved_reference_before_resolution(api, monkeypatch):
    client, base, _, _ = api
    saved = {"type": "openai", "api_key_env": "FIXTURE_AUTH", "chat_base_url": "https://fixture.invalid/v1"}
    base.write_text(yaml.safe_dump({"providers": {"fixture_llm": saved}}))
    lookup = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(config, "test_modular_provider", lookup)
    draft = {**saved, "chat_base_url": "https://replacement.invalid/v1"}
    response = client.post("/config/providers/test", json={"name": "fixture_llm", "config": draft})
    assert response.status_code == 200 and response.json()["success"] is False
    assert "api_key_env" in response.json()["message"]
    lookup.assert_not_awaited()


def test_mcp_stdio_flag_credential_reference_stays_bound_to_the_saved_command():
    from services.yaml_secrets import mask_config, restore_config

    saved = {"mcp": {"servers": {"fixture": {"command": ["service", "--api-key", "${FIXTURE_AUTH}"]}}}}
    draft = deepcopy(mask_config(saved, raw=True))
    draft["mcp"]["servers"]["fixture"]["command"][0] = "replacement"
    with pytest.raises(ValueError, match="command"):
        restore_config(draft, saved)


def test_equivalent_http_origin_and_public_mcp_settings_keep_headers():
    from services.yaml_secrets import mask_config, restore_config

    saved = {"mcp": {"servers": {"fixture": {"transport": "streamable_http", "url": "https://Fixture.invalid:443/v1",
                                            "headers": {"Custom": token_hex()}}}}}
    draft = mask_config(saved, raw=True)
    entry = draft["mcp"]["servers"]["fixture"]
    entry.update(url="https://fixture.invalid/v2", defaults={"timeout_ms": 2000})
    assert restore_config(draft, saved)["mcp"]["servers"]["fixture"]["headers"] == saved["mcp"]["servers"]["fixture"]["headers"]
    for replacement in ("http://fixture.invalid/v1", "https://fixture.invalid:8443/v1"):
        entry["url"] = replacement
        with pytest.raises(ValueError, match="headers"):
            restore_config(draft, saved)


def test_provider_verification_requires_readable_configuration_for_stored_source(api, monkeypatch, tmp_path):
    client, _, _, _ = api
    monkeypatch.setattr(config.settings, "CONFIG_PATH", str(tmp_path / "missing.yaml"))
    verify = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(config, "test_modular_provider", verify)
    source = {"name": "fixture_llm", "config": {"type": "openai", "api_key_env": "FIXTURE_AUTH"}}
    response = client.post("/config/providers/test", json=source)
    assert response.status_code == 200 and response.json()["success"] is False
    verify.assert_not_awaited()


@pytest.mark.parametrize("draft_field,reference", [("api_key", "${FIXTURE_AUTH:=}"), ("api_key_env", "FIXTURE_AUTH")])
def test_equivalent_environment_sources_keep_the_saved_provider_origin(draft_field, reference):
    from services.yaml_secrets import restore_config

    saved = {"providers": {"fixture": {"api_key": "${FIXTURE_AUTH:-}", "base_url": "https://fixture.invalid"}}}
    draft = {"providers": {"fixture": {draft_field: reference, "base_url": "https://replacement.invalid"}}}
    with pytest.raises(ValueError, match=draft_field):
        restore_config(draft, saved)
