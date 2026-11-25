"""Controlled Docker fixtures. Never invoke real prune commands."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import docker_cleanup as cleanup
from api import system


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, 'directory', lambda: tmp_path)
    return tmp_path


def seed(root, resources=('build_cache', 'images')):
    job = dict(job_id='fixture', status='running', started_at=time.time(), finished_at=None,
               steps=[dict(resource=r, status='pending', reclaimed=None, error=None) for r in resources])
    cleanup._write(root, job)
    return job


def test_success_and_fixed_selection(root, monkeypatch):
    seed(root)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        kwargs['stdout'].write(b'Total reclaimed space: 2.5GB\n')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(cleanup, '_run_command', run)
    cleanup.run(root, 10)
    job = cleanup.latest()
    assert job['status'] == 'completed'
    assert [s['reclaimed'] for s in job['steps']] == ['2.5GB', '2.5GB']
    assert commands == [['docker', 'builder', 'prune', '-a', '-f'], ['docker', 'image', 'prune', '-a', '-f']]


@pytest.mark.parametrize('timeout', [False, True])
def test_partial_failure_preserves_measured_results(root, monkeypatch, timeout):
    seed(root)
    def run(command, **kwargs):
        if command == cleanup.COMMANDS['build_cache']:
            kwargs['stdout'].write(b'Total: 3GB\n')
            return subprocess.CompletedProcess(command, 0)
        if timeout:
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        return subprocess.CompletedProcess(command, 1)
    monkeypatch.setattr(cleanup, '_run_command', run)
    cleanup.run(root, 10)
    job = cleanup.latest()
    assert job['status'] == ('timed_out' if timeout else 'failed')
    assert job['steps'][0]['reclaimed'] == '3GB'
    assert job['steps'][1]['reclaimed'] is None
    assert job['steps'][1]['error']


def test_restart_marks_interrupted_without_replay(root, monkeypatch):
    job = seed(root)
    job['steps'][0]['status'] = 'running'
    cleanup._write(root, job)
    monkeypatch.setattr(cleanup.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not replay'))
    assert cleanup.latest()['status'] == 'interrupted'
    assert cleanup.latest()['steps'][0]['status'] == 'interrupted'


def test_concurrent_clicks_and_reload_share_lock(root, monkeypatch):
    held = []
    def spawn(*args, **kwargs):
        # Simulate child inheritance of the same open file description.
        import os
        held.append(os.fdopen(os.dup(kwargs['pass_fds'][0])))
        return SimpleNamespace(wait=lambda: None)
    monkeypatch.setattr(cleanup.subprocess, 'Popen', spawn)
    try:
        def start_with_retry(_):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    return cleanup.start(['images'])
                except BlockingIOError:
                    time.sleep(.005)
            pytest.fail('submission remained busy')
        with ThreadPoolExecutor(8) as pool:
            jobs = list(pool.map(start_with_retry, range(8)))
        assert len(held) == 1
        assert len({job['job_id'] for job in jobs}) == 1
        assert cleanup.latest()['job_id'] == jobs[0]['job_id']
        assert cleanup.latest()['status'] == 'running'
    finally:
        for lock in held:
            lock.close()
    assert cleanup.latest()['status'] == 'interrupted'


@pytest.mark.asyncio
async def test_slow_disk_usage_keeps_event_loop_responsive(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def slow():
        entered.set()
        release.wait(2)
        return {'fixture': True}
    monkeypatch.setattr(system, '_docker_disk_usage', slow)
    task = asyncio.create_task(system.get_docker_disk_usage())
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.wait_for(asyncio.sleep(0), .1)
        assert not task.done()
    finally:
        release.set()
    assert await task == {'fixture': True}


@pytest.mark.asyncio
async def test_start_status_prompt_while_worker_held(root, monkeypatch):
    held = []
    def spawn(*args, **kwargs):
        import os
        held.append(os.fdopen(os.dup(kwargs['pass_fds'][0])))
        return SimpleNamespace(wait=lambda: None)
    monkeypatch.setattr(cleanup.subprocess, 'Popen', spawn)
    try:
        request = system.PruneRequest(prune_build_cache=False, prune_images=True)
        result = await asyncio.wait_for(system.prune_docker_resources(request), .5)
        status = await asyncio.wait_for(system.docker_prune_status(), .5)
        assert status['job']['job_id'] == result['job']['job_id']
        assert status['job']['steps'][0]['resource'] == 'images'
    finally:
        for lock in held:
            lock.close()


@pytest.mark.asyncio
async def test_reject_volumes_and_empty_selection():
    from fastapi import HTTPException
    for request in (system.PruneRequest(prune_volumes=True), system.PruneRequest(prune_build_cache=False)):
        with pytest.raises(HTTPException) as error:
            await system.prune_docker_resources(request)
        assert error.value.status_code == 422


def fake_docker(root, monkeypatch, delay):
    binary = root / 'docker'
    binary.write_text(f'#!{sys.executable}\nimport time\ntime.sleep({delay})\nprint("Total reclaimed space: 4GB")\n')
    binary.chmod(0o755)
    import os
    monkeypatch.setenv('PATH', str(root) + os.pathsep + os.environ['PATH'])


def test_detached_worker_and_cli_retain_ownership(root, monkeypatch):
    fake_docker(root, monkeypatch, .4)
    job = cleanup.start(['images'])
    assert cleanup.latest()['status'] == 'running'
    assert cleanup.start(['build_cache'])['job_id'] == job['job_id']
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = cleanup.latest()
        if result['status'] != 'running':
            break
        time.sleep(.02)
    assert result['status'] == 'completed'
    assert result['steps'][0]['reclaimed'] == '4GB'


def ready_timeout_fixture(root, lock_fd, plugin=False):
    """Run an isolated supervisor, advancing its clock only after CLI readiness."""
    cli_pid = root / 'cli.pid'
    plugin_pid = root / 'plugin.pid'
    fixture = root / 'ready_cli.py'
    fixture.write_text(
        'import os, time\nfrom pathlib import Path\n'
        + (f'if os.fork() == 0:\n    Path({str(plugin_pid)!r}).write_text(str(os.getpid()))\n    time.sleep(30)\n    os._exit(0)\n' if plugin else '')
        + f'Path({str(cli_pid)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n'
    )
    wrapper = f"""
