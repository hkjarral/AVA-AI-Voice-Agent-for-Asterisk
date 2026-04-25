"""Tests for Google Calendar info/verify endpoint helpers.

Covers:
- Calendar key URL-path validation (rejects path traversal, weird chars,
  oversized values; accepts the common alphanumeric/underscore/hyphen shape).
- Service-account JSON metadata loader (rejects non-JSON, non-SA shapes,
  missing required fields; succeeds on valid SA dicts).

The full /verify endpoint is exercised end-to-end via the dev-server smoke
test (it requires real Google API access and is hard to mock cleanly here).
"""

import json
import os
import tempfile

import pytest
from fastapi import HTTPException

from api.config import (
    _validate_calendar_key_or_400,
    _load_sa_metadata,
    _calendar_filename_for_email,
    _resolve_calendar_secret_path,
)


# ─── Calendar key validation ─────────────────────────────────────────────────


class TestValidateCalendarKey:
    """Calendar keys appear in URL paths; we must reject anything path-shaped
    or otherwise sketchy. Allowed: [a-zA-Z0-9_-]{1,64}."""

    @pytest.mark.parametrize(
        "key",
        [
            "default",
            "work",
            "calendar_1",
            "work-calendar",
            "WORK",
            "x",  # 1-char minimum
            "a" * 64,  # 64-char maximum
            "a-b_c-d_e",
        ],
    )
    def test_valid_keys_pass(self, key: str):
        """Standard-shape keys round-trip unchanged."""
        assert _validate_calendar_key_or_400(key) == key

    @pytest.mark.parametrize(
        "key",
        [
            "",  # empty
            "a" * 65,  # over max
            "../etc/passwd",  # path traversal
            "/etc/passwd",
            "key/with/slashes",
            "key.with.dots",  # could be misread as path component
            "key with spaces",
            "key\twith\ttabs",
            "key\nwith\nnewlines",
            "key\x00with\x00nulls",
            "key:colon",
            "key;semi",
            "key%encoded",
            "k€y_unicode",
            "?wildcard",
            "*star",
        ],
    )
    def test_invalid_keys_raise_400(self, key: str):
        """Anything outside the safe charset must raise HTTP 400."""
        with pytest.raises(HTTPException) as exc:
            _validate_calendar_key_or_400(key)
        assert exc.value.status_code == 400
        # Detail should be structured for the UI to display
        assert isinstance(exc.value.detail, dict)
        assert exc.value.detail.get("error_code") == "invalid_calendar_key"

    def test_non_string_input_raises_400(self):
        """Defensive: non-string input is rejected (shouldn't happen via FastAPI but be safe)."""
        for bad in [None, 123, [], {}, b"bytes"]:
            with pytest.raises(HTTPException) as exc:
                _validate_calendar_key_or_400(bad)  # type: ignore[arg-type]
            assert exc.value.status_code == 400


# ─── Service-account metadata loader ─────────────────────────────────────────


