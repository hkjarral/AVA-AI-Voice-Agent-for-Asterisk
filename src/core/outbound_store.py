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
            voicemail_drop_mode TEXT NOT NULL DEFAULT 'upload', -- upload|tts
            voicemail_drop_text TEXT,
            voicemail_drop_media_uri TEXT,
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
            ari_channel_id TEXT,
            outcome TEXT,
            amd_status TEXT,
            amd_cause TEXT,
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
                    conn.commit()
                    self._initialized = True
                    logger.info("Outbound dialer tables initialized", db_path=self._db_path)
                finally:
                    conn.close()
        except Exception as exc:
            logger.error("Failed to initialize outbound tables", error=str(exc), exc_info=True)
            self._enabled = False

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
            vm_mode = _as_str(payload.get("voicemail_drop_mode")).strip() or "upload"
            vm_text = _as_str(payload.get("voicemail_drop_text")).strip() or None
            vm_uri = _as_str(payload.get("voicemail_drop_media_uri")).strip() or None
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
                            default_context, voicemail_drop_mode, voicemail_drop_text,
                            voicemail_drop_media_uri, amd_options_json,
                            created_at_utc, updated_at_utc
                        ) VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            campaign_id,
                            name,
                            timezone_name,
                            payload.get("run_start_at_utc"),
                            payload.get("run_end_at_utc"),
                            daily_start,
                            daily_end,
                            max_concurrent,
                            min_interval,
                            default_context,
                            vm_mode,
                            vm_text,
                            vm_uri,
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
                "voicemail_drop_mode",
                "voicemail_drop_text",
                "voicemail_drop_media_uri",
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
        if status not in ("draft", "running", "paused", "stopped", "archived"):
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
            text = (csv_bytes or b"").decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames:
                raise ValueError("CSV missing header row")

            # Normalize header keys
            headers = [h.strip() for h in (reader.fieldnames or []) if h and h.strip()]
            if "phone_number" not in headers:
                raise ValueError("CSV must include 'phone_number' column")

            with self._lock:
                conn = self._get_connection()
                try:
                    for idx, row in enumerate(reader, start=2):  # header is row 1
                        phone = _as_str((row or {}).get("phone_number")).strip()
                        if not phone:
                            rejected += 1
                            if len(errors) < max_error_rows:
                                errors.append(ImportErrorRow(idx, "", "Missing phone_number"))
                            continue

                        custom_vars_raw = _as_str((row or {}).get("custom_vars")).strip()
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

                        context_override = _as_str((row or {}).get("context")).strip() or None
                        tz_override = _as_str((row or {}).get("timezone")).strip() or None
                        caller_id_override = _as_str((row or {}).get("caller_id")).strip() or None

                        lead_id = str(uuid.uuid4())
                        try:
                            conn.execute(
                                """
                                INSERT INTO outbound_leads (
                                    id, campaign_id, phone_number,
                                    lead_timezone, context_override, caller_id_override,
                                    custom_vars_json, state,
                                    attempt_count, created_at_utc, updated_at_utc
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                                """,
                                (
                                    lead_id,
                                    campaign_id,
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
                                SET lead_timezone = COALESCE(?, lead_timezone),
                                    context_override = COALESCE(?, context_override),
                                    caller_id_override = COALESCE(?, caller_id_override),
                                    custom_vars_json = ?,
                                    updated_at_utc = ?
                                WHERE campaign_id = ? AND phone_number = ?
                                """,
                                (
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

            error_csv = io.StringIO()
            w = csv.writer(error_csv)
            w.writerow(["row_number", "phone_number", "error_reason"])
            for e in errors:
                w.writerow([e.row_number, e.phone_number, e.error_reason])

            return {
                "accepted": accepted,
                "rejected": rejected,
                "duplicates": duplicates,
                "errors": [e.__dict__ for e in errors],
                "error_csv": error_csv.getvalue(),
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

            clauses = ["campaign_id = ?"]
            args: List[Any] = [campaign_id]

            if state:
                clauses.append("state = ?")
                args.append(state)
            if q:
                clauses.append("phone_number LIKE ?")
                args.append(f"%{q}%")

            where = " AND ".join(clauses)
            with self._lock:
                conn = self._get_connection()
                try:
                    total = conn.execute(f"SELECT COUNT(*) AS c FROM outbound_leads WHERE {where}", args).fetchone()["c"]
                    rows = conn.execute(
                        f"""
                        SELECT * FROM outbound_leads
                        WHERE {where}
                        ORDER BY created_at_utc DESC
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

    async def recycle_lead(self, lead_id: str) -> bool:
        """
        Re-queue a lead by moving it back to 'pending'.

        MVP policy:
        - Allowed only from canceled/failed.
        - attempt_count is preserved; attempts table remains the audit trail.
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
                        SET state='pending',
                            last_outcome=NULL,
                            leased_until_utc=NULL,
                            updated_at_utc=?
                        WHERE id=? AND state IN ('canceled','failed')
                        """,
                        (now, lead_id),
                    )
                    conn.commit()
                    return cur.rowcount > 0
                finally:
                    conn.close()

        return await self._run(_sync)

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
                        SELECT a.*, l.phone_number
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

    async def create_attempt(self, campaign_id: str, lead_id: str) -> str:
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
                        INSERT INTO outbound_attempts (id, campaign_id, lead_id, started_at_utc)
                        VALUES (?, ?, ?, ?)
                        """,
                        (attempt_id, campaign_id, lead_id, now),
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

    async def finish_attempt(
        self,
        attempt_id: str,
        *,
        outcome: str,
        amd_status: Optional[str] = None,
        amd_cause: Optional[str] = None,
        call_history_call_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        if not self._enabled:
            return

        def _sync():
            now = _utcnow_iso()
            with self._lock:
                conn = self._get_connection()
                try:
                    conn.execute(
                        """
                        UPDATE outbound_attempts
                        SET ended_at_utc=?, outcome=?, amd_status=?, amd_cause=?,
                            call_history_call_id=?, error_message=?
                        WHERE id=?
                        """,
                        (now, outcome, amd_status, amd_cause, call_history_call_id, error_message, attempt_id),
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
