"""
Outbound campaign dialer persistence (SQLite).

This module intentionally mirrors the Call History persistence style:
- SQLite WAL mode + busy_timeout
- Thread lock around short transactions
- Async facade via run_in_executor to avoid blocking the asyncio loop

MVP scope:
- Campaigns / leads / attempts tables
- Atomic lead leasing (transaction-based; no dependency on RETURNING support)
- Import helpers for Admin UI (skip_existing default)
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import sqlite3
import threading
import uuid
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def _safe_json_loads(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


_NON_DIAL_CHAR_RE = re.compile(r"[^0-9+*#]+")


def _normalize_phone_number(raw: str) -> str:
    """
    Normalize a phone number for storage.

    Accepts common user formats:
      - +15551234567 (E.164)
      - 15551234567
      - 2765 (internal extension)
      - (555) 123-4567, 555-123-4567, etc.

    We store:
      - "+<digits>" when a leading '+' is present
      - "<digits>" otherwise
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("Missing phone_number")
    # Reject alphabets and unsafe characters early. We intentionally avoid enforcing E.164 or digit-length
    # rules because trunk routing/formatting varies internationally and by PBX configuration.
    #
    # Allowed (after stripping formatting): digits plus optional leading '+', and '*'/'#'
    # (useful for lab testing / feature codes).
    if re.search(r"[A-Za-z]", s):
        raise ValueError("Invalid phone_number (letters not allowed)")
    if re.search(r"[^0-9+*#() ./-]", s):
        raise ValueError("Invalid phone_number (contains unsupported characters)")
    s = _NON_DIAL_CHAR_RE.sub("", s)
    if not s:
        raise ValueError("Invalid phone_number (no dialable characters)")

    has_plus = s.startswith("+")
    core = s[1:] if has_plus else s
    if not core:
        raise ValueError("Invalid phone_number (missing digits)")
    if not re.fullmatch(r"[0-9*#]+", core):
        raise ValueError("Invalid phone_number (contains invalid characters)")
    if not re.search(r"[0-9]", core):
        raise ValueError("Invalid phone_number (must include at least one digit)")

    # Keep '+' only if it was leading; remove any other '+' via regex above.
    return f"+{core}" if has_plus else core


def _normalize_header_key(raw: str) -> str:
    return (raw or "").strip().lower().replace(" ", "_")


def _optional_timezone_or_error(raw: Optional[str]) -> Optional[str]:
    tz = (raw or "").strip()
    if not tz:
        return None
    return _validate_iana_timezone_name(tz)


def _validate_iana_timezone_name(tz_name: str) -> str:
    """
    Enforce IANA timezone names like 'America/Phoenix' (not 'Phoenix').
    """
    tz_name = (tz_name or "").strip()
    if not tz_name:
        raise ValueError("timezone is required")
    if tz_name.upper() == "UTC":
        return "UTC"
    if ZoneInfo is None:
        # Should not happen in our container images, but avoid hard failure.
        return tz_name
    try:
        ZoneInfo(tz_name)
    except Exception:
        raise ValueError(f"Invalid timezone '{tz_name}'. Use an IANA timezone like 'America/Phoenix' or 'UTC'.")
    return tz_name


@dataclass(frozen=True)
class ImportErrorRow:
    row_number: int
    phone_number: str
    error_reason: str


