"""Read-only per-call agent resolver (decision D1/D4).
The engine NEVER writes agents.db; admin_ui owns the write path and migration.
If the DB is absent the caller falls back to the YAML contexts path unchanged —
headless installs keep the YAML workflow forever."""
import json, logging, os, re, sqlite3
from contextlib import closing
from typing import Optional
from src.core.transport_orchestrator import ContextConfig

logger = logging.getLogger(__name__)
DB_DEFAULT = "/app/data/operator/agents.db"

_EXTRA_FIELDS = ("pipeline","background_music","pre_call_tools","post_call_tools",
                 "in_call_http_tools","disable_global_pre_call_tools",
                 "disable_global_in_call_tools","disable_global_post_call_tools",
                 "no_input")

# Must match admin_ui/backend/agents_store.py:slugify so resolve-on-read can
# recover the slug from a raw dialplan name (CRIT-1).
_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def _slugify(name: str) -> str:
    s = _SLUG_RE.sub("_", name.strip().lower().replace("-", "_")).strip("_")[:64]
    return re.sub(r"_+", "_", s)


class AgentStoreReadError(Exception):
    """agents.db exists but could not be read (corrupt / locked / bad JSON).

    Distinct from a clean not-found/inactive result (which is ``None``): the
    orchestrator falls back to YAML ONLY on this error, never for a deleted or
    deactivated agent (HIGH-9)."""

class EngineAgentStore:
    def __init__(self, db_path: Optional[str] = None):
        # Honor AGENTS_DB_PATH so a relocated DB is read at runtime; falls back
        # to the historical default when unset (no behavior change for existing
        # installs). EngineAgentStore() is constructed with no arg (see
        # transport_orchestrator), so the env is the only relocation lever here.
        self.db_path = db_path or os.getenv("AGENTS_DB_PATH", DB_DEFAULT)

    def available(self) -> bool:
        return os.path.exists(self.db_path)

    def _conn(self):
        # Per-call connection: cheap under WAL; avoids cross-thread sqlite issues.
        c = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5.0)
        c.row_factory = sqlite3.Row
        return c

    def resolve(self, name: str, prefer: str = "slug") -> Optional[ContextConfig]:
        """Resolve a dialplan context/agent name to a ContextConfig.

        ``prefer`` threads the dialplan channel-variable INTENT so each selector
        resolves correctly (Finding 1, Codex P2 root cause):

        * ``"slug"`` (default; value came from ``AI_AGENT``, the canonical slug
          selector): exact ``slug`` → ``slugify(name)`` → ``display_name``. The
          canonical slug is tried first so a free-form ``display_name`` can never
          shadow a real slug — ``Set(AI_AGENT=sales)`` must reach the agent whose
          *slug* is ``sales``, not one whose display_name happens to be "sales".
        * ``"display_name"`` (value came from ``AI_CONTEXT``, the legacy
          original-name selector): ``display_name`` → exact ``slug`` →
          ``slugify(name)``. The original name wins so a migrated legacy context
          whose collision was disambiguated still routes to the agent that carried
          that original name (e.g. ``Set(AI_CONTEXT=sales_east)`` reaches the agent
          whose display_name is ``sales_east``, not the first agent that happens to
          own the bare slug ``sales_east``).

        In both orders ``slugify(name)`` still resolves legacy raw context names
        (CRIT-1). An unknown ``prefer`` falls back to slug-first (safest canonical).

        Returns ``None`` for a clean not-found/inactive result. Raises
        ``AgentStoreReadError`` if the DB is present but unreadable, so the caller
        can fall back to YAML without resurrecting deleted agents (HIGH-9)."""
        if not self.available():
            return None
        slug_first = (
            "SELECT * FROM agents WHERE slug=? AND is_active=1 LIMIT 1",
            (name,),
        )
        slug_slugified = (
            "SELECT * FROM agents WHERE slug=? AND is_active=1 LIMIT 1",
            (_slugify(name),),
        )
        display = (
            "SELECT * FROM agents WHERE display_name=? AND is_active=1 LIMIT 1",
            (name,),
        )
        if prefer == "display_name":
            lookups = (display, slug_first, slug_slugified)
        else:
            lookups = (slug_first, slug_slugified, display)
        try:
            with closing(self._conn()) as c:
                r = None
                for sql, params in lookups:
                    r = c.execute(sql, params).fetchone()
                    if r is not None:
                        break
        except sqlite3.Error as e:
            logger.warning("agents.db read failed (%s); caller will fall back to YAML", e)
            raise AgentStoreReadError(str(e)) from e
        if r is None:
            return None
        try:
            extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
            tools = json.loads(r["tools_json"]) if r["tools_json"] else None
        except (json.JSONDecodeError, TypeError) as e:
            # Corrupt/invalid JSON (manual edit, bad backup) is a read error, not a
            # not-found — surface it so the caller falls back to YAML (HIGH-9).
            logger.warning("agents.db JSON parse failed for name=%s (%s); caller will fall back to YAML",
                           name, e)
            raise AgentStoreReadError(str(e)) from e
        kwargs = {k: extra[k] for k in _EXTRA_FIELDS if k in extra}
        # Per-agent post-call email overrides (H5): raw DB columns (added by H1
        # migration), not extra_json. Guard column presence so a pre-migration DB
        # resolves cleanly instead of raising. email_enabled is stored as int (0/1)
        # or NULL; coerce to tri-state bool/None.
        cols = r.keys()
        email_recipient = r["email_recipient"] if "email_recipient" in cols else None
        email_from = r["email_from"] if "email_from" in cols else None
        email_enabled_raw = r["email_enabled"] if "email_enabled" in cols else None
        email_enabled = None if email_enabled_raw is None else bool(email_enabled_raw)
        return ContextConfig(
            prompt=r["prompt"], greeting=r["greeting"], profile=r["audio_profile"],
            provider=r["provider"],
            voice=r["voice"] if "voice" in cols else None,
            tools=tools,
            email_recipient=email_recipient,
            email_from=email_from,
            email_enabled=email_enabled,
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
