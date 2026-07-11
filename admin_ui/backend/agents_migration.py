"""YAML contexts merge + normalized hash + one-time migration into agents.db.
Merge semantics mirror the engine's effective configuration: base
``ai-agent.yaml`` contexts, deep-merged operator overrides from
``ai-agent.local.yaml``, then external context files for names not already
present. Parity is covered by fixture tests, not cross-imports (admin_ui does
not import src/ — decision D4)."""
import glob
import hashlib
import json
import logging
import os
import sqlite3
import uuid

import yaml
from agents_store import AgentsStore, slugify, _now


logger = logging.getLogger(__name__)


def _deep_merge_dicts(base: dict, override: dict) -> dict:
    """Mirror src.config.loaders.deep_merge_dicts without importing engine code."""
    merged = dict(base)
    for key, override_val in override.items():
        if override_val is None:
            merged.pop(key, None)
            continue
        base_val = merged.get(key)
        if isinstance(base_val, dict) and isinstance(override_val, dict):
            merged[key] = _deep_merge_dicts(base_val, override_val)
        else:
            merged[key] = override_val
    return merged


def merged_effective_contexts(yaml_path: str, contexts_dir: str) -> dict:
    """Return the effective merged contexts dict, mirroring what the engine loads.

    Merge order: sibling ai-agent.local.yaml is deep-merged over ai-agent.yaml,
    then external context files fill names absent from that effective inline
    mapping. This matches load_config(), which applies the local override before
    _merge_external_contexts().

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

    for k, v in inline.items():
        d = dict(v or {})
        d["_source_file"] = "ai-agent.yaml"
        merged[k] = d

    stem, ext = os.path.splitext(yaml_path)
    local_yaml_path = f"{stem}.local{ext}"
    if os.path.exists(local_yaml_path):
        try:
            local_doc = yaml.safe_load(open(local_yaml_path)) or {}
            local_inline = local_doc.get("contexts") or {} if isinstance(local_doc, dict) else {}
            if isinstance(local_inline, dict):
                valid_local_inline = {
                    key: value
                    for key, value in local_inline.items()
                    if value is None or isinstance(value, dict)
                }
                invalid_keys = sorted(
                    str(key)
                    for key in set(local_inline) - set(valid_local_inline)
                )
                if invalid_keys:
                    logger.warning(
                        "Skipping non-mapping local context overrides: %s",
                        ", ".join(invalid_keys),
                    )
                merged = _deep_merge_dicts(merged, valid_local_inline)
                for key in valid_local_inline:
                    if key in merged and isinstance(merged[key], dict):
                        merged[key]["_source_file"] = os.path.basename(local_yaml_path)
        except Exception:
            # Match runtime behavior: a broken optional override does not take
            # down base configuration or migration status.
            logger.warning(
                "Failed to load/merge local context override %s",
                local_yaml_path,
                exc_info=True,
            )

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

        if name not in merged:
            ext["_source_file"] = os.path.relpath(
                f, os.path.dirname(os.path.dirname(f))
            )
            merged[name] = ext

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


def disambiguate_slug(key: str, seen_slugs: set) -> str:
    """CRIT-3: deterministically disambiguate a context's slug against slugs already
    taken (``Sales-East`` and ``sales_east`` both slugify to ``sales_east`` — the
    second becomes ``sales_east_2``). ``seen_slugs`` is mutated with the result so a
    caller can map several colliding contexts to distinct slugs in one pass. This is
    the single source of truth shared by run_migration() and reconcile (Finding 2)."""
    base = slugify(key) or "agent"
    slug = base
    n = 2
    while slug in seen_slugs:
        slug = f"{base}_{n}"
        n += 1
    seen_slugs.add(slug)
    return slug


# ---------------------------------------------------------------------------
# One-time migration
# ---------------------------------------------------------------------------

MIGRATION_VERSION = 1

# Fields stored in first-class columns; everything else goes into extra_json.
# The per-context email keys are first-class so that an export_agents_yaml.py
# dump (which emits email_recipient/email_from/email_enabled as TOP-LEVEL context
# keys) round-trips back into the email columns on re-migrate rather than leaking
# into extra_json (which EngineAgentStore does NOT read for email dispatch).
_FIRST_CLASS = {
    "provider", "voice", "greeting", "prompt", "audio_profile", "profile", "tools",
    "email_recipient", "email_from", "email_enabled",
}


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

    # H4: legacy per-context email overrides live in the GLOBAL tools config
    # (ai-agent.yaml top-level `tools`), keyed by the ORIGINAL context name. Load
    # the email tool block so we can (1) carry each context's override onto its
    # agent row and (2) re-key the surviving map from original name -> slug.
    email_tool_cfg = {}
    if os.path.exists(yaml_path):
        doc = yaml.safe_load(open(yaml_path)) or {}
        tools_block = doc.get("tools") if isinstance(doc, dict) else None
        if isinstance(tools_block, dict):
            cfg = tools_block.get("send_email_summary")
            if isinstance(cfg, dict):
                email_tool_cfg = cfg
    admin_email_by_ctx = email_tool_cfg.get("admin_email_by_context") or {}
    from_email_by_ctx = email_tool_cfg.get("from_email_by_context") or {}
    rekeyed_admin = {}
    rekeyed_from = {}

    # Validate every context and separate valid rows from skips *before* we
    # open the transaction — skips are not errors and must not trigger rollback.
    rows = []
    skipped = []
    seen_slugs = set()
    for key, ctx in merged.items():
        src = ctx.pop("_source_file", None)
        prompt = ctx.get("prompt")
        if not prompt:
            skipped.append((key, "missing prompt"))
            continue
        provider = ctx.get("provider") or ""
        extra = {k: v for k, v in ctx.items() if k not in _FIRST_CLASS}
        now = _now()
        # CRIT-3: two context names can slugify to the same value
        # (e.g. "Sales-East" and "sales_east" -> "sales_east"). The `slug` column
        # is UNIQUE, so disambiguate deterministically; the original name is kept
        # in `display_name` and the engine resolves on that first, so legacy
        # dialplans using either original name still route correctly.
        slug = disambiguate_slug(key, seen_slugs)
        # H4: carry the legacy per-context email override (keyed by original name)
        # onto the agent row, and re-key the surviving map entry to the slug so the
        # global-tools resolution path resolves once context_name is the slug.
        # bot re-review (Finding 1): an explicit TOP-LEVEL per-context email key
        # (as emitted by export_agents_yaml.py) WINS over the legacy by_context map,
        # so an export -> re-migrate cycle restores the first-class columns instead
        # of dropping the values into extra_json. Fall back to the legacy map when
        # the top-level key is absent. email_enabled is tri-state (None/True/False).
        top_recipient = ctx.get("email_recipient")
        top_from = ctx.get("email_from")
        email_recipient = top_recipient if top_recipient is not None else admin_email_by_ctx.get(key)
        email_from = top_from if top_from is not None else from_email_by_ctx.get(key)
        email_enabled = ctx.get("email_enabled")
        if email_recipient is not None:
            rekeyed_admin[slug] = email_recipient
        if email_from is not None:
            rekeyed_from[slug] = email_from
        rows.append((
            uuid.uuid4().hex,
            slug,
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
            email_recipient,
            email_from,
            email_enabled,
        ))

    with store.conn:
        for r in rows:
            store.conn.execute(
                """INSERT INTO agents (id, slug, display_name, provider, voice, greeting,
                   prompt, tools_json, audio_profile, extra_json, is_operator_managed,
                   is_active, is_default, source_file, created_at, updated_at,
                   email_recipient, email_from, email_enabled)
                   VALUES (?,?,?,?,?,?,?,?,?,?,0,1,?,?,?,?,?,?,?)""",
                r,
            )
        store.conn.execute(
            "INSERT INTO schema_migrations (version, applied_at, contexts_hash) VALUES (?,?,?)",
            (MIGRATION_VERSION, _now(), h),
        )

    store._ensure_default_invariant()
    # LOW-A6: surface which agent became the default. When no context is literally
    # named "default", the invariant promotes the first-created active agent; making
    # that visible lets the UI/log show the operator what was auto-selected.
    default_row = store.get_default()
    # H4: expose the re-keyed send_email_summary map (original-name keys swapped for
    # slugs) preserving the global default. The migration has no YAML write-back
    # path, so this is returned for any consumer that persists the global tools cfg.
    email_rekey = {}
    if rekeyed_admin or rekeyed_from:
        cfg = dict(email_tool_cfg)
        cfg["admin_email_by_context"] = rekeyed_admin
        cfg["from_email_by_context"] = rekeyed_from
        email_rekey["send_email_summary"] = cfg
    return {
        "imported": len(rows),
        "skipped": skipped,
        "already_migrated": False,
        "default_slug": default_row["slug"] if default_row else None,
        "email_by_context_rekey": email_rekey,
    }


def migrate_if_needed(op_dir: str, yaml_path: str, contexts_dir: str,
                      db_filename: str = "agents.db") -> dict:
    """Run the one-time migration atomically so a failed/empty import never leaves
    an authoritative empty agents.db (CRIT-3).

    - If ``<op_dir>/<db_filename>`` already exists, open it and run the (idempotent)
      migration — a no-op if already migrated — so drift detection still works.
    - Otherwise migrate into a temporary DB and only promote it to the final path
      when at least one agent was imported. Nothing to migrate ⇒ no file is left,
      so the engine stays in YAML mode instead of treating an empty DB as
      authoritative.

    ``db_filename`` lets the caller honor a relocated ``AGENTS_DB_PATH`` whose
    basename differs from the default, so the seed path matches the stores' read
    path (the temp file is derived from it: ``<db_filename>.migrating``).
    """
    os.makedirs(op_dir, exist_ok=True)
    final = os.path.join(op_dir, db_filename)

    if os.path.exists(final):
        store = AgentsStore(db_path=final)
        try:
            return run_migration(store, yaml_path, contexts_dir)
        finally:
            store.close()

    tmp = os.path.join(op_dir, db_filename + ".migrating")
    for ext in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + ext)
        except OSError:
            pass

    store = AgentsStore(db_path=tmp)
    try:
        result = run_migration(store, yaml_path, contexts_dir)
        try:
            # Fold the WAL back into the main file so a single rename is complete.
            store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
    finally:
        store.close()

    if result.get("imported", 0) > 0:
        os.replace(tmp, final)
    # Remove any temp leftovers (the unpromoted tmp file, or stray -wal/-shm).
    for ext in ("", "-wal", "-shm"):
        leftover = tmp + ext
        if os.path.exists(leftover):
            try:
                os.remove(leftover)
            except OSError:
                pass
    return result


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
