#!/usr/bin/env python3
"""Apply or revert the temporary tool-guidance trial for GitHub issue #625.

This script changes only the provider-facing description of
``update_call_metadata``. It does not change metadata permissions, persistence,
configuration, or the Admin UI. The replacement is exact and version-guarded:
an unfamiliar source file is never modified.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys
import tempfile


TARGET_RELATIVE = Path("src/tools/business/update_call_metadata.py")

ORIGINAL = '''        description = (
            "Correct one operator-approved, non-authoritative metadata value for this call. "
            "This does not update a CRM, caller identity, routing, consent, transfer, or disposition."
        )'''

TRIAL = '''        description = (
            "Use this tool whenever the caller clearly corrects or confirms a replacement value "
            "for one of the allowed metadata fields. Call the tool before telling the caller that "
            "the value was updated; do not merely acknowledge the correction conversationally. "
            "Only use a replacement value that the caller clearly provided or confirmed. "
            "This changes call-local metadata and post-call outputs only; it does not update a CRM, "
            "caller identity, routing, consent, transfer, or disposition."
        )'''


def _target(repo: Path) -> Path:
    repo = repo.expanduser().resolve()
    target = (repo / TARGET_RELATIVE).resolve()
    try:
        target.relative_to(repo)
    except ValueError as exc:
        raise RuntimeError(f"Refusing target outside repository: {target}") from exc
    if not target.is_file():
        raise RuntimeError(f"Expected source file was not found: {target}")
    return target


def _state(text: str) -> str:
    original_count = text.count(ORIGINAL)
    trial_count = text.count(TRIAL)
    if original_count == 1 and trial_count == 0:
        return "original"
    if original_count == 0 and trial_count == 1:
        return "trial"
    return "unsupported"


def _atomic_write(target: Path, text: str) -> None:
    mode = stat.S_IMODE(target.stat().st_mode)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=target.parent,
            prefix=f".{target.name}.issue625-",
            delete=False,
        ) as temporary:
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, target)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def check(repo: Path) -> int:
    target = _target(repo)
    state = _state(target.read_text(encoding="utf-8"))
    if state == "trial":
        print(f"APPLIED: issue #625 guidance trial is present in {target}")
        return 0
    if state == "original":
        print(f"NOT APPLIED: {target} still has the release guidance")
        return 1
    print(
        f"UNSUPPORTED: {target} does not match the known release or trial text; no change made",
        file=sys.stderr,
    )
    return 2


def apply(repo: Path) -> int:
    target = _target(repo)
    text = target.read_text(encoding="utf-8")
    state = _state(text)
    if state == "trial":
        print(f"ALREADY APPLIED: {target}")
        return 0
    if state != "original":
        print(
            f"REFUSED: {target} does not match the expected release source; no change made",
            file=sys.stderr,
        )
        return 2
    _atomic_write(target, text.replace(ORIGINAL, TRIAL, 1))
    print(f"APPLIED: issue #625 guidance trial in {target}")
    print("Next: rebuild and recreate the ai_engine container, then place a test call.")
    return 0


def revert(repo: Path) -> int:
    target = _target(repo)
    text = target.read_text(encoding="utf-8")
    state = _state(text)
    if state == "original":
        print(f"ALREADY REVERTED: {target}")
        return 0
    if state != "trial":
        print(
            f"REFUSED: {target} does not match the expected trial source; no change made",
            file=sys.stderr,
        )
        return 2
    _atomic_write(target, text.replace(TRIAL, ORIGINAL, 1))
    print(f"REVERTED: issue #625 guidance trial in {target}")
    print("Next: rebuild and recreate the ai_engine container to restore release behavior.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply, check, or revert the temporary GitHub issue #625 guidance trial."
    )
    parser.add_argument("action", choices=("apply", "check", "revert"))
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="AAVA repository root (default: parent of this scripts directory)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return {"apply": apply, "check": check, "revert": revert}[args.action](args.repo)
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
