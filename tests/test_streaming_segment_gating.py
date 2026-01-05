import pytest
import asyncio
from unittest.mock import AsyncMock

from src.core.conversation_coordinator import ConversationCoordinator
from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.core.streaming_playback_manager import StreamingPlaybackManager


class _DummyARI:
    pass


@pytest.mark.asyncio
async def test_end_segment_gating_only_clears_once_with_coordinator(monkeypatch):
    """
    Regression test: end_segment_gating must not clear gating twice when a
    ConversationCoordinator is present.
    """
    session_store = SessionStore()
    call_id = "call-1"
    stream_id = "stream-1"

    await session_store.upsert_call(
        CallSession(call_id=call_id, caller_channel_id="caller-1", provider_name="local")
    )

    coordinator = ConversationCoordinator(session_store)
    await coordinator.on_tts_start(call_id, stream_id)

    original_clear = session_store.clear_gating_token
    mocked_clear = AsyncMock(side_effect=original_clear)
    monkeypatch.setattr(session_store, "clear_gating_token", mocked_clear)

    mgr = StreamingPlaybackManager(
        session_store=session_store,
        ari_client=_DummyARI(),
        conversation_coordinator=coordinator,
        streaming_config={},
    )
    mgr.active_streams[call_id] = {"stream_id": stream_id}

    await mgr.end_segment_gating(call_id)

    assert mocked_clear.await_count == 1


@pytest.mark.asyncio
async def test_start_streaming_playback_normalizes_audiosocket_slin(monkeypatch):
    session_store = SessionStore()
    call_id = "call-slin"
    await session_store.upsert_call(
        CallSession(call_id=call_id, caller_channel_id=call_id, provider_name="pipeline")
    )

    mgr = StreamingPlaybackManager(
        session_store=session_store,
        ari_client=_DummyARI(),
        conversation_coordinator=None,
        streaming_config={},
        audio_transport="audiosocket",
    )
    mgr.audiosocket_format = "slin"

    class _DummyTask:
        def cancel(self):
            return None

    def _fake_create_task(coro):
        try:
            coro.close()
        except Exception:
            pass
        return _DummyTask()

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)

    q: asyncio.Queue = asyncio.Queue()
    stream_id = await mgr.start_streaming_playback(
        call_id,
        q,
        playback_type="pipeline-tts",
        source_encoding="mulaw",
        source_sample_rate=8000,
    )
    assert stream_id is not None
    info = mgr.active_streams[call_id]
    assert info.get("target_format") == "slin"
    assert info.get("target_sample_rate") == 8000


@pytest.mark.asyncio
async def test_start_streaming_playback_normalizes_externalmedia_ulaw(monkeypatch):
    session_store = SessionStore()
    call_id = "call-ulaw"
    session = CallSession(call_id=call_id, caller_channel_id=call_id, provider_name="pipeline")
    session.external_media_codec = "ulaw"
    await session_store.upsert_call(session)

    mgr = StreamingPlaybackManager(
        session_store=session_store,
        ari_client=_DummyARI(),
        conversation_coordinator=None,
        streaming_config={},
        audio_transport="externalmedia",
    )
    mgr.audiosocket_format = "slin"

    class _DummyTask:
        def cancel(self):
            return None

    def _fake_create_task(coro):
        try:
            coro.close()
        except Exception:
            pass
        return _DummyTask()

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)

    q: asyncio.Queue = asyncio.Queue()
    stream_id = await mgr.start_streaming_playback(
        call_id,
        q,
        playback_type="pipeline-tts",
        source_encoding="mulaw",
        source_sample_rate=8000,
    )
    assert stream_id is not None
    info = mgr.active_streams[call_id]
    assert info.get("target_format") == "ulaw"
    assert info.get("target_sample_rate") == 8000