class OutboundStore:
    _CREATE_TABLES_SQL = [
        """
        CREATE TABLE IF NOT EXISTS outbound_campaigns (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft', -- draft|running|paused|stopped|archived
            timezone TEXT NOT NULL DEFAULT 'UTC',
            run_start_at_utc TEXT,
            run_end_at_utc TEXT,
            daily_window_start_local TEXT NOT NULL DEFAULT '09:00',
            daily_window_end_local TEXT NOT NULL DEFAULT '17:00',
            max_concurrent INTEGER NOT NULL DEFAULT 1,
            min_interval_seconds_between_calls INTEGER NOT NULL DEFAULT 5,
            default_context TEXT NOT NULL DEFAULT 'default',
            voicemail_drop_enabled INTEGER NOT NULL DEFAULT 1,
            voicemail_drop_mode TEXT NOT NULL DEFAULT 'upload', -- upload|tts
            voicemail_drop_text TEXT,
            voicemail_drop_media_uri TEXT,
            consent_enabled INTEGER NOT NULL DEFAULT 0,
            consent_media_uri TEXT,
            consent_timeout_seconds INTEGER NOT NULL DEFAULT 5,
            amd_options_json TEXT NOT NULL DEFAULT '{}',
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_outbound_campaigns_status ON outbound_campaigns(status)",
        """
        CREATE TABLE IF NOT EXISTS outbound_leads (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            name TEXT,
            phone_number TEXT NOT NULL,
            lead_timezone TEXT,
            context_override TEXT,
            caller_id_override TEXT,
            custom_vars_json TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT 'pending', -- pending|leased|dialing|amd_pending|in_progress|completed|failed|canceled
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_outcome TEXT,
            last_attempt_at_utc TEXT,
            leased_until_utc TEXT,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            UNIQUE(campaign_id, phone_number)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_outbound_leads_campaign_state ON outbound_leads(campaign_id, state)",
        "CREATE INDEX IF NOT EXISTS idx_outbound_leads_campaign_phone ON outbound_leads(campaign_id, phone_number)",
        """
        CREATE TABLE IF NOT EXISTS outbound_attempts (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            lead_id TEXT NOT NULL,
            started_at_utc TEXT NOT NULL,
            ended_at_utc TEXT,
            duration_seconds INTEGER,
            ari_channel_id TEXT,
            outcome TEXT,
            amd_status TEXT,
            amd_cause TEXT,
            consent_dtmf TEXT,
            consent_result TEXT,
            context TEXT,
            provider TEXT,
            call_history_call_id TEXT,
            error_message TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_outbound_attempts_campaign_started ON outbound_attempts(campaign_id, started_at_utc)",
        "CREATE INDEX IF NOT EXISTS idx_outbound_attempts_lead_started ON outbound_attempts(lead_id, started_at_utc)",
    ]

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or os.getenv("CALL_HISTORY_DB_PATH", "data/call_history.db")
        self._enabled = str(os.getenv("CALL_HISTORY_ENABLED", "true")).strip().lower() not in ("0", "false", "no")
        self._lock = threading.Lock()
        self._initialized = False

        if self._enabled:
            self._init_db()

    def _init_db(self) -> None:
        try:
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                Path(db_dir).mkdir(parents=True, exist_ok=True)
            with self._lock:
                conn = self._get_connection()
                try:
                    cur = conn.cursor()
                    for stmt in self._CREATE_TABLES_SQL:
                        cur.execute(stmt)
                    self._ensure_schema_sync(conn)
                    conn.commit()
                    self._initialized = True
                    logger.info("Outbound dialer tables initialized", db_path=self._db_path)
                finally:
                    conn.close()
        except Exception as exc:
            logger.error("Failed to initialize outbound tables", error=str(exc), exc_info=True)
            self._enabled = False

    def _ensure_schema_sync(self, conn: sqlite3.Connection) -> None:
        """
        Best-effort schema migrations for existing installs.

        SQLite has limited ALTER TABLE support; we add new columns when missing.
        """
        try:
            cur = conn.cursor()

            def _cols(table: str) -> set[str]:
                rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
                return {str(r[1]) for r in rows}  # (cid, name, type, notnull, dflt_value, pk)

            # outbound_campaigns
            ccols = _cols("outbound_campaigns")
            if "voicemail_drop_enabled" not in ccols:
                cur.execute("ALTER TABLE outbound_campaigns ADD COLUMN voicemail_drop_enabled INTEGER NOT NULL DEFAULT 1")
            if "consent_enabled" not in ccols:
                cur.execute("ALTER TABLE outbound_campaigns ADD COLUMN consent_enabled INTEGER NOT NULL DEFAULT 0")
            if "consent_media_uri" not in ccols:
                cur.execute("ALTER TABLE outbound_campaigns ADD COLUMN consent_media_uri TEXT")
            if "consent_timeout_seconds" not in ccols:
                cur.execute("ALTER TABLE outbound_campaigns ADD COLUMN consent_timeout_seconds INTEGER NOT NULL DEFAULT 5")

            # outbound_leads
            lcols = _cols("outbound_leads")
            if "name" not in lcols:
                cur.execute("ALTER TABLE outbound_leads ADD COLUMN name TEXT")

            # outbound_attempts
            acols = _cols("outbound_attempts")
            if "duration_seconds" not in acols:
                cur.execute("ALTER TABLE outbound_attempts ADD COLUMN duration_seconds INTEGER")
            if "consent_dtmf" not in acols:
                cur.execute("ALTER TABLE outbound_attempts ADD COLUMN consent_dtmf TEXT")
            if "consent_result" not in acols:
                cur.execute("ALTER TABLE outbound_attempts ADD COLUMN consent_result TEXT")
            if "context" not in acols:
                cur.execute("ALTER TABLE outbound_attempts ADD COLUMN context TEXT")
            if "provider" not in acols:
                cur.execute("ALTER TABLE outbound_attempts ADD COLUMN provider TEXT")
        except Exception:
            # Never fail startup due to a best-effort migration.
            logger.debug("Outbound schema migration failed (non-fatal)", exc_info=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    async def _run(self, fn):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn)

    # ---------------------------------------------------------------------
    # Campaigns
    # ---------------------------------------------------------------------

    async def create_campaign(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._enabled:
            raise RuntimeError("OutboundStore disabled (CALL_HISTORY_ENABLED=false)")

        def _sync():
            now = _utcnow_iso()
            campaign_id = str(uuid.uuid4())
            name = _as_str(payload.get("name")).strip() or "Untitled Campaign"
            timezone_name = _validate_iana_timezone_name(_as_str(payload.get("timezone")).strip() or "UTC")
            daily_start = _as_str(payload.get("daily_window_start_local")).strip() or "09:00"
            daily_end = _as_str(payload.get("daily_window_end_local")).strip() or "17:00"
            max_concurrent = max(1, min(5, _as_int(payload.get("max_concurrent"), 1)))
            min_interval = max(0, _as_int(payload.get("min_interval_seconds_between_calls"), 5))
            default_context = _as_str(payload.get("default_context")).strip() or "default"
            vm_enabled = 1 if bool(payload.get("voicemail_drop_enabled", True)) else 0
            vm_mode = _as_str(payload.get("voicemail_drop_mode")).strip() or "upload"
            vm_text = _as_str(payload.get("voicemail_drop_text")).strip() or None
            vm_uri = _as_str(payload.get("voicemail_drop_media_uri")).strip() or None
            consent_enabled = 1 if bool(payload.get("consent_enabled", False)) else 0
            consent_uri = _as_str(payload.get("consent_media_uri")).strip() or None
            consent_timeout = max(1, min(30, _as_int(payload.get("consent_timeout_seconds"), 5)))
            amd_opts = payload.get("amd_options") if isinstance(payload.get("amd_options"), dict) else {}

            with self._lock:
                conn = self._get_connection()
                try:
                    conn.execute(
                        """
                        INSERT INTO outbound_campaigns (
                            id, name, status, timezone, run_start_at_utc, run_end_at_utc,
                            daily_window_start_local, daily_window_end_local,
                            max_concurrent, min_interval_seconds_between_calls,
                            default_context,
                            voicemail_drop_enabled, voicemail_drop_mode, voicemail_drop_text,
                            voicemail_drop_media_uri,
                            consent_enabled, consent_media_uri, consent_timeout_seconds,
                            amd_options_json,
                            created_at_utc, updated_at_utc
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?,
                            ?, ?,
                            ?, ?,
                            ?,
                            ?, ?, ?,
                            ?,
                            ?, ?, ?,
                            ?,
                            ?, ?
                        )
                        """,
                        (
                            campaign_id,
                            name,
                            "draft",
                            timezone_name,
                            payload.get("run_start_at_utc"),
                            payload.get("run_end_at_utc"),
                            daily_start,
                            daily_end,
                            max_concurrent,
                            min_interval,
                            default_context,
                            vm_enabled,
                            vm_mode,
                            vm_text,
                            vm_uri,
                            consent_enabled,
                            consent_uri,
                            consent_timeout,
                            json.dumps(amd_opts or {}),
                            now,
                            now,
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
            return self.get_campaign_sync(campaign_id)

        return await self._run(_sync)

    def get_campaign_sync(self, campaign_id: str) -> Dict[str, Any]:
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT * FROM outbound_campaigns WHERE id = ?", (campaign_id,)).fetchone()
                if not row:
                    raise KeyError("campaign not found")
                d = dict(row)
                d["amd_options"] = _safe_json_loads(str(d.get("amd_options_json") or "{}"))
                d.pop("amd_options_json", None)
                return d
            finally:
                conn.close()

    async def get_campaign(self, campaign_id: str) -> Dict[str, Any]:
        return await self._run(lambda: self.get_campaign_sync(campaign_id))

    async def list_campaigns(self, *, include_archived: bool = False) -> List[Dict[str, Any]]:
        if not self._enabled:
            return []

        def _sync():
            clauses = []
            args: List[Any] = []
            if not include_archived:
                clauses.append("status != 'archived'")
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

            with self._lock:
                conn = self._get_connection()
                try:
                    rows = conn.execute(
                        f"SELECT * FROM outbound_campaigns {where} ORDER BY created_at_utc DESC",
                        args,
                    ).fetchall()
                    out: List[Dict[str, Any]] = []
                    for r in rows:
                        d = dict(r)
                        d["amd_options"] = _safe_json_loads(str(d.get("amd_options_json") or "{}"))
                        d.pop("amd_options_json", None)
                        out.append(d)
                    return out
                finally:
                    conn.close()

        return await self._run(_sync)

    async def list_running_campaigns(self) -> List[Dict[str, Any]]:
        """Return campaigns with status=running (lightweight filter for scheduler)."""
        campaigns = await self.list_campaigns(include_archived=False)
        return [c for c in campaigns if (str(c.get("status") or "").lower() == "running")]

    async def update_campaign(self, campaign_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._enabled:
            raise RuntimeError("OutboundStore disabled")

        def _sync():
            now = _utcnow_iso()
            allowed_fields = {
                "name",
                "timezone",
                "run_start_at_utc",
                "run_end_at_utc",
                "daily_window_start_local",
                "daily_window_end_local",
                "max_concurrent",
                "min_interval_seconds_between_calls",
                "default_context",
                "voicemail_drop_enabled",
                "voicemail_drop_mode",
                "voicemail_drop_text",
                "voicemail_drop_media_uri",
                "consent_enabled",
                "consent_media_uri",
                "consent_timeout_seconds",
                "amd_options_json",
            }

            updates: Dict[str, Any] = {}
            for key in allowed_fields:
                if key in payload:
                    updates[key] = payload[key]
            if "amd_options" in payload and isinstance(payload.get("amd_options"), dict):
                updates["amd_options_json"] = json.dumps(payload.get("amd_options") or {})

            if "timezone" in updates:
                updates["timezone"] = _validate_iana_timezone_name(_as_str(updates.get("timezone")).strip() or "UTC")

            if "max_concurrent" in updates:
                updates["max_concurrent"] = max(1, min(5, _as_int(updates.get("max_concurrent"), 1)))
            if "min_interval_seconds_between_calls" in updates:
                updates["min_interval_seconds_between_calls"] = max(
                    0, _as_int(updates.get("min_interval_seconds_between_calls"), 5)
                )
            if "voicemail_drop_enabled" in updates:
                updates["voicemail_drop_enabled"] = 1 if bool(updates.get("voicemail_drop_enabled")) else 0
            if "consent_enabled" in updates:
                updates["consent_enabled"] = 1 if bool(updates.get("consent_enabled")) else 0
            if "consent_timeout_seconds" in updates:
                updates["consent_timeout_seconds"] = max(1, min(30, _as_int(updates.get("consent_timeout_seconds"), 5)))

            updates["updated_at_utc"] = now

            if not updates:
                return self.get_campaign_sync(campaign_id)

            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [campaign_id]

            with self._lock:
                conn = self._get_connection()
                try:
                    cur = conn.execute(
                        f"UPDATE outbound_campaigns SET {set_clause} WHERE id = ?",
                        values,
                    )
                    if cur.rowcount == 0:
                        raise KeyError("campaign not found")
                    conn.commit()
                finally:
                    conn.close()

            return self.get_campaign_sync(campaign_id)

        return await self._run(_sync)

    async def set_campaign_status(self, campaign_id: str, status: str, *, cancel_pending: bool = False) -> Dict[str, Any]:
        if not self._enabled:
            raise RuntimeError("OutboundStore disabled")

        status = (status or "").strip().lower()
        if status not in ("draft", "running", "paused", "stopped", "archived", "completed"):
            raise ValueError("invalid status")

        def _sync():
            now = _utcnow_iso()
            with self._lock:
                conn = self._get_connection()
                try:
                    cur = conn.execute(
                        "UPDATE outbound_campaigns SET status = ?, updated_at_utc = ? WHERE id = ?",
                        (status, now, campaign_id),
                    )
                    if cur.rowcount == 0:
                        raise KeyError("campaign not found")
                    if status == "stopped" and cancel_pending:
                        conn.execute(
                            """
                            UPDATE outbound_leads
                            SET state = 'canceled', updated_at_utc = ?
                            WHERE campaign_id = ? AND state = 'pending'
                            """,
                            (now, campaign_id),
                        )
                    conn.commit()
                finally:
                    conn.close()
            return self.get_campaign_sync(campaign_id)

        return await self._run(_sync)

    async def delete_campaign(self, campaign_id: str) -> None:
        """
        Permanently delete a campaign and all associated leads/attempts.

        This is intentionally destructive and cannot be undone.
        """
        if not self._enabled:
            raise RuntimeError("OutboundStore disabled")

        def _sync():
            campaign = self.get_campaign_sync(campaign_id)
            if str(campaign.get("status") or "").lower() == "running":
                raise ValueError("cannot delete a running campaign")

            with self._lock:
                conn = self._get_connection()
                try:
                    cur = conn.cursor()
                    cur.execute("BEGIN IMMEDIATE")
                    cur.execute("DELETE FROM outbound_attempts WHERE campaign_id = ?", (campaign_id,))
                    cur.execute("DELETE FROM outbound_leads WHERE campaign_id = ?", (campaign_id,))
                    cur.execute("DELETE FROM outbound_campaigns WHERE id = ?", (campaign_id,))
                    if cur.rowcount == 0:
                        raise KeyError("campaign not found")
                    conn.commit()
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    raise
                finally:
                    conn.close()

        return await self._run(_sync)

    async def clone_campaign(self, campaign_id: str) -> Dict[str, Any]:
        original = await self.get_campaign(campaign_id)
        payload = dict(original)
        payload.pop("id", None)
        payload.pop("created_at_utc", None)
        payload.pop("updated_at_utc", None)
        payload.pop("status", None)
        payload["name"] = f"{original.get('name') or 'Campaign'} (Copy)"
        return await self.create_campaign(payload)

    async def cleanup_stale_attempts_and_leads(self, *, stale_seconds: int = 120) -> Dict[str, int]:
        """
        Best-effort cleanup for attempts/leads stuck due to restarts or pre-answer failures.

        - Finalizes outbound_attempts where ended_at_utc is NULL and started_at_utc is older than stale_seconds.
        - Marks associated leads as failed if they are stuck in dialing/leased/amd_pending/in_progress.
        """
        if not self._enabled:
            return {"attempts_closed": 0, "leads_failed": 0}

        def _sync():
            now = _utcnow_iso()
            cutoff_dt = datetime.now(timezone.utc) - timedelta(seconds=max(10, int(stale_seconds or 120)))
            attempts_closed = 0
            leads_failed = 0

            with self._lock:
                conn = self._get_connection()
                try:
                    rows = conn.execute(
                        """
                        SELECT id, lead_id, started_at_utc
                        FROM outbound_attempts
                        WHERE ended_at_utc IS NULL
                        """
                    ).fetchall()
                    for r in rows:
                        started_raw = str(r["started_at_utc"] or "")
                        try:
                            started_dt = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
                            if started_dt.tzinfo is None:
                                started_dt = started_dt.replace(tzinfo=timezone.utc)
                        except Exception:
                            started_dt = datetime.fromtimestamp(0, tz=timezone.utc)
                        if started_dt >= cutoff_dt:
                            continue

                        attempt_id = str(r["id"])
                        lead_id = str(r["lead_id"])
                        conn.execute(
                            """
                            UPDATE outbound_attempts
                            SET ended_at_utc = ?,
                                outcome = COALESCE(outcome, 'error'),
                                error_message = COALESCE(error_message, 'stale attempt cleanup (engine restart or pre-answer failure)')
                            WHERE id = ? AND ended_at_utc IS NULL
                            """,
                            (now, attempt_id),
                        )
                        attempts_closed += 1

                        cur = conn.execute(
                            """
                            UPDATE outbound_leads
                            SET state='failed',
                                last_outcome=COALESCE(last_outcome, 'error'),
                                leased_until_utc=NULL,
                                updated_at_utc=?
                            WHERE id = ?
                              AND state IN ('dialing','leased','amd_pending','in_progress')
                            """,
                            (now, lead_id),
                        )
                        leads_failed += int(cur.rowcount or 0)

                    conn.commit()
                finally:
                    conn.close()

            return {"attempts_closed": attempts_closed, "leads_failed": leads_failed}

        return await self._run(_sync)

    # ---------------------------------------------------------------------
    # Leads
    # ---------------------------------------------------------------------

    async def lease_pending_leads(
        self,
        campaign_id: str,
        *,
        limit: int,
        lease_seconds: int = 60,
    ) -> List[Dict[str, Any]]:
        """
        Atomically lease up to N pending leads.

        Notes:
        - This avoids reliance on SQLite RETURNING to be compatible with older distros.
        - Leases expire via leased_until_utc; expired leased leads are eligible again.
        """
        if not self._enabled:
            return []

        def _sync():
            now_dt = datetime.now(timezone.utc)
            now = now_dt.isoformat()
            lease_until = (now_dt + timedelta(seconds=max(1, int(lease_seconds or 60)))).isoformat()
            batch = max(0, min(200, int(limit or 0)))
            if batch <= 0:
                return []

            with self._lock:
                conn = self._get_connection()
                try:
                    cur = conn.cursor()
                    cur.execute("BEGIN IMMEDIATE")
                    rows = cur.execute(
                        """
                        SELECT id
                        FROM outbound_leads
                        WHERE campaign_id = ?
                          AND (
                            state = 'pending'
                            OR (state = 'leased' AND leased_until_utc IS NOT NULL AND leased_until_utc < ?)
                          )
                        ORDER BY created_at_utc ASC
                        LIMIT ?
                        """,
                        (campaign_id, now, batch),
                    ).fetchall()
                    lead_ids = [str(r["id"]) for r in rows]
                    if not lead_ids:
                        conn.commit()
                        return []

                    placeholders = ",".join(["?"] * len(lead_ids))
                    cur.execute(
                        f"""
                        UPDATE outbound_leads
                        SET state = 'leased',
                            leased_until_utc = ?,
                            updated_at_utc = ?
                        WHERE id IN ({placeholders})
                        """,
                        [lease_until, now, *lead_ids],
                    )
                    conn.commit()

                    data_rows = conn.execute(
                        f"SELECT * FROM outbound_leads WHERE id IN ({placeholders})",
                        lead_ids,
                    ).fetchall()
                    by_id = {str(r["id"]): dict(r) for r in data_rows}
                    out: List[Dict[str, Any]] = []
                    for lead_id in lead_ids:
                        d = by_id.get(lead_id)
                        if not d:
                            continue
                        d["custom_vars"] = _safe_json_loads(str(d.get("custom_vars_json") or "{}"))
                        d.pop("custom_vars_json", None)
                        out.append(d)
                    return out
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    raise
                finally:
                    conn.close()

        return await self._run(_sync)

    async def mark_lead_dialing(self, lead_id: str) -> bool:
        """Transition a lead from leased -> dialing and increment attempt_count."""
        if not self._enabled:
            return False

        def _sync():
            now = _utcnow_iso()
            with self._lock:
                conn = self._get_connection()
                try:
                    cur = conn.execute(
                        """
                        UPDATE outbound_leads
                        SET state='dialing',
                            attempt_count=attempt_count+1,
                            last_attempt_at_utc=?,
                            leased_until_utc=NULL,
                            updated_at_utc=?
                        WHERE id=? AND state='leased'
                        """,
                        (now, now, lead_id),
                    )
                    conn.commit()
                    return cur.rowcount > 0
                finally:
                    conn.close()

        return await self._run(_sync)

    async def set_lead_state(
        self,
        lead_id: str,
        *,
        state: str,
        last_outcome: Optional[str] = None,
    ) -> None:
        if not self._enabled:
            return

        state = (state or "").strip().lower()
        allowed = {
            "pending",
            "leased",
            "dialing",
            "amd_pending",
            "in_progress",
            "completed",
            "failed",
            "canceled",
        }
        if state not in allowed:
            raise ValueError("invalid lead state")

        def _sync():
            now = _utcnow_iso()
            with self._lock:
                conn = self._get_connection()
                try:
                    conn.execute(
                        """
                        UPDATE outbound_leads
                        SET state=?,
                            last_outcome=COALESCE(?, last_outcome),
                            leased_until_utc=NULL,
                            updated_at_utc=?
                        WHERE id=?
                        """,
                        (state, last_outcome, now, lead_id),
                    )
                    conn.commit()
                finally:
                    conn.close()

        await self._run(_sync)

    async def import_leads_csv(
        self,
        campaign_id: str,
        csv_bytes: bytes,
        *,
        skip_existing: bool = True,
        max_error_rows: int = 20,
    ) -> Dict[str, Any]:
        """
        Import leads for a campaign.

        Expected columns:
          - name (optional; stored on lead and used for caller_name in outbound greeting)
          - phone_number (required)
          - custom_vars (optional JSON)
          - context (optional)
          - timezone (optional)
          - caller_id (optional; stored but MVP uses extension identity)
        """
        if not self._enabled:
            raise RuntimeError("OutboundStore disabled")

        def _sync():
            now = _utcnow_iso()
            accepted = 0
            rejected = 0
            duplicates = 0
            errors: List[ImportErrorRow] = []

            # CSV decode
            text = (csv_bytes or b"").decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames:
                raise ValueError("CSV missing header row")

            normalized_to_raw: Dict[str, str] = {}
            for h in (reader.fieldnames or []):
                if not h:
                    continue
                normalized = _normalize_header_key(h)
                if normalized:
                    normalized_to_raw[normalized] = h

            phone_key = (
                normalized_to_raw.get("phone_number")
                or normalized_to_raw.get("phone")
                or normalized_to_raw.get("number")
            )
            if not phone_key:
                raise ValueError("CSV must include 'phone_number' column")

            custom_vars_key = normalized_to_raw.get("custom_vars")
            context_key = normalized_to_raw.get("context")
            tz_key = normalized_to_raw.get("timezone")
            caller_id_key = normalized_to_raw.get("caller_id")
            name_key = normalized_to_raw.get("name")

            with self._lock:
                conn = self._get_connection()
                try:
                    for idx, row in enumerate(reader, start=2):  # header is row 1
                        raw_phone = _as_str((row or {}).get(phone_key)).strip()
                        try:
                            phone = _normalize_phone_number(raw_phone)
                        except Exception as exc:
                            rejected += 1
                            if len(errors) < max_error_rows:
                                errors.append(ImportErrorRow(idx, (raw_phone or ""), str(exc)))
                            continue

                        custom_vars_raw = _as_str((row or {}).get(custom_vars_key)).strip() if custom_vars_key else ""
                        if custom_vars_raw:
                            try:
                                custom_vars = json.loads(custom_vars_raw)
                                if not isinstance(custom_vars, dict):
                                    raise ValueError("custom_vars must be a JSON object")
                            except Exception as exc:
                                rejected += 1
                                if len(errors) < max_error_rows:
                                    errors.append(ImportErrorRow(idx, phone, f"Invalid custom_vars JSON: {exc}"))
                                continue
                        else:
                            custom_vars = {}

                        context_override = _as_str((row or {}).get(context_key)).strip() if context_key else ""
                        context_override = context_override or None
                        if context_override:
                            if not re.match(r"^[a-zA-Z0-9_.-]{1,64}$", context_override):
                                rejected += 1
                                if len(errors) < max_error_rows:
                                    errors.append(ImportErrorRow(idx, phone, "Invalid context (use letters/numbers/._- only)"))
                                continue

                        tz_override_raw = _as_str((row or {}).get(tz_key)).strip() if tz_key else ""
                        try:
                            tz_override = _optional_timezone_or_error(tz_override_raw)
                        except Exception as exc:
                            rejected += 1
                            if len(errors) < max_error_rows:
                                errors.append(ImportErrorRow(idx, phone, str(exc)))
                            continue

                        caller_id_override = _as_str((row or {}).get(caller_id_key)).strip() if caller_id_key else ""
                        caller_id_override = caller_id_override or None
                        lead_name = _as_str((row or {}).get(name_key)).strip() if name_key else ""
                        lead_name = lead_name or None

                        lead_id = str(uuid.uuid4())
                        try:
                            conn.execute(
                                """
                                INSERT INTO outbound_leads (
                                    id, campaign_id, name, phone_number,
                                    lead_timezone, context_override, caller_id_override,
                                    custom_vars_json, state,
                                    attempt_count, created_at_utc, updated_at_utc
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                                """,
                                (
                                    lead_id,
                                    campaign_id,
                                    lead_name,
                                    phone,
                                    tz_override,
                                    context_override,
                                    caller_id_override,
                                    json.dumps(custom_vars or {}),
                                    now,
                                    now,
                                ),
                            )
                            accepted += 1
                        except sqlite3.IntegrityError:
                            duplicates += 1
                            if skip_existing:
                                continue
                            # update_existing (optional)
                            conn.execute(
                                """
                                UPDATE outbound_leads
                                SET name = COALESCE(?, name),
                                    lead_timezone = COALESCE(?, lead_timezone),
                                    context_override = COALESCE(?, context_override),
                                    caller_id_override = COALESCE(?, caller_id_override),
                                    custom_vars_json = ?,
                                    updated_at_utc = ?
                                WHERE campaign_id = ? AND phone_number = ?
                                """,
                                (
                                    lead_name,
                                    tz_override,
                                    context_override,
                                    caller_id_override,
                                    json.dumps(custom_vars or {}),
                                    now,
                                    campaign_id,
                                    phone,
                                ),
                            )
                    conn.commit()
                finally:
                    conn.close()

            error_csv_value = ""
            if errors:
                error_csv = io.StringIO()
                w = csv.writer(error_csv)
                w.writerow(["row_number", "phone_number", "error_reason"])
                for e in errors:
                    w.writerow([e.row_number, e.phone_number, e.error_reason])
                error_csv_value = error_csv.getvalue()

            return {
                "accepted": accepted,
                "rejected": rejected,
                "duplicates": duplicates,
                "errors": [e.__dict__ for e in errors],
                "error_csv": error_csv_value,
                "error_csv_truncated": rejected > len(errors),
            }

        return await self._run(_sync)

    async def list_leads(
        self,
        campaign_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
        state: Optional[str] = None,
        q: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self._enabled:
            return {"leads": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

        def _sync():
            page_i = max(1, int(page or 1))
            size_i = max(1, min(200, int(page_size or 50)))
            offset = (page_i - 1) * size_i

            clauses = ["l.campaign_id = ?"]
            args: List[Any] = [campaign_id]

            if state:
                clauses.append("l.state = ?")
                args.append(state)
            if q:
                clauses.append("(l.phone_number LIKE ? OR COALESCE(l.name,'') LIKE ?)")
                args.append(f"%{q}%")
                args.append(f"%{q}%")

            where = " AND ".join(clauses)
            with self._lock:
                conn = self._get_connection()
                try:
                    total = conn.execute(
                        f"SELECT COUNT(*) AS c FROM outbound_leads l WHERE {where}",
                        args,
                    ).fetchone()["c"]
                    rows = conn.execute(
                        f"""
                        SELECT
                            l.*,
                            a.started_at_utc AS last_started_at_utc,
                            a.ended_at_utc AS last_ended_at_utc,
                            a.duration_seconds AS last_duration_seconds,
                            a.outcome AS last_outcome_attempt,
                            a.amd_status AS last_amd_status,
                            a.amd_cause AS last_amd_cause,
                            a.consent_dtmf AS last_consent_dtmf,
                            a.consent_result AS last_consent_result,
                            a.context AS last_context,
                            a.provider AS last_provider,
                            a.call_history_call_id AS last_call_history_call_id,
                            a.error_message AS last_error_message
                        FROM outbound_leads l
                        LEFT JOIN outbound_attempts a
                          ON a.id = (
                            SELECT id
                            FROM outbound_attempts
                            WHERE lead_id = l.id
                            ORDER BY started_at_utc DESC
                            LIMIT 1
                          )
                        WHERE {where}
                        ORDER BY l.created_at_utc DESC
                        LIMIT ? OFFSET ?
                        """,
                        args + [size_i, offset],
                    ).fetchall()
                    out = []
                    for r in rows:
                        d = dict(r)
                        d["custom_vars"] = _safe_json_loads(str(d.get("custom_vars_json") or "{}"))
                        d.pop("custom_vars_json", None)
                        out.append(d)
                    total_pages = (total + size_i - 1) // size_i
                    return {"leads": out, "total": total, "page": page_i, "page_size": size_i, "total_pages": total_pages}
                finally:
                    conn.close()

        return await self._run(_sync)

    async def cancel_lead(self, lead_id: str) -> bool:
        if not self._enabled:
            return False

        def _sync():
            now = _utcnow_iso()
            with self._lock:
                conn = self._get_connection()
                try:
                    cur = conn.execute(
                        """
                        UPDATE outbound_leads
                        SET state='canceled', updated_at_utc=?
                        WHERE id=? AND state IN ('pending','leased','dialing','amd_pending')
                        """,
                        (now, lead_id),
                    )
                    conn.commit()
                    return cur.rowcount > 0
                finally:
                    conn.close()

        return await self._run(_sync)

    async def ignore_lead(self, lead_id: str) -> bool:
        """
        Mark a lead as ignored (state=canceled). Reversible via recycle.

        This is the operator-facing "Ignore" action in the Scheduling UI.
        """
        if not self._enabled:
            return False

        def _sync():
            now = _utcnow_iso()
            with self._lock:
                conn = self._get_connection()
                try:
                    cur = conn.execute(
                        """
                        UPDATE outbound_leads
                        SET state='canceled',
                            leased_until_utc=NULL,
                            updated_at_utc=?
                        WHERE id=? AND state NOT IN ('in_progress','amd_pending')
                        """,
                        (now, lead_id),
                    )
                    conn.commit()
                    return cur.rowcount > 0
                finally:
                    conn.close()

        return await self._run(_sync)

    async def recycle_lead(self, lead_id: str, *, mode: str = "redial") -> bool:
        """
        Re-queue a lead by moving it back to 'pending'.

        MVP policy:
        - Allowed from canceled/failed/completed (manual retry only; no automatic retries in v1).
        - attempt_count is preserved; attempts table remains the audit trail.
        """
        if not self._enabled:
            return False

        def _sync():
            now = _utcnow_iso()
            with self._lock:
                conn = self._get_connection()
                try:
                    m = (mode or "redial").strip().lower()
                    if m == "reset":
                        # Reset completely: delete attempts and reset lead counters/state.
                        conn.execute("DELETE FROM outbound_attempts WHERE lead_id = ?", (lead_id,))
                        cur = conn.execute(
                            """
                            UPDATE outbound_leads
                            SET state='pending',
                                attempt_count=0,
                                last_outcome=NULL,
                                last_attempt_at_utc=NULL,
                                leased_until_utc=NULL,
                                updated_at_utc=?
                            WHERE id=?
                            """,
                            (now, lead_id),
                        )
                    else:
                        # Re-dial: keep attempts/history; requeue lead.
                        cur = conn.execute(
                            """
                            UPDATE outbound_leads
                            SET state='pending',
                                last_outcome=NULL,
                                leased_until_utc=NULL,
                                updated_at_utc=?
                            WHERE id=?
                            """,
                            (now, lead_id),
                        )
                    conn.commit()
                    return cur.rowcount > 0
                finally:
                    conn.close()

        return await self._run(_sync)

    async def delete_lead(self, lead_id: str) -> None:
        """
        Hard delete a lead and all its attempts.

        The API layer should block this while a campaign is running.
        """
        if not self._enabled:
            raise RuntimeError("OutboundStore disabled")

        def _sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    row = conn.execute(
                        "SELECT campaign_id FROM outbound_leads WHERE id=?",
                        (lead_id,),
                    ).fetchone()
                    if not row:
                        raise KeyError("lead not found")
                    campaign_id = str(row["campaign_id"])
                    camp = conn.execute(
                        "SELECT status FROM outbound_campaigns WHERE id=?",
                        (campaign_id,),
                    ).fetchone()
                    if camp and str(camp["status"] or "").strip().lower() == "running":
                        raise ValueError("Pause/stop the campaign before deleting leads")

                    conn.execute("DELETE FROM outbound_attempts WHERE lead_id=?", (lead_id,))
                    conn.execute("DELETE FROM outbound_leads WHERE id=?", (lead_id,))
                    conn.commit()
                finally:
                    conn.close()

        await self._run(_sync)

    async def campaign_stats(self, campaign_id: str) -> Dict[str, Any]:
        if not self._enabled:
            return {}

        def _sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    lead_rows = conn.execute(
                        "SELECT state, COUNT(*) AS c FROM outbound_leads WHERE campaign_id=? GROUP BY state",
                        (campaign_id,),
                    ).fetchall()
                    attempt_rows = conn.execute(
                        "SELECT outcome, COUNT(*) AS c FROM outbound_attempts WHERE campaign_id=? GROUP BY outcome",
                        (campaign_id,),
                    ).fetchall()
                    return {
                        "lead_states": {str(r["state"]): int(r["c"]) for r in lead_rows},
                        "attempt_outcomes": {str(r["outcome"]): int(r["c"]) for r in attempt_rows if r["outcome"] is not None},
                    }
                finally:
                    conn.close()

        return await self._run(_sync)

    # ---------------------------------------------------------------------
    # Attempts
    # ---------------------------------------------------------------------

    async def list_attempts(
        self,
        campaign_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        if not self._enabled:
            return {"attempts": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

        def _sync():
            page_i = max(1, int(page or 1))
            size_i = max(1, min(200, int(page_size or 50)))
            offset = (page_i - 1) * size_i

            with self._lock:
                conn = self._get_connection()
                try:
                    total = conn.execute(
                        "SELECT COUNT(*) AS c FROM outbound_attempts WHERE campaign_id=?",
                        (campaign_id,),
                    ).fetchone()["c"]
                    rows = conn.execute(
                        """
                        SELECT a.*, l.phone_number, l.name
                        FROM outbound_attempts a
                        LEFT JOIN outbound_leads l ON l.id = a.lead_id
                        WHERE a.campaign_id=?
                        ORDER BY a.started_at_utc DESC
                        LIMIT ? OFFSET ?
                        """,
                        (campaign_id, size_i, offset),
                    ).fetchall()
                    out = [dict(r) for r in rows]
                    total_pages = (total + size_i - 1) // size_i
                    return {"attempts": out, "total": total, "page": page_i, "page_size": size_i, "total_pages": total_pages}
                finally:
                    conn.close()

        return await self._run(_sync)

    async def create_attempt(
        self,
        campaign_id: str,
        lead_id: str,
        *,
        context: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> str:
        if not self._enabled:
            raise RuntimeError("OutboundStore disabled")

        def _sync():
            attempt_id = str(uuid.uuid4())
            now = _utcnow_iso()
            with self._lock:
                conn = self._get_connection()
                try:
                    conn.execute(
                        """
                        INSERT INTO outbound_attempts (id, campaign_id, lead_id, started_at_utc, context, provider)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (attempt_id, campaign_id, lead_id, now, context, provider),
                    )
                    conn.commit()
                finally:
                    conn.close()
            return attempt_id

        return await self._run(_sync)

    async def set_attempt_channel(self, attempt_id: str, channel_id: str) -> None:
        if not self._enabled:
            return

        def _sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    conn.execute(
                        "UPDATE outbound_attempts SET ari_channel_id=? WHERE id=?",
                        (channel_id, attempt_id),
                    )
                    conn.commit()
                finally:
                    conn.close()

        await self._run(_sync)

    async def set_attempt_gate_result(
        self,
        attempt_id: str,
        *,
        amd_status: Optional[str] = None,
        amd_cause: Optional[str] = None,
        consent_dtmf: Optional[str] = None,
        consent_result: Optional[str] = None,
        context: Optional[str] = None,
        provider: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Persist mid-call classification data (AMD + consent) without finalizing the attempt.

        Used so the UI can display last AMD/DTMF while a call is still in progress.
        """
        if not self._enabled:
            return

        def _sync():
            with self._lock:
                conn = self._get_connection()
                try:
                    conn.execute(
                        """
                        UPDATE outbound_attempts
                        SET amd_status=COALESCE(?, amd_status),
                            amd_cause=COALESCE(?, amd_cause),
                            consent_dtmf=COALESCE(?, consent_dtmf),
                            consent_result=COALESCE(?, consent_result),
                            context=COALESCE(?, context),
                            provider=COALESCE(?, provider),
                            error_message=COALESCE(?, error_message)
                        WHERE id=? AND ended_at_utc IS NULL
                        """,
                        (
                            amd_status,
                            amd_cause,
                            consent_dtmf,
                            consent_result,
                            context,
                            provider,
                            error_message,
                            attempt_id,
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()

        await self._run(_sync)

    async def finish_attempt(
        self,
        attempt_id: str,
        *,
        outcome: str,
        amd_status: Optional[str] = None,
        amd_cause: Optional[str] = None,
        consent_dtmf: Optional[str] = None,
        consent_result: Optional[str] = None,
        context: Optional[str] = None,
        provider: Optional[str] = None,
        call_history_call_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        if not self._enabled:
            return

        def _sync():
            now_dt = datetime.now(timezone.utc)
            now = now_dt.isoformat()
            with self._lock:
                conn = self._get_connection()
                try:
                    # Best-effort duration in seconds.
                    duration_seconds = None
                    try:
                        row = conn.execute(
                            "SELECT started_at_utc FROM outbound_attempts WHERE id=?",
                            (attempt_id,),
                        ).fetchone()
                        if row and row["started_at_utc"]:
                            started = datetime.fromisoformat(str(row["started_at_utc"]))
                            if started.tzinfo is None:
                                started = started.replace(tzinfo=timezone.utc)
                            duration_seconds = max(0, int((now_dt - started).total_seconds()))
                    except Exception:
                        duration_seconds = None
                    conn.execute(
                        """
                        UPDATE outbound_attempts
                        SET ended_at_utc=?,
                            duration_seconds=COALESCE(?, duration_seconds),
                            outcome=?,
                            amd_status=?,
                            amd_cause=?,
                            consent_dtmf=COALESCE(?, consent_dtmf),
                            consent_result=COALESCE(?, consent_result),
                            context=COALESCE(?, context),
                            provider=COALESCE(?, provider),
                            call_history_call_id=?,
                            error_message=?
                        WHERE id=?
                        """,
                        (
                            now,
                            duration_seconds,
                            outcome,
                            amd_status,
                            amd_cause,
                            consent_dtmf,
                            consent_result,
                            context,
                            provider,
                            call_history_call_id,
                            error_message,
                            attempt_id,
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()

        await self._run(_sync)


_outbound_store: Optional[OutboundStore] = None


def get_outbound_store() -> OutboundStore:
    global _outbound_store
    if _outbound_store is None:
        _outbound_store = OutboundStore()
    return _outbound_store
