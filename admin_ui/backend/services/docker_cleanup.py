"""One persisted Docker cleanup, owned by a detached worker and an inherited flock."""
from contextlib import contextmanager
import fcntl
import json
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
        _write(root, job)
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
        fcntl.flock(lock, fcntl.LOCK_EX)
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
                raise RuntimeError("Cleanup is starting. Check status again.")
            return snapshot(job)
        _reconcile(root, _read(root))
        job = {"job_id": uuid.uuid4().hex, "status": "running", "started_at": time.time(),
               "finished_at": None, "error": None, "timeout_seconds": TIMEOUT_SECONDS,
               "steps": [{"resource": resource, "status": "pending", "reclaimed": None,
                          "error": None} for resource in resources]}
        _write(root, job)
        try:
            # The worker inherits the lock's open file description. Closing our copy
            # does not unlock it. It survives request disconnects and API restarts.
            worker = subprocess.Popen(
                [sys.executable, "-m", "services.docker_cleanup", str(root), str(lock.fileno())],
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


def _run_command(command, *, stdout, stderr, timeout, pass_fds):
    # Docker can invoke a CLI plugin. Keep its descendants in one killable group.
    process = subprocess.Popen(command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                               pass_fds=pass_fds, start_new_session=True)
    try:
        return subprocess.CompletedProcess(command, process.wait(timeout=timeout))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        raise


def run(root, lock_fd):
    job = _read(root)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        for step in job["steps"]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                job.update(status="timed_out", error="Cleanup time limit reached. Some resources may have been removed.")
                break
            step["status"] = "running"
            _write(root, job)
            # Spool Docker's potentially large list of deleted layers to disk.
            with tempfile.TemporaryFile() as output:
                try:
                    result = _run_command(COMMANDS[step["resource"]], stdout=output, stderr=output,
                                            timeout=remaining, pass_fds=(lock_fd,))
                    step["exit_code"] = result.returncode
                    step["status"] = "completed" if result.returncode == 0 else "failed"
                except subprocess.TimeoutExpired:
                    step.update(status="timed_out", error="Docker cleanup timed out. Docker may still be finishing deletions.")
                output.seek(0, os.SEEK_END)
                output.seek(max(0, output.tell() - 8192))
                text = output.read().decode("utf-8", errors="replace")
                # Return only measured totals. Docker output can contain private image names.
                matches = re.findall(r"(?:Total reclaimed space|Total)\s*:\s*([\d.]+\s*[kMGTPE]?B)\b", text, re.I)
                step["reclaimed"] = matches[-1] if matches else None
            if step["status"] != "completed":
                step["error"] = step["error"] or "Docker cleanup command failed. Check Docker availability before retrying."
                job.update(status=step["status"], error=step["error"])
                break
            _write(root, job)
        else:
            job["status"] = "completed"
    except Exception:
        job.update(status="failed", error="Docker cleanup could not finish. Some resources may have been removed.")
        for step in job["steps"]:
            if step["status"] == "running":
                step.update(status="failed", error=job["error"])
    finally:
        job["finished_at"] = time.time()
        _write(root, job)


if __name__ == "__main__":
    run(Path(sys.argv[1]), int(sys.argv[2]))
