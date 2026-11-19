import copy
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from api import pushover, tools


@pytest.fixture
def client(monkeypatch):
    state = {"tools": {}, "in_call_tools": {}}
    monkeypatch.setattr(tools, "_load_cfg", lambda: copy.deepcopy(state))

    async def persist(cfg):
        state.clear()
        state.update(copy.deepcopy(cfg))
        return {"apply_required": True, "restart_required": False}

    monkeypatch.setattr(tools, "_persist_cfg", persist)
    monkeypatch.setattr(tools, "_reserved_builtin_cache", frozenset())
    sender = AsyncMock()
    sender.send.return_value = {"delivery_status": "accepted"}
    monkeypatch.setattr(pushover, "get_sender", lambda: sender)
    writes = []
    monkeypatch.setattr(
        pushover.config_api,
        "_write_provider_secret",
        lambda name, kind, value: writes.append((name, value)),
    )
    monkeypatch.setattr(
        pushover.config_api,
        "_provider_secret_path",
        lambda name, kind: "/managed/" + name,
    )
    app = FastAPI()
    app.include_router(pushover.router, prefix="/api/pushover")
    app.include_router(tools.router, prefix="/api/tools")
    with TestClient(app) as browser:
        yield browser, state, sender, writes


def test_named_tools_use_normal_crud_and_catalog_classification(client):
    browser, state, sender, _ = client
    body = {
        "name": "notify_sales",
        "kind": "pushover",
        "phase": "in_call",
        "description": "Use before Sales handoff",
        "recipient_label": "Sales",
        "body": "{summary}",
    }
    result = browser.post("/api/tools/managed", json=body)
    assert result.status_code == 201, result.text
    assert state["in_call_tools"]["notify_sales"]["description"] == body["description"]
    assert tools._http_tool_names_from_config(state) == {"notify_sales"}
    assert browser.get("/api/tools/managed/notify_sales").json()["kind"] == "pushover"
    result = browser.patch(
        "/api/tools/managed/notify_sales",
        json={"phase": "post_call", "is_global": False},
    )
    assert result.status_code == 200, result.text
    assert result.json()["kind"] == "pushover" and "notify_sales" in state["tools"]
    assert "notify_sales" not in state["in_call_tools"]
    assert browser.delete("/api/tools/managed/notify_sales").status_code == 200
    assert not sender.send.called


def test_credential_staging_is_immutable_masked_and_does_not_save_tool_or_send(client):
    browser, state, sender, writes = client
    first = browser.post(
        "/api/pushover/credentials", json={"token": "a" * 30, "recipient": "b" * 30}
    )
    second = browser.post("/api/pushover/credentials", json={"token": "c" * 30})
    assert (
        first.status_code == 200
        and first.json()["token_file"] != second.json()["token_file"]
    )
    assert "a" * 30 not in first.text and len(writes) == 3
    assert state == {"tools": {}, "in_call_tools": {}} and not sender.send.called
    bad = browser.post("/api/pushover/credentials", json={"token": "secret-invalid"})
    assert (
        bad.status_code == 422 and "secret-invalid" not in bad.text and len(writes) == 3
    )


def test_preview_validation_and_explicit_test_are_independent(client):
    browser, _, sender, _ = client
    config = {
        "kind": "pushover",
        "phase": "in_call",
        "title": "Booking update",
        "body": "{summary}",
        "token_file": "/managed/app",
        "key_file": "/managed/user",
    }
    result = browser.post("/api/pushover/preview", json={"config": config})
    assert result.status_code == 200 and result.json()["title"] == "Booking update"
    assert not sender.send.called
    assert (
        browser.post("/api/pushover/validate", json={"config": config}).status_code
        == 200
    )
    assert (
        sender.send.call_args.kwargs["validate"]
        and sender.send.call_args.kwargs["message"] is None
    )
    assert (
        browser.post("/api/pushover/test", json={"config": config}).status_code == 200
    )
    assert not sender.send.call_args.kwargs["validate"] and sender.send.call_count == 2
    result = browser.post(
        "/api/pushover/preview", json={"config": {**config, "token": "secret-invalid"}}
    )
    assert result.status_code == 422 and "secret-invalid" not in result.text


