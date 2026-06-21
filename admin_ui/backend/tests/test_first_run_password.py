"""
Tests for first-run random password generation and the 403 gate that enforces
password rotation before any protected endpoint is accessible.
"""
import importlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_users_path(monkeypatch, users_file):
    """Patch USERS_PATH in both settings and auth so a reload picks it up."""
    import settings
    import auth
    monkeypatch.setattr(settings, "USERS_PATH", str(users_file))
    monkeypatch.setattr(auth, "USERS_PATH", str(users_file))
    importlib.reload(auth)


# ---------------------------------------------------------------------------
# Bootstrap tests
# ---------------------------------------------------------------------------

def test_bootstrap_generates_random_password(tmp_path, monkeypatch):
    """ensure_default_user() creates the users file with a random password on first call."""
    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import auth
    pw = auth.ensure_default_user()

    assert pw is not None, "Expected a password on first run"
    assert len(pw) >= 16, "Password should be at least 16 characters"
    assert pw != "admin", "Password must not be the literal string 'admin'"

    data = json.loads(users_file.read_text())
    assert "admin" in data
    assert data["admin"]["must_change_password"] is True
    assert data["admin"]["hashed_password"] != pw  # stored as hash, not plaintext
    assert "username" in data["admin"]


def test_bootstrap_idempotent(tmp_path, monkeypatch):
    """ensure_default_user() returns None on subsequent calls (file already exists)."""
    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import auth
    first = auth.ensure_default_user()
    second = auth.ensure_default_user()

    assert first is not None
    assert second is None


def test_bootstrap_password_is_unique_across_calls(tmp_path, monkeypatch):
    """Each fresh install gets a different one-time password."""
    import settings

    file_a = tmp_path / "a" / "users.json"
    file_b = tmp_path / "b" / "users.json"
    file_a.parent.mkdir()
    file_b.parent.mkdir()

    import auth

    monkeypatch.setattr(settings, "USERS_PATH", str(file_a))
    monkeypatch.setattr(auth, "USERS_PATH", str(file_a))
    importlib.reload(auth)
    pw_a = auth.ensure_default_user()

    monkeypatch.setattr(settings, "USERS_PATH", str(file_b))
    monkeypatch.setattr(auth, "USERS_PATH", str(file_b))
    importlib.reload(auth)
    pw_b = auth.ensure_default_user()

    assert pw_a is not None
    assert pw_b is not None
    assert pw_a != pw_b


def test_bootstrap_file_permissions(tmp_path, monkeypatch):
    """Users file should be created with mode 0o600 (owner read/write only)."""
    import stat as _stat
    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import auth
    auth.ensure_default_user()

    mode = _stat.S_IMODE(users_file.stat().st_mode)
    assert mode == 0o600


def test_first_run_password_file_written_with_0600(tmp_path, monkeypatch):
    """LOW-U2: fresh install writes the one-time password to a root-only 0600 file."""
    import stat as _stat
    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import auth
    pw = auth.ensure_default_user()

    pw_file = Path(auth.FIRST_RUN_PASSWORD_PATH)
    assert pw_file.exists(), "Expected the one-time password file to be created"
    mode = _stat.S_IMODE(pw_file.stat().st_mode)
    assert mode == 0o600, f"Password file must be owner-only 0600, got {oct(mode)}"

    contents = pw_file.read_text()
    assert pw in contents, "File should contain the one-time password"
    assert "delete this file" in contents.lower(), "File should carry a retrieval/cleanup note"


