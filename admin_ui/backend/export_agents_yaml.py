"""Disaster-recovery: dump agents.db back to an ai-agent.yaml-compatible contexts block.
Usage: docker exec admin_ui python -m export_agents_yaml > contexts-recovered.yaml"""
import argparse
import json
import sys

import yaml
from agents_store import AgentsStore
import settings


if settings.PROJECT_ROOT not in sys.path:
    sys.path.insert(0, settings.PROJECT_ROOT)
from src.tools.telephony.hangup_policy import (
    HangupPolicyConfigError,
    dump_agent_hangup_policy,
)


def _safe_json(raw):
    """Parse a JSON string; return None (skip the field) on any decode error."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def export_yaml(store: AgentsStore, *, redact: bool = False) -> str:
    contexts = {}
    for a in store.list_all():
        extra = _safe_json(a["extra_json"])
        ctx = dict(extra) if isinstance(extra, dict) else {}
        ctx.update({"provider": a["provider"], "prompt": a["prompt"]})
        for k in ("voice", "greeting"):
            if a[k]:
                ctx[k] = a[k]
        # LOW-A5: preserve operator metadata across an export -> delete -> re-migrate
        # cycle. These aren't first-class YAML context keys, so on re-import they land
        # in extra_json (harmless to the engine) rather than being silently dropped.
        for k in ("extension", "role_label", "notes", "email_recipient", "email_from"):
            if a[k]:
                ctx[k] = a[k]
        # email_enabled is a tri-state INTEGER (NULL=inherit, 0=off, 1=on); emit only
        # when explicitly set so an explicit 0 round-trips and NULL stays absent.
        if a["email_enabled"] is not None:
            ctx["email_enabled"] = a["email_enabled"]
        if a["audio_profile"]:
            ctx["profile"] = a["audio_profile"]
        tools = _safe_json(a["tools_json"])
        if tools is not None:
            ctx["tools"] = tools
        tool_configs = _safe_json(a.get("tool_configs_json"))
        if tool_configs is not None:
            ctx["tool_configs"] = tool_configs
        raw_hangup_policy = a.get("hangup_policy_json")
        if raw_hangup_policy:
            # Unlike optional diagnostic fields, this policy controls an
            # irreversible call action. Refuse a lossy disaster-recovery
            # export instead of silently converting corrupt data to inherit.
            try:
                canonical = dump_agent_hangup_policy(raw_hangup_policy)
            except HangupPolicyConfigError as exc:
                raise HangupPolicyConfigError(
                    f"Agent {a['slug']!r} has an invalid hangup policy: {exc}"
                ) from exc
            if canonical is not None:
                ctx["hangup_policy"] = json.loads(canonical)
        contexts[a["slug"]] = ctx
    document = {"contexts": contexts}
    if redact:
        from services.yaml_secrets import omit_private_fields
        document = omit_private_fields(document)
    return yaml.safe_dump(document, sort_keys=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--redact", action="store_true", help="Omit fields containing stored credentials.")
    options = parser.parse_args()
    if not options.redact:
        print("Warning: this recovery export may contain credentials. Store it securely.", file=sys.stderr)
    with AgentsStore() as store:
        sys.stdout.write(export_yaml(store, redact=options.redact))
