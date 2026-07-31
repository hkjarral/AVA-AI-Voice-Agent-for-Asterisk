import os
import sys
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import system  # noqa: E402


def _platform_check_inputs(docker_info: dict):
    return {
        "os_info": {
            "arch": "x86_64",
            "is_eol": False,
            "id": "debian",
            "version": "12",
        },
        "docker_info": docker_info,
        "compose_info": {
            "installed": True,
            "version": "2.27.1",
            "status": "ok",
            "message": None,
        },
        "selinux_info": {
            "present": False,
            "mode": None,
            "tools_installed": False,
        },
        "dir_info": {
            "media": {
                "exists": True,
                "writable": True,
                "path": "/mnt/asterisk_media/ai-generated",
            }
        },
        "asterisk_info": {
            "detected": False,
            "config_dir": None,
            "freepbx": {"detected": False, "version": None},
        },
        "platform_cfg": {},
    }


def test_detect_docker_classifies_requests_adapter_failure_as_client_error(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(system.os.path, "exists", lambda path: path == "/var/run/docker.sock")
    monkeypatch.setattr(
        system.os,
        "stat",
        lambda _path: SimpleNamespace(st_gid=os.getgid(), st_mode=0o660),
    )
    monkeypatch.setattr(system.shutil, "which", lambda command: "/usr/bin/docker" if command == "docker" else None)

    def fail_from_env():
        raise RuntimeError(
            "Error while fetching server API version: Not supported URL scheme http+docker"
        )

    monkeypatch.setattr(system.docker, "from_env", fail_from_env)

    result = system._detect_docker()

    assert result["installed"] is True
    assert result["reachable"] is False
    assert result["client_adapter_error"] is True
    assert result["permission_denied"] is False
    assert "incompatible Docker SDK/Requests adapter" in result["message"]


def test_detect_docker_uses_rootless_docker_host_for_adapter_failure(monkeypatch):
    rootless_socket = "/run/user/1000/docker.sock"
    monkeypatch.setenv("DOCKER_HOST", f"unix://{rootless_socket}")
    monkeypatch.setattr(system.os.path, "exists", lambda path: path == rootless_socket)

    def fake_stat(path):
        if path != rootless_socket:
            raise AssertionError(f"unexpected stat path: {path}")
        return SimpleNamespace(st_gid=1000, st_mode=0o140660)

    monkeypatch.setattr(system.os, "stat", fake_stat)
    monkeypatch.setattr(system.shutil, "which", lambda _command: None)

    def fail_from_env():
        raise RuntimeError(
            "Error while fetching server API version: Not supported URL scheme http+docker"
        )

    monkeypatch.setattr(system.docker, "from_env", fail_from_env)

    result = system._detect_docker()

    assert result["socket_path"] == rootless_socket
    assert result["socket_present"] is True
    assert result["socket_gid"] == 1000
    assert result["socket_mode"] == "0o660"
    assert result["mode"] == "rootless"
    assert result["installed"] is True
    assert result["client_adapter_error"] is True

    checks = system._build_checks(**_platform_check_inputs(result))
    docker_checks = [check for check in checks if check["id"].startswith("docker_")]
    assert any(check["id"] == "docker_client_adapter" for check in docker_checks)
    assert all(check["id"] != "docker_socket" for check in docker_checks)
    adapter_check = next(check for check in docker_checks if check["id"] == "docker_client_adapter")
    assert adapter_check["action"]["label"] == "Rebuild Admin UI"
    assert "DOCKER_SOCK" not in adapter_check["action"]["value"]


def test_missing_rootless_socket_is_not_mislabeled_as_adapter_failure(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    monkeypatch.setattr(system.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(system.shutil, "which", lambda _command: None)
    monkeypatch.setattr(
        system.docker,
        "from_env",
        lambda: (_ for _ in ()).throw(
            RuntimeError(
                "Error while fetching server API version: Not supported URL scheme http+docker"
            )
        ),
    )

    result = system._detect_docker()

    assert result["socket_present"] is False
    assert result["installed"] is False
    assert result["client_adapter_error"] is False
    checks = system._build_checks(**_platform_check_inputs(result))
    assert any(check["id"] == "docker_socket" for check in checks)
    assert all(check["id"] != "docker_client_adapter" for check in checks)


def test_rootless_permission_failure_uses_resolved_socket_guidance(monkeypatch):
    rootless_socket = "/run/user/1000/docker.sock"
    monkeypatch.setenv("DOCKER_HOST", f"unix://{rootless_socket}")
    monkeypatch.setattr(system.os.path, "exists", lambda path: path == rootless_socket)
    monkeypatch.setattr(
        system.os,
        "stat",
        lambda path: (
            SimpleNamespace(st_gid=1000, st_mode=0o140660)
            if path == rootless_socket
            else (_ for _ in ()).throw(AssertionError(f"unexpected stat path: {path}"))
        ),
    )
    monkeypatch.setattr(system.shutil, "which", lambda _command: None)
    monkeypatch.setattr(
        system.docker,
        "from_env",
        lambda: (_ for _ in ()).throw(PermissionError(13, "Permission denied")),
    )

    result = system._detect_docker()

    assert result["permission_denied"] is True
    assert result["socket_path"] == rootless_socket
    assert result["socket_gid"] == 1000

    checks = system._build_checks(**_platform_check_inputs(result))
    permission_check = next(
        check for check in checks if check["id"] == "docker_socket_perms"
    )
    command = permission_check["action"]["value"]
    assert rootless_socket in command
    assert "DOCKER_GID=1000" in command
    assert "/var/run/docker.sock" not in command


def test_detect_docker_does_not_stat_non_unix_docker_hosts(monkeypatch):
    class FakeDockerClient:
        def version(self):
            return {"Version": "27.1.1", "ApiVersion": "1.46"}

        def info(self):
            return {
                "OperatingSystem": "Docker Engine",
                "Architecture": "x86_64",
                "OSType": "linux",
            }

    def unexpected_filesystem_probe(path):
        raise AssertionError(f"unexpected filesystem probe: {path}")

    monkeypatch.setattr(system.os.path, "exists", unexpected_filesystem_probe)
    monkeypatch.setattr(system.docker, "from_env", lambda: FakeDockerClient())
    monkeypatch.setattr(system.shutil, "which", lambda _command: None)

    for docker_host in ("tcp://127.0.0.1:2375", "npipe:////./pipe/docker_engine"):
        monkeypatch.setenv("DOCKER_HOST", docker_host)

        result = system._detect_docker()

        assert result["socket_path"] is None
        assert result["socket_present"] is False
        assert result["installed"] is True
        assert result["reachable"] is True
        assert result["mode"] == "rootful"


def test_client_adapter_failure_never_recommends_installing_docker():
    docker_info = {
        "installed": True,
        "reachable": False,
        "version": None,
        "status": "error",
        "message": "Admin UI Docker client cannot use the mounted Docker socket",
        "socket_present": True,
        "permission_denied": False,
        "client_adapter_error": True,
        "is_docker_desktop": False,
    }

    checks = system._build_checks(**_platform_check_inputs(docker_info))

    docker_check = next(check for check in checks if check["id"] == "docker_client_adapter")
    assert docker_check["blocking"] is True
    assert docker_check["action"]["label"] == "Rebuild Admin UI"
    assert all(
        (check.get("action") or {}).get("label") != "Install Docker"
        for check in checks
    )
