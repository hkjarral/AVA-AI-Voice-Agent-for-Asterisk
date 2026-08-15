import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

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
    engine._outbound_forced_hangup_tasks = {}
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
        get_active_attempt_runtime_context=AsyncMock(return_value=None),
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
async def test_answered_channel_remap_drops_stale_originated_channel_owner():
    custom_vars = {"task": "confirm appointment"}
    expected = Engine._serialize_outbound_custom_vars(custom_vars)
    engine = _engine(readback={"value": expected}, custom_vars=custom_vars)
    meta = engine._outbound_attempt_meta_by_attempt_id["attempt-1"]
    meta["channel_id"] = "originated-channel"
    engine._outbound_attempt_meta_by_channel_id = {
        "originated-channel": meta,
    }

    await engine._handle_outbound_answered(
        "answered-channel",
        {"id": "answered-channel"},
        ["outbound", "attempt-1"],
    )

    assert "originated-channel" not in engine._outbound_attempt_meta_by_channel_id
    assert engine._outbound_attempt_meta_by_channel_id["answered-channel"][
        "channel_id"
    ] == "answered-channel"


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
async def test_answered_call_recovers_custom_vars_after_in_memory_state_loss():
    custom_vars = {"task": "confirm appointment"}
    expected = Engine._serialize_outbound_custom_vars(custom_vars)
    engine = _engine(readback={"value": expected}, custom_vars=custom_vars)
    recovered = dict(engine._outbound_attempt_meta_by_attempt_id["attempt-1"])
    engine._outbound_attempt_meta_by_attempt_id.clear()
    engine._outbound_attempt_meta_by_channel_id.clear()
    engine.outbound_store.get_active_attempt_runtime_context.return_value = recovered

    await engine._handle_outbound_answered(
        "channel-1",
        {"id": "channel-1"},
        ["outbound", "attempt-1"],
    )

    engine.outbound_store.get_active_attempt_runtime_context.assert_awaited_once_with(
        "attempt-1"
    )
    engine.ari_client.send_command.assert_awaited_once()
    engine.ari_client.continue_in_dialplan.assert_awaited_once()
    assert engine._outbound_attempt_meta_by_attempt_id["attempt-1"][
        "channel_id"
    ] == "channel-1"


@pytest.mark.asyncio
async def test_answered_call_fails_closed_when_attempt_metadata_is_unrecoverable():
    engine = _engine(readback={"status": 404})
    engine._outbound_attempt_meta_by_attempt_id.clear()
    engine._outbound_attempt_meta_by_channel_id.clear()

    await engine._handle_outbound_answered(
        "channel-1",
        {"id": "channel-1"},
        ["outbound", "attempt-1"],
    )

    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.outbound_store.finish_attempt.assert_awaited_once_with(
        "attempt-1",
        outcome="error",
        error_message="outbound attempt metadata unavailable after answer",
    )
    engine.ari_client.hangup_channel.assert_awaited_once_with("channel-1")
    assert "channel-1" in engine._seen_outbound_channels


