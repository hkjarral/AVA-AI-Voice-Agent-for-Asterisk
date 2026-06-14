"""Agents store — source of truth for agent (context) configuration.
Write path lives here (admin_ui). The engine reads this DB via src/core/agent_store.py.
Spec: archived/AVAOperatorVersion.md §3 + 2026-05-23-v1-implementation-spec.md §5."""
import os, re, sqlite3, uuid
from datetime import datetime, timezone

DB_DEFAULT = "/app/data/operator/agents.db"
_SLUG_RE = re.compile(r"[^a-z0-9_]+")

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    extension TEXT,
    role_label TEXT,
    provider TEXT NOT NULL,
    voice TEXT,
    greeting TEXT,
    prompt TEXT NOT NULL,
    tools_json TEXT,
    mcp_json TEXT,
    audio_profile TEXT,
    extra_json TEXT,                 -- D3: pipeline, background_music, phase tools, disable flags, anything else
    is_operator_managed INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    source_file TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_agents_slug ON agents(slug);
CREATE INDEX IF NOT EXISTS idx_agents_mgmt ON agents(is_operator_managed);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_default ON agents(is_default) WHERE is_default = 1;
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    contexts_hash TEXT
);
"""

def slugify(name: str) -> str:
    s = _SLUG_RE.sub("_", name.strip().lower().replace("-", "_")).strip("_")[:64]
    return re.sub(r"_+", "_", s)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

class AgentsStore:
    COLUMNS = ["id","slug","display_name","extension","role_label","provider","voice",
               "greeting","prompt","tools_json","mcp_json","audio_profile","extra_json",
               "is_operator_managed","is_active","is_default","source_file",
               "created_at","updated_at","notes"]

    def __init__(self, db_path: str = DB_DEFAULT):
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        try: os.chmod(db_path, 0o600)
        except OSError: pass

    # -- reads -------------------------------------------------------------
    def get_by_slug(self, slug):  # -> dict | None
        r = self.conn.execute("SELECT * FROM agents WHERE slug=?", (slug,)).fetchone()
        return dict(r) if r else None

    def list_all(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM agents ORDER BY is_active DESC, created_at")]

    def get_default(self):
        r = self.conn.execute(
            "SELECT * FROM agents WHERE is_default=1 AND is_active=1").fetchone()
        return dict(r) if r else None

    def count_active(self, operator_managed_only: bool = False) -> int:
        q = "SELECT COUNT(*) FROM agents WHERE is_active=1"
        if operator_managed_only:
            q += " AND is_operator_managed=1"
        return self.conn.execute(q).fetchone()[0]

    # -- writes ------------------------------------------------------------
    def create(self, *, display_name, provider, prompt, slug=None, extension=None,
               role_label=None, voice=None, greeting=None, tools_json=None,
               mcp_json=None, audio_profile=None, extra_json=None,
               is_operator_managed=1, source_file=None, notes=None) -> dict:
        slug = slug or slugify(display_name)
        if not slug or not _SLUG_RE.sub("", slug) == slug:
            raise ValueError(f"invalid slug: {slug!r}")
        if self.get_by_slug(slug):
            raise ValueError(f"slug exists: {slug}")
        now = _now()
        with self.conn:
            self.conn.execute(
                """INSERT INTO agents (id,slug,display_name,extension,role_label,provider,
                   voice,greeting,prompt,tools_json,mcp_json,audio_profile,extra_json,
                   is_operator_managed,is_active,is_default,source_file,created_at,updated_at,notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,?,?,?,?)""",
                (str(uuid.uuid4()), slug, display_name, extension, role_label, provider,
                 voice, greeting, prompt, tools_json, mcp_json, audio_profile, extra_json,
                 is_operator_managed, source_file, now, now, notes))
        self._ensure_default_invariant()
        return self.get_by_slug(slug)

    def update(self, slug, **fields) -> dict:
        allowed = set(self.COLUMNS) - {"id","slug","created_at","is_default"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed: raise ValueError(f"field not updatable: {k}")
            sets.append(f"{k}=?"); vals.append(v)
        sets.append("updated_at=?"); vals.append(_now()); vals.append(slug)
        with self.conn:
            self.conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE slug=?", vals)
        self._ensure_default_invariant()
        return self.get_by_slug(slug)

    def set_default(self, slug):
        # Validate target first — only clear the existing default when we know the new
        # target exists and is active, so an invalid request never leaves zero defaults.
        r = self.conn.execute(
            "SELECT slug FROM agents WHERE slug=? AND is_active=1", (slug,)).fetchone()
        if r is None:
            return  # invalid / inactive target — leave current default untouched
        with self.conn:
            self.conn.execute("UPDATE agents SET is_default=0 WHERE is_default=1")
            self.conn.execute(
                "UPDATE agents SET is_default=1 WHERE slug=?", (slug,))
        self._ensure_default_invariant()

    def set_active(self, slug, active: bool):
        with self.conn:
            self.conn.execute("UPDATE agents SET is_active=?, is_default=CASE WHEN ?=0 THEN 0 ELSE is_default END, updated_at=? WHERE slug=?",
                              (int(active), int(active), _now(), slug))
        return self._ensure_default_invariant()

    def delete(self, slug):
        with self.conn:
            self.conn.execute("DELETE FROM agents WHERE slug=?", (slug,))
        return self._ensure_default_invariant()

    def _ensure_default_invariant(self):
        """Exactly-one-default among active agents (spec §8.1).
        Returns the promoted slug if a promotion happened (A4 surfaces it in UI), else None."""
        if self.get_default() is not None:
            return None
        r = self.conn.execute(
            "SELECT slug FROM agents WHERE is_active=1 ORDER BY created_at LIMIT 1").fetchone()
        if r is None:
            return None   # no active agents — UI shows empty state; engine uses fallback prompt
        with self.conn:
            self.conn.execute("UPDATE agents SET is_default=0 WHERE is_default=1")
            self.conn.execute("UPDATE agents SET is_default=1 WHERE slug=?", (r[0],))
        return r[0]
