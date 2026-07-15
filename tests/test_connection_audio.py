from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine


def test_connection_audio_uri_normalization():
    """Only supported Asterisk-local connection media URIs are accepted."""
    assert Engine._normalize_connection_audio_uri("tone:ring") == "tone:ring"
    assert Engine._normalize_connection_audio_uri("tone:ring;tonezone=fr") == "tone:ring;tonezone=fr"
    assert Engine._normalize_connection_audio_uri("custom/please-wait") == "sound:custom/please-wait"
    assert Engine._normalize_connection_audio_uri("file:/tmp/not-allowed.wav") is None
    assert Engine._normalize_connection_audio_uri("sound:https://example.test/ring.wav") is None
    assert Engine._normalize_connection_audio_uri("") is None


@pytest.mark.asyncio
async def test_connection_audio_starts_on_caller_channel_and_stops_idempotently():
    """Connection audio targets only the caller and duplicate stops are harmless."""
    engine = Engine.__new__(Engine)
    engine.ari_client = SimpleNamespace(
        play_media_on_channel_with_id=AsyncMock(return_value=True),
        stop_playback=AsyncMock(return_value=True),
    )
    engine._save_session = AsyncMock()
    session = CallSession(call_id="call-527", caller_channel_id="caller-527")

    await engine._start_connection_audio(session, "tone:ring")

    assert session.connection_audio_playback_id
    assert session.connection_audio_media_uri == "tone:ring"
    assert session.connection_audio_started_ts > 0
    engine.ari_client.play_media_on_channel_with_id.assert_awaited_once_with(
        "caller-527", "tone:ring", session.connection_audio_playback_id
    )

    playback_id = session.connection_audio_playback_id
    await engine._stop_connection_audio(session, reason="first-provider-audio")
    await engine._stop_connection_audio(session, reason="duplicate-first-audio")

    engine.ari_client.stop_playback.assert_awaited_once_with(playback_id)
    assert session.connection_audio_playback_id is None
    assert session.connection_audio_media_uri is None


@pytest.mark.asyncio
async def test_pipeline_context_starts_connection_audio_before_provider_lookup_returns():
    """Pipeline calls start ringback before monolithic provider lookup can return."""
    context = SimpleNamespace(
        provider="local_hybrid",
        connection_audio="tone:ring",
        no_input=None,
        email_recipient=None,
        email_from=None,
        email_enabled=None,
    )

    class Orchestrator:
        agent_store = SimpleNamespace(default_slug=lambda: None)

        def get_context_config(self, name, routing_method=None):
            """Resolve the one agent context used by this isolated lifecycle test."""
            return context if name == "sales" else None

    class ARI:
        play_media_on_channel_with_id = AsyncMock(return_value=True)

        async def send_command(self, method, path, params=None, tolerate_statuses=None):
            """Return the dialplan agent selector without requiring a live ARI server."""
            if params and params.get("variable") == "AI_AGENT":
                return {"value": "sales"}
            return {"value": ""}

    engine = Engine.__new__(Engine)
    engine.ari_client = ARI()
    engine.transport_orchestrator = Orchestrator()
    engine.providers = {}  # Pipeline components are not monolithic providers.
    engine.config = SimpleNamespace(default_provider="local_hybrid")
    engine.no_input_watchdog = None
    engine._save_session = AsyncMock()
    session = CallSession(call_id="call-pipeline-527", caller_channel_id="caller-pipeline-527")

    await Engine._resolve_audio_profile(engine, session, session.caller_channel_id)

    engine.ari_client.play_media_on_channel_with_id.assert_awaited_once()
    assert session.connection_audio_media_uri == "tone:ring"
    assert session.connection_audio_playback_id


@pytest.mark.asyncio
async def test_first_provider_audio_stops_connection_audio_before_playback_work():
    """The first accepted provider chunk stops ringback before playback begins."""
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine._stop_connection_audio = AsyncMock()
    session = CallSession(call_id="call-first-audio", caller_channel_id="caller-first-audio")
    await engine.session_store.upsert_call(session)

    # The remainder of the large provider event handler may need initialized
    # runtime collaborators; its outer safety guard intentionally contains that
    # failure. This assertion isolates the #527 handoff hook itself.
    await engine.on_provider_event(
        {"type": "AgentAudio", "call_id": session.call_id, "data": b"\x00\x01"}
    )

    engine._stop_connection_audio.assert_awaited_once_with(
        session, reason="first-provider-audio"
    )
