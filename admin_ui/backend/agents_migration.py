"""YAML contexts merge + normalized hash + one-time migration into agents.db.
Merge semantics mirror src/config.py:_merge_external_contexts (inline wins;
external files keyed by 'name'; system_prompt→prompt). Parity is covered by
fixture tests, not cross-imports (admin_ui does not import src/ — decision D4)."""
import glob
import hashlib
import json
import os

import yaml


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
