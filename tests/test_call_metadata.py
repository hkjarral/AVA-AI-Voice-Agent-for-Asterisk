from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_call_metadata_policy_is_opt_in_and_rejects_authoritative_or_secret_fields():
    from src.core.call_metadata import (
        CallMetadataValidationError,
        normalize_call_metadata_policy,
    )

    policy = normalize_call_metadata_policy(
        {
            "customer_tier": {"persist": True, "correctable": True},
            "ignored": {"persist": False},
        },
        output_variables={"customer_tier": "contact.tier", "ignored": "contact.ignored"},
    )
    assert set(policy) == {"customer_tier"}
    assert policy["customer_tier"]["correctable"] is True

    with pytest.raises(CallMetadataValidationError, match="authoritative call state"):
        normalize_call_metadata_policy(
            {"caller_number": {"persist": True}},
            output_variables={"caller_number": "contact.phone"},
        )
    with pytest.raises(CallMetadataValidationError, match="credential-related"):
        normalize_call_metadata_policy(
            {"access_token": {"persist": True}},
            output_variables={"access_token": "auth.token"},
        )
    for credential_field in ("customerAccessToken", "crmPassword"):
        with pytest.raises(CallMetadataValidationError, match="credential-related"):
            normalize_call_metadata_policy(
                {credential_field: {"persist": True}},
                output_variables={credential_field: "contact.value"},
            )
    with pytest.raises(CallMetadataValidationError, match="not a configured output variable"):
        normalize_call_metadata_policy(
            {"customer_tier": {"persist": True}},
            output_variables={},
        )
    for invalid_flag in ("true", 1, [], None):
        with pytest.raises(CallMetadataValidationError, match="must be a boolean"):
            normalize_call_metadata_policy(
                {"customer_tier": {"persist": invalid_flag}},
                output_variables={"customer_tier": "contact.tier"},
            )


def test_update_tool_schema_contains_only_call_local_correctable_fields():
    from src.tools.business.update_call_metadata import UpdateCallMetadataTool

    tool = UpdateCallMetadataTool(
        {
            "customer_tier": {"correctable": True, "description": "Confirmed service tier"},
            "account_region": {"correctable": False},
        }
    )
    field = next(param for param in tool.definition.parameters if param.name == "field")
    assert field.enum == ["customer_tier"]


@pytest.mark.asyncio
async def test_session_metadata_update_is_atomic_idempotent_and_fails_after_cleanup():
    from src.core.models import CallSession
    from src.core.session_store import SessionStore

    store = SessionStore()
    session = CallSession(call_id="metadata-call", caller_channel_id="metadata-call")
    session.call_metadata = {"customer_tier": "silver"}
    session.call_metadata_policy = {
        "customer_tier": {"correctable": True, "max_length": 32},
        "immutable_note": {"correctable": False, "max_length": 32},
    }
    await store.upsert_call(session)

    changed = await store.update_call_metadata("metadata-call", "customer_tier", "gold")
    assert changed == {
        "status": "success",
        "message": "Updated 'customer_tier' for this call.",
        "field": "customer_tier",
        "changed": True,
    }
    unchanged = await store.update_call_metadata("metadata-call", "customer_tier", "gold")
    assert unchanged["changed"] is False
    assert len(session.call_metadata_updates) == 1

    denied = await store.update_call_metadata("metadata-call", "immutable_note", "new")
    assert denied["status"] == "error"
    session.cleanup_in_progress = True
    late = await store.update_call_metadata("metadata-call", "customer_tier", "platinum")
    assert late["status"] == "error"
    assert session.call_metadata["customer_tier"] == "gold"

    await store.remove_call("metadata-call")
    missing = await store.update_call_metadata("metadata-call", "customer_tier", "bronze")
    assert missing["status"] == "error"


@pytest.mark.asyncio
async def test_call_history_metadata_round_trip_and_exact_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    from src.core.call_history import CallHistoryStore, CallRecord

    store = CallHistoryStore(db_path=str(tmp_path / "call_metadata.db"))
    now = datetime.now(timezone.utc)
    for index, tier in enumerate(("gold", "golden")):
        assert await store.save(
            CallRecord(
                call_id=f"metadata-{index}",
                start_time=now + timedelta(seconds=index),
                end_time=now + timedelta(seconds=index + 1),
                call_metadata={"customer_tier": tier},
                call_metadata_updates=(
                    [{"field": "customer_tier", "source": "agent_correction"}]
                    if index == 0
                    else []
                ),
            )
        )

    rows = await store.list(
        call_metadata_key="customer_tier",
        call_metadata_value="gold",
    )
    assert [row.call_id for row in rows] == ["metadata-0"]
    assert rows[0].call_metadata == {"customer_tier": "gold"}
    assert rows[0].call_metadata_updates[0]["source"] == "agent_correction"
    assert await store.count(
        call_metadata_key="customer_tier",
        call_metadata_value="gold",
    ) == 1


def test_post_call_payload_keeps_initial_snapshot_and_uses_final_effective_value():
    from src.tools.context import PostCallContext

    payload = PostCallContext(
        call_id="post-call",
        caller_number="1001",
        pre_call_results={"customer_tier": "silver"},
        call_metadata={"customer_tier": "gold"},
    ).to_payload_dict()

    assert payload["customer_tier"] == "gold"
    assert '"customer_tier": "silver"' in payload["pre_call_results_json"]
    assert '"customer_tier": "gold"' in payload["call_metadata_json"]


