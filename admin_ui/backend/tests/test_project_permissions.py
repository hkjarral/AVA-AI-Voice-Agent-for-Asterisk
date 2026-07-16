from __future__ import annotations

import os
import stat

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
                "default_mailbox": "sales",
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