def test_native_validation_rejects_unsupported_phase_summary_and_endpoint(client):
    browser, _, sender, _ = client
    for extra in (
        {"url": "https://attacker.invalid"},
        {"priority": 2},
        {"generate_summary": True},
        {"phase": "pre_call", "generate_summary": True, "summary_provider": "test_llm"},
    ):
        result = browser.post(
            "/api/tools/managed",
            json={"name": "invalid", "kind": "pushover", "phase": "in_call", **extra},
        )
        assert result.status_code == 422, result.text
    assert not sender.send.called


def test_full_document_validation_rejects_native_secret_without_echoing():
    from api.config import _validate_ai_agent_config
    from fastapi import HTTPException
    import yaml

    with pytest.raises(HTTPException) as error:
        _validate_ai_agent_config(
            yaml.safe_dump(
                {
                    "in_call_tools": {
                        "notify": {
                            "kind": "pushover",
                            "phase": "in_call",
                            "token": "secret-value-never-echo",
                        }
                    }
                }
            )
        )
    assert error.value.status_code == 400
    assert error.value.detail == "Invalid managed Pushover tool configuration"
    assert "secret-value" not in str(error.value.detail)


def test_full_document_validation_protects_builtin_tool_names():
    from api.config import _validate_ai_agent_config
    from fastapi import HTTPException
    import yaml

    with pytest.raises(HTTPException) as error:
        _validate_ai_agent_config(
            yaml.safe_dump(
                {
                    "in_call_tools": {
                        "hangup_call": {"kind": "pushover", "phase": "in_call"}
                    }
                }
            )
        )
    assert error.value.status_code == 400
    assert error.value.detail == "Invalid managed Pushover tool configuration"


def test_multiple_recipient_credentials_are_staged_without_plaintext(client):
    browser, _, sender, writes = client
    response = browser.post(
        "/api/pushover/credentials",
        json={"token": "a" * 30, "recipients": {"alice": "b" * 30, "team": "c" * 30}},
    )
    assert response.status_code == 200 and len(writes) == 3 and not sender.send.called
    assert set(response.json()["recipients"]) == {"alice", "team"}
    assert "b" * 30 not in response.text and "c" * 30 not in response.text
    bad = browser.post(
        "/api/pushover/credentials",
        json={"recipients": {"alice": "b" * 30, "bad": "not-a-key"}},
    )
    assert bad.status_code == 422 and len(writes) == 3 and "not-a-key" not in bad.text


def test_enabled_recipients_and_specific_explicit_test_scope(client):
    browser, _, sender, _ = client
    config = {
        "kind": "pushover",
        "phase": "in_call",
        "recipients": {
            "alice": {"label": "Alice", "key_file": "one"},
            "team": {"label": "Group", "key_file": "two"},
            "off": {"enabled": False, "key_file": "three"},
        },
    }
    result = browser.post(
        "/api/pushover/test", json={"config": config, "recipient_id": "alice"}
    )
    assert result.status_code == 200 and sender.send.call_count == 1
    assert set(result.json()["notification_deliveries"]) == {"alice"}
    assert (
        browser.post(
            "/api/pushover/test", json={"config": config, "recipient_id": "off"}
        ).status_code
        == 422
    )
    assert (
        browser.post(
            "/api/pushover/test", json={"config": config, "recipient_id": "removed"}
        ).status_code
        == 422
    )
    sender.send.reset_mock()
    result = browser.post("/api/pushover/validate", json={"config": config})
    assert (
        sender.send.call_count == 2
        and result.json()["notification_deliveries"]["off"]["delivery_status"]
        == "disabled"
    )


