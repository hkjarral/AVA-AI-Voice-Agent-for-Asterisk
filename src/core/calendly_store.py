"""Integration-owned, fail-closed Calendly submission journal.

Every blocking entry point is called outside the audio event loop. No database
transaction crosses network I/O; admitted writes are never replayed on restart.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager

import portalocker

from src.tools.business.calendly_client import CalendlyError


def encoded(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def calendly_footprint_path(db_path: str | Path | None = None) -> Path:
    """Return the durable, non-secret marker for prior Calendly configuration."""
    journal = Path(db_path or (os.getenv("CALENDLY_DB_PATH") or "/app/data/operator/calendly.db"))
    return journal.with_name(journal.name + ".configured")


def record_calendly_footprint(db_path: str | Path | None = None) -> None:
    """Persist that Calendly was configured, independently of removable credentials."""
    marker = calendly_footprint_path(db_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        info = marker.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(info.st_mode):
            raise CalendlyError("footprint_state_invalid")
        if info.st_mode & 0o077:
            os.chmod(marker, 0o600)
        return
    temp = marker.with_name(marker.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(b"calendly-configured-v1\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, marker)
        os.chmod(marker, 0o600)
        directory = os.open(marker.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


class CalendlyStore:
    def __init__(self, db_path: str | None = None, token_path: str | None = None):
        self.path = Path(db_path or (os.getenv("CALENDLY_DB_PATH") or "/app/data/operator/calendly.db"))
        self.token_path = Path(token_path or (os.getenv("CALENDLY_TOKEN_PATH") or "/app/project/secrets/calendly-default-token.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # SQLite derives WAL/SHM permissions from its main file. Never use 0666.
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        # Existing journals need no schema writes or initialization lock. Setup
        # requests and the recovery worker open this store independently.
        with self.db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 1:
                # Pre-marker journals retain private data even after disconnect.
                # Backfill on open, before recovery/setup can remove credentials.
                record_calendly_footprint(self.path)
                return
            if version != 0:
                raise CalendlyError("journal_version_unsupported")
        with portalocker.Lock(str(self.path) + ".init.lock", timeout=2):
            os.chmod(str(self.path) + ".init.lock", 0o600)
            with self.db() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in {0, 1}:
                    raise CalendlyError("journal_version_unsupported")
                if version == 1:
                    record_calendly_footprint(self.path)
                    return
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS connection(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS proposals(
                        id TEXT PRIMARY KEY, operation TEXT NOT NULL, connection_id TEXT NOT NULL,
                        value TEXT NOT NULL, expires REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS attempts(
                        id TEXT PRIMARY KEY, operation TEXT NOT NULL, connection_id TEXT NOT NULL,
                        fingerprint TEXT NOT NULL, duplicate_key TEXT NOT NULL, marker TEXT NOT NULL,
                        state TEXT NOT NULL, private TEXT NOT NULL, public TEXT NOT NULL,
                        created REAL NOT NULL, updated REAL NOT NULL, next_due REAL NOT NULL,
                        passes INTEGER NOT NULL DEFAULT 0, lease REAL NOT NULL DEFAULT 0,
                        checks INTEGER NOT NULL DEFAULT 0);
                    CREATE INDEX IF NOT EXISTS attempts_operation ON attempts(operation);
                    CREATE INDEX IF NOT EXISTS attempts_duplicate ON attempts(duplicate_key);
                    CREATE TABLE IF NOT EXISTS resolutions(
                        id INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL, actor TEXT NOT NULL,
                        outcome TEXT NOT NULL, note TEXT NOT NULL, created REAL NOT NULL);
                    PRAGMA user_version=1;
                """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=1, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=1000")
            yield db
        finally:
            db.close()

    def connection(self) -> dict:
        with self.db() as db:
            row = db.execute("SELECT value FROM connection WHERE id=1").fetchone()
            return json.loads(row[0]) if row else {"revision": 0, "enabled": False}

    def _put(self, db, value):
        db.execute("INSERT OR REPLACE INTO connection VALUES(1,?)", (encoded(value),))

    def credentials(self, *, admission: bool = False) -> tuple[dict, str]:
        conn = self.connection()
        if not conn.get("id") or (admission and not conn.get("enabled")):
            raise CalendlyError("connection_disabled")
        if conn.get("cooldown", 0) > time.time():
            raise CalendlyError("rate_limited")
        try:
            if self.token_path.stat().st_mode & 0o077:
                raise CalendlyError("credential_permissions")
            data = json.loads(self.token_path.read_text())
            if data["connection_id"] != conn["id"] or data["revision"] != conn["revision"] or not data["token"]:
                raise CalendlyError("credential_revision_mismatch")
            return conn, data["token"]
        except (OSError, ValueError, KeyError):
            raise CalendlyError("credential_unavailable") from None

    def save_connection(self, identity: dict, token: str, expected: int) -> dict:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(self.token_path) + ".lock", timeout=2):
            os.chmod(str(self.token_path) + ".lock", 0o600)
            old = self.connection()
            if old["revision"] != expected:
                raise CalendlyError("connection_changed")
            # save_connection is reached only after Calendly verifies the supplied
            # identity. Keep this privacy-erasure obligation after disconnect,
            # credential removal, journal outages and process restarts.
            record_calendly_footprint(self.path)
            same = old.get("user") == identity["uri"] and old.get("organization") == identity["current_organization"]
            new = {"id": old["id"] if same else uuid.uuid4().hex, "user": identity["uri"],
                   "organization": identity["current_organization"], "name": str(identity.get("name") or "")[:200],
                   "timezone": identity.get("timezone", "UTC"), "revision": expected + 1,
                   "enabled": False, "qualified": False, "verified_at": time.time(), "cooldown": 0}
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                self._put(db, new)  # Close admission before touching the file.
                db.commit()
            temp = self.token_path.with_name(self.token_path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as handle:
                    handle.write(encoded({"connection_id": new["id"], "revision": new["revision"], "token": token}))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self.token_path)
                directory = os.open(self.token_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temp.unlink(missing_ok=True)
            # Remain disabled until the explicit enable/test action.
            return new

    def update_connection(self, expected: int, **patch) -> dict:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM connection WHERE id=1").fetchone()
            conn = json.loads(row[0]) if row else {"revision": 0, "enabled": False}
            if conn["revision"] != expected:
                raise CalendlyError("connection_changed")
            conn.update(patch)
            self._put(db, conn)
            db.commit()
            return conn

    def disconnect(self, expected: int) -> dict:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(self.token_path) + ".lock", timeout=2):
            conn = self.update_connection(expected, enabled=False, qualified=False, revision=expected + 1)
            # Do not remove the credential until its independent erasure marker
            # is durable, including for a legacy connection opened before upgrade.
            record_calendly_footprint(self.path)
            self.token_path.unlink(missing_ok=True)
            return conn

    def observe_cooldown(self, identity: dict, seconds: int):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM connection WHERE id=1").fetchone()
            conn = json.loads(row[0]) if row else {}
            if conn.get("id") == identity.get("id") and conn.get("revision") == identity.get("revision"):
                conn["cooldown"] = max(conn.get("cooldown", 0), time.time() + seconds)
                self._put(db, conn)
            db.commit()

    def proposal(self, operation: str, connection_id: str, value: dict) -> str:
        key = uuid.uuid4().hex
        with self.db() as db:
            db.execute("INSERT INTO proposals VALUES(?,?,?,?,?)", (key, operation, connection_id, encoded(value), time.time() + 300))
        return key

    def read_proposal(self, key: str, operation: str, connection_id: str) -> dict:
        with self.db() as db:
            row = db.execute("SELECT * FROM proposals WHERE id=? AND operation=? AND connection_id=?", (key, operation, connection_id)).fetchone()
            if not row or row["expires"] < time.time():
                raise CalendlyError("proposal_expired")
            return json.loads(row["value"])

    def prior(self, operation: str) -> dict | None:
        with self.db() as db:
            row = db.execute("SELECT * FROM attempts WHERE operation=? AND state NOT IN ('rejected','aborted_before_submit') ORDER BY created DESC LIMIT 1", (operation,)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def duplicate_key(conn: dict, payload: dict) -> str:
        return digest([conn["user"], conn["organization"], payload["event_type"], payload["start_time"], payload["invitee"]["email"]])

    def duplicate(self, operation: str, conn: dict, payload: dict) -> dict | None:
        with self.db() as db:
            row = db.execute("SELECT * FROM attempts WHERE duplicate_key=? AND operation!=? AND state NOT IN ('rejected','aborted_before_submit','verified_cancelled') ORDER BY created DESC LIMIT 1",
                             (self.duplicate_key(conn, payload), operation)).fetchone()
            return dict(row) if row else None

    def release_cancelled_duplicate(self, row: dict) -> bool:
        # Called only after positive exact event AND invitee cancellation proof.
        # CAS preserves concurrent reconciliation/operator changes. Keep the row
        # as the original operation's replay barrier, including after retention.
        with self.db() as db:
            return bool(db.execute("UPDATE attempts SET state='verified_cancelled',updated=? WHERE id=? AND state=? AND private=? AND connection_id=? AND state IN ('confirmed','resolved_remote_cancelled')",
                                   (time.time(), row["id"], row["state"], row["private"], row["connection_id"])).rowcount)

    def admit(self, operation: str, conn: dict, payload: dict, public: dict) -> tuple[dict, bool]:
        now = time.time()
        fingerprint = digest(payload)
        duplicate = self.duplicate_key(conn, payload)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = json.loads(db.execute("SELECT value FROM connection WHERE id=1").fetchone()[0])
            if not current.get("enabled") or current.get("id") != conn["id"] or current["revision"] != conn["revision"]:
                raise CalendlyError("connection_changed")
            if current.get("cooldown", 0) > now:
                raise CalendlyError("rate_limited")
            existing = db.execute("SELECT * FROM attempts WHERE (operation=? OR (duplicate_key=? AND state!='verified_cancelled')) AND state NOT IN ('rejected','aborted_before_submit') ORDER BY created DESC LIMIT 1", (operation, duplicate)).fetchone()
            if existing:
                return dict(existing), False
            if db.execute("SELECT 1 FROM attempts WHERE state='submitting' AND lease>?", (now,)).fetchone():
                raise CalendlyError("booking_capacity_busy")
            # Even a definite rejection cannot be blindly retried with identical details.
            if db.execute("SELECT 1 FROM attempts WHERE operation=? AND fingerprint=?", (operation, fingerprint)).fetchone():
                raise CalendlyError("new_proposal_required")
            key = uuid.uuid4().hex
            marker = "ava-v1-" + str(uuid.uuid4())
            private = {"payload": payload, "user": conn["user"], "organization": conn["organization"]}
            db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (key, operation, conn["id"], fingerprint, duplicate, marker, "submitting", encoded(private), encoded(public), now, now, now + 5, 0, now + 20, 0))
            db.commit()
            return self.attempt(key), True

    def attempt(self, key: str) -> dict:
        with self.db() as db:
            row = db.execute("SELECT * FROM attempts WHERE id=?", (key,)).fetchone()
            if not row:
                raise CalendlyError("attempt_not_found")
            return dict(row)

    def finish(self, key: str, state: str, public: dict, private: dict | None = None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM attempts WHERE id=?", (key,)).fetchone()
            # A staff decision or another settled result must not be overwritten.
            if not row or row["state"] not in {"submitting", "uncertain", "needs_operator"}:
                return
            values = json.loads(row["private"]) if private is None else private
            if values.get("lookup") or state not in {"uncertain", "needs_operator"}:
                values.pop("payload", None)
            db.execute("UPDATE attempts SET state=?, public=?, private=?, updated=?, lease=0 WHERE id=?",
                       (state, encoded(public), encoded(values), time.time(), key))
            db.commit()

    def list_attempts(self) -> list[dict]:
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT id,operation,state,public,created,updated,passes FROM attempts WHERE state IN ('submitting','uncertain','needs_operator') OR operation LIKE 'setup:%' ORDER BY created DESC LIMIT 50")]

    def lease_check(self, key: str, manual: bool) -> dict:
        now = time.time()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM attempts WHERE id=?", (key,)).fetchone()
            if not row:
                raise CalendlyError("attempt_not_found")
            row = dict(row)
            if row["state"] not in {"submitting", "uncertain", "needs_operator"} or row["lease"] > now or row["next_due"] > now:
                raise CalendlyError("check_not_due")
            if not manual and row["passes"] >= 3:
                raise CalendlyError("check_limit")
            connection = db.execute("SELECT value FROM connection WHERE id=1").fetchone()
            if connection and json.loads(connection[0]).get("cooldown", 0) > now:
                # Check inside the lease transaction too: a cooldown can start
                # after due() ran, or a manual check can bypass that lookup.
                raise CalendlyError("rate_limited")
            passes = row["passes"] + (0 if manual else 1)
            delay = 30 if manual else (30 if passes == 1 else 120)
            db.execute("UPDATE attempts SET state='uncertain',lease=?,passes=?,checks=checks+1,next_due=? WHERE id=?", (now + 25, passes, now + delay, key))
            db.commit()
            row.update(state="uncertain", passes=passes)
            return row

    def due(self) -> list[str]:
        now = time.time()
        with self.db() as db:
            connection = db.execute("SELECT value FROM connection WHERE id=1").fetchone()
            if connection and json.loads(connection[0]).get("cooldown", 0) > now:
                return []
            return [r[0] for r in db.execute("SELECT id FROM attempts WHERE state IN ('submitting','uncertain') AND lease<? AND next_due<? AND passes<3 LIMIT 10", (now, now))]

    def resolve(self, key: str, actor: str, note: str):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM attempts WHERE id=?", (key,)).fetchone()
            if not row or row[0] not in {"uncertain", "needs_operator"}:
                raise CalendlyError("resolution_not_allowed")
            db.execute("UPDATE attempts SET state='resolved_externally',private='{}',lease=0,updated=? WHERE id=?", (time.time(), key))
            db.execute("INSERT INTO resolutions(attempt_id,actor,outcome,note,created) VALUES(?,?,?,?,?)", (key, actor[:200], "resolved_externally", note[:500], time.time()))
            db.commit()

    def cleanup(self, purged_calls: list[str] | None = None):
        try:
            days = int((os.getenv("CALL_HISTORY_RETENTION_DAYS") or "0").strip())
        except ValueError:
            days = 0
        days = min(30, days) if days > 0 else 30
        cutoff = time.time() - days * 86400
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM proposals WHERE expires<?", (time.time(),))
            db.execute("UPDATE attempts SET private='{}',public='{}' WHERE created<? AND (private!='{}' OR public!='{}')", (cutoff,))
            db.execute("UPDATE resolutions SET note='' WHERE note!='' AND attempt_id IN (SELECT id FROM attempts WHERE created<?)", (cutoff,))
            for call in purged_calls or []:
                operation = "call:" + call
                erased = db.execute(
                    "UPDATE attempts SET state='erased',private='{}',public='{}',lease=0,"
                    "duplicate_key=CASE WHEN state IN ('rejected','aborted_before_submit','verified_cancelled') "
                    "THEN 'erased:'||id ELSE duplicate_key END WHERE operation=?", (operation,))
                if not erased.rowcount:
                    # Deletion may win before an admission worker commits. The
                    # same write transaction leaves an operation-only barrier,
                    # so a late admission cannot create private lookup details.
                    db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        uuid.uuid4().hex, operation, "", "erased", "erased:" + digest(operation), "erased",
                        "erased", "{}", "{}", time.time(), time.time(), 0, 0, 0, 0))
                db.execute("UPDATE resolutions SET note='' WHERE note!='' AND attempt_id IN (SELECT id FROM attempts WHERE operation=?)", (operation,))
            db.commit()


def scrub_deleted_calls(call_ids: list[str]) -> bool:
    """Acknowledge erasure only after accessing the journal; keep replay barriers."""
    path = Path(os.getenv("CALENDLY_DB_PATH") or "/app/data/operator/calendly.db")
    if path.exists():
        CalendlyStore().cleanup(call_ids)
        return True
    return False
