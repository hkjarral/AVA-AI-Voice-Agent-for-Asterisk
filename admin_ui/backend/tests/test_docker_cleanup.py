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
    assert commands == [cleanup.COMMANDS['build_cache'], cleanup.COMMANDS['images']]


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
        with ThreadPoolExecutor(8) as pool:
            jobs = list(pool.map(lambda _: cleanup.start(['images']), range(8)))
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


def test_real_timeout_reaps_cli_and_releases_lock(root, monkeypatch):
    fake_docker(root, monkeypatch, 5)
    seed(root)
    monkeypatch.setattr(cleanup, 'TIMEOUT_SECONDS', .05)
    lock, owned = cleanup._lock(root)
    assert owned
    with lock:
        cleanup.run(root, lock.fileno())
    result = cleanup.latest()
    assert result['status'] == 'timed_out'
    assert result['steps'][0]['reclaimed'] is None
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


def test_worker_death_keeps_cli_lock_until_exit(root, monkeypatch):
    binary = root / 'docker'
    marker = root / 'cli-started'
    binary.write_text(f'#!{sys.executable}\nfrom pathlib import Path\nimport time\nPath({str(marker)!r}).touch()\ntime.sleep(.4)\n')
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
    import os
    child_pid = root / 'plugin.pid'
    script = root / 'plugin_fixture.py'
    script.write_text('import os, time\nfrom pathlib import Path\n'
                      'pid = os.fork()\n'
                      'if pid == 0:\n'
                      f'    Path({str(child_pid)!r}).write_text(str(os.getpid()))\n'
                      'time.sleep(30)\n')
    with (root / 'output').open('wb') as output:
        with pytest.raises(subprocess.TimeoutExpired):
            cleanup._run_command([sys.executable, str(script)], stdout=output, stderr=output,
                                 timeout=.5, pass_fds=())
    assert child_pid.exists()
    # A killed descendant can briefly remain a zombie until the OS reaps it.
    import psutil
    pid = int(child_pid.read_text())
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
