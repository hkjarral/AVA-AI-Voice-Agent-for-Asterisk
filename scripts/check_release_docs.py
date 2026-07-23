#!/usr/bin/env python3
"""Fail when current-release documentation disagrees with CHANGELOG.md."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    changelog = read("CHANGELOG.md")
    releases = re.findall(
        r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}\s*$",
        changelog,
        flags=re.MULTILINE,
    )
    if not releases:
        print("release-docs: CHANGELOG.md has no dated release", file=sys.stderr)
        return 1

    latest = releases[0]
    latest_major, latest_minor, _ = (int(part) for part in latest.split("."))
    previous_train = next(
        (
            ".".join(version.split(".")[:2])
            for version in releases[1:]
            if tuple(map(int, version.split(".")[:2])) != (latest_major, latest_minor)
        ),
        None,
    )
    latest_train = f"{latest_major}.{latest_minor}"
    latest_anchor = latest.replace(".", "")

    checks: list[tuple[str, str, str]] = [
        ("README.md", f"version-{latest}-blue", "version badge"),
        (
            "README.md",
            f"docs/INSTALLATION.md#upgrade-to-v{latest_anchor}-existing-checkout",
            "current upgrade-guide link",
        ),
        ("docs/INSTALLATION.md", f"Guide (v{latest_train})", "guide title"),
        (
            "docs/INSTALLATION.md",
            f"## Upgrade to v{latest} (Existing Checkout)",
            "upgrade heading",
        ),
        ("docs/INSTALLATION.md", f"agent update --ref v{latest}", "pinned CLI command"),
        ("docs/INSTALLATION.md", f"AAVA_RECOVERY_REF=v{latest}", "pinned recovery command"),
        ("docs/MIGRATION.md", f"to v{latest}", "current migration section"),
        (
            "SECURITY.md",
            f"| {latest_train}.x   | :white_check_mark:",
            "current supported release train",
        ),
        ("docs/ROADMAP.md", f"**Latest Stable**: v{latest}", "roadmap release status"),
        (
            "docs/contributing/README.md",
            f"**Latest Stable Version:** {latest}",
            "contributor release status",
        ),
        ("scripts/README.md", f"--ref v{latest}", "recovery-script example"),
        ("scripts/update-recover.sh", f"AAVA_RECOVERY_REF=v{latest}", "recovery help example"),
        ("docs/CLI_TOOLS_GUIDE.md", f"agent update --ref v{latest}", "CLI release example"),
        (
            "docs/README.md",
            f"baselines/golden/v{latest}-validation-matrix.md",
            "release validation-matrix link",
        ),
    ]
    if previous_train:
        checks.append(
            (
                "SECURITY.md",
                f"| {previous_train}.x   | :white_check_mark:",
                "previous supported release train",
            )
        )

    failures: list[str] = []
    cache: dict[str, str] = {}
    for path, expected, label in checks:
        content = cache.setdefault(path, read(path))
        if expected not in content:
            failures.append(f"{path}: missing {label}: {expected!r}")

    migration_heading = next(
        (
            line.strip()
            for line in read("docs/MIGRATION.md").splitlines()
            if line.startswith("## ")
        ),
        "",
    )
    if f"v{latest}" not in migration_heading or "unreleased" in migration_heading.lower():
        failures.append(
            "docs/MIGRATION.md: first migration section must name the current release "
            "without marking it unreleased"
        )

    matrix = ROOT / "docs" / "baselines" / "golden" / f"v{latest}-validation-matrix.md"
    if not matrix.is_file():
        failures.append(f"missing current release validation matrix: {matrix.relative_to(ROOT)}")

    if failures:
        print("release documentation is inconsistent:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(
        f"release-docs: v{latest} is consistent across upgrade, migration, "
        "support, roadmap, recovery, and validation documentation"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
