from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.core.models import CallSession
from src.engine import Engine


@pytest.mark.asyncio
@pytest.mark.parametrize("source, label", [("provider_event", "provider_event"), ("caller-controlled-value", "other")])
async def test_platform_flush_actions_are_counted_with_bounded_source_labels(monkeypatch, source, label):
    counter = Mock()
    monkeypatch.setattr("src.engine._BARGE_ACTIONS", counter)
    engine = Engine.__new__(Engine)
    session = CallSession(call_id="caller", caller_channel_id="caller", media_rx_confirmed=True)
    engine.session_store = SimpleNamespace(
        get_by_call_id=AsyncMock(return_value=session),
        list_playbacks_for_call=AsyncMock(return_value=[]),
    )
    engine.streaming_playback_manager = SimpleNamespace(
        active_streams={}, get_playback_position_ms=Mock(return_value=0),
        stop_streaming_playback=AsyncMock(),
    )
    engine._save_session = AsyncMock()
    engine._provider_stream_queues = {}
    engine._provider_stream_formats = {}
    engine._provider_coalesce_buf = {}
    engine.config = SimpleNamespace(barge_in=None)
    engine.conversation_coordinator = None

    await engine._apply_barge_in_action("caller", source=source, reason="speech-start")

    engine.streaming_playback_manager.stop_streaming_playback.assert_awaited_once_with("caller")
    counter.labels.assert_called_once_with(source=label)
    counter.labels.return_value.inc.assert_called_once_with()
    assert session.barge_in_count == 1

    session.media_rx_confirmed = False
    await engine._apply_barge_in_action("caller", source=source, reason="pre-media")
    counter.labels.return_value.inc.assert_called_once_with()


@pytest.mark.asyncio
async def test_channel_destroyed_logs_asterisk_cause_without_changing_cleanup(monkeypatch):
    logger = Mock()
    monkeypatch.setattr("src.engine.logger", logger)
    engine = Engine.__new__(Engine)
    engine._pre_stasis_channels = set()
    engine.pending_audiosocket_channels = {}
    engine.pending_websocket_channels = {}
    engine._handle_outbound_channel_destroyed = AsyncMock()
    engine._cleanup_call = AsyncMock()

    await engine._handle_channel_destroyed({
        "channel": {"id": "caller"}, "cause": 18, "cause_txt": "No user responding",
    })

    logger.info.assert_called_once_with(
        "Channel destroyed", channel_id="caller", cause=18, cause_txt="No user responding",
    )
    engine._cleanup_call.assert_awaited_once_with("caller")
