"""Sanitized support bundle (spec §12.3 per-column redaction policy).
Never contains .env, prompts, greetings, notes, recordings, transcripts, or tool URLs —
only redacted metadata (lengths + sha prefixes) and structure, so users can share it
for debugging without leaking secrets or business logic."""
import hashlib, io, json, zipfile
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from agents_store import AgentsStore

router = APIRouter()

# A _store() factory (like api/agents.py) so tests can monkeypatch the DB path.
def _store() -> AgentsStore:
    return AgentsStore()

KEEP = {"id", "slug", "provider", "voice", "audio_profile", "is_operator_managed",
        "is_active", "is_default", "source_file", "created_at", "updated_at"}
REDACT_LABEL = {"display_name": "name", "extension": "ext", "role_label": "role",
                "greeting": "greeting", "prompt": "prompt", "notes": "notes"}


def _redact(value, label: str):
    if value is None:
        return None
    sha = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"[{label} len={len(value)} sha={sha}]"


def _structure_only(raw):
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ["<unparseable>"]
    if isinstance(data, list):
        return [
            d.get("name", "<tool>") if isinstance(d, dict)
            else f"<tool len={len(d)}>" if isinstance(d, str)
            else "<tool>"
            for d in data
        ]
    if isinstance(data, dict):
        return sorted(data.keys())
    return ["<unknown>"]


def redact_agent(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if k in KEEP:
            out[k] = v
        elif k in REDACT_LABEL:
            out[k] = _redact(v, REDACT_LABEL[k])
        elif k in ("tools_json", "mcp_json"):
            out[k] = _structure_only(v)
        elif k == "extra_json":
            if not v:
                out[k] = None
            else:
                try:
                    d = json.loads(v)
                    out[k] = sorted(d.keys()) if isinstance(d, dict) else ["<non-dict>"]
                except json.JSONDecodeError:
                    out[k] = ["<unparseable>"]
    return out


@router.get("/support-bundle")
def support_bundle():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        agents = [redact_agent(a) for a in _store().list_all()]
        z.writestr("agents_redacted.json", json.dumps(agents, indent=2))
        z.writestr("BUNDLE_README.txt",
                   "AVA sanitized support bundle. Contains: redacted agent metadata, "
                   "system info. Never contains: .env, prompts, recordings, transcripts.")
        try:
            from api.system import get_basic_system_info
            z.writestr("system_info.json", json.dumps(get_basic_system_info(), indent=2))
        except Exception as e:
            z.writestr("system_info.json", json.dumps({"error": f"unavailable: {e}"}))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": "attachment; filename=ava-support-bundle.zip"})
