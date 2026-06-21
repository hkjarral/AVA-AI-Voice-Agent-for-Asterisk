#!/usr/bin/env python3
"""
Fail if a non-placeholder secret value is committed to a TRACKED file.

Defense-in-depth guard (CRIT-2 / S2): scans every file tracked by git for
assignments of known secret keys (``SMTP_PASSWORD``, ``JWT_SECRET``, anything
ending in ``_API_KEY`` / ``_TOKEN`` / ``_SECRET`` / ``_PASSWORD``) and for
obvious provider key formats (OpenAI ``sk-...``, AWS ``AKIA...``), and flags
any whose value looks like a REAL secret rather than a placeholder.

Only tracked files are scanned (``git ls-files``) — gitignored files such as
the golden ``.env`` are correctly out of scope. The matcher is deliberately
conservative: it prefers missing an exotic edge case over false-positiving and
red-lighting CI. Empty values, ``${ENV...}`` references, documented
placeholders (``changeme``, ``your-...-here``, ``<...>``, ``example``, etc.),
and test fixtures are all treated as allowed.

Usage:
    python scripts/check_no_committed_secrets.py

Exit codes:
    0 — clean (no real secrets in tracked files)
    1 — one or more violations (file:line printed to stdout)
    2 — script error (not in a git repo, etc.)

Stdlib only — designed to run both locally and in CI (GitHub Actions).
"""

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories never worth scanning (vendored / build output / VCS metadata).
SKIP_DIR_PARTS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    "htmlcov",
}

# Path suffixes/segments whose secret-shaped strings are fixtures or prose,
# not real credentials. Conservative: avoids the unavoidable fake keys that
# tests and docs carry by design (e.g. `OPENAI_API_KEY=sk-secret`).
SKIP_FILE_SUFFIXES = (".md", ".rst", ".txt")


def _is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    if "tests" in parts or "test" in parts or "fixtures" in parts:
        return True
    if name.startswith("test_") or name.endswith("_test.py") or name.endswith("_test.go"):
        return True
    return False


# Secret key-name patterns. Matched against the identifier on the LHS of an
# assignment (``KEY = value`` / ``KEY: value`` / ``"KEY": "value"``).
SECRET_KEY_RE = re.compile(
    r"""(?ix)
    \b(
        SMTP_PASSWORD
      | JWT_SECRET
      | [A-Z0-9]+_API_KEY
      | [A-Z0-9]+_TOKEN
      | [A-Z0-9]+_SECRET
      | [A-Z0-9]+_PASSWORD
      | API_KEY
      | SECRET_KEY
    )\b
    """
)

# Assignment of a secret key to a value. Captures the raw value (quoted or not)
# up to a comment / quote / end of line. The (?P<q>) group is non-empty only
# when the value is a quoted string literal — bare values (variables, calls,
# dotenv lines) leave it empty.
ASSIGN_RE = re.compile(
    r"""(?ix)
    \b
    (?P<key>
        SMTP_PASSWORD | JWT_SECRET
      | [A-Z0-9]+_API_KEY | [A-Z0-9]+_TOKEN | [A-Z0-9]+_SECRET | [A-Z0-9]+_PASSWORD
      | API_KEY | SECRET_KEY
    )
    ["']?            # optional closing quote of a quoted key ("API_KEY":)
    \s*[=:]\s*
    (?P<value>
        (?P<q>["'])[^"']*(?P=q)   # quoted string literal
      | [^\s#,;)]+                # bare value (stop at whitespace/comment/delim)
    )
    """
)

# Config/dotenv file extensions where a BARE value is a real literal (not code).
CONFIG_SUFFIXES = (
    ".env",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".conf",
    ".toml",
    ".properties",
)

# OpenAI-style key: sk- followed by a long run of key chars (anchored so the
# "-sk-" inside "asterisk-..." does not match — requires a token boundary and
# 20+ trailing characters, far longer than any product-name fragment).
OPENAI_KEY_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")
# AWS access key id.
AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")

# Tokens in a value that mark it as a placeholder, not a real secret.
PLACEHOLDER_MARKERS = (
    "changeme",
    "change-me",
    "change_me",
    "your-",
    "your_",
    "yourkey",
    "youremail",
    "-here",
    "placeholder",
    "example",
    "dummy",
    "sample",
    "redacted",
    "todo",
    "fixme",
    "xxx",
    "notarealkey",
    "not-a-real",
    "not-needed",
    "fake",
    "test",
    "secret",  # short literal "secret"/"sk-secret"/"my-secret" used in docs/fixtures
)

# Numeric-suffixed keys that collide with the secret patterns but are config
# integers, e.g. `max_tokens`, `max_output_tokens`. The value being numeric is
# the real tell; handled in is_placeholder_value().


