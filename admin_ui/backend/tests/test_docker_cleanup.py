"""Controlled Docker fixtures. Never invoke real prune commands."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import threading
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
    monkeypatch.setattr(cleanup.subprocess, 'run', run)
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
    monkeypatch.setattr(cleanup.subprocess, 'run', run)
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
