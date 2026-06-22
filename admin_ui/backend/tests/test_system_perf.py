"""
Tests for the BACKEND-PERF stream (audit item I7):

- The dashboard polling handlers (/containers, /metrics, /directories, /platform)
  must offload their blocking bodies off the asyncio event loop via
  asyncio.to_thread, while keeping their response shape unchanged.
- /platform must serve a short-TTL cached result (it is the heaviest endpoint and
  is recomputed every ~5s by the dashboard poll), and recompute after the window.
- /preflight must bypass the cache (always fresh).
"""

import asyncio
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import system  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_platform_cache():
    """Each test starts with an empty platform TTL cache."""
    system._reset_platform_cache()
    yield
    system._reset_platform_cache()


def _stub_platform_helpers(monkeypatch, counter):
    """Replace the heavy detectors so we can count how often they run."""

    def _bump(*_args, **_kwargs):
        counter["calls"] += 1
        return {}

    monkeypatch.setattr(system, "_detect_os", lambda: {"id": "ubuntu", "family": "debian"})
    monkeypatch.setattr(system, "_detect_docker", _bump)
    monkeypatch.setattr(system, "_detect_compose", lambda: {})
    monkeypatch.setattr(system, "_detect_selinux", lambda: {})
    monkeypatch.setattr(system, "_detect_directories", lambda: {})
    monkeypatch.setattr(system, "_detect_asterisk", lambda: {})
    monkeypatch.setattr(system, "_detect_project_version", lambda _root: {})
    monkeypatch.setattr(system, "_load_platforms_yaml", lambda: {})
    monkeypatch.setattr(system, "_select_platform_key", lambda *_a, **_k: None)
    monkeypatch.setattr(system, "_resolve_platform", lambda *_a, **_k: None)
    monkeypatch.setattr(system, "_build_checks", lambda *_a, **_k: [])


# --- /platform TTL cache -----------------------------------------------------


def test_platform_returns_cached_object_within_ttl(monkeypatch):
    counter = {"calls": 0}
    _stub_platform_helpers(monkeypatch, counter)

    first = asyncio.run(system.get_platform())
    second = asyncio.run(system.get_platform())

    # Within the TTL window the heavy detectors run exactly once and the same
    # cached object is handed back.
    assert counter["calls"] == 1
    assert second is first
    assert "platform" in first and "checks" in first and "summary" in first


def test_platform_recomputes_after_ttl_expires(monkeypatch):
    counter = {"calls": 0}
    _stub_platform_helpers(monkeypatch, counter)

    # Freeze "now" so we can advance past the TTL deterministically.
    fake_now = {"t": 1000.0}
    monkeypatch.setattr(system.time, "monotonic", lambda: fake_now["t"])

    asyncio.run(system.get_platform())
    assert counter["calls"] == 1

    # Still inside the window -> cached.
    fake_now["t"] += system._PLATFORM_CACHE_TTL_SECONDS / 2
    asyncio.run(system.get_platform())
    assert counter["calls"] == 1

    # Past the window -> recompute.
    fake_now["t"] += system._PLATFORM_CACHE_TTL_SECONDS + 0.01
    asyncio.run(system.get_platform())
    assert counter["calls"] == 2


def test_preflight_bypasses_platform_cache(monkeypatch):
    counter = {"calls": 0}
    _stub_platform_helpers(monkeypatch, counter)

    asyncio.run(system.get_platform())
    assert counter["calls"] == 1

    # /preflight must always recompute even within the TTL window.
    asyncio.run(system.run_preflight())
    assert counter["calls"] == 2


# --- offload: handlers run their blocking body off the loop -------------------


def _spy_to_thread(monkeypatch, used):
    real_to_thread = asyncio.to_thread

    async def _spy(func, *args, **kwargs):
        used["to_thread"] = True
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(system.asyncio, "to_thread", _spy)


