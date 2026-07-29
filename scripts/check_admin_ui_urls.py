#!/usr/bin/env python3
"""Validate external URLs embedded in the Admin UI and its backend API.

The model catalog has a dedicated checker because model artifacts require a
strict 2xx/redirect result. This checker covers the other URLs users see or
depend on in the Admin UI: documentation, API-key consoles, provider API
bases, HTTPS validation endpoints, and realtime WebSocket routes.

No credentials are used. Authenticated API responses such as 401 and 403 are
treated as proof that the endpoint exists. WebSocket URLs receive a standard
unauthenticated upgrade handshake. HEAD-hostile sites use a one-byte ranged GET
fallback, and transient network/5xx failures are retried with exponential
backoff, so the checker avoids both full downloads and one-off CI failures.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import ipaddress
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOTS = (
    REPO_ROOT / "admin_ui" / "frontend" / "src",
    REPO_ROOT / "admin_ui" / "backend" / "api",
)
SOURCE_SUFFIXES = {".ts", ".tsx", ".py"}
EXCLUDED_FILES = {"models_catalog.py"}

URL_RE = re.compile(r"(?:https|wss)://[^\s\"'`<>{}\\]+")
TRAILING_PUNCTUATION = ".,;:!?)]"
ALLOWED_SCHEMES = {"https", "wss"}
SUCCESS_CODES = {200, 201, 202, 203, 204, 206, 301, 302, 303, 307, 308}
AUTHENTICATED_CODES = {401, 403}
API_ROUTE_EXISTS_CODES = {400, 401, 403, 405, 422}
WEBSOCKET_ROUTE_EXISTS_CODES = {101, 401, 403, 426}
WEBSOCKET_BAD_REQUEST_ROUTES = {
    (
        "generativelanguage.googleapis.com",
        "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent",
    ),
}
TRANSIENT_CODES = {429, 500, 502, 503, 504}

API_HOSTS = {
    "api.anthropic.com",
    "api.deepgram.com",
    "api.eu.deepgram.com",
    "api.elevenlabs.io",
    "api.groq.com",
    "api.minimax.io",
    "api.openai.com",
    "api.telnyx.com",
    "api.x.ai",
    "generativelanguage.googleapis.com",
    "graph.microsoft.com",
    "openrouter.ai",
    "voice-generator.pages.dev",
}

# Base URLs are useful configuration values but often do not serve a resource
# themselves. Probe a stable unauthenticated route instead so a removed or
# renamed API is not mistaken for a valid base merely because its host exists.
API_PROBES = {
    "https://api.anthropic.com/v1": "https://api.anthropic.com/v1/models",
    "https://api.deepgram.com": "https://api.deepgram.com/v1/projects",
    "https://api.deepgram.com/v1": "https://api.deepgram.com/v1/projects",
    "https://api.eu.deepgram.com": "https://api.eu.deepgram.com/v1/listen",
    "https://api.elevenlabs.io/v1": "https://api.elevenlabs.io/v1/voices",
    "https://api.groq.com/openai/v1": "https://api.groq.com/openai/v1/models",
    "https://api.minimax.io/v1": "https://api.minimax.io/v1/chat/completions",
    "https://api.openai.com/v1": "https://api.openai.com/v1/models",
    "https://api.telnyx.com/v2/ai": "https://api.telnyx.com/v2/ai/chat/completions",
    "https://api.x.ai/v1": "https://api.x.ai/v1/models",
    "https://generativelanguage.googleapis.com/v1beta": (
        "https://generativelanguage.googleapis.com/v1beta/models"
    ),
    "https://graph.microsoft.com/v1.0": "https://graph.microsoft.com/v1.0/$metadata",
    "https://openrouter.ai/api/v1": "https://openrouter.ai/api/v1/models",
}

SKIPPED_HOSTS = {
    "api.example.com",
    "api.provider.com",
    "example.com",
    "localhost",
    "127.0.0.1",
}


@dataclass(frozen=True)
class UrlTarget:
    url: str
    probe_url: str
    category: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class UrlResult:
    target: UrlTarget
    ok: bool
    code: int
    detail: str


def _strip_url(url: str) -> str:
    if "..." in url:
        return url.rstrip(")]")
    return url.rstrip(TRAILING_PUNCTUATION)


def extract_urls_from_text(text: str) -> set[str]:
    """Extract literal HTTPS/WSS URLs while leaving templates for skip handling."""
    return {_strip_url(match.group(0)) for match in URL_RE.finditer(text)}


def _is_private_literal(hostname: str) -> bool:
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return False
    return not address.is_global


def skip_reason(url: str) -> str | None:
    """Return why a source literal is not a testable external link."""
    if not url or "$" in url or "..." in url or "&lt;" in url or "&gt;" in url:
        return "template or example"
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() not in ALLOWED_SCHEMES or not host:
        return "not a complete secure external URL"
    if parsed.username or parsed.password:
        return "embedded credentials are forbidden"
    if host in SKIPPED_HOSTS or host.endswith(".example.com"):
        return "example or local endpoint"
    if _is_private_literal(host):
        return "private or non-routable address"
    if parsed.query.endswith("="):
        return "incomplete credential-bearing template"
    if parsed.path.endswith(("/blob/main/", "/resolve/main/", "/convai/agents/")):
        return "incomplete path template"
    if host == "www.googleapis.com" and parsed.path.startswith("/auth/"):
        return "OAuth scope identifier, not a navigable link"
    return None


def classify_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() == "wss":
        return "websocket"
    host = (parsed.hostname or "").lower()
    if host in API_HOSTS or host.endswith(".speech.microsoft.com"):
        return "api"
    if host.endswith(".api.cognitive.microsoft.com"):
        return "api"
    return "ui"


def _without_fragment(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(fragment=""))


def probe_url_for(url: str) -> str:
    without_fragment = _without_fragment(url)
    parts = urllib.parse.urlsplit(without_fragment)
    if parts.scheme.lower() == "wss":
        return urllib.parse.urlunsplit(parts._replace(scheme="https"))
    normalized = without_fragment.rstrip("/")
    return API_PROBES.get(normalized, without_fragment)


def websocket_route_exists(target: UrlTarget, code: int) -> bool:
    if code in WEBSOCKET_ROUTE_EXISTS_CODES:
        return True
    if code != 400:
        return False

    parsed = urllib.parse.urlsplit(target.probe_url)
    route = ((parsed.hostname or "").lower(), parsed.path)
    return route in WEBSOCKET_BAD_REQUEST_ROUTES


def collect_targets() -> tuple[list[UrlTarget], dict[str, list[str]]]:
    found: dict[str, set[str]] = defaultdict(set)
    skipped: dict[str, list[str]] = defaultdict(list)
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*")):
            if (
                not path.is_file()
                or path.suffix not in SOURCE_SUFFIXES
                or path.name in EXCLUDED_FILES
            ):
                continue
            relative = str(path.relative_to(REPO_ROOT))
            for url in extract_urls_from_text(path.read_text(encoding="utf-8")):
                reason = skip_reason(url)
                if reason:
                    skipped[reason].append(f"{relative}: {url}")
                    continue
                found[url].add(relative)

    targets = [
        UrlTarget(
            url=url,
            probe_url=probe_url_for(url),
            category=classify_url(url),
            sources=tuple(sorted(sources)),
        )
        for url, sources in sorted(found.items())
    ]
    return targets, skipped


def _validate_redirect(url: str) -> None:
    reason = skip_reason(url)
    if reason:
        raise urllib.error.URLError(f"unsafe redirect: {reason}")


class HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect_url = urllib.parse.urljoin(req.full_url, newurl)
        _validate_redirect(redirect_url)
        return super().redirect_request(req, fp, code, msg, headers, redirect_url)


def _request(
    opener: urllib.request.OpenerDirector,
    url: str,
    method: str,
    timeout: int,
    *,
    websocket_handshake: bool = False,
):
    headers = {"User-Agent": "AAVA-admin-ui-url-check/1.0"}
    data = None
    if websocket_handshake:
        headers.update(
            {
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": base64.b64encode(
                    secrets.token_bytes(16)
                ).decode("ascii"),
            }
        )
    elif method == "GET":
        headers["Range"] = "bytes=0-0"
    elif method == "POST":
        headers["Content-Type"] = "application/json"
        data = b"{}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    response = opener.open(request, timeout=timeout)  # nosec B310: HTTPS validated.
    if method == "GET" and not websocket_handshake:
        response.read(1)
    return response


def _check_target_once(
    target: UrlTarget,
    opener: urllib.request.OpenerDirector,
    timeout: int,
) -> UrlResult:
    if target.category == "websocket":
        try:
            with _request(
                opener,
                target.probe_url,
                "GET",
                timeout,
                websocket_handshake=True,
            ) as response:
                return UrlResult(
                    target,
                    response.status == 101,
                    response.status,
                    "WebSocket upgrade handshake",
                )
        except urllib.error.HTTPError as error:
            if websocket_route_exists(target, error.code):
                return UrlResult(
                    target,
                    True,
                    error.code,
                    "WebSocket route exists; authentication or valid handshake required",
                )
            return UrlResult(target, False, error.code, str(error.reason or ""))
        except urllib.error.URLError as error:
            return UrlResult(target, False, 0, f"URLError: {error.reason}")

    try:
        with _request(opener, target.probe_url, "HEAD", timeout) as response:
            return UrlResult(target, response.status in SUCCESS_CODES, response.status, "HEAD")
    except urllib.error.HTTPError as error:
        if error.code in AUTHENTICATED_CODES:
            return UrlResult(target, True, error.code, "authentication required")
        # Many documentation sites and APIs reject HEAD. A ranged GET
        # distinguishes that behavior from an actually missing URL.
        if error.code in {400, 404, 405}:
            try:
                with _request(opener, target.probe_url, "GET", timeout) as response:
                    return UrlResult(
                        target,
                        response.status in SUCCESS_CODES,
                        response.status,
                        "ranged GET fallback",
                    )
            except urllib.error.HTTPError as get_error:
                if get_error.code in AUTHENTICATED_CODES | {405}:
                    return UrlResult(
                        target,
                        True,
                        get_error.code,
                        "endpoint exists; authentication or method required",
                    )
                if (
                    target.category == "api"
                    and get_error.code in API_ROUTE_EXISTS_CODES
                ):
                    return UrlResult(
                        target,
                        True,
                        get_error.code,
                        "API route exists; request requires auth or valid body",
                    )
                if target.category == "api" and get_error.code == 404:
                    try:
                        with _request(
                            opener, target.probe_url, "POST", timeout
                        ) as response:
                            return UrlResult(
                                target,
                                response.status in SUCCESS_CODES,
                                response.status,
                                "unauthenticated POST probe",
                            )
                    except urllib.error.HTTPError as post_error:
                        if post_error.code in API_ROUTE_EXISTS_CODES:
                            return UrlResult(
                                target,
                                True,
                                post_error.code,
                                "API route exists; request requires auth or valid body",
                            )
                        return UrlResult(
                            target,
                            False,
                            post_error.code,
                            str(post_error.reason or ""),
                        )
                    except urllib.error.URLError as post_error:
                        return UrlResult(
                            target,
                            False,
                            0,
                            f"URLError: {post_error.reason}",
                        )
                return UrlResult(
                    target, False, get_error.code, str(get_error.reason or "")
                )
            except urllib.error.URLError as get_error:
                return UrlResult(target, False, 0, f"URLError: {get_error.reason}")
        return UrlResult(target, False, error.code, str(error.reason or ""))
    except urllib.error.URLError as error:
        return UrlResult(target, False, 0, f"URLError: {error.reason}")


def check_target(
    target: UrlTarget,
    timeout: int = 20,
    max_retries: int = 3,
) -> UrlResult:
    opener = urllib.request.build_opener(HttpsOnlyRedirectHandler)
    delay = 1.0
    last_result = UrlResult(target, False, 0, "unknown failure")

    for attempt in range(max_retries):
        try:
            last_result = _check_target_once(target, opener, timeout)
        except Exception as error:  # Keep one unexpected site from aborting the run.
            last_result = UrlResult(
                target,
                False,
                0,
                f"{type(error).__name__}: {error}",
            )

        if last_result.ok:
            return last_result
        is_transient = (
            last_result.code == 0 or last_result.code in TRANSIENT_CODES
        )
        if not is_transient or attempt == max_retries - 1:
            return last_result

        time.sleep(delay)
        delay *= 2

    return last_result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--max-workers", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()

    targets, skipped = collect_targets()
    print(
        f"Checking {len(targets)} unique Admin UI URLs "
        f"({sum(t.category == 'api' for t in targets)} API, "
        f"{sum(t.category == 'websocket' for t in targets)} WebSocket, "
        f"{sum(t.category == 'ui' for t in targets)} UI/documentation).",
        file=sys.stderr,
    )
    if skipped:
        print(
            f"Skipped {sum(len(items) for items in skipped.values())} "
            "template/example/scope literals.",
            file=sys.stderr,
        )

    if args.list_only:
        for target in targets:
            print(f"{target.category:<4} {target.url} -> {target.probe_url}")
        return 0

    results: list[UrlResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(check_target, target, args.timeout): target
            for target in targets
        }
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            target = futures[future]
            try:
                results.append(future.result())
            except Exception as error:
                results.append(
                    UrlResult(target, False, 0, f"checker bug: {type(error).__name__}: {error}")
                )
            if index % 25 == 0:
                print(f"  … {index}/{len(targets)}", file=sys.stderr)

    failures = sorted(
        (result for result in results if not result.ok),
        key=lambda result: (result.target.category, result.target.url),
    )
    print(f"\n{len(results) - len(failures)}/{len(results)} URLs OK.", file=sys.stderr)
    if not failures:
        print("All Admin UI external URLs reachable.", file=sys.stderr)
        return 0

    print(f"\n{len(failures)} BROKEN URL(S):\n", file=sys.stderr)
    print(f"{'TYPE':<5} {'CODE':<5} {'URL'}")
    print("-" * 120)
    for result in failures:
        code = str(result.code) if result.code else "ERR"
        print(f"{result.target.category:<5} {code:<5} {result.target.url}")
        if result.target.probe_url != result.target.url:
            print(f"      probe: {result.target.probe_url}")
        print(f"      source: {', '.join(result.target.sources)}")
        print(f"      detail: {result.detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
