"""Durable evidence for uncertain completion handoffs; never a retry queue."""
import asyncio
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

class HandoffJournal:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("HANDOFF_JOURNAL_PATH", "/app/data/operator/handoff_actions.db"))

    def _write(self, action_id, payload):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS handoff_actions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            conn.execute("INSERT OR REPLACE INTO handoff_actions VALUES (?, ?)", (action_id, json.dumps(payload)))

    async def write(self, action_id, payload):
        task = asyncio.create_task(asyncio.to_thread(self._write, action_id, dict(payload)))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # A SQLite worker cannot be cancelled. Finish it before the caller
            # records a terminal outcome, so a late intent cannot overwrite it.
            await task
            raise

    def get(self, action_id):
        if not self.path.exists():
            return None
        with closing(sqlite3.connect(self.path, timeout=1)) as conn:
            row = conn.execute("SELECT payload FROM handoff_actions WHERE id=?", (action_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _observe(self, action_id, payload):
        with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
            return conn.execute(
                "UPDATE handoff_actions SET payload=? WHERE id=? AND json_extract(payload, '$.result') IN ('dispatching', 'indeterminate')",
                (json.dumps(payload), action_id),
            ).rowcount == 1

    async def observe(self, action_id, payload):
        return await asyncio.to_thread(self._observe, action_id, dict(payload))

    def _pending(self):
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path, timeout=5)) as conn:
            conn.execute("CREATE INDEX IF NOT EXISTS handoff_result ON handoff_actions(json_extract(payload, '$.result'))")
            rows = conn.execute("SELECT id, payload FROM handoff_actions WHERE json_extract(payload, '$.result') IN ('dispatching', 'indeterminate')").fetchall()
        return [(key, json.loads(value)) for key, value in rows]

    async def pending(self):
        return await asyncio.to_thread(self._pending)
