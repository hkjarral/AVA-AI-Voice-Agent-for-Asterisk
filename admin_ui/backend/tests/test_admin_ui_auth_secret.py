import importlib
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def _reload_auth(monkeypatch, jwt_secret):
    if jwt_secret is None:
        monkeypatch.delenv("JWT_SECRET", raising=False)
    else:
        monkeypatch.setenv("JWT_SECRET", jwt_secret)

    import auth  # noqa: E402

    return importlib.reload(auth)


def test_jwt_secret_unset_uses_default_dev_secret(monkeypatch) -> None:
    auth = _reload_auth(monkeypatch, None)
    assert auth.SECRET_KEY == auth.DEFAULT_DEV_SECRET
    assert auth.USING_PLACEHOLDER_SECRET is True


def test_jwt_secret_empty_uses_default_dev_secret(monkeypatch) -> None:
    auth = _reload_auth(monkeypatch, "")
    assert auth.SECRET_KEY == auth.DEFAULT_DEV_SECRET
    assert auth.USING_PLACEHOLDER_SECRET is True


def test_jwt_secret_change_me_please_marked_placeholder(monkeypatch) -> None:
    auth = _reload_auth(monkeypatch, "change-me-please")
    assert auth.SECRET_KEY == "change-me-please"
    assert auth.USING_PLACEHOLDER_SECRET is True


def test_jwt_secret_custom_not_placeholder(monkeypatch) -> None:
    auth = _reload_auth(monkeypatch, "super-secret-value-123")
    assert auth.SECRET_KEY == "super-secret-value-123"
    assert auth.USING_PLACEHOLDER_SECRET is False



def test_save_users_replaces_complete_file_and_preserves_permissions(tmp_path, monkeypatch):
    import json
    import stat
    import pytest
    import auth
    from services import fs

    path = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_PATH", str(path))
    original = {"operator": {"must_change_password": True}}
    updated = {"operator": {"must_change_password": False}}
    auth.save_users(original)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.chmod(0o640)
    replace = fs.os.replace
    def observe(source, target):
        assert auth.load_users() == original
        with open(source) as stream:
            assert json.load(stream) == updated
        replace(source, target)

    monkeypatch.setattr(fs.os, "replace", observe)
    auth.save_users(updated)
    assert auth.load_users() == updated
    assert stat.S_IMODE(path.stat().st_mode) == 0o640

    def fail_replace(*_args):
        raise OSError("fixture write failure")

    monkeypatch.setattr(fs.os, "replace", fail_replace)
    with pytest.raises(OSError):
        auth.save_users(original)
    assert auth.load_users() == updated
    assert list(tmp_path.iterdir()) == [path]
