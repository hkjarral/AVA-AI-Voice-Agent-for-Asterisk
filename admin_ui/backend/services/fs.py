from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional


def atomic_write_text(path: str, content: str, *, mode_from_existing: bool = True,
                      preserve_owner: bool = False) -> None:
    dir_path = os.path.dirname(path) or "."
    previous = os.stat(path) if mode_from_existing and os.path.exists(path) else None
    temporary = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=dir_path, delete=False, suffix=".tmp")
    temp_path = Path(temporary.name)
    try:
        with temporary as stream:
            stream.write(content)
            if previous is not None:
                if preserve_owner:
                    current = os.fstat(stream.fileno())
                    if (current.st_uid, current.st_gid) != (previous.st_uid, previous.st_gid):
                        os.fchown(stream.fileno(), previous.st_uid, previous.st_gid)
                os.fchmod(stream.fileno(), previous.st_mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_lines(path: str, lines: Iterable[str], *, mode_from_existing: bool = True) -> None:
    # Normalize to single string so we can force trailing newline.
    content = "".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    atomic_write_text(path, content, mode_from_existing=mode_from_existing)


_ENV_KV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


@dataclass(frozen=True)
class EnvUpdateResult:
    updated_keys: List[str]
    added_keys: List[str]


def upsert_env_vars(
    env_path: str,
    updates: Dict[str, str],
    *,
    header: Optional[str] = None,
) -> EnvUpdateResult:
    """
    Update or add env vars in a .env file while preserving unrelated lines/comments.

    Rules:
    - Only rewrites lines that are simple KEY=VALUE assignments.
    - Preserves comments and unknown lines verbatim.
    - Ensures the file ends with a newline.
    - Uses an atomic replace.
    """
    if not updates:
        return EnvUpdateResult(updated_keys=[], added_keys=[])

    existing_lines: List[str] = []
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            existing_lines = f.readlines()

    # Track the last occurrence of each key so we update in place.
    key_to_line_idx: Dict[str, int] = {}
    for idx, line in enumerate(existing_lines):
        if line.lstrip().startswith("#"):
            continue
        match = _ENV_KV_RE.match(line.rstrip("\n"))
        if match:
            key_to_line_idx[match.group(1)] = idx

    new_lines = existing_lines[:]
    updated: List[str] = []
    added: List[str] = []

    # Ensure trailing newline on last line if file not empty.
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    for key, value in updates.items():
        if not key:
            continue
        line_content = f"{key}={value}\n"
        if key in key_to_line_idx:
            new_lines[key_to_line_idx[key]] = line_content
            updated.append(key)
        else:
            added.append(key)

    if added:
        if header:
            new_lines.append("\n" if (new_lines and new_lines[-1].strip() != "") else "")
            new_lines.append(f"# {header}\n")
        for key in added:
            new_lines.append(f"{key}={updates[key]}\n")

    atomic_write_lines(env_path, new_lines, mode_from_existing=True)
    return EnvUpdateResult(updated_keys=sorted(set(updated)), added_keys=sorted(set(added)))


def remove_env_vars(env_path: str, keys: Iterable[str]) -> None:
    key_set = {k for k in keys if k}
    if not key_set:
        return
    if not os.path.exists(env_path):
        return

    with open(env_path, "r") as f:
        lines = f.readlines()

    def should_keep(line: str) -> bool:
        if line.lstrip().startswith("#"):
            return True
        match = _ENV_KV_RE.match(line.rstrip("\n"))
        if not match:
            return True
        return match.group(1) not in key_set

    new_lines = [line for line in lines if should_keep(line)]
    atomic_write_lines(env_path, new_lines, mode_from_existing=True)