def is_placeholder_value(value: str) -> bool:
    """True if ``value`` is a placeholder / non-secret, False if it looks real."""
    v = value.strip().strip("\"'").strip()

    if not v:
        return True
    # Pure numbers (max_tokens: 200) and booleans.
    if v.isdigit() or v.lower() in {"true", "false", "null", "none"}:
        return True
    # Env-var references / shell interpolation: ${VAR}, ${VAR:-}, $VAR, %VAR%.
    if v.startswith("${") or v.startswith("$(") or (v.startswith("$") and v[1:].isidentifier()):
        return True
    if v.startswith("%") and v.endswith("%"):
        return True
    # Angle-bracket placeholders: <your-key>, <token>.
    if v.startswith("<") and v.endswith(">"):
        return True
    # Template / interpolation markers from any config language.
    if "{{" in v or "${" in v or "{%" in v:
        return True
    # Known placeholder words anywhere in the (lowercased) value.
    low = v.lower()
    if any(marker in low for marker in PLACEHOLDER_MARKERS):
        return True
    # Short values are almost never real high-entropy secrets; a real API key /
    # JWT secret is long. Keep this conservative (prefer false negatives).
    if len(v) < 20:
        return True
    return False


def looks_like_real_secret(value: str) -> bool:
    """A value that is NOT a placeholder AND matches a real key format."""
    if is_placeholder_value(value):
        return False
    v = value.strip().strip("\"'").strip()
    # Long high-entropy-ish run of key characters (>= 20, already enforced) that
    # isn't an obvious path/URL/identifier with spaces.
    if " " in v or "/" in v or v.startswith("http"):
        return False
    return True


def scan_line(line: str, is_config: bool = False):
    """Return a violation reason string for ``line``, or None.

    ``is_config`` is True for dotenv/yaml/ini-style files, where a BARE value
    after ``=`` / ``:`` is a real literal. In code files a bare value is almost
    always a variable / function call (``api_key = os.getenv(...)``), so only a
    QUOTED string literal is treated as a candidate secret there.
    """
    # Provider key formats first (catch even outside KEY=VALUE form). Skip
    # documented examples (e.g. AKIAIOSFODNN7EXAMPLE) by running the same
    # placeholder/allowlist filter on the matched token — the guard must still
    # catch real-looking keys.
    _m = OPENAI_KEY_RE.search(line)
    if _m and not is_placeholder_value(_m.group(0)):
        return "OpenAI-style key (sk-...)"
    _m = AWS_KEY_RE.search(line)
    if _m and not is_placeholder_value(_m.group(0)):
        return "AWS access key id (AKIA...)"

    for m in ASSIGN_RE.finditer(line):
        key = m.group("key")
        # Skip numeric-token config keys like max_tokens, max_output_tokens.
        if key.upper().endswith("_TOKEN") and key.lower().split("_")[0] in {
            "max",
            "output",
            "input",
            "response",
            "prompt",
            "completion",
            "total",
            "n",
        }:
            continue
        value = m.group("value")
        is_quoted = bool(m.group("q"))
        # In code, only a quoted string literal can be a hardcoded secret; a
        # bare RHS is a variable / expression, not a credential.
        if not is_quoted and not is_config:
            continue
        if looks_like_real_secret(value):
            return f"{key} assigned a non-placeholder value"
    return None


def iter_tracked_files():
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"FATAL: could not list tracked files: {e}", file=sys.stderr)
        sys.exit(2)
    for rel in out.stdout.split("\0"):
        if rel:
            yield rel


def scan_repo():
    """Scan all tracked files; return a list of (file, lineno, reason)."""
    violations = []
    for rel in iter_tracked_files():
        parts = rel.split("/")
        if any(p in SKIP_DIR_PARTS for p in parts):
            continue
        if rel.endswith(SKIP_FILE_SUFFIXES):
            continue
        if _is_test_path(rel):
            continue
        path = REPO_ROOT / rel
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw:  # binary
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        is_config = rel.endswith(CONFIG_SUFFIXES) or ".env" in parts[-1]
        for i, line in enumerate(text.splitlines(), start=1):
            reason = scan_line(line, is_config=is_config)
            if reason:
                violations.append((rel, i, reason))
    return violations


def main() -> int:
    violations = scan_repo()
    if violations:
        print("Committed-secrets guard FAILED — possible real secrets in tracked files:\n")
        for rel, lineno, reason in violations:
            print(f"  {rel}:{lineno}: {reason}")
        print(
            "\nIf this is a placeholder/example, adjust it to an env reference "
            "(${VAR}) or a clearly-fake value. If it is a real secret, rotate it "
            "and remove it from version control."
        )
        return 1
    print("Committed-secrets guard passed — no real secrets in tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
