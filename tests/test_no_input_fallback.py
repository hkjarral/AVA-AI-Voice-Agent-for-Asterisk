import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.core.models import CallSession
from src.core.no_input_fallback import DEFAULT_TRANSFER_MESSAGE, validate_fallback_policy
from src.core.no_input_watchdog import NoInputPolicy
from src.core.session_store import SessionStore
from src.engine import Engine


def test_transfer_uses_its_own_announcement_and_hangup_remains_default():
    assert NoInputPolicy.from_mapping({}).timeout_action == "hangup"
    policy = NoInputPolicy.from_mapping({"timeout_action": "transfer", "final_message": "Goodbye"})
    assert policy.final_message == DEFAULT_TRANSFER_MESSAGE


@pytest.mark.parametrize("raw", [
    {"timeout_action": "other"}, {"timeout_action": "transfer"},
    {"fallback_destination": []}, {"transfer_message": " "},
])
def test_invalid_policy_is_rejected(raw):
    with pytest.raises(ValueError):
        validate_fallback_policy(raw)


async def setup_call(name="fallback", *, result=True):
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine.ari_client = SimpleNamespace(
        dialplan_target_exists=AsyncMock(return_value=True),
        continue_in_dialplan=AsyncMock(return_value=result),
        send_command=AsyncMock(return_value={"status": 404}),
    )
    engine._save_session = AsyncMock()
    engine._terminate_call_after_audio = AsyncMock()
    engine._fire_and_forget_for_call = Mock(side_effect=lambda _id, coro, **kw: coro.close())
    engine._cleanup_call = AsyncMock()
    session = CallSession(call_id=name, caller_channel_id=name)
    session.allowed_tools = ["hangup_call"]
    session.no_input_policy = {"timeout_action": "transfer", "fallback_destination": "reception"}
    session.tool_runtime_config = {"tools": {"transfer": {"destinations": {
        "reception": {"type": "queue", "target": "600"},
    }}}}
    await engine.session_store.upsert_call(session)
    return engine, session


@pytest.mark.asyncio
async def test_inbound_timeout_transfers_once_without_enabling_a_model_tool():
    engine, session = await setup_call()
    await asyncio.gather(engine._hangup_for_no_input(session.call_id), engine._hangup_for_no_input(session.call_id))
    engine.ari_client.continue_in_dialplan.assert_awaited_once_with(
        session.call_id, context="ext-queues", extension="600", priority=1)
    engine._terminate_call_after_audio.assert_not_awaited()
    assert session.no_input_state["action_result"] == "handed_off"
    assert session.call_outcome == "transferred"
    assert session.allowed_tools == ["hangup_call"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["deleted", "disabled", "unsupported", "rejected", "pbx_missing"])
async def test_definite_failure_falls_back_to_hangup_without_fuzzy_routing(failure):
    engine, session = await setup_call(result=False)
    cfg = session.tool_runtime_config["tools"]["transfer"]
    if failure == "deleted":
        cfg["destinations"] = {"reception_similar": {"type": "queue", "target": "601"}}
    elif failure == "disabled":
        cfg["enabled"] = False
    elif failure == "unsupported":
        cfg["destinations"]["reception"]["type"] = "vicidial_ingroup"
    elif failure == "pbx_missing":
        engine.ari_client.dialplan_target_exists.return_value = False
    await engine._hangup_for_no_input(session.call_id)
    engine._terminate_call_after_audio.assert_awaited_once()
    assert not getattr(session, "transfer_active", False)
    if failure != "rejected":
        engine.ari_client.continue_in_dialplan.assert_not_awaited()


@pytest.mark.asyncio
async def test_indeterminate_response_keeps_guard_and_does_not_repeat_or_hang_up():
    engine, session = await setup_call(result=None)
    await engine._hangup_for_no_input(session.call_id)
    await engine._hangup_for_no_input(session.call_id)
    assert session.transfer_active is True
    assert session.no_input_state["action_result"] == "indeterminate"
    assert session.call_outcome == "error"
    engine.ari_client.continue_in_dialplan.assert_awaited_once()
    engine._terminate_call_after_audio.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["cleanup", "attended", "replacement"])
