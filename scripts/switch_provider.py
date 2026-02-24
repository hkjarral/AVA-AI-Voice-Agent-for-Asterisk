#!/usr/bin/env python3
"""Utility to switch the default provider in config/ai-agent.yaml.

This script keeps the YAML file formatting intact by performing a targeted
replacement of the `default_provider` line after validating that the provider
exists in the providers section.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Switch the default provider")
    parser.add_argument(
        "--config",
        default="config/ai-agent.yaml",
        help="Path to ai-agent YAML configuration (default: %(default)s)",
    )
    parser.add_argument(
        "--provider",
        required=True,
        help="Provider name to activate (must exist in providers mapping)",
    )
    return parser.parse_args()


def validate_provider(config_path: Path, provider: str) -> None:
    try:
        content = config_path.read_text()
    except FileNotFoundError as exc:
        print(f"Config file not found: {config_path}", file=sys.stderr)
        raise SystemExit(1) from exc

    if yaml:
        try:
            data = yaml.safe_load(content)  # type: ignore[arg-type]
        except yaml.YAMLError as exc:  # type: ignore[attr-defined]
            print(f"Failed to parse YAML: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

        providers = (data or {}).get("providers", {})
        if provider not in providers:
            print(
                f"Provider '{provider}' not found in config. "
                f"Available providers: {', '.join(sorted(providers.keys())) or 'none'}",
                file=sys.stderr,
            )
            raise SystemExit(1)

        if providers.get(provider, {}).get("enabled") is False:
            print(
                f"Warning: provider '{provider}' is marked as disabled in the config.",
                file=sys.stderr,
            )
    else:
        # Fallback: best-effort textual check
        search_token = f"\n  {provider}:\n"
        if search_token not in content:
            print(
                f"Provider '{provider}' not found (PyYAML unavailable for full validation).",
                file=sys.stderr,
            )
            raise SystemExit(1)


def update_default_provider(config_path: Path, provider: str) -> None:
    content = config_path.read_text()

    pattern = re.compile(r"^(\s*default_provider:\s*)([\"']?)([^\"'\n]+)([\"']?)",
                         re.MULTILINE)
    match = pattern.search(content)
    if not match:
        print(
            "Could not locate 'default_provider' in configuration file.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    new_line = f"{match.group(1)}\"{provider}\""
    updated_content = content[:match.start()] + new_line + content[match.end():]
    config_path.write_text(updated_content)


def main() -> None:
    args = parse_args()
    config_path = Path(args.config)
    provider = args.provider.strip()

    validate_provider(config_path, provider)
    update_default_provider(config_path, provider)
    print(f"✅ Updated default_provider to '{provider}' in {config_path}")


if __name__ == "__main__":  # pragma: no cover
    main()