def test_raw_yaml_reserved_name_probe_matches_registry(monkeypatch):
    from api.config import _validate_ai_agent_config
    from fastapi import HTTPException
    import yaml

    monkeypatch.setattr(tools, "_reserved_builtin_cache", None)
    with pytest.raises(HTTPException) as error:
        _validate_ai_agent_config(
            yaml.safe_dump(
                {
                    "in_call_tools": {
                        "blind_transfer": {"kind": "pushover", "phase": "in_call"}
                    }
                }
            )
        )
    assert error.value.status_code == 400
    assert error.value.detail == "Invalid managed Pushover tool configuration"


def test_clearing_recipient_map_cannot_revive_legacy_key(client):
    browser, state, _, _ = client
    response = browser.post(
        "/api/tools/managed",
        json={
            "name": "legacy_notice",
            "kind": "pushover",
            "phase": "in_call",
            "key_file": "old-key-reference",
        },
    )
    assert response.status_code == 201
    response = browser.patch(
        "/api/tools/managed/legacy_notice", json={"recipients": None}
    )
    assert response.status_code == 200
    config = state["in_call_tools"]["legacy_notice"]
    assert config["recipients"] == {} and config["key_file"] == ""


@pytest.mark.parametrize(
    "phase,http_kind",
    [
        ("pre_call", "generic_http_lookup"),
        ("in_call", "in_call_http_lookup"),
        ("post_call", "generic_webhook"),
    ],
)
def test_patch_cannot_convert_pushover_to_http(client, phase, http_kind):
    browser, state, _, _ = client
    created = browser.post(
        "/api/tools/managed",
        json={"name": "native_notice", "kind": "pushover", "phase": phase},
    )
    assert created.status_code == 201, created.text
    before = copy.deepcopy(state)
    rejected = browser.patch(
        "/api/tools/managed/native_notice", json={"kind": http_kind}
    )
    assert rejected.status_code == 422 and state == before
    for kind_patch in ({}, {"kind": "pushover"}):
        assert (
            browser.patch(
                "/api/tools/managed/native_notice", json=kind_patch
            ).status_code
            == 200
        )


@pytest.mark.parametrize("phase", ["pre_call", "in_call", "post_call"])
def test_new_default_tools_and_previews_are_generic(client, phase):
    browser, state, sender, _ = client
    created = browser.post(
        "/api/tools/managed",
        json={"name": "default_notice", "kind": "pushover", "phase": phase},
    )
    assert created.status_code == 201, created.text
    saved = browser.get("/api/tools/managed/default_notice").json()["config"]
    assert saved["body"] == "" and saved["title"] == ""
    preview = browser.post("/api/pushover/preview", json={"config": saved})
    assert preview.status_code == 200
    expected = "Caller: +15550102030\nCall: synthetic-preview"
    if phase == "post_call":
        expected += "\nOutcome: caller_hangup"
    assert preview.json()["message"] == expected
    assert not sender.send.called


@pytest.mark.parametrize("phase", ["pre_call", "in_call", "post_call"])
def test_custom_crm_templates_survive_save_reload_patch_and_legacy_upgrade(
    client, phase
):
    browser, _, sender, _ = client
    body = "Company: {company}\nCRM: {crm_status}\nCaller: {caller_number}\nCustom: {{literal}}"
    payload = {
        "name": "custom_notice",
        "kind": "pushover",
        "phase": phase,
        "title": "Custom {agent}",
        "body": body,
        "key_file": "/managed/legacy",
    }
    created = browser.post("/api/tools/managed", json=payload)
    assert created.status_code == 201, created.text
    saved = browser.get("/api/tools/managed/custom_notice").json()["config"]
    assert saved["body"] == body and saved["title"] == payload["title"]
    assert saved["recipients"]["legacy"]["key_file"] == payload["key_file"]
    assert (
        browser.patch(
            "/api/tools/managed/custom_notice", json={"enabled": False}
        ).json()["config"]["body"]
        == body
    )
    assert (
        browser.put(
            "/api/tools/managed/custom_notice", json={"name": "custom_notice", **saved}
        ).json()["config"]["body"]
        == body
    )
    assert (
        "Company: Example Company\nCRM: unknown"
        in browser.post("/api/pushover/preview", json={"config": saved}).json()[
            "message"
        ]
    )
    assert not sender.send.called


