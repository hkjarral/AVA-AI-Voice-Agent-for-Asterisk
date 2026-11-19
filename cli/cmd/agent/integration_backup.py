"""Private integration snapshots; never restore booking state automatically."""
import os
from contextlib import closing
from pathlib import Path
import shutil
import shlex
import sqlite3
import stat
import sys
import time


def backup(root, destination):
    supplied_root = Path(os.path.abspath(root))
    root = supplied_root.resolve()
    destination = Path(os.path.abspath(destination))
    if destination.is_symlink():
        raise ValueError("symlinked backup destination")
    try:
        relative_destination = destination.relative_to(supplied_root)
    except ValueError:
        try:
            relative_destination = destination.relative_to(root)
        except ValueError:
            relative_destination = None
    if relative_destination is not None:
        destination = root / relative_destination
    else:
        info = destination.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("external destination must be private and owned")
        destination = destination.resolve()

    def within(path, parent):
        return os.path.commonpath((str(path), str(parent))) == str(parent)

    if destination.is_symlink() or (relative_destination is not None and not within(destination.resolve(), root)):
        raise ValueError("unsafe backup destination")

    def private_directory(path):
        for parent in [path, *path.parents]:
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError("symlinked backup directory")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)

    private_directory(destination)

    def source_path(value):
        if "$" in value:
            raise ValueError("expanded paths require a manual snapshot")
        for prefix, relative in (("/app/project/", ""), ("/app/data/", "data/")):
            if value.startswith(prefix):
                value = relative + value[len(prefix):]
                break
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        path = Path(os.path.abspath(path))
        relative = path.relative_to(root)
        for parent in [path, *path.parents]:
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError("symlinked integration state")
        if path == root or within(destination.resolve(), path):
            raise ValueError("overlapping integration state")
        return path, relative

    settings = {}
    env_file = root / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            key, separator, value = line.partition("=")
            if separator and key.strip() in {"CALENDLY_DB_PATH", "CALENDLY_TOKEN_PATH"}:
                parts = shlex.split(value, comments=True)
                if len(parts) > 1:
                    raise ValueError("ambiguous integration path")
                settings[key.strip()] = parts[0] if parts else ""

    def configured(key, default):
        return source_path(os.environ.get(key) or settings.get(key) or default)

    def copy_secret(path):
        path, relative = source_path(str(path))
        if not path.exists():
            return
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("non-file integration secret")
            target = destination / relative
            private_directory(target.parent)
            with os.fdopen(fd, "rb", closefd=False) as source:
                out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(out, "wb") as output:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
        finally:
            os.close(fd)

    def raise_walk_error(error):
        raise error

    secrets, _ = source_path("secrets")
    if secrets.exists():
        if not secrets.is_dir():
            raise ValueError("non-directory integration secrets")
        for directory, directories, files in os.walk(secrets, followlinks=False, onerror=raise_walk_error):
            for name in directories:
                source_path(str(Path(directory) / name))
            for name in files:
                copy_secret(Path(directory) / name)
    token, token_relative = configured("CALENDLY_TOKEN_PATH", "secrets/calendly-default-token.json")
    if token_relative.parts[0] != "secrets":
        copy_secret(token)

    journal, relative = configured("CALENDLY_DB_PATH", "data/operator/calendly.db")
    if journal.exists():
        if not stat.S_ISREG(journal.stat().st_mode):
            raise ValueError("non-file integration journal")
        target = destination / relative
        private_directory(target.parent)
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        deadline = time.monotonic() + 30

        def progress(*_):
            if time.monotonic() >= deadline:
                raise TimeoutError("integration snapshot deadline")

        try:
            with closing(sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
                with closing(sqlite3.connect(target)) as output:
                    output.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                    source.backup(output, pages=256, progress=progress, sleep=0.01)
                    progress()
                    output.execute("PRAGMA journal_mode=DELETE")
                    progress()
                    if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("invalid integration snapshot")
                    progress()
        except Exception:
            if target.exists():
                target.unlink()
            raise

    # This non-secret marker preserves the erasure obligation after disconnect
    # and must travel with the journal during update recovery.
    copy_secret(journal.with_name(journal.name + ".configured"))


if __name__ == "__main__":
    try:
        backup(sys.argv[1], sys.argv[2])
    except Exception:
        raise SystemExit("Integration backup failed; preserve the journal and credentials manually before updating.") from None
