"""Tests for the committed-secrets CI guard (scripts/check_no_committed_secrets.py).

Covers CRIT-2 / S2 defense-in-depth: the guard must flag real secret literals in
tracked files while leaving placeholders, env references, and code expressions alone.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_no_committed_secrets.py"

_spec = importlib.util.spec_from_file_location("check_no_committed_secrets", _SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


pytestmark = pytest.mark.unit


# (line, is_config) pairs that MUST be flagged as real secrets.
REAL_SECRETS = [
    ("OPENAI_API_KEY=sk-proj-abcd1234efgh5678ijkl9012mnop3456", True),
    ('api_key = "sk-proj-abcd1234efgh5678ijkl9012mnop3456"', False),
    ("JWT_SECRET=9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a", True),
    ("SMTP_PASSWORD=Tr0ub4dor3xKpQzL9vNmW2aa", True),
    # A real-looking AWS key (no placeholder marker) must still be caught.
    ('aws_key = "AKIA1234567890ABCDEF"', False),
]

# (line, is_config) pairs that MUST NOT be flagged (placeholders / code / env refs).
ALLOWED = [
    ("OPENAI_API_KEY=", True),
    ("OPENAI_API_KEY=${OPENAI_API_KEY}", True),
    ("api_key: ${GOOGLE_API_KEY}", True),
    ("auth_token: ${LOCAL_WS_AUTH_TOKEN:-}", True),
    ("api_key: not-needed", True),
    ("max_tokens: 200", True),
    ("max_response_output_tokens: 4096", True),
    ("JWT_SECRET=changeme", True),
    ("JWT_SECRET=your-secret-key-here", True),
    ("ASTERISK_ARI_PASSWORD=asterisk", True),
    ('api_key = os.getenv("RESEND_API_KEY")', False),
    ("api_key = self._auto_detect_credentials(options)", False),
    ('"hashed_password": get_password_hash(password),', False),
    ("api_key = config.api_key", False),
    # Provider-format detection must honor placeholder filtering: a documented
    # example like AWS's AKIAIOSFODNN7EXAMPLE must NOT trip the guard.
    ('aws_key = "AKIAIOSFODNN7EXAMPLE"', False),
    ("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE", True),
    ("OPENAI_API_KEY=sk-EXAMPLE-not-a-real-key-placeholder", True),
]


@pytest.mark.parametrize("line,is_config", REAL_SECRETS)
def test_flags_real_secret(line, is_config):
    assert guard.scan_line(line, is_config=is_config) is not None, line


@pytest.mark.parametrize("line,is_config", ALLOWED)
def test_allows_placeholder_or_code(line, is_config):
    assert guard.scan_line(line, is_config=is_config) is None, line


def test_env_reference_is_placeholder():
    assert guard.is_placeholder_value("${OPENAI_API_KEY}") is True
    assert guard.is_placeholder_value("${LOCAL_WS_AUTH_TOKEN:-}") is True


def test_real_secret_literal_not_placeholder():
    assert guard.is_placeholder_value("sk-proj-abcd1234efgh5678ijkl9012mnop3456") is False


def test_current_tree_is_clean():
    """The guard must exit 0 on the real repository (else it red-lights CI)."""
    assert guard.scan_repo() == []
