"""Disaster-recovery: dump agents.db back to an ai-agent.yaml-compatible contexts block.
Usage: docker exec admin_ui python -m export_agents_yaml > contexts-recovered.yaml"""
import json, sys, yaml
from agents_store import AgentsStore


def _safe_json(raw):
    """Parse a JSON string; return None (skip the field) on any decode error."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def export_yaml(store: AgentsStore) -> str:
    contexts = {}
    for a in store.list_all():
        ctx = {"provider": a["provider"], "prompt": a["prompt"]}
        for k in ("voice", "greeting"):
            if a[k]: ctx[k] = a[k]
        if a["audio_profile"]: ctx["profile"] = a["audio_profile"]
        tools = _safe_json(a["tools_json"])
        if tools is not None: ctx["tools"] = tools
        extra = _safe_json(a["extra_json"])
        if extra is not None: ctx.update(extra)
        contexts[a["slug"]] = ctx
    return yaml.safe_dump({"contexts": contexts}, sort_keys=True)

if __name__ == "__main__":
    sys.stdout.write(export_yaml(AgentsStore()))