def test_existing_wider_perm_password_file_tightened_before_write(tmp_path, monkeypatch):
    """An EXISTING looser-perm file is fchmod'd to 0o600 BEFORE content is written.

    The mode arg to os.open is ignored for an existing file, so without an fchmod
    immediately after open() the truncate+write happens while the file is still
    world-readable. We assert (a) the final perms are 0o600, and (b) an fchmod to
    0o600 happened on the freshly-opened fd before the file was written — the
    latter fails on the pre-fix truncate-then-chmod ordering (no fchmod at all).
    """
    import os
    import stat as _stat
    import auth

    pw_file = tmp_path / ".first-run-password"
    pw_file.write_text("stale\n")
    os.chmod(pw_file, 0o644)
    assert _stat.S_IMODE(pw_file.stat().st_mode) == 0o644

    monkeypatch.setattr(auth, "FIRST_RUN_PASSWORD_PATH", str(pw_file))

    real_fchmod = os.fchmod
    fchmod_calls = []

    def _spy_fchmod(fd, mode):
        # Record (mode, on-disk-size) so we can prove the chmod preceded the write.
        fchmod_calls.append((mode, os.fstat(fd).st_size))
        return real_fchmod(fd, mode)

    monkeypatch.setattr(os, "fchmod", _spy_fchmod)
    auth._write_first_run_password_file("a-new-one-time-password")

    assert fchmod_calls, "expected fchmod on the fd before writing (perms window)"
    mode, size_at_chmod = fchmod_calls[0]
    assert mode == 0o600, f"fd must be fchmod'd to 0o600, got {oct(mode)}"
    # O_TRUNC empties the file; fchmod must run before any password bytes land.
    assert size_at_chmod == 0, "fchmod must precede the write (file empty at chmod)"
    assert _stat.S_IMODE(pw_file.stat().st_mode) == 0o600
    assert "a-new-one-time-password" in pw_file.read_text()


def test_first_run_password_file_written_on_legacy_rotation(tmp_path, monkeypatch):
    """LOW-U2: rotating a legacy admin/admin install also writes the 0600 file."""
    import stat as _stat
    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import auth
    users_file.write_text(json.dumps({"admin": {"username": "admin",
        "hashed_password": auth.get_password_hash("admin"), "disabled": False,
        "must_change_password": True}}))

    pw = auth.ensure_default_user()
    assert pw is not None

    pw_file = Path(auth.FIRST_RUN_PASSWORD_PATH)
    assert pw_file.exists()
    assert _stat.S_IMODE(pw_file.stat().st_mode) == 0o600
    assert pw in pw_file.read_text()


def test_no_password_file_when_already_secured(tmp_path, monkeypatch):
    """LOW-U2: an already-rotated install creates no password file (no-op path)."""
    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import auth
    users_file.write_text(json.dumps({"admin": {"username": "admin",
        "hashed_password": auth.get_password_hash("a-real-rotated-secret"),
        "disabled": False, "must_change_password": False}}))

    assert auth.ensure_default_user() is None
    assert not Path(auth.FIRST_RUN_PASSWORD_PATH).exists()


def test_load_users_after_bootstrap_returns_data(tmp_path, monkeypatch):
    """load_users() works correctly after ensure_default_user() has run."""
    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import auth
    auth.ensure_default_user()
    users = auth.load_users()

    assert "admin" in users
    assert users["admin"]["must_change_password"] is True


# ---------------------------------------------------------------------------
# 403 gate tests — use a minimal FastAPI app so we avoid the full main.py
# import chain (which requires Docker/src paths).
# ---------------------------------------------------------------------------

