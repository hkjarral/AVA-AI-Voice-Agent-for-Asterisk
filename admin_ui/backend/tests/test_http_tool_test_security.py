import os

import pytest
from fastapi import HTTPException

import api.tools as tools_module
from api.tools import _validate_http_tool_test_target


def test_validate_http_tool_test_target_blocks_localhost_by_default(monkeypatch):
    monkeypatch.delenv("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE", raising=False)
    monkeypatch.delenv("AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS", raising=False)
    monkeypatch.setattr(tools_module, "_dotenv_value", lambda name: None)

    with pytest.raises(HTTPException) as exc:
        _validate_http_tool_test_target("http://127.0.0.1:8080/test")
    assert exc.value.status_code == 403


def test_validate_http_tool_test_target_allows_localhost_with_override(monkeypatch):
    monkeypatch.setenv("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE", "1")
    monkeypatch.delenv("AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS", raising=False)
    monkeypatch.setattr(tools_module, "_dotenv_value", lambda name: None)

    _validate_http_tool_test_target("http://127.0.0.1:8080/test")


def test_validate_http_tool_test_target_rejects_non_http_scheme(monkeypatch):
    monkeypatch.setenv("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE", "1")
    monkeypatch.setattr(tools_module, "_dotenv_value", lambda name: None)
    with pytest.raises(HTTPException) as exc:
        _validate_http_tool_test_target("file:///etc/passwd")
    assert exc.value.status_code == 400


def test_validate_http_tool_test_target_rejects_embedded_credentials(monkeypatch):
    monkeypatch.setenv("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE", "1")
    monkeypatch.setattr(tools_module, "_dotenv_value", lambda name: None)
    with pytest.raises(HTTPException) as exc:
        _validate_http_tool_test_target("http://user:pass@example.com/test")
    assert exc.value.status_code == 400


# Regression tests for #370: Admin UI Environment-page edits to .env must take
# effect without an admin_ui container restart. The HTTP tool test guard now
# prefers `.env` over `os.environ`.


def test_validate_target_reads_dotenv_override_for_allow_private(monkeypatch):
    """`.env` value of AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE wins over a stale
    `os.environ` value (which would have been frozen at process start)."""
    # os.environ has the stale 'block' value; .env has the new 'allow' value.
    monkeypatch.delenv("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE", raising=False)
    monkeypatch.delenv("AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS", raising=False)
    monkeypatch.setattr(
        tools_module,
        "_dotenv_value",
        lambda name: "1" if name == "AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE" else None,
    )

    # Should NOT raise — .env override allows the private target.
    _validate_http_tool_test_target("http://127.0.0.1:8080/test")


def test_validate_target_reads_dotenv_override_for_allow_hosts(monkeypatch):
    """`.env` value of AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS is honored at the
    hostname-allowlist check (literal-IP path)."""
    monkeypatch.delenv("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE", raising=False)
    monkeypatch.delenv("AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS", raising=False)
    monkeypatch.setattr(
        tools_module,
        "_dotenv_value",
        lambda name: "127.0.0.1,::1" if name == "AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS" else None,
    )

    _validate_http_tool_test_target("http://127.0.0.1:8080/test")


def test_dotenv_precedence_over_os_environ(monkeypatch):
    """When both `.env` and `os.environ` have a value, `.env` wins. This is
    intentional — Admin UI writes to `.env` and users expect those edits to
    take effect immediately even though `os.environ` was frozen at admin_ui
    container start with a stale value."""
    monkeypatch.setenv("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE", "")  # stale: empty/disabled
    monkeypatch.delenv("AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS", raising=False)
    monkeypatch.setattr(
        tools_module,
        "_dotenv_value",
        lambda name: "1" if name == "AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE" else None,
    )

    # .env says "1" → allow private. Should NOT raise.
    _validate_http_tool_test_target("http://127.0.0.1:8080/test")


def test_admin_ui_env_key_recognizes_aava_http_tool_test_prefix():
    """Admin UI Environment-page apply/restart UX should recognize
    AAVA_HTTP_TOOL_TEST_* keys as admin_ui-impacting (#370)."""
    from api.config import _admin_ui_env_key

    assert _admin_ui_env_key("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE") is True
    assert _admin_ui_env_key("AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS") is True
    assert _admin_ui_env_key("AAVA_HTTP_TOOL_TEST_FOLLOW_REDIRECTS") is True
    # Unrelated keys still excluded.
    assert _admin_ui_env_key("AAVA_LOG_LEVEL") is False
    assert _admin_ui_env_key("OPENAI_API_KEY") is False

