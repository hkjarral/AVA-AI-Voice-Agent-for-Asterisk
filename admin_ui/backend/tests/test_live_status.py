import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import live_status  # noqa: E402
from services.status_hub import StatusHub, component  # noqa: E402


def _sample_results():
    return {
        "health": {
            "ai_engine": {
                "status": "connected",
                "details": {
                    "status": "healthy",
                    "ari_connected": True,
                    "audio_transport": "audiosocket",
                    "active_calls": 0,
                    "active_sessions": 0,
                    "asterisk_channels": 0,
                    "providers": {
                        "openai_realtime": {"ready": True},
                        "google_live": {"ready": False, "reason": "missing_config"},
                    },
                    "pipelines": {"local_hybrid": {"stt": "local_stt"}},
                },
            },
            "local_ai_server": {
                "status": "connected",
                "details": {
                    "models": {
                        "stt": {"loaded": False},
                        "llm": {"loaded": False},
                        "tts": {"loaded": False},
                    },
                    "config": {
                        "runtime_mode": "minimal",
                        "degraded": True,
                        "startup_errors": {
                            "stt": "model missing",
                            "tts": "model missing",
                        },
                    },
                    "gpu": {"runtime_usable": False},
                },
            },
        },
        "sessions": {"active_calls": 0, "sessions": [], "reachable": True},
        "directories": {
            "overall": "error",
            "checks": {
                "media_dir_configured": {
                    "status": "error",
                    "message": "AST_MEDIA_DIR not set in environment",
                },
                "host_directory": {"status": "ok", "message": "Directory exists and is writable"},
            },
        },
        "platform": {
            "summary": {
                "ready": True,
                "passed": 5,
                "total_checks": 5,
                "warnings": 0,
                "errors": 0,
            },
            "checks": [],
        },
        "asterisk": {"live": {"ari_reachable": True, "app_registered": True}},
        "metrics": {"cpu": {"percent": 1.0}},
    }


def test_normalize_probe_results_keeps_degraded_dimensions_separate():
    components = live_status.normalize_probe_results(_sample_results())

    assert components["ai_engine"]["state"] == "degraded"
    assert "google_live: missing_config" in components["ai_engine"]["warnings"]
    assert components["local_ai_server"]["state"] == "degraded"
    assert "stt: model missing" in components["local_ai_server"]["warnings"]
    assert components["directories"]["state"] == "error"
    assert components["platform"]["state"] == "ready"
    assert components["asterisk"]["state"] == "ready"
    assert components["sessions"]["state"] == "ready"


def test_platform_failed_checks_are_error_not_unreachable():
    results = _sample_results()
    results["platform"] = {
        "summary": {
            "ready": False,
            "passed": 4,
            "total_checks": 5,
            "warnings": 0,
            "errors": 1,
        },
        "checks": [
            {
                "id": "architecture",
                "status": "error",
                "message": "Unsupported architecture",
                "blocking": True,
            }
        ],
    }

    components = live_status.normalize_probe_results(results)

    assert components["platform"]["state"] == "error"
    assert components["platform"]["errors"] == ["Unsupported architecture"]


@pytest.mark.asyncio
async def test_status_hub_marks_stale_then_unreachable():
    now = {"mono": 100.0, "wall": "2026-06-26T00:00:00Z"}
    hub = StatusHub(
        stale_after_seconds=5.0,
        unreachable_after_seconds=10.0,
        monotonic_clock=lambda: now["mono"],
        wall_clock=lambda: now["wall"],
    )

    first = await hub.upsert_components({"ai_engine": component(state="ready", summary="ok")})
    assert first["ai_engine"]["freshness"] == "fresh"
    assert first["ai_engine"]["state"] == "ready"

    now["mono"] += 6.0
    stale = await hub.snapshot()
    assert stale["ai_engine"]["freshness"] == "stale"
    assert stale["ai_engine"]["state"] == "ready"
    assert stale["summary"]["state"] == "ready"

    now["mono"] += 5.0
    expired = await hub.snapshot()
    assert expired["ai_engine"]["freshness"] == "expired"
    assert expired["ai_engine"]["state"] == "unreachable"
    assert expired["summary"]["state"] == "error"


@pytest.mark.asyncio
async def test_status_hub_subscriber_queue_keeps_latest_event():
    hub = StatusHub(queue_size=1)
    queue = await hub.subscribe()
    await queue.get()

    await hub.upsert_components({"ai_engine": component(state="ready", summary="one")})
    await hub.upsert_components({"ai_engine": component(state="degraded", summary="two")})

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event["data"]["event_id"] == 2
    assert event["data"]["ai_engine"]["summary"] == "two"
    await hub.unsubscribe(queue)


@pytest.mark.asyncio
async def test_status_hub_prefers_fresh_push_over_probe_then_allows_stale_probe():
    now = {"mono": 100.0, "wall": "2026-06-26T00:00:00Z"}
    hub = StatusHub(
        stale_after_seconds=5.0,
        unreachable_after_seconds=20.0,
        monotonic_clock=lambda: now["mono"],
        wall_clock=lambda: now["wall"],
    )

    await hub.upsert_components({
        "ai_engine": component(state="ready", summary="pushed", source="push")
    })
    fresh_probe = await hub.upsert_components({
        "ai_engine": component(state="error", summary="probe miss", source="probe")
    })
    assert fresh_probe["components"]["ai_engine"]["summary"] == "pushed"
    assert fresh_probe["components"]["ai_engine"]["source"] == "push"

    now["mono"] += 6.0
    stale_probe = await hub.upsert_components({
        "ai_engine": component(state="ready", summary="probe recovered", source="probe")
    })
    assert stale_probe["components"]["ai_engine"]["summary"] == "probe recovered"
    assert stale_probe["components"]["ai_engine"]["source"] == "probe"


