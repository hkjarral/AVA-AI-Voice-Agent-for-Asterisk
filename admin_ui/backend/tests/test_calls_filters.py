import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from api import calls  # noqa: E402


class _RecordingStore:
    """Captures the filters each endpoint hands to the call history store."""

    def __init__(self):
        self.calls = []

    async def count(self, **kwargs):
        self.calls.append(("count", kwargs))
        return 0

    async def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return []

    async def get_stats(self, **kwargs):
        self.calls.append(("get_stats", kwargs))
        return {"total_calls": 0}


def _client(monkeypatch) -> tuple[TestClient, _RecordingStore]:
    store = _RecordingStore()
    monkeypatch.setattr(calls, "_get_call_history_store", lambda: store)
    # Point the active-calls probe at a closed port so the stats call never waits on a real engine.
    monkeypatch.setenv("AI_ENGINE_HEALTH_URL", "http://127.0.0.1:9")
    app = FastAPI()
    app.include_router(calls.router, prefix="/api")
    return TestClient(app), store


QUERY = {
    "exclude_outcome": "abandoned,no_input_timeout",
    "provider_name": "deepgram",
    "has_tool_calls": "true",
    "min_duration": "10",
    "transcript_search": "refund",
    "start_date": "2026-07-01",
}


def _store_filters(kwargs):
    return {k: v for k, v in kwargs.items() if k not in {"limit", "offset", "order_by", "order_dir", "include_details"}}


def test_list_stats_and_exports_receive_identical_filters(monkeypatch):
    client, store = _client(monkeypatch)

    assert client.get("/api/calls", params=QUERY).status_code == 200
    assert client.get("/api/calls/stats", params=QUERY).status_code == 200
    assert client.get("/api/calls/export/csv", params=QUERY).status_code == 200
    assert client.get("/api/calls/export/json", params=QUERY).status_code == 200

    seen = [_store_filters(kwargs) for _, kwargs in store.calls]
    assert [name for name, _ in store.calls] == ["count", "list", "get_stats", "list", "list"]
    assert all(filters == seen[0] for filters in seen)

    filters = seen[0]
    assert filters["exclude_outcome"] == "abandoned,no_input_timeout"
    assert filters["outcome"] is None
    assert filters["provider_name"] == "deepgram"
    assert filters["has_tool_calls"] is True
    assert filters["min_duration"] == 10.0
    assert filters["transcript_search"] == "refund"
    assert filters["start_date"].isoformat().startswith("2026-07-01")


def test_stats_reject_half_metadata_filter_like_the_list(monkeypatch):
    client, store = _client(monkeypatch)

    response = client.get("/api/calls/stats", params={"call_metadata_key": "customer_tier"})

    assert response.status_code == 422
    assert store.calls == []
