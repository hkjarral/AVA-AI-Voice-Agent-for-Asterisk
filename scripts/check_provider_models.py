#!/usr/bin/env python3
"""Fail CI when a provider model catalog drifts from its consumers."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FISH_CATALOG = ROOT / "admin_ui/frontend/src/config/fishAudioModels.json"
FISH_DEFAULT = "s2.1-pro"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []
    try:
        models = json.loads(FISH_CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"provider-models: cannot read Fish Audio catalog: {exc}", file=sys.stderr)
        return 1

    if not isinstance(models, list) or not models:
        failures.append("Fish Audio catalog must be a non-empty JSON array")
        models = []
    elif any(not isinstance(model, str) or not model.strip() for model in models):
        failures.append("every Fish Audio model must be a non-empty string")
    if len(models) != len(set(models)):
        failures.append("Fish Audio catalog contains duplicate model identifiers")
    if FISH_DEFAULT not in models:
        failures.append(f"Fish Audio default {FISH_DEFAULT!r} is missing from the catalog")

    registry = read("admin_ui/frontend/src/config/modularProviderSubtypes.ts")
    if "suggestions: FISH_AUDIO_MODELS" not in registry or "type: 'select'" not in registry:
        failures.append("Admin UI Fish Audio model field must use the catalog as a select")

    config_source = read("src/config.py")
    default_match = re.search(
        r"class FishAudioProviderConfig\b.*?model: str = Field\(default=\"([^\"]+)\"\)",
        config_source,
        flags=re.DOTALL,
    )
    if not default_match or default_match.group(1) != FISH_DEFAULT:
        failures.append(f"runtime Fish Audio default must remain {FISH_DEFAULT!r}")

    sample_config = read("config/ai-agent.yaml")
    if not re.search(r"(?ms)^  fishaudio_tts:\n.*?^    model: s2\.1-pro\s*$", sample_config):
        failures.append("config/ai-agent.yaml must expose the catalog default")

    for relative in ("docs/Provider-FishAudio-Setup.md", "docs/Configuration-Reference.md"):
        content = read(relative)
        for model in models:
            if f"`{model}`" not in content and model not in content:
                failures.append(f"{relative} does not mention Fish Audio model {model!r}")

    if failures:
        print("provider model catalogs are inconsistent:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(
        "provider-models: Fish Audio catalog is valid and synchronized "
        f"({', '.join(models)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
