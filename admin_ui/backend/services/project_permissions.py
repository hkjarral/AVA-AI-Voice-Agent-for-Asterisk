from __future__ import annotations

import argparse
import grp
import os
import pwd
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class PermissionRepairResult:
    repaired: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _grant_group_access(path: Path, gid: int, *, directory: bool) -> None:
    """Keep the host owner intact while granting the runtime group write access."""
    current_mode = stat.S_IMODE(path.stat().st_mode)
    required = stat.S_IRGRP | stat.S_IWGRP
    if directory:
        required |= stat.S_IXGRP
    os.chown(path, -1, gid)
    os.chmod(path, current_mode | required)


def prepare_project_write_access(
    project_root: str | Path,
    *,
    runtime_gid: int,
    writable_files: Iterable[str] | None = None,
) -> PermissionRepairResult:
    """Prepare the bind-mounted project paths the non-root Admin UI must mutate.

    The Admin UI persists YAML and environment changes with a temp-file + rename.
    Atomic replacement requires write access to the parent directory, not only the
    destination file. Production checkouts are often owned by root, so repairing
    files alone is insufficient.

    Only the project root, ``config`` directory, and known mutable files are
    changed. File owners and all existing permission bits are preserved; the
    runtime group is added and granted the minimum group access needed for atomic
    writes.
    """
    root = Path(project_root)
    result = PermissionRepairResult()
    mutable_files = tuple(
        writable_files
        or (
            ".env",
            "config/ai-agent.local.yaml",
            "config/users.json",
        )
    )

    for directory in (root, root / "config"):
        if not directory.is_dir():
            result.warnings.append(f"Writable project directory is missing: {directory}")
            continue
        try:
            _grant_group_access(directory, runtime_gid, directory=True)
            result.repaired.append(str(directory))
        except OSError as exc:
            result.warnings.append(f"Could not prepare {directory}: {exc}")

    for relative_path in mutable_files:
        path = root / relative_path
        if not path.exists():
            # The writable parent lets the application create optional files.
            continue
        try:
            _grant_group_access(path, runtime_gid, directory=False)
            result.repaired.append(str(path))
        except OSError as exc:
            result.warnings.append(f"Could not prepare {path}: {exc}")

    return result


def _runtime_gid(user_name: str) -> int:
    user = pwd.getpwnam(user_name)
    try:
        return grp.getgrnam(user_name).gr_gid
    except KeyError:
        return user.pw_gid


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Admin UI project write access")
    parser.add_argument("--project-root", default=os.getenv("PROJECT_ROOT", "/app/project"))
    parser.add_argument("--runtime-user", default="appuser")
    args = parser.parse_args()

    try:
        gid = _runtime_gid(args.runtime_user)
    except KeyError as exc:
        print(f"WARNING: Admin UI runtime user is unavailable: {exc}")
        return 0

    result = prepare_project_write_access(args.project_root, runtime_gid=gid)
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    if result.repaired:
        print(f"Prepared Admin UI write access for {len(result.repaired)} project paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
