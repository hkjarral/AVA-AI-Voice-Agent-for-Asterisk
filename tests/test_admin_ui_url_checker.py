from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from scripts.check_admin_ui_urls import (
    UrlResult,
    UrlTarget,
    _validate_redirect,
    check_target,
    classify_url,
    extract_urls_from_text,
    probe_url_for,
    skip_reason,
    websocket_route_exists,
)


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "test response", None, None)


def _response(status: int) -> MagicMock:
    response = MagicMock()
    response.__enter__.return_value.status = status
    return response


def test_extract_urls_strips_source_punctuation() -> None:
    text = (
        "See https://example.org/docs, https://example.org/api?x=1#part), "
        "and wss://socket.example.org/v1/live."
    )

    assert extract_urls_from_text(text) == {
        "https://example.org/docs",
        "https://example.org/api?x=1#part",
        "wss://socket.example.org/v1/live",
    }


def test_extract_urls_preserves_ellipsis_for_template_filtering() -> None:
    url = "wss://generativelanguage.googleapis.com/ws/..."

    assert extract_urls_from_text(f"Placeholder: {url}.") == {f"{url}."}
    assert skip_reason(f"{url}.") == "template or example"


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
    assert classify_url("wss://api.openai.com/v1/realtime") == "websocket"
    assert probe_url_for("wss://api.openai.com/v1/realtime#section") == (
        "https://api.openai.com/v1/realtime"
    )


@pytest.mark.parametrize("route_code", [101, 401, 403, 426])
def test_websocket_probe_accepts_route_level_response(route_code: int) -> None:
    target = UrlTarget(
        url="wss://socket.vendor.example/v1/live",
        probe_url="https://socket.vendor.example/v1/live",
        category="websocket",
        sources=("example.tsx",),
    )

    with patch(
        "scripts.check_admin_ui_urls._request",
        side_effect=_http_error(target.probe_url, route_code),
    ):
        result = check_target(target, max_retries=1)

    assert result.ok is True
    assert result.code == route_code
    assert "WebSocket route exists" in result.detail


def test_google_websocket_probe_accepts_provider_specific_bad_request() -> None:
    target = UrlTarget(
        url=(
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
        ),
        probe_url=(
            "https://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
        ),
        category="websocket",
        sources=("GoogleLiveProviderForm.tsx",),
    )

    with patch(
        "scripts.check_admin_ui_urls._request",
        side_effect=_http_error(target.probe_url, 400),
    ):
        result = check_target(target, max_retries=1)

    assert result.ok is True
    assert websocket_route_exists(target, 400) is True


def test_websocket_bad_request_does_not_validate_a_misspelled_route() -> None:
    target = UrlTarget(
        url=(
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentTypo"
        ),
        probe_url=(
            "https://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentTypo"
        ),
        category="websocket",
        sources=("GoogleLiveProviderForm.tsx",),
    )

    with patch(
        "scripts.check_admin_ui_urls._request",
        side_effect=_http_error(target.probe_url, 400),
    ):
        result = check_target(target, max_retries=1)

    assert result.ok is False
    assert result.code == 400
    assert websocket_route_exists(target, 400) is False


def test_websocket_probe_accepts_upgrade() -> None:
    target = UrlTarget(
        url="wss://socket.vendor.example/v1/live",
        probe_url="https://socket.vendor.example/v1/live",
        category="websocket",
        sources=("example.tsx",),
    )

    with patch(
        "scripts.check_admin_ui_urls._request",
        return_value=_response(101),
    ):
        result = check_target(target, max_retries=1)

    assert result.ok is True
    assert result.code == 101


def test_websocket_404_remains_a_failure() -> None:
    target = UrlTarget(
        url="wss://socket.vendor.example/removed",
        probe_url="https://socket.vendor.example/removed",
        category="websocket",
        sources=("example.tsx",),
    )

    with patch(
        "scripts.check_admin_ui_urls._request",
        side_effect=_http_error(target.probe_url, 404),
    ):
        result = check_target(target, max_retries=1)

    assert result.ok is False
    assert result.code == 404


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


def test_transient_head_error_is_retried() -> None:
    target = UrlTarget(
        url="https://docs.vendor.example/page",
        probe_url="https://docs.vendor.example/page",
        category="ui",
        sources=("example.tsx",),
    )

    with (
        patch(
            "scripts.check_admin_ui_urls._request",
            side_effect=[_http_error(target.probe_url, 503), _response(200)],
        ) as request,
        patch("scripts.check_admin_ui_urls.time.sleep") as sleep,
    ):
        result = check_target(target, max_retries=2)

    assert result.ok is True
    assert request.call_count == 2
    sleep.assert_called_once_with(1.0)


def test_get_fallback_network_error_is_retried() -> None:
    target = UrlTarget(
        url="https://docs.vendor.example/page",
        probe_url="https://docs.vendor.example/page",
        category="ui",
        sources=("example.tsx",),
    )
    errors = [
        _http_error(target.probe_url, 404),
        urllib.error.URLError("temporary DNS failure"),
        _response(200),
    ]

    with (
        patch("scripts.check_admin_ui_urls._request", side_effect=errors) as request,
        patch("scripts.check_admin_ui_urls.time.sleep") as sleep,
    ):
        result = check_target(target, max_retries=2)

    assert result.ok is True
    assert request.call_count == 3
    sleep.assert_called_once_with(1.0)


def test_post_fallback_transient_error_is_retried() -> None:
    target = UrlTarget(
        url="https://api.vendor.example/v1",
        probe_url="https://api.vendor.example/v1/chat/completions",
        category="api",
        sources=("example.tsx",),
    )
    errors = [
        _http_error(target.probe_url, 404),
        _http_error(target.probe_url, 404),
        _http_error(target.probe_url, 503),
        _response(200),
    ]

    with (
        patch("scripts.check_admin_ui_urls._request", side_effect=errors) as request,
        patch("scripts.check_admin_ui_urls.time.sleep") as sleep,
    ):
        result = check_target(target, max_retries=2)

    assert result.ok is True
    assert request.call_count == 4
    sleep.assert_called_once_with(1.0)


def test_persistent_transient_error_stops_after_max_retries() -> None:
    target = UrlTarget(
        url="https://docs.vendor.example/page",
        probe_url="https://docs.vendor.example/page",
        category="ui",
        sources=("example.tsx",),
    )
    transient_failure = UrlResult(target, False, 503, "service unavailable")

    with (
        patch(
            "scripts.check_admin_ui_urls._check_target_once",
            return_value=transient_failure,
        ) as check_once,
        patch("scripts.check_admin_ui_urls.time.sleep") as sleep,
    ):
        result = check_target(target, max_retries=3)

    assert result is transient_failure
    assert check_once.call_count == 3
    assert [call.args for call in sleep.call_args_list] == [(1.0,), (2.0,)]


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
