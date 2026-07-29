#!/usr/bin/env python3
"""
Verify every download URL in admin_ui/backend/api/models_catalog.py is reachable.

Walks the full catalog (STT + TTS + LLM), extracts all URL fields
(download_url, config_url, mirror_url, vocoder_url, plus Kokoro's
nested voice_files dict), HEADs each one, and reports failures.

Usage:
    python scripts/check_catalog_urls.py [--include-cloud] [--max-workers N]

Exit codes:
    0 — all URLs reachable
    1 — one or more URLs broken (table printed to stdout)
    2 — script error (catalog import failed, etc.)

Designed to run both locally and in CI (GitHub Actions). Uses stdlib only.
"""

import argparse
import concurrent.futures
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = REPO_ROOT / "admin_ui" / "backend" / "api"
sys.path.insert(0, str(CATALOG_DIR))

try:
    from models_catalog import get_full_catalog  # type: ignore
except ImportError as e:
    print(f"FATAL: could not import models_catalog: {e}", file=sys.stderr)
    sys.exit(2)

OK_CODES = {200, 301, 302, 307, 308}
URL_FIELDS = ("download_url", "config_url", "mirror_url", "vocoder_url", "tokens_url")
ALLOWED_SCHEMES = {"https"}  # No http://, file://, ftp://, custom schemes


def _validate_url_scheme(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise urllib.error.URLError(
            f"unsupported scheme '{parsed.scheme}' (only https allowed)"
        )
    return url


def _is_unsupported_scheme_error(error):
    return "unsupported scheme" in str(getattr(error, "reason", error))


class HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that leave the allowed URL schemes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect_url = urllib.parse.urljoin(req.full_url, newurl)
        _validate_url_scheme(redirect_url)
        return super().redirect_request(req, fp, code, msg, headers, redirect_url)


def safe_url(url):
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        parts._replace(path=urllib.parse.quote(parts.path, safe="/:@%"))
    )


def _parse_retry_after(value, default):
    """Retry-After can be either delta-seconds (int) or an HTTP-date.
    Be permissive — fall back to `default` if we can't parse it instead
    of crashing the whole worker (one bad header used to abort the run).
    """
    if not value:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        when = parsedate_to_datetime(value)
        delta = (when - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except Exception:
        return default


def check_url(url, timeout=20, max_retries=4):
    """Return (status_code, error_message). Code 0 means network failure."""
    # Reject anything that isn't HTTPS *before* handing it to urlopen,
    # which otherwise happily opens file://, ftp://, custom schemes, etc.
    # A broken or malicious catalog entry could otherwise probe the local
    # filesystem or internal services.
    try:
        _validate_url_scheme(url)
    except urllib.error.URLError as e:
        return 0, str(e.reason)
    req = urllib.request.Request(safe_url(url), method="HEAD",
                                 headers={"User-Agent": "AAVA-catalog-url-check/1.0"})
    opener = urllib.request.build_opener(HttpsOnlyRedirectHandler)
    delay = 2.0
    last_err = None
    for attempt in range(max_retries):
        try:
            # nosec B310: original and redirected schemes are validated as HTTPS-only.
            with opener.open(req, timeout=timeout) as r:  # noqa: S310
                return r.status, None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                wait = _parse_retry_after(e.headers.get("Retry-After"), delay)
                time.sleep(wait)
                delay = min(delay * 2, 30.0)
                last_err = "HTTP 429 (retried)"
                continue
            return e.code, str(e.reason or "")
        except urllib.error.URLError as e:
            last_err = f"URLError: {e.reason}"
            if _is_unsupported_scheme_error(e):
                return 0, last_err
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return 0, last_err
        except Exception as e:
            return 0, f"{type(e).__name__}: {e}"
    return 0, last_err or "unknown"


def collect_urls(catalog, include_cloud):
    """Yield (model_id, name, kind, field, url) for every URL in the catalog."""
    for kind, models in catalog.items():
        for m in models:
            if not include_cloud and m.get("requires_api_key"):
                continue
            mid = m.get("id", "?")
            name = m.get("name", "?")
            for field in URL_FIELDS:
                url = m.get(field)
                if isinstance(url, str) and url:
                    yield mid, name, kind, field, url
            # Kokoro nests voice files inside a dict — not stdlib-flat
            voice_files = m.get("voice_files")
            if isinstance(voice_files, dict):
                for voice_name, url in voice_files.items():
                    if isinstance(url, str) and url:
                        yield mid, name, kind, f"voice_files[{voice_name}]", url


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--include-cloud", action="store_true",
                    help="Also check cloud entries (most have download_url=None and are skipped anyway)")
    ap.add_argument("--max-workers", type=int, default=10,
                    help="Concurrent HEAD requests (default 10)")
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()

    catalog = get_full_catalog()
    targets = list(collect_urls(catalog, args.include_cloud))
    print(f"Checking {len(targets)} URLs across "
          f"{len(catalog['stt'])} STT, {len(catalog['tts'])} TTS, "
          f"{len(catalog['llm'])} LLM entries…", file=sys.stderr)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = {ex.submit(check_url, url, args.timeout): (mid, name, kind, field, url)
                   for mid, name, kind, field, url in targets}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            mid, name, kind, field, url = futures[fut]
            try:
                code, err = fut.result()
            except Exception as exc:
                # check_url should always return cleanly; if it raises (a
                # bug in our retry logic, an unparseable header, etc.),
                # record it as a failure for this URL but keep the run
                # going — losing one entry's status beats aborting the
                # whole CI check.
                code, err = 0, f"checker bug: {type(exc).__name__}: {exc}"
            ok = code in OK_CODES
            results.append((ok, code, err, mid, name, kind, field, url))
            if i % 25 == 0:
                print(f"  … {i}/{len(targets)}", file=sys.stderr)

    failures = [r for r in results if not r[0]]
    print(f"\n{len(results) - len(failures)}/{len(results)} URLs OK.",
          file=sys.stderr)

    if failures:
        print(f"\n{len(failures)} BROKEN URL(S):\n", file=sys.stderr)
        # Sorted by kind then id for readable output
        failures.sort(key=lambda r: (r[5], r[3], r[6]))
        print(f"{'KIND':<5} {'CODE':<5} {'MODEL_ID':<35} {'FIELD':<25} URL")
        print("-" * 140)
        for _, code, err, mid, _name, kind, field, url in failures:
            code_str = str(code) if code else "ERR"
            print(f"{kind:<5} {code_str:<5} {mid:<35} {field:<25} {url}")
            if err:
                print(f"      └─ {err}")
        sys.exit(1)

    print("All catalog URLs reachable.", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
