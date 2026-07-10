from __future__ import annotations

import urllib.error
from unittest.mock import patch

import pytest

from scripts.check_admin_ui_urls import (
    UrlTarget,
    _validate_redirect,
    check_target,
    classify_url,
    extract_urls_from_text,
    probe_url_for,
    skip_reason,
)


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "test response", None, None)


def test_extract_urls_strips_source_punctuation() -> None:
    text = "See https://example.org/docs, then https://example.org/api?x=1#part)."

    assert extract_urls_from_text(text) == {
        "https://example.org/docs",
        "https://example.org/api?x=1#part",
    }


@pytest.mark.parametrize(
    ("url", "reason_fragment"),
    [
        ("https://api.example.com/test", "example"),
        ("https://127.0.0.1/health", "local"),
        ("https://$", "template"),
        ("https://&lt;region&gt;.example.net/v1", "template"),
        ("https://www.googleapis.com/auth/calendar", "OAuth scope"),
        ("https://api.example.net/models?key=", "credential-bearing"),
        ("https://github.com/org/repo/blob/main/", "incomplete path"),
    ],
)
def test_skip_reason_filters_non_links(url: str, reason_fragment: str) -> None:
    assert reason_fragment in (skip_reason(url) or "")


def test_classification_and_stable_api_probes() -> None:
    assert classify_url("https://api.openai.com/v1") == "api"
    assert classify_url("https://platform.openai.com/docs") == "ui"
    assert probe_url_for("https://api.openai.com/v1/") == (
        "https://api.openai.com/v1/models"
    )
    assert probe_url_for("https://docs.example.org/page#section") == (
        "https://docs.example.org/page"
    )


@pytest.mark.parametrize("route_code", [400, 401, 403, 405, 422])
def test_api_post_probe_accepts_unauthenticated_route(route_code: int) -> None:
    target = UrlTarget(
        url="https://api.vendor.example/v1",
        probe_url="https://api.vendor.example/v1/chat/completions",
        category="api",
        sources=("example.tsx",),
    )
    errors = [
        _http_error(target.probe_url, 404),
        _http_error(target.probe_url, 404),
        _http_error(target.probe_url, route_code),
    ]

    with patch("scripts.check_admin_ui_urls._request", side_effect=errors):
        result = check_target(target, max_retries=1)

    assert result.ok is True
    assert result.code == route_code
    assert "API route exists" in result.detail


def test_ui_404_remains_a_failure() -> None:
    target = UrlTarget(
        url="https://docs.vendor.example/removed",
        probe_url="https://docs.vendor.example/removed",
        category="ui",
        sources=("example.tsx",),
    )
    errors = [
        _http_error(target.probe_url, 404),
        _http_error(target.probe_url, 404),
    ]

    with patch("scripts.check_admin_ui_urls._request", side_effect=errors):
        result = check_target(target, max_retries=1)

    assert result.ok is False
    assert result.code == 404


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.example.org/page",
        "https://127.0.0.1/private",
        "https://user:password@docs.example.org/page",
    ],
)
def test_redirects_must_remain_safe_public_https(url: str) -> None:
    with pytest.raises(urllib.error.URLError):
        _validate_redirect(url)
