"""Agents CRUD + stats + dialplan generator (A2) + templates (A3) + migration status."""
import json, os, sqlite3
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from agents_store import AgentsStore, slugify
from agents_migration import current_drift, acknowledge_drift, run_migration, \
    merged_effective_contexts
import settings  # for YAML paths

router = APIRouter()
CALL_HISTORY_DB = os.environ.get("CALL_HISTORY_DB", "/app/data/call_history.db")
TEMPLATES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "agent_templates.json")
# CORRECTION vs plan: the real default Stasis app is "asterisk-ai-voice-agent"
# (confirmed from engine StasisStart logs + golden baselines), NOT "ai-voice-agent".
STASIS_APP = os.environ.get("ASTERISK_APP_NAME", "asterisk-ai-voice-agent")

def _store() -> AgentsStore:
    return AgentsStore()

def _yaml_path() -> str:
    return settings.CONFIG_PATH

def _contexts_dir() -> str:
    return os.path.join(os.path.dirname(settings.CONFIG_PATH), "contexts")

class AgentIn(BaseModel):
    display_name: str
    provider: str
    prompt: str
    slug: str | None = None
    extension: str | None = None
    role_label: str | None = None
    voice: str | None = None
    greeting: str | None = None
    audio_profile: str | None = None
    tools_json: str | None = None
    mcp_json: str | None = None
    extra_json: str | None = None
    notes: str | None = None

class AgentPatch(BaseModel):
    display_name: str | None = None
    provider: str | None = None
    prompt: str | None = None
    extension: str | None = None
    role_label: str | None = None
    voice: str | None = None
    greeting: str | None = None
    audio_profile: str | None = None
    tools_json: str | None = None
    mcp_json: str | None = None
    extra_json: str | None = None
    notes: str | None = None
    is_active: bool | None = None

@router.get("/agents")
def list_agents():
    return _store().list_all()

@router.get("/agents/templates")
def templates():
    with open(TEMPLATES_PATH) as f:
        return json.load(f)

@router.post("/agents", status_code=201)
def create_agent(body: AgentIn, request: Request):
    try:
        return _store().create(**body.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))

@router.patch("/agents/{slug}")
def patch_agent(slug: str, body: AgentPatch):
    store = _store()
    if not store.get_by_slug(slug):
        raise HTTPException(404, "agent not found")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "is_active" in fields:
        promoted = store.set_active(slug, fields.pop("is_active"))
        if promoted:                       # A4: surface promotion to the UI
            store.update(promoted, notes=None)  # no-op write keeps updated_at honest
    return store.update(slug, **fields) if fields else store.get_by_slug(slug)

@router.post("/agents/{slug}/default")
def set_default(slug: str):
    store = _store()
    if not store.get_by_slug(slug):
        raise HTTPException(404)
    store.set_default(slug)
    return store.get_by_slug(slug)

@router.delete("/agents/{slug}", status_code=204)
def delete_agent(slug: str, request: Request):
    store = _store()
    row = store.get_by_slug(slug)
    if not row:
        raise HTTPException(404)
    if row["is_default"] and store.count_active() > 1:
        promoted = store.delete(slug)
        request.app.state.last_default_promotion = promoted   # A4 banner source
    else:
        store.delete(slug)

@router.get("/agents/{slug}/stats")
def stats(slug: str):
    if not _store().get_by_slug(slug):
        raise HTTPException(404)
    if not os.path.exists(CALL_HISTORY_DB):
        return {"calls_30d": 0, "last_call": None}
    with sqlite3.connect(f"file:{CALL_HISTORY_DB}?mode=ro", uri=True) as c:
        calls = c.execute("SELECT COUNT(*) FROM call_records WHERE context_name=? "
                          "AND start_time >= datetime('now','-30 days')", (slug,)).fetchone()[0]
        last = c.execute("SELECT MAX(start_time) FROM call_records WHERE context_name=?",
                         (slug,)).fetchone()[0]
    return {"calls_30d": calls, "last_call": last}

@router.get("/agents/{slug}/dialplan")
def dialplan(slug: str):
    row = _store().get_by_slug(slug)
    if not row:
        raise HTTPException(404)
    ext = row["extension"] or "XXXX"
    safe_name = (row['display_name'] or "").replace('\n', ' ').replace('\r', '')
    text = (
        f"; AVA agent: {safe_name} — paste into extensions_custom.conf\n"
        f"[from-internal-custom]\n"
        f"exten => {ext},1,NoOp(AVA agent {slug})\n"
        f" same => n,Set(AI_AGENT={slug})\n"
        f" same => n,Stasis({STASIS_APP})\n"
        f" same => n,Hangup()\n"
        f"; AI_CONTEXT={slug} also works (legacy variable, still supported)\n")
    return {"dialplan": text, "extension": ext, "stasis_app": STASIS_APP}

@router.get("/agents-migration/status")
def migration_status(request: Request):
    store = _store()
    drift = current_drift(store, _yaml_path(), _contexts_dir())
    return {
        "migration": getattr(request.app.state, "agents_migration_result", None),
        "drift": drift,
        "last_default_promotion": getattr(request.app.state, "last_default_promotion", None),
    }

@router.post("/agents-migration/acknowledge")
def migration_ack():
    acknowledge_drift(_store(), _yaml_path(), _contexts_dir())
    return {"ok": True}

@router.post("/agents-migration/reconcile")
def migration_reconcile():
    """Re-import YAML contexts: upsert by slug (spec §11 'Import YAML changes')."""
    store = _store()
    merged = merged_effective_contexts(_yaml_path(), _contexts_dir())
    changed = []
    for key, ctx in merged.items():
        src = ctx.pop("_source_file", None)
        slug_key = slugify(key)
        existing = store.get_by_slug(slug_key)
        if existing is None and ctx.get("prompt"):
            store.create(display_name=key, provider=ctx.get("provider", ""),
                         prompt=ctx["prompt"], slug=slug_key,
                         is_operator_managed=0, source_file=src)
            changed.append(("added", slug_key))
        elif existing and ctx.get("prompt") and ctx["prompt"] != existing["prompt"]:
            store.update(slug_key, prompt=ctx["prompt"])
            changed.append(("updated", slug_key))
    acknowledge_drift(store, _yaml_path(), _contexts_dir())
    return {"changed": changed}
