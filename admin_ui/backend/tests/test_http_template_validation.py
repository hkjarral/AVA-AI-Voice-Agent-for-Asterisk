import json
from urllib.parse import quote, quote_plus
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import tools


def client():
    app = FastAPI()
    app.include_router(tools.router, prefix="/api/tools")
    return TestClient(app)


def test_validate_preview_is_synthetic_and_has_no_external_effects(monkeypatch):
    external = MagicMock(side_effect=AssertionError("Preview must not send HTTP or read secrets"))
    monkeypatch.setattr(tools.httpx, "AsyncClient", external)
    monkeypatch.setattr(tools, "get_setting", external)
    response = client().post("/api/tools/http-templates/validate", json={"phase": "in_call", "config": {
        "url": "${API_ORIGIN}/briefing", "method": "POST", "headers": {"Authorization": "Bearer ${API_KEY}"},
        "body_template": '{"transcript":{transcript_json}}',
    }})
    assert response.status_code == 200
    assert response.json()["valid"]
    assert response.json()["preview"]["url"] == "https://example.invalid/briefing"
    assert not external.called


@pytest.mark.parametrize("phase,field", [("pre_call", "body_template"), ("in_call", "body_template"), ("post_call", "payload_template")])
def test_invalid_json_is_rejected_on_enabled_save_and_disabled_drafts_are_retained(phase, field):
    config = {"url": "https://example.invalid", "method": "POST", field: '{"broken":'}
    with pytest.raises(tools.HTTPException) as error:
        tools._build_tool_doc(config, phase)
    assert error.value.status_code == 422
    assert error.value.detail["error_code"] == "invalid_json_template"
    assert tools._build_tool_doc({**config, "enabled": False}, phase)[field] == config[field]


def test_declared_parameters_and_unverified_enrichment_are_distinguished():
    response = client().post("/api/tools/http-templates/validate", json={"phase": "in_call", "config": {
        "url": "https://example.invalid", "method": "POST",
        "parameters": [{"name": "date", "type": "string", "required": True}],
        "body_template": '{"date":"{date}","company":"{crm_company}"}',
    }})
    result = response.json()
    assert result["valid"]
    assert all(issue["severity"] == "warning" for issue in result["issues"])
    assert any(item["name"] == "date" and item["source"] == "model_parameter" for item in result["variables"])


def test_live_test_never_sends_invalid_json_as_raw_content(monkeypatch):
    external = MagicMock(side_effect=AssertionError("Invalid template must not send"))
    monkeypatch.setattr(tools.httpx, "AsyncClient", external)
    result = client().post("/api/tools/test-http", json={
        "phase": "in_call", "url": "https://example.invalid", "method": "POST",
        "body_template": '{"missing":{missing}}',
    }).json()
    assert not result["success"]
    assert result["error_code"] == "missing_template_variable"
    assert result["template_variable"] == "missing"
    assert not external.called


@pytest.mark.asyncio
async def test_live_test_uses_runtime_escaping_and_masks_expanded_credentials(monkeypatch):
    response = httpx.Response(200, json={"ok": True})
    http = MagicMock()
    http.request = AsyncMock(return_value=response)
    manager = MagicMock(__aenter__=AsyncMock(return_value=http), __aexit__=AsyncMock(return_value=None))
    monkeypatch.setattr(tools.httpx, "AsyncClient", MagicMock(return_value=manager))
    monkeypatch.setattr(tools, "_validate_http_tool_test_target", lambda value: None)
    monkeypatch.setattr(tools, "get_setting", lambda name, default="": "private-credential")
    result = await tools.test_http_tool(tools.TestHTTPRequest(
        phase="in_call", url="https://example.invalid", method="POST",
        body_template='{"caller":"{caller_name}","credential":"${SECRET}","transcript":{transcript_json}}',
        test_values={"caller_name": 'A "quote" and a newline\n'},
    ))
    assert result.success
    sent = http.request.await_args.kwargs
    assert "content" not in sent and "json" in sent
    assert sent["json"]["caller"] == 'A "quote" and a newline\n'
    assert sent["json"]["credential"] == "private-credential"
    assert isinstance(sent["json"]["transcript"], list)
    assert "private-credential" not in result.model_dump_json()
    assert json.loads(result.resolved_body)["caller"] == sent["json"]["caller"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["pre_call", "in_call", "post_call"])
async def test_live_test_displays_sent_values_with_json_field_and_environment_masks(monkeypatch, phase):
    secrets = {"BASE_URL": "https://receiver.example.com", "SECRET": 'private"\\é\n value'}
    secret = secrets["SECRET"]
    variants = {secret, json.dumps(secret)[1:-1], json.dumps(secret, ensure_ascii=False)[1:-1],
                quote(secret, safe=""), quote_plus(secret)}
    response = httpx.Response(200, json={"echo": secret, "encoded": quote(secret, safe="")},
                              headers={"X-Echo": quote_plus(secret)})
    http = MagicMock(request=AsyncMock(return_value=response))
    manager = MagicMock(__aenter__=AsyncMock(return_value=http), __aexit__=AsyncMock(return_value=None))
    monkeypatch.setattr(tools.httpx, "AsyncClient", MagicMock(return_value=manager))
    monkeypatch.setattr(tools, "_validate_http_tool_test_target", lambda value: None)
    monkeypatch.setattr(tools, "get_setting", lambda name, default="": secrets[name])
    result = await tools.test_http_tool(tools.TestHTTPRequest(
        phase=phase, url="${BASE_URL}/hook/{call_id}", method="POST",
        body_template='{"caller":"{caller_name}","credential":"${SECRET}","nested":{"password":"literal-password"}}',
        test_values={"caller_name": "Actual test caller", "call_id": "actual-test-id"},
    ))
    assert result.success
    assert result.resolved_url == "***/hook/actual-test-id"
    body = json.loads(result.resolved_body)
    assert body["caller"] == "Actual test caller"
    assert body["credential"] == "<redacted>" and body["nested"]["password"] == "<redacted>"
    assert http.request.await_args.kwargs["json"]["nested"]["password"] == "literal-password"
    for value in (result.body_raw, *result.headers.values(), *result.body.values()):
        assert all(variant not in value for variant in variants)
    assert "receiver.example.com" not in result.model_dump_json()


def test_save_and_explicit_test_never_expose_exception_arguments(monkeypatch):
    def fail(*args, **kwargs):
        raise tools.TemplateError("synthetic-private-value", code="invalid_json_template")
    monkeypatch.setattr(tools, "assert_valid_template", fail)
    with pytest.raises(tools.HTTPException) as error:
        tools._validate_http_tool_template({}, "in_call")
    assert "synthetic-private-value" not in str(error.value.detail)
    monkeypatch.setattr(tools, "render_request", fail)
    result = client().post("/api/tools/test-http", json={"url": "https://example.invalid"})
    assert "synthetic-private-value" not in result.text
    assert result.json()["error_code"] == "invalid_json_template"


def test_support_export_keeps_the_call_correlated_template_error():
    from api.support import _filter_call_lines
    line = '2026-01-01T12:00:00Z [warning] HTTP tool template rejected call_id=synthetic-766 tool=briefing phase=in_call error_code=missing_template_variable template_variable=absent'
    filtered, *_ = _filter_call_lines(line, "synthetic-766")
    assert "missing_template_variable" in filtered
    assert "template_variable=absent" in filtered
