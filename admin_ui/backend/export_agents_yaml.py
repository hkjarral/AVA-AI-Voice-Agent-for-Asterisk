"""Disaster-recovery: dump agents.db back to an ai-agent.yaml-compatible contexts block.
Usage: docker exec admin_ui python -m export_agents_yaml > contexts-recovered.yaml"""
import json, sys, yaml
from agents_store import AgentsStore

def export_yaml(store: AgentsStore) -> str:
    contexts = {}
    for a in store.list_all():
        ctx = {"provider": a["provider"], "prompt": a["prompt"]}
        for k in ("voice", "greeting"):
            if a[k]: ctx[k] = a[k]
        if a["audio_profile"]: ctx["profile"] = a["audio_profile"]
        if a["tools_json"]: ctx["tools"] = json.loads(a["tools_json"])
        if a["extra_json"]: ctx.update(json.loads(a["extra_json"]))
        contexts[a["slug"]] = ctx
    return yaml.safe_dump({"contexts": contexts}, sort_keys=True)

if __name__ == "__main__":
    sys.stdout.write(export_yaml(AgentsStore()))
