"""One persisted Docker cleanup, owned by a detached worker and an inherited flock."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from contextlib import contextmanager
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from services.fs import atomic_write_text

logger = logging.getLogger(__name__)

_io_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="docker-cleanup-io")
_io_available = threading.BoundedSemaphore(1)
IO_TIMEOUT_SECONDS = 2


async def bounded_io(function, *args):
    """One storage operation, with prompt rejection while that operation is stalled."""
    if not _io_available.acquire(blocking=False):
        raise BlockingIOError("Cleanup progress storage is busy")
    try:
        future = _io_executor.submit(partial(function, *args))
    except BaseException:
        _io_available.release()
        raise
    future.add_done_callback(lambda _: _io_available.release())
    return await asyncio.wait_for(asyncio.wrap_future(future), timeout=IO_TIMEOUT_SECONDS)


TIMEOUT_SECONDS = 900
COMMANDS = {
    "build_cache": ["docker", "builder", "prune", "-a", "-f"],
    "images": ["docker", "image", "prune", "-a", "-f"],
    "containers": ["docker", "container", "prune", "-f"],
}


def directory():
    import settings
    return Path(os.getenv("PROJECT_ROOT", settings.PROJECT_ROOT)) / "data/operator/docker-cleanup"


def _write(root, job):
    atomic_write_text(str(root / "latest.json"), json.dumps(job) + "\n")


def _read(root):
    try:
        return json.loads((root / "latest.json").read_text())
    except FileNotFoundError:
        return None


def _lock(root):
    root.mkdir(parents=True, exist_ok=True)
    lock = (root / "cleanup.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock, True
    except BlockingIOError:
        return lock, False


def _reconcile(root, job):
    if job and job["status"] == "running":
        job.update(status="interrupted", finished_at=time.time(),
                   error="Cleanup was interrupted. Some resources may have been removed. Refresh disk usage before retrying.")
        for step in job["steps"]:
            if step["status"] == "running":
                step["status"] = "interrupted"
        try:
            _write(root, job)
        except OSError:
            job["error"] += " Progress storage is unavailable, so this status could not be saved."
    return job


def snapshot(job):
    if job:
        job["elapsed_seconds"] = round(max(0, (job.get("finished_at") or time.time()) - job["started_at"]), 1)
    return job


@contextmanager
def _submission_lock(root):
    # Serialize the short create/read handoff across API threads and processes.
    root.mkdir(parents=True, exist_ok=True)
    with (root / "submission.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def latest():
    with _submission_lock(directory()):
        return _latest()


def start(resources):
    with _submission_lock(directory()):
        return _start(resources)


def _latest():
    root = directory()
    lock, owned = _lock(root)
    with lock:
        job = _read(root)
        if owned:
            job = _reconcile(root, job)
        return snapshot(job)


def _start(resources):
    if not resources or any(resource not in COMMANDS for resource in resources):
        raise ValueError("Select supported Docker resources")
    root = directory()
    lock, owned = _lock(root)
    with lock:
        if not owned:
            job = _read(root)
            if job is None:
                raise BlockingIOError("Cleanup is active but its progress is unavailable. Retry shortly.")
            if job["status"] != "running":
                raise BlockingIOError("Cleanup is releasing its lock. Retry shortly.")
            return snapshot(job)
        _reconcile(root, _read(root))
        job = {"job_id": uuid.uuid4().hex, "status": "running", "started_at": time.time(),
               "finished_at": None, "error": None, "timeout_seconds": TIMEOUT_SECONDS,
               "steps": [{"resource": resource, "status": "pending", "reclaimed": None,
                          "error": None} for resource in resources]}
        deadline = time.monotonic() + TIMEOUT_SECONDS
        _write(root, job)
        try:
            # The worker inherits the lock's open file description. Closing our copy
            # does not unlock it. It survives request disconnects and API restarts.
            worker = subprocess.Popen(
                [sys.executable, "-m", "services.docker_cleanup", str(root), str(lock.fileno()), str(deadline)],
                cwd=str(Path(__file__).resolve().parents[1]), pass_fds=(lock.fileno(),),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            job.update(status="failed", finished_at=time.time(), error="Unable to start Docker cleanup worker.")
            _write(root, job)
        else:
            threading.Thread(target=worker.wait, daemon=True, name="docker-cleanup-reaper").start()
        return snapshot(job)


def _run_command(command, *, stdout, stderr, timeout, pass_fds, deadline=None):
    # A separate supervisor owns the deadline even if the journal worker dies.
    deadline = deadline if deadline is not None else time.monotonic() + timeout
    lock_fd = pass_fds[0] if pass_fds else -1
    process = subprocess.Popen(
        [sys.executable, "-m", "services.docker_cleanup", "--command", str(deadline), str(lock_fd), *command],
        stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, pass_fds=pass_fds,
        cwd=str(Path(__file__).resolve().parents[1]), start_new_session=True,
    )
    try:
        code = process.wait()
    finally:
        # The CLI and plugins share the supervisor's group. If the supervisor
        # exits unexpectedly, the live worker still terminates that whole group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if code == 124 or (code == -signal.SIGKILL and time.monotonic() >= deadline):
        raise subprocess.TimeoutExpired(command, timeout)
    return subprocess.CompletedProcess(command, code)


def _run_bounded_command(command, *, stdout, stderr, timeout, pass_fds, deadline=None):
    # Charge interpreter and process startup to the same absolute job deadline.
    deadline = deadline if deadline is not None else time.monotonic() + timeout
    if deadline <= time.monotonic():
        raise subprocess.TimeoutExpired(command, timeout)
    # Docker can invoke a CLI plugin. Keep its descendants in one killable group.
    process = subprocess.Popen(command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                               pass_fds=pass_fds)
    try:
        return subprocess.CompletedProcess(command, process.wait(timeout=max(0, deadline - time.monotonic())))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        # Reap the CLI first, then terminate any plugins and this supervisor.
        # This also enforces the deadline after the journal worker has died.
        os.killpg(os.getpgrp(), signal.SIGKILL)
        raise


def run(root, lock_fd, deadline=None):
    job = _read(root)
    if deadline is None:
        deadline = time.monotonic() + min(TIMEOUT_SECONDS, job.get("timeout_seconds", TIMEOUT_SECONDS))
    try:
        for step in job["steps"]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                job.update(status="timed_out", error="Cleanup time limit reached. Some resources may have been removed.")
                break
            step["status"] = "running"
            _write(root, job)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                step.update(status="timed_out", error="Cleanup time limit reached before this command started.")
                job.update(status="timed_out", error=step["error"])
                break
            # Spool Docker's potentially large list of deleted layers to disk.
            with tempfile.TemporaryFile() as output:
                try:
                    result = _run_command(COMMANDS[step["resource"]], stdout=output, stderr=output,
                                            timeout=remaining, pass_fds=(lock_fd,), deadline=deadline)
                    step["exit_code"] = result.returncode
                    step["status"] = "completed" if result.returncode == 0 else "failed"
                except subprocess.TimeoutExpired:
                    step.update(status="timed_out", error="Docker cleanup timed out. Docker may still be finishing deletions.")
                output.seek(0, os.SEEK_END)
                offset = max(0, output.tell() - 8192)
                output.seek(offset)
                if offset:
                    output.readline()  # Discard a potentially partial first line.
                text = output.read().decode("utf-8", errors="replace")
                # Return only measured totals. Docker output can contain private image names.
                matches = re.findall(r"^\s*(?:Total reclaimed space|Total)\s*:\s*([\d.]+[ \t]*[kMGTPE]?B)[ \t]*$", text, re.I | re.M)
                step["reclaimed"] = matches[-1] if matches else None
            if step["status"] != "completed":
                step["error"] = step["error"] or "Docker cleanup command failed. Check Docker availability before retrying."
                job.update(status=step["status"], error=step["error"])
                break
            _write(root, job)
        else:
            job["status"] = "completed"
    except Exception as exc:
        error = ("Cleanup progress storage or Docker execution is unavailable. Some resources may have been removed."
                 if isinstance(exc, OSError) else "Docker cleanup could not finish. Some resources may have been removed.")
        job.update(status="failed", error=error)
        for step in job["steps"]:
            if step["status"] == "running":
                step.update(status="failed", error=job["error"])
    finally:
        job["finished_at"] = time.time()
        try:
            _write(root, job)
        except OSError:
            logger.error("Docker cleanup result could not be saved to operator storage")


if __name__ == "__main__":
    if sys.argv[1] == "--command":
        deadline, lock_fd = float(sys.argv[2]), int(sys.argv[3])
        try:
            result = _run_bounded_command(sys.argv[4:], stdout=None, stderr=None, timeout=0, deadline=deadline,
                                          pass_fds=(lock_fd,) if lock_fd >= 0 else ())
            sys.exit(result.returncode if result.returncode >= 0 else 1)
        except subprocess.TimeoutExpired:
            sys.exit(124)
    else:
        run(Path(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]) if len(sys.argv) > 3 else None)
