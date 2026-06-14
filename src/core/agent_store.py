"""Read-only per-call agent resolver (decision D1/D4).
The engine NEVER writes agents.db; admin_ui owns the write path and migration.
If the DB is absent the caller falls back to the YAML contexts path unchanged —
headless installs keep the YAML workflow forever."""
import json, logging, os, sqlite3
from contextlib import closing
from typing import Optional
from src.core.transport_orchestrator import ContextConfig

logger = logging.getLogger(__name__)
DB_DEFAULT = "/app/data/operator/agents.db"

_EXTRA_FIELDS = ("pipeline","background_music","pre_call_tools","post_call_tools",
                 "in_call_http_tools","disable_global_pre_call_tools",
                 "disable_global_in_call_tools","disable_global_post_call_tools")

class EngineAgentStore:
    def __init__(self, db_path: str = DB_DEFAULT):
        self.db_path = db_path

    def available(self) -> bool:
        return os.path.exists(self.db_path)

    def _conn(self):
        # Per-call connection: cheap under WAL; avoids cross-thread sqlite issues.
        c = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5.0)
        c.row_factory = sqlite3.Row
        return c

    def resolve(self, slug: str) -> Optional[ContextConfig]:
        if not self.available():
            return None
        try:
            with closing(self._conn()) as c:
                r = c.execute("SELECT * FROM agents WHERE slug=? AND is_active=1",
                              (slug,)).fetchone()
        except sqlite3.Error as e:
            logger.warning("agents.db read failed (%s); falling back to YAML", e)
            return None
        if r is None:
            return None
        try:
            extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
            tools = json.loads(r["tools_json"]) if r["tools_json"] else None
        except (json.JSONDecodeError, TypeError) as e:
            # Corrupt/invalid JSON (manual edit, bad backup). Don't crash the call:
            # return None so the caller falls back to YAML, same as a DB read error.
            logger.warning("agents.db JSON parse failed for slug=%s (%s); falling back to YAML",
                           slug, e)
            return None
        kwargs = {k: extra[k] for k in _EXTRA_FIELDS if k in extra}
        return ContextConfig(
            prompt=r["prompt"], greeting=r["greeting"], profile=r["audio_profile"],
            provider=r["provider"],
            tools=tools,
            **kwargs)

    def default_slug(self) -> Optional[str]:
        if not self.available():
            return None
        try:
            with closing(self._conn()) as c:
                r = c.execute(
                    "SELECT slug FROM agents WHERE is_default=1 AND is_active=1").fetchone()
            return r["slug"] if r else None
        except sqlite3.Error:
            return None