import time
from pathlib import Path
from types import SimpleNamespace
from services import docker_cleanup as cleanup
clock = {{'now': 100}}
original_spawn = cleanup.subprocess.Popen
def ready_spawn(*args, **kwargs):
    process = original_spawn(*args, **kwargs)
    limit = time.monotonic() + 5
    while not (Path({str(cli_pid)!r}).exists() and ({not plugin!r} or Path({str(plugin_pid)!r}).exists())):
        if time.monotonic() > limit:
            process.kill()
            process.wait()
            raise RuntimeError('fixture CLI did not become ready')
        time.sleep(.01)
    clock['now'] = 1001
    return process
cleanup.subprocess.Popen = ready_spawn
cleanup.time = SimpleNamespace(monotonic=lambda: clock['now'])
cleanup._run_bounded_command([{sys.executable!r}, {str(fixture)!r}], stdout=None, stderr=None,
                             timeout=900, pass_fds=({lock_fd},))
"""
    process = subprocess.Popen([sys.executable, '-c', wrapper],
                               cwd=Path(cleanup.__file__).resolve().parents[1],
                               pass_fds=(lock_fd,), start_new_session=True)
    assert process.wait(timeout=8) == -9
    assert cli_pid.exists(), 'CLI must start before the timeout is exercised'
    import psutil
    assert not psutil.pid_exists(int(cli_pid.read_text())), 'CLI leader must be reaped'
    return plugin_pid


def test_real_timeout_reaps_cli_and_releases_lock(root):
    lock, owned = cleanup._lock(root)
    assert owned
    with lock:
        ready_timeout_fixture(root, lock.fileno())
    next_lock, owned = cleanup._lock(root)
    next_lock.close()
    assert owned


@pytest.mark.asyncio
async def test_routes_respond_during_controlled_cleanup(root, monkeypatch):
    from fastapi import FastAPI
    import httpx
    fake_docker(root, monkeypatch, .5)
    app = FastAPI()
    app.include_router(system.router, prefix='/api/system')
    @app.get('/health')
    async def health():
        return {'healthy': True}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        start = await asyncio.wait_for(client.post('/api/system/docker/prune', json={'prune_images': True, 'prune_build_cache': False}), .5)
        assert start.status_code == 202
        job = start.json()['job']
        assert (await asyncio.wait_for(client.get('/health'), .1)).status_code == 200
        status = await asyncio.wait_for(client.get('/api/system/docker/prune'), .1)
        assert status.json()['job']['job_id'] == job['job_id']
        assert status.json()['job']['status'] == 'running'
    # Wait for the controlled worker before the fixture directory disappears.
    deadline = time.monotonic() + 5
    while cleanup.latest()['status'] == 'running' and time.monotonic() < deadline:
        await asyncio.sleep(.02)
    assert cleanup.latest()['status'] == 'completed'


def test_worker_start_failure_is_terminal_and_retryable(root, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('fixture spawn failure')
    monkeypatch.setattr(cleanup.subprocess, 'Popen', fail)
    job = cleanup.start(['images'])
    assert job['status'] == 'failed'
    assert cleanup.latest()['job_id'] == job['job_id']
    assert cleanup.start(['images'])['job_id'] != job['job_id']


def test_worker_death_keeps_cli_lock_until_independent_deadline(root, monkeypatch):
    monkeypatch.setattr(cleanup, 'TIMEOUT_SECONDS', 1)
    binary = root / 'docker'
    marker = root / 'cli-started'
    binary.write_text(f'#!{sys.executable}\nfrom pathlib import Path\nimport time\nPath({str(marker)!r}).touch()\ntime.sleep(30)\n')
    binary.chmod(0o755)
    import os
    monkeypatch.setenv('PATH', str(root) + os.pathsep + os.environ['PATH'])
    original_spawn = subprocess.Popen
    workers = []
    def spawn(*args, **kwargs):
        worker = original_spawn(*args, **kwargs)
        workers.append(worker)
        return worker
    monkeypatch.setattr(cleanup.subprocess, 'Popen', spawn)
    job = cleanup.start(['images'])
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert marker.exists()
    workers[0].kill()
    workers[0].wait(timeout=1)
    assert cleanup.latest()['status'] == 'running'
    assert cleanup.start(['images'])['job_id'] == job['job_id']
    while cleanup.latest()['status'] == 'running' and time.monotonic() < deadline:
        time.sleep(.02)
    assert cleanup.latest()['status'] == 'interrupted'


def test_timeout_stops_cli_plugin_group(root):
    lock, owned = cleanup._lock(root)
    assert owned
    with lock:
        plugin_pid = ready_timeout_fixture(root, lock.fileno(), plugin=True)
    import psutil
    pid = int(plugin_pid.read_text())
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.NoSuchProcess:
            return
        time.sleep(.01)
    pytest.fail('CLI plugin survived the cleanup timeout')


def test_total_budget_and_elapsed_use_controlled_clocks(root, monkeypatch):
    job = seed(root)
    job['started_at'] = 40
    cleanup._write(root, job)
    ticks = iter([100, 1001])
    monkeypatch.setattr(cleanup, 'time', SimpleNamespace(time=lambda: 1000, monotonic=lambda: next(ticks)))
    monkeypatch.setattr(cleanup, '_run_command', lambda *a, **k: pytest.fail('budget already exhausted'))
    cleanup.run(root, 10)
    result = cleanup.latest()
    assert result['status'] == 'timed_out'
    assert result['elapsed_seconds'] == 960
    assert all(step['status'] == 'pending' for step in result['steps'])


def test_terminal_job_with_held_lock_requires_retry(root):
    job = seed(root)
    job['status'] = 'completed'
    cleanup._write(root, job)
    lock, owned = cleanup._lock(root)
    assert owned
    with lock:
        with pytest.raises(BlockingIOError):
            cleanup.start(['images'])


def test_full_disk_reconciliation_still_returns_status(root, monkeypatch):
    seed(root)
    def full(*args):
        raise OSError('fixture full disk')
    monkeypatch.setattr(cleanup, '_write', full)
    result = cleanup.latest()
    assert result['status'] == 'interrupted'
    assert 'storage' in result['error']


@pytest.mark.asyncio
async def test_full_disk_prevents_start_with_clear_error(root, monkeypatch):
    def full(*args):
        raise OSError('fixture full disk')
    monkeypatch.setattr(cleanup, '_write', full)
    monkeypatch.setattr(cleanup.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not prune without journal'))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await system.prune_docker_resources(system.PruneRequest())
    assert error.value.status_code == 503
    assert 'No new cleanup was started' in error.value.detail


def test_journal_failure_after_step_retains_completed_state(root, monkeypatch):
    seed(root)
    writes = []
    original_write = cleanup._write
    def write(root, job):
        writes.append(__import__('copy').deepcopy(job))
        if job['steps'][0]['status'] == 'completed':
            raise OSError('fixture full disk')
        original_write(root, job)
    def run(command, **kwargs):
        kwargs['stdout'].write(b'Total: 2GB\n')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(cleanup, '_write', write)
    monkeypatch.setattr(cleanup, '_run_command', run)
    cleanup.run(root, 10)
    assert writes[-1]['steps'][0]['status'] == 'completed'
    assert writes[-1]['steps'][0]['reclaimed'] == '2GB'
    assert writes[-1]['status'] == 'failed'
    assert cleanup.latest()['status'] == 'interrupted'


def test_slow_journal_exhausts_budget_before_command(root, monkeypatch):
    seed(root)
    clock = {'now': 0}
    original_write = cleanup._write
    def write(root, job):
        original_write(root, job)
        clock['now'] = 901
    monkeypatch.setattr(cleanup, 'time', SimpleNamespace(time=lambda: 1000, monotonic=lambda: clock['now']))
    monkeypatch.setattr(cleanup, '_write', write)
    monkeypatch.setattr(cleanup, '_run_command', lambda *a, **k: pytest.fail('deadline passed during journaling'))
    cleanup.run(root, 10)
    result = cleanup.latest()
    assert result['status'] == 'timed_out'
    assert result['steps'][0]['status'] == 'timed_out'


def test_supervisor_does_not_launch_command_after_absolute_deadline(monkeypatch):
    monkeypatch.setattr(cleanup.subprocess, 'Popen', lambda *a, **k: pytest.fail('deadline already expired'))
    with pytest.raises(subprocess.TimeoutExpired):
        cleanup._run_bounded_command(['docker'], stdout=None, stderr=None, timeout=900,
                                     pass_fds=(), deadline=time.monotonic() - 1)


def test_temporary_storage_failure_marks_running_step_failed(root, monkeypatch):
    seed(root)
    def full():
        raise OSError('fixture temporary storage unavailable')
    monkeypatch.setattr(cleanup.tempfile, 'TemporaryFile', full)
    cleanup.run(root, 10)
    result = cleanup.latest()
    assert result['status'] == 'failed'
    assert result['steps'][0]['status'] == 'failed'
    assert result['steps'][0]['error']


@pytest.mark.asyncio
async def test_missing_journal_with_active_lock_is_retryable(root, monkeypatch):
    from fastapi import HTTPException
    lock, owned = cleanup._lock(root)
    assert owned
    monkeypatch.setattr(cleanup.subprocess, 'Popen', lambda *a, **k: pytest.fail('active lock prevents a second worker'))
    with lock:
        with pytest.raises(HTTPException) as error:
            await system.prune_docker_resources(system.PruneRequest())
    assert error.value.status_code == 409


def test_initial_journal_and_worker_launch_share_start_deadline(root, monkeypatch):
    clock = {'now': 100}
    original_write = cleanup._write
    captured = []
    def slow_write(root, job):
        original_write(root, job)
        clock['now'] = 1001
    def spawn(command, **kwargs):
        captured.append(command)
        return SimpleNamespace(wait=lambda: None)
    monkeypatch.setattr(cleanup, 'time', SimpleNamespace(time=lambda: 1000, monotonic=lambda: clock['now']))
    monkeypatch.setattr(cleanup, '_write', slow_write)
    monkeypatch.setattr(cleanup.subprocess, 'Popen', spawn)
    monkeypatch.setattr(cleanup, '_run_command', lambda *a, **k: pytest.fail('initial journal consumed the budget'))
    cleanup.start(['images'])
    deadline = float(captured[0][-1])
    assert deadline == 1000
    cleanup.run(root, 10, deadline=deadline)
    assert cleanup.latest()['status'] == 'timed_out'


@pytest.mark.parametrize('output', [b'untagged: total:123MB\n', b'untagged: total:123MB\nTotal reclaimed space: 2GB\n'])
def test_reclaimed_space_requires_complete_summary_line(root, monkeypatch, output):
    seed(root, resources=('images',))
    def run(command, **kwargs):
        kwargs['stdout'].write(output)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(cleanup, '_run_command', run)
    cleanup.run(root, 10)
    assert cleanup.latest()['steps'][0]['reclaimed'] == ('2GB' if b'reclaimed space' in output else None)


@pytest.mark.asyncio
async def test_stalled_cleanup_storage_cannot_exhaust_shared_executor(root, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def stalled():
        entered.set()
        release.wait(5)
        return None
    monkeypatch.setattr(cleanup, 'latest', stalled)
    monkeypatch.setattr(cleanup, 'IO_TIMEOUT_SECONDS', .05)
    monkeypatch.setattr(system, '_docker_disk_usage', lambda: {'responsive': True})
    task = asyncio.create_task(system.docker_prune_status())
    from fastapi import HTTPException
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        async def poll():
            with pytest.raises(HTTPException) as error:
                await system.docker_prune_status()
            assert error.value.status_code == 409
        await asyncio.wait_for(asyncio.gather(*(poll() for _ in range(50))), .5)
        assert await asyncio.wait_for(system.get_docker_disk_usage(), .5) == {'responsive': True}
        with pytest.raises(HTTPException) as error:
            await task
        assert error.value.status_code == 503
    finally:
        release.set()
        # Wait for the actual I/O future to release admission after request timeout.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if cleanup._io_available.acquire(blocking=False):
                cleanup._io_available.release()
                break
            await asyncio.sleep(.01)
        else:
            pytest.fail('cleanup storage admission was not released')


def test_supervisor_death_stops_its_cli_group(root, monkeypatch):
    import os
    import psutil
    marker = root / 'cli.pid'
    fixture = root / 'surviving_cli.py'
    fixture.write_text(f'import os, time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n')
    original_spawn = subprocess.Popen
    supervisors = []
    def spawn(*args, **kwargs):
        process = original_spawn(*args, **kwargs)
        supervisors.append(process)
        return process
    monkeypatch.setattr(cleanup.subprocess, 'Popen', spawn)
    with (root / 'output').open('wb') as output, ThreadPoolExecutor(1) as pool:
        future = pool.submit(cleanup._run_command, [sys.executable, str(fixture)], stdout=output,
                             stderr=output, timeout=30, pass_fds=())
        limit = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < limit:
            time.sleep(.01)
        assert marker.exists()
        supervisors[0].kill()
        assert future.result(timeout=2).returncode == -9
    pid = int(marker.read_text())
    limit = time.monotonic() + 1
    while time.monotonic() < limit:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.NoSuchProcess:
            return
        time.sleep(.01)
    os.kill(pid, 9)
    pytest.fail('CLI survived its supervisor')


@pytest.mark.asyncio
async def test_start_storage_timeout_reports_uncertain_outcome(root, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def stalled(*args):
        entered.set()
        release.wait(2)
        return None
    monkeypatch.setattr(cleanup, 'start', stalled)
    monkeypatch.setattr(cleanup, 'IO_TIMEOUT_SECONDS', .02)
    from fastapi import HTTPException
    try:
        with pytest.raises(HTTPException) as error:
            await system.prune_docker_resources(system.PruneRequest())
        assert error.value.status_code == 504
        assert 'Recheck status' in error.value.detail
    finally:
        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if cleanup._io_available.acquire(blocking=False):
                cleanup._io_available.release()
                break
            await asyncio.sleep(.01)
        else:
            pytest.fail('cleanup storage admission was not released')
