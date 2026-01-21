import asyncio
import types

import pytest

from src.engine import Engine


class _StubARIClient:
    def __init__(self):
        self.connect_calls = 0
        self.handlers = []

    async def connect(self):
        self.connect_calls += 1
        raise ConnectionError("ARI not ready yet")

    def add_event_handler(self, event_type, handler):
        self.handlers.append((event_type, handler))

    async def start_listening(self):
        # Mimic reconnect supervisor behavior without sleeping/looping forever.
        try:
            await self.connect()
        except Exception:
            return


class _StubPipelineOrchestrator:
    async def start(self):
        return None


@pytest.mark.unit
async def test_engine_start_does_not_fail_when_ari_unavailable_at_startup():
    engine = Engine.__new__(Engine)
    engine.providers = {}
    engine._call_providers = {}
    engine.call_audio_preferences = {}
    engine.ari_client = _StubARIClient()
    engine._ari_listener_task = None
    engine._outbound_scheduler_task = object()
    engine.pipeline_orchestrator = _StubPipelineOrchestrator()
    engine.mcp_manager = None

    async def _noop(self):
        return None

    engine._load_providers = types.MethodType(_noop, engine)
    engine._start_health_server = types.MethodType(_noop, engine)
    engine._on_playback_finished = types.MethodType(_noop, engine)

    engine.config = types.SimpleNamespace(
        audio_transport="none",
        downstream_mode="streaming",
        default_provider="local",
        mcp=None,
    )

    await engine.start()

    # Let the background task run once.
    await asyncio.sleep(0)

    assert engine._ari_listener_task is not None
    assert engine.ari_client.connect_calls >= 1