async def test_validation_cannot_claim_ownership_after_another_lifecycle_wins(winner):
    engine, session = await setup_call()

    async def validate(*args, **kwargs):
        if winner == "cleanup":
            session.cleanup_in_progress = True
        elif winner == "attended":
            session.current_action = {"type": "attended_transfer"}
        else:
            replacement = CallSession(call_id=session.call_id, caller_channel_id=session.call_id)
            await engine.session_store.upsert_call(replacement)
        return True

    engine.ari_client.dialplan_target_exists.side_effect = validate
    await engine._hangup_for_no_input(session.call_id)
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine._terminate_call_after_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbound_ignores_saved_transfer_action():
    engine, session = await setup_call()
    session.is_outbound = True
    await engine._hangup_for_no_input(session.call_id)
    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine._terminate_call_after_audio.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbound_policy_never_inherits_the_inbound_transfer_message():
    from src.config import NoInputConfig
    engine, session = await setup_call()
    session.is_outbound = True
    engine.config = SimpleNamespace(no_input=NoInputConfig())
    engine.no_input_watchdog = SimpleNamespace(register=AsyncMock())
    await engine._configure_no_input_watchdog(session, SimpleNamespace(no_input={
        "timeout_action": "transfer", "fallback_destination": "reception", "outbound_enabled": True,
    }))
    assert session.no_input_policy["timeout_action"] == "hangup"
    assert "end the call" in session.no_input_policy["final_message"]


@pytest.mark.asyncio
async def test_reconciliation_observes_target_without_reissuing_transfer():
    engine, session = await setup_call(result=None)
    await engine._hangup_for_no_input(session.call_id)
    engine.ari_client.send_command.return_value = {
        "id": session.call_id, "dialplan": {"context": "ext-queues", "exten": "600", "app_name": "Queue"},
    }
    await engine._reconcile_no_input_handoff(session)
    assert session.no_input_state["action_result"] == "confirmed_by_lookup"
    engine._cleanup_call.assert_awaited_once_with(session.call_id)
    engine.ari_client.continue_in_dialplan.assert_awaited_once()
    engine._terminate_call_after_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_response_cannot_restore_flags_or_hang_up_a_replacement_call():
    engine, session = await setup_call()
    replacement = CallSession(call_id=session.call_id, caller_channel_id=session.call_id)
    replacement.transfer_active = True
    replacement.transfer_state = "other-owner"

    async def replace_during_request(*args, **kwargs):
        await engine.session_store.upsert_call(replacement)
        return False

    engine.ari_client.continue_in_dialplan.side_effect = replace_during_request
    await engine._hangup_for_no_input(session.call_id)
    assert replacement.transfer_active is True
    assert replacement.transfer_state == "other-owner"
    engine._terminate_call_after_audio.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_at", ["before_disconnect", "connection_audio_stop", "session_save", "fallback_playback"])
async def test_provider_disconnect_preserves_inactivity_handoff_owner(claim_at):
    engine, session = await setup_call()
    engine.config = SimpleNamespace(tools={"hangup_call": {"fallback_media_uri": "sound:synthetic-failure"}})
    engine._google_long_audio_provider = Mock(return_value=None)
    engine._stop_connection_audio = AsyncMock()
    engine.ari_client.hangup_channel = AsyncMock()
    engine.ari_client.play_media = AsyncMock(return_value={})

    async def transfer_during_await(*args, **kwargs):
        await engine._hangup_for_no_input(session.call_id)
        return {}

    if claim_at == "before_disconnect":
        await engine._hangup_for_no_input(session.call_id)
    elif claim_at == "connection_audio_stop":
        engine._stop_connection_audio.side_effect = transfer_during_await
    elif claim_at == "session_save":
        engine._save_session.side_effect = transfer_during_await
    else:
        engine.ari_client.play_media.side_effect = transfer_during_await

    await engine.on_provider_event({"type": "ProviderDisconnected", "call_id": session.call_id})

    assert session.transfer_active is True
    assert session.transfer_state == "no_input_fallback"
    engine.ari_client.continue_in_dialplan.assert_awaited_once()
    engine.ari_client.hangup_channel.assert_not_awaited()
    if claim_at != "fallback_playback":
        engine.ari_client.play_media.assert_not_awaited()
    engine._terminate_call_after_audio.assert_not_awaited()
