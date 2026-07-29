from __future__ import annotations

import errno
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
import yaml

from api import config as config_api
from services.fs import atomic_write_text
from services.project_permissions import prepare_project_write_access


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_repairs_parent_directories_required_by_atomic_config_saves(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    local_config = config_dir / "ai-agent.local.yaml"
    env_file = tmp_path / ".env"
    users_file = config_dir / "users.json"
    local_config.write_text("tools: {}\n")
    env_file.write_text("TZ=UTC\n")
    users_file.write_text("{}\n")

    tmp_path.chmod(0o755)
    config_dir.chmod(0o755)
    for path in (local_config, env_file, users_file):
        path.chmod(0o644)

    result = prepare_project_write_access(tmp_path, runtime_gid=os.getgid())

    assert result.warnings == []
    assert _mode(tmp_path) & stat.S_IWGRP
    assert _mode(tmp_path) & stat.S_IXGRP
    assert _mode(config_dir) & stat.S_IWGRP
    assert _mode(config_dir) & stat.S_IXGRP
    for path in (local_config, env_file, users_file):
        assert _mode(path) & stat.S_IWGRP

    # Both persistence families use a temp file followed by os.replace().
    atomic_write_text(str(local_config), "tools:\n  leave_voicemail:\n    extension: '2000'\n")
    atomic_write_text(str(env_file), "TZ=America/Los_Angeles\n")
    assert "leave_voicemail" in local_config.read_text()
    assert "America/Los_Angeles" in env_file.read_text()


def test_local_config_write_falls_back_for_file_bind_mount(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    base_path = config_dir / "ai-agent.yaml"
    local_path = config_dir / "ai-agent.local.yaml"
    base_path.write_text("{}\n")
    local_path.write_text("providers:\n  deepgram:\n    input_sample_rate_hz: 16000\n")
    local_path.chmod(0o640)
    original_inode = local_path.stat().st_ino

    monkeypatch.setattr(config_api.settings, "CONFIG_PATH", str(base_path))
    monkeypatch.setattr(config_api.settings, "LOCAL_CONFIG_PATH", str(local_path))

    def reject_mount_point_replace(_src, _dst):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(config_api.os, "replace", reject_mount_point_replace)

    desired = "providers:\n  deepgram:\n    input_sample_rate_hz: 8000\n"
    config_api._write_local_config(desired)

    assert local_path.read_text() == desired
    assert _mode(local_path) == 0o640
    assert local_path.stat().st_ino == original_inode
    backups = list(config_dir.glob("ai-agent.local.yaml.bak.*"))
    assert len(backups) == 1
    assert "input_sample_rate_hz: 16000" in backups[0].read_text()
    assert list(config_dir.glob("*.tmp")) == []


def test_local_config_bind_mount_fallback_requires_existing_target(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    base_path = config_dir / "ai-agent.yaml"
    local_path = config_dir / "ai-agent.local.yaml"
    base_path.write_text("{}\n")

    monkeypatch.setattr(config_api.settings, "CONFIG_PATH", str(base_path))
    monkeypatch.setattr(config_api.settings, "LOCAL_CONFIG_PATH", str(local_path))

    def reject_mount_point_replace(_src, _dst):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(config_api.os, "replace", reject_mount_point_replace)

    with pytest.raises(OSError, match="cannot be inspected"):
        config_api._write_local_config("providers: {}\n")

    assert not local_path.exists()
    assert list(config_dir.glob("*.tmp")) == []


def test_local_config_bind_mount_partial_write_restores_exact_bytes(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    base_path = config_dir / "ai-agent.yaml"
    local_path = config_dir / "ai-agent.local.yaml"
    previous = b"providers:\n  deepgram:\n    input_sample_rate_hz: 16000\n"
    desired = "providers:\n  deepgram:\n    input_sample_rate_hz: 8000\n"
    base_path.write_bytes(b"{}\n")
    local_path.write_bytes(previous)
    original_inode = local_path.stat().st_ino

    monkeypatch.setattr(config_api.settings, "CONFIG_PATH", str(base_path))
    monkeypatch.setattr(config_api.settings, "LOCAL_CONFIG_PATH", str(local_path))

    def reject_mount_point_replace(_src, _dst):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(config_api.os, "replace", reject_mount_point_replace)
    real_open = open
    write_attempts = 0

    class PartialWriter:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.wrapped.close()

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def write(self, data):
            self.wrapped.write(data[:8])
            self.wrapped.flush()
            raise OSError(errno.EIO, "simulated partial write")

    def flaky_open(path, mode="r", *args, **kwargs):
        nonlocal write_attempts
        wrapped = real_open(path, mode, *args, **kwargs)
        if os.fspath(path) == str(local_path) and mode == "r+b":
            write_attempts += 1
            if write_attempts == 1:
                return PartialWriter(wrapped)
        return wrapped

    monkeypatch.setattr(config_api, "open", flaky_open, raising=False)

    with pytest.raises(OSError, match="simulated partial write"):
        config_api._write_local_config(desired)

    assert local_path.read_bytes() == previous
    assert local_path.stat().st_ino == original_inode
    backups = list(config_dir.glob("ai-agent.local.yaml.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == previous
    assert list(config_dir.glob("*.tmp")) == []


def test_local_config_bind_mount_rollback_failure_is_explicit(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    base_path = config_dir / "ai-agent.yaml"
    local_path = config_dir / "ai-agent.local.yaml"
    previous = b"providers:\n  deepgram:\n    input_sample_rate_hz: 16000\n"
    base_path.write_bytes(b"{}\n")
    local_path.write_bytes(previous)

    monkeypatch.setattr(config_api.settings, "CONFIG_PATH", str(base_path))
    monkeypatch.setattr(config_api.settings, "LOCAL_CONFIG_PATH", str(local_path))

    def reject_mount_point_replace(_src, _dst):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(config_api.os, "replace", reject_mount_point_replace)
    real_open = open

    class FailingWriter:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.wrapped.close()

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def write(self, data):
            self.wrapped.write(data[:4])
            self.wrapped.flush()
            raise OSError(errno.EIO, "simulated write failure")

    def failing_open(path, mode="r", *args, **kwargs):
        wrapped = real_open(path, mode, *args, **kwargs)
        if os.fspath(path) == str(local_path) and mode == "r+b":
            return FailingWriter(wrapped)
        return wrapped

    monkeypatch.setattr(config_api, "open", failing_open, raising=False)

    with pytest.raises(
        OSError,
        match="write failed and automatic recovery failed; restore backup",
    ) as exc_info:
        config_api._write_local_config("providers: {}\n")

    assert exc_info.value.errno == errno.EIO
    backups = list(config_dir.glob("ai-agent.local.yaml.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == previous
    assert str(backups[0]) in str(exc_info.value)
    assert list(config_dir.glob("*.tmp")) == []


def test_local_config_write_does_not_mask_non_bind_mount_replace_error(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    base_path = config_dir / "ai-agent.yaml"
    local_path = config_dir / "ai-agent.local.yaml"
    base_path.write_text("{}\n")
    local_path.write_text("{}\n")

    monkeypatch.setattr(config_api.settings, "CONFIG_PATH", str(base_path))
    monkeypatch.setattr(config_api.settings, "LOCAL_CONFIG_PATH", str(local_path))

    def reject_replace(_src, _dst):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(config_api.os, "replace", reject_replace)

    with pytest.raises(OSError, match="Permission denied"):
        config_api._write_local_config("providers: {}\n")

    assert local_path.read_text() == "{}\n"
    assert list(config_dir.glob("*.tmp")) == []


def test_missing_optional_mutable_files_do_not_block_startup(tmp_path):
    (tmp_path / "config").mkdir()

    result = prepare_project_write_access(tmp_path, runtime_gid=os.getgid())

    assert result.warnings == []
    assert set(result.repaired) == {str(tmp_path), str(tmp_path / "config")}


def test_missing_config_directory_is_reported_without_crashing(tmp_path):
    result = prepare_project_write_access(tmp_path, runtime_gid=os.getgid())

    assert result.repaired == [str(tmp_path)]
    assert result.warnings == [f"Writable project directory is missing: {tmp_path / 'config'}"]


def test_shared_yaml_persistence_covers_every_tools_page_family(tmp_path, monkeypatch):
    """Voicemail is not a special save path; every Tools section persists together."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    base_path = config_dir / "ai-agent.yaml"
    local_path = config_dir / "ai-agent.local.yaml"
    base_path.write_text("{}\n")
    local_path.write_text("{}\n")
    tmp_path.chmod(0o755)
    config_dir.chmod(0o755)
    local_path.chmod(0o644)
    prepare_project_write_access(tmp_path, runtime_gid=os.getgid())

    monkeypatch.setattr(config_api.settings, "CONFIG_PATH", str(base_path))
    monkeypatch.setattr(config_api.settings, "LOCAL_CONFIG_PATH", str(local_path))
    monkeypatch.setattr(config_api, "_assert_tool_emails_valid", lambda content: None)
    monkeypatch.setattr(config_api, "_validate_ai_agent_config", lambda content: {"warnings": []})
    monkeypatch.setattr(config_api, "_migrate_inline_provider_secrets", lambda parsed: False)
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: {})
    monkeypatch.setattr(config_api, "_read_base_config_dict", lambda: {})
    monkeypatch.setattr(config_api, "_compute_local_override", lambda base, parsed: parsed)

    desired = {
        "tools": {
            "transfer": {"enabled": True, "destinations": {"support": {"extension": "6000"}}},
            "hangup_call": {"enabled": True},
            "leave_voicemail": {
                "enabled": True,
                "default_mailbox_key": "sales",
                "mailboxes": {"sales": {"extension": "2000"}},
            },
            "google_calendar": {"enabled": True, "calendars": {"work": {"calendar_id": "work@example.com"}}},
            "microsoft_calendar": {"enabled": True, "accounts": {"default": {"calendar_id": "calendar-id"}}},
            "send_email_summary": {"enabled": True, "admin_email": "ops@example.com"},
            "request_transcript": {"enabled": True},
        },
        "in_call_tools": {
            "lookup_order": {"kind": "http", "phase": "in_call", "url": "https://example.com/lookup"}
        },
        "post_call_tools": {
            "archive_call": {"kind": "http", "phase": "post_call", "url": "https://example.com/archive"}
        },
    }

    result = config_api.persist_config_content(yaml.safe_dump(desired, sort_keys=False))
    persisted = yaml.safe_load(local_path.read_text())

    assert result["status"] == "success"
    assert persisted == desired


def test_complete_config_updates_are_serialized_without_lost_fields_or_deadlock(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    base_path = config_dir / "ai-agent.yaml"
    local_path = config_dir / "ai-agent.local.yaml"
    base_path.write_text("{}\n", encoding="utf-8")
    local_path.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(config_api.settings, "CONFIG_PATH", str(base_path))
    monkeypatch.setattr(config_api.settings, "LOCAL_CONFIG_PATH", str(local_path))
    monkeypatch.setattr(config_api, "_assert_tool_emails_valid", lambda content: None)
    monkeypatch.setattr(config_api, "_migrate_inline_provider_secrets", lambda parsed: False)

    persist_holds_lock = Event()
    provider_attempted_update = Event()
    allow_persist = Event()
    first_validation = True

    def controlled_validation(content):
        nonlocal first_validation
        if first_validation:
            first_validation = False
            persist_holds_lock.set()
            assert allow_persist.wait(timeout=5)
        return {"warnings": []}

    monkeypatch.setattr(
        config_api,
        "_validate_ai_agent_config",
        controlled_validation,
    )
    desired = yaml.safe_dump(
        {"tools": {"request_transcript": {"enabled": True}}},
        sort_keys=False,
    )

    def update_provider_field():
        provider_attempted_update.set()
        return config_api.update_yaml_provider_field(
            "local",
            "tts_model",
            "kokoro",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        persist_future = executor.submit(config_api.persist_config_content, desired)
        assert persist_holds_lock.wait(timeout=5)
        provider_future = executor.submit(update_provider_field)
        assert provider_attempted_update.wait(timeout=5)
        assert not provider_future.done()
        allow_persist.set()

        assert persist_future.result(timeout=5)["status"] == "success"
        assert provider_future.result(timeout=5) is True

    merged = config_api._read_merged_config_dict()
    assert merged["tools"]["request_transcript"]["enabled"] is True
    assert merged["providers"]["local"]["tts_model"] == "kokoro"
