"""Headless-safe one-time import of legacy YAML Contexts into ``agents.db``.

The Admin UI normally performs this migration.  The engine also runs this small,
dependency-free bridge before it constructs routing so installations that start
the engine without the Admin UI cannot silently lose their legacy personas.
After the import, Agents are the only runtime source of persona configuration.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from src.tools.runtime_config import (
    merge_legacy_tool_overrides,
    normalize_agent_tool_configs,
)


DB_DEFAULT = "/app/data/operator/agents.db"
MIGRATION_VERSION = 1
_SLUG_RE = re.compile(r"[^a-z0-9_]+")
_FIRST_CLASS = {
    "provider", "voice", "greeting", "prompt", "audio_profile", "profile",
    "tools", "tool_configs", "tool_overrides", "email_recipient", "email_from", "email_enabled",
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


def contexts_hash(contexts: Mapping[str, Any]) -> str:
    """Return the Admin-compatible normalized digest used for drift checks."""
    clean = {
        key: {
            field: value
            for field, value in _mapping(raw_context).items()
            if field != "_source_file"
        }
        for key, raw_context in contexts.items()
    }
    canonical = json.dumps(
        clean, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


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


def _migration_completed(db_path: str) -> bool:
    """Return whether the one-time Context import was already recorded."""
    if not os.path.exists(db_path):
        return False
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        try:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if not table_exists:
                return False
            return connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION_VERSION,)
            ).fetchone() is not None
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise LegacyAgentMigrationError(f"existing agents.db is unreadable: {exc}") from exc


def _record_migration_completed(db_path: str, digest: Optional[str]) -> None:
    """Mark an existing authoritative Agent store as migrated."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        connection = sqlite3.connect(db_path, timeout=5.0)
        try:
            with connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version INTEGER PRIMARY KEY,
                       applied_at TEXT NOT NULL,
                       contexts_hash TEXT
                    )"""
                )
                connection.execute(
                    """INSERT OR IGNORE INTO schema_migrations(
                       version, applied_at, contexts_hash
                    ) VALUES (?,?,?)""",
                    (MIGRATION_VERSION, now, digest),
                )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise LegacyAgentMigrationError(
            f"could not record existing agents.db migration completion: {exc}"
        ) from exc


def _prepare_existing_database_for_replace(db_path: str) -> None:
    """Checkpoint and remove WAL sidecars before atomically replacing an empty DB.

    An Admin UI startup or interrupted prior run may leave an empty ``agents.db``
    in WAL mode. Publishing a newly migrated main file beside those old sidecars
    can make SQLite replay frames that belong to the replaced database. Refuse to
    publish if the checkpoint is busy, then remove both sidecars while the
    migration lock is held.
    """
    if not os.path.exists(db_path):
        return
    try:
        connection = sqlite3.connect(db_path, timeout=5.0)
        try:
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint and int(checkpoint[0]) != 0:
                raise LegacyAgentMigrationError(
                    "existing agents.db WAL is busy; cannot safely replace the empty database"
                )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise LegacyAgentMigrationError(
            f"existing agents.db WAL checkpoint failed: {exc}"
        ) from exc

    for suffix in ("-wal", "-shm"):
        try:
            os.unlink(f"{db_path}{suffix}")
        except FileNotFoundError:
            pass


def _upgrade_existing_resource_policies(db_path: str) -> int:
    """Promote calendar bindings left in extra_json by early v7.4 builds.

    This is an additive, idempotent bridge for databases populated before
    calendar resource policies became first-class. It intentionally preserves
    the legacy extra_json payload for lossless export/debugging.
    """
    try:
        connection = sqlite3.connect(db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(agents)")
        }
        if not {"tool_configs_json", "extra_json"} <= columns:
            connection.close()
            return 0
        changed = 0
        with connection:
            rows = connection.execute(
                "SELECT id, tool_configs_json, extra_json FROM agents"
            ).fetchall()
            for row in rows:
                try:
                    current = (
                        json.loads(row["tool_configs_json"])
                        if row["tool_configs_json"] else None
                    )
                    normalized_current = normalize_agent_tool_configs(current)
                    extra = json.loads(row["extra_json"]) if row["extra_json"] else {}
                    legacy = extra.get("tool_overrides") if isinstance(extra, dict) else None
                    merged = merge_legacy_tool_overrides(current, legacy)
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise LegacyAgentMigrationError(
                        f"existing Agent tool policy migration failed for {row['id']}: {exc}"
                    ) from exc
                if merged != normalized_current:
                    serialized = json.dumps(merged, sort_keys=True) if merged else None
                    connection.execute(
                        "UPDATE agents SET tool_configs_json=? WHERE id=?",
                        (serialized, row["id"]),
                    )
                    changed += 1
        connection.close()
        return changed
    except sqlite3.Error as exc:
        raise LegacyAgentMigrationError(
            f"existing agents.db resource policy upgrade failed: {exc}"
        ) from exc


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
    target = db_path or os.getenv("AGENTS_DB_PATH", DB_DEFAULT)
    # Fresh headless/test environments with no legacy Contexts have nothing to
    # migrate and must not require the production /app volume to be writable.
    # An existing DB still proceeds so early-v7.4 calendar policies can upgrade.
    if not context_map and not os.path.exists(target):
        return {"imported": 0, "already_configured": False}
    parent = os.path.dirname(target) or "."
    os.makedirs(parent, exist_ok=True)

    with _migration_lock(target):
        # The durable migration marker wins even if the operator later deletes
        # every Agent. Legacy YAML remains on disk for rollback diagnostics and
        # must not resurrect deleted Agents on the next engine restart.
        if _migration_completed(target):
            upgraded = _upgrade_existing_resource_policies(target)
            return {
                "imported": 0,
                "already_configured": True,
                "resource_policies_upgraded": upgraded,
            }
        count = _existing_agent_count(target)
        if count:
            upgraded = _upgrade_existing_resource_policies(target)
            # A pre-provisioned/early v7.4 store may have Agent rows without the
            # migration marker. Record that it is authoritative so deleting its
            # final Agent later cannot make retained legacy YAML import again.
            try:
                digest = contexts_hash(context_map)
            except TypeError:
                # A populated Agent store remains authoritative even if retained
                # legacy diagnostics are malformed. Preserve the prior startup
                # behavior and leave the optional drift baseline unknown.
                digest = None
            _record_migration_completed(target, digest)
            return {
                "imported": 0,
                "already_configured": True,
                "resource_policies_upgraded": upgraded,
            }
        if not context_map:
            return {"imported": 0, "already_configured": False}
        rows = []
        errors = []
        seen = set()
        normalized_contexts: Dict[str, Dict[str, Any]] = {}
        now = datetime.now(timezone.utc).isoformat()
        for original_name, raw_context in context_map.items():
            try:
                context = _mapping(raw_context)
            except TypeError as exc:
                errors.append(f"{original_name}: {exc}")
                continue
            normalized_contexts[original_name] = context
            prompt = context.get("prompt") or context.get("system_prompt") or ""
            if not isinstance(prompt, str):
                errors.append(f"{original_name}: prompt must be a string")
                continue
            try:
                tool_configs = merge_legacy_tool_overrides(
                    context.get("tool_configs"), context.get("tool_overrides")
                )
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
        digest = contexts_hash(normalized_contexts)

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
                    "INSERT INTO schema_migrations(version, applied_at, contexts_hash) VALUES (?,?,?)",
                    (MIGRATION_VERSION, now, digest),
                )
                connection.commit()
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise LegacyAgentMigrationError(f"generated agents.db failed integrity check: {integrity}")
            finally:
                connection.close()
            os.chmod(temp_path, 0o600)
            _prepare_existing_database_for_replace(target)
            os.replace(temp_path, target)
            return {"imported": len(rows), "default_slug": "default" if "default" in seen else rows[0][1]}
        except Exception as exc:
            if isinstance(exc, LegacyAgentMigrationError):
                raise
            raise LegacyAgentMigrationError(f"could not create agents.db: {exc}") from exc
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
