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
