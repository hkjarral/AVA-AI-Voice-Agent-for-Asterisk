import time
from types import SimpleNamespace

import pytest

from src.engine import Engine


class _Provider:
    def __init__(self, *, ready=True, connected=True):
        self._ready = ready
        self._connected = connected

    def is_ready(self):
        return self._ready

    def is_connected(self):
        return self._connected


class _SessionStore:
    async def get_all_sessions(self):
        return [SimpleNamespace(call_id="call-1")]

    async def get_session_stats(self):
        return {
            "active_calls": 1,
            "active_playbacks": 0,
            "provider_sessions": 1,
            "sessions": [{"call_id": "call-1", "provider": "local"}],
        }


class _ConversationCoordinator:
    def get_pending_timer_count(self):
        return 2

    async def get_summary(self):
        return {"gating_active": 1, "capture_disabled": 0, "barge_in_total": 3}


def _make_engine(provider):
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(
        audio_transport="audiosocket",
        default_provider="local",
        pipelines={},
        audiosocket=SimpleNamespace(host="127.0.0.1"),
        external_media=None,
        asterisk=SimpleNamespace(host="127.0.0.1"),
    )
    engine.providers = {"local": provider}
    engine.provider_kinds = {"local": "local"}
    engine.pipeline_orchestrator = None
    engine.ari_client = SimpleNamespace(running=True)
    engine.audio_socket_server = object()
    engine.rtp_server = None
    engine.session_store = _SessionStore()
    engine.conversation_coordinator = _ConversationCoordinator()
    engine._pre_stasis_channels = set()
    engine._start_time = time.time() - 42
    engine._config_hash = "hash"
    engine._config_loaded_at = "2026-06-26T00:00:00Z"
    return engine


@pytest.mark.asyncio
async def test_engine_live_status_components_report_ready_and_sessions():
    engine = _make_engine(_Provider(ready=True, connected=True))

    components = await engine._build_live_status_components()

    assert components["ai_engine"]["state"] == "ready"
    assert components["ai_engine"]["details"]["ari_connected"] is True
    assert components["ai_engine"]["details"]["default_ready"] is True
    assert components["sessions"]["summary"] == "1 active calls"
    assert components["sessions"]["details"]["sessions"][0]["call_id"] == "call-1"


@pytest.mark.asyncio
async def test_engine_live_status_components_degrade_when_local_provider_disconnected():
    engine = _make_engine(_Provider(ready=True, connected=False))

    components = await engine._build_live_status_components()

    assert components["ai_engine"]["state"] == "degraded"
    assert components["ai_engine"]["details"]["default_ready"] is False


@pytest.mark.asyncio
async def test_engine_live_status_keeps_default_ready_when_optional_provider_missing_config():
    engine = _make_engine(_Provider(ready=True, connected=True))
    engine.providers["google_live"] = _Provider(ready=False)
    engine.provider_kinds["google_live"] = "google_live"

    components = await engine._build_live_status_components()

    assert components["ai_engine"]["state"] == "ready"
    assert components["ai_engine"]["details"]["default_ready"] is True
    assert components["ai_engine"]["details"]["provider_warning_severity"] == "info"
    assert components["ai_engine"]["warnings"] == ["google_live: missing_config"]
