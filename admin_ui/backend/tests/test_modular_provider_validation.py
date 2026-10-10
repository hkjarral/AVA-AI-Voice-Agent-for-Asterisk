import asyncio
import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import yaml
from fastapi import HTTPException
from pathlib import Path

from api import config as config_api
from services import provider_validation as validation


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(config_api.settings, "ENV_PATH", str(tmp_path / ".env"))
    for name in ("OPENAI_API_KEY", "GROQ_API_KEY", "CUSTOM_API_KEY", "TELNYX_API_KEY", "MINIMAX_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def mock_http(monkeypatch, response=None, error=None):
    calls = []
    response = response if response is not None else httpx.Response(200, json={"data": [{"id": "synthetic-model"}]})

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["timeout"] == 10.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            if error:
                raise error
            return response

        async def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    return calls


async def run_api(monkeypatch, cfg, *, saved=False, name="custom_llm"):
    if saved:
        monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: {"providers": {name: cfg}})
        return await config_api.verify_provider_credentials(name)
    return await config_api.test_provider_connection(config_api.ProviderTestRequest(name=name, config=cfg))


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("kind", ["openai", "telnyx", "telenyx", "minimax"])
@pytest.mark.parametrize("field", ["chat_base_url", "base_url"])
async def test_both_apis_preserve_custom_host_port_path(monkeypatch, saved, kind, field):
    calls = mock_http(monkeypatch)
    cfg = {"type": kind, field: "http://127.0.0.1:8088/custom/api/v2/", "api_key": "synthetic-key"}
    result = await run_api(monkeypatch, cfg, saved=saved)
    assert result.get("success", result.get("status") == "success")
    assert calls[0] == ("GET", "http://127.0.0.1:8088/custom/api/v2/models", {"headers": {"Authorization": "Bearer synthetic-key"}})
    assert len(calls) == (2 if kind in {"telnyx", "telenyx"} and not saved else 1)
    if len(calls) == 2:
        assert calls[1][1] == "http://127.0.0.1:8088/custom/api/v2/chat/completions"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["my_local_llm", "elevenlabs_proxy_llm", "telnyx_proxy_llm", "groq_proxy_llm"])
async def test_explicit_openai_type_wins_over_name_and_realtime_leftovers(monkeypatch, name):
    calls = mock_http(monkeypatch)
    result = await run_api(monkeypatch, {
        "type": "openai", "chat_base_url": "http://192.168.10.10:8080/v1", "api_key": "synthetic-key",
        "realtime_base_url": "wss://api.openai.com/v1/realtime", "agent_id": "leftover",
    }, name=name)
    assert result["success"]
    assert calls[0][1] == "http://192.168.10.10:8080/v1/models"


@pytest.mark.asyncio
@pytest.mark.parametrize("role,resource", [("stt", "transcriptions"), ("tts", "speech")])
@pytest.mark.parametrize("saved", [False, True])
async def test_speech_uses_resource_endpoint_and_reports_limited_validation(monkeypatch, role, resource, saved):
    calls = mock_http(monkeypatch, httpx.Response(405))
    cfg = {"type": "openai", "capabilities": [role], f"{role}_base_url": f"http://127.0.0.1:8080/custom/audio/{resource}", "api_key": "synthetic-key"}
    result = await run_api(monkeypatch, cfg, saved=saved, name=f"custom_{role}")
    assert result["validation_level"] == "reachability"
    assert "not verified" in result["message"]
    assert calls[0][1] == cfg[f"{role}_base_url"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_chat_url_wins_over_legacy_and_official_port_path_survive(monkeypatch):
    calls = mock_http(monkeypatch)
    await run_api(monkeypatch, {"type": "openai", "chat_base_url": "https://api.openai.com:8443/private/v2", "base_url": "http://127.0.0.1:9999/unused", "api_key": "synthetic-key"})
    assert calls[0][1] == "https://api.openai.com:8443/private/v2/models"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["openai_llm", "groq_llm", "telnyx_llm", "minimax_llm"])
async def test_exact_legacy_keys_remain_supported(monkeypatch, name):
    calls = mock_http(monkeypatch)
    result = await run_api(monkeypatch, {"chat_base_url": "http://127.0.0.1:8080/v1", "api_key": "synthetic-key"}, name=name)
    assert result["success"]
    assert calls[0][1] == "http://127.0.0.1:8080/v1/models"