def test_post_call_context_preserves_existing_positional_field_order():
    from src.tools.context import PostCallContext

    summary_generator = object()
    context = PostCallContext(
        "post-call",
        "1001",
        None,
        None,
        "support",
        "deepgram",
        "inbound",
        12,
        "completed",
        None,
        None,
        [],
        None,
        [],
        {},
        "campaign-1",
        "lead-1",
        {"tools": {}},
        summary_generator,
    )

    assert context.campaign_id == "campaign-1"
    assert context.config == {"tools": {}}
    assert context.summary_generator is summary_generator
    assert context.call_metadata == {}


def test_prompt_substitution_uses_final_metadata_without_overriding_builtins():
    from src.core.models import CallSession
    from src.engine import Engine

    session = CallSession(
        call_id="prompt-call",
        caller_channel_id="prompt-call",
        caller_number="1001",
    )
    session.pre_call_results = {
        "customer_tier": "silver",
        "caller_number": "untrusted",
    }
    session.call_metadata = {"customer_tier": "gold"}

    rendered = Engine.__new__(Engine)._apply_prompt_template_substitution(
        "tier={customer_tier}; caller={caller_number}",
        session,
    )
    assert rendered == "tier=gold; caller=1001"


@pytest.mark.asyncio
async def test_pre_call_execution_seeds_only_selected_metadata():
    from src.core.models import CallSession
    from src.engine import Engine

    engine = Engine.__new__(Engine)
    session = CallSession(call_id="pre-call-metadata", caller_channel_id="pre-call-metadata")
    session.context_name = "support"
    tool = SimpleNamespace(
        definition=SimpleNamespace(
            name="crm_lookup",
            timeout_ms=1000,
            hold_audio_file=None,
            hold_audio_threshold_ms=500,
            output_variables=["customer_tier", "internal_note"],
        ),
        config=SimpleNamespace(
            call_metadata_fields={
                "customer_tier": {
                    "persist": True,
                    "correctable": True,
                    "description": "Confirmed tier",
                    "max_length": 32,
                }
            }
        ),
        execute=AsyncMock(return_value={"customer_tier": "gold", "internal_note": "prompt only"}),
    )
    session.tool_runtime_registry = SimpleNamespace(
        get_tools_for_context=lambda **_kwargs: [tool]
    )
    engine.transport_orchestrator = SimpleNamespace(
        get_context_config=lambda *_args: SimpleNamespace(
            pre_call_tools=["crm_lookup"],
            disable_global_pre_call_tools=[],
        )
    )
    engine.config = SimpleNamespace()
    engine.ari_client = None
    engine._save_session = AsyncMock()

    result = await engine._execute_pre_call_tools(session.call_id, session)

    assert result == {"customer_tier": "gold", "internal_note": "prompt only"}
    assert session.pre_call_results == result
    assert session.call_metadata == {"customer_tier": "gold"}
    assert session.call_metadata_policy["customer_tier"]["source_tool"] == "crm_lookup"
    assert "internal_note" not in session.call_metadata


@pytest.mark.asyncio
async def test_in_call_http_substitution_prefers_final_metadata_then_ai_parameters():
    from src.core.models import CallSession
    from src.core.session_store import SessionStore
    from src.tools.context import ToolExecutionContext
    from src.tools.http.in_call_lookup import InCallHTTPConfig, InCallHTTPTool

    store = SessionStore()
    session = CallSession(call_id="in-call-metadata", caller_channel_id="in-call-metadata")
    session.pre_call_results = {"customer_tier": "silver"}
    session.call_metadata = {"customer_tier": "gold"}
    await store.upsert_call(session)
    tool = InCallHTTPTool(InCallHTTPConfig(name="lookup"))
    context = ToolExecutionContext(
        call_id=session.call_id,
        caller_number="1001",
        session_store=store,
    )

    effective = await tool._build_substitution_context({}, context)
    assert effective["customer_tier"] == "gold"
    ai_override = await tool._build_substitution_context(
        {"customer_tier": "explicit-tool-parameter"},
        context,
    )
    assert ai_override["customer_tier"] == "explicit-tool-parameter"


@pytest.mark.asyncio
async def test_call_local_registry_removes_tool_without_fields_and_installs_bounded_schema():
    from src.core.models import CallSession
    from src.core.session_store import SessionStore
    from src.engine import Engine

    class FakeRegistry:
        def __init__(self):
            self.registered = None
            self.removed = []

        def clone(self):
            return FakeRegistry()

        def register_instance(self, tool):
            self.registered = tool

        def unregister(self, name):
            self.removed.append(name)

    store = SessionStore()
    session = CallSession(call_id="schema-call", caller_channel_id="schema-call")
    session.tool_runtime_registry = FakeRegistry()
    session.call_metadata_policy = {
        "customer_tier": {"correctable": True, "max_length": 32},
        "immutable_note": {"correctable": False, "max_length": 32},
    }
    await store.upsert_call(session)
    engine = Engine.__new__(Engine)
    engine.session_store = store
    engine._save_session = AsyncMock()

    configured = await engine._configure_call_metadata_tool_runtime(session)
    tool = configured.tool_runtime_registry.registered
    field = next(param for param in tool.definition.parameters if param.name == "field")
    assert field.enum == ["customer_tier"]

    configured.call_metadata_policy = {}
    configured.tool_runtime_registry = FakeRegistry()
    configured = await engine._configure_call_metadata_tool_runtime(configured)
    assert configured.tool_runtime_registry.registered is None
    assert configured.tool_runtime_registry.removed == ["update_call_metadata"]
