"""HIGH-3: provider-start failure must announce + hang up, not leave dead air.

When a provider's ``start_session()`` raises, the channel was already
answered/bridged, so without intervention it stays open and SILENT until the
caller hangs up. With ``on_provider_failure="announce_hangup"`` (the default) the
engine must play a best-effort error prompt and hang up the channel. With
``on_provider_failure="leave_open"`` the legacy behavior (no hangup) is kept.

The HIGH-1b contract (``session.error_message`` set so the call records as
``error``) must remain intact in both cases.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine


class _FailingProvider:
    """Minimal provider whose session start always raises."""

    async def start_session(self, call_id, context=None):
        raise RuntimeError("boom: upstream provider unreachable")

    async def stop_session(self):
        return None


def _make_engine(on_provider_failure: str, prompt: str = "custom/oops"):
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine._call_providers = {}
    engine.provider_factories = {"local": _FailingProvider}
    engine.pipeline_orchestrator = SimpleNamespace(enabled=False)
    engine.conversation_coordinator = None
    engine.transport_orchestrator = MagicMock()
    engine.config = SimpleNamespace(
        default_provider="local",
        audio_transport="externalmedia",
        audiosocket=None,
        on_provider_failure=on_provider_failure,
        provider_failure_prompt=prompt,
    )
    # ARI client primitives the failure path may reuse.
    engine.ari_client = MagicMock()
    engine.ari_client.play_sound = AsyncMock(return_value={"id": "pb-123"})
    engine.ari_client.hangup_channel = AsyncMock()
    # Heavy collaborators stubbed: not under test here.
    engine._execute_pre_call_tools = AsyncMock(return_value=None)
    engine._apply_provider_overrides = MagicMock()
    engine._save_session = AsyncMock()
    engine._wait_for_ari_playback = AsyncMock(return_value=True)
    return engine


async def _register_session(engine):
    session = CallSession(call_id="call-1", caller_channel_id="chan-1")
    session.provider_name = "local"
    await engine.session_store.upsert_call(session)
    return session


@pytest.mark.asyncio
async def test_announce_hangup_plays_prompt_and_hangs_up():
    engine = _make_engine("announce_hangup")
    session = await _register_session(engine)

    await engine._start_provider_session("call-1")

    # Dead air is ended: the channel is hung up.
    engine.ari_client.hangup_channel.assert_awaited_once_with("chan-1")
    # Best-effort error prompt was attempted.
    engine.ari_client.play_sound.assert_awaited_once()
    assert engine.ari_client.play_sound.await_args.args[0] == "chan-1"
    # HIGH-1b: failure recorded so the call is an 'error'.
    assert session.error_message
    assert "provider_start_failed" in session.error_message


@pytest.mark.asyncio
async def test_leave_open_preserves_legacy_no_hangup():
    engine = _make_engine("leave_open")
    session = await _register_session(engine)

    await engine._start_provider_session("call-1")

    # Legacy behavior: no announcement, no hangup.
    engine.ari_client.hangup_channel.assert_not_awaited()
    engine.ari_client.play_sound.assert_not_awaited()
    # HIGH-1b still holds.
    assert session.error_message
    assert "provider_start_failed" in session.error_message
