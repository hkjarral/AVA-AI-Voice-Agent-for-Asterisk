from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.engine import Engine, OUTBOUND_CUSTOM_VARS_MAX_SERIALIZED_BYTES


def _engine(*, readback, custom_vars=None):
    engine = Engine.__new__(Engine)
    engine._outbound_amd_context = "aava-outbound-amd"
    engine._outbound_awaiting_amd_channel_ids = set()
    engine._outbound_attempt_meta_by_attempt_id = {
        "attempt-1": {
            "attempt_id": "attempt-1",
            "campaign_id": "campaign-1",
            "lead_id": "lead-1",
            "context": "sales",
            "routing_method": "ai_agent",
            "custom_vars": custom_vars or {},
        }
    }
    engine._outbound_attempt_meta_by_channel_id = {
        "channel-1": engine._outbound_attempt_meta_by_attempt_id["attempt-1"]
    }
    engine._outbound_attempt_amd = {}
    engine._seen_outbound_channels = set()
    engine._set_outbound_agent_channel_vars = AsyncMock()
    engine.ari_client = SimpleNamespace(
        set_channel_var=AsyncMock(return_value=True),
        continue_in_dialplan=AsyncMock(return_value=True),
        send_command=AsyncMock(return_value=readback),
        hangup_channel=AsyncMock(return_value=True),
    )
    engine.outbound_store = SimpleNamespace(
        set_attempt_channel=AsyncMock(),
        set_lead_state=AsyncMock(),
        get_campaign=AsyncMock(return_value={}),
        finish_attempt=AsyncMock(),
    )
    return engine


def test_outbound_custom_vars_serialization_is_canonical_and_bounded():
    value = Engine._serialize_outbound_custom_vars(
        {"z": "café", "a": {"enabled": True}}
    )

    assert value == '{"a":{"enabled":true},"z":"caf\\u00e9"}'
    assert Engine._serialize_outbound_custom_vars({}) == "{}"

    with pytest.raises(ValueError, match="serialized limit"):
        Engine._serialize_outbound_custom_vars(
            {"payload": "x" * OUTBOUND_CUSTOM_VARS_MAX_SERIALIZED_BYTES}
        )

    with pytest.raises(ValueError, match="JSON serializable"):
        Engine._serialize_outbound_custom_vars({"bad": object()})


@pytest.mark.asyncio
async def test_confirmation_accepts_matching_readback_after_write_failure():
    engine = _engine(readback={"value": '{"task":"call"}'})
    engine.ari_client.set_channel_var.return_value = False

    confirmed = await engine._set_and_confirm_outbound_custom_vars(
        "channel-1", '{"task":"call"}'
    )

    assert confirmed is True
    engine.ari_client.send_command.assert_awaited_once_with(
        "GET",
        "channels/channel-1/variable",
        params={"variable": "AAVA_CUSTOM_VARS_JSON"},
        tolerate_statuses=[404],
    )