@pytest.mark.asyncio
async def test_answered_call_fails_closed_on_corrupt_durable_custom_vars():
    engine = _engine(readback={"status": 404})
    recovered = dict(engine._outbound_attempt_meta_by_attempt_id["attempt-1"])
    recovered["custom_vars_valid"] = False
    engine._outbound_attempt_meta_by_attempt_id.clear()
    engine._outbound_attempt_meta_by_channel_id.clear()
    engine.outbound_store.get_active_attempt_runtime_context.return_value = recovered

    await engine._handle_outbound_answered(
        "channel-1",
        {"id": "channel-1"},
        ["outbound", "attempt-1"],
    )

    engine.ari_client.continue_in_dialplan.assert_not_awaited()
    engine.outbound_store.finish_attempt.assert_awaited_once_with(
        "attempt-1",
        outcome="error",
        error_message="outbound custom_vars metadata is invalid after answer",
    )
    engine.outbound_store.set_lead_state.assert_awaited_once_with(
        "lead-1",
        state="failed",
        last_outcome="error",
    )
    engine.ari_client.hangup_channel.assert_awaited_once_with("channel-1")


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
async def test_outbound_answered_logs_amd_pending_persistence_failure():
    engine = _engine(readback={"status": 404})
    engine.outbound_store.set_lead_state.side_effect = RuntimeError(
        "database unavailable"
    )

    with patch("src.engine.logger.warning") as warning:
        await engine._handle_outbound_answered(
            "channel-1",
            {"id": "channel-1"},
            ["outbound", "attempt-1"],
        )

    warning.assert_any_call(
        "Failed to persist amd_pending lead state after answer",
        channel_id="channel-1",
        attempt_id="attempt-1",
        lead_id="lead-1",
        exc_info=True,
    )
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
async def test_rejected_outbound_hangup_retains_owner_until_retry_is_accepted(
    monkeypatch,
):
    engine = _engine(
        readback={"status": 404},
        custom_vars={"task": "confirm appointment"},
    )
    engine.ari_client.hangup_channel.side_effect = [False, False, True]
    retry_started = asyncio.Event()
    allow_retry = asyncio.Event()

    async def controlled_sleep(_seconds):
        retry_started.set()
        await allow_retry.wait()

    monkeypatch.setattr("src.engine.asyncio.sleep", controlled_sleep)

    await engine._handle_outbound_answered(
        "channel-1",
        {"id": "channel-1"},
        ["outbound", "attempt-1"],
    )
    await retry_started.wait()

    assert "attempt-1" in engine._outbound_attempt_meta_by_attempt_id
    assert "channel-1" in engine._outbound_attempt_meta_by_channel_id
    assert len(engine._outbound_forced_hangup_tasks) == 1

    # Re-scheduling the same channel must retain the existing single owner.
    existing_task = engine._outbound_forced_hangup_tasks["channel-1"]
    engine._schedule_outbound_forced_hangup_retry(
        attempt_id="attempt-1",
        channel_id="channel-1",
    )
    assert engine._outbound_forced_hangup_tasks["channel-1"] is existing_task

    allow_retry.set()
    await existing_task

    assert engine.ari_client.hangup_channel.await_count == 3
    assert engine._outbound_attempt_meta_by_attempt_id == {}
    assert engine._outbound_attempt_meta_by_channel_id == {}
    assert engine._outbound_forced_hangup_tasks == {}


@pytest.mark.asyncio
async def test_destroyed_rejected_channel_cancels_retry_without_overwriting_error(
    monkeypatch,
):
    engine = _engine(
        readback={"status": 404},
        custom_vars={"task": "confirm appointment"},
    )
    engine.ari_client.hangup_channel.return_value = False
    retry_started = asyncio.Event()
    keep_retry_waiting = asyncio.Event()

    async def controlled_sleep(_seconds):
        retry_started.set()
        await keep_retry_waiting.wait()

    monkeypatch.setattr("src.engine.asyncio.sleep", controlled_sleep)

    await engine._handle_outbound_answered(
        "channel-1",
        {"id": "channel-1"},
        ["outbound", "attempt-1"],
    )
    await retry_started.wait()
    retry_task = engine._outbound_forced_hangup_tasks["channel-1"]

    await engine._handle_outbound_channel_destroyed(
        {"channel": {"id": "channel-1"}, "cause_txt": "Unknown"}
    )
    await retry_task

    engine.outbound_store.finish_attempt.assert_awaited_once_with(
        "attempt-1",
        outcome="error",
        error_message="outbound custom_vars could not be confirmed after answer",
    )
    assert engine._outbound_attempt_meta_by_attempt_id == {}
    assert engine._outbound_attempt_meta_by_channel_id == {}
    assert engine._outbound_forced_hangup_tasks == {}


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