class TestLoadSaMetadata:
    """The SA JSON loader is used by /info and /verify endpoints to surface
    client_email + client_id without requiring the operator to crack open
    the JSON file by hand. Any failure must raise HTTPException with a
    structured error_code so the UI can display a useful message."""

    def _write_sa_file(self, payload: dict) -> str:
        """Helper: write a temp file with the given JSON payload, return path."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(payload, tmp)
        tmp.close()
        return tmp.name

    def _valid_sa_payload(self) -> dict:
        """Return a structurally-valid SA JSON payload (synthetic, no real keys)."""
        return {
            "type": "service_account",
            "project_id": "synthetic-project",
            "private_key_id": "synthetic_key_id_12345",
            "private_key": "-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----\n",
            "client_email": "synthetic-sa@synthetic-project.iam.gserviceaccount.com",
            "client_id": "100000000000000000000",
        }

    def test_valid_sa_returns_metadata(self):
        """Valid SA JSON returns the four metadata fields."""
        payload = self._valid_sa_payload()
        path = self._write_sa_file(payload)
        try:
            meta = _load_sa_metadata(path)
        finally:
            os.unlink(path)
        assert meta["client_email"] == payload["client_email"]
        assert meta["client_id"] == payload["client_id"]
        assert meta["project_id"] == payload["project_id"]
        assert meta["private_key_id"] == payload["private_key_id"]

    def test_empty_path_raises_400(self):
        with pytest.raises(HTTPException) as exc:
            _load_sa_metadata("")
        assert exc.value.status_code == 400
        assert exc.value.detail["error_code"] == "missing_credentials_path"

    def test_nonexistent_path_raises_404(self):
        with pytest.raises(HTTPException) as exc:
            _load_sa_metadata("/nonexistent/synthetic/path.json")
        assert exc.value.status_code == 404
        assert exc.value.detail["error_code"] == "credentials_file_not_found"

    def test_directory_instead_of_file_raises_400(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(HTTPException) as exc:
                _load_sa_metadata(tmpdir)
            assert exc.value.status_code == 400
            assert exc.value.detail["error_code"] == "credentials_path_not_a_file"

    def test_invalid_json_raises_400(self):
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        tmp.write("this is not json {{{")
        tmp.close()
        try:
            with pytest.raises(HTTPException) as exc:
                _load_sa_metadata(tmp.name)
            assert exc.value.status_code == 400
            assert exc.value.detail["error_code"] == "credentials_not_json"
        finally:
            os.unlink(tmp.name)

    def test_non_service_account_json_raises_400(self):
        """A valid JSON file that's not a service account (e.g. user OAuth creds) is rejected."""
        payload = {"type": "authorized_user", "client_id": "x", "refresh_token": "y"}
        path = self._write_sa_file(payload)
        try:
            with pytest.raises(HTTPException) as exc:
                _load_sa_metadata(path)
            assert exc.value.status_code == 400
            assert exc.value.detail["error_code"] == "credentials_not_service_account"
        finally:
            os.unlink(path)

    def test_sa_missing_required_fields_raises_400(self):
        """SA JSON missing client_email / private_key / private_key_id is rejected."""
        payload = {"type": "service_account", "project_id": "x"}  # missing the three required
        path = self._write_sa_file(payload)
        try:
            with pytest.raises(HTTPException) as exc:
                _load_sa_metadata(path)
            assert exc.value.status_code == 400
            assert exc.value.detail["error_code"] == "credentials_missing_fields"
        finally:
            os.unlink(path)

    def test_array_payload_rejected_as_not_sa(self):
        """A JSON array (not a dict) must be rejected without crashing the loader."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump([1, 2, 3], tmp)
        tmp.close()
        try:
            with pytest.raises(HTTPException) as exc:
                _load_sa_metadata(tmp.name)
            assert exc.value.status_code == 400
            assert exc.value.detail["error_code"] == "credentials_not_service_account"
        finally:
            os.unlink(tmp.name)


# ─── Upload filename helpers ─────────────────────────────────────────────────


class TestCalendarFilenameForEmail:
    """Stable-hash filename generation for uploaded SA files. Per Codex
    feedback #3: re-uploading same SA must overwrite same file (preserving
    UI/YAML credentials_path references); different SAs must write to
    different files."""

    def test_same_email_produces_same_filename(self):
        email = "test-sa@synthetic-project.iam.gserviceaccount.com"
        assert _calendar_filename_for_email(email) == _calendar_filename_for_email(email)

    def test_different_emails_produce_different_filenames(self):
        a = "sa-one@project-a.iam.gserviceaccount.com"
        b = "sa-two@project-b.iam.gserviceaccount.com"
        assert _calendar_filename_for_email(a) != _calendar_filename_for_email(b)

    def test_filename_matches_stable_pattern(self):
        """Generated filename must round-trip through _resolve_calendar_secret_path."""
        name = _calendar_filename_for_email("anything@example.com")
        # Should match the pattern: google-calendar-<12-hex-chars>.json
        import re
        assert re.fullmatch(r"google-calendar-[a-f0-9]{12}\.json", name)

    def test_short_hash_collision_resistance(self):
        """12 chars of sha256 = 48 bits → collisions vanishingly unlikely
        for the realistic count of SAs an operator might add. Sanity check
        that 100 distinct emails produce 100 distinct filenames."""
        names = {
            _calendar_filename_for_email(f"sa-{i}@project.iam.gserviceaccount.com")
            for i in range(100)
        }
        assert len(names) == 100


# ─── Path resolution / traversal protection ──────────────────────────────────


class TestResolveCalendarSecretPath:
    """The DELETE endpoint takes a filename in URL path. The resolver must
    refuse anything that doesn't match the stable-hash pattern and refuse
    anything that would resolve outside the secrets directory. Per Codex
    feedback #2."""

    @pytest.mark.parametrize(
        "filename",
        [
            "google-calendar-abcdef012345.json",   # canonical
            "google-calendar-0123456789ab.json",
            "google-calendar-aaaaaaaaaaaa.json",
        ],
    )
    def test_valid_pattern_accepted(self, filename: str):
        """Filenames matching the stable-hash pattern resolve to a path inside the secrets dir."""
        path = _resolve_calendar_secret_path(filename)
        assert path.endswith(filename)
        assert "/secrets/" in path

    @pytest.mark.parametrize(
        "filename",
        [
            "",                                     # empty
            "google-calendar-tooshort.json",        # 8 chars not 12
            "google-calendar-WAY_TOO_LONG_AAAAAAAAA.json",
            "google-calendar-XYZXYZ123456.json",    # X/Y/Z not in [a-f0-9]
            "google-calendar-ABCDEF123456.json",    # uppercase rejected (case-sensitive a-f)
            "google-calendar-abcdef012345.JSON",    # uppercase ext rejected
            "google-calendar-abcdef012345.json.bak",  # extra extension
            "../../etc/passwd",                     # path traversal
            "../google-calendar-abcdef012345.json",
            "google-calendar-abcdef012345.json/../../foo",
            "google-calendar-abcdef012345.json\x00",  # null byte
            "subdir/google-calendar-abcdef012345.json",  # has slash
            "google-calendar.json",                 # missing hash entirely
            "random.json",                          # totally wrong shape
        ],
    )
    def test_invalid_filenames_raise_400(self, filename: str):
        """Anything outside the stable-hash pattern is rejected with 400."""
        with pytest.raises(HTTPException) as exc:
            _resolve_calendar_secret_path(filename)
        assert exc.value.status_code == 400
        # Either invalid_filename (regex failed) or path_outside_secrets_dir (escape attempted)
        assert exc.value.detail["error_code"] in ("invalid_filename", "path_outside_secrets_dir")