def test_platform_offloads_via_to_thread(monkeypatch):
    counter = {"calls": 0}
    _stub_platform_helpers(monkeypatch, counter)
    used = {"to_thread": False}
    _spy_to_thread(monkeypatch, used)

    asyncio.run(system.get_platform())
    assert used["to_thread"] is True


def test_metrics_runs_inline_not_offloaded(monkeypatch):
    # /metrics must NOT use the shared to_thread executor: psutil.cpu_percent(interval=None)
    # keeps a per-thread sampling baseline, so a different worker thread would report 0.0 /
    # inconsistent intervals. It runs inline on the event-loop thread instead.
    used = {"to_thread": False}
    _spy_to_thread(monkeypatch, used)
    result = asyncio.run(system.get_system_metrics())

    assert used["to_thread"] is False
    assert set(result.keys()) == {"cpu", "memory", "disk"}
    assert "percent" in result["cpu"]
    assert "percent" in result["memory"]
    assert "percent" in result["disk"]


def test_directories_offloads_and_keeps_shape(monkeypatch):
    used = {"to_thread": False}
    _spy_to_thread(monkeypatch, used)
    result = asyncio.run(system.get_directory_health())

    assert used["to_thread"] is True
    assert set(result.keys()) == {"overall", "checks"}
    assert set(result["checks"].keys()) == {
        "media_dir_configured",
        "host_directory",
        "asterisk_symlink",
    }


def test_containers_offloads_via_to_thread(monkeypatch):
    used = {"to_thread": False}

    class _FakeContainers:
        def list(self, all=False):  # noqa: A002 - mirror docker SDK signature
            return []

    class _FakeClient:
        containers = _FakeContainers()

    monkeypatch.setattr(system.docker, "from_env", lambda: _FakeClient())
    _spy_to_thread(monkeypatch, used)

    result = asyncio.run(system.get_containers())
    assert used["to_thread"] is True
    assert result == []


def test_platform_refresh_is_single_flight(monkeypatch):
    """Concurrent /platform requests on a cold cache must share ONE recompute — no
    stampede of Docker/subprocess probes under the slow conditions the cache protects."""
    counter = {"calls": 0}
    _stub_platform_helpers(monkeypatch, counter)

    async def _hammer():
        return await asyncio.gather(*[system.get_platform() for _ in range(5)])

    results = asyncio.run(_hammer())
    assert counter["calls"] == 1  # one compute despite 5 concurrent callers
    assert all(r is results[0] for r in results)  # all share the cached object


def test_platform_ttl_covers_dashboard_poll_interval():
    # The dashboard polls /platform every 5s; if the TTL is shorter, a single steady poller
    # misses the cache on every request and the cache does nothing. Keep TTL > poll interval.
    DASHBOARD_POLL_SECONDS = 5.0
    assert system._PLATFORM_CACHE_TTL_SECONDS > DASHBOARD_POLL_SECONDS


def test_directory_write_probe_is_unique_per_call(monkeypatch, tmp_path):
    """The offloaded /directories write-probe must use a unique filename per call, so two
    concurrent requests can't remove each other's probe and falsely report 'not writable'."""
    from pathlib import Path as _P

    media = tmp_path / "asterisk_media" / "ai-generated"
    media.mkdir(parents=True)
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("DOCKER_CONTAINER", raising=False)

    # Force the non-docker branch so path_to_check is our writable tmp host_media_dir.
    real_exists = system.os.path.exists
    monkeypatch.setattr(
        system.os.path, "exists",
        lambda p: False if p == "/.dockerenv" else real_exists(p),
    )

    removed = []
    real_remove = system.os.remove
    monkeypatch.setattr(system.os, "remove", lambda p: (removed.append(p), real_remove(p))[1])

    system._collect_directory_health()
    system._collect_directory_health()

    probes = [p for p in removed if _P(p).name.startswith(".write_test")]
    assert len(probes) == 2
    assert probes[0] != probes[1]
