"""Warm-up observability must report the actual adaptive buffer (#856)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.core.streaming_playback_manager import StreamingPlaybackManager


@pytest.mark.asyncio
@pytest.mark.parametrize("playback_type", ["greeting", "response"])
async def test_warmup_log_reports_calculated_buffer_and_stream_starts(monkeypatch, playback_type):
    store = SessionStore()
    await store.upsert_call(CallSession(call_id="warmup", caller_channel_id="warmup", provider_name="local"))
    manager = StreamingPlaybackManager(
        store, SimpleNamespace(), audio_transport="audiosocket",
        streaming_config={"min_start_ms": 100, "greeting_min_start_ms": 40, "jitter_buffer_ms": 400},
    )
    manager.audiosocket_format = "slin"
    logger = Mock()
    monkeypatch.setattr("src.core.streaming_playback_manager.logger", logger)
    # The pattern calculator may differ from the configured minimum; report its result.
    calculator = Mock(return_value=180)
    monkeypatch.setattr("src.core.streaming_playback_manager.calculate_optimal_buffer", calculator)
    # Keep this startup regression deterministic without running producer/pacer loops.
    def create_task(coro):
        coro.close()
        return SimpleNamespace(cancel=Mock(), done=lambda: False)
    monkeypatch.setattr(asyncio, "create_task", create_task)
    stream_id = await manager.start_streaming_playback("warmup", asyncio.Queue(), playback_type=playback_type)
    assert stream_id is not None
    event = next(call for call in logger.info.call_args_list if "STREAMING ADAPTIVE WARM-UP" in call.args[0])
    assert calculator.call_args.kwargs["base_config_ms"] == (40 if playback_type == "greeting" else 100)
    assert event.kwargs["adaptive_min_ms"] == event.kwargs["adaptive_warmup_ms"] == 180
    info = manager.active_streams["warmup"]
    assert event.kwargs["min_start_chunks"] == info["min_start_chunks"]
    assert event.kwargs["resume_floor_ms"] == 180
    assert (await store.get_by_call_id("warmup")).streaming_started is True