@pytest.mark.asyncio
async def test_refresh_live_status_uses_existing_probe_handlers(monkeypatch):
    async def fake_probe():
        return _sample_results()

    hub = StatusHub()
    monkeypatch.setattr(live_status, "status_hub", hub)
    monkeypatch.setattr(live_status, "probe_live_status", fake_probe)

    snapshot = await live_status.refresh_live_status()

    assert snapshot["version"] == 1
    assert snapshot["components"]["platform"]["state"] == "ready"
    assert snapshot["components"]["local_ai_server"]["state"] == "degraded"
    assert snapshot["summary"]["state"] == "error"


@pytest.mark.asyncio
async def test_refresh_live_status_offloads_probe_collection(monkeypatch):
    async def fake_probe():
        return _sample_results()

    used = {"to_thread": False}
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(func, *args, **kwargs):
        used["to_thread"] = True
        return await real_to_thread(func, *args, **kwargs)

    hub = StatusHub()
    monkeypatch.setattr(live_status, "status_hub", hub)
    monkeypatch.setattr(live_status, "probe_live_status", fake_probe)
    monkeypatch.setattr(live_status.asyncio, "to_thread", spy_to_thread)

    await live_status.refresh_live_status()

    assert used["to_thread"] is True


def test_live_status_endpoint_returns_snapshot(monkeypatch):
    async def fake_probe():
        return _sample_results()

    hub = StatusHub()
    monkeypatch.setattr(live_status, "status_hub", hub)
    monkeypatch.setattr(live_status, "probe_live_status", fake_probe)

    app = FastAPI()
    app.include_router(live_status.router, prefix="/api/system")
    response = TestClient(app).get("/api/system/live-status")

    assert response.status_code == 200
    body = response.json()
    assert body["components"]["ai_engine"]["details"]["ari_connected"] is True
    assert body["components"]["directories"]["errors"] == [
        "media_dir_configured: AST_MEDIA_DIR not set in environment"
    ]


def test_live_status_endpoint_returns_existing_snapshot_without_probe(monkeypatch):
    async def forbidden_probe():
        raise AssertionError("probe should not run when the hub already has data")

    hub = StatusHub()
    asyncio.run(hub.upsert_components({
        "ai_engine": component(state="ready", summary="AI Engine pushed", source="push")
    }))
    monkeypatch.setattr(live_status, "status_hub", hub)
    monkeypatch.setattr(live_status, "probe_live_status", forbidden_probe)

    app = FastAPI()
    app.include_router(live_status.router, prefix="/api/system")
    response = TestClient(app).get("/api/system/live-status")

    assert response.status_code == 200
    body = response.json()
    assert body["components"]["ai_engine"]["summary"] == "AI Engine pushed"
    assert body["components"]["ai_engine"]["source"] == "push"


def test_live_status_publish_requires_service_token(monkeypatch):
    monkeypatch.delenv("LIVE_STATUS_PUSH_TOKEN", raising=False)
    monkeypatch.delenv("HEALTH_API_TOKEN", raising=False)

    app = FastAPI()
    app.include_router(live_status.publish_router, prefix="/api/system")
    response = TestClient(app).post("/api/system/live-status/publish", json={"components": {}})

    assert response.status_code == 503


def test_live_status_publish_upserts_pushed_components(monkeypatch):
    monkeypatch.setenv("LIVE_STATUS_PUSH_TOKEN", "push-secret")
    hub = StatusHub()
    monkeypatch.setattr(live_status, "status_hub", hub)

    app = FastAPI()
    app.include_router(live_status.publish_router, prefix="/api/system")
    response = TestClient(app).post(
        "/api/system/live-status/publish",
        headers={"Authorization": "Bearer push-secret"},
        json={
            "source": "ai_engine",
            "components": {
                "ai_engine": {
                    "state": "ready",
                    "summary": "AI Engine pushed status",
                    "details": {"ari_connected": True},
                },
                "sessions": {
                    "state": "ready",
                    "summary": "0 active calls",
                    "details": {"active_calls": 0, "sessions": []},
                },
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    snapshot = asyncio.run(hub.snapshot())
    assert snapshot["components"]["ai_engine"]["source"] == "push"
    assert snapshot["components"]["ai_engine"]["publisher"] == "ai_engine"
    assert snapshot["components"]["sessions"]["details"]["active_calls"] == 0


def test_live_status_publish_rejects_unsupported_component(monkeypatch):
    monkeypatch.setenv("LIVE_STATUS_PUSH_TOKEN", "push-secret")

    app = FastAPI()
    app.include_router(live_status.publish_router, prefix="/api/system")
    response = TestClient(app).post(
        "/api/system/live-status/publish",
        headers={"Authorization": "Bearer push-secret"},
        json={
            "components": {
                "platform": {"state": "ready", "summary": "not service owned"},
            },
        },
    )

    assert response.status_code == 400


def test_encode_sse_formats_snapshot_event():
    encoded = live_status.encode_sse("snapshot", {"hello": "world"}, 42)

    assert encoded.startswith("id: 42\nevent: snapshot\n")
    assert encoded.endswith("\n\n")
    payload = encoded.split("data: ", 1)[1].strip()
    assert json.loads(payload) == {"hello": "world"}