@pytest.mark.asyncio
async def test_legacy_groq_without_type_or_url_uses_groq_with_scoped_key(monkeypatch):
    calls = mock_http(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-groq-key")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-openai-key")
    result = await run_api(monkeypatch, {}, name="groq_llm")
    assert result["success"]
    assert calls == [("GET", "https://api.groq.com/openai/v1/models", {"headers": {"Authorization": "Bearer synthetic-groq-key"}})]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["chat_base_url", "base_url"])
async def test_legacy_groq_keeps_explicit_url(monkeypatch, field):
    calls = mock_http(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-groq-key")
    await run_api(monkeypatch, {field: "http://127.0.0.1:8080/custom/v2"}, name="groq_llm")
    assert calls == [("GET", "http://127.0.0.1:8080/custom/v2/models", {"headers": {"Authorization": "Bearer synthetic-groq-key"}})]


@pytest.mark.asyncio
async def test_legacy_groq_name_preserves_explicit_openai_endpoint(monkeypatch):
    calls = mock_http(monkeypatch)
    await run_api(monkeypatch, {"type": "openai", "chat_base_url": "https://api.openai.com/v1", "api_key": "explicit-key"}, name="groq_llm")
    assert calls[0][1] == "https://api.openai.com/v1/models"


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("source", ["legacy", "env", "inline", "file"])
async def test_explicit_openai_groq_without_endpoint_rejects_before_sending_key(monkeypatch, tmp_path, saved, source):
    calls = mock_http(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-groq-key")
    cfg = {"type": "openai", "capabilities": ["llm"]}
    if source == "env":
        cfg["api_key_env"] = "GROQ_API_KEY"
    elif source == "inline":
        cfg["api_key"] = "synthetic-groq-key"
    elif source == "file":
        path = tmp_path / "key"
        path.write_text("synthetic-groq-key")
        cfg["api_key_file"] = str(path)
    if saved:
        with pytest.raises(HTTPException, match="requires an explicit"):
            await run_api(monkeypatch, cfg, saved=True, name="groq_llm")
    else:
        result = await run_api(monkeypatch, cfg, name="groq_llm")
        assert not result["success"] and "requires an explicit" in result["message"]
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("field", ["chat_base_url", "base_url"])
async def test_explicit_openai_groq_preserves_configured_endpoint(monkeypatch, saved, field):
    calls = mock_http(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-groq-key")
    result = await run_api(monkeypatch, {"type": "openai", field: "http://127.0.0.1:8080/custom/v2"}, saved=saved, name="groq_llm")
    assert result.get("success", result.get("status") == "success")
    assert calls == [("GET", "http://127.0.0.1:8080/custom/v2/models", {"headers": {"Authorization": "Bearer synthetic-groq-key"}})]


@pytest.mark.parametrize("reference,expected", [
    ("${CUSTOM_API_KEY}", ""), ("${CUSTOM_API_KEY:-fallback}", "fallback"),
    ("${CUSTOM_API_KEY:=fallback}", "fallback"), ("${CUSTOM_API_KEY:-}", ""),
    ("${CUSTOM_API_KEY:-a:b=c}", "a:b=c"),
])
@pytest.mark.parametrize("value", ["", "fresh-key"])
def test_inline_key_references_preserve_environment_and_default_precedence(reference, expected, value):
    assert config_api._modular_validation_key("custom_llm", {"api_key": reference}, lambda _name: value) == (value or expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("operator", [":-", ":="])
@pytest.mark.parametrize("configured", [False, True])
async def test_both_apis_resolve_key_reference_defaults_consistently(monkeypatch, tmp_path, saved, operator, configured):
    calls = mock_http(monkeypatch)
    if configured:
        (tmp_path / ".env").write_text("CUSTOM_API_KEY=fresh-key\n")
    result = await run_api(monkeypatch, {
        "type": "openai", "chat_base_url": "http://127.0.0.1:8080/v1",
        "api_key": "${CUSTOM_API_KEY" + operator + "not-needed}",
    }, saved=saved)
    assert result.get("success", result.get("status") == "success")
    assert calls[0][2]["headers"] == ({"Authorization": "Bearer fresh-key"} if configured else {})
    assert result["validation_level"] == ("authentication" if configured else "connectivity")


@pytest.mark.parametrize("reference", [
    "${1INVALID}", "${NON_ASCII_é}", "${KEY:invalid}", "${KEY:-nested}extra}",
    "prefix${KEY}", "${{A:-" + "${{A:-|" * 10000,
])
def test_malformed_key_references_remain_literal_without_lookup(reference):
    def no_lookup(_name):
        pytest.fail("Malformed key references must not look up environment variables")
    assert config_api._modular_validation_key("custom_llm", {"api_key": reference}, no_lookup) == reference


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
async def test_diagnostic_logs_are_single_line_bounded_and_secret_safe(monkeypatch, caplog, saved):
    mock_http(monkeypatch)
    name = "synthetic-key_llm" if saved else "synthetic-key\r\nFORGED\n" + "x" * 1000 + "_llm"
    with caplog.at_level(logging.INFO, logger=validation.__name__):
        await run_api(monkeypatch, {"type": "openai", "chat_base_url": "http://127.0.0.1:8080/private-path", "api_key": "synthetic-key"}, saved=saved, name=name)
    records = [r for r in caplog.records if r.name == validation.__name__]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "\r" not in message and "\n" not in message
    assert "synthetic-key" not in message and "private-path" not in message
    assert len(message.split("provider=", 1)[1].split(" kind=", 1)[0]) <= 64


@pytest.mark.asyncio
async def test_saved_verification_rejects_log_injection_in_provider_key(monkeypatch):
    calls = mock_http(monkeypatch)
    with pytest.raises(HTTPException, match="Provider key may only contain"):
        await run_api(monkeypatch, {"type": "openai", "api_key": "synthetic-key"}, saved=True, name="custom\r\nFORGED_llm")
    assert calls == []


@pytest.mark.asyncio
async def test_undefined_explicit_env_does_not_fall_back_to_other_credentials(monkeypatch):
    calls = mock_http(monkeypatch)
    monkeypatch.setenv("CUSTOM_API_KEY", "legacy-key")
    result = await run_api(monkeypatch, {"type": "openai", "chat_base_url": "http://127.0.0.1:8080/v1", "api_key_env": "ABSENT_TEST_688_KEY", "api_key": "inline-key"})
    assert not result["success"]
    assert calls == []


def test_runtime_log_config_suppresses_full_http_request_urls():
    cfg = yaml.safe_load((Path(__file__).parents[1] / "uvicorn_log_config.yaml").read_text())
    assert cfg["loggers"]["httpx"]["level"] == "WARNING"
    assert cfg["loggers"]["httpcore"]["level"] == "WARNING"


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("source", ["file", "env", "inline", "legacy"])
async def test_credential_sources_match_scoped_runtime(monkeypatch, tmp_path, saved, source):
    calls = mock_http(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-openai-key")
    monkeypatch.setenv("CUSTOM_API_KEY", "legacy-key")
    cfg = {"type": "openai", "chat_base_url": "http://127.0.0.1:8080/v1"}
    expected = "legacy-key"
    if source == "file":
        path = tmp_path / "key"
        path.write_text("file-key")
        cfg.update(api_key_file=str(path), api_key_env="CUSTOM_API_KEY", api_key="inline-key")
        expected = "file-key"
    elif source == "env":
        (tmp_path / ".env").write_text("CUSTOM_API_KEY=fresh-env-key\n")
        cfg.update(api_key_env="CUSTOM_API_KEY", api_key="inline-key")
        expected = "fresh-env-key"
    elif source == "inline":
        cfg["api_key"] = "inline-key"
        expected = "inline-key"
    await run_api(monkeypatch, cfg, saved=saved)
    assert calls[0][2]["headers"] == {"Authorization": f"Bearer {expected}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
async def test_custom_provider_does_not_inherit_unrelated_openai_key(monkeypatch, saved):
    calls = mock_http(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-openai-key")
    cfg = {"type": "openai", "chat_base_url": "http://127.0.0.1:8080/v1"}
    if saved:
        with pytest.raises(HTTPException, match="not configured"):
            await run_api(monkeypatch, cfg, saved=True)
    else:
        assert not (await run_api(monkeypatch, cfg))["success"]
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
async def test_no_auth_probes_custom_endpoint_without_bearer(monkeypatch, saved):
    calls = mock_http(monkeypatch)
    result = await run_api(monkeypatch, {"type": "openai", "chat_base_url": "http://127.0.0.1:8080/v1", "api_key": "not-needed"}, saved=saved)
    assert result["validation_level"] == "connectivity"
    assert calls[0][2]["headers"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "not-a-url", "file:///tmp/key", "https://user:pass@example.com/v1", "https://api.openai.com/v1?key=secret",
    "https://api.openai.com/v1#fragment", "http://127.0.0.1:0/v1", "http://127.0.0.1:99999/v1",
    "http://169.254.169.254/v1", "http://[::ffff:169.254.169.254]/v1", "http://[fe80::1]/v1",
    "http://0.0.0.0/v1", "http://224.0.0.1/v1", "http://8.8.8.8/v1", "https://metadata.google.internal/v1",
    "https://api.openai.com\\@127.0.0.1/v1", "https://api.openai.com/\nfoo",
])
@pytest.mark.parametrize("saved", [False, True])
async def test_rejected_urls_make_zero_requests_even_with_no_auth(monkeypatch, url, saved):
    calls = mock_http(monkeypatch)
    cfg = {"type": "openai", "chat_base_url": url, "api_key": "not-needed"}
    if saved:
        with pytest.raises(HTTPException):
            await run_api(monkeypatch, cfg, saved=True)
    else:
        assert not (await run_api(monkeypatch, cfg))["success"]
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("addresses,success", [(["192.168.10.10"], True), (["127.0.0.1", "169.254.169.254"], False), (["8.8.8.8"], False)])
async def test_custom_dns_checks_all_addresses_before_sending_key(monkeypatch, addresses, success):
    calls = mock_http(monkeypatch)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 8080)) for address in addresses])
    result = await run_api(monkeypatch, {"type": "openai", "chat_base_url": "http://host.docker.internal:8080/v1", "api_key": "synthetic-key"})
    assert result["success"] is success
    assert len(calls) == int(success)


@pytest.mark.asyncio
async def test_custom_public_https_keeps_nonstandard_deutschlandgpt_path(monkeypatch):
    calls = mock_http(monkeypatch)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    result = await run_api(monkeypatch, {"type": "openai", "chat_base_url": "https://apiv2.deutschlandgpt.de/platform-api/api/v2", "api_key": "synthetic-key"})
    assert result["success"]
    assert calls[0][1] == "https://apiv2.deutschlandgpt.de/platform-api/api/v2/models"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [httpx.Response(301, headers={"Location": "https://api.openai.com/v1/models"}), httpx.Response(401), httpx.Response(403), httpx.Response(404), httpx.Response(429), httpx.Response(500), httpx.Response(200, text="secret response body"), httpx.Response(200, json={"data": {}})])
async def test_failure_does_not_fallback_or_expose_response_body(monkeypatch, caplog, response):
    calls = mock_http(monkeypatch, response)
    with caplog.at_level(logging.INFO, logger=validation.__name__):
        result = await run_api(monkeypatch, {"type": "openai", "chat_base_url": "http://127.0.0.1:8080/private/synthetic-key", "api_key": "synthetic-key"})
    assert not result["success"]
    assert len(calls) == 1
    assert "synthetic-key" not in str(result) + caplog.text
    assert "secret response body" not in str(result) + caplog.text
    assert "status=" in caplog.text and "elapsed_ms=" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectTimeout("secret exception"), httpx.ConnectError("secret exception")])
