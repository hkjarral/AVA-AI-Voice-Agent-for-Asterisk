"""Non-secret onboarding progress. Configuration and Agents retain ownership of settings."""
import json
import logging
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

import settings
from services.fs import atomic_write_text


def effective_config() -> dict:
    """Do not silently replace an unreadable operator override with base defaults."""
    from api.config import _read_merged_config_dict, _safe_load_no_duplicates
    for filename in (settings.CONFIG_PATH, settings.LOCAL_CONFIG_PATH):
        path = Path(filename)
        if path.exists():
            value = _safe_load_no_duplicates(path.read_text())
            if value is not None and not isinstance(value, dict):
                raise ValueError("Configuration must be a YAML mapping")
    return _read_merged_config_dict() or {}


def state_path() -> Path:
    return Path(settings.PROJECT_ROOT) / "data" / "operator" / "onboarding.json"


def read_state() -> dict:
    try:
        value = json.loads(state_path().read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def write_state(stage: str) -> None:
    # Callers hold the shared configuration lock. Never persist keys/form drafts.
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(str(path), json.dumps({"version": 1, "stage": stage, "revision": uuid.uuid4().hex}) + "\n", mode_from_existing=True)


def active_agents() -> list[dict]:
    from agents_store import DB_DEFAULT
    path = Path(os.getenv("AGENTS_DB_PATH", DB_DEFAULT))
    if not path.exists():
        return []
    # Status checks must not create a DB and shadow pending legacy migration.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM agents WHERE is_active=1 ORDER BY is_default DESC, slug")]


def status() -> dict:
    stage = read_state().get("stage")
    if stage == "in_progress":
        return {"configured": False, "stage": stage, "resume_required": True}
    if stage in {"configuration_saved", "verified", "manual"}:
        return {"configured": True, "stage": stage, "resume_required": stage == "configuration_saved"}
    # Upgrades with real Agents or pending legacy Contexts are already configured.
    # A preflight-created .env alone is never evidence of completed setup.
    from agents_migration import merged_effective_contexts
    try:
        existing = bool(active_agents()) or bool(merged_effective_contexts(
            settings.CONFIG_PATH, str(Path(settings.CONFIG_PATH).parent / "contexts")
        ))
    except Exception:
        # Keep repair pages reachable when an existing config or DB is unreadable.
        logging.getLogger(__name__).warning("Could not classify existing setup; allowing configuration repair")
        existing = True
    return {"configured": existing, "stage": "existing" if existing else "not_started", "resume_required": False}


def handoff(config: dict) -> dict:
    from services.dialplan import agent_dialplan, stasis_app
    rows = active_agents()
    selected = next((row for row in rows if row.get("is_default")), None)
    app = stasis_app(config)
    return {
        "stage": read_state().get("stage", "existing"),
        "agent_slug": selected["slug"] if selected else None,
        "agent_provider": selected["provider"] if selected else None,
        "agent_pipeline": json.loads(selected.get("extra_json") or "{}").get("pipeline") if selected else None,
        "app_name": app,
        "dialplan": agent_dialplan(selected["slug"], app) if selected else None,
        "destination": "from-ai-agent,s,1",
        "audio_transport": config.get("audio_transport", "audiosocket"),
        "message": None if selected else "Select an active default Agent in Agents. If legacy import is pending, start the engine first.",
    }
