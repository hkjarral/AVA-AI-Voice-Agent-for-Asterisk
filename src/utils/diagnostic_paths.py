"""Private storage paths for opt-in diagnostic audio artifacts."""

import os
import stat


# These established paths are intentionally ephemeral and are consumed by
# scripts/rca_collect.sh. They are safe only when opened through
# prepare_private_diagnostic_dir(), which rejects symlinks and foreign owners.
DEFAULT_DIAGNOSTIC_TAP_DIR = "/tmp/ai-engine-taps"  # noqa: S108
DEFAULT_DIAGNOSTIC_CAPTURE_DIR = "/tmp/ai-engine-captures"  # noqa: S108


class UnsafeDiagnosticPathError(RuntimeError):
    """Raised when a diagnostics directory cannot be trusted for audio writes."""


def _directory_open_flags() -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required):
        raise UnsafeDiagnosticPathError(
            "secure diagnostic directories require O_DIRECTORY and O_NOFOLLOW"
        )
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _validate_ancestor(path: str, metadata: os.stat_result, effective_uid: int) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise UnsafeDiagnosticPathError(f"diagnostic path ancestor is not a directory: {path}")
    if metadata.st_uid not in (0, effective_uid):
        raise UnsafeDiagnosticPathError(
            f"diagnostic path ancestor is owned by an unexpected user: {path}"
        )
    if metadata.st_mode & 0o022:
        trusted_sticky_root = metadata.st_uid == 0 and bool(metadata.st_mode & stat.S_ISVTX)
        if not trusted_sticky_root:
            raise UnsafeDiagnosticPathError(
                f"diagnostic path ancestor is writable by another user: {path}"
            )


def _resolve_trusted_tmp_alias(path: str) -> str:
    """Resolve macOS's root-owned ``/tmp`` alias without accepting user symlinks."""

    if path != "/tmp" and not path.startswith("/tmp/"):
        return path
    try:
        metadata = os.lstat("/tmp")
    except OSError:
        return path
    if not stat.S_ISLNK(metadata.st_mode):
        return path
    if metadata.st_uid != 0:
        raise UnsafeDiagnosticPathError("/tmp alias is not owned by root")

    target = os.readlink("/tmp")
    if not os.path.isabs(target):
        target = os.path.join(os.path.sep, target)
    suffix = path.removeprefix("/tmp").lstrip(os.path.sep)
    return os.path.normpath(os.path.join(target, suffix))


def prepare_private_diagnostic_dir(path: str) -> str:
    """Create or validate an application-owned diagnostic directory.

    Directory components are traversed using directory file descriptors and
    ``O_NOFOLLOW``. Existing symlinks, foreign-owned components, and unsafe
    writable ancestors are rejected. The final directory must be owned by the
    current process and is restricted to mode ``0700``.
    """

    raw_path = str(path or "").strip()
    if not raw_path:
        raise UnsafeDiagnosticPathError("diagnostic directory must not be empty")

    normalized = os.path.normpath(os.path.abspath(os.path.expanduser(raw_path)))
    normalized = _resolve_trusted_tmp_alias(normalized)
    if normalized == os.path.sep:
        raise UnsafeDiagnosticPathError("filesystem root cannot be a diagnostic directory")

    components = [component for component in normalized.split(os.path.sep) if component]
    flags = _directory_open_flags()
    effective_uid = os.geteuid()
    current_fd = os.open(os.path.sep, flags)
    traversed = os.path.sep

    try:
        for index, component in enumerate(components):
            is_final = index == len(components) - 1
            component_path = os.path.join(traversed, component)
            try:
                os.mkdir(component, mode=0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                raise UnsafeDiagnosticPathError(
                    f"could not create diagnostic directory component: {component_path}"
                ) from exc

            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except OSError as exc:
                raise UnsafeDiagnosticPathError(
                    f"diagnostic directory component is not a safe directory: {component_path}"
                ) from exc

            try:
                metadata = os.fstat(next_fd)
                if is_final:
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise UnsafeDiagnosticPathError(
                            f"diagnostic path is not a directory: {component_path}"
                        )
                    if metadata.st_uid != effective_uid:
                        raise UnsafeDiagnosticPathError(
                            f"diagnostic directory is not owned by the application user: {component_path}"
                        )
                    os.fchmod(next_fd, 0o700)
                else:
                    _validate_ancestor(component_path, metadata, effective_uid)
            except Exception:
                os.close(next_fd)
                raise

            os.close(current_fd)
            current_fd = next_fd
            traversed = component_path
    finally:
        os.close(current_fd)

    return normalized