async def test_transport_failure_is_bounded_and_secret_safe(monkeypatch, caplog, error):
    calls = mock_http(monkeypatch, error=error)
    with caplog.at_level(logging.INFO, logger=validation.__name__):
        result = await run_api(monkeypatch, {"type": "openai", "chat_base_url": "http://127.0.0.1:8080/v1", "api_key": "synthetic-key"})
    assert not result["success"]
    assert len(calls) == 1
    assert "secret exception" not in str(result) + caplog.text
    assert "synthetic-key" not in caplog.text


@pytest.mark.asyncio
async def test_real_http_transport_preserves_resource_and_bearer(monkeypatch):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization")))
            if self.path == "/redirect/v1/models":
                self.send_response(302)
                self.send_header("Location", "/unintended/v1/models")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps({"data": [{"id": "synthetic-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for saved in (False, True):
            result = await run_api(monkeypatch, {"type": "openai", "chat_base_url": f"http://127.0.0.1:{server.server_port}/custom/api/v2", "api_key": "synthetic-key"}, saved=saved)
            assert result["validation_level"] == "authentication"
        assert calls == [("/custom/api/v2/models", "Bearer synthetic-key")] * 2
        result = await run_api(monkeypatch, {"type": "openai", "chat_base_url": f"http://127.0.0.1:{server.server_port}/redirect/v1", "api_key": "synthetic-key"})
        assert result["success"] is False
        assert "redirects are disabled" in result["message"]
        assert calls[-1][0] == "/redirect/v1/models"
        assert not any(path.startswith("/unintended") for path, _ in calls)
        result = await run_api(monkeypatch, {"type": "openai", "chat_base_url": f"http://127.0.0.1:{server.server_port}/noauth/v1", "api_key": "not-needed"})
        assert result["success"]
        assert calls[-1] == ("/noauth/v1/models", None)
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)
