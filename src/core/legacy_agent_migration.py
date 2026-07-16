"""Headless-safe one-time import of legacy YAML Contexts into ``agents.db``.

The Admin UI normally performs this migration.  The engine also runs this small,
dependency-free bridge before it constructs routing so installations that start
the engine without the Admin UI cannot silently lose their legacy personas.
After the import, Agents are the only runtime source of persona configuration.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from src.tools.runtime_config import normalize_agent_tool_configs


DB_DEFAULT = "/app/data/operator/agents.db"
MIGRATION_VERSION = 1
_SLUG_RE = re.compile(r"[^a-z0-9_]+")
_FIRST_CLASS = {
    "provider", "voice", "greeting", "prompt", "audio_profile", "profile",
    "tools", "tool_configs", "email_recipient", "email_from", "email_enabled",
    "extension", "role_label",
}

_SCHEMA = """
CREATE TABLE agents (
    id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
    extension TEXT, role_label TEXT, provider TEXT NOT NULL, voice TEXT,
    greeting TEXT, prompt TEXT NOT NULL, tools_json TEXT, tool_configs_json TEXT,
    mcp_json TEXT, audio_profile TEXT, extra_json TEXT,
    is_operator_managed INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1, is_default INTEGER NOT NULL DEFAULT 0,
    source_file TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    notes TEXT, email_recipient TEXT, email_from TEXT, email_enabled INTEGER
);
CREATE INDEX idx_agents_slug ON agents(slug);
CREATE INDEX idx_agents_mgmt ON agents(is_operator_managed);
CREATE UNIQUE INDEX idx_agents_default ON agents(is_default) WHERE is_default = 1;
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, contexts_hash TEXT
);
"""


class LegacyAgentMigrationError(RuntimeError):
    """Legacy Contexts exist but could not be made safe for Agent-only routing."""


def _slugify(name: str) -> str:
    value = _SLUG_RE.sub("_", name.strip().lower().replace("-", "_")).strip("_")[:64]
    return re.sub(r"_+", "_", value)


def _mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(exclude_none=True))
    if hasattr(value, "dict"):
        return dict(value.dict(exclude_none=True))
    raise TypeError(f"expected mapping, got {type(value).__name__}")


def _email_enabled(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return 1
        if normalized in {"false", "0", "no", "off"}:
            return 0
        return None
    return int(bool(value))


@contextmanager
def _migration_lock(db_path: str):
    lock_path = f"{db_path}.migration.lock"
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _existing_agent_count(db_path: str) -> Optional[int]:
    if not os.path.exists(db_path):
        return None
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        try:
            return int(connection.execute("SELECT COUNT(*) FROM agents").fetchone()[0])
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise LegacyAgentMigrationError(f"existing agents.db is unreadable: {exc}") from exc


def ensure_legacy_contexts_imported(
    contexts: Any,
    *,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomically import Contexts only when no Agent rows exist.

    A populated Agent store always wins and is never changed. Empty Contexts are
    also a no-op so a fresh wizard can seed the v7.4 starter set through the
    Admin API. Any malformed legacy Context blocks fail startup instead of
    continuing with partial or ambiguous routing.
    """
    context_map = _mapping(contexts or {})
    if not context_map:
        return {"imported": 0, "already_configured": False}
    target = db_path or os.getenv("AGENTS_DB_PATH", DB_DEFAULT)
    parent = os.path.dirname(target) or "."
    os.makedirs(parent, exist_ok=True)

    with _migration_lock(target):
        count = _existing_agent_count(target)
        if count:
            return {"imported": 0, "already_configured": True}
        rows = []
        errors = []
        seen = set()
        now = datetime.now(timezone.utc).isoformat()
        for original_name, raw_context in context_map.items():
            try:
                context = _mapping(raw_context)
            except TypeError as exc:
                errors.append(f"{original_name}: {exc}")
                continue
            prompt = context.get("prompt") or context.get("system_prompt") or ""
            if not isinstance(prompt, str):
                errors.append(f"{original_name}: prompt must be a string")
                continue
            try:
                tool_configs = normalize_agent_tool_configs(context.get("tool_configs"))
            except ValueError as exc:
                errors.append(f"{original_name}: {exc}")
                continue
            base = _slugify(str(original_name)) or "agent"
            slug = base
            suffix = 2
            while slug in seen:
                slug = f"{base}_{suffix}"
                suffix += 1
            seen.add(slug)
            extra = {key: value for key, value in context.items() if key not in _FIRST_CLASS}
            rows.append((
                uuid.uuid4().hex, slug, str(original_name), context.get("extension"),
                context.get("role_label"), context.get("provider") or "", context.get("voice"),
                context.get("greeting"), prompt, json.dumps(context.get("tools")) if context.get("tools") is not None else None,
                json.dumps(tool_configs, sort_keys=True) if tool_configs else None,
                None, context.get("audio_profile") or context.get("profile"),
                json.dumps(extra) if extra else None, 0, 1, 0, "legacy YAML Context",
                now, now, "Imported automatically during the v7.4 Agent migration",
                context.get("email_recipient"), context.get("email_from"),
                _email_enabled(context.get("email_enabled")),
            ))

        if errors:
            raise LegacyAgentMigrationError(
                "legacy Context import validation failed: " + "; ".join(errors)
            )

        fd, temp_path = tempfile.mkstemp(prefix=".agents-v740-", suffix=".db", dir=parent)
        os.close(fd)
        try:
            connection = sqlite3.connect(temp_path)
            try:
                connection.executescript(_SCHEMA)
                connection.executemany(
                    """INSERT INTO agents (
                       id,slug,display_name,extension,role_label,provider,voice,greeting,prompt,
                       tools_json,tool_configs_json,mcp_json,audio_profile,extra_json,
                       is_operator_managed,is_active,is_default,source_file,created_at,updated_at,
                       notes,email_recipient,email_from,email_enabled
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
                connection.execute(
                    "UPDATE agents SET is_default=1 WHERE slug=?",
                    ("default" if "default" in seen else rows[0][1],),
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at, contexts_hash) VALUES (?,?,NULL)",
                    (MIGRATION_VERSION, now),
                )
                connection.commit()
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise LegacyAgentMigrationError(f"generated agents.db failed integrity check: {integrity}")
            finally:
                connection.close()
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, target)
            return {"imported": len(rows), "default_slug": "default" if "default" in seen else rows[0][1]}
        except Exception as exc:
            if isinstance(exc, LegacyAgentMigrationError):
                raise
            raise LegacyAgentMigrationError(f"could not create agents.db: {exc}") from exc
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
