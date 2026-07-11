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
from pydantic import ValidationError

from src.config import AppConfig
from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine
from src.pipelines.orchestrator import PipelineOrchestratorError


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
    engine.providers = {"local": object()}
    engine.pipeline_orchestrator = SimpleNamespace(enabled=False)
    engine.conversation_coordinator = None
    engine.transport_orchestrator = MagicMock()
    engine.config = SimpleNamespace(
        default_provider="local",
        audio_transport="externalmedia",
        audiosocket=None,
        on_provider_failure=on_provider_failure,
        provider_failure_prompt=prompt,
        provider_failure_redirect_context="aava-provider-failure",
        provider_failure_redirect_extension="s",
        provider_failure_redirect_priority=1,
    )
    # ARI client primitives the failure path may reuse.
    engine.ari_client = MagicMock()
    engine.ari_client.play_sound = AsyncMock(return_value={"id": "pb-123"})
    engine.ari_client.hangup_channel = AsyncMock()
    engine.ari_client.dialplan_target_exists = AsyncMock(return_value=True)
    engine.ari_client.continue_in_dialplan = AsyncMock(return_value=True)
    # Heavy collaborators stubbed: not under test here.
    engine._execute_pre_call_tools = AsyncMock(return_value=None)
    engine._apply_provider_overrides = MagicMock()
    engine._save_session = AsyncMock()
    engine._wait_for_ari_playback = AsyncMock(return_value=True)
    return engine


class _MissingPipelineOrchestrator:
    enabled = True

    def get_pipeline(self, call_id, pipeline_name=None):
        raise PipelineOrchestratorError(f"Pipeline '{pipeline_name}' is not configured")


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


@pytest.mark.asyncio
async def test_explicit_missing_pipeline_never_falls_back_to_default_provider():
    engine = _make_engine("announce_hangup")
    fallback_factory = MagicMock(side_effect=AssertionError("default provider must not start"))
    engine.provider_factories = {"local": fallback_factory}
    engine.pipeline_orchestrator = _MissingPipelineOrchestrator()
    session = await _register_session(engine)
    session.pipeline_name = "missing_pipeline"

    await engine._start_provider_session("call-1")

    fallback_factory.assert_not_called()
    assert session.pipeline_resolution_error
    assert "missing_pipeline" in session.pipeline_resolution_error
    assert session.error_message == session.pipeline_resolution_error
    engine.ari_client.hangup_channel.assert_awaited_once_with("chan-1")


@pytest.mark.asyncio
async def test_provider_failure_can_redirect_to_dialplan_once():
    engine = _make_engine("dialplan_redirect")
    session = await _register_session(engine)

    await engine._start_provider_session("call-1")
    await engine._handle_provider_start_failure(session)

    engine.ari_client.continue_in_dialplan.assert_awaited_once_with(
        "chan-1",
        context="aava-provider-failure",
        extension="s",
        priority=1,
    )
    assert session.transfer_active is True
    assert session.transfer_state == "provider_failure_redirect"
    engine.ari_client.play_sound.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_provider_failure_redirect_target_announces_and_hangs_up():
    engine = _make_engine("dialplan_redirect")
    engine.ari_client.dialplan_target_exists.return_value = False
    session = await _register_session(engine)

    await engine._start_provider_session("call-1")

    engine.ari_client.dialplan_target_exists.assert_awaited_once_with(
        "chan-1",
        context="aava-provider-failure",
        extension="s",
        priority=1,
    )
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    assert getattr(session, "transfer_active", False) is False
    assert getattr(session, "transfer_state", None) is None
    engine.ari_client.play_sound.assert_awaited_once()
    engine.ari_client.hangup_channel.assert_awaited_once_with("chan-1")


@pytest.mark.asyncio
async def test_failed_provider_failure_redirect_restores_cleanup_and_hangs_up():
    engine = _make_engine("dialplan_redirect")
    engine.ari_client.continue_in_dialplan.return_value = False
    session = await _register_session(engine)

    await engine._start_provider_session("call-1")

    assert session.transfer_active is False
    assert session.transfer_state is None
    engine.ari_client.play_sound.assert_awaited_once()
    engine.ari_client.hangup_channel.assert_awaited_once_with("chan-1")


def test_unknown_provider_failure_policy_is_rejected_at_config_load():
    with pytest.raises(ValidationError, match="on_provider_failure"):
        AppConfig(
            default_provider="local",
            providers={"local": {"enabled": True}},
            asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
            llm={"initial_greeting": "hi", "prompt": "prompt"},
            on_provider_failure="typo_fallback",
        )