def _build_minimal_app(users_file):
    """Build a minimal FastAPI app with auth routes + one protected route."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    import auth

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")

    @app.get("/api/protected")
    async def protected_route(current_user=Depends(auth.get_current_user)):
        return {"status": "ok", "user": current_user.username}

    return app


def _bootstrap_known_password(users_file, monkeypatch):
    """Bootstrap the users file and return the known test password."""
    import auth

    # Write a known password so we can log in deterministically.
    known_password = "test-known-password-abc"
    users = {
        "admin": {
            "username": "admin",
            "hashed_password": auth.get_password_hash(known_password),
            "disabled": False,
            "must_change_password": True,
        }
    }
    users_file.parent.mkdir(parents=True, exist_ok=True)
    users_file.write_text(json.dumps(users, indent=2))
    return known_password


def test_protected_endpoint_returns_403_when_must_change_password(tmp_path, monkeypatch):
    """A protected endpoint should return 403 while must_change_password is True."""
    from fastapi.testclient import TestClient

    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import auth
    known_password = _bootstrap_known_password(users_file, monkeypatch)

    # Confirm flag is set
    users = auth.load_users()
    assert users["admin"]["must_change_password"] is True

    app = _build_minimal_app(users_file)
    client = TestClient(app, raise_server_exceptions=False)

    # Log in
    response = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": known_password},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    token = response.json()["access_token"]
    assert response.json()["must_change_password"] is True

    # Protected endpoint must be 403
    response = client.get(
        "/api/protected",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403, (
        f"Expected 403 while must_change_password=True, got {response.status_code}: {response.text}"
    )


def test_change_password_endpoint_accessible_when_must_change_password(tmp_path, monkeypatch):
    """The /change-password endpoint must still be reachable while must_change_password is True."""
    from fastapi.testclient import TestClient

    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import auth
    known_password = _bootstrap_known_password(users_file, monkeypatch)

    app = _build_minimal_app(users_file)
    client = TestClient(app, raise_server_exceptions=False)

    # Log in
    response = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": known_password},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]

    # Change password — must NOT be blocked by the 403 gate
    response = client.post(
        "/api/auth/change-password",
        json={"old_password": known_password, "new_password": "new-secure-password-123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, (
        f"/change-password should be accessible during forced rotation, "
        f"got {response.status_code}: {response.text}"
    )

    # Flag should now be cleared
    users = auth.load_users()
    assert users["admin"]["must_change_password"] is False


def test_me_endpoint_accessible_when_must_change_password(tmp_path, monkeypatch):
    """/me must be accessible while must_change_password is True (needed by frontend)."""
    from fastapi.testclient import TestClient

    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import auth
    known_password = _bootstrap_known_password(users_file, monkeypatch)

    app = _build_minimal_app(users_file)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": known_password},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, (
        f"/me should be accessible during forced rotation, got {response.status_code}"
    )


def test_bootstrap_rotates_legacy_admin_admin(tmp_path, monkeypatch):
    """ensure_default_user() rotates a still-default admin/admin hash on upgrade."""
    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import json
    from auth import ensure_default_user, get_password_hash, verify_password

    # Simulate an upgraded install with the old admin/admin default still in place.
    users_file.write_text(json.dumps({"admin": {"username": "admin",
        "hashed_password": get_password_hash("admin"), "disabled": False,
        "must_change_password": True}}))

    pw = ensure_default_user()
    assert pw is not None and pw != "admin" and len(pw) >= 16

    data = json.loads(users_file.read_text())
    assert not verify_password("admin", data["admin"]["hashed_password"])  # admin/admin no longer works
    assert data["admin"]["must_change_password"] is True


def test_bootstrap_leaves_already_rotated_user_alone(tmp_path, monkeypatch):
    """ensure_default_user() does nothing when the password is already non-default."""
    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import json
    from auth import ensure_default_user, get_password_hash

    users_file.write_text(json.dumps({"admin": {"username": "admin",
        "hashed_password": get_password_hash("a-real-rotated-secret"), "disabled": False,
        "must_change_password": False}}))

    assert ensure_default_user() is None  # not the default -> no action


def test_protected_endpoint_accessible_after_password_change(tmp_path, monkeypatch):
    """After changing password and clearing the flag, protected endpoints are accessible."""
    from fastapi.testclient import TestClient

    users_file = tmp_path / "users.json"
    _set_users_path(monkeypatch, users_file)

    import auth
    known_password = _bootstrap_known_password(users_file, monkeypatch)

    app = _build_minimal_app(users_file)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": known_password},
    )
    token = response.json()["access_token"]

    # Change password to clear the flag
    new_password = "new-secure-pass-456"
    resp = client.post(
        "/api/auth/change-password",
        json={"old_password": known_password, "new_password": new_password},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    # Re-login with the new password; flag is now cleared on disk
    response = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": new_password},
    )
    assert response.status_code == 200
    assert response.json()["must_change_password"] is False
    new_token = response.json()["access_token"]

    # Protected endpoint should now be accessible (200, not 403)
    response = client.get(
        "/api/protected",
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert response.status_code == 200, (
        f"Protected endpoint should be accessible after password change, "
        f"got {response.status_code}: {response.text}"
    )
