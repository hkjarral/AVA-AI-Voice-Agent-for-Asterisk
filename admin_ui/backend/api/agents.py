"""Agents CRUD + stats + dialplan generator (A2) + templates (A3) + migration status."""
import json, os, sqlite3
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from agents_store import AgentsStore, slugify
from agents_migration import current_drift, acknowledge_drift, run_migration, \
    merged_effective_contexts
import settings  # for YAML paths

router = APIRouter()
CALL_HISTORY_DB = os.environ.get("CALL_HISTORY_DB_PATH", "/app/data/call_history.db")
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
    provider: str | None = None
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

class AgentOut(BaseModel):
    """Full agent row as stored in agents.db. Declares every column so attaching this
    as a response_model never drops a field (wire-compatible with the raw rows we
    returned before). The is_* flags stay int 0/1 — existing consumers depend on that
    shape, so they are NOT coerced to bool."""
    id: str
    slug: str
    display_name: str
    extension: str | None = None
    role_label: str | None = None
    provider: str
    voice: str | None = None
    greeting: str | None = None
    prompt: str
    tools_json: str | None = None
    mcp_json: str | None = None
    audio_profile: str | None = None
    extra_json: str | None = None
    is_operator_managed: int
    is_active: int
    is_default: int
    source_file: str | None = None
    created_at: str
    updated_at: str
    notes: str | None = None

class AgentSummaryResponse(BaseModel):
    active_agents: int
    active_calls: int
    total_routed: int
    total_transfers: int

class AgentStatsBatchItem(BaseModel):
    slug: str
    calls: int
    transfers: int
    avg_duration_seconds: float
    last_call: str | None = None

class DistributionItem(BaseModel):
    context_name: str
    count: int

class RoutingMethodsResponse(BaseModel):
    ai_agent: int
    ai_context: int
    default: int
    unknown: int

class AgentStatsResponse(BaseModel):
    calls_30d: int
    last_call: str | None = None

class DialplanResponse(BaseModel):
    dialplan: str
    extension: str
    stasis_app: str

@router.get("/agents", response_model=list[AgentOut])
def list_agents():
    return _store().list_all()

@router.get("/agents/templates")
def templates():
    with open(TEMPLATES_PATH) as f:
        return json.load(f)

@router.get("/agents/summary", response_model=AgentSummaryResponse)
async def summary():
    """KPI summary: active agents, active calls (from engine), total routed, total transfers."""
    store = _store()
    active_agents = store.count_active()

    total_routed = 0
    total_transfers = 0
    if os.path.exists(CALL_HISTORY_DB):
        try:
            with sqlite3.connect(f"file:{CALL_HISTORY_DB}?mode=ro", uri=True) as c:
                total_routed = c.execute("SELECT COUNT(*) FROM call_records").fetchone()[0]
                total_transfers = c.execute(
                    "SELECT COUNT(*) FROM call_records WHERE outcome='transferred'"
                ).fetchone()[0]
        except sqlite3.OperationalError:
            pass

    active_calls = 0
    try:
        import aiohttp
        ai_engine_url = os.getenv("AI_ENGINE_HEALTH_URL", "http://localhost:15000")
        headers = {}
        health_token = (os.getenv("HEALTH_API_TOKEN") or "").strip()
        if health_token:
            headers["Authorization"] = f"Bearer {health_token}"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{ai_engine_url}/sessions/stats",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                if resp.status == 200:
                    session_stats = await resp.json()
                    active_calls = session_stats.get("active_calls", 0)
    except Exception:
        active_calls = 0

    return {
        "active_agents": active_agents,
        "active_calls": active_calls,
        "total_routed": total_routed,
        "total_transfers": total_transfers,
    }

@router.get("/agents/stats-batch", response_model=list[AgentStatsBatchItem])
def stats_batch():
    """Per-agent call stats for all agents in the store."""
    store = _store()
    agents = store.list_all()

    call_data: dict = {}
    if os.path.exists(CALL_HISTORY_DB):
        try:
            with sqlite3.connect(f"file:{CALL_HISTORY_DB}?mode=ro", uri=True) as c:
                rows = c.execute(
                    "SELECT context_name, COUNT(*) c, "
                    "SUM(CASE WHEN outcome='transferred' THEN 1 ELSE 0 END) t, "
                    "AVG(duration_seconds) d, MAX(start_time) m "
                    "FROM call_records GROUP BY context_name"
                ).fetchall()
            # MED-A3: call_records.context_name holds the raw dialplan name (e.g.
            # "Tool_Example"), while agents are keyed by slug ("tool_example"). Fold
            # the per-context aggregates into slug buckets so per-agent stats match
            # the agent rows instead of silently under-counting legacy/non-slug-safe
            # names. Multiple raw names that map to one slug are merged (duration is a
            # call-weighted mean).
            acc: dict = {}  # slug -> [calls, transfers, dur_sum, dur_cnt, last]
            for ctx, cnt, transfers, avg_dur, last in rows:
                key = slugify(ctx) if ctx else ctx
                a = acc.setdefault(key, [0, 0, 0.0, 0, None])
                a[0] += cnt
                a[1] += (transfers or 0)
                if avg_dur is not None:
                    a[2] += avg_dur * cnt
                    a[3] += cnt
                if last and (a[4] is None or last > a[4]):
                    a[4] = last
            for key, (calls, transfers, dur_sum, dur_cnt, last) in acc.items():
                avg = (dur_sum / dur_cnt) if dur_cnt else None
                call_data[key] = (calls, transfers, avg, last)
        except sqlite3.OperationalError:
            pass

    result = []
    for agent in agents:
        slug = agent["slug"]
        if slug in call_data:
            cnt, transfers, avg_dur, last = call_data[slug]
            result.append({
                "slug": slug,
                "calls": cnt,
                "transfers": transfers,
                "avg_duration_seconds": round(avg_dur, 1) if avg_dur is not None else 0.0,
                "last_call": last,
            })
        else:
            result.append({
                "slug": slug,
                "calls": 0,
                "transfers": 0,
                "avg_duration_seconds": 0.0,
                "last_call": None,
            })
    return result

