"""YAML contexts merge + normalized hash + one-time migration into agents.db.
Merge semantics mirror src/config.py:_merge_external_contexts (inline wins;
external files keyed by 'name'; system_prompt→prompt). Parity is covered by
fixture tests, not cross-imports (admin_ui does not import src/ — decision D4)."""
import glob
import hashlib
import json
import os
import uuid

import yaml
from agents_store import AgentsStore, slugify, _now


def merged_effective_contexts(yaml_path: str, contexts_dir: str) -> dict:
    """Return the effective merged contexts dict, mirroring what the engine loads.

    Merge order: external context files are loaded first, then inline contexts
    from ai-agent.yaml overlay them (inline wins on collision).

    Deliberate divergence from src/config.py:_merge_external_contexts: production
    calls os.path.expandvars() on each external file's raw text before parsing so
    ${ENV_VAR} placeholders are expanded at load time. This function deliberately
    omits that step so contexts_hash() is environment-independent and produces the
    same hash on any machine regardless of local env vars.
    """
    inline = {}
    if os.path.exists(yaml_path):
        doc = yaml.safe_load(open(yaml_path)) or {}
        inline = doc.get("contexts") or {}

    merged = {}

    # Parity fix 1: glob both *.yaml and *.yml — production does the same
    # (src/config.py:_merge_external_contexts lines 1133-1135).
    pattern_yaml = os.path.join(contexts_dir, "*.yaml")
    pattern_yml = os.path.join(contexts_dir, "*.yml")
    files = sorted(glob.glob(pattern_yaml) + glob.glob(pattern_yml))

    for f in files:
        # Parity fix 2: broad except so an unreadable/garbage file is skipped,
        # not fatal (production catches broadly at line 1143).
        try:
            ext = yaml.safe_load(open(f)) or {}
        except Exception:
            continue

        # Parity fix 2 (continued): skip non-dict files (e.g. a YAML list);
        # .pop("name") would crash on a list (production guards at line 1146).
        if not isinstance(ext, dict):
            continue

        name = ext.pop("name", None)
        # Parity fix 3: require a non-empty stripped string (production lines 1150-1152).
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()

        if "prompt" not in ext and "system_prompt" in ext:
            ext["prompt"] = ext.pop("system_prompt")

        ext["_source_file"] = os.path.relpath(f, os.path.dirname(os.path.dirname(f)))
        merged[name] = ext

    for k, v in inline.items():            # inline wins on collision
        d = dict(v or {})
        d["_source_file"] = "ai-agent.yaml"
        merged[k] = d

    return merged


def contexts_hash(merged: dict) -> str:
    """Return a normalized SHA-256 hex digest of the merged contexts for drift detection.

    Strips internal _source_file annotations before hashing so the hash reflects
    only semantically meaningful context data.
    """
    clean = {k: {kk: vv for kk, vv in v.items() if kk != "_source_file"}
             for k, v in merged.items()}
    canon = json.dumps(clean, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


# ---------------------------------------------------------------------------
# One-time migration
# ---------------------------------------------------------------------------

MIGRATION_VERSION = 1

# Fields stored in first-class columns; everything else goes into extra_json.
_FIRST_CLASS = {"provider", "voice", "greeting", "prompt", "audio_profile", "profile", "tools"}


def run_migration(store: AgentsStore, yaml_path: str, contexts_dir: str) -> dict:
    """One-time import of merged YAML contexts into agents.db.

    Idempotent: returns immediately if the migration record already exists or
    any agents row is present.  Per-context validation errors are *skipped*
    (not raised), so a single invalid context does not block valid ones.

    Transaction strategy: AgentsStore opens its connection with the default
    isolation_level (autocommit OFF).  Python's sqlite3 module auto-issues an
    implicit BEGIN before the first DML, so calling conn.execute("BEGIN")
    explicitly would raise "cannot start a transaction within a transaction".
    We use ``with store.conn:`` (the context-manager form) instead — it commits
    on success and rolls back on any exception.  Per-context skips are plain
    ``continue`` statements *outside* the context manager, so they do not
    trigger a rollback; only an unexpected exception does.
    """
    already = store.conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION_VERSION,)
    ).fetchone()
    if already or store.conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] > 0:
        return {"imported": 0, "skipped": [], "already_migrated": True}

    merged = merged_effective_contexts(yaml_path, contexts_dir)
    h = contexts_hash(merged)

    # Validate every context and separate valid rows from skips *before* we
    # open the transaction — skips are not errors and must not trigger rollback.
    rows = []
    skipped = []
    for key, ctx in merged.items():
        src = ctx.pop("_source_file", None)
        prompt = ctx.get("prompt")
        if not prompt:
            skipped.append((key, "missing prompt"))
            continue
        provider = ctx.get("provider") or ""
        extra = {k: v for k, v in ctx.items() if k not in _FIRST_CLASS}
        now = _now()
        rows.append((
            uuid.uuid4().hex,
            slugify(key),
            key,
            provider,
            ctx.get("voice"),
            ctx.get("greeting"),
            prompt,
            json.dumps(ctx["tools"]) if ctx.get("tools") else None,
            ctx.get("profile") or ctx.get("audio_profile"),
            json.dumps(extra) if extra else None,
            1 if key == "default" else 0,  # is_default
            src,
            now,
            now,
        ))

    with store.conn:
        for r in rows:
            store.conn.execute(
                """INSERT INTO agents (id, slug, display_name, provider, voice, greeting,
                   prompt, tools_json, audio_profile, extra_json, is_operator_managed,
                   is_active, is_default, source_file, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,0,1,?,?,?,?)""",
                r,
            )
        store.conn.execute(
            "INSERT INTO schema_migrations (version, applied_at, contexts_hash) VALUES (?,?,?)",
            (MIGRATION_VERSION, _now(), h),
        )

    store._ensure_default_invariant()
    return {"imported": len(rows), "skipped": skipped, "already_migrated": False}


def current_drift(store: AgentsStore, yaml_path: str, contexts_dir: str) -> dict | None:
    """Return drift info if YAML contexts changed since migration (spec §7), else None."""
    row = store.conn.execute(
        "SELECT contexts_hash FROM schema_migrations WHERE version=?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if not row:
        return None
    current = contexts_hash(merged_effective_contexts(yaml_path, contexts_dir))
    if current == row[0]:
        return None
    return {"stored_hash": row[0], "current_hash": current}


def acknowledge_drift(store: AgentsStore, yaml_path: str, contexts_dir: str) -> None:
    """Update the stored hash to the current YAML state (marks drift as acknowledged)."""
    current = contexts_hash(merged_effective_contexts(yaml_path, contexts_dir))
    with store.conn:
        store.conn.execute(
            "UPDATE schema_migrations SET contexts_hash=? WHERE version=?",
            (current, MIGRATION_VERSION),
        )