def test_native_name_limit_matches_runtime_without_changing_http_contract(client):
    browser, state, _, _ = client
    native = {"kind": "pushover", "phase": "in_call"}
    valid_name = "n" * 64
    invalid_name = "n" * 65
    assert (
        browser.post(
            "/api/tools/managed", json={"name": valid_name, **native}
        ).status_code
        == 201
    )
    before = copy.deepcopy(state)
    assert (
        browser.post(
            "/api/tools/managed", json={"name": invalid_name, **native}
        ).status_code
        == 422
    )
    assert state == before
    http = {
        "name": invalid_name,
        "kind": "in_call_http_lookup",
        "phase": "in_call",
        "url": "https://example.com",
    }
    assert browser.post("/api/tools/managed", json=http).status_code == 201
    before = copy.deepcopy(state)
    assert (
        browser.put(
            "/api/tools/managed/" + invalid_name, json={"name": invalid_name, **native}
        ).status_code
        == 422
    )
    assert state == before
    # Historical invalid native entries also cannot be republished through PATCH.
    state["in_call_tools"][invalid_name] = native
    before = copy.deepcopy(state)
    assert (
        browser.patch(
            "/api/tools/managed/" + invalid_name, json={"enabled": False}
        ).status_code
        == 422
    )
    assert state == before


@pytest.mark.parametrize("phase", ["pre_call", "in_call", "post_call"])
@pytest.mark.parametrize(
    "extra",
    [
        {"body_template": "custom body that must not disappear"},
        {"payload_template": '{"custom": "must not disappear"}' },
        {"query_params": {"custom": "value"}},
        {"output_variables": {"custom": "value"}},
        {"method": "GET"},
        {"return_raw_json": True},
    ],
)
def test_native_rejects_nonempty_http_only_fields_in_all_write_paths(
    client, phase, extra
):
    browser, state, sender, _ = client
    payload = {"name": "native_guard", "phase": phase, "kind": "pushover"}
    before = copy.deepcopy(state)
    assert (
        browser.post("/api/tools/managed", json={**payload, **extra}).status_code == 422
    )
    assert state == before
    assert browser.post("/api/tools/managed", json=payload).status_code == 201
    before = copy.deepcopy(state)
    assert (
        browser.put(
            "/api/tools/managed/native_guard", json={**payload, **extra}
        ).status_code
        == 422
    )
    assert state == before
    assert (
        browser.patch("/api/tools/managed/native_guard", json=extra).status_code == 422
    )
    assert state == before and not sender.send.called


@pytest.mark.parametrize("phase", ["pre_call", "in_call", "post_call"])
def test_native_accepts_empty_http_write_defaults_and_native_edits(client, phase):
    browser, _, sender, _ = client
    defaults = {
        "name": "empty_defaults",
        "phase": phase,
        "kind": "pushover",
        "headers": {},
        "parameters": [],
        "query_params": {},
        "body_template": "",
        "payload_template": "",
        "output_variables": {},
        "call_metadata_fields": {},
        "return_raw_json": False,
        "method": "POST",
    }
    response = browser.post("/api/tools/managed", json=defaults)
    assert response.status_code == 201, response.text
    assert (
        browser.put("/api/tools/managed/empty_defaults", json=defaults).status_code
        == 200
    )
    assert (
        browser.patch("/api/tools/managed/empty_defaults", json=defaults).status_code
        == 200
    )
    edited = browser.patch(
        "/api/tools/managed/empty_defaults",
        json={"body": "Caller: {caller_number}\nCall: {call_id}"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["config"]["body"] == "Caller: {caller_number}\nCall: {call_id}"
    assert not sender.send.called