@router.get("/agents/distribution", response_model=list[DistributionItem])
def distribution():
    """Call distribution by context_name, ordered by count desc. Excludes NULL/empty names."""
    if not os.path.exists(CALL_HISTORY_DB):
        return []
    try:
        with sqlite3.connect(f"file:{CALL_HISTORY_DB}?mode=ro", uri=True) as c:
            rows = c.execute(
                "SELECT context_name, COUNT(*) c FROM call_records "
                "WHERE context_name IS NOT NULL AND context_name != '' "
                "GROUP BY context_name ORDER BY c DESC"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"context_name": ctx, "count": cnt} for ctx, cnt in rows]

@router.get("/agents/routing-methods", response_model=RoutingMethodsResponse)
def routing_methods():
    """Routing method breakdown: ai_agent, ai_context, default, unknown (NULL/other)."""
    result = {"ai_agent": 0, "ai_context": 0, "default": 0, "unknown": 0}
    if not os.path.exists(CALL_HISTORY_DB):
        return result
    try:
        with sqlite3.connect(f"file:{CALL_HISTORY_DB}?mode=ro", uri=True) as c:
            rows = c.execute(
                "SELECT routing_method, COUNT(*) FROM call_records GROUP BY routing_method"
            ).fetchall()
    except sqlite3.OperationalError:
        # The routing_method column may not exist yet on a freshly-upgraded install
        # (the engine/CallHistoryStore migration adds it on first use). Count existing
        # rows as 'unknown' so the panel agrees with the other dashboards instead of
        # hiding historical calls. If even the table is absent, fall through to zeros.
        try:
            with sqlite3.connect(f"file:{CALL_HISTORY_DB}?mode=ro", uri=True) as c:
                result["unknown"] = c.execute("SELECT COUNT(*) FROM call_records").fetchone()[0]
        except sqlite3.OperationalError:
            pass
        return result
    for method, cnt in rows:
        if method in ("ai_agent", "ai_context", "default"):
            result[method] += cnt
        else:
            result["unknown"] += cnt
    return result


def _engine_ok(provider, extra_json) -> bool:
    """An agent must have either a monolithic provider or a pipeline (in extra_json)."""
    if (provider or "").strip():
        return True
    try:
        extra = json.loads(extra_json) if extra_json else {}
    except (json.JSONDecodeError, TypeError):
        extra = {}
    return bool(isinstance(extra, dict) and str(extra.get("pipeline") or "").strip())

@router.post("/agents", status_code=201, response_model=AgentOut)
def create_agent(body: AgentIn, request: Request):
    data = body.model_dump()
    if not _engine_ok(data.get("provider"), data.get("extra_json")):
        raise HTTPException(422, "agent must have a provider or a pipeline")
    data["provider"] = (data.get("provider") or "").strip()
    try:
        return _store().create(**data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

@router.patch("/agents/{slug}", response_model=AgentOut)
def patch_agent(slug: str, body: AgentPatch):
    store = _store()
    existing = store.get_by_slug(slug)
    if not existing:
        raise HTTPException(404, "agent not found")
    # Apply exactly the fields the client sent (exclude_unset), INCLUDING explicit
    # nulls — sending tools_json/extra_json/mcp_json=null must clear the column, so
    # the engine doesn't keep serving stale config (e.g. an old pipeline after the
    # agent is switched to a provider). Unsent fields are left untouched.
    fields = body.model_dump(exclude_unset=True)
    if "provider" in fields or "extra_json" in fields:
        eff_provider = fields.get("provider", existing.get("provider"))
        eff_extra = fields.get("extra_json", existing.get("extra_json"))
        if not _engine_ok(eff_provider, eff_extra):
            raise HTTPException(422, "agent must have a provider or a pipeline")
    if "provider" in fields:
        fields["provider"] = (fields["provider"] or "").strip()
    if "is_active" in fields:
        promoted = store.set_active(slug, fields.pop("is_active"))
        if promoted:                       # A4: surface promotion to the UI
            store.update(promoted, notes=None)  # no-op write keeps updated_at honest
    return store.update(slug, **fields) if fields else store.get_by_slug(slug)

@router.post("/agents/{slug}/default", response_model=AgentOut)
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

# NOTE: declared AFTER all literal /agents/... GET routes (templates, summary,
# stats-batch, distribution, routing-methods) so the {slug} path param does not
# shadow them. FastAPI matches in declaration order.
@router.get("/agents/{slug}", response_model=AgentOut)
def get_agent(slug: str):
    row = _store().get_by_slug(slug)
    if not row:
        raise HTTPException(404, "agent not found")
    return row

@router.get("/agents/{slug}/stats", response_model=AgentStatsResponse)
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

@router.get("/agents/{slug}/dialplan", response_model=DialplanResponse)
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
