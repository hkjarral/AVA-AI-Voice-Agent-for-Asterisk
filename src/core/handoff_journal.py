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

    def _pending(self):
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path, timeout=5)) as conn:
            rows = conn.execute("SELECT id, payload FROM handoff_actions").fetchall()
        return [(key, json.loads(value)) for key, value in rows if json.loads(value).get("result") in {"dispatching", "indeterminate"}]

    async def pending(self):
        return await asyncio.to_thread(self._pending)