@pytest.mark.asyncio
async def test_outbound_answered_reapplies_and_confirms_custom_vars_before_amd():
    custom_vars = {"task": "confirm appointment"}
    expected = Engine._serialize_outbound_custom_vars(custom_vars)
    engine = _engine(readback={"value": expected}, custom_vars=custom_vars)

    await engine._handle_outbound_answered(
        "channel-1",
        {"id": "channel-1"},
        ["outbound", "attempt-1"],
    )

    assert (
        ("channel-1", "AAVA_CUSTOM_VARS_JSON", expected)
        in [call.args for call in engine.ari_client.set_channel_var.await_args_list]
    )
    engine.ari_client.continue_in_dialplan.assert_awaited_once()
    engine.outbound_store.finish_attempt.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_safety_write_failure_cannot_skip_custom_vars_confirmation():
    custom_vars = {"task": "confirm appointment"}
    expected = Engine._serialize_outbound_custom_vars(custom_vars)
    engine = _engine(readback={"value": expected}, custom_vars=custom_vars)
    engine.ari_client.set_channel_var.side_effect = [
        True,
        RuntimeError("correlation write unavailable"),
    ]

    await engine._handle_outbound_answered(
        "channel-1",
        {"id": "channel-1"},
        ["outbound", "attempt-1"],
    )

    engine.ari_client.send_command.assert_awaited_once()
    assert engine.ari_client.set_channel_var.await_args_list[0].args == (
        "channel-1",
        "AAVA_CUSTOM_VARS_JSON",
        expected,
    )
    engine.ari_client.continue_in_dialplan.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbound_answered_fails_closed_when_custom_vars_cannot_be_confirmed():
    engine = _engine(
        readback={"status": 404},
        custom_vars={"task": "confirm appointment"},
    )

    await engine._handle_outbound_answered(
        "channel-1",
        {"id": "channel-1"},
        ["outbound", "attempt-1"],
    )

    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.outbound_store.finish_attempt.assert_awaited_once_with(
        "attempt-1",
        outcome="error",
        error_message="outbound custom_vars could not be confirmed after answer",
    )
    engine.outbound_store.set_lead_state.assert_any_await(
        "lead-1",
        state="failed",
        last_outcome="error",
    )
    engine.ari_client.hangup_channel.assert_awaited_once_with("channel-1")
    assert "channel-1" in engine._seen_outbound_channels
    assert "attempt-1" not in engine._outbound_attempt_meta_by_attempt_id
    assert "channel-1" not in engine._outbound_attempt_meta_by_channel_id


@pytest.mark.asyncio
async def test_outbound_answered_does_not_probe_when_custom_vars_are_empty():
    engine = _engine(readback={"status": 404})

    await engine._handle_outbound_answered(
        "channel-1",
        {"id": "channel-1"},
        ["outbound", "attempt-1"],
    )

    engine.ari_client.send_command.assert_not_awaited()
    engine.ari_client.continue_in_dialplan.assert_awaited_once()


@pytest.mark.asyncio
async def test_custom_vars_rejection_remains_fail_closed_when_persistence_fails():
    engine = _engine(
        readback={"status": 404},
        custom_vars={"task": "confirm appointment"},
    )
    engine.outbound_store.finish_attempt.side_effect = RuntimeError("database unavailable")

    await engine._handle_outbound_answered(
        "channel-1",
        {"id": "channel-1"},
        ["outbound", "attempt-1"],
    )

    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_awaited_once_with("channel-1")


@pytest.mark.asyncio
async def test_oversized_custom_vars_fail_before_ari_originate():
    engine = Engine.__new__(Engine)
    engine._outbound_extension_identity = "6789"
    engine._outbound_pbx_type = "generic"
    engine._outbound_attempt_meta_by_attempt_id = {
        "attempt-1": {"attempt_id": "attempt-1"}
    }
    engine.transport_orchestrator = SimpleNamespace(
        get_context_config=Mock(return_value=None)
    )
    engine.providers = {}
    engine._outbound_build_amd_opts = Mock(return_value="")
    engine.ari_client = SimpleNamespace(originate_channel=AsyncMock())
    engine.outbound_store = SimpleNamespace(
        finish_attempt=AsyncMock(),
        set_lead_state=AsyncMock(),
    )
    lead = {
        "id": "lead-1",
        "phone_number": "+15551234567",
        "custom_vars": {
            "payload": "x" * OUTBOUND_CUSTOM_VARS_MAX_SERIALIZED_BYTES
        },
    }
    campaign = {
        "id": "campaign-1",
        "default_context": "sales",
        "agent_routing_method": "ai_agent",
    }

    await engine._outbound_originate_attempt(campaign, lead, "attempt-1")

    engine.ari_client.originate_channel.assert_not_awaited()
    engine.outbound_store.finish_attempt.assert_awaited_once()
    engine.outbound_store.set_lead_state.assert_awaited_once_with(
        "lead-1",
        state="failed",
        last_outcome="error",
    )
    assert "attempt-1" not in engine._outbound_attempt_meta_by_attempt_id
